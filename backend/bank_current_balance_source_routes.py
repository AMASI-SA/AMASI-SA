"""Iter-249d — Bank current_balance SOURCE diagnostic (READ-ONLY).

Goal: Pinpoint EXACTLY which data source(s) feed the displayed
`current_balance` for a given bank account, so we know which mutator
to target before any fix.

Inputs explored:
  • accounts.current_balance                 (raw stored field)
  • account_balance_ssot()                   (canonical UI formula)
  • general_ledger  sub_account=main         (Iter-192 SSOT)
  • general_ledger  sub_account=balance      (BNPL bridge artefact)
  • general_ledger  sub_account=main+balance
  • account_transactions  net (Σ in − Σ out) (legacy walker)
  • financial_movements  net (if collection exists)
  • account_transaction_double_write legs    (Iter-240 helper)

  GET /api/audit/bank-current-balance-source?account_id=<BANK_ID>

100% read-only — no writes, no recomputes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


TOLERANCE = 0.02  # SAR


def _r(n) -> float:
    return round(float(n or 0), 2)


def _matches(a: float, b: float) -> bool:
    return abs(a - b) <= TOLERANCE


def make_bank_current_balance_source_router(db, current_user):
    router = APIRouter(tags=["audit", "bnpl"])

    @router.get("/audit/bank-current-balance-source")
    async def diag(
        account_id: str = Query(..., description="Bank account id"),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # ── 1. Account doc (raw, no enrichment) ────────────────────
        acc = await db.accounts.find_one(
            {"id": account_id, "user_id": uid}, {"_id": 0},
        )
        if not acc:
            raise HTTPException(404, "Account not found.")
        if acc.get("account_type") not in ("bank", "cash"):
            raise HTTPException(
                400,
                "هذا الـ endpoint مخصص للحسابات البنكية/النقدية فقط.",
            )

        current_balance = _r(acc.get("current_balance"))
        opening_balance = _r(acc.get("opening_balance"))
        expected_orders = _r(acc.get("expected_orders_balance"))

        # ── 2. account_balance_ssot() — canonical UI value ────────
        ssot_value: Optional[float] = None
        ssot_error: Optional[str] = None
        try:
            from financial_position_ssot import account_balance_ssot
            ssot_value = _r(await account_balance_ssot(
                db, user_id=uid, account=acc,
            ))
        except Exception as e:  # noqa: BLE001
            ssot_error = repr(e)

        # ── 3. general_ledger aggregations ────────────────────────
        async def _gl_agg(filt: Dict[str, Any]) -> Dict[str, Any]:
            match = {
                "user_id": uid,
                "entity_type": "bank",
                "entity_id": account_id,
                "status": "posted",
                "entry_type": {"$ne": "reversal"},
                "metadata.legacy_orphan": {"$ne": True},
                **filt,
            }
            d = 0.0
            c = 0.0
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
                    d, dn = float(r["total"]), int(r["n"])
                elif r["_id"] == "credit":
                    c, cn = float(r["total"]), int(r["n"])
            return {
                "debits": _r(d), "credits": _r(c),
                "net": _r(d - c),
                "debit_count": dn, "credit_count": cn,
                "row_count": dn + cn,
            }

        gl_main = await _gl_agg({"sub_account": "main"})
        gl_balance = await _gl_agg({"sub_account": "balance"})
        gl_all_sub = await _gl_agg({})

        # ── 4. general_ledger by entry_type (whole entity) ────────
        gl_by_entry_type: Dict[str, Dict[str, Any]] = {}
        async for r in db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid,
                "entity_type": "bank",
                "entity_id": account_id,
                "status": "posted",
                "entry_type": {"$ne": "reversal"},
                "metadata.legacy_orphan": {"$ne": True},
            }},
            {"$group": {
                "_id": {"et": "$entry_type", "sub": "$sub_account"},
                "n": {"$sum": 1},
                "sum_debit": {"$sum": {"$cond": [
                    {"$eq": ["$side", "debit"]}, "$amount", 0]}},
                "sum_credit": {"$sum": {"$cond": [
                    {"$eq": ["$side", "credit"]}, "$amount", 0]}},
            }},
        ]):
            k = f"{r['_id'].get('et')}@{r['_id'].get('sub')}"
            gl_by_entry_type[k] = {
                "count": r["n"],
                "sum_debit": _r(r["sum_debit"]),
                "sum_credit": _r(r["sum_credit"]),
                "net": _r(r["sum_debit"] - r["sum_credit"]),
            }

        # Special: Iter-240 double-write artefacts (these double-count
        # account_transactions in the ledger; SSOT subtracts them).
        dw_d = 0.0
        dw_c = 0.0
        dw_n = 0
        async for leg in db.general_ledger.find(
            {"user_id": uid, "entity_type": "bank",
             "entity_id": account_id, "status": "posted",
             "metadata.source": "account_transaction_double_write"},
            {"_id": 0, "amount": 1, "side": 1},
        ):
            a = float(leg.get("amount") or 0)
            if leg.get("side") == "debit":
                dw_d += a
            else:
                dw_c += a
            dw_n += 1
        double_write = {
            "row_count": dw_n,
            "sum_debit": _r(dw_d),
            "sum_credit": _r(dw_c),
            "net": _r(dw_d - dw_c),
        }

        # ── 5. account_transactions walker (Σ in − Σ out) ─────────
        atx_in = 0.0
        atx_out = 0.0
        atx_count = 0
        atx_by_type: Dict[str, Dict[str, Any]] = {}
        async for r in db.account_transactions.aggregate([
            {"$match": {"user_id": uid, "account_id": account_id}},
            {"$group": {
                "_id": {"t": "$transaction_type",
                        "d": "$direction"},
                "n": {"$sum": 1},
                "sum": {"$sum": "$amount"},
            }},
        ]):
            ttype = r["_id"].get("t") or "<null>"
            direction = r["_id"].get("d") or "<null>"
            s = float(r["sum"])
            n = int(r["n"])
            row = atx_by_type.setdefault(ttype, {
                "in_sum": 0.0, "out_sum": 0.0,
                "in_count": 0, "out_count": 0,
            })
            if direction == "in":
                row["in_sum"] = _r(row["in_sum"] + s)
                row["in_count"] += n
                atx_in += s
            else:
                row["out_sum"] = _r(row["out_sum"] + s)
                row["out_count"] += n
                atx_out += s
            atx_count += n
        atx_net = _r(atx_in - atx_out)
        atx_walker_balance = _r(
            opening_balance + expected_orders + atx_net
        )

        # ── 6. financial_movements walker (if collection exists) ──
        # Same logic as accounts_routes._recompute_balance but for the
        # newer hub. Some accounts only have legacy rows here.
        fm_net: Optional[float] = None
        fm_by_type: Dict[str, Dict[str, Any]] = {}
        fm_count = 0
        try:
            fm_in_total = 0.0
            fm_out_total = 0.0
            async for r in db.financial_movements.aggregate([
                {"$match": {"user_id": uid,
                            "bank_account_id": account_id}},
                {"$group": {
                    "_id": {"t": "$type", "d": "$direction"},
                    "n": {"$sum": 1},
                    "sum": {"$sum": "$amount"},
                }},
            ]):
                ttype = r["_id"].get("t") or "<null>"
                direction = r["_id"].get("d") or "<null>"
                s = float(r["sum"])
                n = int(r["n"])
                row = fm_by_type.setdefault(ttype, {
                    "in_sum": 0.0, "out_sum": 0.0,
                    "in_count": 0, "out_count": 0,
                })
                if direction == "in":
                    row["in_sum"] = _r(row["in_sum"] + s)
                    row["in_count"] += n
                    fm_in_total += s
                else:
                    row["out_sum"] = _r(row["out_sum"] + s)
                    row["out_count"] += n
                    fm_out_total += s
                fm_count += n
            fm_net = _r(fm_in_total - fm_out_total)
        except Exception:  # noqa: BLE001
            fm_net = None

        # ── 7. Reconstruction attempts ────────────────────────────
        # Several plausible formulae for current_balance:
        recon: Dict[str, Dict[str, Any]] = {}

        # 7a — account_transactions walker
        recon["account_transactions_walker"] = {
            "formula": (
                "opening_balance + expected_orders_balance + "
                "Σ(in) − Σ(out)"
            ),
            "value": atx_walker_balance,
            "matches_current_balance": _matches(
                atx_walker_balance, current_balance),
            "matches_ssot": (
                _matches(atx_walker_balance, ssot_value)
                if ssot_value is not None else False
            ),
        }

        # 7b — ledger(main) only
        recon["ledger_main"] = {
            "formula": "Σ debits − Σ credits  on sub_account=main",
            "value": gl_main["net"],
            "matches_current_balance":
                _matches(gl_main["net"], current_balance),
            "matches_ssot": (
                _matches(gl_main["net"], ssot_value)
                if ssot_value is not None else False
            ),
        }

        # 7c — ledger(main+balance)
        recon["ledger_main_plus_balance"] = {
            "formula": (
                "Σ debits − Σ credits  on sub_account "
                "∈ {main, balance}"
            ),
            "value": gl_all_sub["net"],
            "matches_current_balance":
                _matches(gl_all_sub["net"], current_balance),
            "matches_ssot": (
                _matches(gl_all_sub["net"], ssot_value)
                if ssot_value is not None else False
            ),
        }

        # 7d — SSOT formula reconstructed manually:
        # If no opening_balance row in ledger:
        #   ssot = ledger_main + (current_balance − double_write_net)
        # If opening_balance exists:
        #   ssot = ledger_main
        has_opening_in_ledger = await db.general_ledger.find_one(
            {"user_id": uid, "entity_type": "bank",
             "entity_id": account_id,
             "entry_type": "opening_balance",
             "status": "posted"},
            {"_id": 1},
        )
        if has_opening_in_ledger:
            ssot_recon = gl_main["net"]
            ssot_formula = (
                "ledger_main (opening_balance row present → no add)"
            )
        else:
            ssot_recon = _r(
                gl_main["net"]
                + (current_balance - double_write["net"])
            )
            ssot_formula = (
                "ledger_main + (current_balance − "
                "Σ double_write_legs net)"
            )
        recon["ssot_reconstructed"] = {
            "formula": ssot_formula,
            "value": ssot_recon,
            "matches_ssot_endpoint": (
                _matches(ssot_recon, ssot_value)
                if ssot_value is not None else False
            ),
            "matches_current_balance":
                _matches(ssot_recon, current_balance),
            "has_opening_in_ledger": bool(has_opening_in_ledger),
        }

        # 7e — hybrid: ledger + atx without double-counting
        # current_balance was probably set by `_recompute_balance` from
        # account_transactions ONLY, so compare with atx walker.
        recon["hybrid_ledger_main_plus_account_transactions"] = {
            "formula": "ledger_main + account_transactions_walker",
            "value": _r(gl_main["net"] + atx_walker_balance),
            "matches_current_balance": _matches(
                gl_main["net"] + atx_walker_balance, current_balance),
        }

        # ── 8. inferred_source ────────────────────────────────────
        evidence: List[str] = []
        inferred: str
        if _matches(atx_walker_balance, current_balance):
            inferred = "current_balance_matches_account_transactions"
            evidence.append(
                "حقل accounts.current_balance يساوي ناتج walker "
                "account_transactions (opening + expected_orders + "
                f"Σ in − Σ out = {atx_walker_balance}). هذا يعني "
                "أن _recompute_balance في accounts_routes.py هو "
                "المُغذّي الفعلي للحقل، وليس universal ledger."
            )
        elif _matches(gl_main["net"], current_balance):
            inferred = "current_balance_matches_general_ledger"
            evidence.append(
                "current_balance == ledger(main). المصدر هو "
                "universal ledger مباشرة."
            )
        elif _matches(
                gl_main["net"] + atx_walker_balance, current_balance):
            inferred = (
                "current_balance_matches_hybrid_ledger_plus_"
                "account_transactions"
            )
            evidence.append(
                "current_balance == ledger(main) + atx_walker. "
                "احتساب هجين — كلا المصدرين يُضافان."
            )
        else:
            # Check if current_balance is stale (matches NEITHER live
            # source). Tolerate fm_net mismatches.
            inferred = "needs_manual_review"
            evidence.append(
                "current_balance لا يطابق أياً من المصادر الحية: "
                f"atx_walker={atx_walker_balance}, "
                f"ledger_main={gl_main['net']}, "
                f"ledger_main+balance={gl_all_sub['net']}, "
                f"hybrid={_r(gl_main['net'] + atx_walker_balance)}. "
                "إما أنه قديم/stale أو يأتي من مسار غير مُغطّى "
                "في هذا التشخيص."
            )

        # If SSOT mismatches the stored current_balance, flag it:
        ssot_mismatch_note: Optional[str] = None
        if ssot_value is not None \
                and not _matches(ssot_value, current_balance):
            ssot_mismatch_note = (
                f"ssot_endpoint={ssot_value} ≠ "
                f"accounts.current_balance={current_balance}. "
                "الواجهة تعرض ssot_endpoint (راجع "
                "_account_with_meta السطر 478)، لذلك الرقم الذي "
                "تراه في الواجهة قد يختلف عن المخزّن في DB."
            )

        return {
            "ok": True,
            "iter": "iter249d",
            "read_only": True,
            "account": {
                "id": acc.get("id"),
                "name": acc.get("name"),
                "account_type": acc.get("account_type"),
                "currency": acc.get("currency"),
                "auto_created": acc.get("auto_created"),
                "status": acc.get("status"),
                "opening_balance": opening_balance,
                "opening_balance_date":
                    acc.get("opening_balance_date"),
                "expected_orders_balance": expected_orders,
            },
            "values": {
                "accounts_current_balance": current_balance,
                "account_balance_ssot_endpoint": ssot_value,
                "account_balance_ssot_error": ssot_error,
                "ledger_main_net": gl_main["net"],
                "ledger_balance_net": gl_balance["net"],
                "ledger_main_plus_balance_net": gl_all_sub["net"],
                "account_transactions_walker": atx_walker_balance,
                "financial_movements_net": fm_net,
            },
            "ssot_mismatch_note": ssot_mismatch_note,
            "general_ledger": {
                "sub_account_main": gl_main,
                "sub_account_balance": gl_balance,
                "sub_account_main_plus_balance": gl_all_sub,
                "by_entry_type_and_sub_account": gl_by_entry_type,
                "iter240_double_write_legs": double_write,
                "has_opening_balance_in_ledger":
                    bool(has_opening_in_ledger),
            },
            "account_transactions": {
                "total_count": atx_count,
                "sum_in": _r(atx_in),
                "sum_out": _r(atx_out),
                "net": atx_net,
                "by_transaction_type": atx_by_type,
            },
            "financial_movements": {
                "total_count": fm_count,
                "net": fm_net,
                "by_type": fm_by_type,
            },
            "reconstruction_attempts": recon,
            "inferred_source": inferred,
            "evidence": evidence,
            "next_step_hint": {
                "current_balance_matches_account_transactions": (
                    "حقل current_balance مُولَّد من "
                    "_recompute_balance(account_transactions). "
                    "أي إصلاح يجب أن يضمن وجود سطر "
                    "account_transactions لتسوية BNPL — وهو "
                    "موجود بعد Iter-248. لكن واجهة كشف الحساب "
                    "تقرأ من general_ledger، لذلك الحل المنطقي: "
                    "إما توحيد UI ليقرأ من account_transactions "
                    "(تراجع عن Iter-198) أو ضمان كتابة bnpl "
                    "في ledger بـ sub_account='main'."
                ),
                "current_balance_matches_general_ledger": (
                    "current_balance مُغذّى من ledger مباشرة — "
                    "إذن المشكلة في فلتر UI فقط. وسّع فلتر "
                    "_ledger_based_tx_feed."
                ),
                "current_balance_matches_hybrid_ledger_plus_"
                "account_transactions": (
                    "احتساب مزدوج — يحتاج تنظيف شامل قبل أي fix."
                ),
                "needs_manual_review": (
                    "لا تنفّذ أي إصلاح. شارك JSON هذا للتحقيق "
                    "العميق. على الأرجح حقل current_balance "
                    "مُجمّد (stale) أو يأتي من سكربت قديم."
                ),
            }.get(inferred),
        }

    return router
