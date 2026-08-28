# Hierarchical agents in ADK 2.7.1

## Final as-built architecture and rationale

**Status:** Implemented and deterministically validated

**Runtime:** `google-adk==2.7.1`

## Decision

The hierarchy is implemented as a root `Workflow`, not a chat agent with
department tools. The root Workflow runs a bounded single-turn manager, invokes
only the departments selected by that manager, and ends at a deterministic
rendering node. Each department uses the same pattern for its specialists.

```text
user
  -> hello_agent Workflow
       -> root_controller
            -> root_manager [one decision]
                 -> finish, or delegate one necessary department
            -> selected department Workflow
                 -> department controller
                      -> department manager [one decision]
                           -> finish, or delegate one necessary specialist
                      -> selected specialist [one result]
                      -> department manager [next decision]
                 <- one department result
            -> root_manager [next decision]
       -> render_result
  <- one final user-visible response
```

The logical manager at each level is a composite:

```text
manager = deterministic controller + bounded LLM decision agent
```

The LLM decides which capability is needed next. Python owns target validation,
execution, result validation, duplicate prevention, the step budget, and
termination. This keeps model-driven judgment while making call/return behavior
a runtime property rather than a prompt convention.

## Why this change was necessary

The prior implementation fixed the web UI's false input request by hiding each
department Workflow behind an ordinary async function tool. It did not fix the
root control-flow contract. The root was still a chat-mode LLM agent in an
open-ended function-calling loop.

[`latest_trace.json`](latest_trace.json) shows the failure boundary clearly:

1. `hello_agent` calls `writing_department`.
2. The writing manager runs the writer and proofreader.
3. The department Workflow completes.
4. `hello_agent` receives the matching `writing_department` function response.
5. There is no final root text event after that response.

At that point the root has not structurally terminated. It must be called again
so the model can decide whether to answer or call another tool. A prompt can ask
it to answer, but cannot prevent the observed re-entry into the same child.

The old department controller had a second independent defect. It encoded
required sequences such as `researcher -> summarizer` and
`writer -> proofreader`, rejected an early manager `finish`, and required every
worker to complete. That was a fixed sequential pipeline presented as a
hierarchy. It was not selective delegation and it was not fan-out/fan-in.

The refined implementation removes both defects structurally:

- the root is a Workflow with a terminal node, so there is no parent LLM tool
  loop after the final result;
- managers may finish after any sufficient result, including after one child;
- only an explicit `delegate` decision causes a child to run;
- completed children cannot be selected again in the same hierarchy call;
- no parallel execution is implied or performed.

## Selective routing, not fan-out/fan-in

This pattern is intentionally sequential and conditional. On each manager step,
exactly one of two things happens:

```text
delegate(target) -> await exactly that target -> inspect its result -> decide
finish(result)   -> return the result immediately
```

The root manager can choose:

- only `research_department`;
- only `writing_department`;
- research followed by writing when the latter needs researched input;
- neither department when it can finish directly from the request or completed
  results.

The research manager can choose `researcher`, `summarizer`, both in a justified
sequence, or finish. The writing manager can choose `writer`, `proofreader`,
both, or finish. A proofreading-only request therefore does not run the writer,
and an analysis or drafting request does not automatically run the proofreader.

Fan-out/fan-in is a different contract: multiple branches are selected together,
run concurrently, and join at an explicit synchronization node. If a future use
case genuinely requires independent parallel specialists, it should use ADK
Workflow fan-out plus `JoinNode`. It should not be simulated by this manager
loop or enabled merely to make all children run.

## Why the root is now a Workflow

A chat agent with function tools naturally remains in an LLM loop after a tool
response. That is useful for an open-ended assistant but wrong for this bounded
hierarchy because the same model decides both whether to call a child and
whether execution is complete.

The root Workflow separates those responsibilities:

- `root_manager` produces one schema-controlled decision per invocation;
- `root_controller` executes that decision and owns the bounded loop;
- `render_result` emits the final user-visible message and terminal output;
- Workflow graph completion ends the invocation without another model round.

This also removes every root-level tool wrapper. A Workflow placed directly in
an agent's `tools` is wrapped by ADK as a long-running `NodeTool`, which caused
the earlier web UI input prompt. The async adapter avoided that metadata but
left the chat loop in place. The final design needs neither `NodeTool` nor the
adapter, so it avoids both failure modes.

## Explicit state and stateless agents

Every LLM agent has:

```python
mode="single_turn"
include_contents="none"
output_key="temp:<specific_output>"
```

No manager or specialist relies on inherited conversation history. Controllers
publish the exact current request, available targets, selected instruction, and
completed results through `Event(state=...)`. Agent instructions read those
values through ADK's supported `{state_key}` placeholders.

For example, the writer receives its complete working context through:

```text
{temp:writing_department_request}
{temp:writing_department_selected_instruction}
{temp:writing_department_context}
{temp:writing_department_completed_results}
```

The proofreader additionally receives `{temp:writer_output?}` when the writer
was selected. The writing manager receives both optional specialist output keys.
The root manager receives optional department output keys, so a research result
can be incorporated into a later writing instruction explicitly.

The `temp:` prefix is important. These values exist only for the current ADK
invocation and are not persisted into later user turns. This prevents a new
request from accidentally consuming stale manager decisions or child results.
State is written through events rather than direct `ctx.state` mutation so the
data flow remains visible in traces and compatible with Workflow replay.

`output_key` is not used as an implicit return channel. The immediate caller
still receives and validates the value returned by `await ctx.run_node(...)`.
The named state output exists so later agents can consume the same result
explicitly in their instructions.

## Call/return and termination invariants

The implementation enforces the following:

1. Every LLM call is a stateless, schema-controlled single turn.
2. Every delegation is an awaited `ctx.run_node()` call.
3. A manager returns one `ManagerDecision`; it never calls children itself.
4. A controller accepts only names from its closed target mapping.
5. A completed target cannot run again in the same controller invocation.
6. A manager may finish as soon as available results are sufficient.
7. Every controller has a hard `MAX_MANAGER_STEPS` budget.
8. Child outputs are validated as `OutputPlaceholder` immediately.
9. Each controller yields exactly one successful hierarchy result.
10. The root's terminal renderer emits exactly one user-visible answer.
11. No agent has tools or sub-agents, and no transfer represents return.
12. No event should carry `long_running_tool_ids` in this non-interactive flow.

Stable non-numeric `run_id` values identify every manager step and delegated
child. `use_sub_branch=True` isolates dynamic child events while preserving
their trace paths.

## Generic data contracts

The hierarchy uses only three reusable Pydantic models instead of separate
fact, risk, recommendation, draft, and proofread result classes:

```python
class InputPlaceholder(BaseModel):
  text: str
  context: dict[str, str] = Field(default_factory=dict)


class OutputPlaceholder(BaseModel):
  text: str
  details: list[str] = Field(default_factory=list)


class ManagerDecision(BaseModel):
  action: Literal["delegate", "finish"]
  target: str = ""
  instruction: str = ""
  result: str = ""
```

`InputPlaceholder` and `OutputPlaceholder` define generic hierarchy boundaries.
`ManagerDecision` is shared by the root and both department managers; `target`
works for either a department or a specialist. Domain-specific validity remains
in the controller's closed target map rather than proliferating schema classes.

## Implemented topology and file layout

```text
hello_agent/
  agent.py                       # controllers, Workflows, final renderer
  schemas.py                     # three generic contracts
  settings.py                    # model selection
  agents/
    root_manager.py              # root_manager = Agent(...)
    research_manager.py          # research_manager = Agent(...)
    researcher.py                # researcher = Agent(...)
    summarizer.py                # summarizer = Agent(...)
    writing_manager.py           # writing_manager = Agent(...)
    writer.py                    # writer = Agent(...)
    proofreader.py               # proofreader = Agent(...)
tests/
  test_agent.py                  # deterministic hierarchy regressions
```

Each agent is declared directly in its own module. There are no agent factory
or `make_*` helpers. [`hello_agent/agent.py`](hello_agent/agent.py) contains only
orchestration: the reusable bounded hierarchy loop, the three controllers, the
two nested department Workflows, the final renderer, and root Workflow wiring.

## Controller behavior

The reusable controller routine performs the same protocol at every hierarchy
level:

```python
for step in range(MAX_MANAGER_STEPS):
  yield Event(state=current_explicit_context)
  decision = ManagerDecision.model_validate(
      await ctx.run_node(manager, run_id=stable_step_id)
  )

  if decision.action == "finish":
    yield OutputPlaceholder(text=decision.result)
    return

  target = allowed_targets[decision.target]
  reject_if_already_completed(target)
  yield Event(state={selected_instruction_key: decision.instruction})
  completed[decision.target] = OutputPlaceholder.model_validate(
      await ctx.run_node(target, run_id=stable_child_id)
  )
```

Department Workflows receive a typed `InputPlaceholder`. Specialists receive
their task through explicit state placeholders. When a nested department
returns, the parent controller records its output under the department's named
temporary state key for the next root-manager decision.

## Error, resume, and interaction policy

Unknown targets, duplicate targets, empty finish results, invalid child output,
and exhausted step budgets fail visibly at the controller boundary. They do not
cause transfer, recursive re-entry, or silent fallback to another agent.

Controllers use `rerun_on_resume=True`, as required for nodes that dynamically
call `ctx.run_node()`. Dynamic calls are awaited directly and are never placed
in unsupervised `asyncio` tasks.

This hierarchy is deliberately non-interactive below the root. A child must not
yield `RequestInput`. If a future capability requires human approval or missing
information, it should be exposed as a separate explicit HITL boundary with
stable interrupt IDs, resumability, and idempotent external side effects.

## Deterministic validation

The tests use scripted models against the installed ADK 2.7.1 runtime. They
prove behavior through the public runner and emitted events:

- a writing request selects only `writer`, does not call `proofreader`, and
  renders exactly one final answer;
- a correction request selects `proofreader` without running `writer`;
- a research result appears in the later writer's resolved system instruction
  through explicit state;
- every LLM agent uses `mode="single_turn"`, `include_contents="none"`, and an
  invocation-scoped `output_key`;
- no execution emits function calls, transfers, errors, or
  `long_running_tool_ids`.

Validation command:

```text
python -m unittest tests.test_agent -v
```

All four tests pass. The source also compiles with:

```text
python -m compileall -q hello_agent tests
```

`pytest` and `pyink` are not installed in the current project environment, so
those commands were not used.

## Alternatives considered

| Alternative | Decision | Reason |
|---|---|---|
| Recursive conversational transfer | Reject | Transfer changes active conversational ownership; it does not unwind a call. |
| Fixed sequential pipeline | Reject | It runs every child and prevents the manager from deciding what is necessary. |
| Parallel fan-out/fan-in | Reject for this use case | The selected work is conditional and may depend on earlier results; concurrent execution would run unnecessary children. |
| Chat root with direct Workflow tools | Reject | ADK wraps Workflows as long-running `NodeTool` instances, making the web UI request input. |
| Chat root with async Workflow adapters | Reject | It fixes long-running metadata but leaves the root in an open-ended post-tool LLM loop. |
| Manager agents with worker tools | Reject | The model would own an unbounded tool loop and completion would again depend on prompt compliance. |
| Shared conversation history | Reject | Parent and child events can influence later decisions implicitly and recreate re-entry behavior. |
| Persistent state as a mailbox | Reject | It can leak stale outputs across turns and obscures invocation identity. |
| Root Workflow plus selective controllers | Adopt | It provides explicit termination, conditional routing, typed call/return, traceable state, and bounded execution. |

## Final rule

> LLMs decide one next action; Workflows and controllers own execution and
> termination. Every child runs only after an explicit selective decision, all
> information is passed through invocation-scoped state or a validated return
> value, and the root has one deterministic terminal response.

## References

### Local implementation and evidence

- [`latest_trace.json`](latest_trace.json) — prior run ending at the root's
  department function response without a final root text event.
- [`hierarchical-agent-final-investigation.md`](hierarchical-agent-final-investigation.md)
  — original control-flow investigation and rejected recursive designs.
- [`hello_agent/agent.py`](hello_agent/agent.py) — selective controllers,
  nested Workflows, state events, and deterministic terminal renderer.
- [`hello_agent/agents/`](hello_agent/agents/) — one directly configured LLM
  agent per module.
- [`hello_agent/schemas.py`](hello_agent/schemas.py) — generic input, output,
  and manager-decision contracts.
- [`tests/test_agent.py`](tests/test_agent.py) — deterministic regression tests.

### ADK source and official samples

- [`Context.run_node()`](../adk-references/adk-python/src/google/adk/agents/context.py)
  — public dynamic-node call/return API.
- [`Workflow`](../adk-references/adk-python/src/google/adk/workflow/_workflow.py)
  — graph scheduling, dynamic-node supervision, terminal output, and replay.
- [`LLM agent Workflow wrapper`](../adk-references/adk-python/src/google/adk/workflow/_llm_agent_wrapper.py)
  — single-turn node input/output behavior and chat wrapper dispatch loop.
- [`LLM content construction`](../adk-references/adk-python/src/google/adk/flows/llm_flows/contents.py)
  — `include_contents` and branch-filtered model context.
- [`Instruction state injection`](../adk-references/adk-python/src/google/adk/utils/instructions_utils.py)
  — `{state_key}` and optional `{state_key?}` placeholder resolution.
- [`LlmAgent.output_key`](../adk-references/adk-python/src/google/adk/agents/llm_agent.py)
  — validated agent output written into session state.
- [`State scopes`](../adk-references/adk-python/src/google/adk/sessions/state.py)
  — session, application, user, and invocation-only `temp:` state semantics.
- [`Event`](../adk-references/adk-python/src/google/adk/events/event.py)
  — persisted state deltas, node outputs, and user-visible message content.
- [`Node schema validation`](../adk-references/adk-python/src/google/adk/utils/_schema_utils.py)
  — validation and serialization of typed node inputs and outputs.
- [`NodeTool`](../adk-references/adk-python/src/google/adk/tools/_node_tool.py)
  — node-as-tool wrapping and long-running tool behavior.
- [`InMemoryRunner`](../adk-references/adk-python/src/google/adk/runners.py)
  — public invocation, event persistence, and deterministic test boundary.
- [`Dynamic nodes sample`](../adk-references/adk-python/contributing/samples/workflows/dynamic_nodes/agent.py)
  — `Event(state=...)`, instruction placeholders, and bounded
  `ctx.run_node()` orchestration.
- [`use_as_output sample`](../adk-references/adk-python/contributing/samples/workflows/use_as_output/agent.py)
  — root Workflow with deterministic node output propagation.
- [`Custom orchestrator pattern`](../adk-references/adk-workflow-patterns/collaborative-workflows/examples/04_custom_orchestrator.py)
  — programmatic manager routing using explicit state and selected execution.

Legacy sequential, parallel, loop-agent, and direct `AgentTool` implementations
were not used as the target architecture.

## Implementation-to-source traceability

This matrix records why each important implementation choice exists and the
source that supports it. It does not imply that the application copies one
sample wholesale: framework source establishes runtime behavior, official
samples establish supported composition patterns, and the local trace and
tests determine which combination is appropriate here.

| Implemented component or invariant | Source or precedent | Alignment and local justification |
|---|---|---|
| `root_agent = Workflow(...)` | [`Workflow`](../adk-references/adk-python/src/google/adk/workflow/_workflow.py) and the [`use_as_output` sample](../adk-references/adk-python/contributing/samples/workflows/use_as_output/agent.py) | ADK Workflows provide supervised graph completion and a terminal output. The application uses that boundary at the root so completion is structural and cannot fall back into another parent model/tool round. |
| `render_result` as the only terminal UI node | [`Event`](../adk-references/adk-python/src/google/adk/events/event.py) and the ADK rule that message content is distinct from node output | The function emits both the user-visible message and the validated terminal value once. This prevents internal manager JSON from becoming the intended final UI response and avoids requiring a final chat-agent turn. |
| `root_controller`, `research_controller`, and `writing_controller` | [`Context.run_node()`](../adk-references/adk-python/src/google/adk/agents/context.py) and the [`dynamic_nodes` sample](../adk-references/adk-python/contributing/samples/workflows/dynamic_nodes/agent.py) | Each controller awaits a selected child and receives its return value through the supported dynamic-node API. Child events remain traceable, while Python retains control of routing, validation, and completion. |
| Generic `_run_hierarchy` bounded loop | [`dynamic_nodes` sample](../adk-references/adk-python/contributing/samples/workflows/dynamic_nodes/agent.py) and the supporting [`custom orchestrator` pattern](../adk-references/adk-workflow-patterns/collaborative-workflows/examples/04_custom_orchestrator.py) | Both demonstrate programmatic routing after inspecting state or child results. The local implementation generalizes that pattern across root and department levels, adds a closed target map, duplicate prevention, typed decisions, and a hard step budget. |
| One `ManagerDecision` per manager call | [`LLM agent Workflow wrapper`](../adk-references/adk-python/src/google/adk/workflow/_llm_agent_wrapper.py) and [`Node schema validation`](../adk-references/adk-python/src/google/adk/utils/_schema_utils.py) | A single-turn agent node produces one validated value for its caller. The shared `delegate`/`finish` contract limits the model to choosing the next action; it cannot execute children or control the loop itself. |
| Selective execution rather than fixed sequence or fan-out | [`Context.run_node()`](../adk-references/adk-python/src/google/adk/agents/context.py) and the local requirements proved in [`tests/test_agent.py`](tests/test_agent.py) | Imperative dynamic calls allow exactly one chosen child to run. This is why proofreading can run without writing and why an unnecessary summarizer is skipped. Parallel fan-out is reserved for a future case where all selected branches are independently required. |
| `include_contents="none"` on every LLM agent | [`LLM content construction`](../adk-references/adk-python/src/google/adk/flows/llm_flows/contents.py) | ADK then excludes prior conversation history and builds only the current turn context. This prevents child events and earlier parent tool history from silently influencing a later routing decision. |
| `{temp:...}` placeholders in agent instructions | [`Instruction state injection`](../adk-references/adk-python/src/google/adk/utils/instructions_utils.py) and the [`dynamic_nodes` sample](../adk-references/adk-python/contributing/samples/workflows/dynamic_nodes/agent.py) | ADK explicitly resolves required and optional state variables in instructions. The application uses this supported mechanism to make every request, selected instruction, prior result, and available-target list visible and auditable. |
| Invocation-scoped `output_key="temp:..."` | [`LlmAgent.output_key`](../adk-references/adk-python/src/google/adk/agents/llm_agent.py) and [`State scopes`](../adk-references/adk-python/src/google/adk/sessions/state.py) | Each validated LLM result is named for later placeholder injection, while `temp:` prevents it from leaking into later user turns. The immediate caller still validates the direct `run_node()` return, so state is context rather than an implicit RPC mailbox. |
| State writes through `Event(state=...)` | [`Event`](../adk-references/adk-python/src/google/adk/events/event.py), [`Workflow`](../adk-references/adk-python/src/google/adk/workflow/_workflow.py), and the [`dynamic_nodes` sample](../adk-references/adk-python/contributing/samples/workflows/dynamic_nodes/agent.py) | Event-backed deltas are visible in traces and participate in Workflow replay. Direct unrecorded state mutation would make the data flow harder to audit and less reliable during replay. |
| `InputPlaceholder`, `OutputPlaceholder`, and `ManagerDecision` | [`Node schema validation`](../adk-references/adk-python/src/google/adk/utils/_schema_utils.py) | ADK validates typed node boundaries and serializes Pydantic values. Three generic contracts retain that safety without multiplying equivalent domain-specific output classes. |
| Stable non-numeric `run_id` plus `use_sub_branch=True` | [`Context.run_node()`](../adk-references/adk-python/src/google/adk/agents/context.py) and [`Workflow`](../adk-references/adk-python/src/google/adk/workflow/_workflow.py) | Stable paths allow ADK to correlate and deduplicate dynamic executions during replay. Sub-branches isolate child event histories while preserving observability in the trace. |
| No root function tools, `NodeTool`, transfers, or agent `sub_agents` | [`latest_trace.json`](latest_trace.json), [`NodeTool`](../adk-references/adk-python/src/google/adk/tools/_node_tool.py), and the [`LLM agent Workflow wrapper`](../adk-references/adk-python/src/google/adk/workflow/_llm_agent_wrapper.py) | The trace showed that the async adapter still ended at a root function response without structural completion; direct node tools also expose long-running semantics. Removing the tool/transfer layer eliminates both the false UI input boundary and the open-ended parent re-entry path. |
| Public-runner regression tests | [`InMemoryRunner`](../adk-references/adk-python/src/google/adk/runners.py) and [`tests/test_agent.py`](tests/test_agent.py) | Tests exercise the same invocation and event surface used by the application. They assert selective child execution, one terminal answer, explicit state injection, no transfers, and no long-running tool metadata rather than asserting private controller internals. |

The table also defines the change boundary: replacing the manager prompts or
specialist capabilities is safe when the contracts remain intact, but restoring
a chat root, implicit history, unbounded tool loops, persistent result mailboxes,
or mandatory execution of every child would invalidate the trace-backed design.
