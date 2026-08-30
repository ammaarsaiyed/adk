"""Feedback-driven draft refiner agent."""

from google.adk import Agent

from ..schemas import ReflectionAgentOutputDraft
from ..settings import MODEL


refiner_agent = Agent(
    model=MODEL,
    name="refiner",
    description="Revises a draft using the critic's feedback.",
    instruction=(
        "Original user prompt:\n{temp:original_user_prompt}\n\n"
        "Revise the supplied draft to answer the original user prompt, "
        "applying all of the supplied feedback. "
        "Preserve correct and useful material, fix the identified weaknesses, "
        "and return a complete standalone draft. Return only the structured "
        "draft output."
    ),
    mode="single_turn",
    include_contents="none",
    input_schema=ReflectionAgentOutputDraft,
    output_schema=ReflectionAgentOutputDraft,
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
