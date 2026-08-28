"""Hierarchical workflow orchestration and application entry point."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from collections.abc import Mapping
import json

from google.adk import Agent
from google.adk import Context
from google.adk import Event
from google.adk import Workflow
from google.adk.workflow import BaseNode
from google.adk.workflow import node
from google.adk.workflow import START

from .agents.proofreader import proofreader
from .agents.research_manager import research_manager
from .agents.researcher import researcher
from .agents.root_manager import root_manager
from .agents.summarizer import summarizer
from .agents.writer import writer
from .agents.writing_manager import writing_manager
from .schemas import InputPlaceholder
from .schemas import ManagerDecision
from .schemas import OutputPlaceholder


MAX_MANAGER_STEPS = 6


def _serialize_outputs(outputs: Mapping[str, OutputPlaceholder]) -> str:
  """Serialize completed child outputs for explicit state injection."""
  return json.dumps(
      {name: output.model_dump() for name, output in outputs.items()}
  )


async def _run_hierarchy(
    *,
    ctx: Context,
    node_input: InputPlaceholder,
    scope: str,
    manager: Agent,
    targets: Mapping[str, BaseNode],
) -> AsyncGenerator[Event | OutputPlaceholder, None]:
  """Run one selective, bounded manager/child hierarchy."""
  completed: dict[str, OutputPlaceholder] = {}
  completed_order: list[str] = []

  yield Event(
      state={
          f"temp:{scope}_request": node_input.text,
          f"temp:{scope}_context": json.dumps(node_input.context),
          f"temp:{scope}_completed_results": "{}",
      }
  )

  for step in range(MAX_MANAGER_STEPS):
    available_targets = [
        target_name
        for target_name in targets
        if target_name not in completed
    ]
    yield Event(
        state={
            f"temp:{scope}_available_targets": json.dumps(
                available_targets
            ),
            f"temp:{scope}_completed_results": _serialize_outputs(
                completed
            ),
        }
    )

    decision = ManagerDecision.model_validate(
        await ctx.run_node(
            manager,
            run_id=f"{scope}-manager-step-{step}",
            use_sub_branch=True,
        )
    )

    if decision.action == "finish":
      result = decision.result.strip()
      if not result:
        raise ValueError(f"{manager.name} finished without a result.")
      details = [
          detail
          for target_name in completed_order
          for detail in completed[target_name].details
      ]
      yield OutputPlaceholder(text=result, details=details)
      return

    target = targets.get(decision.target)
    if target is None:
      available = ", ".join(targets)
      raise ValueError(
          f"{manager.name} selected unknown target {decision.target!r}. "
          f"Available targets: {available}."
      )
    if decision.target in completed:
      raise ValueError(
          f"{manager.name} selected completed target {decision.target!r}."
      )

    instruction = decision.instruction.strip() or node_input.text
    yield Event(
        state={
            f"temp:{scope}_selected_instruction": instruction,
        }
    )

    child_input = None
    if isinstance(target, Workflow):
      child_input = InputPlaceholder(
          text=instruction,
          context={
              **node_input.context,
              **{
                  name: output.model_dump_json()
                  for name, output in completed.items()
              },
          },
      )
    child_output = OutputPlaceholder.model_validate(
        await ctx.run_node(
            target,
            node_input=child_input,
            run_id=f"{scope}-{decision.target}-step-{step}",
            use_sub_branch=True,
        )
    )
    completed[decision.target] = child_output
    completed_order.append(decision.target)

    if not getattr(target, "output_key", None):
      yield Event(
          state={
              f"temp:{decision.target}_output": child_output.model_dump()
          }
      )

  raise RuntimeError(
      f"{manager.name} exceeded the {MAX_MANAGER_STEPS}-step budget."
  )


@node(name="research_controller", rerun_on_resume=True)
async def research_controller(
    ctx: Context, node_input: InputPlaceholder
) -> AsyncGenerator[Event | OutputPlaceholder, None]:
  """Let the research manager select only necessary specialists."""
  async for event in _run_hierarchy(
      ctx=ctx,
      node_input=node_input,
      scope="research_department",
      manager=research_manager,
      targets={"researcher": researcher, "summarizer": summarizer},
  ):
    yield event


@node(name="writing_controller", rerun_on_resume=True)
async def writing_controller(
    ctx: Context, node_input: InputPlaceholder
) -> AsyncGenerator[Event | OutputPlaceholder, None]:
  """Let the writing manager select only necessary specialists."""
  async for event in _run_hierarchy(
      ctx=ctx,
      node_input=node_input,
      scope="writing_department",
      manager=writing_manager,
      targets={"writer": writer, "proofreader": proofreader},
  ):
    yield event


research_workflow = Workflow(
    name="research_department",
    description="Selectively runs research specialists.",
    input_schema=InputPlaceholder,
    output_schema=OutputPlaceholder,
    edges=[(START, research_controller)],
)


writing_workflow = Workflow(
    name="writing_department",
    description="Selectively runs writing specialists.",
    input_schema=InputPlaceholder,
    output_schema=OutputPlaceholder,
    edges=[(START, writing_controller)],
)


@node(name="root_controller", rerun_on_resume=True)
async def root_controller(
    ctx: Context, node_input: str
) -> AsyncGenerator[Event | OutputPlaceholder, None]:
  """Let the root manager select only necessary departments."""
  async for event in _run_hierarchy(
      ctx=ctx,
      node_input=InputPlaceholder(text=node_input),
      scope="root",
      manager=root_manager,
      targets={
          "research_department": research_workflow,
          "writing_department": writing_workflow,
      },
  ):
    yield event


def render_result(node_input: OutputPlaceholder) -> Event:
  """Render the terminal workflow result exactly once in the web UI."""
  return Event(message=node_input.text, output=node_input)


root_agent = Workflow(
    name="hello_agent",
    description="Routes requests through only the necessary specialist teams.",
    input_schema=str,
    output_schema=OutputPlaceholder,
    edges=[(START, root_controller, render_result)],
)
