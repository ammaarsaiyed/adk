I've gone through all three files. Here's my read.

## What the session log actually shows

Stripping out the 429 rate-limit noise (events 42+ are just cascading quota failures), the trace of one user request — "refine the text: the qucik brown fix" — is damning:

```
hello_agent → middle_b (as a delegation call)
  middle_b → leaf_b1
    leaf_b1 → transfer_to_agent("middle_b")   ← leaf does zero work
  middle_b (resumed inside leaf_b1's branch) → leaf_b2
    leaf_b2 → transfer_to_agent("middle_b")   ← same
  middle_b → leaf_b1 → transfer back
  middle_b → leaf_b1 → transfer back
  middle_b → leaf_b1 → transfer back   ← 5+ identical re-delegations
```

Three distinct pathologies, all visible before the rate limits hit:

**1. The branch stack never pops.** Look at the `branch` field: it grows monotonically — `middle_b@call_220645.leaf_b1@call_408757.leaf_b2@call_203700.leaf_b1@call_168561...`. Every "return" from a leaf is actually `transfer_to_agent`, which doesn't return control — it *resumes the parent inside the child's branch*, one nesting level deeper. This is exactly the "transfer is routing, not call/return" problem the deep-research report describes, and here it's happening *inside* what should be a function-call-style delegation.

**2. The transfer loop.** The leaves (single_turn, with `output_schema` set) never produce output — they immediately call `transfer_to_agent("middle_b")`. Middle_b gets resumed, sees no result, re-delegates to leaf_b1, which transfers back again. Infinite ping-pong with zero useful work; the only reason it terminated at all is the 429s.

**3. The "output already set" ValueError — visible in embryo at events 20–25.** `middle_b` calls `set_model_response` **twice** in one invocation (`"the quick brown fox"`, then `"Task completed successfully."`). A node may produce one output; when the loop causes a node to be re-entered after it already set its output, you get exactly the `ValueError: output already set, a node can produce at most one output` you've been seeing. It's not a random bug — it's the direct consequence of the resume-instead-of-return semantics above.

## The root cause in `agent.py`

The config violates ADK 2's most important structural rule, which both reports call out: **`mode="single_turn"` is applied to the middle agents, which have sub_agents — but task/single_turn agents must be leaves.** You've built a recursive hierarchy out of a primitive that is explicitly leaf-only, on top of transfer-based routing that has no return semantics. Everything in the log follows from that. (Side note: `output_schema` is set without `disallow_transfer_to_parent/peers` — per the known issue, ADK should reject that combination; here it silently lets leaves transfer away, bypassing schema enforcement entirely. And the final state only has `middle_b_output`/`leaf_b1_output` — `hello_agent_output` was never written.)

## My judgment on the two reports

They largely agree on the diagnosis but **disagree on the prescription**, and I think the deep-research report is more right:

- `adk2-hierarchical-agents.md` recommends AgentTool children for level-2 coordinators (citing travel-concierge). That works but is the legacy idiom — current ADK source explicitly discourages direct `AgentTool` use, and it doesn't compose with the graph runtime's branching/isolation.
- `deep-research-report.md` recommends: chat root → Workflow-as-NodeTool → chat manager → task leaves. That matches how the 2.7.1 runtime actually works (task dispatch creates a sub-branch per function-call ID, synthesizes a function response, re-enters the *same* parent — a genuine call stack, structurally immune to ping-pong). Since you're already on 2.7.1, all the machinery it cites is available to you.

One honest caveat: the deep-research report's cited behavior comes from reading source and docs, not from a running reference implementation — its own bottom line admits Google ships no sample combining these pieces. So the Workflow/chat-manager/task-leaf design is well-grounded but unproven as a complete pattern.

## Bottom line

Your implementation fails not because of any single bug but because the architecture uses `single_turn` + `transfer_to_agent` for something they're not designed for: recursive call/return. The fixes being sketched in the two reports converge on the same invariant — **a child's completion must produce a function response to its caller, never a transfer of conversational ownership** — and in ADK 2.7.1 that means Workflow boundaries for nesting + task-mode leaves, not nested single_turn agents.

Happy to take the next step — prototype the Workflow-based version of `hello_agent` against 2.7.1, or first dig into the installed ADK source to verify the claims about task dispatch and the output-already-set guard before committing to a design.