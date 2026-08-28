# HITL 3: single-form workflow loop

```text
user -> same LLM node -> typed RequestInput -> human response
                    ^                            |
                    +----------------------------+
                    -> final answer
```

The shared LLM node first selects one of two workflow branches:

- production deployment shows an `approved: bool` checkbox;
- time off shows a `text: str` field.

Only the `adk_request_input` form is resumable. After submission, the workflow
stores the validated response and loops to the same LLM node, which combines it
with the original request and produces the final answer.

The state handoff is intentional. The workflow owns the `RequestInput`, so its
function response is not the LLM agent's own tool response and is not
guaranteed to appear in that agent's model history. Supplying the stored
original request and human response in the next instruction makes the second
LLM turn deterministic.

## Why this is not a node tool

ADK 2 `NodeTool` is itself long-running. If that node also yields
`RequestInput`, ADK Web currently renders both the outer node-tool response box
and the inner typed `adk_request_input` form. Both can be submitted separately,
creating two contradictory continuations. That makes nested HITL node tools a
poor fit for this UI even though the runtime composition is supported.

This version instead follows the official ADK 2
[`request_input` workflow](../../adk-references/adk-python/contributing/samples/workflows/request_input/agent.py)
for the LLM loop and
[`request_input_advanced`](../../adk-references/adk-python/contributing/samples/workflows/request_input_advanced/agent.py)
for code-owned Pydantic response schemas.
