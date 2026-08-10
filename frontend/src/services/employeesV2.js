import api from "../lib/api";

export const EMPLOYEE_SHADOW_MIGRATION_CONFIRMATION = "MIGRATE_EMPLOYEES_V2_SHADOW";
export const EMPLOYEE_CREATE_CONFIRMATION = "CREATE_EMPLOYEE_V2";
export const EMPLOYEE_ACCOUNT_LINK_CONFIRMATION = "LINK_EMPLOYEE_V2_ACCOUNT";
export const EMPLOYEE_ACCOUNT_UNLINK_CONFIRMATION = "UNLINK_EMPLOYEE_V2_ACCOUNT";
export const EMPLOYEE_ROLE_CONFIRMATION = "ASSIGN_EMPLOYEE_V2_ROLE";
export const EMPLOYEE_PASSWORD_CONFIRMATION = "RESET_EMPLOYEE_V2_ACCOUNT_PASSWORD";

export async function getEmployeesV2() {
    return (await api.get("/employees-v2")).data;
}

export async function previewEmployeesV2Migration() {
    return (await api.get("/employees-v2/migration/preview")).data;
}

export async function applyEmployeesV2ShadowMigration() {
    return (await api.post("/employees-v2/migration/apply-shadow", {
        confirmation: EMPLOYEE_SHADOW_MIGRATION_CONFIRMATION,
    })).data;
}

export async function getEmployeesV2Management() {
    return (await api.get("/employees-v2/management")).data;
}

export async function createEmployeesV2(payload) {
    return (await api.post("/employees-v2/management/employees", {
        ...payload,
        confirmation: EMPLOYEE_CREATE_CONFIRMATION,
    })).data;
}

export async function updateEmployeesV2(employeeId, payload) {
    return (await api.put(
        `/employees-v2/management/employees/${encodeURIComponent(employeeId)}`,
        payload,
    )).data;
}

export async function linkEmployeesV2Account(employeeId, accountUserId) {
    return (await api.put(
        `/employees-v2/management/employees/${encodeURIComponent(employeeId)}/account`,
        {
            account_user_id: accountUserId,
            confirmation: EMPLOYEE_ACCOUNT_LINK_CONFIRMATION,
        },
    )).data;
}

export async function createAndLinkEmployeesV2Account(employeeId, payload) {
    const catalogue = (await api.get("/auth/permissions/catalogue")).data;
    const viewerDefaults = catalogue?.role_defaults?.viewer;
    if (!Array.isArray(viewerDefaults) || viewerDefaults.length === 0) {
        const error = new Error("employee_v2_viewer_permissions_unavailable");
        error.code = "employee_v2_viewer_permissions_unavailable";
        throw error;
    }
    const account = (await api.post("/team/users", {
        name: payload.name,
        email: payload.email,
        password: payload.password,
        role: "viewer",
        extra_permissions: [],
        // The Employee OS assignment becomes the source of operational access.
        // Deny the legacy viewer defaults so this new pilot login starts at zero.
        denied_permissions: viewerDefaults,
    })).data;

    try {
        return await linkEmployeesV2Account(employeeId, account.id);
    } catch (error) {
        // Preserve enough context for the UI to explain the recoverable partial
        // state. The account will appear as a safe candidate after refresh.
        error.employeeV2CreatedAccount = account;
        throw error;
    }
}

export async function unlinkEmployeesV2Account(employeeId) {
    return (await api.delete(
        `/employees-v2/management/employees/${encodeURIComponent(employeeId)}/account`,
        { data: { confirmation: EMPLOYEE_ACCOUNT_UNLINK_CONFIRMATION } },
    )).data;
}

export async function assignEmployeesV2Role(employeeId, payload) {
    return (await api.put(
        `/employees-v2/management/employees/${encodeURIComponent(employeeId)}/role`,
        {
            ...payload,
            confirmation: EMPLOYEE_ROLE_CONFIRMATION,
        },
    )).data;
}

export async function resetEmployeesV2AccountPassword(employeeId, newPassword) {
    return (await api.put(
        `/employees-v2/management/employees/${encodeURIComponent(employeeId)}/account/password`,
        {
            new_password: newPassword,
            confirmation: EMPLOYEE_PASSWORD_CONFIRMATION,
        },
    )).data;
}

export async function getEmployeesV2Events(employeeId) {
    return (await api.get(
        `/employees-v2/management/employees/${encodeURIComponent(employeeId)}/events`,
    )).data;
}
