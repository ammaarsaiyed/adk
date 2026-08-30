# Generator–critic–refiner workflow in ADK 2.7.1

**Status:** Implemented and deterministically validated  
**Application:** `generator_critic_agent/`

## Architecture decision

The application uses a root `Workflow` with one bounded Python controller and
three single-turn LLM agents:

- `generator` creates the initial draft;
- `critic` returns `PASS` or `REFINE` with feedback;
- `refiner` replaces a rejected draft using that feedback.

```text
START -> extract_user_input -> generator_critic_loop -> render_result
                                 |
                                 +-> generator
                                 +-> critic -- PASS -----------------+
                                      |                              |
                                      +-- REFINE -> refiner -> critic+
```

The LLMs own writing and evaluation. Python owns execution order, schema
validation, the refinement budget, and termination. This prevents the model
from skipping review, repeating indefinitely, or returning an unreviewed final
revision.

The wiring follows the official ADK
[`dynamic_nodes` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/dynamic_nodes/agent.py):
a rerunnable Python node awaits LLM nodes through `Context.run_node()` and uses
ordinary control flow to decide whether another child call is required.

## Pattern classification

This application is a **hybrid dynamic workflow**, which is the recommended
ADK pattern for this particular control-flow contract:

- the root `Workflow` is a small declarative graph that provides the
  application boundary and terminal output;
- `generator_critic_loop` is a dynamic orchestrator node because it schedules
  children with `ctx.run_node()` and decides at runtime how many times they run;
- `generator`, `critic`, and `refiner` are single-turn leaf nodes that perform
  typed transformations and do not control the workflow;
- ordinary Python, rather than an LLM supervisor, owns branching, the iteration
  limit, failure, and successful termination.

Here, **dynamic** does not mean nondeterministic or model-directed. The models
produce drafts and judgments, but the set of legal transitions is fixed in
Python. A `REFINE` decision can only call the refiner and then the critic; a
`PASS` decision can only return the reviewed draft; exhaustion can only fail.

ADK provides two complementary current workflow styles:

| Style | Best fit | This application |
| --- | --- | --- |
| [Graph workflow](https://adk.dev/graphs/) | A fixed process whose data and routing remain clear as explicit edges. | Useful for the outer `extract -> orchestrate -> render` lifecycle. |
| [Dynamic workflow](https://adk.dev/graphs/dynamic/) | Iterative or branching logic that is clearer as Python loops, conditions, and awaited child calls. | Best fit for the bounded generator-critic-refiner loop. |

The current structure therefore uses each style at the level where it is
strongest. It is not a workaround around `Workflow`: dynamic children are
registered with the enclosing Workflow, executed by ADK, recorded under their
node paths, and recovered from event history on resume.

### Dynamic-workflow conformance

The implementation follows the important ADK dynamic-node rules:

| Rule | Implementation |
| --- | --- |
| Dynamic callers are rerunnable. | `generator_critic_loop` has `@node(rerun_on_resume=True)`. |
| Child nodes remain supervised. | Every `ctx.run_node()` call is awaited directly. |
| Re-entry identifies the same logical calls. | Every child call has a stable, non-numeric `run_id`. |
| Control flow is bounded. | `MAX_REFINEMENTS` limits revisions and exhaustion raises. |
| Model decisions are schema-validated. | The critic returns `Literal["PASS", "REFINE"]`. |
| Working values stay local where possible. | Draft and feedback are Pydantic values in the controller rather than session-state mailboxes. |
| Visible and machine-readable results are separate. | `render_result` emits both `message` and serializable `output`. |

This is a best-fit recommendation, not a rule that all refinement loops must be
dynamic. A simpler quality gate can be expressed cleanly as a graph back-edge.
The dynamic form becomes stronger when the loop also owns a precise budget,
multiple typed working values, mandatory re-review, and an explicit exhaustion
policy, as this one does.

## Implementation details

### Static workflow

The root graph has one success path:

```text
START -> extract_user_input -> generator_critic_loop -> render_result
```

`extract_user_input` rejects an empty prompt, passes the trimmed prompt to the
controller, and stores it as `temp:original_user_prompt`. The generator receives
the prompt as node input. The critic and refiner receive the changing draft as
node input and read the unchanged original prompt through the state placeholder
`{temp:original_user_prompt}`.

Temporary state is appropriate because the prompt is needed only during the
current invocation and must not leak into a later user turn. ADK's behavior is
defined in
[`BaseSessionService`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/sessions/base_session_service.py#L172-L220).

### Dynamic calls

`generator_critic_loop` uses `@node(rerun_on_resume=True)` because ADK requires
a node that calls `ctx.run_node()` to be rerunnable. Every child call is awaited
directly, preserving Workflow error propagation, cancellation, tracing, and
dynamic call/return behavior described by
[`Context.run_node()`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L424-L477).

Stable non-numeric run IDs identify each logical call:

```text
generator@initial-draft
critic@critique-0 ... critic@critique-5
refiner@refinement-1 ... refiner@refinement-5
```

These IDs keep trace paths deterministic and avoid collisions with ADK's
automatically generated numeric child IDs.

### Typed boundaries

Two Pydantic models define the data flow:

```text
ReflectionAgentOutputDraft(draft, feedback="")
ReflectionAgentCriticOutput(decision="PASS" | "REFINE", feedback="")
```

Each LLM agent declares `input_schema` and/or `output_schema`. ADK validates the
model response and returns a serializable dictionary; the controller immediately
uses `model_validate()` to restore typed access. This matches ADK's
[`LLM workflow wrapper`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_llm_agent_wrapper.py#L352-L380)
and
[`BaseNode` output normalization](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_base_node.py#L100-L185).

All three agents use `mode="single_turn"` and `include_contents="none"`, so
each call is a bounded transformation with explicit input rather than a shared
conversation or transfer.

### Terminal output

`render_result` emits the approved draft as `Event.message` for the UI and as
`model_dump()` in `Event.output` for serializable workflow output. A dedicated
renderer is required because node output is workflow plumbing, not necessarily
visible response content. The official ADK
[`message` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/message/agent.py)
demonstrates this separation.

## Loop logic and termination

The controller implements:

```python
draft = run(generator)

for review_number in range(MAX_REFINEMENTS + 1):
  review = run(critic, draft)
  if review.decision == "PASS":
    return draft
  if review_number == MAX_REFINEMENTS:
    break
  draft = run(refiner, draft + review.feedback)

raise RuntimeError("critic did not approve")
```

`MAX_REFINEMENTS = 5` allows five revisions. The critic evaluates the initial
draft and each revised draft, giving a maximum of:

```text
1 generator + 6 critic calls + 5 refiner calls = 12 LLM calls
```

The central invariant is:

> Every successfully returned draft has been approved by the critic.

The refiner cannot terminate the workflow. Its output always returns to the
critic. If the sixth critic decision is still `REFINE`, the workflow raises
instead of returning the fifth revision unreviewed. This avoids the off-by-one
failure in which a bounded loop returns its last refinement without a final
quality check.

## Why the loop is imperative

ADK also supports a declarative graph back-edge. The official
[`loop` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/loop/agent.py)
and the independent
[`quality-gate example`](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/graph-workflows/examples/06_loop.py)
route a failed evaluation back to an earlier node.

That is valid, but this implementation also needs a hard counter, a combined
draft-and-feedback refiner input, an explicit exhaustion failure, and a final
review after the last refinement. Keeping those values local to one controller
avoids extra counter, adapter, routing, and failure nodes. The supporting
[`dynamic workflow example`](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/graph-workflows/examples/08_dynamic.py)
uses the same bounded `ctx.run_node()` approach.

A behaviorally equivalent declarative graph would need approximately this
shape:

```text
START -> extract -> generator -> critic -> route_review
                                      |       |
                                      |       +-- PASS -> render
                                      |       +-- EXHAUSTED -> fail
                                      |       +-- REFINE -> refiner --+
                                      +-------------------------------+
```

In addition to the visible nodes, `route_review` would have to preserve the
current draft, combine it with critic feedback, update the iteration count, and
emit different payloads for the three routes. That normally requires
`output_key`/state mailboxes or more adapter nodes. It improves top-level graph
visualization and construction-time topology validation, but distributes the
approval invariant across the graph.

Prefer that declarative form if graph visualization, externally configurable
routes, separate operational ownership of stages, or additional independent
branches become primary requirements. Keep the current dynamic form while the
main complexity is the bounded iterative contract and its local typed values.

Legacy `LoopAgent` and `SequentialAgent` wiring was excluded because current
ADK guidance deprecates them in favor of `Workflow`. The Google
[`llm-auditor` recipe](https://github.com/google/adk-samples/blob/30bb11aa40db0705a11859eda2cc3fa7ca7dfa7e/python/agents/llm-auditor/llm_auditor/agent.py)
is a useful critic/reviser analogue, but its deprecated `SequentialAgent`
composition is not used here.

## Reference mapping

| Choice | Reference | Justification |
| --- | --- | --- |
| Root `Workflow` | [`Workflow`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_workflow.py#L145-L220) | Provides graph completion and supervises static and dynamic nodes. |
| Bounded controller | [`dynamic_nodes`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/dynamic_nodes/agent.py) | The required number of child calls is known only after critique. |
| Awaited children | [`Context.run_node()`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L424-L477) | Preserves supervised call/return and error propagation. |
| Stable run IDs | [`Context` scheduling](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L537-L568) | Gives each iteration a deterministic execution identity. |
| Typed output | [`LLM wrapper`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_llm_agent_wrapper.py#L352-L380) | Validates model output before control flow uses it. |
| Terminal renderer | [`message` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/message/agent.py) | Separates visible content from structured node output. |

## Constraints and validation

- Every refiner call must be followed by a critic call.
- The hard refinement budget must remain enforceable.
- The controller must retain `rerun_on_resume=True` and directly await child
  nodes.
- Run IDs must remain stable and non-numeric.
- Terminal `Event.output` must remain serializable.
- `temp:` state is not persisted. Adding tools or HITL requires explicit prompt
  restoration and idempotency design before relying on resume.

`tests/test_generator_critic_agent.py` uses scripted models with the public
`InMemoryRunner`. It verifies re-critique after refinement, approved terminal
output, serializable rendering, and failure after the refinement budget is
exhausted.

```text
python -m compileall -q generator_critic_agent tests
python -m pytest -q
```

The repository suite passes 11 tests plus 7 subtests.
