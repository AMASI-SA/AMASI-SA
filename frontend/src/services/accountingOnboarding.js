import api from "../lib/api";

// Issue #1006 / 5900935996 + 5900990152 + 5901180648. Setup metadata only; no financial handoff/post/activation methods.
const BASE = "/accounting-module/onboarding";
const FINANCIAL_BASE = "/api/financial-provider-apps/accounting-module/financial-accounts";
function accountsPath(financialBase) {
    // Use the server definition only after recognizing the reviewed mount point.
    // api already supplies /api; absolute URLs and legacy paths are not accepted.
    if (financialBase !== FINANCIAL_BASE) throw new Error("onboarding_financial_base_invalid");
    return financialBase.slice(4);
}
const idPath = id => `${BASE}/sessions/${encodeURIComponent(id)}`;
const body = result => result.data;
const SECTIONS = new Set(["banks_cash", "providers", "couriers_cod", "inventory", "suppliers", "payroll_obligations", "equity"]);
const KINDS = new Set(["bank", "provider", "employee", "supplier", "external_person", "courier", "store_driver", "ad_account"]);

export const getOnboardingDefinitions = () => api.get(`${BASE}/definitions`).then(body);
export const listOnboardingSessions = () => api.get(`${BASE}/sessions`).then(body);
export const getOnboardingSession = id => api.get(idPath(id)).then(body);
export const getOnboardingReadiness = id => api.get(`${idPath(id)}/readiness`).then(body);
export const getOnboardingFinancialAccounts = financialBase => api.get(accountsPath(financialBase)).then(body);
export function getOnboardingIdentities(kind) {
    if (!KINDS.has(kind)) throw new Error("onboarding_identity_kind_invalid");
    return api.get(`${BASE}/identities/${kind}`).then(body);
}
// Existing contact registry contract: the returned id is the external_person
// entity_id verbatim. Never synthesize a session-local contact identity.
export async function createOnboardingExternalPerson({ name, phone = "", notes = "" }) {
    const person = await api.post("/counterparties", { kind: "general", name, phone, notes }).then(body);
    if (typeof person?.id !== "string" || !person.id.trim() || person.kind !== "general") {
        throw new Error("onboarding_external_person_response_invalid");
    }
    return person;
}
export const createOnboardingSession = payload => api.post(`${BASE}/sessions`, payload).then(body);
export const saveOnboardingCutover = (id, payload) => api.put(`${idPath(id)}/cutover`, payload).then(body);
export const saveOnboardingSetupDraft = (id, payload) => api.put(`${idPath(id)}/setup-draft`, payload).then(body);
export const getOnboardingSourceGaps = () => api.get(`${BASE}/source-gaps`).then(body);
export const getOnboardingRecurringObligations = () => api.get(`${BASE}/recurring-obligations`).then(body);
export function saveOnboardingSection(id, sectionId, payload) {
    if (!SECTIONS.has(sectionId)) throw new Error("onboarding_section_invalid");
    return api.put(`${idPath(id)}/sections/${sectionId}`, payload).then(body);
}
export const previewOnboardingSession = (id, payload) => api.post(`${idPath(id)}/preview`, payload).then(body);
export const reviewOnboardingSession = (id, payload) => api.post(`${idPath(id)}/review`, payload).then(body);

export function uploadOnboardingEvidence({ file, purpose, sectionId, financialBase }) {
    const accounts = accountsPath(financialBase);
    if (!["cutover", "opening_balance", "fx_rate"].includes(purpose)
        || (purpose === "cutover" ? Boolean(sectionId) : !SECTIONS.has(sectionId))) throw new Error("opening_evidence_contract_mismatch");
    const form = new FormData();
    form.append("file", file); form.append("purpose", purpose);
    if (sectionId) form.append("section_id", sectionId);
    return api.post(`${accounts}/opening-balances/evidence`, form).then(body);
}

const PUBLIC_ERRORS = {
    onboarding_financial_base_invalid: "مسار الحسابات المالية لا يطابق العقد المعتمد؛ أعد تحميل تعريفات التأسيس.",
    onboarding_supplier_link_required: "يجب ربط المورد بهويته المعتمدة قبل إكمال القسم.",
    onboarding_entity_balance_required: "أدخل رصيد الجهة أو صفرًا صريحًا قبل إكمال القسم.",
    supplier_link_required: "يجب ربط المورد بهويته المعتمدة قبل إكمال القسم.",
    entity_balance_required: "أدخل رصيد الجهة أو صفرًا صريحًا قبل إكمال القسم.",
    onboarding_amount_invalid: "أدخل مبلغًا صحيحًا أو صفرًا صريحًا في الحقل المطلوب.",
    onboarding_cutover_required: "حدد تاريخ ووقت القطع قبل حفظ الجلسة.",
    onboarding_inventory_account_required: "اختر حساب المخزون المالي المرتبط بالقيمة الافتتاحية.",
    onboarding_account_currency_mismatch: "عملة المبلغ لا تطابق العملة المسجلة للحساب المالي.",
    onboarding_financial_account_unresolved: "اختر حسابًا ماليًا معرفًا بنوع وعملة صالحين.",
    onboarding_subaccount_metadata_conflict: "بيانات الدليل أو العملة لا تتطابق بين أرصدة الجهة؛ راجعها قبل الحفظ.",
    onboarding_financial_identity_conflict: "هوية الحساب المالي لا تتطابق مع الجهة المختارة.",
    onboarding_classification_invalid: "اختر تصنيفًا معتمدًا لهذا القسم.",
    onboarding_duplicate_inventory_account: "حساب المخزون مكرر؛ راجع توزيع القيمة قبل الحفظ.",
    onboarding_domain_local_stage: "حفظ إعدادات هذا القسم يتطلب عقدًا إضافيًا؛ لم تُحفظ هذه التعديلات على الخادم.",
    onboarding_pending_request_requires_retry_or_reload: "طلب الحفظ السابق لم يُحسم؛ أعد محاولته أو استعد الجلسة قبل حفظ تعديل جديد.",
    onboarding_reload_required: "استعد النسخة المحفوظة من الجلسة قبل متابعة التعديل.",
    onboarding_session_required: "أنشئ جلسة أو اختر جلسة محفوظة أولًا.",
    onboarding_session_already_selected: "توجد جلسة مختارة بالفعل؛ استعد الجلسة المطلوبة للمتابعة.",
    onboarding_response_invalid: "استجابة الجلسة لا تطابق العقد؛ احتفظ بالتعديلات وأعد تحميل الجلسة.",
    onboarding_response_version_invalid: "نسخة الجلسة المستلمة غير متوقعة؛ أعد تحميل النسخة المحفوظة.",
    onboarding_external_person_response_invalid: "تعذر التحقق من هوية الطرف المحفوظ؛ استعد قائمة الجهات قبل المحاولة مجددًا.",
    duplicate: "هذا الطرف موجود بالفعل؛ اختره من قائمة الجهات.",
    similar_name_exists: "يوجد طرف باسم مشابه؛ راجع قائمة الجهات قبل إنشاء طرف جديد.",
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
    const code = typeof detail === "object" && detail !== null ? (detail.code || detail.message) : detail;
    const known = value => typeof value === "string" && Object.prototype.hasOwnProperty.call(PUBLIC_ERRORS, value) ? PUBLIC_ERRORS[value] : null;
    return known(code) || known(error?.message) || (error?.response?.status === 422 ? "راجع الحقول المطلوبة وصيغة المبالغ والتوقيت." : "تعذر إكمال الطلب. احتفظ بالتعديلات وحاول استعادة الجلسة.");
}
