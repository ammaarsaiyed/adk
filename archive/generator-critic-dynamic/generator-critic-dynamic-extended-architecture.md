# Generator–critic–refiner workflow in ADK 2.7.1

## Final as-built architecture, rationale, and source traceability

**Status:** Implemented and deterministically validated

**Runtime:** `google-adk==2.7.1`

**Application:** `generator_critic_agent/`

## Decision

The application is implemented as a root `Workflow` with a bounded Python
controller. The controller dynamically calls three stateless, single-turn LLM
agents:

- `generator` creates one initial draft;
- `critic` either approves the current draft or returns revision feedback;
- `refiner` applies that feedback and returns a complete replacement draft.

Python, rather than an LLM tool loop, owns iteration and termination. A draft
can leave the controller only after a critic returns `PASS`. If the critic
continues to request changes after five refinements, the workflow raises an
error instead of returning an unapproved result.

```text
user
  -> generator_critic_workflow
       -> extract_user_input
            validate and preserve the original request
       -> generator_critic_loop
            -> generator [one initial draft]
            -> critic [PASS or REFINE]
                 PASS   -> return approved draft
                 REFINE -> refiner -> critic
                 ... up to five refinements
                 REFINE after final review -> fail
       -> render_result
            emit one user-visible response and one serializable output
  <- approved draft
```

The logical reflection agent is therefore a composite:

```text
reflection agent
  = generator/refiner language judgment
  + critic quality judgment
  + deterministic Workflow control
```

The agents decide what to write and whether the result is acceptable. The
Workflow and its controller decide which node runs next, how many times it may
run, what data crosses each boundary, and when the invocation is complete.

## Purpose and intended use

This pattern is intended for response generation where one model pass may be
insufficient and a separate quality gate should inspect the answer before it is
shown. It provides:

- separation between content creation and quality evaluation;
- iterative revision driven by explicit feedback;
- a hard upper bound on model calls;
- schema-validated draft and review contracts;
- one deterministic terminal presentation step;
- traceable child executions under the root Workflow.

The implementation is an application-level workflow. It does not add a new ADK
framework primitive, public API, or general-purpose retry engine.

## ADK concepts used

Four ADK concepts are central to the design:

1. **`Workflow`** is a graph node that schedules static children declared in
   `edges` and supervises dynamic children invoked through `Context.run_node()`.
2. **`@node`** converts the Python controller into a Workflow-compatible
   function node.
3. **`Context.run_node()`** dynamically executes an agent, awaits its result,
   and keeps the execution under Workflow cancellation, error, tracing, and
   replay machinery.
4. **`Event`** separates user-visible message content, downstream output, and
   state deltas.

This distinction matters because `Event.output` is workflow plumbing, while
`Event.message` becomes content rendered by ADK clients. Returning a value from
a function node alone does not guarantee a visible final answer.

## High-level architecture

### Static graph

The root graph is intentionally small and linear:

```text
START -> extract_user_input -> generator_critic_loop -> render_result
```

Each static node has one responsibility:

| Node | Responsibility | Output |
| --- | --- | --- |
| `extract_user_input` | Convert the incoming content to a non-empty, trimmed string and expose the original request to later instructions. | `str` |
| `generator_critic_loop` | Own generation, critique, refinement, approval, and the iteration budget. | `ReflectionAgentOutputDraft` |
| `render_result` | Render the approved text and preserve a JSON-serializable structured result. | `dict` validated by the Workflow output schema |

There are no alternate static terminal paths. Successful execution must pass
through `render_result`, while invalid input, invalid structured model output,
or an exhausted refinement budget fails before presentation.

### Dynamic child topology

The controller schedules its LLM agents dynamically:

```text
generator_critic_loop
  ├─ generator@initial-draft
  ├─ critic@critique-0
  ├─ refiner@refinement-1       [only after REFINE]
  ├─ critic@critique-1
  ├─ refiner@refinement-2       [only after REFINE]
  └─ ...
```

The exact number of child calls is not known until the critic responds. The
outer graph remains fixed, but ordinary Python control flow decides how much of
the dynamic child topology is needed for this invocation.

### Why the design combines a static graph and a dynamic loop

The static graph provides a clear application boundary and one terminal
renderer. The dynamic controller keeps the current draft and review in local,
typed variables and expresses the bounded feedback loop directly.

This division avoids turning intermediate draft and feedback values into a
persistent state-machine mailbox. It also makes the approval invariant visible
in one function: every `REFINE` response leads to one refiner call and then
another critic call; only `PASS` returns.

## Detailed invocation flow

For each user request:

1. The root `Workflow` accepts the user content through `input_schema=str`.
2. `extract_user_input` strips whitespace.
3. An empty result raises `ValueError`; no LLM is called.
4. The node emits one event containing:
   - `output=<trimmed prompt>` for the next static node;
   - `state={"temp:original_user_prompt": <trimmed prompt>}` for critic and
     refiner instruction substitution during this invocation.
5. `generator_critic_loop` awaits `generator_agent` using the stable run ID
   `initial-draft`.
6. ADK validates the generator's model text against
   `ReflectionAgentOutputDraft` and returns a plain dictionary.
7. The controller immediately reconstructs
   `ReflectionAgentOutputDraft` with `model_validate`.
8. The controller awaits the critic with the current draft and a run ID of
   `critique-<review number>`.
9. The critic output is validated as `ReflectionAgentCriticOutput`.
10. If the decision is `PASS`, the controller returns the current draft.
11. If the decision is `REFINE` and refinement capacity remains:
    - the controller creates a new `ReflectionAgentOutputDraft` containing the
      current draft and critic feedback;
    - it awaits the refiner using `refinement-<number>`;
    - it validates the replacement draft;
    - it returns to the critic step.
12. If the last allowed critic call still returns `REFINE`, the controller
    raises `RuntimeError`.
13. On success, `render_result` receives the validated draft, emits its text as
    `Event.message`, and emits `model_dump()` as `Event.output`.
14. `render_result` is terminal, so the Workflow completes without another
    model call.

## Loop contract and termination semantics

### Exact budget

`MAX_REFINEMENTS = 5` means five revisions are allowed, not five total model
calls.

| Outcome | Generator calls | Critic calls | Refiner calls | Total LLM calls |
| --- | ---: | ---: | ---: | ---: |
| Initial draft passes | 1 | 1 | 0 | 2 |
| Pass after first revision | 1 | 2 | 1 | 4 |
| Pass after fifth revision | 1 | 6 | 5 | 12 |
| Budget exhausted | 1 | 6 | 5 | 12, then error |

The critic receives six opportunities because it evaluates the initial draft
and each of the five refined drafts.

### Approval invariant

The most important correctness property is:

> Every successfully returned draft is the exact draft most recently approved
> by the critic.

The controller enforces this structurally:

```text
critic PASS
  -> return current draft

critic REFINE and budget remains
  -> refiner produces replacement draft
  -> critic evaluates replacement draft

critic REFINE and budget exhausted
  -> raise
```

The refiner never terminates the workflow. This prevents a subtle failure in
which the fifth refinement is returned immediately even though the critic has
never evaluated it.

### Failure instead of fallback

Budget exhaustion is treated as a failed quality gate. The implementation does
not silently return:

- the last approved draft, because no approved draft may exist;
- the initial draft, because it was rejected;
- the last refined draft, because it was not approved;
- critic feedback as user-facing output, because it is internal control data.

A caller that wants a best-effort fallback must add that policy explicitly and
must label the result as unapproved in its own contract.

## Data contracts and data flow

### Draft contract

`ReflectionAgentOutputDraft` contains:

```text
draft: str
feedback: str = ""
```

The same model serves two related boundaries:

- generator/refiner output: `draft` is the complete current answer;
- refiner input: `draft` is the rejected answer and `feedback` is the critic's
  revision instruction.

The final renderer uses only `draft`; `feedback` remains structured workflow
metadata.

### Critic contract

`ReflectionAgentCriticOutput` contains:

```text
decision: "PASS" | "REFINE"
feedback: str = ""
```

The `Literal` type closes the routing vocabulary. A model cannot introduce an
unhandled decision such as `RETRY`, `ACCEPT`, or `FAIL`; schema validation
rejects it before the controller branches.

### End-to-end data flow

```text
user content
  -> str original prompt
  -> generator input
  -> {draft, feedback}
  -> ReflectionAgentOutputDraft
  -> critic input
  -> {decision, feedback}
  -> ReflectionAgentCriticOutput
  -> PASS: approved ReflectionAgentOutputDraft
  -> or REFINE: ReflectionAgentOutputDraft(draft, feedback)
  -> refiner output
  -> next critic input
  -> render_result
       content: approved draft text
       output: serializable draft dictionary
```

### Why the controller validates again

An ADK LLM node with `output_schema` validates model text and returns a plain,
JSON-serializable dictionary. Reconstructing the Pydantic model in the
controller provides typed attribute access and makes the expected boundary
explicit at the call site. It is not a second model call or a second semantic
review.

The function node returns a Pydantic object, and ADK's `BaseNode` normalization
converts validated Pydantic output to a dictionary for event transport. The
terminal renderer calls `model_dump()` explicitly because it constructs an
`Event` itself and persistent session backends require serializable output.

## Prompt and state propagation

The generator receives the original request as its direct `node_input`. The
critic and refiner need two inputs at once:

- the changing draft/review payload supplied as `node_input`;
- the unchanged original request used as the evaluation target.

The implementation stores the latter under
`temp:original_user_prompt`. Their instructions reference it through ADK's
supported state placeholder syntax:

```text
{temp:original_user_prompt}
```

The `temp:` prefix is deliberate. ADK makes temporary state available to later
nodes during the current invocation but removes it before persistent session
storage. A later user turn therefore cannot accidentally evaluate a new draft
against an old request.

State is emitted with `Event(state=...)`, not assigned as an unrecorded side
effect. This keeps the within-invocation transition visible to ADK while the
session service applies the temporary value for downstream instruction
resolution.

### Resumption limitation of temporary state

Temporary state is not persisted. The current generator, critic, and refiner
have no tools and cannot yield `RequestInput`, so the implemented flow has no
expected child waiting boundary.

`rerun_on_resume=True` is still required by ADK for every node that calls
`ctx.run_node()`, and stable child IDs make the dynamic topology deterministic.
Those properties alone do not make this application fully HITL-resumable.

If a future child can pause, the design must first restore the original prompt
when the controller re-runs. Valid options include re-emitting temporary state
from the rerun path or using a carefully scoped persisted event value. Any
external side effects would also need idempotency guarantees.

## Agent execution boundaries

All three LLM agents use:

```text
mode="single_turn"
include_contents="none"
disallow_transfer_to_parent=True
disallow_transfer_to_peers=True
```

These settings define each call as a bounded transformation rather than an
ongoing conversation:

- `single_turn` produces one node result for its caller;
- `include_contents="none"` prevents prior session messages and sibling agent
  events from becoming implicit input;
- transfer restrictions preserve call/return ownership if these agents are
  later embedded in a broader hierarchy.

The model sees the current structured node input plus its resolved system
instruction. Draft propagation therefore does not depend on shared chat
history.

### Model allocation

The generator and refiner use `gemini-3.5-flash-lite`; the critic uses
`gemini-3.5-flash` with high thinking enabled. This is a deliberate
quality/cost split in the current implementation:

- generation and rewriting are potentially repeated and use the lighter
  model;
- the critic is the quality gate and uses the stronger configuration.

ADK's LLM workflow wrapper excludes thought parts when constructing the typed
node output. Only the non-thought structured response is validated as
`ReflectionAgentCriticOutput`.

## Dynamic execution, identity, and error propagation

### Why `Context.run_node()` is used

`Context.run_node()` is ADK's public dynamic node call/return primitive. The
controller awaits it directly so:

- the child remains supervised by the enclosing Workflow;
- the returned value is available to the controller;
- child validation or execution errors propagate through the parent;
- cancellation is not detached into an unmanaged task;
- ADK can record a parent/child node path for tracing.

No child is wrapped in `asyncio.create_task()`, and no transfer event represents
return.

### Stable run IDs

Each dynamic call receives a stable, non-numeric `run_id`:

| Logical call | Run ID |
| --- | --- |
| Initial generation | `initial-draft` |
| Critic review number `n` | `critique-n` |
| Refinement number `n` | `refinement-n` |

ADK combines the node name and run ID into the dynamic node path. Stable IDs
make traces readable and allow the scheduler to correlate the same logical
child if the parent is re-executed. Non-numeric values avoid collisions with
ADK's automatically generated child counters.

### `raise_on_wait=True`

Each call uses `raise_on_wait=True`. The current LLM nodes are expected to
produce an output immediately, but this prevents a future WAITING/no-output
child from being interpreted as a normal `None` result. It is defensive error
propagation, not a substitute for the prompt-restoration and idempotency work
required for a real human-in-the-loop extension.

## Terminal rendering and serialization

`render_result` is a dedicated terminal node:

```python
def render_result(node_input: ReflectionAgentOutputDraft) -> Event:
  return Event(
      message=node_input.draft,
      output=node_input.model_dump(),
  )
```

The two fields serve different consumers:

- `message` becomes UI/CLI content;
- `output` is structured workflow data for callers and session persistence.

The explicit `model_dump()` prevents a Pydantic instance from being placed
directly in persisted event output. The root Workflow's
`output_schema=ReflectionAgentOutputDraft` validates the terminal dictionary.

The renderer, rather than an LLM agent, ends the graph. No additional model
round is needed to transform an internal node result into a final response.

## Runtime invariants

The implementation establishes the following properties:

1. The generator runs exactly once on every non-empty successful invocation.
2. The critic runs at least once and at most six times.
3. The refiner runs only after `REFINE` and at most five times.
4. Every refined draft is evaluated by a later critic call.
5. Only a critic `PASS` can produce successful controller output.
6. Budget exhaustion raises and cannot reach `render_result`.
7. Every LLM boundary is schema validated.
8. Every dynamic child is awaited directly.
9. Dynamic child identities are deterministic and non-numeric.
10. The original prompt is visible to the critic and refiner only during the
    current invocation.
11. Agents do not transfer conversational ownership.
12. Only `render_result` intentionally emits the final visible answer.
13. Terminal event output is JSON serializable.
14. The root accepts ordinary string input and returns a structured draft.

## Reference findings

### Official dynamic-node precedent

The official ADK
[`dynamic_nodes` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/dynamic_nodes/agent.py)
uses a rerunnable Python node that repeatedly awaits a generator and evaluator
through `ctx.run_node()`. This is the closest direct wiring precedent for the
implemented controller.

The public
[`Context.run_node()` implementation](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L424-L477)
defines dynamic child input, return values, run IDs, sub-branches, and waiting
behavior. The
[`Workflow` implementation](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_workflow.py#L145-L220)
owns static scheduling, dynamic-node supervision, terminal output, and replay.

### Official declarative-loop precedent

The official ADK
[`loop` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/loop/agent.py)
demonstrates the alternative representation: a routing node emits a grade and
a conditional graph edge loops back to the generator.

That approach is standard when loop state naturally lives in node output or
state and the graph itself should expose every transition. It was not selected
here because the controller needs a hard refinement budget, a local
draft-plus-review pair, and a guarantee that the final refinement is reviewed
before exit. These conditions are more directly expressed in one bounded
Python routine than in additional state-counter, adapter, route, and failure
nodes.

### Supporting workflow-pattern examples

The independent
[`quality-gate loop`](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/graph-workflows/examples/06_loop.py)
shows the general draft/evaluate/pass-or-loop topology. Its
[`dynamic workflow example`](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/graph-workflows/examples/08_dynamic.py)
shows a bounded Python loop around `ctx.run_node()`. The current application
combines those concepts while using ADK 2.7.1 event-state and schema behavior.

### Legacy sample deliberately excluded from workflow wiring

The Google `adk-samples`
[`llm-auditor` recipe](https://github.com/google/adk-samples/blob/30bb11aa40db0705a11859eda2cc3fa7ca7dfa7e/python/agents/llm-auditor/llm_auditor/agent.py)
is a useful domain analogue for separate critic and reviser roles. Its wiring
uses `SequentialAgent`, however, so it is not the architectural basis for this
implementation. Current ADK guidance marks `SequentialAgent`, `ParallelAgent`,
and `LoopAgent` as deprecated in favor of `Workflow`.

### Message and output precedent

The official
[`message` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/message/agent.py)
demonstrates explicit user-visible `Event.message` emission. ADK's
[`Event` source](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/events/event.py#L91-L218)
defines content, output, state, route, node identity, and interrupt fields.

## Implementation-to-source traceability

| Implemented choice | Source or precedent | Alignment and justification |
| --- | --- | --- |
| Root `Workflow` | [`Workflow`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_workflow.py#L145-L220) | Provides a graph-owned terminal boundary and supervises both static and dynamic children. |
| `START -> extract -> loop -> render` | [`dynamic_nodes`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/dynamic_nodes/agent.py) | Keeps one static success path while runtime-dependent iteration stays in a controller. |
| `@node(rerun_on_resume=True)` | [`Context.run_node()` precondition](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L489-L515) | Dynamic callers must be rerunnable so ADK can re-enter them when a child waits. |
| Directly awaited `ctx.run_node()` calls | [`Context.run_node()`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L424-L477) | Preserves supervision, error propagation, cancellation, tracing, and returned child values. |
| Stable `initial-draft`, `critique-n`, and `refinement-n` IDs | [`Context` dynamic ID logic](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L537-L568) | Creates deterministic child paths and avoids collisions with automatic numeric IDs. |
| Bounded Python loop | [`dynamic_nodes`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/dynamic_nodes/agent.py); [`dynamic pattern`](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/graph-workflows/examples/08_dynamic.py) | The number of refinement calls depends on runtime judgment, while Python enforces a fixed maximum. |
| Separate critic gate | [`quality-gate pattern`](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/graph-workflows/examples/06_loop.py) | Evaluation controls whether the draft exits or returns for another revision. |
| Re-critique after every refinement | Local loop contract | Prevents the final allowed refinement from bypassing the quality gate. |
| `Literal["PASS", "REFINE"]` | Pydantic output schema used by ADK | Closes the decision vocabulary before Python branches. |
| Explicit `model_validate()` at controller boundaries | [`LLM node output processing`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_llm_agent_wrapper.py#L352-L380) | ADK returns validated dictionaries; reconstruction restores typed access locally. |
| `mode="single_turn"` | [`LlmAgent.mode`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/llm_agent.py#L365-L380) | Each LLM call is a bounded transformation, not conversational ownership. |
| `include_contents="none"` | [`LlmAgent.include_contents`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/llm_agent.py#L392-L400) | Inputs are explicit; prior session events cannot silently alter a later review. |
| Invocation-only original prompt | [`temp:` session behavior](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/sessions/base_session_service.py#L172-L220) | The prompt is available to downstream nodes but not persisted into later turns. |
| State written through `Event(state=...)` | [`Event`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/events/event.py#L91-L218) | Uses ADK's event/state channel instead of an invisible mutation. |
| Pydantic-to-dictionary node output | [`BaseNode` normalization](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_base_node.py#L100-L185) | Keeps workflow output validated and serializable. |
| Dedicated `render_result` | [`message` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/message/agent.py) | Separates final UI content from internal node output. |
| `model_dump()` in terminal event | [`BaseNode` serialization rules](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_base_node.py#L134-L185) | Avoids placing a raw Pydantic object in persisted `Event.output`. |
| Deterministic public-runner tests | [`InMemoryRunner`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/runners.py) | Exercises the application through the same event surface used by ADK clients while replacing only the external model boundary. |

## Alternatives considered

| Alternative | Decision | Reason |
| --- | --- | --- |
| Deprecated `LoopAgent` containing generator, critic, and refiner | Reject | Current ADK guidance replaces legacy orchestration agents with `Workflow`; loop exit would also depend on legacy escalation conventions. |
| Deprecated `SequentialAgent` generator → critic → refiner | Reject | It performs one fixed pass and does not represent conditional iterative approval. |
| Declarative Workflow back-edge | Valid alternative, not selected | It clearly exposes the cycle, but this implementation would need additional adapter, state-counter, routing, and failure nodes to preserve the same bounded draft/review contract. |
| Chat root with generator/critic/refiner tools | Reject | The parent model would own an open-ended tool loop and could skip, repeat, or terminate stages based only on prompt compliance. |
| Agent transfers | Reject | Transfer changes conversational ownership; it is not a typed child call returning to a deterministic controller. |
| Unbounded `while True` dynamic loop | Reject | A permanently dissatisfied critic could consume model calls indefinitely. |
| Return the final refinement when the budget ends | Reject | The final draft would bypass the critic and violate the approval invariant. |
| Persistent session-state draft mailbox | Reject | Draft and feedback are invocation-local values already returned by child calls; persistent keys introduce stale cross-turn coupling. |
| Parallel generator, critic, and refiner | Reject | Critique depends on the generated draft and refinement depends on critique; the work is sequential by definition. |
| Root Workflow plus bounded dynamic controller | Adopt | It makes iteration, approval, budget, and terminal output enforceable runtime properties while preserving model judgment inside each stage. |

## Extension points

### Change the refinement budget

Update `MAX_REFINEMENTS`. The maximum critic calls remain
`MAX_REFINEMENTS + 1`, and the maximum model calls become:

```text
1 generator + (MAX_REFINEMENTS + 1) critics + MAX_REFINEMENTS refiners
```

Tests should continue proving that the last refinement is reviewed and that a
final `REFINE` raises.

### Change evaluation criteria

The critic instruction may add domain-specific checks such as factuality,
policy compliance, tone, citations, or formatting. If the controller needs to
branch on individual criteria, extend `ReflectionAgentCriticOutput` with typed
fields rather than parsing prose feedback.

### Change model allocation

`settings.py` separates the critic model from the generator/refiner model.
These settings can be adjusted independently without changing workflow
topology. Model changes should be evaluated for schema reliability as well as
answer quality.

### Enrich the draft contract

`ReflectionAgentOutputDraft` may gain fields for citations, confidence,
assumptions, or structured sections. The generator and refiner output schemas,
critic input schema, renderer, and tests must change together.

### Add tools to a child

Tools may be attached to an individual LLM agent, but doing so changes the
operational contract. Tool errors, retries, confirmation, side effects, and
possible waiting must be handled deliberately. A pausing child requires prompt
restoration across resume because `temp:` state is not persisted.

### Move to a declarative graph loop

A declarative back-edge becomes attractive when loop transitions themselves
must be visible as graph edges, when several exit routes are required, or when
individual stages need independent retry/timeout policies. Preserve the same
approval and budget invariants with explicit typed adapter and counter nodes.

## Extension constraints

The following properties define the current design contract:

- Do not return directly after a refiner call; every replacement draft must be
  sent to the critic.
- Do not remove the hard refinement budget without introducing another
  enforceable resource bound.
- Do not remove `rerun_on_resume=True` while the controller calls
  `ctx.run_node()`.
- Do not use unstable or numeric explicit run IDs for dynamic children.
- Do not wrap child calls in detached asyncio tasks.
- Do not make these agents `mode="chat"` unless conversational ownership
  transfer is intentionally replacing call/return behavior.
- Do not enable implicit session history as a substitute for explicit draft,
  feedback, and original-prompt propagation.
- Do not persist temporary draft/review mailboxes without a cross-turn state
  design and cleanup policy.
- Do not emit a successful result on budget exhaustion unless the output
  contract explicitly distinguishes approved and unapproved drafts.
- Do not put a Pydantic instance directly in a manually constructed terminal
  `Event.output`; serialize it first.
- Do not emit more than one successful controller output or add multiple
  output-producing terminal nodes.
- Do not assume the current temporary-state design survives HITL resume.

## Operational limitations

- The workflow is sequential; latency accumulates with every critique and
  refinement.
- One invocation may make up to twelve LLM calls.
- The critic is a model-based quality gate, not a formal proof of correctness.
- A schema-valid but semantically poor `PASS` can still approve a weak answer.
- Invalid JSON or schema-invalid model output fails the workflow; there is no
  application-level structured-output retry.
- Budget exhaustion fails rather than returning a best-effort answer.
- No child uses tools, retrieval, citations, memory, or external validation.
- There is no human-in-the-loop path and no complete cross-invocation resume
  contract.
- The workflow stores no approved drafts across user turns.
- The tests fake the model boundary; they verify orchestration behavior rather
  than real-model response quality.

## Deterministic validation

`tests/test_generator_critic_agent.py` uses scripted `BaseLlm` implementations
and the public `InMemoryRunner`.

The tests prove:

- a rejected initial draft is refined;
- the refined draft is sent to the critic again;
- only the later approved draft reaches `render_result`;
- the renderer emits both visible text and a serializable dictionary;
- repeated `REFINE` decisions consume exactly five refiner calls and six
  critic calls;
- exhausted refinement capacity raises instead of rendering an unapproved
  draft.

Validation commands:

```text
python -m compileall -q generator_critic_agent tests
python -m pytest -q tests/test_generator_critic_agent.py
python -m pytest -q
```

At the time of this document update, the complete repository suite passed 11
tests plus 7 subtests.

## Source index

### Local implementation and evidence

- `generator_critic_agent/agent.py` — static Workflow, bounded controller,
  dynamic calls, run IDs, failure policy, and terminal renderer.
- `generator_critic_agent/schemas.py` — draft and critic contracts.
- `generator_critic_agent/settings.py` — generator/refiner and critic model
  selection.
- `generator_critic_agent/agents/generator.py` — initial-draft agent.
- `generator_critic_agent/agents/critic.py` — quality gate and decision schema.
- `generator_critic_agent/agents/refiner.py` — feedback-driven revision agent.
- `tests/test_generator_critic_agent.py` — deterministic orchestration tests.
- `hierarchical-agent-implementation-plan.md` — local precedent for separating
  LLM judgment from bounded Python execution and using one terminal renderer.

### Current ADK 2.7.1 source and guidance

- [`Workflow`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_workflow.py)
- [`Context.run_node()`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py)
- [`DynamicNodeScheduler`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_dynamic_node_scheduler.py)
- [`BaseNode` normalization](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_base_node.py)
- [`FunctionNode`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_function_node.py)
- [`LLM agent Workflow wrapper`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_llm_agent_wrapper.py)
- [`Event`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/events/event.py)
- [`Temporary session state`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/sessions/base_session_service.py#L172-L220)
- [`Instruction state substitution`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/utils/instructions_utils.py)
- [`Workflow best practices`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/best-practices.md)
- [`Dynamic-node guidance`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/dynamic-nodes.md)
- [`Routing and loop guidance`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/routing-and-conditions.md)
- [`Multi-agent and deprecation guidance`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/multi-agent.md)

### Exemplar patterns

- [`dynamic_nodes`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/dynamic_nodes/agent.py)
  — official dynamic generator/evaluator loop using `ctx.run_node()`.
- [`loop`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/loop/agent.py)
  — official declarative conditional back-edge alternative.
- [`message`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/message/agent.py)
  — explicit user-visible event content.
- [`quality-gate loop`](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/graph-workflows/examples/06_loop.py)
  — draft/evaluate/pass-or-loop topology.
- [`bounded dynamic loop`](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/graph-workflows/examples/08_dynamic.py)
  — programmatic bounded iteration.
- [`llm-auditor` recipe](https://github.com/google/adk-samples/blob/30bb11aa40db0705a11859eda2cc3fa7ca7dfa7e/python/agents/llm-auditor/llm_auditor/agent.py)
  — conceptual critic/reviser role split; deprecated sequential wiring was not
  adopted.

## Final rule

> The generator and refiner create candidate answers, the critic alone approves
> them, and the Workflow owns iteration and termination. Every refinement is
> reviewed, the loop has a fixed cost ceiling, and only an approved,
> serializable draft reaches the terminal renderer.

## Concise architectural justification

The implementation uses a root `Workflow` with one linear static path and one
bounded dynamic controller. The generator creates an initial typed draft. The
critic evaluates that draft against the original request and returns `PASS` or
`REFINE`. A `REFINE` decision causes one refiner call followed by another
critic call. Python enforces five refinements, and a final rejection raises
rather than leaking an unapproved draft. A dedicated terminal node renders the
approved text and returns serializable structured output.

This shape follows the official ADK `dynamic_nodes` sample and the public
`Context.run_node()` contract. A declarative Workflow back-edge is also a valid
ADK loop pattern, but the bounded controller expresses this implementation's
draft/review pairing, cost ceiling, and approval invariant with fewer state and
routing adapters. The design deliberately excludes deprecated `LoopAgent` and
`SequentialAgent` wiring, open-ended chat/tool loops, and implicit conversation
history.

The result is a controlled reflection workflow: model calls own language and
quality judgment, while code owns data validation, replay identity, resource
bounds, failure behavior, and the single terminal response.
