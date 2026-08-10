import api from "../lib/api";
import {
    assignEmployeesV2PilotRole,
    applyEmployeesV2ShadowMigration,
    createEmployeesV2Pilot,
    EMPLOYEE_PILOT_ACCOUNT_LINK_CONFIRMATION,
    EMPLOYEE_PILOT_ACCOUNT_UNLINK_CONFIRMATION,
    EMPLOYEE_PILOT_CREATE_CONFIRMATION,
    EMPLOYEE_PILOT_ROLE_CONFIRMATION,
    EMPLOYEE_SHADOW_MIGRATION_CONFIRMATION,
    getEmployeesV2,
    getEmployeesV2Management,
    getEmployeesV2PilotEvents,
    linkEmployeesV2PilotAccount,
    previewEmployeesV2Migration,
    unlinkEmployeesV2PilotAccount,
    updateEmployeesV2Pilot,
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

test("management pilot uses guarded create, edit, account, role, and audit contracts", async () => {
    await getEmployeesV2Management();
    await createEmployeesV2Pilot({ name: "موظف تجريبي", monthly_salary: 1000 });
    await updateEmployeesV2Pilot("pilot/1", { name: "موظف معدل", expected_version: 1 });
    await linkEmployeesV2PilotAccount("pilot/1", "account-1");
    await assignEmployeesV2PilotRole("pilot/1", {
        role_key: "warehouse_operator",
        enabled: true,
        extra_permissions: [],
        denied_permissions: [],
        warehouse_ids: [],
        fulfillment_responsibilities: [],
    });
    await getEmployeesV2PilotEvents("pilot/1");
    await unlinkEmployeesV2PilotAccount("pilot/1");

    expect(EMPLOYEE_PILOT_CREATE_CONFIRMATION).toBe("CREATE_EMPLOYEE_V2_PILOT");
    expect(EMPLOYEE_PILOT_ACCOUNT_LINK_CONFIRMATION).toBe("LINK_EMPLOYEE_V2_PILOT_ACCOUNT");
    expect(EMPLOYEE_PILOT_ACCOUNT_UNLINK_CONFIRMATION).toBe("UNLINK_EMPLOYEE_V2_PILOT_ACCOUNT");
    expect(EMPLOYEE_PILOT_ROLE_CONFIRMATION).toBe("ASSIGN_EMPLOYEE_V2_PILOT_ROLE");
    expect(api.get).toHaveBeenNthCalledWith(1, "/employees-v2/management");
    expect(api.post).toHaveBeenCalledWith("/employees-v2/management/pilot", {
        name: "موظف تجريبي",
        monthly_salary: 1000,
        confirmation: "CREATE_EMPLOYEE_V2_PILOT",
    });
    expect(api.put).toHaveBeenCalledWith(
        "/employees-v2/management/pilot/pilot%2F1",
        { name: "موظف معدل", expected_version: 1 },
    );
    expect(api.put).toHaveBeenCalledWith(
        "/employees-v2/management/pilot/pilot%2F1/account",
        {
            account_user_id: "account-1",
            confirmation: "LINK_EMPLOYEE_V2_PILOT_ACCOUNT",
        },
    );
    expect(api.get).toHaveBeenNthCalledWith(
        2,
        "/employees-v2/management/pilot/pilot%2F1/events",
    );
    expect(api.delete).toHaveBeenCalledWith(
        "/employees-v2/management/pilot/pilot%2F1/account",
        { data: { confirmation: "UNLINK_EMPLOYEE_V2_PILOT_ACCOUNT" } },
    );
});

test("loads the unified employee workspace and the read-only preview separately", async () => {
    await getEmployeesV2();
    await previewEmployeesV2Migration();

    expect(api.get).toHaveBeenNthCalledWith(1, "/employees-v2");
    expect(api.get).toHaveBeenNthCalledWith(2, "/employees-v2/migration/preview");
});

test("shadow migration uses the exact guarded confirmation contract", async () => {
    await applyEmployeesV2ShadowMigration();

    expect(EMPLOYEE_SHADOW_MIGRATION_CONFIRMATION).toBe("MIGRATE_EMPLOYEES_V2_SHADOW");
    expect(api.post).toHaveBeenCalledWith("/employees-v2/migration/apply-shadow", {
        confirmation: "MIGRATE_EMPLOYEES_V2_SHADOW",
    });
});
