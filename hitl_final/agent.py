"""Minimal fixed-schema human-in-the-loop workflow."""

from __future__ import annotations

from typing import Literal

from google.adk import Agent
from google.adk import Workflow
from google.adk.apps import App
from google.adk.apps import ResumabilityConfig
from google.adk.events import RequestInput
from pydantic import BaseModel
from pydantic import Field


class RequestPlan(BaseModel):
  """A normalized request that determines which HITL form to show."""

  request_type: Literal["deploy_to_production", "time_off"] = Field(
      description="The supported request type."
  )
  summary: str = Field(
      min_length=1,
      description="A concise summary of the request for the human reviewer.",
  )


class DeploymentApproval(BaseModel):
  """Boolean response rendered as a deployment approval checkbox."""

  approved: bool = Field(description="Approve the production deployment.")


class TimeOffDetails(BaseModel):
  """String response rendered as a time-off details field."""

  details: str = Field(
      min_length=1,
      description="The dates, duration, reason, or other time-off details.",
  )


class FinalContext(BaseModel):
  """Complete input for the agent that runs after human input."""

  request: RequestPlan
  human_input: DeploymentApproval | TimeOffDetails


request_agent = Agent(
    name="classify_request",
    model="gemini-3.5-flash-lite",
    mode="single_turn",
    include_contents="none",
    instruction=(
        "Classify the user's request as deploy_to_production or time_off and "
        "produce a concise factual summary for a human reviewer. Do not ask "
        "questions, approve the request, or invent missing details."
    ),
    output_schema=RequestPlan,
    output_key="request_plan",
)


def request_human_input(node_input: RequestPlan) -> RequestInput:
  """Pause once with the code-owned schema for the classified request."""
  if node_input.request_type == "deploy_to_production":
    return RequestInput(
        interrupt_id="deployment_approval",
        message=f"Approve this production deployment?\n\n{node_input.summary}",
        payload=node_input,
        response_schema=DeploymentApproval,
    )

  return RequestInput(
      interrupt_id="time_off_details",
      message=(
          "Provide the missing time-off details, such as dates, duration, "
          f"and reason.\n\n{node_input.summary}"
      ),
      payload=node_input,
      response_schema=TimeOffDetails,
)


def build_final_context(
    node_input: DeploymentApproval | TimeOffDetails,
    request_plan: RequestPlan,
) -> FinalContext:
  """Combine the prior request and validated response for the final agent."""
  if request_plan.request_type == "deploy_to_production":
    if not isinstance(node_input, DeploymentApproval):
      raise TypeError("A deployment requires DeploymentApproval input.")
  elif request_plan.request_type == "time_off":
    if not isinstance(node_input, TimeOffDetails):
      raise TypeError("A time-off request requires TimeOffDetails input.")
  else:
    raise ValueError(
        f"Unsupported request type: {request_plan.request_type}"
    )

  return FinalContext(request=request_plan, human_input=node_input)


action_agent = Agent(
    name="respond_after_human_input",
    model="gemini-3.5-flash-lite",
    mode="single_turn",
    include_contents="none",
    instruction=(
        "Your input is JSON containing a normalized request and a validated "
        "human_input. Write a concise final response using both. For a "
        "deployment, state whether it was approved, but do not claim the "
        "deployment was executed. For time off, acknowledge the supplied "
        "details without inventing any. Never request more human input."
    ),
)


root_agent = Workflow(
    name="hitl_final",
    edges=[
        (
            "START",
            request_agent,
            request_human_input,
            build_final_context,
            action_agent,
        ),
    ],
)


app = App(
    name="hitl_final",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
