# Hierarchical Agent Pattern in Google ADK 2.0 — Findings & Recommended Template

*Research compiled 2026-08-25 from official ADK docs (adk.dev), google/adk-samples, google/adk-python issues/discussions, and community sources. All claims cited inline.*

---

## TL;DR

Your instincts are right, with one important correction from the evidence:

- **Flat `sub_agents` fan-out is genuinely broken-ish**: transfer is a *control hand-off*, not call-and-return. Sub-agents are "sticky" — once transferred to, the child owns all subsequent turns and doesn't come back ([issues #147, #620](https://github.com/google/adk-python/issues/147)).
- **AgentTool is *not* what ADK 2.0 recommends as the primary fix** — it works and is still used in official samples, but ADK 2.0's idiomatic mechanisms are **collaboration modes** (`mode="task"` / `mode="single_turn"`) and the graph-based **`Workflow`** API. `SequentialAgent` is deprecated in 2.x.
- For a true 3-level hierarchy (root → coordinator → specialists) where non-root agents return to their parent and never get ping-ponged back into, the **recommended ADK 2.0 template is: LLM router at root → level-2 coordinators as `LlmAgent`s with `mode="task"`… wait, no — task agents must be leaf agents.** That's exactly the catch. The working template is detailed below.

---

## 1. The key finding: no single mechanism does what you want — combine two

ADK gives you four delegation primitives, each with a different "return" contract:

| Mechanism | Return semantics | Can have its own sub-agents? | ADK 2.0 status |
|---|---|---|---|
| `sub_agents` + `transfer_to_agent` (default AutoFlow) | **None — sticky hand-off.** Child owns all future turns. | Yes | Still default; known stickiness bugs |
| `AgentTool(agent)` | Call-and-return: result comes back as a FunctionResponse, parent keeps control | Yes (the wrapped agent's whole subtree runs in isolation) | Works; official samples still use it; but HITL tools don't work inside it, and 2.0 docs steer toward collaboration modes |
| `mode="task"` / `mode="single_turn"` (ADK 2.0) | **Automatic return to parent** (`finish_task` / immediate result) | **No — task-mode agents must be leaf agents** ([docs](https://adk.dev/workflows/collaboration/)) | New, recommended; but leaf-only |
| `Workflow` graph (ADK 2.0) | Deterministic — engine drives the flow, no LLM routing | Nodes, not sub-agents; a Workflow **cannot yet be an LlmAgent sub-agent**, only a tool via `NodeTool` ([adk-docs #1937](https://github.com/google/adk-docs/issues/1937)) | New, replaces deprecated SequentialAgent |

The critical constraint: **`task`-mode agents must be leaf agents** — so you cannot build a 3-level tree purely out of `task`-mode sub-agents. The middle layer (coordinators that themselves delegate) must keep control some other way. The two proven middle-layer options:

1. **LlmAgent coordinator whose children are `AgentTool`s** — call-and-return at every level, zero transfer involved. This is exactly how Google's own `travel-concierge` sample does level 2.
2. **Workflow/graph node as the coordinator** — deterministic orchestration, no LLM routing at all (best when the level-2 flow is known at build time).

---

## 2. Recommended template (ADK 2.x, Python)

Root = LlmAgent with `sub_agents` (LLM picks one or more coordinators — this is the one place transfer is acceptable, or you can wrap coordinators in AgentTool too for pure call-return). Level-2 coordinators = LlmAgents whose level-3 specialists are **AgentTools** (or `single_turn` sub-agents if you want the 2.0-native mechanism, but see §4 caveats). Every leaf writes results via `output_key` → session state.

```python
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool

# ---------- Level 3: leaf specialists ----------
# disallow_transfer_to_parent=True is REQUIRED when output_schema is set,
# and per the API docs it "will transfer control back to the parent agent
# in the next turn" — guaranteeing return-to-parent, never sticky.
# https://adk.dev/api-reference/python/google-adk.html

web_searcher = LlmAgent(
    name="web_searcher", model="gemini-2.5-flash",
    instruction="Search the web for facts on the topic. Return raw findings.",
    description="Searches the web for factual information.",
    tools=[google_search],
    output_key="research_findings",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)

summarizer = LlmAgent(
    name="summarizer", model="gemini-2.5-flash",
    instruction="Condense the research in {research_findings} into a brief.",
    description="Condenses research into a structured brief.",
    output_key="research_brief",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)

# ---------- Level 2: coordinator ----------
# Has its OWN sub-capabilities, exposed as AgentTools => function-call
# semantics: each child runs to completion, result returns HERE, and this
# coordinator continues. It can never be "stuck" inside a child, and it
# will never re-enter a child it already called unless it deliberately
# calls the tool again.

research_coordinator = LlmAgent(
    name="research_coordinator", model="gemini-2.5-flash",
    instruction=(
        "You coordinate research. Call web_searcher to gather facts, then "
        "summarizer to condense them. When done, return the final brief as "
        "your answer. Do NOT attempt to transfer to any agent."
    ),
    description="Coordinates web research and summarization.",
    tools=[AgentTool(web_searcher), AgentTool(summarizer)],
    output_key="coordinator_output",
    disallow_transfer_to_parent=True,   # return to root next turn
    disallow_transfer_to_peers=True,
)

writing_coordinator = LlmAgent(
    name="writing_coordinator", model="gemini-2.5-flash",
    instruction="...call drafter, then editor, return the polished text...",
    description="Coordinates drafting and editing.",
    tools=[AgentTool(drafter), AgentTool(editor)],
    output_key="writing_output",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)

# ---------- Level 1: root ----------
# Root is the ONLY agent that uses LLM-driven transfer, and only downward.
# Even here you may prefer AgentTool(research_coordinator) for pure
# call-return; sub_agents+transfer is shown because you want the root to
# SELECT one or more coordinators by intent.

root_agent = LlmAgent(
    name="root", model="gemini-2.5-flash",
    instruction=(
        "You are the orchestrator. Delegate research to research_coordinator "
        "and writing tasks to writing_coordinator. After a coordinator "
        "returns, synthesize its result for the user. Never transfer back "
        "to a coordinator that has already answered unless the user asks "
        "for new work."
    ),
    sub_agents=[research_coordinator, writing_coordinator],
)
```

**Why each constraint is satisfied:**

- *Root selects one or more sub-agents* → root is an LlmAgent with `sub_agents`; standard AutoFlow routing.
- *Coordinators call one or more sub-agents* → their children are tools; the LLM can call them multiple times, in any order, even in parallel.
- *Non-root agents return to parent, and the parent doesn't re-enter the child after return* → AgentTool is a function call: control returns to the caller's next model turn by construction. Ping-pong is structurally impossible — there is no `transfer_to_agent` inside this layer at all. `disallow_transfer_to_parent=True` on leaves/coordinators additionally guarantees that *if* any transfer mechanism does fire, the next turn routes back up, not sideways or down ([API docs quote](https://adk.dev/api-reference/python/google-adk.html): "Setting this as True also prevents this agent from continuing to reply to the end-user, and will transfer control back to the parent agent in the next turn").
- *ADK 2.0-native alternative for leaves*: you can replace `AgentTool(leaf)` with `sub_agents=[leaf]` + `mode="single_turn"` on the leaf — documented as "No user interaction with automatic return and can be run in parallel" ([adk.dev/workflows/collaboration](https://adk.dev/workflows/collaboration/)). But read §4's bugs before doing so.

If the level-2 flow is **fixed** (always searcher → summarizer), don't pay an LLM to route it: make the coordinator a `Workflow` graph (2.0-native; `SequentialAgent` is deprecated, [adk-docs #1937](https://github.com/google/adk-docs/issues/1937)) and expose it to the root as a tool via `NodeTool`/`AgentTool`. "Don't waste an LLM call to figure out what you already know."

---

## 3. Reference implementations found online

1. **Official `travel-concierge` sample** ([github.com/google/adk-samples](https://github.com/google/adk-samples), `python/agents/travel-concierge`) — root with 6 `sub_agents`; level-2 agents (e.g., `planning_agent`) own their children **via AgentTool**, and leaves like `itinerary_agent` set `disallow_transfer_to_parent=True, disallow_transfer_to_peers=True, output_schema=Itinerary, output_key="itinerary"`. This is the closest official precedent to the template above.
2. **Google Developers Blog, "Developer's guide to multi-agent patterns in ADK"** ([developers.googleblog.com](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/), Dec 2025) — explicitly shows the 3-level "hierarchical decomposition (russian doll)" pattern: `ReportWriter` → `AgentTool(ResearchAssistant)` where `ResearchAssistant` itself has `sub_agents=[WebSearchAgent, SummarizerAgent]`.
3. **`haiyuan-eng-google/exmaples-BigQuery-agent-analytics-plugin`** ([agent.py](https://github.com/haiyuan-eng-google/exmaples-BigQuery-agent-analytics-plugin)) — root LlmAgent whose level-2 node is a `SequentialAgent` (`data_team`) containing two LlmAgent specialists passing data via `output_key`/`{state_key}` injection. Workflow agents contain no transfer tool, so ping-pong is impossible by construction. (Note: migrate `SequentialAgent` → `Workflow` in 2.x.)
4. **`ThreeFish-AI/negentropy` framework notes** ([framework.md](https://github.com/ThreeFish-AI/negentropy/blob/master/docs/concepts/framework.md)) — production use of `mode="single_turn"` + `disallow_transfer_to_parent/peers` conditioned on `output_key` presence, including the factory-function workaround for ADK's single-parent rule.
5. **Hatchworks ADK best practices** ([hatchworks.com](https://hatchworks.com/blog/gen-ai/google-adk-best-practices/)) — "Our sub-agents all declare `output_schema=SubAgentOutput` with `disallow_transfer_to_parent=True` and `disallow_transfer_to_peers=True`… they ensure the sub-agent returns a structured response directly to the parent rather than attempting to transfer control, which would bypass the schema enforcement."

---

## 4. Pitfalls confirmed with sources

### AgentTool — what the problems actually are
Your premise that "AgentTool is not recommended in ADK 2" is only half right — the evidence shows a more nuanced picture:

- AgentTool remains in official samples and was historically the community's *recommended fix* for transfer stickiness ("start with AgentTool unless you need multi-turn child conversations" — [Practically Agents](https://practicallyagents.com/articles/adk-sub-agents-vs-agent-tool/)).
- **But** its real, sourced limitations: synchronous human-in-the-loop `require_confirmation` tools **do not work inside AgentTool** ([discussion #3276](https://github.com/google/adk-python/discussions/3276)); and ADK 2.0's collaboration modes + `Workflow`/`NodeTool` are the team's stated "cleanest and most idiomatic" replacement for agent-as-tool orchestration ([discussion #4110](https://github.com/google/adk-python/discussions/4110)). So: AgentTool still works, but it's the legacy idiom, and 2.0-native code should prefer `single_turn` sub-agents or Workflow nodes where possible.

### `sub_agents` + transfer — the bugs you suspected, confirmed
- **Stickiness / no return to parent**: [issue #147](https://github.com/google/adk-python/issues/147) ("Goes into sub-agent and not back up to the root agent"), [#620](https://github.com/google/adk-python/issues/620), [adk-docs #158](https://github.com/google/adk-docs/issues/158).
- **`output_key` never captures delegated sub-agent responses**: [issue #3758](https://github.com/google/adk-python/issues/3758) — root cause in `llm_agent.py` `__maybe_save_output_to_state()`: `if event.author != self.name: return` skips events authored by sub-agents. **Workaround: write state manually via `tool_context.state[key] = value` inside the child**, and read it in the parent via `{key}` instruction injection. This is exactly the output-key-write failure you flagged.
- **Parent going back to a child it already visited / loops**: [#3081](https://github.com/google/adk-python/issues/3081) (undocumented routing reset behavior with `disallow_transfer_to_parent`), [#6566](https://github.com/google/adk-python/issues/6566) (infinite tool-call loop with transfer + SSE streaming), [#5536](https://github.com/google/adk-python/issues/5536) (no response after transfer in live mode, regression in 2.0.0b1).
- **A2A sub-agents terminate the parent flow**: [#5977](https://github.com/google/adk-python/issues/5977) — transfer is a permanent hand-off signal; conflicts with call-and-return expectations.
- **`single_turn`/task-mode caveats**: task mode **disabled inside graph workflows** and **task agents must be leaf agents** ([docs](https://adk.dev/workflows/collaboration/)); mode must not be set on the root.
- **`output_schema` forces both disallow flags** — ADK rejects the config otherwise ([issue #3318](https://github.com/google/adk-python/issues/3318)).
- `transfer_to_agent` instruction injection ignores `disallow_transfer_to_parent=True` ([issue #844](https://github.com/google/adk-python/issues/844)); `disallow_transfer_to_peers` can be bypassed by an LLM guessing sibling names ([issue #3850](https://github.com/google/adk-python/issues/3850)) — another reason to prefer tool-call semantics over transfer at non-root levels.

---

## 5. Decision summary

| Your requirement | Recommended mechanism |
|---|---|
| Root selects coordinator(s) | LlmAgent `sub_agents` (transfer OK at root only) or `AgentTool(coordinator)` for strict call-return |
| Coordinator delegates to specialists | **AgentTool children** (battle-tested) or `mode="single_turn"` leaf sub-agents (2.0-native; leaves only) |
| Child returns to parent, never re-entered | Tool-call semantics (structural) + `disallow_transfer_to_parent=True, disallow_transfer_to_peers=True` (belt-and-braces) |
| Data flows up the tree | `output_key` → `session.state`, `{key}` injection in parent instructions; manual `tool_context.state` writes to dodge issue #3758 |
| Fixed coordinator pipelines | `Workflow` graph (SequentialAgent is deprecated in 2.x) |
