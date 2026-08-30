My preliminary judgment: this is primarily a control-flow protocol collision, not a rate-limit problem and not merely weak prompting.

The current hierarchy mixes nested `single_turn` tool calls with chat-style `transfer_to_agent`. That turns an attempted child “return” into recursive re-entry of the parent, and eventually breaks node-output ownership.

### What the trace demonstrates

The intended flow is:

```text
root → middle_b → leaf_b1 → middle_b resumes → leaf_b2 → middle_b → root
```

The actual flow starts correctly:

- Root calls `middle_b` in [session_export.json](/Users/ammaar/dev/adk/session_export.json:46).
- `middle_b` calls `leaf_b1` at [session_export.json](/Users/ammaar/dev/adk/session_export.json:90).
- But `leaf_b1` calls `transfer_to_agent("middle_b")` at [session_export.json](/Users/ammaar/dev/adk/session_export.json:135).

That transfer does not unwind the leaf call. ADK resolves the transfer by changing the currently executing node to `middle_b` and continuing inside the existing `ctx.run_node()` call. `middle_b` then invokes another leaf, which transfers back again, producing the steadily lengthening branch.

Before the first 429 appears, this single request already generated:

- 11 leaf-agent calls
- 11 `transfer_to_agent` calls
- Only 2 actual `set_model_response` calls

So the rate limit terminates an existing runaway recursion; it does not cause it.

### Why this configuration triggers it

Every middle and leaf agent is assigned `mode="single_turn"` by the shared factory in [agent.py](/Users/ammaar/dev/adk/hello_agent/agent.py:10). ADK consequently exposes each `single_turn` child as an internal tool.

However:

- ADK’s internal `_SingleTurnAgentTool` says it is intended for a `mode="chat"` parent, not another `single_turn` parent ([agent_tool.py](/Users/ammaar/dev/adk/.venv/lib/python3.12/site-packages/google/adk/tools/agent_tool.py:363)).
- The current implementation still offers a single-turn leaf’s parent as a transfer target when `disallow_transfer_to_parent=False` ([agent_transfer.py](/Users/ammaar/dev/adk/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/agent_transfer.py:162)).
- A transfer encountered inside `ctx.run_node()` continues execution at the target rather than returning a function result ([context.py](/Users/ammaar/dev/adk/.venv/lib/python3.12/site-packages/google/adk/agents/context.py:634)).

That combination explains the ping-pong exactly.

### The `Output already set` error

Although that exception is not in this export, I think it is a downstream manifestation of the same problem.

Single-turn output processing treats model-content events as the enclosing node’s output without checking that the event author is the node being wrapped ([llm_agent_wrapper.py](/Users/ammaar/dev/adk/.venv/lib/python3.12/site-packages/google/adk/workflow/_llm_agent_wrapper.py:336)). With nested execution and transfers, a parent’s response can therefore be processed as a child’s output.

The trace contains direct evidence: one event is authored by `middle_b`, has the node path of `leaf_b1`, and writes both `middle_b_output` and `leaf_b1_output` around [session_export.json](/Users/ammaar/dev/adk/session_export.json:901).

Once another output reaches that same node execution, `NodeRunner` assigns it to `ctx.output` again, whose setter raises exactly:

> Output already set. A node can produce at most one output.

See [context.py](/Users/ammaar/dev/adk/.venv/lib/python3.12/site-packages/google/adk/agents/context.py:361). So I would not treat that ValueError as an independent bug yet; it is highly consistent with output events crossing node boundaries during recursive transfer.

### Judgment on the two reports

[adk2-hierarchical-agents.md](/Users/ammaar/dev/adk/adk2-hierarchical-agents.md:17) gets the essential distinction right: transfer is conversational ownership routing, not stack-like call/return. But I would not adopt its recommended AgentTool-heavy design:

- Current ADK explicitly discourages direct `AgentTool` use.
- It relies too heavily on `output_key` as RPC.
- Its claim that transfer-disallow flags themselves “guarantee return” is too strong and partly version-stale.
- Allowing root-to-coordinator transfer still weakens the strict ownership invariant.

[deep-research-report.md](/Users/ammaar/dev/adk/deep-research-report.md:230) is much closer to the architecture I would investigate:

```text
chat root
  → Workflow/NodeTool boundary
      → chat manager
          → task-mode leaf
          → task-mode leaf
      → Workflow returns
  → root resumes
```

That matches ADK 2.7.1’s actual specialized path for chat coordinators dispatching task children and receiving synthesized function responses.

One correction: its example root omits `mode="chat"` at [deep-research-report.md](/Users/ammaar/dev/adk/deep-research-report.md:576). In the current runtime, an agent used as a workflow node defaults to `single_turn` when its mode is unset ([llm_agent_wrapper.py](/Users/ammaar/dev/adk/.venv/lib/python3.12/site-packages/google/adk/workflow/_llm_agent_wrapper.py:369)). A genuinely conversational root should therefore be explicit.

Secondary problems also exist—the generic `Placeholder` schema permits meaningless outputs such as `"Task completed successfully."`, loses domain-specific contracts, and makes stale `output_key` state easy to mistake for the current result—but those are not the main loop cause.

No files were changed. My current direction would be to test the corrected `chat root → Workflow → chat manager → task leaves` structure with deterministic trace assertions before trusting another live-model run.