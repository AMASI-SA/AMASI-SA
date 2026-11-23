"""Bounded, read-only Tabby estimates from accepted statement sale rows.

The sample is the seven completed calendar days before the report's Riyadh
day. An empty current week is unavailable; this utility never substitutes an
older week or a lifetime average. The caller supplies owner-scoped,
immutable statement documents and their saved average acceptance proofs; this
module performs no database, provider, ledger or configuration writes.
"""
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import re

from operational_provider_statement_fees import (
    derive_statement_average_profile,
    statement_average_summary,
    statement_evidence_fingerprint,
)

MAX_SOURCES = 32
MAX_SOURCE_ROWS = 10000
WINDOW_DAYS = 7
RIYADH = timezone(timedelta(hours=3))
_HEX = re.compile(r"[a-f0-9]{64}")
_ECONOMIC_FIELDS = (
    "provider", "order_number", "provider_order_id", "event_type",
    "event_reference", "transaction_reference", "event_date", "native_currency",
    "principal_amount", "fee_net", "fee_vat", "fee_total", "transferred_amount",
    "fee_variable_net", "fee_fixed_net", "fee_vat_rate", "fee_component_issues",
    "payment_method", "plan_id", "installments", "merchant_account",
)


def _unavailable(reason, **metadata):
    return {"available": False, "profile": None, "reason_code": reason,
            "window_start": None, "window_end": None, "source_statement_ids": [],
            "source_sale_rows": 0, "stale_source": False, **metadata}


def _instant(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("source_timestamp_invalid")
    return stamp


def _report_boundary(value):
    if isinstance(value, datetime):
        stamp = _instant(value)
        return stamp.astimezone(RIYADH).date(), stamp
    if isinstance(value, date) or isinstance(value, str) and len(value) == 10:
        day = value if isinstance(value, date) else date.fromisoformat(value)
        return day, datetime.combine(day, time.max, tzinfo=RIYADH)
    stamp = _instant(value)
    return stamp.astimezone(RIYADH).date(), stamp


def _row_day(row):
    value = row.get("event_date")
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("source_sale_date_unavailable")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("source_sale_date_unavailable") from None


def _statement_fingerprint(data):
    return statement_evidence_fingerprint({
        **{key: data.get(key) for key in (
            "schema_version", "provider", "statement_reference", "merchant_account", "currency", "statement_date")},
        "rows": sorted(row["row_fingerprint"] for row in data["rows"]),
    })


def _source_profile(owner_id, document):
    """Rebuild and match the exact source proof, without trusting import labels."""
    data = document.get("statement")
    if (document.get("owner_id") != owner_id or not isinstance(data, dict)
            or not document.get("id") or document.get("_id") != document["id"]
            or document.get("content_hash") != statement_evidence_fingerprint(data)
            or data.get("schema_version") != "operational_statement_v1"
            or not isinstance(data.get("file_hash"), str) or not _HEX.fullmatch(data["file_hash"])):
        raise ValueError("source_statement_invalid")
    for row in data["rows"]:
        if (not isinstance(row, dict) or not isinstance(row.get("row_fingerprint"), str)
                or row["row_fingerprint"] != statement_evidence_fingerprint({
                    key: value for key, value in row.items() if key not in {"id", "source_row", "blockers", "row_fingerprint"}})):
            raise ValueError("source_statement_fingerprint_invalid")
    if data.get("fingerprint") != _statement_fingerprint(data):
        raise ValueError("source_statement_fingerprint_invalid")
    profile = derive_statement_average_profile({"owner_id": owner_id}, data,
        statement_id=document["id"], accepted_at=document["uploaded_at"])
    if profile is None:
        raise ValueError("source_average_invalid")
    accepted_profile, accepted_summary = document.get("accepted_profile"), document.get("accepted_summary")
    if accepted_profile is None and accepted_summary is None:
        raise ValueError("source_average_not_accepted")
    if accepted_profile is not None and accepted_profile != profile:
        raise ValueError("source_average_acceptance_changed")
    if accepted_summary is not None and accepted_summary != statement_average_summary(profile):
        raise ValueError("source_average_acceptance_changed")
    return profile


def derive_weekly_statement_average(owner_id, sources, *, as_of,
                                    native_currency="SAR", merchant_account=None,
                                    accepted_as_of=None):
    """Return an existing-schema profile and separate, explicit weekly metadata.

    Each source is an immutable ``operational_provider_statements_v1`` document
    plus either its saved ``accepted_profile`` or ``accepted_summary``. The
    sales window is bound to ``as_of``; ``accepted_as_of`` independently limits
    which imports are known, permitting a historical window to be restated
    after a later upload. It defaults to ``as_of``. A date-only cutoff permits
    acceptance through that day's end; timestamps retain their exact instant.

    Duplicate exports of one economic event contribute once. A conflicting
    transaction reference or unreferenced order/day is held for review.
    ``profile.statement_id`` is a derived sample identity, and its ``file_hash``
    is a hash of the source manifest, not the hash of an invented original file.
    The unchanged profile schema permits reuse of the operational fee calculator.
    """
    metadata = {}
    try:
        if (not isinstance(owner_id, str) or not owner_id
                or not isinstance(native_currency, str) or not re.fullmatch(r"[A-Z]{3}", native_currency)
                or merchant_account is not None and (not isinstance(merchant_account, str) or not merchant_account)
                or not isinstance(sources, (list, tuple)) or len(sources) > MAX_SOURCES):
            return _unavailable("source_scope_invalid")
        report_day, _ = _report_boundary(as_of)
        _, acceptance_limit = _report_boundary(as_of if accepted_as_of is None else accepted_as_of)
        cutoff = report_day - timedelta(days=1)
        start = report_day - timedelta(days=WINDOW_DAYS)
        metadata = {"latest_complete_day": cutoff.isoformat(), "as_of_day": report_day.isoformat(),
                    "window_start": start.isoformat(), "window_end": cutoff.isoformat(),
                    "window_days": WINDOW_DAYS,
                    "basis": "last_seven_complete_calendar_days", "stale_source": False,
                    "accepted_as_of": acceptance_limit.isoformat()}
        row_count = 0
        for source in sources:
            if not isinstance(source, dict):
                return _unavailable("source_statement_invalid", **metadata)
            data = source.get("statement")
            if not isinstance(data, dict):
                return _unavailable("source_statement_invalid", **metadata)
            rows = data.get("rows")
            if not isinstance(rows, list) or not rows:
                return _unavailable("source_statement_invalid", **metadata)
            row_count += len(rows)
            if row_count > MAX_SOURCE_ROWS:
                return _unavailable("source_row_limit", **metadata)
        eligible, identities = [], {}
        for source in sources:
            if source.get("owner_id") != owner_id:
                return _unavailable("source_owner_mismatch", **metadata)
            data = source["statement"]
            if data.get("provider") != "tabby":
                continue
            if _instant(source.get("uploaded_at")) > acceptance_limit:
                continue
            profile = _source_profile(owner_id, source)
            if profile["native_currency"] != native_currency:
                continue
            if merchant_account is not None and profile.get("merchant_account") != merchant_account:
                continue
            previous = identities.get(source["id"])
            if previous is not None:
                if previous != source["content_hash"]:
                    return _unavailable("source_statement_conflict", **metadata)
                continue
            identities[source["id"]] = source["content_hash"]
            dated_sales = [(row, _row_day(row)) for row in data["rows"] if row.get("event_type") == "sale"]
            dated_sales = [(row, day) for row, day in dated_sales if start <= day <= cutoff]
            if dated_sales:
                eligible.append((source, profile, dated_sales))
        if not eligible:
            return _unavailable("source_weekly_average_unavailable", **metadata)
        accounts = {profile.get("merchant_account") for _, profile, _ in eligible}
        if len(accounts) != 1:
            return _unavailable("source_account_ambiguous", **metadata)
        account = next(iter(accounts))
        end = cutoff
        selected, manifests, accepted = {}, {}, []
        for source, profile, rows in eligible:
            for row, day in rows:
                reference = row.get("transaction_reference") or row.get("event_reference")
                if reference is not None and (not isinstance(reference, str) or not reference.strip()):
                    return _unavailable("source_sale_identity_invalid", **metadata)
                identity = (("reference", reference) if reference else ("order_day", str(row["order_number"]), day.isoformat()))
                economic = {key: row.get(key) for key in _ECONOMIC_FIELDS}
                digest = statement_evidence_fingerprint(economic)
                if identity in selected and selected[identity][0] != digest:
                    return _unavailable("source_sale_conflict", **metadata)
                selected.setdefault(identity, (digest, deepcopy(row)))
                manifests[source["id"]] = {"statement_id": source["id"],
                    "file_hash": profile["file_hash"], "source_fingerprint": profile["source_fingerprint"]}
                accepted.append(profile["accepted_at"])
        manifest = [manifests[key] for key in sorted(manifests)]
        manifest_hash = statement_evidence_fingerprint({"sources": manifest, "window_start": start.isoformat(),
            "window_end": end.isoformat(), "native_currency": native_currency, "merchant_account": account})
        rows = []
        for index, (_identity, (economic_hash, row)) in enumerate(sorted(selected.items())):
            row.update(id="weekly:" + str(index), source_row=index, statement_reference="weekly:" + manifest_hash,
                       row_fingerprint=economic_hash)
            rows.append(row)
        normalized = {"schema_version": "operational_statement_v1", "provider": "tabby",
            # The calendar window may end after the last source was accepted.
            # Keep its dates in metadata; the profile's source date remains an
            # actual observed sale date, not a synthetic later statement date.
            "merchant_account": account, "currency": native_currency, "statement_date": None,
            "statement_reference": "weekly:" + manifest_hash, "file_hash": manifest_hash, "rows": rows}
        normalized["fingerprint"] = _statement_fingerprint(normalized)
        profile = derive_statement_average_profile({"owner_id": owner_id}, normalized,
            statement_id="weekly:" + manifest_hash, accepted_at=max(accepted, key=_instant))
        if profile is None:
            return _unavailable("source_weekly_average_invalid", **metadata)
        return {"available": True, "profile": profile, "reason_code": None, **metadata,
                "source_statement_ids": sorted(manifests), "source_sale_rows": len(rows),
                "sample_days": len({row["event_date"] for row in rows})}
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        reason = str(error)
        return _unavailable(reason if reason.startswith("source_") else "source_weekly_average_invalid", **metadata)
