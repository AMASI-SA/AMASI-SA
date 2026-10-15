import {
    ACCOUNTING_ACTIONS,
    ACCOUNTING_EXPLICIT_GRANT_PERMISSIONS,
    ACCOUNTING_PAGES,
    accountingNavItems,
    accountingPageFromSearchParams,
    userCanAccessAccounting,
} from "./accountingPages";

test("accounting module exposes the exact approved nine pages in order", () => {
    expect(ACCOUNTING_PAGES.map((page) => page.label)).toEqual([
        "المحاسبة اليومية",
        "الصناديق والحسابات المالية",
        "التسويات",
        "الشحن والتحصيل",
        "المخزون والمشتريات",
        "الحركات المالية",
        "الرواتب والالتزامات",
        "الأرصدة الافتتاحية",
        "القيود والتقارير",
    ]);
    expect(new Set(ACCOUNTING_PAGES.map((page) => page.permission)).size).toBe(9);
    expect(accountingNavItems()).toHaveLength(9);
    expect(accountingNavItems().every((item) => item.to.includes("workspace=financial"))).toBe(true);
});

test("P01 home, settlements and opening balances are implemented", () => {
    const implemented = ACCOUNTING_PAGES
        .filter((page) => page.implementationStatus === "implemented")
        .map((page) => page.id);
    expect(implemented).toEqual(["home", "financial-accounts", "settlements", "shipping-cod", "financial-movements", "payroll-obligations", "opening-balances"]);
    expect(ACCOUNTING_PAGES.find((page) => page.id === "shipping-cod")?.implementationStatus)
        .toBe("implemented");
    expect(ACCOUNTING_PAGES.find((page) => page.id === "opening-balances")?.implementationStatus)
        .toBe("implemented");
});

test("unknown or missing page query fails safely to accounting home", () => {
    expect(accountingPageFromSearchParams(new URLSearchParams("page=unknown")).id).toBe("home");
    expect(accountingPageFromSearchParams(new URLSearchParams()).id).toBe("home");
});

test("all non-owner roles are denied until dedicated accounting assignment arrives", () => {
    for (const role of ["admin", "accountant", "operations", "viewer", "employee"]) {
        expect(userCanAccessAccounting(
            { role, permissions: ["accounting.home.view"] },
            "accounting.home.view",
            [],
        )).toBe(false);
    }
    expect(userCanAccessAccounting(
        { role: "accountant" },
        "accounting.home.view",
        ["accounting.home.view"],
    )).toBe(true);
    expect(userCanAccessAccounting({ role: "owner" }, "accounting.home.view", [])).toBe(true);
});

test("owner gets only setup additions; financial authorities stay explicit in both contracts", () => {
    const setup = [
        "accounting.financial_accounts.view", "accounting.financial_accounts.manage",
        "accounting.opening_balances.view", "accounting.opening_balances.drafts.manage",
        "accounting.opening_balances.review",
    ];
    const sensitive = [
        "accounting.opening_balances.post", "accounting.journals.reverse",
        "accounting.ledger_transition.manage", "accounting.shipping.contracts.review",
    ];
    expect(new Set(ACCOUNTING_EXPLICIT_GRANT_PERMISSIONS)).toEqual(new Set(sensitive));
    for (const permission of [...setup, ...sensitive]) {
        expect(userCanAccessAccounting({ role: "owner" }, permission, [])).toBe(setup.includes(permission));
        expect(userCanAccessAccounting({ role: "employee" }, permission, [])).toBe(false);
        expect(userCanAccessAccounting({ role: "employee" }, permission, [permission])).toBe(true);
    }
    for (const permission of sensitive) {
        expect(userCanAccessAccounting({ role: "owner" }, permission, setup)).toBe(false);
        expect(userCanAccessAccounting({ role: "owner" }, permission, [permission])).toBe(true);
    }
    const opening = accountingPageFromSearchParams(new URLSearchParams("workspace=financial&page=opening-balances"));
    expect(userCanAccessAccounting({ role: "owner" }, opening.permission)).toBe(true);
});

test("sensitive actions remain separate from page access", () => {
    const actionPermissions = ACCOUNTING_ACTIONS.map((action) => action.permission);
    expect(actionPermissions).toContain("accounting.opening_balances.approve");
    expect(actionPermissions).toContain("accounting.movements.import");
    expect(actionPermissions).toContain("accounting.journals.manual_create");
    expect(actionPermissions).toContain("accounting.journals.reverse");
    expect(actionPermissions).toContain("accounting.financial_accounts.manage");
    expect(actionPermissions).toContain("accounting.opening_balances.drafts.manage");
    expect(actionPermissions).toContain("accounting.opening_balances.review");
    expect(actionPermissions).toContain("accounting.opening_balances.post");
    expect(actionPermissions).toContain("accounting.ledger_transition.manage");
    expect(actionPermissions).not.toContain("accounting.opening_balances.view");
});
