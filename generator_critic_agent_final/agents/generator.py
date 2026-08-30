"""Initial-draft generator agent."""

from google.adk import Agent

from ..schemas import ReflectionAgentOutputDraft
from ..settings import MODEL


generator_agent = Agent(
    model=MODEL,
    name="generator",
    description="Creates a concise first draft for the user's request.",
    instruction=(
        "Create a useful first draft that directly answers the user's request. "
        "Keep it concise, complete, and grounded in the supplied information. "
        "Return only the structured draft output."
    ),
    mode="single_turn",
    include_contents="none",
    output_schema=ReflectionAgentOutputDraft,
    output_key="temp:current_draft",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
