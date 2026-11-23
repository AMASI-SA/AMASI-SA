"""Bounded, read-only freshness evidence for automatic operational sources.

This observes stored metadata. It never loads financial amounts, calls a
provider, initializes indexes, starts a worker or repairs a source. A fresh
aggregate is not evidence that an advertising connector succeeded. The caller
overlays persisted calculation/source issues on the two calendar-derived rows.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


SOURCE_TIMEOUT_SECONDS = 2.0
QUERY_TIMEOUT_MS = 1200
MAX_BINDINGS = 50
MAX_ACCOUNTS = 50
# Diagnostic completion allowance, not a change to the worker's cadence:
# ads_auto_sync_scheduler uses a 25-minute active-job bound and a 15-second
# dispatch heartbeat. Its next cycle is due after the *whole* cycle finishes;
# a per-account timestamp is therefore not a strict next-completion deadline.
ADS_COMPLETION_ALLOWANCE_SECONDS = 25 * 60 + 15
RIYADH = ZoneInfo("Asia/Riyadh")
ADS = {
    "snapchat": ("snapchat_ads", "analytics_refresh", "snapchat_account_hourly_campaign_breakdown_riyadh_refresh_v3"),
    "meta": ("meta_ads", "meta_reporting_async", "meta_marketing_reporting_v2"),
    "tiktok": ("tiktok_ads", "tiktok_reporting_async", "tiktok_marketing_reporting_v2"),
    "google_ads": ("google_ads", "google_ads_reporting_async", "google_ads_reporting_v1"),
}
LABELS = {
    "payroll": "الرواتب", "recurring": "المصروفات الدورية",
    "salla": "طلبات سلة ومصادر الشحن ومنصات الدفع",
    "snapchat": "إعلانات Snapchat", "meta": "إعلانات Meta",
    "tiktok": "إعلانات TikTok", "google_ads": "إعلانات Google",
}
MESSAGES = {
    "calculation_current": "وقت الاحتساب المحفوظ حديث؛ اكتمال إعدادات المصدر يُراجع بصورة مستقلة.",
    "calculation_stale": "آخر احتساب محفوظ قديم أو يعود ليوم سابق؛ لم يُثبت احتساب الاستحقاقات الحالية.",
    "metadata_unavailable": "تعذر التحقق من بيانات حالة المصدر؛ لا توجد دلالة مؤكدة على سلامته.",
    "metadata_invalid": "بيانات حالة المصدر ناقصة أو غير متسقة؛ يلزم التحقق منها.",
    "metadata_limit": "تجاوزت بيانات الحالة حد القراءة المحدود؛ لا يمكن تأكيد اكتمالها.",
    "source_not_configured": "لا يوجد مصدر تلقائي معتمد ومفعّل لهذا النوع.",
    "ad_account_unavailable": "أحد الحسابات المرتبطة بالأرصدة غير متصل أو غير مشمول بالتحديث المعتمد.",
    "ad_account_partial": "لم يكتمل آخر تحديث لأحد الحسابات المرتبطة؛ بقي وقت النجاح السابق محفوظًا.",
    "ad_source_failed": "سُجّل تعذر في تحديث المصدر بعد آخر نجاح مؤكد؛ لم يُثبت التعافي بعد.",
    "ad_source_authentication": "تعذر اعتماد اتصال المصدر أو انتهت صلاحيته؛ يلزم مراجعة الربط قبل تأكيد التعافي.",
    "ad_source_rate_limited": "رفض المصدر التحديث بسبب حد الطلبات؛ لم يُثبت نجاح تحديث لاحق بعد.",
    "ad_source_running": "توجد محاولة تحديث جارية؛ لم يُثبت نجاحها بعد.",
    "ad_source_stalled": "تجاوزت محاولة تحديث المصدر مهلة التشغيل المعتمدة دون اكتمال.",
    "ad_source_stale": "تجاوز آخر نجاح مؤكد للمصدر نافذة التحديث ومهلة اكتمال المحاولة.",
    "ad_source_current": "آخر نجاح موثق ضمن نافذة التحديث ومهلة اكتمال المحاولة؛ هذا لا يثبت إقفال يوم مالي.",
    "automatic_sync_disabled": "مصدر الأرصدة مرتبط، لكن بيانات الجدولة تسجل أن التحديث التلقائي غير مفعّل.",
    "salla_schedule_unverified": "لم تُثبت بيانات الحالة جدول تحديث سلة المعتمد لهذا الربط.",
    "salla_source_failed": "لم يكتمل تحديث طلبات سلة؛ لا يثبت وقت احتساب الأرصدة تعافي هذا المصدر.",
    "salla_source_running": "تحديث طلبات سلة جارٍ ضمن مهلة المحاولة؛ لم يُثبت نجاحه بعد.",
    "salla_source_stalled": "تجاوز تحديث طلبات سلة مهلة المحاولة دون اكتمال موثق.",
    "salla_source_stale": "لم يكتمل تحديث سلة للفترة اليومية المستحقة ضمن نافذة التشغيل.",
    "salla_source_current": "اكتمل تحديث الطلبات للفترة اليومية المقررة؛ لا يشترط وجود طلب جديد.",
}
AUTHENTICATION_ERRORS = frozenset({
    "meta_needs_reauth", "meta_oauth_credential_missing", "meta_oauth_not_configured",
    "tiktok_needs_reauth", "tiktok_oauth_credential_missing", "tiktok_oauth_not_configured",
    "google_ads_needs_reauth", "google_ads_oauth_credential_missing", "google_ads_oauth_not_configured",
})
RATE_LIMIT_ERRORS = frozenset({"meta_rate_limited", "tiktok_rate_limited", "google_ads_rate_limited"})


def _projection(*fields):
    return {"_id": 0, **dict.fromkeys(fields, 1)}


BINDING_FIELDS = _projection("user_id", "platform", "integration_account_id", "platform_account_id",
    "confirmed_by", "confirmed_at", "funding_mode", "status", "active", "is_active",
    "archived", "deleted", "is_archived", "is_deleted")
ACCOUNT_FIELDS = _projection("user_id", "provider", "mezan_integration_account_id", "external_account_id",
    "ad_account_id", "connection_status", "connection_provenance", "mezan_selected", "last_sync_at",
    "last_observed_at", "source_mode", "financial_last_sync_at", "financial_last_observed_at",
    "financial_data_quality", "financial_coverage.status", "financial_coverage.data_state",
    "financial_coverage.expected_requests", "financial_coverage.completed_requests")
INTEGRATION_FIELDS = _projection("user_id", "provider", "connection_status", "account_selection_configured",
    "selected_account_ids", "financial_last_sync_at", "financial_data_quality", "financial_coverage.status",
    "financial_coverage.data_state", "financial_coverage.expected_requests", "financial_coverage.completed_requests")
RUN_FIELDS = _projection("user_id", "provider", "run_type", "status", "started_at", "finished_at",
    "summary.accounts_attempted", "summary.accounts_complete", "summary.errors_count",
    "summary.financial_proof.version", "summary.financial_proof.status",
    "summary.financial_proof.accounts_complete", "summary.financial_proof.errors_count",
    "summary.financial_proof.coverage.status", "summary.financial_proof.coverage.data_state",
    "summary.financial_proof.coverage.expected_requests", "summary.financial_proof.coverage.completed_requests")
SALLA_FIELDS = _projection("schedule_version", "status", "started_at", "last_success_at", "completed_slot",
    "last_result.errors_count", "last_result.truncated", "last_result.incomplete_count",
    "last_result.coverage_complete")


def _instant(value):
    if isinstance(value, datetime):
        # Mongo's default BSON decoder returns naive UTC datetimes.
        parsed = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    elif type(value) is str and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
    else:
        return None
    return parsed.astimezone(timezone.utc)


def _known_time(value, now):
    stamp = _instant(value)
    return stamp if stamp is not None and stamp <= now else None


def _row(key, state, code, *, last=None, through=None, expected=None):
    return {"key": key, "label": LABELS[key], "state": state,
            "last_success_at": last.isoformat() if isinstance(last, datetime) else None,
            "data_through": through, "expected_through": expected,
            "message": MESSAGES[code], "code": code}


async def _rows(db, collection, query, projection, *, limit, sort=None):
    cursor = db[collection].find(query, projection).max_time_ms(QUERY_TIMEOUT_MS)
    if sort:
        cursor = cursor.sort(sort)
    result = await cursor.limit(limit).to_list(length=limit)
    if type(result) is not list or any(type(row) is not dict for row in result):
        raise ValueError("invalid_metadata_rows")
    return result


async def _safe(key, operation):
    try:
        async with asyncio.timeout(SOURCE_TIMEOUT_SECONDS):
            return await operation()
    except Exception:
        # Cancellation of the caller remains a BaseException and propagates.
        # Never expose provider/database exception messages or treat failure as empty.
        return _row(key, "unknown", "metadata_unavailable")


def _calculation(key, as_of, now):
    stamp = _known_time(as_of, now)
    if stamp is None:
        return _row(key, "unknown", "metadata_invalid")
    day, today = stamp.astimezone(RIYADH).date(), now.astimezone(RIYADH).date()
    stale = now - stamp >= timedelta(hours=2) or day < today
    return _row(key, "stale" if stale else "current", "calculation_stale" if stale else "calculation_current",
                last=stamp, through=day.isoformat(), expected=today.isoformat())


def _usable(row):
    return (row.get("status") not in {"inactive", "archived", "deleted", "hidden", "suspended"}
            and row.get("active") is not False and row.get("is_active") is not False
            and not any(row.get(key) for key in ("archived", "deleted", "is_archived", "is_deleted")))


def _coverage(proof):
    if type(proof) is not dict:
        return False
    expected, completed = proof.get("expected_requests"), proof.get("completed_requests")
    return (proof.get("status") == "complete"
            and proof.get("data_state") in {"confirmed_data", "confirmed_zero", "confirmed_no_data"}
            and type(expected) is int and expected > 0
            and type(completed) is int and completed == expected)


def _run_proven(key, run):
    summary = run.get("summary")
    if type(summary) is not dict:
        return False
    attempted = summary.get("accounts_attempted")
    if type(attempted) is not int or attempted <= 0:
        return False
    proof = summary.get("financial_proof") if key == "snapchat" else summary
    if type(proof) is not dict:
        return False
    complete, errors = proof.get("accounts_complete"), proof.get("errors_count")
    counts = type(complete) is int and complete == attempted and type(errors) is int and errors == 0
    if key == "snapchat":
        return (counts and run.get("status") in {"complete", "partial"}
                and type(proof.get("version")) is int and proof["version"] == 1 and proof.get("status") == "complete"
                and _coverage(proof.get("coverage")))
    return counts and run.get("status") == "complete"


def _google_selected(account, accounts, integration):
    identity = str(account.get("ad_account_id") or account.get("external_account_id") or "").replace("-", "").strip()
    if len(identity) != 10 or not identity.isascii() or not identity.isdigit():
        return False
    if "account_selection_configured" in integration or "selected_account_ids" in integration:
        selected = integration.get("selected_account_ids")
        if (integration.get("account_selection_configured") is not True or type(selected) is not list
                or not 1 <= len(selected) <= MAX_ACCOUNTS
                or any(type(item) not in {str, int} for item in selected)):
            return False
        selected = [str(item).replace("-", "").strip() for item in selected]
        if (len(set(selected)) != len(selected)
                or any(len(item) != 10 or not item.isascii() or not item.isdigit() for item in selected)):
            return False
        return identity in selected
    if any("mezan_selected" in row for row in accounts):
        return account.get("mezan_selected") is True
    return True  # The canonical wholly-unconfigured legacy policy is all accounts.


async def _advertising(db, owner, key, bindings, now):
    provider, run_type, source_mode = ADS[key]
    accounts = await _rows(db, "mezan_integration_accounts_v2", {"user_id": owner, "provider": provider},
                           ACCOUNT_FIELDS, limit=MAX_ACCOUNTS + 1)
    if len(accounts) > MAX_ACCOUNTS:
        return _row(key, "unknown", "metadata_limit")
    integrations = await _rows(db, "mezan_integrations_v2", {"user_id": owner, "provider": provider},
                               INTEGRATION_FIELDS, limit=2)
    if len(integrations) != 1:
        return _row(key, "unknown", "metadata_invalid")
    integration = integrations[0]
    if integration.get("connection_status") != "connected":
        return _row(key, "failed", "ad_account_unavailable")
    matched = []
    for binding in bindings:
        identifier, external = binding.get("integration_account_id"), binding.get("platform_account_id")
        if not all(type(item) is str and 0 < len(item) <= 200 for item in (identifier, external)):
            return _row(key, "unknown", "metadata_invalid")
        candidates = [row for row in accounts if row.get("mezan_integration_account_id") == identifier
                      and row.get("external_account_id") == external]
        if len(candidates) != 1:
            return _row(key, "unknown", "metadata_invalid")
        row = candidates[0]
        if (row.get("connection_status") != "connected"
                or key in {"snapchat", "meta"} and row.get("mezan_selected") is not True
                or key != "snapchat" and row.get("connection_provenance") != "api_connection"
                or key == "google_ads" and not _google_selected(row, accounts, integration)):
            return _row(key, "failed", "ad_account_unavailable")
        matched.append(row)

    sync_field = "financial_last_sync_at" if key == "snapchat" else "last_sync_at"
    observed_field = "financial_last_observed_at" if key == "snapchat" else "last_observed_at"
    stamps = [_known_time(row.get(sync_field), now) for row in matched]
    last = min(stamps) if stamps and all(stamp is not None for stamp in stamps) else None
    flushed = _known_time(integration.get("financial_last_sync_at"), now) if key == "snapchat" else None
    if key == "snapchat":
        # A per-account observation may precede a batch which ultimately fails.
        # Do not display that observation as the last successfully stored batch.
        last = min(last, flushed) if last is not None and flushed is not None else None
    query = {"user_id": owner, "provider": provider, "run_type": run_type}
    latest_rows = await _rows(db, "mezan_integration_sync_runs_v2", query, RUN_FIELDS, limit=1,
                              sort=[("started_at", -1)])
    success_query = {**query, "summary.financial_proof.status": "complete"} if key == "snapchat" else {**query, "status": "complete"}
    success_rows = await _rows(db, "mezan_integration_sync_runs_v2", success_query, RUN_FIELDS, limit=1,
                               sort=[("finished_at", -1)])
    errors = await _rows(db, "mezan_integration_errors_v2", {"user_id": owner, "provider": provider},
                        _projection("occurred_at", "code"), limit=1, sort=[("occurred_at", -1)])
    latest = latest_rows[0] if latest_rows else {}
    success = success_rows[0] if success_rows else {}
    success_at = _known_time(success.get("finished_at"), now) if _run_proven(key, success) else None
    if errors:
        error_at = _known_time(errors[0].get("occurred_at"), now)
        if error_at is None:
            return _row(key, "unknown", "metadata_invalid", last=last)
        if success_at is None or error_at > success_at:
            error_code = errors[0].get("code")
            reason = ("ad_source_authentication" if type(error_code) is str and error_code in AUTHENTICATION_ERRORS
                      else "ad_source_rate_limited" if type(error_code) is str and error_code in RATE_LIMIT_ERRORS
                      else "ad_source_failed")
            return _row(key, "failed", reason, last=last)
    latest_at = _known_time(latest.get("finished_at") or latest.get("started_at"), now)
    if latest and latest_at is None:
        return _row(key, "unknown", "metadata_invalid", last=last)
    if latest.get("status") not in {"queued", "running", "complete", "partial", "failed", "abandoned", "skipped"}:
        return _row(key, "unknown", "metadata_invalid", last=last)
    if (latest.get("status") in {"failed", "partial", "abandoned"}
            and not _run_proven(key, latest) and (success_at is None or latest_at >= success_at)):
        return _row(key, "failed", "ad_source_failed", last=last)
    if latest.get("status") in {"running", "queued"}:
        started = _known_time(latest.get("started_at"), now)
        if started is None:
            return _row(key, "unknown", "metadata_invalid", last=last)
        stalled = now - started > timedelta(minutes=25)
        return _row(key, "failed" if stalled else "updating",
                    "ad_source_stalled" if stalled else "ad_source_running", last=last)
    if last is None or success_at is None:
        return _row(key, "unknown", "metadata_invalid", last=last)
    for row, stamp in zip(matched, stamps):
        observed = _known_time(row.get(observed_field), now)
        if observed is None or observed < stamp or stamp > success_at or row.get("source_mode") != source_mode:
            return _row(key, "unknown", "metadata_invalid", last=last)
        if observed > stamp:
            return _row(key, "failed", "ad_account_partial", last=last)
        if key == "snapchat" and (row.get("financial_data_quality") != "complete" or not _coverage(row.get("financial_coverage"))):
            return _row(key, "failed", "ad_account_partial", last=last)
    scheduler = await _rows(db, "mezan_ads_auto_sync_scheduler_v2", {"_id": "ads-v2-server-scheduler"},
                            _projection("enabled", "interval_seconds"), limit=2)
    if len(scheduler) != 1 or type(scheduler[0].get("enabled")) is not bool:
        return _row(key, "unknown", "metadata_invalid", last=last)
    if scheduler[0]["enabled"] is False:
        return _row(key, "failed", "automatic_sync_disabled", last=last)
    if key == "snapchat":
        # Account timestamps are written before the all-account deferred flush.
        # The integration timestamp and a completed financial run must corroborate it.
        if (flushed is None or flushed < max(stamps) or flushed > success_at
                or integration.get("financial_data_quality") != "complete"
                or not _coverage(integration.get("financial_coverage"))):
            return _row(key, "unknown", "metadata_invalid", last=last)
        interval = 7200
    else:
        interval = scheduler[0].get("interval_seconds")
        if type(interval) is not int or not 300 <= interval <= 3600:
            return _row(key, "unknown", "metadata_invalid", last=last)
    # A known active run was handled above as updating, never as success. This
    # bounded allowance avoids labeling ordinary dispatch/overlap as a stall;
    # it cannot mask explicit errors or replace the displayed success time.
    stale = now - last > timedelta(seconds=interval + ADS_COMPLETION_ALLOWANCE_SECONDS)
    return _row(key, "stale" if stale else "current", "ad_source_stale" if stale else "ad_source_current", last=last)


async def _ads_sources(db, owner, now):
    try:
        async with asyncio.timeout(SOURCE_TIMEOUT_SECONDS):
            bindings = await _rows(db, "mz2_ad_account_bindings_v2", {"user_id": owner}, BINDING_FIELDS, limit=MAX_BINDINGS + 1)
        if len(bindings) > MAX_BINDINGS:
            return [_row(key, "unknown", "metadata_limit") for key in ADS]
        selected = [row for row in bindings if _usable(row) and row.get("confirmed_by") and row.get("confirmed_at")]
        if any(row.get("platform") not in ADS or row.get("funding_mode") not in {"prepaid", "postpaid", "hybrid"} for row in selected):
            return [_row(key, "unknown", "metadata_invalid") for key in ADS]
    except Exception:
        return [_row(key, "unknown", "metadata_unavailable") for key in ADS]

    async def one(key):
        required = [row for row in selected if row.get("platform") == key]
        if not required:
            return _row(key, "inactive", "source_not_configured")
        return await _safe(key, lambda: _advertising(db, owner, key, required, now))

    return await asyncio.gather(*(one(key) for key in ADS))


def _salla_due(day):
    return datetime.combine(day, datetime.min.time(), RIYADH).replace(hour=10).astimezone(timezone.utc)


async def _salla(db, owner, now):
    integrations = await _rows(db, "salla_integrations", {"user_id": owner},
                               _projection("status", "store_id"), limit=2)
    if not integrations:
        return _row("salla", "inactive", "source_not_configured")
    if len(integrations) != 1:
        return _row("salla", "unknown", "metadata_invalid")
    if integrations[0].get("status") != "connected":
        return _row("salla", "failed", "salla_source_failed")
    if integrations[0].get("store_id") not in {"1673234356", 1673234356}:
        return _row("salla", "unknown", "salla_schedule_unverified")
    records = await _rows(db, "salla_scheduled_reconciliation", {"_id": owner}, SALLA_FIELDS, limit=2)
    if len(records) != 1 or records[0].get("schedule_version") != "daily_10_riyadh_update_v1":
        return _row("salla", "unknown", "salla_schedule_unverified")
    record = records[0]
    last = _known_time(record.get("last_success_at"), now)
    status = record.get("status")
    if status in {"failed", "partial", "interrupted", "incomplete"}:
        return _row("salla", "failed", "salla_source_failed", last=last)
    if status == "running":
        started = _known_time(record.get("started_at"), now)
        if started is None:
            return _row("salla", "unknown", "metadata_invalid", last=last)
        stalled = now - started > timedelta(minutes=12)
        return _row("salla", "failed" if stalled else "updating",
                    "salla_source_stalled" if stalled else "salla_source_running", last=last)
    result = record.get("last_result")
    if status != "completed" or last is None or type(result) is not dict:
        return _row("salla", "unknown", "metadata_invalid", last=last)
    if type(result.get("errors_count")) is not int or type(result.get("truncated")) is not bool:
        return _row("salla", "unknown", "metadata_invalid", last=last)
    if (result.get("coverage_complete") is False or result.get("truncated") is True
            or any(type(result.get(field, 0)) is not int or result.get(field, 0) != 0
                   for field in ("errors_count", "incomplete_count"))):
        return _row("salla", "failed", "salla_source_failed", last=last)
    try:
        completed_day = date.fromisoformat(record["completed_slot"])
    except (KeyError, ValueError, TypeError):
        return _row("salla", "unknown", "metadata_invalid", last=last)
    through = _salla_due(completed_day)
    if through > last or last > now:
        return _row("salla", "unknown", "metadata_invalid", last=last)
    today_due = _salla_due(now.astimezone(RIYADH).date())
    # The existing daily cycle may continue/retry until 11:00 Riyadh.
    expected = today_due if now >= today_due + timedelta(hours=1) else today_due - timedelta(days=1)
    stale = through < expected
    return _row("salla", "stale" if stale else "current", "salla_source_stale" if stale else "salla_source_current",
                last=last, through=through.isoformat(), expected=expected.isoformat())


async def read_source_freshness(db, owner, *, as_of, now):
    """Return seven bounded source rows without exposing source identities or money.

    ``now`` is the caller's server UTC time. Errors are isolated by source and
    awaited deadlines cancel their owned reads; this function creates no retry
    loop or detached work. The caller owns authorization before and after reads.
    """
    if (type(owner) is not str or not owner or len(owner) > 200 or not isinstance(now, datetime)
            or now.tzinfo is None):
        return [_row(key, "unknown", "metadata_invalid") for key in LABELS]
    now = now.astimezone(timezone.utc)
    salla, advertising = await asyncio.gather(_safe("salla", lambda: _salla(db, owner, now)), _ads_sources(db, owner, now))
    return [_calculation("payroll", as_of, now), _calculation("recurring", as_of, now), salla, *advertising]
