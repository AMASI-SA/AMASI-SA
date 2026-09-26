"""Read-only MZ2 report boundary over the existing ledger, never legacy balances.

The approved opening group and post-cutover producer contracts are the only
financial inputs. Account records supply identity/type only. Missing evidence
returns unavailable amounts, not a fabricated approved zero.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import Depends, HTTPException, Query, Request

from accounting_module_contract import OPERATION_ID, accounting_owner_id, require_accounting_permission
from accounting_module_readiness import build_accounting_module_status, _aware_utc_iso
from accounting_report_dates import accounting_instant, report_cutoff
from accounting_write_control import fresh_actor

MAX_REPORT_LEGS = 10000


def mz2_query(owner):
    """Never accept a tenant/operation supplied by a caller's payload."""
    return {"user_id": owner, "metadata.operation_id": OPERATION_ID,
            "status": {"$in": ["posted", "reversed"]},
            "metadata.legacy_orphan": {"$ne": True}}


def _date(row):
    meta = row.get("metadata") or {}
    # No insertion/audit timestamp fallback at this boundary.
    if "accounting_at" not in meta and not (
        row.get("entry_type") == "bnpl_sale" and "recognized_at" in meta
    ):
        raise ValueError("mz2_accounting_date_required")
    return accounting_instant(row)


def _producer(row):
    kind = row.get("entry_type")
    meta = row.get("metadata") or {}
    if kind == "bnpl_sale":
        return bool(meta.get("recognition_event_key"))
    if kind == "settlement":
        return meta.get("source") == "accounting_settlement_p01"
    if kind in {"customer_refund_due", "customer_refund_payment"}:
        return meta.get("refund_accounting_version") == 2
    if kind in {"customer_advance_capture", "customer_advance_cancellation", "customer_advance_payment"}:
        return bool(meta.get("customer_advance_id"))
    if kind in {
        "salary_accrual", "salary_payment", "advance_grant", "advance_settle",
        "advance_repay_cash", "custody_grant", "custody_return",
    }:
        return meta.get("source") == "accounting_payroll_p01" and bool(meta.get("payroll_event_id"))
    if kind in {"bank_transfer_advance", "bank_transfer_sale"}:
        return (
            meta.get("source") == "accounting_bank_transfer_receipt_p01"
            and bool(meta.get("bank_transfer_review_id"))
        )
    if kind in {"cod_sale", "shipping_fee_accrual", "shipping_settlement"}:
        return meta.get("source") == "accounting_shipping_p02" and bool(meta.get("shipping_event_id"))
    if kind in {"expense_record", "supplier_payment"}:
        return (
            meta.get("source") == "accounting_daily_outgoing_p01"
            and bool(meta.get("outgoing_event_id"))
            and bool(meta.get("daily_movement_id"))
        )
    return False


def _balanced(rows):
    if len(rows) < 2:
        return False
    sides = {"debit": Decimal(0), "credit": Decimal(0)}
    try:
        dates = set()
        for row in rows:
            if any(not isinstance(row.get(key), str) or not row[key].strip()
                   for key in ("entity_type", "entity_id")):
                return False
            if row.get("sub_account") is not None and not isinstance(row["sub_account"], str):
                return False
            value = Decimal(str(row.get("amount")))
            if not value.is_finite() or value < 0 or row.get("side") not in sides:
                return False
            sides[row["side"]] += value
            dates.add(_date(row))
        return len(dates) == 1 and sides["debit"] > 0 and sides["debit"] == sides["credit"]
    except (ValueError, InvalidOperation, TypeError):
        return False


async def _cutover(db, owner):
    row = await db.settings.find_one({"user_id": owner}, {"_id": 0, "mezan2_financial_cutover": 1})
    return (row or {}).get("mezan2_financial_cutover") or {}


async def read_mz2_ledger(db, *, owner, as_of=None, required_accounts=()):
    upper = report_cutoff(as_of) if as_of is not None else datetime.now(timezone.utc)
    state = await _cutover(db, owner)
    result = dict(status="not_ready", reason="cutover_not_approved", operation_id=OPERATION_ID,
                  legacy_financial_data_included=False, ledger_only=True,
                  cutover_at=None, opening_balance_txn_group_id=None, as_of=as_of,
                  timezone="Asia/Riyadh", items=[], missing_accounts=[])
    def blocked(reason, status="not_ready"):
        return {**result, "status": status, "reason": reason, "items": []}
    cut = _aware_utc_iso(state.get("cutover_at"))
    if state.get("operation_id") != OPERATION_ID or not cut:
        return result
    cut_at = datetime.fromisoformat(cut)
    group = str(state.get("opening_balance_txn_group_id") or "").strip()
    result.update(cutover_at=cut, opening_balance_txn_group_id=group or None)
    if not group:
        return blocked("approved_opening_group_required", "needs_opening_balance")
    opening = await db.general_ledger.find({"user_id": owner, "txn_group_id": group}, {"_id": 0}).to_list(MAX_REPORT_LEGS + 1)
    try:
        opening_ok = len(opening) <= MAX_REPORT_LEGS and _balanced(opening) and all(
            r.get("entry_type") == "opening_balance" and r.get("status") == "posted"
            and (r.get("metadata") or {}).get("operation_id") == OPERATION_ID
            and not (r.get("metadata") or {}).get("legacy_orphan") and _date(r) == cut_at for r in opening)
    except (ValueError, TypeError):
        opening_ok = False
    if not opening_ok:
        return blocked("approved_opening_group_invalid", "needs_opening_balance")
    readiness = build_accounting_module_status(state, opening_posted_verified=True)
    if not readiness["cutover"]["safe_active"]:
        return blocked("cutover_evidence_or_approval_incomplete")
    if upper <= cut_at:
        return blocked("report_before_cutover")
    rows = await db.general_ledger.find(mz2_query(owner), {"_id": 0}).to_list(MAX_REPORT_LEGS + 1)
    if len(rows) > MAX_REPORT_LEGS:
        return blocked("report_scope_too_large")
    eligible = []
    for row in rows:
        if row.get("entry_type") == "opening_balance":
            if row.get("txn_group_id") == group:
                eligible.append(row)
            continue  # Every other opening batch is outside this cutover.
        try:
            at = _date(row)
        except (ValueError, TypeError):
            return blocked("mz2_accounting_date_invalid")
        if at < cut_at or at >= upper:
            continue
        if row.get("status") == "reversed":
            return blocked("reversed_mz2_group_requires_review")
        if not _producer(row):
            return blocked("unsupported_mz2_journal_source")
        eligible.append(row)
    by_group = defaultdict(list)
    for row in eligible:
        by_group[row.get("txn_group_id")].append(row)
    if not by_group or any(not key or not _balanced(legs) for key, legs in by_group.items()):
        return blocked("incomplete_or_unbalanced_mz2_group")
    # Activity cannot turn an unknown opening into an implicit zero. Core GL
    # forbids zero-value legs, so an approved zero must be an explicit evidence
    # record bound to this same opening batch and economic instant.
    covered = {(r.get("entity_type"), str(r.get("entity_id")), r.get("sub_account") or "") for r in opening}
    zero_accounts = state.get("opening_balance_zero_accounts") or []
    if not isinstance(zero_accounts, list):
        return blocked("approved_zero_opening_evidence_invalid")
    for zero in zero_accounts:
        if not isinstance(zero, dict):
            return blocked("approved_zero_opening_evidence_invalid")
        at = _aware_utc_iso(zero.get("accounting_at"))
        if (zero.get("opening_balance_txn_group_id") != group or at != cut
                or not str(zero.get("evidence_ref") or "").strip()
                or zero.get("entity_type") not in {"bank", "payment_gateway", "employee", "courier", "store_driver"}
                or not str(zero.get("entity_id") or "").strip()):
            return blocked("approved_zero_opening_evidence_invalid")
        key = (zero["entity_type"], str(zero["entity_id"]), zero.get("sub_account") or "")
        if not isinstance(key[2], str) or key in covered:
            return blocked("approved_zero_opening_evidence_invalid")
        covered.add(key)
    accounts = await db.accounts.find({"user_id": owner, "status": {"$ne": "hidden"},
        "account_type": {"$in": ["bank", "cash", "payment_platform"]}},
        {"_id": 0, "id": 1, "account_type": 1}).to_list(MAX_REPORT_LEGS + 1)
    if len(accounts) > MAX_REPORT_LEGS:
        return blocked("account_scope_too_large")
    required = {("bank", str(a.get("id")), "main") for a in accounts}
    # Internal write callers may need a never-used provider account. Its zero
    # must still be explicitly approved; this argument never widens GL scope.
    required.update(required_accounts)
    required.update((r.get("entity_type"), str(r.get("entity_id")), r.get("sub_account") or "")
                    for r in eligible if r.get("entity_type") in {"bank", "payment_gateway"})
    missing = ["/".join(key) for key in sorted(required - covered)]
    if missing:
        result["missing_accounts"] = missing
        return blocked("accounts_require_approved_opening", "needs_opening_balance")
    if state != await _cutover(db, owner):
        return blocked("cutover_changed_refresh_required")
    eligible.sort(key=lambda r: (_date(r), str(r.get("txn_group_id")), str(r.get("entry_no") or "")))
    return {**result, "status": "available", "reason": "approved_opening_and_post_cutover_mz2_only", "items": eligible}


def _sums(rows):
    groups = defaultdict(lambda: {"debits": Decimal(0), "credits": Decimal(0)})
    for row in rows:
        key = (row.get("entity_type"), row.get("entity_id"), row.get("sub_account") or "")
        groups[key]["debits" if row["side"] == "debit" else "credits"] += Decimal(str(row["amount"]))
    return [{"entity_type": key[0], "entity_id": key[1], "sub_account": key[2],
             "debits": float(v["debits"]), "credits": float(v["credits"]),
             "net": float(v["debits"] - v["credits"])} for key, v in sorted(groups.items())]


async def mz2_trial_balance(db, *, owner, as_of=None):
    scope = await read_mz2_ledger(db, owner=owner, as_of=as_of)
    return {**scope, "items": _sums(scope["items"]) if scope["status"] == "available" else []}


async def mz2_financial_position(db, *, owner, as_of=None):
    scope = await read_mz2_ledger(db, owner=owner, as_of=as_of)
    base = {k: v for k, v in scope.items() if k != "items"}
    if scope["status"] != "available":
        return {**base, "assets": None, "liabilities": None, "totals": None}
    assets = {"banks": Decimal(0), "payment_platforms_remaining": Decimal(0), "input_vat": Decimal(0)}
    liabilities = {"customer_refund_payable": Decimal(0), "customer_advance": Decimal(0), "sales_vat_payable": Decimal(0)}
    accounts = await db.accounts.find({"user_id": owner}, {"_id": 0, "id": 1, "account_type": 1}).to_list(MAX_REPORT_LEGS + 1)
    types = {a.get("id"): a.get("account_type") for a in accounts}
    asset_map = {("payment_gateway", "receivable"): "payment_platforms_remaining", ("tax", "input_vat"): "input_vat",
                 ("tax", "recoverable"): "input_vat", ("employee", "advance"): "employee_advance",
                 ("employee", "custody"): "employee_custody", ("external_person", "receivable"): "external_receivable",
                 ("courier", "cod_receivable"): "courier_cod_receivable", ("store_driver", "cod_receivable"): "store_driver_cod_receivable",
                 ("ad_account", "balance"): "ad_account_prepaid"}
    liability_map = {("tax", "sales_vat_payable"): "sales_vat_payable", ("employee", "salary_payable"): "salaries_unpaid",
                     ("supplier", "payable"): "supplier_payable", ("courier", "payable"): "courier_payable",
                     ("store_driver", "delivery_fee_payable"): "store_driver_payable", ("external_person", "payable"): "external_payable",
                     ("ad_account", "debt"): "ad_accounts_unpaid"}
    for row in _sums(scope["items"]):
        key = (row["entity_type"], row["sub_account"] or (row["entity_id"] if row["entity_type"] == "tax" else ""))
        net = Decimal(str(row["net"]))
        if key[0] == "bank":
            name = "payment_platforms_remaining" if types.get(row["entity_id"]) == "payment_platform" else "banks"
            assets[name] += net
        elif key in asset_map or key[0] == "asset":
            name = asset_map.get(key, key[1] or "other_assets")
            assets[name] = assets.get(name, Decimal(0)) + net
        elif key in liability_map or key[0] == "liability":
            name = liability_map.get(key, key[1] or "other_liabilities")
            liabilities[name] = liabilities.get(name, Decimal(0)) - net
    a, l = sum(assets.values()), sum(liabilities.values())
    return {**base, "assets": {k: float(v) for k, v in assets.items()},
            "liabilities": {k: float(v) for k, v in liabilities.items()},
            "totals": {"total_assets": float(a), "total_liabilities": float(l), "net_position": float(a-l)}}


def install_mz2_report_routes(router, db, current_user):
    async def report_scope(request, user):
        if set(request.query_params) - {"as_of"}:
            raise HTTPException(422, "mz2_report_scope_is_server_controlled")
        actor = await fresh_actor(db, user)
        require_accounting_permission(actor, "accounting.journals_reports.view")
        return accounting_owner_id(actor)

    def install(path, reader):
        async def report(request: Request, as_of: str | None = Query(None), user: dict = Depends(current_user)):
            owner = await report_scope(request, user)
            try:
                return await reader(db, owner=owner, as_of=as_of)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        router.add_api_route("/accounting-module/reports/" + path, report, methods=["GET"])
    install("financial-position", mz2_financial_position)
    install("trial-balance", mz2_trial_balance)
    install("journals", read_mz2_ledger)
