import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

let mockSearchParams = new URLSearchParams("");

jest.mock("react-router-dom", () => ({
    Link: ({ children, to, className }) => <a href={to} className={className}>{children}</a>,
    useSearchParams: () => [mockSearchParams, jest.fn()],
}));

jest.mock("./StoreOperationsAccessWorkspace", () => function AccessFixture() {
    return <div data-testid="store-operations-access-fixture">صلاحيات إدارة التجهيز الفعلية</div>;
});

jest.mock("./EmployeesV2ManagementPilot", () => function ManagementFixture() {
    return <div data-testid="employees-v2-management-fixture">إدارة موظف تجريبي آمن</div>;
});

jest.mock("../services/employeesV2", () => ({
    getEmployeesV2: jest.fn(),
    applyEmployeesV2ShadowMigration: jest.fn(),
}));

jest.mock("sonner", () => ({
    toast: { success: jest.fn(), error: jest.fn() },
}));

import EmployeesV2 from "./EmployeesV2";
import {
    applyEmployeesV2ShadowMigration,
    getEmployeesV2,
} from "../services/employeesV2";

const preview = {
    summary: {
        legacy_employees: 15,
        active_employees: 13,
        stopped_employees: 2,
        active_monthly_salary_total: 38200,
        linked_login_accounts: 0,
        accounts_needing_review: 1,
        blocking_issues: 0,
        warnings: 1,
        ready_to_create: 15,
        salary_payable_total: 38200,
        advance_total: 60,
        custody_total: 5100,
    },
    safety: {
        operating_salaries_writes: false,
        general_ledger_writes: false,
        historical_recompute: false,
        liability_writes: false,
        user_account_writes: false,
        role_assignment_writes: false,
    },
    employees: [],
};

function deferred() {
    let resolve;
    const promise = new Promise((resolvePromise) => {
        resolve = resolvePromise;
    });
    return { promise, resolve };
}

beforeEach(() => {
    mockSearchParams = new URLSearchParams("");
    getEmployeesV2.mockReset();
    applyEmployeesV2ShadowMigration.mockReset();
});

test("employee workspace opens safe management before the migrated employees", () => {
    const markup = renderToStaticMarkup(<EmployeesV2 />);

    expect(markup).toContain("إدارة موظف تجريبي آمن");
    expect(markup).toContain("إدارة الموظفين");
    expect(markup).toContain("تقرير الترحيل والرواتب");
    expect(markup).not.toContain("صلاحيات إدارة التجهيز الفعلية");
});

test("migration report remains available as a separate guarded workspace", () => {
    mockSearchParams = new URLSearchParams("workspace=migration");
    const markup = renderToStaticMarkup(<EmployeesV2 />);

    expect(markup).toContain("Mezan Employee OS");
    expect(markup).toContain("إنشاء النسخة التجريبية");
    expect(markup).toContain("لا تعديل على الرواتب القديمة");
    expect(markup).toContain("لا قيود جديدة أو إعادة احتساب");
    expect(markup).toContain("لا تعديل على السلف والعهد");
    expect(markup).not.toContain("إدارة موظف تجريبي آمن");
});

test("permissions stay in the same employee workspace instead of a second page", () => {
    mockSearchParams = new URLSearchParams("workspace=permissions");
    const markup = renderToStaticMarkup(<EmployeesV2 />);

    expect(markup).toContain("صلاحيات إدارة التجهيز الفعلية");
    expect(markup).not.toContain("Mezan Employee OS");
});

test("uses an in-app confirmation and submits the shadow migration exactly once", async () => {
    mockSearchParams = new URLSearchParams("workspace=migration");
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const nativeConfirm = jest.spyOn(window, "confirm").mockImplementation(() => {
        throw new Error("native confirmation must not be used");
    });
    const migration = deferred();
    getEmployeesV2.mockResolvedValue(preview);
    applyEmployeesV2ShadowMigration.mockReturnValue(migration.promise);

    try {
        await act(async () => {
            root.render(<EmployeesV2 />);
        });

        const openButton = container.querySelector('[data-testid="employees-v2-open-shadow-confirmation"]');
        expect(openButton).not.toBeNull();

        await act(async () => {
            openButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });

        expect(document.body.querySelector('[data-testid="employees-v2-shadow-confirmation"]')).not.toBeNull();
        expect(nativeConfirm).not.toHaveBeenCalled();

        await act(async () => {
            document.body.querySelector('[data-testid="employees-v2-cancel-shadow-migration"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });

        expect(applyEmployeesV2ShadowMigration).not.toHaveBeenCalled();
        expect(document.body.querySelector('[data-testid="employees-v2-shadow-confirmation"]')).toBeNull();

        await act(async () => {
            openButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });

        const confirmButton = document.body.querySelector('[data-testid="employees-v2-confirm-shadow-migration"]');
        await act(async () => {
            confirmButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
            confirmButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });

        expect(applyEmployeesV2ShadowMigration).toHaveBeenCalledTimes(1);
        expect(confirmButton.disabled).toBe(true);
        expect(confirmButton.textContent).toContain("جارٍ إنشاء النواة");

        await act(async () => {
            migration.resolve({
                preview: { ...preview, summary: { ...preview.summary, ready_to_create: 0 } },
                idempotent_replay: false,
            });
            await migration.promise;
        });

        expect(document.body.querySelector('[data-testid="employees-v2-shadow-confirmation"]')).toBeNull();
        expect(applyEmployeesV2ShadowMigration).toHaveBeenCalledTimes(1);
    } finally {
        await act(async () => root.unmount());
        nativeConfirm.mockRestore();
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});
