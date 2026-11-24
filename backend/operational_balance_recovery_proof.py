"""Compact evidence of one committed financial delta, never a recovery writer.

The reducer runs before the enclosing revision CAS. Original-dated additions,
undated/observation-based additions and changes to earlier obligations remain
separate. A receipt verifies its enumeration, not completeness of all sources.
Only aggregate amounts and hashed internal financial identities are retained.
"""
import asyncio
from collections import Counter, defaultdict
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from itertools import chain
import json
from zoneinfo import ZoneInfo

import pymongo

from operational_balance_store import STATES, digest
from operational_entity_freshness import recovery_snapshot
from operational_demand_refresh import calculation_checkpoint, demand_context
from operational_financial_activity_runtime import preparation_scope


FIELD = "recovery_proof_v1"
MAX_RECORDS = 100_000
MAX_GROUPS = 1024
MAX_BYTES = 262_144
READ_SECONDS = 8
RIYADH = ZoneInfo("Asia/Riyadh")
AUTO_FLAGS = ("automatic_order_bank", "automatic_prepaid_shipping")
OBSERVATION_BASES = {"delivered_order_created_after_start", "owner_confirmed_after_start",
                     "aramex_status_created_after_start", "aramex_owner_approved_status_v2"}
DISPLAY_FIELDS = {"name", "label"}


def _checked(rows):
    for row in rows:
        calculation_checkpoint()
        yield row


def _instant(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _day(value):
    try:
        day = date.fromisoformat(value)
        return day if day.isoformat() == value else None
    except (ValueError, TypeError):
        return None


def _money(value):
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() and amount >= 0 else None
    except (ValueError, InvalidOperation):
        return None


def _automatic(row):
    return any(row.get(flag) is True for flag in AUTO_FLAGS)


def _index(rows, key):
    indexed = {}
    for row in _checked(rows):
        if type(row) is dict:
            identity = key(row)
            if identity is not None:
                indexed[identity] = row if identity not in indexed else None
    return indexed


def _preservation(before, after):
    keys = sorted(before)
    present = {key: after[key] for key in keys if key in after}
    changed = sum(before[key] != present[key] for key in present)
    missing = len(keys) - len(present)
    return {"before_count": len(keys), "missing_count": missing, "changed_count": changed,
            "before_digest": digest(before), "after_existing_digest": digest(present),
            "preserved": not missing and not changed}


def _movement_manifest(rows, *, manual_only=False, automatic_only=False):
    positions, result = Counter(), {}
    for row in _checked(rows):
        identity = digest(row.get("id"))
        position = positions[identity]
        positions[identity] += 1
        if (not manual_only or not _automatic(row)) and (not automatic_only or _automatic(row)):
            result[digest([identity, position])] = digest(row)
    return result


def _duplicate_keys(state):
    counts = {key: Counter() for key in ("movement_id", "request_id", "automatic_bank_order", "bank_reference", "obligation_id")}
    for row in _checked(state.get("movements", [])):
        if row.get("id"):
            counts["movement_id"][digest(row["id"])] += 1
        if row.get("request_id"):
            counts["request_id"][digest([row.get("actor_id"), row["request_id"]])] += 1
        if row.get("automatic_order_bank") is True:
            for key, field in (("automatic_bank_order", "order_number"), ("bank_reference", "bank_transaction_reference")):
                if row.get(field):
                    counts[key][digest(row[field])] += 1
    for key, row in _checked(state.get("engine", {}).get("obligations", {}).items()):
        counts["obligation_id"][digest(row.get("id", key))] += 1
    return {key: {identity: count - 1 for identity, count in values.items() if count > 1}
            for key, values in counts.items()}


def _source_indexes(sources, state):
    ad_parts = defaultdict(list)
    for identity, row in _checked(state.get("engine", {}).get("obligations", {}).items()):
        if row.get("ad_day_id"):
            ad_parts[row["ad_day_id"]].append((identity, row))
    return {"salary_candidates": {}, "ad_parts": ad_parts, "ad_verified": {},
            "orders": _index(sources.get("orders", []), lambda row: str(row.get("id", ""))),
            "deliveries": _index(chain(sources.get("orders", []), sources.get("delivery_events", [])), lambda row: str(row.get("id", ""))),
            "employees": _index(sources.get("employees", []), lambda row: str(row.get("id", ""))),
            "ads": _index(sources.get("ad_snapshots", sources.get("advertising", [])),
                          lambda row: (str(row.get("account_id", "")), str(row.get("date", "")))),
            "recurring": _index(sources.get("recurring", sources.get("recurring_obligations", [])),
                                lambda row: str(row.get("id", ""))),
            "completed_shipping": _index(sources.get("completed_shipping_events", []), lambda row: str(row.get("order_id", ""))),
            "prepaid": _index(sources.get("prepaid_shipping_events", []), lambda row: str(row.get("order_id", "")))}


def _ad_original(state, sources, row, day, stamp):
    key = "advertising:" + row["party_id"] + ":" + day.isoformat()
    if key not in sources["ad_verified"]:
        source = sources["ads"].get((row["party_id"], day.isoformat()))
        saved = state.get("engine", {}).get("ad_days", {}).get(key)
        zone = ZoneInfo(source["timezone"])
        end = datetime.combine(day + timedelta(days=1), time.min, zone)
        parts = sources["ad_parts"].get(key, [])
        valid_parts = bool(parts) and all(identity in {key, key + ":wallet", key + ":credit"}
            and part.get("party_type") == "ad_account" and part.get("party_id") == row["party_id"]
            and part.get("currency") == source["currency"] and part.get("business_date") == day.isoformat()
            and part.get("timezone") == source["timezone"] and not part.get("superseded")
            and _money(part.get("expected")) == 0 and _money(part.get("confirmed")) is not None
            and _money(part.get("ad_total_amount")) == _money(source["amount"]) for identity, part in _checked(parts))
        sources["ad_verified"][key] = (source.get("closed") is True and source.get("complete") is True
            and source.get("day_ended") is True and saved.get("closed") is True
            and saved.get("timezone") == source["timezone"] and valid_parts
            and _money(source["amount"]) == _money(saved.get("amount"))
            and sum((_money(part["confirmed"]) for _, part in parts), Decimal(0)) == _money(source["amount"])
            and end <= _instant(source["observed_at"]) <= stamp and stamp >= end + timedelta(hours=2))
    return row.get("ad_day_id") == key and sources["ad_verified"][key]


def _original_date(state, sources, identity, row, stamp, *, movement=False):
    """Use paired source evidence; a saved display date alone proves nothing."""
    engine = state.get("engine", {})
    day = (_instant(row.get("occurred_at")) or datetime.min.replace(tzinfo=timezone.utc)).astimezone(RIYADH).date() if movement else _day(row.get("business_date"))
    if day is None or day > stamp.astimezone(RIYADH).date():
        return False, "invalid_original_date"
    try:
        if row.get("business_date_basis") == "recognition_recorded_at" or row.get("recognition_basis") in OBSERVATION_BASES:
            return False, "recognition_observation_date"
        if movement and row.get("automatic_order_bank") is True:
            from operational_bank_credit_recognition import original_bank_occurrence
            source = sources["orders"].get(str(row.get("order_id")))
            credit = source["bank_credit"]
            original = original_bank_occurrence(source, state, stamp.isoformat())
            valid = (_instant(original) == _instant(row.get("occurred_at"))
                     and credit["bank_id"] == row.get("bank_id")
                     and row.get("party_type") == "bank" and row.get("party_id") == credit["bank_id"]
                     and row.get("direction") == "incoming" and row.get("currency") == source["currency"]
                     and _money(row.get("amount")) is not None
                     and _money(credit["amount"]) == _money(row.get("amount")))
            return valid, "bank_status_history" if valid else "bank_evidence_mismatch"
        if row.get("automatic_prepaid_shipping") is True or identity.startswith("shipping-prepaid:"):
            event = sources["prepaid"].get(str(row.get("order_id")))
            recorded = engine.get("facts", {}).get("prepaid_shipping:" + str(row.get("order_id")))
            at = _instant(event.get("completed_at"))
            amount = row.get("amount") if movement else row.get("confirmed")
            valid = (event == recorded and not event.get("recognition_basis") and at is not None
                     and at.astimezone(RIYADH).date() == day and _money(event.get("amount")) == _money(amount)
                     and event.get("currency") == row.get("currency") and event.get("party_id") == row.get("party_id")
                     and event.get("party_type") == row.get("party_type")
                     and (not movement or at == _instant(row.get("occurred_at"))))
            return valid, "prepaid_completion_event" if valid else "prepaid_date_unproven"
        if movement:
            return False, "movement_date_unproven"
        if row.get("derived_credit") is True:
            return False, "derived_adjustment"
        if row.get("kind") == "provider":
            from operational_provider_dates import verified_original_obligation
            order_id = identity.removeprefix("provider:").split(":", 1)[0]
            valid = verified_original_obligation(state, sources["orders"].get(order_id), identity, row, stamp)
            return valid, "provider_capture_event" if valid else "provider_observation_or_aggregate"
        if row.get("business_date_basis") == "native_driver_earned_at":
            from operational_driver_original_dates import PREFIX, earning_fact
            order_id = identity.removeprefix("shipping:").removesuffix(":base_shipping")
            source = sources["deliveries"].get(order_id)
            event = earning_fact(state.get("owner_id"), source, source.get("native_driver_earning"))
            at = _instant(event["earned_at"])
            valid = (identity == "shipping:" + order_id + ":base_shipping"
                     and event == engine.get("facts", {}).get(PREFIX + order_id)
                     and _instant(state.get("started_at")) <= at <= stamp and at.astimezone(RIYADH).date() == day
                     and _instant(row.get("earned_at")) == at and row.get("kind") == "shipping"
                     and event["party_id"] == row.get("party_id") and row.get("party_type") == "store_driver"
                     and event["earning_id"] == row.get("source_earning_id") and row.get("currency") == "SAR"
                     and _money(event["amount"]) == _money(row.get("confirmed")) and _money(row.get("expected")) == 0
                     and row.get("direction") == "payable")
            return valid, "native_driver_earning" if valid else "driver_earning_date_unproven"
        if identity.startswith("shipping-completed:"):
            order_id = identity.removeprefix("shipping-completed:")
            event = sources["completed_shipping"].get(order_id)
            recorded = engine.get("facts", {}).get("completed_shipping:" + order_id)
            at = _instant(event.get("completed_at"))
            valid = (event == recorded and event.get("schema") == "operational_completed_shipping_v1"
                     and event.get("owner_id") == state.get("owner_id") and event.get("order_id") == order_id
                     and event.get("payment_mode") == row.get("payment_mode") == "deferred"
                     and event.get("recognition_event") == row.get("recognition_event") == "order_completed"
                     and event.get("component") == row.get("component") == "base_shipping"
                     and not event.get("recognition_basis") and at is not None
                     and _instant(state.get("started_at")) <= at <= stamp and at.astimezone(RIYADH).date() == day
                     and _instant(row.get("completed_at")) == at and row.get("kind") == "shipping"
                     and row.get("direction") == "payable" and _money(row.get("expected")) == 0
                     and event.get("currency") == row.get("currency") == "SAR"
                     and event.get("party_type") == row.get("party_type") == "courier"
                     and event.get("party_id") == row.get("party_id")
                     and _money(event.get("amount")) == _money(row.get("confirmed"))
                     and _money(row.get("confirmed")) is not None)
            return valid, "shipping_completion_event" if valid else "shipping_completion_date_unproven"
        if row.get("kind") == "salary":
            from operational_salary_projection import candidates
            from operational_salary_start import select_first_day
            employee = str(row.get("party_id"))
            source = sources["employees"].get(employee)
            if employee not in sources["salary_candidates"]:
                first = select_first_day(state, stamp)
                if (stamp.astimezone(RIYADH).date() - first).days >= 3660:
                    return False, "source_proof_limit"
                projected, missing = candidates(source, first, stamp.astimezone(RIYADH).date())
                sources["salary_candidates"][employee] = projected, frozenset(missing)
            projected, missing = sources["salary_candidates"][employee]
            candidate = projected.get(identity)
            valid = (identity == "salary:" + employee + ":" + day.isoformat()
                     and identity not in engine.get("salary_conflicts", {}) and _money(row.get("expected")) == 0
                     and row.get("party_type") == "employee" and row.get("direction") == "payable"
                     and identity not in missing and candidate is not None and candidate == engine.get("salary_days", {}).get(identity)
                     and _money(candidate["amount"]) == _money(row.get("confirmed"))
                     and candidate["currency"] == row.get("currency"))
            return valid, "salary_contract_day" if valid else "salary_day_unproven"
        if row.get("kind") == "advertising":
            valid = _ad_original(state, sources, row, day, stamp)
            return valid, "advertising_source_day" if valid else "advertising_day_unproven"
        if row.get("kind") == "recurring":
            source = sources["recurring"].get(identity.removeprefix("recurring:"))
            due = _day(source.get("due_at")) or (_instant(source.get("due_at")) or datetime.min.replace(tzinfo=timezone.utc)).date()
            valid = (due == day and source.get("party_id") == row.get("party_id")
                     and source.get("currency") == row.get("currency")
                     and _money(source["amount"]) == _money(row["confirmed"]) + _money(row["expected"]))
            return valid, "recurring_due_date" if valid else "recurring_date_unproven"
        if row.get("kind") == "cod" and identity.startswith("cod:"):
            order_id = identity.removeprefix("cod:")
            source = sources["deliveries"].get(order_id)
            event = source.get("delivery_event")
            frozen = engine.get("facts", {}).get("shipping:" + order_id)
            collected = engine.get("facts", {}).get("cod_collection:" + order_id)
            at = _instant(event.get("delivered_at"))
            valid = (event.get("schema") == "operational_delivery_event_v1"
                     and event == engine.get("facts", {}).get("delivery_event:" + order_id)
                     and not frozen.get("recognition_basis") and at is not None
                     and at == _instant(frozen.get("delivered_at")) and at.astimezone(RIYADH).date() == day
                     and event.get("party_id") == frozen.get("id") == collected.get("party_id") == row.get("party_id")
                     and event.get("party_type") == frozen.get("party_type") == row.get("party_type")
                     and event.get("currency") == source.get("currency") == row.get("currency")
                     and source.get("cod") == collected.get("evidence") and source["cod"].get("evidence_complete") is True
                     and _money(source.get("cod_amount")) == _money(collected.get("amount")) == _money(row.get("confirmed"))
                     and _money(row.get("confirmed")) is not None and _money(row.get("expected")) == 0
                     and row.get("direction") == "receivable")
            return valid, "delivery_collection_event" if valid else "collection_date_unproven"
        if row.get("kind") == "shipping":
            from operational_workspace_projection import automatic_source
            family, key = automatic_source(state, row)
            frozen = engine.get("facts", {}).get(key, {})
            at = _instant(frozen.get("delivered_at"))
            order_id = key.removeprefix("shipping:")
            source = sources["deliveries"].get(order_id)
            event = source.get("delivery_event")
            valid = (family == "delivery" and not frozen.get("recognition_basis") and at is not None
                     and at.astimezone(RIYADH).date() == day and event.get("schema") == "operational_delivery_event_v1"
                     and event == engine.get("facts", {}).get("delivery_event:" + order_id)
                     and event.get("party_id") == row.get("party_id") and event.get("party_type") == row.get("party_type")
                     and event.get("currency") == row.get("currency") and _instant(event.get("delivered_at")) == at)
            return valid, "delivery_event" if valid else "delivery_date_unproven"
        return False, "provider_observation_or_aggregate" if row.get("kind") == "provider" else "source_date_unproven"
    except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError):
        return False, "source_date_unproven"


def _summary(obligations, movements, first, last):
    receipt = recovery_snapshot({"engine": {"obligations": obligations}, "movements": movements}, first, last)
    result = {key: receipt[key] for key in ("obligations", "automatic_movements", "obligation_identity_digest",
                                           "automatic_movements_unverified_date_or_amount")}
    positive = Counter((row.get("party_type"), row.get("party_id"), row.get("currency"), row.get("direction"), row.get("business_date"))
                       for row in obligations.values() if (_money(row.get("confirmed")) or Decimal(0)) > 0)
    for row in result["obligations"]:
        row["confirmed_count"] = positive[(row["entity_type"], row["entity_id"], row["currency"], row["direction"], row["business_date"])]
    return result


def _unavailable(base, reason, **details):
    return {key: base[key] for key in ("schema_version", "owner_id", "previous_revision", "calculation_revision",
            "previous_as_of", "as_of", "source_completeness_verified")} | {"complete": False, "reason": reason, **details}


def build_recovery_proof(before, after, sources, as_of):
    context = demand_context()
    # The shared grouping and salary helpers use the read preparation stop
    # scope; bind that scope to this refresh's owned cancellation event too.
    with preparation_scope(context.cancelled) if context is not None else nullcontext():
        calculation_checkpoint()
        return _build_recovery_proof(before, after, sources, as_of)


def _build_recovery_proof(before, after, sources, as_of):
    """Describe all financial changes, including days older than the prior as_of."""
    previous_revision = before.get("revision")
    base = {"schema_version": 1, "owner_id": before.get("owner_id"),
            "previous_revision": previous_revision, "calculation_revision": previous_revision + 1 if type(previous_revision) is int else None,
            "previous_as_of": before.get("engine", {}).get("as_of"), "as_of": as_of,
            "source_completeness_verified": False, "complete": False}
    try:
        stamp = _instant(as_of)
        if stamp is None or type(previous_revision) is not int or previous_revision < 0 or before.get("owner_id") != after.get("owner_id"):
            raise ValueError("invalid_snapshot")
        old = before.get("engine", {}).get("obligations", {})
        current = after.get("engine", {}).get("obligations", {})
        old_moves, moves = before.get("movements", []), after.get("movements", [])
        if type(old) is not dict or type(current) is not dict or type(old_moves) is not list or type(moves) is not list:
            raise ValueError("invalid_financial_evidence")
        if sum(map(len, (old, current, old_moves, moves))) > MAX_RECORDS:
            raise ValueError("record_limit")
        if any(type(row) is not dict for row in _checked(chain(old.values(), current.values(), old_moves, moves))):
            raise ValueError("invalid_financial_evidence")
        old_ids = {digest(row.get("id")) for row in old_moves}
        additions = {key: row for key, row in current.items() if key not in old}
        added_moves = [row for row in moves if _automatic(row) and digest(row.get("id")) not in old_ids]
        changed = {key for key in _checked(old.keys() & current.keys())
                   if {k: v for k, v in old[key].items() if k not in DISPLAY_FIELDS}
                   != {k: v for k, v in current[key].items() if k not in DISPLAY_FIELDS}}
        removed = old.keys() - current.keys()
        days = [_day(row.get("business_date")) for row in [*additions.values(), *(old[key] for key in changed | removed), *(current[key] for key in changed)]]
        days += [at.astimezone(RIYADH).date() for row in added_moves if (at := _instant(row.get("occurred_at")))]
        days = [day for day in days if day is not None]
        today = stamp.astimezone(RIYADH).date()
        first, last = min(days + [today]).isoformat(), max(days + [today]).isoformat()
        indexed = _source_indexes(sources, after)
        dated, undated, dated_moves, undated_moves, reasons = {}, {}, [], [], Counter()
        for key, row in _checked(additions.items()):
            verified, reason = _original_date(after, indexed, key, row, stamp)
            (dated if verified else undated)[key] = row
            if not verified:
                reasons[reason] += 1
        for row in _checked(added_moves):
            verified, reason = _original_date(after, indexed, str(row.get("id", "")), row, stamp, movement=True)
            (dated_moves if verified else undated_moves).append(row)
            if not verified:
                reasons[reason] += 1
        changed_reasons = Counter()
        for key in _checked(changed):
            verified, reason = _original_date(after, indexed, key, current[key], stamp)
            if not verified:
                changed_reasons[reason] += 1
        manual_before = _movement_manifest(old_moves, manual_only=True)
        all_after = _movement_manifest(moves)
        preservation = {"manual_movements": _preservation(manual_before, all_after),
                        "automatic_movements": _preservation(_movement_manifest(old_moves, automatic_only=True), all_after),
                        **{field: _preservation({digest(k): digest(v) for k, v in before.get(field, {}).items()},
                                               {digest(k): digest(v) for k, v in after.get(field, {}).items()})
                           for field in ("openings", "requests")}}
        old_duplicates, duplicates = _duplicate_keys(before), _duplicate_keys(after)
        new_duplicates = {key: sum(max(0, count - old_duplicates[key].get(identity, 0)) for identity, count in values.items())
                          for key, values in duplicates.items()}
        base.update(window={"from": first, "through": last}, new_original_dated=_summary(dated, dated_moves, first, last),
            new_date_unverified=_summary(undated, undated_moves, first, last), original_date_unknown_reasons=dict(reasons),
            new_obligation_count=len(additions), new_automatic_movement_count=len(added_moves),
            changed_existing={"count": len(changed), "before": _summary({key: old[key] for key in changed}, [], first, last),
                              "after": _summary({key: current[key] for key in changed}, [], first, last),
                              "original_dates_verified": not changed_reasons, "original_date_unknown_reasons": dict(changed_reasons)},
            removed_obligation_count=len(removed), preservation=preservation,
            new_duplicate_keys=new_duplicates, existing_duplicate_keys={key: sum(values.values()) for key, values in old_duplicates.items()},
            financial_delta_digest=digest({"new_obligations": additions, "new_automatic_movements": added_moves,
                                          "changed": {key: [old[key], current[key]] for key in changed}, "removed": sorted(removed)}))
        groups = sum(len(section[k]) for section in (base["new_original_dated"], base["new_date_unverified"],
                     base["changed_existing"]["before"], base["changed_existing"]["after"]) for k in ("obligations", "automatic_movements"))
        sections = (base["new_original_dated"], base["new_date_unverified"],
                    base["changed_existing"]["before"], base["changed_existing"]["after"])
        represented = sum(row["count"] for section in sections for key in ("obligations", "automatic_movements") for row in section[key])
        base["ungrouped_record_count"] = len(additions) + len(added_moves) + 2 * len(changed) - represented
        base["unidentified_movement_count"] = sum(type(row.get("id")) is not str or not row["id"]
                                                 for row in chain(old_moves, moves))
        base["complete"] = (not removed and not base["ungrouped_record_count"] and not any(new_duplicates.values())
                            and not base["unidentified_movement_count"]
                            and all(value["preserved"] for value in preservation.values()))
        base["original_dates_verified"] = not reasons and not changed_reasons
        if groups > MAX_GROUPS or len(json.dumps(base, ensure_ascii=False).encode()) > MAX_BYTES:
            return _unavailable(base, "metadata_limit", omitted_group_count=groups,
                financial_delta_digest=base["financial_delta_digest"], new_obligation_count=len(additions),
                new_automatic_movement_count=len(added_moves))
        return base
    except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError) as exc:
        code = str(exc) if str(exc) in {"invalid_snapshot", "invalid_financial_evidence", "record_limit", "invalid_recovery_window"} else "invalid_financial_evidence"
        return _unavailable(base, code)


async def read_proof(db, owner, *, now=None):
    """Project only the compact receipt/header; never hydrate financial archives."""
    from operational_entity_freshness import FIELD as COVERAGE_FIELD, coverage_rows
    unavailable = {"state": "unknown", "proof": None, "entities": []}
    try:
        with pymongo.timeout(READ_SECONDS):
            async with asyncio.timeout(READ_SECONDS):
                row = await db[STATES].find_one({"_id": owner, "owner_id": owner},
                    {"_id": 0, "owner_id": 1, "revision": 1, "engine.as_of": 1,
                     "engine." + FIELD: 1, "engine." + COVERAGE_FIELD: 1})
        proof = (row or {}).get("engine", {}).get(FIELD)
        if proof is None:
            return {**unavailable, "reason": "not_recorded"}
        stamp = _instant(proof.get("as_of")) if type(proof) is dict else None
        valid = (type(proof) is dict and type(proof.get("schema_version")) is int and proof["schema_version"] == 1
                 and proof.get("owner_id") == owner == row.get("owner_id")
                 and type(row.get("revision")) is int and type(proof.get("calculation_revision")) is int
                 and type(proof.get("previous_revision")) is int and proof["previous_revision"] + 1 == proof["calculation_revision"]
                 and 0 < proof["calculation_revision"] <= row["revision"]
                 and proof.get("as_of") == row.get("engine", {}).get("as_of")
                 and stamp is not None and stamp <= (now or datetime.now(timezone.utc))
                 and len(json.dumps(proof, ensure_ascii=False).encode()) <= MAX_BYTES)
        if not valid:
            return {**unavailable, "reason": "invalid_receipt"}
        coverage = coverage_rows(row["engine"].get(COVERAGE_FIELD), owner, row["engine"]["as_of"],
            now=now or datetime.now(timezone.utc), revision=row["revision"])
        entities = [{key: item[key] for key in ("entity_type", "entity_id", "currency", "label")}
                    for item in coverage["rows"]]
        return {"state": "available", "proof": proof, "entities": entities}
    except Exception:
        return {**unavailable, "reason": "unavailable"}
