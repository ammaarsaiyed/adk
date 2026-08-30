"""Draft critic agent."""

from google.adk import Agent
from google.genai import types

from ..schemas import ReflectionAgentCriticOutput
from ..schemas import ReflectionAgentOutputDraft
from ..settings import CRITIC_MODEL


critic_agent = Agent(
    model=CRITIC_MODEL,
    name="critic",
    description="Checks a draft for correctness, relevance, and clarity.",
    instruction=(
        "Original user prompt:\n{temp:original_user_prompt}\n\n"
        "Review the supplied draft against the original user prompt. Choose "
        "PASS only when it is correct and fully meets all of the user's "
        "requirements. Otherwise choose REFINE "
        "and give concise, actionable feedback describing the most important "
        "changes. Return only the structured review output."
    ),
    mode="single_turn",
    include_contents="none",
    input_schema=ReflectionAgentOutputDraft,
    output_schema=ReflectionAgentCriticOutput,
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.HIGH,
            include_thoughts=True,
        )
    ),
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
