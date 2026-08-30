"""Standalone ADK 2 agent with LLM-owned human input."""

from google.adk import Agent
from google.adk.tools import request_input


root_agent = Agent(
    name="hitl_2",
    model="gemini-3.5-flash-lite",
    instruction="""
You are a simple human-in-the-loop assistant.

If the user asks to deploy to production, call `adk_request_input` once with
the message "Approve the production deployment?" and this response schema:
{
  "type": "object",
  "properties": {
    "approved": {
      "type": "boolean",
      "description": "Approve the production deployment"
    }
  },
  "required": ["approved"]
}

If the user asks for time off, call `adk_request_input` once with a useful
question and this response schema:
{
  "type": "object",
  "properties": {
    "text": {
      "type": "string",
      "description": "The user's answer"
    }
  },
  "required": ["text"]
}

After the tool response arrives, answer using the original request and the
human response already present in your conversation. Do not request input a
second time. For all other requests, answer directly without calling the tool.
""",
    tools=[request_input],
)
