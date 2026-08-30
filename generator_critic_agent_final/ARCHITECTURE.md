# Declarative generator–critic–refiner workflow in ADK 2.7.1

**Status:** Implemented and deterministically validated  
**Application:** `generator_critic_agent_2/`

## Architecture decision

The application uses one declarative `Workflow`, three single-turn LLM agents,
and three small Python boundary and policy nodes:

- `extract_user_input` validates the request and initializes temporary state;
- `generator` creates the initial draft;
- `critic` returns `PASS` or `REFINE` with actionable feedback;
- `refiner` replaces a rejected draft using that feedback;
- `route_review` enforces the refinement budget and selects the next edge;
- `render_result` exposes only an approved draft as the final response.

```text
START -> extract_user_input -> generator -> critic -> route_review
                                      ^                    |
                                      |                    +-- PASS -> render_result
                                      +-- refiner <--------+-- REFINE
```

This is a **declarative graph workflow with a routed revision loop**. All legal
execution paths are declared in `Workflow.edges`; no node schedules another
node with `Context.run_node()`. The models own drafting, evaluation, and
revision. The graph and Python routing logic own sequencing, the hard budget,
and termination.

The design follows the official ADK
[`workflows/loop` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/loop/agent.py):
an evaluator produces a typed decision, a function node converts that decision
into an `Event.route`, and a routed edge forms a cycle back through the writing
stage. This implementation extends that recipe with a dedicated refiner, a
bounded iteration policy, and an explicit approved terminal output.

## Pattern classification

ADK supports both declarative graph workflows and dynamic workflows. This
application is a strong fit for the declarative form because its topology is
fixed and small:

- every initial draft goes to the critic;
- `PASS` always goes to the renderer;
- `REFINE` always goes to the refiner;
- every refined draft always returns to the critic;
- budget exhaustion always raises.

The critic decision changes which declared edge runs, but it never changes the
set or identity of nodes. That is conditional graph routing, not dynamic
workflow construction.

| Style | Best fit | This application |
| --- | --- | --- |
| [Graph workflow](https://adk.dev/graphs/) | Fixed stages and routes that remain understandable as explicit edges. | Best fit: all stages and transitions are known when the workflow is built. |
| [Dynamic workflow](https://adk.dev/graphs/dynamic/) | Runtime-selected children or iterative logic that becomes less clear as graph edges. | Unnecessary here: the bounded loop is one routed back-edge plus a counter. |

Using a graph makes the complete control flow visible at construction time and
lets ADK validate important topology and schema constraints before a run. It
also keeps tracing straightforward: repeated refinements are repeated static
node executions rather than children hidden beneath an orchestration node.

### Graph-workflow conformance

The implementation follows the relevant ADK 2 workflow rules:

| Rule | Implementation |
| --- | --- |
| A cycle must contain a routed edge. | `route_review --REFINE--> refiner` is conditional; `refiner -> critic` is unconditional. |
| Every outgoing edge from a routing node should be routed. | Both `PASS` and `REFINE` edges from `route_review` have explicit route values. |
| Adjacent typed nodes must have compatible schemas. | Generator/refiner output and critic input use `ReflectionAgentOutputDraft`; critic output and router input use `ReflectionAgentCriticOutput`. |
| Mid-graph LLM agents must not use chat mode. | All three agents use `mode="single_turn"`. |
| State changes must be event-backed. | Python nodes return `Event(state=...)`; LLM draft nodes use `output_key`, which writes to the event state delta. |
| Model decisions must be constrained. | The critic decision is `Literal["PASS", "REFINE"]`. |
| Terminal output must be serializable and visible. | `render_result` emits the draft as both `message` and a dumped `output`. |
| Loops need an application-level stopping policy. | `MAX_REFINEMENTS` bounds the cycle and exhaustion raises. |

## Implementation details

### Input boundary

`extract_user_input` rejects an empty request, forwards the trimmed prompt to
the generator, and initializes invocation-local state:

```text
temp:original_user_prompt = trimmed user input
temp:refinement_count     = 0
```

The generator receives the prompt as normal `node_input`. The critic and
refiner receive typed outputs from their immediate predecessors and read the
unchanged original prompt through `{temp:original_user_prompt}`.

The `temp:` prefix is appropriate because these values coordinate only the
current invocation and must not leak into a later user turn. ADK's temporary
state handling is defined by
[`BaseSessionService`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/sessions/base_session_service.py#L172-L220).

### Direct graph data flow

Most values remain on the graph's direct output channel:

```text
user prompt
  -> generator
  -> ReflectionAgentOutputDraft
  -> critic
  -> ReflectionAgentCriticOutput
  -> route_review
```

On `REFINE`, `route_review` combines the current draft with the critic feedback
and emits a `ReflectionAgentOutputDraft` payload for the refiner. The refiner's
output goes directly to the critic. On `PASS`, `route_review` emits the approved
draft to `render_result`.

This keeps state from becoming a general-purpose message bus. State is used
only where a value must survive replacement of the graph payload or coordinate
the bounded loop.

### Why `output_key` stores the current draft

The critic consumes a draft but replaces the direct graph payload with a
review. `route_review` therefore needs a second way to recover the exact draft
that the review describes. Both draft-producing agents declare:

```python
output_schema=ReflectionAgentOutputDraft,
output_key="temp:current_draft",
```

For an LLM agent used as a workflow node, ADK first validates the model text
against `output_schema`. It then performs two operations with the validated,
serializable dictionary:

1. assigns it to the node's output for the next graph edge;
2. assigns it to `ctx.actions.state_delta[output_key]` for later state reads.

That behavior is implemented by the ADK
[`LLM workflow wrapper`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_llm_agent_wrapper.py#L352-L380).
It means the generator and refiner can both forward their draft directly to the
critic while atomically updating `temp:current_draft` in event history.

A separate `remember_draft` function node would only receive the same validated
output, wrap it in `Event(state=...)`, and forward it again. That adds a graph
stage and trace event without adding validation, policy, or transformation.
`output_key` is the intended ADK mechanism and removes that redundant adapter.

`Event(state=...)` remains the correct mechanism for the Python nodes because
they own their return events. `output_key` is the corresponding native
mechanism for `LlmAgent`, whose response event is produced by ADK's wrapper.

The critic intentionally has no `output_key`. Its review is consumed
immediately by `route_review` and does not need to survive a later payload.
Persisting an unused duplicate would make state larger without simplifying any
edge or consumer.

### State ownership

| State key | Producer | Consumer | Reason |
| --- | --- | --- | --- |
| `temp:original_user_prompt` | `extract_user_input` via `Event(state=...)` | critic and refiner instructions | Preserves the original request independently of the changing draft. |
| `temp:current_draft` | generator and refiner via `output_key` | `route_review` | Retains the exact draft after the critic replaces the direct payload with its review. |
| `temp:refinement_count` | `extract_user_input`, then `route_review` via `Event(state=...)` | `route_review` | Enforces a deterministic upper bound on the graph cycle. |

All stored structured values are JSON-serializable dictionaries. When Python
needs typed access, it reconstructs `ReflectionAgentOutputDraft` with
`model_validate()` rather than treating session state as an untyped model
instance.

### Typed boundaries

Two Pydantic models define the workflow protocol:

```text
ReflectionAgentOutputDraft(draft, feedback="")
ReflectionAgentCriticOutput(decision="PASS" | "REFINE", feedback="")
```

The graph's typed edges are:

```text
generator -> critic:       ReflectionAgentOutputDraft
refiner -> critic:         ReflectionAgentOutputDraft
critic -> route_review:    ReflectionAgentCriticOutput
route_review -> refiner:   ReflectionAgentOutputDraft
route_review -> renderer:  ReflectionAgentOutputDraft
```

The generator and refiner use `output_schema`; the critic uses both
`input_schema` and `output_schema`; and the Python functions acquire their
schemas from type annotations. ADK validates statically known adjacent schemas
when constructing the graph and validates model output at runtime.

All LLM agents use `mode="single_turn"` and `include_contents="none"`. Each
call is therefore a bounded typed transformation based on its explicit input
and required state, not a shared conversation or agent-transfer hierarchy.

### Routing and termination

`route_review` is deliberately the only node that decides control flow. Its
behavior is equivalent to:

```python
draft = state["temp:current_draft"]

if review.decision == "PASS":
  route PASS with draft

if refinement_count >= MAX_REFINEMENTS:
  raise RuntimeError

increment refinement_count
route REFINE with draft + review.feedback
```

The routing map is the same pattern used by the official loop sample:

```python
(route_review, {"PASS": render_result, "REFINE": refiner_agent})
(refiner_agent, critic_agent)
```

Keeping the budget check in the router is justified because it is part of the
transition policy: the router already interprets the review, chooses the next
edge, and owns the only point at which another refinement may be authorized.
A separate counter or failure node would split one policy across multiple
places without improving reuse or clarity.

`MAX_REFINEMENTS = 5` permits five calls to the refiner. The critic evaluates
the initial draft and every revision, so the maximum is:

```text
1 generator + 6 critic calls + 5 refiner calls = 12 LLM calls
```

The central invariant is:

> Every successfully returned draft has been approved by the critic.

The refiner is never terminal. Its only outgoing edge returns to the critic.
If the critic requests another refinement after the fifth revision,
`route_review` raises rather than rendering an unapproved draft. This prevents
the common off-by-one error where the final permitted revision bypasses review.

### Terminal output

`render_result` is the only terminal node. It receives a draft only through the
`PASS` route and emits:

- `Event.message` for the human-visible response;
- `Event.output` for the workflow's structured, serializable result.

Keeping rendering separate from routing makes the success boundary explicit
and prevents critic JSON or internal feedback from becoming the final UI
response. The official ADK
[`message` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/message/agent.py)
demonstrates the same distinction between visible content and node output.

## Why this implementation is the right level of complexity

The workflow contains only nodes that add a distinct responsibility:

| Node | Responsibility that cannot be expressed by an edge alone |
| --- | --- |
| `extract_user_input` | Validate input and initialize invocation-local state. |
| `generator` | Produce the first typed draft. |
| `critic` | Produce the typed quality decision and feedback. |
| `route_review` | Enforce budget and translate the decision into a route and next payload. |
| `refiner` | Apply feedback to the current draft. |
| `render_result` | Publish only an approved result to both UI and workflow output. |

There is no orchestration node, storage adapter, manual run ID, or duplicated
agent wrapper. The graph itself expresses order and repetition. `output_key`
handles the one value that must outlive its direct edge, and `route_review`
contains the small amount of application policy that edges cannot express by
themselves.

Compared with the dynamic v1 implementation, this version trades controller-
local variables for three explicit temporary state keys. For this fixed loop,
that trade is worthwhile: the topology is visible, ADK can validate it, and
the state roles are narrow and documented. A dynamic controller would become
preferable only if future requirements introduced runtime-selected nodes,
several nested loop types, or branching logic that no longer reads clearly as
a small graph.

## Differences from the official loop sample

The official sample intentionally demonstrates the minimum loop mechanism.
This implementation preserves that mechanism while adding production-oriented
contracts:

| Official sample | This implementation | Reason |
| --- | --- | --- |
| Routes a failed evaluation back to the generator. | Routes to a dedicated refiner, then back to the critic. | Separates initial drafting from feedback-driven revision. |
| Stores evaluator feedback for the next generator call. | Stores the current draft using `output_key`; passes feedback directly through the router. | Only persist values that must survive replacement of the direct payload. |
| Ends when no route matches. | Routes `PASS` to an explicit renderer. | Returns the exact approved draft as visible and structured output. |
| Has no application-level iteration bound. | Allows five refinements and raises on exhaustion. | Prevents an unbounded model-driven cycle. |
| Uses a simple demonstration schema. | Uses typed draft and critic schemas at every LLM boundary. | Makes routing and refinement inputs validated contracts. |

These additions strengthen termination and output correctness without changing
the official sample's core architectural pattern.

Legacy `LoopAgent`, `SequentialAgent`, and `ParallelAgent` composition is not
used. Current ADK graph workflows express the sequence and loop directly with
`Workflow.edges`.

## Reference mapping

| Choice | Reference | Justification |
| --- | --- | --- |
| Declarative root graph | [`Workflow`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_workflow.py#L145-L220) | Schedules static nodes, re-triggers nodes on back-edges, and delegates terminal output. |
| Routed revision cycle | [`workflows/loop`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/loop/agent.py) | Canonical current recipe for evaluator-driven graph repetition. |
| Route map | [`Graph`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_graph.py#L95-L180) | Selects declared successors from `Event.route`. |
| Typed graph edges | [`graph validation`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/utils/_graph_validation.py#L160-L180) | Rejects known incompatible adjacent schemas at construction time. |
| Draft persistence | [`LLM workflow wrapper`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_llm_agent_wrapper.py#L352-L380) | Validates output, forwards it, and records `output_key` in the event state delta. |
| Typed output | [`BaseNode`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_base_node.py#L100-L185) | Normalizes and validates node input and output. |
| Terminal renderer | [`message` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/message/agent.py) | Separates visible content from structured workflow output. |

## Constraints and validation

- Generator and refiner outputs must retain
  `output_key="temp:current_draft"` while the router reads that state key.
- Every refiner execution must be followed by the critic.
- Both outgoing edges from `route_review` must remain explicitly routed.
- Only the `PASS` route may reach `render_result`.
- The hard refinement budget must remain enforceable before another refiner
  call is authorized.
- Terminal `Event.output` and all state values must remain JSON-serializable.
- `temp:` state is invocation-local. Adding tools or human-in-the-loop pauses
  requires a separate review of replay and idempotency behavior.

`tests/test_generator_critic_agent_2.py` uses scripted models with the public
`InMemoryRunner`. It verifies that a refinement is re-criticized before it can
be rendered and that repeated `REFINE` decisions fail when the budget is
exhausted instead of leaking an unapproved draft.

```text
.venv/bin/python -m pytest -q tests/test_generator_critic_agent_2.py
```

The targeted suite passes 2 tests. Running the v1 and v2 workflow tests
together passes 4 tests; the only warning is an unrelated ADK deprecation
warning for `BaseAgentConfig`.
