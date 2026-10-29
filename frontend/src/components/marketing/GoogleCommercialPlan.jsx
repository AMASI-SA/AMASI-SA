import { useEffect, useRef, useState } from "react";
import { googleErrorMessage, isGoogleRequestCancelled } from "../../services/googleAds";
import { getGoogleCommercialPlan, saveGoogleCommercialPlan } from "../../services/googleCommercialPlan";
import GooglePinnedCatalogReview from "./GooglePinnedCatalogReview";
import GoogleStoreProductObservations from "./GoogleStoreProductObservations";
import GoogleCommercialPlanSuggestion from "./GoogleCommercialPlanSuggestion";
import { applyGoogleSuggestionToEmptyFields } from "../../services/googleCommercialPlanSuggestion";

const INPUT = "mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm disabled:bg-slate-100";
const BUTTON = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40";
const LISTS = ["category_ids", "pinned_product_ids", "excluded_product_ids", "exclusions"];
const LIMITS = ["daily_budget_native", "total_budget_native", "minimum_margin_percent", "target_roas"];
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
        limits: { currency_code: "", ...Object.fromEntries(LIMITS.map((key) => [key, ""])), ...(fields.limits || {}) } };
}

export function commercialPlanPayload(form, revision) {
    const fields = { ...form };
    for (const key of LISTS) fields[key] = form[key].split(key === "exclusions" ? /\n+/ : /[\s,،]+/).map((value) => value.trim()).filter(Boolean);
    fields.category_url = form.category_url.trim() || null;
    const hasLimits = LIMITS.some((key) => String(form.limits[key] || "").trim());
    fields.limits = hasLimits ? { currency_code: form.limits.currency_code.trim().toUpperCase(),
        ...Object.fromEntries(LIMITS.map((key) => [key, String(form.limits[key] || "").trim() || null])) } : null;
    fields.market = form.market.trim().toUpperCase();
    return { ...fields, expected_revision: revision, acknowledge_review: true };
}

function PlanPanel({ planId }) {
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
        inflight.current = true; setBusy(true); setError(""); setNotice("");
        const controller = new AbortController(); request.current = controller;
        try {
            const result = await saveGoogleCommercialPlan(planId, commercialPlanPayload(form, plan?.revision || 0), { signal: controller.signal });
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
                <label className="block text-sm font-bold">الاستبعادات الإضافية<textarea aria-label="الاستبعادات الإضافية" className={INPUT} rows={2} value={form.exclusions} onChange={(event) => edit("exclusions", event.target.value)} placeholder="استبعاد واحد في كل سطر" /></label>
                <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-sm font-bold">مرجع الموسم<select className={INPUT} aria-label="مرجع الموسم" value={form.calendar} onChange={(event) => edit("calendar", event.target.value)}><option value="gregorian">ميلادي</option><option value="hijri_season">موسم هجري · يحتاج مطابقة العام السابق</option></select></label>
                    <label className="text-sm font-bold">نطاق ترشيح المنتجات<select className={INPUT} aria-label="نطاق ترشيح المنتجات" value={form.selection_mode} onChange={(event) => edit("selection_mode", event.target.value)}><option value="pinned_only">المنتجات المثبتة فقط</option><option value="review_category_candidates">مراجعة مرشحي القسم</option></select></label>
                </div>
                <p className="text-xs leading-6 text-slate-500">رابط القسم والمعرفات مقترحات محفوظة؛ عضوية المنتجات ومخزونها ومعرفاتها في الكتالوج لم تُثبت بعد. فصل المعرفات بمسافة أو فاصلة.</p>
                <div className="grid gap-3 sm:grid-cols-2">
                    {[["currency_code", "عملة الحدود مثل SAR"], ["daily_budget_native", "حد الميزانية اليومي الاختياري"], ["total_budget_native", "حد الميزانية الإجمالي الاختياري"], ["minimum_margin_percent", "الحد الأدنى للهامش %"], ["target_roas", "هدف العائد الاختياري ROAS"]].map(([key, label]) => <label key={key} className="text-sm font-bold">{label}<input className={INPUT} inputMode={key === "currency_code" ? "text" : "decimal"} aria-label={label} value={form.limits[key] || ""} onChange={(event) => edit("limits", { ...form.limits, [key]: event.target.value })} /></label>)}
                </div>
                <label className="block text-sm"><input type="checkbox" aria-label="راجعت حقول خطة الموسم" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /> راجعت الحقول والمنتجات والحدود أعلاه؛ هذا اعتماد للسياق التجاري فقط.</label>
                <button type="submit" className={BUTTON} disabled={!reviewed}>حفظ خطة الموسم للمراجعة</button>
            </fieldset>
        </form>}
        {plan && form && <GooglePinnedCatalogReview plan={plan} isOwner={true} planDirty={busy || conflict || JSON.stringify(form) !== JSON.stringify(formFrom(plan))} />}
        {plan && form && <GoogleStoreProductObservations plan={plan} isOwner={true} planDirty={busy || conflict || JSON.stringify(form) !== JSON.stringify(formFrom(plan))} />}
        {!plan && form && <p role="status" className="mt-3 text-sm text-slate-600">احفظ خطة ومعرفات منتجات سلة الرقمية أولًا لإتاحة مراجعة المنتجات المثبتة في الكتالوج.</p>}
        <section aria-label="جاهزية أدلة خطة الموسم" className="mt-4 rounded-xl bg-slate-50 p-3 text-sm leading-7">
            <p>المبيعات المكتملة: آخر 14 يومًا مقابل الـ14 السابقة، واتجاه 30–90 يومًا، والموسم المقابل عند توفر المطابقة — اكتمالها غير مثبت. المشاهدات الجزئية لا تثبت صافي المبيعات أو نموها.</p>
            <p>الربحية غير مثبتة عند نقص التكاليف. المخزون والمقاسات ووقت التجهيز وربط الكتالوج تحتاج التحقق.</p>
            <p>نتائج سناب وتيك توك وميتا ترشح اختبارًا على جوجل؛ لا تضمن النجاح. تبقى نتائج كل منصة وحساب وعملة وفترة منفصلة، ولا تُجمع تحويلاتها كطلبات متجر.</p>
        </section>
    </details>;
}

export default function GoogleCommercialPlan({ isOwner, planId = "seasonal-focus" }) {
    if (isOwner !== true || !/^[A-Za-z0-9_-]{1,128}$/.test(planId)) return null;
    return <PlanPanel key={planId} planId={planId} />;
}
