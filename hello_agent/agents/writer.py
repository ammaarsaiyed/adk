"""Writing specialist agent."""

from google.adk import Agent

from ..schemas import OutputPlaceholder
from ..settings import MODEL


writer = Agent(
    model=MODEL,
    name="writer",
    description="Drafts or revises text for a writing objective.",
    instruction=(
        "Act as a writer.\n\n"
        "Department request:\n{temp:writing_department_request}\n\n"
        "Assigned work:\n"
        "{temp:writing_department_selected_instruction}\n\n"
        "Request context:\n{temp:writing_department_context}\n\n"
        "Prior specialist results:\n"
        "{temp:writing_department_completed_results}\n\n"
        "Produce the requested draft, rewrite, or analysis while preserving "
        "the user's meaning. Use only this explicit state. Complete the task "
        "in one turn and return an OutputPlaceholder, not a progress message."
    ),
    mode="single_turn",
    include_contents="none",
    output_schema=OutputPlaceholder,
    output_key="temp:writer_output",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
