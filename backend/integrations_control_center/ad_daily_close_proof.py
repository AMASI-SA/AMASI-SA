"""Provider-response evidence, not accounting authorization or financial finality.

Only explicit provider spend can prove zero. A successful empty response cannot.
Consumers must additionally enforce the configured day-close and freshness policy.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def native_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def close_proof(*, account_id, business_date, timezone, currency, spend,
                provider_row_count, complete_response, explicit_spend_present,
                identity_proven, source_mode, observed_at, reason=None):
    number = native_number(spend)
    try:
        ZoneInfo(timezone or "")
        date.fromisoformat(str(business_date))
        valid_identity = bool(account_id and currency and len(currency) == 3 and identity_proven)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        valid_identity = False
    complete = bool(complete_response and explicit_spend_present and valid_identity
                    and provider_row_count > 0 and number is not None)
    proof = dict(version=1, complete=complete, account_id=str(account_id),
        business_date=str(business_date), timezone=timezone, currency=currency,
        spend_native=format(number.normalize(), "f") if number is not None else None,
        provider_row_count=provider_row_count, explicit_spend_present=bool(explicit_spend_present),
        complete_response=bool(complete_response), zero_confirmed=complete and number == 0,
        observed_at=observed_at, source_mode=source_mode,
        reason=None if complete else reason or "provider_daily_evidence_incomplete")
    proof["fingerprint"] = hashlib.sha256(json.dumps(proof, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return proof


def meta_evidence(payload, account_id, day):
    rows = payload.get("data")
    rows = rows if isinstance(rows, list) else []
    row = rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else {}
    paging = payload.get("paging") or {}
    complete = isinstance(paging, dict) and not paging.get("next") and len(rows) == 1
    return dict(spend=row.get("spend"), provider_row_count=len(rows),
        complete_response=complete, explicit_spend_present=native_number(row.get("spend")) is not None,
        identity_proven=str(row.get("account_id") or "").removeprefix("act_") == str(account_id).removeprefix("act_")
            and row.get("date_start") == str(day) and row.get("date_stop") == str(day),
        reason="meta_explicit_single_account_day_and_complete_response_required")


def tiktok_evidence(data, account_id):
    rows = data.get("list")
    rows = rows if isinstance(rows, list) else []
    row = rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else {}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
    page = data.get("page_info") if isinstance(data.get("page_info"), dict) else {}
    complete = page.get("page") == 1 and page.get("total_page") == 1 and page.get("total_number") == len(rows) == 1
    return dict(spend=metrics.get("spend"), provider_row_count=len(rows),
        complete_response=complete, explicit_spend_present=native_number(metrics.get("spend")) is not None,
        identity_proven=str(dimensions.get("advertiser_id") or "") == str(account_id),
        reason="tiktok_explicit_advertiser_spend_and_exhausted_page_required")


class StreamRows(list):
    """Preserve response completeness alongside the backwards-compatible list."""
    def __init__(self, rows, complete_response):
        super().__init__(rows)
        self.complete_response = complete_response


def google_evidence(rows, metadata, account_id, day, start, end):
    complete = getattr(rows, "complete_response", False) is True
    identity = metadata.get("provider_identity_proven") is True
    total = Decimal(0)
    count = 0
    explicit = True
    seen = set()
    for row in rows:
        customer = row.get("customer") if isinstance(row.get("customer"), dict) else {}
        segments = row.get("segments") if isinstance(row.get("segments"), dict) else {}
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        row_day, hour = segments.get("date"), segments.get("hour")
        try:
            parsed_day = date.fromisoformat(row_day)
            valid_hour = not isinstance(hour, bool) and str(int(hour)) == str(hour) and 0 <= int(hour) <= 23
        except (ValueError, TypeError):
            parsed_day, valid_hour = None, False
        valid = (parsed_day is not None and start <= parsed_day <= end and valid_hour
            and (str(row_day), str(hour)) not in seen
            and str(customer.get("id") or "") == str(account_id)
            and customer.get("currencyCode") == metadata.get("currency")
            and customer.get("timeZone") == metadata.get("timezone"))
        if not valid:
            identity = False
        seen.add((str(row_day), str(hour)))
        cost = native_number(metrics.get("costMicros"))
        if cost is None or cost != cost.to_integral_value():
            explicit = False
        if row_day == str(day):
            count += 1
            if cost is not None:
                total += cost / Decimal(1_000_000)
    return dict(spend=total, provider_row_count=count, complete_response=complete,
        explicit_spend_present=explicit and count > 0, identity_proven=identity,
        reason="google_explicit_cost_rows_and_provider_timezone_complete_stream_required")
