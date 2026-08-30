# HITL final pattern

This example demonstrates one fixed-schema human-in-the-loop boundary with two
UI inputs:

- a production deployment displays `approved: bool` as a checkbox;
- a time-off request displays `details: str` as a text field.

```text
user -> intake agent -> typed RequestInput -> human response
     -> build final context -> response agent
```

The workflow is deliberately a static chain. The agents before and after the
pause have separate, phase-specific instructions, and no node exists merely to
copy the human response into state.

## Why the workflow has this shape

### 1. The LLM has one bounded job

`intake_agent` converts the user message into `RequestPlan`, containing only a
supported request type and a factual summary. `output_schema=RequestPlan`
validates that boundary, while `output_key="request_plan"` records the result
for the downstream continuation.

The agent does not construct a JSON Schema, decide whether an approval was
granted, or handle both pre-input and post-input phases in one prompt.

### 2. Python owns the human-input contract

`request_human_input` selects `DeploymentApproval` or `TimeOffDetails` in
code. Both are Pydantic models passed through `RequestInput.response_schema`,
so the client receives the intended checkbox or text-field schema.

This follows the ADK [RequestInput guide](../../adk-references/adk-python/docs/guides/events/request_input/index.md),
which describes `RequestInput` as the workflow interrupt carrying a message,
payload, and response schema.

### 3. The resumed answer flows directly downstream

A plain function node defaults to `rerun_on_resume=False`. When its
`RequestInput` is resolved, the node completes and the human response becomes
the next node's `node_input`. The
[BaseNode guide](../../adk-references/adk-python/docs/guides/workflow/base_node/index.md#rerun_on_resume)
documents this behavior.

`build_final_context` therefore receives:

- `node_input`: the validated `DeploymentApproval` or `TimeOffDetails`;
- `request_plan`: the earlier structured agent output, resolved by name from
  workflow state.

It returns `FinalContext`, a typed object containing both values. This is a
meaningful boundary adapter: it validates that the response type matches the
request and supplies the next agent with one complete input. It does not store
or interpret the answer.

There is no `has_human_input` flag, serialized response string, response-store
node, dynamic child, or graph loop.

### 4. A separate agent runs after human input

`response_agent` always runs after the form has been answered. Its direct node
input is `FinalContext`, so its prompt never has to determine whether human
input exists. It has one job: combine the normalized request and authoritative
human response into a concise answer.

The two agents may use the same model, but they are intentionally separate
definitions. Reusing one agent would force its instruction and output contract
to cover incompatible pre-input and post-input phases.

In production, side effects should remain deterministic nodes. For example, an
approved deployment could route to a deployment operation before the response
agent summarizes the result. This example only demonstrates the HITL wiring.

### 5. Resumability belongs on the application

The exported `App` enables `ResumabilityConfig(is_resumable=True)`. ADK's
[App guide](../../adk-references/adk-python/docs/guides/apps/app/index.md#configuring-the-cross-cutting-features)
places durable pause/resume configuration on `App`, while services remain
runner concerns.

## Alignment with ADK examples

The closest reference is the official
[`request_input_advanced` example](../../adk-references/adk-python/contributing/samples/workflows/request_input_advanced/agent.py):

1. an agent produces typed output and stores it with `output_key`;
2. a function returns `RequestInput` with a Pydantic response schema;
3. the downstream function receives the human answer as `node_input` and the
   earlier request through a state-bound parameter;
4. a final-only agent receives their combined typed context as ordinary node
   input.

This example extends that same pattern from one time-off approval schema to
two form types. It intentionally does not copy the review loop from the
official [`request_input` example](../../adk-references/adk-python/contributing/samples/workflows/request_input/agent.py),
because deployment approval and missing time-off details require one bounded
response rather than iterative draft revision.

## What this example does not claim

This is the workflow-owned HITL pattern: Python owns the schemas and the
continuation. An agent-owned clarification using the built-in
`adk_request_input` tool is a separate pattern with different history and
control-flow semantics. Combining both in one template would obscure who owns
the interrupt.
