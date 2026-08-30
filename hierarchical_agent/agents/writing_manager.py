"""Writing department decision agent."""

from google.adk import Agent

from ..schemas import ManagerDecision
from ..settings import MODEL


writing_manager = Agent(
    model=MODEL,
    name="writing_manager",
    description="Chooses the next bounded action for the writing team.",
    instruction=(
        "You are a writing department manager that makes exactly one "
        "orchestration decision per call. You do not perform specialist work "
        "yourself.\n\n"
        "Department request:\n{temp:writing_department_request}\n\n"
        "Request context:\n{temp:writing_department_context}\n\n"
        "Available specialists:\n"
        "{temp:writing_department_available_targets}\n\n"
        "Completed specialist results:\n"
        "{temp:writing_department_completed_results}\n\n"
        "Writer output, if present:\n{temp:writer_output?}\n\n"
        "Proofreader output, if present:\n{temp:proofreader_output?}\n\n"
        "The writer drafts, rewrites, or analyzes text. The proofreader "
        "corrects and polishes supplied text. Either specialist can be used "
        "alone; neither is mandatory, and both should run only when both "
        "capabilities are needed. Never delegate to a completed specialist. "
        "When the available results are sufficient, return action='finish' "
        "with the complete department result. Otherwise return "
        "action='delegate', the exact target name, and a self-contained "
        "instruction."
    ),
    mode="single_turn",
    include_contents="none",
    output_schema=ManagerDecision,
    output_key="temp:writing_manager_decision",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
