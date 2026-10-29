import { useEffect, useRef, useState } from "react";
import GoogleNativeInventoryPreview from "./GoogleNativeInventoryPreview";
import { getGoogleLiveCategoryPreview, liveCategoryOptions, liveCategoryScope } from "../../services/googleLiveCategoryPreview";
import { isGoogleRequestCancelled } from "../../services/googleAds";

const CATEGORY_STATUS = { active: "نشط بحسب المصدر", hidden: "مخفي بحسب المصدر", unknown: "غير مثبتة" };
const PRODUCT_STATUS = { sale: "معروض للبيع بحسب المصدر؛ المخزون غير مثبت", out: "غير متاح بحسب حالة المصدر؛ المخزون غير مثبت", hidden: "مخفي بحسب المصدر", unknown: "غير مثبتة" };
function failureMessage(failure) {
    const detail = failure?.response?.data?.detail;
    if (detail?.code === "google_live_category_unavailable" && detail.reason === "permission_unproved") return "لم تثبت صلاحية قراءة هذا القسم في هذا الطلب. لا يثبت ذلك فقدان اتصال المتجر؛ لم تتغير إعدادات الربط.";
    if (detail?.code === "google_live_category_unavailable" && detail.reason === "native_read_unavailable") return "تعذرت قراءة القسم من سلة الآن. يمكن إعادة المحاولة؛ لم تتغير إعدادات الربط.";
    if (detail?.code === "google_commercial_plan_revision_conflict" || failure?.response?.status === 404) return "تغيرت الخطة أو لم تعد متاحة؛ أعد تحميل نسختها المحفوظة قبل المعاينة.";
    return "تعذرت معاينة موثوقة للقسم ضمن حدود هذه القراءة. راجع نطاق الخطة المحفوظة ثم أعد المحاولة؛ لا توجد نتيجة صالحة للاختيار.";
}
function Observation({ result, plan }) {
    return <div className="mt-3 space-y-2" data-live-category-result>
        <p>المتجر {result.store_id} · القسم {result.category_id} · نسخة الخطة {result.plan_revision}</p>
        {result.category_url && <p className="break-all">رابط القسم كما أعاده المتجر: {result.category_url}</p>}
        <p>بدأت الملاحظة: {result.observed_started_at} · انتهت: {result.observed_finished_at}</p>
        <p>حالة القسم: {CATEGORY_STATUS[result.category_status]}</p>
        <p>{result.pagination_traversal_complete ? "اكتمل تصفح الصفحات ضمن هذه القراءة." : "تصفح الصفحات جزئي؛ قد توجد منتجات لم تظهر ضمن حدود القراءة."} الصفحات المقروءة: {result.pages_read} · المنتجات المرصودة: {result.products_observed}.</p>
        <p>الصفحات لا تشكل لقطة متزامنة ثابتة، ولا توجد نسخة موحدة مثبتة من المزود. اكتمال التصفح لا يثبت جاهزية الإعلان.</p>
        {!!result.unresolved_exclusion_ids.length && <p>تعذر ربط معرفات الاستبعاد التالية بمعرفات سلة الرقمية: {result.unresolved_exclusion_ids.join("، ")}. لا تعني هذه الفجوة السماح باختيارها.</p>}
        {!result.products.length ? <p>لم تظهر منتجات في الصفحات المقروءة؛ لا يثبت ذلك غيابها خارج هذه القراءة.</p> : <div className="overflow-x-auto"><table className="w-full text-right"><caption className="text-right">عضوية مرصودة من واجهة قسم المتجر فقط</caption><thead><tr><th>معرف سلة</th><th>استبعاد الخطة</th><th>حالة المصدر</th><th>المخزون والأهلية</th></tr></thead><tbody>
            {result.products.map((product) => <tr key={product.salla_product_id} data-salla-product-id={product.salla_product_id}><td>{product.salla_product_id}</td><td>{product.excluded ? "مستبعد في الخطة" : "لا يطابق استبعادًا رقميًا محفوظًا؛ لا يمنح إذن اختيار"}</td><td>{PRODUCT_STATUS[product.product_status]}</td><td>المخزون غير مثبت · غير مؤهل للاختيار الإعلاني</td></tr>)}
        </tbody></table></div>}
        <p>المقاسات والمخزون ومدة التجهيز والمطابقة بمنتجات ميزان وعروض الكتالوج غير مثبتة. لا ترتيب للمنتجات أو استنتاج للربح أو تفويض تشغيل وميزانية.</p>
        <GoogleNativeInventoryPreview plan={plan} category={result} isOwner={true} />
    </div>;
}
function RequestPanel({ plan, categoryId }) {
    const [result, setResult] = useState(null), [busy, setBusy] = useState(false), [locked, setLocked] = useState(false), [error, setError] = useState("");
    const mounted = useRef(true), request = useRef(null), inflight = useRef(false);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
    let ready = true; try { liveCategoryScope(plan, categoryId); } catch { ready = false; }
    async function read() {
        if (!ready || inflight.current || locked) return;
        inflight.current = true; setBusy(true); setResult(null); setError(""); request.current = new AbortController();
        try { const data = await getGoogleLiveCategoryPreview(plan, { categoryId, signal: request.current.signal }); if (mounted.current) setResult(data); }
        catch (failure) { if (mounted.current && !isGoogleRequestCancelled(failure)) {
            setLocked(failure?.response?.status === 404 || failure?.response?.data?.detail?.code === "google_commercial_plan_revision_conflict" || failure?.code === "GOOGLE_LIVE_CATEGORY_PREVIEW_UNPROVED");
            setError(failureMessage(failure));
        } } finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    return <>
        <button type="button" onClick={read} disabled={!ready || busy || locked} className="rounded border px-3 py-2 disabled:opacity-40">{busy ? "جارٍ معاينة قسم سلة…" : "معاينة عضوية قسم سلة الآن"}</button>
        {error && <p role="alert">{error}</p>}{result && <Observation result={result} plan={plan} />}
    </>;
}
function Panel({ plan, options }) {
    const [categoryId, setCategoryId] = useState(options.categoryIds.length === 1 ? options.categoryIds[0] : "");
    return <section dir="rtl" aria-label="معاينة عضوية قسم سلة" className="rounded-xl border border-slate-200 p-4 text-sm leading-7">
        <h3 className="font-bold">معاينة عضوية قسم سلة</h3>
        <p>قراءة من واجهة المتجر المملوك عند طلبك، لقسم واحد من الخطة المحفوظة. تعرض العضوية المرصودة فقط، ولا تحفظ أو تثبت أو تختار منتجات في الخطة.</p>
        {options.categoryIds.length > 1 && <label className="block">القسم المحفوظ للمعاينة <select aria-label="القسم المحفوظ للمعاينة" value={categoryId} onChange={(event) => setCategoryId(event.target.value)}>
            <option value="">{options.categoryUrl ? "الرابط الدقيق المحفوظ في الخطة" : "اختر قسمًا محفوظًا"}</option>
            {options.categoryIds.map((id) => <option key={id} value={id}>القسم {id}</option>)}
        </select></label>}
        {options.categoryIds.length === 1 && <p>القسم المحفوظ: {options.categoryIds[0]}</p>}
        {options.categoryUrl && <p className="break-all">الرابط المحفوظ: {options.categoryUrl} · يلزم تطابقه مع رابط القسم الذي يعيده المتجر.</p>}
        <RequestPanel key={categoryId} plan={plan} categoryId={categoryId} />
    </section>;
}
export default function GoogleLiveCategoryPreview({ plan, isOwner, planDirty = false }) {
    if (isOwner !== true) return null;
    if (planDirty) return <p dir="rtl">احفظ الخطة أو أعد تحميل نسختها قبل معاينة قسم سلة.</p>;
    let options; try { options = liveCategoryOptions(plan); } catch { return <p dir="rtl">احفظ معرف قسم سلة الرقمي أو رابط القسم الدقيق ضمن الخطة قبل المعاينة.</p>; }
    return <Panel key={JSON.stringify([plan.plan_id, plan.revision, plan.fields])} plan={plan} options={options} />;
}
