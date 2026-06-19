"""Iter-250a — Financial Pages Inventory (READ-ONLY).

Single source-of-truth list classifying every financial-impact page
and endpoint in the system.  Consumed by:

  • GET /api/audit/financial-pages-inventory  (live JSON view)
  • /app/docs/FINANCIAL_PAGES_INVENTORY.md    (static doc, generated
    from this list by `scripts/regen_inventory_doc.py`)

⚠️ Inventory only — no logic changes, no writes.

Field schema for every entry:
  area               — domain bucket (banks / bnpl / suppliers / ...)
  frontend_route     — React-Router path (or null for backend-only)
  frontend_file      — relative path under /app/frontend/src/pages
  backend_endpoints  — list of API routes that feed the page
  data_source        — one of: general_ledger | account_transactions
                       | financial_movements | accounts.current_balance
                       | mixed | external | config_only
  ssot_status        — SSOT | LEGACY | DIAGNOSTIC | CONFIG | EXTERNAL
  affects_balance    — True if the page can mutate balances
  classification     — KEEP | MERGE | DEPRECATE | DELETE
  hide_safety        — SAFE_TO_HIDE | NEEDS_REDIRECT | NEEDS_REVIEW
                       | KEEP_VISIBLE
  replacement        — recommended successor (null if none)
  risk               — LOW | MEDIUM | HIGH
  reason             — short Arabic rationale
"""
from __future__ import annotations

from typing import Any, Dict, List


# NOTE: ordering inside each section is meaningful for the docs.
INVENTORY: List[Dict[str, Any]] = [
    # ════════════ 1. البنوك والحسابات ════════════
    {
        "area": "banks_and_accounts",
        "frontend_route": "/accounts",
        "frontend_file": "pages/Accounts.jsx",
        "backend_endpoints": [
            "GET /api/accounts",
            "POST /api/accounts",
            "GET /api/accounts/summary",
        ],
        "data_source": "mixed",
        "ssot_status": "SSOT",
        "affects_balance": True,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": (
            "نقطة الدخول الرئيسية لإدارة الحسابات. ترتد على "
            "account_balance_ssot() لذا متوافقة مع SSOT."
        ),
    },
    {
        "area": "banks_and_accounts",
        "frontend_route": "/accounts/:id",
        "frontend_file": "pages/AccountDetails.jsx",
        "backend_endpoints": [
            "GET /api/accounts/{id}",
            "GET /api/accounts/{id}/transactions",
            "POST /api/accounts/{id}/transactions",
            "DELETE /api/accounts/{id}/transactions/{tx_id}",
            "GET /api/accounts/{id}/breakdown",
        ],
        "data_source": "mixed",
        "ssot_status": "LEGACY",
        "affects_balance": True,
        "classification": "MERGE",
        "replacement": (
            "دمج _ledger_based_tx_feed + legacy walker إلى مصدر "
            "موحّد بعد Reset"
        ),
        "risk": "HIGH",
        "reason": (
            "ينقسم بين فرعين (is_migrated → ledger، غير ذلك → "
            "account_transactions). أصل مشكلة Iter-249. لا يجب "
            "إبقاء فرعين."
        ),
    },
    {
        "area": "banks_and_accounts",
        "frontend_route": "/financial-position",
        "frontend_file": "pages/FinancialPosition.jsx",
        "backend_endpoints": [
            "GET /api/accounting/financial-position",
        ],
        "data_source": "mixed",
        "ssot_status": "LEGACY",
        "affects_balance": False,
        "classification": "DEPRECATE",
        "replacement": "/financial-position-ledger",
        "risk": "MEDIUM",
        "reason": (
            "تقرير المركز المالي القديم. تم استبداله بالنسخة "
            "المبنية على ledger مباشرة."
        ),
    },
    {
        "area": "banks_and_accounts",
        "frontend_route": "/financial-position-ledger",
        "frontend_file": "pages/FinancialPositionLedger.jsx",
        "backend_endpoints": [
            "GET /api/accounting/financial-position-ledger",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "البديل الـ SSOT لتقرير المركز المالي.",
    },

    # ════════════ 2. التحويلات والإدخالات ════════════
    {
        "area": "transfers_and_entries",
        "frontend_route": "/new-transaction",
        "frontend_file": "pages/UnifiedEntryScreen.jsx",
        "backend_endpoints": [
            "POST /api/financial-movements",
            "GET /api/financial-movements/accounts-with-availability",
        ],
        "data_source": "financial_movements",
        "ssot_status": "SSOT",
        "affects_balance": True,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": (
            "نقطة إدخال الحركة الموحّدة (SSOT). يجب توجيه كل "
            "الكتابات الجديدة هنا."
        ),
    },
    {
        "area": "transfers_and_entries",
        "frontend_route": "/financial-movements",
        "frontend_file": "pages/FinancialMovementsListPage.jsx",
        "backend_endpoints": [
            "GET /api/financial-movements",
        ],
        "data_source": "financial_movements",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "العرض القانوني لجدول financial_movements.",
    },
    {
        "area": "transfers_and_entries",
        "frontend_route": "/financial-movement/new",
        "frontend_file": "pages/FinancialMovementNewPage.jsx",
        "backend_endpoints": [
            "POST /api/financial-movements",
        ],
        "data_source": "financial_movements",
        "ssot_status": "SSOT",
        "affects_balance": True,
        "classification": "MERGE",
        "replacement": "/new-transaction (UnifiedEntryScreen)",
        "risk": "LOW",
        "reason": (
            "نسخة قديمة من شاشة الإدخال. ندمج الوظيفتين تحت "
            "/new-transaction لتجنّب الازدواج."
        ),
    },
    {
        "area": "transfers_and_entries",
        "frontend_route": "/transfers",
        "frontend_file": "pages/Transfers.jsx",
        "backend_endpoints": [
            "GET /api/transfers",
            "POST /api/transfers",
        ],
        "data_source": "mixed",
        "ssot_status": "LEGACY",
        "affects_balance": True,
        "classification": "DEPRECATE",
        "replacement": "/new-transaction (type=internal_transfer)",
        "risk": "MEDIUM",
        "reason": (
            "تكتب في account_transactions + general_ledger. "
            "/new-transaction يغطي نفس الحالة بدون ازدواج كتابة."
        ),
    },
    {
        "area": "transfers_and_entries",
        "frontend_route": "/financial-input-hub",
        "frontend_file": "pages/FinancialInputHub.jsx",
        "backend_endpoints": [
            "GET /api/financial-input-hub",
        ],
        "data_source": "mixed",
        "ssot_status": "LEGACY",
        "affects_balance": False,
        "classification": "DEPRECATE",
        "replacement": "/new-transaction + /financial-movements",
        "risk": "LOW",
        "reason": (
            "صفحة قديمة تجمع liabilities + account_transactions. "
            "العرض والإدخال الموحّد البديل أفضل."
        ),
    },
    {
        "area": "transfers_and_entries",
        "frontend_route": "/transactions",
        "frontend_file": "pages/LedgerTransactionsPage.jsx",
        "backend_endpoints": [
            "GET /api/ledger/transactions",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "العرض الموحّد لكل قيود الـ ledger.",
    },

    # ════════════ 3. BNPL (Tamara/Tabby) ════════════
    {
        "area": "bnpl",
        "frontend_route": "/bnpl-settlements",
        "frontend_file": "pages/BnplSettlements.jsx",
        "backend_endpoints": [
            "GET /api/bnpl/settlements",
            "POST /api/bnpl/settlements",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": True,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": (
            "نقطة تسجيل التسوية الكنونية. تكتب الـ ledger مباشرة "
            "عبر settlement_bridge."
        ),
    },
    {
        "area": "bnpl",
        "frontend_route": "/bnpl-settlements/register",
        "frontend_file": "pages/BnplSettlementsRegister.jsx",
        "backend_endpoints": [
            "POST /api/bnpl/settlements",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": True,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "صفحة تسجيل تسوية واحدة (form).",
    },
    {
        "area": "bnpl",
        "frontend_route": "/integrations/bnpl",
        "frontend_file": "pages/BnplIntegrations.jsx",
        "backend_endpoints": [
            "GET /api/bnpl/config",
            "PUT /api/bnpl/config",
        ],
        "data_source": "config_only",
        "ssot_status": "CONFIG",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "إعدادات وربط Tamara/Tabby.",
    },
    {
        "area": "bnpl",
        "frontend_route": "/integrations/bnpl/diagnostics",
        "frontend_file": "pages/BnplDiagnostics.jsx",
        "backend_endpoints": [
            "GET /api/bnpl/audit/*",
        ],
        "data_source": "general_ledger",
        "ssot_status": "DIAGNOSTIC",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تشخيصات قراءة فقط.",
    },
    {
        "area": "bnpl",
        "frontend_route": "/bnpl-balances",
        "frontend_file": "pages/BnplBalancesDiagnostic.jsx",
        "backend_endpoints": [
            "GET /api/bnpl/diagnostics/balances",
        ],
        "data_source": "general_ledger",
        "ssot_status": "DIAGNOSTIC",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تشخيص رصيد BNPL.",
    },
    {
        "area": "bnpl",
        "frontend_route": "/refund-audit",
        "frontend_file": "pages/RefundAudit.jsx",
        "backend_endpoints": [
            "GET /api/bnpl/refund-audit",
        ],
        "data_source": "general_ledger",
        "ssot_status": "DIAGNOSTIC",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تدقيق المرتجعات (قراءة فقط).",
    },

    # ════════════ 4. الحسابات الإعلانية ════════════
    {
        "area": "ad_accounts",
        "frontend_route": "/ad-accounts",
        "frontend_file": "pages/AdAccounts.jsx",
        "backend_endpoints": [
            "GET /api/ad-accounts",
            "POST /api/ad-accounts/{id}/topup",
            "POST /api/ad-accounts/{id}/charges",
        ],
        "data_source": "mixed",
        "ssot_status": "LEGACY",
        "affects_balance": True,
        "classification": "MERGE",
        "replacement": (
            "توحيد عبر /new-transaction + ad_account_ledger sub"
        ),
        "risk": "HIGH",
        "reason": (
            "يكتب في general_ledger (sub_account=balance) + "
            "account_transactions + يحدّث current_balance لـ "
            "counterparty. ثلاثة مسارات متوازية — مصدر تضارب "
            "حقيقي مع نموذج Iter-249."
        ),
    },
    {
        "area": "ad_accounts",
        "frontend_route": "/snapchat-accounts",
        "frontend_file": "pages/SnapchatAccounts.jsx",
        "backend_endpoints": [
            "GET /api/snapchat/accounts-summary",
            "POST /api/snapchat/sync",
        ],
        "data_source": "external",
        "ssot_status": "EXTERNAL",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": (
            "مزامنة قراءة من Snapchat API. لا تكتب في الـ ledger."
        ),
    },
    {
        "area": "ad_accounts",
        "frontend_route": "/audit/ad-debt",
        "frontend_file": "pages/AdDebtDiagnostic.jsx",
        "backend_endpoints": [
            "GET /api/audit/ad-debt",
        ],
        "data_source": "general_ledger",
        "ssot_status": "DIAGNOSTIC",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تشخيص ديون الإعلانات.",
    },
    {
        "area": "ad_accounts",
        "frontend_route": "/settings/ads-currencies",
        "frontend_file": "pages/AdsCurrencySettings.jsx",
        "backend_endpoints": ["GET/PUT /api/ads/currencies"],
        "data_source": "config_only",
        "ssot_status": "CONFIG",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تكوين أسعار الصرف للحسابات الإعلانية.",
    },

    # ════════════ 5. الموردين ════════════
    {
        "area": "suppliers",
        "frontend_route": "/counterparties",
        "frontend_file": "pages/Counterparties.jsx",
        "backend_endpoints": [
            "GET /api/counterparties",
            "POST /api/counterparties",
            "POST /api/counterparties/{id}/payments",
        ],
        "data_source": "mixed",
        "ssot_status": "LEGACY",
        "affects_balance": True,
        "classification": "DEPRECATE",
        "replacement": "/suppliers-new + /suppliers-ledger",
        "risk": "MEDIUM",
        "reason": (
            "يكتب في liabilities + account_transactions ويعدّل "
            "current_balance. النموذج الحديث (suppliers-new) "
            "يستخدم financial_movements."
        ),
    },
    {
        "area": "suppliers",
        "frontend_route": "/suppliers-new",
        "frontend_file": "pages/SuppliersPage.jsx",
        "backend_endpoints": [
            "GET /api/suppliers",
            "POST /api/suppliers",
        ],
        "data_source": "financial_movements",
        "ssot_status": "SSOT",
        "affects_balance": True,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": (
            "النموذج الحديث للموردين. يكتب عبر "
            "financial_movements (SSOT)."
        ),
    },
    {
        "area": "suppliers",
        "frontend_route": "/suppliers-ledger",
        "frontend_file": "pages/SuppliersLedger.jsx",
        "backend_endpoints": [
            "GET /api/suppliers/ledger",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "كشف حساب الموردين من الـ ledger مباشرة.",
    },
    {
        "area": "suppliers",
        "frontend_route": "/purchase-invoices",
        "frontend_file": "pages/PurchaseInvoices.jsx",
        "backend_endpoints": [
            "GET /api/purchase-invoices",
            "POST /api/purchase-invoices",
        ],
        "data_source": "mixed",
        "ssot_status": "LEGACY",
        "affects_balance": True,
        "classification": "MERGE",
        "replacement": "/suppliers-new (نموذج فاتورة موحّد)",
        "risk": "MEDIUM",
        "reason": (
            "يكتب في liabilities + account_transactions. النموذج "
            "الجديد يجب أن يتولّى دورة الفاتورة كاملة."
        ),
    },
    {
        "area": "suppliers",
        "frontend_route": "/reports/suppliers",
        "frontend_file": "pages/SuppliersReportPage.jsx",
        "backend_endpoints": [
            "GET /api/reports/suppliers",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تقرير قراءة فقط من ledger.",
    },

    # ════════════ 6. الموظفين والرواتب ════════════
    {
        "area": "employees_and_salaries",
        "frontend_route": "/employees-ledger",
        "frontend_file": "pages/EmployeesLedger.jsx",
        "backend_endpoints": [
            "GET /api/employees/ledger",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "كشف الموظف من ledger.",
    },
    {
        "area": "employees_and_salaries",
        "frontend_route": "/employees/custody-balances",
        "frontend_file": "pages/CustodyOpenBalances.jsx",
        "backend_endpoints": [
            "GET /api/employees/custody-balances",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "عُهد الموظفين من ledger.",
    },
    {
        "area": "employees_and_salaries",
        "frontend_route": "/advances",
        "frontend_file": "pages/Advances.jsx",
        "backend_endpoints": [
            "GET /api/advances",
            "POST /api/advances",
        ],
        "data_source": "mixed",
        "ssot_status": "LEGACY",
        "affects_balance": True,
        "classification": "DEPRECATE",
        "replacement": "/new-transaction (type=salary_advance)",
        "risk": "MEDIUM",
        "reason": (
            "يكتب في liabilities + account_transactions. ندمج مع "
            "/new-transaction."
        ),
    },
    {
        "area": "employees_and_salaries",
        "frontend_route": "/operating-expenses",
        "frontend_file": "pages/OperatingExpenses.jsx",
        "backend_endpoints": [
            "GET /api/operating-salaries",
            "POST /api/operating-salaries",
        ],
        "data_source": "config_only",
        "ssot_status": "CONFIG",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": (
            "إدارة الموظفين الأساسية (بيانات مرجعية فقط، الراتب "
            "اليومي يحتسب لاحقاً)."
        ),
    },
    {
        "area": "employees_and_salaries",
        "frontend_route": "/employee-corrections",
        "frontend_file": "pages/EmployeeCorrections.jsx",
        "backend_endpoints": [
            "POST /api/employees/{id}/correction",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": True,
        "classification": "KEEP",
        "replacement": None,
        "risk": "MEDIUM",
        "reason": (
            "تصحيح قيود الموظفين عبر ledger correction "
            "(Iter-196). أداة إدارية."
        ),
    },
    {
        "area": "employees_and_salaries",
        "frontend_route": "/salary-reversals",
        "frontend_file": "pages/SalaryReversals.jsx",
        "backend_endpoints": [
            "POST /api/employees/salary-reversal",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": True,
        "classification": "KEEP",
        "replacement": None,
        "risk": "MEDIUM",
        "reason": "عكس قيود رواتب (admin).",
    },
    {
        "area": "employees_and_salaries",
        "frontend_route": "/audit/employee-orphans",
        "frontend_file": "pages/EmployeeOrphanDiagnostic.jsx",
        "backend_endpoints": [
            "GET /api/audit/employee-orphans",
        ],
        "data_source": "general_ledger",
        "ssot_status": "DIAGNOSTIC",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تشخيص قيود الموظفين اليتيمة.",
    },

    # ════════════ 7. شركات الشحن ════════════
    {
        "area": "shipping",
        "frontend_route": "/shipping-accounts",
        "frontend_file": "pages/ShippingAccounts.jsx",
        "backend_endpoints": [
            "GET /api/shipping-accounts",
            "POST /api/shipping-accounts/{id}/payments",
        ],
        "data_source": "mixed",
        "ssot_status": "LEGACY",
        "affects_balance": True,
        "classification": "DEPRECATE",
        "replacement": (
            "/shipping/orders-ledger + /couriers-ledger"
        ),
        "risk": "MEDIUM",
        "reason": (
            "يكتب في account_transactions + ledger. تم تجاوزه "
            "بالنموذج المبني على ledger مباشرة."
        ),
    },
    {
        "area": "shipping",
        "frontend_route": "/shipping/orders-ledger",
        "frontend_file": "pages/ShippingLedger.jsx",
        "backend_endpoints": [
            "GET /api/shipping/orders-ledger",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "كشف طلبات الشحن من ledger.",
    },
    {
        "area": "shipping",
        "frontend_route": "/couriers-ledger",
        "frontend_file": "pages/CouriersLedger.jsx",
        "backend_endpoints": [
            "GET /api/couriers/ledger",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "كشف شركات الشحن من ledger.",
    },
    {
        "area": "shipping",
        "frontend_route": "/shipping/ledger",
        "frontend_file": "pages/ShippingLedgerStub.jsx",
        "backend_endpoints": [],
        "data_source": "general_ledger",
        "ssot_status": "LEGACY",
        "affects_balance": False,
        "classification": "DELETE",
        "replacement": "/shipping/orders-ledger",
        "risk": "LOW",
        "reason": "Stub فارغ يعيد توجيه للنسخة الـ ledger.",
    },
    {
        "area": "shipping",
        "frontend_route": "/shipping/transfers",
        "frontend_file": "pages/ShippingTransfers.jsx",
        "backend_endpoints": [
            "GET /api/shipping/transfers",
        ],
        "data_source": "mixed",
        "ssot_status": "LEGACY",
        "affects_balance": True,
        "classification": "MERGE",
        "replacement": "/new-transaction (type=courier_transfer)",
        "risk": "MEDIUM",
        "reason": "ندمج تحويلات الشحن مع شاشة الإدخال الموحّدة.",
    },
    {
        "area": "shipping",
        "frontend_route": "/shipping/cod-settlements",
        "frontend_file": "pages/ShippingLedgerStub.jsx",
        "backend_endpoints": [],
        "data_source": "general_ledger",
        "ssot_status": "LEGACY",
        "affects_balance": False,
        "classification": "DELETE",
        "replacement": "/shipping/orders-ledger",
        "risk": "LOW",
        "reason": "نفس الـ stub مكرّر تحت مسار آخر.",
    },
    {
        "area": "shipping",
        "frontend_route": "/shipping/settings",
        "frontend_file": "pages/ShippingCompanySettings.jsx",
        "backend_endpoints": [
            "GET/PUT /api/shipping/companies",
        ],
        "data_source": "config_only",
        "ssot_status": "CONFIG",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تكوين شركات الشحن.",
    },
    {
        "area": "shipping",
        "frontend_route": "/diagnostics/cod-source",
        "frontend_file": "pages/CODDiagnostic.jsx",
        "backend_endpoints": [
            "GET /api/diagnostics/cod-source",
        ],
        "data_source": "mixed",
        "ssot_status": "DIAGNOSTIC",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تشخيص COD (قراءة فقط).",
    },

    # ════════════ 8. الذمم والعملاء (Receivables) ════════════
    {
        "area": "receivables",
        "frontend_route": "/receivables",
        "frontend_file": "pages/Receivables.jsx",
        "backend_endpoints": [
            "GET /api/receivables",
            "POST /api/receivables/{id}/collect",
        ],
        "data_source": "mixed",
        "ssot_status": "LEGACY",
        "affects_balance": True,
        "classification": "MERGE",
        "replacement": (
            "/new-transaction (type=receivable_collect) + "
            "/externals-ledger"
        ),
        "risk": "MEDIUM",
        "reason": (
            "يستخدم liabilities + account_transactions. الكيان "
            "الخارجي يجب أن يُدار من /externals-ledger."
        ),
    },
    {
        "area": "receivables",
        "frontend_route": "/externals-ledger",
        "frontend_file": "pages/ExternalsLedger.jsx",
        "backend_endpoints": [
            "GET /api/externals/ledger",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "كشف الأطراف الخارجية من ledger.",
    },

    # ════════════ 9. التسويات (Payment Settlements) ════════════
    {
        "area": "settlements",
        "frontend_route": "/settlements",
        "frontend_file": "pages/Settlements.jsx",
        "backend_endpoints": [
            "GET /api/settlements",
        ],
        "data_source": "account_transactions",
        "ssot_status": "LEGACY",
        "affects_balance": False,
        "classification": "DEPRECATE",
        "replacement": "/settlements-overview أو /salla-settlements",
        "risk": "LOW",
        "reason": "صفحة قديمة لتسويات سلة.",
    },
    {
        "area": "settlements",
        "frontend_route": "/payment-settlements",
        "frontend_file": "pages/PaymentSettlements.jsx",
        "backend_endpoints": [
            "GET /api/payment-settlements",
        ],
        "data_source": "account_transactions",
        "ssot_status": "LEGACY",
        "affects_balance": False,
        "classification": "MERGE",
        "replacement": "/settlements-overview",
        "risk": "LOW",
        "reason": "نظرة عامة عن تسويات منصات الدفع.",
    },
    {
        "area": "settlements",
        "frontend_route": "/salla-settlements",
        "frontend_file": "pages/SallaSettlements.jsx",
        "backend_endpoints": [
            "GET /api/salla/settlements",
        ],
        "data_source": "external",
        "ssot_status": "EXTERNAL",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "مزامنة تسويات سلة من API الخارجي.",
    },
    {
        "area": "settlements",
        "frontend_route": "/settlements-overview",
        "frontend_file": "pages/SettlementsOverview.jsx",
        "backend_endpoints": [
            "GET /api/settlements/overview",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "نظرة عامة شاملة (ledger-based).",
    },
    {
        "area": "settlements",
        "frontend_route": "/reconciliation",
        "frontend_file": "pages/Reconciliation.jsx",
        "backend_endpoints": [
            "GET /api/reconciliation",
        ],
        "data_source": "mixed",
        "ssot_status": "LEGACY",
        "affects_balance": False,
        "classification": "DEPRECATE",
        "replacement": "/accounting/reconciliation",
        "risk": "LOW",
        "reason": (
            "النسخة القديمة من المطابقة (يقرأ AT + current_"
            "balance). تم استبدالها بـ forensic ledger."
        ),
    },
    {
        "area": "settlements",
        "frontend_route": "/accounting/reconciliation",
        "frontend_file": "pages/ReconciliationReport.jsx",
        "backend_endpoints": [
            "GET /api/accounting/reconciliation-forensic",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "المطابقة القانونية (ledger-only).",
    },

    # ════════════ 10. المصروفات اليومية ════════════
    {
        "area": "expenses",
        "frontend_route": "/daily-costs",
        "frontend_file": "pages/DailyCosts.jsx",
        "backend_endpoints": [
            "GET /api/daily-costs",
            "POST /api/daily-costs",
        ],
        "data_source": "external",
        "ssot_status": "EXTERNAL",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": (
            "تكاليف يومية لتحليل P&L. كيان منفصل خارج الـ "
            "ledger."
        ),
    },
    {
        "area": "expenses",
        "frontend_route": "/expense-categories-tree",
        "frontend_file": "pages/ExpenseCategoryTreePage.jsx",
        "backend_endpoints": [
            "GET/POST /api/expense-categories",
        ],
        "data_source": "config_only",
        "ssot_status": "CONFIG",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "إدارة شجرة فئات المصروفات.",
    },
    {
        "area": "expenses",
        "frontend_route": "/expense-reversals",
        "frontend_file": "pages/ExpenseReversals.jsx",
        "backend_endpoints": [
            "POST /api/expenses/reverse",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": True,
        "classification": "KEEP",
        "replacement": None,
        "risk": "MEDIUM",
        "reason": "عكس قيد مصروف (admin).",
    },

    # ════════════ 11. التقارير ════════════
    {
        "area": "reports",
        "frontend_route": "/reports",
        "frontend_file": "pages/Reports.jsx",
        "backend_endpoints": ["GET /api/reports"],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تقارير عامة قراءة فقط.",
    },
    {
        "area": "reports",
        "frontend_route": "/reports/ads",
        "frontend_file": "pages/AdsReport.jsx",
        "backend_endpoints": ["GET /api/reports/ads"],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تقرير الإعلانات.",
    },
    {
        "area": "reports",
        "frontend_route": "/reports/advertising-expenses",
        "frontend_file": "pages/AdvertisingExpensesReport.jsx",
        "backend_endpoints": [
            "GET /api/reports/advertising-expenses",
        ],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تقرير مصروفات الإعلانات.",
    },
    {
        "area": "reports",
        "frontend_route": "/operational-reports",
        "frontend_file": "pages/OperationalReports.jsx",
        "backend_endpoints": ["GET /api/operational-reports"],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تقارير تشغيلية مجمّعة.",
    },
    {
        "area": "reports",
        "frontend_route": "/legacy-usage-report",
        "frontend_file": "pages/LegacyUsageReportPage.jsx",
        "backend_endpoints": [
            "GET /api/audit/legacy-usage",
        ],
        "data_source": "mixed",
        "ssot_status": "DIAGNOSTIC",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": (
            "تشخيص استخدام الصفحات القديمة (يساعد في تنظيف "
            "Iter-250)."
        ),
    },

    # ════════════ 12. الإعدادات والتشخيصات ════════════
    {
        "area": "admin_diagnostics",
        "frontend_route": "/audit/ledger-health",
        "frontend_file": "pages/LedgerHealthDiagnostic.jsx",
        "backend_endpoints": [
            "GET /api/audit/ledger-health",
        ],
        "data_source": "general_ledger",
        "ssot_status": "DIAGNOSTIC",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "صحة الـ ledger العامة.",
    },
    {
        "area": "admin_diagnostics",
        "frontend_route": "/audit/post-migration",
        "frontend_file": "pages/PostMigrationAudit.jsx",
        "backend_endpoints": [
            "GET /api/audit/post-migration",
        ],
        "data_source": "general_ledger",
        "ssot_status": "DIAGNOSTIC",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تدقيق بعد الـ migration.",
    },
    {
        "area": "admin_diagnostics",
        "frontend_route": "/settings/accounting-cutoffs",
        "frontend_file": "pages/AccountingCutoffs.jsx",
        "backend_endpoints": [
            "GET/PUT /api/settings/accounting-cutoffs",
        ],
        "data_source": "config_only",
        "ssot_status": "CONFIG",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": (
            "تواريخ القطع المحاسبية. سيستخدمها Reset لاحقاً."
        ),
    },
    {
        "area": "admin_diagnostics",
        "frontend_route": "/accounting/migration",
        "frontend_file": "pages/MigrationWizard.jsx",
        "backend_endpoints": [
            "POST /api/accounting/migrate-*",
        ],
        "data_source": "general_ledger",
        "ssot_status": "LEGACY",
        "affects_balance": True,
        "classification": "DEPRECATE",
        "replacement": None,
        "risk": "HIGH",
        "reason": (
            "أداة هجرة استُخدمت مرة واحدة. خطر إعادة تشغيلها "
            "بالخطأ. يجب إخفاؤها من القائمة بعد Iter-250."
        ),
    },
    {
        "area": "admin_diagnostics",
        "frontend_route": "/diagnostics/api-permissions",
        "frontend_file": "pages/ApiPermissionsDiagnostic.jsx",
        "backend_endpoints": [
            "GET /api/diagnostics/api-permissions",
        ],
        "data_source": "config_only",
        "ssot_status": "DIAGNOSTIC",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تشخيص صلاحيات الـ API.",
    },
    {
        "area": "admin_diagnostics",
        "frontend_route": "/settings/operation-account-bindings",
        "frontend_file": "pages/OperationAccountBindings.jsx",
        "backend_endpoints": [
            "GET/PUT /api/settings/op-bindings",
        ],
        "data_source": "config_only",
        "ssot_status": "CONFIG",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "ربط أنواع العمليات بالحسابات.",
    },
    {
        "area": "admin_diagnostics",
        "frontend_route": "/alerts",
        "frontend_file": "pages/AlertsPage.jsx",
        "backend_endpoints": ["GET /api/alerts"],
        "data_source": "general_ledger",
        "ssot_status": "DIAGNOSTIC",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "تنبيهات النظام.",
    },
    {
        "area": "admin_diagnostics",
        "frontend_route": "/operations-dashboard",
        "frontend_file": "pages/OperationsDashboard.jsx",
        "backend_endpoints": ["GET /api/dashboard"],
        "data_source": "general_ledger",
        "ssot_status": "SSOT",
        "affects_balance": False,
        "classification": "KEEP",
        "replacement": None,
        "risk": "LOW",
        "reason": "لوحة تشغيلية رئيسية.",
    },
]


def _auto_hide_safety(item: Dict[str, Any]) -> str:
    """Iter-250a — auto-classify each row for Sidebar/Route hiding.

    Rules:
      • KEEP                       → KEEP_VISIBLE
      • DELETE                     → NEEDS_REDIRECT (route still exists
                                     so the user sees a banner instead
                                     of a hard 404)
      • DEPRECATE + risk LOW/MED   → SAFE_TO_HIDE
      • DEPRECATE + risk HIGH      → NEEDS_REVIEW (one-shot tools like
                                     migration wizard — hide deferred)
      • MERGE                      → NEEDS_REVIEW (active workflow,
                                     requires manual merge before
                                     hiding)
    """
    cl = item["classification"]
    risk = item["risk"]
    if cl == "KEEP":
        return "KEEP_VISIBLE"
    if cl == "DELETE":
        return "NEEDS_REDIRECT"
    if cl == "DEPRECATE":
        return "NEEDS_REVIEW" if risk == "HIGH" else "SAFE_TO_HIDE"
    if cl == "MERGE":
        return "NEEDS_REVIEW"
    return "NEEDS_REVIEW"


# Apply hide_safety to every row right after the list is defined.
for _it in INVENTORY:
    _it.setdefault("hide_safety", _auto_hide_safety(_it))


def summary() -> Dict[str, Any]:
    """Compute aggregate metrics over INVENTORY."""
    total = len(INVENTORY)
    classifications: Dict[str, int] = {
        "KEEP": 0, "MERGE": 0, "DEPRECATE": 0, "DELETE": 0,
    }
    by_area: Dict[str, int] = {}
    by_data_source: Dict[str, int] = {}
    by_risk: Dict[str, int] = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    affecting_balance_legacy = 0

    high_risk_duplicates: List[Dict[str, Any]] = []
    _by_hide_safety: Dict[str, int] = {}
    for it in INVENTORY:
        classifications[it["classification"]] = (
            classifications.get(it["classification"], 0) + 1
        )
        by_area[it["area"]] = by_area.get(it["area"], 0) + 1
        by_data_source[it["data_source"]] = (
            by_data_source.get(it["data_source"], 0) + 1
        )
        by_risk[it["risk"]] = by_risk.get(it["risk"], 0) + 1
        _by_hide_safety[it["hide_safety"]] = (
            _by_hide_safety.get(it["hide_safety"], 0) + 1
        )
        if it["affects_balance"] and it["ssot_status"] == "LEGACY":
            affecting_balance_legacy += 1
        if it["risk"] == "HIGH":
            high_risk_duplicates.append({
                "route": it["frontend_route"],
                "classification": it["classification"],
                "replacement": it["replacement"],
                "reason": it["reason"],
            })

    # Recommended next cleanup batch — start with HIGH risk MERGE
    # and DEPRECATE entries that affect balance.
    next_batch: List[Dict[str, Any]] = []
    for it in INVENTORY:
        if (it["classification"] in ("MERGE", "DEPRECATE")
                and it["risk"] in ("HIGH", "MEDIUM")
                and it["affects_balance"]):
            next_batch.append({
                "route": it["frontend_route"],
                "classification": it["classification"],
                "replacement": it["replacement"],
                "risk": it["risk"],
                "reason": it["reason"],
            })
    # Top 6 sorted by risk
    risk_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    next_batch.sort(key=lambda x: risk_order.get(x["risk"], 99))
    return {
        "total_pages": total,
        "keep_count": classifications["KEEP"],
        "merge_count": classifications["MERGE"],
        "deprecate_count": classifications["DEPRECATE"],
        "delete_count": classifications["DELETE"],
        "by_area": by_area,
        "by_data_source": by_data_source,
        "by_risk": by_risk,
        "by_hide_safety": _by_hide_safety,
        "legacy_pages_affecting_balance": affecting_balance_legacy,
        "highest_risk_duplicates": high_risk_duplicates,
        "recommended_next_cleanup_batch": next_batch[:6],
        "routes_to_hide_now": [
            {"route": r["frontend_route"],
             "replacement": r["replacement"],
             "reason": r["reason"]}
            for r in INVENTORY if r["hide_safety"] == "SAFE_TO_HIDE"
        ],
        "routes_needing_redirect_stub": [
            {"route": r["frontend_route"],
             "replacement": r["replacement"]}
            for r in INVENTORY if r["hide_safety"] == "NEEDS_REDIRECT"
        ],
        "routes_needing_review": [
            {"route": r["frontend_route"],
             "classification": r["classification"],
             "replacement": r["replacement"],
             "reason": r["reason"]}
            for r in INVENTORY if r["hide_safety"] == "NEEDS_REVIEW"
        ],
    }
