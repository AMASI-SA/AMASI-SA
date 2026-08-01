from __future__ import annotations

from pathlib import Path


SERVICE_PATH = Path("backend/ads_manager/service.py")
TEST_PATH = Path("backend/tests/test_unified_ads_manager_phase1.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(
    text: str,
    start_marker: str,
    end_marker: str,
    replacement: str,
    label: str,
) -> str:
    if text.count(start_marker) != 1:
        raise SystemExit(
            f"{label}: expected one start marker, found {text.count(start_marker)}"
        )
    start = text.index(start_marker)
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"{label}: end marker missing")
    return text[:start] + replacement + text[end:]


def patch_service(service: str) -> str:
    service = replace_once(
        service,
        'PROVIDER_ORDER = ("snapchat", "tiktok", "meta")\nPROVIDER_ALIASES = {',
        'PROVIDER_ORDER = ("snapchat", "tiktok", "meta")\n'
        'META_V2_PERFORMANCE_COLLECTION = "mezan_meta_performance_daily_v2"\n'
        'META_LEGACY_PERFORMANCE_COLLECTION = "meta_ads_daily"\n'
        'META_INTEGRATION_PROVIDER = "meta_ads"\n'
        'PROVIDER_ALIASES = {',
        "Meta collection constants",
    )

    source_start = '    {\n        "key": "meta_ads_daily",\n'
    source_end = '    {\n        "key": "tiktok_ads_daily",\n'
    source_replacement = '''    {
        "key": META_V2_PERFORMANCE_COLLECTION,
        "role": "أداء حسابات Meta الأصلي المحفوظ عبر Integrations V2",
        "grain": "حساب إعلاني محدد × يوم",
        "authoritative_for": [
            "meta_provider_reported_spend",
            "meta_impressions",
            "meta_clicks",
            "meta_platform_attribution",
        ],
    },
    {
        "key": META_LEGACY_PERFORMANCE_COLLECTION,
        "role": "مصدر Meta التاريخي الاحتياطي عند غياب مصدر V2",
        "grain": "حملة × يوم",
        "authoritative_for": [
            "meta_provider_reported_spend",
            "meta_campaign_identity",
            "meta_impressions",
            "meta_clicks",
            "meta_platform_attribution",
        ],
    },
'''
    service = replace_between(
        service,
        source_start,
        source_end,
        source_replacement,
        "Meta source definitions",
    )

    helper = '''def _normalize_meta_v2_rows(rows: list[dict]) -> list[dict]:
    """Adapt native Meta V2 account-day facts to the Ads Manager read model.

    Native V2 is account-grain, not campaign-grain. The adapter preserves the
    provider-native currency and stored FX evidence, marks the row as aggregate
    only, and never mixes it with the historical ``meta_ads_daily`` source.
    """

    output: list[dict] = []
    for row in rows:
        account_id = _clean_text(row.get("ad_account_id"), limit=120)
        display_name = _clean_text(row.get("display_name"), limit=180)
        output.append(
            {
                "date": row.get("date"),
                "account_id": account_id or None,
                "campaign_id": "_default",
                "campaign_name": (
                    f"إجمالي {display_name}" if display_name else "إجمالي الحساب"
                ),
                "spend": row.get("spend_native"),
                "currency_native": row.get("currency_native"),
                "fx_rate": row.get("fx_rate_to_sar"),
                "purchases": row.get("purchases"),
                "purchase_value": row.get("purchase_value_native"),
                "impressions": row.get("impressions"),
                "clicks": row.get("clicks"),
                "updated_at": row.get("updated_at") or row.get("observed_at"),
                "_data_source": META_V2_PERFORMANCE_COLLECTION,
                "_aggregate_only": True,
            }
        )
    return output


'''
    service = replace_once(
        service,
        "def _campaign_rows(\n",
        helper + "def _campaign_rows(\n",
        "Meta V2 adapter insertion",
    )

    service = replace_once(
        service,
        '''                "data_source": (
                    "meta_ads_daily" if provider == "meta" else "tiktok_ads_daily"
                ),
''',
        '''                "data_source": (
                    _clean_text(row.get("_data_source"), limit=80)
                    or (
                        META_LEGACY_PERFORMANCE_COLLECTION
                        if provider == "meta"
                        else "tiktok_ads_daily"
                    )
                ),
''',
        "campaign source evidence",
    )

    meta_task_start = "        meta_task = _rows(\n"
    meta_task_end = "        accounts_task = _rows(\n"
    meta_tasks = '''        meta_v2_task = _rows(
            self.db,
            META_V2_PERFORMANCE_COLLECTION,
            {**date_query, "provider": META_INTEGRATION_PROVIDER},
            {
                "_id": 0,
                "date": 1,
                "ad_account_id": 1,
                "display_name": 1,
                "spend_native": 1,
                "spend_sar": 1,
                "currency_native": 1,
                "fx_rate_to_sar": 1,
                "purchases": 1,
                "purchase_value_native": 1,
                "purchase_value_sar": 1,
                "impressions": 1,
                "clicks": 1,
                "empty_provider_row": 1,
                "observed_at": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("ad_account_id", 1)],
        )
        meta_legacy_task = _rows(
            self.db,
            META_LEGACY_PERFORMANCE_COLLECTION,
            date_query,
            {
                "_id": 0,
                "date": 1,
                "account_id": 1,
                "campaign_id": 1,
                "campaign_name": 1,
                "spend": 1,
                "currency": 1,
                "currency_native": 1,
                "fx_rate": 1,
                "purchases": 1,
                "purchase_value": 1,
                "impressions": 1,
                "clicks": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("campaign_id", 1), ("account_id", 1)],
        )
        meta_selected_accounts_task = _rows(
            self.db,
            "mezan_integration_accounts_v2",
            {
                "user_id": user_id,
                "provider": META_INTEGRATION_PROVIDER,
                "connection_provenance": "api_connection",
                "mezan_selected": True,
            },
            {
                "_id": 0,
                "external_account_id": 1,
                "ad_account_id": 1,
                "display_name": 1,
                "mezan_selected": 1,
            },
            limit=MAX_ACCOUNTS,
            sort=[("display_name", 1), ("external_account_id", 1)],
        )
'''
    service = replace_between(
        service,
        meta_task_start,
        meta_task_end,
        meta_tasks,
        "Meta read tasks",
    )

    service = replace_once(
        service,
        '''            tiktok_rows,
            meta_rows,
            accounts,
            legacy_accounts,
            currency_settings,
        ) = await asyncio.gather(
            integration_task,
            booked_expense_task,
            snap_account_task,
            snap_accounts_task,
            snap_stats_task,
            tiktok_task,
            meta_task,
            accounts_task,
            legacy_accounts_task,
            currency_settings_task,
        )
''',
        '''            tiktok_rows,
            meta_v2_rows,
            meta_legacy_rows,
            meta_selected_accounts,
            accounts,
            legacy_accounts,
            currency_settings,
        ) = await asyncio.gather(
            integration_task,
            booked_expense_task,
            snap_account_task,
            snap_accounts_task,
            snap_stats_task,
            tiktok_task,
            meta_v2_task,
            meta_legacy_task,
            meta_selected_accounts_task,
            accounts_task,
            legacy_accounts_task,
            currency_settings_task,
        )
''',
        "Meta gather tuple",
    )

    limits_start = "        source_limit_reached = {\n"
    limits_end = "        source_invalid_date_rows = {\n"
    limits_and_source_selection = '''        snap_account_limit_reached = (
            len(snap_account_rows) > MAX_PERFORMANCE_ROWS
        )
        snap_accounts_limit_reached = len(snap_accounts) > MAX_ACCOUNTS
        snap_stats_limit_reached = len(snap_stats_rows) > MAX_PERFORMANCE_ROWS
        tiktok_limit_reached = len(tiktok_rows) > MAX_PERFORMANCE_ROWS
        meta_v2_limit_reached = len(meta_v2_rows) > MAX_PERFORMANCE_ROWS
        meta_legacy_limit_reached = len(meta_legacy_rows) > MAX_PERFORMANCE_ROWS
        meta_selection_limit_reached = len(meta_selected_accounts) > MAX_ACCOUNTS
        accounts_limit_reached = len(accounts) > MAX_ACCOUNTS
        legacy_accounts_limit_reached = len(legacy_accounts) > MAX_ACCOUNTS

        snap_account_rows = snap_account_rows[:MAX_PERFORMANCE_ROWS]
        snap_accounts = snap_accounts[:MAX_ACCOUNTS]
        snap_stats_rows = snap_stats_rows[:MAX_PERFORMANCE_ROWS]
        tiktok_rows = tiktok_rows[:MAX_PERFORMANCE_ROWS]
        meta_v2_rows = meta_v2_rows[:MAX_PERFORMANCE_ROWS]
        meta_legacy_rows = meta_legacy_rows[:MAX_PERFORMANCE_ROWS]
        meta_selected_accounts = meta_selected_accounts[:MAX_ACCOUNTS]
        accounts = accounts[:MAX_ACCOUNTS]
        legacy_accounts = legacy_accounts[:MAX_ACCOUNTS]

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

        source_limit_reached = {
            "snapchat_account_daily": snap_account_limit_reached,
            "snapchat_ad_accounts": snap_accounts_limit_reached,
            "snapchat_daily_stats": snap_stats_limit_reached,
            "tiktok_ads_daily": tiktok_limit_reached,
            meta_source_key: active_meta_limit_reached,
            "ads_accounts": accounts_limit_reached,
            "counterparties": legacy_accounts_limit_reached,
        }
        dated_sources = {
            "snapchat_account_daily": snap_account_rows,
            "snapchat_daily_stats": snap_stats_rows,
            "tiktok_ads_daily": tiktok_rows,
            meta_source_key: meta_rows,
        }
'''
    service = replace_between(
        service,
        limits_start,
        limits_end,
        limits_and_source_selection,
        "active Meta source selection",
    )

    provider_source_old = '''            provider_source_key = (
                "snapchat_account_daily"
                if provider_key == "snapchat"
                else f"{provider_key}_ads_daily"
            )
'''
    provider_source_new = '''            provider_source_key = (
                "snapchat_account_daily"
                if provider_key == "snapchat"
                else meta_source_key
                if provider_key == "meta"
                else "tiktok_ads_daily"
            )
'''
    provider_source_count = service.count(provider_source_old)
    if provider_source_count != 1:
        raise SystemExit(
            "provider source key: expected one match, "
            f"found {provider_source_count}"
        )
    service = service.replace(provider_source_old, provider_source_new, 1)
    if provider_source_old in service:
        raise SystemExit("legacy provider source expression remains")
    return service


def patch_tests(tests: str) -> str:
    test_anchor = '''@pytest.mark.asyncio
async def test_provider_fact_and_booked_accounting_fact_stay_distinct():
'''
    new_tests = '''@pytest.mark.asyncio
async def test_meta_v2_is_authoritative_and_filters_to_selected_accounts():
    db = FakeDB(
        {
            "mezan_meta_performance_daily_v2": [
                {
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "ad_account_id": "act-selected",
                    "display_name": "Selected Meta",
                    "date": "2026-07-10",
                    "spend_native": 10,
                    "spend_sar": 37.5,
                    "currency_native": "USD",
                    "fx_rate_to_sar": 3.75,
                    "purchases": 2,
                    "purchase_value_native": 20,
                    "purchase_value_sar": 75,
                    "impressions": 1000,
                    "clicks": 50,
                    "updated_at": "2026-07-28T10:00:00+00:00",
                },
                {
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "ad_account_id": "act-not-selected",
                    "display_name": "Old Meta",
                    "date": "2026-07-10",
                    "spend_native": 1000,
                    "spend_sar": 3750,
                    "currency_native": "USD",
                    "fx_rate_to_sar": 3.75,
                    "purchases": 100,
                    "purchase_value_native": 2000,
                    "purchase_value_sar": 7500,
                    "impressions": 100000,
                    "clicks": 5000,
                    "updated_at": "2026-07-28T10:00:00+00:00",
                },
                {
                    "user_id": OTHER_OWNER_ID,
                    "provider": "meta_ads",
                    "ad_account_id": "act-other-owner",
                    "date": "2026-07-10",
                    "spend_native": 999,
                    "currency_native": "SAR",
                    "fx_rate_to_sar": 1,
                    "purchases": 99,
                    "purchase_value_native": 999,
                    "impressions": 9999,
                    "clicks": 999,
                },
            ],
            "mezan_integration_accounts_v2": [
                {
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "connection_provenance": "api_connection",
                    "external_account_id": "act-selected",
                    "display_name": "Selected Meta",
                    "mezan_selected": True,
                },
                {
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "connection_provenance": "api_connection",
                    "external_account_id": "act-not-selected",
                    "display_name": "Old Meta",
                    "mezan_selected": False,
                },
            ],
            "meta_ads_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "account_id": "act-legacy",
                    "campaign_id": "legacy-campaign",
                    "campaign_name": "Legacy Meta",
                    "spend": 999,
                    "currency": "SAR",
                    "purchase_value": 999,
                    "purchases": 99,
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
        provider="meta",
    )

    provider = result["providers"][0]
    assert provider["metrics"]["provider_reported_spend_sar"] == 37.5
    assert provider["metrics"]["platform_attributed_revenue_sar"] == 75
    assert provider["metrics"]["platform_reported_purchases"] == 2
    assert provider["campaign_coverage"]["status"] == "aggregate_only"
    assert result["campaign_pagination"]["total"] == 1
    assert result["campaigns"][0]["campaign_id"] == "_default"
    assert result["campaigns"][0]["data_source"] == (
        "mezan_meta_performance_daily_v2"
    )
    serialized = json.dumps(result, ensure_ascii=False)
    assert "legacy-campaign" not in serialized
    assert "act-not-selected" not in serialized
    assert OTHER_OWNER_ID not in serialized
    assert db.write_attempts == []


@pytest.mark.asyncio
async def test_meta_v2_selection_without_rows_does_not_fall_back_to_legacy():
    db = FakeDB(
        {
            "mezan_integration_accounts_v2": [
                {
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "connection_provenance": "api_connection",
                    "external_account_id": "act-selected",
                    "display_name": "Selected Meta",
                    "mezan_selected": True,
                }
            ],
            "meta_ads_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "account_id": "act-legacy",
                    "campaign_id": "legacy-campaign",
                    "campaign_name": "Legacy Meta",
                    "spend": 50,
                    "currency": "SAR",
                    "purchase_value": 100,
                    "purchases": 2,
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
        provider="meta",
    )

    assert result["metrics"]["provider_reported_spend_sar"] is None
    assert result["campaigns"] == []
    assert result["providers"][0]["campaign_coverage"]["status"] == (
        "unavailable"
    )
    assert db.write_attempts == []


'''
    return replace_once(tests, test_anchor, new_tests + test_anchor, "test insertion")


def main() -> None:
    service = SERVICE_PATH.read_text(encoding="utf-8")
    tests = TEST_PATH.read_text(encoding="utf-8")
    SERVICE_PATH.write_text(patch_service(service), encoding="utf-8")
    TEST_PATH.write_text(patch_tests(tests), encoding="utf-8")
    print("META_V2_ADS_MANAGER_PATCH_APPLIED")


if __name__ == "__main__":
    main()
