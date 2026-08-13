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

jest.mock("./EmployeesV2Management", () => function ManagementFixture() {
    return <div data-testid="employees-v2-management-fixture">إدارة جميع الموظفين</div>;
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
    cutover_readiness: {
        status: "blocked",
        retire_legacy_page_allowed: false,
        read_only: true,
        financial_writes: 0,
        salary_contract_writes_enabled: false,
        parallel_cycle_completed: false,
        blocking_reasons: [
            "salary_payable_ledger_unreconciled",
            "employee_advances_ledger_unreconciled",
            "salary_contract_management_not_enabled",
            "parallel_payroll_cycle_not_completed",
        ],
        summary: {
            legacy_employee_count: 15,
            employee_shadow_count: 15,
            salary_contract_count: 15,
            exact_contract_count: 15,
            matched_employee_count: 15,
            legacy_active_monthly_total: 38200,
            v2_active_monthly_total: 38200,
            legacy_net_due: 120819.34,
            v2_projected_net_due: 120819.34,
            ledger_salary_payable: 0,
            salary_payable_ledger_gap: 120819.34,
            legacy_open_advances: 2695,
            ledger_advances: 60,
            advances_ledger_gap: 2635,
        },
        employees: [],
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

test("employee workspace opens full employee management by default", () => {
    const markup = renderToStaticMarkup(<EmployeesV2 />);

    expect(markup).toContain("إدارة جميع الموظفين");
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
    expect(markup).not.toContain("إدارة جميع الموظفين");
});

test("permissions stay in the same employee workspace instead of a second page", () => {
    mockSearchParams = new URLSearchParams("workspace=permissions");
    const markup = renderToStaticMarkup(<EmployeesV2 />);

    expect(markup).toContain("صلاحيات إدارة التجهيز الفعلية");
    expect(markup).not.toContain("Mezan Employee OS");
});

test("shows a read-only payroll retirement gate with live accrual and ledger gaps", async () => {
    mockSearchParams = new URLSearchParams("workspace=migration");
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    getEmployeesV2.mockResolvedValue(preview);

    try {
        await act(async () => {
            root.render(<EmployeesV2 />);
        });

        const gate = container.querySelector('[data-testid="employees-v2-cutover-readiness"]');
        expect(gate).not.toBeNull();
        expect(gate.textContent).toContain("الإيقاف محظور");
        expect(gate.textContent).toContain("120,819.34");
        expect(gate.textContent).toContain("2,695.00");
        expect(gate.textContent).toContain("60.00");
        expect(gate.textContent).toContain("تعديل عقود الرواتب لم يُفعّل بعد");
        expect(gate.textContent).toContain("لم تكتمل دورة رواتب متوازية كاملة");
        expect(gate.textContent).toContain("كتابات مالية من هذا التقرير");
    } finally {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
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
