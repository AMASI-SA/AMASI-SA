import api from "../lib/api";

const BASE = "/financial-provider-apps/accounting-module";
const SETTLEMENTS = `${BASE}/settlements`;

function compactParams(params = {}) {
    return Object.fromEntries(
        Object.entries(params).filter(([, value]) => (
            value !== undefined && value !== null && value !== ""
        )),
    );
}

// The authoritative P01 lifecycle stores the state as `matched`. The original
// settlements screen was built against the compatibility label
// `ready_for_review`. Preserve the workflow state while translating only the
// presentation status so the review action remains available during cutover.
function normalizeSettlementDraftForUi(draft) {
    if (!draft || typeof draft !== "object") return draft;
    if (draft.status !== "matched") return draft;
    return {
        ...draft,
        status: "ready_for_review",
        workflow_state: draft.workflow_state || "matched",
    };
}

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

export async function getAccountingCourierBankBindings() {
    const { data } = await api.get(`${SETTLEMENTS}/courier-bindings`);
    return data;
}

export async function saveAccountingCourierBankBinding(courierKey, payload) {
    const { data } = await api.put(
        `${SETTLEMENTS}/courier-bindings/${encodeURIComponent(courierKey)}`,
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
    if (data?.draft) {
        return { ...data, draft: normalizeSettlementDraftForUi(data.draft) };
    }
    return data;
}

export async function createAccountingSettlementDraftFromFile(payload) {
    const { data } = await api.post(`${SETTLEMENTS}/drafts/from-file`, payload);
    return normalizeSettlementDraftForUi(data);
}

export async function getAccountingSettlementDrafts(params = {}) {
    const { data } = await api.get(`${SETTLEMENTS}/drafts`, {
        params: compactParams(params),
    });
    return {
        ...(data || {}),
        items: (data?.items || []).map(normalizeSettlementDraftForUi),
    };
}

export async function getAccountingSettlementDraft(draftId) {
    const { data } = await api.get(`${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}`);
    return normalizeSettlementDraftForUi(data);
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
    return normalizeSettlementDraftForUi(data);
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
    return normalizeSettlementDraftForUi(data);
}

export async function matchAccountingSettlementEntry(draftId, payload) {
    const { data } = await api.post(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/match-entry`,
        payload,
    );
    return normalizeSettlementDraftForUi(data);
}

export async function getAccountingSettlementBankCandidates(draftId, params = {}) {
    const { data } = await api.get(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/bank-candidates`,
        { params: compactParams(params) },
    );
    return data;
}

export async function saveAccountingSettlementBankMatch(draftId, payload) {
    const { data } = await api.put(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/bank-match`,
        payload,
    );
    return normalizeSettlementDraftForUi(data);
}

export async function submitAccountingSettlementDraft(draftId, notes = "") {
    const { data } = await api.post(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/submit`,
        { notes },
    );
    return normalizeSettlementDraftForUi(data);
}

export async function reviewAccountingSettlementDraft(draftId, notes = "") {
    const { data } = await api.post(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/review`,
        { notes },
    );
    return normalizeSettlementDraftForUi(data);
}

export async function rejectAccountingSettlementDraft(draftId, reason) {
    const { data } = await api.post(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/reject`,
        { reason },
    );
    return normalizeSettlementDraftForUi(data);
}

export async function postAccountingSettlementDraft(draftId, notes = "") {
    const { data } = await api.post(
        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}/post`,
        { notes },
    );
    return normalizeSettlementDraftForUi(data);
}

export async function getAccountingSettlementRegister(params = {}) {
    const { data } = await api.get(`${SETTLEMENTS}/register`, {
        params: compactParams(params),
    });
    return data;
}

export async function getAccountingSettlementRegisterDetail(draftId) {
    const { data } = await api.get(
        `${SETTLEMENTS}/register/${encodeURIComponent(draftId)}`,
    );
    return data;
}


export async function getAccountingOpeningBalances() {
    const { data } = await api.get(`${BASE}/opening-balances`);
    return data;
}

export async function previewAccountingOpeningBalances(payload) {
    const { data } = await api.post(`${BASE}/opening-balances/preview`, payload);
    return data;
}

export async function approveAccountingOpeningBalances(previewId) {
    const { data } = await api.post(`${BASE}/opening-balances/approve`, {
        preview_id: previewId,
        confirmation: "APPROVE_OPENING_BALANCE",
    });
    return data;
}

export async function activateAccountingP01(activationRef) {
    const { data } = await api.post(`${BASE}/opening-balances/activate`, {
        activation_ref: activationRef,
        confirmation: "ACTIVATE_MZ2_P01",
    });
    return data;
}


export async function getAccountingDailyMovementContext() {
    const { data } = await api.get(`${BASE}/daily-movements/context`);
    return data;
}

export async function getAccountingDailyMovements(params = {}) {
    const { data } = await api.get(`${BASE}/daily-movements`, {
        params: compactParams(params),
    });
    return data;
}

export async function uploadAccountingDailyMovements({ bankAccountId, file }) {
    const form = new FormData();
    form.append("bank_account_id", bankAccountId);
    form.append("file", file);
    const { data } = await api.post(`${BASE}/daily-movements/upload`, form, {
        headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
}

export async function createAccountingManualIncomingMovement(payload) {
    const { data } = await api.post(`${BASE}/daily-movements/manual-incoming`, payload);
    return data;
}

export async function createAccountingManualOutgoingMovement(payload) {
    const { data } = await api.post(`${BASE}/daily-movements/manual-outgoing`, payload);
    return data;
}

export async function classifyAccountingOutgoingMovement(movementId, payload) {
    const { data } = await api.post(
        `${BASE}/daily-movements/${encodeURIComponent(movementId)}/classify-outgoing`,
        payload,
    );
    return data;
}

export async function getAccountingShippingWorkspace() {
    const { data } = await api.get(`${BASE}/shipping-p02/workspace`);
    return data;
}

export async function saveAccountingShippingRate(payload) {
    const { data } = await api.put(`${BASE}/shipping-p02/rates`, payload);
    return data;
}

export async function previewAccountingCourierFee(evidenceId) {
    const { data } = await api.get(
        `${BASE}/shipping-p02/courier-fee/${encodeURIComponent(evidenceId)}/preview`,
    );
    return data;
}

export async function postAccountingCourierFee(evidenceId) {
    const { data } = await api.post(`${BASE}/shipping-p02/courier-fee`, {
        evidence_id: evidenceId,
    });
    return data;
}

export async function previewAccountingStoreDriverCod(assignmentId) {
    const { data } = await api.get(
        `${BASE}/shipping-p02/store-driver-cod/${encodeURIComponent(assignmentId)}/preview`,
    );
    return data;
}

export async function postAccountingStoreDriverCod(assignmentId) {
    const { data } = await api.post(`${BASE}/shipping-p02/store-driver-cod`, {
        assignment_id: assignmentId,
    });
    return data;
}

export async function previewAccountingShippingSettlement(payload) {
    const { data } = await api.post(`${BASE}/shipping-p02/settlements/preview`, payload);
    return data;
}

export async function postAccountingShippingSettlement(payload) {
    const { data } = await api.post(`${BASE}/shipping-p02/settlements/post`, payload);
    return data;
}

export async function confirmAccountingDailyMovementProvider(movementId, provider, reason) {
    const { data } = await api.post(
        `${BASE}/daily-movements/${encodeURIComponent(movementId)}/confirm-provider`,
        { provider, reason },
    );
    return data;
}


export async function getAccountingPayrollContext() {
    const { data } = await api.get(`${BASE}/payroll/context`);
    return data;
}

export async function accrueAccountingPayroll(payload) {
    const { data } = await api.post(`${BASE}/payroll/accrue`, payload);
    return data;
}

export async function classifyAccountingEmployeeMovement(movementId, payload) {
    const { data } = await api.post(
        `${BASE}/payroll/movements/${encodeURIComponent(movementId)}/classify`,
        payload,
    );
    return data;
}


export async function uploadAccountingOrderEvidence(file) {
    const form = new FormData();
    form.append("file", file);
    const { data } = await api.post(`${BASE}/order-evidence/upload`, form, {
        headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
}

export async function getAccountingOrderRecognitionQueue(params = {}) {
    const { data } = await api.get(`${BASE}/order-recognition/queue`, {
        params: compactParams(params),
    });
    return data;
}

export async function recognizeAccountingReadyOrders({ limit = 100, dryRun = true, fileId = "" } = {}) {
    const { data } = await api.post(`${BASE}/order-recognition/recognize-ready`, {
        limit,
        dry_run: dryRun,
        ...(fileId ? { file_id: fileId } : {}),
    });
    return data;
}


export async function getAccountingBankTransferReviews(params = {}) {
    const { data } = await api.get(`${BASE}/bank-transfer-receipts`, {
        params: compactParams(params),
    });
    return data;
}

export async function uploadAccountingBankTransferReceipt(evidenceId, file, notes = "") {
    const form = new FormData();
    form.append("file", file);
    if (notes) form.append("notes", notes);
    const { data } = await api.post(
        `${BASE}/bank-transfer-receipts/${encodeURIComponent(evidenceId)}/receipt`,
        form,
        { headers: { "Content-Type": "multipart/form-data" } },
    );
    return data;
}

export async function getAccountingBankTransferCandidates(reviewId, params = {}) {
    const { data } = await api.get(
        `${BASE}/bank-transfer-receipts/${encodeURIComponent(reviewId)}/bank-candidates`,
        { params: compactParams(params) },
    );
    return data;
}

export async function getAccountingBankTransferReceiptFile(reviewId) {
    const { data } = await api.get(
        `${BASE}/bank-transfer-receipts/${encodeURIComponent(reviewId)}/receipt`,
        { responseType: "blob" },
    );
    return data;
}

export async function approveAccountingBankTransferReceipt(reviewId, movementId) {
    const { data } = await api.post(
        `${BASE}/bank-transfer-receipts/${encodeURIComponent(reviewId)}/approve`,
        {
            movement_id: movementId,
            confirmation: "CONFIRM_BANK_TRANSFER_RECEIPT",
        },
    );
    return data;
}

export async function convertAccountingDeliveredBankTransfers({
    fileId = "",
    limit = 300,
    dryRun = true,
} = {}) {
    const { data } = await api.post(`${BASE}/bank-transfer-receipts/convert-delivered`, {
        file_id: fileId || null,
        limit,
        dry_run: dryRun,
    });
    return data;
}
