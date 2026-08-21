import api from "../lib/api";
import {
    applyEmployeesV2ShadowMigration,
    assignEmployeesV2Role,
    assignEmployeesV2MobileAppPermissions,
    captureEmployeesV2ParallelCycle,
    createAndLinkEmployeesV2Account,
    createEmployeesV2,
    EMPLOYEE_ACCOUNT_LINK_CONFIRMATION,
    EMPLOYEE_ACCOUNT_UNLINK_CONFIRMATION,
    EMPLOYEE_CREATE_CONFIRMATION,
    EMPLOYEE_PASSWORD_CONFIRMATION,
    EMPLOYEE_PARALLEL_CYCLE_CAPTURE_CONFIRMATION,
    EMPLOYEE_PAYROLL_STATUS_CONFIRMATION,
    EMPLOYEE_ROLE_CONFIRMATION,
    EMPLOYEE_MOBILE_APP_PERMISSIONS_CONFIRMATION,
    EMPLOYEE_SHADOW_MIGRATION_CONFIRMATION,
    EMPLOYEE_SALARY_CONTRACT_SYNC_CONFIRMATION,
    getEmployeesV2,
    getEmployeesV2Events,
    getEmployeesV2Management,
    linkEmployeesV2Account,
    previewEmployeesV2Migration,
    resetEmployeesV2AccountPassword,
    syncEmployeesV2SalaryContracts,
    unlinkEmployeesV2Account,
    updateEmployeesV2,
} from "./employeesV2";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
        post: jest.fn(),
        put: jest.fn(),
        delete: jest.fn(),
    },
}));

beforeEach(() => {
    jest.clearAllMocks();
    api.get.mockResolvedValue({ data: { employees: [] } });
    api.post.mockResolvedValue({ data: { ok: true } });
    api.put.mockResolvedValue({ data: { ok: true } });
    api.delete.mockResolvedValue({ data: { ok: true } });
});

test("payroll status changes carry the exact guarded confirmation", async () => {
    await updateEmployeesV2("employee/1", {
        expected_version: 2,
        status: "unpaid_leave",
        status_effective_date: "2026-08-13",
    });

    expect(EMPLOYEE_PAYROLL_STATUS_CONFIRMATION).toBe("CHANGE_EMPLOYEE_V2_PAYROLL_STATUS");
    expect(api.put).toHaveBeenCalledWith(
        "/employees-v2/management/employees/employee%2F1",
        {
            expected_version: 2,
            status: "unpaid_leave",
            status_effective_date: "2026-08-13",
            confirmation: "CHANGE_EMPLOYEE_V2_PAYROLL_STATUS",
        },
    );
});

test("full management uses guarded employee, account, role, password, and audit contracts", async () => {
    await getEmployeesV2Management();
    await createEmployeesV2({ name: "تركي صادق", status: "active" });
    await updateEmployeesV2("employee/1", { name: "تركي", expected_version: 1 });
    await linkEmployeesV2Account("employee/1", "account-1");
    await assignEmployeesV2Role("employee/1", {
        role_key: "preparation_operator",
        enabled: true,
        extra_permissions: [],
        denied_permissions: [],
        warehouse_ids: [],
        fulfillment_responsibilities: [],
    });
    await assignEmployeesV2MobileAppPermissions("employee/1", {
        enabled: true,
        permissions: ["app.page.orders"],
    });
    await resetEmployeesV2AccountPassword("employee/1", "Temporary123!");
    await getEmployeesV2Events("employee/1");
    await unlinkEmployeesV2Account("employee/1");

    expect(EMPLOYEE_CREATE_CONFIRMATION).toBe("CREATE_EMPLOYEE_V2");
    expect(EMPLOYEE_ACCOUNT_LINK_CONFIRMATION).toBe("LINK_EMPLOYEE_V2_ACCOUNT");
    expect(EMPLOYEE_ACCOUNT_UNLINK_CONFIRMATION).toBe("UNLINK_EMPLOYEE_V2_ACCOUNT");
    expect(EMPLOYEE_ROLE_CONFIRMATION).toBe("ASSIGN_EMPLOYEE_V2_ROLE");
    expect(EMPLOYEE_MOBILE_APP_PERMISSIONS_CONFIRMATION).toBe("ASSIGN_EMPLOYEE_V2_MOBILE_APP_PERMISSIONS");
    expect(EMPLOYEE_PASSWORD_CONFIRMATION).toBe("RESET_EMPLOYEE_V2_ACCOUNT_PASSWORD");
    expect(api.get).toHaveBeenNthCalledWith(1, "/employees-v2/management");
    expect(api.post).toHaveBeenCalledWith("/employees-v2/management/employees", {
        name: "تركي صادق",
        status: "active",
        confirmation: "CREATE_EMPLOYEE_V2",
    });
    expect(api.put).toHaveBeenCalledWith(
        "/employees-v2/management/employees/employee%2F1",
        { name: "تركي", expected_version: 1 },
    );
    expect(api.put).toHaveBeenCalledWith(
        "/employees-v2/management/employees/employee%2F1/account",
        {
            account_user_id: "account-1",
            confirmation: "LINK_EMPLOYEE_V2_ACCOUNT",
        },
    );
    expect(api.put).toHaveBeenCalledWith(
        "/employees-v2/management/employees/employee%2F1/account/password",
        {
            new_password: "Temporary123!",
            confirmation: "RESET_EMPLOYEE_V2_ACCOUNT_PASSWORD",
        },
    );
    expect(api.put).toHaveBeenCalledWith(
        "/employees-v2/management/employees/employee%2F1/mobile-app-permissions",
        {
            enabled: true,
            permissions: ["app.page.orders"],
            confirmation: "ASSIGN_EMPLOYEE_V2_MOBILE_APP_PERMISSIONS",
        },
    );
    expect(api.get).toHaveBeenNthCalledWith(
        2,
        "/employees-v2/management/employees/employee%2F1/events",
    );
    expect(api.delete).toHaveBeenCalledWith(
        "/employees-v2/management/employees/employee%2F1/account",
        { data: { confirmation: "UNLINK_EMPLOYEE_V2_ACCOUNT" } },
    );
});

test("loads the unified employee workspace and read-only migration report separately", async () => {
    await getEmployeesV2();
    await previewEmployeesV2Migration();

    expect(api.get).toHaveBeenNthCalledWith(1, "/employees-v2");
    expect(api.get).toHaveBeenNthCalledWith(2, "/employees-v2/migration/preview");
});

test("shadow migration keeps its exact guarded confirmation", async () => {
    await applyEmployeesV2ShadowMigration();

    expect(EMPLOYEE_SHADOW_MIGRATION_CONFIRMATION).toBe("MIGRATE_EMPLOYEES_V2_SHADOW");
    expect(api.post).toHaveBeenCalledWith("/employees-v2/migration/apply-shadow", {
        confirmation: "MIGRATE_EMPLOYEES_V2_SHADOW",
    });
});

test("payroll validation writes use exact non-financial confirmations", async () => {
    await syncEmployeesV2SalaryContracts();
    await captureEmployeesV2ParallelCycle();

    expect(EMPLOYEE_SALARY_CONTRACT_SYNC_CONFIRMATION).toBe("SYNC_EMPLOYEE_V2_SALARY_CONTRACTS");
    expect(EMPLOYEE_PARALLEL_CYCLE_CAPTURE_CONFIRMATION).toBe("CAPTURE_EMPLOYEE_V2_PARALLEL_CYCLE");
    expect(api.post).toHaveBeenNthCalledWith(1, "/employees-v2/migration/sync-contracts", {
        confirmation: "SYNC_EMPLOYEE_V2_SALARY_CONTRACTS",
    });
    expect(api.post).toHaveBeenNthCalledWith(2, "/employees-v2/migration/parallel-cycle/capture", {
        confirmation: "CAPTURE_EMPLOYEE_V2_PARALLEL_CYCLE",
    });
});

test("creates a login with zero legacy viewer access before linking it", async () => {
    api.get.mockResolvedValueOnce({
        data: { role_defaults: { viewer: ["dashboard.view", "orders.view"] } },
    });
    api.post.mockResolvedValueOnce({
        data: { id: "safe-account-1", name: "تركي صادق", email: "turki@example.com" },
    });
    api.put.mockResolvedValueOnce({ data: { ok: true, management: { employees: [] } } });

    await createAndLinkEmployeesV2Account("employee/1", {
        name: "تركي صادق",
        email: "turki@example.com",
        password: "Pilot123!",
    });

    expect(api.get).toHaveBeenCalledWith("/auth/permissions/catalogue");
    expect(api.post).toHaveBeenCalledWith("/team/users", {
        name: "تركي صادق",
        email: "turki@example.com",
        password: "Pilot123!",
        role: "viewer",
        extra_permissions: [],
        denied_permissions: ["dashboard.view", "orders.view"],
    });
    expect(api.put).toHaveBeenCalledWith(
        "/employees-v2/management/employees/employee%2F1/account",
        {
            account_user_id: "safe-account-1",
            confirmation: "LINK_EMPLOYEE_V2_ACCOUNT",
        },
    );
});

test("fails closed before account creation when viewer defaults are unavailable", async () => {
    api.get.mockResolvedValueOnce({ data: { role_defaults: {} } });

    await expect(createAndLinkEmployeesV2Account("employee-1", {
        name: "تركي صادق",
        email: "turki@example.com",
        password: "Pilot123!",
    })).rejects.toMatchObject({ code: "employee_v2_viewer_permissions_unavailable" });

    expect(api.post).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
});
