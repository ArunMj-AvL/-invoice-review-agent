"""Run the CrewAI adapter directly without LangGraph or an API key."""

from __future__ import annotations

import json
import logging

from app.crewai_adapter import run_crew


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    case_facts = {
        "case_id": "CASE-INV-004",
        "invoice": {
            "invoice_id": "INV-004",
            "po_id": "PO-104",
            "vendor_id": "V-01",
            "billed_qty": "50",
            "billed_unit_price_inr": "900",
            "invoice_date": "2026-11-18",
        },
        "po": {
            "po_id": "PO-104",
            "vendor_id": "V-01",
            "unit_price_inr": "850",
        },
        "receipt": {"receipt_id": "GR-04", "po_id": "PO-104", "received_qty": "50"},
        "vendor": {"vendor_id": "V-01", "risk_tier": "low", "active": "yes"},
        "invoice_total_inr": "45000",
        "duplicate_of": [],
        "flags": ["UNIT_PRICE_MISMATCH"],
        "evidence": ["INV-004", "PO-104", "GR-04", "V-01", "P3"],
        "policy_clauses": ["P3"],
    }
    rule_result = {
        "invoice_id": "INV-004",
        "decision": "HOLD_FOR_REVIEW",
        "reasons": ["UNIT_PRICE_MISMATCH"],
        "evidence": ["INV-004", "PO-104", "GR-04", "V-01", "P3"],
        "policy_clauses": ["P3"],
        "approval_required": True,
        "action_executed": False,
    }

    result = run_crew(
        case_facts=case_facts,
        rule_result=rule_result,
        correlation_id="demo-CASE-INV-004",
        mock=True,
    )
    logging.getLogger(__name__).info(
        "Standalone CrewAI adapter output:\n%s",
        json.dumps(result, ensure_ascii=False, indent=2),
    )


if __name__ == "__main__":
    main()

