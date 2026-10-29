import { useEffect, useRef, useState } from "react";
import { getGoogleNativeInventoryPreview, nativeInventoryOptions } from "../../services/googleNativeInventoryPreview";
import { isGoogleRequestCancelled } from "../../services/googleAds";

const STATUS = { sale: "معروض للبيع في المصدر", out: "حالته غير متاح في المصدر", hidden: "مخفي في المصدر", unknown: "حالة المصدر غير مثبتة" };
const BLOCKERS = { global_stock_unproved: "المخزون الشامل غير مثبت", preparation_time_unproved: "مدة التجهيز غير معلومة", size_coverage_unproved: "تغطية المقاسات غير مثبتة", paid_authority_absent: "لا يوجد تفويض إنفاق", native_visibility_or_availability_blocked_or_unknown: "الظهور أو التوفر في المصدر محجوب أو غير مثبت", native_option_value_out_of_stock: "ورد وسم نفاد على قيمة خيار في المصدر", category_hidden_or_visibility_unproved: "ظهور القسم محجوب أو غير مثبت في القراءة الجديدة" };
function Stock({ value }) {
    return <span>الكمية: {value.quantity === null ? "غير معلومة" : value.quantity} · غير محدود: {value.unlimited === null ? "غير معلوم" : value.unlimited ? "نعم بحسب المصدر" : "لا بحسب المصدر"} · {value.state === "zero_unlimited_unknown" ? "صفر مع حالة غير محدود مجهولة؛ لا يثبت النفاد" : value.state === "positive" ? "رصد إيجابي في المصدر؛ لا يثبت قابلية الشراء" : value.state === "zero" ? "صفر مع غير محدود = لا، بحسب المصدر" : "حالة المخزون غير معلومة"}</span>;
}
function Observations({ result }) {
    return <div data-native-inventory-result className="mt-3 space-y-3">
        <p>المتجر {result.store_id} · القسم {result.category_id} · نسخة الخطة {result.plan_revision}</p>
        <p>بدأت القراءة: {result.observed_started_at} · انتهت: {result.observed_finished_at}</p>
        <p>{result.category_traversal_complete ? "اكتمل تصفح القسم في إعادة القراءة." : "إعادة تصفح القسم جزئية؛ الأرقام تخص المنتجات المطلوبة التي رُصدت فقط."} القراءات لا تشكل لقطة متزامنة ثابتة، ونسخة المزود غير مثبتة.</p>
        {result.products.map((product) => <article key={product.salla_product_id} data-native-product-id={product.salla_product_id} className="rounded border p-3">
            <h4 className="font-bold">المنتج {product.salla_product_id}</h4>
            <p>{STATUS[product.status]} · علم التوفر: {product.is_available === null ? "غير معلوم" : product.is_available ? "نعم بحسب المصدر" : "لا بحسب المصدر"}</p>
            <p>وقت رصد المنتج: {product.observed_at}</p>
            <p data-parent-stock>مخزون المنتج الأب: <Stock value={product.parent_stock} /></p>
            <p>{product.simple_product_observed ? "رُصدت بنية منتج بسيط مع خيارات ومتغيرات فارغة؛ لا يثبت ذلك الجاهزية." : "لا تُستخدم كمية المنتج الأب بدل كميات متغيراته."}</p>
            <p>{product.variant_traversal_complete ? "اكتمل تصفح صفحات المتغيرات ضمن القراءة." : "تصفح المتغيرات جزئي؛ قد توجد متغيرات لم تظهر."} الصفحات المقروءة: {product.native_variant_page_digests.length}.</p>
            {product.variants.length > 0 ? <div className="overflow-x-auto"><table className="w-full text-right"><caption className="text-right">مشاهدات المتغيرات مستقلة عن كمية المنتج الأب</caption><thead><tr><th>المتغير</th><th>مخزونه المرصود</th><th>تسميات الخيارات الخام</th></tr></thead><tbody>
                {product.variants.map((variant) => <tr key={variant.variant_id} data-native-variant-id={variant.variant_id}><td>{variant.variant_id}</td><td><Stock value={variant.stock} /></td><td>
                    {variant.source_option_labels === null ? "ربط تسميات الخيارات غير مثبت" : !variant.source_option_labels.length ? "لا توجد تسميات مرصودة لهذا المتغير" : <>
                        {variant.source_option_labels.slice(0, 10).map((label) => <p key={label.value_id}>{label.option_label ?? `الخيار ${label.option_id}`}: {label.value_label ?? `القيمة ${label.value_id}`} · وسم النفاد: {label.is_out_of_stock === null ? "غير معلوم" : label.is_out_of_stock ? "نعم" : "لا"}</p>)}
                        {variant.source_option_labels.length > 10 && <p>تُعرض أول 10 تسميات من {variant.source_option_labels.length}.</p>}
                    </>}
                </td></tr>)}
            </tbody></table></div> : <p>لم تظهر متغيرات في الصفحات المقروءة.</p>}
            <p>التسميات كما وردت من المصدر؛ لا تثبت تصنيف المقاسات أو تغطيتها أو قابلية الشراء.</p>
            <p>{product.blocking_reasons.map((reason) => BLOCKERS[reason]).join(" · ")}</p>
        </article>)}
        <p>مدة التجهيز غير معلومة، وربط Merchant Center والأهلية الإعلانية غير مثبتين. لا يمنح هذا الفحص اختيارًا إعلانيًا أو حفظًا للخطة أو تفويض تشغيل.</p>
    </div>;
}
function failureMessage(failure) {
    const detail = failure?.response?.data?.detail;
    if (detail?.reason === "permission_unproved") return "لم تثبت صلاحية قراءة المخزون لهذا الطلب؛ لا يثبت ذلك فقدان اتصال المتجر. لم تتغير إعدادات الربط.";
    if (detail?.reason === "native_read_unavailable") return "تعذرت قراءة مخزون المصدر الآن؛ يمكن إعادة المحاولة، ولم تتغير إعدادات الربط.";
    if (detail?.code === "google_commercial_plan_revision_conflict" || failure?.response?.status === 404) return "تغيرت الخطة أو لم تعد متاحة؛ أعد تحميل الخطة ومعاينة القسم.";
    return "لم تثبت معاينة المخزون ضمن هذا الطلب. أعد معاينة القسم وتحقق من المنتجات المطلوبة؛ لا توجد نتيجة تمنح أهلية الإعلان.";
}
function Request({ plan, category, selected }) {
    const [result, setResult] = useState(null), [busy, setBusy] = useState(false), [locked, setLocked] = useState(false), [error, setError] = useState("");
    const mounted = useRef(true), request = useRef(null), pending = useRef(false);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
    async function read() {
        if (!selected.length || pending.current || locked) return;
        pending.current = true; setBusy(true); setResult(null); setError(""); request.current = new AbortController();
        try { const data = await getGoogleNativeInventoryPreview(plan, category, selected, { signal: request.current.signal }); if (mounted.current) setResult(data); }
        catch (failure) { if (mounted.current && !isGoogleRequestCancelled(failure)) {
            setLocked(failure?.response?.status === 404 || failure?.response?.data?.detail?.code === "google_commercial_plan_revision_conflict" || failure?.code === "GOOGLE_NATIVE_INVENTORY_PREVIEW_UNPROVED");
            setError(failureMessage(failure));
        } } finally { pending.current = false; if (mounted.current) setBusy(false); }
    }
    return <><button type="button" disabled={!selected.length || busy || locked} onClick={read} className="rounded border px-3 py-2 disabled:opacity-40">{busy ? "جارٍ فحص المخزون المرصود…" : "فحص مخزون المنتجات المحددة فقط"}</button>{error && <p role="alert">{error}</p>}{result && <Observations result={result} />}</>;
}
function Panel({ plan, category, products }) {
    const [selected, setSelected] = useState([]);
    function toggle(id) { setSelected((old) => old.includes(id) ? old.filter((value) => value !== id) : old.length < 5 ? [...old, id] : old); }
    return <section dir="rtl" aria-label="فحص مخزون المنتجات المرصودة" className="mt-3 rounded border p-3 text-sm leading-7">
        <h3 className="font-bold">فحص مخزون المنتجات المرصودة</h3>
        <p>حدد حتى خمسة منتجات لفحص مخزونها فقط، ثم اطلب القراءة. سيعيد الخادم التحقق من القسم والاستبعادات؛ لا تُضاف المنتجات إلى الخطة ولا تُختار للإعلان.</p>
        {!products.length && <p>لا توجد منتجات مرصودة يمكن طلب فحصها.</p>}
        <div className="flex flex-wrap gap-3">{products.map((product) => <label key={product.salla_product_id}><input type="checkbox" aria-label={`فحص مخزون المنتج ${product.salla_product_id}`} checked={selected.includes(product.salla_product_id)} disabled={product.excluded || selected.length >= 5 && !selected.includes(product.salla_product_id)} onChange={() => toggle(product.salla_product_id)} /> المنتج {product.salla_product_id}{product.excluded ? " · مستبعد في الخطة" : ""}</label>)}</div>
        <p>المحدد للفحص: {selected.length} من 5.</p>
        <Request key={JSON.stringify(selected)} plan={plan} category={category} selected={selected} />
    </section>;
}
export default function GoogleNativeInventoryPreview({ plan, category, isOwner, planDirty = false }) {
    if (isOwner !== true) return null;
    if (planDirty) return <p dir="rtl">احفظ الخطة أو أعد تحميل نسختها ومعاينة القسم قبل فحص المخزون.</p>;
    let products; try { products = nativeInventoryOptions(plan, category); } catch { return <p dir="rtl">راجع معاينة القسم ومعرفات الاستبعاد الرقمية في الخطة قبل فحص المخزون؛ النطاق الحالي غير مثبت.</p>; }
    return <Panel key={JSON.stringify([plan.plan_id, plan.revision, plan.fields, category])} plan={plan} category={category} products={products} />;
}
