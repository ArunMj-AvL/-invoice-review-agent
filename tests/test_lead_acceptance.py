"""All 14 dataset cases + rule edge cases, run against whichever rules module is active
(Niranjan's app/rules.py if it exists, otherwise the stand-in). No LangGraph, no LLM."""

import copy

import pytest

from app.rules_loader import get_rules

rules = get_rules()

EXPECTED = {
    "INV-001": "FINANCE_APPROVAL",  # INR 475,000 > 100,000 (P6)
    "INV-002": "HOLD_FOR_REVIEW",  # billed 20, received 18 (P2)
    "INV-003": "FINANCE_APPROVAL",  # INR 142,500 (P6)
    "INV-004": "HOLD_FOR_REVIEW",  # price 900 vs PO 850 (P3)
    "INV-005": "AUTO_CLEAR",  # INR 64,800, low-risk vendor
    "INV-006": "FINANCE_APPROVAL",  # INR 178,500
    "INV-007": "FINANCE_APPROVAL",  # INR 124,800
    "INV-008": "HOLD_FOR_REVIEW",  # billed 8, received 7 (P2); also high-risk vendor (P7)
    "INV-009": "HOLD_FOR_REVIEW",  # V-99 is not PO vendor V-03 (P4)
    "INV-010": "AUTO_CLEAR",  # INR 14,000
    "INV-011": "HOLD_FOR_REVIEW",  # vendor V-07 inactive (P4)
    "INV-012": "AUTO_CLEAR",  # INR 45,000
    "INV-013": "POSSIBLE_DUPLICATE",  # same PO/vendor/qty/price as INV-001 (P5)
    "INV-014": "NEEDS_INFORMATION",  # PO-999 does not exist (P1)
}


@pytest.fixture()
def data():
    return rules.load_data()


@pytest.mark.parametrize("invoice_id,expected", sorted(EXPECTED.items()))
def test_dataset_decisions(data, invoice_id, expected):
    out = rules.evaluate_invoice(invoice_id, data)
    result = out["rule_result"]
    assert result["decision"] == expected
    assert result["invoice_id"] == invoice_id
    assert result["action_executed"] is False
    assert invoice_id in result["evidence"]  # every case has evidence
    assert result["policy_clauses"]  # and policy citations


def test_acceptance_totals_and_duplicate_target(data):
    assert rules.evaluate_invoice("INV-010", data)["case_facts"]["invoice_total_inr"] == "14000"
    dup = rules.evaluate_invoice("INV-013", data)["case_facts"]
    assert dup["duplicate_of"] == ["INV-001"]
    assert rules.evaluate_invoice("INV-001", data)["rule_result"]["decision"] == "FINANCE_APPROVAL"  # original is not flagged


def test_missing_po_never_hallucinates_a_po(data):
    facts = rules.evaluate_invoice("INV-014", data)["case_facts"]
    assert facts["po"] is None and facts["receipt"] is None


def test_po_without_goods_receipt_is_needs_information(data):
    data = copy.deepcopy(data)
    del data["receipts_by_po"]["PO-110"]
    assert rules.evaluate_invoice("INV-010", data)["rule_result"]["decision"] == "NEEDS_INFORMATION"


@pytest.mark.parametrize("bad_qty", ["0", "-5", "", None, "abc", "NaN"])
def test_invalid_quantity_is_needs_information(data, bad_qty):
    data = copy.deepcopy(data)
    data["invoices"]["INV-010"]["billed_qty"] = bad_qty
    assert rules.evaluate_invoice("INV-010", data)["rule_result"]["decision"] == "NEEDS_INFORMATION"


def test_high_risk_vendor_on_low_value_invoice_needs_finance(data):
    data = copy.deepcopy(data)
    inv = data["invoices"]["INV-008"]
    inv["billed_qty"] = "7"  # within received qty, ~INR 54,600, but V-06 is high risk (P7)
    assert rules.evaluate_invoice("INV-008", data)["rule_result"]["decision"] == "FINANCE_APPROVAL"


def test_price_difference_of_one_rupee_is_still_a_hold(data):
    data = copy.deepcopy(data)
    data["invoices"]["INV-010"]["billed_unit_price_inr"] = "141"  # PO says 140: no tolerance
    assert rules.evaluate_invoice("INV-010", data)["rule_result"]["decision"] == "HOLD_FOR_REVIEW"


def test_distinct_ids_with_matching_values_are_flagged_not_fraud(data):
    data = copy.deepcopy(data)
    clone = dict(data["invoices"]["INV-010"], invoice_id="INV-777", invoice_date="2026-12-01")
    data["invoices"]["INV-777"] = clone
    out = rules.evaluate_invoice("INV-777", data)["rule_result"]
    assert out["decision"] == "POSSIBLE_DUPLICATE" and out["approval_required"] is True


def test_unknown_invoice_is_needs_information(data):
    assert rules.evaluate_invoice("INV-NOPE", data)["rule_result"]["decision"] == "NEEDS_INFORMATION"
