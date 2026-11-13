"""Bounded reads of existing native snapshots; no sync, writes or provider calls.

Array snapshots are filtered server-side before transfer. A name is never used
as a join key; only explicit campaign IDs can produce spend offers.
"""
from datetime import timedelta

SNAP_SOURCE = "snapchat_ads_manager_account_timezone_conversion_v8:account_day_v3"


async def _array_snapshots(db, collection, query, array, id_field, ids, fields):
    if not ids:
        return [], True
    # Snapshot counts and returned elements are independently bounded. Do not
    # fetch entire 5,000-entity catalogues into the request worker.
    project = {field: 1 for field in fields}
    project.update(_id=0, selected={"$slice": [{"$filter": {
        "input": {"$ifNull": ["$" + array, []]}, "as": "item",
        "cond": {"$in": ["$$item." + id_field, ids]},
    }}, len(ids) + 1]})
    try:
        rows = await db[collection].aggregate([
            {"$match": query}, {"$limit": 251}, {"$project": project},
        ], maxTimeMS=1500, allowDiskUse=False).to_list(251)
    except Exception:
        return [], False
    if len(rows) > 250 or any(len(row.get("selected", [])) > len(ids) for row in rows):
        return [], False
    return rows, True


async def read_native_campaign_sources(db, user_id, ids, accounts, windows, now):
    from .campaign_hover import _read, _amount, _timestamp, _text
    if not ids:
        return [], [], True
    identities, spends = [], []
    health = True
    by_provider = {provider: [key[1] for key in accounts if key[0] == provider]
                   for provider in ("snapchat", "tiktok", "google")}
    dates = sorted({window[1] for window in windows.values() if window})

    # The active scheduled account-timezone reader writes v3, whereas the
    # separate initial native sync writes v2. Read both without changing either.
    if by_provider["snapchat"]:
        rows, complete = await _read(db, "mezan_snapchat_performance_account_day_v3", {
            "user_id": user_id, "ad_account_id": {"$in": by_provider["snapchat"]},
            "entity_type": "campaign", "external_id": {"$in": ids},
            "date": {"$in": dates}, "source_mode": SNAP_SOURCE,
            "action_report_time": "conversion", "stored_granularity": "ACCOUNT_LOCAL_DAY",
        }, {"_id": 0, "ad_account_id": 1, "external_id": 1, "date": 1,
            "date_timezone": 1, "account_timezone": 1, "spend_sar": 1,
            "spend_native": 1, "currency": 1, "provider_window_start": 1,
            "provider_window_end": 1, "updated_at": 1})
        health &= complete
        for row in rows:
            cid, key = _text(row.get("external_id")), ("snapchat", _text(row.get("ad_account_id")))
            identities.append((cid, key, None))
            window = windows.get(key)
            if not window:
                continue
            start, day = window
            stamp = _timestamp(row.get("updated_at"))
            begin = _timestamp(row.get("provider_window_start"))
            end = _timestamp(row.get("provider_window_end"))
            zone = accounts[key].get("timezone")
            if (row.get("date") != day or row.get("date_timezone") != zone
                    or row.get("account_timezone") != zone
                    or _text(row.get("currency")).upper() != _text(accounts[key].get("currency_native")).upper()
                    or not stamp or not begin or not end
                    or not start <= begin < end or not begin <= stamp <= now):
                continue
            ceiling = stamp.replace(minute=0, second=0, microsecond=0)
            if ceiling < stamp:
                ceiling += timedelta(hours=1)
            if end > ceiling:
                continue
            spends.append((cid, key, {
                "spend_sar": row.get("spend_sar"), "spend_native": row.get("spend_native"),
                "currency_native": row.get("currency"), "fetched_at": row.get("updated_at"),
                "coverage_end_utc": min(end, stamp).isoformat(),
                "spend_coverage": "account_day_observed",
                # v3 does not store per-hour completeness; never pretend it does.
                "spend_warning": "snapchat_hour_coverage_unverified",
            }))

    if by_provider["tiktok"]:
        base = {"user_id": user_id, "ad_account_id": {"$in": by_provider["tiktok"]},
                "entity_type": "campaign", "complete": True, "source_mode": "tiktok_native_hierarchy_v2"}
        rows, complete = await _array_snapshots(db, "mezan_tiktok_entities_v2", base,
            "entities", "entity_id", ids, ["ad_account_id", "observed_at"])
        health &= complete
        for row in rows:
            key = ("tiktok", _text(row.get("ad_account_id")))
            for item in row.get("selected", []):
                identities.append((_text(item.get("entity_id")), key, item.get("entity_name")))
        rows, complete = await _array_snapshots(db, "mezan_tiktok_entity_daily_v2",
            {**base, "date": {"$in": dates}}, "rows", "entity_id", ids,
            ["ad_account_id", "date", "observed_at"])
        health &= complete
        for row in rows:
            key = ("tiktok", _text(row.get("ad_account_id")))
            window = windows.get(key)
            stamp = _timestamp(row.get("observed_at"))
            if not window or row.get("date") != window[1] or not stamp or not window[0] <= stamp <= now:
                continue
            for item in row.get("selected", []):
                cid = _text(item.get("entity_id"))
                identities.append((cid, key, None))
                spends.append((cid, key, {"spend_native": item.get("spend_native"),
                    "currency_native": accounts[key].get("currency_native"),
                    "fetched_at": row.get("observed_at")}))

    if by_provider["google"]:
        # Period facts establish identity independently of today's spend. Only
        # an exact one-day snapshot may supply the campaign-day spend value.
        rows, complete = await _array_snapshots(db, "mezan_google_ads_campaign_factsets_v1", {
            "user_id": user_id, "account_id": {"$in": by_provider["google"]},
            "complete": True, "source": "google_ads_api",
            "date_to": {"$gte": (now - timedelta(days=31)).date().isoformat()},
        }, "campaigns", "campaign_id", ids, ["account_id", "date_from", "date_to", "observed_at"])
        health &= complete
        for row in rows:
            key = ("google", _text(row.get("account_id")))
            window = windows.get(key)
            for item in row.get("selected", []):
                cid = _text(item.get("campaign_id"))
                if _text(item.get("account_id")) != key[1] or item.get("source") != "google_ads_api":
                    continue
                identities.append((cid, key, item.get("name")))
                stamp = _timestamp(row.get("observed_at"))
                if (not window or row.get("date_from") != window[1] or row.get("date_to") != window[1]
                        or item.get("time_zone") != accounts[key].get("timezone")
                        or _text(item.get("currency_code")).upper() != _text(accounts[key].get("currency_native")).upper()
                        or not stamp or not window[0] <= stamp <= now):
                    continue
                spends.append((cid, key, {"spend_native": (item.get("metrics") or {}).get("spend_native"),
                    "currency_native": item.get("currency_code"), "fetched_at": row.get("observed_at")}))

    # Native account-day FX is proof for currency conversion, never campaign
    # spend. Read it only when needed; no hard-coded exchange rate or zero fill.
    foreign = {key for _, key, row in spends if row.get("spend_sar") is None
               and _amount(row.get("spend_native")) is not None
               and _text(row.get("currency_native")).upper() not in {"", "SAR"}}
    for provider, collection, mode in (
        ("tiktok", "mezan_tiktok_performance_daily_v2", "tiktok_marketing_reporting_v2"),
        ("google", "mezan_google_ads_performance_daily_v2", "google_ads_reporting_v1"),
    ):
        needed = [key[1] for key in foreign if key[0] == provider]
        if not needed:
            continue
        rows, complete = await _read(db, collection, {"user_id": user_id,
            "ad_account_id": {"$in": needed}, "date": {"$in": dates}, "source_mode": mode},
            {"_id": 0, "ad_account_id": 1, "date": 1, "account_timezone": 1,
             "fx_rate_to_sar": 1, "currency_native": 1}, 250)
        if not complete:
            continue
        rates = {(str(r.get("ad_account_id")), r.get("date")): r for r in rows}
        for _, key, spend in spends:
            if key[0] != provider or key not in foreign or not windows.get(key):
                continue
            rate_row = rates.get((key[1], windows[key][1]), {})
            rate = _amount(rate_row.get("fx_rate_to_sar"))
            native = _amount(spend.get("spend_native"))
            if (rate and native is not None
                    and rate_row.get("account_timezone") == accounts[key].get("timezone")
                    and _text(rate_row.get("currency_native")).upper() == _text(spend.get("currency_native")).upper()):
                spend["spend_sar"] = native * rate
    return identities, spends, bool(health)
