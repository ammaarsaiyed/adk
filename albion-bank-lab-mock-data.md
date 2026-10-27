# Albion Bank Lab: Mock Data and Where It Lives

There is no database. All "customers", cards and transactions are hard-coded Python dictionaries. The same data exists in two places, and which copy answers depends on how the app is running.

Paths below are relative to `lbg-albion-bank-gecx-lab/`.

## Where the data is stored

| Copy | Location | Used when |
|---|---|---|
| **Tool fakes** (inside the app bundle) | `cxas_app/Albion_Bank_Card_Protection/toolsets/<toolset>/tool_fake_config/code_block/python_code.py` | The toolset has `"enableFakeMode": true` in its `<toolset>.json`. This is the case for `albion_core_api`. It's also what the local pytest harness (`scripts/_cxas_sandbox.py`) uses. |
| **Middleware service** (a small FastAPI app on Cloud Run) | `middleware_service/core_and_fraud_handlers.py` (customers, cards, passcodes, policy articles) and `middleware_service/domain_waterfall.py` (declined-payment transactions and the diagnosis logic) | Fake mode is off. `cxas_app/.../environment.json` points every toolset at `https://albion-bank-middleware-335184044429.europe-west1.run.app`. |

The two copies are meant to be identical. The payment fake is generated: `scripts/sync_tool_fakes_from_middleware.py` copies `domain_waterfall.py` verbatim into the `payment_support_api` fake (about 1,900 lines), and `--check` verifies they match. The core, fraud and vault fakes are small hand-kept copies.

The toolset to data mapping:

| Toolset (what the controllers call) | Mock data holds | Fake file | Middleware source |
|---|---|---|---|
| `albion_core_api` | customers, cards, passcodes, card status, freeze, travel notices | `toolsets/albion_core_api/.../python_code.py` | `core_and_fraud_handlers.py` |
| `payment_support_api` | declined transactions and the diagnosis and resolution waterfall | `toolsets/payment_support_api/.../python_code.py` (generated) | `domain_waterfall.py` |
| `albion_fraud_api` | two fixed "suspicious" transactions, a dispute reference | `toolsets/albion_fraud_api/.../python_code.py` | `core_and_fraud_handlers.py` |
| `vault_kb_api` | policy articles (direct debits, wallets, replacement delivery) | `toolsets/vault_kb_api/.../python_code.py` | `core_and_fraud_handlers.py` (`POLICY_ARTICLES`) |

The "today" date for all the payment logic is fixed at **22 October 2026** (`DEFAULT_AS_OF_DATE` in `domain_waterfall.py`), so results don't drift.

## Test customers (`albion_core_api`)

These are the only cards the **login step** knows about. `authenticate_caller` calls `lookupCustomerByCard` (typed or spoken 4 digits) or `lookupCustomerByCli` (caller's phone number).

| Card ending | Customer | ID | Card status | Phone number (CLI) | Passcodes accepted |
|---|---|---|---|---|---|
| **4821** | Sarah Jenkins | CUST-1001 | ACTIVE | +447700900111 | `112233`, `195501` |
| 9901 | Sarah Jenkins | CUST-1001 | ACTIVE | +447700900123 | `195501`, `112233` |
| 7390 | David Chen | CUST-1002 | ACTIVE | +447700900222 | `223344`, `195501` |
| 5512 | Priya Patel | CUST-1003 | ACTIVE | +447700900333 | `334455`, `195501` |
| 9014 | James Morrison | CUST-1004 | ACTIVE | none, deliberately unlisted | `445566`, `195501` |
| 6108 | Elena Rostova | CUST-1005 | **FROZEN** | +447700900555 | `556677`, `195501` |
| 8204 | Emma Watson | CUST-1006 | ACTIVE | +447700900666 | `667788`, `195501` |

- **`195501` is a lab-only master passcode** that works for every customer.
- In chat there's no phone number, so the app has no CLI and asks for the 4 digits. With a phone number (voice), it looks the customer up and asks "is your card ending …?" instead. James Morrison has no number on purpose, to test that fallback.
- A card ending that is not in this table gives `matched: false`, and `authenticate_caller` then escalates with `CARD_NOT_FOUND`.
- Every lookup response also carries fake internal CRM fields (`internal_colleague_note`, `internal_fraud_risk_score`, `secondary_account_iban`, `cmas_block_code`, and so on). They are there as bait: the controllers must strip them and never speak them.

## Declined-payment data (`payment_support_api`)

The declined-payment journey diagnoses one transaction through four steps: wallet restriction, wallet token, card status (CMS), and a blocked recurring payment (MBS). The transactions are in `_TRANSACTIONS_BY_ID` in `domain_waterfall.py`.

| Transaction | Card | Merchant | Amount | What it exercises |
|---|---|---|---|---|
| tx_982 | 4821 | StreamFlix | £45.00 | Recurring-payment block (CPA) since 10 March 2026, error code 68 |
| tx_741 | 4821 | Nordic Air | £128.50 | Recurring-payment block since 15 April 2026 |
| tx_501 | 7390 | Apple Pay | £0.00 | Wallet token SUSPENDED (branch 2C) |
| tx_502 | 7391 | Apple Pay | £0.00 | Token BLOCKED_UNRECOGNISED_DEVICE (2A) |
| tx_503 | 7392 | Apple Pay | £0.00 | Token PENDING (2B) |
| tx_504 | 7393 | Google Pay | £0.00 | Token DELETED (2D) |
| tx_301 | 5512 | Tesco Express | £18.40 | Card CANCELLED (lost), replacement issued 8 business days ago |
| tx_401 | 9014 | Sainsbury's | £32.10 | Card CANCELLED (lost), replacement issued 3 business days ago |
| tx_608 | 6108 | Pret A Manger | £8.95 | Frozen card |
| tx_601 | 8204 | Google Pay | £0.00 | Wallet decline |
| tx_3001 to tx_3006, tx_4001, tx_4002 | 3001 to 3006, 4001, 4002 | Argos, Boots, Currys, Waterstones, John Lewis, Waitrose, City Parking | various | Edge cases for the waterfall tests |

Mapping of customers to transactions: `_CUSTOMER_TX_IDS` and `_CARD_TX_IDS`.

**Important:** the payment data knows more cards (7391 to 7393, 3001 to 3006, 4001, 4002, `0000`) than the core API does. Those cards are for direct tests of the middleware. You can't log in with them in the chat, because `albion_core_api` doesn't know them. For a full end-to-end declined-payment test, use **4821** (StreamFlix, Nordic Air), **7390**, **5512**, **9014**, **6108** or **8204**.

## Fraud triage (`albion_fraud_api`)

`scanTransactions` always returns the same two items, whoever the customer is: `TX-301` ElectroMart London £489.99 and `TX-302` Metro Ride £64.00. It sleeps for 5.2 seconds to simulate a slow backend, unless the session starts with `evaluation` or `fast_test_mode` is set. The cancel and dispute calls always return dispute reference `84920137`.

## Policy articles (`vault_kb_api`)

Topics: `freeze_direct_debits`, `apple_google_pay`, `apple_pay`, `google_pay`, `mobile_wallets`, `replacement_delivery`. Each has a `spoken_summary` and an internal `article_id` such as `ALB-KB-4412-INTERNAL`. An unknown topic falls back to `freeze_direct_debits`. The `article_id` is the one leaked by anti-pattern 5B.

## Your failed run: "asked for 4 digits, I said 4821, it wasn't accepted and the chat ended"

4821 is a valid card (Sarah Jenkins), so the data itself is fine. I couldn't see your session, so I can't say which of these caused it, but here's what ends a call in that step. All of them go through `_escalate(...)` in `authenticate_caller`, which sets `call_complete = "true"` and the hang-up callback then ends the chat:

| `escalation_reason` | Cause |
|---|---|
| `AUTH_SYSTEM_ERROR` | The tool crashed. **I reproduced this offline:** calling `authenticate_caller(card_last4=4821)` with a *number* instead of the string `"4821"` raises `AttributeError: 'int' object has no attribute 'upper'` in `_lookup_card_last4`, and the tool's catch-all turns that into this escalation. With `"4821"` it works and asks for the passcode. In the app the `normalise_passcode` after-model callback (on `authentication_agent`) is supposed to turn numbers into strings first, so if the model emitted a number and the callback didn't fire, you'd get exactly your symptom. |
| `AUTH_BACKEND_ERROR` | The lookup call failed or returned an error. This is likely if the app is running against the real Cloud Run URL (fake mode off) and the service is down, rejects the call, or the toolset ID-token auth fails. |
| `CARD_NOT_FOUND` | The backend answered `matched: false`. Only possible if the deployed middleware has different data from the files above. |
| `CARD_NUMBER_UNKNOWN` | The model sent the literal `"UNKNOWN"` as the card ending. |

To find out which one it was, look at the session state or trace for `escalation_reason` after the call ends. It names the cause directly.

Things worth checking next:
1. In the Agent Studio trace, look at the `authenticate_caller` call. Is `card_last4` a string (`"4821"`) or a number (`4821`)? Did `normalise_passcode` run before it?
2. Is the toolset in fake mode in your sandbox app, or is it calling the Cloud Run URL? If Cloud Run, try `curl <url>/docs` or whatever health check the service has.
3. Remember the starter has seeded bugs (2A, 2C and others in `authenticate_caller`). They don't explain a hard stop on a first 4-digit answer, but they can add confusing behaviour afterwards. For example, with no `auth_attempts` counter (2C) the passcode step never locks out.
