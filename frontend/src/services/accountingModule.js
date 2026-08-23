import api from "../lib/api";

const BASE = "/financial-provider-apps/accounting-module";

export async function getAccountingAccess() {
    const { data } = await api.get(`${BASE}/access`);
    return data;
}

export async function getAccountingModuleStatus(page = "home") {
    const { data } = await api.get(`${BASE}/status`, { params: { page } });
    return data;
}

export async function getAccountingPermissionsCatalogue() {
    const { data } = await api.get(`${BASE}/permissions/catalogue`);
    return data;
}

export async function getAccountingPermissionUsers() {
    const { data } = await api.get(`${BASE}/permissions/users`);
    return data;
}

export async function updateAccountingPermissionUser(userId, permissions) {
    const { data } = await api.put(`${BASE}/permissions/users/${encodeURIComponent(userId)}`, {
        permissions,
    });
    return data;
}
