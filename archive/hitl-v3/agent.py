"""Minimal ADK 2 HITL workflow with one visible input form."""

from __future__ import annotations

from typing import Literal

from google.adk import Agent
from google.adk import Event
from google.adk import Workflow
from google.adk.apps import ResumabilityConfig
from google.adk.apps.app import App
from google.adk.events import RequestInput
from pydantic import BaseModel
from pydantic import Field


class AgentTurn(BaseModel):
  """One structured turn from the shared LLM agent."""

  action: Literal[
      "final", "request_time_off", "deploy_to_production"
  ] = Field(description="The next workflow action.")
  message: str = Field(
      description="The question for the human or the final answer."
  )


class ApprovalResponse(BaseModel):
  """Response rendered as an approval checkbox."""

  approved: bool = Field(description="Approve the production deployment.")


class TextResponse(BaseModel):
  """Response rendered as a text input."""

  text: str = Field(description="The requested time-off details.")


def start(node_input: str) -> Event:
  """Store the original request and initialize the interaction."""
  return Event(
      state={
          "original_request": node_input,
          "has_human_input": False,
          "human_input": "",
      }
  )


def route_turn(node_input: AgentTurn) -> Event:
  """Route the LLM's structured decision."""
  return Event(output=node_input, route=node_input.action)


def request_deployment_approval(node_input: AgentTurn) -> RequestInput:
  """Show one boolean approval form."""
  return RequestInput(
      interrupt_id="deployment_approval",
      message=node_input.message,
      response_schema=ApprovalResponse,
  )


def store_deployment_approval(node_input: ApprovalResponse) -> Event:
  """Make the approval available to the next LLM turn."""
  return Event(
      state={
          "has_human_input": True,
          "human_input": node_input.model_dump_json(),
      }
  )


def request_time_off_details(node_input: AgentTurn) -> RequestInput:
  """Show one text response form."""
  return RequestInput(
      interrupt_id="time_off_details",
      message=node_input.message,
      response_schema=TextResponse,
  )


def store_time_off_details(node_input: TextResponse) -> Event:
  """Make the text response available to the next LLM turn."""
  return Event(
      state={
          "has_human_input": True,
          "human_input": node_input.model_dump_json(),
      }
  )


def emit_final(node_input: AgentTurn) -> str:
  """Emit the final LLM message as the workflow result."""
  return node_input.message


hitl_agent = Agent(
    name="hitl_model",
    model="gemini-3.5-flash-lite",
    instruction=(
        "You are the decision-maker in a bounded human-in-the-loop workflow. "
        "The original request is: {original_request}. Human input has been "
        "collected: {has_human_input}. The human input is: {human_input}. If "
        "human input has not been collected, use deploy_to_production for a "
        "production deployment and ask an approval question; use "
        "request_time_off for a time-off request and ask a useful text "
        "question; otherwise answer directly with final. If human input has "
        "been collected, combine it with the original request and always use "
        "final. Never request a second human response."
    ),
    output_schema=AgentTurn,
    output_key="agent_turn",
)


root_agent = Workflow(
    name="hitl_3",
    edges=[
        ("START", start, hitl_agent, route_turn),
        (
            route_turn,
            {
                "deploy_to_production": request_deployment_approval,
                "request_time_off": request_time_off_details,
                "final": emit_final,
            },
        ),
        (request_deployment_approval, store_deployment_approval, hitl_agent),
        (request_time_off_details, store_time_off_details, hitl_agent),
    ],
)


app = App(
    name="hitl_3",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
