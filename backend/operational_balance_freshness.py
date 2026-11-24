"""Bounded, owner-scoped health metadata for automatic balances.

This reader never recalculates, initializes an index, or calls a provider. The
separate attempt recorder writes only diagnostic metadata after an authorized
refresh acquired its existing lease. Financial state remains the authority.
"""
import asyncio
from datetime import datetime, timezone
import logging
from uuid import uuid4
from zoneinfo import ZoneInfo
import pymongo

from operational_balance_store import STATES, digest
from operational_source_diagnostics import source_failure_detail
from operational_entity_freshness import FIELD as COVERAGE_FIELD, MAX_ENTITIES, coverage_rows


HEALTH = "operational_balance_refresh_health_v1"
LEASES = "operational_balance_refresh_leases_v1"
MAX_AGE_SECONDS = 7200
READ_SECONDS = 8
WRITE_SECONDS = 1
RIYADH = ZoneInfo("Asia/Riyadh")
_LOG = logging.getLogger(__name__)
_STATES = {"current", "stale", "failed", "updating", "unknown", "inactive"}
_SOURCE_KEYS = {"payroll", "recurring", "salla", "snapchat", "meta", "tiktok", "google_ads"}
_HEADER = {"_id": 0, "owner_id": 1, "status": 1, "revision": 1, "engine.as_of": 1,
    **{f'engine.{COVERAGE_FIELD}.{key}': 1 for key in
       ('schema_version', 'owner_id', 'as_of', 'calculation_revision', 'complete', 'total_entities')},
    f'engine.{COVERAGE_FIELD}.rows': {'$slice': MAX_ENTITIES + 1}}
_ERROR_MESSAGES = {
    "operational_refresh_timeout": "انتهت مهلة تحديث الأرصدة؛ تظهر آخر نتيجة محفوظة ولم يثبت اكتمال التحديث.",
    "operational_refresh_cancelled": "انقطع تحديث الأرصدة قبل وصول تأكيد نهائي؛ يلزم التحقق من آخر احتساب محفوظ.",
    "operational_refresh_lease_lost": "فقد طلب التحديث حجزه؛ لم يثبت اكتمال احتساب المصادر.",
    "operational_capacity_requires_archive": "بلغ سجل الأرصدة حد السعة؛ يحتاج إلى أرشفة معتمدة قبل استكمال التحديث.",
    "operational_actor_scope_changed": "تغيرت جلسة الحساب أثناء التحديث؛ يلزم إعادة التحقق من الصلاحية.",
    "operational_concurrent_change": "تغيرت البيانات أثناء التحديث ولم يثبت حفظ نتيجة جديدة.",
    "operational_refresh_failed": "تعذر استكمال تحديث الأرصدة؛ يلزم مراجعة المصدر وإعادة التحقق.",
}
_FAMILIES = {
    "bank": ("bank", "التحصيلات البنكية"),
    "courier": ("shipping", "شركات الشحن"),
    "sar": ("payments", "تحصيلات منصات الدفع"),
    "provider": ("payments", "تحصيلات منصات الدفع"),
    "return_waybills": ("shipping", "شركات الشحن"),
    "delivery_history": ("shipping", "شركات الشحن"),
}
_CALCULATION_ISSUES = {
    "operational_salary_start_policy_invalid": ("payroll", "الموظفون", "إعداد بداية احتساب الرواتب غير مكتمل."),
    "salary_contract_missing": ("payroll", "الموظفون", "يوجد استحقاق راتب لا يغطيه عقد صالح؛ يلزم مراجعة عقود الموظفين."),
    "salary_source_changed": ("payroll", "الموظفون", "تغير مصدر راتب سبق احتسابه؛ يحتاج إلى مراجعة موثقة."),
    "operational_order_bank_credit_conflict": ("bank", "التحصيلات البنكية", "تعارض دليل تحصيل بنكي؛ لم يُعتمد كتحصيل جديد."),
    "operational_order_bank_occurrence_unproven": ("bank", "التحصيلات البنكية", "لم يثبت التاريخ الأصلي للتحصيل البنكي؛ لم تُسجل حركة بتاريخ إعادة الاحتساب."),
    "operational_order_bank_in_progress_history_missing": ("bank", "التحصيلات البنكية", "سجل حالة طلب بنكي غير مكتمل؛ لم يثبت تاريخ التحصيل الأصلي."),
    "operational_order_bank_in_progress_unproven": ("bank", "التحصيلات البنكية", "لم يثبت تاريخ انتقال الطلب إلى حالة مؤهلة للتحصيل البنكي."),
    "operational_obligation_entity_incomplete": ("parties", "الجهات التلقائية", "يوجد استحقاق مرتبط بجهة غير مكتملة الإعداد."),
    "provider_original_capture_date_unproven": ("payments", "منصات الدفع", "لم يثبت تاريخ التحصيل الأصلي لمنصة دفع؛ أُوقف تأكيد المبلغ الجديد حتى وصول دليل التحصيل."),
    "provider_capture_multiple_business_dates": ("payments", "منصات الدفع", "يتوزع تحصيل منصة الدفع على أيام مختلفة؛ يلزم إثبات مستقل لكل يوم قبل تأكيد الزيادة."),
    "provider_capture_acquisition_pending": ("payments", "منصات الدفع", "لم يثبت اكتمال جلب أدلة التحصيل الأصلية؛ أُوقف تأكيد مبالغ جديدة حتى اكتمال التحقق من المصدر."),
    "operational_shipping_original_date_unproven": ("shipping", "شركات الشحن والموصلون", "لم يثبت وقت التوصيل أو الإكمال الأصلي؛ لم يُسجل استحقاق شحن جديد بتاريخ إعادة الاحتساب."),
    "operational_prepaid_shipping_original_date_unproven": ("shipping", "شركات الشحن", "لم يثبت وقت التنفيذ الأصلي للشحن المدفوع مسبقًا؛ يلزم دليل من المصدر قبل تسجيل الاستحقاق."),
}


def timestamp(value):
    if isinstance(value, datetime):
        # PyMongo decodes BSON UTC dates as naive datetimes by default. String
        # evidence still requires an explicit offset; never infer its timezone.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if type(value) is not str or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def iso(value):
    parsed = timestamp(value)
    return parsed.isoformat() if parsed is not None else None


def failure_detail(detail):
    if type(detail) is dict and detail.get("code") == "operational_source_setup_incomplete":
        # Rebuild from the closed vocabulary; never copy an exception message.
        return source_failure_detail(detail.get("cause"), detail.get("source_collection"))
    code = detail.get("code") if type(detail) is dict else None
    code = code if type(code) is str and code in _ERROR_MESSAGES else "operational_refresh_failed"
    return {"code": code, "message": _ERROR_MESSAGES[code]}


def safe_failure(error):
    return failure_detail(getattr(error, "detail", None))


def source_warnings(state, native):
    """Retain known unresolved source gaps without amounts or entity IDs."""
    found = {}
    engine = state.get("engine") if type(state) is dict else None
    issues = engine.get("issues", []) if type(engine) is dict else []
    if type(issues) is list:
        for row in issues:
            code = row.get("code") if type(row) is dict else None
            if type(code) is str and code in _CALCULATION_ISSUES:
                key, label, message = _CALCULATION_ISSUES[code]
                found[(key, code)] = {"key": key, "label": label, "code": code, "message": message}
    if type(native) is dict:
        for name, (key, label) in _FAMILIES.items():
            part = native.get(name)
            if type(part) is not dict:
                continue
            outcomes = part.get("outcomes")
            unresolved = type(outcomes) is list and any(type(row) is dict and type(row.get("status")) is str and row.get("status") in
                {"blocked", "unavailable", "source_changed"} for row in outcomes)
            if part.get("status") in ("deferred", "partial", "unavailable", "blocked", "source_changed") or unresolved:
                found[(key, "operational_native_evidence_incomplete")] = {
                    "key": key, "label": label, "code": "operational_native_evidence_incomplete",
                    "message": "لم يكتمل التحقق من أدلة هذا المصدر؛ نجاح الاحتساب لا يعني اكتمال بياناته.",
                }
        if native.get("status") == "deferred" and not any(code == "operational_native_evidence_incomplete" for _, code in found):
            found[("sources", "operational_native_evidence_incomplete")] = {
                "key": "sources", "label": "مصادر الأرصدة", "code": "operational_native_evidence_incomplete",
                "message": "لم تكتمل قراءة بعض أدلة المصادر؛ تظل النتائج غير المكتملة بحاجة إلى مراجعة.",
            }
    return list(found.values())[:24]


async def begin_attempt(db, owner, *, now=None):
    stamp = now or datetime.now(timezone.utc)
    token = uuid4().hex
    # The existing refresh lease serializes ordinary starts. The timestamp
    # predicate also fences a Mongo write whose client timed out before reply.
    query = {"_id": owner, "owner_id": owner, "$or": [
        {"attempt_started_at": {"$exists": False}}, {"attempt_started_at": {"$lte": stamp}}]}
    values = {"owner_id": owner, "attempt_token": token, "attempt_started_at": stamp,
              "last_attempt": {"started_at": stamp.isoformat(), "finished_at": None,
                               "outcome": "running", "error_code": None}}
    try:
        with pymongo.timeout(WRITE_SECONDS):
            async with asyncio.timeout(WRITE_SECONDS):
                await db[HEALTH].update_one(query, {"$set": values}, upsert=True)
        return {"token": token, "started_at": stamp.isoformat()}
    except Exception:
        _LOG.warning("Operational freshness attempt metadata unavailable")
        return None


async def finish_attempt(db, owner, attempt, *, result=None, error=None, warnings=None, now=None):
    if not attempt:
        return
    stamp = now or datetime.now(timezone.utc)
    receipt = result if type(result) is dict else {}
    as_of = timestamp(receipt.get("as_of"))
    success = (error is None and receipt.get("status") == "refreshed" and as_of is not None
               and as_of <= stamp and type(receipt.get("revision")) is int and receipt["revision"] > 0)
    failure = safe_failure(error) if error is not None else {
        "code": "operational_refresh_failed", "message": _ERROR_MESSAGES["operational_refresh_failed"]}
    unknown = isinstance(error, asyncio.CancelledError) or failure["code"] == "operational_refresh_cancelled"
    values = {"last_attempt": {"started_at": attempt["started_at"], "finished_at": stamp.isoformat(),
              "outcome": "success" if success else "unknown" if unknown or error is None else "failed",
              "error_code": None if success else failure["code"]}}
    if success:
        values.update(last_success_at=as_of.isoformat(), last_success_revision=receipt["revision"],
                      source_warnings=warnings or [], last_failure=None, last_failure_trace=None)
    else:
        values["last_failure"] = failure
        # Preserve only allowlisted stages and timings, never raw error details.
        # The refresh timeout boundary already emitted this bounded trace.
        from operational_refresh_failure_trace import safe_failure_trace
        values["last_failure_trace"] = safe_failure_trace(getattr(error, "detail", None))
    try:
        with pymongo.timeout(WRITE_SECONDS):
            async with asyncio.timeout(WRITE_SECONDS):
                # A late response from an earlier attempt cannot overwrite a newer
                # attempt or clear its alert. Never upsert the completion.
                await db[HEALTH].update_one({"_id": owner, "owner_id": owner, "attempt_token": attempt["token"]},
                                           {"$set": values})
    except Exception:
        _LOG.warning("Operational freshness completion metadata unavailable")


def _issue(code, source, message, last_success=None, severity="warning"):
    return {"code": code, "source": source, "severity": severity,
            "message": message, "last_success_at": iso(last_success)}


def compose_freshness(owner, actor, header, health, lease, sources, *, now, unavailable=False):
    header = header if type(header) is dict else {}
    health = health if type(health) is dict else {}
    lease = lease if type(lease) is dict else {}
    status = header.get("status", "draft")
    revision = header.get("revision", 0)
    engine = header.get("engine")
    as_of = timestamp(engine.get("as_of")) if type(engine) is dict else None
    if type(revision) is not int or revision < 0 or type(status) is not str:
        status, revision, as_of, unavailable = "unknown", 0, None, True
    issues = []
    aggregate = "current"
    if status != "active":
        aggregate = "unknown" if status == "unknown" else "inactive"
        if status != "draft":
            issues.append(_issue("operational_calculation_inactive", "calculation",
                "الاحتساب التشغيلي غير نشط؛ لا تُعامل الأرصدة المحفوظة على أنها تحديث تلقائي جديد.", as_of, "info"))
    elif as_of is None or as_of > now:
        aggregate = "unknown"
        issues.append(_issue("operational_calculation_unverified", "calculation",
            "تعذر التحقق من وقت آخر احتساب ناجح للأرصدة.", as_of))
    elif ((now - as_of).total_seconds() >= MAX_AGE_SECONDS
          or now.astimezone(RIYADH).date() != as_of.astimezone(RIYADH).date()):
        aggregate = "stale"
        issues.append(_issue("operational_calculation_stale", "calculation",
            "الأرصدة من احتساب قديم؛ قد لا تشمل الاستحقاقات الجديدة. يلزم تحديث المصادر والتحقق من النتيجة.", as_of))

    attempt = health.get("last_attempt") if type(health) is dict else None
    safe_attempt = None
    if type(attempt) is dict and type(attempt.get("outcome")) is str and attempt.get("outcome") in {"running", "success", "failed", "unknown"}:
        started = timestamp(attempt.get("started_at"))
        finished = timestamp(attempt.get("finished_at"))
        if started is None or started > now or finished is not None and (finished < started or finished > now):
            unavailable = True
        else:
            code = attempt.get("error_code")
            safe_attempt = {"started_at": started.isoformat(), "finished_at": iso(finished),
                "outcome": attempt["outcome"], "error_code": code if type(code) is str and (code in _ERROR_MESSAGES or code == "operational_source_setup_incomplete") else None}
            expires = timestamp(lease.get("expires_at"))
            running = attempt["outcome"] == "running" and expires is not None and expires > now
            if status == "active" and running:
                aggregate = "updating"
                issues.append(_issue("operational_refresh_running", "calculation",
                    "يجري تحديث المصادر؛ الأرقام المعروضة هي آخر نتيجة محفوظة حتى وصول التأكيد.", as_of, "info"))
            elif status == "active" and attempt["outcome"] == "running":
                # The financial commit can succeed before diagnostic completion
                # fails. Never leave clients permanently blocked by a durable
                # running marker after its lease has gone, even if as_of moved.
                safe_attempt["outcome"] = "unknown"
                aggregate = "unknown"
                issues.append(_issue("operational_refresh_outcome_unknown", "calculation",
                    "انتهت محاولة التحديث دون تأكيد كامل لحالة جميع المصادر؛ تظهر آخر نتيجة احتساب محفوظة.", as_of))
            elif (finished is not None and as_of is not None and as_of > finished
                  and attempt["outcome"] in {"failed", "unknown"}):
                # A later authoritative aggregate supersedes this older failed
                # attempt. Its own independently read source warnings remain.
                safe_attempt.update(outcome="success", finished_at=as_of.isoformat(), error_code=None)
            later_than_saved = as_of is None or started >= as_of
            if status == "active" and later_than_saved:
                if attempt["outcome"] in {"failed", "unknown"}:
                    failure = failure_detail(health.get("last_failure"))
                    aggregate = "failed" if attempt["outcome"] == "failed" else "unknown"
                    safe_attempt["outcome"] = "failed" if aggregate == "failed" else "unknown"
                    message = failure["message"] if aggregate == "failed" else "لم يصل تأكيد نهائي للتحديث السابق؛ يلزم التحقق قبل تكرار الاحتساب."
                    issues.append(_issue(failure["code"] if aggregate == "failed" else "operational_refresh_outcome_unknown",
                                         "calculation", message, as_of, "error"))
    elif attempt is not None:
        unavailable = True

    safe_sources = []
    if status == "active":
        if type(sources) is not list or len(sources) > 64:
            unavailable = True
            sources = sources[:64] if type(sources) is list else []
        for row in sources[:64]:
            if (type(row) is not dict or type(row.get("state")) is not str or row["state"] not in _STATES
                    or any(type(row.get(key)) is not str or not row[key] or len(row[key]) > limit
                           for key, limit in (("key", 40), ("label", 120), ("message", 600)))):
                unavailable = True
                continue
            safe = {key: row.get(key) for key in ("key", "label", "state", "last_success_at", "data_through", "expected_through", "message")}
            safe["last_success_at"] = iso(safe["last_success_at"])
            for key in ("data_through", "expected_through"):
                if safe[key] is not None and (type(safe[key]) is not str or len(safe[key]) > 64):
                    safe[key], unavailable = None, True
            safe_sources.append(safe)
        keys = [row["key"] for row in safe_sources]
        if not _SOURCE_KEYS.issubset(keys) or len(keys) != len(set(keys)):
            unavailable = True
        # A successful aggregate cannot erase known failed/deferred native
        # acquisition or unresolved salary source evidence from that attempt.
        warning_as_of = timestamp(health.get("last_success_at"))
        warnings = health.get("source_warnings") or []
        if warning_as_of is not None and as_of == warning_as_of and type(warnings) is list:
            for row in warnings[:24]:
                if type(row) is dict and type(row.get("code")) is str and row.get("code") in {*_CALCULATION_ISSUES, "operational_native_evidence_incomplete"}:
                    if row["code"] in _CALCULATION_ISSUES:
                        key, label, message = _CALCULATION_ISSUES[row["code"]]
                    else:
                        key = row.get("key") if type(row.get("key")) is str and row.get("key") in {"bank", "shipping", "payments", "sources"} else "sources"
                        label = {"bank": "التحصيلات البنكية", "shipping": "شركات الشحن", "payments": "منصات الدفع", "sources": "مصادر الأرصدة"}[key]
                        message = "لم يكتمل التحقق من أدلة هذا المصدر؛ نجاح الاحتساب لا يعني اكتمال بياناته."
                    found = next((item for item in safe_sources if item["key"] == key), None)
                    if found is not None and found["state"] == "current":
                        found.update(state="unknown", message=message)
                    elif found is None:
                        safe_sources.append({"key": key, "label": label, "state": "unknown", "last_success_at": None,
                                             "data_through": None, "expected_through": None, "message": message})
                    issues.append(_issue(row["code"], key, message, None))
        for row in safe_sources:
            if row["state"] not in {"current", "inactive"}:
                issues.append(_issue("operational_source_" + row["state"], row["key"],
                                     row["message"], row["last_success_at"], "error" if row["state"] == "failed" else "warning"))
        if aggregate == "current" and any(row["state"] not in {"current", "inactive"} for row in safe_sources):
            aggregate = next((state for state in ("failed", "stale", "unknown", "updating")
                              if any(row["state"] == state for row in safe_sources)), "unknown")
    if unavailable:
        issues.append(_issue("operational_health_unavailable", "health",
            "تعذر التحقق من حالة بعض التحديثات؛ لا يوجد تأكيد بأن جميع المصادر محدثة.", as_of))
        if aggregate in {"current", "inactive"}:
            aggregate = "unknown"
    saved_coverage = engine.get(COVERAGE_FIELD) if type(engine) is dict else None
    entity_coverage = (coverage_rows(saved_coverage, owner, engine.get('as_of') if type(engine) is dict else None,
        now=now, revision=revision) if status == 'active' else
        {'state': 'inactive', 'rows': [], 'omitted_entities': 0, 'message': ''})
    # Native acquisition may still be deferred after a successful calculation.
    # Its family warning also prevents a compact entity row claiming recovery.
    family = {'employee': 'payroll', 'recurring': 'recurring', 'courier': 'shipping',
              'store_driver': 'shipping', 'provider': 'payments', 'bank': 'bank', 'supplier': 'salla'}
    for row in entity_coverage['rows']:
        keys = {row.get('platform') or family.get(row['entity_type'])}
        if row['entity_type'] in {'courier', 'store_driver', 'provider', 'bank', 'supplier'}:
            keys.add('salla')
        source = next((source for source in safe_sources if source['key'] in keys
                       and source['state'] not in {'current', 'inactive'}), None)
        if source and row['state'] == 'current':
            row.update(state='unknown', message=source['message'])
            entity_coverage['state'] = 'unknown'
    if saved_coverage is not None and aggregate == 'current' and entity_coverage['state'] in {'stale', 'unknown'}:
        aggregate = entity_coverage['state']
    if safe_attempt is not None and safe_attempt["outcome"] in {"failed", "unknown"}:
        # The latest persisted timeout is independently inspectable; it cannot
        # turn a failed health receipt into a successful financial snapshot.
        from operational_refresh_failure_trace import safe_failure_trace
        safe_attempt["diagnostics"] = safe_failure_trace(health.get("last_failure_trace"))
    return {"schema_version": 1, "status": status, "actor": {"id": actor, "is_owner": True},
            "session_scope": digest([owner, actor]), "as_of": iso(as_of), "revision": revision,
            "generated_at": now.isoformat(), "freshness": {"state": aggregate, "max_age_seconds": MAX_AGE_SECONDS},
            "last_attempt": safe_attempt, "sources": safe_sources, "issues": issues[:100],
            "entity_coverage": entity_coverage}


async def read_freshness(db, owner, actor, *, now=None):
    from operational_source_freshness import read_source_freshness
    stamp = now or datetime.now(timezone.utc)

    async def one(collection, query, projection):
        with pymongo.timeout(1.5):
            result = await db[collection].find_one(query, projection, max_time_ms=1500)
        if result is not None and type(result) is not dict:
            raise ValueError("invalid_freshness_metadata")
        return result or {}

    async def owned_reads(*operations):
        tasks = [asyncio.create_task(operation) for operation in operations]
        try:
            return await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Observe every sibling before the error/timeout leaves this read;
            # no detached metadata work continues after a response is returned.
            await asyncio.gather(*tasks, return_exceptions=True)

    async with asyncio.timeout(READ_SECONDS):
        for attempt in range(2):
            header, health, lease, marker, settings = await owned_reads(
                one(STATES, {"_id": owner, "owner_id": owner}, _HEADER),
                one(HEALTH, {"_id": owner, "owner_id": owner}, {"_id": 0, "last_attempt": 1,
                    "last_failure": 1, "last_failure_trace": 1, "last_success_at": 1, "source_warnings": 1}),
                one(LEASES, {"_id": owner, "owner_id": owner}, {"_id": 0, "expires_at": 1}),
                one("mz2_atomic_owners", {"_id": owner}, {"_id": 0, "ledger_backend_state": 1}),
                one("settings", {"user_id": owner}, {"_id": 0, "mezan2_financial_cutover.status": 1}),
            )
            original_header = header.copy()
            cutover = settings.get("mezan2_financial_cutover")
            if (marker.get("ledger_backend_state") == "v2_active"
                    or type(cutover) is dict and cutover.get("status") == "active"):
                header = {**header, "status": "accounting_active"}
            sources, unavailable = [], False
            if header.get("status") == "active":
                try:
                    engine = header.get("engine")
                    sources = await read_source_freshness(db, owner, as_of=engine.get("as_of") if type(engine) is dict else None, now=stamp)
                except Exception:
                    # Retain calculation metadata while stating that source
                    # freshness could not be established; no error text leaks.
                    unavailable = True
            latest = await one(STATES, {"_id": owner, "owner_id": owner}, _HEADER)
            if latest != original_header:
                if attempt == 0:
                    continue
                header, sources, unavailable = {**latest, "status": "unknown"}, [], True
            return compose_freshness(owner, actor, header, health, lease, sources, now=stamp, unavailable=unavailable)
