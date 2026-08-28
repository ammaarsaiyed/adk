"""ADK 2 workflow demonstrating text and checkbox HITL input."""

from __future__ import annotations

from typing import Literal

from google.adk import Agent
from google.adk import Context
from google.adk import Workflow
from google.adk.events import RequestInput
from google.adk.workflow import node
from pydantic import BaseModel
from pydantic import Field


class AgentTurn(BaseModel):
  """One structured decision from the shared LLM agent."""

  action: Literal[
      "final", "request_time_off", "deploy_to_production"
  ] = Field(
      description=(
          "Return final, request a text answer for time off, or request a"
          " checkbox approval for production deployment."
      )
  )
  message: str = Field(
      description="The final answer or the question to show to the human."
  )


class TextResponse(BaseModel):
  """Schema rendered by the client as a text input."""

  text: str = Field(description="The human's answer.")


class ApprovalResponse(BaseModel):
  """Schema rendered by the client as an approval checkbox."""

  approved: bool = Field(description="Whether the request is approved.")


class ModelInput(BaseModel):
  """Complete input supplied to either turn of the same LLM agent."""

  original_request: str
  human_input: TextResponse | ApprovalResponse | None = None


@node(rerun_on_resume=False)
def request_human_input(node_input: AgentTurn) -> RequestInput:
  """Pause for the response type selected by the first LLM turn."""
  if node_input.action == "deploy_to_production":
    response_schema = ApprovalResponse
  elif node_input.action == "request_time_off":
    response_schema = TextResponse
  else:
    raise ValueError("A final turn must not request human input.")

  return RequestInput(
      message=node_input.message,
      response_schema=response_schema,
  )


@node(rerun_on_resume=True)
async def run(ctx: Context, node_input: str) -> str:
  """Run the bounded decide, pause, and answer interaction."""
  original_request = node_input.strip()
  if not original_request:
    raise ValueError("A non-empty user message is required.")

  first_turn = AgentTurn.model_validate(
      await ctx.run_node(
          hitl_agent,
          node_input=ModelInput(
              original_request=original_request
          ).model_dump_json(exclude_none=True),
          run_id="initial_decision",
      )
  )
  if first_turn.action == "final":
    return first_turn.message

  raw_human_input = await ctx.run_node(
      request_human_input,
      first_turn,
      run_id="human_input",
  )
  if first_turn.action == "deploy_to_production":
    human_input = ApprovalResponse.model_validate(raw_human_input)
  else:
    human_input = TextResponse.model_validate(raw_human_input)

  final_turn = AgentTurn.model_validate(
      await ctx.run_node(
          hitl_agent,
          node_input=ModelInput(
              original_request=original_request,
              human_input=human_input,
          ).model_dump_json(),
          run_id="final_response",
      )
  )
  if final_turn.action != "final":
    raise ValueError("The LLM must return final after human input.")
  return final_turn.message


hitl_agent = Agent(
    model="gemini-3.5-flash-lite",
    name="hitl",
    description="Selects HITL when needed and answers after the response.",
    instruction=(
        "You are the decision-maker in a bounded human-in-the-loop workflow. "
        "Your input is JSON with original_request and, only after a pause, "
        "human_input. On the first turn: use action request_time_off for a "
        "time-off request and ask a question that expects a text answer; use "
        "action deploy_to_production for a production deployment request and "
        "ask an approval question; otherwise use action final and answer "
        "directly. When human_input is present, combine it with "
        "original_request, use action final, and put the complete answer in "
        "message. Never request a second pause."
    ),
    mode="single_turn",
    include_contents="none",
    output_schema=AgentTurn,
    output_key="temp:hitl_agent_turn",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)


root_agent = Workflow(
    name="hitl_workflow",
    edges=[("START", run)],
)
