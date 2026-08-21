import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../services/employeesV2", () => ({
    assignEmployeesV2MobileAppPermissions: jest.fn(),
    assignEmployeesV2Role: jest.fn(),
    createAndLinkEmployeesV2Account: jest.fn(),
    createEmployeesV2: jest.fn(),
    getEmployeesV2Events: jest.fn(),
    getEmployeesV2Management: jest.fn(),
    linkEmployeesV2Account: jest.fn(),
    resetEmployeesV2AccountPassword: jest.fn(),
    unlinkEmployeesV2Account: jest.fn(),
    updateEmployeesV2: jest.fn(),
}));

jest.mock("sonner", () => ({
    toast: { success: jest.fn(), error: jest.fn() },
}));

import EmployeesV2Management from "./EmployeesV2Management";
import {
    assignEmployeesV2MobileAppPermissions,
    getEmployeesV2Management,
    resetEmployeesV2AccountPassword,
} from "../services/employeesV2";


const roleCatalog = {
    product_operator: ["products.read"],
    preparation_operator: ["preparation.assigned.read", "preparation.assigned.work"],
    warehouse_operator: ["inventory.receipts.read"],
};
const employees = Array.from({ length: 15 }, (_item, index) => ({
    id: `employee-${index + 1}`,
    name: index === 0 ? "تركي صادق" : `موظف ${index + 1}`,
    phone: index === 0 ? "0500000000" : "",
    contact_email: index === 0 ? "turki@example.com" : "",
    job_title: index === 0 ? "موظف تجهيز" : "موظف",
    department: index === 0 ? "التجهيز" : "العمليات",
    status: index === 1 ? "inactive" : "active",
    version: 1,
    migrated: true,
    salary_contract: { monthly_amount: 3000 },
    account: index === 0 ? {
        status: "linked",
        user_id: "turki-account",
        name: "تركي صادق",
        email: "turki@example.com",
        access_enabled: true,
    } : { status: "not_linked", user_id: null, access_enabled: false },
    operational_role: index === 0 ? {
        role_key: "preparation_operator",
        enabled: true,
        effective_permissions: ["preparation.assigned.read", "preparation.assigned.work"],
    } : { role_key: null, enabled: false, effective_permissions: [] },
    mobile_app_access: index === 0 ? {
        configured: true,
        enabled: true,
        permissions: ["app.page.my_products"],
        stored_permissions: ["app.page.my_products"],
        scope: "amasi_mobile_only",
    } : { configured: false, enabled: false, permissions: [], stored_permissions: [] },
}));
const workspace = {
    summary: { legacy_employees: 15, already_migrated: 15 },
    management: {
        rollout_mode: "full_management",
        managed_count: 15,
        active_count: 14,
        inactive_count: 1,
        linked_account_count: 1,
        can_create_employee: true,
        migrated_employee_writes_enabled: true,
        employee_salary_source: "mezan_employee_salary_contracts_v2",
        legacy_employee_salary_reads: 0,
        payroll_status_writes_enabled: true,
        legacy_payroll_writes_enabled: false,
        general_ledger_writes_enabled: false,
        financial_writes: 0,
        employees,
        login_account_candidates: [],
        role_catalog: roleCatalog,
        role_labels: {
            product_operator: "موظف المنتجات",
            preparation_operator: "موظف التجهيز",
            warehouse_operator: "موظف المخزن",
        },
        mobile_app_permission_catalog: [
            {
                key: "preparation",
                label: "إدارة التجهيز",
                permissions: [
                    { key: "app.page.my_products", label: "إدارة منتجاتي", kind: "page" },
                ],
            },
            {
                key: "actions",
                label: "إجراءات إدارة منتجاتي",
                permissions: [
                    { key: "app.action.my_products.service.add", label: "إضافة خدمة", kind: "action", requires: "app.page.my_products" },
                ],
            },
        ],
    },
};


async function renderPage() {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<EmployeesV2Management />); });
    return { container, root };
}


async function cleanup(container, root) {
    await act(async () => root.unmount());
    container.remove();
    globalThis.IS_REACT_ACT_ENVIRONMENT = false;
}


beforeEach(() => {
    jest.clearAllMocks();
    getEmployeesV2Management.mockResolvedValue(workspace);
});


test("opens full management for all 15 employees with V2 payroll authority", async () => {
    const { container, root } = await renderPage();
    try {
        expect(container.textContent).toContain("إدارة الموظفين");
        expect(container.textContent).toContain("مصدر رواتب الموظفين: عقود ميزان 2");
        expect(container.textContent).toContain("الاعتماد على رواتب الموظفين القديمة: 0");
        expect(container.textContent).not.toContain("موظف تجريبي واحد");
        expect(container.querySelectorAll('[data-testid="employees-v2-employee-card"]')).toHaveLength(15);
        expect(container.querySelector('[data-testid="employees-v2-add-employee"]').disabled).toBe(false);
    } finally {
        await cleanup(container, root);
    }
});


test("unpaid leave shows its effective-date salary stop warning", async () => {
    const { container, root } = await renderPage();
    try {
        const firstCard = container.querySelector('[data-testid="employees-v2-employee-card"]');
        const edit = firstCard.querySelector('button[aria-label="تعديل الموظف"]');
        await act(async () => edit.dispatchEvent(new MouseEvent("click", { bubbles: true })));

        const status = document.body.querySelector('[data-testid="employees-v2-status-select"]');
        expect([...status.options].map((option) => option.value)).toContain("unpaid_leave");
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
            setter.call(status, "unpaid_leave");
            status.dispatchEvent(new Event("change", { bubbles: true }));
        });

        expect(document.body.querySelector('[data-testid="employees-v2-payroll-status-warning"]')).not.toBeNull();
        expect(document.body.textContent).toContain("أول يوم غير مدفوع");
        expect(document.body.querySelector('[data-testid="employees-v2-status-effective-date"]')).not.toBeNull();
    } finally {
        await cleanup(container, root);
    }
});


test("search and status filters narrow the employee list", async () => {
    const { container, root } = await renderPage();
    try {
        const search = container.querySelector('[data-testid="employees-v2-search"]');
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            setter.call(search, "تركي");
            search.dispatchEvent(new Event("input", { bubbles: true }));
        });
        expect(container.querySelectorAll('[data-testid="employees-v2-employee-card"]')).toHaveLength(1);
        expect(container.textContent).toContain("تركي صادق");

        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            setter.call(search, "");
            search.dispatchEvent(new Event("input", { bubbles: true }));
            const status = container.querySelector('[data-testid="employees-v2-status-filter"]');
            const selectSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
            selectSetter.call(status, "inactive");
            status.dispatchEvent(new Event("change", { bubbles: true }));
        });
        expect(container.querySelectorAll('[data-testid="employees-v2-employee-card"]')).toHaveLength(1);
        expect(container.textContent).toContain("موظف 2");
    } finally {
        await cleanup(container, root);
    }
});


test("preparation employee role remains limited to assigned work", async () => {
    const { container, root } = await renderPage();
    try {
        const firstCard = container.querySelector('[data-testid="employees-v2-employee-card"]');
        const roleButton = [...firstCard.querySelectorAll("button")].find((button) => button.textContent.includes("صلاحيات ميزان"));
        await act(async () => roleButton.dispatchEvent(new MouseEvent("click", { bubbles: true })));

        const roleSelect = document.body.querySelector('[data-testid="employees-v2-role-select"]');
        expect([...roleSelect.options].map((option) => option.textContent)).toContain("موظف التجهيز");
        expect(document.body.querySelector('[data-testid="employees-v2-role-description"]').textContent).toContain("المسندة إليه فقط");
        expect(document.body.textContent).toContain("preparation.assigned.read");
        expect(document.body.textContent).toContain("preparation.assigned.work");
        expect(document.body.textContent).not.toContain("inventory.preparation.receive");
    } finally {
        await cleanup(container, root);
    }
});


test("mobile app permissions are edited separately without changing Mezan permissions", async () => {
    assignEmployeesV2MobileAppPermissions.mockResolvedValue(workspace);
    const { container, root } = await renderPage();
    try {
        const firstCard = container.querySelector('[data-testid="employees-v2-employee-card"]');
        const appButton = [...firstCard.querySelectorAll("button")].find((button) => button.textContent.includes("صلاحيات التطبيق"));
        await act(async () => appButton.dispatchEvent(new MouseEvent("click", { bubbles: true })));

        expect(document.body.textContent).toContain("هذه الصلاحيات للتطبيق فقط");
        expect(document.body.textContent).toContain("صلاحيات ميزان الحالية للموظف: 2");
        const addService = document.body.querySelector('[data-testid="mobile-app-permission-app.action.my_products.service.add"]');
        await act(async () => addService.dispatchEvent(new MouseEvent("click", { bubbles: true })));
        await act(async () => document.body.querySelector('[data-testid="employees-v2-mobile-app-permissions-submit"]').dispatchEvent(new MouseEvent("click", { bubbles: true })));

        expect(assignEmployeesV2MobileAppPermissions).toHaveBeenCalledWith("employee-1", {
            enabled: true,
            permissions: ["app.page.my_products", "app.action.my_products.service.add"],
        });
    } finally {
        await cleanup(container, root);
    }
});


test("linked employee password can be reset from the employee account dialog", async () => {
    resetEmployeesV2AccountPassword.mockResolvedValue(workspace);
    const { container, root } = await renderPage();
    try {
        const firstCard = container.querySelector('[data-testid="employees-v2-employee-card"]');
        const accountButton = [...firstCard.querySelectorAll("button")].find((button) => button.textContent.includes("الحساب وكلمة المرور"));
        await act(async () => accountButton.dispatchEvent(new MouseEvent("click", { bubbles: true })));

        const password = document.body.querySelector('[data-testid="employees-v2-new-password"]');
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            setter.call(password, "Temporary123!");
            password.dispatchEvent(new Event("input", { bubbles: true }));
        });
        await act(async () => {
            document.body.querySelector('[data-testid="employees-v2-reset-password"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });

        expect(resetEmployeesV2AccountPassword).toHaveBeenCalledWith("employee-1", "Temporary123!");
    } finally {
        await cleanup(container, root);
    }
});
