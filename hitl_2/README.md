# HITL 2

This is the minimal LLM-owned ADK 2 HITL pattern. The root agent calls the
built-in `adk_request_input` tool itself. ADK pauses the invocation and later
adds the human response to the same agent conversation as a `FunctionResponse`.

Unlike `hitl/agent.py`, there is no workflow controller, no second explicit
`ctx.run_node()` call, and no need to resend the original request. The model
selects either a boolean checkbox schema for deployment approval or a string
schema for a time-off question.
