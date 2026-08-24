import api from "../lib/api";

const BASE = "/financial-provider-apps/accounting-module";
const SETTLEMENTS = `${BASE}/settlements`;

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

export async function getAccountingSettlementContext() {
    const { data } = await api.get(`${SETTLEMENTS}/context`);
    return data;
}

export async function saveAccountingProviderBankBinding(provider, payload) {
    const { data } = await api.put(
        `${SETTLEMENTS}/bindings/${encodeURIComponent(provider)}`,
        payload,
    );
    return data;
}

export async function uploadAccountingSettlementDraft({
    provider,
    bankAccountId,
    statementDate,
    notes,
    file,
}) {
    const form = new FormData();
    form.append("provider", provider);
    form.append("file", file);
    if (bankAccountId) form.append("bank_account_id", bankAccountId);
    if (statementDate) form.append("statement_date", statementDate);
    if (notes) form.append("notes", notes);
    const { data } = await api.post(`${SETTLEMENTS}/drafts/upload`, form, {
        headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
}

export async function createAccountingSettlementDraftFromFile(payload) {
    const { data } = await api.post(`${SETTLEMENTS}/drafts/from-file`, payload);
    return data;
}

export async function getAccountingSettlementDrafts(params = {}) {
    const { data } = await api.get(`${SETTLEMENTS}/drafts`, { params });
    return data;
}

export async function getAccountingSettlementDraft(draftId) {
    const { data } = await api.get(`${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}`);
    return data;
}

export async function updateAccountingSettlementIdentity(
    draftId,
    statementReference,
    reason = "تحديث مرجع الكشف من شاشة التسويات",
) {
    const { data } = await api.patch(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/identity`,
        {
            statement_reference: statementReference || "",
            reason,
        },
    );
    return data;
}

export async function updateAccountingSettlementDraft(draftId, payload) {
    const next = { ...(payload || {}) };
    if (Object.prototype.hasOwnProperty.call(next, "statement_reference")) {
        await updateAccountingSettlementIdentity(
            draftId,
            next.statement_reference,
            next.manual_override_reason || "تحديث مرجع الكشف من شاشة التسويات",
        );
        delete next.statement_reference;
    }
    const { data } = await api.patch(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}`,
        next,
    );
    return data;
}

export async function matchAccountingSettlementEntry(draftId, payload) {
    const { data } = await api.post(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/match-entry`,
        payload,
    );
    return data;
}

export async function submitAccountingSettlementDraft(draftId, notes = "") {
    const { data } = await api.post(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/submit`,
        { notes },
    );
    return data;
}

export async function reviewAccountingSettlementDraft(draftId, notes = "") {
    const { data } = await api.post(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/review`,
        { notes },
    );
    return data;
}

export async function rejectAccountingSettlementDraft(draftId, reason) {
    const { data } = await api.post(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/reject`,
        { reason },
    );
    return data;
}

export async function postAccountingSettlementDraft(draftId, notes = "") {
    const { data } = await api.post(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/post`,
        { notes },
    );
    return data;
}
