# Hierarchical call/return agents in ADK 2.7.1

## Consolidated investigation and architectural judgment

This document consolidates four prior analyses of the `hello_agent` experiment:

- `investigation_1.md`
- `investigation_2.md`
- `adk2-hierarchical-agents.md`
- `deep-research-report.md`

It also grounds the conclusions in the supplied `session_export.json`, the current `hello_agent/agent.py`, and the locally installed `google-adk==2.7.1` runtime.

The purpose is not to present a finished implementation. It is to explain why a seemingly simple three-level hierarchy is difficult to implement correctly, identify what is demonstrably failing, and define the architecture and tests that should guide the next prototype.

---

## Executive conclusion

The present implementation is not failing because of rate limits, one bad prompt, or one isolated transfer bug. It combines two incompatible control-flow models:

1. **Call-like delegation:** a `single_turn` child is exposed as a tool and run through `ctx.run_node()`.
2. **Conversational transfer:** the child calls `transfer_to_agent()` to hand execution to its parent.

Those operations do not have the same return semantics. A tool or node call is expected to complete and yield one result to its caller. A transfer changes the active agent and continues execution; it does not unwind a call stack.

In this experiment, a leaf tries to “return” by transferring to `middle_b`. ADK resumes `middle_b` from inside the leaf's dynamic node execution. The resumed manager calls another leaf, which transfers back again, and the branch grows instead of unwinding:

```text
Expected

root
  calls middle_b
    middle_b calls leaf_b1
    leaf_b1 returns a result
    middle_b resumes
    middle_b calls leaf_b2
    leaf_b2 returns a result
    middle_b returns a result
  root resumes

Observed

root
  calls middle_b
    middle_b calls leaf_b1
      leaf_b1 transfers to middle_b
        middle_b calls leaf_b2
          leaf_b2 transfers to middle_b
            middle_b calls leaf_b1
              leaf_b1 transfers to middle_b
                ...
```

This also mixes output events from different agents inside the same node execution. Since an ADK node may set `Context.output` only once, a later output can produce:

```text
ValueError: Output already set. A node can produce at most one output.
```

The strongest architecture to test next is:

```text
explicit chat root
  -> Workflow exposed as a node tool
       -> explicit chat manager
            -> task-mode leaf
            <- task function response
            -> task-mode leaf
            <- task function response
       <- Workflow result
  <- root resumes
```

The essential invariant is:

> Internal child completion must return data to the immediate caller. It must never be represented as a transfer of conversational ownership.

---

## Scope and evidence

The analysis uses:

- [`hello_agent/agent.py`](hello_agent/agent.py), the current three-level agent definition.
- [`session_export.json`](session_export.json), a 71-event session export from one user request.
- [`adk2-hierarchical-agents.md`](adk2-hierarchical-agents.md), the first architectural research report.
- [`deep-research-report.md`](deep-research-report.md), the more extensive ADK 2.x research report.
- The installed ADK 2.7.1 implementation under `.venv/lib/python3.12/site-packages/google/adk/`.

Connection, timeout, quota, and `429 RESOURCE_EXHAUSTED` events are intentionally excluded from the causal diagnosis. They terminate and amplify the runaway execution, but the incorrect control flow is already established before the first quota error.

---

## The required semantics

The desired logical hierarchy is ordinary enough:

```text
root
├── research manager
│   ├── researcher
│   └── summarizer
└── writing manager
    ├── writer
    └── proofreader
```

The difficult requirement is not the tree itself. It is the desired execution contract:

1. The root owns the user conversation.
2. The root invokes a manager for a bounded job.
3. The manager invokes one or more workers.
4. Each worker returns exactly once to that manager.
5. The manager may inspect the result and explicitly invoke another worker.
6. The manager returns exactly once to the root.
7. A completed child is never resumed or re-entered unless its parent issues a new invocation.
8. Every invocation has one well-defined output owner.

Plain nested `sub_agents` describe ownership and discoverability, but they do not by themselves create a lexical call stack. That difference is the heart of the problem.

---

## What the current configuration actually builds

The helper in `hello_agent/agent.py` gives every middle and leaf agent:

```python
mode="single_turn"
input_schema=Placeholder
output_schema=Placeholder
output_key=f"{name}_output"
sub_agents=sub_agents or []
```

The root has no explicit mode and owns the two middle agents as `sub_agents`.

Under ADK 2.7.1, a `single_turn` sub-agent is automatically wrapped as an internal `_SingleTurnAgentTool`. Consequently, the instantiated topology is approximately:

```text
hello_agent
  tools:
    middle_a -> _SingleTurnAgentTool
    middle_b -> _SingleTurnAgentTool

middle_b [single_turn]
  tools:
    leaf_b1 -> _SingleTurnAgentTool
    leaf_b2 -> _SingleTurnAgentTool

leaf_b1 [single_turn]
leaf_b2 [single_turn]
```

This is important: downward delegation is already tool-like. It should return through a function response. No upward `transfer_to_agent()` is required.

The runtime's own `_SingleTurnAgentTool` documentation says it is used in a `mode='chat'` `LlmAgent`. The current experiment recursively places these tools beneath a `single_turn` manager. The constructor permits this shape, but the execution and output-processing code does not provide a safe recursive-manager contract for it.

There is a second problem. The leaf agents retain:

```python
disallow_transfer_to_parent = False
disallow_transfer_to_peers = False
```

For `task` and `single_turn` agents, ADK suppresses the prose instruction explaining transfer, but its target discovery can still expose the parent as a transfer target. The model therefore sees and calls `transfer_to_agent("middle_b")`.

---

## What the session trace proves

The user requested a correction and refinement of:

```text
the qucik brown fix
```

Ignoring quota errors, the relevant event sequence is:

| Event | Author | Action |
|---:|---|---|
| 1 | `hello_agent` | Calls `middle_b` |
| 2 | `middle_b` | Calls `leaf_b1` |
| 3–4 | `leaf_b1` | Calls `transfer_to_agent("middle_b")` |
| 5 | `middle_b` | Calls `leaf_b2` from inside the leaf branch |
| 6–7 | `leaf_b2` | Transfers to `middle_b` |
| 8 | `middle_b` | Calls `leaf_b1` again |
| 9–19 | leaf/manager | Repeats the same transfer and re-entry pattern |
| 20–25 | `middle_b` | Produces two structured responses in the nested execution |
| 26 onward | manager/leaves | Continues invoking workers instead of unwinding |

Before the first quota failure, the trace contains:

- 1 root-to-manager call;
- 11 leaf-agent calls;
- 11 `transfer_to_agent` calls;
- only 2 `set_model_response` calls.

The branch path grows monotonically, for example:

```text
middle_b@call_220645
.leaf_b1@call_408757
.leaf_b2@call_203700
.leaf_b1@call_168561
.leaf_b1@call_234604
...
```

A real return would complete a child branch and resume its existing caller. Instead, each transfer creates or continues execution deeper within the current dynamic call chain. The branch stack never pops.

### The leaves initially do no useful work

The first `leaf_b1` call does not draft anything. Its first model action is a transfer to `middle_b`. The first `leaf_b2` behaves the same way. Because the manager receives no proper function result, it attempts further delegations.

This is not reliably repairable through wording such as “return to the parent.” In transfer semantics, “return” commonly leads the model to call `transfer_to_agent(parent)`, which is precisely the wrong operation for a tool-style child invocation.

### Output ownership is already corrupted

The strongest event-level evidence is the structured output around events 20–25:

- An event authored by `middle_b` is recorded on the node path belonging to `leaf_b1`.
- Its state delta writes both `middle_b_output` and `leaf_b1_output`.
- `middle_b` subsequently emits another structured response: `"Task completed successfully."`

This shows that a transferred parent's model event is passing through the enclosing leaf's single-turn output processor and is being treated as output for more than one logical node.

The final session state contains:

```json
{
  "middle_b_output": {"placeholder": "Task completed successfully."},
  "leaf_b1_output": {"placeholder": "the quick brown fox"}
}
```

It does not contain a completed `hello_agent_output`, and it does not preserve a clean proofreader result. State therefore cannot be used to reconstruct a trustworthy call tree or final answer.

---

## Why `Output already set` belongs to the same failure

The exact `ValueError` is not present in the supplied JSON export, so its relationship to this trace is an evidence-based inference rather than a directly logged fact.

The runtime mechanics make the inference strong:

1. `run_llm_agent_as_node()` processes every non-partial model-content event from a `single_turn` agent run as a possible node output.
2. That processing does not require `event.author == agent.name`.
3. Nested agent and transfer events can therefore be promoted to the enclosing node's output.
4. `NodeRunner` copies every event output into `ctx.output`.
5. `Context.output` raises if it is assigned a second time during one node execution.

There are two complementary ways the observed architecture can reach this condition:

- A transferred manager's response is first interpreted as the enclosing leaf's result, followed by another response in that same dynamic execution.
- A nested child's response is interpreted as the `single_turn` manager's result, after which the manager produces its own final response.

The two `middle_b` responses visible in events 20–25 are therefore the “embryo” of the reported failure, but the JSON does not prove that those exact two events were assigned to the same `Context`. The runtime source and the cross-attributed state delta establish the broader output-ownership problem.

This distinction matters: the exception should not be fixed by weakening the one-output guard. That guard is protecting the node contract. The architecture must stop unrelated or nested model events from becoming multiple outputs of the same node.

---

## Why this pattern is unusually difficult in ADK 2

ADK exposes several mechanisms that all look like “delegation” at the configuration level but have different runtime contracts.

| Mechanism | Actual control behavior | Appropriate use |
|---|---|---|
| Chat `sub_agents` + `transfer_to_agent` | Changes active conversational agent; return is another model-directed transfer | Multi-turn ownership hand-off |
| `single_turn` sub-agent | Inline node/tool execution with a returned result | Simple bounded leaf call |
| `task` sub-agent | Dedicated task completion protocol and synthesized function response to the coordinator | Non-trivial leaf work with explicit completion |
| `AgentTool` | Older agent-as-tool call boundary, often with separate runner/session machinery | Compatibility and existing designs |
| `Workflow` / node tool | Graph-controlled bounded execution and explicit node output | Deterministic boundaries, nesting, routing, fan-out/fan-in |

The difficulty comes from several constraints interacting:

### 1. Tree structure does not imply stack semantics

`sub_agents=[...]` describes an agent relationship. In chat mode it enables transfer targets; in task or single-turn modes it can generate internal delegation tools. The same-looking tree therefore behaves differently depending on modes and execution context.

### 2. Transfer is not return

`transfer_to_agent(parent)` sounds like returning to the caller, but it is a routing action. Inside `ctx.run_node()`, ADK resolves the target and continues executing it. There is no lexical `return result` operation involved.

### 3. Reliable native task return is leaf-oriented

Task delegation has the desired immediate return behavior: it dispatches the child in an isolated scope, captures terminal task output, synthesizes a function response, and re-enters the same chat coordinator. But task agents are intended to remain leaves, so task mode cannot simply be applied recursively to every manager.

### 4. Recursive `single_turn` managers are not a safe substitute

Single-turn children are the preferred modern replacement for direct `AgentTool` in simple cases. That does not establish arbitrary recursive composition. The internal wrapper is documented for use beneath a chat parent, and generic single-turn output promotion is vulnerable to nested model events being mistaken for the manager's own result.

### 5. Workflow is a node, not an ordinary chat sub-agent

A `Workflow` gives the bounded scope needed around a manager, but it cannot simply be treated as another conversational sub-agent. It should be exposed to the caller through node-as-tool composition.

### 6. Events and outputs are separate channels

Events stream and persist messages, tool calls, transfers, state deltas, and errors. `Context.output` is the one result a node returns to its parent. A composite or nested agent may yield many events while still being allowed only one node output. Confusing message events with node outputs creates the reported exception.

### 7. Defaults are context-dependent

In the graph runtime, an `LlmAgent` with `mode=None` defaults to `single_turn`. A human-facing root intended to retain conversation history should not rely on an ambiguous default; it should declare `mode="chat"` explicitly.

### 8. Shared state is not an RPC protocol

`output_key` persists state. It is useful for durable workflow data but is weaker than a direct function response for identifying the result of the current child call. Stale, intermediate, or cross-attributed values can be mistaken for the active result.

---

## Assessment of the earlier reports

### Where all four analyses agree

The investigations and research reports converge on the following conclusions:

- Chat transfer is conversational routing, not call/return.
- A recursively nested transfer tree cannot guarantee stack-like return behavior.
- Child completion should produce a function response to its immediate caller.
- The current branch growth and repeated leaf execution are architectural, not rate-limit artifacts.
- `output_key` should not be the only mechanism carrying critical child results.
- Fixed routing, parallel fan-out, and nested bounded scopes belong in `Workflow` rather than in prompt-directed transfers.
- The eventual design needs execution-trace tests, not only answer-quality tests.

### Contribution and limitations of `adk2-hierarchical-agents.md`

The first report correctly identifies the key transfer-versus-call distinction and recognizes the task-leaf constraint. It also identifies `AgentTool` and `Workflow` as possible call boundaries.

Its recommendation is less suitable for a new ADK 2.7.1 design:

- It makes direct `AgentTool` the main coordinator-to-worker mechanism even though current ADK source explicitly discourages direct use in favor of native single-turn sub-agents.
- It allows root-level transfer as an acceptable default, which weakens the invariant that the root always owns the user conversation.
- It leans on `output_key` and session-state injection for result passing despite known ambiguity around delegated and intermediate outputs.
- Its statement that transfer-disallow flags themselves “guarantee return” is too strong. Those flags remove transfer routes; the actual return semantics come from the surrounding tool/task execution protocol.
- Some validation claims appear version-dependent and do not match what this installed 2.7.1 configuration currently accepts.

The report remains useful as a catalogue of mechanisms and historical pitfalls, but its template should not be treated as the final target.

### Contribution and limitations of `deep-research-report.md`

The second report provides the strongest architectural direction:

```text
chat root
  -> Workflow/node tool
       -> chat manager
            -> task leaves
```

This aligns with the installed runtime:

- The chat-agent workflow wrapper contains an outer dispatch loop for sequential task calls.
- Task dispatch runs the leaf through `ctx.run_node()` with a function-call-specific branch and isolation scope.
- Completion is converted into a function response visible to the originating manager.
- The manager is re-entered after that response and can explicitly issue another task.
- A Workflow can create the bounded manager scope and return one result to the root as a node tool.

Its main caveat is stated honestly: there is no cited official sample combining every piece into this complete arbitrary-hierarchy pattern. It is a well-grounded synthesis that still needs a local proof.

Its implementation template also needs one correction. The human-facing root is described as chat mode but does not explicitly set `mode="chat"`. In ADK's graph runtime, an unset mode defaults to `single_turn`, so the prototype should make the root mode explicit.

### Combined judgment of the two investigations

Both investigations independently read the trace as a non-popping branch stack and an infinite transfer/re-delegation loop. Both connect the separately observed `Output already set` error to re-entry and confused output ownership.

The most precise combined diagnosis is:

> The implementation recursively composes single-turn node calls in a shape intended for chat coordinators, while still allowing chat-style upward transfer. The upward transfer re-enters the manager instead of completing the child call. Nested events are then promoted across node boundaries, allowing one logical execution to acquire more than one output.

---

## Recommended architecture for the next prototype

### Logical shape

```text
ROOT AGENT
mode="chat"
owns the user conversation
│
├── calls research_department as a Workflow/node tool
│     └── research_manager, mode="chat"
│           ├── researcher, mode="task"
│           └── critic, mode="task"
│
└── calls writing_department as a Workflow/node tool
      └── writing_manager, mode="chat"
            ├── writer, mode="task"
            └── proofreader, mode="task"
```

### Runtime shape

```text
user
  -> root
       -> calls writing_department
            -> Workflow starts writing_manager
                 -> delegate writer(task)
                      -> writer uses tools/model as needed
                      -> writer calls finish_task(result)
                 <- synthesized writer function response
                 -> manager inspects current result
                 -> delegate proofreader(task)
                      -> proofreader calls finish_task(result)
                 <- synthesized proofreader function response
                 -> manager produces one department output
            <- Workflow returns one result
       <- root receives department tool result
       -> root produces the user-facing answer
```

### Required design rules

1. **Set the root to `mode="chat"` explicitly.**
2. **Set managers to `mode="chat"` explicitly.** Their role is to coordinate several bounded task calls.
3. **Keep `mode="task"` agents as leaves.** They should own tools, not sub-agent hierarchies.
4. **Use `single_turn` only for genuinely simple leaf calls.** Do not make it the recursive manager primitive.
5. **Do not use `transfer_to_agent` for internal return paths.** Ideally, internal task agents should have no eligible transfer targets.
6. **Expose each department Workflow as a node tool to its caller.** Do not insert it as an ordinary chat sub-agent.
7. **Pass the current child result through the task function response.** Use `output_key` only when durable shared state is independently required.
8. **Give each task a meaningful input and output schema.** Avoid a generic `Placeholder` envelope.
9. **Use Workflow fan-out/fan-in for true concurrency.** Do not ask one manager model turn to issue several task-delegation tools in parallel unless the runtime explicitly supports that combination.
10. **Treat all model instructions as guidance, not control-flow guarantees.** Structural constraints and trace assertions must enforce correctness.

---

## Data-contract problems in the current example

The control-flow bug is primary, but the generic schema makes the failure harder to detect and the result less trustworthy.

Every agent accepts and returns:

```python
class Placeholder(BaseModel):
    placeholder: str = ""
```

This permits all of the following to be considered valid outputs:

```json
{"placeholder": "the quick brown fox"}
{"placeholder": "Task completed successfully."}
{"placeholder": "Drafting content for the task"}
```

The schema cannot distinguish:

- a task request from a task result;
- a draft from a proofread revision;
- a status message from a completed output;
- current data from stale state.

The manager also truncates or rewrites the initial request before delegation, and later passes completion/status text to another worker. Strong contracts should make those mistakes invalid or at least visible.

A better writing pipeline would use separate models, for example:

```text
WritingRequest
  original_text
  requested_changes

DraftResult
  revised_text
  substantive_changes
  uncertainties

ProofreadRequest
  draft
  original_objective

ProofreadResult
  final_text
  corrections
```

The immediate caller should receive these values in the child function response. Persist them to session state only if another independent workflow step needs durable access.

---

## Mandatory behavioral tests

The next implementation should not be evaluated merely by checking whether it eventually produces plausible prose. The event and node trace are the specification.

### Core call/return tests

| Scenario | Required trace |
|---|---|
| One worker | `root -> manager -> A -> manager -> root` |
| Two sequential workers | `root -> manager -> A -> manager -> B -> manager -> root` |
| Completed worker | Worker does not run again without a new parent delegation function call |
| Repeated user turn | New user input begins with root ownership, not the previous manager or worker |
| Department completion | Workflow produces exactly one result for root |

### Output integrity tests

- Every node execution sets output zero or one times, never twice.
- An output event's author and `nodeInfo.outputFor` identify the intended node.
- A leaf output cannot write the manager's output key.
- A manager output cannot write a leaf's output key.
- The manager consumes the current child function response rather than a stale state value.
- Status text such as `"Task completed successfully"` cannot satisfy a domain result schema.

### Isolation and recovery tests

- A task leaf sees its delegated input and its own tool history, but not unrelated sibling task history.
- Sequential task calls use distinct function-call IDs and isolation scopes.
- A paused/resumed task does not see or execute its parent's delegation call as one of its own tools.
- A failed leaf returns a bounded error to its manager and does not recursively unwind through every ancestor.
- Rate limits and model failures stop within a configured call budget rather than producing an unbounded branch.

### Transfer prohibition test

For the strict hierarchy, fail the test if any internal worker or department manager emits:

```text
actions.transferToAgent
```

unless the test explicitly covers a separate human-facing conversational hand-off feature.

### Trace invariant

For every completed child execution:

```text
child completion
    must be preceded by one parent delegation call
    must be followed by one matching function response
    must return control to that same parent execution
```

A child appearing again without a new delegation call is a correctness failure even if the final prose looks acceptable.

---

## Risks and open questions

The proposed design is the best match for ADK 2.7.1's implementation, but it should still be treated as a hypothesis until exercised locally.

Important questions for the prototype are:

1. Does a Workflow containing a start-edge chat manager reliably produce exactly one terminal output after several task delegations?
2. Does exposing that Workflow as a tool preserve the root's conversation history and tool-call balance across multiple user turns?
3. What is the correct schema boundary between Workflow input/output and manager input/output?
4. How do interruptions and resumability behave when a task leaf pauses inside a Workflow called as a node tool?
5. Do plugins, artifacts, and state deltas retain the intended scope at both the task and department boundaries?
6. Does the manager's final event remain distinct from the terminal Workflow output event, avoiding another double-output path?
7. Which version-specific behaviors need regression coverage before upgrading beyond 2.7.1?

These questions are reasons to build a small deterministic proof, not reasons to revert to transfer-based nesting.

---

## Suggested next-stage sequence

1. Build the smallest possible topology: one chat root, one department Workflow, one chat manager, and one task leaf.
2. Use deterministic or scripted model responses where possible so validation does not depend on API quotas or model routing choices.
3. Assert the exact event, branch, function-call, function-response, and output ownership sequence.
4. Add a second sequential task leaf and repeat the assertions.
5. Add multi-turn root conversation tests.
6. Add failure, interruption, and resumability tests.
7. Only after the execution contract is stable, replace the placeholder schemas and prompts with the real domain contracts.
8. Add fan-out/fan-in through Workflow edges only if parallel work is required.

---

## Final judgment

The implementation difficulty comes from trying to express a call stack using an API surface that includes several superficially similar but semantically different delegation mechanisms.

The current implementation nests `single_turn` agents and then allows those agents to use chat transfer as a return mechanism. That causes the parent to be resumed inside the child's dynamic execution, grows the branch indefinitely, corrupts output attribution, overwrites state, and can violate the one-output-per-node invariant.

The first research report correctly identifies the historical mechanisms and transfer stickiness, but its AgentTool- and state-oriented prescription is not the strongest basis for new ADK 2.7.1 code. The deeper report's Workflow/chat-manager/task-leaf design is better aligned with the current runtime, with two qualifications: the root must explicitly use chat mode, and the complete composition still needs a local reference test.

The architecture should therefore be judged by one non-negotiable property:

> Every internal delegation is a bounded call with a matching returned value; conversational transfer is never used as a return statement.

If ADK 2.7.1 cannot satisfy that property in the proposed Workflow-based proof, the conclusion should not be to add more transfer prompts. It should be to reduce the hierarchy, move more orchestration into deterministic Workflow nodes, or isolate manager scopes behind a different explicit call boundary.
