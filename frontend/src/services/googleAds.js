import api from "../lib/api";

export const GOOGLE_ADS_ROOT = "/integrations-v2/google_ads";
export const GOOGLE_CAMPAIGN_LABELS = Object.freeze({
    SEARCH: "البحث", DISPLAY: "شبكة العرض", SHOPPING: "التسوق",
    PERFORMANCE_MAX: "Performance Max", DEMAND_GEN: "Demand Gen",
    APP: "التطبيقات", APP_INSTALLS: "تثبيت التطبيق", APP_ENGAGEMENT: "تفاعل التطبيق",
    APP_PRE_REGISTRATION: "التسجيل المسبق للتطبيق", HOTEL: "الفنادق والسفر",
    VIDEO: "فيديو YouTube التقليدي", SMART: "الحملات الذكية القديمة",
    LOCAL_SERVICES: "الخدمات المحلية", GLS_PERFORMANCE_MAX: "الخدمات المحلية Performance Max",
    PERFORMANCE_MAX_TRAVEL: "Performance Max للسفر",
});
export const GOOGLE_STATUS_LABELS = Object.freeze({
    ENABLED: "مفعّلة", PAUSED: "موقوفة", REMOVED: "محذوفة", UNKNOWN: "غير محسومة",
    ELIGIBLE: "مؤهّلة", LIMITED: "تسليم محدود", NOT_ELIGIBLE: "غير مؤهّلة",
    APPROVED: "مقبول", DISAPPROVED: "مرفوض", UNDER_REVIEW: "قيد المراجعة",
    previewed: "معاينة محفوظة", prepared: "معاينة محفوظة", validated: "معاينة محفوظة",
    executing: "قيد التنفيذ", submitted: "أُرسلت إلى جوجل", verifying: "قيد التحقق",
    outcome_unknown: "نتيجة غير مؤكدة", uncertain: "نتيجة غير مؤكدة",
    verification_pending: "بانتظار التحقق", completed: "مكتملة ومثبتة", verified: "مكتملة ومثبتة",
    failed: "لم تكتمل", rejected: "رُفضت", expired: "انتهت المعاينة", draft: "مسودة",
});
export const GOOGLE_BIDDING_LABELS = Object.freeze({
    MAXIMIZE_CONVERSIONS: "زيادة التحويلات", MAXIMIZE_CONVERSION_VALUE: "زيادة قيمة التحويلات",
    TARGET_CPA: "تكلفة مستهدفة للتحويل", TARGET_ROAS: "عائد مستهدف على الإنفاق",
    MAXIMIZE_CLICKS: "زيادة النقرات", TARGET_SPEND: "زيادة النقرات",
    MANUAL_CPC: "تكلفة نقرة يدوية", TARGET_IMPRESSION_SHARE: "حصة ظهور مستهدفة",
    MANUAL_CPM: "تكلفة ظهور يدوية", TARGET_CPM: "تكلفة ظهور مستهدفة",
    PERCENT_CPC: "نسبة تكلفة النقرة للفنادق",
});

export function googleIdempotencyKey() {
    if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
    if (typeof globalThis.crypto?.getRandomValues !== "function") {
        throw new Error("استخدم متصفحًا حديثًا واتصالًا آمنًا لتجهيز عملية جوجل.");
    }
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    return `google_${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

export function googleErrorMessage(error, fallback = "تعذر إكمال الطلب. حدّث البيانات ثم حاول مرة أخرى.") {
    const detail = error?.response?.data?.detail;
    const message = typeof detail === "object" && !Array.isArray(detail) ? detail?.message : null;
    if (typeof message === "string" && message.trim()) return message;
    if (detail?.code === "google_ai_response_incomplete") return "لم تكتمل نتيجة ذكاء جوجل. أعد المحاولة بعد قليل؛ لم تُنفّذ أي تغييرات على الحملات.";
    if (["google_ai_analysis_contract_invalid", "google_ai_recommendation_campaign_unknown", "google_ai_recommendation_evidence_unknown", "google_ai_recommendation_action_not_permitted"].includes(detail?.code)) return "تعذر اعتماد توصية الذكاء على بيانات جوجل المثبتة. أعد التحليل؛ لم تُنفّذ أي تغييرات على الحملات.";
    const analysisRequest = /\/ai\/analyze$/.test(error?.config?.url || "");
    if (analysisRequest && (detail?.code === "google_ai_timeout" || error?.code === "ECONNABORTED" || error?.response?.status === 504)) return "انتهت مهلة تحليل أداء جوجل. جرّب تحليل حملة واحدة أو أعد المحاولة بعد قليل. لم تُنفّذ أي تغييرات على الحملات.";
    if (detail?.code === "google_ai_timeout" || error?.code === "ECONNABORTED" && /\/ai\/draft$/.test(error?.config?.url || "")) return "انتهت مهلة تجهيز مسودة الذكاء. قلّل تفاصيل الطلب ثم أعد تجهيز المسودة للمراجعة.";
    if (detail?.code === "google_ai_unavailable") return "تعذر الاتصال بخدمة ذكاء ميزان. يمكنك استكمال المسودة يدويًا ثم إعادة محاولة الذكاء.";
    if (error?.response?.status === 409) return "تغيّرت النسخة المحفوظة أو الإعدادات. حدّث المسودة وأنشئ معاينة جديدة.";
    if (error?.response?.status === 403) return "صلاحيتك أو تفويض الحساب لا يسمح بهذه العملية. راجع صلاحيات الحساب في ميزان.";
    if (error?.response?.status === 401) return "انتهت جلسة الدخول. أعد تسجيل الدخول لإكمال العمل.";
    if (error?.response?.status === 422) return "راجع الحقول المطلوبة ومواد الإعلان ومتطلبات نوع الحملة.";
    return fallback;
}

export function isGoogleRequestCancelled(error) {
    return error?.name === "AbortError" || error?.name === "CanceledError" || error?.code === "ERR_CANCELED";
}

export function googleRecommendationAction(item = {}) {
    return ({ PAUSE_CAMPAIGN: "pause", RESUME_CAMPAIGN: "enable", CHANGE_BUDGET: "set_budget" })[item.action] || null;
}

export function googleConfidenceLabel(value) {
    const label = ({ high: "مرتفعة", medium: "متوسطة", low: "منخفضة" })[value];
    if (label) return label;
    if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return "غير متاحة";
    const number = Number(value);
    return `${number <= 1 ? number * 100 : number}%`;
}

// Each read has one owner; an older response cannot repopulate a newer account,
// date range, campaign, draft or language lookup.
export function createLatestGoogleResponseGuard() {
    let revision = 0;
    return { begin: () => ++revision, current: (token) => token === revision, invalidate: () => ++revision };
}

export function googleProposalCanApprove(proposal, executionStarted = false) {
    return !executionStarted && !!proposal?.confirmation_digest && !!proposal?.proposal_id
        && ["previewed", "prepared", "validated"].includes(proposal.status)
        && (!proposal.expires_at || Date.parse(proposal.expires_at) > Date.now());
}

export function googleProposalNeedsReconcile(proposal, executionStarted = false) {
    return executionStarted && !["completed", "verified", "failed", "rejected", "expired"].includes(proposal?.status)
        || ["executing", "submitted", "verifying", "uncertain", "outcome_unknown", "verification_pending"].includes(proposal?.status)
        || proposal?.local_finalization_pending === true;
}

export function googleCampaignCapabilities(readiness = {}, accountId) {
    const account = (readiness.accounts || []).find((item) => String(item.account_id || item.id) === String(accountId));
    const list = account?.campaign_types || readiness.campaign_types || readiness.capabilities?.campaign_types || [];
    const rows = Array.isArray(list) ? list : Object.entries(list).map(([type, value]) => ({ campaign_type: type, ...(typeof value === "object" ? value : { creation_supported: value === true }) }));
    return rows.map((item) => ({ ...item, label: GOOGLE_CAMPAIGN_LABELS[item.type || item.campaign_type || item.id] || item.label }));
}

export function googleCapabilityType(capability) {
    return String(capability.campaign_type || capability.type || capability.id || "");
}

export function googleCapabilityCanCreate(capability) {
    if (!capability) return false;
    if ([capability.api_creation_supported, capability.creation_supported, capability.can_create, capability.eligible, capability.account_eligible, capability.implemented].some((value) => value === false)) return false;
    const enabled = capability.create === true || capability.can_create === true || capability.creation_supported === true || capability.api_creation_supported === true;
    if (capability.create === false) return false;
    return enabled && !["not_started", "pending", "unsupported", "blocked", "not_implemented"].includes(capability.implementation_status);
}

export function googleCapabilityReason(capability) {
    const gaps = capability?.missing_prerequisites || capability?.prerequisites_missing || [];
    const limitation = {
        VIDEO: "إنشاء حملات فيديو YouTube التقليدية غير متاح عبر Google Ads API. فيديو Demand Gen نوع مختلف ويمكن اختياره صراحة.",
        SMART: "أوقفت جوجل إنشاء حملات Smart الجديدة. تُعرض الحملات الموجودة حسب قدرات القراءة والإدارة المتاحة.",
        LOCAL_SERVICES: "إنشاء حملات الخدمات المحلية غير متاح عبر Google Ads API. تُعرض بيانات الموجود والعمليات المتاحة فقط.",
        PERFORMANCE_MAX_TRAVEL: "هذا مسار متخصص يحتاج ربط الفنادق وموادها وتغذيتها؛ لم يكتمل تنفيذه في ميزان.",
        HOTEL: "يتطلب حسابًا مؤهّلًا وربط Hotel Center وتغذية فنادق وأسعار صحيحة.",
    }[googleCapabilityType(capability || {})];
    return capability?.reason_ar || capability?.reason || (capability?.provider_limitation ? limitation || capability.provider_limitation : null) || capability?.creation_reason || capability?.message
        || (gaps.length ? gaps.map((item) => typeof item === "string" ? item : item.message || item.label).filter(Boolean).join(" · ") : "هذا المسار غير جاهز للإنشاء على الحساب المختار.");
}

const data = (response) => response.data;
export function normalizeGoogleAdsPayload(value = {}) {
    const normalizeAccount = (account) => ({ ...account, account_id: String(account.account_id || account.id || account.external_account_id || ""), account_name: account.account_name || account.name || "حساب جوجل", currency: account.currency || account.currency_code, timezone: account.timezone || account.time_zone });
    const normalizeRow = (row) => ({ ...row, currency: row.currency || row.currency_code, timezone: row.timezone || row.time_zone, ctr_pct: row.ctr_pct ?? row.metrics?.ctr_pct ?? (row.metrics?.ctr != null ? Number(row.metrics.ctr) * 100 : undefined) });
    return { ...value,
        ...(value.accounts ? { accounts: value.accounts.map(normalizeAccount) } : {}),
        ...(value.items ? { items: value.items.map(normalizeRow) } : {}),
        ...(value.campaigns ? { campaigns: value.campaigns.map(normalizeRow) } : {}),
        ...(value.totals ? { totals: { ...value.totals, conversion_value: value.totals.conversion_value ?? value.totals.conversion_value_native, currency: value.totals.currency || value.totals.currency_code || value.currency || value.currency_code } } : {}),
        currency: value.currency || value.currency_code,
    };
}
const encoded = (id) => encodeURIComponent(String(id));
const options = (signal, params) => ({ timeout: 30000, ...(signal ? { signal } : {}), ...(params ? { params } : {}) });

export const getGoogleAdsWorkspace = ({ dateFrom, dateTo, accountId, entityType = "campaigns", campaignId, groupId, page = 1, query = "", signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/workspace`, options(signal, {
    from_date: dateFrom, to_date: dateTo, account_id: accountId || undefined,
    entity_type: entityType, campaign_id: campaignId || undefined, group_id: groupId || undefined,
    page, limit: 25, campaign_query: query || undefined,
})).then(data).then(normalizeGoogleAdsPayload);
export const recoverGoogleAdsReporting = ({ accountId, requestId, signal } = {}) => api.post(`${GOOGLE_ADS_ROOT}/reporting-recovery`, {
    account_id: String(accountId || ""), request_id: requestId,
}, { timeout: 100000, ...(signal ? { signal } : {}) }).then(data);
export const getGoogleAdsReadiness = ({ accountId, signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/management/readiness`, options(signal, { account_id: accountId || undefined })).then(data).then(normalizeGoogleAdsPayload);
export const getGoogleAdsSchema = ({ signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/management/schema`, options(signal)).then(data);
export const getGoogleAssets = ({ accountId, signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/assets`, options(signal, { account_id: accountId })).then(data);
export const lookupGoogleGeo = ({ accountId, query, signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/lookup/geo`, options(signal, { account_id: accountId, query })).then(data);
export const lookupGoogleLanguages = ({ accountId, signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/lookup/languages`, options(signal, { account_id: accountId })).then(data);
export const lookupGoogleOptions = ({ accountId, kind, query, merchantId, signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/lookup/${encoded(kind)}`, options(signal, { account_id: accountId, query, merchant_id: merchantId || undefined })).then(data);
export const getGoogleDrafts = ({ accountId, signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/management/drafts`, options(signal, { account_id: accountId, limit: 25 })).then(data);
export const getGoogleDraft = (id, { signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/management/drafts/${encoded(id)}`, options(signal)).then(data);
export const createGoogleDraft = (draft, idempotencyKey, { signal } = {}) => api.post(`${GOOGLE_ADS_ROOT}/management/drafts`, { draft, idempotency_key: idempotencyKey }, options(signal)).then(data);
export const updateGoogleDraft = (id, draft, expectedRevision, { signal } = {}) => api.put(`${GOOGLE_ADS_ROOT}/management/drafts/${encoded(id)}`, { draft, expected_revision: expectedRevision }, options(signal)).then(data);
export const createGoogleProposal = (payload, { signal } = {}) => api.post(`${GOOGLE_ADS_ROOT}/management/proposals`, payload, options(signal)).then(data);
export const previewGoogleRecommendation = (analysisId, recommendationId, { signal } = {}) => api.post(`${GOOGLE_ADS_ROOT}/ai/recommendations/preview`, { analysis_id: analysisId, recommendation_id: recommendationId }, options(signal)).then(data);
export const getGoogleProposals = ({ accountId, campaignId, signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/management/proposals`, options(signal, { account_id: accountId || undefined, campaign_id: campaignId || undefined, limit: 25 })).then(data);
export const getGoogleProposal = (id, { signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/management/proposals/${encoded(id)}`, options(signal)).then(data);
export const executeGoogleProposal = (id, digest, { signal } = {}) => api.post(`${GOOGLE_ADS_ROOT}/management/proposals/${encoded(id)}/approve-and-execute`, { confirmation_digest: digest }, { ...options(signal), timeout: 60000 }).then(data);
export const reconcileGoogleProposal = (id, { signal } = {}) => api.post(`${GOOGLE_ADS_ROOT}/management/proposals/${encoded(id)}/reconcile`, {}, options(signal)).then(data);
export const generateGoogleAiDraft = (payload, { signal } = {}) => api.post(`${GOOGLE_ADS_ROOT}/ai/draft`, payload, { ...options(signal), timeout: 90000 }).then(data);
export const analyzeGoogleCampaign = (payload, { signal } = {}) => api.post(`${GOOGLE_ADS_ROOT}/ai/analyze`, payload, { ...options(signal), timeout: 90000 }).then(data);
export const getGoogleAnalyses = ({ accountId, dateFrom, dateTo, signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/ai/analyses`, options(signal, { account_id: accountId || undefined, date_from: dateFrom || undefined, date_to: dateTo || undefined })).then(data);
export const getGoogleProducts = ({ query = "", limit = 50, signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/products`, options(signal, { query: String(query).trim().slice(0, 100), limit: Math.min(50, Math.max(1, Math.trunc(Number(limit)) || 50)) })).then(data);
export const getGoogleManagementSubjects = ({ accountId, signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/management/subjects`, options(signal, { account_id: accountId })).then(data);
export const getGoogleManagementPolicy = (accountId, subjectId, { signal } = {}) => api.get(`${GOOGLE_ADS_ROOT}/management/policies/${encoded(accountId)}/${encoded(subjectId)}`, options(signal)).then(data);
export const updateGoogleManagementPolicy = (accountId, subjectId, policy, { signal } = {}) => api.put(`${GOOGLE_ADS_ROOT}/management/policies/${encoded(accountId)}/${encoded(subjectId)}`, policy, options(signal)).then(data);
export function uploadGoogleImage({ accountId, file, name, assetType = "IMAGE", idempotencyKey, signal }) {
    const form = new FormData();
    form.append("account_id", accountId); form.append("file", file); form.append("name", name || file.name);
    form.append("asset_type", assetType);
    if (idempotencyKey) form.append("idempotency_key", idempotencyKey);
    return api.post(`${GOOGLE_ADS_ROOT}/assets`, form, options(signal)).then(data);
}
export const addGoogleVideo = ({ accountId, youtubeVideoId, name, idempotencyKey, signal }) => api.post(`${GOOGLE_ADS_ROOT}/assets/video`, {
    account_id: accountId, youtube_video_id: youtubeVideoId, name, idempotency_key: idempotencyKey,
}, options(signal)).then(data);
