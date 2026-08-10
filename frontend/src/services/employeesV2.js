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
