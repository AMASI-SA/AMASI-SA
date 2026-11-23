"""Owner-scoped, bounded reads of accepted Tabby weekly fee evidence.

Only saved acceptance proofs identify eligible immutable statement documents.
No legacy settlement scan, source refresh, index creation or financial write is
part of this report reader. The same reader supplies dashboards and balances.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json

from fastapi import HTTPException
from pymongo.errors import PyMongoError

from operational_financial_activity_runtime import prepare_in_thread
from operational_provider_statement_fees import (
    select_statement_average_profile, statement_average_summary,
    statement_average_source_summary,
)
from provider_fee_weekly_average import (
    MAX_SOURCES, MAX_SOURCE_ROWS, WINDOW_DAYS, RIYADH,
    derive_weekly_statement_average, _instant, _report_boundary,
)

STATES = "operational_balance_states_v1"
STATEMENTS = "operational_provider_statements_v1"
STATE_PROJECTION = {
    "_id": 1, "owner_id": 1, "status": 1, "started_at": 1, "revision": 1,
    "engine.as_of": 1,
    "provider_statement_reconciliation.statement_summaries": 1,
    "provider_statement_reconciliation.average_profiles": 1,
}
DOCUMENT_PROJECTION = {
    "_id": 1, "id": 1, "owner_id": 1, "uploaded_at": 1,
    "content_hash": 1, "statement": 1,
}


async def load_weekly_fee_source_state(db, owner):
    if not isinstance(owner, str) or not owner.strip() or owner != owner.strip():
        raise ValueError("weekly_statement_source_owner_invalid")
    try:
        state = await db[STATES].find_one({"_id": owner, "owner_id": owner}, STATE_PROJECTION)
    except (PyMongoError, TimeoutError, HTTPException):
        raise ValueError("weekly_statement_source_unavailable") from None
    return state or {}


def weekly_fee_source_revision(state):
    """Bind report caches to accepted evidence without changing tariff identity."""
    payload = {key: state.get(key) for key in (
        "owner_id", "revision", "status", "started_at", "provider_statement_reconciliation")}
    payload["calculated_as_of"] = (state.get("engine") or {}).get("as_of")
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), default=str).encode()).hexdigest()


def _unavailable(currencies, reason):
    return {currency: {"available": False, "profile": None, "reason_code": reason}
            for currency in currencies}


def _accepted_sources(state, currencies, evidence_as_of):
    """Select proof identities first; never infer acceptance from a raw file."""
    reconciliation = state.get("provider_statement_reconciliation") or {}
    if not isinstance(reconciliation, dict):
        raise ValueError("weekly_statement_acceptance_invalid")
    summaries = reconciliation.get("statement_summaries") or {}
    profiles = reconciliation.get("average_profiles") or {}
    if not isinstance(summaries, dict) or not isinstance(profiles, dict):
        raise ValueError("weekly_statement_acceptance_invalid")
    _, acceptance_limit = _report_boundary(evidence_as_of)
    proofs = {}
    for identity, summary in summaries.items():
        proof = summary.get("statement_average") if isinstance(summary, dict) else None
        if not isinstance(proof, dict) or not proof.get("available") or proof.get("provider") != "tabby":
            continue
        if proof.get("native_currency") not in currencies or _instant(proof.get("accepted_at")) > acceptance_limit:
            continue
        if not isinstance(identity, str) or not identity or proof.get("statement_id") != identity:
            raise ValueError("weekly_statement_acceptance_invalid")
        proofs[identity] = {"accepted_summary": deepcopy(proof)}
    # Older accepted imports may retain a full active profile without a review
    # summary. Its exact recomputed hash/terms are still verified by the utility.
    for profile in profiles.values():
        if not isinstance(profile, dict) or profile.get("provider") != "tabby":
            continue
        if profile.get("native_currency") not in currencies or _instant(profile.get("accepted_at")) > acceptance_limit:
            continue
        if profile.get("status") != "active":
            raise ValueError("weekly_statement_acceptance_ambiguous")
        identity = profile.get("statement_id")
        if not isinstance(identity, str) or not identity:
            raise ValueError("weekly_statement_acceptance_invalid")
        proofs.setdefault(identity, {})["accepted_profile"] = deepcopy(profile)
    return proofs


def _source_day_upper_bound(proof):
    """Only saved day-precision proofs can rule a file out of the week.

    The accepted profile derivation verifies every sale day is no later than
    ``statement_date`` (or uses the maximum sale day itself). An instant-only
    or malformed bound must be examined, never silently treated as old.
    """
    bounds = []
    for key in ("accepted_summary", "accepted_profile"):
        value = proof.get(key)
        if value is None:
            continue
        if (value.get("date_precision") != "day"
                or value.get("source_date_basis") not in {"statement_date", "sale_event_date"}
                or not isinstance(value.get("source_as_of"), str) or len(value["source_as_of"]) != 10):
            return None
        try:
            bound = date.fromisoformat(value["source_as_of"])
            if bound > _instant(value["accepted_at"]).astimezone(RIYADH).date():
                return None
        except (ValueError, TypeError, KeyError):
            return None
        bounds.append(bound)
    return bounds[0] if bounds and len(set(bounds)) == 1 else None


def _operational_average_fallback(state, currency, evidence_as_of):
    """Select the same saved profile as the current operational balance.

    The statement writer updates the active profiles and freezes that same
    mapping into engine fee inputs in one aggregate CAS. Reading its compact
    source mapping avoids unpacking the unrelated financial snapshot; the
    existing selector retains profile, currency and account validation.
    """
    profiles = (state.get("provider_statement_reconciliation") or {}).get("average_profiles") or {}
    view = {"owner_id": state.get("owner_id"), "engine": {
        "provider_fee_inputs": {"average_profiles": list(profiles.values())}}}
    profile = select_statement_average_profile(view, "tabby", currency)
    if profile is None:
        return {"available": False, "profile": None,
                "source_summary": statement_average_source_summary(view, "tabby", currency),
                "basis": "operational_balance_average"}
    _, acceptance_limit = _report_boundary(evidence_as_of)
    if _instant(profile["accepted_at"]) > acceptance_limit:
        return {"available": False, "profile": None, "reason_code": "source_average_not_yet_accepted",
                "basis": "operational_balance_average"}
    return {"available": True, "profile": deepcopy(profile),
            "source_summary": statement_average_summary(profile), "basis": "operational_balance_average",
            "period_start": state.get("started_at"), "period_end": (state.get("engine") or {}).get("as_of")}


async def load_weekly_statement_averages(db, owner, *, as_of, currencies=("SAR",), source_state=None,
                                       evidence_as_of=None):
    """One cohort read, shared by all its orders; parsing runs off the event loop.

    A metadata preflight bounds total source rows before fetching original
    normalized statements. Every query contains the owner and exact saved IDs.
    Report dates select the seven sale days; current accepted knowledge can
    restate historical reports. Only a genuinely empty week permits the exact
    existing operational average. Invalid evidence does not enable a fallback.
    """
    currencies = tuple(sorted(set(currencies)))
    if not currencies or len(currencies) > MAX_SOURCES:
        return _unavailable(currencies, "weekly_statement_source_scope_too_large")
    evidence_as_of = evidence_as_of if evidence_as_of is not None else datetime.now(timezone.utc)
    try:
        state = source_state if source_state is not None else await load_weekly_fee_source_state(db, owner)
        if not state:
            return _unavailable(currencies, "weekly_statement_average_unavailable")
        if state.get("_id") != owner or state.get("owner_id") != owner:
            return _unavailable(currencies, "weekly_statement_source_owner_invalid")
        if state.get("status") != "active":
            return _unavailable(currencies, "weekly_statement_average_unavailable")
        proofs = _accepted_sources(state, currencies, evidence_as_of)
        report_day, _ = _report_boundary(as_of)
        start = report_day - timedelta(days=WINDOW_DAYS)
        # Old accepted summaries remain in state for provenance. Their proved
        # upper bounds let a long history stay outside the bounded raw-file read.
        proofs = {identity: proof for identity, proof in proofs.items()
                  if (bound := _source_day_upper_bound(proof)) is None or bound >= start}
        if len(proofs) > MAX_SOURCES:
            return _unavailable(currencies, "weekly_statement_source_scope_too_large")
        if not proofs:
            results = {currency: derive_weekly_statement_average(owner, [], as_of=as_of,
                       accepted_as_of=evidence_as_of, native_currency=currency) for currency in currencies}
            for currency, result in results.items():
                if result.get("reason_code") == "source_weekly_average_unavailable":
                    result["operational_fallback"] = _operational_average_fallback(state, currency, evidence_as_of)
            return results
        query = {"owner_id": owner, "_id": {"$in": sorted(proofs)}}
        headers = await db[STATEMENTS].find(query, {
            "_id": 1, "id": 1, "owner_id": 1, "metadata.row_count": 1,
        }).limit(MAX_SOURCES + 1).to_list(MAX_SOURCES + 1)
        if (len(headers) != len(proofs) or {row.get("_id") for row in headers} != set(proofs)
                or any(row.get("owner_id") != owner or row.get("id") != row.get("_id") for row in headers)):
            return _unavailable(currencies, "weekly_statement_source_missing")
        row_counts = [(row.get("metadata") or {}).get("row_count") for row in headers]
        if any(type(count) is not int or count < 1 for count in row_counts):
            return _unavailable(currencies, "weekly_statement_source_invalid")
        if sum(row_counts) > MAX_SOURCE_ROWS:
            return _unavailable(currencies, "weekly_statement_source_scope_too_large")
        documents = await db[STATEMENTS].find(query, DOCUMENT_PROJECTION).limit(MAX_SOURCES + 1).to_list(MAX_SOURCES + 1)
        if (len(documents) != len(proofs) or {row.get("_id") for row in documents} != set(proofs)
                or any(row.get("owner_id") != owner for row in documents)):
            return _unavailable(currencies, "weekly_statement_source_missing")
        expected_counts = {row["_id"]: count for row, count in zip(headers, row_counts)}
        if any(not isinstance(row.get("statement"), dict)
               or not isinstance(row["statement"].get("rows"), list)
               or len(row["statement"]["rows"]) != expected_counts[row["_id"]] for row in documents):
            return _unavailable(currencies, "weekly_statement_source_invalid")
        sources = [{**row, **proofs[row["_id"]]} for row in documents]
        def derive():
            results = {currency: derive_weekly_statement_average(owner, sources, as_of=as_of,
                        accepted_as_of=evidence_as_of, native_currency=currency) for currency in currencies}
            for currency, result in results.items():
                if result.get("reason_code") == "source_weekly_average_unavailable":
                    result["operational_fallback"] = _operational_average_fallback(state, currency, evidence_as_of)
            return results
        return await prepare_in_thread(derive)
    except (PyMongoError, TimeoutError, HTTPException):
        return _unavailable(currencies, "weekly_statement_source_unavailable")
    except (ValueError, TypeError, KeyError):
        return _unavailable(currencies, "weekly_statement_source_invalid")
