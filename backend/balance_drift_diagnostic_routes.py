"""Iter-250b · P1.5 — Balance Drift Diagnostic (READ-ONLY).

Compares for every bank / cash / payment_platform account:

  • stored_current_balance       (accounts.current_balance — raw doc field)
  • ledger_main_net              (Σ debit − Σ credit on sub_account=main)
  • ledger_balance_net           (Σ debit − Σ credit on sub_account=balance)
  • ledger_main_plus_balance     (sum of both)
  • ssot_value                   (account_balance_ssot() — canonical UI value)
  • account_transactions_walk    (legacy: Σ in − Σ out)
  • displayed_balance            (= ssot_value after _account_with_meta)
  • feed_visible_tx_count        (rows that /accounts/:id/transactions shows)
  • feed_hidden_tx_count         (rows in ledger sub=balance — hidden by UI filter)

Detects:
  • drift_ssot_vs_stored
  • drift_ssot_vs_walk
  • drift_ledger_main_vs_displayed
  • ITER249_BNPL_HIDDEN flag      (when sub=balance has BNPL entries hidden)

STRICT READ-ONLY · NO writes · NO recomputes · NO migrations.

Endpoint:
  GET /api/diagnostics/balance-drift
    ?account_id=<optional>
    &account_type=bank|cash|payment_platform|all (default=all)
    &include_zero_drift=false
    &tolerance=0.02
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_balance_drift_diagnostic_router(db, current_user):
    router = APIRouter(tags=["diagnostics", "balance-drift"])

    async def _gl_net(
        uid: str, entity_id: str, sub_account: Optional[str] = None,
    ) -> Dict[str, Any]:
        match = {
            "user_id": uid,
            "entity_type": "bank",
            "entity_id": entity_id,
            "status": "posted",
            "entry_type": {"$ne": "reversal"},
            "metadata.legacy_orphan": {"$ne": True},
        }
        if sub_account is not None:
            match["sub_account"] = sub_account

        debit = 0.0
        credit = 0.0
        dn = 0
        cn = 0
        async for r in db.general_ledger.aggregate([
            {"$match": match},
            {"$group": {
                "_id": "$side",
                "total": {"$sum": "$amount"},
                "n": {"$sum": 1},
            }},
        ]):
            if r["_id"] == "debit":
                debit = float(r["total"])
                dn = int(r["n"])
            elif r["_id"] == "credit":
                credit = float(r["total"])
                cn = int(r["n"])

        return {
            "debits": _r(debit), "credits": _r(credit),
            "net": _r(debit - credit),
            "debit_count": dn, "credit_count": cn,
            "row_count": dn + cn,
        }

    async def _account_tx_walk(uid: str, account_id: str) -> Dict[str, Any]:
        inc = 0.0
        out = 0.0
        n_in = 0
        n_out = 0
        async for r in db.account_transactions.aggregate([
            {"$match": {"user_id": uid, "account_id": account_id}},
            {"$group": {
                "_id": "$direction",
                "total": {"$sum": "$amount"},
                "n": {"$sum": 1},
            }},
        ]):
            if r["_id"] == "in":
                inc = float(r["total"])
                n_in = int(r["n"])
            elif r["_id"] == "out":
                out = float(r["total"])
                n_out = int(r["n"])
        return {
            "in_total": _r(inc), "out_total": _r(out),
            "net": _r(inc - out),
            "in_count": n_in, "out_count": n_out,
            "row_count": n_in + n_out,
        }

    async def _bnpl_hidden_count(uid: str, account_id: str) -> Dict[str, Any]:
        """Count rows in ledger that are entity_type=bank but
        sub_account != 'main' (e.g. BNPL bridge writes).
        These are the rows hidden by the current UI filter.
        """
        cnt = 0
        amt = 0.0
        async for r in db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid,
                "entity_type": "bank",
                "entity_id": account_id,
                "status": "posted",
                "sub_account": {"$ne": "main"},
                "entry_type": {"$ne": "reversal"},
                "metadata.legacy_orphan": {"$ne": True},
            }},
            {"$group": {
                "_id": "$sub_account",
                "n": {"$sum": 1},
                "debits": {"$sum": {"$cond": [
                    {"$eq": ["$side", "debit"]}, "$amount", 0]}},
                "credits": {"$sum": {"$cond": [
                    {"$eq": ["$side", "credit"]}, "$amount", 0]}},
            }},
        ]):
            cnt += int(r["n"])
            amt += float(r["debits"]) - float(r["credits"])
        return {"hidden_rows": cnt, "hidden_net_amount": _r(amt)}

    async def _bnpl_entry_types_present(
        uid: str, account_id: str,
    ) -> List[str]:
        out = set()
        async for r in db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid,
                "entity_type": "bank",
                "entity_id": account_id,
                "status": "posted",
                "sub_account": "balance",
            }},
            {"$group": {"_id": "$entry_type"}},
        ]):
            if r["_id"]:
                out.add(str(r["_id"]))
        return sorted(out)

    @router.get("/diagnostics/balance-drift")
    async def balance_drift(
        account_id: Optional[str] = Query(None),
        account_type: str = Query("all"),
        include_zero_drift: bool = Query(False),
        tolerance: float = Query(0.02, ge=0.0, le=10.0),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # ── 1. Pick the account scope ──────────────────────────────
        match: Dict[str, Any] = {"user_id": uid}
        if account_id:
            match["id"] = account_id
        else:
            allowed = {"bank", "cash", "payment_platform"}
            if account_type != "all":
                if account_type not in allowed:
                    return {
                        "ok": False,
                        "error": (
                            f"account_type must be one of {allowed} or 'all'."
                        ),
                    }
                match["account_type"] = account_type
            else:
                match["account_type"] = {"$in": list(allowed)}

        # ── 2. Try to import the SSOT helper ───────────────────────
        account_balance_ssot = None
        ssot_import_error: Optional[str] = None
        try:
            from financial_position_ssot import (
                account_balance_ssot as _ssot,
            )
            account_balance_ssot = _ssot
        except Exception as e:  # noqa: BLE001
            ssot_import_error = repr(e)

        # ── 3. Walk accounts ───────────────────────────────────────
        rows: List[Dict[str, Any]] = []
        summary = {
            "total_accounts": 0,
            "ok": 0,
            "drift": 0,
            "iter249_bnpl_hidden": 0,
            "total_hidden_amount": 0.0,
            "by_account_type": {},
        }

        async for acc in db.accounts.find(match, {"_id": 0}).sort("name", 1):
            atype = acc.get("account_type")
            stored = _r(acc.get("current_balance"))
            opening = _r(acc.get("opening_balance"))
            expected = _r(acc.get("expected_orders_balance"))

            gl_main = await _gl_net(uid, acc["id"], "main")
            gl_balance = await _gl_net(uid, acc["id"], "balance")
            gl_all = await _gl_net(uid, acc["id"], None)

            walk = await _account_tx_walk(uid, acc["id"])
            hidden = await _bnpl_hidden_count(uid, acc["id"])
            bnpl_types = await _bnpl_entry_types_present(uid, acc["id"])

            # SSOT (canonical UI value)
            ssot_value: Optional[float] = None
            ssot_error: Optional[str] = None
            if account_balance_ssot is not None:
                try:
                    ssot_value = _r(
                        await account_balance_ssot(
                            db, user_id=uid, account=acc,
                        )
                    )
                except Exception as e:  # noqa: BLE001
                    ssot_error = repr(e)

            displayed = ssot_value if ssot_value is not None else stored

            # Drifts
            drift_ssot_vs_stored = (
                _r((ssot_value or 0) - stored)
                if ssot_value is not None else None
            )
            drift_ssot_vs_walk = (
                _r((ssot_value or 0) - walk["net"])
                if ssot_value is not None else None
            )
            # Difference between what UI feed visualises (sub=main) and
            # what /accounts/:id displays in the headline (ssot/displayed).
            # Positive means: the headline includes something the feed misses.
            drift_ledger_main_vs_displayed = (
                _r(displayed - (gl_main["net"] + opening
                                if False else gl_main["net"]))
            )

            # Iter-249 specific: sub_account != "main" rows on a bank
            # ⇒ those WOULD be hidden by the current feed filter.
            has_bnpl_drift = (
                hidden["hidden_rows"] > 0
                and any(t.startswith("bnpl_") for t in bnpl_types)
            )

            # Status
            ok_ssot = (
                drift_ssot_vs_stored is not None
                and abs(drift_ssot_vs_stored) <= tolerance
            )
            if has_bnpl_drift:
                status = "ITER249_BNPL_HIDDEN"
                summary["iter249_bnpl_hidden"] += 1
                summary["total_hidden_amount"] += hidden["hidden_net_amount"]
            elif ok_ssot:
                status = "ok"
                summary["ok"] += 1
            else:
                status = "drift"
                summary["drift"] += 1

            row = {
                "id": acc["id"],
                "name": acc.get("name") or "(بدون اسم)",
                "account_type": atype,
                "provider_name": acc.get("provider_name"),
                "status_field": acc.get("status"),
                # balances
                "stored_current_balance": stored,
                "opening_balance": opening,
                "expected_orders_balance": expected,
                "ledger_main_net": gl_main["net"],
                "ledger_balance_net": gl_balance["net"],
                "ledger_main_plus_balance": _r(
                    gl_main["net"] + gl_balance["net"]),
                "ledger_all_sub_net": gl_all["net"],
                "ssot_value": ssot_value,
                "ssot_error": ssot_error,
                "account_transactions_walk": walk["net"],
                "displayed_balance": displayed,
                # row counts
                "ledger_main_row_count": gl_main["row_count"],
                "ledger_balance_row_count": gl_balance["row_count"],
                "ledger_all_row_count": gl_all["row_count"],
                "account_transactions_row_count": walk["row_count"],
                # iter-249 specific
                "feed_visible_tx_count": gl_main["row_count"],
                "feed_hidden_tx_count": hidden["hidden_rows"],
                "feed_hidden_net_amount": hidden["hidden_net_amount"],
                "feed_hidden_sub_account_entry_types": bnpl_types,
                # drifts
                "drift_ssot_vs_stored": drift_ssot_vs_stored,
                "drift_ssot_vs_walk": drift_ssot_vs_walk,
                "drift_ledger_main_vs_displayed":
                    drift_ledger_main_vs_displayed,
                "has_bnpl_drift": has_bnpl_drift,
                "status": status,
            }

            summary["total_accounts"] += 1
            summary["by_account_type"].setdefault(
                atype, {"count": 0, "ok": 0, "drift": 0,
                        "iter249_bnpl_hidden": 0})
            summary["by_account_type"][atype]["count"] += 1
            if status == "ok":
                summary["by_account_type"][atype]["ok"] += 1
            elif status == "drift":
                summary["by_account_type"][atype]["drift"] += 1
            else:
                summary["by_account_type"][atype][
                    "iter249_bnpl_hidden"] += 1

            if status == "ok" and not include_zero_drift:
                # Skip emitting zero-drift rows unless explicitly asked.
                continue

            rows.append(row)

        summary["total_hidden_amount"] = _r(summary["total_hidden_amount"])

        return {
            "ok": True,
            "iter": "iter250b_p1_5",
            "generated_at": _now_iso(),
            "tolerance": tolerance,
            "filters": {
                "account_id": account_id,
                "account_type": account_type,
                "include_zero_drift": include_zero_drift,
            },
            "ssot_import_error": ssot_import_error,
            "accounts": rows,
            "summary": summary,
            "notes": [
                "READ-ONLY diagnostic — no DB writes performed.",
                "Iter-249 flag is raised when sub_account != 'main' rows "
                "exist on a bank entity AND any entry_type starts with "
                "'bnpl_'. These rows are CURRENTLY HIDDEN from "
                "/accounts/:id/transactions because the UI filter uses "
                "sub_account='main' only.",
                "drift_ssot_vs_stored within tolerance = SSOT matches "
                "cached current_balance (healthy).",
                "drift_ledger_main_vs_displayed shows how much the "
                "feed under-represents vs the headline.",
            ],
        }

    return router


__all__ = ["make_balance_drift_diagnostic_router"]
