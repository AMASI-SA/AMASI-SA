import { useEffect, useMemo, useRef, useState } from "react";
import GoogleAnalysisAttempts from "./GoogleAnalysisAttempts";
import GoogleAutomationPolicy from "./GoogleAutomationPolicy";
import GoogleCreationPolicy from "./GoogleCreationPolicy";
import GoogleCommercialPlan from "./GoogleCommercialPlan";
import GoogleAdsIntelligenceCard from "./GoogleAdsIntelligenceCard";
import GoogleRecommendationAction from "./GoogleRecommendationAction";
import { googleEvidenceDisplay } from "../../services/googleAdsEvidence";
import GoogleCampaignComposer, { googleDefaultDraft, googleDefaultGroup } from "./GoogleCampaignComposer";
import {
    GOOGLE_CAMPAIGN_LABELS, GOOGLE_STATUS_LABELS, analyzeGoogleCampaign,
    createLatestGoogleResponseGuard, getGoogleAdsReadiness, getGoogleAdsWorkspace,
    getGoogleDrafts, getGoogleManagementPolicy, getGoogleManagementSubjects,
    getGoogleProposal, getGoogleProposals, googleCampaignCapabilities,
    googleCapabilityCanCreate, googleCapabilityReason, googleCapabilityType,
    googleErrorMessage, googleConfidenceLabel, isGoogleRequestCancelled, updateGoogleManagementPolicy,
} from "../../services/googleAds";

const BUTTON = "rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-bold disabled:opacity-40";
const PRIMARY = "rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-bold text-white disabled:opacity-40";
const INPUT = "w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm disabled:bg-slate-100";
const LEVELS = [["campaigns", "الحملات"], ["groups", "المجموعات"], ["ads", "الإعلانات"], ["assets", "مواد الإعلان"]];
const PREREQUISITE_LABELS = {
    google_ads_link: "ربط حساب جوجل", eligible_conversion_actions_for_smart_bidding: "تحويلات مؤهلة للمزايدة الذكية",
    dsa_website_language_if_selected: "نطاق الموقع ولغته للبحث الديناميكي", approved_image_assets: "صور إعلان صالحة",
    html5_account_eligibility_if_selected: "أهلية الحساب لإعلانات HTML5", linked_merchant_center: "ربط Merchant Center",
    approved_merchant_products: "منتجات معتمدة في Merchant Center", complete_asset_group: "مواد المجموعة المطلوبة",
    brand_name_and_logo: "اسم النشاط والشعار", linked_merchant_center_if_retail: "ربط Merchant Center لحملات المنتجات",
    format_specific_image_video_assets: "صور أو فيديو حسب صيغة الإعلان", linked_merchant_center_if_feed: "ربط منتجات الحملة",
    valid_store_app_id: "تطبيق صالح في المتجر", conversion_tracking: "تتبّع تحويلات التطبيق",
    engagement_user_list_if_selected: "جمهور تفاعل التطبيق", allowlisted_account: "حساب مؤهل لإعلانات الفنادق",
    linked_hotel_center: "ربط Hotel Center", hotel_feed_and_pricing: "تغذية الفنادق والأسعار",
};
const POLICY_ACTIONS = [
    ["create_campaign", "إنشاء حملات"], ["rename", "تعديل الأسماء"], ["pause", "الإيقاف"],
    ["enable", "التفعيل"], ["set_budget", "تعديل الميزانية"], ["set_bidding", "تعديل المزايدة"],
    ["set_targeting", "تعديل الاستهداف"], ["update_ad", "تعديل الإعلانات"],
    ["add_group", "إضافة مجموعات"], ["add_ad", "إضافة إعلانات"], ["rename_group", "تعديل أسماء المجموعات"],
    ["pause_group", "إيقاف المجموعات"], ["enable_group", "تفعيل المجموعات"], ["set_group_bidding", "مزايدة المجموعة"],
    ["set_keywords", "الكلمات المفتاحية"], ["set_channels", "قنوات Demand Gen"], ["set_conversion_goals", "أهداف التحويل"],
    ["pause_ad", "إيقاف الإعلان"], ["enable_ad", "تفعيل الإعلان"], ["update_group_assets", "مواد مجموعات Performance Max"],
    ["duplicate", "نسخ الحملات"], ["remove", "حذف الحملات"], ["remove_group", "حذف المجموعات"], ["remove_ad", "حذف الإعلانات"],
    ["upload_image", "إضافة صور الإعلان"], ["upload_html5", "إضافة إعلانات HTML5 الجاهزة"], ["attach_video", "إضافة فيديو YouTube"],
];

function number(value, digits = 0) {
    if (value === undefined || value === null || value === "" || !Number.isFinite(Number(value))) return "—";
    return Number(value).toLocaleString("en-US", { maximumFractionDigits: digits });
}
function money(value, currency) { return value === undefined || value === null || value === "" ? "—" : `${number(value, 2)} ${currency || ""}`.trim(); }
function accountIdOf(account) { return String(account.account_id || account.id || account.external_account_id || ""); }
function accountNameOf(account) { return account.account_name || account.name || "حساب جوجل"; }
function rowName(row) { return row.name || row.entity_name || row.campaign_name || row.group_name || row.ad_name || "عنصر جوجل"; }
function rowId(row, kind) {
    return String(row.entity_id || row.id || (kind === "campaigns" ? row.campaign_id : kind === "groups" ? row.group_id || row.ad_group_id || row.asset_group_id : kind === "ads" ? row.ad_id : row.asset_id) || row.resource_name || "");
}
function rowAccountId(row) { return String(row.account_id || row.customer_id || ""); }
function rowType(row) { return row.campaign_type || row.channel_type || row.advertising_channel_type; }
function rowMetric(row, key) {
    if (key === "spend") return row.spend_native ?? row.cost_native ?? row.metrics?.spend_native ?? row.metrics?.cost_native ?? row.metrics?.cost ?? row.delivery?.spend?.amount;
    return row[key] ?? row.metrics?.[key] ?? row.delivery?.[key] ?? row.platform_outcomes?.[key];
}
function prerequisiteName(item) { return typeof item === "string" ? PREREQUISITE_LABELS[item] || "متطلب حساب إضافي" : item.label || item.message || PREREQUISITE_LABELS[item.code] || "متطلب حساب إضافي"; }
function creationGaps(capability) { return capability.missing_prerequisites || capability.prerequisites_missing || []; }
export function googleTypedCampaignContext(value = {}) {
    if (!value || typeof value !== "object") return {};
    const output = {};
    if (value.shopping && typeof value.shopping === "object") output.shopping = { ...value.shopping };
    if (value.hotel && typeof value.hotel === "object") output.hotel = { ...value.hotel };
    if (value.app && typeof value.app === "object") output.app = { ...value.app, subtype: value.app.subtype === "INSTALL" ? "INSTALLS" : value.app.subtype };
    return output;
}
export function googleCreativeEditAllowed(row = {}, action) {
    return !["update_ad", "update_group_assets"].includes(action) || row.editable !== false && row.creative_supported !== false;
}
function googleFieldGapLabel(value) {
    const field = typeof value === "string" ? value : value?.field || value?.path || value?.code || "";
    if (/headline/i.test(field)) return "عناوين الإعلان";
    if (/description/i.test(field)) return "أوصاف الإعلان";
    if (/asset|image|video|logo|mediaBundle/i.test(field)) return "مواد الإعلان";
    if (/app|engagement/i.test(field)) return "نوع التطبيق ووجهة التفاعل";
    if (/url|destination/i.test(field)) return "روابط الوصول";
    if (/audience|userList|signal/i.test(field)) return "الجمهور وإشارات الاستهداف";
    if (/shopping|merchant|product/i.test(field)) return "ربط منتجات الحملة";
    if (/hotel/i.test(field)) return "ربط الفنادق";
    return "إعداد أو صيغة لا يمكن استعادتها بالكامل";
}
function googleCreativeGaps(row = {}) {
    const entries = [...(row.missing_fields || []), ...(row.unsupported_fields || []), ...(row.field_gaps || [])];
    return [...new Set(entries.map(googleFieldGapLabel))];
}
function editableGroup(row, type) {
    const source = row.group || row.creative || row.ad || {};
    const fields = Object.keys(googleDefaultGroup(type)).concat(["business_name", "media_bundle_asset", "cpc_bid_native", "percent_cpc_bid", "disable_ai_max_search_term_matching"]);
    return { ...googleDefaultGroup(type), ...Object.fromEntries(fields.filter((key) => source[key] !== undefined).map((key) => [key, source[key]])), name: source.name || rowName(row) };
}

export function GoogleCapabilityMatrix({ readiness = {}, accountId }) {
    const rows = googleCampaignCapabilities(readiness, accountId);
    return <details className="rounded-2xl border border-slate-200 bg-white p-4" data-testid="google-capability-matrix"><summary className="cursor-pointer font-black">قدرات الأنواع ومتطلبات الحساب</summary><p className="mt-2 text-xs leading-6 text-slate-500">تختلف جاهزية التنفيذ عن دعم جوجل وأهلية الحساب. تظهر القيود والمتطلبات لكل نوع.</p><div className="mt-3 overflow-x-auto"><table className="w-full min-w-[720px] text-right text-sm"><thead className="bg-slate-50"><tr>{["النوع", "إنشاء عبر جوجل", "تنفيذ ميزان", "أهلية الحساب", "المتطلبات والقيود"].map((label) => <th key={label} className="p-3">{label}</th>)}</tr></thead><tbody>{rows.map((row) => { const type = googleCapabilityType(row); const gaps = creationGaps(row); const supported = row.api_creation_supported ?? (row.create === true || row.creation_supported === true); return <tr key={type} className="border-t border-slate-100"><th className="p-3 font-bold">{GOOGLE_CAMPAIGN_LABELS[type] || row.label || "مسار متخصص"}</th><td className="p-3">{supported ? "متاح" : "مقيّد"}</td><td className="p-3">{row.live_verified === true ? "مُثبت" : row.implemented === "not_implemented" ? "لم يكتمل" : row.implemented === "creation_unavailable" ? "الإنشاء مقيّد" : "قابل للاختبار"}</td><td className="p-3">{row.account_eligible === true || row.eligible === true ? "مؤهّل" : gaps.length || row.account_eligible === false || row.eligible === false ? "متطلبات ناقصة" : "تُفحص عند المعاينة"}</td><td className="max-w-lg p-3 text-xs leading-6 text-slate-600">{row.provider_limitation && <p>{row.reason_ar || googleCapabilityReason(row)}</p>}{gaps.map((gap, index) => <p key={index} className="text-amber-900">{prerequisiteName(gap)}</p>)}{!gaps.length && (row.prerequisites || []).map(prerequisiteName).join(" · ")}{!googleCapabilityCanCreate(row) && !row.provider_limitation && !gaps.length && <p>{googleCapabilityReason(row)}</p>}</td></tr>; })}{!rows.length && <tr><td colSpan={5} className="p-5 text-center text-slate-500">جارٍ تحميل خريطة قدرات الحساب.</td></tr>}</tbody></table></div></details>;
}

function GoogleDelegationPanel({ account, onUpdated }) {
    const accountId = accountIdOf(account);
    const [subjects, setSubjects] = useState([]);
    const [subjectId, setSubjectId] = useState("");
    const [policy, setPolicy] = useState({ enabled: false, allowed_actions: [], max_daily_budget_native: "", max_total_budget_native: "", expires_at: "" });
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const inflight = useRef(false), mounted = useRef(true), controller = useRef(null);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, []);
    useEffect(() => {
        let active = true; const readController = new AbortController();
        getGoogleManagementSubjects({ accountId, signal: readController.signal }).then((response) => { if (active) setSubjects(response.items || []); }).catch((failure) => { if (active && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure, "تعذر تحميل أعضاء فريقك.")); });
        return () => { active = false; readController.abort(); };
    }, [accountId]);
    useEffect(() => {
        if (!subjectId) return undefined;
        let active = true; const readController = new AbortController();
        setBusy(true); setError(""); setNotice("");
        getGoogleManagementPolicy(accountId, subjectId, { signal: readController.signal }).then((response) => { if (active) setPolicy({ enabled: false, allowed_actions: [], max_daily_budget_native: "", max_total_budget_native: "", expires_at: "", ...(response.policy || response) }); }).catch((failure) => { if (active && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure)); }).finally(() => { if (active) setBusy(false); });
        return () => { active = false; readController.abort(); };
    }, [accountId, subjectId]);
    function edit(patch) { setPolicy((current) => ({ ...current, ...patch })); setNotice(""); }
    async function save(event) {
        event.preventDefault(); if (inflight.current || !subjectId) return;
        const payload = { enabled: policy.enabled, allowed_actions: policy.allowed_actions, max_daily_budget_native: String(policy.max_daily_budget_native || "0"), max_total_budget_native: String(policy.max_total_budget_native || "0") };
        if (policy.expires_at) payload.expires_at = new Date(policy.expires_at).toISOString();
        inflight.current = true; setBusy(true); setError(""); controller.current = new AbortController();
        try { await updateGoogleManagementPolicy(accountId, subjectId, payload, { signal: controller.current.signal }); if (mounted.current) { setNotice("حُفظ تفويض هذا الموظف على الحساب المختار."); onUpdated?.(); } }
        catch (failure) { if (mounted.current && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure)); }
        finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    return <details className="rounded-2xl border border-amber-200 bg-white p-4"><summary className="cursor-pointer font-black">تفويض فريق التسويق · {accountNameOf(account)}</summary><p className="mt-2 text-xs leading-6 text-slate-600">حدّد الموظف والعمليات وحدود الميزانية. صلاحية التسويق وحدها تتيح تجهيز المسودات، والتنفيذ يتبع تفويضك للحساب.</p>{error && <p role="alert" className="mt-3 rounded-xl bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}{notice && <p role="status" className="mt-3 text-sm text-emerald-800">{notice}</p>}<form onSubmit={save} className="mt-4 grid gap-4 md:grid-cols-2"><label className="space-y-2 text-sm font-bold">الموظف<select aria-label="موظف تفويض جوجل" className={INPUT} disabled={busy} value={subjectId} onChange={(event) => setSubjectId(event.target.value)}><option value="">اختر من فريقك</option>{subjects.map((subject) => <option key={subject.subject_id} value={subject.subject_id}>{subject.name || subject.email || "موظف"}</option>)}</select></label>{subjectId && <><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={policy.enabled === true} disabled={busy} onChange={(event) => edit({ enabled: event.target.checked })} />تفعيل تفويض التنفيذ</label><label className="space-y-2 text-sm font-bold">أقصى متوسط ميزانية يومية · {account.currency}<input aria-label="سقف التفويض اليومي لجوجل" className={INPUT} type="number" min="0" step="0.01" disabled={busy} value={policy.max_daily_budget_native} onChange={(event) => edit({ max_daily_budget_native: event.target.value })} /></label><label className="space-y-2 text-sm font-bold">أقصى ميزانية إجمالية · {account.currency}<input aria-label="سقف التفويض الإجمالي لجوجل" className={INPUT} type="number" min="0" step="0.01" disabled={busy} value={policy.max_total_budget_native} onChange={(event) => edit({ max_total_budget_native: event.target.value })} /></label><label className="space-y-2 text-sm font-bold">انتهاء التفويض (اختياري)<input aria-label="انتهاء تفويض جوجل" className={INPUT} type="datetime-local" disabled={busy} value={policy.expires_at ? policy.expires_at.slice(0, 16) : ""} onChange={(event) => edit({ expires_at: event.target.value })} /></label><fieldset className="grid gap-3 rounded-xl border p-3 sm:grid-cols-2 md:col-span-2"><legend className="px-2 text-sm font-bold">العمليات المسموح بها</legend>{POLICY_ACTIONS.map(([action, label]) => <label key={action} className="flex items-center gap-2 text-xs"><input type="checkbox" disabled={busy} checked={policy.allowed_actions.includes(action)} onChange={(event) => edit({ allowed_actions: event.target.checked ? [...policy.allowed_actions, action] : policy.allowed_actions.filter((entry) => entry !== action) })} />{label}</label>)}</fieldset><p className="text-xs text-amber-900 md:col-span-2">السقف اليومي يقيّد متوسط الميزانية المعتمد، ويخضع الصرف الفعلي لقواعد جوجل اليومية والشهرية.</p><button type="submit" disabled={busy} className={PRIMARY}>حفظ تفويض جوجل</button></>}</form></details>;
}

function GoogleAnalysisPanel({ selection, dateFrom, dateTo, onClose, onDraft, onRecommendation, onPreview }) {
    const [result, setResult] = useState(null), [busy, setBusy] = useState(true), [error, setError] = useState("");
    useEffect(() => {
        let active = true; const controller = new AbortController(); setBusy(true); setError(""); setResult(null);
        analyzeGoogleCampaign({ account_id: selection.accountId, campaign_id: selection.campaignId, from_date: dateFrom, to_date: dateTo }, { signal: controller.signal }).then((response) => { if (active) setResult(response); }).catch((failure) => { if (active && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure, "تعذر تحليل هذه الفترة. حاول مجددًا.")); }).finally(() => { if (active) setBusy(false); });
        return () => { active = false; controller.abort(); };
    }, [selection.accountId, selection.campaignId, dateFrom, dateTo]);
    const recommendations = result?.recommendations || result?.insights || [];
    return <section className="space-y-3 rounded-2xl border border-violet-200 bg-white p-5" aria-label="تحليل جوجل بذكاء ميزان">
        <header className="flex items-center justify-between gap-2"><h3 className="font-black">ذكاء ميزان · {selection.name || "الحساب"}</h3><button type="button" className={BUTTON} onClick={onClose}>إغلاق التحليل</button></header>
        {busy && <p role="status" className="text-sm text-slate-500">جارٍ تحليل أداء جوجل وربطه بنتائج المتجر المتاحة…</p>}
        {error && <p role="alert" className="rounded-xl bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
        {!busy && <GoogleAnalysisAttempts accountId={selection.accountId} campaignId={selection.campaignId} dateFrom={dateFrom} dateTo={dateTo} refreshKey={result?.analysis_id || error} />}
        {result && <><p className="text-sm leading-7">{result.summary || result.analysis?.summary || "اكتمل التحليل للبيانات المتاحة في هذه الفترة."}</p>
            {(result.limitations || result.data_gaps || []).map((item, index) => <p key={index} className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{typeof item === "string" ? item : item.message || item.detail}</p>)}
            {recommendations.map((item, index) => <article key={index} className="space-y-2 rounded-xl border border-violet-100 bg-violet-50 p-4"><h4 className="font-bold">{item.title || item.name || "توصية"}</h4><p className="text-sm leading-6">{item.rationale || item.explanation || item.detail || item.reason || (typeof item === "string" ? item : "")}</p>{item.confidence != null && <p className="text-xs text-slate-500">درجة الثقة: {googleConfidenceLabel(item.confidence)}</p>}{(item.evidence || []).map((evidence, evidenceIndex) => { const display = googleEvidenceDisplay(evidence); return <p key={evidenceIndex} className="text-xs text-slate-600">{evidence.label || evidence.source || "دليل أداء"} · {display.label}: {display.value}</p>; })}{item.observation_plan && <p className="text-xs text-slate-600">{typeof item.observation_plan === "string" ? item.observation_plan : item.observation_plan.summary || "تُقاس النتائج بعد تنفيذ التغيير وتوفر فترة كافية."}</p>}<GoogleRecommendationAction analysisId={result.analysis_id} recommendation={item} onPreview={onPreview} />{item.draft && <button type="button" className={BUTTON} onClick={() => onDraft(item.draft)}>فتح التوصية كمسودة</button>}{item.campaign_id && ["set_budget", "pause", "enable", "set_bidding", "set_targeting"].includes(item.action) && <button type="button" className={BUTTON} onClick={() => onRecommendation(item)}>مراجعة التحسين المقترح</button>}</article>)}
            {result.draft && <button type="button" className={PRIMARY} onClick={() => onDraft(result.draft)}>فتح مسودة الذكاء للتحرير</button>}
            <p className="text-xs leading-6 text-slate-500">التحويلات من جوجل تختلف عن الطلبات المثبتة في سلة. توضح فجوات التكلفة والإسناد حدود الاستنتاجات؛ تنفيذ التوصية يحتاج معاينة واعتمادًا مستقلين.</p>
        </>}
    </section>;
}

export default function GoogleAdsWorkspace({ dateFrom, dateTo, query = "", onSynced, onAccountChange, selectedAccountId, initialSelection, initialAccountId, initialView = "campaigns" }) {
    const [context, setContext] = useState({ accountId: selectedAccountId !== undefined ? selectedAccountId || "" : initialAccountId || initialSelection?.accountId || "", kind: "campaigns", page: 1, parent: {} });
    const [accounts, setAccounts] = useState([]);
    const [report, setReport] = useState(null);
    const [readiness, setReadiness] = useState({});
    const [loading, setLoading] = useState(true), [loadingReadiness, setLoadingReadiness] = useState(true);
    const [error, setError] = useState(""), [notice, setNotice] = useState("");
    const [revision, setRevision] = useState(0);
    const [composer, setComposer] = useState(initialSelection?.mode ? { ...initialSelection, key: 1 } : null), [analysis, setAnalysis] = useState(null);
    const [drafts, setDrafts] = useState(null), [history, setHistory] = useState(null);
    const [listLoading, setListLoading] = useState(false);
    const readGuard = useRef(createLatestGoogleResponseGuard());
    const mounted = useRef(true), listController = useRef(null), listBusy = useRef(false), composeSequence = useRef(initialSelection?.mode ? 1 : 0);
    const completedIds = useRef(new Set()), syncedCallback = useRef(onSynced);
    const accountChangedCallback = useRef(onAccountChange);
    const editorRef = useRef(null);
    const { accountId, kind, page, parent } = context;
    useEffect(() => { syncedCallback.current = onSynced; }, [onSynced]);
    useEffect(() => { accountChangedCallback.current = onAccountChange; }, [onAccountChange]);
    useEffect(() => {
        if (selectedAccountId !== undefined && String(selectedAccountId || "") !== context.accountId) {
            changeContext({ accountId: String(selectedAccountId || ""), kind: "campaigns", parent: {} }, false);
        }
    }, [selectedAccountId]);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; readGuard.current.invalidate(); listController.current?.abort(); }; }, []);

    useEffect(() => {
        let active = true; const controller = new AbortController(); const token = readGuard.current.begin();
        setLoading(true); setReport(null); setError("");
        getGoogleAdsWorkspace({ dateFrom, dateTo, accountId, entityType: kind, campaignId: parent.campaignId, groupId: parent.groupId, page, query, signal: controller.signal }).then((response) => {
            if (!active || !readGuard.current.current(token)) return;
            setReport(response); if (Array.isArray(response.accounts)) setAccounts(response.accounts);
            if (!accountId && response.account_id) {
                setContext((current) => current.accountId ? current : { ...current, accountId: String(response.account_id), page: 1 });
                accountChangedCallback.current?.(String(response.account_id));
            }
        }).catch((failure) => { if (active && readGuard.current.current(token) && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure, "تعذر تحميل حملات جوجل. راجع الربط ثم حدّث البيانات.")); }).finally(() => { if (active && readGuard.current.current(token)) setLoading(false); });
        return () => { active = false; controller.abort(); };
    }, [dateFrom, dateTo, query, accountId, kind, page, parent, revision]);

    useEffect(() => {
        let active = true; const controller = new AbortController(); setLoadingReadiness(true); setReadiness({});
        getGoogleAdsReadiness({ accountId, signal: controller.signal }).then((response) => {
            if (!active) return;
            setReadiness(response); if (response.accounts?.length) setAccounts(response.accounts);
            const readable = (response.accounts || []).filter((item) => item.readable === true);
            if (!accountId && readable.length === 1) {
                const next = accountIdOf(readable[0]);
                setContext((current) => current.accountId ? current : { ...current, accountId: next, kind: "campaigns", parent: {}, page: 1 });
                accountChangedCallback.current?.(next);
            }
        }).catch((failure) => { if (active && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure, "تعذر التحقق من قدرات إدارة الحساب.")); }).finally(() => { if (active) setLoadingReadiness(false); });
        return () => { active = false; controller.abort(); };
    }, [accountId, revision]);

    useEffect(() => { setContext((current) => current.page === 1 ? current : { ...current, page: 1 }); setAnalysis(null); }, [dateFrom, dateTo, query]);
    useEffect(() => { if (composer) editorRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" }); }, [composer]);

    const account = accounts.find((item) => accountIdOf(item) === accountId);
    const capabilities = googleCampaignCapabilities(readiness, accountId);
    const permissions = readiness.permissions || report?.permissions || {};
    const allowedActions = permissions.allowed_actions;
    const creationAllowed = !loadingReadiness && readiness.management_enabled !== false && permissions.can_draft !== false && capabilities.some(googleCapabilityCanCreate);
    const items = report?.items || report?.entities || [];
    const pagination = report?.pagination || report?.campaign_pagination || {};
    const pages = Math.max(1, Number(pagination.pages || Math.ceil((pagination.total || 0) / 25)) || 1);
    const accountCurrency = account?.currency || report?.currency;
    const totals = report?.totals || {};
    const unresolvedPrerequisites = useMemo(() => account?.missing_prerequisites || readiness.missing_prerequisites || [], [account, readiness]);
    useEffect(() => {
        if (initialView === "ai" && accountId) { setComposer(null); setAnalysis({ accountId, name: accountNameOf(account || {}) }); }
    }, [initialView, accountId, account?.account_name]);

    function changeContext(patch, notifyAccount = true) {
        listController.current?.abort(); setDrafts(null); setHistory(null); setComposer(null); setAnalysis(null); setNotice(""); setError("");
        if (notifyAccount && Object.hasOwn(patch, "accountId") && patch.accountId !== accountId) accountChangedCallback.current?.(patch.accountId || null);
        setContext((current) => ({ ...current, page: 1, ...patch }));
    }
    function openComposer(selection) {
        if (selection.accountId && selection.accountId !== accountId) setContext((current) => ({ ...current, accountId: selection.accountId, page: 1 }));
        setAnalysis(null); setComposer({ ...selection, key: ++composeSequence.current });
    }
    function openRecommendationProposal(proposal) {
        if (String(proposal.account_id || "") !== accountId) {
            setError("حساب التوصية تغير؛ حدّث تحليلات الحساب المختار."); return;
        }
        openComposer({ mode: "edit", action: proposal.action, accountId,
            accountName: proposal.account_name || accountNameOf(account || {}), currency: proposal.currency,
            campaignId: proposal.campaign_id, campaignType: proposal.before?.campaign_type,
            name: proposal.before?.name, reason: proposal.reason, proposal });
    }
    function selectionFor(row, mode = "edit", action = "rename") {
        const rowAccount = accounts.find((item) => accountIdOf(item) === rowAccountId(row));
        const type = rowType(row) || parent.campaignType || "SEARCH";
        const campaignContext = googleTypedCampaignContext(row.campaign_context || parent.campaignContext);
        return { mode, action, accountId: rowAccountId(row) || accountId, accountName: row.account_name || (rowAccount ? accountNameOf(rowAccount) : account ? accountNameOf(account) : "حساب جوجل"), currency: row.currency || rowAccount?.currency || accountCurrency,
            campaignId: kind === "campaigns" ? rowId(row, kind) : String(row.campaign_id || parent.campaignId || ""), campaignType: type, budgetMode: row.budget_mode,
            groupId: kind === "groups" ? rowId(row, kind) : row.group_id || row.ad_group_id || parent.groupId,
            adId: kind === "ads" ? rowId(row, kind) : undefined, adResource: kind === "ads" ? row.resource_name || row.ad_group_ad_resource : undefined,
            name: rowName(row), campaignContext, initialDraft: ["ads", "groups"].includes(kind) ? { ...googleDefaultDraft(rowAccountId(row) || accountId, type), ...campaignContext, name: rowName(row), groups: [editableGroup(row, type)] } : undefined };
    }
    function canManage(row, action) {
        const capability = capabilities.find((item) => googleCapabilityType(item) === (rowType(row) || parent.campaignType));
        return !loadingReadiness && readiness.management_enabled !== false && permissions.can_manage !== false && permissions.can_draft !== false && capability?.manage !== false
            && googleCreativeEditAllowed(row, action)
            && (!capability?.actions || capability.actions.includes(action)) && (!Array.isArray(allowedActions) || allowedActions.includes(action));
    }
    function create() { if (!account) return; const firstType = capabilities.find(googleCapabilityCanCreate); openComposer({ mode: "create", accountId, accountName: accountNameOf(account), currency: account.currency, campaignType: firstType ? googleCapabilityType(firstType) : "SEARCH" }); }
    function completed(proposal) {
        if (completedIds.current.has(proposal.proposal_id)) return;
        completedIds.current.add(proposal.proposal_id); setNotice("اكتملت العملية وتأكدت النتيجة من جوجل.");
        setRevision((value) => value + 1); syncedCallback.current?.(proposal);
    }
    async function readList(kindToLoad) {
        if (!accountId || listBusy.current) return;
        listBusy.current = true; setListLoading(true); setError(""); listController.current = new AbortController();
        try {
            const response = await (kindToLoad === "drafts" ? getGoogleDrafts({ accountId, signal: listController.current.signal }) : getGoogleProposals({ accountId, campaignId: parent.campaignId, signal: listController.current.signal }));
            if (mounted.current && !listController.current.signal.aborted) { if (kindToLoad === "drafts") { setDrafts(response.items || []); setHistory(null); } else { setHistory(response.items || []); setDrafts(null); } }
        } catch (failure) { if (mounted.current && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure)); }
        finally { listBusy.current = false; if (mounted.current) setListLoading(false); }
    }
    async function openHistory(proposal) {
        if (listBusy.current) return;
        listBusy.current = true; setListLoading(true); listController.current = new AbortController();
        try {
            const result = await getGoogleProposal(proposal.proposal_id, { signal: listController.current.signal });
            if (mounted.current && !listController.current.signal.aborted) openComposer({ mode: "edit", accountId: String(result.account_id || accountId), accountName: result.account_name || accountNameOf(account || {}), currency: result.currency || accountCurrency, name: result.campaign_name || result.planned?.name, campaignId: result.campaign_id, proposal: result });
        } catch (failure) { if (mounted.current && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure)); }
        finally { listBusy.current = false; if (mounted.current) setListLoading(false); }
    }
    function openChildren(row) {
        const type = rowType(row) || parent.campaignType;
        const campaignContext = googleTypedCampaignContext(row.campaign_context || parent.campaignContext);
        if (kind === "campaigns") changeContext({ accountId: rowAccountId(row) || accountId, kind: "groups", parent: { campaignId: rowId(row, kind), campaignName: rowName(row), campaignType: type, campaignContext } });
        else changeContext({ accountId: rowAccountId(row) || accountId, kind: type === "PERFORMANCE_MAX" || row.entity_type === "asset_group" ? "assets" : "ads", parent: { ...parent, campaignId: String(row.campaign_id || parent.campaignId), campaignName: row.campaign_name || parent.campaignName, campaignType: type, campaignContext, groupId: rowId(row, kind), groupName: rowName(row) } });
    }
    return <section className="space-y-4" dir="rtl" data-testid="google-ads-workspace">
        <header className="flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4"><div><h2 className="font-black">إدارة إعلانات جوجل</h2><p className="mt-1 text-xs leading-6 text-slate-500">الحساب والحملات والمجموعات والإعلانات ومواد الإعلان، مع مسودات المسوّق وذكاء ميزان.</p></div><button type="button" className={BUTTON} disabled={loading} onClick={() => setRevision((value) => value + 1)}>تحديث بيانات جوجل</button></header>
        {(error || notice) && <p role={error ? "alert" : "status"} className={`rounded-xl p-3 text-sm font-bold ${error ? "bg-rose-50 text-rose-800" : "bg-emerald-50 text-emerald-900"}`}>{error || notice}</p>}
        {!accountId && accounts.length > 0 && !loadingReadiness && <p role="status" className="rounded-xl bg-blue-50 p-3 text-sm text-blue-900">اختر حساب جوجل لعرض تقريره وإدارة حملاته. لا تُجمع حسابات مختلفة في تقرير واحد.</p>}
        <div className="flex flex-wrap items-center gap-3 rounded-2xl border bg-white p-4"><label className="min-w-[230px] flex-1 text-xs font-bold text-slate-500">حساب جوجل<select aria-label="حساب إعلانات جوجل" className={`${INPUT} mt-2 text-slate-900`} value={accountId} onChange={(event) => changeContext({ accountId: event.target.value, parent: {}, kind: "campaigns" })}><option value="">اختر حساب جوجل</option>{accounts.map((item) => <option key={accountIdOf(item)} value={accountIdOf(item)}>{accountNameOf(item)} · {item.currency || "عملة الحساب"}</option>)}</select></label><button type="button" className={PRIMARY} disabled={!account || !creationAllowed} onClick={create}>إنشاء حملة جوجل</button><button type="button" className={BUTTON} disabled={!accountId || listLoading} onClick={() => readList("drafts")}>المسودات المحفوظة</button><button type="button" className={BUTTON} disabled={!accountId || listLoading} onClick={() => readList("history")}>سجل عمليات جوجل</button><button type="button" className={BUTTON} disabled={!accountId || loading || loadingReadiness || permissions.can_analyze === false} onClick={() => { setComposer(null); setAnalysis({ accountId, name: accountNameOf(account || {}) }); }}>تحليل الحساب بالذكاء</button></div>
        {account && <p className="text-xs leading-6 text-slate-500">عملة الحساب: {account.currency || "غير متاحة"} · توقيت التقارير: {account.timezone || account.time_zone || report?.range?.timezone || "حسب حساب جوجل"}{permissions.can_execute === false && " · التنفيذ يحتاج تفويضًا من المالك لهذا الحساب"}{permissions.max_daily_budget_native && ` · أعلى متوسط ميزانية يومية مفوض: ${permissions.max_daily_budget_native} ${account.currency}`}</p>}
        {unresolvedPrerequisites.length > 0 && <section className="rounded-xl bg-amber-50 p-4 text-sm text-amber-900"><h3 className="font-bold">متطلبات الحساب</h3>{unresolvedPrerequisites.map((gap, index) => <p key={index} className="mt-2">{prerequisiteName(gap)}</p>)}<a href="/integrations-v2?provider=google_ads" className="mt-3 inline-block font-bold underline">فتح ربط الحساب في ميزان</a></section>}
        <GoogleCapabilityMatrix readiness={readiness} accountId={accountId} />
        {permissions.owner === true && account && <GoogleDelegationPanel key={accountId} account={account} onUpdated={() => setRevision((value) => value + 1)} />}
        {permissions.owner === true && account && <GoogleAutomationPolicy accountId={accountId} isOwner={permissions.owner} />}
        {permissions.owner === true && account && <GoogleCreationPolicy accountId={accountId} isOwner={permissions.owner} />}
        <GoogleCommercialPlan isOwner={permissions.owner} />
        {listLoading && <p role="status" className="text-center text-sm text-slate-500">جارٍ تحميل القائمة…</p>}
        {drafts && <section className="space-y-2 rounded-2xl border bg-white p-4" aria-label="مسودات جوجل المحفوظة"><h3 className="font-black">المسودات المحفوظة</h3>{drafts.map((record) => <button type="button" key={record.draft_id || record.id} className="flex w-full flex-wrap items-center justify-between gap-2 rounded-xl border p-3 text-right text-sm" onClick={() => openComposer({ mode: "create", accountId, accountName: accountNameOf(account || {}), currency: accountCurrency, draftId: record.draft_id || record.id })}><span className="font-bold">{record.draft?.name || record.name || "مسودة حملة"}</span><span className="text-xs text-slate-500">{GOOGLE_CAMPAIGN_LABELS[record.draft?.campaign_type || record.campaign_type] || "حملة"} · النسخة {record.revision}</span><span className="font-bold text-emerald-700">استكمال المسودة</span></button>)}{!drafts.length && <p className="p-3 text-sm text-slate-500">لا توجد مسودات محفوظة للحساب المختار.</p>}</section>}
        {history && <section className="space-y-2 rounded-2xl border bg-white p-4" aria-label="تاريخ عمليات جوجل"><h3 className="font-black">سجل التنفيذ والاعتماد</h3>{history.map((proposal) => <button type="button" key={proposal.proposal_id} disabled={listLoading} className="flex w-full flex-wrap items-center justify-between gap-2 rounded-xl border p-3 text-right text-sm" onClick={() => openHistory(proposal)}><span>{proposal.campaign_name || proposal.planned?.name || "عملية مواد أو حملة"}</span><span className="font-bold">{GOOGLE_STATUS_LABELS[proposal.status] || "جارٍ التحقق"}</span><span className="text-xs text-slate-500">{proposal.created_at ? new Date(proposal.created_at).toLocaleString("ar-SA") : ""}</span></button>)}{!history.length && <p className="p-3 text-sm text-slate-500">لا توجد عمليات محفوظة للحساب المختار.</p>}</section>}
        {parent.campaignId && accountId && <div className="flex flex-wrap gap-2 rounded-2xl border bg-white p-4" aria-label="إضافة عناصر حملة جوجل">
            <button type="button" className={PRIMARY} disabled={!canManage({ campaign_type: parent.campaignType }, "add_group")} onClick={() => openComposer({ mode: "edit", action: "add_group", accountId, accountName: accountNameOf(account || {}), currency: accountCurrency, campaignId: parent.campaignId, campaignType: parent.campaignType, campaignContext: parent.campaignContext, name: parent.campaignName })}>{parent.campaignType === "PERFORMANCE_MAX" ? "إضافة مجموعة مواد للحملة" : "إضافة مجموعة للحملة"}</button>
            {parent.groupId && parent.campaignType !== "PERFORMANCE_MAX" && <button type="button" className={PRIMARY} disabled={!canManage({ campaign_type: parent.campaignType }, "add_ad")} onClick={() => openComposer({ mode: "edit", action: "add_ad", accountId, accountName: accountNameOf(account || {}), currency: accountCurrency, campaignId: parent.campaignId, campaignType: parent.campaignType, campaignContext: parent.campaignContext, groupId: parent.groupId, name: parent.groupName })}>إضافة إعلان للمجموعة</button>}
        </div>}
        {kind === "groups" && items.length > 0 && <details className="rounded-2xl border bg-white p-4" open aria-label="إدارة مجموعات جوجل"><summary className="cursor-pointer font-black">إدارة المجموعات مباشرة</summary><div className="mt-3 grid gap-3 lg:grid-cols-2">{items.map((row) => <article key={`${rowAccountId(row)}:${rowId(row, kind)}`} className="rounded-xl border p-3"><h3 className="font-bold">{rowName(row)}</h3><div className="mt-3 flex flex-wrap gap-2">{[["rename_group", "تعديل الاسم"], [row.status === "ENABLED" ? "pause_group" : "enable_group", row.status === "ENABLED" ? "إيقاف المجموعة" : "تفعيل المجموعة"], ...((rowType(row) || parent.campaignType) === "PERFORMANCE_MAX" ? [["update_group_assets", "تعديل مواد المجموعة"]] : [["set_group_bidding", "تعديل مزايدة المجموعة"], ["add_ad", "إضافة إعلان"], ...((rowType(row) || parent.campaignType) === "SEARCH" ? [["set_keywords", "الكلمات المفتاحية"]] : (rowType(row) || parent.campaignType) === "DEMAND_GEN" ? [["set_channels", "قنوات العرض"]] : [])]), ["remove_group", "حذف المجموعة"]].map(([action, label]) => <button key={action} type="button" className={`${BUTTON} text-xs`} disabled={row.status === "REMOVED" || !canManage(row, action)} onClick={() => openComposer(selectionFor(row, "edit", action))}>{label}</button>)}</div></article>)}</div></details>}
        {kind === "ads" && items.length > 0 && <details className="rounded-2xl border bg-white p-4" aria-label="حالة إعلانات جوجل"><summary className="cursor-pointer font-black">تفعيل وإيقاف الإعلانات</summary><div className="mt-3 space-y-2">{items.map((row) => <article key={`${rowAccountId(row)}:${rowId(row, kind)}`} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border p-3"><span className="text-sm font-bold">{rowName(row)}</span><div className="flex gap-2"><button type="button" className={BUTTON} disabled={row.status === "REMOVED" || !canManage(row, row.status === "ENABLED" ? "pause_ad" : "enable_ad")} onClick={() => openComposer(selectionFor(row, "edit", row.status === "ENABLED" ? "pause_ad" : "enable_ad"))}>{row.status === "ENABLED" ? "إيقاف الإعلان" : "تفعيل الإعلان"}</button><button type="button" className={`${BUTTON} text-rose-700`} disabled={row.status === "REMOVED" || !canManage(row, "remove_ad")} onClick={() => openComposer(selectionFor(row, "edit", "remove_ad"))}>حذف الإعلان</button></div></article>)}</div></details>}
        {items.some((row) => row.editable === false || row.creative_supported === false) && <section className="space-y-2 rounded-2xl border border-amber-200 bg-amber-50 p-4" aria-label="فجوات قراءة مواد جوجل">
            <h3 className="text-sm font-bold text-amber-950">بعض الإعلانات تحتاج قراءة إضافية قبل تعديل محتواها</h3>
            {items.filter((row) => row.editable === false || row.creative_supported === false).map((row) => <p key={`${rowAccountId(row)}:${rowId(row, kind)}`} className="text-xs leading-6 text-amber-900">{rowName(row)}: {googleCreativeGaps(row).join(" · ") || "لا يمكن استعادة جميع إعدادات المحتوى الحالية بأمان."} · يظل تعديل المحتوى مقفلًا حتى تتوفر قراءة كاملة للصيغة والمواد.</p>)}
        </section>}
        <div ref={editorRef}>{composer && <GoogleCampaignComposer key={composer.key} selection={composer} readiness={readiness} onClose={() => setComposer(null)} onCompleted={completed} onDraftSaved={() => setDrafts(null)} />}</div>
        {accountId && <GoogleAdsIntelligenceCard accountId={accountId} dateFrom={dateFrom} dateTo={dateTo} onPreview={openRecommendationProposal} />}
        {analysis && <GoogleAnalysisPanel selection={analysis} dateFrom={dateFrom} dateTo={dateTo} onPreview={openRecommendationProposal} onClose={() => setAnalysis(null)} onDraft={(draft) => openComposer({ mode: "create", accountId: analysis.accountId, accountName: accountNameOf(account || {}), currency: accountCurrency, initialDraft: draft })} onRecommendation={(item) => openComposer({ mode: "edit", action: item.action, accountId: item.account_id || analysis.accountId, accountName: accountNameOf(account || {}), currency: item.currency || accountCurrency, campaignId: String(item.campaign_id), name: item.campaign_name || analysis.name, reason: item.rationale || item.title || "توصية من ذكاء ميزان", changes: { ...(item.changes || {}), ...(item.proposed_budget_native ? { budget_native: String(item.proposed_budget_native) } : {}) } })} />}
        <nav aria-label="مستويات إعلانات جوجل" className="flex flex-wrap gap-2">{LEVELS.map(([value, label]) => <button key={value} type="button" className={`rounded-xl px-4 py-2.5 text-sm font-bold ${kind === value ? "bg-slate-950 text-white" : "border bg-white text-slate-700"}`} aria-pressed={kind === value} disabled={["groups", "ads"].includes(value) && !parent.campaignId} onClick={() => changeContext({ kind: value, parent: value === "campaigns" ? {} : parent })}>{label}</button>)}</nav>
        {parent.campaignId && <nav aria-label="مسار حملة جوجل" className="flex flex-wrap items-center gap-2 rounded-xl bg-slate-100 p-3 text-sm"><button type="button" onClick={() => changeContext({ kind: "campaigns", parent: {} })} className="font-bold text-blue-700">الحملات</button><span>›</span><button type="button" onClick={() => changeContext({ kind: "groups", parent: { campaignId: parent.campaignId, campaignName: parent.campaignName, campaignType: parent.campaignType, campaignContext: parent.campaignContext } })} className="font-bold">{parent.campaignName}</button>{parent.groupName && <><span>›</span><span>{parent.groupName}</span></>}</nav>}
        {(totals.spend_native !== undefined || totals.cost_native !== undefined || totals.impressions !== undefined) && <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-5">{[["صرف جوجل", money(totals.spend_native ?? totals.cost_native, totals.currency || accountCurrency)], ["الظهور", number(totals.impressions)], ["النقرات", number(totals.clicks)], ["تحويلات جوجل", number(totals.conversions, 2)], ["قيمة تحويلات جوجل", money(totals.conversion_value, totals.currency || accountCurrency)]].map(([label, value]) => <article key={label} className="rounded-2xl border bg-white p-4"><p className="text-xs font-bold text-slate-500">{label}</p><p className="mt-2 font-mono text-lg font-black">{value}</p></article>)}</div>}
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white"><table className="w-full min-w-[1060px] text-right text-sm" data-testid="google-entities-table"><thead className="bg-slate-50"><tr>{["العنصر والحساب", "الحالة والتسليم", "الميزانية", "الصرف · عملة الحساب", "تحويلات جوجل", "الظهور", "النقرات", "CTR", "الإدارة"].map((label) => <th key={label} className="p-3">{label}</th>)}</tr></thead><tbody>{items.map((row) => { const id = rowId(row, kind); const status = String(row.status || row.entity?.status || "UNKNOWN").toUpperCase(); const type = rowType(row) || parent.campaignType; const warnings = row.policy_issues || row.disapproval_reasons || row.issues || []; return <tr key={`${rowAccountId(row)}:${id}`} className="border-t border-slate-100"><td className="max-w-[280px] p-3"><div className="font-bold">{rowName(row)}</div><div className="mt-1 text-xs text-slate-500">{row.account_name || accountNameOf(accounts.find((item) => accountIdOf(item) === rowAccountId(row)) || {})} · {GOOGLE_CAMPAIGN_LABELS[type] || (kind === "assets" ? row.type === "YOUTUBE_VIDEO" ? "فيديو YouTube" : "مادة إعلان" : "حملة")}</div>{kind === "assets" && row.preview_url && <img src={row.preview_url} alt={rowName(row)} className="mt-2 h-16 w-20 rounded-lg object-contain" loading="lazy" />}{row.salla_results?.status === "complete" && <p className="mt-2 text-xs text-emerald-800">سلة: {number(row.salla_results.orders)} طلب · {money(row.salla_results.sales_sar ?? row.salla_results.revenue_sar, "SAR")}</p>}</td><td className="max-w-[280px] p-3"><span className={`rounded-lg px-2 py-1 text-xs font-bold ${status === "ENABLED" ? "bg-emerald-50 text-emerald-800" : "bg-slate-100 text-slate-600"}`}>{GOOGLE_STATUS_LABELS[status] || "غير محسومة"}</span><p className="mt-2 text-xs text-slate-500">{row.delivery_status_label || GOOGLE_STATUS_LABELS[row.primary_status || row.delivery_status] || row.primary_status_reason || ""}</p>{warnings.map((warning, index) => <p key={index} className="mt-1 text-xs leading-5 text-amber-900">{typeof warning === "string" ? warning : warning.message || warning.topic || "توجد ملاحظة مراجعة من جوجل"}</p>)}</td><td className="p-3 font-mono text-xs">{money(row.budget_native ?? row.budget?.daily_native ?? row.budget?.amount, row.currency || accountCurrency)}{row.budget_mode && <p className="mt-1 font-sans text-slate-500">{row.budget_mode === "TOTAL" ? "إجمالية" : "متوسط يومي"}</p>}</td><td className="p-3 font-mono">{money(rowMetric(row, "spend"), row.currency || accountCurrency)}</td><td className="p-3 font-mono">{number(rowMetric(row, "conversions"), 2)}</td><td className="p-3 font-mono">{number(rowMetric(row, "impressions"))}</td><td className="p-3 font-mono">{number(rowMetric(row, "clicks"))}</td><td className="p-3 font-mono">{number(row.ctr_pct ?? row.metrics?.ctr_pct, 2)}{row.ctr_pct != null || row.metrics?.ctr_pct != null ? "%" : ""}</td><td className="p-3"><div className="flex min-w-[190px] flex-wrap gap-2">{["campaigns", "groups"].includes(kind) && <button type="button" className="rounded-lg bg-blue-50 px-3 py-2 text-xs font-bold text-blue-800" onClick={() => openChildren(row)}>{kind === "campaigns" ? type === "PERFORMANCE_MAX" ? "عرض مجموعات المواد" : "عرض المجموعات" : type === "PERFORMANCE_MAX" || row.entity_type === "asset_group" ? "عرض المواد" : "عرض الإعلانات"}</button>}{kind === "campaigns" && <><button type="button" className="rounded-lg bg-violet-50 px-3 py-2 text-xs font-bold text-violet-800" disabled={permissions.can_analyze === false} onClick={() => { setComposer(null); setAnalysis(selectionFor(row)); }}>تحليل بالذكاء</button><button type="button" className="rounded-lg bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-800 disabled:opacity-40" disabled={status === "REMOVED" || !canManage(row, "rename")} onClick={() => openComposer(selectionFor(row))}>إدارة الحملة</button><button type="button" className="rounded-lg border px-3 py-2 text-xs font-bold disabled:opacity-40" disabled={status === "REMOVED" || !canManage(row, status === "ENABLED" ? "pause" : "enable")} onClick={() => openComposer(selectionFor(row, "edit", status === "ENABLED" ? "pause" : "enable"))}>{status === "ENABLED" ? "إيقاف" : "تفعيل"}</button></>}{kind === "ads" && <button type="button" className="rounded-lg bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-800 disabled:opacity-40" disabled={status === "REMOVED" || !canManage(row, "update_ad") || !row.resource_name && !row.ad_group_ad_resource} onClick={() => openComposer(selectionFor(row, "edit", "update_ad"))}>تعديل الإعلان</button>}</div></td></tr>; })}</tbody></table>{loading && <p role="status" className="p-10 text-center text-sm text-slate-500">جارٍ تحميل مستوى جوجل المختار…</p>}{!loading && !items.length && <p className="p-10 text-center text-sm text-slate-500">لا توجد عناصر لهذه الفترة والاختيارات. حدّث البيانات أو أنشئ حملة على حساب مؤهّل.</p>}</div>
        <footer className="flex flex-wrap items-center justify-between gap-3 text-sm"><p className="text-slate-500">{pagination.total != null ? `${number(pagination.total)} عنصر · ` : ""}صفحة {page} من {pages}</p><div className="flex gap-2"><button type="button" className={BUTTON} disabled={loading || page <= 1} onClick={() => setContext((current) => ({ ...current, page: current.page - 1 }))}>السابق</button><button type="button" className={BUTTON} disabled={loading || page >= pages} onClick={() => setContext((current) => ({ ...current, page: current.page + 1 }))}>التالي</button></div></footer>
        <p className="text-xs leading-6 text-slate-500">التقارير بحسب توقيت وعملة حساب جوجل. لا تُجمع أرقام الحملات والمجموعات والإعلانات معًا. ظهور الحملة أو نجاح إرسالها لا يثبت قبول الإعلان أو بدء التسليم.</p>
    </section>;
}

