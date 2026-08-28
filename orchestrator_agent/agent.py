"""Bounded workflow orchestration with specialist call/return semantics."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk import Agent
from google.adk import Context
from google.adk import Event
from google.adk import Workflow
from google.adk.workflow import BaseNode
from google.adk.workflow import node
from google.adk.workflow import START

from .schemas import OrchestrationContext
from .schemas import OrchestratorDecision
from .schemas import OrchestratorResult
from .schemas import SpecialistName
from .schemas import SpecialistResult
from .schemas import SpecialistTask
from .settings import MODEL


MAX_ORCHESTRATOR_STEPS = 4


math_agent = Agent(
    model=MODEL,
    name="math",
    description=(
        "Solves arithmetic, algebra, geometry, probability, and other "
        "quantitative problems."
    ),
    instruction=(
        "Act only as the math specialist. Solve the focused task in the JSON "
        "input. Show enough calculation or reasoning to make the result "
        "checkable. Return a concise answer and key points."
    ),
    mode="single_turn",
    include_contents="none",
    input_schema=SpecialistTask,
    output_schema=SpecialistResult,
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)


science_agent = Agent(
    model=MODEL,
    name="science",
    description=(
        "Explains and analyzes questions in biology, chemistry, physics, "
        "earth science, and scientific reasoning."
    ),
    instruction=(
        "Act only as the science specialist. Address the focused task in the "
        "JSON input accurately, distinguish established facts from "
        "uncertainty, and return a concise answer and key points."
    ),
    mode="single_turn",
    include_contents="none",
    input_schema=SpecialistTask,
    output_schema=SpecialistResult,
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)


english_agent = Agent(
    model=MODEL,
    name="english",
    description=(
        "Handles writing, editing, grammar, rhetoric, reading comprehension, "
        "and literary analysis."
    ),
    instruction=(
        "Act only as the English specialist. Complete the focused writing or "
        "language task in the JSON input. Preserve the user's meaning and "
        "return a polished answer with useful key points."
    ),
    mode="single_turn",
    include_contents="none",
    input_schema=SpecialistTask,
    output_schema=SpecialistResult,
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)


orchestrator = Agent(
    model=MODEL,
    name="orchestrator",
    description=(
        "Selects math, science, or English specialists and synthesizes their "
        "results."
    ),
    instruction=(
        "You are the main orchestrator. The JSON input contains the original "
        "request, the specialists still available, and validated results from "
        "specialists already consulted. Make exactly one decision.\n\n"
        "Use action='delegate' when another specialist is needed. Select one "
        "exact remaining agent and provide a self-contained instruction. Use "
        "math for quantitative reasoning, science for scientific reasoning, "
        "and english for writing or language work. A mixed request may need "
        "more than one specialist, called sequentially.\n\n"
        "Use action='finish' as soon as the available results are sufficient. "
        "Synthesize those results into the complete user-facing answer. Never "
        "delegate to an agent absent from remaining_agents and never ask a "
        "specialist to choose another agent."
    ),
    mode="single_turn",
    include_contents="none",
    input_schema=OrchestrationContext,
    output_schema=OrchestratorDecision,
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)


_SPECIALISTS: dict[SpecialistName, BaseNode] = {
    "math": math_agent,
    "science": science_agent,
    "english": english_agent,
}


@node(name="orchestrate", rerun_on_resume=True)
async def orchestrate(
    ctx: Context, node_input: str
) -> AsyncGenerator[OrchestratorResult, None]:
  """Run selected specialists and return one synthesized result."""
  completed: dict[str, SpecialistResult] = {}

  for step in range(MAX_ORCHESTRATOR_STEPS):
    remaining = [name for name in _SPECIALISTS if name not in completed]
    decision = OrchestratorDecision.model_validate(
        await ctx.run_node(
            orchestrator,
            node_input=OrchestrationContext(
                request=node_input,
                remaining_agents=remaining,
                completed_results=completed,
            ),
            run_id=f"orchestrator-step-{step}",
            use_sub_branch=True,
        )
    )

    if decision.action == "finish":
      answer = decision.answer.strip()
      if not answer:
        raise ValueError("The orchestrator finished without an answer.")
      yield OrchestratorResult(
          answer=answer,
          consulted_agents=list(completed),
      )
      return

    target_name = decision.target
    if target_name is None:
      raise ValueError("A delegate decision requires a target.")
    if target_name in completed:
      raise ValueError(
          f"The orchestrator selected completed agent {target_name!r}."
      )

    specialist = _SPECIALISTS[target_name]
    instruction = decision.instruction.strip() or node_input
    completed[target_name] = SpecialistResult.model_validate(
        await ctx.run_node(
            specialist,
            node_input=SpecialistTask(
                request=node_input,
                instruction=instruction,
                previous_results=completed,
            ),
            run_id=f"{target_name}-step-{step}",
            use_sub_branch=True,
        )
    )

  raise RuntimeError(
      "The orchestrator exceeded its four-step delegation budget."
  )


def render_result(node_input: OrchestratorResult) -> Event:
  """Render the workflow's terminal result once."""
  return Event(message=node_input.answer, output=node_input)


root_agent = Workflow(
    name="orchestrator_agent",
    description=(
        "Uses math, science, and English specialists with bounded call/return "
        "orchestration."
    ),
    input_schema=str,
    output_schema=OrchestratorResult,
    edges=[(START, orchestrate, render_result)],
)

