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
    createEmployeesV2,
    createAndLinkEmployeesV2Account,
    getEmployeesV2Management,
    resetEmployeesV2AccountPassword,
    updateEmployeesV2,
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
    salary_contract: index === 1 ? null : {
        monthly_amount: 3000,
        effective_from: "2026-08-01",
        salary_revisions: [{
            id: `salary-rev-${index + 1}`,
            monthly_amount: 3000,
            effective_from: "2026-08-01",
            effective_to: null,
        }],
    },
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
        expect(container.textContent).toContain("لم يُحدد راتب");
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


test("create employee accepts monthly salary and accrual start date", async () => {
    createEmployeesV2.mockResolvedValue(workspace);
    const { container, root } = await renderPage();
    try {
        await act(async () => container.querySelector('[data-testid="employees-v2-add-employee"]').dispatchEvent(new MouseEvent("click", { bubbles: true })));
        const name = document.body.querySelector('[data-testid="employees-v2-employee-name"]');
        const salary = document.body.querySelector('[data-testid="employees-v2-monthly-salary"]');
        const effective = document.body.querySelector('[data-testid="employees-v2-salary-effective-date"]');
        await act(async () => {
            const inputSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            inputSetter.call(name, "المحاسب");
            name.dispatchEvent(new Event("input", { bubbles: true }));
            inputSetter.call(salary, "4200");
            salary.dispatchEvent(new Event("input", { bubbles: true }));
            inputSetter.call(effective, "2026-09-01");
            effective.dispatchEvent(new Event("input", { bubbles: true }));
        });
        await act(async () => document.body.querySelector('[data-testid="employees-v2-employee-form-submit"]').dispatchEvent(new MouseEvent("click", { bubbles: true })));

        expect(createEmployeesV2).toHaveBeenCalledWith(expect.objectContaining({
            name: "المحاسب",
            monthly_salary: 4200,
            salary_effective_date: "2026-09-01",
        }));
    } finally {
        await cleanup(container, root);
    }
});


test("edit employee changes salary with effective date and shows prior history", async () => {
    updateEmployeesV2.mockResolvedValue(workspace);
    const { container, root } = await renderPage();
    try {
        const firstCard = container.querySelector('[data-testid="employees-v2-employee-card"]');
        await act(async () => firstCard.querySelector('button[aria-label="تعديل الموظف"]').dispatchEvent(new MouseEvent("click", { bubbles: true })));
        expect(document.body.querySelector('[data-testid="employees-v2-salary-history"]')).not.toBeNull();

        const salary = document.body.querySelector('[data-testid="employees-v2-monthly-salary"]');
        const effective = document.body.querySelector('[data-testid="employees-v2-salary-effective-date"]');
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            setter.call(salary, "3600");
            salary.dispatchEvent(new Event("input", { bubbles: true }));
            setter.call(effective, "2026-10-15");
            effective.dispatchEvent(new Event("input", { bubbles: true }));
        });
        expect(document.body.querySelector('[data-testid="employees-v2-salary-change-warning"]')).not.toBeNull();
        await act(async () => document.body.querySelector('[data-testid="employees-v2-employee-form-submit"]').dispatchEvent(new MouseEvent("click", { bubbles: true })));

        expect(updateEmployeesV2).toHaveBeenCalledWith("employee-1", expect.objectContaining({
            expected_version: 1,
            monthly_salary: 3600,
            salary_effective_date: "2026-10-15",
        }));
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

describe("employee password minimum", () => {
    test.each([0, 5, 6, 7, 10, 11, 12])("create and reset accept length %i only from six", async (length) => {
        createAndLinkEmployeesV2Account.mockResolvedValue(workspace);
        resetEmployeesV2AccountPassword.mockResolvedValue(workspace);
        for (const linked of [false, true]) {
            const { container, root } = await renderPage();
            try {
                const card = container.querySelectorAll('[data-testid="employees-v2-employee-card"]')[linked ? 0 : 1];
                const button = [...card.querySelectorAll("button")].find((b) => b.textContent.includes(linked ? "الحساب وكلمة المرور" : "ربط حساب"));
                await act(async () => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
                const set = async (id, value) => act(async () => {
                    const input = document.body.querySelector(`[data-testid="${id}"]`);
                    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(input, value);
                    input.dispatchEvent(new Event("input", { bubbles: true }));
                });
                const id = linked ? "employees-v2-new-password" : "employees-v2-account-password";
                expect(document.body.querySelector(`[data-testid="${id}"]`).minLength).toBe(6);
                await set(id, "a".repeat(length));
                if (linked) {
                    const submit = document.body.querySelector('[data-testid="employees-v2-reset-password"]');
                    expect(submit.disabled).toBe(length < 6);
                    await act(async () => submit.dispatchEvent(new MouseEvent("click", { bubbles: true })));
                    expect(resetEmployeesV2AccountPassword).toHaveBeenCalledTimes(length >= 6 ? 1 : 0);
                } else {
                    await set("employees-v2-account-name", "Employee");
                    await set("employees-v2-account-email", "employee@example.com");
                    const form = document.body.querySelector('[data-testid="employees-v2-account-dialog"] form');
                    await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
                    expect(createAndLinkEmployeesV2Account).toHaveBeenCalledTimes(length >= 6 ? 1 : 0);
                }
            } finally { await cleanup(container, root); }
        }
    });
});

describe("MZ2 salary effective date editing", () => {
    beforeEach(() => { jest.useFakeTimers({ doNotFake: ["nextTick", "setImmediate"] }); jest.setSystemTime(new Date("2026-10-05T12:00:00Z")); });
    afterEach(() => jest.useRealTimers());
    const fixture = (date = "2026-10-31", amount = 1500) => ({ ...workspace, management: { ...workspace.management, employees: [{ ...employees[0], salary_contract: {
        monthly_amount: amount, effective_from: date, editable_effective_from: date, editable_revision_id: "future-rev",
        current_monthly_amount: date > "2026-10-05" ? 0 : amount, current_effective_from: date > "2026-10-05" ? null : date,
        salary_revisions: [{ id: "future-rev", monthly_amount: amount, effective_from: date, effective_to: null }],
        scheduled_salary_revisions: date > "2026-10-05" ? [{ id: "future-rev", monthly_amount: amount, effective_from: date }] : [],
    } }] } });
    const field = (id) => document.body.querySelector(`[data-testid="employees-v2-${id}"]`);
    const open = async (container) => act(async () => container.querySelector('button[aria-label="تعديل الموظف"]').click());
    const change = async (id, value) => act(async () => {
        const input = field(id);
        Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const save = async () => act(async () => field("employee-form-submit").closest("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    test.each(["2026-10-01", "2026-10-05", "2026-10-31"])("opening %s populates saved date and distinguishes current from scheduled", async (date) => {
        getEmployeesV2Management.mockResolvedValue(fixture(date));
        const { container, root } = await renderPage();
        try {
            await open(container);
            expect(field("salary-effective-date").value).toBe(date);
            expect(field("contract-effective-from").textContent).toContain(date);
            expect(field("first-accrual-date").textContent).toContain(date === "2026-10-01" ? "2026-10-02" : date === "2026-10-05" ? "2026-10-06" : "2026-11-01");
            expect(field("editable-first-accrual-date").textContent).toContain(date > "2026-10-05" ? "2026-11-01" : date === "2026-10-05" ? "2026-10-06" : "دون إعادة بدء الاستحقاق");
            expect(field("current-salary").textContent).toContain(date > "2026-10-05" ? "0.00" : "1,500.00");
            expect(field("scheduled-salary").textContent).toContain(date > "2026-10-05" ? "2026-10-31" : "لا يوجد راتب مجدول");
        } finally { await cleanup(container, root); }
    });
    test.each([
        ["date only", null, "2026-10-20", 1500, "2026-10-20"],
        ["amount only", "1700", null, 1700, "2026-10-31"],
        ["both", "1800", "2026-11-01", 1800, "2026-11-01"],
    ])("saves %s and reopens returned persisted values", async (_label, amount, date, expectedAmount, expectedDate) => {
        getEmployeesV2Management.mockResolvedValue(fixture());
        updateEmployeesV2.mockResolvedValue(fixture(expectedDate, expectedAmount));
        const { container, root } = await renderPage();
        try {
            await open(container);
            if (amount) await change("monthly-salary", amount);
            if (date) await change("salary-effective-date", date);
            await save();
            expect(updateEmployeesV2).toHaveBeenCalledWith("employee-1", expect.objectContaining({ monthly_salary: expectedAmount, salary_effective_date: expectedDate, salary_revision_id: "future-rev" }));
            await open(container);
            expect(field("salary-effective-date").value).toBe(expectedDate);
            expect(field("monthly-salary").value).toBe(String(expectedAmount));
            expect(field("current-salary").textContent).toContain("0.00");
        } finally { await cleanup(container, root); }
    });
    test("unchanged save omits salary mutation fields", async () => {
        getEmployeesV2Management.mockResolvedValue(fixture()); updateEmployeesV2.mockResolvedValue(fixture());
        const { container, root } = await renderPage();
        try {
            await open(container); await save();
            const payload = updateEmployeesV2.mock.calls[0][1];
            expect(payload).not.toHaveProperty("monthly_salary");
            expect(payload).not.toHaveProperty("salary_effective_date");
            expect(payload).not.toHaveProperty("salary_revision_id");
        } finally { await cleanup(container, root); }
    });
    test("amount-only current salary visibly proposes tomorrow to protect history", async () => {
        getEmployeesV2Management.mockResolvedValue(fixture("2026-10-01")); updateEmployeesV2.mockResolvedValue(fixture("2026-10-06", 1700));
        const { container, root } = await renderPage();
        try {
            await open(container); await change("monthly-salary", "1700");
            expect(field("salary-effective-date").value).toBe("2026-10-06");
            expect(field("proposed-salary-date").textContent).toContain("2026-10-06");
            await save();
            expect(updateEmployeesV2.mock.calls[0][1]).toMatchObject({ monthly_salary: 1700, salary_effective_date: "2026-10-06" });
        } finally { await cleanup(container, root); }
    });
    test("initial contract starting today changes amount on same date before first accrual", async () => {
        getEmployeesV2Management.mockResolvedValue(fixture("2026-10-05")); updateEmployeesV2.mockResolvedValue(fixture("2026-10-05", 1700));
        const { container, root } = await renderPage();
        try {
            await open(container); await change("monthly-salary", "1700");
            expect(field("salary-effective-date").value).toBe("2026-10-05");
            expect(field("editable-first-accrual-date").textContent).toContain("2026-10-06");
            await save();
            expect(updateEmployeesV2.mock.calls[0][1]).toMatchObject({ monthly_salary: 1700, salary_effective_date: "2026-10-05" });
        } finally { await cleanup(container, root); }
    });
    test("latest revision is editable while initial start and previous history remain visible", async () => {
        const data = fixture(); const contract = data.management.employees[0].salary_contract;
        contract.effective_from = "2026-10-01"; contract.current_monthly_amount = 1200; contract.current_effective_from = "2026-10-01";
        contract.salary_revisions.unshift({ id: "old-rev", monthly_amount: 1200, effective_from: "2026-10-01", effective_to: "2026-10-30" });
        contract.salary_revision_corrections = [{ changed_at: "2026-10-05T12:00:00Z", before: [{ id: "future-rev", monthly_amount: 1500, effective_from: "2026-11-10" }], after: [{ id: "future-rev", monthly_amount: 1500, effective_from: "2026-10-31" }] }];
        getEmployeesV2Management.mockResolvedValue(data);
        const { container, root } = await renderPage();
        try {
            await open(container);
            expect(field("salary-effective-date").value).toBe("2026-10-31");
            expect(field("contract-effective-from").textContent).toContain("2026-10-01");
            expect(field("current-salary").textContent).toContain("1,200.00");
            expect(field("scheduled-salary").textContent).toContain("1,500.00");
            expect(field("scheduled-salary").textContent).not.toContain("أول يوم استحقاق");
            expect(field("editable-first-accrual-date").textContent).toContain("دون إعادة بدء الاستحقاق");
            expect(field("salary-corrections").textContent).toContain("2026-11-10");
            expect(field("salary-corrections").textContent).toContain("2026-10-31");
            expect(field("salary-history").textContent).toContain("2026-10-01");
            expect(field("salary-history").textContent).toContain("2026-10-31");
        } finally { await cleanup(container, root); }
    });
});
