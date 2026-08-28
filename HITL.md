# Human-in-the-loop patterns in ADK 2

## Purpose

The requirement is a bounded interaction:

1. The user makes an original request.
2. The LLM decides whether human input is needed.
3. The UI displays either a boolean checkbox or a string field.
4. The human submits one response.
5. The same LLM definition produces a final answer using both inputs.

The important ADK distinction is who owns step 3. A workflow can emit
`RequestInput`, or an LLM agent can call the built-in `adk_request_input` tool.
That decision determines how the human response reaches the final LLM call.

## ADK concepts needed for the comparison

- An LLM is stateless between calls. Reusing the same model or `Agent`
  definition does not itself preserve the original request.
- Session events form the persisted audit record, but the model receives a
  filtered, branch-specific request assembled from those events—not the raw
  event stream.
- Workflow-owned input must therefore be handed to the next LLM call
  explicitly, either as node input or workflow state.
- Agent-owned input is different: the LLM makes the tool call, so ADK can pair
  the human `FunctionResponse` with that call when assembling the agent's next
  model request.
- `RequestInput.response_schema` controls the typed form. A Pydantic boolean
  produces the checkbox and a Pydantic string produces the text field.

Abridged schema-owned form nodes look like this:

```python
class ApprovalResponse(BaseModel):
  approved: bool


class TextResponse(BaseModel):
  text: str


def request_human_input(turn):
  schema = (
      ApprovalResponse
      if turn.action == "deploy_to_production"
      else TextResponse
  )
  return RequestInput(message=turn.message, response_schema=schema)
```

## The three implementations

There are three implementations, but only two architectural families:

| Option | Pause owner | How the final LLM receives context | Schema owner |
|---|---|---|---|
| 1. Imperative workflow (`hitl`) | Workflow controller | Complete input passed directly to the second LLM call | Python/Pydantic |
| 2. Agent-owned input (`hitl_2`) | LLM tool call | ADK assembles the original request and matching tool response into the next model request | LLM-generated JSON Schema |
| 3. Declarative workflow (`hitl_3`) | Static workflow graph | Original request and response stored in state and injected into the second instruction | Python/Pydantic |

Options 1 and 3 are two ways to implement the same workflow-owned pattern.
Option 2 is the genuinely different agent-owned pattern.

## Option 1: imperative workflow controller

```text
controller -> LLM decision -> typed RequestInput -> validated response
           -> same LLM definition with complete combined input -> final
```

One rerunnable Python node owns the complete interaction. It invokes the LLM,
pauses through a child input node, validates the response, and invokes the same
LLM definition again:

```python
@node(rerun_on_resume=True)
async def run(ctx, node_input):
  original_request = node_input
  decision = await ctx.run_node(
      hitl_agent, node_input=original_request, run_id="initial_decision"
  )
  human_input = await ctx.run_node(
      request_human_input, node_input=decision, run_id="human_input"
  )
  return await ctx.run_node(
      hitl_agent,
      node_input={
          "original_request": original_request,
          "human_input": human_input,
      },
      run_id="final_response",
  )
```

The production form of this pattern uses structured Pydantic input and output
rather than the abbreviated dictionaries above. Both LLM executions are
independent single turns, so the controller deliberately supplies everything
the second turn needs.

Strengths:

- Code selects and validates the boolean or string schema.
- Stable child run IDs make replay deterministic.
- Code permits at most one pause and requires the second decision to be final.
- The final model request does not depend on implicit event-to-context rules.

Trade-off: this is a project-specific composition of standard ADK primitives,
so it contains more controller logic than the official static graph examples.

## Option 2: agent-owned `request_input`

```text
user -> agent -> adk_request_input -> human FunctionResponse
     -> tool result returned to the same agent -> final
```

The LLM owns the pause by calling ADK's built-in long-running input tool:

```python
root_agent = Agent(
    name="hitl_agent",
    model="gemini-...",
    instruction=(
        "When input is missing, call adk_request_input with a message and "
        "the required JSON response_schema. After it returns, answer using "
        "the original request and returned tool result."
    ),
    tools=[request_input],
)
```

No workflow needs to resend the original request. ADK can correlate the tool
response with the LLM's own `adk_request_input` call and assemble both the
original request and returned value into the next model request.

Strengths:

- Smallest implementation for model-directed clarification.
- Directly matches ADK's official `request_input_tool` sample.
- Best when the model discovers missing fields dynamically.

Trade-off: this option is non-deterministic by design. At inference time the
model decides whether to call the tool, constructs its JSON Schema arguments,
and decides whether to call it again after receiving a result. The same input
can therefore lead to a skipped form, the wrong schema, or repeated requests.
Prompt instructions and a low temperature can reduce that risk but cannot
provide a code-level guarantee. For two fixed contracts, this is weaker than
code-owned Pydantic schemas and a code-enforced pause limit.

## Option 3: declarative workflow loop

```text
start -> same LLM node -> route -> typed RequestInput -> store response
                    ^                              |
                    +------------------------------+
                    -> final route -> output
```

The workflow expresses the interaction as static nodes and routed edges:

```python
root_agent = Workflow(
    name="hitl_workflow",
    edges=[
        ("START", store_original, hitl_agent, route_turn),
        (route_turn, {
            "deploy": request_approval,
            "time_off": request_text,
            "final": emit_final,
        }),
        (request_approval, store_approval, hitl_agent),
        (request_text, store_text, hitl_agent),
    ]
)
```

The first LLM result chooses the branch. After the human submits the single
typed form, a function node stores the validated response and the graph loops
to the same LLM node. Its instruction includes values such as
`{original_request}` and `{human_input}` from workflow state.

That state handoff is intentional. The workflow—not the LLM—owns
`RequestInput`, so its `FunctionResponse` is not guaranteed to be included in
the LLM node's next model request. `include_contents` alone is not a reliable
replacement for the explicit state handoff.

Strengths:

- Code owns the Pydantic response schemas.
- The topology is visible and easy to extend with routes or review steps.
- It most closely mirrors ADK's official `request_input` graph loop and
  `request_input_advanced` typed-schema sample.

Trade-off: the graph appears deterministic, but its most important transition
is still prompt-controlled. The same LLM output schema permits `final`,
`deploy`, and `time_off` on both calls. On the second call, state values such as
`has_human_input=True` and serialized `human_input` are interpolated into the
instruction, and the model is told to choose `final`. The graph trusts that
choice; it does not prevent the model from selecting another input route.

Using prompt text as the phase controller is less robust than enforcing the
phase in Python. It also asks the model to interpret orchestration flags and a
serialized response instead of receiving a purpose-built final-response input
contract. A stronger production graph would use code to force the post-input
route, or use a separate final-only LLM node/schema. Option 1 already enforces
that boundary in its controller.

## Why `NodeTool` containing `RequestInput` is not recommended for ADK Web

This pattern places a HITL node inside an LLM tool call:

```text
agent -> NodeTool (long-running) -> RequestInput (long-running) -> human
```

This is valid ADK runtime composition and is demonstrated by the official
`node_as_tool` sample. It is not recommended for this UI because of behavior
observed in ADK Web with ADK 2.7.1, not because the runtime architecture is
invalid.

With ADK 2.7.1, ADK Web renders two independently answerable controls:

1. A generic response box for the outer long-running `NodeTool` call.
2. The intended typed `adk_request_input` form from the inner node.

Submitting the outer box could resume the LLM while the inner checkbox or text
form remained pending. Submitting that form later created a second,
potentially contradictory continuation for the same request. This violated
the requirement for exactly one clear human response.

A custom client could still use this architecture by displaying and resuming
only the inner `adk_request_input` interrupt. It is not a safe ADK Web template
under the tested behavior without that client-side filtering.

## Resume and production considerations

- A simple `RequestInput` leaf normally uses `rerun_on_resume=False`; the
  resolved human response becomes its downstream output.
- An imperative controller that calls dynamic child nodes uses
  `rerun_on_resume=True` so it can replay and recover the interrupted child.
- Stable node and interrupt identities make resumed execution auditable and
  predictable.
- Production workflows should be exported through an `App` with resumability
  enabled so a paused invocation can continue durably across runner calls.

```python
app = App(
    name="hitl_app",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
```

## Reference basis

The assessment uses these ADK 2 patterns:

- Official `request_input` workflow: an LLM/workflow review loop.
- Official `request_input_advanced` workflow: Pydantic `response_schema` on
  `RequestInput`.
- Official `dynamic_nodes` workflow: a rerunnable controller using
  `ctx.run_node()`.
- Official `request_input_tool` sample: agent-owned dynamic input.
- Official `node_as_tool` sample: a node tool that can yield `RequestInput`.
- ADK runtime rules for node replay, `rerun_on_resume`, event persistence,
  branch-filtered LLM context, and resumable applications.

No ADK source declares one universal HITL shape as the recommended solution;
the correct choice depends on whether the workflow or the LLM agent should own
the pause and response contract.

## Final choice

**Strongest production design for this fixed checkbox/string requirement:**
Option 1, the imperative workflow controller, with resumable `App`
configuration. It provides code-owned schemas, an explicit complete second LLM
input, and a deterministic one-pause/two-call boundary.

**Best aligned with the official fixed-schema workflow examples:** Option 3,
the declarative workflow loop. It directly combines the official graph-loop
and Pydantic response-schema patterns with the least project-specific wiring.

**Best for genuinely dynamic clarification:** Option 2, the agent-owned tool.
It is the simplest implementation, but it deliberately accepts
non-deterministic schema construction and continuation in exchange for that
flexibility.
