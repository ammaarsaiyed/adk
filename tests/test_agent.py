from __future__ import annotations

from collections.abc import AsyncGenerator
import json
import unittest

from google.adk import Agent
from google.adk import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.workflow import BaseNode
from google.genai import types
from pydantic import Field

from hello_agent.agent import root_agent
from hello_agent.agents.proofreader import proofreader
from hello_agent.agents.research_manager import research_manager
from hello_agent.agents.researcher import researcher
from hello_agent.agents.root_manager import root_manager
from hello_agent.agents.summarizer import summarizer
from hello_agent.agents.writer import writer
from hello_agent.agents.writing_manager import writing_manager


_LLM_AGENTS = (
    root_manager,
    research_manager,
    researcher,
    summarizer,
    writing_manager,
    writer,
    proofreader,
)
_AGENTS_BY_NAME = {agent.name: agent for agent in _LLM_AGENTS}


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
    target: str = "",
    instruction: str = "",
    result: str = "",
) -> str:
  return json.dumps(
      {
          "action": action,
          "target": target,
          "instruction": instruction,
          "result": result,
      }
  )


def _output(text: str, *details: str) -> str:
  return json.dumps({"text": text, "details": list(details)})


def _instruction_text(request: LlmRequest) -> str:
  instruction = request.config.system_instruction
  if isinstance(instruction, str):
    return instruction
  if not instruction or not instruction.parts:
    return ""
  return "".join(part.text or "" for part in instruction.parts)


async def _run_agent(agent: BaseNode, text: str) -> list[Event]:
  app_name = "hierarchical_agent_test"
  user_id = "test_user"
  runner = InMemoryRunner(agent=agent, app_name=app_name)
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


class HierarchicalAgentTest(unittest.IsolatedAsyncioTestCase):

  def setUp(self) -> None:
    self._original_models = {
        name: agent.model for name, agent in _AGENTS_BY_NAME.items()
    }

  def tearDown(self) -> None:
    for name, model in self._original_models.items():
      _AGENTS_BY_NAME[name].model = model

  def _configure_models(self, models: dict[str, _FakeLlm]) -> None:
    for agent in _LLM_AGENTS:
      agent.model = models.get(agent.name, _fake_model())

  async def test_only_selected_specialist_runs_and_workflow_terminates(
      self,
  ) -> None:
    """A writing request runs only the writer and renders one final answer."""
    root_model = _fake_model(
        _decision(
            "delegate",
            target="writing_department",
            instruction="Analyze the supplied phrase.",
        ),
        _decision("finish", result="Final analysis"),
    )
    writing_manager_model = _fake_model(
        _decision(
            "delegate",
            target="writer",
            instruction="Provide a linguistic analysis.",
        ),
        _decision("finish", result="Final analysis"),
    )
    writer_model = _fake_model(_output("Final analysis", "Analyzed"))
    proofreader_model = _fake_model()
    self._configure_models(
        {
            "root_manager": root_model,
            "writing_manager": writing_manager_model,
            "writer": writer_model,
            "proofreader": proofreader_model,
        }
    )

    events = await _run_agent(root_agent, "Analyze the quick brown fox.")

    final_texts = [
        part.text
        for event in events
        if event.author == "hello_agent"
        and event.content
        and event.content.parts
        for part in event.content.parts
        if part.text
    ]
    self.assertEqual(root_model.response_index, 1)
    self.assertEqual(writing_manager_model.response_index, 1)
    self.assertEqual(writer_model.response_index, 0)
    self.assertEqual(proofreader_model.response_index, -1)
    self.assertEqual(final_texts, ["Final analysis"])
    self.assertFalse(
        any(event.get_function_calls() for event in events)
    )
    self.assertFalse(
        any(event.actions.transfer_to_agent for event in events)
    )
    self.assertFalse(any(event.long_running_tool_ids for event in events))
    self.assertFalse(
        any(event.error_code or event.error_message for event in events)
    )

  async def test_manager_can_choose_proofreader_without_writer(self) -> None:
    """A correction request can run the proofreader as the only specialist."""
    writer_model = _fake_model()
    proofreader_model = _fake_model(_output("Corrected text", "Corrected"))
    self._configure_models(
        {
            "root_manager": _fake_model(
                _decision(
                    "delegate",
                    target="writing_department",
                    instruction="Correct the supplied text.",
                ),
                _decision("finish", result="Corrected text"),
            ),
            "writing_manager": _fake_model(
                _decision(
                    "delegate",
                    target="proofreader",
                    instruction="Correct grammar and spelling.",
                ),
                _decision("finish", result="Corrected text"),
            ),
            "writer": writer_model,
            "proofreader": proofreader_model,
        }
    )

    events = await _run_agent(root_agent, "Correct: The fox are quick.")

    self.assertEqual(writer_model.response_index, -1)
    self.assertEqual(proofreader_model.response_index, 0)
    self.assertFalse(
        any(event.error_code or event.error_message for event in events)
    )

  async def test_department_results_are_injected_into_later_instructions(
      self,
  ) -> None:
    """Research reaches writing through explicit state, not agent history."""
    writer_model = _fake_model(_output("Researched article", "Drafted"))
    self._configure_models(
        {
            "root_manager": _fake_model(
                _decision(
                    "delegate",
                    target="research_department",
                    instruction="Find fox facts.",
                ),
                _decision(
                    "delegate",
                    target="writing_department",
                    instruction="Write from the completed research.",
                ),
                _decision("finish", result="Researched article"),
            ),
            "research_manager": _fake_model(
                _decision(
                    "delegate",
                    target="researcher",
                    instruction="Find fox facts.",
                ),
                _decision("finish", result="Foxes are mammals."),
            ),
            "researcher": _fake_model(
                _output("Foxes are mammals.", "Verified fact")
            ),
            "writing_manager": _fake_model(
                _decision(
                    "delegate",
                    target="writer",
                    instruction="Write the researched article.",
                ),
                _decision("finish", result="Researched article"),
            ),
            "writer": writer_model,
        }
    )

    events = await _run_agent(
        root_agent, "Research foxes and write an article."
    )

    writer_instruction = _instruction_text(writer_model.requests[0])
    self.assertIn("Foxes are mammals.", writer_instruction)
    self.assertEqual(
        [
            part.text
            for event in events
            if event.author == "hello_agent"
            and event.content
            and event.content.parts
            for part in event.content.parts
            if part.text
        ],
        ["Researched article"],
    )

  def test_all_llm_agents_use_explicit_stateless_boundaries(self) -> None:
    """Every LLM agent disables history and publishes a named output."""
    for agent in _LLM_AGENTS:
      with self.subTest(agent=agent.name):
        self.assertEqual(agent.include_contents, "none")
        self.assertEqual(agent.mode, "single_turn")
        self.assertIsNotNone(agent.output_key)
        self.assertTrue(agent.output_key.startswith("temp:"))


if __name__ == "__main__":
  unittest.main()
