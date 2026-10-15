"""Append-only decision ledger for Snapchat campaign management.

The campaign-management collection is an operational workflow store: proposal
rows legitimately change as they move from preview to execution.  This module
turns those mutable rows (and provider-observed changes) into an immutable,
tenant-scoped history that can later be evaluated against business outcomes.

There are deliberately three entry kinds in one collection:

``change``
    What was attempted or observed, including the provider before/after facts.
``annotation``
    A human/agent note explaining additional context without rewriting history.
``evaluation``
    A later outcome assessment.  A new evaluation supersedes an older one only
    in the read model; the older entry remains untouched.

Nothing in this module writes to Snapchat, Salla, accounting, or Qoyod.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .snapchat_native_entities_sync import _safe_provider_value


DECISION_LEDGER_COLLECTION = "mezan_ad_decision_ledger_v1"
MANAGEMENT_PROPOSAL_COLLECTION = "mezan_snapchat_campaign_proposals_v1"
DECISION_LEDGER_SOURCE_MODE = "snapchat_decision_ledger_v1"
PROVIDER_OBSERVED_REASON = "السبب غير مسجل؛ رُصد التغيير من Snapchat"

EntryType = Literal["change", "annotation", "evaluation"]


class AdDecisionFieldDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    before: Any = None
    after: Any = None


class AdDecisionEvaluation(BaseModel):
    model_config = ConfigDict(extra="allow")

    evaluation_id: str
    outcome_status: str
    summary: str | None = None
    evidence: Any = None
    evaluated_at: str
    actor_id: str | None = None
    actor_kind: str
    source: str


class AdDecisionAnnotation(BaseModel):
    model_config = ConfigDict(extra="allow")

    annotation_id: str
    text: str
    annotated_at: str
    actor_id: str | None = None
    actor_kind: str
    source: str


class AdDecisionItem(BaseModel):
    """Public decision read model; never exposes tenant or Mongo internals."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    proposal_id: str | None = None
    reverses_decision_id: str | None = None
    provider: Literal["snapchat_ads"] = "snapchat_ads"
    account_id: str
    account_name: str | None = None
    entity: dict[str, Any]
    entity_type: str
    entity_id: str | None = None
    entity_name: str | None = None
    action: str
    execution_status: str
    outcome_status: str
    before: Any = None
    after: Any = None
    field_diffs: list[AdDecisionFieldDiff] = Field(default_factory=list)
    planned_changes: Any = None
    reason: str | None = None
    expected: Any = None
    evidence: Any = None
    baseline: Any = None
    baseline_windows: Any = None
    latest_evaluation: AdDecisionEvaluation | None = None
    evaluations: list[AdDecisionEvaluation] = Field(default_factory=list)
    annotations: list[AdDecisionAnnotation] = Field(default_factory=list)
    business_outcome: str
    source: str
    actor_id: str | None = None
    actor_kind: str
    effective_at: str
    recorded_at: str
    created_at: str
    content_hash: str


class AdDecisionAccountSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    account_name: str | None = None
    decisions: list[AdDecisionItem] = Field(default_factory=list)


class AdDecisionSummariesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["snapchat_ads"] = "snapchat_ads"
    limit_per_account: int
    accounts: list[AdDecisionAccountSummary] = Field(default_factory=list)


class AdDecisionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["snapchat_ads"] = "snapchat_ads"
    account_id: str
    page: int
    limit: int
    total: int
    pages: int
    items: list[AdDecisionItem] = Field(default_factory=list)


class AdDecisionDetailResponse(AdDecisionItem):
    model_config = ConfigDict(extra="forbid")

    change_history: list[AdDecisionItem] = Field(default_factory=list)


def _collection(db: Any, name: str) -> Any:
    try:
        return db[name]
    except (KeyError, TypeError, AttributeError):
        return getattr(db, name)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any = None) -> str:
    if value is None:
        parsed = _utcnow()
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    else:
        text = str(value or "").strip()
        if not text:
            parsed = _utcnow()
        else:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                # Provider timestamps are evidence.  If a provider emits an
                # unfamiliar format, keep the bounded value rather than claim
                # that it happened "now".
                return text[:160]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _safe(value: Any) -> Any:
    """Use the existing provider sanitizer and detach caller-owned objects."""

    return deepcopy(_safe_provider_value(value))


def _canonical_value(value: Any) -> Any:
    """Return a deterministic, JSON-safe value for hashing."""

    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) != "_id"
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _entry_content_hash(row: dict[str, Any]) -> str:
    return _digest(
        {key: value for key, value in row.items() if key not in {"_id", "content_hash"}}
    )


def _clean_text(value: Any, *, maximum: int, required: bool = False) -> str | None:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        if required:
            raise ValueError("text is required")
        return None
    return text[:maximum]


def _account_identity(account: Any) -> tuple[str, str | None]:
    if isinstance(account, dict):
        account_id = str(
            account.get("ad_account_id")
            or account.get("account_id")
            or account.get("external_account_id")
            or ""
        ).strip()
        account_name = _clean_text(
            account.get("display_name") or account.get("name"), maximum=240
        )
    else:
        account_id = str(account or "").strip()
        account_name = None
    if not account_id:
        raise ValueError("account_id is required")
    return account_id[:160], account_name


def _entity_id(before: Any, after: Any) -> str | None:
    for value in (after, before):
        if isinstance(value, dict):
            candidate = str(
                value.get("id")
                or value.get("external_id")
                or value.get("entity_id")
                or ""
            ).strip()
            if candidate:
                return candidate[:160]
    return None


def _field_diffs(
    before: Any,
    after: Any,
    *,
    fields: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """Compare provider facts without treating a missing snapshot as an outcome."""

    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    keys = set(fields or ()) if fields is not None else set(before) | set(after)
    output: list[dict[str, Any]] = []
    for key in sorted(str(item) for item in keys):
        before_present = key in before
        after_present = key in after
        old = before.get(key)
        new = after.get(key)
        if before_present == after_present and old == new:
            continue
        output.append(
            {
                "field": key[:240],
                "before": _safe(old) if before_present else None,
                "after": _safe(new) if after_present else None,
            }
        )
        if len(output) >= 200:
            break
    return output


def _operation_changes(operation: Any) -> dict[str, Any]:
    if not isinstance(operation, dict):
        return {}
    changes = operation.get("changes")
    if isinstance(changes, dict):
        safe = _safe(changes)
        return safe if isinstance(safe, dict) else {}
    body = operation.get("body")
    plural = str(operation.get("plural") or "").strip()
    if isinstance(body, dict) and plural:
        rows = body.get(plural)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            safe = _safe(rows[0])
            return safe if isinstance(safe, dict) else {}
    return {}


def _proposal_effective_at(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "unknown").strip().lower()
    candidates: tuple[str, ...]
    if status == "failed":
        candidates = ("failed_at", "execution_started_at", "created_at")
    elif status == "completed":
        candidates = ("executed_at", "execution_started_at", "created_at")
    elif status == "rolled_back":
        rollback = row.get("rollback") if isinstance(row.get("rollback"), dict) else {}
        rollback_at = (
            rollback.get("rolled_back_at") if isinstance(rollback, dict) else None
        )
        if rollback_at:
            return _iso(rollback_at)
        candidates = ("rollback_started_at", "executed_at", "created_at")
    elif status in {"approved", "executing"}:
        candidates = ("execution_started_at", "approved_at", "created_at")
    else:
        candidates = ("created_at",)
    for key in candidates:
        if row.get(key):
            return _iso(row[key])
    return _iso()


def _management_actor(row: dict[str, Any]) -> tuple[str | None, str]:
    actor_id = (
        str(
            row.get("rolled_back_by")
            or row.get("executed_by")
            or row.get("approved_by")
            or row.get("actor_id")
            or ""
        ).strip()
        or None
    )
    explicit_kind = str(row.get("actor_kind") or "").strip()
    # A proposal created by the governed Mezan control plane has a known actor
    # identity, but whether that identity is human or AI is not inferable from
    # old rows.  Keep the neutral, truthful kind unless explicitly recorded.
    return actor_id, explicit_kind or "mezan_management"


def _management_evidence(row: dict[str, Any]) -> dict[str, Any] | None:
    evidence: dict[str, Any] = {}
    verification = row.get("verification")
    failure = row.get("failure")
    if isinstance(verification, dict):
        evidence["verification"] = _safe(verification)
    if failure is not None:
        evidence["failure"] = _safe(failure)
    if isinstance(row.get("rollback"), dict):
        evidence["rollback"] = _safe(row.get("rollback"))
    if row.get("decision_evidence") is not None:
        evidence["decision_evidence"] = _safe(row.get("decision_evidence"))
    if row.get("parent_id") is not None:
        evidence["parent_id"] = _safe(row.get("parent_id"))
    if row.get("products") is not None:
        evidence["products"] = _safe(row.get("products"))
        evidence["product_link_state"] = _safe(
            row.get("product_link_state") or "not_supplied"
        )
    if row.get("trend_review") is not None:
        evidence["trend_review"] = _safe(row.get("trend_review"))
    trend_override_reason = _clean_text(row.get("trend_override_reason"), maximum=4000)
    if trend_override_reason:
        evidence["trend_override_reason"] = trend_override_reason
    for key in (
        "provider_write_reached",
        "provider_write_state",
        "provider_write_uncertain",
    ):
        if key in row:
            evidence[key] = _safe(row.get(key))
    return evidence or None


async def ensure_ad_decision_indexes(db: Any) -> None:
    ledger = _collection(db, DECISION_LEDGER_COLLECTION)
    await ledger.create_index(
        [("user_id", 1), ("ledger_entry_id", 1)],
        unique=True,
        name="ad_decision_ledger_entry_unique",
    )
    await ledger.create_index(
        [("user_id", 1), ("source_event_key", 1)],
        unique=True,
        name="ad_decision_source_event_unique",
    )
    await ledger.create_index(
        [
            ("user_id", 1),
            ("account_id", 1),
            ("entry_type", 1),
            ("effective_at", -1),
        ],
        name="ad_decision_account_timeline",
    )
    await ledger.create_index(
        [("user_id", 1), ("decision_id", 1), ("created_at", 1)],
        name="ad_decision_detail_timeline",
    )
    await ledger.create_index(
        [
            ("user_id", 1),
            ("decision_id", 1),
            ("entry_type", 1),
            ("content_hash", 1),
        ],
        name="ad_decision_content_lookup",
    )


async def _append_entry(
    db: Any,
    *,
    user_id: str,
    entry: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    tenant = str(user_id or "").strip()
    if not tenant:
        raise ValueError("user_id is required")
    source_event_key = str(entry.get("source_event_key") or "").strip()
    if not source_event_key:
        raise ValueError("source_event_key is required")
    ledger = _collection(db, DECISION_LEDGER_COLLECTION)
    existing = await ledger.find_one(
        {"user_id": tenant, "source_event_key": source_event_key},
        {"_id": 0},
    )
    if existing:
        return _safe(existing), False

    now_iso = _iso()
    row = {
        **_safe(entry),
        "ledger_entry_id": str(entry.get("ledger_entry_id") or uuid.uuid4()),
        "user_id": tenant,
        "source_event_key": source_event_key[:320],
        "source_mode": DECISION_LEDGER_SOURCE_MODE,
        "created_at": _iso(entry.get("created_at") or now_iso),
    }
    row.pop("content_hash", None)
    row["content_hash"] = _entry_content_hash(row)
    try:
        await ledger.insert_one(deepcopy(row))
    except Exception:
        # A unique-index race is an ordinary idempotent replay.  Re-raise every
        # other insertion error instead of silently losing a ledger event.
        raced = await ledger.find_one(
            {"user_id": tenant, "source_event_key": source_event_key},
            {"_id": 0},
        )
        if not raced:
            raise
        return _safe(raced), False
    return _safe(row), True


def _proposal_version_key(row: dict[str, Any], decision_id: str) -> str:
    version_facts = {
        "proposal_id": row.get("proposal_id"),
        "revision": row.get("revision"),
        "status": row.get("status"),
        "action": row.get("action"),
        "account_id": row.get("account_id"),
        "target_id": row.get("target_id"),
        "reason": row.get("reason"),
        "original_snapshot": row.get("original_snapshot"),
        "operation": row.get("operation"),
        "verification": row.get("verification"),
        "failure": row.get("failure"),
        "provider_write_state": row.get("provider_write_state"),
        "provider_write_reached": row.get("provider_write_reached"),
        "baseline": row.get("baseline"),
        "expected": row.get("expected"),
        "decision_evidence": row.get("decision_evidence"),
        "products": row.get("products"),
        "product_link_state": row.get("product_link_state"),
        "trend_review": row.get("trend_review"),
        "trend_override_reason": row.get("trend_override_reason"),
        "failed_at": row.get("failed_at"),
        "executed_at": row.get("executed_at"),
        "rolled_back": row.get("rollback"),
    }
    return f"snapchat-management:{decision_id}:{_digest(_safe(version_facts))[:24]}"


def _rollback_decision_id(proposal_id: str) -> str:
    """Keep a rollback separate from the forward decision and its evaluations."""
    return f"snap-rollback-{_digest(proposal_id)[:32]}"


async def record_management_decision(
    db: Any,
    user_id: str,
    proposal_row: dict[str, Any],
    baseline: Any = None,
    expected: Any = None,
    *,
    execution_status: str | None = None,
) -> dict[str, Any]:
    """Append the current immutable version of one management proposal."""

    if not isinstance(proposal_row, dict):
        raise ValueError("proposal_row must be an object")
    proposal_row = deepcopy(proposal_row)
    if execution_status is not None:
        proposal_row["status"] = _clean_text(
            execution_status, maximum=80, required=True
        )
    proposal_id = str(
        proposal_row.get("proposal_id") or proposal_row.get("idempotency_key") or ""
    ).strip()
    if not proposal_id:
        proposal_id = f"legacy-{_digest(_safe(proposal_row))[:32]}"
    status = str(proposal_row.get("status") or "unknown").strip().lower() or "unknown"
    rollback = (
        proposal_row.get("rollback")
        if isinstance(proposal_row.get("rollback"), dict)
        else {}
    )
    forward_decision_id = proposal_id[:200]
    if status == "rolled_back":
        # A terminal rollback row still contains all verified forward facts.
        # Backfill that original decision first so a legacy reconciliation does
        # not lose it, while keeping its already-recorded evaluations attached
        # only to the forward decision.
        forward_row = deepcopy(proposal_row)
        forward_row["status"] = "completed"
        forward_row.pop("rollback", None)
        forward_row.pop("rolled_back_by", None)
        await record_management_decision(
            db,
            user_id,
            forward_row,
            baseline=baseline,
            expected=expected,
        )
        decision_id = _rollback_decision_id(proposal_id)
    else:
        decision_id = forward_decision_id
    account_id, account_name = _account_identity(
        {
            "ad_account_id": proposal_row.get("account_id"),
            "display_name": proposal_row.get("account_name"),
        }
    )
    operation = proposal_row.get("operation")
    operation = operation if isinstance(operation, dict) else {}
    action = (
        str(proposal_row.get("action") or operation.get("action") or "unknown").strip()[
            :160
        ]
        or "unknown"
    )
    entity_type = str(
        operation.get("entity_type") or action.split(".", 1)[0] or "unknown"
    ).strip()[:80]
    planned_changes = _operation_changes(operation)
    original = proposal_row.get("original_snapshot")
    before = _safe(original) if isinstance(original, dict) else None
    verification = proposal_row.get("verification")
    provider_snapshot = (
        verification.get("provider_snapshot")
        if isinstance(verification, dict)
        else None
    )
    after = _safe(provider_snapshot) if isinstance(provider_snapshot, dict) else None
    actor_id, actor_kind = _management_actor(proposal_row)
    reverses_decision_id = None
    if status == "rolled_back":
        reverses_decision_id = forward_decision_id
        action = f"{action}.rollback"
        rollback_before = rollback.get("before")
        rollback_after = rollback.get("after")
        before = (
            _safe(rollback_before)
            if isinstance(rollback_before, dict)
            else after
        )
        after = (
            _safe(rollback_after)
            if isinstance(rollback_after, dict)
            else (_safe(original) if isinstance(original, dict) else None)
        )
        planned_changes = (
            {
                key: _safe(after.get(key))
                for key in planned_changes
                if isinstance(after, dict) and key in after
            }
            or None
        )
    entity_id = (
        str(
            proposal_row.get("provider_entity_id")
            or proposal_row.get("target_id")
            or (after or {}).get("id")
            or ""
        ).strip()
        or None
    )
    baseline_value = baseline if baseline is not None else proposal_row.get("baseline")
    expected_value = expected if expected is not None else proposal_row.get("expected")
    reason = _clean_text(proposal_row.get("reason"), maximum=2000)
    execution_status_value = status
    if status == "rolled_back":
        baseline_value = proposal_row.get("rollback_baseline")
        expected_value = rollback.get("expected")
        reason = _clean_text(rollback.get("reason"), maximum=2000)
        # The rollback itself was provider-verified and is therefore a new
        # effective decision eligible for 24h/72h/7d outcome measurement.
        execution_status_value = "completed"
    entry = {
        "entry_type": "change",
        "decision_id": decision_id,
        "proposal_id": proposal_id[:200],
        "reverses_decision_id": reverses_decision_id,
        "account_id": account_id,
        "account_name": account_name,
        "provider": "snapchat_ads",
        "entity_type": entity_type,
        "entity_id": entity_id[:160] if entity_id else None,
        "action": action,
        "execution_status": execution_status_value,
        "outcome_status": "not_evaluated",
        "before": before,
        "after": after,
        "field_diffs": _field_diffs(
            before,
            after,
            fields=set(planned_changes) if planned_changes else None,
        ),
        "planned_changes": planned_changes or None,
        # Missing historical reasons stay missing.  In particular, do not use
        # operation summaries as a made-up reason.
        "reason": reason,
        "expected": _safe(expected_value) if expected_value is not None else None,
        "evidence": _management_evidence(proposal_row),
        "baseline": _safe(baseline_value) if baseline_value is not None else None,
        "source": "mezan_snapchat_management",
        "actor_id": actor_id,
        "actor_kind": actor_kind,
        "effective_at": _proposal_effective_at(proposal_row),
        "source_event_key": _proposal_version_key(proposal_row, decision_id),
    }
    await _append_entry(db, user_id=user_id, entry=entry)
    detail = await get_ad_decision(db, user_id, decision_id)
    if detail is None:  # pragma: no cover - insertion and scoped read are atomic enough
        raise RuntimeError("decision ledger insert was not readable")
    return detail


async def record_provider_observed_decision(
    db: Any,
    user_id: str,
    account: Any = None,
    entity_type: str = "unknown",
    entity_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    source_event_key: str | None = None,
    *,
    account_id: str | None = None,
    before_snapshot: dict[str, Any] | None = None,
    after_snapshot: dict[str, Any] | None = None,
    observed_at: Any = None,
    provider_updated_at: Any = None,
    changed_fields: list[str] | None = None,
    matched_proposal_id: str | None = None,
) -> dict[str, Any]:
    """Record a provider-side change whose actor/reason Mezan does not know."""

    before = before_snapshot if before_snapshot is not None else before
    after = after_snapshot if after_snapshot is not None else after
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("before and after provider snapshots are required")
    resolved_account_id, account_name = _account_identity(
        account if account is not None else account_id
    )
    safe_before = _safe(before)
    safe_after = _safe(after)
    entity = str(entity_id or _entity_id(safe_before, safe_after) or "").strip() or None
    occurrence_value = provider_updated_at or observed_at
    occurrence_at = _iso(occurrence_value) if occurrence_value else None
    stable_change = {
        "account_id": resolved_account_id,
        "entity_type": entity_type,
        "entity_id": entity,
        "before": safe_before,
        "after": safe_after,
        "changed_fields": sorted(set(changed_fields or [])),
        "matched_proposal_id": matched_proposal_id,
        # The same transition may legitimately happen again after an intervening
        # change (ACTIVE -> PAUSED -> ACTIVE -> PAUSED).  Its provider timestamp,
        # or the preserved observation timestamp when absent, identifies that
        # occurrence while keeping retries of the same occurrence idempotent.
        "occurrence_at": occurrence_at,
    }
    stable_fingerprint = _digest(stable_change)
    external_key = str(source_event_key or stable_fingerprint).strip()
    stable_identity = (
        f"{resolved_account_id}|{entity_type}|{entity or ''}|{external_key}"
    )
    decision_id = f"snap-observed-{_digest(stable_identity)[:32]}"
    effective_at = (
        provider_updated_at
        or safe_after.get("updated_at")
        or safe_after.get("last_observed_at")
        or safe_before.get("updated_at")
        or observed_at
        or _iso()
    )
    if safe_after.get("entity_created") is True and safe_before.get(
        "entity_created"
    ) is False:
        observed_action = "provider.observed_create"
    elif safe_after.get("deleted") is True and safe_before.get("deleted") is not True:
        observed_action = "provider.observed_delete"
    else:
        observed_action = "provider.observed_update"
    entry = {
        "entry_type": "change",
        "decision_id": decision_id,
        "proposal_id": None,
        "account_id": resolved_account_id,
        "account_name": account_name,
        "provider": "snapchat_ads",
        "entity_type": str(entity_type or "unknown").strip()[:80] or "unknown",
        "entity_id": entity,
        "action": observed_action,
        "execution_status": "observed",
        "outcome_status": "not_evaluated",
        "before": safe_before,
        "after": safe_after,
        "field_diffs": _field_diffs(
            safe_before,
            safe_after,
            fields=set(changed_fields) if changed_fields else None,
        ),
        "planned_changes": None,
        "reason": PROVIDER_OBSERVED_REASON,
        "expected": None,
        "evidence": {
            "provider_observation": True,
            "changed_fields": sorted(set(changed_fields or [])),
            "provider_updated_at": _safe(provider_updated_at),
            "observed_at": _safe(observed_at),
            "matched_proposal_id": matched_proposal_id,
            "detection_coverage": {
                "creates": "after_versioned_catalog_baseline",
                "updates": "monitored_provider_control_fields",
                "deletes": "explicit_deleted_or_status_signal_only",
                "absence_from_catalog_is_not_assumed_deleted": True,
            },
        },
        "baseline": None,
        "source": "snapchat_provider_observed",
        "actor_id": None,
        "actor_kind": "unknown_external",
        "effective_at": _iso(effective_at),
        "source_event_key": f"snapchat-provider:{external_key}"[:320],
    }
    await _append_entry(db, user_id=user_id, entry=entry)
    detail = await get_ad_decision(db, user_id, decision_id)
    if detail is None:  # pragma: no cover
        raise RuntimeError("decision ledger insert was not readable")
    return detail


async def _cursor_rows(
    cursor: Any, *, length: int | None = None
) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        try:
            rows = await cursor.to_list(length=length)
        except TypeError:
            rows = await cursor.to_list(length or 100_000)
        return [_safe(row) for row in rows]
    if hasattr(cursor, "__aiter__"):
        output: list[dict[str, Any]] = []
        async for row in cursor:
            output.append(_safe(row))
            if length is not None and len(output) >= length:
                break
        return output
    rows = list(cursor or [])
    return [_safe(row) for row in (rows[:length] if length is not None else rows)]


def _matches_query(row: dict[str, Any], query: dict[str, Any]) -> bool:
    """Small fallback for the repository's intentionally minimal async mocks."""

    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


async def _find_rows(
    collection: Any,
    query: dict[str, Any],
    *,
    projection: dict[str, int] | None = None,
    sort: list[tuple[str, int]] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    finder = getattr(collection, "find", None)
    if not callable(finder):
        stored_rows = getattr(collection, "rows", None)
        if isinstance(stored_rows, list):
            rows = [_safe(row) for row in stored_rows if _matches_query(row, query)]
        else:
            row = await collection.find_one(query, projection or {"_id": 0})
            rows = [_safe(row)] if row else []
        if sort:
            for field, direction in reversed(sort):
                rows.sort(
                    key=lambda item: str(item.get(field) or ""),
                    reverse=direction < 0,
                )
        return rows[:limit] if limit is not None else rows

    cursor = finder(query, projection or {"_id": 0})
    if isinstance(cursor, list):
        rows = [_safe(row) for row in cursor]
        if sort:
            for field, direction in reversed(sort):
                rows.sort(
                    key=lambda item: str(item.get(field) or ""),
                    reverse=direction < 0,
                )
        return rows[:limit] if limit is not None else rows
    if sort and hasattr(cursor, "sort"):
        try:
            cursor = cursor.sort(sort)
        except TypeError:
            if len(sort) == 1:
                cursor = cursor.sort(sort[0][0], sort[0][1])
    if limit is not None and hasattr(cursor, "limit"):
        cursor = cursor.limit(limit)
    rows = await _cursor_rows(cursor, length=limit)
    if sort and not hasattr(cursor, "sort"):
        # In-memory fallbacks used by focused tests still receive deterministic
        # output.  Production Motor cursors sort server-side.
        for field, direction in reversed(sort):
            rows.sort(key=lambda row: str(row.get(field) or ""), reverse=direction < 0)
    return rows


async def reconcile_snapchat_management_decisions(
    db: Any,
    user_id: str,
    limit: int = 1000,
) -> dict[str, Any]:
    """Idempotently backfill current proposal versions into the ledger."""

    bounded_limit = max(1, min(int(limit or 1000), 10_000))
    tenant = str(user_id or "").strip()
    if not tenant:
        raise ValueError("user_id is required")
    rows = await _find_rows(
        _collection(db, MANAGEMENT_PROPOSAL_COLLECTION),
        {"user_id": tenant},
        projection={"_id": 0},
        sort=[("created_at", -1)],
        limit=bounded_limit,
    )
    ledger = _collection(db, DECISION_LEDGER_COLLECTION)
    inserted = 0
    unchanged = 0
    for row in rows:
        if str(row.get("status") or "").strip().lower() not in {
            "completed",
            "failed",
            "rolled_back",
        }:
            continue
        proposal_id = str(
            row.get("proposal_id") or row.get("idempotency_key") or ""
        ).strip()
        decision_id = proposal_id or f"legacy-{_digest(_safe(row))[:32]}"
        if str(row.get("status") or "").strip().lower() == "rolled_back":
            decision_id = _rollback_decision_id(decision_id)
        source_key = _proposal_version_key(row, decision_id[:200])
        existing = await ledger.find_one(
            {"user_id": tenant, "source_event_key": source_key},
            {"_id": 0},
        )
        if existing:
            unchanged += 1
            continue
        await record_management_decision(db, tenant, row)
        inserted += 1
    return {
        "provider": "snapchat_ads",
        "scanned": len(rows),
        "inserted": inserted,
        "unchanged": unchanged,
        "limit": bounded_limit,
    }


def _event_order(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("effective_at") or ""),
        str(row.get("created_at") or ""),
        str(row.get("ledger_entry_id") or ""),
    )


def _evaluation_public(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("evaluation") if isinstance(row.get("evaluation"), dict) else {}
    reserved = {
        "evaluation_id": str(row.get("ledger_entry_id") or ""),
        "outcome_status": str(
            row.get("outcome_status")
            or payload.get("outcome_status")
            or "not_evaluated"
        ),
        "summary": _clean_text(payload.get("summary"), maximum=4000),
        "evidence": _safe(payload.get("evidence")) if "evidence" in payload else None,
        "evaluated_at": _iso(row.get("effective_at") or row.get("created_at")),
        "actor_id": row.get("actor_id"),
        "actor_kind": str(row.get("actor_kind") or "unknown"),
        "source": str(row.get("source") or "mezan_decision_evaluation"),
    }
    extras = {
        key: _safe(value)
        for key, value in payload.items()
        if key not in {"outcome_status", "summary", "evidence"}
    }
    return {**extras, **reserved}


def _annotation_public(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("annotation") if isinstance(row.get("annotation"), dict) else {}
    extras = {key: _safe(value) for key, value in payload.items() if key != "text"}
    return {
        **extras,
        "annotation_id": str(row.get("ledger_entry_id") or ""),
        "text": _clean_text(payload.get("text"), maximum=4000, required=True),
        "annotated_at": _iso(row.get("effective_at") or row.get("created_at")),
        "actor_id": row.get("actor_id"),
        "actor_kind": str(row.get("actor_kind") or "unknown"),
        "source": str(row.get("source") or "mezan_decision_annotation"),
    }


def _change_public(
    row: dict[str, Any],
    latest_evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    outcome = str(
        (latest_evaluation or {}).get("outcome_status")
        or row.get("outcome_status")
        or "not_evaluated"
    )
    baseline = _safe(row.get("baseline"))
    baseline_windows = (
        _safe(baseline.get("windows"))
        if isinstance(baseline, dict) and "windows" in baseline
        else None
    )
    created_at = _iso(row.get("created_at"))
    entity_type = str(row.get("entity_type") or "unknown")
    entity_id = row.get("entity_id")
    return {
        "decision_id": str(row.get("decision_id") or ""),
        "proposal_id": row.get("proposal_id"),
        "reverses_decision_id": row.get("reverses_decision_id"),
        "provider": "snapchat_ads",
        "account_id": str(row.get("account_id") or ""),
        "account_name": row.get("account_name"),
        "entity": {"type": entity_type, "id": entity_id},
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_name": (
            row.get("entity_name")
            or (row.get("after") or {}).get("name")
            or (row.get("after") or {}).get("display_name")
            or (row.get("before") or {}).get("name")
            or (row.get("before") or {}).get("display_name")
        ),
        "action": str(row.get("action") or "unknown"),
        "execution_status": str(row.get("execution_status") or "unknown"),
        "outcome_status": outcome,
        "before": _safe(row.get("before")),
        "after": _safe(row.get("after")),
        "field_diffs": _safe(row.get("field_diffs") or []),
        "planned_changes": _safe(row.get("planned_changes")),
        "reason": row.get("reason"),
        "expected": _safe(row.get("expected")),
        "evidence": _safe(row.get("evidence")),
        "baseline": baseline,
        "baseline_windows": baseline_windows,
        "latest_evaluation": latest_evaluation,
        "evaluations": [latest_evaluation] if latest_evaluation else [],
        "annotations": [],
        "business_outcome": outcome,
        "source": str(row.get("source") or "unknown"),
        "actor_id": row.get("actor_id"),
        "actor_kind": str(row.get("actor_kind") or "unknown"),
        "effective_at": _iso(row.get("effective_at") or row.get("created_at")),
        "recorded_at": created_at,
        "created_at": created_at,
        "content_hash": str(row.get("content_hash") or ""),
    }


async def _tenant_entries(db: Any, user_id: str) -> list[dict[str, Any]]:
    tenant = str(user_id or "").strip()
    if not tenant:
        raise ValueError("user_id is required")
    return await _find_rows(
        _collection(db, DECISION_LEDGER_COLLECTION),
        {"user_id": tenant},
        projection={"_id": 0},
    )


def _aggregate_decisions(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in entries:
        decision_id = str(row.get("decision_id") or "")
        if decision_id:
            grouped.setdefault(decision_id, []).append(row)
    output: dict[str, dict[str, Any]] = {}
    for decision_id, decision_entries in grouped.items():
        changes = sorted(
            (row for row in decision_entries if row.get("entry_type") == "change"),
            key=_event_order,
        )
        if not changes:
            continue
        evaluations = sorted(
            (row for row in decision_entries if row.get("entry_type") == "evaluation"),
            key=_event_order,
        )
        evaluation = _evaluation_public(evaluations[-1]) if evaluations else None
        annotations = sorted(
            (row for row in decision_entries if row.get("entry_type") == "annotation"),
            key=_event_order,
        )
        item = _change_public(changes[-1], evaluation)
        item["annotations"] = [_annotation_public(row) for row in annotations]
        output[decision_id] = item
    return output


async def list_account_decision_summaries(
    db: Any,
    user_id: str,
    limit_per_account: int = 5,
) -> dict[str, Any]:
    bounded = max(1, min(int(limit_per_account or 5), 25))
    decisions = _aggregate_decisions(await _tenant_entries(db, user_id))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in decisions.values():
        grouped.setdefault(item["account_id"], []).append(item)
    accounts: list[dict[str, Any]] = []
    for account_id in sorted(grouped):
        items = sorted(
            grouped[account_id],
            key=lambda item: (
                item["effective_at"],
                item["recorded_at"],
                item["decision_id"],
            ),
            reverse=True,
        )[:bounded]
        account_name = next(
            (item.get("account_name") for item in items if item.get("account_name")),
            None,
        )
        accounts.append(
            {
                "account_id": account_id,
                "account_name": account_name,
                "decisions": items,
            }
        )
    response = {
        "provider": "snapchat_ads",
        "limit_per_account": bounded,
        "accounts": accounts,
    }
    return AdDecisionSummariesResponse.model_validate(response).model_dump(mode="json")


async def list_ad_decisions(
    db: Any,
    user_id: str,
    account_id: str,
    page: int,
    limit: int,
) -> dict[str, Any]:
    account = str(account_id or "").strip()
    if not account:
        raise ValueError("account_id is required")
    page_number = max(1, int(page or 1))
    page_limit = max(1, min(int(limit or 20), 100))
    decisions = [
        item
        for item in _aggregate_decisions(await _tenant_entries(db, user_id)).values()
        if item.get("account_id") == account
    ]
    decisions.sort(
        key=lambda item: (
            item["effective_at"],
            item["recorded_at"],
            item["decision_id"],
        ),
        reverse=True,
    )
    total = len(decisions)
    pages = math.ceil(total / page_limit) if total else 0
    offset = (page_number - 1) * page_limit
    response = {
        "provider": "snapchat_ads",
        "account_id": account,
        "page": page_number,
        "limit": page_limit,
        "total": total,
        "pages": pages,
        "items": decisions[offset : offset + page_limit],
    }
    return AdDecisionListResponse.model_validate(response).model_dump(mode="json")


async def get_ad_decision(
    db: Any,
    user_id: str,
    decision_id: str,
) -> dict[str, Any] | None:
    tenant = str(user_id or "").strip()
    identity = str(decision_id or "").strip()
    if not tenant or not identity:
        return None
    rows = await _find_rows(
        _collection(db, DECISION_LEDGER_COLLECTION),
        {"user_id": tenant, "decision_id": identity},
        projection={"_id": 0},
    )
    changes = sorted(
        (row for row in rows if row.get("entry_type") == "change"),
        key=_event_order,
    )
    if not changes:
        return None
    evaluations = sorted(
        (row for row in rows if row.get("entry_type") == "evaluation"),
        key=_event_order,
    )
    annotations = sorted(
        (row for row in rows if row.get("entry_type") == "annotation"),
        key=_event_order,
    )
    public_evaluations = [_evaluation_public(row) for row in evaluations]
    latest = public_evaluations[-1] if public_evaluations else None
    current = _change_public(changes[-1], latest)
    response = {
        **current,
        "annotations": [_annotation_public(row) for row in annotations],
        "evaluations": public_evaluations,
        "change_history": [_change_public(row, None) for row in changes],
    }
    return AdDecisionDetailResponse.model_validate(response).model_dump(mode="json")


async def add_decision_annotation(
    db: Any,
    user_id: str,
    decision_id: str,
    annotation: str | dict[str, Any],
    actor_id: str | None = None,
    actor_kind: str = "mezan_user",
    *,
    source: str = "mezan_decision_annotation",
    source_event_key: str | None = None,
    annotated_at: Any = None,
) -> dict[str, Any]:
    existing = await get_ad_decision(db, user_id, decision_id)
    if existing is None:
        raise ValueError("decision not found")
    if isinstance(annotation, dict):
        payload = _safe(annotation)
        text = _clean_text(payload.get("text"), maximum=4000, required=True)
        payload["text"] = text
    else:
        payload = {"text": _clean_text(annotation, maximum=4000, required=True)}
    effective = _iso(annotated_at)
    key = source_event_key or f"annotation:{decision_id}:{uuid.uuid4()}"
    entry = {
        "entry_type": "annotation",
        "decision_id": str(decision_id),
        "account_id": existing["account_id"],
        "provider": "snapchat_ads",
        "annotation": payload,
        "source": _clean_text(source, maximum=160, required=True),
        "actor_id": _clean_text(actor_id, maximum=200),
        "actor_kind": _clean_text(actor_kind, maximum=80, required=True),
        "effective_at": effective,
        "source_event_key": str(key)[:320],
    }
    await _append_entry(db, user_id=user_id, entry=entry)
    detail = await get_ad_decision(db, user_id, decision_id)
    if detail is None:  # pragma: no cover
        raise RuntimeError("annotation decision disappeared")
    return detail


async def append_decision_evaluation(
    db: Any,
    user_id: str,
    decision_id: str,
    evaluation: dict[str, Any],
    actor_id: str | None = None,
    actor_kind: str = "mezan_ai",
    *,
    outcome_status: str | None = None,
    source: str = "mezan_decision_evaluation",
    source_event_key: str | None = None,
    evaluated_at: Any = None,
) -> dict[str, Any]:
    existing = await get_ad_decision(db, user_id, decision_id)
    if existing is None:
        raise ValueError("decision not found")
    if not isinstance(evaluation, dict):
        raise ValueError("evaluation must be an object")
    payload = _safe(evaluation)
    outcome = _clean_text(
        outcome_status or payload.get("outcome_status"),
        maximum=80,
        required=True,
    )
    payload["outcome_status"] = outcome
    if "summary" in payload:
        payload["summary"] = _clean_text(payload.get("summary"), maximum=4000)
    effective = _iso(evaluated_at or payload.get("evaluated_at"))
    key = source_event_key or f"evaluation:{decision_id}:{uuid.uuid4()}"
    entry = {
        "entry_type": "evaluation",
        "decision_id": str(decision_id),
        "account_id": existing["account_id"],
        "provider": "snapchat_ads",
        "outcome_status": outcome,
        "evaluation": payload,
        "source": _clean_text(source, maximum=160, required=True),
        "actor_id": _clean_text(actor_id, maximum=200),
        "actor_kind": _clean_text(actor_kind, maximum=80, required=True),
        "effective_at": effective,
        "source_event_key": str(key)[:320],
    }
    await _append_entry(db, user_id=user_id, entry=entry)
    detail = await get_ad_decision(db, user_id, decision_id)
    if detail is None:  # pragma: no cover
        raise RuntimeError("evaluated decision disappeared")
    return detail


__all__ = [
    "DECISION_LEDGER_COLLECTION",
    "MANAGEMENT_PROPOSAL_COLLECTION",
    "PROVIDER_OBSERVED_REASON",
    "AdDecisionFieldDiff",
    "AdDecisionEvaluation",
    "AdDecisionAnnotation",
    "AdDecisionItem",
    "AdDecisionAccountSummary",
    "AdDecisionSummariesResponse",
    "AdDecisionListResponse",
    "AdDecisionDetailResponse",
    "ensure_ad_decision_indexes",
    "reconcile_snapchat_management_decisions",
    "record_management_decision",
    "record_provider_observed_decision",
    "list_account_decision_summaries",
    "list_ad_decisions",
    "get_ad_decision",
    "add_decision_annotation",
    "append_decision_evaluation",
]
