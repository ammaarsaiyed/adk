"""Tests for the ADK 2 HITL workflow example."""

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

from hitl.agent import hitl_agent
from hitl.agent import root_agent


class _FakeLlm(BaseLlm):
  """A deterministic model that records its two workflow turns."""

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


def _fake_model(*turns: dict[str, str]) -> _FakeLlm:
  return _FakeLlm(
      responses=[
          LlmResponse(
              content=types.Content(
                  role="model",
                  parts=[types.Part.from_text(text=json.dumps(turn))],
              )
          )
          for turn in turns
      ]
  )


def _request_text(request: LlmRequest) -> str:
  return "".join(
      part.text or ""
      for content in request.contents
      for part in content.parts or []
  )


class HitlAgentTest(unittest.IsolatedAsyncioTestCase):

  def setUp(self) -> None:
    self._original_model = hitl_agent.model

  def tearDown(self) -> None:
    hitl_agent.model = self._original_model

  async def _run_and_resume(
      self,
      *,
      user_message: str,
      first_action: str,
      question: str,
      human_response: dict[str, bool | str],
      final_message: str,
  ) -> tuple[_FakeLlm, dict, list[Event]]:
    model = _fake_model(
        {"action": first_action, "message": question},
        {"action": "final", "message": final_message},
    )
    hitl_agent.model = model

    app_name = f"hitl_{first_action}_test"
    user_id = "test_user"
    runner = InMemoryRunner(agent=root_agent, app_name=app_name)
    session = await runner.session_service.create_session(
        app_name=app_name, user_id=user_id
    )
    initial_message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_message)],
    )
    paused = [
        event
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=initial_message,
        )
    ]
    function_calls = [
        function_call
        for event in paused
        for function_call in event.get_function_calls()
        if function_call.name == "adk_request_input"
    ]
    self.assertEqual(len(function_calls), 1)
    function_call = function_calls[0]

    response = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=function_call.id,
                    name=function_call.name,
                    response=human_response,
                )
            )
        ],
    )
    resumed = [
        event
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=response,
        )
    ]
    return model, function_call.args["response_schema"], resumed

  async def test_checkbox_response_reaches_same_llm_with_original_input(
      self,
  ) -> None:
    model, response_schema, events = await self._run_and_resume(
        user_message="Can I deploy the release to production?",
        first_action="deploy_to_production",
        question="Approve the production deployment?",
        human_response={"approved": True},
        final_message="The production deployment was approved.",
    )

    self.assertEqual(
        response_schema["properties"]["approved"]["type"], "boolean"
    )
    final_input = _request_text(model.requests[1])
    self.assertIn("Can I deploy the release to production?", final_input)
    self.assertIn('"approved":true', final_input)
    self.assertTrue(
        any(
            event.output == "The production deployment was approved."
            for event in events
        )
    )

  async def test_text_response_reaches_same_llm_with_original_input(
      self,
  ) -> None:
    model, response_schema, events = await self._run_and_resume(
        user_message="I would like to take time off next month.",
        first_action="request_time_off",
        question="How many days should be requested?",
        human_response={"text": "Five days"},
        final_message="Your request for five days off is ready.",
    )

    self.assertEqual(
        response_schema["properties"]["text"]["type"], "string"
    )
    final_input = _request_text(model.requests[1])
    self.assertIn("I would like to take time off next month.", final_input)
    self.assertIn('"text":"Five days"', final_input)
    self.assertTrue(
        any(
            event.output == "Your request for five days off is ready."
            for event in events
        )
    )


if __name__ == "__main__":
  unittest.main()
