"""Iter-293.4 — Global Qoyod Production Write Lock.

ZATCA-sensitive guard: when `production_writes_locked=True` in
qoyod_settings, NO write (POST/PUT/PATCH/DELETE) to api.qoyod.com is
allowed, across ALL paths:

    - create_invoice / create_invoice_payment / create_receipt
    - create_product / create_contact
    - delete_invoice / delete_receipt / delete_product / delete_customer
    - any future write endpoint added to QoyodAPIClient

Defense-in-depth: enforced at the QoyodAPIClient._request layer itself
so even if a pipeline / resolver / retry / repair tool forgets to
check the lock at its callsite, the client itself refuses to send.

Fail-Closed on Production:
    Setting `QOYOD_FAIL_CLOSED_DEFAULT=true` in backend/.env makes a
    MISSING `production_writes_locked` field behave as True. This
    protects newly-deployed Production tenants from accidental writes
    during the window between Deploy and the operator explicitly
    setting the flag.

Blocked attempts are persisted to `qoyod_write_lock_attempts` (audit
collection) AND emitted to stdout/journal at WARNING level in the
format:
    BLOCKED_QOYOD_WRITE action=<x> order=<y> reason=production_writes_locked

The recorder NEVER raises — it is best-effort and must not break the
write-block path.
"""
from __future__ import annotations

import contextvars
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


logger = logging.getLogger("qoyod.write_lock")


# HTTP methods that mutate state on Qoyod's side.
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# Per-call context. Pipeline / resolver / one_shot / retry should set
# this BEFORE the api_client call so blocked-attempt records carry
# meaningful audit fields (order_number, trace_id, sku, etc).
_write_lock_context: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "qoyod_write_lock_context", default={})


def set_write_lock_context(**ctx) -> contextvars.Token:
    """Set per-call audit context. Returns a token the caller can use
    with `reset_write_lock_context` to restore the previous frame."""
    return _write_lock_context.set(dict(ctx))


def get_write_lock_context() -> dict:
    return dict(_write_lock_context.get() or {})


def reset_write_lock_context(token: contextvars.Token) -> None:
    _write_lock_context.reset(token)


# ─────────────────────────────────────────────────────────────────────


class QoyodWriteLockedError(Exception):
    """Raised when a write attempt is blocked by the global write lock.

    The pipeline / route handler can match on this to surface a clean
    `QOYOD_WRITE_LOCKED` outcome to the operator with the attempt_id
    they can use to find the locked payload in the audit log.
    """

    def __init__(
        self,
        action: str,
        *,
        reason: str = "production_writes_locked",
        attempt_id: Optional[str] = None,
        method: str = "",
        path: str = "",
    ):
        self.action = action
        self.reason = reason
        self.attempt_id = attempt_id
        self.method = method
        self.path = path
        super().__init__(
            f"QOYOD_WRITE_LOCKED: {method} {path} action={action} "
            f"reason={reason} attempt_id={attempt_id}")

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "code":       "qoyod_write_locked",
            "reason":     self.reason,
            "action":     self.action,
            "method":     self.method,
            "endpoint":   self.path,
            "attempt_id": self.attempt_id,
            "message":    str(self),
        }


# ─────────────────────────────────────────────────────────────────────


def classify_action(method: str, path: str) -> str:
    """Map (HTTP method, Qoyod path) → human-readable action name.

    Used in audit records and operator dashboards. Keep stable.
    """
    m = (method or "").upper()
    p = path or ""
    if m == "POST":
        if p.startswith("/invoice_payments"):
            return "create_invoice_payment"
        if p.startswith("/invoices"):
            return "create_invoice"
        if p.startswith("/receipts"):
            return "create_receipt"
        if p.startswith("/products"):
            return "create_product"
        if p.startswith("/customers") or p.startswith("/contacts"):
            return "create_contact"
    if m == "DELETE":
        if p.startswith("/invoices/"):
            return "delete_invoice"
        if p.startswith("/receipts/"):
            return "delete_receipt"
        if p.startswith("/products/"):
            return "delete_product"
        if p.startswith("/customers/") or p.startswith("/contacts/"):
            return "delete_contact"
    if m in ("PUT", "PATCH"):
        seg = p.strip("/").split("/")[0] if p else "unknown"
        return f"update_{seg}"
    seg = (p.strip("/").split("/")[0] if p else "unknown") or "unknown"
    return f"{m.lower()}_{seg}"


def mask_email(email: Optional[str]) -> Optional[str]:
    if not email or not isinstance(email, str):
        return None
    parts = email.split("@")
    if len(parts) != 2:
        return "***"
    local, domain = parts
    if len(local) <= 2:
        return f"{'*' * len(local)}@{domain}"
    return f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}@{domain}"


def extract_payload_hints(action: str, payload: Any) -> dict:
    """Pull harmless audit hints from the outbound payload.

    NEVER includes the full payload; just enough for the operator to
    correlate the blocked attempt with the original Salla order.
    """
    hints: dict = {}
    if not isinstance(payload, dict):
        return hints

    if action == "create_product":
        p = payload.get("product") if isinstance(payload.get("product"), dict) else payload
        sku = (p.get("sku") if isinstance(p, dict) else None) or (
            p.get("reference") if isinstance(p, dict) else None)
        if sku:
            hints["sku"] = str(sku)[:64]
        name = p.get("name") if isinstance(p, dict) else None
        if name:
            hints["product_name"] = str(name)[:120]

    elif action == "create_contact":
        c = payload.get("contact") if isinstance(payload.get("contact"), dict) else None
        if c is None:
            c = payload.get("customer") if isinstance(payload.get("customer"), dict) else payload
        email = c.get("email") if isinstance(c, dict) else None
        if email:
            hints["customer_email_masked"] = mask_email(email)
        name = c.get("name") if isinstance(c, dict) else None
        if name:
            hints["customer_name"] = str(name)[:120]
        phone = c.get("phone") if isinstance(c, dict) else None
        if phone:
            # Mask all but last 4 digits
            s = str(phone)
            hints["customer_phone_masked"] = (
                ("*" * max(0, len(s) - 4)) + s[-4:] if len(s) > 4 else "***")

    elif action in ("create_invoice", "create_invoice_payment", "create_receipt"):
        inner_keys = ("invoice", "invoice_payment", "receipt")
        inv: Any = payload
        for k in inner_keys:
            if isinstance(payload.get(k), dict):
                inv = payload[k]
                break
        if isinstance(inv, dict):
            ref = inv.get("reference") or inv.get("invoice_id")
            if ref is not None:
                hints["reference"] = str(ref)
            for amt_key in ("amount", "total", "grand_total"):
                if inv.get(amt_key) is not None:
                    hints["amount"] = inv.get(amt_key)
                    break
            if inv.get("contact_id") is not None:
                hints["contact_id"] = inv.get("contact_id")

    elif action.startswith("delete_"):
        hints["target_id_from_path"] = True  # caller can read it off `path`

    return hints


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Audit log


async def record_blocked_attempt(
    db,
    *,
    user_id: str,
    action: str,
    method: str,
    path: str,
    payload: Any = None,
    idempotency_key: Optional[str] = None,
    extra_context: Optional[dict] = None,
) -> str:
    """Persist a blocked-write attempt. Returns attempt_id.

    NEVER raises — recording must not interfere with the lock semantics.
    """
    attempt_id = str(uuid.uuid4())
    ctx = get_write_lock_context()
    if extra_context:
        ctx = {**ctx, **extra_context}

    order_number = ctx.get("order_number")
    trace_id = ctx.get("trace_id")
    callsite = ctx.get("callsite")

    hints = extract_payload_hints(action, payload)

    doc = {
        "attempt_id":      attempt_id,
        "user_id":         user_id,
        "action":          action,
        "method":          (method or "").upper(),
        "path":            path,
        "reason":          "production_writes_locked",
        "order_number":    order_number,
        "trace_id":        trace_id,
        "callsite":        callsite,
        "idempotency_key": idempotency_key,
        "blocked_at":      _now(),
        # Persist the full outbound payload so an operator can replay
        # exactly what would have been sent if the lock is later
        # released for THIS specific order via one_shot_reprocess.
        "locked_payload":  payload if isinstance(payload, (dict, list)) else None,
        "hints":           hints,
        "extra_context":   {k: v for k, v in ctx.items()
                            if k not in ("order_number", "trace_id", "callsite")},
    }
    try:
        await db.qoyod_write_lock_attempts.insert_one(doc)
    except Exception:
        # Best-effort audit — never break the lock path.
        pass

    # Iter-293.4 — stdout/journal log alongside the DB audit.
    emit_blocked_log(
        action=action, method=method, path=path,
        order_number=order_number, trace_id=trace_id,
        hints=hints, attempt_id=attempt_id,
    )
    return attempt_id


async def list_blocked_attempts(
    db,
    *,
    user_id: str,
    limit: int = 100,
    action: Optional[str] = None,
    order_number: Optional[str] = None,
    since_hours: Optional[int] = None,
) -> list[dict]:
    q: dict = {"user_id": user_id}
    if action:
        q["action"] = action
    if order_number:
        q["order_number"] = order_number
    if since_hours:
        q["blocked_at"] = {"$gte": _now() - timedelta(hours=since_hours)}
    cur = db.qoyod_write_lock_attempts.find(q, {"_id": 0}).sort(
        "blocked_at", -1).limit(max(1, min(limit, 500)))
    out: list[dict] = []
    async for d in cur:
        # Coerce datetimes to isoformat for the API response.
        if isinstance(d.get("blocked_at"), datetime):
            d["blocked_at"] = d["blocked_at"].isoformat()
        out.append(d)
    return out


async def count_blocked_attempts_by_action(
    db, *, user_id: str, since_hours: int = 24,
) -> dict[str, int]:
    pipe = [
        {"$match": {
            "user_id":   user_id,
            "blocked_at": {"$gte": _now() - timedelta(hours=since_hours)},
        }},
        {"$group": {"_id": "$action", "count": {"$sum": 1}}},
    ]
    out: dict[str, int] = {}
    try:
        async for d in db.qoyod_write_lock_attempts.aggregate(pipe):
            out[d["_id"]] = int(d.get("count", 0))
    except Exception:
        pass
    return out


# ─────────────────────────────────────────────────────────────────────


def is_locked(settings: dict | None) -> bool:
    """Single source of truth for the lock flag.

    Iter-293.4 Fail-Closed semantics:
        • If `production_writes_locked` is explicitly True/False in
          settings → honour exactly.
        • If MISSING (None / key absent):
            – Env `QOYOD_FAIL_CLOSED_DEFAULT=true` → LOCKED.
            – Otherwise → unlocked (legacy / dev / preview behaviour).

    The env-driven default lets Production deploys land already-locked
    so a webhook can NEVER write to api.qoyod.com during the window
    between Deploy and the operator explicitly setting the flag.
    """
    if settings is not None:
        v = settings.get("production_writes_locked")
        if v is True:
            return True
        if v is False:
            return False
    # Explicitly unset — fall back to env-driven default.
    return os.environ.get(
        "QOYOD_FAIL_CLOSED_DEFAULT", "").strip().lower() in ("1", "true", "yes")


def fail_closed_default_enabled() -> bool:
    """Return True if the env-driven fail-closed default is active.
    Exposed so the operator UI / reports can surface the policy."""
    return os.environ.get(
        "QOYOD_FAIL_CLOSED_DEFAULT", "").strip().lower() in ("1", "true", "yes")


def emit_blocked_log(
    *, action: str, method: str, path: str,
    order_number: Optional[str] = None,
    trace_id: Optional[str] = None,
    hints: Optional[dict] = None,
    attempt_id: Optional[str] = None,
) -> None:
    """Emit a single-line WARNING to stdout/journal so operators can
    grep the live log without opening MongoDB.

    Format (stable — pinned by tests):
        BLOCKED_QOYOD_WRITE action=<x> method=<M> path=<p> order=<o>
        trace=<t> reason=production_writes_locked attempt_id=<id> [extras]
    """
    extras: list[str] = []
    if hints:
        if hints.get("sku"):
            extras.append(f"sku={hints['sku']}")
        if hints.get("customer_email_masked"):
            extras.append(f"email_masked={hints['customer_email_masked']}")
        if hints.get("reference"):
            extras.append(f"reference={hints['reference']}")
        if hints.get("amount") is not None:
            extras.append(f"amount={hints['amount']}")
    extras_str = (" " + " ".join(extras)) if extras else ""
    logger.warning(
        "BLOCKED_QOYOD_WRITE action=%s method=%s path=%s order=%s "
        "trace=%s reason=production_writes_locked attempt_id=%s%s",
        action, method, path,
        order_number or "-", trace_id or "-",
        attempt_id or "-", extras_str,
    )
