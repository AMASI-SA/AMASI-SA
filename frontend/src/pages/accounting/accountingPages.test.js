import {
    ACCOUNTING_ACTIONS,
    ACCOUNTING_PAGES,
    accountingNavItems,
    accountingPageFromSearchParams,
    userCanAccessAccounting,
} from "./accountingPages";

test("accounting module exposes the exact approved eight pages in order", () => {
    expect(ACCOUNTING_PAGES.map((page) => page.label)).toEqual([
        "الرئيسية المحاسبية",
        "التسويات",
        "الشحن والتحصيل",
        "المخزون والمشتريات",
        "الحركات المالية",
        "الرواتب والالتزامات",
        "الأرصدة الافتتاحية",
        "القيود والتقارير",
    ]);
    expect(new Set(ACCOUNTING_PAGES.map((page) => page.permission)).size).toBe(8);
    expect(accountingNavItems()).toHaveLength(8);
    expect(accountingNavItems().every((item) => item.to.includes("workspace=financial"))).toBe(true);
});

test("only P00 home and P01 settlements are implemented at this phase", () => {
    const implemented = ACCOUNTING_PAGES
        .filter((page) => page.implementationStatus === "implemented")
        .map((page) => page.id);
    expect(implemented).toEqual(["home", "settlements"]);
    expect(ACCOUNTING_PAGES.find((page) => page.id === "shipping-cod")?.implementationStatus)
        .toBe("partial_existing_workflows");
    expect(ACCOUNTING_PAGES.find((page) => page.id === "opening-balances")?.implementationStatus)
        .toBe("blocked_not_implemented");
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

test("sensitive actions remain separate from page access", () => {
    const actionPermissions = ACCOUNTING_ACTIONS.map((action) => action.permission);
    expect(actionPermissions).toContain("accounting.opening_balances.approve");
    expect(actionPermissions).toContain("accounting.journals.manual_create");
    expect(actionPermissions).toContain("accounting.journals.reverse");
    expect(actionPermissions).not.toContain("accounting.opening_balances.view");
});
