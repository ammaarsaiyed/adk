from __future__ import annotations

from collections.abc import AsyncGenerator
import json
import unittest

from google.adk import Event
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import Field

from generator_critic_agent_2.agent import MAX_REFINEMENTS
from generator_critic_agent_2.agent import root_agent


def _llm_node(name: str) -> LlmAgent:
  return next(
      node
      for node in root_agent.graph.nodes
      if isinstance(node, LlmAgent) and node.name == name
  )


generator_agent = _llm_node("generator")
critic_agent = _llm_node("critic")
refiner_agent = _llm_node("refiner")


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


def _fake_model(*responses: dict[str, str]) -> _FakeLlm:
  return _FakeLlm(
      responses=[
          LlmResponse(
              content=types.Content(
                  role="model",
                  parts=[types.Part.from_text(text=json.dumps(response))],
              )
          )
          for response in responses
      ]
  )


def _draft(text: str) -> dict[str, str]:
  return {"draft": text, "feedback": ""}


def _review(decision: str, feedback: str = "") -> dict[str, str]:
  return {"decision": decision, "feedback": feedback}


def _node_name(event: Event) -> str | None:
  if not event.node_info:
    return None
  return event.node_info.path.split("/")[-1].split("@")[0]


async def _run_agent(text: str) -> list[Event]:
  app_name = "generator_critic_2_test"
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


class GeneratorCriticAgent2Test(unittest.IsolatedAsyncioTestCase):

  def setUp(self) -> None:
    self._original_models = {
        generator_agent.name: generator_agent.model,
        critic_agent.name: critic_agent.model,
        refiner_agent.name: refiner_agent.model,
    }

  def tearDown(self) -> None:
    generator_agent.model = self._original_models[generator_agent.name]
    critic_agent.model = self._original_models[critic_agent.name]
    refiner_agent.model = self._original_models[refiner_agent.name]

  async def test_refined_draft_is_rechecked_before_rendering(self) -> None:
    """A refinement is rendered only after a later critic pass."""
    generator_model = _fake_model(_draft("first draft"))
    critic_model = _fake_model(
        _review("REFINE", "Make it precise."),
        _review("PASS"),
    )
    refiner_model = _fake_model(_draft("approved draft"))
    generator_agent.model = generator_model
    critic_agent.model = critic_model
    refiner_agent.model = refiner_model

    events = await _run_agent("Write a response.")

    rendered_text = [
        part.text
        for event in events
        if _node_name(event) == "render_result"
        and event.content
        and event.content.parts
        for part in event.content.parts
        if part.text
    ]
    rendered_output = [
        event.output
        for event in events
        if _node_name(event) == "render_result"
    ]
    self.assertEqual(rendered_text, ["approved draft"])
    self.assertEqual(rendered_output, [_draft("approved draft")])
    self.assertEqual(generator_model.response_index, 0)
    self.assertEqual(critic_model.response_index, 1)
    self.assertEqual(refiner_model.response_index, 0)

  async def test_unapproved_draft_fails_when_budget_is_exhausted(self) -> None:
    """Repeated refinement requests never leak an unapproved final draft."""
    generator_model = _fake_model(_draft("initial"))
    critic_model = _fake_model(
        *[
            _review("REFINE", "Try again.")
            for _ in range(MAX_REFINEMENTS + 1)
        ]
    )
    refiner_model = _fake_model(
        *[_draft(f"revision {index}") for index in range(MAX_REFINEMENTS)]
    )
    generator_agent.model = generator_model
    critic_agent.model = critic_model
    refiner_agent.model = refiner_model

    with self.assertRaisesRegex(RuntimeError, "did not approve"):
      await _run_agent("Write a response.")

    self.assertEqual(generator_model.response_index, 0)
    self.assertEqual(critic_model.response_index, MAX_REFINEMENTS)
    self.assertEqual(refiner_model.response_index, MAX_REFINEMENTS - 1)


if __name__ == "__main__":
  unittest.main()
