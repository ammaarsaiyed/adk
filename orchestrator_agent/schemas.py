"""Typed boundaries for orchestration decisions and specialist calls."""

from typing import Literal

from pydantic import BaseModel
from pydantic import Field


SpecialistName = Literal["math", "science", "english"]


class SpecialistResult(BaseModel):
  """A validated result returned by one specialist."""

  answer: str = Field(description="The specialist's answer to its task.")
  key_points: list[str] = Field(
      default_factory=list,
      description="Important facts, calculations, edits, or caveats.",
  )


class OrchestrationContext(BaseModel):
  """Everything the orchestrator needs for one routing decision."""

  request: str = Field(description="The user's original request.")
  remaining_agents: list[SpecialistName] = Field(
      description="Specialists that have not run during this invocation."
  )
  completed_results: dict[str, SpecialistResult] = Field(
      default_factory=dict,
      description="Validated results from specialists already consulted.",
  )


class OrchestratorDecision(BaseModel):
  """One bounded decision made by the main orchestrator."""

  action: Literal["delegate", "finish"]
  target: SpecialistName | None = Field(
      default=None,
      description="Specialist to call when action is delegate.",
  )
  instruction: str = Field(
      default="",
      description="A self-contained task for the selected specialist.",
  )
  answer: str = Field(
      default="",
      description="The final user-facing answer when action is finish.",
  )


class SpecialistTask(BaseModel):
  """The call payload sent from the workflow to a specialist."""

  request: str = Field(description="The user's original request.")
  instruction: str = Field(description="The specialist's focused task.")
  previous_results: dict[str, SpecialistResult] = Field(
      default_factory=dict,
      description="Useful results returned by earlier specialists.",
  )


class OrchestratorResult(BaseModel):
  """The final workflow output."""

  answer: str = Field(description="The synthesized answer for the user.")
  consulted_agents: list[SpecialistName] = Field(
      default_factory=list,
      description="Specialists whose results informed the answer.",
  )

