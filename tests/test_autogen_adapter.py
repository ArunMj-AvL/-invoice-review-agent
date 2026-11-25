
import pytest

from app.autogen_adapter import run_dispute_review
from app.errors import AdapterBadOutput, AdapterTimeout


def test_mock_review_returns_valid_structure():
    result = run_dispute_review(
        case_facts={"invoice_id": "INV-010"},
        rule_result={
            "decision": "AUTO_CLEAR",
            "flags": [],
        },
        mock=True,
    )

    assert result["verdict"] in {"AGREE", "CHALLENGE"}
    assert isinstance(result["summary"], str)
    assert isinstance(result["concerns"], list)
    assert isinstance(result["transcript"], list)
    assert result["model_calls"] == 0


def test_mock_review_challenges_exception():
    result = run_dispute_review(
        case_facts={"invoice_id": "INV-002"},
        rule_result={
            "decision": "HOLD_FOR_REVIEW",
            "flags": ["QTY_EXCEEDS_RECEIVED"],
        },
        mock=True,
    )

    assert result["verdict"] == "CHALLENGE"
    assert "QTY_EXCEEDS_RECEIVED" in result["concerns"]


def test_forced_timeout(monkeypatch):
    monkeypatch.setenv("FORCE_ADAPTER_FAILURE", "timeout")

    with pytest.raises(AdapterTimeout):
        run_dispute_review({}, {}, mock=True)


def test_forced_bad_output(monkeypatch):
    monkeypatch.setenv("FORCE_ADAPTER_FAILURE", "bad_json")

    with pytest.raises(AdapterBadOutput):
        run_dispute_review({}, {}, mock=True)
