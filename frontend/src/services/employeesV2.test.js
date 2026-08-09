import api from "../lib/api";
import {
    applyEmployeesV2ShadowMigration,
    EMPLOYEE_SHADOW_MIGRATION_CONFIRMATION,
    getEmployeesV2,
    previewEmployeesV2Migration,
} from "./employeesV2";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
        post: jest.fn(),
    },
}));

beforeEach(() => {
    jest.clearAllMocks();
    api.get.mockResolvedValue({ data: { employees: [] } });
    api.post.mockResolvedValue({ data: { ok: true } });
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
