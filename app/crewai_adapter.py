"""Standalone CrewAI invoice-exception investigation adapter.

This module accepts facts and a deterministic rule result from the caller. It
does not load datasets, import LangGraph, or decide an invoice outcome.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.errors import AdapterBadOutput, AdapterError, AdapterTimeout

logger = logging.getLogger(__name__)


def _json_default(value: Any) -> str:
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    return str(value)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default, sort_keys=True)


def _forced_failure() -> None:
    mode = os.getenv("FORCE_ADAPTER_FAILURE", "").strip().lower()
    if mode == "timeout":
        raise AdapterTimeout("Forced CrewAI adapter timeout")
    if mode == "bad_json":
        raise AdapterBadOutput("Forced invalid CrewAI output")


def _suggested_steps(rule_result: dict[str, Any]) -> list[str]:
    """Create safe operational steps from the authoritative result."""
    decision = rule_result.get("decision")
    invoice_id = rule_result.get("invoice_id", "this invoice")
    if decision == "NEEDS_INFORMATION":
        return [
            f"Request the missing or corrected source record for {invoice_id}.",
            "Rerun deterministic checks after the source data is updated.",
        ]
    if decision == "AUTO_CLEAR":
        return [
            "Record the recommendation in the local audit log.",
            "Do not initiate a bank transfer from this workflow.",
        ]
    if decision == "POSSIBLE_DUPLICATE":
        return [
            "Have a human compare this invoice with the earlier matching invoice.",
            "Keep the duplicate flag as a review signal, not a fraud conclusion.",
        ]
    if decision in {"HOLD_FOR_REVIEW", "FINANCE_APPROVAL"}:
        return [
            "Route the case and its evidence to the designated human reviewer.",
            "Record the reviewer outcome locally; do not initiate payment.",
        ]
    raise AdapterBadOutput(f"Unsupported rule decision: {decision!r}")


def _mock_memo(case_facts: dict[str, Any], rule_result: dict[str, Any]) -> str:
    invoice = case_facts.get("invoice") or {}
    invoice_id = rule_result.get("invoice_id", invoice.get("invoice_id", "unknown"))
    decision = rule_result.get("decision", "unknown")
    reasons = rule_result.get("reasons", [])
    evidence = rule_result.get("evidence", [])
    clauses = rule_result.get("policy_clauses", [])
    reason_summary = ", ".join(str(reason) for reason in reasons) or "no exception flags"
    evidence_summary = ", ".join(str(item) for item in evidence) or "no evidence identifiers supplied"
    clause_summary = ", ".join(str(item) for item in clauses) or "no policy clauses supplied"
    return (
        f"Investigation memo for {invoice_id}: deterministic review result is "
        f"{decision}. The rule findings are {reason_summary}. Evidence supplied "
        f"by the caller: {evidence_summary}. Applicable policy clauses supplied: "
        f"{clause_summary}. This memo is advisory and does not change the decision."
    )


def run_crew(
    case_facts: dict[str, Any],
    rule_result: dict[str, Any],
    correlation_id: str = "",
    mock: bool | None = None,
) -> dict[str, Any]:
    """Run the invoice analyst and vendor risk analyst and return an advisory memo.

    ``model_calls`` is zero in mock mode and counts the two specialist task
    completions in live mode. The agents receive only the caller's case facts
    and deterministic rule result; neither can alter the final decision.
    """
    _forced_failure()
    if not isinstance(case_facts, dict) or not isinstance(rule_result, dict):
        raise AdapterError("case_facts and rule_result must be dictionaries")
    if "decision" not in rule_result or "invoice_id" not in rule_result:
        raise AdapterBadOutput("rule_result must contain invoice_id and decision")

    use_mock = mock if mock is not None else os.getenv("USE_MOCK_LLM", "0") == "1"
    if use_mock:
        return {
            "memo": _mock_memo(case_facts, rule_result),
            "suggested_next_steps": _suggested_steps(rule_result),
            "model_calls": 0,
        }

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise AdapterError("OPENAI_API_KEY is required unless mock=True or USE_MOCK_LLM=1")

    try:
        from crewai import Agent, Crew, LLM, Process, Task

        timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
        model_name = os.getenv("MODEL_NAME", "gpt-4o-mini")
        llm = LLM(
            model=f"openai/{model_name}",
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,
        )

        analyst = Agent(
            role="Invoice Analyst",
            goal="Summarize invoice, PO, and receipt facts using only supplied records.",
            backstory=(
                "You are a careful accounts-payable analyst. You cite supplied "
                "identifiers, distinguish missing facts from negative findings, "
                "and never make or change a policy decision."
            ),
            llm=llm,
            allow_delegation=False,
            verbose=False,
            max_iter=1,
            max_execution_time=timeout_seconds,
        )
        risk_analyst = Agent(
            role="Vendor Risk Analyst",
            goal="Explain only vendor facts and risk signals present in the case input.",
            backstory=(
                "You are a skeptical vendor-risk reviewer. You do not invent vendor "
                "history, treat a duplicate as fraud, or override deterministic rules."
            ),
            llm=llm,
            allow_delegation=False,
            verbose=False,
            max_iter=1,
            max_execution_time=timeout_seconds,
        )

        case_json = _json(case_facts)
        result_json = _json(rule_result)
        correlation = correlation_id or "not supplied"
        invoice_task = Task(
            description=(
                "Review the supplied invoice and matching records. Use no outside "
                "facts and do not recalculate or change the decision. Report key "
                "facts, missing evidence, and applicable policy identifiers.\n"
                f"Correlation ID: {correlation}\n"
                f"case_facts JSON: {case_json}\nrule_result JSON: {result_json}"
            ),
            expected_output="A concise, evidence-grounded invoice reconciliation finding.",
            agent=analyst,
        )
        risk_task = Task(
            description=(
                "Using the invoice analyst's finding and only the supplied data, "
                "write a concise investigation memo for the human reviewer. State "
                "that the deterministic result is authoritative. Do not invent "
                "records, assert fraud, approve payment, or change the decision."
            ),
            expected_output="A concise advisory investigation memo grounded in supplied evidence.",
            agent=risk_analyst,
            context=[invoice_task],
        )
        crew = Crew(
            agents=[analyst, risk_analyst],
            tasks=[invoice_task, risk_task],
            process=Process.sequential,
            verbose=False,
        )
        result = crew.kickoff()
        memo = getattr(result, "raw", None)
        if not isinstance(memo, str) or not memo.strip():
            raise AdapterBadOutput("CrewAI returned an empty investigation memo")
        return {
            "memo": memo.strip(),
            "suggested_next_steps": _suggested_steps(rule_result),
            "model_calls": 2,
        }
    except (AdapterBadOutput, AdapterTimeout, AdapterError):
        raise
    except Exception as exc:
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        if "timeout" in name or "timed out" in message or "timeout" in message:
            raise AdapterTimeout("CrewAI investigation timed out") from exc
        logger.exception("CrewAI investigation failed; correlation_id=%s", correlation_id)
        raise AdapterError(f"CrewAI investigation failed: {exc}") from exc

