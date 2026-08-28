"""Research specialist agent."""

from google.adk import Agent

from ..schemas import OutputPlaceholder
from ..settings import MODEL


researcher = Agent(
    model=MODEL,
    name="researcher",
    description="Finds relevant facts for a research objective.",
    instruction=(
        "Act as a researcher.\n\n"
        "Department request:\n{temp:research_department_request}\n\n"
        "Assigned work:\n"
        "{temp:research_department_selected_instruction}\n\n"
        "Request context:\n{temp:research_department_context}\n\n"
        "Prior specialist results:\n"
        "{temp:research_department_completed_results}\n\n"
        "Identify the facts needed for the assigned work. Put the main "
        "findings in text and supporting facts or uncertainties in details. "
        "Use only this explicit state. Complete the task in one turn and "
        "return an OutputPlaceholder, not a progress message."
    ),
    mode="single_turn",
    include_contents="none",
    output_schema=OutputPlaceholder,
    output_key="temp:researcher_output",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
