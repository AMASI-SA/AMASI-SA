from financial_provider_apps import (
    ACCOUNTING_ACTIONS,
    ACCOUNTING_PAGES,
    ACCOUNTING_PERMISSION_KEYS,
    OPERATION_ID,
    accounting_owner_id,
    accounting_permissions_for_user,
    build_accounting_module_status,
    make_financial_provider_apps_router,
    summarize_accounting_home_ledger,
)


def _complete_cutover(**overrides):
    state = {
        "operation_id": OPERATION_ID,
        "status": "active",
        "cutover_at": "2026-08-23T23:00:00+03:00",
        "evidence_sheet_ref": "SIGNED-CUTOVER-001",
        "evidence_sections": {
            "banks_cash": {"ref": "BANKS-001"},
            "providers": {"ref": "PROVIDERS-001"},
            "couriers_cod": {"ref": "COURIERS-001"},
            "inventory": {"ref": "INV-001"},
            "suppliers": {"ref": "SUP-001"},
            "payroll_obligations": {"ref": "PAY-001"},
            "equity": {"ref": "EQ-001"},
        },
        "opening_balance_preview_id": "OPEN-PREVIEW-001",
        "opening_balance_preview_balanced": True,
        "opening_balance_approved_at": "2026-08-23T23:30:00+03:00",
        "opening_balance_approved_by": "owner-1",
        "opening_balance_txn_group_id": "OPEN-GROUP-001",
    }
    state.update(overrides)
    return state


def test_accounting_module_has_exact_eight_pages_and_sensitive_actions():
    assert [row["label"] for row in ACCOUNTING_PAGES] == [
        "الرئيسية المحاسبية",
        "التسويات",
        "الشحن والتحصيل",
        "المخزون والمشتريات",
        "الحركات المالية",
        "الرواتب والالتزامات",
        "الأرصدة الافتتاحية",
        "القيود والتقارير",
    ]
    assert len({row["permission"] for row in ACCOUNTING_PAGES}) == 8
    action_keys = {row["permission"] for row in ACCOUNTING_ACTIONS}
    assert "accounting.opening_balances.approve" in action_keys
    assert "accounting.journals.manual_create" in action_keys
    assert "accounting.journals.reverse" in action_keys
    assert ACCOUNTING_PERMISSION_KEYS == {
        row["permission"] for row in (*ACCOUNTING_PAGES, *ACCOUNTING_ACTIONS)
    }


def test_every_non_owner_role_starts_without_accounting_access():
    for role in ("admin", "accountant", "operations", "viewer", "employee", ""):
        assert accounting_permissions_for_user({"role": role}) == []


def test_team_user_resolves_only_to_explicit_creator_owner_scope():
    assert accounting_owner_id({"id": "owner-1", "role": "owner"}) == "owner-1"
    assert accounting_owner_id({"id": "employee-1", "role": "accountant", "created_by": "owner-1"}) == "owner-1"
    assert accounting_owner_id({"id": "unlinked", "role": "viewer"}) is None


def test_permissions_are_independent_from_legacy_role_permissions():
    user = {
        "role": "accountant",
        "extra_permissions": ["accounting.home.view", "reports.view"],
        "denied_permissions": [],
        "accounting_permissions": [
            "accounting.home.view",
            "accounting.settlements.view",
            "unknown.permission",
        ],
    }
    assert accounting_permissions_for_user(user) == [
        "accounting.home.view",
        "accounting.settlements.view",
    ]
    assert len(accounting_permissions_for_user({"role": "owner"})) == len(
        ACCOUNTING_PERMISSION_KEYS
    )


def test_status_never_invents_balances_or_cutover_readiness():
    status = build_accounting_module_status({}, provider_summary={"providers": 9})
    assert status["operation_id"] == OPERATION_ID
    assert status["legacy_financial_data_included"] is False
    assert status["cutover"]["ready_for_activation"] is False
    assert status["cutover"]["active"] is False
    assert status["balance_visibility"]["status"] == "blocked"
    assert status["balance_visibility"]["banks"] is None
    assert status["balance_visibility"]["providers"] is None
    assert status["balance_visibility"]["couriers_cod"] is None
    assert status["review_count"] > 0


def test_status_rejects_naive_cutover_and_flags_unsafe_activation():
    status = build_accounting_module_status({
        "operation_id": OPERATION_ID,
        "status": "active",
        "cutover_at": "2026-08-23T23:00:00",
    })
    assert status["cutover"]["cutover_at"] is None
    assert status["cutover"]["active"] is False
    assert status["cutover"]["safe_active"] is False

    unsafe = build_accounting_module_status({
        "operation_id": OPERATION_ID,
        "status": "active",
        "cutover_at": "2026-08-23T23:00:00+03:00",
    })
    assert unsafe["cutover"]["active"] is True
    assert unsafe["cutover"]["safe_active"] is False
    assert unsafe["cutover"]["unsafe_activation_detected"] is True
    assert unsafe["balance_visibility"]["status"] == "blocked"


def test_approval_without_verified_opening_journal_keeps_balances_blocked():
    status = build_accounting_module_status(
        _complete_cutover(),
        opening_posted_verified=False,
        ledger_balances={"banks": 100},
    )
    assert status["cutover"]["ready_for_activation"] is False
    assert status["cutover"]["safe_active"] is False
    assert status["balance_visibility"]["status"] == "blocked"
    assert status["balance_visibility"]["banks"] is None


def test_status_only_unlocks_after_verified_opening_and_ledger_summary():
    status = build_accounting_module_status(
        _complete_cutover(),
        opening_posted_verified=True,
        ledger_balances={
            "banks": 1000.25,
            "providers": 420.50,
            "couriers_cod": -75.0,
            "couriers_cod_receivable": 125.0,
            "couriers_payable": 200.0,
            "unclassified_count": 0,
        },
    )
    assert status["cutover"]["ready_for_activation"] is True
    assert status["cutover"]["safe_active"] is True
    assert status["balance_visibility"]["status"] == "available"
    assert status["balance_visibility"]["source"] == "general_ledger_operation_scoped"
    assert status["balance_visibility"]["banks"] == 1000.25
    assert status["balance_visibility"]["providers"] == 420.50
    assert status["balance_visibility"]["couriers_cod"] == -75.0
    assert status["tasks"] == []


def test_home_ledger_summary_uses_signed_bank_and_courier_balances():
    summary = summarize_accounting_home_ledger(
        [
            {"entity_type": "bank", "entity_id": "bank-1", "sub_account": "main", "net": 5000},
            {"entity_type": "bank", "entity_id": "cash-1", "sub_account": "main", "net": 250},
            {"entity_type": "bank", "entity_id": "salla-1", "sub_account": "main", "net": 700},
            {"entity_type": "payment_gateway", "entity_id": "tamara", "sub_account": "receivable", "net": 300},
            {"entity_type": "payment_gateway", "entity_id": "tabby", "sub_account": "receivable", "net": -10},
            {"entity_type": "courier", "entity_id": "smsa", "sub_account": "cod_receivable", "net": 900},
            {"entity_type": "courier", "entity_id": "smsa", "sub_account": "payable", "net": -200},
            {"entity_type": "store_driver", "entity_id": "driver-1", "sub_account": "cod_receivable", "net": 100},
            {"entity_type": "store_driver", "entity_id": "driver-1", "sub_account": "delivery_fee_payable", "net": -75},
            {"entity_type": "bank", "entity_id": "unknown", "sub_account": "main", "net": 999},
            {"entity_type": "revenue", "entity_id": "sales", "sub_account": "", "net": -1000},
        ],
        account_types={
            "bank-1": "bank",
            "cash-1": "cash",
            "salla-1": "payment_platform",
        },
    )
    assert summary == {
        "banks": 5250.0,
        "providers": 1000.0,
        "couriers_cod": 725.0,
        "couriers_cod_receivable": 1000.0,
        "couriers_payable": 275.0,
        "unclassified_count": 1,
    }


def test_router_registers_readiness_and_permission_contract_paths():
    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    router = make_financial_provider_apps_router(object(), current_user)
    routes = {(route.path, frozenset(route.methods or [])) for route in router.routes}
    expected = {
        "/financial-provider-apps/accounting-module/access",
        "/financial-provider-apps/accounting-module/status",
        "/financial-provider-apps/accounting-module/permissions/catalogue",
        "/financial-provider-apps/accounting-module/permissions/users",
        "/financial-provider-apps/accounting-module/permissions/users/{user_id}",
    }
    assert expected <= {path for path, _methods in routes}
