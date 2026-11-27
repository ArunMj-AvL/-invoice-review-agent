"""LangGraph workflow: the single owner of end-to-end state, routing, retries and approval.

START -> load_invoice -> three_way_match -> route_by_exception
      -> direct_clear | crew_reconciliation (retry loop) | (skip)
      -> autogen_dispute_review (retry loop, only if needed)
      -> deterministic_decision -> explain_findings
      -> human_approval (interrupt, only if needed) -> audit_write -> END

Golden rule: ONLY the deterministic rules decide. CrewAI, AutoGen and LangChain output is
stored as advice for the human reviewer and can never change ``final_decision``.
"""

import logging
import os
import sqlite3
import time
import uuid
from typing import Any, Callable, Dict, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.approval import (
    APPROVE,
    SYSTEM_ACTOR,
    AuditLog,
    build_audit_entry,
    case_id_for,
    compute_facts_hash,
    parse_approval,
    review_id_for,
)
from app.reliability import call_with_timeout
from app.rules_loader import get_rules
from app.state import (
    AUTO_CLEAR,
    DECISIONS,
    FINAL_APPROVED,
    FINAL_AUTO_CLEARED,
    FINAL_INVALID,
    FINAL_NEEDS_INFO,
    FINAL_PENDING,
    FINAL_REJECTED,
    FINAL_STALE,
    HOLD_FOR_REVIEW,
    NEEDS_INFORMATION,
    POSSIBLE_DUPLICATE,
    SPEC_ESCALATED,
    SPEC_NOT_REQUIRED,
    SPEC_OK,
    SPEC_RETRYING,
    STATUS_AWAITING_APPROVAL,
    STATUS_COMPLETED,
    STATUS_INVALID,
    STATUS_RUNNING,
    CaseState,
)

logger = logging.getLogger("invoice_graph")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _load_default_adapters() -> Dict[str, Optional[Callable[..., Any]]]:
    """Import the specialist adapters. Each import is isolated so one missing file
    cannot take the whole workflow down."""
    adapters: Dict[str, Optional[Callable[..., Any]]] = {}
    for key, module, name in (
        ("crew_fn", "app.crewai_adapter", "run_crew"),
        ("autogen_fn", "app.autogen_adapter", "run_dispute_review"),
        ("explain_fn", "app.langchain_components", "build_explanation"),
    ):
        try:
            adapters[key] = getattr(__import__(module, fromlist=[name]), name)
        except (ImportError, AttributeError) as exc:
            logger.warning("adapter %s.%s unavailable (%s); degrading gracefully", module, name, exc)
            adapters[key] = None
    return adapters


def deterministic_explanation(rule_result: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    """Plain-Python explanation used when the LangChain chain is unavailable or invalid."""
    decision = rule_result.get("decision")
    reasons = rule_result.get("reasons", [])
    total = facts.get("invoice_total_inr")
    next_actions = {
        "AUTO_CLEAR": "Record the simulated clearance in the audit log. No payment is made.",
        "HOLD_FOR_REVIEW": "Hold the invoice and send the evidence to a human reviewer.",
        "NEEDS_INFORMATION": "Request the missing or corrected source records, then re-run the checks.",
        "POSSIBLE_DUPLICATE": "Have a human compare it with the earlier matching invoice (not a fraud finding).",
        "FINANCE_APPROVAL": "Route to Finance for approval. No payment is made by this system.",
    }
    summary = f"{rule_result.get('invoice_id')} -> {decision}."
    if reasons:
        summary += " Findings: " + ", ".join(reasons) + "."
    if total is not None:
        summary += f" Invoice total INR {total}."
    return {
        "invoice_id": rule_result.get("invoice_id"),
        "decision": decision,
        "summary": summary,
        "reasons": list(reasons),
        "evidence": list(rule_result.get("evidence", [])),
        "next_action": next_actions.get(decision, "Escalate to a human reviewer."),
    }


class InvoiceWorkflow:
    """Graph nodes and routers. Dependencies are injectable so tests can use fakes."""

    def __init__(
        self,
        *,
        rules_module: Any = None,
        data_loader: Optional[Callable[[], Dict[str, Any]]] = None,
        audit_log: Optional[AuditLog] = None,
        crew_fn: Optional[Callable[..., Any]] = None,
        autogen_fn: Optional[Callable[..., Any]] = None,
        explain_fn: Optional[Callable[..., Any]] = None,
        use_default_adapters: bool = True,
        max_attempts: Optional[int] = None,
        backoff_seconds: Optional[float] = None,
        timeout_seconds: Optional[float] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rules = rules_module or get_rules()
        self.data_loader = data_loader or self.rules.load_data
        self.audit = audit_log or AuditLog()
        defaults = _load_default_adapters() if use_default_adapters else {}
        self.crew_fn = crew_fn or defaults.get("crew_fn")
        self.autogen_fn = autogen_fn or defaults.get("autogen_fn")
        self.explain_fn = explain_fn or defaults.get("explain_fn")
        self.max_attempts = max_attempts or _env_int("GRAPH_MAX_ATTEMPTS", 3)
        self.backoff = backoff_seconds if backoff_seconds is not None else _env_float("GRAPH_RETRY_BACKOFF_SECONDS", 0.5)
        self.timeout = timeout_seconds or _env_float("GRAPH_ADAPTER_TIMEOUT_SECONDS", 120.0)
        self._sleep = sleep

    # ------------------------------------------------------------------ helpers
    def _log(self, state: CaseState, node: str, message: str) -> str:
        logger.info("[%s] case=%s correlation_id=%s %s", node, state.get("case_id"), state.get("correlation_id"), message)
        return f"[{node}] {message}"

    # -------------------------------------------------------------------- nodes
    def load_invoice(self, state: CaseState) -> Dict[str, Any]:
        """Validate the request, assign the case/thread ID and a correlation ID."""
        invoice_id = state.get("invoice_id")
        correlation_id = state.get("correlation_id") or uuid.uuid4().hex[:12]
        base = {"correlation_id": correlation_id}
        if not isinstance(invoice_id, str) or not invoice_id:
            line = self._log({**state, **base}, "load_invoice", "rejected: invoice_id must be a non-empty string")
            return {**base, "case_id": "CASE-INVALID", "status": STATUS_INVALID, "final_status": FINAL_INVALID, "trace": [line]}
        comment = state.get("comment")
        update = {
            **base,
            "case_id": case_id_for(invoice_id),
            "comment": comment if isinstance(comment, str) else "",
            "status": STATUS_RUNNING,
            "crew_status": SPEC_NOT_REQUIRED,
            "crew_attempts": 0,
            "autogen_status": SPEC_NOT_REQUIRED,
            "autogen_attempts": 0,
        }
        update["trace"] = [self._log({**state, **update}, "load_invoice", f"accepted invoice_id={invoice_id}")]
        return update

    def three_way_match(self, state: CaseState) -> Dict[str, Any]:
        """Deterministic PO / goods receipt / vendor match (all money logic lives in rules)."""
        invoice_id = state["invoice_id"]
        try:
            out = self.rules.evaluate_invoice(invoice_id, self.data_loader())
            facts, result = out["case_facts"], out["rule_result"]
            if result.get("decision") not in DECISIONS or not isinstance(result.get("reasons"), list):
                raise ValueError(f"invalid rule_result: {result!r}")
        except Exception as exc:  # fail closed: never clear anything we could not evaluate
            logger.exception("rules engine failed for %s", invoice_id)
            facts = {
                "case_id": state["case_id"], "invoice": None, "po": None, "receipt": None, "vendor": None,
                "invoice_total_inr": None, "duplicate_of": [], "flags": ["RULES_ENGINE_ERROR"],
                "evidence": [invoice_id, "P8"], "policy_clauses": ["P8"],
            }
            result = {
                "invoice_id": invoice_id, "decision": NEEDS_INFORMATION, "reasons": ["RULES_ENGINE_ERROR"],
                "evidence": [invoice_id, "P8"], "policy_clauses": ["P8"], "approval_required": False,
                "action_executed": False,
            }
            self._log(state, "three_way_match", f"rules engine error: {exc!r}")
        line = self._log(state, "three_way_match", f"decision={result['decision']} reasons={result['reasons']}")
        return {
            "case_facts": facts,
            "rule_result": result,
            "facts_hash": compute_facts_hash(facts, result),
            "review_id": review_id_for(invoice_id),
            "trace": [line],
        }

    def direct_clear(self, state: CaseState) -> Dict[str, Any]:
        return {"trace": [self._log(state, "direct_clear", "clean invoice; no specialist review needed")]}

    def _run_specialist(self, state: CaseState, *, node: str, prefix: str, fn: Optional[Callable[..., Any]], required_key: str) -> Dict[str, Any]:
        """Shared bounded-retry logic for the CrewAI and AutoGen nodes."""
        attempts = state.get(f"{prefix}_attempts", 0) + 1
        error: Optional[str] = None
        try:
            if fn is None:
                raise RuntimeError(f"{node} adapter is not available")
            output = call_with_timeout(
                fn, state["case_facts"], state["rule_result"],
                correlation_id=state["correlation_id"], timeout=self.timeout,
            )
            if not isinstance(output, dict) or not isinstance(output.get(required_key), (str, list)):
                raise ValueError(f"{node} returned malformed output")
            line = self._log(state, node, f"attempt {attempts}/{self.max_attempts}: OK")
            return {
                f"{prefix}_attempts": attempts, f"{prefix}_status": SPEC_OK, f"{prefix}_error": None,
                ("crew_memo" if prefix == "crew" else "autogen_review"): output, "trace": [line],
            }
        except Exception as exc:  # timeout, bad JSON, missing adapter, anything
            error = f"{type(exc).__name__}: {exc}"
        if attempts < self.max_attempts:
            line = self._log(state, node, f"attempt {attempts}/{self.max_attempts} failed ({error}); retrying")
            self._sleep(self.backoff * attempts)
            return {f"{prefix}_attempts": attempts, f"{prefix}_status": SPEC_RETRYING, f"{prefix}_error": error, "trace": [line]}
        line = self._log(state, node, f"attempt {attempts}/{self.max_attempts} failed ({error}); ESCALATED to human reviewer")
        return {f"{prefix}_attempts": attempts, f"{prefix}_status": SPEC_ESCALATED, f"{prefix}_error": error, "trace": [line]}

    def crew_reconciliation(self, state: CaseState) -> Dict[str, Any]:
        return self._run_specialist(state, node="crew_reconciliation", prefix="crew", fn=self.crew_fn, required_key="memo")

    def autogen_dispute_review(self, state: CaseState) -> Dict[str, Any]:
        return self._run_specialist(state, node="autogen_dispute_review", prefix="autogen", fn=self.autogen_fn, required_key="concerns")

    def deterministic_decision(self, state: CaseState) -> Dict[str, Any]:
        """The decision is copied from the rules. Agent output is never consulted."""
        result = state["rule_result"]
        notes = []
        review = state.get("autogen_review") or {}
        if review.get("verdict") == "CHALLENGE":
            notes.append("AutoGen reviewer raised concerns (advisory only)")
        if state.get("crew_status") == SPEC_ESCALATED or state.get("autogen_status") == SPEC_ESCALATED:
            notes.append("a specialist step failed and was escalated")
        extra = f"; note: {'; '.join(notes)}" if notes else ""
        line = self._log(state, "deterministic_decision", f"final_decision={result['decision']} (from rules){extra}")
        return {"final_decision": result["decision"], "approval_required": bool(result.get("approval_required")), "trace": [line]}

    def explain_findings(self, state: CaseState) -> Dict[str, Any]:
        """LangChain explanation, validated against the rules. Falls back to plain Python."""
        result, facts = state["rule_result"], state["case_facts"]
        source = "langchain"
        try:
            if self.explain_fn is None:
                raise RuntimeError("explanation chain is not available")
            memo = (state.get("crew_memo") or {}).get("memo")
            explanation = call_with_timeout(
                self.explain_fn, facts, result, memo=memo, review=state.get("autogen_review"), timeout=self.timeout,
            )
            if hasattr(explanation, "model_dump"):
                explanation = explanation.model_dump()
            if (
                not isinstance(explanation, dict)
                or explanation.get("decision") != result["decision"]
                or list(explanation.get("reasons", [])) != list(result["reasons"])
                or list(explanation.get("evidence", [])) != list(result["evidence"])
            ):
                raise ValueError("explanation does not match the deterministic result")
        except Exception as exc:
            source = "deterministic_fallback"
            explanation = deterministic_explanation(result, facts)
            self._log(state, "explain_findings", f"using fallback explanation ({type(exc).__name__}: {exc})")
        line = self._log(state, "explain_findings", f"explanation_source={source}")
        return {"explanation": explanation, "explanation_source": source, "trace": [line]}

    def human_approval(self, state: CaseState) -> Dict[str, Any]:
        """Pause the graph. Nothing before the interrupt() call has side effects, so
        re-running this node on resume is safe. Earlier nodes (CrewAI, AutoGen) are NOT re-run."""
        result = state["rule_result"]
        payload = {
            "review_id": state["review_id"],
            "case_id": state["case_id"],
            "invoice_id": state["invoice_id"],
            "decision": result["decision"],
            "reasons": result["reasons"],
            "evidence": result["evidence"],
            "policy_clauses": result["policy_clauses"],
            "invoice_total_inr": state["case_facts"].get("invoice_total_inr"),
            "summary": (state.get("explanation") or {}).get("summary"),
            "crew_status": state.get("crew_status"),
            "autogen_verdict": (state.get("autogen_review") or {}).get("verdict"),
            "requester_comment": state.get("comment", ""),
            "facts_hash": state["facts_hash"],
            "instructions": "Resume with {'action': 'approve'|'reject', 'actor': '<name>', 'comment': '...'}",
        }
        answer = interrupt(payload)
        approval = parse_approval(answer)
        line = self._log(state, "human_approval", f"received action={approval['action']} actor={approval['actor']} valid={approval['valid']}")
        return {"approval": approval, "trace": [line]}

    def audit_write(self, state: CaseState) -> Dict[str, Any]:
        """The only side effect in the system: one local, idempotent audit record."""
        decision = state["final_decision"]
        if state.get("approval_required"):
            approval = state.get("approval") or parse_approval(None)
            fresh_hash = self._fresh_hash(state)
            if fresh_hash != state["facts_hash"]:
                status, actor = FINAL_STALE, approval["actor"]  # records changed after the review began
            elif approval["action"] == APPROVE:
                status, actor = FINAL_APPROVED, approval["actor"]
            else:
                status, actor = FINAL_REJECTED, approval["actor"]
            key, comment = state["review_id"], approval.get("comment", "")
        else:
            status = FINAL_AUTO_CLEARED if decision == AUTO_CLEAR else FINAL_NEEDS_INFO
            actor, key, comment = SYSTEM_ACTOR, f"{state['case_id']}:recommendation", ""
        entry, created = self.audit.write_once(
            build_audit_entry(idempotency_key=key, state=state, final_status=status, actor=actor, approval_comment=comment)
        )
        status = entry["final_status"]  # if a racing writer won, report what is really on disk
        line = self._log(state, "audit_write", f"final_status={status} audit_created={created}")
        return {"final_status": status, "audit_entry": entry, "audit_created": created, "status": STATUS_COMPLETED, "trace": [line]}

    def _fresh_hash(self, state: CaseState) -> str:
        out = self.rules.evaluate_invoice(state["invoice_id"], self.data_loader())
        return compute_facts_hash(out["case_facts"], out["rule_result"])

    # ------------------------------------------------------------------ routers
    def route_after_load(self, state: CaseState) -> str:
        return "invalid" if state.get("status") == STATUS_INVALID else "match"

    def route_by_exception(self, state: CaseState) -> str:
        result = state["rule_result"]
        if result["decision"] == AUTO_CLEAR:
            return "clear"
        if result["decision"] in (HOLD_FOR_REVIEW, POSSIBLE_DUPLICATE) or "HIGH_RISK_VENDOR" in result["reasons"]:
            return "crew"
        return "decide"  # NEEDS_INFORMATION and plain FINANCE_APPROVAL need no specialists

    def _needs_autogen(self, state: CaseState) -> bool:
        result = state["rule_result"]
        return result["decision"] == POSSIBLE_DUPLICATE or len(result["reasons"]) >= 2

    def route_after_crew(self, state: CaseState) -> str:
        if state.get("crew_status") == SPEC_RETRYING:
            return "retry"
        return "autogen" if self._needs_autogen(state) else "decide"

    def route_after_autogen(self, state: CaseState) -> str:
        return "retry" if state.get("autogen_status") == SPEC_RETRYING else "decide"

    def route_approval(self, state: CaseState) -> str:
        return "approve" if state.get("approval_required") else "audit"

    # -------------------------------------------------------------------- build
    def build(self, checkpointer: Any = None):
        graph = StateGraph(CaseState)
        for name in (
            "load_invoice", "three_way_match", "direct_clear", "crew_reconciliation",
            "autogen_dispute_review", "deterministic_decision", "explain_findings",
            "human_approval", "audit_write",
        ):
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, "load_invoice")
        graph.add_conditional_edges("load_invoice", self.route_after_load, {"match": "three_way_match", "invalid": END})
        graph.add_conditional_edges(
            "three_way_match", self.route_by_exception,
            {"clear": "direct_clear", "crew": "crew_reconciliation", "decide": "deterministic_decision"},
        )
        graph.add_edge("direct_clear", "deterministic_decision")
        graph.add_conditional_edges(  # retry loop #1
            "crew_reconciliation", self.route_after_crew,
            {"retry": "crew_reconciliation", "autogen": "autogen_dispute_review", "decide": "deterministic_decision"},
        )
        graph.add_conditional_edges(  # retry loop #2
            "autogen_dispute_review", self.route_after_autogen,
            {"retry": "autogen_dispute_review", "decide": "deterministic_decision"},
        )
        graph.add_edge("deterministic_decision", "explain_findings")
        graph.add_conditional_edges("explain_findings", self.route_approval, {"approve": "human_approval", "audit": "audit_write"})
        graph.add_edge("human_approval", "audit_write")
        graph.add_edge("audit_write", END)
        return graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Checkpointing and the public run / resume API
# ---------------------------------------------------------------------------
def make_checkpointer(path: Optional[str] = None):
    """SQLite checkpoints survive restarts, so a paused case can be resumed from another
    process. Falls back to in-memory (with a warning) if the sqlite package is missing."""
    path = path or os.getenv("CHECKPOINT_DB", "checkpoints.sqlite")
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        return SqliteSaver(sqlite3.connect(path, check_same_thread=False))
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver

        logger.warning("langgraph-checkpoint-sqlite not installed; checkpoints are in-memory only")
        return MemorySaver()


def create_app(checkpointer: Any = None, **workflow_kwargs: Any):
    """Return (workflow, compiled_graph)."""
    workflow = InvoiceWorkflow(**workflow_kwargs)
    return workflow, workflow.build(checkpointer or make_checkpointer())


def _config(case_id: str) -> Dict[str, Any]:
    return {"configurable": {"thread_id": case_id}, "recursion_limit": 60}


def _pending_interrupt(snapshot: Any) -> Optional[Dict[str, Any]]:
    for task in getattr(snapshot, "tasks", ()) or ():
        for item in getattr(task, "interrupts", ()) or ():
            return getattr(item, "value", None)
    return None


def summarize(snapshot: Any) -> Dict[str, Any]:
    """Output contract: machine-readable findings plus a short business explanation."""
    values = snapshot.values or {}
    result = values.get("rule_result") or {}
    pending = bool(snapshot.next)
    explanation = values.get("explanation") or {}
    return {
        "invoice_id": values.get("invoice_id"),
        "case_id": values.get("case_id"),
        "thread_id": values.get("case_id"),
        "correlation_id": values.get("correlation_id"),
        "decision": result.get("decision"),
        "reasons": result.get("reasons", []),
        "evidence": result.get("evidence", []),
        "policy_clauses": result.get("policy_clauses", []),
        "approval_required": result.get("approval_required", False),
        "action_executed": False,
        "status": STATUS_AWAITING_APPROVAL if pending else values.get("status"),
        "final_status": FINAL_PENDING if pending else values.get("final_status"),
        "review_id": values.get("review_id"),
        "explanation": explanation.get("summary"),
        "next_action": explanation.get("next_action"),
        "explanation_source": values.get("explanation_source"),
        "crew_status": values.get("crew_status"),
        "autogen_status": values.get("autogen_status"),
        "audit_created": values.get("audit_created"),
        "trace": values.get("trace", []),
    }


def invalid_request(reason: str) -> Dict[str, Any]:
    return {
        "invoice_id": None, "decision": NEEDS_INFORMATION, "reasons": ["INVALID_INPUT"], "evidence": ["P8"],
        "approval_required": False, "action_executed": False, "status": STATUS_INVALID,
        "final_status": FINAL_INVALID, "explanation": reason,
    }


def run_case(graph: Any, invoice_id: Any, comment: Any = "", correlation_id: Optional[str] = None) -> Dict[str, Any]:
    """Run (or replay) one invoice. Safe to call twice: a replay never starts a second run."""
    if not isinstance(invoice_id, str) or not invoice_id:
        return invalid_request("invoice_id must be a non-empty string")
    if comment is not None and not isinstance(comment, str):
        return invalid_request("comment must be text")
    config = _config(case_id_for(invoice_id))
    existing = graph.get_state(config)
    if existing is not None and existing.values:
        if existing.next or existing.values.get("status") in (STATUS_COMPLETED, STATUS_INVALID):
            logger.info("replay of %s ignored: case already %s", invoice_id, "paused" if existing.next else "completed")
            return {**summarize(existing), "replayed": True}
    payload: Dict[str, Any] = {"invoice_id": invoice_id, "comment": comment or ""}
    if correlation_id:
        payload["correlation_id"] = correlation_id
    graph.invoke(payload, config)
    return summarize(graph.get_state(config))


def resume_case(graph: Any, invoice_id: str, action: str, actor: str, comment: str = "") -> Dict[str, Any]:
    """Resume the SAME thread with a human decision. Safe to call twice."""
    config = _config(case_id_for(invoice_id))
    snapshot = graph.get_state(config)
    if snapshot is None or not snapshot.values:
        return {"invoice_id": invoice_id, "status": "NOT_FOUND", "final_status": None, "action_executed": False}
    if not snapshot.next:
        return {**summarize(snapshot), "resume_ignored": "case is not waiting for approval"}
    graph.invoke(Command(resume={"action": action, "actor": actor, "comment": comment}), config)
    return summarize(graph.get_state(config))
