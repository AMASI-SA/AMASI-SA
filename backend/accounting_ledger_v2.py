"""Sealed, append-only Accounting V2 journal storage.

This module is the only production module allowed to access the physical V2
ledger collections.  It deliberately exposes no FastAPI router.  Mezan 2
workflows may call these functions only after their own persisted permission
and cutover gates have succeeded.

The legacy ``general_ledger`` remains an unrelated archive.  No row is copied
from it and no V2 balance calculation reads it.  The sole legacy access below
is the read-only activation preflight that detects counterfeit/stale MZ2 tags.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Mapping, Sequence

from bson.int64 import Int64

from accounting_module_contract import OPERATION_ID as CONTRACT_OPERATION_ID


GROUPS_COLLECTION = "accounting_journal_groups_v2"
GENERAL_LEDGER_COLLECTION = "accounting_general_ledger_v2"
AUDIT_COLLECTION = "accounting_audit_log_v2"
SEQUENCES_COLLECTION = "accounting_ledger_sequences_v2"

OPERATION_ID = "MZ2-FIN-CUTOVER-001"
if CONTRACT_OPERATION_ID != OPERATION_ID:
    raise RuntimeError("Accounting V2 operation identity diverged from its module contract")

SCHEMA_VERSION = 2
CURRENCY = "SAR"
MAX_JOURNAL_LEGS = 10_000
MAX_LEGACY_SCAN_SAMPLE = 100
MAX_AGGREGATE_GROUPS = 10_000
MAX_AMOUNT_MINOR = (2**63) - 1
_CENT = Decimal("0.01")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
_LEG_FIELDS = frozenset({
    "leg_key",
    "entity_type",
    "entity_id",
    "sub_account",
    "entry_type",
    "amount",
    "side",
    "metadata",
})
_REQUIRED_LEG_FIELDS = frozenset({
    "leg_key",
    "entity_type",
    "entity_id",
    "entry_type",
    "amount",
    "side",
})
_RESERVED_PUBLIC_TXN_TYPES = frozenset({"opening_balance", "reversal"})
_OPERATIONAL_WRITE_MODE = object()
_OPENING_WRITE_MODE = object()
_REVERSAL_WRITE_MODE = object()
_RESERVED_METADATA_FIELDS = frozenset({
    "_id",
    "user_id",
    "operation_id",
    "schema_version",
    "txn_group_id",
    "idempotency_key",
    "effective_at",
    "currency",
    "status",
    "content_hash",
    "amount_minor",
    "debit_total_minor",
    "credit_total_minor",
    "entry_no",
    "leg_no",
})


class AccountingLedgerV2Error(RuntimeError):
    """Stable domain failure for future route adapters."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


def _fail(code: str, message: str, **details: Any) -> None:
    raise AccountingLedgerV2Error(code, message, details=details)


def _required_text(value: Any, *, field: str, max_length: int = 240) -> str:
    if not isinstance(value, str):
        _fail(f"{field}_required", f"{field} must be a non-empty string")
    cleaned = value.strip()
    if not cleaned:
        _fail(f"{field}_required", f"{field} must be a non-empty string")
    if len(cleaned) > max_length or "\x00" in cleaned:
        _fail(f"{field}_invalid", f"{field} is invalid")
    return cleaned


def _slug(value: Any, *, field: str, max_length: int = 120) -> str:
    cleaned = _required_text(value, field=field, max_length=max_length)
    if not _SLUG.fullmatch(cleaned):
        _fail(f"{field}_invalid", f"{field} must use the canonical slug format")
    return cleaned


def _utc_iso(value: Any, *, field: str = "effective_at") -> str:
    if not isinstance(value, str):
        _fail(f"{field}_required", f"{field} must be an ISO timestamp string")
    raw = value.strip()
    if not raw:
        _fail(f"{field}_required", f"{field} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        _fail(f"{field}_invalid", f"{field} must be a valid ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(
            f"{field}_timezone_required",
            f"{field} must include an explicit timezone offset",
        )
    normalized = parsed.astimezone(timezone.utc)
    # Fixed-width UTC text preserves chronological ordering in Mongo range
    # queries and in the cutover comparison.
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _amount(value: Any) -> tuple[str, Decimal, int]:
    if not isinstance(value, str):
        _fail(
            "amount_decimal_string_required",
            "Journal amounts must be canonical decimal strings",
        )
    raw = value.strip()
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        _fail("amount_invalid", "Journal amount is not a valid decimal")
    if not parsed.is_finite() or parsed <= 0:
        _fail("amount_invalid", "Journal amount must be finite and positive")
    if parsed.as_tuple().exponent != -2 or format(parsed, "f") != raw:
        _fail(
            "amount_not_canonical",
            "Journal amount must use exactly two decimal places without exponent notation",
        )
    amount_minor = int(parsed * 100)
    if amount_minor > MAX_AMOUNT_MINOR:
        _fail(
            "amount_minor_overflow",
            "Journal amount exceeds the signed 64-bit minor-unit limit",
        )
    return raw, parsed, amount_minor


def _money(value: Decimal) -> str:
    return format(value.quantize(_CENT), "f")


def _minor_money(value: int) -> str:
    return format(Decimal(value).scaleb(-2).quantize(_CENT), "f")


def _stored_amount_minor(entry: Mapping[str, Any]) -> int:
    try:
        _text, _decimal, expected = _amount(entry.get("amount"))
    except AccountingLedgerV2Error as exc:
        raise AccountingLedgerV2Error(
            "accounting_v2_journal_integrity_failure",
            "A stored Accounting V2 amount is invalid",
            details={"entry_id": entry.get("id")},
        ) from exc
    stored = entry.get("amount_minor")
    if not isinstance(stored, Int64) or stored != expected:
        _fail(
            "accounting_v2_journal_integrity_failure",
            "Stored Accounting V2 amount and amount_minor disagree",
            entry_id=entry.get("id"),
        )
    return stored


def _canonical_value(value: Any, *, path: str) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        if not -(2**63) <= value <= MAX_AMOUNT_MINOR:
            _fail("metadata_invalid", f"{path} contains an out-of-range integer")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            _fail("metadata_invalid", f"{path} contains a non-finite decimal")
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            _fail("metadata_invalid", f"{path} contains a naive datetime")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
    if isinstance(value, float):
        _fail("metadata_float_forbidden", f"{path} must not contain binary floats")
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key in value:
            if not isinstance(key, str) or not key or key.startswith("$") or "." in key:
                _fail("metadata_invalid", f"{path} contains an invalid key")
        for key in sorted(value):
            result[key] = _canonical_value(value[key], path=f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [
            _canonical_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    _fail("metadata_invalid", f"{path} contains an unsupported value")


def _metadata(value: Any, *, field: str = "metadata") -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        _fail("metadata_invalid", f"{field} must be an object")
    reserved = sorted(_RESERVED_METADATA_FIELDS & set(value))
    if reserved:
        _fail(
            "metadata_reserved_fields",
            f"{field} contains reserved ledger fields",
            fields=reserved,
        )
    normalized = _canonical_value(value, path=field)
    assert isinstance(normalized, dict)
    return normalized


def _normalize_leg(value: Any) -> tuple[dict[str, Any], Decimal]:
    if not isinstance(value, Mapping):
        _fail("ledger_leg_invalid", "Every journal leg must be an object")
    keys = set(value)
    unknown = sorted(keys - _LEG_FIELDS)
    if unknown:
        _fail(
            "ledger_leg_unknown_fields",
            "Journal leg contains unsupported fields",
            fields=unknown,
        )
    missing = sorted(_REQUIRED_LEG_FIELDS - keys)
    if missing:
        _fail(
            "ledger_leg_missing_fields",
            "Journal leg is incomplete",
            fields=missing,
        )
    amount_text, amount_decimal, amount_minor = _amount(value.get("amount"))
    side = value.get("side")
    if side not in {"debit", "credit"}:
        _fail("ledger_side_invalid", "Journal side must be debit or credit")
    sub_account = value.get("sub_account")
    if sub_account is not None:
        sub_account = _slug(sub_account, field="sub_account")
    return ({
        "leg_key": _slug(value.get("leg_key"), field="leg_key", max_length=180),
        "entity_type": _slug(value.get("entity_type"), field="entity_type"),
        "entity_id": _required_text(value.get("entity_id"), field="entity_id"),
        "sub_account": sub_account,
        "entry_type": _slug(value.get("entry_type"), field="entry_type"),
        "amount": amount_text,
        "amount_minor": amount_minor,
        "side": side,
        "metadata": _metadata(value.get("metadata"), field="leg_metadata"),
    }, amount_decimal)


def _normalize_entries(entries: Any) -> tuple[list[dict[str, Any]], Decimal, Decimal]:
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        _fail("journal_entries_invalid", "Journal entries must be a sequence")
    if len(entries) < 2 or len(entries) > MAX_JOURNAL_LEGS:
        _fail(
            "journal_leg_count_invalid",
            f"Journal must contain between 2 and {MAX_JOURNAL_LEGS} legs",
        )
    normalized: list[dict[str, Any]] = []
    debit = Decimal("0.00")
    credit = Decimal("0.00")
    keys: set[str] = set()
    for raw in entries:
        leg, amount_decimal = _normalize_leg(raw)
        if leg["leg_key"] in keys:
            _fail("journal_duplicate_leg_key", "Journal leg keys must be unique")
        keys.add(leg["leg_key"])
        normalized.append(leg)
        if leg["side"] == "debit":
            debit += amount_decimal
        else:
            credit += amount_decimal
    if debit != credit:
        _fail(
            "journal_unbalanced",
            "Journal debit and credit totals must be exactly equal",
            debit_total=_money(debit),
            credit_total=_money(credit),
        )
    total_minor = int(debit * 100)
    if total_minor > MAX_AMOUNT_MINOR:
        _fail(
            "journal_total_minor_overflow",
            "Journal side total exceeds the signed 64-bit minor-unit limit",
        )
    return normalized, debit, credit


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _prepare_journal(
    *,
    user_id: str,
    idempotency_key: str,
    txn_type: str,
    source: str,
    effective_at: str,
    entries: Sequence[Mapping[str, Any]],
    notes: str,
    metadata: Mapping[str, Any] | None,
    reversal_of_txn_group_id: str | None = None,
) -> dict[str, Any]:
    owner = _required_text(user_id, field="user_id")
    idem = _required_text(idempotency_key, field="idempotency_key", max_length=300)
    kind = _slug(txn_type, field="txn_type")
    source_name = _slug(source, field="source", max_length=180)
    effective = _utc_iso(effective_at)
    normalized_entries, debit, credit = _normalize_entries(entries)
    if not isinstance(notes, str):
        _fail("notes_invalid", "Journal notes must be a string")
    normalized_notes = notes.strip()
    if len(normalized_notes) > 1_000:
        _fail("notes_invalid", "Journal notes exceed the maximum length")
    normalized_metadata = _metadata(metadata)
    reversal_of = None
    if reversal_of_txn_group_id is not None:
        reversal_of = _required_text(
            reversal_of_txn_group_id,
            field="reversal_of_txn_group_id",
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "operation_id": OPERATION_ID,
        "user_id": owner,
        "idempotency_key": idem,
        "txn_type": kind,
        "source": source_name,
        "effective_at": effective,
        "currency": CURRENCY,
        "notes": normalized_notes,
        "metadata": normalized_metadata,
        "reversal_of_txn_group_id": reversal_of,
        "entries": normalized_entries,
    }
    content_hash = _digest(payload)
    txn_group_id = _stable_id("mz2j", OPERATION_ID, owner, idem)
    return {
        **payload,
        "txn_group_id": txn_group_id,
        "content_hash": content_hash,
        "debit_total": _money(debit),
        "credit_total": _money(credit),
        "debit_total_minor": int(debit * 100),
        "credit_total_minor": int(credit * 100),
        "entry_count": len(normalized_entries),
    }


def _validate_prepared_write_mode(prepared: Mapping[str, Any], mode: object) -> None:
    txn_type = prepared.get("txn_type")
    reversal_of = prepared.get("reversal_of_txn_group_id")
    entries = prepared.get("entries")
    if (
        isinstance(entries, (str, bytes))
        or not isinstance(entries, Sequence)
        or not entries
        or any(not isinstance(entry, Mapping) for entry in entries)
    ):
        _fail("accounting_v2_internal_write_contract", "Prepared journal has no entries")
    entry_types = {entry.get("entry_type") for entry in entries}

    if mode is _OPERATIONAL_WRITE_MODE:
        valid = (
            txn_type not in _RESERVED_PUBLIC_TXN_TYPES
            and reversal_of is None
            and not (_RESERVED_PUBLIC_TXN_TYPES & entry_types)
        )
    elif mode is _OPENING_WRITE_MODE:
        metadata = prepared.get("metadata") or {}
        valid = (
            txn_type == "opening_balance"
            and reversal_of is None
            and prepared.get("source") == "accounting_opening_balances_v2"
            and entry_types == {"opening_balance"}
            and isinstance(metadata.get("opening_operation_id"), str)
            and bool(metadata.get("opening_operation_id"))
            and isinstance(metadata.get("approved_preview_hash"), str)
            and bool(_HEX_64.fullmatch(metadata.get("approved_preview_hash", "")))
            and metadata.get("historical_import") is False
            and metadata.get("legacy_financial_data_included") is False
        )
    elif mode is _REVERSAL_WRITE_MODE:
        metadata = prepared.get("metadata") or {}
        valid = (
            txn_type == "reversal"
            and isinstance(reversal_of, str)
            and bool(reversal_of)
            and prepared.get("source") == "accounting_ledger_v2_reversal"
            and entry_types == {"reversal"}
            and metadata.get("reason") == prepared.get("notes")
            and isinstance(metadata.get("original_content_hash"), str)
            and bool(_HEX_64.fullmatch(metadata.get("original_content_hash", "")))
        )
    else:
        valid = False
    if not valid:
        _fail(
            "accounting_v2_internal_write_contract",
            "Prepared journal does not match its sealed write mode",
        )


async def _run_atomic(
    db: Any,
    callback: Callable[[Any], Awaitable[dict[str, Any]]],
    *,
    mongo_session: Any = None,
) -> dict[str, Any]:
    if mongo_session is None or not bool(
        getattr(mongo_session, "in_transaction", False)
    ):
        _fail(
            "accounting_v2_atomic_transaction_required",
            "Every Accounting V2 write requires a caller-owned active Mongo transaction",
        )
    return await callback(mongo_session)


async def _reserve_entry_numbers(db: Any, *, user_id: str, count: int, session: Any) -> int:
    selector = {"user_id": user_id, "operation_id": OPERATION_ID}
    before = await db[SEQUENCES_COLLECTION].find_one(selector, session=session)
    last_entry_no = 0 if before is None else before.get("last_entry_no")
    if (
        (before is not None and not isinstance(last_entry_no, Int64))
        or last_entry_no < 0
        or last_entry_no > MAX_AMOUNT_MINOR - count
    ):
        _fail(
            "accounting_v2_sequence_overflow",
            "Accounting V2 entry sequence is invalid or exhausted",
        )
    expected_last_entry_no = last_entry_no + count
    await db[SEQUENCES_COLLECTION].update_one(
        selector,
        {
            "$inc": {"last_entry_no": Int64(count)},
            "$setOnInsert": {
                "_id": _stable_id("mz2seq", OPERATION_ID, user_id),
                "schema_version": SCHEMA_VERSION,
                "currency": CURRENCY,
            },
        },
        upsert=True,
        session=session,
    )
    sequence = await db[SEQUENCES_COLLECTION].find_one(selector, session=session)
    if (
        not sequence
        or not isinstance(sequence.get("last_entry_no"), Int64)
        or sequence.get("last_entry_no") != expected_last_entry_no
    ):
        _fail("accounting_v2_sequence_failure", "Could not reserve journal entry numbers")
    return expected_last_entry_no - count + 1


async def _insert_prepared_journal(
    db: Any,
    *,
    prepared: Mapping[str, Any],
    actor_id: str,
    actor_name: str,
    write_mode: object,
    session: Any,
) -> dict[str, Any]:
    _validate_prepared_write_mode(prepared, write_mode)
    owner = prepared["user_id"]
    group_selector = {
        "user_id": owner,
        "operation_id": OPERATION_ID,
        "idempotency_key": prepared["idempotency_key"],
    }
    existing = await db[GROUPS_COLLECTION].find_one(group_selector, session=session)
    if existing:
        if existing.get("content_hash") != prepared["content_hash"]:
            _fail(
                "accounting_v2_idempotency_conflict",
                "The idempotency key already belongs to different journal content",
                txn_group_id=existing.get("txn_group_id"),
            )
        return {"txn_group_id": existing["txn_group_id"], "existing": True}

    if prepared["txn_type"] == "opening_balance":
        opening = await db[GROUPS_COLLECTION].find_one(
            {
                "user_id": owner,
                "operation_id": OPERATION_ID,
                "txn_type": "opening_balance",
            },
            session=session,
        )
        if opening:
            _fail(
                "accounting_v2_opening_already_exists",
                "This Accounting V2 book already has an opening journal",
                txn_group_id=opening.get("txn_group_id"),
            )

    first_entry_no = await _reserve_entry_numbers(
        db,
        user_id=owner,
        count=prepared["entry_count"],
        session=session,
    )
    posted_at = _now()
    group_id = prepared["txn_group_id"]
    audit_id = _stable_id("mz2audit", group_id, "posted")
    group = {
        "_id": group_id,
        "id": group_id,
        "txn_group_id": group_id,
        "user_id": owner,
        "operation_id": OPERATION_ID,
        "schema_version": SCHEMA_VERSION,
        "idempotency_key": prepared["idempotency_key"],
        "txn_type": prepared["txn_type"],
        "source": prepared["source"],
        "effective_at": prepared["effective_at"],
        "currency": CURRENCY,
        "status": "posted",
        "notes": prepared["notes"],
        "metadata": deepcopy(prepared["metadata"]),
        "reversal_of_txn_group_id": prepared["reversal_of_txn_group_id"],
        "content_hash": prepared["content_hash"],
        "entry_count": prepared["entry_count"],
        "debit_total": prepared["debit_total"],
        "credit_total": prepared["credit_total"],
        "debit_total_minor": Int64(prepared["debit_total_minor"]),
        "credit_total_minor": Int64(prepared["credit_total_minor"]),
        "first_entry_no": Int64(first_entry_no),
        "last_entry_no": Int64(first_entry_no + prepared["entry_count"] - 1),
        "audit_id": audit_id,
        "posted_at": posted_at,
        "posted_by": actor_id,
        "posted_by_name": actor_name,
        "created_at": posted_at,
    }
    legs: list[dict[str, Any]] = []
    for offset, leg in enumerate(prepared["entries"]):
        leg_no = offset + 1
        entry_id = _stable_id("mz2e", group_id, leg["leg_key"])
        legs.append({
            "_id": entry_id,
            "id": entry_id,
            "user_id": owner,
            "operation_id": OPERATION_ID,
            "schema_version": SCHEMA_VERSION,
            "txn_group_id": group_id,
            "content_hash": prepared["content_hash"],
            "entry_no": Int64(first_entry_no + offset),
            "leg_no": leg_no,
            "leg_key": leg["leg_key"],
            "entity_type": leg["entity_type"],
            "entity_id": leg["entity_id"],
            "sub_account": leg["sub_account"],
            "entry_type": leg["entry_type"],
            "amount": leg["amount"],
            "amount_minor": Int64(leg["amount_minor"]),
            "side": leg["side"],
            "currency": CURRENCY,
            "status": "posted",
            "effective_at": prepared["effective_at"],
            "source": prepared["source"],
            "metadata": deepcopy(leg["metadata"]),
            "posted_at": posted_at,
            "posted_by": actor_id,
            "created_at": posted_at,
        })
    event_type = "journal_reversed" if prepared["reversal_of_txn_group_id"] else "journal_posted"
    audit = {
        "_id": audit_id,
        "id": audit_id,
        "user_id": owner,
        "operation_id": OPERATION_ID,
        "schema_version": SCHEMA_VERSION,
        "currency": CURRENCY,
        "event_type": event_type,
        "txn_group_id": group_id,
        "reversal_of_txn_group_id": prepared["reversal_of_txn_group_id"],
        "content_hash": prepared["content_hash"],
        "actor_id": actor_id,
        "actor_name": actor_name,
        "effective_at": prepared["effective_at"],
        "recorded_at": posted_at,
        "summary": {
            "txn_type": prepared["txn_type"],
            "source": prepared["source"],
            "entry_count": prepared["entry_count"],
            "debit_total": prepared["debit_total"],
            "credit_total": prepared["credit_total"],
            "debit_total_minor": Int64(prepared["debit_total_minor"]),
            "credit_total_minor": Int64(prepared["credit_total_minor"]),
        },
    }
    await db[GROUPS_COLLECTION].insert_one(group, session=session)
    await db[GENERAL_LEDGER_COLLECTION].insert_many(legs, ordered=True, session=session)
    await db[AUDIT_COLLECTION].insert_one(audit, session=session)
    return {"txn_group_id": group_id, "existing": False}


async def _existing_idempotent_group(
    db: Any,
    *,
    prepared: Mapping[str, Any],
    session: Any,
) -> dict[str, Any] | None:
    existing = await db[GROUPS_COLLECTION].find_one(
        {
            "user_id": prepared["user_id"],
            "operation_id": OPERATION_ID,
            "idempotency_key": prepared["idempotency_key"],
        },
        session=session,
    )
    if not existing:
        return None
    if existing.get("content_hash") != prepared["content_hash"]:
        _fail(
            "accounting_v2_idempotency_conflict",
            "The idempotency key already belongs to different journal content",
            txn_group_id=existing.get("txn_group_id"),
        )
    return {"txn_group_id": existing["txn_group_id"], "existing": True}


async def _require_opening_gate(
    db: Any,
    *,
    user_id: str,
    effective_at: str,
    session: Any,
) -> dict[str, Any]:
    openings = await db[GROUPS_COLLECTION].find(
        {
            "user_id": user_id,
            "operation_id": OPERATION_ID,
            "txn_type": "opening_balance",
        },
        session=session,
    ).limit(2).to_list(2)
    if len(openings) != 1:
        _fail(
            "accounting_v2_verified_opening_required",
            "Exactly one verified opening journal is required before operational posting",
            opening_count=len(openings),
        )
    opening_id = openings[0].get("txn_group_id")
    opening = await _load_journal(
        db,
        user_id=user_id,
        txn_group_id=opening_id,
        session=session,
    )
    verification = _verify_loaded(opening)
    if not verification["verified"]:
        _fail(
            "accounting_v2_opening_integrity_failure",
            "The Accounting V2 opening journal failed verification",
            txn_group_id=opening_id,
            errors=verification["errors"],
        )
    reversed_opening = await db[GROUPS_COLLECTION].find_one(
        {
            "user_id": user_id,
            "operation_id": OPERATION_ID,
            "reversal_of_txn_group_id": opening_id,
        },
        session=session,
    )
    if reversed_opening:
        _fail(
            "accounting_v2_opening_reversed",
            "The opening journal has been reversed; operational posting is blocked",
            opening_txn_group_id=opening_id,
            reversal_txn_group_id=reversed_opening.get("txn_group_id"),
        )
    assert opening is not None
    opening_effective_at = opening["group"]["effective_at"]
    if effective_at < opening_effective_at:
        _fail(
            "accounting_v2_effective_before_cutover",
            "Journal effective_at cannot be earlier than the opening cutover",
            cutover_at=opening_effective_at,
            effective_at=effective_at,
        )
    return opening


def _public(document: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(document))
    result.pop("_id", None)
    return result


async def _load_journal(
    db: Any,
    *,
    user_id: str,
    txn_group_id: str,
    session: Any = None,
) -> dict[str, Any] | None:
    kwargs = {"session": session} if session is not None else {}
    selector = {
        "user_id": user_id,
        "operation_id": OPERATION_ID,
        "txn_group_id": txn_group_id,
    }
    group = await db[GROUPS_COLLECTION].find_one(
        {**selector, "_id": txn_group_id},
        **kwargs,
    )
    if not group:
        return None
    entries = await db[GENERAL_LEDGER_COLLECTION].find(
        selector,
        **kwargs,
    ).sort("leg_no", 1).to_list(MAX_JOURNAL_LEGS + 1)
    audits = await db[AUDIT_COLLECTION].find(
        selector,
        **kwargs,
    ).to_list(2)
    return {
        "group": _public(group),
        "entries": [_public(entry) for entry in entries],
        "audit": [_public(event) for event in audits],
    }


async def get_journal_v2(
    db: Any,
    *,
    user_id: str,
    txn_group_id: str,
) -> dict[str, Any] | None:
    """Return one tenant-scoped V2 group, its legs, and immutable audit."""
    owner = _required_text(user_id, field="user_id")
    group_id = _required_text(txn_group_id, field="txn_group_id")
    return await _load_journal(db, user_id=owner, txn_group_id=group_id)


def _verify_loaded(journal: Mapping[str, Any] | None) -> dict[str, Any]:
    if not journal:
        return {
            "verified": False,
            "errors": ["journal_not_found"],
            "txn_group_id": None,
            "content_hash": None,
            "entry_count": 0,
            "debit_total": "0.00",
            "credit_total": "0.00",
        }
    group = journal["group"]
    entries = journal["entries"]
    audits = journal["audit"]
    errors: list[str] = []

    def add(error: str) -> None:
        if error not in errors:
            errors.append(error)

    group_id = group.get("txn_group_id")
    if group.get("id") != group_id:
        add("journal_group_id_mismatch")
    if (
        isinstance(group.get("user_id"), str)
        and isinstance(group.get("idempotency_key"), str)
        and group_id
        != _stable_id(
            "mz2j",
            OPERATION_ID,
            group["user_id"],
            group["idempotency_key"],
        )
    ):
        add("journal_group_id_mismatch")
    if group.get("operation_id") != OPERATION_ID:
        add("journal_operation_mismatch")
    if group.get("schema_version") != SCHEMA_VERSION:
        add("journal_schema_mismatch")
    if group.get("currency") != CURRENCY:
        add("journal_currency_mismatch")
    if group.get("status") != "posted":
        add("journal_status_mismatch")
    try:
        effective_at = _utc_iso(group.get("effective_at"))
        if effective_at != group.get("effective_at"):
            add("journal_effective_at_not_canonical")
    except AccountingLedgerV2Error:
        effective_at = str(group.get("effective_at") or "")
        add("journal_effective_at_invalid")
    group_entry_count = group.get("entry_count")
    if (
        isinstance(group_entry_count, bool)
        or not isinstance(group_entry_count, int)
        or len(entries) != group_entry_count
    ):
        add("journal_entry_count_mismatch")

    debit = Decimal("0.00")
    credit = Decimal("0.00")
    normalized_entries: list[dict[str, Any]] = []
    leg_numbers: set[int] = set()
    leg_keys: set[str] = set()
    entry_numbers: set[int] = set()
    for entry in entries:
        if entry.get("user_id") != group.get("user_id"):
            add("journal_entry_user_mismatch")
        if entry.get("operation_id") != OPERATION_ID:
            add("journal_entry_operation_mismatch")
        if entry.get("schema_version") != SCHEMA_VERSION:
            add("journal_entry_schema_mismatch")
        if entry.get("txn_group_id") != group_id:
            add("journal_entry_group_mismatch")
        if entry.get("content_hash") != group.get("content_hash"):
            add("journal_entry_hash_mismatch")
        if entry.get("currency") != CURRENCY:
            add("journal_entry_currency_mismatch")
        if entry.get("status") != "posted":
            add("journal_entry_status_mismatch")
        if entry.get("effective_at") != effective_at:
            add("journal_entry_effective_at_mismatch")
        if entry.get("source") != group.get("source"):
            add("journal_entry_source_mismatch")
        if entry.get("posted_at") != group.get("posted_at"):
            add("journal_entry_posted_at_mismatch")
        if entry.get("posted_by") != group.get("posted_by"):
            add("journal_entry_actor_mismatch")
        leg_no = entry.get("leg_no")
        entry_no = entry.get("entry_no")
        leg_key = entry.get("leg_key")
        if isinstance(leg_no, bool) or not isinstance(leg_no, int) or leg_no in leg_numbers:
            add("journal_leg_number_invalid")
        else:
            leg_numbers.add(leg_no)
        if not isinstance(entry_no, Int64) or entry_no in entry_numbers:
            add("journal_entry_number_invalid")
        else:
            entry_numbers.add(entry_no)
        if not isinstance(leg_key, str) or leg_key in leg_keys:
            add("journal_leg_key_invalid")
        else:
            leg_keys.add(leg_key)
        try:
            normalized, amount_decimal = _normalize_leg({
                "leg_key": entry.get("leg_key"),
                "entity_type": entry.get("entity_type"),
                "entity_id": entry.get("entity_id"),
                "sub_account": entry.get("sub_account"),
                "entry_type": entry.get("entry_type"),
                "amount": entry.get("amount"),
                "side": entry.get("side"),
                "metadata": entry.get("metadata") or {},
            })
            normalized_entries.append(normalized)
            if not isinstance(entry.get("amount_minor"), Int64) or (
                entry.get("amount_minor") != normalized["amount_minor"]
            ):
                add("journal_entry_amount_minor_mismatch")
            if normalized["side"] == "debit":
                debit += amount_decimal
            else:
                credit += amount_decimal
            expected_id = _stable_id("mz2e", str(group_id), normalized["leg_key"])
            if entry.get("id") != expected_id:
                add("journal_entry_id_mismatch")
        except AccountingLedgerV2Error:
            add("journal_entry_invalid")

    if leg_numbers != set(range(1, len(entries) + 1)):
        add("journal_leg_sequence_invalid")
    first_entry_no = group.get("first_entry_no")
    last_entry_no = group.get("last_entry_no")
    if (
        not isinstance(first_entry_no, Int64)
        or not isinstance(last_entry_no, Int64)
    ):
        add("journal_entry_number_range_invalid")
    else:
        expected_entry_numbers = set(range(first_entry_no, last_entry_no + 1))
        if (
            last_entry_no - first_entry_no + 1 != len(entries)
            or entry_numbers != expected_entry_numbers
        ):
            add("journal_entry_number_range_invalid")
    debit_text = _money(debit)
    credit_text = _money(credit)
    debit_minor = int(debit * 100)
    credit_minor = int(credit * 100)
    if debit != credit:
        add("journal_unbalanced")
    if group.get("debit_total") != debit_text:
        add("journal_debit_total_mismatch")
    if group.get("credit_total") != credit_text:
        add("journal_credit_total_mismatch")
    if (
        not isinstance(group.get("debit_total_minor"), Int64)
        or group.get("debit_total_minor") != debit_minor
    ):
        add("journal_debit_total_minor_mismatch")
    if (
        not isinstance(group.get("credit_total_minor"), Int64)
        or group.get("credit_total_minor") != credit_minor
    ):
        add("journal_credit_total_minor_mismatch")

    try:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "operation_id": OPERATION_ID,
            "user_id": group.get("user_id"),
            "idempotency_key": group.get("idempotency_key"),
            "txn_type": group.get("txn_type"),
            "source": group.get("source"),
            "effective_at": effective_at,
            "currency": CURRENCY,
            "notes": group.get("notes") or "",
            "metadata": _metadata(group.get("metadata") or {}),
            "reversal_of_txn_group_id": group.get("reversal_of_txn_group_id"),
            "entries": normalized_entries,
        }
        if _digest(payload) != group.get("content_hash"):
            add("journal_content_hash_mismatch")
    except AccountingLedgerV2Error:
        add("journal_content_hash_mismatch")

    expected_event = "journal_reversed" if group.get("reversal_of_txn_group_id") else "journal_posted"
    matching_audits = [event for event in audits if event.get("event_type") == expected_event]
    if len(audits) != 1 or len(matching_audits) != 1:
        add("journal_audit_missing_or_duplicated")
    else:
        audit = matching_audits[0]
        expected_audit_id = _stable_id("mz2audit", str(group_id), "posted")
        audit_summary = audit.get("summary") or {}
        if (
            audit.get("id") != group.get("audit_id")
            or audit.get("id") != expected_audit_id
            or audit.get("user_id") != group.get("user_id")
            or audit.get("operation_id") != OPERATION_ID
            or audit.get("schema_version") != SCHEMA_VERSION
            or audit.get("currency") != CURRENCY
            or audit.get("txn_group_id") != group_id
            or audit.get("content_hash") != group.get("content_hash")
            or audit.get("effective_at") != group.get("effective_at")
            or audit.get("recorded_at") != group.get("posted_at")
            or audit.get("actor_id") != group.get("posted_by")
            or audit.get("actor_name") != group.get("posted_by_name")
            or audit.get("reversal_of_txn_group_id")
            != group.get("reversal_of_txn_group_id")
            or not isinstance(audit_summary.get("debit_total_minor"), Int64)
            or not isinstance(audit_summary.get("credit_total_minor"), Int64)
            or audit_summary
            != {
                "txn_type": group.get("txn_type"),
                "source": group.get("source"),
                "entry_count": len(entries),
                "debit_total": debit_text,
                "credit_total": credit_text,
                "debit_total_minor": debit_minor,
                "credit_total_minor": credit_minor,
            }
        ):
            add("journal_audit_mismatch")

    return {
        "verified": not errors,
        "errors": errors,
        "txn_group_id": group_id,
        "content_hash": group.get("content_hash"),
        "entry_count": len(entries),
        "debit_total": debit_text,
        "credit_total": credit_text,
    }


async def verify_journal_v2(
    db: Any,
    *,
    user_id: str,
    txn_group_id: str,
    mongo_session: Any = None,
) -> dict[str, Any]:
    """Independently reconstruct and verify one V2 journal.

    A future workflow may pass its active Mongo transaction to verify a newly
    inserted journal before commit.  Omitting the session verifies committed
    storage only.
    """
    owner = _required_text(user_id, field="user_id")
    group_id = _required_text(txn_group_id, field="txn_group_id")
    journal = await _load_journal(
        db,
        user_id=owner,
        txn_group_id=group_id,
        session=mongo_session,
    )
    return _verify_loaded(journal)


async def _verified_result(
    db: Any,
    *,
    user_id: str,
    txn_group_id: str,
    session: Any = None,
) -> dict[str, Any]:
    journal = await _load_journal(
        db,
        user_id=user_id,
        txn_group_id=txn_group_id,
        session=session,
    )
    verification = _verify_loaded(journal)
    if not verification["verified"]:
        _fail(
            "accounting_v2_journal_integrity_failure",
            "The committed Accounting V2 journal failed verification",
            txn_group_id=txn_group_id,
            errors=verification["errors"],
        )
    assert journal is not None
    return journal


async def _post_prepared_v2(
    db: Any,
    *,
    actor_id: str,
    actor_name: str,
    prepared: Mapping[str, Any],
    write_mode: object,
    mongo_session: Any = None,
) -> dict[str, Any]:
    if write_mode not in {_OPERATIONAL_WRITE_MODE, _OPENING_WRITE_MODE}:
        _fail(
            "accounting_v2_internal_write_contract",
            "Unsupported sealed post mode",
        )
    _validate_prepared_write_mode(prepared, write_mode)
    owner = prepared["user_id"]

    async def insert(session: Any) -> dict[str, Any]:
        existing = await _existing_idempotent_group(
            db,
            prepared=prepared,
            session=session,
        )
        if existing:
            return existing
        if write_mode is _OPERATIONAL_WRITE_MODE:
            await _require_opening_gate(
                db,
                user_id=owner,
                effective_at=prepared["effective_at"],
                session=session,
            )
        return await _insert_prepared_journal(
            db,
            prepared=prepared,
            actor_id=actor_id,
            actor_name=actor_name,
            write_mode=write_mode,
            session=session,
        )

    marker = await _run_atomic(db, insert, mongo_session=mongo_session)
    return await _verified_result(
        db,
        user_id=owner,
        txn_group_id=marker["txn_group_id"],
        session=mongo_session,
    )


async def post_journal_v2(
    db: Any,
    *,
    user_id: str,
    actor_id: str,
    actor_name: str,
    idempotency_key: str,
    txn_type: str,
    source: str,
    effective_at: str,
    entries: Sequence[Mapping[str, Any]],
    notes: str = "",
    metadata: Mapping[str, Any] | None = None,
    mongo_session: Any = None,
) -> dict[str, Any]:
    """Append one operational journal after the verified opening cutover.

    Permission and ``safe_active`` remain mandatory caller gates.  This sealed
    storage boundary independently enforces the opening and effective-time
    invariants and is not connected to an HTTP endpoint in Phase B.
    """
    owner = _required_text(user_id, field="user_id")
    actor = _required_text(actor_id, field="actor_id")
    actor_label = _required_text(actor_name, field="actor_name")
    normalized_type = _slug(txn_type, field="txn_type")
    if normalized_type in _RESERVED_PUBLIC_TXN_TYPES:
        _fail(
            "accounting_v2_reserved_txn_type",
            "Opening and reversal journals must use their sealed wrappers",
        )
    prepared = _prepare_journal(
        user_id=owner,
        idempotency_key=idempotency_key,
        txn_type=normalized_type,
        source=source,
        effective_at=effective_at,
        entries=entries,
        notes=notes,
        metadata=metadata,
    )
    return await _post_prepared_v2(
        db,
        actor_id=actor,
        actor_name=actor_label,
        prepared=prepared,
        write_mode=_OPERATIONAL_WRITE_MODE,
        mongo_session=mongo_session,
    )


async def post_opening_journal_v2(
    db: Any,
    *,
    user_id: str,
    actor_id: str,
    actor_name: str,
    opening_operation_id: str,
    approved_preview_hash: str,
    effective_at: str,
    entries: Sequence[Mapping[str, Any]],
    mongo_session: Any = None,
) -> dict[str, Any]:
    """Append the book's sole opening group without activating the book.

    P07 must independently validate the persisted approval and pass its Mongo
    transaction here.  This function only binds the supplied approval identity
    and preview hash into the immutable group.  P07 also owns proof that every
    supplied balance is a new-book cutover value rather than legacy replay;
    arbitrary leg metadata cannot prove business provenance at this layer.
    """
    owner = _required_text(user_id, field="user_id")
    actor = _required_text(actor_id, field="actor_id")
    actor_label = _required_text(actor_name, field="actor_name")
    opening_id = _required_text(
        opening_operation_id,
        field="opening_operation_id",
    )
    preview_hash = _required_text(
        approved_preview_hash,
        field="approved_preview_hash",
        max_length=64,
    ).lower()
    if not _HEX_64.fullmatch(preview_hash):
        _fail(
            "approved_preview_hash_invalid",
            "Approved opening preview hash must be a SHA-256 hex digest",
        )
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("entry_type") != "opening_balance":
            _fail(
                "opening_entry_type_required",
                "Every opening journal leg must use opening_balance",
            )
    prepared = _prepare_journal(
        user_id=owner,
        idempotency_key=f"opening:{opening_id}",
        txn_type="opening_balance",
        source="accounting_opening_balances_v2",
        effective_at=effective_at,
        entries=entries,
        notes="Accounting V2 opening journal",
        metadata={
            "opening_operation_id": opening_id,
            "approved_preview_hash": preview_hash,
            "historical_import": False,
            "legacy_financial_data_included": False,
        },
    )
    return await _post_prepared_v2(
        db,
        actor_id=actor,
        actor_name=actor_label,
        prepared=prepared,
        write_mode=_OPENING_WRITE_MODE,
        mongo_session=mongo_session,
    )


async def reverse_journal_v2(
    db: Any,
    *,
    user_id: str,
    actor_id: str,
    actor_name: str,
    original_txn_group_id: str,
    effective_at: str,
    reason: str,
    mongo_session: Any = None,
) -> dict[str, Any]:
    """Append exactly one opposite group; never update the original group."""
    owner = _required_text(user_id, field="user_id")
    actor = _required_text(actor_id, field="actor_id")
    actor_label = _required_text(actor_name, field="actor_name")
    original_id = _required_text(
        original_txn_group_id,
        field="original_txn_group_id",
    )
    normalized_effective = _utc_iso(effective_at)
    normalized_reason = _required_text(reason, field="reversal_reason", max_length=1_000)

    async def insert(session: Any) -> dict[str, Any]:
        existing_reversal = await db[GROUPS_COLLECTION].find_one(
            {
                "user_id": owner,
                "operation_id": OPERATION_ID,
                "reversal_of_txn_group_id": original_id,
            },
            session=session,
        )
        if existing_reversal:
            if (
                existing_reversal.get("effective_at") != normalized_effective
                or existing_reversal.get("notes") != normalized_reason
            ):
                _fail(
                    "accounting_v2_reversal_conflict",
                    "The original journal already has a different reversal request",
                    reversal_txn_group_id=existing_reversal.get("txn_group_id"),
                )
            return {
                "txn_group_id": existing_reversal["txn_group_id"],
                "existing": True,
            }
        original = await _load_journal(
            db,
            user_id=owner,
            txn_group_id=original_id,
            session=session,
        )
        if not original:
            _fail("accounting_v2_journal_not_found", "Original journal does not exist")
        verification = _verify_loaded(original)
        if not verification["verified"]:
            _fail(
                "accounting_v2_journal_integrity_failure",
                "Original journal failed verification and cannot be reversed",
                errors=verification["errors"],
            )
        original_group = original["group"]
        if original_group.get("reversal_of_txn_group_id"):
            _fail("accounting_v2_reverse_of_reversal_forbidden", "A reversal cannot be reversed")
        if normalized_effective < original_group["effective_at"]:
            _fail(
                "accounting_v2_reversal_before_original",
                "A reversal cannot be effective before its original journal",
            )
        await _require_opening_gate(
            db,
            user_id=owner,
            effective_at=normalized_effective,
            session=session,
        )
        reversed_entries: list[dict[str, Any]] = []
        for entry in original["entries"]:
            reverse_key = _stable_id("revleg", original_id, entry["leg_key"])
            reversed_entries.append({
                "leg_key": reverse_key,
                "entity_type": entry["entity_type"],
                "entity_id": entry["entity_id"],
                "sub_account": entry.get("sub_account"),
                "entry_type": "reversal",
                "amount": entry["amount"],
                "side": "credit" if entry["side"] == "debit" else "debit",
                "metadata": {
                    "reverses_entry_id": entry["id"],
                    "reverses_leg_key": entry["leg_key"],
                    "original_entry_type": entry["entry_type"],
                },
            })
        prepared = _prepare_journal(
            user_id=owner,
            idempotency_key=f"reversal:{original_id}",
            txn_type="reversal",
            source="accounting_ledger_v2_reversal",
            effective_at=normalized_effective,
            entries=reversed_entries,
            notes=normalized_reason,
            metadata={
                "reason": normalized_reason,
                "original_content_hash": original_group["content_hash"],
            },
            reversal_of_txn_group_id=original_id,
        )
        return await _insert_prepared_journal(
            db,
            prepared=prepared,
            actor_id=actor,
            actor_name=actor_label,
            write_mode=_REVERSAL_WRITE_MODE,
            session=session,
        )

    marker = await _run_atomic(db, insert, mongo_session=mongo_session)
    return await _verified_result(
        db,
        user_id=owner,
        txn_group_id=marker["txn_group_id"],
        session=mongo_session,
    )


def _entry_read_query(
    *,
    user_id: str,
    txn_group_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    sub_account: str | None = None,
    entry_type: str | None = None,
    effective_from: str | None = None,
    effective_through: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "user_id": _required_text(user_id, field="user_id"),
    }
    if txn_group_id is not None:
        query["txn_group_id"] = _required_text(txn_group_id, field="txn_group_id")
    if entity_type is not None:
        query["entity_type"] = _slug(entity_type, field="entity_type")
    if entity_id is not None:
        query["entity_id"] = _required_text(entity_id, field="entity_id")
    if sub_account is not None:
        query["sub_account"] = _slug(sub_account, field="sub_account")
    if entry_type is not None:
        query["entry_type"] = _slug(entry_type, field="entry_type")
    effective: dict[str, str] = {}
    if effective_from is not None:
        effective["$gte"] = _utc_iso(effective_from, field="effective_from")
    if effective_through is not None:
        effective["$lte"] = _utc_iso(effective_through, field="effective_through")
    if effective.get("$gte") and effective.get("$lte"):
        if effective["$gte"] > effective["$lte"]:
            _fail(
                "effective_range_invalid",
                "effective_from cannot be later than effective_through",
            )
    if effective:
        query["effective_at"] = effective
    return query


async def query_entries_v2(
    db: Any,
    *,
    user_id: str,
    txn_group_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    sub_account: str | None = None,
    entry_type: str | None = None,
    effective_from: str | None = None,
    effective_through: str | None = None,
    after_entry_no: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return tenant-scoped V2 legs without exposing a physical collection."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
        _fail("query_limit_invalid", "Query limit must be between 1 and 1000")
    query = _entry_read_query(
        user_id=user_id,
        txn_group_id=txn_group_id,
        entity_type=entity_type,
        entity_id=entity_id,
        sub_account=sub_account,
        entry_type=entry_type,
        effective_from=effective_from,
        effective_through=effective_through,
    )
    if after_entry_no is not None:
        if (
            isinstance(after_entry_no, bool)
            or not isinstance(after_entry_no, int)
            or after_entry_no < 0
        ):
            _fail("after_entry_no_invalid", "after_entry_no must be a non-negative integer")
        query["entry_no"] = {"$gt": after_entry_no}
    rows = await db[GENERAL_LEDGER_COLLECTION].find(query).sort("entry_no", 1).limit(limit).to_list(
        limit
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        _stored_amount_minor(row)
        if (
            row.get("operation_id") != OPERATION_ID
            or row.get("schema_version") != SCHEMA_VERSION
            or row.get("currency") != CURRENCY
            or row.get("status") != "posted"
        ):
            _fail(
                "accounting_v2_journal_integrity_failure",
                "A queried Accounting V2 leg has invalid provenance",
                entry_id=row.get("id"),
            )
        result.append(_public(row))
    return result


async def aggregate_balances_v2(
    db: Any,
    *,
    user_id: str,
    group_by: Sequence[str] = ("entity_type", "entity_id", "sub_account"),
    entity_type: str | None = None,
    entity_id: str | None = None,
    sub_account: str | None = None,
    entry_type: str | None = None,
    effective_from: str | None = None,
    effective_through: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate exact SAR minor units behind the sealed V2 access boundary."""
    allowed_group_fields = {
        "entity_type",
        "entity_id",
        "sub_account",
        "entry_type",
        "txn_group_id",
    }
    if (
        isinstance(group_by, (str, bytes))
        or not isinstance(group_by, Sequence)
        or not group_by
        or len(group_by) > len(allowed_group_fields)
        or any(not isinstance(field, str) for field in group_by)
        or len(set(group_by)) != len(group_by)
        or any(field not in allowed_group_fields for field in group_by)
    ):
        _fail("aggregate_group_by_invalid", "Unsupported Accounting V2 grouping")
    query = _entry_read_query(
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        sub_account=sub_account,
        entry_type=entry_type,
        effective_from=effective_from,
        effective_through=effective_through,
    )
    group_id = {field: f"${field}" for field in group_by}
    parsed_amount = {
        "$convert": {
            "input": "$amount",
            "to": "decimal",
            "onError": None,
            "onNull": None,
        }
    }
    valid_minor = {
        "$and": [
            {"$eq": ["$operation_id", OPERATION_ID]},
            {"$eq": ["$schema_version", SCHEMA_VERSION]},
            {"$eq": ["$currency", CURRENCY]},
            {"$eq": ["$status", "posted"]},
            {"$eq": [{"$type": "$amount_minor"}, "long"]},
            {"$gt": ["$amount_minor", 0]},
            {"$lte": ["$amount_minor", MAX_AMOUNT_MINOR]},
            {
                "$regexMatch": {
                    "input": {
                        "$cond": [
                            {"$eq": [{"$type": "$amount"}, "string"]},
                            "$amount",
                            "",
                        ]
                    },
                    "regex": r"^(0|[1-9][0-9]*)\.[0-9]{2}$",
                }
            },
            {
                "$eq": [
                    {"$multiply": [parsed_amount, 100]},
                    "$amount_minor",
                ]
            },
        ]
    }
    pipeline = [
        {"$match": query},
        {
            "$group": {
                "_id": group_id,
                "debit_total_minor": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$side", "debit"]},
                            "$amount_minor",
                            Int64(0),
                        ]
                    }
                },
                "credit_total_minor": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$side", "credit"]},
                            "$amount_minor",
                            Int64(0),
                        ]
                    }
                },
                "entry_count": {"$sum": 1},
                "debit_entry_count": {
                    "$sum": {"$cond": [{"$eq": ["$side", "debit"]}, 1, 0]}
                },
                "credit_entry_count": {
                    "$sum": {"$cond": [{"$eq": ["$side", "credit"]}, 1, 0]}
                },
                "valid_minor_count": {"$sum": {"$cond": [valid_minor, 1, 0]}},
            }
        },
        {"$sort": {f"_id.{field}": 1 for field in group_by}},
        {"$limit": MAX_AGGREGATE_GROUPS + 1},
    ]
    rows = await db[GENERAL_LEDGER_COLLECTION].aggregate(
        pipeline,
        allowDiskUse=False,
    ).to_list(MAX_AGGREGATE_GROUPS + 1)
    if len(rows) > MAX_AGGREGATE_GROUPS:
        _fail(
            "aggregate_group_limit_exceeded",
            "Accounting V2 aggregation exceeded its safe group limit",
        )
    result: list[dict[str, Any]] = []
    for row in rows:
        debit_minor = row.get("debit_total_minor")
        credit_minor = row.get("credit_total_minor")
        entry_count = row.get("entry_count")
        if (
            not isinstance(debit_minor, Int64)
            or not isinstance(credit_minor, Int64)
            or debit_minor < 0
            or credit_minor < 0
            or debit_minor > MAX_AMOUNT_MINOR
            or credit_minor > MAX_AMOUNT_MINOR
            or not isinstance(entry_count, int)
            or row.get("valid_minor_count") != entry_count
            or row.get("debit_entry_count", 0) + row.get("credit_entry_count", 0)
            != entry_count
        ):
            _fail(
                "accounting_v2_aggregate_integrity_failure",
                "Accounting V2 aggregate contains invalid or overflowing minor units",
            )
        result.append({
            "group": deepcopy(row.get("_id") or {}),
            "debit_total": _minor_money(debit_minor),
            "credit_total": _minor_money(credit_minor),
            "net_balance": _minor_money(debit_minor - credit_minor),
            "debit_total_minor": int(debit_minor),
            "credit_total_minor": int(credit_minor),
            "net_balance_minor": int(debit_minor - credit_minor),
            "entry_count": entry_count,
        })
    return result


async def compute_balance_v2(
    db: Any,
    *,
    user_id: str,
    entity_type: str,
    entity_id: str,
    sub_account: str | None = None,
    effective_through: str | None = None,
) -> dict[str, Any]:
    """Compute a balance exclusively from posted V2 legs, including reversals."""
    query: dict[str, Any] = {
        "user_id": _required_text(user_id, field="user_id"),
        "entity_type": _slug(entity_type, field="entity_type"),
        "entity_id": _required_text(entity_id, field="entity_id"),
    }
    if sub_account is not None:
        query["sub_account"] = _slug(sub_account, field="sub_account")
    if effective_through is not None:
        query["effective_at"] = {"$lte": _utc_iso(effective_through, field="effective_through")}
    debit_minor = 0
    credit_minor = 0
    count = 0
    async for entry in db[GENERAL_LEDGER_COLLECTION].find(query):
        value_minor = _stored_amount_minor(entry)
        if (
            entry.get("currency") != CURRENCY
            or entry.get("operation_id") != OPERATION_ID
            or entry.get("schema_version") != SCHEMA_VERSION
            or entry.get("status") != "posted"
        ):
            _fail(
                "accounting_v2_journal_integrity_failure",
                "A stored Accounting V2 leg has invalid provenance",
                entry_id=entry.get("id"),
            )
        if entry.get("side") == "debit":
            debit_minor += value_minor
        elif entry.get("side") == "credit":
            credit_minor += value_minor
        else:
            _fail(
                "accounting_v2_journal_integrity_failure",
                "A stored Accounting V2 leg has an invalid side",
                entry_id=entry.get("id"),
            )
        if debit_minor > MAX_AMOUNT_MINOR or credit_minor > MAX_AMOUNT_MINOR:
            _fail(
                "accounting_v2_balance_overflow",
                "Accounting V2 balance exceeds the signed 64-bit minor-unit limit",
            )
        count += 1
    return {
        "debit_total": _minor_money(debit_minor),
        "credit_total": _minor_money(credit_minor),
        "net_balance": _minor_money(debit_minor - credit_minor),
        "debit_total_minor": debit_minor,
        "credit_total_minor": credit_minor,
        "net_balance_minor": debit_minor - credit_minor,
        "entry_count": count,
    }


async def scan_mz2_rows_in_legacy_ledger(
    db: Any,
    *,
    user_id: str,
    sample_limit: int = 20,
    mongo_session: Any = None,
) -> dict[str, Any]:
    """Read-only, single-snapshot diagnostic for MZ2 tags in legacy storage."""
    owner = _required_text(user_id, field="user_id")
    if isinstance(sample_limit, bool) or not isinstance(sample_limit, int) or sample_limit < 1:
        _fail("legacy_scan_limit_invalid", "Legacy scan limit must be a positive integer")
    limit = min(sample_limit, MAX_LEGACY_SCAN_SAMPLE)
    query = {
        "user_id": owner,
        "$or": [
            {"operation_id": OPERATION_ID},
            {"metadata.operation_id": OPERATION_ID},
            {"metadata.accounting_operation_id": OPERATION_ID},
        ],
    }
    legacy = db["general_ledger"]
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    result = await legacy.aggregate(
        [
            {"$match": query},
            {
                "$facet": {
                    "count": [{"$count": "value"}],
                    "samples": [
                        {"$sort": {"id": 1}},
                        {"$limit": limit},
                        {"$project": {"_id": 0, "id": 1, "txn_group_id": 1}},
                    ],
                }
            },
        ],
        **kwargs,
    ).to_list(1)
    bucket = result[0] if result else {"count": [], "samples": []}
    count_rows = bucket.get("count") or []
    count = int(count_rows[0].get("value") or 0) if count_rows else 0
    rows = bucket.get("samples") or []
    return {
        "clear": count == 0,
        "count": count,
        "sample_entry_ids": [str(row.get("id")) for row in rows if row.get("id")],
        "sample_txn_group_ids": sorted({
            str(row.get("txn_group_id"))
            for row in rows
            if row.get("txn_group_id")
        }),
    }


async def assert_no_mz2_rows_in_legacy_ledger(
    db: Any,
    *,
    user_id: str,
    mongo_session: Any = None,
) -> dict[str, Any]:
    """Fail an in-transaction activation preflight on legacy MZ2 rows.

    P08 must also hold its event fence in this same transaction.  A snapshot
    scan alone cannot stop a legacy writer that ignores that future fence.
    """
    if mongo_session is None or not bool(getattr(mongo_session, "in_transaction", False)):
        _fail(
            "accounting_v2_atomic_transaction_required",
            "Legacy-clear activation assertion requires an active caller transaction",
        )
    result = await scan_mz2_rows_in_legacy_ledger(
        db,
        user_id=user_id,
        mongo_session=mongo_session,
    )
    if not result["clear"]:
        _fail(
            "accounting_v2_legacy_rows_detected",
            "MZ2-tagged rows exist in legacy general_ledger; activation is blocked",
            **result,
        )
    return result


async def ensure_accounting_ledger_v2_indexes(db: Any) -> None:
    """Install all V2 integrity and query indexes; never suppress failures."""
    groups = db[GROUPS_COLLECTION]
    ledger = db[GENERAL_LEDGER_COLLECTION]
    audit = db[AUDIT_COLLECTION]
    sequences = db[SEQUENCES_COLLECTION]

    await groups.create_index(
        [("user_id", 1), ("operation_id", 1), ("idempotency_key", 1)],
        unique=True,
        name="uq_accounting_v2_group_idempotency",
    )
    await groups.create_index(
        [("user_id", 1), ("operation_id", 1), ("reversal_of_txn_group_id", 1)],
        unique=True,
        partialFilterExpression={"reversal_of_txn_group_id": {"$type": "string"}},
        name="uq_accounting_v2_group_reversal",
    )
    await groups.create_index(
        [("user_id", 1), ("operation_id", 1), ("txn_type", 1)],
        unique=True,
        partialFilterExpression={"txn_type": "opening_balance"},
        name="uq_accounting_v2_opening",
    )
    await groups.create_index(
        [("user_id", 1), ("operation_id", 1), ("effective_at", -1)],
        name="ix_accounting_v2_groups_effective",
    )
    await ledger.create_index(
        [("user_id", 1), ("operation_id", 1), ("txn_group_id", 1), ("leg_no", 1)],
        unique=True,
        name="uq_accounting_v2_leg_number",
    )
    await ledger.create_index(
        [("user_id", 1), ("operation_id", 1), ("txn_group_id", 1), ("leg_key", 1)],
        unique=True,
        name="uq_accounting_v2_leg_key",
    )
    await ledger.create_index(
        [("user_id", 1), ("operation_id", 1), ("entry_no", 1)],
        unique=True,
        name="uq_accounting_v2_entry_number",
    )
    await ledger.create_index(
        [
            ("user_id", 1),
            ("operation_id", 1),
            ("entity_type", 1),
            ("entity_id", 1),
            ("sub_account", 1),
            ("effective_at", 1),
            ("status", 1),
        ],
        name="ix_accounting_v2_entity_balance",
    )
    await ledger.create_index(
        [
            ("user_id", 1),
            ("operation_id", 1),
            ("effective_at", 1),
            ("entry_type", 1),
            ("entity_type", 1),
        ],
        name="ix_accounting_v2_reporting",
    )
    await audit.create_index(
        [("user_id", 1), ("operation_id", 1), ("id", 1)],
        unique=True,
        name="uq_accounting_v2_audit_id",
    )
    await audit.create_index(
        [("user_id", 1), ("operation_id", 1), ("txn_group_id", 1), ("recorded_at", 1)],
        name="ix_accounting_v2_audit_group",
    )
    await sequences.create_index(
        [("user_id", 1), ("operation_id", 1)],
        unique=True,
        name="uq_accounting_v2_sequence",
    )


__all__ = [
    "AUDIT_COLLECTION",
    "CURRENCY",
    "GENERAL_LEDGER_COLLECTION",
    "GROUPS_COLLECTION",
    "OPERATION_ID",
    "SCHEMA_VERSION",
    "SEQUENCES_COLLECTION",
    "AccountingLedgerV2Error",
    "aggregate_balances_v2",
    "assert_no_mz2_rows_in_legacy_ledger",
    "compute_balance_v2",
    "ensure_accounting_ledger_v2_indexes",
    "get_journal_v2",
    "post_journal_v2",
    "post_opening_journal_v2",
    "query_entries_v2",
    "reverse_journal_v2",
    "scan_mz2_rows_in_legacy_ledger",
    "verify_journal_v2",
]
