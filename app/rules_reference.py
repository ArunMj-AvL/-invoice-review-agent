"""Reference implementation of the deterministic rules (policy P1-P9).

STAND-IN: this follows the team's format contract exactly so the LangGraph workflow can be
built and tested before Niranjan's ``app/rules.py`` is merged. ``app/rules_loader.py`` uses
``app/rules.py`` automatically as soon as it exists. No LLM, Decimal money, exact ID match.
"""

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional

THRESHOLD = Decimal("100000")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

NEEDS_INFO_FLAGS = ("MISSING_PO", "MISSING_RECEIPT", "INVALID_INPUT")
HOLD_FLAGS = (
    "QTY_EXCEEDS_RECEIVED",
    "UNIT_PRICE_MISMATCH",
    "VENDOR_MISMATCH",
    "MISSING_VENDOR",
    "VENDOR_INACTIVE",
)
CLAUSE_FOR_FLAG = {
    "MISSING_PO": "P1",
    "MISSING_RECEIPT": "P1",
    "INVALID_INPUT": "P8",
    "QTY_EXCEEDS_RECEIVED": "P2",
    "CUMULATIVE_OVERBILL": "P2",
    "UNIT_PRICE_MISMATCH": "P3",
    "VENDOR_MISMATCH": "P4",
    "MISSING_VENDOR": "P4",
    "VENDOR_INACTIVE": "P4",
    "POSSIBLE_DUPLICATE": "P5",
    "OVER_THRESHOLD": "P6",
    "HIGH_RISK_VENDOR": "P7",
}


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle) if any(row.values())]


def load_data(data_dir: Optional[Path] = None) -> Dict[str, Dict[str, Dict[str, str]]]:
    base = Path(data_dir) if data_dir else DATA_DIR
    receipts: Dict[str, Dict[str, str]] = {}
    for row in _read_csv(base / "goods_receipts.csv"):
        receipts.setdefault(row["po_id"], row)
    return {
        "invoices": {r["invoice_id"]: r for r in _read_csv(base / "invoices.csv")},
        "purchase_orders": {r["po_id"]: r for r in _read_csv(base / "purchase_orders.csv")},
        "receipts_by_po": receipts,
        "vendors": {r["vendor_id"]: r for r in _read_csv(base / "vendors.csv")},
    }


def _dec(value: Any) -> Optional[Decimal]:
    """Parse a positive number. None / blank / text / zero / negative -> None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number <= 0:
        return None
    return number


def _order_key(invoice: Dict[str, Any]):
    return (str(invoice.get("invoice_date", "")), str(invoice.get("invoice_id", "")))


def _empty_result(invoice_id: str, flags: List[str], evidence: List[str], clauses: List[str]):
    facts = {
        "case_id": f"CASE-{invoice_id}",
        "invoice": None,
        "po": None,
        "receipt": None,
        "vendor": None,
        "invoice_total_inr": None,
        "duplicate_of": [],
        "flags": flags,
        "evidence": evidence,
        "policy_clauses": clauses,
    }
    result = {
        "invoice_id": invoice_id,
        "decision": "NEEDS_INFORMATION",
        "reasons": flags,
        "evidence": evidence,
        "policy_clauses": clauses,
        "approval_required": False,
        "action_executed": False,
    }
    return {"case_facts": facts, "rule_result": result}


def evaluate_invoice(invoice_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    invoice = data["invoices"].get(invoice_id)
    if invoice is None:
        return _empty_result(invoice_id, ["INVALID_INPUT"], [str(invoice_id), "P8"], ["P8"])

    flags: List[str] = []
    po = data["purchase_orders"].get(invoice.get("po_id"))
    receipt = data["receipts_by_po"].get(invoice.get("po_id")) if po else None
    vendor = data["vendors"].get(invoice.get("vendor_id"))

    billed_qty = _dec(invoice.get("billed_qty"))
    billed_price = _dec(invoice.get("billed_unit_price_inr"))
    if billed_qty is None or billed_price is None or not invoice.get("vendor_id"):
        flags.append("INVALID_INPUT")

    if po is None:
        flags.append("MISSING_PO")
    elif receipt is None:
        flags.append("MISSING_RECEIPT")

    received_qty = _dec(receipt.get("received_qty")) if receipt else None
    po_price = _dec(po.get("unit_price_inr")) if po else None
    if (receipt and received_qty is None) or (po and po_price is None):
        flags.append("INVALID_INPUT")

    total = billed_qty * billed_price if billed_qty and billed_price else None

    # P2 quantity
    if billed_qty is not None and received_qty is not None and billed_qty > received_qty:
        flags.append("QTY_EXCEEDS_RECEIVED")
    # P3 price: any difference at all, no tolerance
    if billed_price is not None and po_price is not None and billed_price != po_price:
        flags.append("UNIT_PRICE_MISMATCH")
    # P4 vendor
    if po is not None:
        if invoice.get("vendor_id") != po.get("vendor_id"):
            flags.append("VENDOR_MISMATCH")
        if vendor is None:
            flags.append("MISSING_VENDOR")
        elif str(vendor.get("active", "")).strip().lower() != "yes":
            flags.append("VENDOR_INACTIVE")

    # P5 duplicate: same PO + vendor + qty + price, earlier invoice, different ID
    duplicate_of: List[str] = []
    if billed_qty is not None and billed_price is not None:
        for other in data["invoices"].values():
            if other.get("invoice_id") == invoice_id:
                continue
            if (
                other.get("po_id") == invoice.get("po_id")
                and other.get("vendor_id") == invoice.get("vendor_id")
                and _dec(other.get("billed_qty")) == billed_qty
                and _dec(other.get("billed_unit_price_inr")) == billed_price
                and _order_key(other) < _order_key(invoice)
            ):
                duplicate_of.append(other["invoice_id"])
    duplicate_of.sort()
    if duplicate_of:
        flags.append("POSSIBLE_DUPLICATE")
        # Informational: cumulative billing for this PO now exceeds what was received.
        if "QTY_EXCEEDS_RECEIVED" not in flags and received_qty is not None:
            cumulative = Decimal(0)
            for other in data["invoices"].values():
                if other.get("po_id") == invoice.get("po_id") and _order_key(other) <= _order_key(invoice):
                    cumulative += _dec(other.get("billed_qty")) or Decimal(0)
            if cumulative > received_qty:
                flags.append("CUMULATIVE_OVERBILL")

    # P6 / P7
    if total is not None and total > THRESHOLD:
        flags.append("OVER_THRESHOLD")
    if vendor is not None and str(vendor.get("risk_tier", "")).strip().lower() == "high":
        flags.append("HIGH_RISK_VENDOR")

    flags = list(dict.fromkeys(flags))  # de-duplicate, keep order

    if any(flag in flags for flag in NEEDS_INFO_FLAGS):
        decision = "NEEDS_INFORMATION"
    elif any(flag in flags for flag in HOLD_FLAGS):
        decision = "HOLD_FOR_REVIEW"
    elif "POSSIBLE_DUPLICATE" in flags:
        decision = "POSSIBLE_DUPLICATE"
    elif "OVER_THRESHOLD" in flags or "HIGH_RISK_VENDOR" in flags:
        decision = "FINANCE_APPROVAL"
    else:
        decision = "AUTO_CLEAR"

    if decision == "AUTO_CLEAR":
        clauses = ["P1", "P2", "P3", "P4", "P5", "P6"]
    else:
        clauses = list(dict.fromkeys(CLAUSE_FOR_FLAG[f] for f in flags if f in CLAUSE_FOR_FLAG))

    evidence = [invoice_id]
    if po:
        evidence.append(po["po_id"])
    if receipt:
        evidence.append(receipt["receipt_id"])
    if vendor:
        evidence.append(vendor["vendor_id"])
    evidence.extend(duplicate_of)
    evidence.extend(clauses)
    evidence = list(dict.fromkeys(evidence))

    facts = {
        "case_id": f"CASE-{invoice_id}",
        "invoice": invoice,
        "po": po,
        "receipt": receipt,
        "vendor": vendor,
        "invoice_total_inr": f"{total:f}" if total is not None else None,
        "duplicate_of": duplicate_of,
        "flags": flags,
        "evidence": evidence,
        "policy_clauses": clauses,
    }
    result = {
        "invoice_id": invoice_id,
        "decision": decision,
        "reasons": flags,
        "evidence": evidence,
        "policy_clauses": clauses,
        "approval_required": decision in ("HOLD_FOR_REVIEW", "POSSIBLE_DUPLICATE", "FINANCE_APPROVAL"),
        "action_executed": False,
    }
    return {"case_facts": facts, "rule_result": result}
