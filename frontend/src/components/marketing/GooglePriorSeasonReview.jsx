import { useEffect, useRef, useState } from "react";
import { getGooglePriorSeasonReview, priorSeasonReviewScope } from "../../services/googlePriorSeasonReview";
import { isGoogleRequestCancelled } from "../../services/googleAds";

function Panel({ plan, ownerId }) {
    const [result, setResult] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
    const mounted = useRef(true), request = useRef(null), inflight = useRef(false);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
    async function read() {
        if (inflight.current) return;
        inflight.current = true; setBusy(true); setResult(null); setError(""); request.current = new AbortController();
        try { const response = await getGooglePriorSeasonReview(plan, { ownerId, signal: request.current.signal }); if (mounted.current) setResult(response); }
        catch (failure) { if (mounted.current && !isGoogleRequestCancelled(failure)) setError("تعذرت مراجعة هذه النسخة؛ أعد تحميل الخطة وتحقق من صلاحية المالك والفترة المحفوظة."); }
        finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    const prior = plan.fields.prior_season_context, out = result?.observations;
    return <section dir="rtl" aria-label="مراجعة مشاهدات الموسم السابق" className="rounded-xl border border-slate-200 p-4 text-sm leading-7">
        <h3 className="font-bold">مشاهدات الموسم السابق المحفوظ</h3>
        <p>{prior.season_label}: {prior.date_from} — {prior.date_to} · بتوقيت الرياض</p>
        <p>سبب اختيار الفترة: {prior.alignment_note}</p>
        {prior.calendar_reference_text && <p>مرجع ذكره المالك، لم يُتحقق منه: {prior.calendar_reference_text}</p>}
        <p>هذه فترة اختارها المالك؛ مطابقة الموسم الهجري أو العام السابق غير مثبتة. لا نُزيح التواريخ تلقائيًا.</p>
        <button type="button" disabled={busy} onClick={read} className="rounded border px-3 py-2 disabled:opacity-40">{busy ? "جارٍ قراءة الموسم السابق…" : "قراءة مشاهدات الفترة المحفوظة"}</button>
        {error && <p role="alert">{error}</p>}
        {out && <div><p>وقت القراءة الثابت: {out.as_of} · مراجعة المالك المحفوظة: {result.owner_review_provenance.reviewed_at} · نسخة الخطة {result.plan_revision}</p>
            <p>حد أدنى لمشاهدات السطور الخام المؤهلة؛ ليس إجمالي المبيعات أو صافي المرتجعات. اكتمال المصدر والتكاليف التاريخية غير مثبتين؛ لا نحسب النمو أو الربحية ولا نرتب المنتجات الفائزة.</p>
            <p>العملات منفصلة، وحساب المنصة الأصلي غير مثبت. تحويلات الحملات لا تُضاف إلى طلبات المتجر، ولا تُجمع هذه الفترة مع النوافذ الأخرى.</p>
            {out.truncated && <p role="status">تجاوزت القراءة الحد؛ حُجبت المقادير الجزئية ولا تعني صفر مبيعات.</p>}
            {out.products.length === 0 && <p>لا توجد منتجات مثبتة غير مستبعدة في هذه الخطة.</p>}
            {out.products.map((product) => <article key={product.product_id} className="mt-3 rounded bg-slate-50 p-2"><h4>المنتج {product.product_id}</h4>
                {product.currency_groups.length === 0 && <p>لا توجد مشاهدات مؤهلة في القراءة المحدودة؛ لا يعني ذلك عدم وجود مبيعات.</p>}
                {product.currency_groups.map((group) => { const row = group.windows.owner_reviewed_prior_season; return <div key={group.currency_code}><p>عملة الطلب: {group.currency_code}</p><table className="w-full text-right"><thead><tr><th>طلبات مرصودة</th><th>سطور مرصودة</th><th>كميات خام مرصودة</th></tr></thead><tbody><tr><td>{row.observed_orders ?? "غير مرصود"}</td><td>{row.observed_lines ?? "غير مرصود"}</td><td>{row.observed_units ?? "غير مرصود"}</td></tr></tbody></table></div>; })}
            </article>)}
            <p>القراءة لا تحفظ تعديلًا ولا تمنح تفويض إنفاق أو تشغّل حملة.</p>
        </div>}
    </section>;
}
export default function GooglePriorSeasonReview({ plan, isOwner, ownerId, planDirty = false }) {
    if (isOwner !== true) return null;
    if (planDirty) return <p dir="rtl">احفظ الخطة أو أعد تحميلها قبل مراجعة الموسم السابق.</p>;
    if (!plan?.fields?.prior_season_context) return <p dir="rtl">احفظ فترة الموسم السابق وسبب اختيارها في الخطة قبل قراءة مشاهداتها.</p>;
    try { priorSeasonReviewScope(plan, ownerId); } catch { return <p dir="rtl">تعذر إثبات نسخة خطة محفوظة وفترة صحيحة لمراجعة الموسم السابق.</p>; }
    return <Panel key={JSON.stringify([ownerId, plan.plan_id, plan.revision, plan.fields])} plan={plan} ownerId={ownerId} />;
}
