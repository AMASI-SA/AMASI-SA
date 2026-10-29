import { useEffect, useRef, useState } from "react";
import { googleErrorMessage, isGoogleRequestCancelled } from "../../services/googleAds";
import { getGoogleCommercialPlan, saveGoogleCommercialPlan } from "../../services/googleCommercialPlan";
import { useAuth } from "../../context/AuthContext";
import GooglePinnedCatalogReview from "./GooglePinnedCatalogReview";
import GoogleStoreProductObservations from "./GoogleStoreProductObservations";
import GoogleCategoryMembershipReview from "./GoogleCategoryMembershipReview";
import GoogleLinkedCampaignEvidence from "./GoogleLinkedCampaignEvidence";
import GoogleLiveCategoryPreview from "./GoogleLiveCategoryPreview";
import GooglePriorSeasonReview from "./GooglePriorSeasonReview";
import GoogleCommercialPlanSuggestion from "./GoogleCommercialPlanSuggestion";
import { applyGoogleSuggestionToEmptyFields } from "../../services/googleCommercialPlanSuggestion";

const INPUT = "mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm disabled:bg-slate-100";
const BUTTON = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40";
const LISTS = ["category_ids", "pinned_product_ids", "excluded_product_ids", "exclusions"];
const LIMITS = ["daily_budget_native", "total_budget_native", "minimum_margin_percent", "target_roas"];
const emptyPriorContext = () => ({ date_from: "", date_to: "", season_label: "", alignment_note: "", calendar_reference_text: "" });
const FIELDS = [
    ["season", "الموسم", "text"], ["starts_on", "بداية الموسم", "date"], ["ends_on", "نهاية الموسم", "date"],
    ["market", "السوق · رمز الدولة مثل SA", "text"], ["audience", "الجمهور المستهدف", "text"], ["message", "الرسالة التسويقية", "text"],
    ["category_url", "رابط القسم", "url"], ["category_ids", "معرفات الأقسام", "text"],
    ["pinned_product_ids", "معرفات المنتجات المثبتة", "text"], ["excluded_product_ids", "معرفات المنتجات المستبعدة", "text"],
];

function formFrom(plan) {
    const fields = plan?.fields || {};
    return { instruction: "", season: "", starts_on: "", ends_on: "", market: "", audience: "", message: "", category_url: "",
        calendar: "gregorian", selection_mode: "pinned_only", ...fields,
        ...Object.fromEntries(LISTS.map((key) => [key, (fields[key] || []).join(key === "exclusions" ? "\n" : ", ")])),
        prior_season_context: fields.prior_season_context ? { ...fields.prior_season_context, calendar_reference_text: fields.prior_season_context.calendar_reference_text ?? "" } : null,
        limits: { currency_code: "", ...Object.fromEntries(LIMITS.map((key) => [key, ""])), ...(fields.limits || {}) } };
}

function priorContextPayload(prior, startsOn) {
    if (prior == null) return null;
    const day = (value) => {
        const stamp = typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? Date.parse(`${value}T00:00:00Z`) : NaN;
        return Number.isFinite(stamp) && new Date(stamp).toISOString().slice(0, 10) === value ? stamp : NaN;
    };
    const first = day(prior.date_from), last = day(prior.date_to), current = day(startsOn);
    if (![first, last, current].every(Number.isFinite) || last < first || last - first > 365 * 86400000 || last >= current) {
        throw new Error("اختر فترة سابقة صحيحة من يوم إلى 366 يومًا، تنتهي قبل بداية الموسم الحالي.");
    }
    const season = String(prior.season_label || "").trim(), note = String(prior.alignment_note || "").trim();
    const reference = String(prior.calendar_reference_text || "").trim();
    if (!season || season.length > 160 || !note || note.length > 1000 || reference.length > 500) {
        throw new Error("أكمل وصف الموسم السابق وسبب اختيار الفترة، وراجع طول المرجع الاختياري.");
    }
    return { date_from: prior.date_from, date_to: prior.date_to, season_label: season, alignment_note: note, calendar_reference_text: reference || null };
}

export function commercialPlanPayload(form, revision) {
    const fields = { ...form };
    for (const key of LISTS) fields[key] = form[key].split(key === "exclusions" ? /\n+/ : /[\s,،]+/).map((value) => value.trim()).filter(Boolean);
    fields.category_url = form.category_url.trim() || null;
    const hasLimits = LIMITS.some((key) => String(form.limits[key] || "").trim());
    fields.limits = hasLimits ? { currency_code: form.limits.currency_code.trim().toUpperCase(),
        ...Object.fromEntries(LIMITS.map((key) => [key, String(form.limits[key] || "").trim() || null])) } : null;
    fields.market = form.market.trim().toUpperCase();
    fields.prior_season_context = priorContextPayload(form.prior_season_context, form.starts_on);
    return { ...fields, expected_revision: revision, acknowledge_review: true };
}

function PlanPanel({ planId, ownerId }) {
    const [plan, setPlan] = useState(null), [form, setForm] = useState(null);
    const [busy, setBusy] = useState(true), [error, setError] = useState(""), [notice, setNotice] = useState("");
    const [conflict, setConflict] = useState(false), [reload, setReload] = useState(0), [reviewed, setReviewed] = useState(false);
    const mounted = useRef(true), request = useRef(null), inflight = useRef(false);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
    useEffect(() => {
        const controller = new AbortController(); request.current = controller; let active = true;
        setBusy(true); setError(""); setNotice("");
        getGoogleCommercialPlan(planId, { signal: controller.signal }).then((result) => {
            if (active) { setPlan(result); setForm(formFrom(result)); setConflict(false); setReviewed(false); }
        }).catch((failure) => {
            if (active && !isGoogleRequestCancelled(failure)) { setError(googleErrorMessage(failure, "تعذر تحميل خطة الموسم.")); setConflict(true); }
        }).finally(() => { if (active) setBusy(false); });
        return () => { active = false; controller.abort(); };
    }, [planId, reload]);
    function edit(key, value) { setForm((prior) => ({ ...prior, [key]: value })); setReviewed(false); setNotice(""); }
    function applySuggestion(suggestion) {
        if (busy || conflict || !form) return;
        try {
            setForm(applyGoogleSuggestionToEmptyFields(form, suggestion));
            setReviewed(false); setNotice("عُبئت الحقول الفارغة من الاقتراح. راجع الخطة وحدودها قبل الحفظ."); setError("");
        } catch (failure) { setError(failure.message); }
    }
    async function save(event) {
        event.preventDefault();
        if (busy || inflight.current || conflict || !reviewed || !form) return;
        let payload;
        try { payload = commercialPlanPayload(form, plan?.revision || 0); }
        catch (failure) { setError(failure.message); setReviewed(false); return; }
        inflight.current = true; setBusy(true); setError(""); setNotice("");
        const controller = new AbortController(); request.current = controller;
        try {
            const result = await saveGoogleCommercialPlan(planId, payload, { signal: controller.signal });
            if (mounted.current) { setPlan(result); setForm(formFrom(result)); setReviewed(false); setNotice("حُفظت خطة الموسم للمراجعة التجارية. لم يبدأ تشغيل أي حملة."); }
        } catch (failure) {
            if (mounted.current && !isGoogleRequestCancelled(failure)) {
                setConflict(true);
                setError(failure?.response?.status === 409 ? "تغيرت الخطة المحفوظة. بقيت مدخلاتك ظاهرة؛ أعد التحميل لمراجعة النسخة الحالية." : googleErrorMessage(failure, "تعذر تأكيد الحفظ. بقيت مدخلاتك ظاهرة؛ أعد التحميل للتحقق من النسخة المحفوظة قبل المحاولة."));
            }
        } finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    return <details className="rounded-2xl border border-slate-200 bg-white p-4" aria-label="خطة المواسم والمنتجات لجوجل" dir="rtl">
        <summary className="cursor-pointer font-black">خطة المواسم والمنتجات لجوجل</summary>
        <p className="mt-3 text-sm leading-7 text-slate-600">اكتب توجيهك بالكلام العادي لاقتراح حقول الموسم والسوق والجمهور والرابط، ثم راجعها وأكمل الحقول الناقصة. اختيار المنتجات تلقائيًا غير متاح في هذه المرحلة.</p>
        <p className="mt-2 text-sm leading-7 text-amber-900">حفظ الخطة والحدود التجارية لا يمنح تفويض تشغيل أو إنفاق؛ التنفيذ يحتاج تفويضًا منفصلًا وحدودًا معتمدة.</p>
        <button type="button" className={`${BUTTON} mt-3`} disabled={busy} onClick={() => setReload((value) => value + 1)}>إعادة تحميل خطة الموسم</button>
        {busy && <p role="status">جارٍ قراءة أو حفظ الخطة…</p>}
        {error && <p role="alert" className="mt-3 text-sm text-red-800">{error}</p>}
        {notice && <p role="status" className="mt-3 text-sm text-emerald-800">{notice}</p>}
        {form && <form className="mt-4 space-y-4" onSubmit={save}>
            <p className="text-xs text-slate-500">النسخة المحفوظة: {plan?.revision || 0} · المنتجات والأدلة تحتاج تحققًا مستقلًا</p>
            <fieldset disabled={busy || conflict} className="space-y-4">
                <label className="block text-sm font-bold">توجيه المالك<textarea required maxLength={6000} aria-label="توجيه المالك" className={INPUT} rows={3} value={form.instruction} onChange={(event) => edit("instruction", event.target.value)} placeholder="ركز على هدايا الشتاء بالسعودية وهذا رابط قسم الشالات…" /></label>
                <GoogleCommercialPlanSuggestion form={form} reviewed={reviewed} disabled={busy || conflict} onApply={applySuggestion} />
                <div className="grid gap-3 sm:grid-cols-2">{FIELDS.map(([key, label, type]) => <label className="text-sm font-bold" key={key}>{label}<input required={["season", "starts_on", "ends_on", "market", "audience", "message"].includes(key)} type={type} className={INPUT} aria-label={label} value={form[key] || ""} onChange={(event) => edit(key, event.target.value)} /></label>)}</div>
                <section aria-label="سياق الموسم السابق الاختياري" className="space-y-3 rounded-xl border p-3">
                    <label className="block text-sm font-bold"><input type="checkbox" aria-label="إضافة فترة موسم سابق للمراجعة" checked={form.prior_season_context !== null} onChange={(event) => edit("prior_season_context", event.target.checked ? emptyPriorContext() : null)} /> إضافة فترة موسم سابق للمراجعة</label>
                    <p className="text-xs leading-6 text-slate-600">حدد التواريخ وسبب المقارنة بنفسك. لا تُستنتج مطابقة الموسم الهجري أو العام السابق، ولا تتغير التواريخ تلقائيًا.</p>
                    {form.prior_season_context && <>
                        <div className="grid gap-3 sm:grid-cols-2">{[["date_from", "بداية الفترة السابقة", "date"], ["date_to", "نهاية الفترة السابقة", "date"], ["season_label", "وصف الموسم السابق", "text"]].map(([key, label, type]) => <label key={key} className="text-sm font-bold">{label}<input required type={type} maxLength={key === "season_label" ? 160 : undefined} aria-label={label} className={INPUT} value={form.prior_season_context[key]} onChange={(event) => edit("prior_season_context", { ...form.prior_season_context, [key]: event.target.value })} /></label>)}</div>
                        <label className="block text-sm font-bold">سبب اختيار الفترة السابقة<textarea required maxLength={1000} aria-label="سبب اختيار الفترة السابقة" className={INPUT} value={form.prior_season_context.alignment_note} onChange={(event) => edit("prior_season_context", { ...form.prior_season_context, alignment_note: event.target.value })} /></label>
                        <label className="block text-sm font-bold">مرجع تقويمي اختياري<input maxLength={500} aria-label="مرجع تقويمي اختياري" className={INPUT} value={form.prior_season_context.calendar_reference_text} onChange={(event) => edit("prior_season_context", { ...form.prior_season_context, calendar_reference_text: event.target.value })} /></label>
                        <button type="button" className={BUTTON} onClick={() => edit("prior_season_context", null)}>إزالة فترة الموسم السابق</button>
                    </>}
                </section>
                <label className="block text-sm font-bold">الاستبعادات الإضافية<textarea aria-label="الاستبعادات الإضافية" className={INPUT} rows={2} value={form.exclusions} onChange={(event) => edit("exclusions", event.target.value)} placeholder="استبعاد واحد في كل سطر" /></label>
                <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-sm font-bold">مرجع الموسم<select className={INPUT} aria-label="مرجع الموسم" value={form.calendar} onChange={(event) => edit("calendar", event.target.value)}><option value="gregorian">ميلادي</option><option value="hijri_season">موسم هجري · يحتاج مطابقة العام السابق</option></select></label>
                    <label className="text-sm font-bold">نطاق ترشيح المنتجات<select className={INPUT} aria-label="نطاق ترشيح المنتجات" value={form.selection_mode} onChange={(event) => edit("selection_mode", event.target.value)}><option value="pinned_only">المنتجات المثبتة فقط</option><option value="review_category_candidates">مراجعة مرشحي القسم</option></select></label>
                </div>
                <p className="text-xs leading-6 text-slate-500">رابط القسم والمعرفات سياق محفوظ؛ راجع العضوية ضمن لقطة المصدر من مراجعة الأقسام أدناه بعد الحفظ. لا تثبت العضوية أهلية الإعلان أو اكتمال الكتالوج البعيد. فصل المعرفات بمسافة أو فاصلة.</p>
                <div className="grid gap-3 sm:grid-cols-2">
                    {[["currency_code", "عملة الحدود مثل SAR"], ["daily_budget_native", "حد الميزانية اليومي الاختياري"], ["total_budget_native", "حد الميزانية الإجمالي الاختياري"], ["minimum_margin_percent", "الحد الأدنى للهامش %"], ["target_roas", "هدف العائد الاختياري ROAS"]].map(([key, label]) => <label key={key} className="text-sm font-bold">{label}<input className={INPUT} inputMode={key === "currency_code" ? "text" : "decimal"} aria-label={label} value={form.limits[key] || ""} onChange={(event) => edit("limits", { ...form.limits, [key]: event.target.value })} /></label>)}
                </div>
                <label className="block text-sm"><input type="checkbox" aria-label="راجعت حقول خطة الموسم" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> راجعت الحقول والمنتجات والحدود أعلاه؛ هذا اعتماد للسياق التجاري فقط.</label>
                <button type="submit" className={BUTTON} disabled={!reviewed}>حفظ خطة الموسم للمراجعة</button>
            </fieldset>
        </form>}
        {plan && form && <GooglePinnedCatalogReview plan={plan} isOwner={true} planDirty={busy || conflict || JSON.stringify(form) !== JSON.stringify(formFrom(plan))} />}
        {plan && form && <GoogleStoreProductObservations plan={plan} isOwner={true} planDirty={busy || conflict || JSON.stringify(form) !== JSON.stringify(formFrom(plan))} />}
        {plan && form && <GoogleCategoryMembershipReview plan={plan} isOwner={true} planDirty={busy || conflict || JSON.stringify(form) !== JSON.stringify(formFrom(plan))} />}
        {plan && form && <GoogleLinkedCampaignEvidence plan={plan} isOwner={true} planDirty={busy || conflict || JSON.stringify(form) !== JSON.stringify(formFrom(plan))} />}
        {plan && form && <GoogleLiveCategoryPreview plan={plan} isOwner={true} planDirty={busy || conflict || JSON.stringify(form) !== JSON.stringify(formFrom(plan))} />}
        {plan && form && <GooglePriorSeasonReview plan={plan} ownerId={ownerId} isOwner={true} planDirty={busy || conflict || JSON.stringify(form) !== JSON.stringify(formFrom(plan))} />}
        {!plan && form && <p role="status" className="mt-3 text-sm text-slate-600">احفظ خطة ومعرفات منتجات سلة الرقمية أولًا لإتاحة مراجعة المنتجات المثبتة في الكتالوج.</p>}
        <section aria-label="جاهزية أدلة خطة الموسم" className="mt-4 rounded-xl bg-slate-50 p-3 text-sm leading-7">
            <p>المبيعات المكتملة: آخر 14 يومًا مقابل الـ14 السابقة، واتجاه 30–90 يومًا، والموسم المقابل عند توفر المطابقة — اكتمالها غير مثبت. المشاهدات الجزئية لا تثبت صافي المبيعات أو نموها.</p>
            <p>الربحية غير مثبتة عند نقص التكاليف. المخزون والمقاسات ووقت التجهيز وربط الكتالوج تحتاج التحقق.</p>
            <p>نتائج سناب وتيك توك وميتا ترشح اختبارًا على جوجل؛ لا تضمن النجاح. تبقى نتائج كل منصة وحساب وعملة وفترة منفصلة، ولا تُجمع تحويلاتها كطلبات متجر.</p>
        </section>
    </details>;
}

export default function GoogleCommercialPlan({ isOwner, planId = "seasonal-focus" }) {
    const { user, authStatus } = useAuth();
    const ownerId = user?.id;
    if (isOwner !== true || authStatus !== "authenticated" || user?.role !== "owner" || typeof ownerId !== "string"
        || !ownerId.trim() || ownerId.trim() !== ownerId || ownerId.length > 128 || !/^[A-Za-z0-9_-]{1,128}$/.test(planId)) return null;
    return <PlanPanel key={JSON.stringify([ownerId, planId])} planId={planId} ownerId={ownerId} />;
}
