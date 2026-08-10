import api from "../lib/api";

const BASE = "/integrations-v2/snapchat_ads/management";
const ACTIONS = new Set([
    "campaign.create",
    "campaign.update",
    "ad_squad.create",
    "ad_squad.update",
    "ad.create",
    "ad.update",
    "creative.create",
]);

function text(value, fallback = "") {
    return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function number(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function object(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function managementError(error, fallback = "تعذّر تنفيذ طلب إدارة Snapchat.") {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail && typeof detail === "object") {
        if (typeof detail.message === "string" && detail.message.trim()) {
            return detail.message;
        }
        if (typeof detail.code === "string" && detail.code.trim()) {
            return `${fallback} (${detail.code})`;
        }
    }
    return error?.message || fallback;
}

export function normalizeSnapchatManagementReadiness(payload = {}) {
    const value = object(payload?.data || payload);
    return {
        provider: "snapchat_ads",
        proposal_enabled: value.proposal_enabled === true,
        execution_enabled: value.execution_enabled === true,
        activation_enabled: value.activation_enabled === true,
        max_daily_budget_micro: number(value.max_daily_budget_micro),
        new_entities_status: text(value.new_entities_status, "PAUSED"),
        salla_permission_dependency: value.salla_permission_dependency === true,
        required_lifecycle: Array.isArray(value.required_lifecycle)
            ? value.required_lifecycle.filter((item) => typeof item === "string")
            : [],
        accounts: Array.isArray(value.accounts) ? value.accounts.map((account) => ({
            account_id: text(account?.account_id),
            display_name: text(account?.display_name, account?.account_id || "حساب Snapchat"),
            currency: text(account?.currency, "SAR"),
            timezone: text(account?.timezone),
            role: text(account?.role) || null,
            management_allowed: account?.management_allowed === true,
            reason: text(account?.reason) || null,
            creative_role: text(account?.creative_role) || null,
            creative_allowed: account?.creative_allowed === true,
            creative_reason: text(account?.creative_reason) || null,
        })).filter((account) => account.account_id) : [],
    };
}

export function normalizeSnapchatManagementProposal(payload = {}) {
    const value = object(payload?.data || payload);
    const action = ACTIONS.has(value.action) ? value.action : null;
    return {
        proposal_id: text(value.proposal_id),
        status: text(value.status, "unknown"),
        revision: Math.max(1, Math.trunc(number(value.revision) || 1)),
        action,
        account_id: text(value.account_id),
        target_id: text(value.target_id) || null,
        parent_id: text(value.parent_id) || null,
        reason: text(value.reason),
        preview: object(value.preview),
        creates_paused: value.creates_paused === true,
        activates_delivery: value.activates_delivery === true,
        confirm_token: text(value.confirm_token) || null,
        confirmation_phrase: text(value.confirmation_phrase) || null,
        created_at: text(value.created_at) || null,
        expires_at: text(value.expires_at) || null,
        approved_at: text(value.approved_at) || null,
        executed_at: text(value.executed_at) || null,
        verification: object(value.verification),
        rollback: object(value.rollback),
        provider_write_reached: value.provider_write_reached === true,
        provider_write_state: text(value.provider_write_state, "not_attempted"),
        provider_write_uncertain: value.provider_write_uncertain === true,
        accounting_write_reached: value.accounting_write_reached === true,
        qoyod_write_reached: value.qoyod_write_reached === true,
    };
}

export async function getSnapchatManagementReadiness() {
    const response = await api.get(`${BASE}/readiness`);
    return normalizeSnapchatManagementReadiness(response.data);
}

export async function listSnapchatManagementProposals({ limit = 20 } = {}) {
    const response = await api.get(`${BASE}/proposals`, {
        params: { limit: Math.min(100, Math.max(1, Math.trunc(Number(limit) || 20))) },
    });
    const value = object(response.data);
    return Array.isArray(value.proposals)
        ? value.proposals.map(normalizeSnapchatManagementProposal).filter((row) => row.proposal_id)
        : [];
}

export async function createSnapchatManagementProposal(input = {}) {
    if (!ACTIONS.has(input.action)) throw new Error("إجراء Snapchat غير مدعوم.");
    const response = await api.post(`${BASE}/proposals`, {
        action: input.action,
        account_id: text(input.account_id),
        target_id: text(input.target_id) || null,
        parent_id: text(input.parent_id) || null,
        payload: object(input.payload),
        reason: text(input.reason),
        idempotency_key: text(input.idempotency_key),
        activation_acknowledged: input.activation_acknowledged === true,
    });
    return normalizeSnapchatManagementProposal(response.data);
}

export async function approveSnapchatManagementProposal(proposal) {
    const normalized = normalizeSnapchatManagementProposal(proposal);
    if (!normalized.proposal_id || !normalized.confirm_token) {
        throw new Error("رمز اعتماد المعاينة غير متوفر؛ أنشئ المعاينة من جديد.");
    }
    const response = await api.post(
        `${BASE}/proposals/${encodeURIComponent(normalized.proposal_id)}/approve`,
        {
            confirm_token: normalized.confirm_token,
            expected_revision: normalized.revision,
        },
    );
    return normalizeSnapchatManagementProposal(response.data);
}

export async function executeSnapchatManagementProposal(proposalId) {
    const response = await api.post(
        `${BASE}/proposals/${encodeURIComponent(text(proposalId))}/execute`,
    );
    return normalizeSnapchatManagementProposal(response.data);
}

export async function rollbackSnapchatManagementProposal(proposal, reason) {
    const normalized = normalizeSnapchatManagementProposal(proposal);
    const response = await api.post(
        `${BASE}/proposals/${encodeURIComponent(normalized.proposal_id)}/rollback`,
        {
            confirmation_phrase: normalized.confirmation_phrase,
            reason: text(reason),
        },
    );
    return normalizeSnapchatManagementProposal(response.data);
}

export function nativeAmountToMicro(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0) return null;
    return Math.round(parsed * 1_000_000);
}

export function microToNativeAmount(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed / 1_000_000 : null;
}
