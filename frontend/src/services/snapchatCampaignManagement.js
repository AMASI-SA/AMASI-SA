import api from "../lib/api";

const BASE = "/integrations-v2/snapchat_ads/management";
const FINANCIAL_SETTINGS_READY = new Set(["settings_complete"]);
const TARGET_COST_STRATEGIES = new Set(["TARGET_COST"]);
const ACTIONS = new Set([
    "campaign.create",
    "campaign.update",
    "ad_squad.create",
    "ad_squad.update",
    "ad.create",
    "ad.update",
    "creative.create",
]);
const TRANSIENT_PREVIEW_TRANSPORT_STATUSES = new Set([502, 503, 504, 520]);
const MAX_PREVIEW_START_ATTEMPTS = 3;
const PREVIEW_RESUME_STORAGE_KEY = "mezan:snapchat-management-preview:v2";
const PREVIEW_RESUME_TTL_MS = 60 * 60 * 1000;
const previewPreparationInflight = new Map();
const SAFETY_PROTOCOL_VERSION = 2;

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

function objectList(value) {
    return Array.isArray(value) ? value.map(object).filter((item) => Object.keys(item).length) : [];
}

export function snapchatBidLabel(strategy) {
    const value = text(strategy).toUpperCase();
    if (TARGET_COST_STRATEGIES.has(value)) return "Target Cost";
    if (value === "LOWEST_COST_WITH_MAX_BID") return "Max Bid";
    return "Bid";
}

export function normalizeSnapchatEntitySettings(payload = {}) {
    const value = object(payload?.data || payload);
    const quality = object(value.quality);
    const campaignAggregate = object(value.campaign_aggregate);
    const accountCurrency = text(value.account_currency).toUpperCase() || null;
    const settingsStatus = text(
        quality.settings_status || value.settings_status,
        "settings_not_loaded",
    );
    const dailyBudgetMicro = number(value.daily_budget_micro);
    const bidMicro = number(value.bid_micro);
    const childBudgetMicro = number(
        value.ad_squads_daily_budget_micro
        ?? value.child_daily_budget_micro
        ?? value.ad_squad_daily_budget_sum_micro
        ?? campaignAggregate.daily_budget_sum_micro,
    );
    const usdAllowed = accountCurrency === "USD";
    return {
        ...value,
        entity_type: text(value.entity_type || value.provider_entity_type) || null,
        ad_account_id: text(value.ad_account_id) || null,
        unified_entity_id: text(value.unified_entity_id || value.entity_id) || null,
        provider_entity_id: text(value.provider_entity_id || value.external_id) || null,
        provider_parent_id: text(value.provider_parent_id || value.parent_provider_id) || null,
        account_currency: accountCurrency,
        daily_budget_micro: dailyBudgetMicro,
        daily_budget_usd: usdAllowed ? microToNativeAmount(dailyBudgetMicro) : null,
        bid_micro: bidMicro,
        bid_usd: usdAllowed ? microToNativeAmount(bidMicro) : null,
        ad_squads_daily_budget_micro: childBudgetMicro,
        ad_squads_daily_budget_usd: usdAllowed
            ? microToNativeAmount(childBudgetMicro)
            : null,
        active_ad_squads: number(
            value.active_ad_squads
            ?? value.active_ad_squad_count
            ?? campaignAggregate.active_ad_squad_count,
        ),
        ad_squad_bid_strategies: Array.isArray(value.ad_squad_bid_strategies)
            ? value.ad_squad_bid_strategies
            : Array.isArray(campaignAggregate.ad_squad_bid_strategies)
                ? campaignAggregate.ad_squad_bid_strategies
                : [],
        campaign_bid_strategy: text(
            value.campaign_bid_strategy
            || campaignAggregate.shared_ad_squad_bid_strategy,
        ) || null,
        bid_strategy: text(value.bid_strategy) || null,
        optimization_goal: text(value.optimization_goal) || null,
        billing_event: text(value.billing_event) || null,
        conversion_window: value.conversion_window && typeof value.conversion_window === "object"
            ? value.conversion_window
            : text(value.conversion_window) || null,
        status: text(value.status) || null,
        settings_synced_at: text(value.settings_synced_at || value.last_observed_at) || null,
        provider_updated_at: text(value.provider_updated_at || value.updated_at_provider) || null,
        mapping_status: text(value.mapping_status || quality.mapping_status) || null,
        mapping_verified: value.mapping_verified === true,
        quality: {
            ...quality,
            settings_status: settingsStatus,
            freshness_seconds: number(quality.freshness_seconds ?? value.freshness_seconds),
            freshness_threshold_seconds: number(
                quality.freshness_threshold_seconds ?? value.freshness_threshold_seconds,
            ),
            reason: text(quality.reason || value.settings_reason) || null,
            financial_controls_allowed: quality.financial_controls_allowed === true
                || value.financial_controls_allowed === true,
        },
    };
}

function baseFinancialSettingsReady(value, accountId = "") {
    const freshnessSeconds = number(value.quality.freshness_seconds);
    const freshnessThreshold = number(value.quality.freshness_threshold_seconds);
    const settingsSyncedAt = Date.parse(value.settings_synced_at || "");
    const clientAgeSeconds = Number.isFinite(settingsSyncedAt)
        ? (Date.now() - settingsSyncedAt) / 1000
        : Number.NaN;
    const expectedAccountId = text(accountId);
    return Boolean(
        value.unified_entity_id
        && value.provider_entity_id
        && value.mapping_verified === true
        && value.account_currency === "USD"
        && value.ad_account_id
        && (!expectedAccountId || value.ad_account_id === expectedAccountId)
        && FINANCIAL_SETTINGS_READY.has(value.quality.settings_status)
        && Number.isFinite(freshnessSeconds)
        && freshnessSeconds >= 0
        && Number.isFinite(freshnessThreshold)
        && freshnessThreshold > 0
        && freshnessThreshold <= 1800
        && freshnessSeconds <= freshnessThreshold
        && Number.isFinite(settingsSyncedAt)
        && Number.isFinite(clientAgeSeconds)
        && clientAgeSeconds >= 0
        && clientAgeSeconds <= freshnessThreshold
    );
}

function financialControlKey(field) {
    if (field === "daily_budget_micro") return "daily_budget";
    if (["bid_micro", "bid_strategy"].includes(field)) return "bid";
    return field;
}

export function snapchatFinancialFieldReady(settings, field, accountId = "") {
    const value = normalizeSnapchatEntitySettings(settings);
    if (!baseFinancialSettingsReady(value, accountId)) return false;
    const controls = object(value.quality.financial_field_controls);
    const control = controls[financialControlKey(field)];
    if (control === true) return true;
    if (!control || typeof control !== "object") return false;
    return control.allowed === true
        || control.financial_controls_allowed === true
        || control.preview_execute_allowed === true;
}

export function snapchatFinancialSettingsReady(settings, accountId = "") {
    const value = normalizeSnapchatEntitySettings(settings);
    if (!baseFinancialSettingsReady(value, accountId)) return false;
    const controls = object(value.quality.financial_field_controls);
    const values = Object.values(controls);
    return values.length > 0
        ? values.some((control) => control === true || (
            control
            && typeof control === "object"
            && (
                control.allowed === true
                || control.financial_controls_allowed === true
                || control.preview_execute_allowed === true
            )
        ))
        : value.quality.financial_controls_allowed === true;
}

function settingsItems(payload = {}) {
    const value = object(payload?.data || payload);
    const rows = Array.isArray(value.items)
        ? value.items
        : Array.isArray(value.settings)
            ? value.settings
            : Array.isArray(value.entities)
                ? value.entities
                : Object.keys(value).length ? [value] : [];
    return rows.map(normalizeSnapchatEntitySettings)
        .filter((item) => item.unified_entity_id || item.provider_entity_id);
}

function previewSessionStorage() {
    try {
        return typeof window !== "undefined" ? window.sessionStorage : null;
    } catch (_error) {
        return null;
    }
}

export function clearSnapchatManagementPreviewResume(ownerId = "") {
    const ownerScope = text(ownerId);
    const storage = previewSessionStorage();
    if (!storage) return;
    try {
        const existing = JSON.parse(storage.getItem(PREVIEW_RESUME_STORAGE_KEY) || "null");
        if (!ownerScope || !existing?.owner_id || existing.owner_id === ownerScope) {
            storage.removeItem(PREVIEW_RESUME_STORAGE_KEY);
        }
    } catch (_error) {
        storage.removeItem(PREVIEW_RESUME_STORAGE_KEY);
    }
}

export function getSnapchatManagementPreviewResume(ownerId = "") {
    const ownerScope = text(ownerId);
    const storage = previewSessionStorage();
    if (!storage || !ownerScope) return null;
    try {
        const value = JSON.parse(storage.getItem(PREVIEW_RESUME_STORAGE_KEY) || "null");
        const savedAt = Number(value?.saved_at);
        if (
            value?.version !== SAFETY_PROTOCOL_VERSION
            || value?.owner_id !== ownerScope
            || !Number.isFinite(savedAt)
            || Date.now() - savedAt > PREVIEW_RESUME_TTL_MS
        ) {
            storage.removeItem(PREVIEW_RESUME_STORAGE_KEY);
            return null;
        }
        const request = proposalRequest(value.request || {});
        if (!request.idempotency_key || request.idempotency_key !== value.idempotency_key) {
            storage.removeItem(PREVIEW_RESUME_STORAGE_KEY);
            return null;
        }
        return {
            owner_id: ownerScope,
            saved_at: savedAt,
            request,
            idempotency_key: request.idempotency_key,
            preview_job_id: text(value.preview_job_id) || null,
        };
    } catch (_error) {
        storage.removeItem(PREVIEW_RESUME_STORAGE_KEY);
        return null;
    }
}

function saveSnapchatManagementPreviewResume({ ownerId, request, previewJobId = null }) {
    const ownerScope = text(ownerId);
    const storage = previewSessionStorage();
    if (!storage || !ownerScope) return;
    const normalizedRequest = proposalRequest(request);
    storage.setItem(PREVIEW_RESUME_STORAGE_KEY, JSON.stringify({
        version: SAFETY_PROTOCOL_VERSION,
        owner_id: ownerScope,
        saved_at: Date.now(),
        request: normalizedRequest,
        idempotency_key: normalizedRequest.idempotency_key,
        preview_job_id: text(previewJobId) || null,
    }));
}

function proposalRequest(input = {}) {
    if (!ACTIONS.has(input.action)) throw new Error("إجراء Snapchat غير مدعوم.");
    return {
        action: input.action,
        account_id: text(input.account_id),
        target_id: text(input.target_id) || null,
        parent_id: text(input.parent_id) || null,
        provider_target_id: text(input.provider_target_id) || null,
        provider_parent_id: text(input.provider_parent_id) || null,
        settings_proof: object(input.settings_proof),
        payload: object(input.payload),
        reason: text(input.reason),
        idempotency_key: text(input.idempotency_key),
        activation_acknowledged: input.activation_acknowledged === true,
        safety_protocol_version: SAFETY_PROTOCOL_VERSION,
        expected_outcome: input.expected_outcome === null
            || input.expected_outcome === undefined
            ? null
            : object(input.expected_outcome),
        supporting_evidence: objectList(input.supporting_evidence),
        products: objectList(input.products),
        trend_override_reason: text(input.trend_override_reason) || null,
    };
}

function isTransientPreviewTransportError(error) {
    if (!error?.response) return true;
    return TRANSIENT_PREVIEW_TRANSPORT_STATUSES.has(
        Number(error.response.status),
    );
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
        accounts: Array.isArray(value.accounts) ? value.accounts.map((account) => {
            const pixels = Array.isArray(account?.pixels) ? account.pixels.map((pixel) => ({
                pixel_id: text(pixel?.pixel_id),
                display_name: text(pixel?.display_name, pixel?.pixel_id || "Snap Pixel"),
                status: text(pixel?.status) || null,
                effective_status: text(pixel?.effective_status) || null,
                diagnostics_status: text(pixel?.diagnostics_status) || null,
                has_event_data: pixel?.has_event_data === true,
                last_observed_at: text(pixel?.last_observed_at) || null,
            })).filter((pixel) => pixel.pixel_id) : [];
            return {
                account_id: text(account?.account_id),
                display_name: text(account?.display_name, account?.account_id || "حساب Snapchat"),
                currency: text(account?.currency) || null,
                timezone: text(account?.timezone),
                role: text(account?.role) || null,
                management_allowed: account?.management_allowed === true,
                reason: text(account?.reason) || null,
                creative_role: text(account?.creative_role) || null,
                creative_allowed: account?.creative_allowed === true,
                creative_reason: text(account?.creative_reason) || null,
                pixels,
                pixel_selection_required: account?.pixel_selection_required === true
                    || pixels.length > 1,
            };
        }).filter((account) => account.account_id) : [],
    };
}

export function verifiedSnapchatManagementEntityId(payload = {}) {
    const value = object(payload?.data || payload);
    const verification = object(value.verification);
    const verifiedEntityId = text(verification.entity_id);
    const providerEntityId = text(value.provider_entity_id);

    if (
        text(value.status) !== "completed"
        || value.provider_write_reached !== true
        || text(value.provider_write_state) !== "confirmed"
        || value.provider_write_uncertain !== false
        || verification.verified !== true
        || !providerEntityId
        || !verifiedEntityId
    ) {
        return null;
    }
    if (providerEntityId !== verifiedEntityId) {
        return null;
    }
    return providerEntityId;
}

export function normalizeSnapchatManagementProposal(payload = {}) {
    const value = object(payload?.data || payload);
    const changeAuditEnvelope = object(value.change_audit);
    const fieldChangesEnvelope = Array.isArray(value.field_changes)
        ? {}
        : Object.keys(object(value.field_changes)).length
            ? object(value.field_changes)
            : changeAuditEnvelope;
    const fieldChangesFields = object(fieldChangesEnvelope.fields);
    const fieldChangeMetadata = {
        ...Object.fromEntries(
            Object.entries(changeAuditEnvelope)
                .filter(([field]) => [
                    "actor_id",
                    "actor_name",
                    "occurred_at",
                    "provider_entity_id",
                    "provider_reread_verified",
                    "reread",
                ].includes(field)),
        ),
        ...Object.fromEntries(
            Object.entries(fieldChangesEnvelope)
                .filter(([field]) => [
                    "actor_id",
                    "actor_name",
                    "occurred_at",
                    "provider_entity_id",
                    "provider_reread_verified",
                    "reread",
                ].includes(field)),
        ),
        ...object(value.field_change_metadata),
    };
    const directFieldChanges = Object.fromEntries(
        Object.entries(fieldChangesEnvelope)
            .filter(([field]) => ![
                "fields",
                "actor_id",
                "actor_name",
                "occurred_at",
                "provider_entity_id",
                "provider_reread_verified",
                "reread",
            ].includes(field)),
    );
    const fieldChanges = Array.isArray(value.field_changes)
        ? value.field_changes
        : Array.isArray(fieldChangesEnvelope.fields)
            ? fieldChangesEnvelope.fields
            : Object.keys(fieldChangesFields).length
                ? Object.entries(fieldChangesFields).map(([field, change]) => ({
                    field,
                    ...object(change),
                }))
                : Object.entries(directFieldChanges)
                    .map(([field, change]) => ({ field, ...object(change) }));
    const rawSettingsProof = object(value.settings_proof);
    const settingsProof = normalizeSnapchatEntitySettings({
        ...rawSettingsProof,
        account_currency: rawSettingsProof.account_currency || rawSettingsProof.currency,
        settings_synced_at: (
            rawSettingsProof.settings_synced_at
            || rawSettingsProof.last_synced_at
        ),
        quality: {
            ...object(rawSettingsProof.quality),
            settings_status: (
                object(rawSettingsProof.quality).settings_status
                || rawSettingsProof.settings_status
            ),
            freshness_seconds: (
                object(rawSettingsProof.quality).freshness_seconds
                ?? rawSettingsProof.freshness_seconds
            ),
            freshness_threshold_seconds: (
                object(rawSettingsProof.quality).freshness_threshold_seconds
                ?? rawSettingsProof.freshness_threshold_seconds
            ),
            reason: object(rawSettingsProof.quality).reason || rawSettingsProof.reason,
            financial_controls_allowed: (
                object(rawSettingsProof.quality).financial_controls_allowed === true
                || rawSettingsProof.financial_controls_allowed === true
            ),
            financial_field_controls: (
                Object.keys(object(object(rawSettingsProof.quality).financial_field_controls)).length
                    ? object(object(rawSettingsProof.quality).financial_field_controls)
                    : object(rawSettingsProof.financial_field_controls)
            ),
        },
    });
    const action = ACTIONS.has(value.action) ? value.action : null;
    const providerStatus = text(value.status, "unknown");
    const status = providerStatus.endsWith("_v2")
        ? providerStatus.slice(0, -3)
        : providerStatus;
    const rawVerification = object(value.verification);
    const failure = object(value.failure);
    const rereadEnvelope = object(
        fieldChangeMetadata.reread
        || value.provider_reread,
    );
    const providerReadback = object(
        value.provider_readback
        || rawVerification.provider_readback
        || rawVerification.provider_snapshot
        || rereadEnvelope.snapshot
        || value.reconciliation_snapshot,
    );
    const mismatchedFields = Array.isArray(rawVerification.mismatched_fields)
        ? rawVerification.mismatched_fields
        : Array.isArray(failure.mismatched_fields)
            ? failure.mismatched_fields
            : [];
    const verification = {
        ...rawVerification,
        source: text(rawVerification.source || rawVerification.verification_source) || null,
        mismatched_fields: mismatchedFields,
    };
    return {
        proposal_id: text(value.proposal_id),
        status,
        provider_status: providerStatus,
        revision: Math.max(1, Math.trunc(number(value.revision) || 1)),
        action,
        account_id: text(value.account_id),
        account_currency: settingsProof.account_currency,
        target_id: text(value.target_id) || null,
        parent_id: text(value.parent_id) || null,
        provider_target_id: text(value.provider_target_id) || null,
        provider_parent_id: text(value.provider_parent_id) || null,
        settings_proof: settingsProof,
        reason: text(value.reason),
        expected_outcome: value.expected_outcome === null
            || value.expected_outcome === undefined
            ? null
            : object(value.expected_outcome),
        supporting_evidence: objectList(value.supporting_evidence),
        products: objectList(value.products),
        trend_override_reason: text(value.trend_override_reason) || null,
        preview: object(value.preview),
        creates_paused: value.creates_paused === true,
        activates_delivery: value.activates_delivery === true,
        confirm_token: text(value.confirm_token) || null,
        confirmation_phrase: text(value.confirmation_phrase) || null,
        created_at: text(value.created_at) || null,
        actor_id: text(value.actor_id || fieldChangeMetadata.actor_id || value.executed_by || value.created_by) || null,
        actor_name: text(value.actor_name || fieldChangeMetadata.actor_name || value.executor_name) || null,
        field_changes: fieldChanges.map(object).filter((item) => Object.keys(item).length),
        field_changes_known: Array.isArray(value.field_changes)
            || Array.isArray(fieldChangesEnvelope.fields)
            || Object.keys(fieldChangesFields).length > 0
            || Object.keys(directFieldChanges).length > 0,
        preview_changed_fields_known: Array.isArray(value.preview?.changed_fields),
        field_changes_metadata: {
            actor_id: text(fieldChangeMetadata.actor_id) || null,
            actor_name: text(fieldChangeMetadata.actor_name) || null,
            occurred_at: text(fieldChangeMetadata.occurred_at) || null,
            provider_entity_id: text(fieldChangeMetadata.provider_entity_id) || null,
            provider_reread_verified: fieldChangeMetadata.provider_reread_verified === true,
            reread: object(fieldChangeMetadata.reread),
        },
        expires_at: text(value.expires_at) || null,
        approved_at: text(value.approved_at) || null,
        executed_at: text(value.executed_at) || null,
        failed_at: text(value.failed_at) || null,
        verification,
        provider_readback: providerReadback,
        provider_reread: {
            ...rereadEnvelope,
            verified: rereadEnvelope.verified === true
                || fieldChangeMetadata.provider_reread_verified === true
                || rawVerification.verified === true,
            snapshot: providerReadback,
            mismatched_fields: mismatchedFields,
        },
        rollback: object(value.rollback),
        failure,
        recovery_action: text(value.recovery_action) || null,
        safety_protocol_version: Math.max(
            1,
            Math.trunc(number(value.safety_protocol_version) || 1),
        ),
        execution_retryable: value.execution_retryable === true,
        automatic_retry_allowed: value.automatic_retry_allowed === true,
        pixel_eligibility: object(value.pixel_eligibility),
        provider_write_reached: value.provider_write_reached === true,
        provider_write_state: text(value.provider_write_state, "not_attempted"),
        provider_write_uncertain: value.provider_write_uncertain === true,
        provider_entity_id: text(value.provider_entity_id) || null,
        verified_entity_id: verifiedSnapchatManagementEntityId(value),
        accounting_write_reached: value.accounting_write_reached === true,
        qoyod_write_reached: value.qoyod_write_reached === true,
    };
}

export function normalizeSnapchatManagementPreviewJob(payload = {}) {
    const value = object(payload?.data || payload);
    return {
        provider: "snapchat_ads",
        preview_job_id: text(value.preview_job_id),
        status: text(value.status, "unknown"),
        proposal_id: text(value.proposal_id) || null,
        created_at: text(value.created_at) || null,
        started_at: text(value.started_at) || null,
        finished_at: text(value.finished_at) || null,
        phase: text(value.phase, "unknown"),
        phase_started_at: text(value.phase_started_at) || null,
        terminal_reconciled: value.terminal_reconciled === true,
        reconcile_deadline_at: text(value.reconcile_deadline_at) || null,
        recovery_action: text(value.recovery_action) || null,
        failure: object(value.failure),
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

export async function getSnapchatEntitySettings({
    entityType,
    unifiedEntityId = "",
    parentUnifiedId = "",
    limit = 500,
} = {}) {
    const normalizedType = text(entityType);
    if (!["campaign", "ad_squad"].includes(normalizedType)) {
        throw new Error("invalid_snapchat_settings_entity_type");
    }
    const response = await api.get(`${BASE}/entity-settings`, {
        params: {
            entity_type: normalizedType,
            unified_entity_id: text(unifiedEntityId) || undefined,
            parent_unified_id: text(parentUnifiedId) || undefined,
            limit: Math.min(500, Math.max(1, Math.trunc(Number(limit) || 500))),
        },
    });
    return settingsItems(response.data);
}

export async function diagnoseSnapchatManagementPixels({ days = 7 } = {}) {
    const parsedDays = Math.trunc(Number(days));
    if (!Number.isInteger(parsedDays) || parsedDays < 1 || parsedDays > 62) {
        throw new Error("invalid_snapchat_tracking_days");
    }
    const response = await api.post(
        "/integrations-v2/snapchat_ads/tracking-diagnostics",
        {
            days: parsedDays,
            idempotency_key: `management-pixel-${Date.now()}`,
        },
    );
    return object(response.data);
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

export async function startSnapchatManagementPreviewJob(
    input = {},
    {
        attempts = MAX_PREVIEW_START_ATTEMPTS,
        intervalMs = 250,
        wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
    } = {},
) {
    // Build once so every transport retry carries the exact same bounded body
    // and idempotency key.  A lost 202 can therefore only recover the durable
    // Mongo job; it cannot start a logically new preview.
    const request = proposalRequest(input);
    const maxAttempts = Math.min(
        MAX_PREVIEW_START_ATTEMPTS,
        Math.max(1, Math.trunc(Number(attempts) || MAX_PREVIEW_START_ATTEMPTS)),
    );
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
        let response;
        try {
            response = await api.post(`${BASE}/preview-jobs`, request);
        } catch (error) {
            const canRetry = isTransientPreviewTransportError(error)
                && attempt < maxAttempts - 1;
            if (!canRetry) throw error;
            await wait(intervalMs);
            continue;
        }
        return normalizeSnapchatManagementPreviewJob(response.data);
    }
    throw new Error("تعذّر بدء تجهيز معاينة Snapchat.");
}

export async function getSnapchatManagementPreviewJob(previewJobId) {
    const response = await api.get(
        `${BASE}/preview-jobs/${encodeURIComponent(text(previewJobId))}`,
    );
    return normalizeSnapchatManagementPreviewJob(response.data);
}

export async function getCurrentSnapchatManagementPreviewJob(idempotencyKey) {
    const response = await api.get(`${BASE}/preview-jobs/current`, {
        params: { idempotency_key: text(idempotencyKey) },
    });
    return normalizeSnapchatManagementPreviewJob(response.data);
}

export async function pollSnapchatManagementPreviewJob({
    previewJobId,
    attempts = 180,
    intervalMs = 1000,
    wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
    load = getSnapchatManagementPreviewJob,
} = {}) {
    const normalizedId = text(previewJobId);
    const maxAttempts = Math.max(1, Math.trunc(Number(attempts) || 180));
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
        let job;
        try {
            job = await load(normalizedId);
        } catch (error) {
            if (!isTransientPreviewTransportError(error)) throw error;
        }
        if (
            job
            && (
                job.status === "ready"
                || (job.status === "failed" && job.terminal_reconciled)
            )
        ) return job;
        if (attempt < maxAttempts - 1) await wait(intervalMs);
    }
    const error = new Error(
        "ما زال تجهيز المعاينة مستمرًا في الخلفية. لا تنشئ معاينة أخرى الآن؛ حدّث السجل لاحقًا.",
    );
    error.code = "snapchat_management_preview_poll_timeout";
    error.preview_job_id = normalizedId;
    throw error;
}

function previewJobFailure(job) {
    const failure = object(job?.failure);
    const error = new Error(
        text(failure.message, "تعذّر تجهيز معاينة Snapchat في الخلفية."),
    );
    error.code = text(failure.code, "snapchat_management_preview_failed");
    error.preview_job_id = job?.preview_job_id || null;
    error.response = { data: { detail: failure } };
    return error;
}

async function createSnapchatManagementProposalOnce(request, ownerScope) {
    let accepted;
    try {
        accepted = await startSnapchatManagementPreviewJob(request);
    } catch (startError) {
        if (!isTransientPreviewTransportError(startError)) {
            clearSnapchatManagementPreviewResume(ownerScope);
            throw startError;
        }
        try {
            accepted = await getCurrentSnapchatManagementPreviewJob(
                request.idempotency_key,
            );
        } catch (lookupError) {
            if (Number(lookupError?.response?.status) === 404) {
                clearSnapchatManagementPreviewResume(ownerScope);
            }
            throw startError;
        }
    }
    if (!accepted.preview_job_id) {
        clearSnapchatManagementPreviewResume(ownerScope);
        throw new Error("لم يُعد ميزان معرّف مهمة المعاينة.");
    }
    if (ownerScope) {
        saveSnapchatManagementPreviewResume({
            ownerId: ownerScope,
            request,
            previewJobId: accepted.preview_job_id,
        });
    }
    const job = await pollSnapchatManagementPreviewJob({
        previewJobId: accepted.preview_job_id,
    });
    if (job.status === "failed") {
        if (ownerScope && job.terminal_reconciled) {
            clearSnapchatManagementPreviewResume(ownerScope);
        }
        throw previewJobFailure(job);
    }

    // The worker deliberately never persists the plaintext confirmation token.
    // Replaying the same bounded request is idempotent and only rotates a token
    // on the already-prepared proposal; it does not repeat baseline/provider reads.
    const response = await api.post(`${BASE}/proposals`, request);
    return normalizeSnapchatManagementProposal(response.data);
}

function trackPreviewPreparation(inflightKey, operation) {
    const tracked = operation.finally(() => {
        if (previewPreparationInflight.get(inflightKey) === tracked) {
            previewPreparationInflight.delete(inflightKey);
        }
    });
    previewPreparationInflight.set(inflightKey, tracked);
    return tracked;
}

export function createSnapchatManagementProposal(
    input = {},
    { ownerId = "" } = {},
) {
    const request = proposalRequest(input);
    const ownerScope = text(ownerId);
    if (!ownerScope) {
        return createSnapchatManagementProposalOnce(request, ownerScope);
    }
    const inflightKey = `${ownerScope}:${request.idempotency_key}`;
    const existing = previewPreparationInflight.get(inflightKey);
    if (existing) return existing;
    saveSnapchatManagementPreviewResume({ ownerId: ownerScope, request });
    return trackPreviewPreparation(
        inflightKey,
        createSnapchatManagementProposalOnce(request, ownerScope),
    );
}

async function resumeSnapchatManagementProposalOnce({ ownerId, saved }) {
    const ownerScope = text(ownerId);
    let job;
    if (saved.preview_job_id) {
        job = await pollSnapchatManagementPreviewJob({
            previewJobId: saved.preview_job_id,
        });
    } else {
        try {
            const current = await getCurrentSnapchatManagementPreviewJob(
                saved.idempotency_key,
            );
            if (!current.preview_job_id) {
                throw new Error("لم يُعد ميزان معرّف مهمة المعاينة.");
            }
            saveSnapchatManagementPreviewResume({
                ownerId: ownerScope,
                request: saved.request,
                previewJobId: current.preview_job_id,
            });
            job = await pollSnapchatManagementPreviewJob({
                previewJobId: current.preview_job_id,
            });
        } catch (error) {
            if (Number(error?.response?.status) === 404) {
                clearSnapchatManagementPreviewResume(ownerScope);
            }
            throw error;
        }
    }
    if (job.status === "failed") {
        if (job.terminal_reconciled) {
            clearSnapchatManagementPreviewResume(ownerScope);
        }
        throw previewJobFailure(job);
    }
    const response = await api.post(`${BASE}/proposals`, saved.request);
    return normalizeSnapchatManagementProposal(response.data);
}

export function resumeSnapchatManagementProposal({ ownerId = "" } = {}) {
    const ownerScope = text(ownerId);
    const saved = getSnapchatManagementPreviewResume(ownerScope);
    if (!saved) return Promise.resolve(null);
    const inflightKey = `${ownerScope}:${saved.idempotency_key}`;
    const existing = previewPreparationInflight.get(inflightKey);
    if (existing) return existing;
    const operation = resumeSnapchatManagementProposalOnce({
        ownerId: ownerScope,
        saved,
    });
    return trackPreviewPreparation(inflightKey, operation);
}

export async function approveSnapchatManagementProposal(
    proposal,
    { ownerId = "" } = {},
) {
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
    const result = normalizeSnapchatManagementProposal(response.data);
    clearSnapchatManagementPreviewResume(ownerId);
    return result;
}

export async function executeSnapchatManagementProposal(proposalId) {
    const response = await api.post(
        `${BASE}/proposals/${encodeURIComponent(text(proposalId))}/execute`,
    );
    return normalizeSnapchatManagementProposal(response.data);
}

export async function reconcileSnapchatManagementProposal(proposalId) {
    const response = await api.post(
        `${BASE}/proposals/${encodeURIComponent(text(proposalId))}/reconcile`,
    );
    return normalizeSnapchatManagementProposal(response.data);
}

export async function pollSnapchatManagementProposal({
    proposalId,
    attempts = 30,
    intervalMs = 1000,
    wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
    load = listSnapchatManagementProposals,
} = {}) {
    const normalizedId = text(proposalId);
    const maxAttempts = Math.max(1, Math.trunc(Number(attempts) || 30));
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
        const proposals = await load({ limit: 100 });
        const proposal = proposals.find((row) => row.proposal_id === normalizedId);
        if (proposal && ["completed", "failed"].includes(proposal.status)) {
            return { proposal, proposals };
        }
        if (attempt < maxAttempts - 1) await wait(intervalMs);
    }
    const error = new Error(
        "لم تظهر النتيجة النهائية بعد. لا تضغط تنفيذ مرة أخرى؛ حدّث السجل لاحقًا.",
    );
    error.code = "snapchat_management_execution_poll_timeout";
    throw error;
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
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0) return null;
    return Math.round(parsed * 1_000_000);
}

export function microToNativeAmount(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed / 1_000_000 : null;
}
