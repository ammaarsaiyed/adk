"""Root hierarchy decision agent."""

from google.adk import Agent

from ..schemas import ManagerDecision
from ..settings import MODEL


root_manager = Agent(
    model=MODEL,
    name="root_manager",
    description="Selects only the departments needed for the user request.",
    instruction=(
        "You are the root orchestration manager. Make exactly one routing "
        "decision and do not call tools or perform a department's specialist "
        "work yourself.\n\n"
        "Original request:\n{temp:root_request}\n\n"
        "Available departments:\n{temp:root_available_targets}\n\n"
        "Completed department results:\n"
        "{temp:root_completed_results}\n\n"
        "Direct research output, if present:\n"
        "{temp:research_department_output?}\n\n"
        "Direct writing output, if present:\n"
        "{temp:writing_department_output?}\n\n"
        "Delegate to research_department only when fact-finding or synthesis "
        "is genuinely required. Delegate to writing_department only when "
        "drafting, rewriting, analysis, correction, or proofreading is "
        "required. A request may need one department, both in sequence, or "
        "neither. Never delegate to a completed department. When existing "
        "results are sufficient, return action='finish' and put the complete "
        "user-facing answer in result. Otherwise return action='delegate', "
        "the exact target name, and a self-contained instruction."
    ),
    mode="single_turn",
    include_contents="none",
    output_schema=ManagerDecision,
    output_key="temp:root_decision",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
