"""Tests for the workflow-native orchestrator example."""

from __future__ import annotations

from collections.abc import AsyncGenerator
import json
import unittest

from google.adk import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import Field

from orchestrator_agent.agent import english_agent
from orchestrator_agent.agent import math_agent
from orchestrator_agent.agent import orchestrator
from orchestrator_agent.agent import root_agent
from orchestrator_agent.agent import science_agent


_LLM_AGENTS = (orchestrator, math_agent, science_agent, english_agent)


class _FakeLlm(BaseLlm):
  """A deterministic model that records requests and returns fixed replies."""

  model: str = "fake"
  responses: list[LlmResponse]
  response_index: int = -1
  requests: list[LlmRequest] = Field(default_factory=list)

  @classmethod
  def supported_models(cls) -> list[str]:
    return ["fake"]

  async def generate_content_async(
      self, llm_request: LlmRequest, stream: bool = False
  ) -> AsyncGenerator[LlmResponse, None]:
    del stream
    self.response_index += 1
    self.requests.append(llm_request)
    yield self.responses[self.response_index]


def _fake_model(*responses: str) -> _FakeLlm:
  return _FakeLlm(
      responses=[
          LlmResponse(
              content=types.Content(
                  role="model",
                  parts=[types.Part.from_text(text=response)],
              )
          )
          for response in responses
      ]
  )


def _decision(
    action: str,
    *,
    target: str | None = None,
    instruction: str = "",
    answer: str = "",
) -> str:
  return json.dumps(
      {
          "action": action,
          "target": target,
          "instruction": instruction,
          "answer": answer,
      }
  )


def _specialist_result(answer: str, *key_points: str) -> str:
  return json.dumps({"answer": answer, "key_points": list(key_points)})


def _request_text(request: LlmRequest) -> str:
  return "\n".join(
      part.text
      for content in request.contents
      for part in content.parts or []
      if part.text
  )


async def _run(text: str) -> list[Event]:
  app_name = "orchestrator_agent_test"
  user_id = "test_user"
  runner = InMemoryRunner(agent=root_agent, app_name=app_name)
  session = await runner.session_service.create_session(
      app_name=app_name, user_id=user_id
  )
  message = types.Content(
      role="user", parts=[types.Part.from_text(text=text)]
  )
  return [
      event
      async for event in runner.run_async(
          user_id=user_id,
          session_id=session.id,
          new_message=message,
      )
  ]


class OrchestratorAgentTest(unittest.IsolatedAsyncioTestCase):

  def setUp(self) -> None:
    self._original_models = {
        agent.name: agent.model for agent in _LLM_AGENTS
    }

  def tearDown(self) -> None:
    for agent in _LLM_AGENTS:
      agent.model = self._original_models[agent.name]

  async def test_selected_specialist_returns_to_orchestrator(self) -> None:
    """A selected specialist returns data and never owns the final response."""
    orchestrator_model = _fake_model(
        _decision(
            "delegate",
            target="science",
            instruction="Explain Rayleigh scattering.",
        ),
        _decision("finish", answer="Blue light scatters more strongly."),
    )
    science_model = _fake_model(
        _specialist_result(
            "Short wavelengths scatter more strongly.",
            "Rayleigh scattering scales approximately with 1/lambda^4.",
        )
    )
    math_model = _fake_model()
    english_model = _fake_model()
    orchestrator.model = orchestrator_model
    science_agent.model = science_model
    math_agent.model = math_model
    english_agent.model = english_model

    events = await _run("Why is the sky blue?")

    final_texts = [
        part.text
        for event in events
        if event.author == "orchestrator_agent"
        and event.content
        and event.content.parts
        for part in event.content.parts
        if part.text
    ]
    self.assertEqual(final_texts, ["Blue light scatters more strongly."])
    self.assertEqual(science_model.response_index, 0)
    self.assertEqual(math_model.response_index, -1)
    self.assertEqual(english_model.response_index, -1)
    self.assertIn(
        "Short wavelengths scatter more strongly.",
        _request_text(orchestrator_model.requests[1]),
    )
    self.assertFalse(any(event.get_function_calls() for event in events))
    self.assertFalse(
        any(event.actions.transfer_to_agent for event in events)
    )
    self.assertFalse(any(event.long_running_tool_ids for event in events))

  async def test_multiple_specialists_run_once_before_final_answer(self) -> None:
    """A mixed request can consult specialists sequentially and finish once."""
    orchestrator_model = _fake_model(
        _decision(
            "delegate",
            target="math",
            instruction="Calculate the photon energy.",
        ),
        _decision(
            "delegate",
            target="english",
            instruction="Rewrite the explanation for a child.",
        ),
        _decision("finish", answer="A polished combined explanation."),
    )
    math_model = _fake_model(
        _specialist_result("The photon energy is 3.97e-19 J.")
    )
    english_model = _fake_model(
        _specialist_result("Blue light bumps into air more often.")
    )
    orchestrator.model = orchestrator_model
    math_agent.model = math_model
    science_agent.model = _fake_model()
    english_agent.model = english_model

    events = await _run("Calculate and explain a blue photon's energy.")

    self.assertEqual(orchestrator_model.response_index, 2)
    self.assertEqual(math_model.response_index, 0)
    self.assertEqual(english_model.response_index, 0)
    final_events = [
        event
        for event in events
        if event.author == "orchestrator_agent" and event.content
    ]
    self.assertEqual(len(final_events), 1)

  async def test_completed_specialist_cannot_be_selected_again(self) -> None:
    """Selecting the same specialist twice fails at the workflow boundary."""
    orchestrator.model = _fake_model(
        _decision("delegate", target="math", instruction="Calculate it."),
        _decision("delegate", target="math", instruction="Calculate again."),
    )
    math_agent.model = _fake_model(_specialist_result("Four."))
    science_agent.model = _fake_model()
    english_agent.model = _fake_model()

    with self.assertRaisesRegex(ValueError, "completed agent 'math'"):
      await _run("What is two plus two?")


if __name__ == "__main__":
  unittest.main()
