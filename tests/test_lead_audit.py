"""Audit log + approval parsing tests. No LangGraph and no LLM needed."""

import threading

from app.approval import AuditLog, compute_facts_hash, parse_approval


def _entry(key="REV-INV-001", status="APPROVED_SIMULATION"):
    return {"idempotency_key": key, "final_status": status, "actor": "tester", "action_executed": False}


def test_same_key_is_written_once(tmp_path):
    log = AuditLog(tmp_path / "audit_log.jsonl")
    first, created_first = log.write_once(_entry())
    second, created_second = log.write_once(_entry())
    assert created_first is True and created_second is False
    assert first == second
    assert len(log.read_all()) == 1


def test_racing_approve_and_reject_write_exactly_one_entry(tmp_path):
    log = AuditLog(tmp_path / "audit_log.jsonl")
    results = []

    def writer(status):
        results.append(log.write_once(_entry(status=status)))

    threads = [threading.Thread(target=writer, args=(s,)) for s in ["APPROVED_SIMULATION", "REJECTED"] * 4]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(log.read_all()) == 1
    assert sum(1 for _, created in results if created) == 1
    assert len({entry["final_status"] for entry, _ in results}) == 1  # everyone sees the winner


def test_different_keys_are_both_written(tmp_path):
    log = AuditLog(tmp_path / "audit_log.jsonl")
    log.write_once(_entry("REV-INV-001"))
    log.write_once(_entry("REV-INV-002"))
    assert len(log.read_all()) == 2


def test_valid_approval_payload():
    parsed = parse_approval({"action": "Approve", "actor": " alice ", "comment": "ok"})
    assert parsed == {"action": "approve", "actor": "alice", "comment": "ok", "valid": True}


def test_invalid_approval_payloads_fail_safe_to_reject():
    for bad in (None, "approve", {}, {"action": "approve"}, {"actor": "a"}, {"action": "maybe", "actor": "a"}):
        parsed = parse_approval(bad)
        assert parsed["action"] == "reject" and parsed["valid"] is False


def test_facts_hash_changes_when_a_record_changes():
    facts = {"invoice": {"billed_qty": "10"}}
    result = {"decision": "HOLD_FOR_REVIEW"}
    changed = {"invoice": {"billed_qty": "11"}}
    assert compute_facts_hash(facts, result) == compute_facts_hash(dict(facts), dict(result))
    assert compute_facts_hash(facts, result) != compute_facts_hash(changed, result)
