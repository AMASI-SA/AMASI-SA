import api from "../lib/api";

// Issue #1006 / 5900935996. Setup metadata only; no financial handoff/post/activation methods.
const BASE = "/accounting-module/onboarding";
const ACCOUNTS = "/accounting-module/financial-accounts";
const idPath = id => `${BASE}/sessions/${encodeURIComponent(id)}`;
const body = result => result.data;
const SECTIONS = new Set(["banks_cash", "providers", "couriers_cod", "inventory", "suppliers", "payroll_obligations", "equity"]);
const KINDS = new Set(["bank", "provider", "employee", "supplier", "external_person", "courier", "store_driver", "ad_account"]);

export const getOnboardingDefinitions = () => api.get(`${BASE}/definitions`).then(body);
export const listOnboardingSessions = () => api.get(`${BASE}/sessions`).then(body);
export const getOnboardingSession = id => api.get(idPath(id)).then(body);
export const getOnboardingReadiness = id => api.get(`${idPath(id)}/readiness`).then(body);
export const getOnboardingFinancialAccounts = () => api.get(ACCOUNTS).then(body);
export function getOnboardingIdentities(kind) {
    if (!KINDS.has(kind)) throw new Error("onboarding_identity_kind_invalid");
    return api.get(`${BASE}/identities/${kind}`).then(body);
}
export const createOnboardingSession = payload => api.post(`${BASE}/sessions`, payload).then(body);
export const saveOnboardingCutover = (id, payload) => api.put(`${idPath(id)}/cutover`, payload).then(body);
export function saveOnboardingSection(id, sectionId, payload) {
    if (!SECTIONS.has(sectionId)) throw new Error("onboarding_section_invalid");
    return api.put(`${idPath(id)}/sections/${sectionId}`, payload).then(body);
}
export const previewOnboardingSession = (id, payload) => api.post(`${idPath(id)}/preview`, payload).then(body);
export const reviewOnboardingSession = (id, payload) => api.post(`${idPath(id)}/review`, payload).then(body);

export function uploadOnboardingEvidence({ file, purpose, sectionId }) {
    if (!["cutover", "opening_balance", "fx_rate"].includes(purpose)
        || (purpose === "cutover" ? Boolean(sectionId) : !SECTIONS.has(sectionId))) throw new Error("opening_evidence_contract_mismatch");
    const form = new FormData();
    form.append("file", file); form.append("purpose", purpose);
    if (sectionId) form.append("section_id", sectionId);
    return api.post(`${ACCOUNTS}/opening-balances/evidence`, form).then(body);
}

const PUBLIC_ERRORS = {
    onboarding_version_conflict: "تغيرت الجلسة في نافذة أخرى. أعد تحميل النسخة المحفوظة قبل المحاولة.",
    onboarding_idempotency_conflict: "تعارض مرجع الحفظ. أعد تحميل الجلسة للمراجعة.",
    onboarding_session_locked: "الجلسة مراجعة ومقفلة؛ لا يمكن تعديلها.",
    onboarding_sections_incomplete: "بعض الأقسام أو أدلتها غير مكتملة.",
    onboarding_not_applicable_conflict: "لا ينطبق يتعارض مع حساب أو تعيين مطلوب.",
    onboarding_identity_invalid: "هوية جهة غير صالحة أو غير مكتملة.",
    onboarding_provider_binding_required: "ربط مزود الدفع بالبنك ودليله مطلوبان.",
    onboarding_inventory_value_mismatch: "قيمة المخزون لا تطابق الأسطر الافتتاحية.",
    onboarding_snapshot_changed: "تغيرت الأدلة أو التعيينات؛ يلزم مراجعة جديدة.",
    accounting_permission_required: "الصلاحية المطلوبة غير متاحة.",
    opening_evidence_missing_or_foreign: "ملف الدليل غير متاح لهذه الجلسة.",
    opening_evidence_contract_mismatch: "الدليل لا يخص الغرض أو القسم المطلوب.",
    mz2_writes_paused: "هذا الإجراء محجوب أثناء إيقاف الكتابات المالية.",
};
export function onboardingErrorMessage(error) {
    const detail = error?.response?.data?.detail;
    const code = typeof detail === "object" && detail !== null ? detail.code : detail;
    return PUBLIC_ERRORS[code] || (error?.response?.status === 422 ? "راجع الحقول المطلوبة وصيغة المبالغ والتوقيت." : "تعذر إكمال الطلب. احتفظ بالتعديلات وحاول استعادة الجلسة.");
}
