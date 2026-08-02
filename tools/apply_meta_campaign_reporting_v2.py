from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return source.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Meta native reporting: collect campaign catalogue and campaign-day insights.
# ---------------------------------------------------------------------------
meta_path = Path("backend/integrations_control_center/meta_native_reporting.py")
meta = meta_path.read_text(encoding="utf-8")
meta = replace_once(
    meta,
    "from .meta_oauth_security import (\n",
    "from .meta_campaign_reporting import (\n"
    "    MetaCampaignReportingError,\n"
    "    ensure_meta_campaign_reporting_indexes,\n"
    "    fetch_meta_campaign_catalog,\n"
    "    sync_meta_campaign_day,\n"
    ")\n"
    "from .meta_oauth_security import (\n",
    "meta campaign imports",
)
meta = replace_once(
    meta,
    "    await ensure_meta_reporting_indexes(db)\n",
    "    await ensure_meta_reporting_indexes(db)\n"
    "    await ensure_meta_campaign_reporting_indexes(db)\n",
    "meta campaign indexes",
)
meta = replace_once(
    meta,
    "        for account in accounts:\n"
    "            saved = 0\n"
    "            account_errors: list[dict[str, Any]] = []\n"
    "            for day in days:\n",
    "        for account in accounts:\n"
    "            saved = 0\n"
    "            campaign_saved = 0\n"
    "            account_errors: list[dict[str, Any]] = []\n"
    "            campaign_catalog: dict[str, dict[str, Any]] = {}\n"
    "            try:\n"
    "                catalog_result = await fetch_meta_campaign_catalog(\n"
    "                    client, access_token, account\n"
    "                )\n"
    "                provider_calls += int(catalog_result.get(\"provider_calls\") or 0)\n"
    "                campaign_catalog = catalog_result.get(\"campaigns\") or {}\n"
    "            except MetaCampaignReportingError as exc:\n"
    "                provider_calls += int(exc.provider_calls or 0)\n"
    "                if exc.code == \"meta_needs_reauth\":\n"
    "                    raise MetaReportingError(\n"
    "                        exc.code, exc.message, status_code=exc.status_code\n"
    "                    ) from exc\n"
    "                item = {\n"
    "                    \"ad_account_id\": account[\"ad_account_id\"],\n"
    "                    \"kind\": \"campaign_catalog\",\n"
    "                    \"code\": exc.code,\n"
    "                }\n"
    "                account_errors.append(item)\n"
    "                error_items.append(item)\n"
    "            for day in days:\n",
    "meta campaign account setup",
)
meta = replace_once(
    meta,
    "                    saved += 1\n"
    "                except MetaReportingError as exc:\n",
    "                    saved += 1\n"
    "                    try:\n"
    "                        campaign_result = await sync_meta_campaign_day(\n"
    "                            db,\n"
    "                            user_id,\n"
    "                            client,\n"
    "                            access_token,\n"
    "                            account,\n"
    "                            day,\n"
    "                            campaign_catalog=campaign_catalog,\n"
    "                            observed_at=observed_at,\n"
    "                        )\n"
    "                        provider_calls += int(\n"
    "                            campaign_result.get(\"provider_calls\") or 0\n"
    "                        )\n"
    "                        campaign_saved += int(\n"
    "                            campaign_result.get(\"rows_saved\") or 0\n"
    "                        )\n"
    "                    except MetaCampaignReportingError as exc:\n"
    "                        provider_calls += int(exc.provider_calls or 0)\n"
    "                        if exc.code == \"meta_needs_reauth\":\n"
    "                            raise MetaReportingError(\n"
    "                                exc.code,\n"
    "                                exc.message,\n"
    "                                status_code=exc.status_code,\n"
    "                            ) from exc\n"
    "                        item = {\n"
    "                            \"ad_account_id\": account[\"ad_account_id\"],\n"
    "                            \"date\": day.isoformat(),\n"
    "                            \"kind\": \"campaign_insights\",\n"
    "                            \"code\": exc.code,\n"
    "                        }\n"
    "                        account_errors.append(item)\n"
    "                        error_items.append(item)\n"
    "                except MetaReportingError as exc:\n",
    "meta campaign day sync",
)
meta = replace_once(
    meta,
    "                    \"rows_saved\": saved,\n"
    "                    \"errors\": len(account_errors),\n",
    "                    \"rows_saved\": saved,\n"
    "                    \"campaign_rows_saved\": campaign_saved,\n"
    "                    \"errors\": len(account_errors),\n",
    "meta account campaign summary",
)
meta = replace_once(
    meta,
    "    rows_saved = sum(item[\"rows_saved\"] for item in account_summaries)\n",
    "    rows_saved = sum(item[\"rows_saved\"] for item in account_summaries)\n"
    "    campaign_rows_saved = sum(\n"
    "        item.get(\"campaign_rows_saved\", 0) for item in account_summaries\n"
    "    )\n",
    "meta campaign total",
)
meta = replace_once(
    meta,
    "        \"rows_saved\": rows_saved,\n"
    "        \"errors_count\": len(error_items),\n",
    "        \"rows_saved\": rows_saved,\n"
    "        \"campaign_rows_saved\": campaign_rows_saved,\n"
    "        \"errors_count\": len(error_items),\n",
    "meta campaign return",
)
meta_path.write_text(meta, encoding="utf-8")


# ---------------------------------------------------------------------------
# Ads Manager response model: surface provider campaign metadata safely.
# ---------------------------------------------------------------------------
models_path = Path("backend/ads_manager/models.py")
models = models_path.read_text(encoding="utf-8")
models = replace_once(
    models,
    "class CampaignRow(StrictResponseModel):\n",
    "class CampaignBudget(StrictResponseModel):\n"
    "    currency: str | None = None\n"
    "    daily_native: float | None = None\n"
    "    lifetime_native: float | None = None\n\n\n"
    "class CampaignRow(StrictResponseModel):\n",
    "campaign budget model",
)
models = replace_once(
    models,
    "    campaign_name: str\n"
    "    spend_reported: float | None = None\n",
    "    campaign_name: str\n"
    "    status: str | None = None\n"
    "    delivery_status: str | None = None\n"
    "    objective: str | None = None\n"
    "    start_time: str | None = None\n"
    "    end_time: str | None = None\n"
    "    budget: CampaignBudget = Field(default_factory=CampaignBudget)\n"
    "    spend_reported: float | None = None\n",
    "campaign metadata fields",
)
models_path.write_text(models, encoding="utf-8")


# ---------------------------------------------------------------------------
# Ads Manager service: account totals stay in account facts; campaign table
# reads the dedicated campaign collection for selected Meta accounts only.
# ---------------------------------------------------------------------------
service_path = Path("backend/ads_manager/service.py")
service = service_path.read_text(encoding="utf-8")
service = replace_once(
    service,
    'META_V2_PERFORMANCE_COLLECTION = "mezan_meta_performance_daily_v2"\n',
    'META_V2_PERFORMANCE_COLLECTION = "mezan_meta_performance_daily_v2"\n'
    'META_CAMPAIGN_V2_PERFORMANCE_COLLECTION = (\n'
    '    "mezan_meta_campaign_performance_daily_v2"\n'
    ')\n',
    "meta campaign collection constant",
)
service = replace_once(
    service,
    "    {\n"
    "        \"key\": META_LEGACY_PERFORMANCE_COLLECTION,\n",
    "    {\n"
    "        \"key\": META_CAMPAIGN_V2_PERFORMANCE_COLLECTION,\n"
    "        \"role\": \"تفاصيل حملات Meta الأصلية المحفوظة عبر Integrations V2\",\n"
    "        \"grain\": \"حساب محدد × حملة × يوم\",\n"
    "        \"authoritative_for\": [\n"
    "            \"meta_campaign_identity\",\n"
    "            \"meta_campaign_status\",\n"
    "            \"meta_campaign_objective\",\n"
    "            \"meta_campaign_performance\",\n"
    "        ],\n"
    "    },\n"
    "    {\n"
    "        \"key\": META_LEGACY_PERFORMANCE_COLLECTION,\n",
    "meta campaign source definition",
)
service = replace_once(
    service,
    "    return output\n\n\ndef _snapchat_metric(row: dict, key: str) -> Any:\n",
    "    return output\n\n\n"
    "def _normalize_meta_campaign_v2_rows(rows: list[dict]) -> list[dict]:\n"
    "    output: list[dict] = []\n"
    "    for row in rows:\n"
    "        account_id = _clean_text(row.get(\"ad_account_id\"), limit=120)\n"
    "        campaign_id = _clean_text(row.get(\"campaign_id\"), limit=160)\n"
    "        if not campaign_id:\n"
    "            continue\n"
    "        output.append(\n"
    "            {\n"
    "                \"date\": row.get(\"date\"),\n"
    "                \"account_id\": account_id or None,\n"
    "                \"campaign_id\": campaign_id,\n"
    "                \"campaign_name\": (\n"
    "                    _clean_text(row.get(\"campaign_name\"), limit=180)\n"
    "                    or campaign_id\n"
    "                ),\n"
    "                \"status\": row.get(\"status\"),\n"
    "                \"delivery_status\": row.get(\"effective_status\"),\n"
    "                \"objective\": row.get(\"objective\"),\n"
    "                \"start_time\": row.get(\"start_time\"),\n"
    "                \"end_time\": row.get(\"stop_time\"),\n"
    "                \"daily_budget_native\": row.get(\"daily_budget_native\"),\n"
    "                \"lifetime_budget_native\": row.get(\"lifetime_budget_native\"),\n"
    "                \"spend\": row.get(\"spend_native\"),\n"
    "                \"currency_native\": row.get(\"currency_native\"),\n"
    "                \"fx_rate\": row.get(\"fx_rate_to_sar\"),\n"
    "                \"purchases\": row.get(\"purchases\"),\n"
    "                \"purchase_value\": row.get(\"purchase_value_native\"),\n"
    "                \"impressions\": row.get(\"impressions\"),\n"
    "                \"clicks\": row.get(\"clicks\"),\n"
    "                \"updated_at\": row.get(\"updated_at\") or row.get(\"observed_at\"),\n"
    "                \"_data_source\": META_CAMPAIGN_V2_PERFORMANCE_COLLECTION,\n"
    "            }\n"
    "        )\n"
    "    return output\n\n\n"
    "def _snapchat_metric(row: dict, key: str) -> Any:\n",
    "meta campaign normalization",
)
service = replace_once(
    service,
    "                \"campaign_name\": (\n"
    "                    campaign_name\n"
    "                    or (\"إجمالي غير مفصل\" if campaign_id == \"_default\" else campaign_id)\n"
    "                ),\n"
    "                \"spend_reported\": 0.0,\n",
    "                \"campaign_name\": (\n"
    "                    campaign_name\n"
    "                    or (\"إجمالي غير مفصل\" if campaign_id == \"_default\" else campaign_id)\n"
    "                ),\n"
    "                \"status\": _clean_text(row.get(\"status\"), limit=40) or None,\n"
    "                \"delivery_status\": (\n"
    "                    _clean_text(\n"
    "                        row.get(\"delivery_status\")\n"
    "                        or row.get(\"effective_status\"),\n"
    "                        limit=40,\n"
    "                    )\n"
    "                    or None\n"
    "                ),\n"
    "                \"objective\": _clean_text(row.get(\"objective\"), limit=80) or None,\n"
    "                \"start_time\": _clean_text(row.get(\"start_time\"), limit=80) or None,\n"
    "                \"end_time\": (\n"
    "                    _clean_text(row.get(\"end_time\") or row.get(\"stop_time\"), limit=80)\n"
    "                    or None\n"
    "                ),\n"
    "                \"_daily_budget_native\": _optional_nonnegative_number(\n"
    "                    row.get(\"daily_budget_native\")\n"
    "                ),\n"
    "                \"_lifetime_budget_native\": _optional_nonnegative_number(\n"
    "                    row.get(\"lifetime_budget_native\")\n"
    "                ),\n"
    "                \"_metadata_date\": observed_date,\n"
    "                \"spend_reported\": 0.0,\n",
    "campaign grouped metadata",
)
service = replace_once(
    service,
    "        if observed_date and (\n"
    "            target[\"last_observed_date\"] is None\n"
    "            or observed_date > target[\"last_observed_date\"]\n"
    "        ):\n"
    "            target[\"last_observed_date\"] = observed_date\n",
    "        if observed_date and (\n"
    "            target[\"last_observed_date\"] is None\n"
    "            or observed_date > target[\"last_observed_date\"]\n"
    "        ):\n"
    "            target[\"last_observed_date\"] = observed_date\n"
    "        if observed_date and (\n"
    "            not target.get(\"_metadata_date\")\n"
    "            or observed_date >= target[\"_metadata_date\"]\n"
    "        ):\n"
    "            target[\"status\"] = (\n"
    "                _clean_text(row.get(\"status\"), limit=40) or target.get(\"status\")\n"
    "            )\n"
    "            target[\"delivery_status\"] = (\n"
    "                _clean_text(\n"
    "                    row.get(\"delivery_status\") or row.get(\"effective_status\"),\n"
    "                    limit=40,\n"
    "                )\n"
    "                or target.get(\"delivery_status\")\n"
    "            )\n"
    "            target[\"objective\"] = (\n"
    "                _clean_text(row.get(\"objective\"), limit=80)\n"
    "                or target.get(\"objective\")\n"
    "            )\n"
    "            target[\"start_time\"] = (\n"
    "                _clean_text(row.get(\"start_time\"), limit=80)\n"
    "                or target.get(\"start_time\")\n"
    "            )\n"
    "            target[\"end_time\"] = (\n"
    "                _clean_text(row.get(\"end_time\") or row.get(\"stop_time\"), limit=80)\n"
    "                or target.get(\"end_time\")\n"
    "            )\n"
    "            daily_budget = _optional_nonnegative_number(\n"
    "                row.get(\"daily_budget_native\")\n"
    "            )\n"
    "            lifetime_budget = _optional_nonnegative_number(\n"
    "                row.get(\"lifetime_budget_native\")\n"
    "            )\n"
    "            if daily_budget is not None:\n"
    "                target[\"_daily_budget_native\"] = daily_budget\n"
    "            if lifetime_budget is not None:\n"
    "                target[\"_lifetime_budget_native\"] = lifetime_budget\n"
    "            target[\"_metadata_date\"] = observed_date\n",
    "campaign metadata refresh",
)
service = replace_once(
    service,
    "        value.pop(\"_campaign_name_date\", None)\n"
    "        currency = value[\"spend_currency\"]\n",
    "        value.pop(\"_campaign_name_date\", None)\n"
    "        value.pop(\"_metadata_date\", None)\n"
    "        daily_budget_native = value.pop(\"_daily_budget_native\", None)\n"
    "        lifetime_budget_native = value.pop(\"_lifetime_budget_native\", None)\n"
    "        currency = value[\"spend_currency\"]\n"
    "        value[\"budget\"] = {\n"
    "            \"currency\": currency,\n"
    "            \"daily_native\": daily_budget_native,\n"
    "            \"lifetime_native\": lifetime_budget_native,\n"
    "        }\n",
    "campaign output budget",
)
service = replace_once(
    service,
    "        meta_legacy_task = _rows(\n",
    "        meta_campaign_v2_task = _rows(\n"
    "            self.db,\n"
    "            META_CAMPAIGN_V2_PERFORMANCE_COLLECTION,\n"
    "            {**date_query, \"provider\": META_INTEGRATION_PROVIDER},\n"
    "            {\n"
    "                \"_id\": 0,\n"
    "                \"date\": 1,\n"
    "                \"ad_account_id\": 1,\n"
    "                \"campaign_id\": 1,\n"
    "                \"campaign_name\": 1,\n"
    "                \"objective\": 1,\n"
    "                \"status\": 1,\n"
    "                \"effective_status\": 1,\n"
    "                \"start_time\": 1,\n"
    "                \"stop_time\": 1,\n"
    "                \"daily_budget_native\": 1,\n"
    "                \"lifetime_budget_native\": 1,\n"
    "                \"currency_native\": 1,\n"
    "                \"spend_native\": 1,\n"
    "                \"fx_rate_to_sar\": 1,\n"
    "                \"purchases\": 1,\n"
    "                \"purchase_value_native\": 1,\n"
    "                \"impressions\": 1,\n"
    "                \"clicks\": 1,\n"
    "                \"observed_at\": 1,\n"
    "                \"updated_at\": 1,\n"
    "            },\n"
    "            limit=MAX_PERFORMANCE_ROWS,\n"
    "            sort=[(\"date\", 1), (\"campaign_id\", 1), (\"ad_account_id\", 1)],\n"
    "        )\n"
    "        meta_legacy_task = _rows(\n",
    "meta campaign query task",
)
service = replace_once(
    service,
    "            meta_v2_rows,\n"
    "            meta_legacy_rows,\n",
    "            meta_v2_rows,\n"
    "            meta_campaign_v2_rows,\n"
    "            meta_legacy_rows,\n",
    "meta campaign gather target",
)
service = replace_once(
    service,
    "            meta_v2_task,\n"
    "            meta_legacy_task,\n",
    "            meta_v2_task,\n"
    "            meta_campaign_v2_task,\n"
    "            meta_legacy_task,\n",
    "meta campaign gather task",
)
service = replace_once(
    service,
    "        meta_v2_limit_reached = len(meta_v2_rows) > MAX_PERFORMANCE_ROWS\n",
    "        meta_v2_limit_reached = len(meta_v2_rows) > MAX_PERFORMANCE_ROWS\n"
    "        meta_campaign_v2_limit_reached = (\n"
    "            len(meta_campaign_v2_rows) > MAX_PERFORMANCE_ROWS\n"
    "        )\n",
    "meta campaign limit",
)
service = replace_once(
    service,
    "        meta_v2_rows = meta_v2_rows[:MAX_PERFORMANCE_ROWS]\n"
    "        meta_legacy_rows = meta_legacy_rows[:MAX_PERFORMANCE_ROWS]\n",
    "        meta_v2_rows = meta_v2_rows[:MAX_PERFORMANCE_ROWS]\n"
    "        meta_campaign_v2_rows = meta_campaign_v2_rows[:MAX_PERFORMANCE_ROWS]\n"
    "        meta_legacy_rows = meta_legacy_rows[:MAX_PERFORMANCE_ROWS]\n",
    "meta campaign slice",
)
service = replace_once(
    service,
    "            meta_rows = _normalize_meta_v2_rows(meta_v2_rows)\n"
    "            meta_source_key = META_V2_PERFORMANCE_COLLECTION\n"
    "            active_meta_limit_reached = (\n"
    "                meta_v2_limit_reached or meta_selection_limit_reached\n"
    "            )\n",
    "            meta_campaign_v2_rows = [\n"
    "                row\n"
    "                for row in meta_campaign_v2_rows\n"
    "                if _clean_text(row.get(\"ad_account_id\"), limit=120)\n"
    "                .removeprefix(\"act_\")\n"
    "                in selected_meta_ids\n"
    "            ]\n"
    "            meta_rows = _normalize_meta_v2_rows(meta_v2_rows)\n"
    "            normalized_meta_campaign_rows = _normalize_meta_campaign_v2_rows(\n"
    "                meta_campaign_v2_rows\n"
    "            )\n"
    "            meta_campaign_source_rows = (\n"
    "                normalized_meta_campaign_rows or list(meta_rows)\n"
    "            )\n"
    "            meta_source_key = META_V2_PERFORMANCE_COLLECTION\n"
    "            meta_campaign_source_key = META_CAMPAIGN_V2_PERFORMANCE_COLLECTION\n"
    "            active_meta_limit_reached = (\n"
    "                meta_v2_limit_reached or meta_selection_limit_reached\n"
    "            )\n"
    "            active_meta_campaign_limit_reached = (\n"
    "                meta_campaign_v2_limit_reached or meta_selection_limit_reached\n"
    "            )\n",
    "meta campaign authority",
)
service = replace_once(
    service,
    "            meta_rows = meta_legacy_rows\n"
    "            meta_source_key = META_LEGACY_PERFORMANCE_COLLECTION\n"
    "            active_meta_limit_reached = meta_legacy_limit_reached\n",
    "            meta_rows = meta_legacy_rows\n"
    "            meta_campaign_source_rows = meta_legacy_rows\n"
    "            meta_source_key = META_LEGACY_PERFORMANCE_COLLECTION\n"
    "            meta_campaign_source_key = META_LEGACY_PERFORMANCE_COLLECTION\n"
    "            active_meta_limit_reached = meta_legacy_limit_reached\n"
    "            active_meta_campaign_limit_reached = meta_legacy_limit_reached\n",
    "meta campaign legacy fallback",
)
service = replace_once(
    service,
    "            meta_source_key: active_meta_limit_reached,\n",
    "            meta_source_key: active_meta_limit_reached,\n"
    "            meta_campaign_source_key: active_meta_campaign_limit_reached,\n",
    "meta campaign source limit",
)
service = replace_once(
    service,
    "            meta_source_key: meta_rows,\n",
    "            meta_source_key: meta_rows,\n"
    "            meta_campaign_source_key: meta_campaign_source_rows,\n",
    "meta campaign dated source",
)
service = replace_once(
    service,
    "        meta_rows = [\n"
    "            row\n"
    "            for row in meta_rows\n"
    "            if _valid_source_date(row.get(\"date\"), from_iso, to_iso)\n"
    "        ]\n",
    "        meta_rows = [\n"
    "            row\n"
    "            for row in meta_rows\n"
    "            if _valid_source_date(row.get(\"date\"), from_iso, to_iso)\n"
    "        ]\n"
    "        meta_campaign_source_rows = [\n"
    "            row\n"
    "            for row in meta_campaign_source_rows\n"
    "            if _valid_source_date(row.get(\"date\"), from_iso, to_iso)\n"
    "        ]\n",
    "meta campaign date filter",
)
service = replace_once(
    service,
    "            \"meta\": _campaign_rows(\n"
    "                \"meta\",\n"
    "                meta_rows,\n",
    "            \"meta\": _campaign_rows(\n"
    "                \"meta\",\n"
    "                meta_campaign_source_rows,\n",
    "meta campaign grouping source",
)
service = replace_once(
    service,
    "                campaign_source_rows = raw_rows[provider_key]\n",
    "                campaign_source_rows = (\n"
    "                    meta_campaign_source_rows\n"
    "                    if provider_key == \"meta\"\n"
    "                    else raw_rows[provider_key]\n"
    "                )\n",
    "meta campaign coverage source",
)
service_path.write_text(service, encoding="utf-8")


# ---------------------------------------------------------------------------
# Frontend adapter: keep the existing campaign card contract.
# ---------------------------------------------------------------------------
frontend_path = Path("frontend/src/services/marketingPerformance.js")
frontend = frontend_path.read_text(encoding="utf-8")
frontend = replace_once(
    frontend,
    "function adaptAdsManager(platform, overview) {\n",
    "export function adaptAdsManager(platform, overview) {\n",
    "export ads manager adapter",
)
frontend = replace_once(
    frontend,
    "                status: \"unknown\",\n"
    "            }))\n",
    "                status: row.status || \"unknown\",\n"
    "                delivery_status: row.delivery_status || null,\n"
    "                objective: row.objective || null,\n"
    "                start_time: row.start_time || null,\n"
    "                end_time: row.end_time || null,\n"
    "                budget: row.budget || {\n"
    "                    currency: row.spend_currency || null,\n"
    "                    daily_native: null,\n"
    "                    lifetime_native: null,\n"
    "                },\n"
    "            }))\n",
    "frontend campaign metadata adapter",
)
frontend_path.write_text(frontend, encoding="utf-8")


# ---------------------------------------------------------------------------
# Backend Meta tests: route fake provider responses by requested level.
# ---------------------------------------------------------------------------
meta_test_path = Path("backend/tests/test_meta_native_reporting.py")
meta_test = meta_test_path.read_text(encoding="utf-8")
meta_test = replace_once(
    meta_test,
    "from integrations_control_center import meta_account_selection as selection\n",
    "from integrations_control_center import meta_account_selection as selection\n"
    "from integrations_control_center import meta_campaign_reporting as campaign_reporting\n",
    "meta campaign test import",
)
meta_test = replace_once(
    meta_test,
    "class FakeHttpClient:\n",
    "class FakeCampaignCatalogResponse:\n"
    "    status_code = 200\n\n"
    "    def json(self):\n"
    "        return {\n"
    "            \"data\": [\n"
    "                {\n"
    "                    \"id\": \"campaign-1\",\n"
    "                    \"name\": \"Meta Sales Campaign\",\n"
    "                    \"objective\": \"OUTCOME_SALES\",\n"
    "                    \"status\": \"ACTIVE\",\n"
    "                    \"effective_status\": \"ACTIVE\",\n"
    "                    \"daily_budget\": \"25000\",\n"
    "                    \"lifetime_budget\": \"100000\",\n"
    "                    \"start_time\": \"2026-07-01T00:00:00+0000\",\n"
    "                    \"stop_time\": \"2026-08-31T23:59:59+0000\",\n"
    "                }\n"
    "            ]\n"
    "        }\n\n\n"
    "class FakeCampaignInsightsResponse:\n"
    "    status_code = 200\n\n"
    "    def json(self):\n"
    "        return {\n"
    "            \"data\": [\n"
    "                {\n"
    "                    \"campaign_id\": \"campaign-1\",\n"
    "                    \"campaign_name\": \"Meta Sales Campaign\",\n"
    "                    \"spend\": \"120.50\",\n"
    "                    \"impressions\": \"15000\",\n"
    "                    \"clicks\": \"510\",\n"
    "                    \"account_currency\": \"USD\",\n"
    "                    \"date_start\": \"2026-07-30\",\n"
    "                    \"date_stop\": \"2026-07-30\",\n"
    "                    \"actions\": [\n"
    "                        {\"action_type\": \"omni_purchase\", \"value\": \"21\"}\n"
    "                    ],\n"
    "                    \"action_values\": [\n"
    "                        {\"action_type\": \"omni_purchase\", \"value\": \"620.40\"}\n"
    "                    ],\n"
    "                }\n"
    "            ]\n"
    "        }\n\n\n"
    "class FakeHttpClient:\n",
    "meta campaign fake responses",
)
meta_test = replace_once(
    meta_test,
    "    async def get(self, url, **kwargs):\n"
    "        type(self).calls.append((url, deepcopy(kwargs)))\n"
    "        return FakeResponse()\n",
    "    async def get(self, url, **kwargs):\n"
    "        type(self).calls.append((url, deepcopy(kwargs)))\n"
    "        if url.endswith(\"/campaigns\"):\n"
    "            return FakeCampaignCatalogResponse()\n"
    "        if (kwargs.get(\"params\") or {}).get(\"level\") == \"campaign\":\n"
    "            return FakeCampaignInsightsResponse()\n"
    "        return FakeResponse()\n",
    "meta campaign fake client routing",
)
meta_test = replace_once(
    meta_test,
    "    assert result[\"rows_saved\"] == 1\n",
    "    assert result[\"rows_saved\"] == 1\n"
    "    assert result[\"campaign_rows_saved\"] == 1\n",
    "meta campaign rows result",
)
meta_test = replace_once(
    meta_test,
    "    assert reporting.META_REPORTING_COLLECTION in written_collections\n\n"
    "    url, kwargs = FakeHttpClient.calls[0]\n"
    "    assert url.endswith(\"/act_111/insights\")\n",
    "    assert reporting.META_REPORTING_COLLECTION in written_collections\n"
    "    assert campaign_reporting.META_CAMPAIGN_REPORTING_COLLECTION in written_collections\n\n"
    "    campaign_rows = db.rows[\n"
    "        campaign_reporting.META_CAMPAIGN_REPORTING_COLLECTION\n"
    "    ]\n"
    "    assert len(campaign_rows) == 1\n"
    "    campaign = campaign_rows[0]\n"
    "    assert campaign[\"campaign_id\"] == \"campaign-1\"\n"
    "    assert campaign[\"campaign_name\"] == \"Meta Sales Campaign\"\n"
    "    assert campaign[\"objective\"] == \"OUTCOME_SALES\"\n"
    "    assert campaign[\"effective_status\"] == \"ACTIVE\"\n"
    "    assert campaign[\"daily_budget_native\"] == 250.0\n"
    "    assert campaign[\"spend_sar\"] == 451.88\n"
    "    assert campaign[\"purchase_value_sar\"] == 2326.50\n"
    "    assert campaign[\"source_only\"] is True\n"
    "    assert campaign[\"accounting_eligible\"] is False\n\n"
    "    url, kwargs = next(\n"
    "        (url, kwargs)\n"
    "        for url, kwargs in FakeHttpClient.calls\n"
    "        if (kwargs.get(\"params\") or {}).get(\"level\") == \"account\"\n"
    "    )\n"
    "    assert url.endswith(\"/act_111/insights\")\n",
    "meta campaign persisted assertions",
)
meta_test_path.write_text(meta_test, encoding="utf-8")


# ---------------------------------------------------------------------------
# Ads Manager contract: dedicated Meta campaign rows become authoritative.
# ---------------------------------------------------------------------------
ads_test_path = Path("backend/tests/test_unified_ads_manager_phase1.py")
ads_test = ads_test_path.read_text(encoding="utf-8")
ads_test += r'''

@pytest.mark.asyncio
async def test_meta_campaign_v2_details_are_used_without_double_counting_account_totals():
    db = FakeDB(
        {
            "mezan_meta_performance_daily_v2": [
                {
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "ad_account_id": "act-selected",
                    "display_name": "Selected Meta",
                    "date": "2026-07-10",
                    "spend_native": 100,
                    "spend_sar": 375,
                    "currency_native": "USD",
                    "fx_rate_to_sar": 3.75,
                    "purchases": 5,
                    "purchase_value_native": 300,
                    "purchase_value_sar": 1125,
                    "impressions": 10000,
                    "clicks": 500,
                    "updated_at": "2026-07-28T10:00:00+00:00",
                }
            ],
            "mezan_meta_campaign_performance_daily_v2": [
                {
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "ad_account_id": "act-selected",
                    "campaign_id": "campaign-sales",
                    "campaign_name": "Sales Riyadh",
                    "objective": "OUTCOME_SALES",
                    "status": "ACTIVE",
                    "effective_status": "ACTIVE",
                    "date": "2026-07-10",
                    "currency_native": "USD",
                    "spend_native": 100,
                    "fx_rate_to_sar": 3.75,
                    "purchases": 5,
                    "purchase_value_native": 300,
                    "impressions": 10000,
                    "clicks": 500,
                    "daily_budget_native": 250,
                    "lifetime_budget_native": 1000,
                    "start_time": "2026-07-01T00:00:00+00:00",
                    "stop_time": "2026-08-31T23:59:59+00:00",
                    "updated_at": "2026-07-28T10:00:00+00:00",
                }
            ],
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
        }
    )

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="meta",
    )

    provider = result["providers"][0]
    assert provider["metrics"]["provider_reported_spend_sar"] == 375
    assert provider["metrics"]["platform_attributed_revenue_sar"] == 1125
    assert provider["campaign_coverage"]["status"] == "available"
    assert result["campaign_pagination"]["total"] == 1
    campaign = result["campaigns"][0]
    assert campaign["campaign_id"] == "campaign-sales"
    assert campaign["campaign_name"] == "Sales Riyadh"
    assert campaign["status"] == "ACTIVE"
    assert campaign["delivery_status"] == "ACTIVE"
    assert campaign["objective"] == "OUTCOME_SALES"
    assert campaign["budget"] == {
        "currency": "USD",
        "daily_native": 250,
        "lifetime_native": 1000,
    }
    assert campaign["spend_sar_equivalent"] == 375
    assert campaign["revenue_sar_equivalent"] == 1125
    assert campaign["data_source"] == (
        "mezan_meta_campaign_performance_daily_v2"
    )
    assert db.write_attempts == []
'''
ads_test_path.write_text(ads_test, encoding="utf-8")


# ---------------------------------------------------------------------------
# Frontend adapter contract for Meta campaign metadata.
# ---------------------------------------------------------------------------
frontend_test_path = Path("frontend/src/services/marketingPerformance.test.js")
frontend_test = frontend_test_path.read_text(encoding="utf-8")
frontend_test = replace_once(
    frontend_test,
    "    isMarketingPerformanceProvider,\n",
    "    adaptAdsManager,\n"
    "    isMarketingPerformanceProvider,\n",
    "frontend adapter test import",
)
frontend_test += r'''

test("Meta Ads Manager adapter preserves campaign status objective and budget", () => {
    const result = adaptAdsManager("meta", {
        range: { date_from: "2026-08-01", date_to: "2026-08-01", timezone: "Asia/Riyadh" },
        providers: [
            {
                provider: "meta",
                connection_status: "connected",
                connection_provenance: "api_connection",
                last_sync_at: "2026-08-01T17:00:00+00:00",
                health_status: "healthy",
                health_score: 100,
                freshness: { data_delay_minutes: 5, observed_days: 1 },
                performance_coverage: { status: "complete", eligible_for_ratios: true },
                campaign_coverage: { status: "available", source_rows: 1 },
                metrics: {
                    provider_reported_spend_sar: 375,
                    platform_attributed_revenue_sar: 1125,
                    platform_reported_purchases: 5,
                    platform_reported_impressions: 10000,
                    platform_reported_clicks: 500,
                    platform_roas: 3,
                },
            },
        ],
        daily_spend: [{ date: "2026-08-01", meta: 375 }],
        campaigns: [
            {
                provider: "meta",
                account_id: "act-selected",
                campaign_id: "campaign-sales",
                campaign_name: "Sales Riyadh",
                status: "ACTIVE",
                delivery_status: "ACTIVE",
                objective: "OUTCOME_SALES",
                start_time: "2026-07-01T00:00:00+00:00",
                end_time: "2026-08-31T23:59:59+00:00",
                budget: { currency: "USD", daily_native: 250, lifetime_native: 1000 },
                spend_sar_equivalent: 375,
                revenue_sar_equivalent: 1125,
                purchases: 5,
                impressions: 10000,
                clicks: 500,
            },
        ],
        campaign_pagination: { page: 1, limit: 25, total: 1, pages: 1 },
        insights: [],
        coverage: { source_row_limit_reached: [] },
    });

    expect(result.campaigns[0]).toMatchObject({
        campaign_id: "campaign-sales",
        campaign_name: "Sales Riyadh",
        status: "ACTIVE",
        delivery_status: "ACTIVE",
        objective: "OUTCOME_SALES",
        budget: { currency: "USD", daily_native: 250, lifetime_native: 1000 },
        spend_sar: 375,
        sales_sar: 1125,
        orders: 5,
    });
});
'''
frontend_test_path.write_text(frontend_test, encoding="utf-8")

print("META_CAMPAIGN_REPORTING_V2_PATCH_APPLIED")
