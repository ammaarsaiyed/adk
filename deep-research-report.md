# Hierarchical call/return agents in Google ADK 2.x

## Executive finding

I found an **official Google three-layer hierarchical sample that looks almost exactly like the topology you described**: `root_agent → writer_agent → translator_agent → writer_agent → root_agent`. Google calls it the **“Three Layer Transfer Sample”** and describes it as demonstrating a “three-layer multi-agent system” with a double round trip: root delegates to the middle agent, the middle delegates to the grandchild, the grandchild returns to the middle, and the middle returns to root. citeturn19view0turn20view0

However, **I would not use that sample as the production implementation of the semantics you want**. It is built on ordinary `chat`-mode `sub_agents` and `transfer_to_agent`. Its return behaviour is therefore model-directed transfer routing, not a strict call stack. The source literally relies on instructions such as “transfer back to the writer_agent” and later “transfer back to the root_agent”. citeturn20view0 Google’s own current multi-agent reference describes chat transfer as control being handed to another agent and returned through transfer, and lists “a sub-agent takes over and never gives control back” as a common failure mode. citeturn17view0

The important conclusion from the current ADK 2.x implementation is:

> **There is presently no pure, arbitrarily nested `sub_agents=[...]` configuration that simultaneously gives you a literal three-level agent tree and strict automatic call/return semantics at every level.**

The reason is structural. ADK 2's reliable call/return mechanism is the new collaborative `task`/`single_turn` delegation mechanism. A `task` child automatically returns to the coordinator rather than becoming the active conversational agent, but Google explicitly states that **task-mode agents must be leaf agents and cannot themselves have subagents**. citeturn21view4turn3view2 That means this seemingly ideal configuration is deliberately unsupported:

```text
root
└── manager          mode="task"       ❌ manager cannot have subagents
    ├── worker_a     mode="task"
    └── worker_b     mode="task"
```

The best ADK 2.x implementation I found is therefore a **hybrid graph/collaboration hierarchy**:

```text
ROOT LLM AGENT
    │
    │ model calls a Workflow as a NodeTool
    ▼
DEPARTMENT WORKFLOW
    │
    ▼
CHAT-MODE MANAGER
    │
    ├── task delegation ──► TASK LEAF A ──return──┐
    │                                             │
    ├── task delegation ──► TASK LEAF B ──return──┤
    │                                             │
    ◄─────────────────────────────────────────────┘
    │
    │ workflow completes
    ▼
ROOT LLM AGENT
```

This gives you the behaviour you actually care about:

```text
root
  calls manager-scope
       manager
          calls worker
          worker returns
       manager resumes
          optionally calls another worker
          worker returns
       manager finishes
  root resumes
```

There is **no implicit `worker → manager → worker` transfer bounce**. Returning from a worker produces a function response to its immediate manager. Going to that worker again requires a **new explicit delegation** by the manager. Likewise, completing the department Workflow returns its output as a tool result to root; it does not transfer conversational ownership to the department. This is precisely the distinction ADK 2's graph runtime and task delegation machinery are designed to provide. citeturn22view0turn22view1turn21view0

As of **25 August 2026, the latest published Python ADK 2.x release is 2.7.1**, released on 17 August 2026. I would target that, rather than writing against 2.0.0 behaviour: significant task-delegation, isolation and `output_key` fixes landed during the 2.x series. citeturn17view3

## What changed in ADK 2 and why old hierarchical examples mislead

ADK 2 is not merely ADK 1 with some new agent modes. Google describes the central architectural change as a move from the older **hierarchical agent executor to a graph-based execution engine**, where agents, tools and functions are evaluated as nodes. `BaseAgent` is now a node type, and the graph runtime owns routing, persistence and event processing. citeturn2view1

That distinction matters enormously for hierarchical agents.

### Chat transfer is routing, not a function call

The classic ADK pattern is:

```python
root = Agent(
    sub_agents=[manager]
)

manager = Agent(
    sub_agents=[worker]
)
```

Without a `mode`, those subagents are effectively conversational/chat agents. Google still supports this and provides the `three_layer_transfer` sample for exactly that structure. citeturn19view0turn20view0

But consider its code. The grandchild is instructed:

```python
instruction="""
...
Once the translation is complete, output the translated text,
explain what you did, and then transfer back to the writer_agent.
"""
```

The manager is separately instructed:

```python
instruction="""
...
If the user asks to translate ... transfer the task to translator_agent.
If the user is satisfied ... transfer back to the root_agent.
"""
```

That is fundamentally different from:

```python
result = await child(...)
# execution necessarily continues here
```

The model decides to transfer back. citeturn20view0

There is another implementation detail that strongly supports your concern about agents unexpectedly becoming active again. The current `LlmAgent` source still contains `_get_subagent_to_resume` in its legacy asynchronous execution path. That logic examines prior transfer events to determine whether a subagent should be resumed. In other words, chat delegation has session-history/resumption semantics; it is not a lexical parent/child stack. citeturn11view1turn11view2

The transfer implementation makes the distinction even clearer. ADK's `transfer_to_agent` target discovery includes ordinary conversational subagents, parents and eligible peers, while `task` and `single_turn` children are excluded from those ordinary transfer targets. They are handled through the newer delegation mechanism instead. citeturn13search0

So I agree with the premise in your question: **simply taking an ADK 1 hierarchy and nesting more `sub_agents` is not sufficient to get reliable stack-like control flow.**

### ADK 2 introduces a much better child-call protocol

The collaborative-workflow documentation defines three modes. A normal `chat` child uses conversational transfer. A `task` child automatically returns to its parent when it completes through `finish_task`. A `single_turn` child also automatically returns after producing its result. citeturn21view4turn3view1

The current implementation is even more revealing than the documentation. For a chat coordinator delegating to a task agent, ADK now:

1. recognises the child's delegation function call;
2. executes that target through `ctx.run_node(...)`;
3. creates a dedicated sub-branch and isolation scope based on the function-call ID;
4. synthesises a function response containing the child's output;
5. returns that response to the **same parent coordinator**;
6. reruns that parent, allowing it to delegate another task or produce its final response. citeturn22view0turn22view1

The source comments explicitly describe this sequence:

```text
parent emits task FC
        ↓
dispatch task child
        ↓
synthesise FR
        ↓
re-enter parent
        ↓
parent may issue another task FC or finish
```

It also says an earlier implementation stopped after the first task dispatch and that the current outer loop exists specifically so **multiple chained delegations can continue in one coordinator invocation**. citeturn22view0

That is extremely close to the control flow you requested.

The snag is the leaf restriction.

Google's collaboration documentation says explicitly that **task-mode agents must be leaf agents and cannot have subagents**. citeturn3view2

Therefore this cannot be recursively repeated through `sub_agents`:

```text
root/chat
  └─ manager/task
       └─ worker/task
```

That is the key constraint that many ADK 1-style examples miss.

## Why I would avoid AgentTool and a single-turn-everywhere hierarchy

Your warning about `AgentTool` is correct, with one nuance: **it is discouraged rather than removed or formally deprecated**.

### AgentTool is now explicitly discouraged by Google

This is not inference. Current `google/adk/tools/agent_tool.py` contains this note in `AgentTool` itself:

> To expose an agent as an inline tool of a parent `LlmAgent`, prefer `mode='single_turn'` with `sub_agents=[...]`.

The same source then explicitly says that **direct usage of `AgentTool` is discouraged**. citeturn15view0turn15view3

Google's current official `single_turn_sub_agent` sample makes the intended migration even more explicit: it calls the single-turn subagent mechanism **the recommended replacement for the older AgentTool pattern**, and highlights that a single-turn subagent keeps its internal interactions such as tool calls in the session history. citeturn19view2

There is a practical architectural reason. `AgentTool` historically ran the wrapped agent as a separate agent invocation; the implementation constructs its own `Runner`/session machinery, whereas native ADK 2 child delegation integrates into the workflow/node execution and session branching model. The current source's preference for native subagent delegation reflects this difference. citeturn15view0turn15view3

And there is a concrete ADK 2 bug report illustrating the consequence. In issue **#5780**, a user migrating to ADK 2.0 `Workflow` reported:

```text
Agent1
   → AgentTool
   → Agent1
```

but the second execution of Agent1 had lost its previous context. The report says this happened consistently under ADK 2.0.0. The issue remains labelled as a v2 Workflow bug and, in the current GitHub metadata, has no linked fix branch or pull request. citeturn21view3

So for new ADK 2 code I would treat `AgentTool(agent=...)` as a **compatibility mechanism**, not the foundation of a hierarchical orchestrator.

### But replacing every AgentTool with single_turn is not enough

Google currently recommends `mode="single_turn"` as the straightforward replacement for the old AgentTool pattern. citeturn19view2turn15view0 But that does **not** mean I would use a recursively nested tree of single-turn coordinator agents for the system you described.

There has been a real ADK 2 workflow isolation bug in precisely this area. Issue **#5989** reported that multiple tool-using `LlmAgent`s invoked concurrently through `ctx.run_node` could corrupt one another's function-call/function-response windows because single-turn synthetic user events were written into shared session history without sufficient branch/scope isolation. The reporter found it intermittent and increasingly likely as concurrency and tool rounds increased. citeturn21view1

Importantly, the current implementation has changed in a way that appears to address the specific root cause of that report. A single-turn synthetic input event is now stamped with the current `isolation_scope` and branch before being appended, and resumed nodes avoid injecting another synthetic event. citeturn22view3

So I would **not claim #5989 is proof that single-turn is broken in 2.7.1**. The current code is materially different. But the episode demonstrates why I would reserve single-turn for simple, genuinely one-shot leaf calls rather than use it as the recursive control primitive for an elaborate hierarchy. The issue is closed, but its GitHub metadata does not identify a linked development PR, while the newer source contains the relevant isolation changes. citeturn22view4turn22view3

### Your output_key concern was also real

There was also an actual ADK 2 task-mode `output_key` bug. The 2.x release history records a fix to **stop processing `output_key` from intermediate conversational text in task-mode `LlmAgent`**. citeturn18view3

The current task wrapper now waits until the terminal successful `finish_task` function response, derives `event.output` from the completed task arguments, and only then writes the task agent's `output_key` into the state delta. citeturn22view2

That is much safer than treating each intermediate model utterance as the child result.

There was also issue **#6457** against ADK 2.4.0: with resumability enabled, a task child could see the parent coordinator's delegation function call and then try to execute its own delegation name as if it were one of its tools. The reporter found disabling resumability avoided the problem. citeturn21view2

Again, current source is materially improved: task dispatch now creates a dedicated sub-branch and uses the delegation function-call ID as an isolation scope, with source comments explicitly explaining that the returned function response is visible to the parent but hidden from other task scopes. citeturn22view1 The issue is closed, although its GitHub “Development” section does not identify the particular fix PR. citeturn22view6

Taken together, I would use the modes this way:

| Role | Recommended mechanism | Reason |
|---|---|---|
| Human-facing root | `Agent`, chat/default | Owns the conversation |
| Internal multi-worker manager | `Agent(mode="chat")` **inside a Workflow** | Can perform multiple task delegations and resume after each |
| Worker | `Agent(mode="task")` | Explicit task completion and automatic return |
| Very simple one-shot worker | `mode="single_turn"` | Native AgentTool replacement, but keep scope small |
| Agent wrapped with `AgentTool` | Avoid for new hierarchy | Current source explicitly discourages it |
| Department/subtree exposed to parent | `Workflow` as a node/tool | Graph-native call/return boundary |
| Recursive chat-transfer tree | Avoid for strict stack semantics | Transfer/resumption is routing rather than lexical return |

The underlying evidence for those distinctions is in Google's collaboration docs, the current task-dispatch wrapper, AgentTool source and current workflow samples. citeturn21view4turn22view0turn15view0turn21view0

## The ADK 2 hierarchy I recommend

The crucial ADK 2 feature for getting around the task-leaf restriction is **Workflow composition**.

Google's current repository says the Workflow runtime supports routing, fan-out/fan-in, loops, state management, dynamic nodes and **nested workflows**. citeturn16search4 There is an official `nested_workflow` sample in which a Workflow is itself placed inside a larger Workflow as a single child node. citeturn18view1turn20view1

There is separately an official **`node_as_tool`** sample demonstrating that both an ordinary node and an entire **Workflow can be exposed as a tool callable by an LLM Agent**. citeturn21view0

That combination gives us a better hierarchy than nested chat transfers.

### Desired call tree

For example, suppose the logical hierarchy is:

```text
root
├── research_manager
│   ├── web_researcher
│   └── evidence_critic
└── coding_manager
    ├── repository_reader
    └── code_writer
```

Implement it physically like this:

```text
root_agent [chat]
│
├── research_department [Workflow / NodeTool]
│      │
│      └── research_manager [chat workflow node]
│             ├── web_researcher [task subagent]
│             └── evidence_critic [task subagent]
│
└── coding_department [Workflow / NodeTool]
       │
       └── coding_manager [chat workflow node]
              ├── repository_reader [task subagent]
              └── code_writer [task subagent]
```

The runtime behaviour becomes:

```text
User
 │
 ▼
ROOT
 │
 │ calls research_department
 ▼
RESEARCH WORKFLOW
 │
 ▼
research_manager
 │
 │ delegate web_researcher(...)
 ▼
web_researcher [task]
 │
 │ finish_task(result)
 ▼
research_manager
 │
 │ delegate evidence_critic(...)
 ▼
evidence_critic [task]
 │
 │ finish_task(result)
 ▼
research_manager
 │
 │ final department result
 ▼
RESEARCH WORKFLOW completes
 │
 │ NodeTool result
 ▼
ROOT
 │
 │ optionally calls coding_department
 ▼
...
 │
 ▼
ROOT final response
```

This uses the new task protocol exactly where it is strongest: **chat coordinator → task leaf → same chat coordinator**. The current ADK wrapper explicitly supports repeated task delegations from one chat coordinator invocation and re-enters the coordinator after every returned task function response. citeturn22view0

The root-to-department boundary deliberately does **not** use `transfer_to_agent`. The root calls a Workflow node as a tool. Google's official Node-as-Tool example exists specifically to let a parent LLM call a Workflow this way. citeturn21view0

That gives you this invariant:

> **Returning from a child never changes conversational ownership. It merely produces a result for the caller.**

This is exactly the property that plain nested chat transfers cannot guarantee.

### Why the manager itself stays chat mode

This detail is important.

Do **not** make the manager:

```python
manager = Agent(
    mode="task",
    sub_agents=[...],
)
```

because task agents must be leaves. citeturn3view2

Instead:

```python
manager = Agent(
    mode="chat",
    sub_agents=[task_worker_a, task_worker_b],
)
```

and place that chat manager **inside its Workflow**.

That works because the current ADK 2 Workflow wrapper explicitly supports `chat`, `task`, and `single_turn` LLM agents as workflow nodes. For chat nodes it runs the special task-dispatch loop described above. citeturn22view3turn22view0

The Workflow becomes the automatic-return boundary around the manager.

### Why this is better than Workflow-as-subagent

There is another current framework limitation worth knowing: Google's own current multi-agent reference says a **Workflow still cannot be used directly as an `LlmAgent` sub-agent**. It explicitly identifies that as one of the remaining gaps between old orchestration agents and the new Workflow model. citeturn17view0

So this does not work:

```python
root = Agent(
    sub_agents=[
        research_workflow,   # ❌ not the supported relationship
    ]
)
```

Instead, use the ADK 2-native node-as-tool relationship:

```python
root = Agent(
    tools=[
        research_workflow,   # ✅ BaseNode / Workflow exposed as NodeTool
    ]
)
```

Google maintains an official example for exactly that pattern. citeturn21view0

This is also materially different from `AgentTool`: the thing being called is a **graph node/Workflow**, not another Agent wrapped in the older agent-as-tool abstraction.

## Implementation template

Below is the template I would actually start from on current ADK 2.x.

It intentionally has:

- a conversational root;
- Workflow boundaries for logical tier-two “subsystems”;
- `mode="chat"` managers inside those workflows;
- `mode="task"` workers;
- no direct `AgentTool`;
- no chat transfer between hierarchy levels;
- no `output_key` dependency for passing critical child results back to the immediate caller.

The underlying APIs and composition style are all present in current official ADK samples and runtime source. citeturn19view1turn20view1turn21view0turn22view0

```python
from __future__ import annotations

from pydantic import BaseModel, Field

from google.adk import Agent, Workflow


MODEL = "gemini-2.5-pro"


# ---------------------------------------------------------------------------
# Structured contracts for LEAF tasks.
#
# The important part is that these workers are mode="task" and are leaves.
# They return through ADK's task-delegation protocol rather than chat transfer.
# ---------------------------------------------------------------------------

class ResearchRequest(BaseModel):
    question: str = Field(
        description="The precise question the researcher must investigate."
    )


class ResearchResult(BaseModel):
    findings: list[str]
    caveats: list[str] = []
    source_notes: list[str] = []


class CritiqueRequest(BaseModel):
    draft: str
    objective: str


class CritiqueResult(BaseModel):
    accepted: bool
    problems: list[str] = []
    revised_recommendation: str


# Replace these with your actual function/MCP tools.
async def search_repository(query: str) -> str:
    """Example repository/documentation search tool."""
    return f"Search results for: {query}"


async def inspect_evidence(text: str) -> str:
    """Example deterministic evidence-checking tool."""
    return f"Evidence analysis: {text}"


# ---------------------------------------------------------------------------
# LEAF AGENTS
# ---------------------------------------------------------------------------

web_researcher = Agent(
    name="web_researcher",
    description=(
        "Investigates documentation, source repositories, issues and examples "
        "and returns evidence to the research manager."
    ),
    model=MODEL,
    mode="task",
    input_schema=ResearchRequest,
    output_schema=ResearchResult,
    tools=[search_repository],
    instruction="""
You are a leaf research worker.

Complete only the task delegated by research_manager.

You may use your tools as many times as necessary.
Do not attempt to transfer to another agent.
Do not answer the end user directly.

When the requested research is complete, finish the task with a ResearchResult.
""",
)


evidence_critic = Agent(
    name="evidence_critic",
    description=(
        "Critically checks evidence and proposed conclusions for gaps, "
        "contradictions and overclaiming."
    ),
    model=MODEL,
    mode="task",
    input_schema=CritiqueRequest,
    output_schema=CritiqueResult,
    tools=[inspect_evidence],
    instruction="""
You are a leaf review worker.

Evaluate exactly the material delegated by research_manager.
Do not transfer conversational control.
When finished, return a CritiqueResult via task completion.
""",
)


# ---------------------------------------------------------------------------
# LEVEL-TWO MANAGER
#
# Deliberately CHAT mode.
#
# Its children are TASK agents. ADK exposes those children through its task
# delegation protocol. When one finishes, the manager receives the result and
# resumes. It can then explicitly call another worker or finish.
# ---------------------------------------------------------------------------

research_manager = Agent(
    name="research_manager",
    description="Coordinates a bounded research job using specialist workers.",
    model=MODEL,
    mode="chat",
    sub_agents=[
        web_researcher,
        evidence_critic,
    ],
    instruction="""
You are an INTERNAL research manager.

You are not the user-facing root agent.

For the job you receive:

1. Decide which specialist worker is required.
2. Delegate a concrete task to that worker.
3. WAIT for that worker's returned task result.
4. Inspect the returned result yourself.
5. Delegate another worker only when a NEW task is necessary.
6. When the department job is complete, produce one final department result.

Important control-flow rules:

- web_researcher and evidence_critic are task workers, not conversational
  destinations.
- A returned worker result is evidence for YOU to process.
- Never ask a worker to transfer back to you.
- Never transfer conversational ownership to a worker.
- Do not re-invoke a completed worker unless you have a distinct new task
  that genuinely requires another call.
- Do not attempt to transfer to root_agent.
- Your final text is the result of this department invocation.
""",
)


# ---------------------------------------------------------------------------
# WORKFLOW BOUNDARY
#
# This is the crucial extra layer. The root calls this Workflow as a NodeTool.
# The manager therefore executes inside a bounded invocation and, when the
# Workflow finishes, its result goes back to root as a tool result.
# ---------------------------------------------------------------------------

research_department = Workflow(
    name="research_department",
    edges=[
        ("START", research_manager),
    ],
)


# ---------------------------------------------------------------------------
# ROOT
#
# A Workflow/BaseNode can be supplied as a tool in ADK 2; it is exposed using
# the NodeTool mechanism. This is NOT AgentTool.
# ---------------------------------------------------------------------------

root_agent = Agent(
    name="root_agent",
    model=MODEL,
    instruction="""
You are the only user-facing coordinator.

Use research_department when a request requires specialist research.

Treat every department invocation exactly like a function call:

    root -> department -> root

When research_department returns:
- YOU regain control;
- inspect its result;
- call another department only if additional work is genuinely needed;
- otherwise produce the final answer to the user.

Never use transfer_to_agent for department delegation.
""",
    tools=[
        research_department,
    ],
)
```

The exact `Workflow → Agent` composition above is supported by Google's Workflow examples, while the official Node-as-Tool sample demonstrates exposing an entire Workflow to an LLM parent. citeturn20view1turn21view0

### Extending it to several managers

A production root can expose several bounded department workflows:

```python
root_agent = Agent(
    name="root_agent",
    model=MODEL,
    instruction="""
Select the department or departments necessary to solve the request.

Departments are bounded calls.
After a department returns, remain in control.
Never transfer conversational ownership to a department.
""",
    tools=[
        research_department,
        coding_department,
        data_department,
    ],
)
```

Each Workflow can contain its own chat manager with its own task leaves:

```text
root_agent
│
├─ research_department   [Workflow as NodeTool]
│    └─ research_manager [chat]
│        ├─ searcher     [task]
│        └─ critic       [task]
│
├─ coding_department     [Workflow as NodeTool]
│    └─ coding_manager   [chat]
│        ├─ repo_reader  [task]
│        └─ code_writer  [task]
│
└─ data_department       [Workflow as NodeTool]
     └─ data_manager     [chat]
         ├─ sql_worker   [task]
         └─ validator    [task]
```

The task dispatcher is already designed to support a manager making one delegation, receiving its result, then making another delegation during the same coordinator invocation. citeturn22view0

### Extending the hierarchy beyond three levels

For a fourth or fifth level, I would recurse with **Workflow boundaries**, not recursively turn managers into task subagents.

For example:

```text
root
  ↓ NodeTool
division_workflow
  ↓
division_manager [chat]
  ↓ NodeTool
speciality_workflow
  ↓
speciality_manager [chat]
  ↓ task
leaf_worker
  ↑
speciality_manager
  ↑ Workflow return
division_manager
  ↑ Workflow return
root
```

Nested Workflows are a first-class feature of the ADK 2 Workflow runtime, and Google ships an official nested-Workflow sample. citeturn16search4turn20view1

That recursion gives you arbitrary hierarchy while preserving explicit call boundaries.

## Failure modes, version guidance and the pattern I would ship

There are several subtle distinctions I would encode into tests rather than trust to prompting.

### Do not treat the official three-layer transfer sample as stack semantics

It is useful because it proves Google supports:

```text
Agent
└─ Agent
   └─ Agent
```

and Google explicitly demonstrates returning grandchild → parent → root. citeturn19view0turn20view0

But it implements those returns through model-directed transfers. The current repository itself characterises chat-transfer hierarchies separately from schema-validated `task`/`single_turn` delegation. citeturn17view0

Therefore:

```python
root = Agent(sub_agents=[manager])
manager = Agent(sub_agents=[worker])
```

is **not equivalent** to:

```python
manager_result = await manager(...)
worker_result = await worker(...)
```

Do not build correctness assumptions around it.

### Prefer task to single_turn for non-trivial leaves

For a pure one-shot specialist with no conversational interaction, `single_turn` is the official replacement for AgentTool. citeturn19view2turn15view0

For a worker that may require several model/tool rounds, structured completion, confirmation or task-specific interaction, I would prefer `task`, because its completion protocol gives you a clearly identifiable terminal `finish_task` result and the current dispatcher returns that result explicitly to the originating coordinator. citeturn21view4turn22view2

The caveat remains: task agents are leaves. citeturn3view2

### Avoid using output_key as the parent/child RPC mechanism

`output_key` is useful for persisted shared workflow state. It should not be necessary to determine **what a child returned to its caller**.

The task delegation already returns the child's completed output in the synthesised function response to the manager. Current source separately writes an `output_key` only when appropriate. citeturn22view1turn22view2

I would therefore structure critical data flow as:

```text
child final task output
       ↓
FunctionResponse
       ↓
parent manager
```

and reserve:

```python
output_key="something"
```

for state that genuinely needs to survive elsewhere in the Workflow.

That reduces the chance of confusing a stale state value with the result of the current child invocation, and avoids making your call stack dependent on a feature that has had intermediate-output bugs earlier in the 2.x line. citeturn18view3turn22view2

### Do not parallelise task delegation by simply asking the manager to issue parallel task calls

There is an interesting detail in `_TaskAgentTool`: its generated description explicitly tells the model **not to call the task-delegation tool in parallel with other tools**. citeturn15view1

For true parallel fan-out, use the Workflow graph's fan-out/fan-in capability rather than relying on several simultaneous child-agent function calls from one chat coordinator. ADK 2's Workflow runtime is explicitly intended to provide deterministic routing and fan-out/fan-in, and the official sample repository contains dedicated `fan_out_fan_in` and `dynamic_fan_out_fan_in` examples. citeturn16search4turn18view1

So:

```text
manager decides which workers are needed
              ↓
Workflow fans out explicitly
       ┌──────┼──────┐
       ▼      ▼      ▼
       A      B      C
       └──────┼──────┘
              ▼
            Join
              ↓
manager/synthesiser
```

is safer than:

```text
LLM emits task(A), task(B), task(C) simultaneously
```

for workloads where concurrency matters.

### Pin a modern 2.x release

I would **not develop this architecture against `google-adk==2.0.0`**.

ADK Python 2.0 became generally available in May 2026, but the framework has evolved rapidly since then. As of 25 August 2026 GitHub marks **2.7.1** as the latest 2.x release, released on 17 August. Version 2.7.0 alone contained more than two hundred changes, including further task-mode work. citeturn2view1turn17view3

There have been concrete 2.x corrections in the exact areas relevant to this architecture:

| Area | Evidence |
|---|---|
| Single-turn branch/session isolation | #5989 documented cross-branch contamination under concurrent `ctx.run_node`; current source now stamps branch and isolation scope on synthetic single-turn input. citeturn21view1turn22view3 |
| Task delegation/resumability isolation | #6457 documented parent delegation calls leaking into a task child under ADK 2.4.0; current dispatch explicitly creates a sub-branch and function-call-specific isolation scope. citeturn21view2turn22view1 |
| Task `output_key` correctness | Release history records a fix preventing intermediate task conversational text from being processed as `output_key`; current code writes terminal task output after successful finish. citeturn18view3turn22view2 |
| Multiple sequential task children | Current wrapper explicitly re-enters the chat coordinator after every completed task delegation so another task can be issued in the same invocation. citeturn22view0 |
| AgentTool migration | Current `AgentTool` source explicitly discourages direct use; official single-turn sample calls native single-turn subagents the replacement. citeturn15view0turn19view2 |

A sensible current pin for testing this pattern is therefore:

```text
google-adk==2.7.1
```

rather than assuming behaviour from 2.0.0 examples. citeturn17view3

### Tests I would make mandatory

For this particular architecture, the important tests are behavioural, not merely “agent returned the right prose”.

| Test | Required execution trace |
|---|---|
| Single worker | `root → manager → A → manager → root` |
| Two sequential workers | `root → manager → A → manager → B → manager → root` |
| Repeated user turn | Next user message begins at `root`, not at previous leaf |
| Child completion | No leaf runs again after its completion unless parent emits a **new** delegation |
| Structured output | Parent receives the child's current function response, not merely an old `output_key` |
| Child with several tool rounds | `task` child stays isolated while using its own tools, then returns once |
| Resumability | Pausing/resuming a leaf cannot expose the parent's delegation function call inside the leaf |
| Parallel workload | Fan-out occurs through Workflow branches rather than parallel `_TaskAgentTool` calls |

Those tests directly target failure modes documented in the ADK repository and current task/single-turn implementation. citeturn21view1turn21view2turn15view1turn22view0

The invariant I would assert in event traces is:

```text
For every invocation:

ROOT
 ├── calls subtree X
 │     ├── manager X calls child A
 │     │      └── A completes
 │     ├── manager X resumes
 │     ├── manager X optionally calls child B
 │     │      └── B completes
 │     └── manager X completes
 ├── ROOT resumes
 │
 └── ROOT alone owns user conversation
```

A completed child appearing again **without a fresh parent delegation event** should fail the test.

## Bottom line

After going through the current docs, source, samples, release notes and relevant 2.x issues, I would distinguish the available patterns this way:

**Google's `three_layer_transfer` sample is the literal hierarchy you asked for, but it is not the execution semantics you asked for.** It demonstrates `root.sub_agents → child.sub_agents → grandchild`, yet it relies on chat transfers in both directions. citeturn19view0turn20view0

**Native task-mode collaboration has the execution semantics you asked for, but not arbitrary structural nesting.** It is real call/return-like delegation: child finishes, a function response goes back to the originating coordinator, and the coordinator resumes. Current source even has a dedicated loop for chained child calls. But task agents must remain leaves. citeturn21view4turn22view0turn3view2

**Single-turn is Google's replacement for the old AgentTool pattern, but I would use it for simple leaves rather than recursive managers.** The framework has had branch/history isolation bugs around single-turn execution, even though current code now contains changes addressing the specific reported problem. citeturn19view2turn21view1turn22view3

**AgentTool should not be the foundation of new ADK 2 hierarchy code.** Google's current source explicitly says direct AgentTool usage is discouraged, and there is an ADK 2 Workflow context-loss report involving AgentTool. It is not removed, but it is no longer the preferred composition primitive. citeturn15view0turn21view3

**The strongest ADK 2-native solution is therefore:**

```text
                     ┌───────────────────────────────┐
                     │ ROOT LLM AGENT                │
                     │ user-facing, chat             │
                     └───────────────┬───────────────┘
                                     │
                            Workflow / NodeTool call
                                     │
                     ┌───────────────▼───────────────┐
                     │ DEPARTMENT WORKFLOW           │
                     │ bounded call scope            │
                     └───────────────┬───────────────┘
                                     │
                     ┌───────────────▼───────────────┐
                     │ MANAGER AGENT                 │
                     │ mode="chat"                   │
                     └───────┬─────────────────┬─────┘
                             │                 │
                        task call         task call
                             │                 │
                     ┌───────▼───────┐ ┌──────▼────────┐
                     │ WORKER A      │ │ WORKER B      │
                     │ mode="task"   │ │ mode="task"   │
                     │ LEAF          │ │ LEAF          │
                     └───────┬───────┘ └──────┬────────┘
                             │                 │
                         return             return
                             └────────┬────────┘
                                      ▼
                                  MANAGER
                                      │
                              Workflow completes
                                      │
                                      ▼
                                    ROOT
```

Google now provides all the constituent examples for this design: **task subagents**, **single-turn subagents**, **three-layer transfer**, **nested Workflows**, and **Workflow/Node-as-Tool**. What it does not currently provide is one example combining them into this strict hierarchical call/return pattern. citeturn18view0turn18view1turn19view1turn19view2turn21view0

That combination is nevertheless the one most consistent with ADK 2's actual architecture: **use the Workflow graph to create scope and nesting, use chat agents to make managerial decisions, and use task-mode agents for leaf RPC-like delegation.** Avoid treating `transfer_to_agent` as a return statement, and avoid trying to make a task-mode manager own another layer of agents. citeturn2view1turn3view2turn22view0