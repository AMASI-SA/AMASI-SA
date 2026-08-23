"""Iter-217 — SSOT financial position helpers.

Computes the merchant's financial position **strictly** from
`general_ledger` (with one documented fallback for accounts that have
ZERO ledger activity — they keep using `accounts.current_balance`,
mirroring the Iter-192 rule).

Public entry point:
    `compute_financial_position(db, user_id)` →
        same shape the legacy /financial-position page expects:
            assets, liabilities, totals, salary_breakdown,
            by_ad_provider, payment_platforms_remaining, source

Subcomputations are exposed so tests (Phase C) can assert each block
in isolation.
"""
from __future__ import annotations

from typing import Any

from employee_payroll_status import employee_salary_rows
from ledger_core import compute_balance
from tz_utils import riyadh_today


# ── small helper: aggregate ledger by (entity_type, sub_account) ─────
async def _group_by_subaccount(db, user_id: str) -> dict:
    """Returns `{(entity_type, sub_account): net_debit_minus_credit}`.

    Single pipeline pass — used by every higher-level helper below.
    """
    pipeline = [
        {"$match": {"user_id": user_id, "status": "posted",
                     "entry_type": {"$ne": "reversal"},
                     "metadata.legacy_orphan": {"$ne": True}}},
        {"$group": {
            "_id": {"entity_type": "$entity_type",
                     "sub_account": "$sub_account"},
            "debits": {"$sum": {"$cond": [
                {"$eq": ["$side", "debit"]}, "$amount", 0]}},
            "credits": {"$sum": {"$cond": [
                {"$eq": ["$side", "credit"]}, "$amount", 0]}},
        }},
    ]
    out: dict[tuple[str, str | None], float] = {}
    async for r in db.general_ledger.aggregate(pipeline):
        key = (r["_id"]["entity_type"], r["_id"].get("sub_account"))
        out[key] = round(float(r["debits"]) - float(r["credits"]), 2)
    return out


# ── per-entity net ledger (used to build per-employee salary table) ──
async def _group_by_entity(
    db, user_id: str, entity_type: str,
) -> dict[tuple[str, str | None], float]:
    """Returns `{(entity_id, sub_account): net}` for one entity_type."""
    pipeline = [
        {"$match": {"user_id": user_id, "status": "posted",
                     "entity_type": entity_type,
                     "entry_type": {"$ne": "reversal"},
                     "metadata.legacy_orphan": {"$ne": True}}},
        {"$group": {
            "_id": {"entity_id": "$entity_id",
                     "sub_account": "$sub_account"},
            "debits": {"$sum": {"$cond": [
                {"$eq": ["$side", "debit"]}, "$amount", 0]}},
            "credits": {"$sum": {"$cond": [
                {"$eq": ["$side", "credit"]}, "$amount", 0]}},
        }},
    ]
    out: dict = {}
    async for r in db.general_ledger.aggregate(pipeline):
        key = (r["_id"]["entity_id"], r["_id"].get("sub_account"))
        out[key] = round(float(r["debits"]) - float(r["credits"]), 2)
    return out


# ── account-level balance with SSOT fallback rule ────────────────────
async def account_balance_ssot(
    db, *, user_id: str, account: dict,
) -> float:
    """Returns the live balance for ONE account using the SSOT rule:

        IF the account has any general_ledger activity →
            balance = ledger_net (treats current_balance as the
            implicit pre-ledger opening; if an opening_balance entry
            already exists in the ledger, it already captures the
            historical balance, so no double-count).
        ELSE  → balance = accounts.current_balance (legacy fallback).

    BNPL accounts use the canonical BNPL formula irrespective of
    ledger state (same rule as Iter-118/119; matches the per-row
    balance shown on /accounts and /bnpl-settlements).
    """
    try:
        from bnpl.balance_service import (  # noqa: WPS433
            get_bnpl_provider_balance, is_bnpl_account,
        )
        bnpl_provider = is_bnpl_account(account)
        if bnpl_provider:
            canon = await get_bnpl_provider_balance(
                db, user_id, bnpl_provider,
            )
            return float(canon["balance"])
    except Exception:  # noqa: BLE001
        pass

    # Has ANY ledger activity for this bank/cash/platform account?
    activity = await db.general_ledger.find_one(
        {"user_id": user_id, "entity_type": "bank",
         "entity_id": account["id"], "status": "posted"},
        {"_id": 1, "entry_type": 1},
    )
    if activity:
        bal = await compute_balance(
            db, user_id=user_id, entity_type="bank",
            entity_id=account["id"], sub_account="main",
        )
        net = round(float(bal.get("net_balance") or 0), 2)
        # If the account also has a `current_balance` from pre-Iter-192
        # times and NO `opening_balance` row in the ledger, the ledger
        # net is "incremental" — add current_balance as the implicit
        # opening (matches Iter-192 semantics).
        opening = await db.general_ledger.find_one(
            {"user_id": user_id, "entity_type": "bank",
             "entity_id": account["id"],
             "entry_type": "opening_balance",
             "status": "posted"},
            {"_id": 1},
        )
        if not opening:
            # Iter-240 — `accounts.current_balance` already reflects every
            # manual account_transaction (transfers, expenses, liability
            # payments, shipping payments, ad topups) because
            # `_recompute_balance` re-derives it from account_transactions.
            # The Iter-240 double-write helper also posts those same
            # movements into general_ledger as a bank leg, so if we
            # blindly add `current_balance` we double-count them. Net
            # them out so the SSOT view matches current_balance until a
            # real opening_balance entry is seeded.
            dw_net = 0.0
            async for leg in db.general_ledger.find(
                {"user_id": user_id, "entity_type": "bank",
                 "entity_id": account["id"], "status": "posted",
                 "metadata.source": "account_transaction_double_write"},
                {"_id": 0, "amount": 1, "side": 1},
            ):
                amt = float(leg.get("amount") or 0)
                dw_net += amt if leg.get("side") == "debit" else -amt
            net += round(
                float(account.get("current_balance") or 0) - dw_net, 2
            )
        return round(net, 2)

    # No ledger activity at all → legacy fallback.
    return round(float(account.get("current_balance") or 0), 2)


# ── salary breakdown (ledger-driven) ─────────────────────────────────
async def salary_breakdown_ssot(db, user_id: str) -> dict:
    """Per-employee salary breakdown from Ledger + Employee OS V2 contracts."""
    from liabilities_routes import _compute_employee_accrual

    employee_ledger = await _group_by_entity(db, user_id, "employee")

    today = riyadh_today()
    employees: list[dict] = []
    accrued_total = 0.0
    paid_total = 0.0
    advances_total = 0.0
    active_count = 0
    suspended_count = 0

    for emp in await employee_salary_rows(db, user_id):
        emp_id = emp.get("id")
        calc = _compute_employee_accrual(emp, today=today)
        accrued = round(float(calc.get("accrued") or 0), 2)
        accrued_total += accrued
        if calc.get("is_active"):
            active_count += 1
        else:
            suspended_count += 1

        # Salary payable net (debits − credits). In a healthy ledger
        # this equals (paid − accrued_posted). Since accrual is
        # currently calendar-derived (not always posted), we read
        # the PAID side directly from debits.
        salary_payable_net = employee_ledger.get(
            (emp_id, "salary_payable"), 0.0,
        )
        # Outstanding advance = net debit balance on advance sub.
        advance_net = max(
            0.0, employee_ledger.get((emp_id, "advance"), 0.0),
        )
        # paid_total per employee = positive contributions to ledger
        # by salary-payment txns. We approximate as -salary_payable_net
        # when negative (i.e., debits > credits) means we've paid more
        # than accrued (rare). Otherwise we use the legacy lookup as a
        # graceful proxy — but inside SSOT we PREFER the ledger value.
        # net_due = accrued − ledger_paid.
        # ledger_paid = sum of debits to salary_payable for this emp.
        paid = await _sum_side(
            db, user_id, entity_type="employee", entity_id=emp_id,
            sub_account="salary_payable", side="debit",
        )
        net_due = round(accrued - paid, 2)
        paid_total += paid
        advances_total += advance_net

        employees.append({
            "id": emp_id, "name": emp.get("name"),
            "status": emp.get("status") or "active",
            "monthly_amount": round(
                float(emp.get("monthly_amount") or 0), 2),
            "start_date": calc.get("start_date"),
            "end_date": calc.get("end_date"),
            "days_worked": calc.get("days_worked"),
            "accrued": accrued,
            "paid": paid,
            "outstanding_advance": advance_net,
            "net_due": net_due,
        })

    return {
        "accrued_total": round(accrued_total, 2),
        "paid_total": round(paid_total, 2),
        "advances_total": round(advances_total, 2),
        "net_due": round(accrued_total - paid_total, 2),
        "active_count": active_count,
        "suspended_count": suspended_count,
        "employees": employees,
    }


async def _sum_side(
    db, user_id: str, *, entity_type: str, entity_id: str,
    sub_account: str | None, side: str,
) -> float:
    pipe = [
        {"$match": {
            "user_id": user_id, "status": "posted",
            "entry_type": {"$ne": "reversal"},
            "metadata.legacy_orphan": {"$ne": True},
            "entity_type": entity_type, "entity_id": entity_id,
            "sub_account": sub_account, "side": side,
        }},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]
    docs = await db.general_ledger.aggregate(pipe).to_list(1)
    return round(float(docs[0]["total"]) if docs else 0.0, 2)


# ── ad-provider breakdown of unpaid debt ─────────────────────────────
async def by_ad_provider_ssot(db, user_id: str) -> dict:
    """Total unpaid debt per ad provider, derived from
    `ad_account.debt` entries cross-referenced with the counterparty's
    `ad_provider` field. Keys are normalised to lowercase provider
    names (snapchat / meta / tiktok / unknown)."""
    pipeline = [
        {"$match": {"user_id": user_id, "status": "posted",
                     "entry_type": {"$ne": "reversal"},
                     "metadata.legacy_orphan": {"$ne": True},
                     "entity_type": "ad_account",
                     "sub_account": "debt"}},
        {"$group": {
            "_id": "$entity_id",
            "debits": {"$sum": {"$cond": [
                {"$eq": ["$side", "debit"]}, "$amount", 0]}},
            "credits": {"$sum": {"$cond": [
                {"$eq": ["$side", "credit"]}, "$amount", 0]}},
        }},
    ]
    per_account: dict[str, float] = {}
    async for r in db.general_ledger.aggregate(pipeline):
        net = round(float(r["credits"]) - float(r["debits"]), 2)
        if net > 0:
            per_account[r["_id"]] = net
    # Map account ID → provider via counterparties.
    by_provider: dict[str, float] = {
        "snapchat": 0.0, "meta": 0.0, "tiktok": 0.0, "unknown": 0.0,
    }
    if not per_account:
        return by_provider
    async for cp in db.counterparties.find(
        {"user_id": user_id, "kind": "ad_account",
         "id": {"$in": list(per_account.keys())}},
        {"_id": 0, "id": 1, "ad_provider": 1},
    ):
        p = (cp.get("ad_provider") or "unknown").lower()
        if p not in by_provider:
            p = "unknown"
        by_provider[p] += per_account.pop(cp["id"], 0.0)
    # Any leftover (ad_account that isn't in counterparties) → unknown.
    by_provider["unknown"] += sum(per_account.values())
    return {k: round(v, 2) for k, v in by_provider.items()}


# ── master entry point ───────────────────────────────────────────────
async def compute_financial_position(db, user_id: str) -> dict[str, Any]:
    """SSOT financial position. Returns the legacy `/liabilities/
    summary` shape so the existing `FinancialPosition.jsx` can read
    it with a one-line endpoint swap.
    """
    grouped = await _group_by_subaccount(db, user_id)

    # Assets (debit-positive)
    assets = {
        "banks": 0.0,                 # bank.* (sum of all banks)
        "employee_advance": 0.0,
        "employee_custody": 0.0,
        "external_receivable": 0.0,
        "courier_cod_receivable": 0.0,
        "store_driver_cod_receivable": 0.0,
        "ad_account_prepaid": 0.0,
        "input_vat": 0.0,
    }
    # Liabilities (credit-positive)
    liabilities = {
        "salaries_unpaid": 0.0,
        "supplier_payable": 0.0,
        "courier_payable": 0.0,
        "store_driver_payable": 0.0,
        "external_payable": 0.0,
        "ad_accounts_unpaid": 0.0,
    }

    for (et, sub), net in grouped.items():
        if et == "bank":
            assets["banks"] += net
        elif et == "employee" and sub == "advance":
            assets["employee_advance"] += max(net, 0.0)
        elif et == "employee" and sub == "custody":
            assets["employee_custody"] += max(net, 0.0)
        elif et == "employee" and sub == "salary_payable":
            liabilities["salaries_unpaid"] += max(-net, 0.0)
        elif et == "supplier" and sub == "payable":
            liabilities["supplier_payable"] += max(-net, 0.0)
        elif et == "courier" and sub == "payable":
            liabilities["courier_payable"] += max(-net, 0.0)
        elif et == "courier" and sub == "cod_receivable":
            assets["courier_cod_receivable"] += max(net, 0.0)
        elif et == "store_driver" and sub == "cod_receivable":
            assets["store_driver_cod_receivable"] += max(net, 0.0)
        elif et == "store_driver" and sub == "delivery_fee_payable":
            liabilities["store_driver_payable"] += max(-net, 0.0)
        elif et == "tax" and sub == "recoverable":
            assets["input_vat"] += max(net, 0.0)
        elif et == "external_person" and sub == "receivable":
            assets["external_receivable"] += max(net, 0.0)
        elif et == "external_person" and sub == "payable":
            liabilities["external_payable"] += max(-net, 0.0)
        elif et == "ad_account":
            # balance sub → asset (prepaid), debt sub → liability.
            if sub == "balance" and net > 0:
                assets["ad_account_prepaid"] += net
            elif sub == "debt" and net < 0:
                liabilities["ad_accounts_unpaid"] += -net

    # Per-account balances for bank+cash+payment_platform accounts.
    # Iter-217 — Phase B: every account with ANY ledger activity is
    # read from the ledger; others fall back to current_balance.
    banks_via_account_rule = 0.0
    platforms_remaining = 0.0
    async for acc in db.accounts.find(
        {"user_id": user_id, "status": {"$ne": "hidden"}},
        {"_id": 0, "id": 1, "account_type": 1,
         "current_balance": 1, "provider_name": 1, "name": 1,
         "normalized_payment_method": 1, "status": 1},
    ):
        t = acc.get("account_type")
        if t == "bank":
            bal = await account_balance_ssot(
                db, user_id=user_id, account=acc,
            )
            banks_via_account_rule += bal
        elif t == "payment_platform":
            bal = await account_balance_ssot(
                db, user_id=user_id, account=acc,
            )
            platforms_remaining += bal

    # Prefer the per-account walk for `banks` (it merges legacy +
    # ledger correctly per the SSOT fallback rule). The pipeline-only
    # value is kept for diagnostics under `banks_ledger_only`.
    banks_ledger_only = assets["banks"]
    assets["banks"] = round(banks_via_account_rule, 2)

    # Salary breakdown (per-employee) + ad-provider breakdown.
    salary = await salary_breakdown_ssot(db, user_id)
    by_provider = await by_ad_provider_ssot(db, user_id)

    # Headline salary number must match the per-employee view.
    liabilities["salaries_unpaid"] = salary["net_due"]

    # Totals
    assets_total = round(sum(assets.values())
                         + round(platforms_remaining, 2), 2)
    liabilities_total = round(sum(liabilities.values()), 2)
    net_position = round(assets_total - liabilities_total, 2)

    return {
        "assets": {k: round(v, 2) for k, v in assets.items()} | {
            "payment_platforms_remaining": round(platforms_remaining, 2),
        },
        "liabilities": {k: round(v, 2) for k, v in liabilities.items()},
        "totals": {
            "total_assets": assets_total,
            "total_liabilities": liabilities_total,
            "net_position": net_position,
        },
        # Top-level extras for the legacy /financial-position page.
        "net_position": net_position,
        "salary_breakdown": salary,
        "by_ad_provider": by_provider,
        "banks_ledger_only": round(banks_ledger_only, 2),
        "source": "general_ledger_v2",
        "iter": "iter217",
    }


__all__ = [
    "compute_financial_position",
    "account_balance_ssot",
    "salary_breakdown_ssot",
    "by_ad_provider_ssot",
]
