
"""Simple benchmark for the AutoGen invoice review adapter."""

import time

from app.autogen_adapter import run_dispute_review


cases = [
    ("Clean invoice", "AUTO_CLEAR", []),
    ("Quantity mismatch", "HOLD_FOR_REVIEW", ["QTY_EXCEEDS_RECEIVED"]),
    ("Price mismatch", "HOLD_FOR_REVIEW", ["UNIT_PRICE_MISMATCH"]),
    ("High-risk vendor", "FINANCE_APPROVAL", ["HIGH_RISK_VENDOR"]),
    ("Duplicate invoice", "POSSIBLE_DUPLICATE", ["POSSIBLE_DUPLICATE"]),
]

print("\n=== AutoGen Benchmark ===")
print("-" * 65)

total_time = 0

for name, decision, flags in cases:
    case_facts = {"invoice_id": name.replace(" ", "_")}

    rule_result = {
        "decision": decision,
        "flags": flags,
    }

    start = time.perf_counter()

    result = run_dispute_review(
        case_facts=case_facts,
        rule_result=rule_result,
        mock=True,
    )

    elapsed_ms = (time.perf_counter() - start) * 1000
    total_time += elapsed_ms

    print(f"{name:25} | {elapsed_ms:8.3f} ms | {result['model_calls']} calls")

print("-" * 65)
print(f"Total time: {total_time:.3f} ms")
print(f"Average time: {total_time / len(cases):.3f} ms")
print("Mode: MOCK (0 actual LLM calls)")
