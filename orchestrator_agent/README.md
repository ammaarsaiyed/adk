# Workflow-native agent orchestration

This example has one main orchestrator and three specialists: `math`,
`science`, and `english`. A specialist behaves like a called tool: it receives
a typed task, returns a typed result, and never owns the user's conversation.
The orchestrator sees that result and produces the final answer.

```text
user
  -> Workflow controller
       -> orchestrator: delegate(math | science | english)
       -> selected specialist: SpecialistTask -> SpecialistResult
       -> orchestrator: delegate another specialist or finish(answer)
  -> one terminal response
```

## Why the Workflow owns the calls

ADK 2.7.1 recommends `mode="single_turn"` sub-agents over constructing
`AgentTool` directly. That automatic agent-tool pattern still leaves the main
LLM in charge of an open-ended tool loop, so prompt mistakes can cause repeated
calls.

Here, the main `orchestrator` still decides which specialist is needed and
synthesizes its results. The `Workflow` performs the actual call/return with
`ctx.run_node()`. This makes the safety properties structural:

- specialists cannot transfer conversational ownership;
- each specialist can run at most once per request;
- every input and output is validated with Pydantic;
- the loop has a four-step budget: at most three calls and one finish;
- the terminal node renders exactly one final answer.

## Run it

From the repository root:

```bash
uv run adk run orchestrator_agent
```

Try a mixed request to see sequential delegation:

```text
Explain why the sky is blue, calculate the energy of a 500 nm photon, and
rewrite the explanation for a twelve-year-old.
```

Run the deterministic tests without making model API calls:

```bash
uv run pytest tests/test_orchestrator_agent.py -q
```

