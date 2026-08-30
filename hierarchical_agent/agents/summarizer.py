"""Research synthesis agent."""

from google.adk import Agent

from ..schemas import OutputPlaceholder
from ..settings import MODEL


summarizer = Agent(
    model=MODEL,
    name="summarizer",
    description="Synthesizes research into a concise answer.",
    instruction=(
        "Act as a research synthesizer.\n\n"
        "Department request:\n{temp:research_department_request}\n\n"
        "Assigned work:\n"
        "{temp:research_department_selected_instruction}\n\n"
        "Researcher output, if present:\n{temp:researcher_output?}\n\n"
        "Prior specialist results:\n"
        "{temp:research_department_completed_results}\n\n"
        "Produce the requested concise, coherent synthesis using only this "
        "explicit state. Complete the task in one turn and return an "
        "OutputPlaceholder, not a progress message."
    ),
    mode="single_turn",
    include_contents="none",
    output_schema=OutputPlaceholder,
    output_key="temp:summarizer_output",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
