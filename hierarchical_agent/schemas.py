"""Shared schemas for agent and workflow boundaries."""

from typing import Literal

from pydantic import BaseModel
from pydantic import Field


class InputPlaceholder(BaseModel):
  """Generic input contract shared by departments and workers."""

  text: str = Field(description="The task or source text to process.")
  context: dict[str, str] = Field(
      default_factory=dict,
      description="Named results or constraints from earlier work.",
  )


class OutputPlaceholder(BaseModel):
  """Generic output contract shared by departments and workers."""

  text: str = Field(description="The completed result.")
  details: list[str] = Field(
      default_factory=list,
      description="Supporting facts, changes, or caveats.",
  )


class ManagerDecision(BaseModel):
  """One bounded orchestration decision made by a hierarchy manager."""

  action: Literal["delegate", "finish"]
  target: str = Field(
      default="",
      description="Exact child name when action is delegate.",
  )
  instruction: str = Field(
      default="",
      description="The complete task to send to the selected worker.",
  )
  result: str = Field(
      default="",
      description="The final department result when action is finish.",
  )
