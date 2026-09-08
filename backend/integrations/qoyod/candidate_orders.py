"""Canonical, read-only Salla -> Qoyod candidate universe.

``unified_orders`` is the only collection allowed to introduce an order into
the Qoyod billing universe.  The inbox is an operational trace/marker source;
it must never decide whether an order exists or is billable.  Likewise, an
order is considered present in Qoyod only when its Salla order number equals
the local Qoyod invoice ``reference``.  Counts in this module are therefore
set cardinalities, never arithmetic differences between unrelated totals.

The helpers are deliberately persistence-free and make no network calls.  A
caller can use the resulting audit both for a UI report and for a worker Dry
Run without risking a Qoyod or Mongo write.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from integrations.qoyod.eligible_orders import _parse_iso_date
from integrations.qoyod.payment_methods import (
    is_bank_transfer_family,
    is_cod_family,
)


RIYADH_TZ = ZoneInfo("Asia/Riyadh")

CANDIDATE_AUDIT_SCAN_LIMIT = 10_000
CANDIDATE_AUDIT_BATCH_SIZE = 100
CANDIDATE_AUDIT_MAX_TIME_MS = 8_000


class CandidateAuditScanLimitExceeded(RuntimeError):
    """Fail closed when an exact candidate audit exceeds its safety ceiling."""

    def __init__(self, scan_metadata: dict[str, Any]):
        self.scan_metadata = scan_metadata
        super().__init__(
            "candidate audit scan limit exceeded; exact result unavailable"
        )


def _bounded_cursor(
    cursor: Any,
    *,
    scan_limit: int,
    batch_size: int = CANDIDATE_AUDIT_BATCH_SIZE,
    max_time_ms: int = CANDIDATE_AUDIT_MAX_TIME_MS,
) -> Any:
    """Apply Motor/PyMongo bounds while remaining compatible with test fakes."""
    for method_name, value in (
        ("batch_size", max(1, int(batch_size))),
        ("max_time_ms", max(1, int(max_time_ms))),
        ("limit", max(1, int(scan_limit)) + 1),
    ):
        method = getattr(cursor, method_name, None)
        if callable(method):
            cursor = method(value)
    return cursor

# Candidate visibility and automated financial-write authorization are
# deliberately separate. This worker-only flag is absent/false on existing
# production settings, so deploying the unified read model cannot send a
# backlog. No manual-send or invoice-construction code reads this setting.
UNIFIED_CANDIDATE_AUTO_FLAG = "plan_b_unified_auto_send_enabled"

# Public/API tabs retain the historical ``in_delivery`` key.  Internally the
# canonical business state is named ``delivering`` as supplied in the 2026-08-22
# acceptance criteria.
ELIGIBLE_STATUS_KEYS: tuple[str, ...] = (
    "completed",
    "delivering",
    "delivered",
)
STATUS_KEY_TO_API: dict[str, str] = {
    "completed": "completed",
    "delivering": "in_delivery",
    "delivered": "delivered",
}
API_STATUS_TO_KEY: dict[str, str] = {
    "completed": "completed",
    "delivering": "delivering",
    "in_delivery": "delivering",
    "delivered": "delivered",
}


def normalize_status(value: Any) -> str:
    """Return a stable comparison token for Salla status values."""
    if value is None:
        return ""
    text = str(value).replace("_", " ").strip()
    return " ".join(text.split()).casefold()


_ELIGIBLE_ALIASES: dict[str, frozenset[str]] = {
    "completed": frozenset(normalize_status(value) for value in (
        "completed", "تم التنفيذ", "منتهي", "مكتمل",
    )),
    "delivering": frozenset(normalize_status(value) for value in (
        "delivering", "in_delivery", "in delivery", "under_delivery",
        # Salla has historically emitted `shipping` for جاري التوصيل.  The
        # completed `shipped` state remains explicitly excluded below.
        "shipping", "جاري التوصيل", "جارٍ التوصيل", "قيد التوصيل",
    )),
    "delivered": frozenset(normalize_status(value) for value in (
        "delivered", "تم التوصيل",
    )),
}

# Fail closed when either the slug or the native label carries one of these
# states.  This prevents a stale eligible field from overriding a current
# cancelled/refunded/unpaid value on the same unified row.
INELIGIBLE_STATUS_ALIASES: frozenset[str] = frozenset(
    normalize_status(value) for value in (
        "payment_pending", "payment pending", "pending_payment", "pending",
        "under_review", "under review", "in_review", "in review",
        "processing", "in_progress", "in progress",
        "shipped", "canceled", "cancelled", "canceled_by_customer",
        "restored", "restoring", "refunded", "refund", "deleted",
        "بانتظار الدفع", "بإنتظار الدفع", "قيد المراجعة", "تحت المراجعة",
        "قيد التنفيذ", "تم الشحن", "ملغي", "ملغى", "مسترجع",
        "قيد الاسترجاع", "محذوف",
    )
)

INELIGIBLE_PAYMENT_STATUSES: frozenset[str] = frozenset(
    normalize_status(value) for value in (
        "payment_pending", "payment pending", "pending_payment",
        "pending", "waiting", "awaiting_payment", "unpaid", "not_paid",
        "failed", "declined", "voided", "refunded", "under_review",
        "pending_accountant_review", "partial", "partially_paid",
        "partially paid", "بانتظار الدفع", "بإنتظار الدفع",
        "قيد المراجعة", "غير مدفوع", "فشل الدفع",
    )
)

# These states invalidate even COD.  COD may legitimately be awaiting cash
# collection, but a failed/refunded/review/partial signal is not equivalent to
# the ordinary invoice-only COD state and must remain fail-closed.
ALWAYS_BLOCKED_PAYMENT_STATUSES: frozenset[str] = frozenset(
    normalize_status(value) for value in (
        "failed", "declined", "voided", "refunded", "under_review",
        "pending_accountant_review", "partial", "partially_paid",
        "partially paid", "قيد المراجعة", "فشل الدفع",
    )
)

ELIGIBLE_PAYMENT_STATUSES: frozenset[str] = frozenset(
    normalize_status(value) for value in (
        "paid", "fully_paid", "fully paid", "captured", "completed",
        "success", "succeeded", "تم الدفع", "مدفوع",
    )
)


PAYMENT_ELIGIBLE = "eligible"
PAYMENT_INELIGIBLE = "ineligible"
PAYMENT_NEEDS_LIVE_VERIFICATION = "needs_live_verification"


def payment_eligibility(row: dict[str, Any]) -> str:
    """Classify durable payment evidence without hiding legacy rows.

    COD orders are intentionally invoice-only in ``manual_send_one`` and are
    therefore billable without a captured electronic payment. Explicit
    unpaid/pending/refunded/partial evidence is ineligible. Missing legacy
    evidence is *visible* as needing live verification, then the final shared
    send gate uses :func:`payment_is_eligible` and fails closed after Salla
    resync. This preserves backlog visibility without risking an unpaid send.
    """
    payment_method = (
        row.get("payment_method") or row.get("payment_method_native")
    )
    normalized_method = normalize_status(payment_method)
    statuses = {
        normalize_status(row.get("payment_collection_status")),
        normalize_status(row.get("payment_status")),
    }
    statuses.discard("")
    if statuses & ALWAYS_BLOCKED_PAYMENT_STATUSES:
        return PAYMENT_INELIGIBLE
    if is_cod_family(payment_method):
        # COD is intentionally invoice-only. Missing/unpaid collection is
        # expected, but explicit failed/refunded/review/partial evidence above
        # still blocks the invoice.
        return PAYMENT_ELIGIBLE
    if normalized_method in INELIGIBLE_PAYMENT_STATUSES:
        return PAYMENT_INELIGIBLE
    if statuses & INELIGIBLE_PAYMENT_STATUSES:
        return PAYMENT_INELIGIBLE
    if row.get("is_pending_payment") is True:
        return PAYMENT_INELIGIBLE
    try:
        remaining_raw = row.get("remaining_amount")
        paid_raw = row.get("paid_amount")
        total_raw = row.get("total_amount")
        remaining = (
            float(remaining_raw) if remaining_raw not in (None, "") else None
        )
        paid = float(paid_raw) if paid_raw not in (None, "") else None
        total = float(total_raw) if total_raw not in (None, "") else None
    except (TypeError, ValueError):
        return PAYMENT_INELIGIBLE
    if (
        row.get("has_remaining_amount") is True
        or (remaining is not None and remaining > 0)
    ):
        return PAYMENT_INELIGIBLE
    if (
        row.get("is_pending_payment") is False
        and not is_bank_transfer_family(payment_method)
    ):
        # Salla Order Details exposes this boolean even when the light
        # response omits paid_amount/payment.status. Keep contradictory
        # partial numeric evidence fail-closed.
        if (
            paid is not None
            and paid > 0
            and total is not None
            and total > 0
            and paid + 0.01 < total
        ):
            return PAYMENT_INELIGIBLE
        return PAYMENT_ELIGIBLE
    # Prepaid/BNPL/bank-transfer orders require positive proof of collection;
    # method alone is not proof that the order was paid. A positive amount is
    # proof only when it covers the known order total (within one halalah), or
    # when the source also explicitly reports a zero remaining balance.
    if statuses & ELIGIBLE_PAYMENT_STATUSES:
        # A textual "paid" flag cannot override contradictory numeric
        # evidence. This catches stale/partial rows such as paid=1, total=100
        # even when the remaining amount was omitted by the source.
        if (
            paid is not None
            and total is not None
            and total > 0
            and paid + 0.01 < total
        ):
            return PAYMENT_INELIGIBLE
        return PAYMENT_ELIGIBLE
    if paid is None or paid <= 0:
        return PAYMENT_NEEDS_LIVE_VERIFICATION
    if total is not None and total > 0:
        return (
            PAYMENT_ELIGIBLE
            if paid + 0.01 >= total
            else PAYMENT_INELIGIBLE
        )
    if remaining == 0 and row.get("has_remaining_amount") is False:
        return PAYMENT_ELIGIBLE
    return PAYMENT_NEEDS_LIVE_VERIFICATION


def payment_is_eligible(row: dict[str, Any]) -> bool:
    """Strict final-write predicate; unknown legacy evidence is not enough."""
    return payment_eligibility(row) == PAYMENT_ELIGIBLE


def eligible_status_key(*values: Any) -> Optional[str]:
    """Resolve the closed three-state policy, rejecting conflicts.

    All supplied status representations are considered together.  An explicit
    ineligible value wins, and two different eligible keys on one row are
    treated as conflicting rather than guessing which field is newer.
    """
    normalized = {normalize_status(value) for value in values}
    normalized.discard("")
    if not normalized or normalized & INELIGIBLE_STATUS_ALIASES:
        return None
    matches = {
        key for key, aliases in _ELIGIBLE_ALIASES.items()
        if normalized & aliases
    }
    if len(matches) != 1:
        return None
    return next(iter(matches))


def unified_status_key(row: dict[str, Any]) -> Optional[str]:
    return eligible_status_key(
        row.get("order_status_slug"),
        row.get("status_slug"),
        row.get("order_status"),
        row.get("order_status_native"),
        row.get("status_native"),
    )


def inbox_status_key(row: Optional[dict[str, Any]]) -> Optional[str]:
    canonical = (row or {}).get("canonical_payload") or {}
    return eligible_status_key(
        canonical.get("order_status_slug"),
        canonical.get("order_status"),
        canonical.get("order_status_native"),
    )


def _salla_business_date(value: Any, *, timezone_name: Any = None) -> Optional[date]:
    """Parse a Salla timestamp as an Asia/Riyadh business date.

    Salla's date object contains a local wall-clock value plus an explicit
    timezone.  Other sources sometimes preserve the same instant as UTC.  A
    bare ``value[:10]`` therefore moves near-midnight orders to the prior day.
    """
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        return _salla_business_date(
            value.get("date") or value.get("created_at"),
            timezone_name=value.get("timezone") or timezone_name,
        )
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    parsed: Optional[datetime] = None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            if len(text) == 10:
                return date.fromisoformat(text)
            parsed = datetime.fromisoformat(
                text.replace("Z", "+00:00").replace(" ", "T", 1)
            )
        except ValueError:
            return _parse_iso_date(text)
    if parsed.tzinfo is not None:
        return parsed.astimezone(RIYADH_TZ).date()
    try:
        local_tz = ZoneInfo(str(timezone_name)) if timezone_name else RIYADH_TZ
    except (KeyError, ValueError):
        local_tz = RIYADH_TZ
    return parsed.replace(tzinfo=local_tz).astimezone(RIYADH_TZ).date()


def unified_order_date_evidence(row: dict[str, Any]) -> tuple[Optional[date], str]:
    """Return authoritative Salla business date and its auditable source."""
    raw_salla = ((row.get("raw_by_source") or {}).get("salla_direct") or {})
    if isinstance(raw_salla, dict):
        for field in ("date", "created_at"):
            parsed = _salla_business_date(raw_salla.get(field))
            if parsed is not None:
                return parsed, f"raw_by_source.salla_direct.{field}"
    raw_date = row.get("order_date_raw")
    if raw_date not in (None, ""):
        parsed = _salla_business_date(raw_date)
        if parsed is not None:
            return parsed, "order_date_raw"
    if row.get("order_date_inferred") is True:
        return None, "inferred_order_date_rejected"
    return _parse_iso_date(row.get("order_date")), "order_date"


def unified_order_date(row: dict[str, Any]) -> Optional[date]:
    """Return the real Salla business date, never a Mezan write time."""
    return unified_order_date_evidence(row)[0]


def real_invoice_id(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and not text.upper().startswith(("DRY:", "PREVIEW:"))


def official_qoyod_reference(row: dict[str, Any]) -> tuple[str, str]:
    """Return only a proven Qoyod invoice `reference` and its provenance.

    Older sync code incorrectly copied `external_reference` or
    `source_reference` into the local `reference` field. Synced rows must
    therefore prove the official value from an explicitly attested provenance
    field or the preserved raw Qoyod response. A local ``reference`` value, or
    an un-attested ``qoyod_official_reference``, is diagnostic data only: it is
    not evidence that Qoyod accepted that exact reference.
    """
    explicit = str(row.get("qoyod_official_reference") or "").strip()
    provenance = str(row.get("reference_provenance") or "").strip()
    if explicit and provenance == "qoyod.reference":
        return explicit, "qoyod_official_reference"
    raw = row.get("raw_response") or {}
    raw_reference = (
        str(raw.get("reference") or "").strip()
        if isinstance(raw, dict) else ""
    )
    if raw_reference:
        return raw_reference, "raw_response.reference"
    local_reference = str(row.get("reference") or "").strip()
    if (
        local_reference
        and str(row.get("source") or "").strip() == "plan_b_send"
        and real_invoice_id(row.get("qoyod_invoice_id"))
    ):
        # The unchanged Plan-B sender writes this mirror only after Qoyod has
        # returned a real invoice id (and after payment success, except COD or
        # a recorded partial-payment outcome). It is an exact copy of the
        # `reference` sent by that path, never an external/source alias.
        return local_reference, "plan_b_send.reference"
    if any(str(row.get(field) or "").strip() for field in (
        "reference",
        "qoyod_official_reference",
        "external_reference",
        "source_reference",
    )):
        return "", "unproven_local_reference"
    return "", "missing_official_reference"


def reference_set_sha256(references: Iterable[Any]) -> str:
    """Stable audit hash: sorted UTF-8 refs joined by LF, no trailing LF."""
    normalized = sorted({
        str(value).strip() for value in references if str(value).strip()
    })
    return hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()


def candidate_snapshot_fingerprint(
    rows: Iterable[dict[str, Any]],
    *,
    orders_user_id: Any = "",
    from_date: Any = "",
    to_date: Any = "",
) -> str:
    """Return a stable digest for one dynamically discovered candidate view.

    Capture time is deliberately excluded: an unchanged candidate universe
    keeps the same fingerprint across worker ticks, while a status, amount,
    payment-verdict, or exact-reference change alters it.
    """
    content = [
        {
            "order_number": str(row.get("order_number") or ""),
            "order_date": str(row.get("order_date") or ""),
            "current_status": str(row.get("current_status") or ""),
            "current_status_key": str(row.get("current_status_key") or ""),
            "total_amount": str(row.get("total_amount") or ""),
            "currency": str(row.get("currency") or "SAR"),
            "payment_eligibility": str(row.get("payment_eligibility") or ""),
            "worker_candidate": bool(row.get("worker_candidate")),
            "has_qoyod_reference_match": bool(
                row.get("has_qoyod_reference_match")
            ),
        }
        for row in sorted(
            rows,
            key=lambda value: str(value.get("order_number") or ""),
        )
    ]
    payload = json.dumps(
        {
            "scope": {
                "orders_user_id": str(orders_user_id or ""),
                "from_date": str(from_date or ""),
                "to_date": str(to_date or ""),
            },
            "orders": content,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _coerce_date(value: Any, *, field: str) -> Optional[date]:
    if value in (None, ""):
        return None
    parsed = _parse_iso_date(value)
    if parsed is None:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)")
    return parsed


@dataclass(frozen=True)
class CandidateDateRange:
    from_date: date
    to_date: date
    requested_from_date: date

    def as_dict(self) -> dict[str, str]:
        return {
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "requested_from_date": self.requested_from_date.isoformat(),
        }


def resolve_candidate_date_range(
    *,
    from_date: Any = None,
    to_date: Any = None,
    days: int = 90,
    now: Optional[datetime] = None,
) -> CandidateDateRange:
    """Resolve an inclusive real order-date interval.

    The historical UI describes ``days=7`` as the range from seven calendar
    days ago through today (eight inclusive date labels), so that compatibility
    is retained.  Explicit ``from_date``/``to_date`` always win.
    """
    current = now or datetime.now(RIYADH_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=RIYADH_TZ)
    end = (
        _coerce_date(to_date, field="to_date")
        or current.astimezone(RIYADH_TZ).date()
    )
    lookback = max(1, min(int(days), 3650))
    requested_start = (
        _coerce_date(from_date, field="from_date")
        or (end - timedelta(days=lookback))
    )
    if requested_start > end:
        raise ValueError("from_date must be on or before to_date")
    # Apply the caller's real interval exactly.  The historical 2026-07-01
    # accounting rollout date is report metadata, not an implicit query floor.
    # Silently clamping here made otherwise valid from/to filters misleading.
    return CandidateDateRange(requested_start, end, requested_start)


def _owner_query(owner_ids: Iterable[str]) -> str | dict[str, Any]:
    values = list(dict.fromkeys(
        str(value).strip() for value in owner_ids if str(value).strip()
    ))
    if not values:
        return ""
    return values[0] if len(values) == 1 else {"$in": values}


async def load_unified_candidates(
    db: Any,
    *,
    orders_user_id: str,
    date_range: CandidateDateRange,
    search: Optional[str] = None,
    scan_limit: int = CANDIDATE_AUDIT_SCAN_LIMIT,
    lightweight: bool = False,
) -> dict[str, Any]:
    """Load the authoritative eligible set keyed by Salla order number."""
    # Do not pre-filter on the legacy root ``order_date``. It can be one day
    # behind the Salla/Riyadh business date, or absent while raw Salla
    # evidence is valid. Scanning the tenant projection and applying the
    # authoritative range below is the only way to report every exclusion
    # reason without silently dropping rows at the Mongo query boundary.
    query: dict[str, Any] = {"user_id": str(orders_user_id)}
    if search and str(search).strip():
        import re
        query["order_number"] = {
            "$regex": re.escape(str(search).strip())
        }
    projection = {
        "_id": 0,
        "user_id": 1,
        "order_id": 1,
        "order_number": 1,
        "order_date": 1,
        "order_date_raw": 1,
        "order_date_inferred": 1,
        "raw_by_source.salla_direct.date": 1,
        "raw_by_source.salla_direct.created_at": 1,
        "order_status": 1,
        "order_status_slug": 1,
        "order_status_native": 1,
        "status_slug": 1,
        "status_native": 1,
        "payment_status": 1,
        "payment_collection_status": 1,
        "paid_amount": 1,
        "remaining_amount": 1,
        "has_remaining_amount": 1,
        "is_pending_payment": 1,
        "payment_method": 1,
        "payment_method_native": 1,
        "total_amount": 1,
        "shipping_amount": 1,
        "tax_amount": 1,
        "items": 1,
        "products": 1,
        "currency": 1,
        "customer_name": 1,
        "customer_mobile": 1,
        "customer_email": 1,
        "customer": 1,
        "created_at": 1,
        "completed_at": 1,
        "delivered_at": 1,
        "updated_at": 1,
    }
    if lightweight:
        for field in (
            "order_id", "shipping_amount", "tax_amount", "items", "products",
            "customer_email", "customer",
        ):
            projection.pop(field, None)
        projection["customer.name"] = 1
        projection["customer.phone"] = 1

    scan_limit = max(1, int(scan_limit))
    cursor = db.unified_orders.find(query, projection).sort("order_date", -1)
    cursor = _bounded_cursor(cursor, scan_limit=scan_limit)
    candidates: dict[str, dict[str, Any]] = {}
    excluded: dict[str, int] = {
        "missing_order_number": 0,
        "missing_or_inferred_order_date": 0,
        "outside_requested_date_range": 0,
        "status_not_eligible": 0,
        "payment_not_eligible": 0,
        "duplicate_unified_reference": 0,
    }
    excluded_by_status: dict[str, int] = {}
    scanned = 0
    scan_truncated = False
    async for row in cursor:
        scanned += 1
        if scanned > scan_limit:
            scan_truncated = True
            break
        order_number = str(row.get("order_number") or "").strip()
        if not order_number:
            excluded["missing_order_number"] += 1
            continue
        order_date, order_date_source = unified_order_date_evidence(row)
        if order_date is None:
            excluded["missing_or_inferred_order_date"] += 1
            continue
        if not (date_range.from_date <= order_date <= date_range.to_date):
            excluded["outside_requested_date_range"] += 1
            continue
        status_key = unified_status_key(row)
        if status_key is None:
            excluded["status_not_eligible"] += 1
            raw_status = str(
                row.get("order_status_native")
                or row.get("order_status")
                or row.get("order_status_slug")
                or "(empty)"
            )
            excluded_by_status[raw_status] = (
                excluded_by_status.get(raw_status, 0) + 1
            )
            continue
        payment_key = payment_eligibility(row)
        if payment_key == PAYMENT_INELIGIBLE:
            excluded["payment_not_eligible"] += 1
            continue
        if order_number in candidates:
            excluded["duplicate_unified_reference"] += 1
            continue
        candidates[order_number] = {
            **row,
            "order_number": order_number,
            "stored_order_date": row.get("order_date"),
            "order_date": order_date.isoformat(),
            "order_date_source": order_date_source,
            "qoyod_status_key": status_key,
            "qoyod_status_api_key": STATUS_KEY_TO_API[status_key],
            "qoyod_payment_eligibility": payment_key,
        }
    return {
        "by_reference": candidates,
        "references": set(candidates),
        "scanned": min(scanned, scan_limit),
        "observed_rows": scanned,
        "scan_limit": scan_limit,
        "scan_truncated": scan_truncated,
        "excluded": excluded,
        "excluded_by_status": excluded_by_status,
    }


async def load_inbox_evidence(
    db: Any,
    *,
    marker_user_ids: Iterable[str],
    order_numbers: Optional[Iterable[str]] = None,
    scan_limit: int = CANDIDATE_AUDIT_SCAN_LIMIT,
) -> dict[str, Any]:
    """Stream bounded marker evidence without retaining every inbox event."""
    references = list(dict.fromkeys(
        str(value).strip()
        for value in (order_numbers or [])
        if str(value).strip()
    ))
    scan_limit = max(1, int(scan_limit))
    if order_numbers is not None and not references:
        return {
            "newest": {},
            "markers": {},
            "event_counts": {},
            "owners_by_reference": {},
            "scanned_rows": 0,
            "observed_rows": 0,
            "scan_limit": scan_limit,
            "scan_truncated": False,
        }
    query = {
        "user_id": _owner_query(marker_user_ids),
        "salla_order_number": {"$in": references},
    }
    # Keep this projection deliberately small: canonical payloads, raw payloads,
    # item arrays, and complete stage histories can be very large.  The report
    # needs only classification fields plus real local marker ids.
    projection = {
        "_id": 0,
        "id": 1,
        "trace_id": 1,
        "user_id": 1,
        "salla_order_number": 1,
        "received_at": 1,
        "pipeline_stage": 1,
        "pipeline_error.code": 1,
        "dead_letter_evidence.fail_stage": 1,
        "duplicate_of_invoice.qoyod_invoice_id": 1,
        "duplicate_of_invoice.qoyod_invoice_number": 1,
        "canary_budget_hold": 1,
        "selective_auto_send_gate.reason": 1,
        "stage_history": {"$slice": -6},
        "manual_qoyod_invoice_id": 1,
        "manual_qoyod_invoice_number": 1,
        "manual_qoyod_payment_id": 1,
        "qoyod_invoice_id": 1,
        "qoyod_invoice_number": 1,
        "qoyod_invoice_source": 1,
        "canonical_payload.order_status_slug": 1,
        "canonical_payload.order_status": 1,
        "canonical_payload.order_status_native": 1,
    }
    cursor = db.integration_inbox.find(query, projection).sort(
        "received_at", -1
    )
    cursor = _bounded_cursor(cursor, scan_limit=scan_limit)
    newest: dict[str, dict[str, Any]] = {}
    markers: dict[str, dict[str, Any]] = {}
    event_counts: dict[str, int] = {}
    owners: dict[str, set[str]] = {}
    allowed = set(references)
    marker_fields = (
        "manual_qoyod_invoice_id",
        "manual_qoyod_invoice_number",
        "manual_qoyod_payment_id",
        "qoyod_invoice_id",
        "qoyod_invoice_number",
        "qoyod_invoice_source",
    )
    scanned = 0
    scan_truncated = False
    async for row in cursor:
        scanned += 1
        if scanned > scan_limit:
            scan_truncated = True
            break
        reference = str(row.get("salla_order_number") or "").strip()
        if reference not in allowed:
            continue
        newest.setdefault(reference, row)
        event_counts[reference] = event_counts.get(reference, 0) + 1
        marker_summary = markers.setdefault(reference, {})
        for field in marker_fields:
            value = row.get(field)
            if field not in marker_summary and value not in (None, ""):
                marker_summary[field] = value
        owner = str(row.get("user_id") or "").strip()
        if owner:
            owners.setdefault(reference, set()).add(owner)
    return {
        "newest": newest,
        "markers": markers,
        "event_counts": event_counts,
        "owners_by_reference": owners,
        "scanned_rows": min(scanned, scan_limit),
        "observed_rows": scanned,
        "scan_limit": scan_limit,
        "scan_truncated": scan_truncated,
    }


async def load_qoyod_reference_evidence(
    db: Any,
    *,
    markers_user_id: str,
    order_numbers: Iterable[str],
    scan_limit: int = CANDIDATE_AUDIT_SCAN_LIMIT,
) -> dict[str, Any]:
    """Load bounded real local Qoyod invoices grouped by exact reference."""
    references = list(dict.fromkeys(
        str(value).strip() for value in order_numbers if str(value).strip()
    ))
    scan_limit = max(1, int(scan_limit))
    if not references:
        return {
            "by_reference": {},
            "references": set(),
            "unreferenced": [],
            "duplicate_references": {},
            "scanned_rows": 0,
            "observed_rows": 0,
            "scan_limit": scan_limit,
            "scan_truncated": False,
        }
    projection = {
        "_id": 0,
        "user_id": 1,
        "qoyod_invoice_id": 1,
        "invoice_number": 1,
        "reference": 1,
        "qoyod_official_reference": 1,
        "reference_provenance": 1,
        "external_reference": 1,
        "source_reference": 1,
        "raw_response.reference": 1,
        "salla_order_number": 1,
        "issue_date": 1,
        "total": 1,
        "paid_amount": 1,
        "remaining": 1,
        "status": 1,
        "posting_mode": 1,
        "source": 1,
        "created_at": 1,
        "last_sync_at": 1,
    }
    query: dict[str, Any] = {"user_id": str(markers_user_id)}
    if references:
        reference_match = {"$in": references}
        query["$or"] = [
            {"reference": reference_match},
            {"qoyod_official_reference": reference_match},
            {"external_reference": reference_match},
            {"source_reference": reference_match},
            {"raw_response.reference": reference_match},
            {"salla_order_number": reference_match},
        ]
    cursor = db.qoyod_invoices.find(query, projection).sort(
        [("issue_date", -1), ("created_at", -1)]
    )
    cursor = _bounded_cursor(cursor, scan_limit=scan_limit)
    by_reference: dict[str, list[dict[str, Any]]] = {}
    unreferenced: list[dict[str, Any]] = []
    scanned = 0
    scan_truncated = False
    async for row in cursor:
        scanned += 1
        if scanned > scan_limit:
            scan_truncated = True
            break
        if not real_invoice_id(row.get("qoyod_invoice_id")):
            continue
        stored_reference = row.get("reference")
        reference, reference_source = official_qoyod_reference(row)
        row["stored_reference"] = stored_reference
        row["reference"] = reference or None
        row["reference_match_provenance"] = reference_source
        if not reference:
            unreferenced.append(row)
            continue
        by_reference.setdefault(reference, []).append(row)
    return {
        "by_reference": by_reference,
        "references": set(by_reference),
        "unreferenced": unreferenced,
        "duplicate_references": {
            reference: rows for reference, rows in by_reference.items()
            if len(rows) > 1
        },
        "scanned_rows": min(scanned, scan_limit),
        "observed_rows": scanned,
        "scan_limit": scan_limit,
        "scan_truncated": scan_truncated,
    }


def _real_marker(rows: Iterable[dict[str, Any]], field: str) -> Optional[str]:
    for row in rows:
        value = row.get(field)
        if real_invoice_id(value):
            return str(value)
    return None


async def build_candidate_audit(
    db: Any,
    *,
    orders_user_id: str,
    markers_user_id: str,
    marker_user_ids: Optional[Iterable[str]] = None,
    from_date: Any = None,
    to_date: Any = None,
    days: int = 90,
    now: Optional[datetime] = None,
    search: Optional[str] = None,
    scan_limit: int = CANDIDATE_AUDIT_SCAN_LIMIT,
    lightweight: bool = False,
    require_complete: bool = True,
) -> dict[str, Any]:
    """Build exact eligible/sent/unsent reference sets and per-order proof."""
    captured = now or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    captured_at = captured.astimezone(timezone.utc).isoformat()
    date_range = resolve_candidate_date_range(
        from_date=from_date, to_date=to_date, days=days, now=now
    )
    unified = await load_unified_candidates(
        db,
        orders_user_id=str(orders_user_id),
        date_range=date_range,
        search=search,
        scan_limit=scan_limit,
        lightweight=lightweight,
    )
    eligible_refs: set[str] = set(unified["references"])
    evidence_owners = list(marker_user_ids or (
        str(markers_user_id), str(orders_user_id)
    ))
    inbox = await load_inbox_evidence(
        db,
        marker_user_ids=evidence_owners,
        order_numbers=eligible_refs if lightweight else None,
        scan_limit=scan_limit,
    )
    invoices = await load_qoyod_reference_evidence(
        db,
        markers_user_id=str(markers_user_id),
        order_numbers=eligible_refs,
        scan_limit=scan_limit,
    )
    scan_metadata = {
        "scan_truncated": bool(
            unified["scan_truncated"]
            or inbox["scan_truncated"]
            or invoices["scan_truncated"]
        ),
        "scan_limit": max(1, int(scan_limit)),
        "scanned_rows": {
            "unified_orders": unified["scanned"],
            "integration_inbox": inbox["scanned_rows"],
            "qoyod_invoices": invoices["scanned_rows"],
        },
    }
    if scan_metadata["scan_truncated"] and require_complete:
        raise CandidateAuditScanLimitExceeded(scan_metadata)
    invoice_refs: set[str] = set(invoices["references"])

    # These are genuine set operations over exact references.  No total-count
    # subtraction is used anywhere in the reconciliation contract.
    sent_refs = eligible_refs & invoice_refs
    unsent_refs = eligible_refs - invoice_refs
    qoyod_only_refs = invoice_refs - eligible_refs

    rows: list[dict[str, Any]] = []
    for reference, order in unified["by_reference"].items():
        inbox_row = inbox["newest"].get(reference)
        inbox_markers = inbox["markers"].get(reference, {})
        inbox_rows = [inbox_markers] if inbox_markers else []
        invoice_rows = invoices["by_reference"].get(reference, [])
        invoice = invoice_rows[0] if invoice_rows else None
        has_inbox = inbox_row is not None
        has_qoyod_reference = invoice is not None
        payment_key = order.get("qoyod_payment_eligibility")
        if has_qoyod_reference:
            before_fix_reason = "already_in_qoyod_by_exact_reference"
            candidate_reason = "excluded_already_in_qoyod"
        else:
            if not has_inbox:
                before_fix_reason = "missing_from_integration_inbox"
            elif inbox_status_key(inbox_row) is None:
                before_fix_reason = "newest_inbox_status_not_eligible"
            else:
                before_fix_reason = "eligible_in_legacy_inbox_queue"
            candidate_reason = (
                "requires_live_salla_payment_verification"
                if payment_key == PAYMENT_NEEDS_LIVE_VERIFICATION
                else "eligible_unified_missing_exact_qoyod_reference"
            )

        customer = order.get("customer") or {}
        status_display = (
            order.get("order_status_native")
            or order.get("order_status")
            or order.get("order_status_slug")
        )
        rows.append({
            "order_number": reference,
            "order_date": order.get("order_date"),
            "stored_order_date": order.get("stored_order_date"),
            "order_date_source": order.get("order_date_source"),
            "order_date_mismatch": (
                order.get("stored_order_date") not in (None, order.get("order_date"))
            ),
            "current_status": status_display,
            "current_status_key": order.get("qoyod_status_key"),
            "total_amount": order.get("total_amount"),
            "currency": order.get("currency") or "SAR",
            "payment_method": (
                order.get("payment_method_native")
                or order.get("payment_method")
            ),
            "payment_eligibility": payment_key,
            "customer_name": order.get("customer_name") or customer.get("name"),
            "customer_phone": (
                order.get("customer_mobile") or customer.get("phone")
            ),
            "in_unified_orders": True,
            "unified_orders_owner_id": str(orders_user_id),
            "in_integration_inbox": has_inbox,
            "integration_inbox_event_count": inbox["event_counts"].get(
                reference, 0
            ),
            "integration_inbox_owner_ids": sorted(
                inbox["owners_by_reference"].get(reference, set())
            ),
            "has_qoyod_reference_match": has_qoyod_reference,
            "qoyod_reference": invoice.get("reference") if invoice else None,
            "qoyod_invoice_id": (
                invoice.get("qoyod_invoice_id") if invoice else None
            ),
            "qoyod_invoice_number": (
                invoice.get("invoice_number") if invoice else None
            ),
            "qoyod_invoice_count_for_reference": len(invoice_rows),
            "manual_qoyod_invoice_id": _real_marker(
                inbox_rows, "manual_qoyod_invoice_id"
            ),
            "local_qoyod_invoice_id": _real_marker(
                inbox_rows, "qoyod_invoice_id"
            ),
            "legacy_worker_visibility_reason": before_fix_reason,
            "worker_candidate": not has_qoyod_reference,
            "candidate_reason": candidate_reason,
            "trace_id": inbox_row.get("trace_id") if inbox_row else None,
            "inbox_row": inbox_row,
            "inbox_rows": inbox_rows,
            "unified_order": order,
            "qoyod_invoice": invoice,
        })
    rows.sort(
        key=lambda row: (row.get("order_date") or "", row["order_number"]),
        reverse=True,
    )

    by_reference = {row["order_number"]: row for row in rows}
    total_amount = round(sum(
        float(row.get("total_amount") or 0.0) for row in rows
    ), 2)
    status_counts = {
        status_key: sum(
            row.get("current_status_key") == status_key for row in rows
        )
        for status_key in ELIGIBLE_STATUS_KEYS
    }
    worker_candidate_status_counts = {
        status_key: sum(
            row.get("worker_candidate")
            and row.get("current_status_key") == status_key
            for row in rows
        )
        for status_key in ELIGIBLE_STATUS_KEYS
    }
    status_display_counts: dict[str, int] = {}
    worker_candidate_status_display_counts: dict[str, int] = {}
    for row in rows:
        display = str(
            row.get("current_status") or row.get("current_status_key") or ""
        ).strip()
        if not display:
            continue
        status_display_counts[display] = (
            status_display_counts.get(display, 0) + 1
        )
        if row.get("worker_candidate"):
            worker_candidate_status_display_counts[display] = (
                worker_candidate_status_display_counts.get(display, 0) + 1
            )
    return {
        "ok": True,
        "read_only": True,
        "captured_at": captured_at,
        "snapshot_fingerprint": candidate_snapshot_fingerprint(
            rows,
            orders_user_id=orders_user_id,
            from_date=date_range.from_date,
            to_date=date_range.to_date,
        ),
        "source_authority": "unified_orders",
        **scan_metadata,
        "match_contract": "unified_orders.order_number == qoyod_invoices.reference",
        "orders_user_id": str(orders_user_id),
        "markers_user_id": str(markers_user_id),
        **date_range.as_dict(),
        "eligible_references": eligible_refs,
        "sent_references": sent_refs,
        "unsent_references": unsent_refs,
        "qoyod_only_references": qoyod_only_refs,
        "by_reference": by_reference,
        "orders": rows,
        "counts": {
            "scanned_unified_orders": unified["scanned"],
            "eligible_unified_orders": len(eligible_refs),
            "exact_qoyod_reference_matches": len(sent_refs),
            "worker_candidates": len(unsent_refs),
            "missing_from_integration_inbox": sum(
                not row["in_integration_inbox"] for row in rows
            ),
            "qoyod_only_exact_references": len(qoyod_only_refs),
            "duplicate_qoyod_references": len(
                invoices["duplicate_references"]
            ),
        },
        "reference_hashes": {
            "eligible": reference_set_sha256(eligible_refs),
            "sent_exact": reference_set_sha256(sent_refs),
            "worker_candidates": reference_set_sha256(unsent_refs),
            "qoyod_only": reference_set_sha256(qoyod_only_refs),
        },
        "status_counts": status_counts,
        "worker_candidate_status_counts": worker_candidate_status_counts,
        "status_display_counts": status_display_counts,
        "worker_candidate_status_display_counts": (
            worker_candidate_status_display_counts
        ),
        "eligible_total_amount": total_amount,
        "unified_exclusions": unified["excluded"],
        "unified_excluded_statuses": unified["excluded_by_status"],
        "duplicate_qoyod_references": invoices["duplicate_references"],
        "unreferenced_qoyod_invoices": invoices["unreferenced"],
    }


def json_safe_audit(audit: dict[str, Any]) -> dict[str, Any]:
    """Drop internal Mongo rows/sets for a stable API and report payload."""
    safe_orders = []
    for row in audit.get("orders") or []:
        safe_orders.append({
            key: value for key, value in row.items()
            if key not in {
                "inbox_row", "inbox_rows", "unified_order", "qoyod_invoice"
            }
        })
    return {
        **{
            key: value for key, value in audit.items()
            if key not in {
                "eligible_references", "sent_references", "unsent_references",
                "qoyod_only_references", "by_reference", "orders",
                "duplicate_qoyod_references", "unreferenced_qoyod_invoices",
            }
        },
        "reference_sets": {
            "eligible": sorted(audit.get("eligible_references") or set()),
            "sent_exact": sorted(audit.get("sent_references") or set()),
            "worker_candidates": sorted(
                audit.get("unsent_references") or set()
            ),
            "qoyod_only": sorted(
                audit.get("qoyod_only_references") or set()
            ),
        },
        "orders": safe_orders,
        "duplicate_qoyod_references": {
            reference: [
                {
                    key: value for key, value in row.items()
                    if key != "raw_response"
                }
                for row in rows
            ]
            for reference, rows in (
                audit.get("duplicate_qoyod_references") or {}
            ).items()
        },
        "unreferenced_qoyod_invoice_count": len(
            audit.get("unreferenced_qoyod_invoices") or []
        ),
    }
