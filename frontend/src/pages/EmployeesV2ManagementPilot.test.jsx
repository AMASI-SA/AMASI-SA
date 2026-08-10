import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("react-router-dom", () => ({
    Link: ({ children, to, className }) => <a href={to} className={className}>{children}</a>,
}));

jest.mock("../services/employeesV2", () => ({
    assignEmployeesV2PilotRole: jest.fn(),
    createAndLinkEmployeesV2PilotAccount: jest.fn(),
    createEmployeesV2Pilot: jest.fn(),
    getEmployeesV2Management: jest.fn(),
    getEmployeesV2PilotEvents: jest.fn(),
    linkEmployeesV2PilotAccount: jest.fn(),
    unlinkEmployeesV2PilotAccount: jest.fn(),
    updateEmployeesV2Pilot: jest.fn(),
}));

jest.mock("sonner", () => ({
    toast: { success: jest.fn(), error: jest.fn() },
}));

import EmployeesV2ManagementPilot from "./EmployeesV2ManagementPilot";
import {
    createAndLinkEmployeesV2PilotAccount,
    createEmployeesV2Pilot,
    getEmployeesV2Management,
} from "../services/employeesV2";


const emptyWorkspace = {
    summary: {
        legacy_employees: 15,
        already_migrated: 15,
    },
    management: {
        rollout_mode: "pilot_only",
        pilot_limit: 1,
        pilot_count: 0,
        can_create_pilot: true,
        migrated_employee_writes_enabled: false,
        legacy_payroll_writes_enabled: false,
        general_ledger_writes_enabled: false,
        reserved_review_accounts: 1,
        employees: [],
        login_account_candidates: [],
        role_catalog: {
            product_operator: ["products.read"],
            warehouse_operator: ["inventory.receipts.read"],
        },
        role_labels: {
            product_operator: "موظف المنتجات",
            warehouse_operator: "موظف المخزن",
        },
    },
};

const pilotWorkspace = {
    ...emptyWorkspace,
    management: {
        ...emptyWorkspace.management,
        pilot_count: 1,
        can_create_pilot: false,
        employees: [{
            id: "pilot-1",
            name: "موظف تجريبي",
            status: "draft",
            version: 1,
            management_mode: "pilot_only",
            payroll_enabled: false,
            salary_contract: {
                monthly_amount: 1000,
                payroll_enabled: false,
            },
            account: { status: "not_linked", user_id: null },
            operational_role: { effective_permissions: [] },
        }],
    },
};

function deferred() {
    let resolve;
    const promise = new Promise((resolvePromise) => { resolve = resolvePromise; });
    return { promise, resolve };
}

beforeEach(() => {
    jest.clearAllMocks();
    getEmployeesV2Management.mockResolvedValue(emptyWorkspace);
});

test("pilot workspace keeps all 15 migrated employees locked and financial writes at zero", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
        await act(async () => { root.render(<EmployeesV2ManagementPilot />); });

        expect(container.textContent).toContain("إدارة الموظفين");
        expect(container.textContent).toContain("الموظفون المرحّلون وعددهم 15 محميون من التعديل");
        expect(container.textContent).toContain("كتابات مالية");
        expect(container.textContent).toContain("حساب عرفات");
        expect(container.querySelector('[data-testid="employees-v2-add-pilot"]').disabled).toBe(false);
    } finally {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});

test("creates one pilot employee exactly once and renders it as payroll-disabled", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const createRequest = deferred();
    createEmployeesV2Pilot.mockReturnValue(createRequest.promise);
    try {
        await act(async () => { root.render(<EmployeesV2ManagementPilot />); });
        await act(async () => {
            container.querySelector('[data-testid="employees-v2-add-pilot"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });

        const name = document.body.querySelector('[data-testid="employees-v2-pilot-name"]');
        const submit = document.body.querySelector('[data-testid="employees-v2-pilot-form-submit"]');
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            setter.call(name, "موظف تجريبي");
            name.dispatchEvent(new Event("input", { bubbles: true }));
        });
        await act(async () => {
            submit.closest("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
            submit.closest("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        });

        expect(createEmployeesV2Pilot).toHaveBeenCalledTimes(1);
        await act(async () => {
            createRequest.resolve(pilotWorkspace);
            await createRequest.promise;
        });

        expect(container.querySelector('[data-testid="employees-v2-pilot-card"]')).not.toBeNull();
        expect(container.textContent).toContain("موظف تجريبي");
        expect(container.textContent).toContain("غير مفعّل في الرواتب");
        expect(container.textContent).toContain("لا Ledger · لا سلف · لا عهد");
        expect(container.querySelector('[data-testid="employees-v2-add-pilot"]').disabled).toBe(true);
    } finally {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});


test("unlinked pilot creates a zero-access login inline and links it without leaving Employee OS", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    getEmployeesV2Management.mockResolvedValue(pilotWorkspace);
    createAndLinkEmployeesV2PilotAccount.mockResolvedValue({
        ...pilotWorkspace,
        management: {
            ...pilotWorkspace.management,
            employees: [{
                ...pilotWorkspace.management.employees[0],
                account: { status: "linked", user_id: "account-1", name: "موظف تجريبي", email: "pilot@example.com" },
            }],
        },
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
        await act(async () => { root.render(<EmployeesV2ManagementPilot />); });

        const primaryAction = container.querySelector('[data-testid="employees-v2-primary-action"]');
        expect(primaryAction).not.toBeNull();
        expect(primaryAction.disabled).toBe(false);
        expect(primaryAction.textContent).toContain("ربط حساب الدخول");

        await act(async () => {
            primaryAction.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });

        expect(document.body.querySelector('[data-testid="employees-v2-account-dialog"]')).not.toBeNull();
        expect(document.body.textContent).toContain("صفر صلاحيات قديمة");
        expect(document.body.querySelector('a[href="/team"]')).toBeNull();

        const email = document.body.querySelector('[data-testid="employees-v2-account-email"]');
        const password = document.body.querySelector('[data-testid="employees-v2-account-password"]');
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            setter.call(email, "pilot@example.com");
            email.dispatchEvent(new Event("input", { bubbles: true }));
            setter.call(password, "Pilot123!");
            password.dispatchEvent(new Event("input", { bubbles: true }));
        });
        await act(async () => {
            document.body.querySelector('[data-testid="employees-v2-create-link-account"]').closest("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        });

        expect(createAndLinkEmployeesV2PilotAccount).toHaveBeenCalledWith("pilot-1", {
            name: "موظف تجريبي",
            email: "pilot@example.com",
            password: "Pilot123!",
        });
        expect(container.querySelector('[data-testid="employees-v2-primary-action"]').textContent).toContain("تعيين الدور والصلاحيات");
    } finally {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});
