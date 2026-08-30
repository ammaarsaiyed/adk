"""Proofreading specialist agent."""

from google.adk import Agent

from ..schemas import OutputPlaceholder
from ..settings import MODEL


proofreader = Agent(
    model=MODEL,
    name="proofreader",
    description="Proofreads and polishes a draft.",
    instruction=(
        "Act as a proofreader.\n\n"
        "Department request:\n{temp:writing_department_request}\n\n"
        "Assigned work:\n"
        "{temp:writing_department_selected_instruction}\n\n"
        "Writer output, if present:\n{temp:writer_output?}\n\n"
        "Prior specialist results:\n"
        "{temp:writing_department_completed_results}\n\n"
        "Correct grammar, spelling, and clarity in the supplied text and "
        "return polished user-ready text. Use only this explicit state. "
        "Complete the task in one turn and return an OutputPlaceholder, not "
        "a progress message."
    ),
    mode="single_turn",
    include_contents="none",
    output_schema=OutputPlaceholder,
    output_key="temp:proofreader_output",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
