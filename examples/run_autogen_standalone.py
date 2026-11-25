
"""Standalone demo for the AutoGen invoice review adapter."""

from app.autogen_adapter import run_dispute_review


def main():
    case_facts = {
        "invoice_id": "INV-002",
        "vendor": "ABC Supplies",
        "invoice_amount": "25000",
        "invoice_quantity": 100,
    }

    rule_result = {
        "decision": "HOLD_FOR_REVIEW",
        "flags": ["QTY_EXCEEDS_RECEIVED"],
        "reason": "Invoice quantity exceeds the received quantity.",
    }

    result = run_dispute_review(
        case_facts=case_facts,
        rule_result=rule_result,
        correlation_id="demo-001",
        mock=True,
    )

    print("\n=== AutoGen Invoice Review Demo ===")
    print("Verdict:", result["verdict"])
    print("Summary:", result["summary"])
    print("Concerns:", result["concerns"])
    print("\nTranscript:")
    for message in result["transcript"]:
        print("-", message)
    print("Model calls:", result["model_calls"])


if __name__ == "__main__":
    main()
