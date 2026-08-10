import api from "../lib/api";

export const EMPLOYEE_SHADOW_MIGRATION_CONFIRMATION = "MIGRATE_EMPLOYEES_V2_SHADOW";
export const EMPLOYEE_PILOT_CREATE_CONFIRMATION = "CREATE_EMPLOYEE_V2_PILOT";
export const EMPLOYEE_PILOT_ACCOUNT_LINK_CONFIRMATION = "LINK_EMPLOYEE_V2_PILOT_ACCOUNT";
export const EMPLOYEE_PILOT_ACCOUNT_UNLINK_CONFIRMATION = "UNLINK_EMPLOYEE_V2_PILOT_ACCOUNT";
export const EMPLOYEE_PILOT_ROLE_CONFIRMATION = "ASSIGN_EMPLOYEE_V2_PILOT_ROLE";

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

export async function createEmployeesV2Pilot(payload) {
    return (await api.post("/employees-v2/management/pilot", {
        ...payload,
        confirmation: EMPLOYEE_PILOT_CREATE_CONFIRMATION,
    })).data;
}

export async function updateEmployeesV2Pilot(employeeId, payload) {
    return (await api.put(
        `/employees-v2/management/pilot/${encodeURIComponent(employeeId)}`,
        payload,
    )).data;
}

export async function linkEmployeesV2PilotAccount(employeeId, accountUserId) {
    return (await api.put(
        `/employees-v2/management/pilot/${encodeURIComponent(employeeId)}/account`,
        {
            account_user_id: accountUserId,
            confirmation: EMPLOYEE_PILOT_ACCOUNT_LINK_CONFIRMATION,
        },
    )).data;
}

export async function createAndLinkEmployeesV2PilotAccount(employeeId, payload) {
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
        return await linkEmployeesV2PilotAccount(employeeId, account.id);
    } catch (error) {
        // Preserve enough context for the UI to explain the recoverable partial
        // state. The account will appear as a safe candidate after refresh.
        error.employeeV2CreatedAccount = account;
        throw error;
    }
}

export async function unlinkEmployeesV2PilotAccount(employeeId) {
    return (await api.delete(
        `/employees-v2/management/pilot/${encodeURIComponent(employeeId)}/account`,
        { data: { confirmation: EMPLOYEE_PILOT_ACCOUNT_UNLINK_CONFIRMATION } },
    )).data;
}

export async function assignEmployeesV2PilotRole(employeeId, payload) {
    return (await api.put(
        `/employees-v2/management/pilot/${encodeURIComponent(employeeId)}/role`,
        {
            ...payload,
            confirmation: EMPLOYEE_PILOT_ROLE_CONFIRMATION,
        },
    )).data;
}

export async function getEmployeesV2PilotEvents(employeeId) {
    return (await api.get(
        `/employees-v2/management/pilot/${encodeURIComponent(employeeId)}/events`,
    )).data;
}
