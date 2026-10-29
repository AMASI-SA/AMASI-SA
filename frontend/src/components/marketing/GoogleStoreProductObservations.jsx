import { useEffect, useRef, useState } from "react";
import { getGoogleStoreProductObservations } from "../../services/googleStoreProductObservations";
import { pinnedCatalogReviewScope } from "../../services/googleCatalogReview";
import { isGoogleRequestCancelled } from "../../services/googleAds";

const LABELS = { last_14_days: "آخر 14 يومًا", previous_14_days: "الـ14 يومًا السابقة", last_30_days: "آخر 30 يومًا", last_90_days: "آخر 90 يومًا" };
function Panel({ plan }) {
    const [result, setResult] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
    const mounted = useRef(true), request = useRef(null), inflight = useRef(false);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
    async function read() {
        if (inflight.current) return;
        inflight.current = true; setBusy(true); setResult(null); setError("");
        request.current = new AbortController();
        try { const response = await getGoogleStoreProductObservations(plan, { signal: request.current.signal }); if (mounted.current) setResult(response); }
        catch (failure) { if (mounted.current && !isGoogleRequestCancelled(failure)) setError("تعذرت مراجعة مشاهدات الطلبات لهذه النسخة. أعد تحميل الخطة؛ لم تُنفّذ أي تغييرات."); }
        finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    const out = result?.observations;
    return <section dir="rtl" aria-label="مشاهدات سطور منتجات الطلبات المحفوظة" className="rounded-xl border border-slate-200 p-4 text-sm leading-7">
        <h3 className="font-bold">مشاهدات سطور منتجات الطلبات المحفوظة</h3>
        <p>حد أدنى لما ظهر في اللقطات المؤهلة المحفوظة؛ ليس إجمالي مبيعات المتجر أو صافي الكميات بعد الإرجاع. لا نحسب نمو المبيعات أو الربحية أو نرتب المنتجات الفائزة من هذه القراءة.</p>
        <button type="button" disabled={busy} onClick={read} className="rounded border px-3 py-2 disabled:opacity-40">{busy ? "جارٍ قراءة المشاهدات…" : "قراءة مشاهدات منتجات الخطة"}</button>
        {error && <p role="alert">{error}</p>}
        {out && <div><p>وقت القراءة الثابت: {out.as_of} · الفترات بتوقيت الرياض. النوافذ متداخلة ولا تُجمع.</p>
            <p>حساب المنصة الأصلي غير مثبت؛ نطاق القراءة متجر المالك داخل ميزان. العملات منفصلة، وتحويلات المنصات لا تُضاف إلى طلبات المتجر.</p>
            <p>المصدر لقطة خام قد تكون مدمجة أو قديمة؛ اكتمال الطلبات وتسوية الإرجاعات والتكاليف التاريخية غير مثبتة.</p>
            {out.truncated && <p role="status">تجاوزت القراءة الحد؛ حُجبت المقادير الجزئية ولا تعني صفر مبيعات.</p>}
            {out.products.length === 0 && <p>لا توجد منتجات مثبتة غير مستبعدة في هذه الخطة.</p>}
            {out.products.map((product) => <article key={product.product_id} className="mt-3 rounded bg-slate-50 p-2"><h4>المنتج {product.product_id}</h4>
                {product.currency_groups.length === 0 && <p>لا توجد مشاهدات مؤهلة ضمن القراءة المحدودة؛ لا يعني ذلك عدم وجود مبيعات.</p>}
                {product.currency_groups.map((group) => <div key={group.currency_code}><p>عملة الطلب: {group.currency_code}</p><div className="overflow-x-auto"><table className="w-full text-right"><thead><tr><th>الفترة</th><th>طلبات مرصودة</th><th>سطور مرصودة</th><th>كميات خام مرصودة</th></tr></thead><tbody>
                    {Object.entries(LABELS).map(([name, label]) => { const row = group.windows[name], period = out.windows[name]; return <tr key={name}><td>{label} ({period.from} — {period.to})</td><td>{row.observed_orders ?? "غير مرصود"}</td><td>{row.observed_lines ?? "غير مرصود"}</td><td>{row.observed_units ?? "غير مرصود"}</td></tr>; })}
                </tbody></table></div></div>)}
            </article>)}
            {result.catalog_missing_or_unverified_ids.length > 0 && <p>هوية الكتالوج الحالية تحتاج مراجعة للمعرفات: {result.catalog_missing_or_unverified_ids.join("، ")}. هذه المشاهدات لا تثبت أهلية الإعلان.</p>}
            <p>لا تحفظ هذه القراءة تعديلًا ولا تمنح تفويضًا أو تُشغّل حملة.</p>
        </div>}
    </section>;
}
export default function GoogleStoreProductObservations({ plan, isOwner, planDirty = false }) {
    if (!isOwner) return null;
    if (planDirty) return <p dir="rtl">احفظ الخطة أو أعد تحميل نسختها قبل مراجعة مشاهدات الطلبات.</p>;
    try { pinnedCatalogReviewScope(plan); } catch { return <p dir="rtl">احفظ خطة بمعرفات منتجات صحيحة قبل مراجعة المشاهدات.</p>; }
    return <Panel key={JSON.stringify([plan.plan_id, plan.revision, plan.fields])} plan={plan} />;
}
