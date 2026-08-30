"""Bounded generator-critic-refiner workflow."""

from __future__ import annotations

from google.adk import Context
from google.adk import Event
from google.adk import Workflow
from google.adk.workflow import node
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
      state={"temp:original_user_prompt": original_user_prompt},
  )


@node(rerun_on_resume=True)
async def generator_critic_loop(
    ctx: Context, node_input: str
) -> ReflectionAgentOutputDraft:
  """Generate, critique, and refine until approved or the budget expires."""
  draft = ReflectionAgentOutputDraft.model_validate(
      await ctx.run_node(
          generator_agent,
          node_input,
          run_id="initial-draft",
          raise_on_wait=True,
      )
  )

  for review_number in range(MAX_REFINEMENTS + 1):
    review = ReflectionAgentCriticOutput.model_validate(
        await ctx.run_node(
            critic_agent,
            draft,
            run_id=f"critique-{review_number}",
            raise_on_wait=True,
        )
    )
    if review.decision == "PASS":
      return draft
    if review_number == MAX_REFINEMENTS:
      break

    draft = ReflectionAgentOutputDraft.model_validate(
        await ctx.run_node(
            refiner_agent,
            ReflectionAgentOutputDraft(
                draft=draft.draft,
                feedback=review.feedback,
            ),
            run_id=f"refinement-{review_number + 1}",
            raise_on_wait=True,
        )
    )

  raise RuntimeError(
      "The critic did not approve a draft within the refinement budget."
  )


def render_result(node_input: ReflectionAgentOutputDraft) -> Event:
  """Render the approved draft while preserving it as workflow output."""
  return Event(message=node_input.draft, output=node_input.model_dump())


root_agent = Workflow(
    name="generator_critic_workflow",
    input_schema=str,
    output_schema=ReflectionAgentOutputDraft,
    edges=[
        (
            START,
            extract_user_input,
            generator_critic_loop,
            render_result,
        )
    ],
)
