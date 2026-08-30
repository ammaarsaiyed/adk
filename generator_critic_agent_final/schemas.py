"""Structured inputs and outputs for the reflection agents."""

from typing import Literal

from pydantic import BaseModel
from pydantic import Field


class ReflectionAgentOutputDraft(BaseModel):
  """A generated draft, optionally paired with feedback for refinement."""

  draft: str = Field(description="The current response draft.")
  feedback: str = Field(
      default="",
      description="Critic feedback to apply when refining the draft.",
  )


class ReflectionAgentCriticOutput(BaseModel):
  """The critic's decision and actionable feedback."""

  decision: Literal["PASS", "REFINE"] = Field(
      description="PASS when the draft is ready, otherwise REFINE."
  )
  feedback: str = Field(
      default="",
      description="Specific revision guidance when the decision is REFINE.",
  )
