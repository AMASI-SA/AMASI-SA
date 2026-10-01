"""Read-only MZ2 report boundary over the existing ledger, never legacy balances.

The approved opening group and post-cutover producer contracts are the only
financial inputs. Account records supply identity/type only. Missing evidence
returns unavailable amounts, not a fabricated approved zero.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import Depends, HTTPException, Query, Request

from accounting_module_contract import OPERATION_ID, accounting_owner_id, require_accounting_permission
from accounting_module_readiness import build_accounting_module_status, _aware_utc_iso
from accounting_report_dates import accounting_instant, report_cutoff
from accounting_write_control import fresh_actor

MAX_REPORT_LEGS = 10000


def _date(row):
    meta = row.get("metadata") or {}
    if row.get("effective_at"):
        value = _aware_utc_iso(row.get("effective_at"))
        if not value:
            raise ValueError("mz2_accounting_date_required")
        return datetime.fromisoformat(value)
    # No insertion/audit timestamp fallback at this boundary.
    if "accounting_at" not in meta and not (
        row.get("entry_type") == "bnpl_sale" and "recognized_at" in meta
    ):
        raise ValueError("mz2_accounting_date_required")
    return accounting_instant(row)


async def _cutover(db, owner):
    row = await db.settings.find_one({"user_id": owner}, {"_id": 0, "mezan2_financial_cutover": 1})
    return (row or {}).get("mezan2_financial_cutover") or {}


async def _read_v2_mz2_ledger(db, *, owner, as_of=None, required_accounts=()):
    from accounting_ledger_v2 import (
        AccountingLedgerV2Error,
        read_reporting_entries_v2,
        verify_active_opening_v2,
    )

    upper = report_cutoff(as_of) if as_of is not None else datetime.now(timezone.utc)
    state = await _cutover(db, owner)
    active_id = str(state.get("opening_active_txn_group_id") or "").strip()
    result = dict(
        status="not_ready", reason="cutover_not_approved", operation_id=OPERATION_ID,
        legacy_financial_data_included=False, ledger_only=True, ledger_backend="v2",
        cutover_at=None, opening_balance_txn_group_id=active_id or None,
        as_of=as_of, timezone="Asia/Riyadh", items=[], missing_accounts=[],
    )

    def blocked(reason, status="not_ready"):
        return {**result, "status": status, "reason": reason, "items": []}

    cut = _aware_utc_iso(state.get("cutover_at"))
    if state.get("operation_id") != OPERATION_ID or not cut:
        return result
    result["cutover_at"] = cut
    if state.get("status") != "active" or not active_id:
        return blocked("approved_opening_group_required", "needs_opening_balance")
    if not await verify_active_opening_v2(db, user_id=owner, cutover=state):
        return blocked("approved_opening_group_invalid", "needs_opening_balance")
    readiness = build_accounting_module_status(
        {**state, "ledger_source": "accounting_v2_operation_scoped"},
        opening_posted_verified=True,
    )
    if not readiness["cutover"]["safe_active"]:
        return blocked("cutover_evidence_or_approval_incomplete")
    cut_at = datetime.fromisoformat(cut)
    if upper <= cut_at:
        return blocked("report_before_cutover")
    try:
        rows = await read_reporting_entries_v2(
            db,
            user_id=owner,
            effective_before=upper.isoformat(),
            limit=MAX_REPORT_LEGS,
            mongo_session=getattr(db, "_session", None),
        )
    except AccountingLedgerV2Error as error:
        return blocked(error.code)
    eligible = [row for row in rows if _date(row) >= cut_at]
    # Coverage is an opening fact, never something later operational activity
    # can manufacture.  A newly active account must therefore appear in the
    # currently active opening (or its explicit-zero manifest), not merely in
    # an arbitrary post-cutover journal.
    covered = {
        (row.get("entity_type"), str(row.get("entity_id")), row.get("sub_account") or "")
        for row in eligible
        if row.get("txn_group_id") == active_id
        and row.get("entry_type") in {"opening_balance", "opening_replacement"}
    }
    zero_accounts = state.get("opening_balance_zero_accounts") or []
    if not isinstance(zero_accounts, list):
        return blocked("approved_zero_opening_evidence_invalid")
    for zero in zero_accounts:
        if not isinstance(zero, dict):
            return blocked("approved_zero_opening_evidence_invalid")
        at = _aware_utc_iso(zero.get("accounting_at"))
        if (
            zero.get("opening_balance_txn_group_id") != active_id
            or at != cut
            or not str(zero.get("evidence_ref") or "").strip()
            or not str(zero.get("entity_type") or "").strip()
            or not str(zero.get("entity_id") or "").strip()
        ):
            return blocked("approved_zero_opening_evidence_invalid")
        key = (zero["entity_type"], str(zero["entity_id"]), zero.get("sub_account") or "")
        if not isinstance(key[2], str) or key in covered:
            return blocked("approved_zero_opening_evidence_invalid")
        covered.add(key)
    if not eligible and not zero_accounts:
        return blocked("opening_balance_evidence_missing", "needs_opening_balance")
    accounts = await db.mz2_financial_accounts.find(
        {"user_id": owner, "status": "active"},
        {"_id": 0, "id": 1, "account_type": 1},
    ).to_list(MAX_REPORT_LEGS + 1)
    if len(accounts) > MAX_REPORT_LEGS:
        return blocked("account_scope_too_large")
    rules = {
        "bank": ("bank", "main"), "cash": ("bank", "main"),
        "ad_prepaid_wallet": ("ad_account", "balance"),
        "ad_payable": ("ad_account", "debt"),
        "overdraft": ("liability", "bank_overdraft"),
    }
    required = {
        (rules[row["account_type"]][0], str(row["id"]), rules[row["account_type"]][1])
        for row in accounts if row.get("account_type") in rules
    }
    required.update(required_accounts)
    missing = ["/".join(key) for key in sorted(required - covered)]
    if missing:
        result["missing_accounts"] = missing
        return blocked("accounts_require_approved_opening", "needs_opening_balance")
    if state != await _cutover(db, owner):
        return blocked("cutover_changed_refresh_required")
    eligible.sort(key=lambda row: (_date(row), int(row.get("entry_no") or 0)))
    return {
        **result,
        "status": "available",
        "reason": "approved_v2_opening_and_operation_scoped_ledger_only",
        "items": eligible,
        "zero_accounts": zero_accounts,
    }


async def read_mz2_ledger(db, *, owner, as_of=None, required_accounts=()):
    """Select exactly one ledger backend; never combine or fall back."""
    from accounting_writer_transition import transition_state

    transition = await transition_state(
        db, owner, mongo_session=getattr(db, "_session", None),
    )
    if transition["state"] == "v2_active":
        return await _read_v2_mz2_ledger(
            db, owner=owner, as_of=as_of, required_accounts=required_accounts,
        )
    return {
        "status": "not_ready", "reason": "mz2_native_ledger_required" if transition["state"] == "legacy_active" else "accounting_transition_blocked",
        "operation_id": OPERATION_ID, "legacy_financial_data_included": False,
        "ledger_only": True, "ledger_backend": None, "cutover_at": None,
        "opening_balance_txn_group_id": None, "as_of": as_of,
        "timezone": "Asia/Riyadh", "items": [], "missing_accounts": [],
    }


def _sums(rows):
    groups = defaultdict(lambda: {"debits": Decimal(0), "credits": Decimal(0)})
    for row in rows:
        key = (row.get("entity_type"), row.get("entity_id"), row.get("sub_account") or "")
        groups[key]["debits" if row["side"] == "debit" else "credits"] += Decimal(str(row["amount"]))
    return [{"entity_type": key[0], "entity_id": key[1], "sub_account": key[2],
             "debits": float(v["debits"]), "credits": float(v["credits"]),
             "net": float(v["debits"] - v["credits"])} for key, v in sorted(groups.items())]


def _report_sums(scope):
    rows = _sums(scope["items"])
    present = {_identity(row) for row in rows}
    for zero in scope.get("zero_accounts", []):
        if _identity(zero) not in present:
            rows.append({"entity_type": zero["entity_type"], "entity_id": zero["entity_id"],
                         "sub_account": zero.get("sub_account") or "", "debits": 0.0,
                         "credits": 0.0, "net": 0.0, "explicit_zero": True,
                         "evidence_ref": zero["evidence_ref"]})
            present.add(_identity(zero))
    return rows


# Classification is an explicit native contract, never a guessed catch-all.
ASSET_CATEGORIES = {
    ("bank", "main"): "banks",
    ("payment_gateway", "receivable"): "payment_platforms_remaining",
    ("tax", "input_vat"): "input_vat",
    ("employee", "advance"): "employee_advance",
    ("employee", "custody"): "employee_custody",
    ("external_person", "receivable"): "external_receivable",
    ("courier", "cod_receivable"): "courier_cod_receivable",
    ("store_driver", "cod_receivable"): "store_driver_cod_receivable",
    ("ad_account", "balance"): "ad_account_prepaid",
    ("supplier", "advance"): "supplier_advance",
    ("asset", "inventory"): "inventory",
    ("asset", "prepaid_expense"): "prepaid_expense",
    ("asset", "other_receivable"): "other_receivable",
}
LIABILITY_CATEGORIES = {
    ("tax", "sales_vat_payable"): "sales_vat_payable",
    ("employee", "salary_payable"): "salaries_unpaid",
    ("supplier", "payable"): "supplier_payable",
    ("courier", "payable"): "courier_payable",
    ("store_driver", "delivery_fee_payable"): "store_driver_payable",
    ("external_person", "payable"): "external_payable",
    ("ad_account", "debt"): "ad_accounts_unpaid",
    ("liability", "bank_overdraft"): "bank_overdraft",
    ("liability", "accrued_expense"): "accrued_expense",
    ("liability", "other_payable"): "other_payable",
    ("liability", "customer_refund_payable"): "customer_refund_payable",
    ("liability", "customer_advance"): "customer_advance",
}


def _identity(row):
    return (row["entity_type"], str(row["entity_id"]), row.get("sub_account") or "")


async def _classified_scope(db, *, owner, as_of=None):
    """Validate every identity before returning any partial financial total."""
    from accounting_financial_accounts import FINANCIAL_ACCOUNT_RULES, OPENING_CATEGORY_CATALOG
    from accounting_onboarding_identities import identities

    scope = await read_mz2_ledger(db, owner=owner, as_of=as_of)
    if scope["status"] != "available":
        return scope
    accounts = await db.mz2_financial_accounts.find({"user_id": owner}, {"_id": 0}).to_list(MAX_REPORT_LEGS + 1)
    if len(accounts) > MAX_REPORT_LEGS:
        return {**scope, "status": "not_ready", "reason": "account_scope_too_large", "items": []}
    canonical = set()
    for account in accounts:
        rule = FINANCIAL_ACCOUNT_RULES.get(account.get("account_type"))
        if rule and account.get("id"):
            canonical.add((rule["entity_type"], str(account["id"]), rule["sub_account"]))
    # A verified native opening row contains its exact typed category and evidence.
    # This is the existing ledger identity contract for inventory and typed facts;
    # arbitrary asset/liability subaccounts never acquire financial authority.
    for row in scope["items"]:
        meta = row.get("metadata") or {}
        rule = OPENING_CATEGORY_CATALOG.get(meta.get("opening_category"))
        if (row.get("txn_group_id") == scope["opening_balance_txn_group_id"]
                and row.get("entry_type") in {"opening_balance", "opening_replacement"}
                and meta.get("evidence_file_id") and rule
                and (row.get("entity_type"), row.get("sub_account")) == ("asset", "inventory")
                and (rule["entity_type"], rule["sub_account"]) == (row["entity_type"], row.get("sub_account"))):
            canonical.add(_identity(row))
    for collection in ("mz2_opening_facts_v2", "mz2_prepaid_selections_v2"):
        facts = await db[collection].find({"user_id": owner, "status": "active"}, {"_id": 0}).to_list(MAX_REPORT_LEGS + 1)
        if len(facts) > MAX_REPORT_LEGS:
            return {**scope, "status": "not_ready", "reason": "identity_scope_too_large", "items": []}
        for fact in facts:
            if fact.get("entity_type") and fact.get("entity_id") and fact.get("sub_account"):
                canonical.add(_identity(fact))
    # Customer liabilities are identities created by the existing confirmed
    # workflow, not selectable financial accounts. Bind the owner-scoped
    # domain record to its verified native origin before admitting the key.
    from accounting_ledger_v2 import read_verified_journal_metadata_v2
    journal_metadata, expense_contracts = {}, set()
    for row in scope["items"]:
        kind, key, sub = _identity(row)
        if kind == "expense":
            group = row["txn_group_id"]
            if group not in journal_metadata:
                journal_metadata[group] = await read_verified_journal_metadata_v2(db,
                    user_id=owner, txn_group_id=group, mongo_session=getattr(db, "_session", None))
            meta = journal_metadata[group]
            if not sub and meta.get("expense_category") == key and meta.get("outgoing_event_id"):
                event = await db.mz2_outgoing_financial_events.find_one({"user_id": owner,
                    "id": meta["outgoing_event_id"], "kind": "expense", "status": "posted",
                    "classification_ref": key, "txn_group_id": group})
                if event:
                    expense_contracts.add((kind, key, sub))
            if (meta.get("source") == "accounting_settlement_p01" and meta.get("provider") == sub
                    and sub in {"salla", "tamara", "tabby", "emkan"}
                    and key in {"provider_commission", "provider_commission_vat", "provider_settlement_fee",
                        "provider_settlement_fee_vat", "salla_wallet_purchases", "provider_other_deductions",
                        "provider_fee_rebates"}):
                expense_contracts.add((kind, key, sub))
            if not sub and row.get("entry_type") == "advertising_v2":
                identity = await db.mz2_ad_expense_identities_v2.find_one({"user_id": owner,
                    "entity_id": key, "purpose": {"$in": ["advertising", "bank_fee"]},
                    "confirmed_by": {"$exists": True, "$nin": [None, ""]}})
                if identity:
                    expense_contracts.add((kind, key, sub))
        if kind != "liability" or sub not in {"customer_advance", "customer_refund_payable"}:
            continue
        group = row["txn_group_id"]
        if group not in journal_metadata:
            journal_metadata[group] = await read_verified_journal_metadata_v2(db,
                user_id=owner, txn_group_id=group, mongo_session=getattr(db, "_session", None))
        meta = journal_metadata[group]
        if sub == "customer_refund_payable" and meta.get("refund_case_id") == key:
            case = await db.mz2_customer_refunds.find_one({"user_id": owner, "id": key,
                "accounting_version": 2, "recognized": True, "due_txn_group_id": group})
            if case:
                canonical.add((kind, key, sub))
        if meta.get("customer_advance_id") == key:
            advance = await db.mz2_customer_advances.find_one({"user_id": owner, "id": key,
                "$or": [{"capture_txn_group_id": group}, {"due_txn_group_id": group}]})
            receipt = await db.mz2_bank_transfer_receipts.find_one({"user_id": owner,
                "advance_id": key, "receipt_txn_group_id": group})
            if advance or receipt:
                canonical.add((kind, key, sub))
    cache, unresolved = {}, []
    for row in _report_sums(scope):
        kind, key, sub = _identity(row)
        pair = (kind, sub)
        valid = (kind, key, sub) in canonical
        reason = "native_identity_missing"
        if kind in {"employee", "supplier", "external_person", "courier", "store_driver", "payment_gateway"}:
            registry_kind = "provider" if kind == "payment_gateway" else kind
            if registry_kind not in cache:
                try:
                    native_rows = await identities(db, owner, registry_kind)
                    cache[registry_kind] = {r["id"] for r in native_rows
                                            if registry_kind != "employee" or r.get("financial_identity_ready") is True}
                    if registry_kind == "employee" and any(r.get("financial_identity_ready") is not True for r in native_rows):
                        cache[registry_kind + "_reason"] = "onboarding_employee_financial_identity_dependency"
                except HTTPException as error:
                    detail = error.detail if isinstance(error.detail, dict) else {}
                    cache[registry_kind] = set()
                    cache[registry_kind + "_reason"] = detail.get("code", "native_identity_unavailable")
            valid = key in cache[registry_kind]
            reason = cache.get(registry_kind + "_reason", reason)
            if kind == "supplier" and not valid:
                # Supplier financial identity is canonical V2 and may be
                # verified directly without using aliases or legacy records.
                from supplier_identity_service import require_supplier_v2
                try:
                    await require_supplier_v2(db, owner, key)
                    valid = True
                    reason = "native_supplier_v2_verified"
                except HTTPException:
                    valid = False
        if kind == "ad_account" and valid:
            from accounting_onboarding_identities import require_ad_financial_binding
            try:
                await require_ad_financial_binding(db, owner, key)
            except HTTPException as error:
                valid = False
                reason = (error.detail or {}).get("code", "native_ad_binding_unavailable") if isinstance(error.detail, dict) else "native_ad_binding_unavailable"
        system_contract = (
            (kind, key, sub) in expense_contracts
            or (kind, key, sub) == ("equity", "opening_balance_equity", "main")
            or (kind == "tax" and key in {"input_vat", "sales_vat_payable"} and sub in {"", key})
            or (kind, key, sub) == ("revenue", "bnpl_sales", "")
            or (kind == "expense" and key in {"salary", "shipping", "store_delivery", "courier_cod_commission"} and sub == "")
        )
        if system_contract:
            valid = True
        if pair not in ASSET_CATEGORIES and pair not in LIABILITY_CATEGORIES and not system_contract:
            valid, reason = False, "native_report_classification_contract_missing"
        if not valid:
            unresolved.append({"entity_type": kind, "entity_id": key, "sub_account": sub,
                               "status": "UNRESOLVED", "reason": reason})
    if unresolved:
        return {**scope, "status": "not_ready", "reason": "unresolved_mz2_identity",
                "readiness_blockers": unresolved, "items": []}
    return scope


async def mz2_report_readiness(db, *, owner):
    """Read-only onboarding port; an unopened native book is not a report failure."""
    state = await _cutover(db, owner)
    if not state.get("opening_active_txn_group_id"):
        return {"applicable": False, "status": "not_opened", "blockers": []}
    scope = await _classified_scope(db, owner=owner)
    blockers = scope.get("readiness_blockers") or ([] if scope["status"] == "available" else [
        {"status": "UNRESOLVED", "reason": scope["reason"],
         "missing_accounts": scope.get("missing_accounts", [])}])
    return {"applicable": True, "status": scope["status"], "blockers": blockers}


async def mz2_trial_balance(db, *, owner, as_of=None):
    scope = await _classified_scope(db, owner=owner, as_of=as_of)
    return {**scope, "items": _report_sums(scope) if scope["status"] == "available" else []}


async def mz2_journals(db, *, owner, as_of=None):
    return await _classified_scope(db, owner=owner, as_of=as_of)


async def mz2_financial_position(db, *, owner, as_of=None):
    scope = await _classified_scope(db, owner=owner, as_of=as_of)
    base = {k: v for k, v in scope.items() if k != "items"}
    if scope["status"] != "available":
        return {**base, "assets": None, "liabilities": None, "totals": None}
    assets, liabilities = {}, {}
    for row in _report_sums(scope):
        pair = (row["entity_type"], row["sub_account"] or (row["entity_id"] if row["entity_type"] == "tax" else ""))
        net = Decimal(str(row["net"]))
        if pair in ASSET_CATEGORIES:
            name = ASSET_CATEGORIES[pair]
            assets[name] = assets.get(name, Decimal(0)) + net
        elif pair in LIABILITY_CATEGORIES:
            name = LIABILITY_CATEGORIES[pair]
            liabilities[name] = liabilities.get(name, Decimal(0)) - net
    a, l = sum(assets.values()), sum(liabilities.values())
    return {**base, "assets": {k: float(v) for k, v in assets.items()},
            "liabilities": {k: float(v) for k, v in liabilities.items()},
            "totals": {"total_assets": float(a), "total_liabilities": float(l), "net_position": float(a-l)}}


async def mz2_account_statement(db, *, owner, entity_type, entity_id, sub_account=None, as_of=None):
    scope = await _classified_scope(db, owner=owner, as_of=as_of)
    base = {k: v for k, v in scope.items() if k != "items"}
    if scope["status"] != "available":
        return {**base, "items": [], "balance": None}
    rows = [r for r in scope["items"] if r["entity_type"] == entity_type
            and str(r["entity_id"]) == entity_id
            and (sub_account is None or (r.get("sub_account") or "") == sub_account)]
    totals = [r for r in _report_sums(scope) if r["entity_type"] == entity_type
              and str(r["entity_id"]) == entity_id
              and (sub_account is None or r["sub_account"] == sub_account)]
    if not totals:
        # Do not infer an approved zero from absence of movements.
        return {**base, "status": "not_ready", "reason": "account_evidence_missing",
                "items": [], "balance": None}
    return {**base, "items": rows, "balances": totals,
            "balance": float(sum(Decimal(str(r["net"])) for r in totals)) if len(totals) == 1 else None}


async def mz2_entity_report(db, *, owner, entity_types, as_of=None):
    scope = await mz2_trial_balance(db, owner=owner, as_of=as_of)
    if scope["status"] != "available":
        return scope
    rows = [r for r in scope["items"] if r["entity_type"] in entity_types]
    return {**scope, "items": rows, "status": "available" if rows else "not_ready",
            "reason": scope["reason"] if rows else "account_evidence_missing"}


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
    install("journals", mz2_journals)
    for path, kinds in {"couriers-drivers": ("courier", "store_driver"), "suppliers": ("supplier",),
                        "employees": ("employee",), "ads": ("ad_account",), "tax": ("tax",),
                        "expenses-liabilities": ("expense", "liability")}.items():
        async def reader(db, *, owner, as_of=None, _kinds=kinds):
            return await mz2_entity_report(db, owner=owner, entity_types=_kinds, as_of=as_of)
        install(path, reader)

    @router.get("/accounting-module/reports/account-statement/{entity_type}/{entity_id}")
    async def statement(entity_type: str, entity_id: str, request: Request,
                        as_of: str | None = Query(None), user: dict = Depends(current_user)):
        owner = await report_scope(request, user)
        try:
            return await mz2_account_statement(db, owner=owner, entity_type=entity_type,
                                               entity_id=entity_id, as_of=as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
