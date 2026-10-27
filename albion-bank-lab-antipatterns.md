# Albion Bank Lab: Anti-Patterns and Fixes

Compared `kata-starter` against `origin/main` (the reference solution). Everything is under `cxas_app/Albion_Bank_Card_Protection/`.

The "Improved" snippets below are my own implementations, informed by the reference but deliberately not copies of it, so you can use them as a guide without just pasting the answer. I applied them to a scratch copy of the starter: `cxas lint` passes with no errors, and the kata tests in `test_router_and_auth.py`, `test_callback_chains.py` and `test_bundle_integrity.py` pass, except two that depend on the later greenfield exercises (they also fail on the untouched starter).

There are 3 katas and 13 seeded problems. Kata 1 covers 1A, 1B, 2A, 2B and 2C. Kata 2 covers 3B, 4A and 4B. Kata 3 covers 5, 5A, 5B, 6A and 6B. The Day 1 afternoon and Day 2 morning exercises are different: they are greenfield TODOs, not anti-patterns. They are covered at the end.

## Matching this guide to the workbook

The workbook numbers its steps (1A, 1B, 1C, 2A, ...), but the code comments use their own IDs (`# KATA 1 ANTI-PATTERN 2A`, and so on). This guide uses the **code IDs**. Use this table to jump between them.

| Workbook step | What it covers | Section in this guide |
|---|---|---|
| Kata 1, Step 1A | Router prompt | 1A |
| Kata 1, Step 1B | Auth prompt | 1B |
| Kata 1, Step 1C | Three fixes in `authenticate_caller` | 2A, 2B, 2C |
| Kata 2, Step 2A | `agent_transfer` callback | 3B |
| Kata 2, Step 2B | `release_line` callback | 4A |
| Kata 2, Step 2C | Callback order in `card_freeze_agent.json` | 4B |
| Kata 3, Step 3A | Remove raw toolsets | 5 |
| Kata 3, Step 3B | Three fixes in `manage_card_freeze` | 5A, 5B, 6A |
| Kata 3, Step 3C | `capture_utterance` Stage 6 and 7 | 6B |

---

## What the app is

**Albion Bank Card Protection** is a voice (and chat) assistant for a fictional UK bank, built on Google's CX Agent Studio (GECX / CXAS). The assistant is called **Clara**. A customer rings in and Clara helps with one of five things:

| Journey | What the caller wants | Handled by |
|---|---|---|
| Card freeze / unfreeze, and card policy questions | "Lock my card", "can I still use direct debits?" | `card_freeze_agent` |
| Declined payment | "Why was my payment at Tesco declined?" | `declined_payment_agent` |
| Overseas travel and blocks | "I'm going to Spain", "my card was blocked abroad" | `travel_and_block_agent` |
| Unrecognised transaction | "I don't recognise this £80 charge" | `fraud_triage_agent` |
| Anything else, or too many failures | | A human banking specialist (escalation) |

Because it's a bank on a live phone line, the app has strict rules: the caller must be verified before any account action, legal (FCA) disclosures must be read word-for-word, nothing internal can be spoken, and failures must end in a clean handoff to a human.

### The call flow

```
Caller
  -> intent_router_agent       greet, work out what they want, confirm it ("Just to confirm, you'd like to freeze your card ending 4821?")
  -> authentication_agent      identify the card (from the phone number or 4 digits), verify the 6-digit passcode, max 2 attempts
  -> one specialist agent      card_freeze / declined_payment / travel_and_block / fraud_triage
  -> hang up (or hand to a human)
```

### The core design idea: controllers, not prompts

Each agent has a **prompt** (`instruction.txt`) and one **Python controller tool**. The LLM's job is narrow: understand what the caller said, call the controller with the right slots, and speak the `agent_action` text it gets back. The controller does everything deterministic: the decisions, the state, the backend API calls and the escalation rules. **Callbacks** are Python hooks that run before and after each model call. They handle the things an LLM shouldn't be trusted with: transferring between agents, hanging up, silence handling and regulatory wording.

Nearly all of the 13 anti-patterns break this split. Either the LLM is doing a controller's job, or a controller or callback isn't doing its own.

### Key files

All paths are under `cxas_app/Albion_Bank_Card_Protection/`.

| File | What it is |
|---|---|
| `app.json` | App config: the root agent (`intent_router_agent`), model settings, and `variableDeclarations`, the list of all legal session-state keys. Writing to a key not on this list is lint error `V100`. |
| `global_instruction.txt` | Prompt shared by every agent: Clara's persona, British English, "rely on your controller tool", and the voice rules (speech tags, say numbers as words, read disclosures verbatim). |
| `agents/<name>/instruction.txt` | One agent's prompt: `<role>`, `<persona>`, `<constraints>`, `<taskflow>`, `<examples>`. |
| `agents/<name>/<name>.json` | The agent manifest: its tools, and the **ordered** lists of before-model and after-model callbacks. |
| `agents/<name>/before_model_callbacks/` | Run before the LLM sees the turn. `capture_utterance` (housekeeping, pass counting, silent transfer, verbatim disclosure, loop cap) and `silence_detection` (the 2-strike "are you there?" rule, and answering stray audio after hang-up). |
| `agents/<name>/after_model_callbacks/` | Run on the LLM's output. `agent_transfer` (do the handoff queued in `target_agent`), `release_line` (hang up when `call_complete` is set) and `normalise_passcode` (restore leading zeros the LLM dropped). |
| `tools/<name>/python_function/python_code.py` | The controller tools. `classify_banking_intent` (router), `authenticate_caller`, `manage_card_freeze`, `manage_travel_and_blocks`, `assist_declined_payment` and `triage_unrecognised_transaction`. |
| `toolsets/<name>/` | OpenAPI definitions for the backends. `albion_core_api` (customers, cards, passcodes), `albion_fraud_api` (transaction scan, card cancellation), `payment_support_api` (declined-payment diagnosis) and `vault_kb_api` (policy knowledge base). Only controllers should call these. |
| `tests/` | Unit tests. `test_router_and_auth.py` (kata 1), `test_callback_chains.py` (katas 2 and 3), `test_bundle_integrity.py`. |
| `scripts/prepare_kata_starter.py` | Generates the broken `kata-starter` branch from the finished code. This is where the seeded `# KATA n ANTI-PATTERN` comments come from. |

### Session-state keys you will see in the fixes

State is a dict shared by the prompts, controllers and callbacks. All values are strings.

| Key | Meaning |
|---|---|
| `confirmed_intent` | What the caller wants, once they have said yes to the confirmation (for example `CARD_FREEZE`). |
| `target_agent` | A **queued transfer**. A controller sets it, and `agent_transfer` or `capture_utterance` acts on it and should clear it. |
| `call_complete` | `"true"` means the call is over and should be ended. `"released"` means it has been ended (a latch). |
| `escalation_reason` | Why the call is being handed to a human (for example `PASSCODE_FAILED`). |
| `auth_attempts` | Failed passcode count. |
| `card_last4`, `card_confirmed` | The identified card, and whether the caller said "yes" to the readback. |
| `verbatim_script`, `verbatim_lock_barge_in` | A regulatory disclosure staged to be read word-for-word, and whether the caller may interrupt it. |
| `pass_count` | How many model calls this turn has used, so runaway loops can be capped. |

---

## How to detect each one: linter vs tests

I ran `uv run cxas lint` on the starter (12 errors, 3 warnings). The linter only catches the structural problems. The behavioural ones need the unit tests or a read of the code.

| ID | `cxas lint` catches it? | Rule or test |
|---|---|---|
| 1A | Yes | `I001` missing `<persona>`, plus `I015` banned `<state_update>` tag (8 hits). `I003` (excessive IF/ELSE) is in the rule list, but it didn't fire on this prompt. |
| 1B | Partly | `I012` fires for the router (`classify_banking_intent` is never referenced), not for the auth prompt. The auth prompt's problems are caught by `-k kata1` tests. |
| 2A, 2B, 2C | No | `uv run pytest tests/test_router_and_auth.py -k kata1` |
| 3B, 4A | No | `uv run pytest tests/test_callback_chains.py -k kata2` |
| 4B | No | Callback order is not linted. The `-k kata2` tests check it. |
| 5 | Yes, but the message is odd | `A003` "lists tool 'albion_core_api' but it does not exist" (they're toolsets, not tools), and `I012` warnings for both |
| 5A | Yes | `V100` "State key 'language' is not declared in app.json variableDeclarations". The premature `confirmed` check is not linted. |
| 5B, 6A, 6B | No | `-k kata3` tests |

Reading the lint output the other way: after your fixes it should be 0 errors and 0 warnings. If `A003` or `I012` remain, you missed 5 or 1A.

---

## Kata 1: Prompts and the authentication controller

### 1A. Router prompt is a monolith (`agents/intent_router_agent/instruction.txt`)

**What it's trying to do:** This is the first agent the caller hears. Its job is to find out why they called, read it back for confirmation ("you'd like to freeze your card ending 4821?"), and send them on to be verified. `classify_banking_intent` already holds that logic: the five intents, the yes/no confirmation, the "I didn't understand" retry count, and the escalation to a human.

**Starter:** The prompt has a block of `IF / ELSE IF` rules. It uses `<state_update>confirmed_intent=...</state_update>` tags, hardcodes `{@AGENT: authentication_agent}` and `{@AGENT: card_freeze_agent}` transfers, and tracks `{unclear_retry_count}` in prose.

**Why it's bad:**
- The linter flags `<state_update>` as a banned tag (`I015`): custom XML tags can trigger thought-leakage safety filters.
- The LLM is acting as the router, state machine and counter. Those are deterministic jobs, and the model will sometimes get them wrong. It can skip a branch, miscount retries, or write state in the wrong format.
- The routing logic is invisible to tests and the linter, because it lives in prose.
- The tool `classify_banking_intent` exists for exactly this job, so the prompt duplicates it.

**Fix:**
- Use five XML sections: `<role>`, `<persona>`, `<constraints>`, `<taskflow>` and `<examples>`.
- Tell the model to call `{@TOOL: classify_banking_intent}` every turn and speak `agent_action`.
- Remove all branching, `<state_update>` tags and `{@AGENT}` tokens.
- Add a constraint that `end_session` is callback-only.
- Add 1–2 few-shot examples in the `EXAMPLE 1: / Begin example / [user] / [model] / End example` format.

Rule of thumb: the prompt describes behaviour and tone, and tools or callbacks decide control flow.

The repo's design guide (`.agents/skills/cxas-agent-foundry/references/gecx-design-guide.md`, "Instruction Design Anti-Patterns") backs this up: "Complex programmatic logic in instructions" and "`conditional_logic` for intent classification" are both listed as anti-patterns, with the fix "use separate `<step>` elements with distinct triggers" and "simple natural language". It also says the LLM decides WHAT (detects intent) and the callback or tool decides HOW (executes).

#### Before and after

The workbook asks for: five XML sections (`<role>`, `<persona>`, `<constraints>`, `<taskflow>`, `<examples>`), a call to `classify_banking_intent` every turn, no `IF/ELSE`, `<state_update>` or `{@AGENT}`, an `end_session` constraint, and 1–2 examples, in under 60 lines. The starter has only two of the five sections and none of the rest.

Faulty (starter, complete file; note there is no `<persona>` and no `<examples>`):
```text
<role>
You are Clara, Albion Bank's retail card protection assistant handling intent routing. Today's date is {current_date}.
</role>

<constraints>
1. IF the customer asks to freeze or unfreeze their card, set <state_update>confirmed_intent="CARD_FREEZE"</state_update> and transfer directly to {@AGENT: authentication_agent}.
2. ELSE IF the customer asks a general policy question about direct debits or digital wallets, transfer directly to {@AGENT: card_freeze_agent}.
3. ELSE IF the customer mentions a declined card payment or digital wallet decline, set <state_update>confirmed_intent="DECLINED_PAYMENT"</state_update> and transfer to {@AGENT: authentication_agent}.
4. ELSE IF the customer mentions an overseas decline or travel notice, set <state_update>confirmed_intent="OVERSEAS_TRAVEL_AND_BLOCKS"</state_update> and transfer to {@AGENT: authentication_agent}.
5. ELSE IF the customer reports an unrecognised transaction, set <state_update>confirmed_intent="UNRECOGNISED_TRANSACTION"</state_update> and transfer to {@AGENT: authentication_agent}.
6. ELSE increment {unclear_retry_count} by 1. IF {unclear_retry_count} >= 2, transfer to a human specialist; OTHERWISE ask the customer to clarify their request.
</constraints>

<taskflow>
  <subtask name="monolithic_prompt_router">
    <step name="step_1_branch_in_prose">
      <trigger>On every customer turn before routing.</trigger>
      <action>Follow the IF/ELSE rules above to classify the request, track {unclear_retry_count} in prose, and transfer to {@AGENT: authentication_agent} or {@AGENT: card_freeze_agent}.</action>
    </step>
  </subtask>
</taskflow>
```

Improved (one possible solution; yours can differ as long as it meets the workbook checklist):
````text
<role>
You are Clara, the Albion Bank virtual assistant. You work out why a customer is calling and confirm it with them before they are verified.
</role>

<persona>
Calm, friendly British English. Short spoken sentences, one question at a time.
</persona>

<constraints>
1. Call {@TOOL: classify_banking_intent} on every customer turn. It decides the intent, the confirmation step and what happens next.
2. Say what `agent_action` tells you to say, as one plain paragraph. When confirming, name the action and the 4-digit card ending if the caller gave one.
3. Do not greet the customer again after the opening, and never promise a named team. If a handover is needed, say "a general Albion Bank specialist".
4. {@TOOL: end_session} is reserved for callbacks. Never call it yourself.
</constraints>

<taskflow>
  <subtask name="route_customer_request">
    <step name="classify_or_confirm">
      <trigger>The customer says why they are calling, or answers yes or no to a confirmation question.</trigger>
      <action>Call {@TOOL: classify_banking_intent} with only the field that matches what they just said, then speak the guidance from `agent_action`.</action>
    </step>
  </subtask>
</taskflow>

<examples>
EXAMPLE 1:
Begin example
[user]
There's a payment on my card I don't recognise, it was eighty quid at some electronics place.
[model]
```tool_code
classify_banking_intent(intent_id="UNRECOGNISED_TRANSACTION")
```
```tool_outputs
{"agent_action": "Confirm with the customer that they would like help reviewing an unrecognised transaction, and ask them to say yes or no."}
```
[model]
I'm sorry to hear that. Just to confirm, you'd like help reviewing a payment you don't recognise. Is that right?
End example

EXAMPLE 2:
Begin example
[user]
Yes, that's right.
[model]
```tool_code
classify_banking_intent(confirmation="yes")
```
```tool_outputs
{"agent_action": "Thank the customer and let them know you will now complete a quick security check."}
```
[model]
Thank you. I'll just complete a quick security check first.
End example
</examples>
````

Why it's better: each workbook checkbox is met. `<persona>` and `<examples>` are present, with the exact `EXAMPLE n: / Begin example / [user] / [model] / End example` format the platform needs. The only instruction is "call the tool, speak `agent_action`", and the branching, retry counting and transfers are in the tool and callbacks. The file is 55 lines, under the 60-line limit.

### 1B. Auth prompt hardcodes the destination (`agents/authentication_agent/instruction.txt`)

**What it's trying to do:** This agent proves the caller is who they say they are before any account action: identify the card, then verify the passcode. Once that succeeds, the caller needs to go to whichever specialist matches what they asked for earlier. The destination depends on `confirmed_intent` from the router, so only the tool knows it.

**Starter:** The prompt says "route the customer to `{@AGENT: card_freeze_agent}`". It has no `end_session` constraint and no examples.

**Why it's bad:**
- `authenticate_caller` already sets `state["target_agent"]` from `confirmed_intent`, and the `agent_transfer` callback performs the transfer. Hardcoding card_freeze here means a declined-payment or fraud caller would be sent to the wrong agent. It also makes the LLM race the callback.
- The `I012` lint rule requires every tool attached to the agent to be referenced in the prompt. `end_session` is attached, so it must be mentioned (as callback-only).
- With no examples, the model has nothing to anchor on for the tool-call format.

**Fix:** Delete every `{@AGENT: ...}` reference. Add the `{@TOOL: end_session}` constraint ("only through lifecycle callbacks"). Add `<examples>` showing `authenticate_caller(...)` followed by speaking `agent_action`.

#### Before and after

The workbook asks for: delete every `{@AGENT: card_freeze_agent}` from `<constraints>` and `<taskflow>`, add the `{@TOOL: end_session}` constraint, and add an `<examples>` section showing a turn that calls `authenticate_caller`.

Faulty (starter, complete file; no `end_session` constraint, no `<examples>`, hardcoded agent):
```text
<role>
You are Clara, Albion Bank's retail card protection assistant handling customer identification and security verification. Today's date is {current_date}.
</role>

<persona>
Speak in warm, clear, concise British English suitable for a telephone call.
</persona>

<constraints>
1. Always call {@TOOL: authenticate_caller} to identify the customer's card and verify their 6-digit security passcode.
2. When verification succeeds, route the customer to {@AGENT: card_freeze_agent}.
3. Follow the spoken guidance returned by {@TOOL: authenticate_caller}.
</constraints>

<taskflow>
  <subtask name="verify_caller_identity">
    <step name="invoke_authenticate_caller">
      <trigger>Whenever the customer confirms their card ending, states their 4-digit card number, or provides their 6-digit passcode.</trigger>
      <action>Call {@TOOL: authenticate_caller} and then transfer to {@AGENT: card_freeze_agent} once verified.</action>
    </step>
  </subtask>
</taskflow>
```

Improved (one possible solution; yours can differ as long as it meets the workbook checklist):
````text
<role>
You are Clara, the Albion Bank virtual assistant. In this part of the call you identify the customer's card and check their security passcode.
</role>

<persona>
Reassuring, clear British English. Keep each turn short and ask for one thing at a time.
</persona>

<constraints>
1. Never make up card details, account statuses or passcodes.
2. Only ask for what {@TOOL: authenticate_caller} asks for, and say its `agent_action` guidance directly. Do not add a stand-alone "Thank you." before the question.
3. {@TOOL: end_session} is reserved for callbacks. Never call it yourself.
</constraints>

<taskflow>
  <subtask name="verify_caller">
    <step name="call_authentication_tool">
      <trigger>The customer arrives, gives their card digits, answers a card readback, says their passcode, or changes what they need help with.</trigger>
      <action>Call {@TOOL: authenticate_caller} with only the field that matches what they just said (use `switch_intent` if they change topic), then speak the guidance from `agent_action`.</action>
    </step>
  </subtask>
</taskflow>

<examples>
EXAMPLE 1:
Begin example
[user]
Actually the card I'm calling about ends in 7390.
[model]
```tool_code
authenticate_caller(card_last4="7390")
```
```tool_outputs
{"agent_action": "Ask the customer for their 6-digit security passcode, which they can say aloud or enter on their keypad."}
```
[model]
Thanks. What's your 6-digit security passcode? You can say it or type it on your keypad.
End example
</examples>
````

Why it's better: no `{@AGENT}` anywhere (the tool queues the right destination through `target_agent`), `end_session` is documented as callback-only (this satisfies lint `I012` and the unit test), and `<examples>` shows the tool-call format. The workbook only requires one example, and one is enough here.

### 2A. Control token checked before substantive slots (`tools/authenticate_caller`, Step 4)

**What it's trying to do:** `authenticate_caller` is a small state machine for identification. It reads the caller's phone number, asks "is your card ending 4821?", takes a yes or no, then asks for the passcode. Callers don't answer in neat single steps, so the tool has to cope with several things arriving at once.

**Starter:** `number_confirmed in ("yes","no")` is evaluated first, then `card_last4`, then `passcode`.

**Why it's bad:** The model often fills several slots in one utterance. For "No, wait, my card ends in 4821" it sets `number_confirmed="no"` and `card_last4="4821"`. The starter handles only the "no" and drops the digits. The caller then has to repeat themselves. The same happens with "Yes, and my passcode is 123456".

**Fix:** Evaluate in this order, with data before control tokens:
1. `card_last4 and passcode and not card_confirmed` → `_lookup_then_verify`
2. `passcode` → `_verify_passcode`. If `number_confirmed == "yes"` and a `card_last4` is already in state, set `card_confirmed = True` first.
3. `card_last4` → `_lookup_card_last4`
4. `number_confirmed in ("yes","no")` → `_handle_card_confirmation`
5. `card_confirmed` → `_prompt_for_passcode`
6. otherwise → `_initiate_identification`

#### Before and after

Faulty (starter):
```python
if number_confirmed in ("yes", "no"):
    return _handle_card_confirmation(number_confirmed)
if card_last4:
    return _lookup_card_last4(card_last4)
if passcode:
    return _verify_passcode(passcode)
return _initiate_identification()
```

Improved (one possible solution; yours can differ as long as it meets the workbook checklist):
```python
        # Step 4: Identify and verify. Anything the caller actually said (digits) is handled
        # before the yes/no flags, because one utterance can fill several slots at once.
        if card_last4 and passcode and not state.get("card_confirmed"):
            return _lookup_then_verify(card_last4, passcode)
        if passcode:
            # "Yes, and my passcode is ..." confirms the readback as well.
            if number_confirmed == "yes" and state.get("card_last4"):
                state["card_confirmed"] = True
            return _verify_passcode(passcode)
        if card_last4:
            return _lookup_card_last4(card_last4)
        if number_confirmed in ("yes", "no"):
            return _handle_card_confirmation(number_confirmed)
        if state.get("card_confirmed"):
            return _prompt_for_passcode()
        return _initiate_identification()
```

Why it's better: real data (digits) is checked before yes/no flags, so "No, wait, my card ends in 4821" uses the new digits instead of discarding them.

### 2B. Raw backend dict returned (`_initiate_identification`)

**What it's trying to do:** When the call arrives with a phone number, the app looks the customer up, saves the match, and asks "I can see a card ending 4821, is that the one you're calling about?". This saves the caller from reading out their card digits.

**Starter:** On a CLI match the tool does `return dict(payload)`, which is the raw CRM response.

**Why it's bad:**
- The design guide's error-handling rules say tools must return an `agent_action` key to give the model deterministic next steps (lint rule `T001` covers the error path). Every tool must return the `agent_action` contract that tells the model what to say. Without it the model improvises.
- It leaks internal fields (`customer_id`, card status and so on) into the LLM context.
- The `{"matched": true, ...}` shape means nothing to the prompt.

**Fix:** `return _ask_to_confirm_card(payload["card_last4"])`. Copy only the needed fields into state, and return only guidance.

#### Before and after

The workbook asks: when the phone-number lookup matches a customer, call `_ask_to_confirm_card(...)` instead of returning the raw `payload`.

Faulty (starter, complete function):
```python
def _initiate_identification() -> dict[str, Any]:
    cli = context.state.get("session_cli") or ""
    if cli:
        resp = tools.albion_core_api_lookupCustomerByCli({"cli": cli})
        if resp.ok:
            payload = resp.json()
            if payload.get("status") != "error" and payload.get("matched"):
                context.state["cli_matched"] = True
                context.state["customer_id"] = payload["customer_id"]
                context.state["card_last4"] = payload["card_last4"]
                context.state["active_card_status"] = payload["card_status"]
                return dict(payload)   # raw CRM response, no agent_action
    if context.state.get("card_last4") and not context.state.get("card_confirmed"):
        return _ask_to_confirm_card(str(context.state["card_last4"]))
    return _reply("Ask the customer for the last four digits of their Albion Bank card.")
```

Improved (one possible solution; yours can differ as long as it meets the workbook checklist):
```python
def _initiate_identification() -> dict[str, Any]:
    """Match inbound CLI if available; broken starter returns raw backend payload without agent_action."""
    cli = context.state.get("session_cli") or ""
    if cli:
        resp = tools.albion_core_api_lookupCustomerByCli({"cli": cli})
        if resp.ok:
            payload = resp.json()
            if payload.get("status") != "error" and payload.get("matched"):
                context.state["cli_matched"] = True
                context.state["customer_id"] = payload["customer_id"]
                context.state["card_last4"] = payload["card_last4"]
                context.state["active_card_status"] = payload["card_status"]
                # Only guidance goes back to the model, never the raw CRM payload.
                return _ask_to_confirm_card(payload["card_last4"])
    if context.state.get("card_last4") and not context.state.get("card_confirmed"):
        return _ask_to_confirm_card(str(context.state["card_last4"]))
    return _reply("Ask the customer for the last four digits of their Albion Bank card.")
```

Why it's better: the only change is the return line, but it means every path out of the tool returns `{"agent_action": ...}`. The caller is asked "is your card ending 4 - 8 - 2 - 1?" and the CRM's internal fields stay in Python.

### 2C. No retry counter or lockout (`_verify_passcode`)

**What it's trying to do:** Verify the 6-digit passcode with the bank's backend. On a wrong answer, give them one more go, then stop and hand over to a human. That is a normal banking lockout rule.

**Starter:** A wrong passcode just returns "try again". `auth_attempts` is never incremented.

**Why it's bad:** There is no brute-force protection. Anyone can guess passcodes forever, which is a security hole in a bank flow. The retry limit also lives nowhere, so the model could never enforce it.

#### Before and after

The workbook asks: increment `auth_attempts` (a string in `app.json`) on every failed passcode check, and call `_escalate("PASSCODE_FAILED")` when it reaches `_MAX_AUTH_ATTEMPTS` (2).

Faulty (starter, tail of `_verify_passcode`):
```python
    if data.get("verified"):
        context.state["auth_verified"] = True
        context.state["auth_attempts"] = "0"
        return _route_authenticated_customer()

    # KATA 1 ANTI-PATTERN 2C: Does not increment context.state["auth_attempts"] ...
    return _reply(
        "Let the customer know that passcode did not match our records, and ask them "
        "to try their 6-digit security passcode one more time."
    )
```

Improved (one possible solution; yours can differ as long as it meets the workbook checklist):
```python
def _register_failed_attempt() -> int:
    """Count one more failed passcode (state values are strings) and return the new total."""
    total = int(context.state.get("auth_attempts") or "0") + 1
    context.state["auth_attempts"] = str(total)
    return total


# ... inside _verify_passcode, after the `if data.get("verified"):` branch:
    if _register_failed_attempt() >= _MAX_AUTH_ATTEMPTS:
        return _escalate("PASSCODE_FAILED")
    return _reply(
        "Let the customer know that passcode did not match our records, and ask them "
        "to try their 6-digit security passcode one more time."
    )
```

Why it's better: the limit is enforced in code. The first miss gets "try once more", the second hands over to a human, and a success resets the counter to `"0"`.

---

## Kata 2: Callback chains

### 3B. `agent_transfer` double-speaks and can loop (`authentication_agent/after_model_callbacks/agent_transfer`)

**What it's trying to do:** After verification, the tool sets `target_agent = "card_freeze_agent"` and the callback performs the handoff after the model speaks. The caller should hear one smooth line ("Thanks, you're verified, I'll help with that now"), and the next agent should start fresh.

**Starter:** It reads `target_agent` but never clears it. It also always keeps the model's text parts alongside `Part.from_agent_transfer`.

**Why it's bad:**
- `target_agent` stays set, so the transfer re-fires on later passes or in the next agent. That can loop or bounce.
- On live calls (Gemini Live) the text has already been streamed as audio. Resending it makes the caller hear the line twice.
- There's no self-transfer guard.

**Fix:** Clear `target_agent` right after reading it, skip self-transfers, and send only the transfer part on live calls. Eval sessions keep the text so golden tests can still score the wording.

#### Before and after

The workbook asks for three things: clear `target_agent` right after reading it, return `None` if the target equals `callback_context.agent_name`, and on live calls return only the transfer part, keeping the text parts only for offline evaluation runs (`session_id` starts with `"evaluation"`).

Faulty (starter, complete callback):
```python
def after_model_callback(callback_context, llm_response):
    parts = llm_response.content.parts if llm_response.content else []
    if any(p.function_call for p in parts):
        return None

    target = callback_context.state.get("target_agent")
    if not target:
        return None

    # KATA 2 ANTI-PATTERN 3: Does not clear target_agent="" and unconditionally keeps text_parts
    text_parts = [p for p in parts if p.text]
    return LlmResponse.from_parts(
        parts=text_parts + [Part.from_agent_transfer(agent=target)]
    )
```

Improved (one possible solution; yours can differ as long as it meets the workbook checklist):
```python
from typing import Optional


def after_model_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> Optional[LlmResponse]:
    """Append Part.from_agent_transfer when a controller tool has queued a target agent.

    Args:
        callback_context: Runtime callback context with session_id, agent_name and state.
        llm_response: Model response from the post-tool LLM pass.

    Returns:
        LlmResponse containing Part.from_agent_transfer, or None if no transfer is queued.
    """
    state = callback_context.state
    parts = llm_response.content.parts if llm_response.content else []
    if any(p.function_call for p in parts):
        return None  # a tool call is still pending

    target = state.get("target_agent")
    if not target:
        return None
    state["target_agent"] = ""  # consume it so the transfer cannot fire twice
    if target == callback_context.agent_name:
        return None

    # Live calls have already played the model's words, so send only the transfer; keep the
    # text in evaluation sessions so the goldens can still check what was said.
    in_eval = str(callback_context.session_id).startswith("evaluation")
    spoken = [p for p in parts if p.text] if in_eval else []
    return LlmResponse.from_parts(parts=spoken + [Part.from_agent_transfer(agent=target)])
```

Why it's better: all three workbook points are in. The transfer fires once, never targets the current agent, and on live calls the caller doesn't hear the transition line twice.

### 4A. `release_line` mutates in place and returns None (`card_freeze_agent/after_model_callbacks/release_line`)

**What it's trying to do:** When the specialist has finished, or has escalated to a human, the tool sets `call_complete = "true"`. This callback turns that flag into a real hang-up (`Part.from_end_session`), with the right reason and an `escalated` flag so the platform can route the call to a human queue.

**Starter:** `parts.append(Part.from_end_session(...)); return None`

**Why it's bad:** `callback-api.md` says to build responses with `LlmResponse.from_parts()` and never mutate the one you were given. CXAS only honours a returned `LlmResponse`. Returning `None` means "no change", so the in-place edit is discarded and the call never hangs up. Nothing latches `call_complete`, so even a working hang-up could fire more than once.

#### Before and after

The workbook asks for three things: latch `state["call_complete"] = "released"`, work out the reason (`escalation_reason` or `"CALL_COMPLETED"`) and whether the call was escalated, and return a new `LlmResponse.from_parts(...)` containing the spoken text parts plus `Part.from_end_session(...)`.

Faulty (starter, complete callback body):
```python
parts = llm_response.content.parts if llm_response.content else []
if any(p.function_call for p in parts):
    return None

if callback_context.state.get("call_complete") != "true":
    return None

reason = callback_context.state.get("escalation_reason") or "CALL_COMPLETED"
escalated = reason != "CALL_COMPLETED"

# KATA 2 ANTI-PATTERN 4: Mutates parts in-place and returns None without latching
parts.append(Part.from_end_session(reason=reason, escalated=escalated))
return None
```

Improved (one possible solution; yours can differ as long as it meets the workbook checklist):
```python
from typing import Optional


def after_model_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> Optional[LlmResponse]:
    """Replace the response with the closing words plus Part.from_end_session.

    Args:
        callback_context: Runtime callback context with session state.
        llm_response: Model response for the closing turn.

    Returns:
        A new LlmResponse that ends the session, or None if the call stays open.
    """
    state = callback_context.state
    parts = llm_response.content.parts if llm_response.content else []
    if any(p.function_call for p in parts) or state.get("call_complete") != "true":
        return None

    state["call_complete"] = "released"  # latch: later packets are answered by silence_detection
    reason = state.get("escalation_reason") or "CALL_COMPLETED"
    closing = [p for p in parts if p.text]
    end = Part.from_end_session(reason=reason, escalated=reason != "CALL_COMPLETED")
    return LlmResponse.from_parts(parts=closing + [end])  # new object: in-place edits are ignored
```

Why it's better: CXAS only honours a returned `LlmResponse`, so the hang-up now actually happens. The latch means it happens once, and `silence_detection` and `capture_utterance` can recognise the released line.

### 4B. Wrong callback wiring (`card_freeze_agent/card_freeze_agent.json`)

**What it's trying to do:** Each agent runs a pipeline of callbacks around the model. Before the model: `capture_utterance` (housekeeping), then `silence_detection` ("are you there?", and cleaning up stray audio after hang-up). After the model: `agent_transfer` (hand off if queued), then `release_line` (hang up if finished). The JSON file is where the pipeline and its order are defined.

**Starter:**
- `beforeModelCallbacks` has only `capture_utterance`. `silence_detection` is missing.
- `afterModelCallbacks` runs `release_line` before `agent_transfer`.

**Why it's bad:**
- With no `silence_detection`, a "no user activity" or post-hangup SIP audio turn goes straight to the LLM. The released-line guard in `capture_utterance` returns `None` on purpose and relies on `silence_detection` to re-emit `end_session`.
- Callback order matters. `release_line` returns a new `LlmResponse` that ends the session. If it runs first, a pending transfer is never emitted or is clobbered.

**Fix:** Before: `capture_utterance`, then `silence_detection`. After: `agent_transfer`, then `release_line`. `travel_and_block_agent.json` is the reference.

#### Before and after

Faulty (starter `card_freeze_agent.json`):
```json
"beforeModelCallbacks": [
  { "description": "capture_utterance", ... }
],
"afterModelCallbacks": [
  { "description": "release_line", ... },
  { "description": "agent_transfer", ... }
]
```

Improved (the JSON change is the same whatever way you do it):
```json
"beforeModelCallbacks": [
  { "description": "capture_utterance", ... },
  { "description": "silence_detection", ... }
],
"afterModelCallbacks": [
  { "description": "agent_transfer", ... },
  { "description": "release_line", ... }
]
```

Why it's better: silence handling is back in the pipeline, and a queued transfer is emitted before the hang-up check can end the call.

---

## Kata 3: Tool encapsulation and disclosures

### 5. Raw toolsets exposed to the model (`card_freeze_agent.json`)

**What it's trying to do:** Each agent gets a short, curated list of tools. `manage_card_freeze` wraps the core API (freeze, unfreeze, card status) and the policy knowledge base (VaultKB), and adds the rules: who can freeze, what must be said, what to do on errors.

**Starter:** `"tools"` lists `albion_core_api` and `vault_kb_api` next to `manage_card_freeze`.

**Why it's bad:** The model can call the freeze API or the knowledge base directly. That bypasses the controller's validation, disclosures, auth checks and escalation logic.

The design guide says the same ("Prefer Toolset Wrappers"): if an OpenAPI endpoint returns data the LLM doesn't need, or calls need chaining, don't expose the raw OpenAPI tools. Write a Python wrapper that calls `tools.<Toolset>_<operationId>` internally, filters the result and returns clean JSON. `manage_card_freeze` is that wrapper.

**Fix:** Leave only `manage_card_freeze` and `end_session`. The controller calls the APIs through `tools.*` internally.

#### Before and after

Faulty (starter `card_freeze_agent.json`):
```json
"tools": ["manage_card_freeze", "albion_core_api", "vault_kb_api", "end_session"]
```

Improved (the JSON change is the same whatever way you do it):
```json
"tools": ["manage_card_freeze", "end_session"]
```

Why it's better: the model can only reach the backends through the controller, which applies the checks and disclosures. As a side effect, the `A003` and `I012` lint findings go away.

### 5A. Undeclared state write and premature confirmation check (`manage_card_freeze`)

**What it's trying to do:** `manage_card_freeze` decides what to do based on what the caller said. A typical case: the system asks "do you want a permanent replacement?", and the caller answers "No, just freeze it for now". That one reply has two parts: a "no" to the question and a request to freeze. The tool should act on the request.

**Starter has two problems:**
1. `context.state["language"] = "en-GB"` writes a key that isn't declared in `app.json`. The linter flags this as `V100`, and it's a hidden side effect.
2. Step 5 checks `if confirmed in ("yes","no")` before `if action in ("freeze","unfreeze")`.

**Why it's bad:** This is the same slot-precedence bug as 2A. If the caller is asked to confirm a permanent replacement and says "No, just freeze it temporarily", the model fills `confirmed="no"` and `action="freeze"`. The starter treats it as a plain "no" to the replacement and never freezes the card.

**Fix:** Delete the `language` line and the early `confirmed` block. Substantive action slots come first.

#### Before and after

The workbook asks for two deletions: the undeclared `context.state["language"] = "en-GB"` line at the top of `manage_card_freeze`, and the premature `if confirmed in ("yes", "no"):` block above `if action in ("freeze", "unfreeze"):` in Step 5.

Faulty (starter, both problems):
```python
state = context.state
_consume_handoff_context()

# KATA 3 ANTI-PATTERN 5A: Mutates undeclared session state key ... (trips V100)
context.state["language"] = "en-GB"                       # problem 1

# Step 1: Mid-conversation intent switch ...
...
# Step 5: Core card action (broken starter checks confirmed before substantive action slots).
if confirmed in ("yes", "no"):                            # problem 2
    return _handle_permanent_replace(confirmed)
if action in ("freeze", "unfreeze"):
    return _set_temporary_freeze(action == "freeze", policy_topic)
if action == "replace" or state.get("freeze_pending_action") == "replace":
    return _handle_permanent_replace(confirmed)
return _check_card_status()
```

Improved (one possible solution; yours can differ as long as it meets the workbook checklist):
```python
# (the `context.state["language"] = "en-GB"` line at the top of the tool is simply deleted)

        # Step 5: Core card action. An explicit action beats a yes/no in the same utterance.
        if action in ("freeze", "unfreeze"):
            return _set_temporary_freeze(action == "freeze", policy_topic)
        if action == "replace" or state.get("freeze_pending_action") == "replace":
            return _handle_permanent_replace(confirmed)
        return _check_card_status()
```

Why it's better: with the `language` line gone, `V100` is clean. With the early `confirmed` block gone, "No, just freeze it temporarily" (`confirmed="no"`, `action="freeze"`) freezes the card. A bare "no" still reaches the replacement flow through the `replace` branch.

### 5B. Internal article ID leaked (`_answer_policy_question`)

**What it's trying to do:** Customers ask policy questions like "will my direct debits still work while my card is frozen?". The tool looks up the answer in VaultKB and returns a short `spoken_summary` for Clara to say. The knowledge base also returns an internal `article_id` for the bank's own tracking.

**Starter:** `_reply(f"[{article_id}] {summary}")` with `article_id` defaulting to `ALB-KB-4412-INTERNAL`.

**Why it's bad:** `agent_action` is spoken. The caller would hear "ALB-KB-4412-INTERNAL…". It also ends up in conversation history, and `agent_action` is guidance rather than raw data. The starter also dropped the "Then ask if they need anything else" follow-up.

**Fix:** Return `f"Answer the customer's question clearly: {summary} Then ask: {resume_prompt}"`, or the same with "Then ask if they need anything else." when there is no paused workflow. Never pass internal IDs to the model.

#### Before and after

The workbook asks: remove `[{article_id}]` from the strings passed to `_reply(...)`, so only the spoken summary (and any paused-workflow follow-up question) is returned.

Faulty (starter, complete tail of `_answer_policy_question`):
```python
# KATA 3 ANTI-PATTERN 5B: Leaks raw internal VaultKB article_id into agent_action.
article_id = data.get("article_id", "ALB-KB-4412-INTERNAL")
summary = str(data["spoken_summary"]).strip()
resume_prompt = _paused_workflow_prompt()
if resume_prompt:
    return _reply(f"[{article_id}] {summary} Then ask: {resume_prompt}")
return _reply(f"[{article_id}] {summary}")
```

Improved (one possible solution; yours can differ as long as it meets the workbook checklist):
```python
    summary = str(data["spoken_summary"]).strip()
    resume_prompt = _paused_workflow_prompt()
    if resume_prompt:
        return _reply(f"Tell the customer: {summary} Then ask: {resume_prompt}")
    return _reply(f"Tell the customer: {summary} Then ask if there is anything else you can help with.")
```

Why it's better: nothing internal reaches the model. The caller won't hear "ALB-KB-4412-INTERNAL", and the guidance says what to do next (resume the paused workflow, or ask if there is anything else). Dropping the unused `article_id` variable as well is tidier than just stripping it from the `_reply(...)` strings, which is the minimum the workbook asks for.

### 6A. Freeze disclosure not staged (`_set_temporary_freeze`)

**What it's trying to do:** UK rules (FCA) require specific wording to be read when a card is frozen, such as what happens to direct debits and wallets. The design is that the tool stages the exact wording as a `verbatim_script` and asks for barge-in to be locked, and a callback then reads it out unchanged.

**Starter:** It builds the disclosure and only does `return _reply(disclosure)`.

**Why it's bad:** The freeze disclosure is regulatory wording that must be read in full. Sent through the normal LLM path, the model can paraphrase or truncate it, and the caller can barge in and cut it off.

**Fix:** Stage `verbatim_script` and `verbatim_lock_barge_in` before returning. `capture_utterance` Stage 6 then delivers it word-for-word. Compare `_execute_card_cancellation` in `triage_unrecognised_transaction`.

#### Before and after

The workbook asks: in `_set_temporary_freeze()`, when `freeze_flag` is `True`, stage the disclosure in session state (`verbatim_script` and `verbatim_lock_barge_in`) before returning `_reply(disclosure)`.

Faulty (starter):
```python
if freeze_flag:
    context.state["active_card_status"] = "FROZEN"
    disclosure = FREEZE_DISCLOSURE.format(last4=last4)
    if policy_summary:
        disclosure = f"{disclosure} {policy_summary}"
    if resume_prompt:
        disclosure = f"{disclosure} {resume_prompt}"
    # KATA 3 ANTI-PATTERN 6A: Omits staging verbatim_script and verbatim_lock_barge_in,
    # leaving the regulatory freeze disclosure interruptible.
    return _reply(disclosure)
```

Improved (one possible solution; yours can differ as long as it meets the workbook checklist):
```python
def _stage_verbatim(script: str) -> None:
    """Queue regulatory wording for capture_utterance to read word-for-word, uninterrupted."""
    context.state["verbatim_script"] = script
    context.state["verbatim_lock_barge_in"] = True


# ... in _set_temporary_freeze, inside `if freeze_flag:`
        disclosure = FREEZE_DISCLOSURE.format(last4=last4)
        if policy_summary:
            disclosure = f"{disclosure} {policy_summary}"
        if resume_prompt:
            disclosure = f"{disclosure} {resume_prompt}"
        _stage_verbatim(disclosure)
        return _reply(disclosure)
```

Why it's better: the same pattern as `_execute_card_cancellation` in `triage_unrecognised_transaction`. Staging the text lets `capture_utterance` Stage 6 (anti-pattern 6B) deliver it unchanged and uninterruptible, instead of leaving the wording to the LLM. It is only done on a freeze, because the unfreeze confirmation isn't a regulatory disclosure.

### 6B. Missing Stage 6 and Stage 7 in `capture_utterance`

**What it's trying to do:** `capture_utterance` is the first callback on every agent, in numbered stages. Stage 6 reads out a staged disclosure exactly as written, and skips the extra LLM call that would otherwise rewrite it. Stage 7 is a safety net: if the model keeps calling tools in a loop, end the call politely on pass 9 rather than let the platform kill it at 10.

**Starter:** The callback ends with `return None` after Stage 5. The `_MAX_PASSES` constant and the `_deliver_verbatim_script` helper are both gone.

**Why it's bad:**
- Without Stage 6, the staged `verbatim_script` from 6A is never delivered, so 6A has no effect on its own.
- Without Stage 7, a reasoning loop (tool call → tool call → ...) runs until the platform's hard 10-pass limit kills the session ungracefully. The cap is meant to end the call cleanly first.

**Fix:**
- **Stage 6:** If `verbatim_script` is set and this is a tool-response turn, call `_deliver_verbatim_script`. It clears the flags and emits `Part.from_customized_response(content=verbatim, disable_barge_in=True)`. If `call_complete == "true"`, it also latches `"released"` and appends `end_session`.
- **Stage 7:** Define `_MAX_PASSES = 8`. If `count > _MAX_PASSES`, clear the verbatim state, set `escalation_reason="MAX_PASSES_EXCEEDED"` and `call_complete="released"`, and return an apology plus `Part.from_end_session(..., escalated=True)`. The latch has to be set manually here because a `before_model` short-circuit skips `after_model`, which is where `release_line` runs.

Every agent shares this callback, so check the other agents' copies when you fix this one.

#### Before and after

Faulty (starter, end of `before_model_callback`):
```python
    # KATA 3 ANTI-PATTERN 6B: Omits Stage 6 ... and Stage 7 (8-pass loop cap).
    return None
```

Improved (one possible solution; yours can differ as long as it meets the workbook checklist):
```python
_MAX_PASSES = 8   # module level, next to _SILENCE_MARKER

    # (end of before_model_callback)
    # === Stage 6: Read a staged disclosure exactly as written ===
    script = state.get("verbatim_script")
    if script and is_tool_response:
        return _speak_verbatim(callback_context, script)

    # === Stage 7: Reasoning-loop cap ===
    # Returning here skips after_model (release_line), so latch and end the session ourselves.
    if count > _MAX_PASSES:
        state["verbatim_script"] = ""
        state["verbatim_lock_barge_in"] = False
        state["escalation_reason"] = "MAX_PASSES_EXCEEDED"
        state["call_complete"] = "released"
        apology = "I am sorry, I am having trouble processing that request. A banking specialist will assist you shortly."
        return LlmResponse.from_parts(
            parts=[
                Part.from_text(text=apology),
                Part.from_end_session(reason="MAX_PASSES_EXCEEDED", escalated=True),
            ]
        )

    return None


def _speak_verbatim(callback_context: Any, script: str) -> Any:
    """Deliver a staged disclosure unchanged, optionally with barge-in disabled."""
    state = callback_context.state
    locked = bool(state.get("verbatim_lock_barge_in"))
    state["last_agent_action"] = script
    state["verbatim_script"] = ""
    state["verbatim_lock_barge_in"] = False
    spoken = (
        Part.from_customized_response(content=script, disable_barge_in=True)
        if locked
        else Part.from_text(text=script)
    )
    parts = [spoken]
    # Short-circuiting skips release_line, so end the call here if the tool already finished it.
    if state.get("call_complete") == "true":
        state["call_complete"] = "released"
        reason = state.get("escalation_reason") or "CALL_COMPLETED"
        parts.append(Part.from_end_session(reason=reason, escalated=reason != "CALL_COMPLETED"))
    return LlmResponse.from_parts(parts=parts)
```

Why it's better: the disclosure is spoken exactly as written, and a runaway tool loop ends the call cleanly on pass 9, before the platform's own 10-pass limit kills it.

---

## Extra pointers from the repo's skills

- `callback-api.md` (in `.agents/skills/cxas-agent-foundry/references/`) lists the allowed callback API: use `Part.from_*` factories, never `Part(text=...)`, and import `Optional` from `typing` explicitly (lint `C008`).
- It also says session state values are strings, with counters stored as `str(int(state.get("counter", "0")) + 1)`. That is the pattern in the 2C fix.
- The design guide notes that `after_model_callback` can fire on each model call within one turn, so check `callback_context.events` before injecting text twice. This is the same root issue as 3B's double-speaking.
- In multi-agent apps, trigger-handling `before_model` callbacks must exist on every agent, not just the root. This is why `capture_utterance` is shared across all agents (6B).
- `examples/cxaslint.yaml` is a template of all rule IDs (`I0xx` instructions, `C0xx` callbacks, `T0xx` tools, `A0xx` config, `S0xx` structure, `V0xx` schema). Run `uv run cxas lint --list-rules` for the full set. `V100` is not in that template.

---

## Common themes

1. **Deterministic logic belongs in tools and callbacks.** Routing, retries, locks and transfers should not live in prompts (1A, 1B, 2C).
2. **Substantive slots beat control tokens** (2A, 5A).
3. **Return a new `LlmResponse`; never mutate and return `None`** (3B, 4A).
4. **Tools return guidance (`agent_action`), not raw data or internal IDs** (2B, 5B).
5. **Encapsulate.** The model sees controller tools only, not the raw APIs (5).
6. **Callback order is behaviour** (4B).
7. **Latch and clear state flags.** `target_agent`, `call_complete` and `auth_attempts` need explicit reset or latch handling (2C, 3B, 4A, 6B).

---

## Not anti-patterns: the greenfield exercises

These are build-from-scratch tasks, not fixes. The starter has `NOT_IMPLEMENTED` stubs or missing logic, and `main` has the finished version.
- **Day 1 PM:** 3 TODOs in `tools/assist_declined_payment`.
- **Day 2 AM:** the 4-step dispatcher in `tools/triage_unrecognised_transaction`.

The starter also reorders the `_SWITCH_TARGET_AGENTS` dict and reshuffles some docstrings. This is cosmetic noise in the diff, not an intentional bug.
