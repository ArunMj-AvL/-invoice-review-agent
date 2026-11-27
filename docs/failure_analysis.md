# Failure analysis: retries, timeouts and escalation

Reproduce the failure run with:

```powershell
python main.py --demo failure
```

It forces a CrewAI timeout (`FORCE_ADAPTER_FAILURE=timeout`) on INV-004 and prints the trace.

## What happens when a model-backed step fails

| Failure | Where it is caught | Behaviour |
|---|---|---|
| Model call hangs | `app/reliability.py::call_with_timeout` wraps every adapter call (`GRAPH_ADAPTER_TIMEOUT_SECONDS`, default 120 s); adapters also have their own `LLM_TIMEOUT_SECONDS` | `AdapterTimeout` is raised, the graph stops waiting |
| Malformed or invalid JSON from an agent | Adapter raises `AdapterBadOutput`; the graph also validates the shape of the result | Same retry path |
| Adapter missing / import error / any other exception | `InvoiceWorkflow._run_specialist` catches `Exception` | Same retry path |
| Explanation does not match the rules (wrong decision, reasons or evidence) | `explain_findings` compares it with `rule_result` | Discarded; a plain-Python explanation is used and `explanation_source = "deterministic_fallback"` |
| Rules engine raises, or returns an invalid result | `three_way_match` | **Fails closed**: `NEEDS_INFORMATION` with reason `RULES_ENGINE_ERROR`. It is never auto-cleared |
| Invalid request (`invoice_id` null, empty, not text) | `run_case` and `load_invoice` | `INVALID_REQUEST`, no graph run, no audit entry |

## Retry and escalation policy

1. Each specialist step (CrewAI, AutoGen) is attempted up to `GRAPH_MAX_ATTEMPTS` times (default 3) with a linear back-off (`GRAPH_RETRY_BACKOFF_SECONDS`, default 0.5 s x attempt number). The retries are a conditional edge that loops the node back to itself, so every attempt is a checkpointed step.
2. After the last attempt the status becomes `ESCALATED`. The workflow **continues**, because the specialists only advise: the decision comes from the rules either way.
3. The escalation is visible to the human reviewer (the approval payload carries `crew_status` / `autogen_verdict`), in the trace, and in the audit entry (`crew_status`, `autogen_status`).
4. Cases that need approval still stop at `human_approval`. Nothing is cleared because an agent failed.

## Side effects and idempotency

* The only side effect is one line in `audit_log.jsonl`, written by `AuditLog.write_once`.
* Each entry has an `idempotency_key`: `REV-<invoice>` for approvals, `CASE-<invoice>:recommendation` for auto-clears and needs-information cases.
* A replayed webhook, a duplicate resume or a racing approve and reject all hit the same key, so one entry is written and the first writer wins. A lock file makes this safe across processes, and it works on Windows.
* `run_case` checks the checkpoint first. A request for a case that is paused or completed returns the stored result (`"replayed": true`) and does not start a second run.

## Stale approvals

The reviewer's evidence is fingerprinted (`facts_hash`) when the graph pauses. `audit_write` recomputes the facts from the current data after the human answers. If anything changed, the status is `STALE_APPROVAL_REJECTED` and no approval is recorded.

## Known limits

* A timed-out worker thread cannot be killed. The graph stops waiting, but the thread may finish in the background.
* Re-processing an invoice after its records legitimately change needs a new case. This version intentionally treats the same invoice ID as the same case.
* The audit log is a local file. It is not a tamper-proof ledger.
