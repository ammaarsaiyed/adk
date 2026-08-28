# Workflow-native orchestrator with specialist agents

## Final as-built architecture, justification, and source traceability

**Status:** Implemented and deterministically validated

**Runtime:** `google-adk==2.7.1`

**Application:** [`orchestrator_agent/`](orchestrator_agent/)

## Decision

The application is implemented as a root `Workflow` containing a bounded
Python orchestration node. One `orchestrator` LLM agent makes one typed decision
at a time. The workflow controller executes the selected `math`, `science`, or
`english` specialist with `await ctx.run_node(...)`, validates its result, and
passes all completed results back to the same orchestrator for the next
decision. Only the root workflow's terminal renderer produces the user-facing
response.

```text
user
  -> orchestrator_agent Workflow
       -> orchestrate controller
            -> orchestrator [one delegate-or-finish decision]
                 -> finish(answer)
                 -> or delegate(math | science | english)
            -> selected specialist [one typed result]
            -> orchestrator [next decision with completed results]
            -> ...
       -> render_result
  <- one final user-visible answer
```

The logical main orchestrator is therefore a composite:

```text
main orchestrator = orchestrator LLM judgment + deterministic Workflow control
```

The LLM decides what expertise is needed and synthesizes the final answer.
Python owns target validation, awaited execution, duplicate prevention, the
step budget, and terminal rendering.

This is intentionally **agent-as-tool semantics**, not a literal
`AgentTool(...)` object. Each specialist receives a typed call payload and
returns a typed result to its caller, but no model-generated function call or
conversation transfer represents that call. The distinction is important:
current ADK source discourages direct `AgentTool` construction and recommends
single-turn sub-agents for the ordinary inline-tool case, while the public
workflow API recommends `ctx.run_node()` for supervised dynamic node execution.

## Requirements satisfied

The design directly satisfies the requested behavioral contract:

- there is one main orchestrator and exactly three domain specialists;
- specialists behave as called capabilities and never own the response;
- every specialist result returns to the orchestrator;
- the orchestrator, not a specialist, synthesizes the final answer;
- a request may use one specialist, several specialists sequentially, or none;
- no transfer-to-agent operation is used for call or return;
- no specialist can be selected twice in one invocation;
- workflow completion, rather than prompt compliance alone, ends execution;
- the existing [`hello_agent/`](hello_agent/) implementation is unchanged.

## Reference findings

### Current ADK recommendation for agent tools

The ADK 2.7.1 [`AgentTool` source](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/tools/agent_tool.py#L108-L127)
states that direct `AgentTool` use is discouraged. For a straightforward inline
agent tool, it recommends attaching an agent with `mode="single_turn"` through
`sub_agents=[...]`; ADK then exposes it as a tool automatically.

The Google-provided [`task-mode` guidance](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/task-mode.md)
describes the intended semantic difference:

- `chat` means conversational transfer and a change of active ownership;
- `task` means a schema-validated delegated task that may interact with the
  user;
- `single_turn` means an autonomous schema-validated delegated task that
  returns to its coordinator.

The official [`input_output_schema` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/core/input_output_schema/agent.py)
is the canonical small example of a parent agent using a typed single-turn
sub-agent and then composing the final response.

That pattern is recommended when an LLM-owned tool loop is acceptable. It was
not selected here because the motivating failure is repeated or re-entered
delegation after a child returns. Moving from explicit `AgentTool` to automatic
single-turn wrapping changes how the child is exposed, but the parent LLM still
owns the repeated tool-call loop.

### Current ADK recommendation for dynamic workflow calls

The public [`Context.run_node()` API](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L424-L477)
executes a node dynamically, awaits its completion, and returns its output. Its
documentation explicitly says to await it directly so execution remains
supervised and is cancelled or resumed with its parent.

The official [`dynamic_nodes` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/dynamic_nodes/agent.py)
demonstrates the same control shape used here:

```text
bounded Python control
  -> await one LLM node
  -> validate its result
  -> conditionally await another LLM node
  -> return one terminal output
```

The official [`use_as_output` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/use_as_output/agent.py)
further establishes that an LLM agent may be dynamically called from a
workflow and its result passed through later workflow nodes.

The supporting [`adk-workflow-patterns` dynamic example](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/graph-workflows/examples/08_dynamic.py)
uses a bounded loop around `ctx.run_node()`. Its
[`custom orchestrator` example](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/collaborative-workflows/examples/04_custom_orchestrator.py)
demonstrates the broader principle that LLM specialists can supply judgment
while programmatic orchestration owns routing and completion.

### Conceptual agent-as-tool precedent

The [`market-research-agent` sample](https://github.com/google/adk-samples/blob/30bb11aa40db0705a11859eda2cc3fa7ca7dfa7e/contrib/python/market-research-agent/app/agent.py)
is a useful conceptual exemplar: a main LLM calls several specialist agents and
synthesizes their outputs into one report. This implementation preserves that
ownership model but does not copy its explicit `AgentTool(...)` wiring because
the current 2.7.1 SDK source now discourages direct use of that class.

### Legacy implementations deliberately excluded

The Google-provided [`multi-agent` guidance](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/multi-agent.md)
marks `SequentialAgent`, `ParallelAgent`, and `LoopAgent` as deprecated in
favor of `Workflow`. None of those legacy orchestration shells is used.

Chat-mode supervisor routing was also excluded because its contract is to
transfer conversational ownership. That is the opposite of the required
call/return behavior.

## High-level architecture

### Static graph

The root graph is intentionally small:

```text
START -> orchestrate -> render_result
```

`orchestrate` is the only dynamic controller. The three specialists and the
orchestrator LLM are scheduled from that node through `ctx.run_node()` rather
than being statically connected by graph edges.

This division keeps two concerns separate:

- the static graph guarantees a single terminal path;
- the dynamic controller chooses which optional specialists actually run.

### Invocation flow

For each user request:

1. `Workflow` validates the root input as `str` and invokes `orchestrate`.
2. `orchestrate` starts with an empty `completed` mapping.
3. It builds an `OrchestrationContext` containing:
   - the original request;
   - specialists that have not yet run;
   - all validated completed results.
4. It awaits the single-turn `orchestrator` agent.
5. `OrchestratorDecision` validates the returned JSON.
6. For `delegate`:
   - the target must be present and not already completed;
   - the controller builds a `SpecialistTask`;
   - it awaits the selected specialist;
   - `SpecialistResult` validates the returned JSON;
   - the result is stored under the specialist name;
   - the loop returns to step 3.
7. For `finish`:
   - the answer must be non-empty;
   - the controller yields one `OrchestratorResult` and returns.
8. `render_result` emits the answer as both user-visible message content and
   structured workflow output.
9. The graph has no successor, so the invocation terminates.

### Data flow

```text
str user request
  -> OrchestrationContext
       request: str
       remaining_agents: list[math | science | english]
       completed_results: mapping of SpecialistResult
  -> OrchestratorDecision
       delegate(target, instruction)
       or finish(answer)
  -> SpecialistTask
       original request
       focused instruction
       previous specialist results
  -> SpecialistResult
       answer
       key_points
  -> next OrchestrationContext
  -> OrchestratorResult
       final answer
       consulted agent names
  -> Event(message=answer, output=result)
```

Every boundary is declared in
[`orchestrator_agent/schemas.py`](orchestrator_agent/schemas.py). No result is
passed through implicit conversation history or an untyped state mailbox.

## Call/return and termination invariants

The implementation establishes these runtime invariants:

1. The orchestrator produces exactly one typed decision per model call.
2. A decision either delegates once or finishes; it cannot do both.
3. Every delegated agent is awaited directly.
4. Only names from the closed specialist mapping can execute.
5. Each specialist executes at most once per root invocation.
6. A specialist never selects or transfers to another specialist.
7. Specialist output returns to the workflow controller, not to the user.
8. Completed specialist results are explicitly included in the next
   orchestration request.
9. At most three specialists can run, followed by one finish decision.
10. Invalid decisions, empty final answers, and exhausted budgets fail visibly.
11. Only `render_result` intentionally renders the final workflow answer.
12. No transfer event, model function call, or long-running tool boundary is
    required for delegation.

## Implementation-choice traceability

| Implementation choice | Exemplar or recommended source | Alignment and justification |
| --- | --- | --- |
| `root_agent = Workflow(...)` | [`Workflow` source](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_workflow.py#L145-L181); [`multi-agent` guidance](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/multi-agent.md) | Current ADK recommends `Workflow` for new orchestration. Graph completion supplies a structural terminal boundary and avoids deprecated orchestration agents. |
| One static path, `START -> orchestrate -> render_result` | [`dynamic_nodes` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/dynamic_nodes/agent.py) | The optional topology lives inside supervised dynamic execution, while the outer graph retains one obvious completion path. |
| Dynamic specialist execution with `await ctx.run_node()` | [`Context.run_node()`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L424-L477); [`use_as_output` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/use_as_output/agent.py) | This is ADK's public workflow call/return primitive. Awaiting directly keeps errors, interruption, cancellation, and return values supervised by the workflow. |
| No explicit `AgentTool(...)` | [`AgentTool` recommendation](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/tools/agent_tool.py#L108-L127) | Direct construction is discouraged in 2.7.1. More importantly, a model-owned tool loop cannot structurally prohibit repeated calls after a tool response. |
| No `sub_agents=[...]` hierarchy | [`task-mode` guidance](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/task-mode.md) | Automatic single-turn agent tools are the recommended simple pattern, but they still put repeated tool selection inside the parent LLM loop. Here the controller must enforce uniqueness and termination. |
| One main `orchestrator` LLM reused for each decision | [`custom orchestrator` pattern](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/collaborative-workflows/examples/04_custom_orchestrator.py) | The same decision-maker sees progressively richer completed results. It remains responsible for expertise selection and final synthesis without owning execution mechanics. |
| `mode="single_turn"` on all four LLM agents | [`LlmAgent.mode` source](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/llm_agent.py#L365-L380); [`LLM workflow wrapper`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_llm_agent_wrapper.py#L384-L442) | Workflow agents are bounded autonomous calls, not conversations. Explicit mode avoids relying on wrapper defaults and documents the intended boundary. |
| `include_contents="none"` | [`LlmAgent.include_contents` source](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/llm_agent.py#L392-L400); [`LLM workflow wrapper`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_llm_agent_wrapper.py#L402-L418) | Each decision depends only on its typed input. Prior workflow events cannot silently affect routing or recreate conversational bouncing. |
| Pydantic `input_schema` and `output_schema` on every LLM node | [`input_output_schema` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/core/input_output_schema/agent.py); [`schema validation`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/utils/_schema_utils.py#L111-L165) | The caller and callee share explicit JSON contracts. ADK validates model text and returns serializable data, then the controller reconstructs the declared model at its boundary. |
| `Literal["math", "science", "english"]` | [`orchestrator_agent/schemas.py`](orchestrator_agent/schemas.py) | The model schema advertises the closed capability set. Invalid names fail during output validation instead of reaching dynamic lookup. |
| `_SPECIALISTS` closed mapping | [`orchestrator_agent/agent.py`](orchestrator_agent/agent.py) | Runtime dispatch is restricted to declared nodes. The model cannot invent a callable, module path, or arbitrary execution target. |
| Distinct specialist descriptions and instructions | [`multi-agent` guidance](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/multi-agent.md) | Clear domain boundaries reduce routing ambiguity. In this workflow, routing criteria are repeated in the orchestrator instruction because specialist descriptions are not automatically exposed through `ctx.run_node()`. |
| `OrchestrationContext.remaining_agents` | [`orchestrator_agent/schemas.py`](orchestrator_agent/schemas.py) | The model receives the exact currently valid choice set. This reduces invalid repeats while Python remains the final enforcement boundary. |
| `completed_results` passed into every subsequent decision | [`dynamic_nodes` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/dynamic_nodes/agent.py) | The orchestrator can inspect earlier work and decide whether to finish or consult another domain. Explicit node input replaces implicit shared conversation history. |
| `previous_results` passed to specialists | [`market-research-agent` conceptual pattern](https://github.com/google/adk-samples/blob/30bb11aa40db0705a11859eda2cc3fa7ca7dfa7e/contrib/python/market-research-agent/app/agent.py) | Later specialists may depend on earlier calculations or explanations. Passing those results makes sequential cross-domain tasks possible without exposing the entire session. |
| Sequential delegation | [`task-mode` rules](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/task-mode.md) | Delegated tasks are naturally awaited one at a time, and later work may depend on earlier output. This avoids unnecessary specialists and does not pretend dependent work is parallel. |
| Duplicate prevention in Python | [`orchestrator_agent/agent.py`](orchestrator_agent/agent.py); [`tests/test_orchestrator_agent.py`](tests/test_orchestrator_agent.py) | Prompt guidance is advisory. The controller checks `target_name in completed` and raises before execution, making the no-bounce rule enforceable and testable. |
| `MAX_ORCHESTRATOR_STEPS = 4` | [`adk-workflow-patterns` bounded dynamic loop](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/graph-workflows/examples/08_dynamic.py) | Three specialists require at most three delegate decisions; one additional decision allows final synthesis. A hard bound prevents indefinite model-driven routing. |
| `@node(rerun_on_resume=True)` | [`Context` precondition](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L505-L511); [`dynamic_nodes` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/dynamic_nodes/agent.py) | ADK requires a dynamic caller to be rerunnable because a child may interrupt and the parent must be able to wake and recover the child result. |
| Stable non-numeric `run_id` values | [`Context.run_node()` implementation](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L537-L553) | Stable names correlate each decision and specialist execution. Non-numeric IDs avoid collision with ADK-generated child counters. |
| `use_sub_branch=True` | [`Context.run_node()` documentation](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L459-L461) | Each dynamic LLM call has isolated state and events while remaining visible beneath its parent in traces. |
| Explicit model on every LLM agent | [`orchestrator_agent/settings.py`](orchestrator_agent/settings.py) | These agents are dynamic workflow nodes, not an LLM parent/sub-agent tree. Explicit configuration avoids relying on parent-model inheritance that this topology does not establish. A single settings constant still keeps replacement mechanical. |
| `disallow_transfer_to_parent=True` and `disallow_transfer_to_peers=True` | [`LlmAgent` transfer settings](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/llm_agent.py#L380-L391) | No agent currently has a transfer hierarchy, but the flags preserve the intended non-transfer contract if an agent is later embedded differently. They are defense in depth, not the primary return mechanism. |
| No `output_key` or session-state mailbox | [`LlmAgent.output_key` source](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/llm_agent.py#L426-L433) | `ctx.run_node()` already returns the immediate child output. Direct validated return values are clearer and cannot leak stale results into a later user turn. |
| Fallback to the original request when delegated instruction is empty | [`orchestrator_agent/agent.py`](orchestrator_agent/agent.py) | An incomplete but otherwise valid delegate decision still supplies the specialist with meaningful input. It avoids an empty-task call without hiding invalid targets or outputs. |
| Empty final answer rejected | [`orchestrator_agent/agent.py`](orchestrator_agent/agent.py) | `action="finish"` is not accepted as successful termination unless it contains a renderable answer. The workflow fails visibly instead of emitting a blank response. |
| `OrchestratorResult.consulted_agents` | [`orchestrator_agent/schemas.py`](orchestrator_agent/schemas.py) | The structured output preserves which capabilities informed the answer, supporting evaluation and debugging without exposing internal event history in user-facing prose. |
| Dedicated `render_result` terminal node | [`Event` message convenience](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/events/event.py#L180-L218) | `Event.message` becomes UI content, while `Event.output` remains workflow data. Emitting both once gives the CLI/web UI an answer and downstream callers a typed result. |
| `root_agent.input_schema=str` and typed `output_schema` | [`Workflow` source](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_workflow.py) | The public application boundary accepts an ordinary user request and returns a structured result. Internal schemas need not leak into the CLI interaction. |
| All agents declared in one orchestration module; schemas separate | [`multi-agent` circular-import guidance](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/multi-agent.md) | The example is small enough that one wiring module is easier to inspect and avoids per-agent circular imports. Shared data contracts remain isolated in `schemas.py`. |
| Deterministic tests through `InMemoryRunner` | [`tests/test_orchestrator_agent.py`](tests/test_orchestrator_agent.py) | Tests exercise the same public runner and event stream used by the application while replacing only the external model boundary. They verify behavior rather than private scheduler internals. |

## Why this differs from a literal agent-tool implementation

The direct agent-tool topology would be:

```text
chat orchestrator LLM
  -> model emits specialist tool call
  -> specialist runs
  -> function response returns to chat orchestrator
  -> model chooses tool call or final text
  -> model may choose the same tool again
```

That is a valid ADK pattern when the coordinator should remain conversational
and repeated calls are acceptable. It is also the pattern illustrated
conceptually by the market-research sample.

The implemented topology is:

```text
Workflow controller
  -> orchestrator emits typed decision
  -> Python validates and awaits specialist
  -> Python records completed target
  -> orchestrator receives explicit result set
  -> Python rejects any duplicate target
  -> orchestrator emits typed finish
  -> terminal renderer ends graph
```

The second topology is preferred for this example because the motivating
requirement is stronger than “encourage the model not to repeat a tool.” It is
“make repeated specialist execution impossible within one invocation.”

## Alternatives considered

| Alternative | Decision | Reason |
| --- | --- | --- |
| Chat-mode `sub_agents` | Reject | This transfers conversational ownership and implements supervisor routing rather than call/return specialization. |
| `mode="single_turn"` sub-agents under an LLM root | Reject for this bounded example | This is ADK's recommended simple agent-tool pattern, but the root model still owns an open-ended tool loop and may select a completed specialist again. |
| Explicit `AgentTool(agent=...)` instances | Reject | Direct use is discouraged by the current SDK and retains the same parent tool-loop termination problem. |
| `SequentialAgent`, `ParallelAgent`, or `LoopAgent` | Reject | These orchestration shells are deprecated in favor of `Workflow`. |
| Fixed Workflow chain through math, science, and English | Reject | It would run irrelevant specialists and remove the orchestrator's selective judgment. |
| Static conditional graph with a single routing pass | Reject | Mixed requests may require a later routing choice after inspecting an earlier specialist result. |
| Parallel fan-out and `JoinNode` | Reject for this example | Specialist work may be dependent, and not every request needs every specialist. Parallel fan-out would perform unnecessary work. |
| Unbounded `while True` controller | Reject | Completion would again depend entirely on model compliance. |
| Persistent session state for completed outputs | Reject | Results are invocation-local and already returned directly by `ctx.run_node()`; persistent keys could leak stale context into later requests. |
| Root Workflow plus bounded dynamic controller | Adopt | It preserves selective model judgment while making execution, uniqueness, and termination runtime properties. |

## Recommended usage boundaries

Use this workflow-native pattern when:

- the main agent must retain final-response ownership;
- specialists should behave as typed, awaited capabilities;
- specialist selection is conditional or depends on earlier specialist output;
- repeated specialist execution must be prevented or bounded structurally;
- a single deterministic terminal response is important;
- execution traces should show explicit parent/child call paths.

Use ADK's simpler `mode="single_turn"` plus `sub_agents=[...]` pattern when:

- the coordinator is intentionally conversational;
- the model may freely decide how many tool calls to make;
- repeated calls are useful or harmless;
- a Python-owned orchestration policy would add unnecessary complexity.

Use chat-mode sub-agents when the specialist should actually take over the
conversation and communicate with the user directly.

Use `mode="task"` when a delegated child must conduct a multi-turn task or ask
the user for missing information before returning a typed result.

Use Workflow fan-out and `JoinNode` when several independent branches are all
required and can safely run concurrently. Do not simulate that requirement by
sequentially prompting an orchestrator to call every agent.

## Extension points

### Add or replace a specialist

A new specialist requires coordinated changes to:

1. `SpecialistName` in [`schemas.py`](orchestrator_agent/schemas.py);
2. the new `Agent(...)` declaration;
3. `_SPECIALISTS` in [`agent.py`](orchestrator_agent/agent.py);
4. the orchestrator's routing instruction;
5. `MAX_ORCHESTRATOR_STEPS`, which must remain at least the maximum number of
   one-time specialist calls plus one final decision;
6. focused tests for selection, result propagation, and duplicate prevention.

### Give a specialist tools

Function tools, MCP toolsets, or other capabilities may be added to an
individual specialist's `tools` list without changing orchestration ownership.
The specialist remains a single awaited node and must still produce
`SpecialistResult`.

If a tool introduces human-in-the-loop waiting or irreversible side effects,
the controller's replay and idempotency behavior must be reconsidered rather
than assuming the current non-interactive specialist contract is sufficient.

### Enrich the contracts

`SpecialistTask`, `SpecialistResult`, and `OrchestratorResult` may gain typed
fields for citations, confidence, calculations, or evaluation metadata. The
controller should continue validating immediately at each boundary instead of
passing raw JSON strings.

### Change the model

Update [`orchestrator_agent/settings.py`](orchestrator_agent/settings.py) to
change the shared model. If routing quality and specialist capability need
different cost or latency profiles, replace the single setting with explicit
per-agent model settings while retaining deterministic controller behavior.

### Add concurrency

Concurrency is not a local flag change. If the orchestrator must select a set
of independent specialists together, introduce an explicit fan-out/fan-in
contract and join their serializable outputs. Preserve the one-terminal-output
rule and do not share mutable state between concurrent branches.

## Extension constraints

The following properties define the design contract and should not be changed
casually:

- Do not make a specialist `mode="chat"`; that changes call/return into
  conversational ownership transfer.
- Do not put specialists into the root agent's `sub_agents` list unless the
  architecture intentionally returns to an LLM-owned tool or transfer loop.
- Do not remove the closed target map or duplicate check if one-call-per-agent
  behavior remains a requirement.
- Do not remove the hard step budget. Increasing the specialist set requires a
  deliberate corresponding budget change.
- Do not reuse a numeric or unstable dynamic `run_id`; replay correlation
  depends on stable child identity.
- Do not wrap `ctx.run_node()` in `asyncio.create_task()`; the ADK API warns
  that this makes execution unsupervised.
- Do not remove `rerun_on_resume=True` from a node that dynamically schedules
  children.
- Do not rely on shared conversation history to move specialist results; keep
  result propagation explicit and typed.
- Do not emit user-visible answers from specialist nodes. `render_result` is
  the terminal presentation boundary.
- Do not yield more than one successful controller output; multiple outputs
  change downstream input aggregation semantics.

## Operational limitations

- Specialists run sequentially, so latency is additive for mixed requests.
- The example permits at most three specialist calls and one final decision.
- A specialist can contribute only one result per invocation.
- There is no automatic retry for malformed model output beyond behavior
  provided internally by the model/API layer; schema failure reaches the
  workflow boundary.
- There is no human-in-the-loop interaction below the root.
- There are no external data or calculation tools, so specialist answers rely
  solely on model knowledge and reasoning.
- Results are invocation-local; no cross-turn memory or persistence is
  implemented.
- The orchestrator can still make a poor but schema-valid routing or synthesis
  decision. The workflow constrains control flow, not semantic correctness.
- `use_sub_branch=True` isolates dynamic events, but the example does not expose
  a custom tracing or evaluation UI for those branches.

## Deterministic validation

[`tests/test_orchestrator_agent.py`](tests/test_orchestrator_agent.py) uses
scripted `BaseLlm` implementations with the public `InMemoryRunner`. It proves:

- a science request runs only `science`;
- the science result appears in the orchestrator's next typed input;
- the workflow, not the specialist, authors the one final response;
- a mixed request can call math and English sequentially before finishing;
- each selected specialist runs exactly once;
- a second selection of the same specialist raises at the workflow boundary;
- no function calls, transfer actions, or long-running tool IDs represent
  delegation.

Validation commands:

```text
python -m compileall -q orchestrator_agent tests/test_orchestrator_agent.py
python -m pytest tests/test_orchestrator_agent.py -q
python -m pytest -q
```

At implementation time, the focused suite passed 3 tests and the complete
repository suite passed 7 tests plus 7 subtests.

## Source index

### Local implementation and evidence

- [`orchestrator_agent/agent.py`](orchestrator_agent/agent.py) — agents,
  controller, dynamic calls, terminal renderer, and root Workflow.
- [`orchestrator_agent/schemas.py`](orchestrator_agent/schemas.py) — all typed
  call, decision, result, and workflow contracts.
- [`orchestrator_agent/settings.py`](orchestrator_agent/settings.py) — shared
  model configuration.
- [`orchestrator_agent/README.md`](orchestrator_agent/README.md) — concise usage
  guide and runnable prompt.
- [`tests/test_orchestrator_agent.py`](tests/test_orchestrator_agent.py) —
  deterministic public-runner regression coverage.
- [`hierarchical-agent-implementation-plan.md`](hierarchical-agent-implementation-plan.md)
  — trace-backed rationale for moving bounded orchestration and termination out
  of an open-ended parent tool loop.

### Current ADK 2.7.1 source and guidance

- [`AgentTool`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/tools/agent_tool.py)
- [`Context.run_node()`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py)
- [`Workflow`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_workflow.py)
- [`LLM agent Workflow wrapper`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/workflow/_llm_agent_wrapper.py)
- [`LlmAgent`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/llm_agent.py)
- [`Schema validation`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/utils/_schema_utils.py)
- [`Event`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/events/event.py)
- [`Task and single-turn guidance`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/task-mode.md)
- [`Multi-agent and deprecation guidance`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/multi-agent.md)
- [`Workflow best practices`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/.agents/skills/adk-agent-builder/references/best-practices.md)

### Exemplar patterns

- [`input_output_schema`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/core/input_output_schema/agent.py)
  — typed single-turn agent used as a coordinator capability.
- [`dynamic_nodes`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/dynamic_nodes/agent.py)
  — dynamic awaited LLM nodes inside Python control flow.
- [`use_as_output`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/use_as_output/agent.py)
  — dynamic agent result propagated through a Workflow.
- [`market-research-agent`](https://github.com/google/adk-samples/blob/30bb11aa40db0705a11859eda2cc3fa7ca7dfa7e/contrib/python/market-research-agent/app/agent.py)
  — conceptual orchestrator-calls-specialists-and-synthesizes pattern.
- [`dynamic workflow pattern`](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/graph-workflows/examples/08_dynamic.py)
  — bounded dynamic scheduling.
- [`custom orchestrator pattern`](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/collaborative-workflows/examples/04_custom_orchestrator.py)
  — programmatic control over multiple LLM specialists.

## Final rule

> The orchestrator LLM chooses expertise and synthesizes the answer; the
> Workflow owns execution and termination. Every specialist is an awaited,
> typed call whose result returns to the orchestrator, and Python enforces the
> capability set, one-call-per-specialist rule, step budget, and single terminal
> response.

## Concise architectural justification

The implementation provides agent-as-tool behavior without giving a specialist
the conversation or leaving repeated tool selection inside an unbounded parent
LLM loop. A root `Workflow` runs one controller. On each step, the
`orchestrator` agent either selects `math`, `science`, or `english`, or returns
the final answer. The controller awaits the selected specialist with
`Context.run_node()`, records its result, prevents that specialist from running
again, and passes all completed results into the orchestrator's next decision.
The terminal node alone renders the response.

The distinct native ADK structures are `Workflow` for a deterministic terminal
boundary and `@node(rerun_on_resume=True)` with `Context.run_node()` for
supervised dynamic call/return. The orchestrator and specialists use
`mode="single_turn"` because each invocation is a bounded task rather than a
conversation. Structured task, decision, and result contracts define the data
passed between these calls; the logic does not depend on shared chat history or
persistent state.

The main references are ADK's
[`Context.run_node()` API](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/context.py#L424-L477)
and official
[`dynamic_nodes` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/workflows/dynamic_nodes/agent.py),
which establish awaited dynamic execution inside Workflow-owned control. The
official
[`input_output_schema` sample](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/contributing/samples/core/input_output_schema/agent.py)
provides the typed specialist call/return precedent. The
[`market-research-agent`](https://github.com/google/adk-samples/blob/30bb11aa40db0705a11859eda2cc3fa7ca7dfa7e/contrib/python/market-research-agent/app/agent.py)
provides the conceptual pattern of an orchestrator consulting specialists and
synthesizing their results, while the
[`custom orchestrator` example](https://github.com/rominirani/adk-workflow-patterns/blob/63584e6ce1aa6d8fd70802058cdd857f075d9f4f/collaborative-workflows/examples/04_custom_orchestrator.py)
supports programmatic ownership of routing and completion.

The main alternatives were rejected as follows:

- Chat-mode sub-agents were unsuitable because they transfer response
  ownership.
- Automatic single-turn sub-agent tools are appropriate for a simple
  conversational coordinator, but the parent LLM may still repeat a completed
  call.
- Explicit `AgentTool(...)` was not used because current
  [`AgentTool` guidance](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/tools/agent_tool.py#L108-L127)
  discourages direct construction and it retains the same parent tool loop.
- A fixed sequence would run irrelevant specialists; parallel fan-out would be
  incorrect when later work depends on earlier output.
- An unbounded dynamic loop was rejected because termination would again rely
  only on model compliance.

The resulting split is deliberate: the LLM owns expertise selection and final
synthesis, while the Workflow owns execution, uniqueness, and termination.
