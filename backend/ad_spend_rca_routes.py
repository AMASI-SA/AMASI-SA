"""Iter-251 v10 — Ad Spend RCA (Read-Only).

Pure read-only diagnostic for Meta + Snapchat spend discrepancies
between:
  • Raw API payload (collected by the cron)
  • Cumulative / daily spend stored in *_account_daily / *_ads_daily
  • General ledger entries actually posted
  • FX-converted SAR amount used in the ledger (Snapchat USD → SAR)

Triggered on demand by the merchant via:

    GET /api/ad-spend-rca?date=YYYY-MM-DD&providers=meta,snapchat

The endpoint reads ONLY, never writes.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query


def make_ad_spend_rca_router(db, current_user):
    router = APIRouter(prefix="/ad-spend-rca", tags=["ad-spend-rca"])

    PROVIDER_SOURCES = {
        "meta": [
            {"collection": "meta_ads_daily", "scope": "account_id"},
        ],
        "snapchat": [
            {"collection": "snapchat_account_daily",
             "scope": "ad_account_id"},
            {"collection": "snapchat_ads_daily", "scope": None},
        ],
    }

    @router.get("")
    async def rca(
        date:      str = Query(..., description="YYYY-MM-DD"),
        providers: str = Query("meta,snapchat",
                                description="comma-separated"),
        user: dict = Depends(current_user),
    ):
        """Per-account RCA for the given date.

        For each ad_account counterparty (provider in providers list)
        returns:
          1.  internal account_id  (counterparty.id)
          2.  external_account_id  (counterparty.external_id)
          3.  account name
          4.  date used                (= the requested date)
          5.  timezone used            ("Asia/Riyadh" by convention)
          6.  raw spend from API       (sum over *_ads_daily for the
                                         date — exact source row count
                                         shown too)
          7.  previously-stored spend  (sum from previous-day rollup
                                         doc, when present)
          8.  delta posted to GL       (general_ledger entries for the
                                         date with entry_type='ad_spend'
                                         scoped to this entity_id)
          9.  raw payload              (the source documents)
          10. cumulative vs daily      (derived from the document
                                         period_key / shape)
          11. Reconciliation triplet
              (API_sar  vs  GL_sar  vs  UI_sar/balance)
              + delta_api_vs_gl, delta_api_vs_ui
        """
        uid = user["id"]
        provider_list = [
            p.strip().lower() for p in (providers or "").split(",")
            if p.strip()
        ]
        out: dict[str, Any] = {
            "date":       date,
            "providers":  provider_list,
            "timezone":   "Asia/Riyadh",
            "accounts":   [],
            "note": (
                "Read-only. Compare api_spend_sar (what the cron saw "
                "via API), gl_total_sar (what the ledger holds for "
                "this date), and ui_balance_sar (what the dashboard "
                "shows now).  delta_* fields surface the gap."
            ),
        }

        for provider in provider_list:
            if provider not in PROVIDER_SOURCES:
                continue
            # 1. List the merchant's ad-account counterparties of
            # this provider.
            async for cp in db.counterparties.find(
                {"user_id": uid, "kind": "ad_account",
                 "provider": provider},
                {"_id": 0},
            ):
                record = await _build_account_record(
                    db, uid, provider, cp, date,
                    PROVIDER_SOURCES[provider])
                out["accounts"].append(record)

        # Top-level totals per provider
        totals: dict[str, dict] = {}
        for r in out["accounts"]:
            p = r["provider"]
            t = totals.setdefault(p, {
                "api_spend_sar": 0.0, "gl_total_sar": 0.0,
                "ui_balance_sar": 0.0,
            })
            t["api_spend_sar"]  += r["reconciliation"]["api_spend_sar"]  or 0
            t["gl_total_sar"]   += r["reconciliation"]["gl_total_sar"]   or 0
            t["ui_balance_sar"] += r["reconciliation"]["ui_balance_sar"] or 0
        for p, t in totals.items():
            t["delta_api_vs_gl"]  = round(
                (t["api_spend_sar"]  or 0) - (t["gl_total_sar"] or 0), 2)
            t["delta_api_vs_ui"]  = round(
                (t["api_spend_sar"]  or 0) - (t["ui_balance_sar"] or 0), 2)
            t["delta_gl_vs_ui"]   = round(
                (t["gl_total_sar"]   or 0) - (t["ui_balance_sar"] or 0), 2)
            for k in ("api_spend_sar", "gl_total_sar",
                      "ui_balance_sar"):
                t[k] = round(t[k], 2)
        out["totals_per_provider"] = totals

        return out

    async def _build_account_record(
        db, uid: str, provider: str, cp: dict, date_str: str,
        sources: list,
    ) -> dict:
        cp_id     = cp.get("id")
        ext_id    = cp.get("external_id")
        name      = cp.get("name")
        currency  = (cp.get("currency") or
                     cp.get("ad_account_currency") or "").upper()

        # 6 + 9 — Raw API spend rows for this date (source-of-truth)
        source_docs: list[dict] = []
        api_spend_native = 0.0
        source_used: Optional[str] = None
        for src in sources:
            q: dict = {"user_id": uid, "date": date_str}
            if src["scope"] and ext_id:
                q[src["scope"]] = ext_id
            elif src["scope"] and not ext_id:
                # cannot scope ⇒ skip to next source
                continue
            cursor = db[src["collection"]].find(q, {"_id": 0})
            collected = []
            async for r in cursor:
                collected.append(r)
                api_spend_native += float(r.get("spend") or 0)
            if collected:
                source_docs = collected
                source_used = src["collection"]
                break

        # 7 — Previous-day stored spend (for cumulative comparison)
        prev_stored = 0.0
        prev_date: Optional[str] = None
        if source_used:
            from datetime import date as _date, timedelta
            try:
                pd = _date.fromisoformat(date_str) - timedelta(days=1)
                prev_date = pd.isoformat()
                pq: dict = {"user_id": uid, "date": prev_date}
                if ext_id and any(s["scope"] for s in sources):
                    scope = next((s["scope"] for s in sources
                                   if s["collection"] == source_used
                                   and s["scope"]), None)
                    if scope:
                        pq[scope] = ext_id
                async for r in db[source_used].find(pq, {"_id": 0}):
                    prev_stored += float(r.get("spend") or 0)
            except Exception:
                pass

        # 8 — General-ledger entries posted for this date / account
        gl_legs: list[dict] = []
        gl_total_sar = 0.0
        async for e in db.general_ledger.find(
            {
                "user_id": uid,
                "entry_type": "ad_spend",
                "$or": [
                    {"metadata.spend_date": date_str},
                    {"metadata.target_date": date_str},
                    {"posted_at": {
                        "$regex": f"^{date_str}", "$options": "i"}},
                ],
                "$and": [
                    {"$or": [
                        {"entity_id": cp_id},
                        {"metadata.ad_account_id":  ext_id} if ext_id
                            else {"metadata.ad_account_id": "__never__"},
                        {"metadata.account_id":     ext_id} if ext_id
                            else {"metadata.account_id":   "__never__"},
                        {"metadata.counterparty_id": cp_id},
                    ]},
                ],
            },
            {"_id": 0},
        ).sort("posted_at", 1):
            meta = e.get("metadata") or {}
            gl_legs.append({
                "entry_no":     e.get("entry_no"),
                "txn_group_id": e.get("txn_group_id"),
                "posted_at":    e.get("posted_at"),
                "side":         e.get("side"),
                "amount":       e.get("amount"),
                "entity_type":  e.get("entity_type"),
                "entity_id":    e.get("entity_id"),
                "sub_account":  e.get("sub_account"),
                "status":       e.get("status"),
                "period_key":   meta.get("period_key"),
                "spend_native": meta.get("spend_native"),
                "spend_native_currency":
                                meta.get("spend_native_currency"),
                "fx_rate":      meta.get("fx_rate"),
                "fx_source":    meta.get("fx_source"),
                "idempotency_key": meta.get("idempotency_key"),
                "metadata":     meta,
            })
            # Sum spend legs by their SAR amount.  Only the debit leg
            # to expense/COGS is the true spend; credit closes to the
            # ad account liability.
            if e.get("side") == "debit" and (
                e.get("entity_type") in (None, "expense", "ad_spend")
                or "expense" in (e.get("sub_account") or "")
            ):
                gl_total_sar += float(e.get("amount") or 0)

        # If our heuristic didn't catch any debit leg, fall back to
        # summing CREDIT side (against the ad account) which equals
        # spend by accounting identity.
        if not gl_total_sar:
            for leg in gl_legs:
                if leg["side"] == "credit":
                    gl_total_sar += float(leg["amount"] or 0)
            gl_total_sar = -gl_total_sar  # debit value
            gl_total_sar = abs(gl_total_sar)

        # 11 — UI balance (running account balance, what the merchant
        # sees on the ad account page TODAY)
        ui_balance_sar = await _ui_balance(db, uid, cp_id)

        # FX conversion for API_native → SAR
        fx_rate, fx_source = await _fx_rate(
            db, uid, currency or "SAR", date_str)
        api_spend_sar = round(api_spend_native * fx_rate, 2)

        # 10 — Cumulative vs daily classification (derived from
        # window_key on GL legs).
        period_keys = sorted({
            lg.get("period_key") for lg in gl_legs if lg.get("period_key")
        })
        cumulative_or_daily = (
            "windowed (AM/PM/corrections)" if any(
                p and ("AM_" in p or "PM_" in p)
                for p in period_keys
            ) else (
                "daily_cumulative" if gl_legs else "—"
            )
        )

        # Sanity diffs
        delta_api_vs_gl = round(api_spend_sar - gl_total_sar, 2)
        delta_api_vs_ui = round(api_spend_sar - ui_balance_sar, 2)

        return {
            "provider":              provider,
            "internal_account_id":   cp_id,
            "external_account_id":   ext_id,
            "account_name":          name,
            "date_used":             date_str,
            "timezone_used":         "Asia/Riyadh",
            "currency":              currency or "—",
            "fx_rate_used":          fx_rate,
            "fx_source":              fx_source,
            "api": {
                "source_collection": source_used,
                "rows_found":        len(source_docs),
                "spend_native":      round(api_spend_native, 4),
                "spend_native_currency": currency or "—",
                "spend_sar":         api_spend_sar,
                "raw_documents":     source_docs,
            },
            "previous_stored": {
                "date":              prev_date,
                "spend_native":      round(prev_stored, 4),
            },
            "general_ledger": {
                "legs_count":        len(gl_legs),
                "total_sar":         round(gl_total_sar, 2),
                "period_keys":       period_keys,
                "computation_mode":  cumulative_or_daily,
                "legs":              gl_legs,
            },
            "reconciliation": {
                "api_spend_sar":     api_spend_sar,
                "gl_total_sar":      round(gl_total_sar, 2),
                "ui_balance_sar":    round(ui_balance_sar, 2),
                "delta_api_vs_gl":   delta_api_vs_gl,
                "delta_api_vs_ui":   delta_api_vs_ui,
                "delta_gl_vs_ui":    round(gl_total_sar - ui_balance_sar, 2),
            },
        }

    async def _ui_balance(db, uid: str, cp_id: str) -> float:
        """Total liability balance for this ad account counterparty
        (what the dashboard 'الحسابات الإعلانية' page shows)."""
        if not cp_id:
            return 0.0
        try:
            from ledger_core import compute_balance
            bal = await compute_balance(
                db, uid, entity_type="ad_account", entity_id=cp_id)
            return float(bal or 0)
        except Exception:
            # Fallback: aggregate GL directly
            total = 0.0
            async for e in db.general_ledger.find(
                {"user_id": uid,
                 "entity_type": "ad_account",
                 "entity_id":   cp_id,
                 "status":      "posted"},
                {"_id": 0, "side": 1, "amount": 1},
            ):
                amt = float(e.get("amount") or 0)
                total += amt if e.get("side") == "credit" else -amt
            return total

    async def _fx_rate(
        db, uid: str, currency: str, date_str: str,
    ) -> tuple[float, str]:
        currency = (currency or "SAR").upper()
        if currency == "SAR":
            return 1.0, "identity"
        # 1. ads_currency_settings (Iter-236)
        try:
            doc = await db.ads_currency_settings.find_one(
                {"user_id": uid, "currency": currency},
                {"_id": 0, "rate_to_sar": 1, "source": 1,
                 "effective_date": 1},
            )
            if doc and doc.get("rate_to_sar"):
                return (float(doc["rate_to_sar"]),
                        f"ads_currency_settings:{doc.get('source','')}")
        except Exception:
            pass
        # 2. SAMA default
        defaults = {"USD": 3.75, "EUR": 4.10, "GBP": 4.78}
        return defaults.get(currency, 1.0), "default"

    return router
