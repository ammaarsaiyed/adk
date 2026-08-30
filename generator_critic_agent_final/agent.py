"""Declarative generator-critic-refiner loop workflow."""

from __future__ import annotations

from google.adk import Context
from google.adk import Event
from google.adk import Workflow
from google.adk.workflow import START

from .agents.critic import critic_agent
from .agents.generator import generator_agent
from .agents.refiner import refiner_agent
from .schemas import ReflectionAgentCriticOutput
from .schemas import ReflectionAgentOutputDraft


MAX_REFINEMENTS = 5


def extract_user_input(node_input: str) -> Event:
  """Validate and preserve the original request for the reflection loop."""
  original_user_prompt = node_input.strip()
  if not original_user_prompt:
    raise ValueError("A non-empty user prompt is required.")
  return Event(
      output=original_user_prompt,
      state={
          "temp:original_user_prompt": original_user_prompt,
          "temp:refinement_count": 0,
      },
  )


def route_review(
    ctx: Context, node_input: ReflectionAgentCriticOutput
) -> Event:
  """Route an approved draft to output or a rejected draft to refinement."""
  draft = ReflectionAgentOutputDraft.model_validate(
      ctx.state["temp:current_draft"]
  )
  if node_input.decision == "PASS":
    return Event(output=draft.model_dump(), route="PASS")

  refinement_count = ctx.state["temp:refinement_count"]
  if refinement_count >= MAX_REFINEMENTS:
    raise RuntimeError(
        "The critic did not approve a draft within the refinement budget."
    )

  refiner_input = ReflectionAgentOutputDraft(
      draft=draft.draft,
      feedback=node_input.feedback,
  )
  return Event(
      output=refiner_input.model_dump(),
      route="REFINE",
      state={"temp:refinement_count": refinement_count + 1},
  )


def render_result(node_input: ReflectionAgentOutputDraft) -> Event:
  """Render the approved draft while preserving it as workflow output."""
  return Event(message=node_input.draft, output=node_input.model_dump())


root_agent = Workflow(
    name="generator_critic_workflow_2",
    input_schema=str,
    output_schema=ReflectionAgentOutputDraft,
    edges=[
        (
            START,
            extract_user_input,
            generator_agent,
            critic_agent,
            route_review,
        ),
        (
            route_review,
            {"PASS": render_result, "REFINE": refiner_agent},
        ),
        (refiner_agent, critic_agent),
    ],
)
