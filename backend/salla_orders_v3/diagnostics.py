"""Owner-bound parity-run evidence and cutover gates for Salla Orders V3."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .config import (
    JOBS_COLLECTION,
    PARITY_AUDITS_COLLECTION,
    PARITY_EVIDENCE_COLLECTION,
    PARITY_EVIDENCE_FUTURE_SKEW_SECONDS,
    PARITY_EVIDENCE_MAX_AGE_SECONDS,
    PARITY_RUNS_COLLECTION,
    PARITY_RUN_TTL_SECONDS,
    shadow_collection,
)
from .parity import (
    compare_attribution_parity,
    compare_fulfillment_parity,
    compare_qoyod_parity,
)


REQUIRED_SCOPE = "orders.read"
ACCEPTED_ORDER_READ_SCOPES = frozenset({"orders.read", "orders.read_write"})
REQUIRED_REGRESSION_KEYS = frozenset({
    "order_review",
    "fulfillment",
    "qoyod",
    "snapchat_attribution",
    "dashboard_order_totals",
})
_HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")
_AUDIT_SEAL = object()
_EVIDENCE_SEAL = object()
_RUN_SEAL = object()
_PARITY_ARTIFACT_KEYS = frozenset({
    "legacy_order",
    "v3_order",
    "legacy_qoyod_dry_run",
    "v3_qoyod_dry_run",
    "legacy_attribution_rows",
    "v3_attribution_rows",
    "regression_results",
})
_CALLER_CONTEXT_KEYS = frozenset({
    "evidence_head_sha",
    "evidence_run_id",
    "evidence_owner_id",
    "evidence_observed_at",
})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_run_id() -> str:
    return str(uuid.uuid4())


def _trusted_candidate_head_sha() -> str:
    """Resolve the already-validated runtime identity inside the backend."""
    try:
        from release_identity import release_health_payload
        from startup_guard import verified_release_key
    except ImportError:  # pragma: no cover - package-style runtime import
        from ..release_identity import release_health_payload
        from ..startup_guard import verified_release_key

    candidate_head_sha = verified_release_key(release_health_payload())
    if not _HEAD_SHA.fullmatch(candidate_head_sha):
        raise RuntimeError("Salla Orders V3 verified candidate HEAD is unavailable")
    return candidate_head_sha


@dataclass(frozen=True, init=False)
class _PersistedParityRun:
    candidate_head_sha: str
    run_id: str
    owner_id: str
    created_by: str
    created_at: datetime
    expires_at: datetime

    def __init__(
        self,
        *,
        candidate_head_sha: str,
        run_id: str,
        owner_id: str,
        created_by: str,
        created_at: datetime,
        expires_at: datetime,
        _seal: object,
    ) -> None:
        if _seal is not _RUN_SEAL:
            raise TypeError("parity context must come from a persisted parity run")
        for key, value in {
            "candidate_head_sha": candidate_head_sha,
            "run_id": run_id,
            "owner_id": owner_id,
            "created_by": created_by,
            "created_at": created_at,
            "expires_at": expires_at,
        }.items():
            object.__setattr__(self, key, value)


@dataclass(frozen=True, init=False)
class _PersistedParityEvidence:
    artifact_id: str
    artifact_digest: str
    artifact: dict[str, Any]
    candidate_head_sha: str
    run_id: str
    owner_id: str
    created_by: str
    observed_at: datetime
    expires_at: datetime

    def __init__(
        self,
        *,
        artifact_id: str,
        artifact_digest: str,
        artifact: dict[str, Any],
        candidate_head_sha: str,
        run_id: str,
        owner_id: str,
        created_by: str,
        observed_at: datetime,
        expires_at: datetime,
        _seal: object,
    ) -> None:
        if _seal is not _EVIDENCE_SEAL:
            raise TypeError("parity evidence must come from persisted evidence")
        for key, value in {
            "artifact_id": artifact_id,
            "artifact_digest": artifact_digest,
            "artifact": deepcopy(artifact),
            "candidate_head_sha": candidate_head_sha,
            "run_id": run_id,
            "owner_id": owner_id,
            "created_by": created_by,
            "observed_at": observed_at,
            "expires_at": expires_at,
        }.items():
            object.__setattr__(self, key, value)


@dataclass(frozen=True, init=False)
class _PersistedNotApplicable:
    gate: str
    owner_id: str
    audit_id: str
    recorded_by: str
    reason: str
    audited_at: datetime
    evidence_head_sha: str
    evidence_run_id: str
    order_identity: tuple[str, str, str]

    def __init__(
        self,
        *,
        gate: str,
        owner_id: str,
        audit_id: str,
        recorded_by: str,
        reason: str,
        audited_at: datetime,
        evidence_head_sha: str,
        evidence_run_id: str,
        order_identity: tuple[str, str, str],
        _seal: object,
    ) -> None:
        if _seal is not _AUDIT_SEAL:
            raise TypeError("not-applicable evidence must come from a persisted audit")
        for key, value in {
            "gate": gate,
            "owner_id": owner_id,
            "audit_id": audit_id,
            "recorded_by": recorded_by,
            "reason": reason,
            "audited_at": audited_at,
            "evidence_head_sha": evidence_head_sha,
            "evidence_run_id": evidence_run_id,
            "order_identity": order_identity,
        }.items():
            object.__setattr__(self, key, value)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif value not in (None, ""):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _persisted_timestamp(value: Any) -> Optional[datetime]:
    """Decode a Mongo date while keeping caller-supplied naive values invalid.

    PyMongo's default BSON decoder returns UTC instants as naive ``datetime``
    objects.  Only values crossing this persisted-storage boundary receive the
    BSON-defined UTC interpretation; timestamps embedded in parity artifacts
    still go through :func:`_timestamp` and fail closed when naive.
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return _timestamp(value)


def _bson_utc_datetime(value: Any) -> datetime:
    """Normalize an aware instant to Mongo BSON's millisecond precision."""
    parsed = _timestamp(value)
    if parsed is None:
        raise ValueError("Salla Orders V3 timestamp must be timezone-aware")
    return parsed.replace(microsecond=(parsed.microsecond // 1000) * 1000)


def _runtime_head_matches(candidate_head_sha: str) -> bool:
    try:
        runtime_head = _trusted_candidate_head_sha()
    except (ImportError, RuntimeError, TypeError, ValueError):
        return False
    return bool(
        type(runtime_head) is str
        and _HEAD_SHA.fullmatch(runtime_head)
        and runtime_head == candidate_head_sha
    )


def _parity_run_valid(value: Any, *, evaluated_at: datetime) -> bool:
    if not isinstance(value, _PersistedParityRun):
        raise TypeError("parity context must come from a persisted parity run")
    reference = _timestamp(evaluated_at)
    created_at = _timestamp(value.created_at)
    expires_at = _timestamp(value.expires_at)
    if reference is None or created_at is None or expires_at is None:
        return False
    return bool(
        _HEAD_SHA.fullmatch(value.candidate_head_sha)
        and _text(value.run_id)
        and _text(value.owner_id)
        and _text(value.created_by) == value.owner_id
        and created_at
        <= reference + timedelta(seconds=PARITY_EVIDENCE_FUTURE_SKEW_SECONDS)
        and expires_at == created_at + timedelta(seconds=PARITY_RUN_TTL_SECONDS)
        and reference < expires_at
    )


async def read_parity_run_context(
    db: Any,
    *,
    authenticated_user_id: str,
    user_id: str,
    run_id: str,
    owner_authorized: bool,
) -> _PersistedParityRun:
    """Load one owner-bound context; its head and clock are not caller inputs."""
    actor = _text(authenticated_user_id)
    owner = _text(user_id)
    normalized_run_id = _text(run_id)
    if owner_authorized is not True or not actor or actor != owner:
        raise PermissionError("Salla Orders V3 parity runs are owner-only")
    if not normalized_run_id:
        raise ValueError("Salla Orders V3 parity run id is required")
    row = await shadow_collection(db, PARITY_RUNS_COLLECTION).find_one(
        {"_id": f"parity-run:{normalized_run_id}"},
        {"_id": 0},
    )
    created_at = _persisted_timestamp((row or {}).get("created_at"))
    expires_at = _persisted_timestamp((row or {}).get("expires_at"))
    valid = bool(
        isinstance(row, dict)
        and row.get("record_type") == "salla_orders_v3_parity_run"
        and row.get("status") == "active"
        and row.get("trusted_context") is True
        and _text(row.get("run_id")) == normalized_run_id
        and _text(row.get("owner_id")) == owner
        and _text(row.get("created_by")) == actor
        and type(row.get("candidate_head_sha")) is str
        and bool(_HEAD_SHA.fullmatch(row["candidate_head_sha"]))
        and created_at is not None
        and expires_at is not None
    )
    if not valid:
        raise RuntimeError("Salla Orders V3 persisted parity run is invalid")
    if not _runtime_head_matches(row["candidate_head_sha"]):
        raise RuntimeError(
            "Salla Orders V3 persisted parity run HEAD no longer matches runtime"
        )
    context = _PersistedParityRun(
        candidate_head_sha=row["candidate_head_sha"],
        run_id=normalized_run_id,
        owner_id=owner,
        created_by=actor,
        created_at=created_at,
        expires_at=expires_at,
        _seal=_RUN_SEAL,
    )
    if not _parity_run_valid(context, evaluated_at=_utcnow()):
        raise RuntimeError("Salla Orders V3 persisted parity run is stale")
    return context


async def create_parity_run_context(
    db: Any,
    *,
    authenticated_user_id: str,
    user_id: str,
    owner_authorized: bool,
) -> _PersistedParityRun:
    """Persist a run bound to the backend's verified runtime identity."""
    actor = _text(authenticated_user_id)
    owner = _text(user_id)
    if owner_authorized is not True or not actor or actor != owner:
        raise PermissionError("Salla Orders V3 parity runs are owner-only")
    candidate_head_sha = _trusted_candidate_head_sha()
    if type(candidate_head_sha) is not str or not _HEAD_SHA.fullmatch(
        candidate_head_sha
    ):
        raise RuntimeError("Salla Orders V3 verified candidate HEAD is unavailable")
    created_at = _bson_utc_datetime(_utcnow())
    run_id = _new_run_id()
    expires_at = created_at + timedelta(seconds=PARITY_RUN_TTL_SECONDS)
    document = {
        "_id": f"parity-run:{run_id}",
        "record_type": "salla_orders_v3_parity_run",
        "status": "active",
        "trusted_context": True,
        "candidate_head_sha": candidate_head_sha,
        "run_id": run_id,
        "owner_id": owner,
        "created_by": actor,
        "created_at": created_at,
        "expires_at": expires_at,
        "shadow_only": True,
    }
    created = await shadow_collection(db, PARITY_RUNS_COLLECTION).update_one(
        {"_id": document["_id"]},
        {"$setOnInsert": document},
        upsert=True,
    )
    if getattr(created, "upserted_id", None) is None:
        raise RuntimeError("Salla Orders V3 parity run id collision")
    return await read_parity_run_context(
        db,
        authenticated_user_id=actor,
        user_id=owner,
        run_id=run_id,
        owner_authorized=True,
    )


def _canonical_artifact_value(value: Any) -> Any:
    """Return a BSON-safe, deterministic JSON value for sealed evidence."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Salla Orders V3 parity evidence must be finite")
        return value
    if isinstance(value, list):
        return [_canonical_artifact_value(item) for item in value]
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("Salla Orders V3 parity evidence keys must be strings")
        if _CALLER_CONTEXT_KEYS & set(value):
            raise ValueError(
                "Salla Orders V3 parity context is stamped by persisted storage"
            )
        return {
            key: _canonical_artifact_value(item)
            for key, item in value.items()
        }
    raise ValueError("Salla Orders V3 parity evidence must be JSON-compatible")


def _canonical_artifact(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _PARITY_ARTIFACT_KEYS:
        raise ValueError(
            "Salla Orders V3 parity evidence artifact has an invalid key set"
        )
    artifact = _canonical_artifact_value(value)
    if not isinstance(artifact, dict):  # pragma: no cover - guarded above
        raise ValueError("Salla Orders V3 parity evidence artifact is invalid")
    return artifact


def _artifact_digest(
    artifact: dict[str, Any],
    *,
    candidate_head_sha: str,
    run_id: str,
    owner_id: str,
    observed_at: datetime,
) -> str:
    canonical = json.dumps(
        {
            "artifact": artifact,
            "candidate_head_sha": candidate_head_sha,
            "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
            "owner_id": owner_id,
            "run_id": run_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parity_evidence_valid(
    value: Any,
    *,
    parity_run: _PersistedParityRun,
    evaluated_at: datetime,
) -> bool:
    if not isinstance(value, _PersistedParityEvidence):
        raise TypeError("parity evidence must come from persisted evidence")
    reference = _timestamp(evaluated_at)
    run_created_at = _timestamp(parity_run.created_at)
    run_expires_at = _timestamp(parity_run.expires_at)
    observed_at = _timestamp(value.observed_at)
    expires_at = _timestamp(value.expires_at)
    if None in (
        reference,
        run_created_at,
        run_expires_at,
        observed_at,
        expires_at,
    ):
        return False
    try:
        artifact = _canonical_artifact(value.artifact)
    except (TypeError, ValueError, OverflowError):
        return False
    expected_expiry = min(
        run_expires_at,
        observed_at + timedelta(seconds=PARITY_EVIDENCE_MAX_AGE_SECONDS),
    )
    expected_digest = _artifact_digest(
        artifact,
        candidate_head_sha=value.candidate_head_sha,
        run_id=value.run_id,
        owner_id=value.owner_id,
        observed_at=observed_at,
    )
    age_seconds = (reference - observed_at).total_seconds()
    return bool(
        _text(value.artifact_id)
        and re.fullmatch(r"[0-9a-f]{64}", value.artifact_digest)
        and value.artifact_digest == expected_digest
        and value.candidate_head_sha == parity_run.candidate_head_sha
        and value.run_id == parity_run.run_id
        and value.owner_id == parity_run.owner_id
        and value.created_by == parity_run.owner_id
        and observed_at >= run_created_at
        and observed_at
        <= reference + timedelta(seconds=PARITY_EVIDENCE_FUTURE_SKEW_SECONDS)
        and 0 <= age_seconds <= PARITY_EVIDENCE_MAX_AGE_SECONDS
        and expires_at == expected_expiry
        and reference < expires_at
    )


async def read_persisted_parity_evidence(
    db: Any,
    *,
    authenticated_user_id: str,
    user_id: str,
    evidence_id: str,
    owner_authorized: bool,
    parity_run: _PersistedParityRun,
) -> _PersistedParityEvidence:
    """Load an immutable, owner/run/HEAD-bound parity evidence artifact."""
    actor = _text(authenticated_user_id)
    owner = _text(user_id)
    normalized_evidence_id = _text(evidence_id)
    if owner_authorized is not True or not actor or actor != owner:
        raise PermissionError("Salla Orders V3 parity evidence is owner-only")
    if not normalized_evidence_id:
        raise ValueError("Salla Orders V3 parity evidence id is required")
    evaluated_at = _utcnow()
    if not _parity_run_valid(parity_run, evaluated_at=evaluated_at):
        raise RuntimeError("Salla Orders V3 persisted parity run is stale")
    if parity_run.owner_id != owner:
        raise PermissionError("Salla Orders V3 parity run owner mismatch")
    if not _runtime_head_matches(parity_run.candidate_head_sha):
        raise RuntimeError(
            "Salla Orders V3 persisted parity run HEAD no longer matches runtime"
        )
    row = await shadow_collection(db, PARITY_EVIDENCE_COLLECTION).find_one(
        {"_id": f"parity-evidence:{normalized_evidence_id}"},
        {"_id": 0},
    )
    observed_at = _persisted_timestamp((row or {}).get("observed_at"))
    expires_at = _persisted_timestamp((row or {}).get("expires_at"))
    artifact = (row or {}).get("artifact")
    valid = bool(
        isinstance(row, dict)
        and row.get("record_type") == "salla_orders_v3_parity_evidence"
        and row.get("status") == "sealed"
        and row.get("trusted_context") is True
        and _text(row.get("artifact_id")) == normalized_evidence_id
        and type(row.get("artifact_digest")) is str
        and isinstance(artifact, dict)
        and _text(row.get("candidate_head_sha"))
        == parity_run.candidate_head_sha
        and _text(row.get("run_id")) == parity_run.run_id
        and _text(row.get("owner_id")) == owner
        and _text(row.get("created_by")) == actor
        and observed_at is not None
        and expires_at is not None
    )
    if not valid:
        raise RuntimeError("Salla Orders V3 persisted parity evidence is invalid")
    evidence = _PersistedParityEvidence(
        artifact_id=normalized_evidence_id,
        artifact_digest=row["artifact_digest"],
        artifact=artifact,
        candidate_head_sha=parity_run.candidate_head_sha,
        run_id=parity_run.run_id,
        owner_id=owner,
        created_by=actor,
        observed_at=observed_at,
        expires_at=expires_at,
        _seal=_EVIDENCE_SEAL,
    )
    if not _parity_evidence_valid(
        evidence,
        parity_run=parity_run,
        evaluated_at=evaluated_at,
    ):
        raise RuntimeError("Salla Orders V3 persisted parity evidence is invalid")
    return evidence


def _context_valid(
    value: Any,
    *,
    evidence_head_sha: str,
    evidence_run_id: str,
    evidence_owner_id: str,
    evaluated_at: datetime,
    observed_field: str = "evidence_observed_at",
) -> bool:
    observed_at = _timestamp(
        value.get(observed_field) if isinstance(value, dict) else None
    )
    reference = _timestamp(evaluated_at)
    if observed_at is None or reference is None:
        return False
    age_seconds = (reference - observed_at).total_seconds()
    return bool(
        isinstance(value, dict)
        and type(evidence_head_sha) is str
        and bool(_HEAD_SHA.fullmatch(evidence_head_sha))
        and value.get("evidence_head_sha") == evidence_head_sha
        and type(evidence_run_id) is str
        and evidence_run_id.strip()
        and value.get("evidence_run_id") == evidence_run_id
        and type(evidence_owner_id) is str
        and evidence_owner_id.strip()
        and value.get("evidence_owner_id") == evidence_owner_id
        and -PARITY_EVIDENCE_FUTURE_SKEW_SECONDS
        <= age_seconds
        <= PARITY_EVIDENCE_MAX_AGE_SECONDS
    )


def _order_identity(value: Any) -> Optional[tuple[str, str, str]]:
    if not isinstance(value, dict):
        return None
    fields = tuple(
        value.get(field)
        for field in ("user_id", "store_id", "order_number")
    )
    if any(type(field) is not str for field in fields):
        return None
    identity = tuple(field.strip() for field in fields)
    return identity if all(identity) else None


def _qoyod_evidence_valid(
    value: Any,
    *,
    evidence_owner_id: str,
) -> bool:
    identity = _order_identity(
        value.get("order_identity") if isinstance(value, dict) else None
    )
    eligible = value.get("eligible") if isinstance(value, dict) else None
    payload = value.get("payload") if isinstance(value, dict) else None
    idempotency_key = (
        value.get("idempotency_key") if isinstance(value, dict) else None
    )
    ineligible_reason = (
        value.get("ineligible_reason") if isinstance(value, dict) else None
    )
    return bool(
        isinstance(value, dict)
        and type(eligible) is bool
        and isinstance(payload, dict)
        and (
            bool(payload)
            if eligible
            else type(ineligible_reason) is str
            and bool(ineligible_reason.strip())
        )
        and type(value.get("provider_write_reached")) is bool
        and value.get("provider_write_reached") is False
        and type(idempotency_key) is str
        and bool(idempotency_key.strip())
        and identity is not None
        and identity[0] == evidence_owner_id
    )


def _attribution_evidence_valid(
    rows: Any,
) -> bool:
    if not isinstance(rows, list) or not rows:
        return False
    expected_count = len(rows)
    identities: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            return False
        if type(row.get("evidence_count")) is not int:
            return False
        if row.get("evidence_count") != expected_count:
            return False
        raw_order_number = row.get("order_number")
        if type(raw_order_number) is not str:
            return False
        order_number = raw_order_number.strip()
        if not order_number or order_number in identities:
            return False
        identities.add(order_number)
        amount = row.get("revenue")
        if amount is None:
            amount = row.get("total_amount")
        if type(amount) not in (int, float):
            return False
        if not math.isfinite(float(amount)):
            return False
    return True


def _persisted_audit_valid(
    value: Any,
    *,
    gate: str,
    parity_run: _PersistedParityRun,
    order_identity: Optional[tuple[str, str, str]],
    evaluated_at: datetime,
) -> bool:
    if value is None:
        return False
    if not isinstance(value, _PersistedNotApplicable):
        raise TypeError("not-applicable evidence must come from a persisted audit")
    audited_at = _timestamp(value.audited_at)
    run_created_at = _timestamp(parity_run.created_at)
    return bool(
        value.gate == gate
        and value.owner_id == parity_run.owner_id
        and value.evidence_head_sha == parity_run.candidate_head_sha
        and value.evidence_run_id == parity_run.run_id
        and order_identity is not None
        and value.order_identity == order_identity
        and audited_at is not None
        and run_created_at is not None
        and audited_at >= run_created_at
        and _context_valid(
            {
                "evidence_head_sha": value.evidence_head_sha,
                "evidence_run_id": value.evidence_run_id,
                "evidence_owner_id": value.owner_id,
                "evidence_observed_at": value.audited_at,
            },
            evidence_head_sha=parity_run.candidate_head_sha,
            evidence_run_id=parity_run.run_id,
            evidence_owner_id=parity_run.owner_id,
            evaluated_at=evaluated_at,
        )
    )


def scope_diagnostic(integration: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Report effective stored scopes without returning either OAuth token."""
    scope_value = (integration or {}).get("scope")
    if isinstance(scope_value, list):
        scopes = {str(value).strip() for value in scope_value if str(value).strip()}
    else:
        scopes = {
            value
            for value in str(scope_value or "").replace(",", " ").split()
            if value
        }
    effective_read_scopes = sorted(scopes & ACCEPTED_ORDER_READ_SCOPES)
    return {
        "connected": bool(integration and integration.get("status") == "connected"),
        "stored_scopes": sorted(scopes),
        "required_scope": REQUIRED_SCOPE,
        "accepted_order_read_scopes": sorted(ACCEPTED_ORDER_READ_SCOPES),
        "effective_order_read_scopes": effective_read_scopes,
        "required_scope_present": bool(effective_read_scopes),
        "token_fields_returned": False,
    }


async def read_audited_not_applicable(
    db: Any,
    *,
    authenticated_user_id: str,
    user_id: str,
    audit_id: str,
    gate: str,
    owner_authorized: bool,
    parity_run: _PersistedParityRun,
) -> _PersistedNotApplicable:
    """Load one immutable N/A assertion from the isolated audit state."""
    actor = _text(authenticated_user_id)
    owner = _text(user_id)
    normalized_audit_id = _text(audit_id)
    normalized_gate = _text(gate)
    if owner_authorized is not True or not actor or actor != owner:
        raise PermissionError("Salla Orders V3 parity audits are owner-only")
    evaluated_at = _utcnow()
    if not _parity_run_valid(parity_run, evaluated_at=evaluated_at):
        raise RuntimeError("Salla Orders V3 persisted parity run is stale")
    if parity_run.owner_id != owner:
        raise PermissionError("Salla Orders V3 parity run owner mismatch")
    if not _runtime_head_matches(parity_run.candidate_head_sha):
        raise RuntimeError(
            "Salla Orders V3 persisted parity run HEAD no longer matches runtime"
        )
    evidence_head_sha = parity_run.candidate_head_sha
    evidence_run_id = parity_run.run_id
    if normalized_gate not in {"qoyod", "attribution"}:
        raise ValueError("Salla Orders V3 parity audit gate is invalid")
    if not normalized_audit_id:
        raise ValueError("Salla Orders V3 parity audit id is required")
    row = await shadow_collection(db, PARITY_AUDITS_COLLECTION).find_one(
        {"_id": f"parity-audit:{normalized_audit_id}"},
        {"_id": 0},
    )
    audited_at = _persisted_timestamp((row or {}).get("audited_at"))
    order_identity = _order_identity((row or {}).get("order_identity"))
    run_created_at = _timestamp(parity_run.created_at)
    valid = bool(
        isinstance(row, dict)
        and row.get("record_type")
        == "salla_orders_v3_parity_not_applicable"
        and row.get("status") == "not_applicable"
        and row.get("gate") == normalized_gate
        and _text(row.get("owner_id")) == owner
        and _text(row.get("recorded_by")) == actor
        and _text(row.get("audit_id")) == normalized_audit_id
        and _text(row.get("reason"))
        and audited_at is not None
        and run_created_at is not None
        and audited_at >= run_created_at
        and order_identity is not None
        and order_identity[0] == owner
        and _context_valid(
            {
                "evidence_head_sha": row.get("evidence_head_sha"),
                "evidence_run_id": row.get("evidence_run_id"),
                "evidence_owner_id": owner,
                "evidence_observed_at": audited_at,
            },
            evidence_head_sha=evidence_head_sha,
            evidence_run_id=evidence_run_id,
            evidence_owner_id=owner,
            evaluated_at=evaluated_at,
        )
    )
    if not valid:
        raise RuntimeError("Salla Orders V3 persisted parity audit is invalid")
    return _PersistedNotApplicable(
        gate=normalized_gate,
        owner_id=owner,
        audit_id=normalized_audit_id,
        recorded_by=actor,
        reason=_text(row.get("reason")),
        audited_at=audited_at,
        evidence_head_sha=evidence_head_sha,
        evidence_run_id=evidence_run_id,
        order_identity=order_identity,
        _seal=_AUDIT_SEAL,
    )


def build_parity_report(
    *,
    parity_run: _PersistedParityRun,
    parity_evidence: _PersistedParityEvidence,
    qoyod_not_applicable: Optional[_PersistedNotApplicable] = None,
    attribution_not_applicable: Optional[_PersistedNotApplicable] = None,
) -> dict[str, Any]:
    reference_time = _utcnow()
    parity_run_valid = _parity_run_valid(
        parity_run,
        evaluated_at=reference_time,
    )
    runtime_head_matches = _runtime_head_matches(
        parity_run.candidate_head_sha
    )
    parity_evidence_valid = _parity_evidence_valid(
        parity_evidence,
        parity_run=parity_run,
        evaluated_at=reference_time,
    )
    evidence_head_sha = parity_run.candidate_head_sha
    evidence_run_id = parity_run.run_id
    evidence_owner_id = parity_run.owner_id
    artifact = deepcopy(parity_evidence.artifact)
    if not isinstance(artifact, dict):  # pragma: no cover - sealed type guard
        artifact = {}
    legacy_order = artifact.get("legacy_order")
    v3_order = artifact.get("v3_order")
    legacy_qoyod_dry_run = artifact.get("legacy_qoyod_dry_run")
    v3_qoyod_dry_run = artifact.get("v3_qoyod_dry_run")
    legacy_attribution_rows = artifact.get("legacy_attribution_rows")
    v3_attribution_rows = artifact.get("v3_attribution_rows")
    regression_results = artifact.get("regression_results")
    try:
        fulfillment = compare_fulfillment_parity(legacy_order, v3_order)
    except (AttributeError, TypeError, ValueError, OverflowError):
        fulfillment = {"passed": False, "invalid_evidence": True}
    try:
        qoyod = compare_qoyod_parity(legacy_qoyod_dry_run, v3_qoyod_dry_run)
    except (AttributeError, TypeError, ValueError, OverflowError):
        qoyod = {
            "passed": False,
            "provider_write_reached": True,
            "invalid_evidence": True,
        }
    try:
        attribution = compare_attribution_parity(
            legacy_attribution_rows,
            v3_attribution_rows,
        )
    except (TypeError, ValueError, OverflowError):
        attribution = {
            "passed": False,
            "duplicate_orders": {"legacy": [], "v3": []},
            "legacy": {},
            "v3": {},
            "invalid_evidence": True,
        }
    regression_input_valid = isinstance(regression_results, dict)
    regressions = deepcopy(regression_results) if regression_input_valid else {}
    common_context_valid = bool(
        parity_run_valid
        and runtime_head_matches
        and parity_evidence_valid
        and type(evidence_head_sha) is str
        and bool(_HEAD_SHA.fullmatch(evidence_head_sha))
        and type(evidence_run_id) is str
        and evidence_run_id.strip()
        and type(evidence_owner_id) is str
        and evidence_owner_id.strip()
    )
    legacy_fulfillment_identity = _order_identity(
        legacy_order.get("order_identity")
        if isinstance(legacy_order, dict)
        else None
    )
    v3_fulfillment_identity = _order_identity(
        v3_order.get("order_identity") if isinstance(v3_order, dict) else None
    )
    fulfillment_identity_matches = bool(
        legacy_fulfillment_identity is not None
        and legacy_fulfillment_identity == v3_fulfillment_identity
        and legacy_fulfillment_identity[0] == evidence_owner_id
    )
    fulfillment_context_valid = bool(
        common_context_valid
        and fulfillment_identity_matches
    )
    regression_context_valid = common_context_valid
    regressions_passed = bool(
        regression_context_valid
        and regression_input_valid
        and set(regressions) == REQUIRED_REGRESSION_KEYS
        and all(regressions[key] is True for key in REQUIRED_REGRESSION_KEYS)
    )

    qoyod_supplied = bool(legacy_qoyod_dry_run or v3_qoyod_dry_run)
    legacy_qoyod_valid = bool(
        common_context_valid
        and _qoyod_evidence_valid(
            legacy_qoyod_dry_run,
            evidence_owner_id=evidence_owner_id,
        )
    )
    v3_qoyod_valid = bool(
        common_context_valid
        and _qoyod_evidence_valid(
            v3_qoyod_dry_run,
            evidence_owner_id=evidence_owner_id,
        )
    )
    qoyod_identity_matches = bool(
        legacy_qoyod_valid
        and v3_qoyod_valid
        and _order_identity(legacy_qoyod_dry_run.get("order_identity"))
        == _order_identity(v3_qoyod_dry_run.get("order_identity"))
    )
    qoyod_identity_matches_fulfillment = bool(
        qoyod_identity_matches
        and fulfillment_identity_matches
        and _order_identity(legacy_qoyod_dry_run.get("order_identity"))
        == legacy_fulfillment_identity
    )
    qoyod_comparison_valid = bool(
        legacy_qoyod_valid
        and v3_qoyod_valid
        and qoyod_identity_matches_fulfillment
    )
    qoyod_audit_valid = _persisted_audit_valid(
        qoyod_not_applicable,
        gate="qoyod",
        parity_run=parity_run,
        order_identity=legacy_fulfillment_identity,
        evaluated_at=reference_time,
    )
    qoyod_exempt = bool(
        common_context_valid and not qoyod_supplied and qoyod_audit_valid
    )

    attribution_supplied = bool(
        legacy_attribution_rows or v3_attribution_rows
    )
    legacy_attribution_valid = bool(
        common_context_valid
        and _attribution_evidence_valid(legacy_attribution_rows)
    )
    v3_attribution_valid = bool(
        common_context_valid
        and _attribution_evidence_valid(v3_attribution_rows)
    )
    attribution_comparison_valid = bool(
        legacy_attribution_valid and v3_attribution_valid
    )
    attribution_audit_valid = _persisted_audit_valid(
        attribution_not_applicable,
        gate="attribution",
        parity_run=parity_run,
        order_identity=legacy_fulfillment_identity,
        evaluated_at=reference_time,
    )
    attribution_exempt = bool(
        common_context_valid
        and not attribution_supplied
        and attribution_audit_valid
    )
    qoyod_evidence_accepted = qoyod_comparison_valid or qoyod_exempt
    attribution_evidence_accepted = bool(
        attribution_comparison_valid or attribution_exempt
    )
    evidence_context_valid = bool(
        fulfillment_context_valid
        and regression_context_valid
        and qoyod_evidence_accepted
        and attribution_evidence_accepted
    )
    parity_ready = bool(
        fulfillment["passed"]
        and evidence_context_valid
        and qoyod_evidence_accepted
        and (qoyod["passed"] if qoyod_comparison_valid else qoyod_exempt)
        and attribution_evidence_accepted
        and (
            attribution["passed"]
            if attribution_comparison_valid
            else attribution_exempt
        )
        and regressions_passed
    )
    return {
        "fulfillment_parity": fulfillment,
        "qoyod_parity": qoyod,
        "attribution_parity": attribution,
        "regressions": regressions,
        "regressions_passed": regressions_passed,
        "parity_run_valid": parity_run_valid,
        "runtime_head_matches": runtime_head_matches,
        "persisted_evidence_valid": parity_evidence_valid,
        "parity_evidence_id": parity_evidence.artifact_id,
        "parity_evidence_digest": parity_evidence.artifact_digest,
        "evidence_context_valid": evidence_context_valid,
        "evidence_head_sha": evidence_head_sha,
        "evidence_run_id": evidence_run_id,
        "evidence_owner_id": evidence_owner_id,
        "evaluated_at": reference_time.isoformat(),
        "fulfillment_identity_matches": fulfillment_identity_matches,
        "qoyod_identity_matches_fulfillment": (
            qoyod_identity_matches_fulfillment
        ),
        "qoyod_evidence_accepted": qoyod_evidence_accepted,
        "qoyod_evidence_mode": "dry_run" if qoyod_comparison_valid else (
            "persisted_audited_not_applicable" if qoyod_exempt else "invalid_or_missing"
        ),
        "attribution_evidence_accepted": attribution_evidence_accepted,
        "attribution_evidence_mode": (
            "comparison" if attribution_comparison_valid else (
                "persisted_audited_not_applicable"
                if attribution_exempt
                else "invalid_or_missing"
            )
        ),
        "parity_ready": parity_ready,
        # P0 has no cutover switch or operational adapter. A later reviewed
        # phase must consume parity evidence under a separate authorization.
        "cutover_allowed": False,
        "provider_write_reached": bool(qoyod["provider_write_reached"]),
    }


async def read_fulfillment_shadow_comparison(
    db: Any,
    *,
    authenticated_user_id: str,
    user_id: str,
    store_id: str,
    order_number: str,
    owner_authorized: bool,
) -> dict[str, Any]:
    """Compare one current order and one isolated V3 snapshot without writes."""
    actor = str(authenticated_user_id or "").strip()
    owner = str(user_id or "").strip()
    if (
        owner_authorized is not True
        or not actor
        or actor != owner
    ):
        raise PermissionError("Salla Orders V3 diagnostics are owner-only")
    normalized_store = str(store_id or "").strip()
    normalized_order = str(order_number or "").strip()
    if not normalized_store or not normalized_order:
        raise ValueError("Salla Orders V3 diagnostics require store and order identity")
    legacy = await db.unified_orders.find_one(
        {"user_id": owner, "order_number": normalized_order},
        {"_id": 0, "raw_by_source": 0, "raw_by_user": 0},
    )
    snapshot_job = await shadow_collection(db, JOBS_COLLECTION).find_one(
        {
            "user_id": owner,
            "store_id": normalized_store,
            "order_number": normalized_order,
        },
        {"_id": 0, "compatibility_order": 1, "signal_revision": 1},
    )
    snapshot = (snapshot_job or {}).get("compatibility_order")
    if not legacy or not isinstance(snapshot, dict):
        return {
            "available": False,
            "legacy_found": bool(legacy),
            "shadow_found": isinstance(snapshot, dict),
            "provider_write_reached": False,
        }
    snapshot = deepcopy(snapshot)
    # Always compare the saved success evidence against the queue's latest
    # signal, including while a newer revision is pending enrichment.
    snapshot["signal_revision"] = snapshot_job.get("signal_revision")
    return {
        "available": True,
        **compare_fulfillment_parity(
            legacy,
            snapshot,
        ),
        "provider_write_reached": False,
    }
