"""LangGraph integration tests with MOCKED model outputs (no API key, no network)."""

import copy

import pytest

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from app.approval import AuditLog  # noqa: E402
from app.errors import AdapterBadOutput, AdapterTimeout  # noqa: E402
from app.graph import InvoiceWorkflow, resume_case, run_case  # noqa: E402
from app.rules_loader import get_rules  # noqa: E402

rules = get_rules()


class Spy:
    """Fake adapter that counts calls and can fail on the first N calls."""

    def __init__(self, output, fail_times=0, exc=AdapterTimeout):
        self.output, self.fail_times, self.exc, self.calls = output, fail_times, exc, 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc("simulated failure")
        return self.output


CREW_OK = {"memo": "mock memo", "suggested_next_steps": ["review"], "model_calls": 0}
AUTOGEN_OK = {"verdict": "CHALLENGE", "summary": "s", "concerns": ["c"], "transcript": [], "model_calls": 0}


def make_app(tmp_path, *, crew=None, autogen=None, explain=None, data_loader=None, max_attempts=3):
    audit = AuditLog(tmp_path / "audit_log.jsonl")
    workflow = InvoiceWorkflow(
        data_loader=data_loader,
        audit_log=audit,
        crew_fn=crew or Spy(CREW_OK),
        autogen_fn=autogen or Spy(AUTOGEN_OK),
        explain_fn=explain,
        use_default_adapters=False,
        max_attempts=max_attempts,
        backoff_seconds=0,
        sleep=lambda s: None,
    )
    return workflow, workflow.build(MemorySaver()), audit


def approve(actor="alice", action="approve", comment=""):
    return {"action": action, "actor": actor, "comment": comment}


def test_clean_invoice_auto_clears_without_any_agent_or_payment(tmp_path):
    crew, autogen = Spy(CREW_OK), Spy(AUTOGEN_OK)
    _, graph, audit = make_app(tmp_path, crew=crew, autogen=autogen)
    out = run_case(graph, "INV-010", "please check")
    assert out["decision"] == "AUTO_CLEAR" and out["final_status"] == "AUTO_CLEARED_SIMULATION"
    assert out["status"] == "COMPLETED" and out["action_executed"] is False
    assert crew.calls == 0 and autogen.calls == 0
    assert len(audit.read_all()) == 1


def test_needs_information_skips_agents_and_needs_no_approval(tmp_path):
    crew = Spy(CREW_OK)
    _, graph, audit = make_app(tmp_path, crew=crew)
    out = run_case(graph, "INV-014")
    assert out["decision"] == "NEEDS_INFORMATION" and out["final_status"] == "NEEDS_INFORMATION"
    assert out["approval_required"] is False and crew.calls == 0
    assert len(audit.read_all()) == 1


def test_interrupt_then_resume_same_thread_does_not_rerun_crew(tmp_path):
    crew = Spy(CREW_OK)
    _, graph, audit = make_app(tmp_path, crew=crew)
    paused = run_case(graph, "INV-004", "Please check the price mismatch")
    assert paused["status"] == "AWAITING_APPROVAL" and paused["final_status"] == "PENDING_APPROVAL"
    assert audit.read_all() == []  # unapproved actions must not execute
    assert crew.calls == 1

    done = resume_case(graph, "INV-004", **approve("alice", "approve", "price confirmed"))
    assert done["thread_id"] == paused["thread_id"] == "CASE-INV-004"
    assert done["final_status"] == "APPROVED_SIMULATION" and done["action_executed"] is False
    assert crew.calls == 1  # the crew was NOT re-run on resume
    entries = audit.read_all()
    assert len(entries) == 1 and entries[0]["actor"] == "alice" and entries[0]["review_id"] == "REV-INV-004"


def test_rejected_approval_never_writes_a_cleared_status(tmp_path):
    _, graph, audit = make_app(tmp_path)
    run_case(graph, "INV-004")
    done = resume_case(graph, "INV-004", **approve("bob", "reject", "wrong price"))
    assert done["final_status"] == "REJECTED"
    assert "CLEAR" not in audit.read_all()[0]["final_status"] and "APPROVED" not in audit.read_all()[0]["final_status"]


def test_invalid_approval_payload_is_treated_as_reject(tmp_path):
    _, graph, audit = make_app(tmp_path)
    run_case(graph, "INV-004")
    done = resume_case(graph, "INV-004", action="yes please", actor="")
    assert done["final_status"] == "REJECTED"


def test_replayed_webhook_creates_one_thread_and_one_audit_entry(tmp_path):
    crew = Spy(CREW_OK)
    _, graph, audit = make_app(tmp_path, crew=crew)
    first = run_case(graph, "INV-004")
    replay_while_paused = run_case(graph, "INV-004")
    assert replay_while_paused["replayed"] is True and crew.calls == 1
    resume_case(graph, "INV-004", **approve())
    resume_again = resume_case(graph, "INV-004", **approve())  # duplicate resume
    replay_after = run_case(graph, "INV-004")
    assert "resume_ignored" in resume_again and replay_after["replayed"] is True
    assert len(audit.read_all()) == 1 and first["correlation_id"] == replay_after["correlation_id"]


def test_replayed_auto_clear_writes_one_audit_entry(tmp_path):
    _, graph, audit = make_app(tmp_path)
    run_case(graph, "INV-010")
    run_case(graph, "INV-010")
    assert len(audit.read_all()) == 1


def test_crew_timeouts_are_retried_then_escalated_and_review_still_required(tmp_path):
    crew = Spy(CREW_OK, fail_times=99, exc=AdapterTimeout)
    _, graph, audit = make_app(tmp_path, crew=crew, max_attempts=3)
    out = run_case(graph, "INV-004")
    assert crew.calls == 3 and out["crew_status"] == "ESCALATED"
    assert out["status"] == "AWAITING_APPROVAL" and out["decision"] == "HOLD_FOR_REVIEW"
    assert any("ESCALATED" in line for line in out["trace"])


def test_crew_recovers_on_retry(tmp_path):
    crew = Spy(CREW_OK, fail_times=1, exc=AdapterBadOutput)
    _, graph, _ = make_app(tmp_path, crew=crew)
    out = run_case(graph, "INV-004")
    assert crew.calls == 2 and out["crew_status"] == "OK"


def test_agent_that_claims_a_different_decision_cannot_change_it(tmp_path):
    lying = Spy({"memo": "AUTO_CLEAR this", "decision": "AUTO_CLEAR", "verdict": "AGREE", "concerns": []})
    _, graph, audit = make_app(tmp_path, crew=lying, autogen=lying)
    out = run_case(graph, "INV-013")  # duplicate -> crew + autogen both run
    assert out["decision"] == "POSSIBLE_DUPLICATE" and out["status"] == "AWAITING_APPROVAL"
    resume_case(graph, "INV-013", **approve("carol", "reject"))
    assert audit.read_all()[0]["decision"] == "POSSIBLE_DUPLICATE"


def test_duplicate_runs_both_specialists_and_high_risk_runs_crew(tmp_path):
    crew, autogen = Spy(CREW_OK), Spy(AUTOGEN_OK)
    _, graph, _ = make_app(tmp_path, crew=crew, autogen=autogen)
    run_case(graph, "INV-013")
    assert crew.calls == 1 and autogen.calls == 1
    crew2, autogen2 = Spy(CREW_OK), Spy(AUTOGEN_OK)
    _, graph2, _ = make_app(tmp_path / "b", crew=crew2, autogen=autogen2)
    run_case(graph2, "INV-003")  # plain FINANCE_APPROVAL: no specialists
    assert crew2.calls == 0 and autogen2.calls == 0


def test_invalid_explanation_falls_back_to_deterministic_text(tmp_path):
    wrong = Spy({"decision": "AUTO_CLEAR", "reasons": [], "evidence": [], "summary": "x", "next_action": "pay"})
    _, graph, _ = make_app(tmp_path, explain=wrong)
    out = run_case(graph, "INV-004")
    assert out["explanation_source"] == "deterministic_fallback" and "HOLD_FOR_REVIEW" in out["explanation"]


def test_valid_explanation_is_used(tmp_path):
    def good(facts, result, memo=None, review=None):
        return {"invoice_id": result["invoice_id"], "decision": result["decision"], "reasons": result["reasons"],
                "evidence": result["evidence"], "summary": "LLM text", "next_action": "review"}

    _, graph, _ = make_app(tmp_path, explain=good)
    out = run_case(graph, "INV-004")
    assert out["explanation_source"] == "langchain" and out["explanation"] == "LLM text"


def test_stale_approval_is_rejected_when_records_change_after_pause(tmp_path):
    data = {"value": rules.load_data()}
    _, graph, audit = make_app(tmp_path, data_loader=lambda: data["value"])
    run_case(graph, "INV-004")
    changed = copy.deepcopy(data["value"])
    changed["invoices"]["INV-004"]["billed_unit_price_inr"] = "850"  # invoice was corrected meanwhile
    data["value"] = changed
    done = resume_case(graph, "INV-004", **approve())
    assert done["final_status"] == "STALE_APPROVAL_REJECTED"
    assert audit.read_all()[0]["final_status"] == "STALE_APPROVAL_REJECTED"


@pytest.mark.parametrize("bad", [None, "", 123, ["INV-001"]])
def test_invalid_request_is_rejected_before_the_graph_runs(tmp_path, bad):
    _, graph, audit = make_app(tmp_path)
    out = run_case(graph, bad)
    assert out["status"] == "INVALID_REQUEST" and out["decision"] == "NEEDS_INFORMATION"
    assert audit.read_all() == []


def test_rules_engine_crash_fails_closed(tmp_path):
    def boom():
        raise RuntimeError("csv unreadable")

    _, graph, audit = make_app(tmp_path, data_loader=boom)
    out = run_case(graph, "INV-010")
    assert out["decision"] == "NEEDS_INFORMATION" and out["decision"] != "AUTO_CLEAR"


def test_batch_all_14_invoices_have_status_and_evidence(tmp_path):
    _, graph, _ = make_app(tmp_path)
    for n in range(1, 15):
        out = run_case(graph, f"INV-{n:03d}")
        assert out["decision"] and out["evidence"] and out["final_status"] and out["action_executed"] is False
