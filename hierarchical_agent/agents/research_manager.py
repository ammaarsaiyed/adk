"""Research department decision agent."""

from google.adk import Agent

from ..schemas import ManagerDecision
from ..settings import MODEL


research_manager = Agent(
    model=MODEL,
    name="research_manager",
    description="Chooses the next bounded action for the research team.",
    instruction=(
        "You are a research department manager that makes exactly one "
        "orchestration decision per call. You do not perform specialist work "
        "yourself.\n\n"
        "Department request:\n{temp:research_department_request}\n\n"
        "Request context:\n{temp:research_department_context}\n\n"
        "Available specialists:\n"
        "{temp:research_department_available_targets}\n\n"
        "Completed specialist results:\n"
        "{temp:research_department_completed_results}\n\n"
        "Researcher output, if present:\n{temp:researcher_output?}\n\n"
        "Summarizer output, if present:\n{temp:summarizer_output?}\n\n"
        "The researcher finds facts and identifies uncertainty. The "
        "summarizer synthesizes existing research; it is not mandatory. "
        "Delegate only to a specialist that is genuinely needed, and never "
        "delegate to a completed specialist. When the available results are "
        "sufficient, return action='finish' with the complete department "
        "result. Otherwise return action='delegate', the exact target name, "
        "and a self-contained instruction."
    ),
    mode="single_turn",
    include_contents="none",
    output_schema=ManagerDecision,
    output_key="temp:research_manager_decision",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
