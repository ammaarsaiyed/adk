"""Tests for the final fixed-schema HITL workflow."""

from __future__ import annotations

from collections.abc import AsyncGenerator
import json
import unittest

from google.adk import Event
from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import Field

from hitl_final.agent import app
from hitl_final.agent import root_agent


class _FakeLlm(BaseLlm):
  """A deterministic model that returns one structured request plan."""

  model: str = "fake"
  response: LlmResponse
  requests: list[LlmRequest] = Field(default_factory=list)

  @classmethod
  def supported_models(cls) -> list[str]:
    return ["fake"]

  async def generate_content_async(
      self, llm_request: LlmRequest, stream: bool = False
  ) -> AsyncGenerator[LlmResponse, None]:
    del stream
    self.requests.append(llm_request)
    yield self.response


def _fake_plan_model(*, request_type: str, summary: str) -> _FakeLlm:
  plan = json.dumps({"request_type": request_type, "summary": summary})
  return _FakeLlm(
      response=LlmResponse(
          content=types.Content(
              role="model",
              parts=[types.Part.from_text(text=plan)],
          )
      )
  )


def _fake_response_model(response: str) -> _FakeLlm:
  return _FakeLlm(
      response=LlmResponse(
          content=types.Content(
              role="model",
              parts=[types.Part.from_text(text=response)],
          )
      )
  )


def _request_text(request: LlmRequest) -> str:
  return "".join(
      part.text or ""
      for content in request.contents
      for part in content.parts or []
  )


def _event_text(event: Event) -> str:
  if event.content is None:
    return ""
  return "".join(part.text or "" for part in event.content.parts or [])


class HitlFinalTest(unittest.IsolatedAsyncioTestCase):

  def setUp(self) -> None:
    workflow_agents = {
        node.name: node
        for node in root_agent.graph.nodes
        if isinstance(node, LlmAgent)
    }
    self._workflow_intake = workflow_agents["classify_request"]
    self._workflow_response = workflow_agents["respond_after_human_input"]
    self._original_intake_model = self._workflow_intake.model
    self._original_response_model = self._workflow_response.model

  def tearDown(self) -> None:
    self._workflow_intake.model = self._original_intake_model
    self._workflow_response.model = self._original_response_model

  async def _run_and_resume(
      self,
      *,
      user_message: str,
      request_type: str,
      summary: str,
      human_response: dict[str, bool | str],
      final_response: str,
  ) -> tuple[dict, _FakeLlm, list[Event]]:
    self._workflow_intake.model = _fake_plan_model(
        request_type=request_type,
        summary=summary,
    )
    response_model = _fake_response_model(final_response)
    self._workflow_response.model = response_model
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id="test_user",
    )
    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_message)],
    )
    paused = [
        event
        async for event in runner.run_async(
            user_id="test_user",
            session_id=session.id,
            new_message=message,
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
            user_id="test_user",
            session_id=session.id,
            new_message=response,
        )
    ]
    return function_call.args["response_schema"], response_model, resumed

  async def test_deployment_uses_boolean_input_and_continues(self) -> None:
    """Deployment approval is a checkbox and reaches the continuation."""
    final_response = "Release 1.4 was approved for production deployment."
    response_schema, response_model, events = await self._run_and_resume(
        user_message="Deploy release 1.4 to production.",
        request_type="deploy_to_production",
        summary="Deploy release 1.4 to production",
        human_response={"approved": True},
        final_response=final_response,
    )

    self.assertEqual(
        response_schema["properties"]["approved"]["type"], "boolean"
    )
    final_input = _request_text(response_model.requests[0])
    self.assertIn("Deploy release 1.4 to production", final_input)
    self.assertIn('"approved": true', final_input)
    self.assertTrue(
        any(final_response in _event_text(event) for event in events)
    )
    self.assertFalse(
        any(event.get_function_calls() for event in events),
        "the continuation must not request a second human response",
    )

  async def test_time_off_uses_string_input_and_continues(self) -> None:
    """Time-off details are a text field and reach the continuation."""
    final_response = "Your five-day family-event request has been recorded."
    response_schema, response_model, events = await self._run_and_resume(
        user_message="I need time off next month.",
        request_type="time_off",
        summary="Request time off next month",
        human_response={"details": "Five days for a family event"},
        final_response=final_response,
    )

    self.assertEqual(
        response_schema["properties"]["details"]["type"], "string"
    )
    final_input = _request_text(response_model.requests[0])
    self.assertIn("Request time off next month", final_input)
    self.assertIn("Five days for a family event", final_input)
    self.assertTrue(
        any(final_response in _event_text(event) for event in events)
    )
    self.assertFalse(
        any(event.get_function_calls() for event in events),
        "the continuation must not request a second human response",
    )


if __name__ == "__main__":
  unittest.main()
