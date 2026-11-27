"""Typed workflow state and shared constants for the LangGraph invoice review graph.

Owned by the team lead. Only the values listed here may be written by graph nodes.
"""

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

# --- The five decisions (exact spelling, shared by every module) -------------
AUTO_CLEAR = "AUTO_CLEAR"
HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"
NEEDS_INFORMATION = "NEEDS_INFORMATION"
POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
FINANCE_APPROVAL = "FINANCE_APPROVAL"

DECISIONS = (
    AUTO_CLEAR,
    HOLD_FOR_REVIEW,
    NEEDS_INFORMATION,
    POSSIBLE_DUPLICATE,
    FINANCE_APPROVAL,
)

# Decisions that need a human before anything is recorded as approved.
APPROVAL_DECISIONS = (HOLD_FOR_REVIEW, POSSIBLE_DUPLICATE, FINANCE_APPROVAL)

# --- Workflow status ----------------------------------------------------------
STATUS_RUNNING = "RUNNING"
STATUS_AWAITING_APPROVAL = "AWAITING_APPROVAL"
STATUS_COMPLETED = "COMPLETED"
STATUS_INVALID = "INVALID_REQUEST"

# --- Specialist (CrewAI / AutoGen) status ------------------------------------
SPEC_NOT_REQUIRED = "NOT_REQUIRED"
SPEC_OK = "OK"
SPEC_RETRYING = "RETRYING"
SPEC_ESCALATED = "ESCALATED"  # retries exhausted -> human reviewer is told

# --- Final audit statuses (nothing here ever means "money moved") ------------
FINAL_AUTO_CLEARED = "AUTO_CLEARED_SIMULATION"
FINAL_APPROVED = "APPROVED_SIMULATION"
FINAL_REJECTED = "REJECTED"
FINAL_STALE = "STALE_APPROVAL_REJECTED"
FINAL_NEEDS_INFO = "NEEDS_INFORMATION"
FINAL_PENDING = "PENDING_APPROVAL"
FINAL_INVALID = "INVALID_REQUEST"


class CaseState(TypedDict, total=False):
    """State carried through the graph and checkpointed after every node."""

    # Input (contract: invoice_id + optional comment)
    invoice_id: str
    comment: str

    # Identity: the case ID doubles as the LangGraph thread ID
    case_id: str
    correlation_id: str

    # Deterministic results (authoritative)
    case_facts: Dict[str, Any]
    rule_result: Dict[str, Any]
    facts_hash: str

    # Specialists (advisory only)
    crew_memo: Optional[Dict[str, Any]]
    crew_attempts: int
    crew_status: str
    crew_error: Optional[str]
    autogen_review: Optional[Dict[str, Any]]
    autogen_attempts: int
    autogen_status: str
    autogen_error: Optional[str]

    # LangChain explanation
    explanation: Dict[str, Any]
    explanation_source: str

    # Decision, approval and audit
    final_decision: str
    approval_required: bool
    review_id: str
    approval: Dict[str, Any]
    final_status: str
    audit_entry: Dict[str, Any]
    audit_created: bool
    status: str

    # Execution trace (appended by every node)
    trace: Annotated[List[str], operator.add]
