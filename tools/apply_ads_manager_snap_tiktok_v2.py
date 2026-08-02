from __future__ import annotations

from pathlib import Path


SERVICE = Path("backend/ads_manager/service.py")
TESTS = Path("backend/tests/test_unified_ads_manager_phase1.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, new: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"{label}: start marker missing")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"{label}: end marker missing")
    return text[:start_index] + new + text[end_index:]


service = SERVICE.read_text(encoding="utf-8")

service = replace_once(
    service,
    '''PROVIDER_ORDER = ("snapchat", "tiktok", "meta")
META_V2_PERFORMANCE_COLLECTION = "mezan_meta_performance_daily_v2"
META_LEGACY_PERFORMANCE_COLLECTION = "meta_ads_daily"
META_INTEGRATION_PROVIDER = "meta_ads"
''',
    '''PROVIDER_ORDER = ("snapchat", "tiktok", "meta")
SNAPCHAT_V2_PERFORMANCE_COLLECTION = "mezan_snapchat_performance_daily_v2"
SNAPCHAT_LEGACY_ACCOUNT_COLLECTION = "snapchat_account_daily"
SNAPCHAT_LEGACY_STATS_COLLECTION = "snapchat_daily_stats"
SNAPCHAT_INTEGRATION_PROVIDER = "snapchat_ads"
TIKTOK_V2_PERFORMANCE_COLLECTION = "mezan_tiktok_performance_daily_v2"
TIKTOK_LEGACY_PERFORMANCE_COLLECTION = "tiktok_ads_daily"
TIKTOK_INTEGRATION_PROVIDER = "tiktok_ads"
META_V2_PERFORMANCE_COLLECTION = "mezan_meta_performance_daily_v2"
META_LEGACY_PERFORMANCE_COLLECTION = "meta_ads_daily"
META_INTEGRATION_PROVIDER = "meta_ads"
''',
    "constants",
)

service = replace_once(
    service,
    '''    {
        "key": "snapchat_account_daily",
        "role": "صرف وأداء حسابات Snapchat المبلّغ من المنصة",
        "grain": "حساب إعلاني × يوم",
        "authoritative_for": [
            "snapchat_provider_reported_spend",
            "snapchat_provider_attribution",
        ],
    },
    {
        "key": "snapchat_daily_stats",
        "role": "أداء Snapchat المجمع المحفوظ محليًا",
        "grain": "يوم",
        "authoritative_for": ["snapchat_purchases", "snapchat_revenue"],
    },
''',
    '''    {
        "key": SNAPCHAT_V2_PERFORMANCE_COLLECTION,
        "role": "أداء Snapchat الأصلي للحسابات والحملات المحددة داخل ميزان 2",
        "grain": "حساب أو حملة × يوم الرياض",
        "authoritative_for": [
            "snapchat_provider_reported_spend",
            "snapchat_provider_attribution",
            "snapchat_campaign_identity",
        ],
    },
    {
        "key": SNAPCHAT_LEGACY_ACCOUNT_COLLECTION,
        "role": "صرف حسابات Snapchat التاريخي الاحتياطي عند غياب تفعيل V2",
        "grain": "حساب إعلاني × يوم",
        "authoritative_for": [
            "snapchat_provider_reported_spend",
            "snapchat_provider_attribution",
        ],
    },
    {
        "key": SNAPCHAT_LEGACY_STATS_COLLECTION,
        "role": "أداء Snapchat التاريخي المجمع الاحتياطي عند غياب تفعيل V2",
        "grain": "يوم",
        "authoritative_for": ["snapchat_purchases", "snapchat_revenue"],
    },
''',
    "snapchat source definitions",
)

service = replace_once(
    service,
    '''    {
        "key": "tiktok_ads_daily",
        "role": "تغذية أداء حملات TikTok المحفوظة محليًا",
        "grain": "حملة × يوم",
        "authoritative_for": [
            "tiktok_provider_reported_spend",
            "tiktok_campaign_identity",
            "tiktok_impressions",
            "tiktok_clicks",
            "tiktok_platform_attribution",
        ],
    },
''',
    '''    {
        "key": TIKTOK_V2_PERFORMANCE_COLLECTION,
        "role": "أداء حسابات TikTok الأصلي المحفوظ عبر Integrations V2",
        "grain": "حساب إعلاني متصل × يوم",
        "authoritative_for": [
            "tiktok_provider_reported_spend",
            "tiktok_impressions",
            "tiktok_clicks",
            "tiktok_platform_attribution",
        ],
    },
    {
        "key": TIKTOK_LEGACY_PERFORMANCE_COLLECTION,
        "role": "تغذية TikTok التاريخية الاحتياطية عند غياب التفعيل الأصلي",
        "grain": "حملة × يوم",
        "authoritative_for": [
            "tiktok_provider_reported_spend",
            "tiktok_campaign_identity",
            "tiktok_impressions",
            "tiktok_clicks",
            "tiktok_platform_attribution",
        ],
    },
''',
    "tiktok source definitions",
)

adapter_marker = '''\n\ndef _campaign_rows(\n'''
adapters = r'''

def _snapchat_metric(row: dict, key: str) -> Any:
    metrics = row.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    return metrics.get(key)


def _normalize_snapchat_v2_rows(
    rows: list[dict],
    account_names: dict[str, str],
) -> list[dict]:
    """Adapt native Snapchat V2 facts without double-counting account rows.

    Snapchat persists provider-native values plus already-converted SAR values.
    The Ads Manager read model uses the stored SAR evidence directly so it does
    not need to guess an FX rate for historical rows.
    """

    output: list[dict] = []
    for row in rows:
        account_id = _clean_text(row.get("ad_account_id"), limit=120)
        entity_type = _clean_text(row.get("entity_type"), limit=40).lower()
        external_id = _clean_text(
            row.get("campaign_id") or row.get("external_id"),
            limit=160,
        )
        display_name = account_names.get(account_id) or account_id
        purchases = (
            row.get("purchases")
            if row.get("purchases") is not None
            else _snapchat_metric(row, "conversion_purchases")
        )
        revenue_sar = row.get("purchase_value_sar")
        normalized = {
            "date": row.get("date"),
            "account_id": account_id or None,
            "ad_account_id": account_id or None,
            "account_name": display_name or account_id or "حساب Snapchat",
            "campaign_id": (
                external_id if entity_type == "campaign" and external_id else "_default"
            ),
            "campaign_name": (
                f"حملة {external_id}"
                if entity_type == "campaign" and external_id
                else f"إجمالي {display_name or 'الحساب'}"
            ),
            # Normalize Snapchat campaign/account rows to stored SAR evidence.
            "spend": row.get("spend_sar"),
            "spend_sar": row.get("spend_sar"),
            "currency_native": "SAR",
            "fx_rate": 1.0,
            "purchases": purchases,
            "revenue": revenue_sar,
            "revenue_sar": revenue_sar,
            "impressions": _snapchat_metric(row, "impressions"),
            "clicks": _snapchat_metric(row, "swipes"),
            "updated_at": row.get("updated_at"),
            "conversion_data_status": (
                "available"
                if purchases is not None and revenue_sar is not None
                else "partial"
            ),
            "_data_source": SNAPCHAT_V2_PERFORMANCE_COLLECTION,
            "_aggregate_only": entity_type != "campaign",
            "_entity_type": entity_type,
        }
        output.append(normalized)
    return output


def _aggregate_snapchat_v2_daily(account_rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in account_rows:
        date_key = _clean_text(row.get("date"), limit=10)
        if date_key:
            grouped[date_key].append(row)

    output: list[dict] = []
    for date_key, rows in sorted(grouped.items()):
        def complete_sum(key: str, *, integer: bool = False):
            parser = (
                _optional_nonnegative_integer
                if integer
                else _optional_nonnegative_number
            )
            values = [parser(row.get(key)) for row in rows]
            if not values or any(value is None for value in values):
                return None
            total = sum(value for value in values if value is not None)
            return int(total) if integer else round(total, 2)

        complete_accounts = sum(
            row.get("purchases") is not None and row.get("revenue_sar") is not None
            for row in rows
        )
        markers = [
            _clean_text(row.get("updated_at"), limit=80)
            for row in rows
            if _clean_text(row.get("updated_at"), limit=80)
        ]
        output.append(
            {
                "date": date_key,
                "purchases": complete_sum("purchases", integer=True),
                "revenue": complete_sum("revenue_sar"),
                "impressions": complete_sum("impressions", integer=True),
                "clicks": complete_sum("clicks", integer=True),
                "conversion_data_status": (
                    "available" if complete_accounts == len(rows) else "partial"
                ),
                "conversion_accounts_total": len(rows),
                "conversion_accounts_complete": complete_accounts,
                "updated_at": max(markers) if markers else None,
                "_data_source": SNAPCHAT_V2_PERFORMANCE_COLLECTION,
            }
        )
    return output


def _normalize_tiktok_v2_rows(rows: list[dict]) -> list[dict]:
    """Adapt native TikTok account-day facts to the Ads Manager read model."""

    output: list[dict] = []
    for row in rows:
        account_id = _clean_text(row.get("ad_account_id"), limit=120)
        display_name = _clean_text(row.get("display_name"), limit=180)
        output.append(
            {
                "date": row.get("date"),
                "account_id": account_id or None,
                "advertiser_id": account_id or None,
                "campaign_id": "_default",
                "campaign_name": (
                    f"إجمالي {display_name}" if display_name else "إجمالي الحساب"
                ),
                "spend": row.get("spend_native"),
                "currency_native": row.get("currency_native"),
                "fx_rate": row.get("fx_rate_to_sar"),
                "conversions": row.get("conversions"),
                "impressions": row.get("impressions"),
                "clicks": row.get("clicks"),
                "updated_at": row.get("updated_at") or row.get("observed_at"),
                "_data_source": TIKTOK_V2_PERFORMANCE_COLLECTION,
                "_aggregate_only": True,
            }
        )
    return output
'''
if adapter_marker not in service:
    raise RuntimeError("adapter insertion marker missing")
service = service.replace(adapter_marker, adapters + adapter_marker, 1)

service = replace_once(
    service,
    '''def _campaign_coverage(provider: str, rows: list[dict]) -> dict:
    if provider == "snapchat":
        return {
            "status": "unavailable",
            "campaign_count": 0,
            "source_rows": 0,
            "detail": (
                "موصل Snapchat الحالي يحفظ أداء الحساب، ولا يحفظ هوية الحملات بعد."
            ),
        }
    campaign_ids = {
''',
    '''def _campaign_coverage(provider: str, rows: list[dict]) -> dict:
    campaign_ids = {
''',
    "campaign coverage",
)

task_start = '''        snap_account_task = _rows(\n'''
task_end = '''        meta_v2_task = _rows(\n'''
new_tasks = '''        snap_legacy_account_task = _rows(
            self.db,
            SNAPCHAT_LEGACY_ACCOUNT_COLLECTION,
            date_query,
            {
                "_id": 0,
                "date": 1,
                "spend_sar": 1,
                "spend": 1,
                "updated_at": 1,
                "ad_account_id": 1,
                "account_name": 1,
                "purchases": 1,
                "revenue_sar": 1,
                "conversion_data_status": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("ad_account_id", 1)],
        )
        snap_legacy_accounts_task = _rows(
            self.db,
            "snapchat_ad_accounts",
            {"user_id": user_id},
            {
                "_id": 0,
                "ad_account_id": 1,
                "name": 1,
                "enabled": 1,
            },
            limit=MAX_ACCOUNTS,
            sort=[("enabled", -1), ("name", 1), ("ad_account_id", 1)],
        )
        snap_legacy_stats_task = _rows(
            self.db,
            SNAPCHAT_LEGACY_STATS_COLLECTION,
            date_query,
            {
                "_id": 0,
                "date": 1,
                "purchases": 1,
                "revenue": 1,
                "conversion_data_status": 1,
                "conversion_accounts_total": 1,
                "conversion_accounts_complete": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1)],
        )
        snap_v2_task = _rows(
            self.db,
            SNAPCHAT_V2_PERFORMANCE_COLLECTION,
            {**date_query, "provider": SNAPCHAT_INTEGRATION_PROVIDER},
            {
                "_id": 0,
                "date": 1,
                "ad_account_id": 1,
                "entity_type": 1,
                "external_id": 1,
                "campaign_id": 1,
                "currency": 1,
                "spend_native": 1,
                "spend_sar": 1,
                "purchases": 1,
                "purchase_value_native": 1,
                "purchase_value_sar": 1,
                "metrics": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("ad_account_id", 1), ("entity_type", 1)],
        )
        snap_selected_accounts_task = _rows(
            self.db,
            "mezan_integration_accounts_v2",
            {
                "user_id": user_id,
                "provider": SNAPCHAT_INTEGRATION_PROVIDER,
                "connection_provenance": "api_connection",
                "mezan_selected": True,
            },
            {
                "_id": 0,
                "external_account_id": 1,
                "ad_account_id": 1,
                "display_name": 1,
                "currency": 1,
                "timezone": 1,
                "mezan_selected": 1,
            },
            limit=MAX_ACCOUNTS,
            sort=[("display_name", 1), ("external_account_id", 1)],
        )
        tiktok_legacy_task = _rows(
            self.db,
            TIKTOK_LEGACY_PERFORMANCE_COLLECTION,
            date_query,
            {
                "_id": 0,
                "date": 1,
                "account_id": 1,
                "advertiser_id": 1,
                "campaign_id": 1,
                "campaign_name": 1,
                "spend": 1,
                "currency": 1,
                "currency_native": 1,
                "fx_rate": 1,
                "purchases": 1,
                "conversions": 1,
                "revenue": 1,
                "impressions": 1,
                "clicks": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("campaign_id", 1), ("account_id", 1)],
        )
        tiktok_v2_task = _rows(
            self.db,
            TIKTOK_V2_PERFORMANCE_COLLECTION,
            {**date_query, "provider": TIKTOK_INTEGRATION_PROVIDER},
            {
                "_id": 0,
                "date": 1,
                "ad_account_id": 1,
                "display_name": 1,
                "currency_native": 1,
                "spend_native": 1,
                "spend_sar": 1,
                "fx_rate_to_sar": 1,
                "conversions": 1,
                "impressions": 1,
                "clicks": 1,
                "empty_provider_row": 1,
                "observed_at": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("ad_account_id", 1)],
        )
        tiktok_connected_accounts_task = _rows(
            self.db,
            "mezan_integration_accounts_v2",
            {
                "user_id": user_id,
                "provider": TIKTOK_INTEGRATION_PROVIDER,
                "connection_provenance": "api_connection",
                "connection_status": "connected",
            },
            {
                "_id": 0,
                "external_account_id": 1,
                "ad_account_id": 1,
                "display_name": 1,
                "currency": 1,
                "timezone": 1,
            },
            limit=MAX_ACCOUNTS,
            sort=[("display_name", 1), ("external_account_id", 1)],
        )
'''
service = replace_between(service, task_start, task_end, new_tasks, "provider tasks")

gather_start = '''        (\n            integration_overview,\n'''
gather_end = '''        source_limit_reached = {\n'''
new_gather = '''        (
            integration_overview,
            booked_expense,
            snap_legacy_account_rows,
            snap_legacy_accounts,
            snap_legacy_stats_rows,
            snap_v2_rows,
            snap_selected_accounts,
            tiktok_legacy_rows,
            tiktok_v2_rows,
            tiktok_connected_accounts,
            meta_v2_rows,
            meta_legacy_rows,
            meta_selected_accounts,
            accounts,
            legacy_accounts,
            currency_settings,
        ) = await asyncio.gather(
            integration_task,
            booked_expense_task,
            snap_legacy_account_task,
            snap_legacy_accounts_task,
            snap_legacy_stats_task,
            snap_v2_task,
            snap_selected_accounts_task,
            tiktok_legacy_task,
            tiktok_v2_task,
            tiktok_connected_accounts_task,
            meta_v2_task,
            meta_legacy_task,
            meta_selected_accounts_task,
            accounts_task,
            legacy_accounts_task,
            currency_settings_task,
        )

        snap_legacy_account_limit_reached = (
            len(snap_legacy_account_rows) > MAX_PERFORMANCE_ROWS
        )
        snap_legacy_accounts_limit_reached = len(snap_legacy_accounts) > MAX_ACCOUNTS
        snap_legacy_stats_limit_reached = (
            len(snap_legacy_stats_rows) > MAX_PERFORMANCE_ROWS
        )
        snap_v2_limit_reached = len(snap_v2_rows) > MAX_PERFORMANCE_ROWS
        snap_selection_limit_reached = len(snap_selected_accounts) > MAX_ACCOUNTS
        tiktok_legacy_limit_reached = len(tiktok_legacy_rows) > MAX_PERFORMANCE_ROWS
        tiktok_v2_limit_reached = len(tiktok_v2_rows) > MAX_PERFORMANCE_ROWS
        tiktok_accounts_limit_reached = len(tiktok_connected_accounts) > MAX_ACCOUNTS
        meta_v2_limit_reached = len(meta_v2_rows) > MAX_PERFORMANCE_ROWS
        meta_legacy_limit_reached = len(meta_legacy_rows) > MAX_PERFORMANCE_ROWS
        meta_selection_limit_reached = len(meta_selected_accounts) > MAX_ACCOUNTS
        accounts_limit_reached = len(accounts) > MAX_ACCOUNTS
        legacy_accounts_limit_reached = len(legacy_accounts) > MAX_ACCOUNTS

        snap_legacy_account_rows = snap_legacy_account_rows[:MAX_PERFORMANCE_ROWS]
        snap_legacy_accounts = snap_legacy_accounts[:MAX_ACCOUNTS]
        snap_legacy_stats_rows = snap_legacy_stats_rows[:MAX_PERFORMANCE_ROWS]
        snap_v2_rows = snap_v2_rows[:MAX_PERFORMANCE_ROWS]
        snap_selected_accounts = snap_selected_accounts[:MAX_ACCOUNTS]
        tiktok_legacy_rows = tiktok_legacy_rows[:MAX_PERFORMANCE_ROWS]
        tiktok_v2_rows = tiktok_v2_rows[:MAX_PERFORMANCE_ROWS]
        tiktok_connected_accounts = tiktok_connected_accounts[:MAX_ACCOUNTS]
        meta_v2_rows = meta_v2_rows[:MAX_PERFORMANCE_ROWS]
        meta_legacy_rows = meta_legacy_rows[:MAX_PERFORMANCE_ROWS]
        meta_selected_accounts = meta_selected_accounts[:MAX_ACCOUNTS]
        accounts = accounts[:MAX_ACCOUNTS]
        legacy_accounts = legacy_accounts[:MAX_ACCOUNTS]

        selected_snap_ids = {
            _clean_text(
                row.get("external_account_id") or row.get("ad_account_id"),
                limit=120,
            )
            for row in snap_selected_accounts
            if _clean_text(
                row.get("external_account_id") or row.get("ad_account_id"),
                limit=120,
            )
        }
        snap_v2_authoritative = bool(snap_v2_rows or snap_selected_accounts)
        if snap_v2_authoritative:
            if selected_snap_ids:
                snap_v2_rows = [
                    row
                    for row in snap_v2_rows
                    if _clean_text(row.get("ad_account_id"), limit=120)
                    in selected_snap_ids
                ]
            snap_account_names = {
                _clean_text(
                    row.get("external_account_id") or row.get("ad_account_id"),
                    limit=120,
                ): _clean_text(row.get("display_name"), limit=180)
                for row in snap_selected_accounts
                if _clean_text(
                    row.get("external_account_id") or row.get("ad_account_id"),
                    limit=120,
                )
            }
            snap_normalized_rows = _normalize_snapchat_v2_rows(
                snap_v2_rows,
                snap_account_names,
            )
            snap_account_rows = [
                row
                for row in snap_normalized_rows
                if row.get("_entity_type") == "ad_account"
            ]
            snap_campaign_rows = [
                row
                for row in snap_normalized_rows
                if row.get("_entity_type") == "campaign"
            ]
            snap_campaign_source_rows = snap_campaign_rows or list(snap_account_rows)
            snap_stats_rows = _aggregate_snapchat_v2_daily(snap_account_rows)
            snap_accounts = [
                {
                    "ad_account_id": _clean_text(
                        row.get("external_account_id") or row.get("ad_account_id"),
                        limit=120,
                    ),
                    "name": _clean_text(row.get("display_name"), limit=180),
                    "enabled": True,
                }
                for row in snap_selected_accounts
                if _clean_text(
                    row.get("external_account_id") or row.get("ad_account_id"),
                    limit=120,
                )
            ]
            snap_source_key = SNAPCHAT_V2_PERFORMANCE_COLLECTION
            snap_stats_source_key = SNAPCHAT_V2_PERFORMANCE_COLLECTION
            snap_account_config_source_key = (
                "mezan_integration_accounts_v2:snapchat_ads"
            )
            active_snap_limit_reached = (
                snap_v2_limit_reached or snap_selection_limit_reached
            )
            active_snap_stats_limit_reached = active_snap_limit_reached
            active_snap_account_config_limit_reached = snap_selection_limit_reached
        else:
            snap_account_rows = snap_legacy_account_rows
            snap_accounts = snap_legacy_accounts
            snap_stats_rows = snap_legacy_stats_rows
            snap_campaign_source_rows = []
            snap_normalized_rows = []
            snap_source_key = SNAPCHAT_LEGACY_ACCOUNT_COLLECTION
            snap_stats_source_key = SNAPCHAT_LEGACY_STATS_COLLECTION
            snap_account_config_source_key = "snapchat_ad_accounts"
            active_snap_limit_reached = snap_legacy_account_limit_reached
            active_snap_stats_limit_reached = snap_legacy_stats_limit_reached
            active_snap_account_config_limit_reached = (
                snap_legacy_accounts_limit_reached
            )

        connected_tiktok_ids = {
            _clean_text(
                row.get("external_account_id") or row.get("ad_account_id"),
                limit=120,
            )
            for row in tiktok_connected_accounts
            if _clean_text(
                row.get("external_account_id") or row.get("ad_account_id"),
                limit=120,
            )
        }
        tiktok_v2_authoritative = bool(tiktok_v2_rows or tiktok_connected_accounts)
        if tiktok_v2_authoritative:
            if connected_tiktok_ids:
                tiktok_v2_rows = [
                    row
                    for row in tiktok_v2_rows
                    if _clean_text(row.get("ad_account_id"), limit=120)
                    in connected_tiktok_ids
                ]
            tiktok_rows = _normalize_tiktok_v2_rows(tiktok_v2_rows)
            tiktok_source_key = TIKTOK_V2_PERFORMANCE_COLLECTION
            active_tiktok_limit_reached = (
                tiktok_v2_limit_reached or tiktok_accounts_limit_reached
            )
        else:
            tiktok_rows = tiktok_legacy_rows
            tiktok_source_key = TIKTOK_LEGACY_PERFORMANCE_COLLECTION
            active_tiktok_limit_reached = tiktok_legacy_limit_reached

        selected_meta_ids = {
            _clean_text(
                row.get("external_account_id") or row.get("ad_account_id"),
                limit=120,
            ).removeprefix("act_")
            for row in meta_selected_accounts
            if _clean_text(
                row.get("external_account_id") or row.get("ad_account_id"),
                limit=120,
            )
        }
        meta_v2_authoritative = bool(meta_v2_rows or meta_selected_accounts)
        if meta_v2_authoritative:
            meta_v2_rows = [
                row
                for row in meta_v2_rows
                if _clean_text(row.get("ad_account_id"), limit=120)
                .removeprefix("act_")
                in selected_meta_ids
            ]
            meta_rows = _normalize_meta_v2_rows(meta_v2_rows)
            meta_source_key = META_V2_PERFORMANCE_COLLECTION
            active_meta_limit_reached = (
                meta_v2_limit_reached or meta_selection_limit_reached
            )
        else:
            meta_rows = meta_legacy_rows
            meta_source_key = META_LEGACY_PERFORMANCE_COLLECTION
            active_meta_limit_reached = meta_legacy_limit_reached

'''
service = replace_between(service, gather_start, gather_end, new_gather, "gather and authority")

source_start = '''        source_limit_reached = {\n'''
source_end = '''        stored_usd_to_sar_rate = _optional_nonnegative_number(\n'''
new_sources = '''        source_limit_reached = {
            snap_source_key: active_snap_limit_reached,
            snap_stats_source_key: active_snap_stats_limit_reached,
            snap_account_config_source_key: active_snap_account_config_limit_reached,
            tiktok_source_key: active_tiktok_limit_reached,
            meta_source_key: active_meta_limit_reached,
            "ads_accounts": accounts_limit_reached,
            "counterparties": legacy_accounts_limit_reached,
        }
        dated_sources = {
            tiktok_source_key: tiktok_rows,
            meta_source_key: meta_rows,
        }
        if snap_v2_authoritative:
            dated_sources[snap_source_key] = snap_normalized_rows
        else:
            dated_sources[snap_source_key] = snap_account_rows
            dated_sources[snap_stats_source_key] = snap_stats_rows
        source_invalid_date_rows = {
            source: sum(
                not _valid_source_date(row.get("date"), from_iso, to_iso)
                for row in rows
            )
            for source, rows in dated_sources.items()
        }
        snap_account_rows = [
            row
            for row in snap_account_rows
            if _valid_source_date(row.get("date"), from_iso, to_iso)
        ]
        snap_stats_rows = [
            row
            for row in snap_stats_rows
            if _valid_source_date(row.get("date"), from_iso, to_iso)
        ]
        snap_campaign_source_rows = [
            row
            for row in snap_campaign_source_rows
            if _valid_source_date(row.get("date"), from_iso, to_iso)
        ]
        tiktok_rows = [
            row
            for row in tiktok_rows
            if _valid_source_date(row.get("date"), from_iso, to_iso)
        ]
        meta_rows = [
            row
            for row in meta_rows
            if _valid_source_date(row.get("date"), from_iso, to_iso)
        ]
'''
service = replace_between(service, source_start, source_end, new_sources, "dynamic sources")

service = replace_once(
    service,
    '''        raw_rows = {
            "snapchat": snap_stats_rows,
            "tiktok": tiktok_rows,
            "meta": meta_rows,
        }
        campaign_rows = {
            "tiktok": _campaign_rows(
                "tiktok",
                tiktok_rows,
                exact_accounts=exact_accounts,
                provider_accounts=provider_accounts,
            ),
            "meta": _campaign_rows(
                "meta",
                meta_rows,
                exact_accounts=exact_accounts,
                provider_accounts=provider_accounts,
            ),
        }
''',
    '''        raw_rows = {
            "snapchat": snap_stats_rows,
            "tiktok": tiktok_rows,
            "meta": meta_rows,
        }
        campaign_rows = {
            "snapchat": _campaign_rows(
                "snapchat",
                snap_campaign_source_rows,
                exact_accounts=exact_accounts,
                provider_accounts=provider_accounts,
            ),
            "tiktok": _campaign_rows(
                "tiktok",
                tiktok_rows,
                exact_accounts=exact_accounts,
                provider_accounts=provider_accounts,
            ),
            "meta": _campaign_rows(
                "meta",
                meta_rows,
                exact_accounts=exact_accounts,
                provider_accounts=provider_accounts,
            ),
        }
''',
    "campaign rows",
)

service = replace_once(
    service,
    '''        provider_summaries: list[dict] = []
        snap_account_coverage: list[dict] = []
''',
    '''        provider_source_keys = {
            "snapchat": snap_source_key,
            "tiktok": tiktok_source_key,
            "meta": meta_source_key,
        }
        provider_summaries: list[dict] = []
        snap_account_coverage: list[dict] = []
''',
    "provider source keys",
)

old_provider_key = '''            provider_source_key = (
                "snapchat_account_daily"
                if provider_key == "snapchat"
                else meta_source_key
                if provider_key == "meta"
                else "tiktok_ads_daily"
            )
'''
service = replace_once(
    service,
    old_provider_key,
    '''            provider_source_key = provider_source_keys[provider_key]
''',
    "provider loop source key",
)
service = replace_once(
    service,
    old_provider_key,
    '''                    provider_source_key = provider_source_keys[provider_key]
''',
    "daily source key",
)

service = replace_once(
    service,
    '''                            provider_source_truncated
                            or source_limit_reached["snapchat_ad_accounts"]
''',
    '''                            provider_source_truncated
                            or source_limit_reached[snap_account_config_source_key]
''',
    "snap account config limit",
)
service = replace_once(
    service,
    '''                performance_source_truncated = source_limit_reached[
                    "snapchat_daily_stats"
                ]
                performance_source_invalid = bool(
                    source_invalid_date_rows["snapchat_daily_stats"]
                )
''',
    '''                performance_source_truncated = source_limit_reached[
                    snap_stats_source_key
                ]
                performance_source_invalid = bool(
                    source_invalid_date_rows[snap_stats_source_key]
                )
''',
    "snap performance source",
)

service = replace_once(
    service,
    '''                candidate_impressions = None
                candidate_clicks = None
                campaign_source_rows = []
''',
    '''                snap_impression_values = [
                    _optional_nonnegative_integer(row.get("impressions"))
                    for row in snap_stats_rows
                ]
                snap_click_values = [
                    _optional_nonnegative_integer(row.get("clicks"))
                    for row in snap_stats_rows
                ]
                candidate_impressions = (
                    sum(value for value in snap_impression_values if value is not None)
                    if snap_stats_rows
                    and all(value is not None for value in snap_impression_values)
                    else None
                )
                candidate_clicks = (
                    sum(value for value in snap_click_values if value is not None)
                    if snap_stats_rows
                    and all(value is not None for value in snap_click_values)
                    else None
                )
                campaign_source_rows = snap_campaign_source_rows
''',
    "snap raw performance metrics",
)

service = replace_once(
    service,
    '''            performance_eligible = performance_coverage[
                "eligible_for_ratios"
            ]
            revenue_sar = (
                candidate_revenue_sar if performance_eligible else None
            )
            purchases = (
                candidate_purchases if performance_eligible else None
            )
            impressions = (
                candidate_impressions if performance_eligible else None
            )
            clicks = candidate_clicks if performance_eligible else None
''',
    '''            performance_eligible = performance_coverage[
                "eligible_for_ratios"
            ]
            fatal_performance_reasons = {
                "source_unavailable",
                "source_truncated",
                "invalid_source_dates",
                "incomplete_spend",
                "missing_performance_dates",
                "stale_performance",
                "unverified_zero_performance",
            }
            performance_facts_usable = bool(performance_rows) and not (
                fatal_performance_reasons
                & set(performance_coverage.get("reasons") or [])
            )
            revenue_sar = (
                candidate_revenue_sar
                if performance_facts_usable and revenue_complete
                else None
            )
            purchases = (
                candidate_purchases
                if performance_facts_usable and conversions_complete
                else None
            )
            impressions = (
                candidate_impressions if performance_facts_usable else None
            )
            clicks = candidate_clicks if performance_facts_usable else None
''',
    "raw performance eligibility",
)

service = replace_once(
    service,
    '''            metrics = _metric_set(
                provider_reported_spend_sar=provider_reported_spend_sar,
                booked_ad_expense_sar=booked_ad_expense_sar,
                revenue_sar=revenue_sar,
                purchases=purchases,
                impressions=impressions,
                clicks=clicks,
            )
''',
    '''            metrics = _metric_set(
                provider_reported_spend_sar=provider_reported_spend_sar,
                booked_ad_expense_sar=booked_ad_expense_sar,
                revenue_sar=revenue_sar,
                purchases=purchases,
                impressions=impressions,
                clicks=clicks,
            )
            if not performance_eligible:
                for ratio_key in (
                    "platform_roas",
                    "platform_cpa_sar",
                    "platform_cpc_sar",
                    "platform_cpm_sar",
                    "platform_ctr_pct",
                ):
                    metrics[ratio_key] = None
''',
    "ratio fail closed",
)

service = replace_once(
    service,
    '''        all_campaigns = [
            row
            for provider_key in ("tiktok", "meta")
''',
    '''        all_campaigns = [
            row
            for provider_key in ("snapchat", "tiktok", "meta")
''',
    "include snapchat campaigns",
)

SERVICE.write_text(service, encoding="utf-8")

tests = TESTS.read_text(encoding="utf-8")
new_tests = r'''

@pytest.mark.asyncio
async def test_snapchat_v2_is_authoritative_and_filters_selected_accounts():
    db = FakeDB(
        {
            "mezan_snapchat_performance_daily_v2": [
                {
                    "user_id": OWNER_ID,
                    "provider": "snapchat_ads",
                    "ad_account_id": "snap-selected",
                    "entity_type": "ad_account",
                    "external_id": "snap-selected",
                    "date": "2026-07-10",
                    "currency": "USD",
                    "spend_native": 10,
                    "spend_sar": 37.5,
                    "purchases": 2,
                    "purchase_value_native": 20,
                    "purchase_value_sar": 75,
                    "metrics": {
                        "impressions": 1000,
                        "swipes": 50,
                        "conversion_purchases": 2,
                    },
                    "updated_at": "2026-07-28T10:00:00+00:00",
                },
                {
                    "user_id": OWNER_ID,
                    "provider": "snapchat_ads",
                    "ad_account_id": "snap-selected",
                    "entity_type": "campaign",
                    "external_id": "snap-campaign",
                    "campaign_id": "snap-campaign",
                    "date": "2026-07-10",
                    "currency": "USD",
                    "spend_native": 10,
                    "spend_sar": 37.5,
                    "purchases": 2,
                    "purchase_value_native": 20,
                    "purchase_value_sar": 75,
                    "metrics": {
                        "impressions": 1000,
                        "swipes": 50,
                        "conversion_purchases": 2,
                    },
                    "updated_at": "2026-07-28T10:00:00+00:00",
                },
                {
                    "user_id": OWNER_ID,
                    "provider": "snapchat_ads",
                    "ad_account_id": "snap-not-selected",
                    "entity_type": "ad_account",
                    "external_id": "snap-not-selected",
                    "date": "2026-07-10",
                    "spend_sar": 999,
                    "purchases": 99,
                    "purchase_value_sar": 999,
                    "metrics": {"impressions": 9999, "swipes": 999},
                },
                {
                    "user_id": OTHER_OWNER_ID,
                    "provider": "snapchat_ads",
                    "ad_account_id": "snap-other-owner",
                    "entity_type": "ad_account",
                    "external_id": "snap-other-owner",
                    "date": "2026-07-10",
                    "spend_sar": 999,
                    "purchases": 99,
                    "purchase_value_sar": 999,
                    "metrics": {"impressions": 9999, "swipes": 999},
                },
            ],
            "mezan_integration_accounts_v2": [
                {
                    "user_id": OWNER_ID,
                    "provider": "snapchat_ads",
                    "connection_provenance": "api_connection",
                    "external_account_id": "snap-selected",
                    "display_name": "Selected Snap",
                    "currency": "USD",
                    "mezan_selected": True,
                },
                {
                    "user_id": OWNER_ID,
                    "provider": "snapchat_ads",
                    "connection_provenance": "api_connection",
                    "external_account_id": "snap-not-selected",
                    "display_name": "Old Snap",
                    "mezan_selected": False,
                },
            ],
            "snapchat_account_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "ad_account_id": "legacy-snap",
                    "spend_sar": 999,
                    "purchases": 99,
                    "revenue_sar": 999,
                }
            ],
            "snapchat_daily_stats": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "purchases": 99,
                    "revenue": 999,
                    "conversion_data_status": "available",
                }
            ],
        }
    )

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="snapchat",
    )

    provider = result["providers"][0]
    assert provider["metrics"]["provider_reported_spend_sar"] == 37.5
    assert provider["metrics"]["platform_attributed_revenue_sar"] == 75
    assert provider["metrics"]["platform_reported_purchases"] == 2
    assert provider["metrics"]["platform_reported_impressions"] == 1000
    assert provider["metrics"]["platform_reported_clicks"] == 50
    assert provider["campaign_coverage"]["status"] == "available"
    assert result["campaign_pagination"]["total"] == 1
    assert result["campaigns"][0]["campaign_id"] == "snap-campaign"
    assert result["campaigns"][0]["data_source"] == (
        "mezan_snapchat_performance_daily_v2"
    )
    serialized = json.dumps(result, ensure_ascii=False)
    assert "legacy-snap" not in serialized
    assert "snap-not-selected" not in serialized
    assert OTHER_OWNER_ID not in serialized
    assert db.write_attempts == []


@pytest.mark.asyncio
async def test_snapchat_v2_selection_without_rows_does_not_use_legacy():
    db = FakeDB(
        {
            "mezan_integration_accounts_v2": [
                {
                    "user_id": OWNER_ID,
                    "provider": "snapchat_ads",
                    "connection_provenance": "api_connection",
                    "external_account_id": "snap-selected",
                    "display_name": "Selected Snap",
                    "mezan_selected": True,
                }
            ],
            "snapchat_account_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "ad_account_id": "legacy-snap",
                    "spend_sar": 50,
                }
            ],
            "snapchat_daily_stats": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "purchases": 2,
                    "revenue": 100,
                    "conversion_data_status": "available",
                }
            ],
        }
    )

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="snapchat",
    )

    assert result["metrics"]["provider_reported_spend_sar"] is None
    assert result["campaigns"] == []
    assert result["providers"][0]["campaign_coverage"]["status"] == "unavailable"
    assert db.write_attempts == []


@pytest.mark.asyncio
async def test_tiktok_v2_is_authoritative_and_exposes_raw_facts_without_fake_revenue():
    db = FakeDB(
        {
            "mezan_tiktok_performance_daily_v2": [
                {
                    "user_id": OWNER_ID,
                    "provider": "tiktok_ads",
                    "ad_account_id": "tt-connected",
                    "display_name": "TikTok Riyadh",
                    "date": "2026-07-10",
                    "currency_native": "USD",
                    "spend_native": 10,
                    "spend_sar": 37.5,
                    "fx_rate_to_sar": 3.75,
                    "conversions": 2,
                    "impressions": 1000,
                    "clicks": 50,
                    "updated_at": "2026-07-28T10:00:00+00:00",
                },
                {
                    "user_id": OWNER_ID,
                    "provider": "tiktok_ads",
                    "ad_account_id": "tt-disconnected",
                    "date": "2026-07-10",
                    "currency_native": "SAR",
                    "spend_native": 999,
                    "fx_rate_to_sar": 1,
                    "conversions": 99,
                    "impressions": 9999,
                    "clicks": 999,
                },
                {
                    "user_id": OTHER_OWNER_ID,
                    "provider": "tiktok_ads",
                    "ad_account_id": "tt-other-owner",
                    "date": "2026-07-10",
                    "currency_native": "SAR",
                    "spend_native": 999,
                    "fx_rate_to_sar": 1,
                    "conversions": 99,
                    "impressions": 9999,
                    "clicks": 999,
                },
            ],
            "mezan_integration_accounts_v2": [
                {
                    "user_id": OWNER_ID,
                    "provider": "tiktok_ads",
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                    "external_account_id": "tt-connected",
                    "display_name": "TikTok Riyadh",
                    "currency": "USD",
                }
            ],
            "tiktok_ads_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "advertiser_id": "tt-legacy",
                    "campaign_id": "legacy-tiktok",
                    "campaign_name": "Legacy TikTok",
                    "spend": 999,
                    "currency": "SAR",
                    "purchases": 99,
                    "revenue": 999,
                    "impressions": 9999,
                    "clicks": 999,
                }
            ],
        }
    )

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="tiktok",
    )

    provider = result["providers"][0]
    assert provider["metrics"]["provider_reported_spend_sar"] == 37.5
    assert provider["metrics"]["platform_attributed_revenue_sar"] is None
    assert provider["metrics"]["platform_reported_purchases"] == 2
    assert provider["metrics"]["platform_reported_impressions"] == 1000
    assert provider["metrics"]["platform_reported_clicks"] == 50
    assert provider["metrics"]["platform_roas"] is None
    assert provider["metrics"]["platform_cpa_sar"] is None
    assert provider["campaign_coverage"]["status"] == "aggregate_only"
    assert result["campaign_pagination"]["total"] == 1
    assert result["campaigns"][0]["campaign_id"] == "_default"
    assert result["campaigns"][0]["data_source"] == (
        "mezan_tiktok_performance_daily_v2"
    )
    serialized = json.dumps(result, ensure_ascii=False)
    assert "legacy-tiktok" not in serialized
    assert "tt-disconnected" not in serialized
    assert OTHER_OWNER_ID not in serialized
    assert db.write_attempts == []


@pytest.mark.asyncio
async def test_tiktok_native_connection_without_rows_does_not_use_legacy():
    db = FakeDB(
        {
            "mezan_integration_accounts_v2": [
                {
                    "user_id": OWNER_ID,
                    "provider": "tiktok_ads",
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                    "external_account_id": "tt-connected",
                    "display_name": "TikTok Riyadh",
                }
            ],
            "tiktok_ads_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "advertiser_id": "tt-legacy",
                    "campaign_id": "legacy-tiktok",
                    "campaign_name": "Legacy TikTok",
                    "spend": 50,
                    "currency": "SAR",
                    "purchases": 2,
                    "revenue": 100,
                    "impressions": 500,
                    "clicks": 20,
                }
            ],
        }
    )

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="tiktok",
    )

    assert result["metrics"]["provider_reported_spend_sar"] is None
    assert result["campaigns"] == []
    assert result["providers"][0]["campaign_coverage"]["status"] == "unavailable"
    assert db.write_attempts == []
'''
if "test_snapchat_v2_is_authoritative_and_filters_selected_accounts" in tests:
    raise RuntimeError("native V2 tests already present")
tests += new_tests
TESTS.write_text(tests, encoding="utf-8")

print("ADS_MANAGER_SNAP_TIKTOK_V2_PATCH_APPLIED")
