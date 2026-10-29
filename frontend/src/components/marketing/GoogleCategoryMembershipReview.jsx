import { useEffect, useRef, useState } from "react";
import { categoryReviewScope, getGoogleCategoryMembershipReview } from "../../services/googleCategoryMembershipReview";
import { isGoogleRequestCancelled } from "../../services/googleAds";
function Panel({ plan }) {
    const [result, setResult] = useState(null), [busy, setBusy] = useState(false), [locked, setLocked] = useState(false), [error, setError] = useState("");
    const mounted = useRef(true), inflight = useRef(false), request = useRef(null);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
    async function read() {
        if (inflight.current || locked) return;
        inflight.current = true; setBusy(true); setResult(null); setError(""); request.current = new AbortController();
        try { const data = await getGoogleCategoryMembershipReview(plan, { signal: request.current.signal }); if (mounted.current) setResult(data); }
        catch (failure) { if (mounted.current && !isGoogleRequestCancelled(failure)) { setLocked([404, 409].includes(failure?.response?.status) || failure?.code === "GOOGLE_CATEGORY_REVIEW_UNPROVED"); setError("تعذرت مراجعة الأقسام لهذه النسخة؛ أعد تحميل الخطة. لم تنفذ أي تغييرات."); } }
        finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    return <section dir="rtl" aria-label="مراجعة عضوية أقسام المنتجات" className="rounded-xl border border-slate-200 p-4 text-sm leading-7"><h3 className="font-bold">مراجعة عضوية أقسام المنتجات</h3>
        <p>المنتجات المثبتة غير المستبعدة فقط. إثبات إيجابي داخل لقطة سلة المحفوظة؛ لا يحصر كل منتجات القسم ولا يثبت مخزونًا أو ربحية أو ملاءمة الموسم أو ربط كتالوجات الإعلان.</p>
        <button type="button" disabled={busy || locked} onClick={read} className="rounded border px-3 py-2 disabled:opacity-40">{busy ? "جارٍ مراجعة الأقسام…" : "مراجعة أقسام منتجات الخطة"}</button>
        {error && <p role="alert">{error}</p>}
        {result && <div><p>نسخة الخطة: {result.plan_revision} · وقت المراجعة: {result.observed_at}</p>
            {!result.review.category_ids_reviewed.length && <p>لا توجد معرفات أقسام محددة في الخطة؛ رابط القسم وحده لا يُحوّل إلى معرف مفترض.</p>}
            {result.review.category_url === null && <p>رابط القسم اختياري ولم يرفق؛ المراجعة بمعرفات الأقسام.</p>}
            {result.review.products.length === 0 && <p>لا توجد منتجات مثبتة غير مستبعدة في هذه النسخة.</p>}
            {result.review.products.map((row) => <article key={row.product_id} className="mt-3 rounded bg-slate-50 p-2"><h4>المنتج {row.product_id}</h4>
                {row.membership === "observed_in_stored_source" ? <p>عضوية مرصودة في لقطة سلة المحفوظة: {row.matched_category_ids.join("، ")} · بتاريخ {row.observed_at}<br />معرف ميزان: {row.mezan_product_id} · معرف سلة: {row.salla_product_id}</p> : <p>{row.membership === "unproved" ? "تعذر إثبات العضوية من المصدر الحالي." : "لم تظهر عضوية الأقسام المحددة في اللقطة؛ لا يثبت ذلك عدم العضوية في الكتالوج البعيد."}</p>}
                {row.catalog_visibility === "hidden" && <p>منتج مخفي؛ لا يُرشح للإعلان.</p>}
                {row.catalog_visibility === "out_of_stock" && <p>نفاد مخزون مسجل؛ لا يُرشح للإعلان.</p>}
                {row.category_url_status === "explicit_url_observed" && <p>رابط القسم مطابق لحقل صريح في لقطة المصدر للمعرفات: {row.category_url_matched_ids.join("، ")}؛ لم تُختبر الوجهة الحية.</p>}
                {row.category_url_status === "not_verified" && <p>مطابقة رابط القسم بمعرفه غير مثبتة؛ لم يُستنتج الرابط من الاسم أو المعرف.</p>}
            </article>)}
            <p>معرفات كتالوجات جوجل وسناب وتيك توك وميتا غير مثبتة بهذه القراءة. لا حفظ أو اختيار تلقائي أو تشغيل حملة.</p>
        </div>}
    </section>;
}
export default function GoogleCategoryMembershipReview({ plan, isOwner, planDirty = false }) {
    if (!isOwner) return null;
    if (planDirty) return <p dir="rtl">احفظ الخطة أو أعد تحميل نسختها قبل مراجعة الأقسام.</p>;
    try { categoryReviewScope(plan); } catch { return <p dir="rtl">معرفات الأقسام والمنتجات تحتاج مراجعة في الخطة المحفوظة.</p>; }
    return <Panel key={JSON.stringify([plan.plan_id, plan.revision, plan.fields])} plan={plan} />;
}
