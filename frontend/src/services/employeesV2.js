import api from "../lib/api";

export const EMPLOYEE_SHADOW_MIGRATION_CONFIRMATION = "MIGRATE_EMPLOYEES_V2_SHADOW";

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
