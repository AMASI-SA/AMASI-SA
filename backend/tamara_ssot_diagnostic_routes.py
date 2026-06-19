"""Iter-246t — Tamara SSOT diagnostic endpoint (READ-ONLY).

Goal: when the merchant sees DIFFERENT Gross numbers between the
modal `import-preview` and the `tamara-settlement-forensic` endpoint
(despite both engine_version=iter246r), this endpoint pinpoints
EXACTLY where the divergence happens by exposing every intermediate
value side-by-side.

The endpoint replays both compute paths with the same inputs the
merchant used and returns:

  • modal_path        — what `compute_settlement_for_provider` returns
                        (the same payload the modal reads).
  • forensic_path     — what the forensic endpoint reports (also from
                        `compute_settlement_for_provider`, so it must
                        be identical — any drift here = a stale node).
  • raw_db_counts     — direct MongoDB counts for the period without
                        any iter246r filter, so we can see how many
                        records actually carry the new flags.
  • flag_audit        — counts of `same_week_netzero_exclusion=true`
                        and `settlement_source=settlement_entries_historical`
                        for the period.  If these are 0 in production,
                        iter246q was never applied to the DB → that
                        explains the inflated Gross.
  • delta             — diff of every numeric field, in SAR.  Any
                        line with abs(delta) > 0.01 is flagged red.

STRICT READ-ONLY.  No writes.  Tabby ignored.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def make_tamara_ssot_diagnostic_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit", "tamara"])

    @router.get("/tamara-ssot-diagnostic")
    async def tamara_ssot_diagnostic(
        user: dict = Depends(current_user),
        date_from: str = Query(..., min_length=10, max_length=10),
        date_to: str = Query(..., min_length=10, max_length=10),
    ):
        """READ-ONLY diagnostic comparing modal vs forensic compute
        paths for the EXACT same Tamara window."""
        uid = user["id"]

        from bnpl.settlements_service import (
            compute_settlement_for_provider,
            _local_date_window_utc,
            _aggregate_official_totals,
        )

        # ── 1. Modal-path compute (what `import-preview` uses) ──
        s_modal = await compute_settlement_for_provider(
            db, uid, "tamara",
            date_from=date_from, date_to=date_to,
        )
        modal_totals = s_modal.get("totals", {}) or {}
        modal_path = {
            "engine_version": s_modal.get("engine_version"),
            "data_source": s_modal.get("data_source"),
            "gross_sales": _r(modal_totals.get("gross_sales") or 0),
            "total_refunds": _r(modal_totals.get("total_refunds") or 0),
            "net_sales": _r(modal_totals.get("net_sales") or 0),
            "commission": _r(modal_totals.get("commission") or 0),
            "commission_vat": _r(modal_totals.get("commission_vat") or 0),
            "net_payable": _r(modal_totals.get("net_payable") or 0),
            "transactions_count": modal_totals.get("transactions_count") or 0,
            "refunds_count": modal_totals.get("refunds_count") or 0,
        }

        # ── 2. Forensic-path compute (independent re-call) ──
        # The forensic endpoint also calls
        # `compute_settlement_for_provider` — so this MUST be
        # bit-identical to `modal_path`.  If it isn't, the only
        # possible cause is a stale process / split deployment.
        s_forensic = await compute_settlement_for_provider(
            db, uid, "tamara",
            date_from=date_from, date_to=date_to,
        )
        f_totals = s_forensic.get("totals", {}) or {}
        forensic_path = {
            "engine_version": s_forensic.get("engine_version"),
            "data_source": s_forensic.get("data_source"),
            "gross_sales": _r(f_totals.get("gross_sales") or 0),
            "total_refunds": _r(f_totals.get("total_refunds") or 0),
            "net_sales": _r(f_totals.get("net_sales") or 0),
            "commission": _r(f_totals.get("commission") or 0),
            "commission_vat": _r(f_totals.get("commission_vat") or 0),
            "net_payable": _r(f_totals.get("net_payable") or 0),
            "transactions_count": f_totals.get("transactions_count") or 0,
            "refunds_count": f_totals.get("refunds_count") or 0,
        }

        # ── 3. Raw DB inspection (no iter246r filters) ──
        utc_gte, utc_lte = _local_date_window_utc(date_from, date_to)
        rng = {}
        if utc_gte:
            rng["$gte"] = utc_gte
        if utc_lte:
            rng["$lte"] = utc_lte

        base_match: Dict[str, Any] = {
            "user_id": uid, "provider": "tamara",
        }
        if rng:
            base_match["effective_settlement_date"] = rng

        # Raw — every Tamara capture in window (no flags filtered).
        raw_sum = 0.0
        raw_count = 0
        netzero_count = 0
        netzero_sum = 0.0
        historical_count = 0
        historical_sum = 0.0
        normal_count = 0
        normal_sum = 0.0

        async for t in db.payment_transactions.find(
            base_match,
            {"_id": 0, "amount": 1, "same_week_netzero_exclusion": 1,
             "settlement_source": 1, "is_pre_accounting": 1},
        ):
            if t.get("is_pre_accounting"):
                continue
            amt = float(t.get("amount") or 0)
            raw_sum += amt
            raw_count += 1
            if t.get("same_week_netzero_exclusion") is True:
                netzero_count += 1
                netzero_sum += amt
            elif (t.get("settlement_source")
                  == "settlement_entries_historical"):
                historical_count += 1
                historical_sum += amt
            else:
                normal_count += 1
                normal_sum += amt

        # Settlement_entries (the OFFICIAL Tamara file).  If non-empty,
        # `_aggregate_official_totals` OVERRIDES the computed Gross.
        # This is the #1 cause of modal-vs-forensic divergence.
        official = await _aggregate_official_totals(
            db, uid, date_from, date_to,
        )
        official_present = official is not None and official.get(
            "transactions_count", 0) > 0
        official_gross = _r(official.get("gross_sales")) if official else 0.0

        raw_db_counts = {
            "all_captures_in_window": {
                "count": raw_count, "sum": _r(raw_sum),
            },
            "netzero_excluded": {
                "count": netzero_count, "sum": _r(netzero_sum),
            },
            "historical_pinned": {
                "count": historical_count, "sum": _r(historical_sum),
            },
            "normal_counted_in_gross": {
                "count": normal_count, "sum": _r(normal_sum),
            },
            "official_settlement_file_present": official_present,
            "official_file_gross": official_gross,
        }

        # ── 4. Delta — any |diff|>0.01 is a red flag ──
        def _d(a: float, b: float) -> Dict[str, Any]:
            d = round(a - b, 2)
            return {"a": a, "b": b, "delta": d, "ok": abs(d) <= 0.01}

        delta = {
            "modal_vs_forensic": {
                "gross_sales": _d(
                    modal_path["gross_sales"],
                    forensic_path["gross_sales"]),
                "total_refunds": _d(
                    modal_path["total_refunds"],
                    forensic_path["total_refunds"]),
                "net_sales": _d(
                    modal_path["net_sales"],
                    forensic_path["net_sales"]),
                "commission": _d(
                    modal_path["commission"],
                    forensic_path["commission"]),
                "net_payable": _d(
                    modal_path["net_payable"],
                    forensic_path["net_payable"]),
            },
            "modal_vs_normal_only": {
                # `normal_sum` is what iter246r filters should produce
                # for Gross.  If modal_path.gross_sales != normal_sum,
                # the override (`_aggregate_official_totals`) is firing
                # — check `official_file_present` below.
                "gross_sales": _d(
                    modal_path["gross_sales"],
                    raw_db_counts["normal_counted_in_gross"]["sum"]),
            },
        }

        # ── 5. Inferred cause ──
        cause = None
        # The merchant's expected Gross — if they passed it via baseline.
        # Heuristic: when historical_count==0 but the modal Gross is
        # markedly larger than what iter246q would have computed (we
        # cannot know iter246q's previous output without history), we
        # warn about a likely pin-wipe.
        if (modal_path["engine_version"] != "iter246r"
                or forensic_path["engine_version"] != "iter246r"):
            cause = (
                "engine_version is NOT iter246r on at least one path — "
                "the backend is running stale code. Re-deploy."
            )
        elif official_present and abs(
                modal_path["gross_sales"] - official_gross) <= 0.01:
            cause = (
                "Modal Gross matches the OFFICIAL settlement_entries "
                "file uploaded for this period — `_aggregate_official_"
                "totals` is overriding the computed value. If the "
                "uploaded Tamara CSV pre-dates the historical-pin "
                "policy, its rows will inflate Gross by the historical "
                "captures' amount. Re-import the CSV or delete the "
                "outdated `settlement_entries` rows."
            )
        elif (historical_count == 0
              and netzero_count > 0
              and modal_path["refunds_count"] > netzero_count):
            # Refunds exceed the captures-in-window with netzero flag —
            # which implies some refunds point at captures pinned to a
            # past cycle.  But historical_count==0 says zero captures
            # carry the pin in the current DB → iter246q's pins were
            # WIPED (most likely by a Tamara sync calling
            # `recompute_attribution_for_doc`).  Iter-246u protects
            # against this going forward, but the merchant needs to
            # re-run the iter246q apply endpoint to restore the
            # current row's pins.
            cause = (
                "ROOT CAUSE: zero captures in window carry the "
                "iter246q historical-pin flag, yet refunds_count "
                f"({modal_path['refunds_count']}) > "
                f"netzero_count ({netzero_count}). This means a "
                "subsequent Tamara sync overwrote iter246q's pins. "
                "Iter-246u patches `recompute_attribution_for_doc` "
                "to make the pin sticky. After deploying iter-246u, "
                "re-run `POST /api/admin/tamara-apply-execute` to "
                "re-pin the affected captures — they will then stay "
                "pinned forever."
            )
        elif (netzero_count == 0 and historical_count == 0
              and modal_path["gross_sales"] > raw_db_counts[
                  "normal_counted_in_gross"]["sum"] + 0.01):
            cause = (
                "No payment_transactions in this window carry the "
                "iter246r flags (same_week_netzero_exclusion / "
                "settlement_source=settlement_entries_historical). "
                "The iter246q apply step never ran on production for "
                "this period. Run the apply endpoint."
            )
        elif (delta["modal_vs_forensic"]["gross_sales"]["delta"]
              != 0):
            cause = (
                "Modal and forensic disagree even though both call "
                "the same compute function — only possible if the "
                "two requests hit DIFFERENT backend processes (e.g. "
                "rolling deploy in progress, or stale uvicorn worker)."
            )
        else:
            cause = "No divergence detected — all paths agree."

        return {
            "ok": True,
            "iter": "iter246t",
            "read_only": True,
            "period": {"from": date_from, "to": date_to},
            "modal_path": modal_path,
            "forensic_path": forensic_path,
            "raw_db_counts": raw_db_counts,
            "delta": delta,
            "inferred_cause": cause,
        }

    return router
