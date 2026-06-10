"""BNPL FastAPI routes (Iter-116).

  GET  /api/bnpl/settings                          — list both providers
  GET  /api/bnpl/settings/{provider}               — masked config
  PUT  /api/bnpl/settings/{provider}               — upsert config (encrypted)
  POST /api/bnpl/{provider}/test-connection        — validates credentials
  POST /api/bnpl/tabby/sync                        — pull payments from
                                                     activation_date (or
                                                     ?since=YYYY-MM-DD for
                                                     manual backfill).
  GET  /api/bnpl/{provider}/transactions           — list local data
  GET  /api/bnpl/{provider}/refunds                — list local data
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth import get_current_user_from_db

from .clients.tabby import TabbyClient, TabbyError
from .clients.tamara import TamaraClient, TamaraError
from .config_store import (
    BNPL_PROVIDERS, DEFAULTS,
    ensure_indexes as ensure_settings_indexes,
    get_raw_secrets, get_settings, record_test_result, save_settings,
)
from .sync_service import ensure_sync_indexes, sync_tabby_payments
from .tabby_backfill_jobs import (
    continue_tabby_backfill, ensure_jobs_indexes,
    get_job_status, start_tabby_backfill,
)
from .tamara_backfill import backfill_tamara, backfill_tamara_full


async def ensure_bnpl_indexes(db) -> None:
    await ensure_settings_indexes(db)
    await ensure_sync_indexes(db)
    await ensure_jobs_indexes(db)


def attach_bnpl_routes(parent_router: APIRouter, db) -> None:
    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    router = APIRouter(prefix="/bnpl", tags=["bnpl"])

    # ── SETTINGS ───────────────────────────────────────────────
    @router.get("/settings")
    async def list_all_settings(user: dict = Depends(current_user)):
        out = {}
        for p in BNPL_PROVIDERS:
            out[p] = await get_settings(db, user["id"], p)
        return {"providers": out, "defaults": DEFAULTS}

    @router.get("/settings/{provider}")
    async def get_provider_settings(
        provider: str, user: dict = Depends(current_user),
    ):
        if provider not in BNPL_PROVIDERS:
            raise HTTPException(404, "Unknown provider")
        return await get_settings(db, user["id"], provider)

    @router.put("/settings/{provider}")
    async def update_provider_settings(
        provider: str, payload: dict, user: dict = Depends(current_user),
    ):
        if provider not in BNPL_PROVIDERS:
            raise HTTPException(404, "Unknown provider")
        try:
            return await save_settings(db, user["id"], provider, payload)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    # ── TEST CONNECTION ────────────────────────────────────────
    @router.post("/{provider}/test-connection")
    async def test_connection(
        provider: str, user: dict = Depends(current_user),
    ):
        if provider not in BNPL_PROVIDERS:
            raise HTTPException(404, "Unknown provider")
        secrets = await get_raw_secrets(db, user["id"], provider)
        try:
            if provider == "tabby":
                if not secrets.get("secret_key"):
                    raise HTTPException(400, "Tabby secret_key not set")
                cli = TabbyClient(
                    secret_key=secrets["secret_key"],
                    merchant_code=secrets.get("merchant_code") or "",
                    base_url=secrets.get("api_base_url") or "https://api.tabby.sa",
                )
                res = await cli.test_connection()
            else:
                if not secrets.get("api_token"):
                    raise HTTPException(400, "Tamara api_token not set")
                cli = TamaraClient(
                    api_token=secrets["api_token"],
                    base_url=secrets.get("api_base_url") or "https://api.tamara.co",
                )
                res = await cli.test_connection()
        except (TabbyError, TamaraError) as exc:
            await record_test_result(db, user["id"], provider, False, str(exc))
            raise HTTPException(400, str(exc))
        await record_test_result(db, user["id"], provider, True, None)
        return {"ok": True, "provider": provider, "detail": res}

    # ── SYNC (Tabby) ───────────────────────────────────────────
    @router.post("/tabby/sync")
    async def tabby_sync(
        since: Optional[str] = Query(
            None, pattern=r"^\d{4}-\d{2}-\d{2}$",
            description="Optional backfill date YYYY-MM-DD (overrides "
                        "activation_date for this call only).",
        ),
        user: dict = Depends(current_user),
    ):
        since_iso = f"{since}T00:00:00Z" if since else None
        res = await sync_tabby_payments(
            db, user["id"], since_iso=since_iso,
        )
        if not res.get("ok"):
            raise HTTPException(400, res.get("error") or "sync failed")
        return res

    # ── BACKFILL (Tamara — webhook-only provider) ──────────────
    @router.post("/tamara/backfill")
    async def tamara_backfill_endpoint(
        since: Optional[str] = Query(
            None, pattern=r"^\d{4}-\d{2}-\d{2}$",
            description="Only scan unified_orders with order_date >= since.",
        ),
        limit: int = Query(
            500, ge=1, le=5000,
            description="Max number of unified_orders rows to scan in one run.",
        ),
        user: dict = Depends(current_user),
    ):
        """Walk through `unified_orders` and look each up in Tamara via
        GET /merchants/orders/reference-id/{ref}.  Per-order outcome:
        not_found (404) is normal and means the order wasn't paid via
        Tamara.  Found orders get their payment_transactions and
        unified_orders row updated."""
        res = await backfill_tamara(
            db, user["id"], since=since, limit=int(limit),
        )
        if not res.get("ok"):
            raise HTTPException(400, res.get("error") or "backfill failed")
        return res

    @router.post("/tamara/backfill/full")
    async def tamara_backfill_full_endpoint(
        since: Optional[str] = Query(
            None, pattern=r"^\d{4}-\d{2}-\d{2}$",
            description="Only scan unified_orders with order_date >= since.",
        ),
        hard_cap: int = Query(
            10000, ge=100, le=50000,
            description="Absolute upper bound — safety net so a runaway "
                        "merchant catalogue can't burn a million Tamara calls.",
        ),
        user: dict = Depends(current_user),
    ):
        """Process EVERY pending order in batches of 100 until the
        candidate set is empty.  Returns aggregate + per-batch stats."""
        res = await backfill_tamara_full(
            db, user["id"], since=since, hard_cap=int(hard_cap),
        )
        if not res.get("ok"):
            raise HTTPException(400, res.get("error") or "backfill failed")
        return res

    # ── DEBUG (Tabby) ──────────────────────────────────────────
    @router.post("/tabby/backfill/start")
    async def tabby_backfill_start(
        cutoff: Optional[str] = Query(
            None, pattern=r"^\d{4}-\d{2}-\d{2}$",
            description="Stop when we reach payments older than this date.",
        ),
        user: dict = Depends(current_user),
    ):
        res = await start_tabby_backfill(db, user["id"], cutoff_date=cutoff)
        if not res.get("ok"):
            raise HTTPException(400, res.get("error") or "start failed")
        return res

    @router.post("/tabby/backfill/continue/{job_id}")
    async def tabby_backfill_continue(
        job_id: str, user: dict = Depends(current_user),
    ):
        # Ownership check
        job = await db.bnpl_sync_jobs.find_one(
            {"job_id": job_id, "user_id": user["id"]}, {"_id": 0, "job_id": 1},
        )
        if not job:
            raise HTTPException(404, "Unknown job_id")
        return await continue_tabby_backfill(db, job_id)

    @router.get("/tabby/backfill/status/{job_id}")
    async def tabby_backfill_status(
        job_id: str, user: dict = Depends(current_user),
    ):
        job = await get_job_status(db, job_id)
        if not job or job.get("user_id") != user["id"]:
            raise HTTPException(404, "Unknown job_id")
        # Strip large fields just in case
        job.pop("error_traceback", None)
        return job

    @router.post("/tabby/sync-debug")
    async def tabby_sync_debug(user: dict = Depends(current_user)):
        """Forensic debug — runs FIVE variations of the same Tabby call
        and returns the exact URL, params, status code and first 20
        payment IDs/dates from each, so we can pinpoint exactly which
        filter combination is hiding the merchant's data.

        Variations:
          A) NO filter at all (control — matches Debug)
          B) date filter only, YYYY-MM-DD format (matches current sync)
          C) date filter only, full ISO8601 datetime
          D) status=closed only, no date
          E) status=closed + date filter
        """
        import json as _json
        import httpx

        uid = user["id"]
        secrets = await get_raw_secrets(db, uid, "tabby")
        if not secrets.get("secret_key"):
            raise HTTPException(400, "Tabby secret_key not set")

        masked = await get_settings(db, uid, "tabby")
        act = masked.get("activation_date") or "2025-01-01"
        base = secrets.get("api_base_url") or "https://api.tabby.sa"
        url = f"{base}/api/v2/payments"
        headers = {
            "Authorization": f"Bearer {secrets['secret_key']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if secrets.get("merchant_code"):
            headers["X-Merchant-Code"] = secrets["merchant_code"]

        variations = [
            ("A_no_filter",          {"limit": 20}),
            ("B_date_yyyymmdd",      {"limit": 20, "created_at__gte": act}),
            ("C_date_iso8601",       {"limit": 20, "created_at__gte": f"{act}T00:00:00Z"}),
            ("D_status_closed",      {"limit": 20, "status": "closed"}),
            ("E_date_plus_closed",   {"limit": 20, "created_at__gte": act,
                                       "status": "closed"}),
        ]

        results: Dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=30) as cli_http:
            for label, params in variations:
                try:
                    resp = await cli_http.get(url, headers=headers, params=params)
                    raw_body: Any = {}
                    try:
                        raw_body = resp.json()
                    except ValueError:
                        raw_body = {"_raw_text": resp.text[:500]}

                    payments = (raw_body or {}).get("payments") or []
                    pagination = (raw_body or {}).get("pagination") or {}
                    results[label] = {
                        "http_status": resp.status_code,
                        "full_url": str(resp.url),
                        "params_sent": params,
                        "pagination": pagination,
                        "raw_payments_count": len(payments),
                        "payment_ids": [p.get("id") for p in payments[:20]],
                        "payment_dates": [(p.get("created_at") or "")[:19]
                                          for p in payments[:20]],
                        "payment_statuses": [p.get("status") for p in payments[:20]],
                        # Truncated raw body for inspection
                        "raw_body_preview": _json.dumps(raw_body)[:1500],
                    }
                except httpx.HTTPError as exc:
                    results[label] = {
                        "error": f"HTTP transport error: {exc}",
                        "params_sent": params,
                    }

        # Comparison verdict
        counts = {k: v.get("raw_payments_count", 0) for k, v in results.items()
                  if "raw_payments_count" in v}
        a = counts.get("A_no_filter", 0)
        b = counts.get("B_date_yyyymmdd", 0)
        c = counts.get("C_date_iso8601", 0)
        d = counts.get("D_status_closed", 0)
        e = counts.get("E_date_plus_closed", 0)

        if a > 0 and b == 0 and c == 0:
            verdict = (
                f"🔴 BUG IDENTIFIED: Date filter zeros out the result on "
                f"BOTH date formats (YYYY-MM-DD AND ISO8601). Without "
                f"filter we get {a}, with date filter we get 0. This is "
                "a Tabby-side filter quirk for THIS account."
            )
        elif a > 0 and b > 0:
            verdict = (
                f"✓ Date filter works! A={a}, B={b}. If the sync still "
                "shows 0, the bug is in our sync loop (offset/page_size)."
            )
        elif a > 0 and b == 0 and c > 0:
            verdict = (
                f"🟡 BUG FOUND: YYYY-MM-DD returns {b}, but ISO8601 "
                f"returns {c}. Tabby requires the ISO8601 format for "
                "your account. Will patch the client."
            )
        elif a > 0 and d == 0:
            verdict = (
                "🔴 status=closed returns 0 even though A has results "
                "with status=CLOSED. Status param is the culprit."
            )
        else:
            verdict = (
                f"Inspect manually: A={a}, B={b}, C={c}, D={d}, E={e}"
            )

        return {
            "ok": True,
            "activation_date_used": act,
            "base_url": base,
            "merchant_code": secrets.get("merchant_code") or "(none)",
            "variations": results,
            "counts_summary": counts,
            "verdict": verdict,
        }

    @router.post("/tabby/debug")
    async def tabby_debug(user: dict = Depends(current_user)):
        """Forensic check — fetch the LAST 10 payments from Tabby with
        NO date/status filter so we can prove (or disprove) that any
        payments exist for this merchant.

        Surfaces: key type, merchant_code, endpoint URL, raw count,
        and the first few payment summaries verbatim from Tabby.
        Returns a clear directive when the API returns an empty page so
        the merchant knows whether to contact Tabby support or fix the
        key locally.
        """
        uid = user["id"]
        secrets = await get_raw_secrets(db, uid, "tabby")
        masked = await get_settings(db, uid, "tabby")

        if not secrets.get("secret_key"):
            raise HTTPException(400, "Tabby secret_key not set")

        cli = TabbyClient(
            secret_key=secrets["secret_key"],
            merchant_code=secrets.get("merchant_code") or "",
            base_url=secrets.get("api_base_url") or "https://api.tabby.sa",
        )

        endpoint = f"{cli.base_url}/api/v2/payments"
        report: Dict[str, Any] = {
            "ok": True,
            "key_type": masked.get("secret_key_type") or "unknown",
            "key_masked": masked.get("secret_key_masked") or "",
            "merchant_code": secrets.get("merchant_code") or "(not set)",
            "endpoint": endpoint,
            "request_params": {"limit": 10, "no_date_filter": True,
                               "no_status_filter": True},
        }

        try:
            raw = await cli._get("/api/v2/payments", params={"limit": 10})  # noqa: SLF001
        except TabbyError as exc:
            report["ok"] = False
            report["error"] = str(exc)
            report["diagnosis"] = (
                f"Tabby rejected the request with status {exc.status}. "
                "This is an authentication / authorization issue at "
                "Tabby's side — NOT a Mezan bug. Action: verify the "
                "secret_key (must be Live, not Test, if your store is "
                "live), and that API access is enabled on this account."
            )
            return report

        payments = []
        pagination = {}
        if isinstance(raw, dict):
            payments = raw.get("payments") or []
            pagination = raw.get("pagination") or {}

        report["raw_payments_count"] = len(payments)
        report["pagination_total_count"] = pagination.get("total_count")
        report["sample_payments"] = [
            {
                "id": p.get("id"),
                "status": p.get("status"),
                "amount": p.get("amount"),
                "currency": p.get("currency"),
                "is_test": p.get("is_test"),
                "created_at": p.get("created_at"),
                "order_reference_id": (p.get("order") or {}).get("reference_id"),
            }
            for p in payments[:10]
        ]

        # ── Probe B: same call WITH the merchant's activation_date filter
        # so we can prove (or rule out) the date-filter as the cause.
        act = (await get_settings(db, uid, "tabby")).get("activation_date")
        report["activation_date_in_settings"] = act
        if act:
            try:
                raw2 = await cli._get(
                    "/api/v2/payments",
                    params={"limit": 10, "created_at__gte": act},
                )
                payments2 = (raw2 or {}).get("payments") or []
                report["with_date_filter"] = {
                    "filter": {"created_at__gte": act, "limit": 10},
                    "raw_count": len(payments2),
                    "pagination_total_count": (raw2 or {}).get(
                        "pagination", {}).get("total_count"),
                    "sample_dates": [p.get("created_at") for p in payments2[:10]],
                }
            except TabbyError as exc:
                report["with_date_filter"] = {"error": str(exc)}

        # ── Probe C: scan ALL Tabby statuses one-by-one to find hidden
        # payments (CREATED / REJECTED / EXPIRED don't show in default).
        report["by_status"] = {}
        for st in ("authorized", "closed", "rejected", "new",
                   "captured", "refunded", "cancelled"):
            try:
                rs = await cli._get(
                    "/api/v2/payments",
                    params={"limit": 1, "status": st},
                )
                report["by_status"][st] = (rs or {}).get(
                    "pagination", {}).get("total_count", 0)
            except TabbyError as exc:
                report["by_status"][st] = f"error: {exc.status}"

        if len(payments) == 0:
            if report["key_type"] == "test":
                report["diagnosis"] = (
                    "❌ المفتاح المحفوظ هو مفتاح Test (sk_test_…)، لذا "
                    "Tabby لن يُرجع أي معاملات حقيقية. الحل: استخدم "
                    "مفتاح Live (sk_live_…) من merchant.tabby.sa → "
                    "Developer → Live API Keys."
                )
            else:
                report["diagnosis"] = (
                    "✓ المفتاح يعمل (تمّت المصادقة بنجاح)، لكن Tabby "
                    "أرجع 0 معاملات بدون أي فلاتر. الأسباب المحتملة:\n"
                    "  1. Merchant API access غير مفعّل على حسابك. "
                    "تواصل مع partner@tabby.sa واطلب التفعيل.\n"
                    "  2. Merchant Code خاطئ أو ناقص (إن كان عندك "
                    "متاجر متعدّدة).\n"
                    "  3. حسابك جديد ولم تتمّ أي عملية بعد عبر Tabby.\n"
                    "هذه مشكلة من جانب Tabby وليست من Mezan."
                )
        else:
            # We have payments WITHOUT a filter. If WITH filter we
            # have zero, the activation_date is the culprit.
            with_filter = report.get("with_date_filter") or {}
            wf_count = with_filter.get("raw_count")
            sample_dates = [
                (p.get("created_at") or "")[:10] for p in payments
            ]
            latest_existing = max(sample_dates) if sample_dates else None
            base_msg = (
                f"✓ Tabby أرجع {len(payments)} معاملة بدون فلتر "
                f"(الأحدث بتاريخ {latest_existing}). "
            )
            if act and wf_count == 0:
                report["diagnosis"] = (
                    base_msg
                    + f"⚠️ مع فلتر activation_date={act} أرجع 0 معاملة. "
                    + f"هذا يعني أن كل معاملاتك في Tabby أقدم من {act}. "
                    + "الحل: غيّر activation_date إلى تاريخ أقدم "
                    + "(مثل 2025-01-01) من صفحة الإعدادات، ثم زامن."
                )
            elif act and wf_count and wf_count > 0:
                report["diagnosis"] = (
                    base_msg
                    + f"✓ مع فلتر activation_date={act} يُرجع {wf_count}+ معاملة. "
                    + "النظام يعمل بالكامل — تأكّد أن المزامنة تُحفظ "
                    + "في DB (راجع reconciliation في sync stats)."
                )
            else:
                report["diagnosis"] = base_msg + (
                    "النظام يعمل بشكل صحيح. تابع نتائج المزامنة "
                    "العادية لمعرفة كم معاملة تُحفظ."
                )

        return report

    # ── LIST LOCAL DATA ────────────────────────────────────────
    @router.get("/{provider}/transactions")
    async def list_transactions(
        provider: str,
        from_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        limit: int = Query(200, ge=1, le=2000),
        user: dict = Depends(current_user),
    ):
        if provider not in BNPL_PROVIDERS:
            raise HTTPException(404, "Unknown provider")
        q = {"user_id": user["id"], "provider": provider}
        if from_date or to_date:
            d = {}
            if from_date:
                d["$gte"] = from_date
            if to_date:
                d["$lte"] = to_date + "T23:59:59Z"
            q["created_at_provider"] = d
        rows = await (
            db.payment_transactions.find(q, {"_id": 0, "raw_payload": 0})
            .sort([("created_at_provider", -1)])
            .limit(limit).to_list(limit)
        )
        return {"items": rows, "count": len(rows)}

    @router.get("/{provider}/refunds")
    async def list_refunds(
        provider: str,
        limit: int = Query(200, ge=1, le=2000),
        user: dict = Depends(current_user),
    ):
        if provider not in BNPL_PROVIDERS:
            raise HTTPException(404, "Unknown provider")
        rows = await (
            db.payment_refunds.find(
                {"user_id": user["id"], "provider": provider},
                {"_id": 0, "raw": 0},
            )
            .sort([("refunded_at", -1)])
            .limit(limit).to_list(limit)
        )
        return {"items": rows, "count": len(rows)}

    parent_router.include_router(router)
