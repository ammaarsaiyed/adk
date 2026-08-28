# HITL workflow architecture (ADK 2)

This example uses the ADK 2.7.1 graph and node runtime. It does not use the
legacy sequential, loop, or parallel agent implementations.

```text
START -> run
           |-- hitl_agent@initial_decision
           |-- request_human_input@human_input -> pause/resume
           `-- hitl_agent@final_response
```

`run` is a deterministic orchestration node. It calls one schema-bound,
single-turn `hitl_agent` instance for the initial decision. If human input is
needed, it awaits `request_human_input`, then calls that same agent instance
again with both `original_request` and the validated `human_input`. Stable
`run_id` values make those three dynamic executions distinct and replayable.

## Is the two-call pattern standard?

No reviewed ADK 2 example matches this file line-for-line. The implementation
is a deliberate composition of documented ADK 2 patterns rather than a copied
canonical example, but the composition is not arbitrary:

- The official `request_input` workflow runs `draft_email`, pauses in a
  separate `RequestInput` node, and can route human feedback back to that same
  `draft_email` agent. It gives the repeated agent the original complaint and
  human feedback through explicit state.
- The official `dynamic_nodes` workflow puts repeated LLM calls under a
  deterministic `@node(rerun_on_resume=True)` controller using awaited
  `ctx.run_node()` calls.
- The official `request_input_advanced` workflow selects a Pydantic
  `response_schema`, validates the resumed human value, and passes that value
  to downstream processing.

This example combines those three ideas into the shortest bounded protocol for
the stated requirement. One model call decides whether input is required and
which schema to show. After the separate function node resumes, one further
call to the same agent produces the answer. A final-action check prevents a
third call or second pause.

## Why the original request is passed again

“The same LLM” here means the same `hitl_agent` definition and model, not the
same model call or a continuously running model process. The two calls have
different dynamic node identities:

```text
hitl@initial_decision  -> first independent model request
hitl@final_response    -> second independent model request
```

The agent deliberately uses `mode="single_turn"` and
`include_contents="none"`. Each call therefore receives only the
`node_input` supplied for that execution. Reusing the Python `Agent` object
does not give its later execution memory of an earlier execution, and the ADK
event log is not automatically copied into every model prompt. The final call
must consequently receive both values explicitly:

```json
{
  "original_request": "Can I deploy to production?",
  "human_input": {"approved": true}
}
```

This explicit hand-off is intentional. It makes the final answer depend on
exactly the original request and the validated HITL response, rather than on
implicit conversation history or branch visibility. The tests assert that
both values are present in the second request sent to the same fake model.

`request_human_input` is a leaf with `rerun_on_resume=False`. ADK therefore
does not execute it again after the pause; the human response becomes the
node's output and returns to `run`. Its `response_schema` is selected from the
first model decision:

- `ApprovalResponse.approved: bool` demonstrates a checkbox.
- `TextResponse.text: str` demonstrates a string field.

The orchestration node uses `rerun_on_resume=True` because it calls dynamic
children through `ctx.run_node()`. On resume, completed child calls are
rehydrated from the event history, the HITL response is returned from the leaf,
and only the final LLM turn is new. The controller validates both model turns,
validates the human response against the selected schema, and rejects a second
pause structurally by requiring the last action to be `final`.

## How this relates to the other ADK HITL pattern

The “Human-in-the-loop from an LLM agent” section of
`human-in-the-loop.md` describes a different topology. There, the LLM itself
calls a long-running or built-in `request_input` tool. ADK later injects the
human response as a `FunctionResponse` into that agent's model history, so the
agent can continue without the application rebuilding its input.

This example instead places a `RequestInput` function node between two bounded
LLM executions. That is why the response first returns to the `run` controller
and why the controller constructs the complete second input. This topology is
appropriate here because Python, rather than the model, owns:

- whether the UI receives the checkbox or string schema;
- validation of the corresponding human response;
- the guarantee that at most one pause occurs; and
- the exact context supplied for the final answer.

The LLM-owned tool pattern is also valid, but it is a different implementation
contract: it trades this deterministic two-node boundary for a conversational
agent that owns its tool call and continued history.

## References used

- The Google-provided `adk-agent-builder` references
  [human-in-the-loop.md](../.agents/skills/adk-agent-builder/references/human-in-the-loop.md),
  [function-nodes.md](../.agents/skills/adk-agent-builder/references/function-nodes.md),
  and [llm-agent-nodes.md](../.agents/skills/adk-agent-builder/references/llm-agent-nodes.md)
  define the resume flags, `RequestInput` output hand-off, dynamic calls, and
  schema-bound LLM-node behavior used here.
- The Google-provided `adk-architecture` references
  [architecture-checkpoint-resume.md](../.agents/skills/adk-architecture/references/architecture-checkpoint-resume.md)
  and [interface-workflow.md](../.agents/skills/adk-architecture/references/interface-workflow.md)
  describe event replay, dynamic-node identity, and why an orchestrator that
  calls `ctx.run_node()` must rerun after an interrupt.
- The ADK 2 official examples
  [request_input/agent.py](../../adk-references/adk-python/contributing/samples/workflows/request_input/agent.py),
  [request_input_advanced/agent.py](../../adk-references/adk-python/contributing/samples/workflows/request_input_advanced/agent.py)
  and
  [dynamic_nodes/agent.py](../../adk-references/adk-python/contributing/samples/workflows/dynamic_nodes/agent.py)
  provide the structured `RequestInput` leaf and rerunnable dynamic controller
  patterns respectively.
- The official
  [request_input_tool agent](../../adk-references/adk-python/contributing/samples/hitl/request_input_tool/agent.py)
  is the contrasting LLM-owned HITL pattern in which a function response is
  injected into one agent's conversation history.
- [08_dynamic.py](../../adk-references/adk-workflow-patterns/graph-workflows/examples/08_dynamic.py)
  reinforces the ADK 2 `@node(rerun_on_resume=True)` plus awaited
  `ctx.run_node()` orchestration pattern.
- [hierarchical-agent-implementation-plan.md](../hierarchical-agent-implementation-plan.md)
  supplies the local pattern of separating LLM decisions from deterministic
  control flow, validating every boundary, and assigning stable dynamic run
  identifiers.
