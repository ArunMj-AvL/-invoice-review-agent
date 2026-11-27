"""Approval handling and the local, idempotent audit log.

Nothing in this module (or anywhere in the project) moves money. "Approval" only
writes a record to ``audit_log.jsonl``.
"""

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

APPROVE = "approve"
REJECT = "reject"
SYSTEM_ACTOR = "system:rules-engine"


def case_id_for(invoice_id: str) -> str:
    """Stable case ID. It is also the LangGraph thread ID."""
    return f"CASE-{invoice_id}"


def review_id_for(invoice_id: str) -> str:
    """One review per invoice: approve and reject race for the SAME review ID."""
    return f"REV-{invoice_id}"


def compute_facts_hash(case_facts: Dict[str, Any], rule_result: Dict[str, Any]) -> str:
    """Fingerprint of the records a human reviewed. A change means the approval is stale."""
    blob = json.dumps(
        {"case_facts": case_facts, "rule_result": rule_result}, sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _invalid(reason: str, actor: str = "unknown") -> Dict[str, Any]:
    # Fail safe: anything we cannot understand is a REJECT, never an approval.
    return {
        "action": REJECT,
        "actor": actor,
        "comment": f"invalid approval payload: {reason}",
        "valid": False,
    }


def parse_approval(raw: Any) -> Dict[str, Any]:
    """Validate the value a human sends when resuming the graph."""
    if not isinstance(raw, dict):
        return _invalid("payload must be an object")
    actor = raw.get("actor")
    action = str(raw.get("action", "")).strip().lower()
    if not isinstance(actor, str) or not actor.strip():
        return _invalid("actor is required")
    if action not in (APPROVE, REJECT):
        return _invalid("action must be 'approve' or 'reject'", actor.strip())
    comment = raw.get("comment")
    return {
        "action": action,
        "actor": actor.strip(),
        "comment": comment if isinstance(comment, str) else "",
        "valid": True,
    }


class _FileLock:
    """Tiny cross-platform lock file (works on Windows, unlike fcntl)."""

    def __init__(self, path: Path, timeout: float = 5.0, stale_after: float = 30.0) -> None:
        self.path = path
        self.timeout = timeout
        self.stale_after = stale_after
        self._fd: Optional[int] = None

    def __enter__(self) -> "_FileLock":
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale_after:
                        self.path.unlink()  # left behind by a crashed process
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"could not lock {self.path}")
                time.sleep(0.01)

    def __exit__(self, *exc_info: Any) -> None:
        if self._fd is not None:
            os.close(self._fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class AuditLog:
    """Append-only JSON-lines log. ``write_once`` is idempotent per idempotency_key."""

    def __init__(self, path: Optional[os.PathLike] = None) -> None:
        self.path = Path(path or os.getenv("AUDIT_LOG_PATH", "audit_log.jsonl"))
        self._lock = threading.Lock()

    def read_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def find(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        for entry in self.read_all():
            if entry.get("idempotency_key") == idempotency_key:
                return entry
        return None

    def write_once(self, entry: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """Write ``entry`` unless its key already exists. Returns (entry_on_disk, created).

        Replayed webhooks and a racing approve/reject both hit the same key, so exactly
        one entry is ever written and the first writer wins.
        """
        key = entry["idempotency_key"]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        with self._lock, _FileLock(lock_path):
            existing = self.find(key)
            if existing is not None:
                logger.info("audit entry %s already exists; not writing again", key)
                return existing, False
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return entry, True


def build_audit_entry(
    *,
    idempotency_key: str,
    state: Dict[str, Any],
    final_status: str,
    actor: str,
    approval_comment: str = "",
) -> Dict[str, Any]:
    """Assemble the audit record: decision, actor, timestamp, review ID plus evidence."""
    rule_result = state.get("rule_result", {})
    facts = state.get("case_facts", {})
    return {
        "idempotency_key": idempotency_key,
        "case_id": state.get("case_id"),
        "invoice_id": state.get("invoice_id"),
        "review_id": state.get("review_id"),
        "correlation_id": state.get("correlation_id"),
        "decision": rule_result.get("decision"),
        "final_status": final_status,
        "actor": actor,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reasons": rule_result.get("reasons", []),
        "evidence": rule_result.get("evidence", []),
        "policy_clauses": rule_result.get("policy_clauses", []),
        "invoice_total_inr": facts.get("invoice_total_inr"),
        "approval_comment": approval_comment,
        "requester_comment": state.get("comment", ""),
        "crew_status": state.get("crew_status"),
        "autogen_status": state.get("autogen_status"),
        "explanation_source": state.get("explanation_source"),
        "action_executed": False,
    }
