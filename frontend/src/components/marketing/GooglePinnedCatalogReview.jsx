import { useEffect, useRef, useState } from "react";
import { googleErrorMessage, isGoogleRequestCancelled } from "../../services/googleAds";
import { getGoogleCatalogReview, pinnedCatalogReviewScope } from "../../services/googleCatalogReview";

const BUTTON = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40";

function rowStatus(row, proof) {
    if (row.verification !== "verified") return "صف الكتالوج غير مثبت";
    if (row.status === "hidden") return "مخفي في الكتالوج · لا يُرشّح للإعلان";
    if (row.status === "out_of_stock") return "نفاد مخزون مسجل · لا يُرشّح للإعلان";
    if (row.status === "available" && proof?.state === "available") return `مخزون مثبت من تفاصيل محفوظة بتاريخ ${proof.observed_at} · لقطة محدودة حتى 50 متغيرًا؛ اكتمال التغطية البعيدة غير مثبت`;
    return "هوية صف الكتالوج مثبتة؛ التوفر القابل للشراء غير مثبت";
}

function ReviewPanel({ plan }) {
    const [result, setResult] = useState(null), [busy, setBusy] = useState(false);
    const [error, setError] = useState(""), [locked, setLocked] = useState(false);
    const mounted = useRef(true), inflight = useRef(false), controller = useRef(null);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; controller.current?.abort(); };
    }, []);
    async function review() {
        if (busy || locked || inflight.current) return;
        inflight.current = true; setBusy(true); setError(""); setResult(null);
        const request = new AbortController(); controller.current = request;
        try {
            const response = await getGoogleCatalogReview(plan, { signal: request.signal });
            if (mounted.current) setResult(response);
        } catch (failure) {
            if (mounted.current && !isGoogleRequestCancelled(failure)) {
                const stale = failure?.response?.status === 409 || failure?.response?.status === 404 || failure?.code === "GOOGLE_CATALOG_REVIEW_UNPROVEN";
                setLocked(stale);
                setError(stale ? "تغيرت الخطة أو تعذر إثبات مطابقة المنتجات لنسختها. أعد تحميل الخطة المحفوظة قبل المراجعة." : googleErrorMessage(failure, "تعذرت قراءة الكتالوج. لم تُنفّذ أي تغييرات."));
            }
        } finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    return <section dir="rtl" aria-label="مراجعة المنتجات المثبتة في الخطة" className="rounded-xl border border-slate-200 bg-white p-4 text-sm leading-7">
        <h3 className="font-bold">مراجعة المنتجات المثبتة في الخطة</h3>
        <p>قراءة لصفوف كتالوج ميزان فقط · نسخة الخطة: {plan.revision}</p>
        <p className="text-slate-600">تُستبعد المعرفات المستبعدة من الخطة. ملاءمة المقاسات ووقت التجهيز وملاءمة الموسم غير مثبتة. هذه القراءة لا تثبت ربحية المنتج أو نجاحه أو جاهزيته لحملة إعلانية.</p>
        <button type="button" className={`${BUTTON} mt-3`} disabled={busy || locked} onClick={review}>قراءة منتجات الخطة المحفوظة</button>
        {busy && <p role="status">جارٍ قراءة المنتجات المثبتة…</p>}
        {error && <p role="alert" className="mt-3 text-red-800">{error}</p>}
        {result && <div className="mt-3 space-y-3">
            {result.catalog.products.length === 0 && result.catalog.missing_or_unverified_product_ids.length === 0 && <p role="status">لا توجد منتجات مثبتة غير مستبعدة في هذه النسخة.</p>}
            {result.catalog.products.length > 0 && <ul className="space-y-2">{result.catalog.products.map((row) => <li key={row.product_id} className="rounded-lg bg-slate-50 p-2"><span className="font-bold">المنتج {row.product_id}</span><p>{rowStatus(row, result.catalog.inventory_reviews?.find((proof) => proof.product_id === row.product_id))}</p></li>)}</ul>}
            {result.catalog.missing_or_unverified_product_ids.length > 0 && <div className="rounded-lg bg-amber-50 p-3"><p className="font-bold">منتجات مفقودة أو بياناتها غير مثبتة</p><p>{result.catalog.missing_or_unverified_product_ids.join("، ")}</p><p>قد تكون المزامنة قديمة أو هوية الصف غير مثبتة؛ لا تعني النتيجة أن المنتج محذوف من سلة.</p></div>}
            <p className="text-slate-600">عضوية القسم وتوفر المقاسات ورابط المنتج وتكلفته لم تُثبت بهذه القراءة. لا تُشغّل هذه المراجعة حملات ولا تحفظ تعديلًا.</p>
        </div>}
    </section>;
}

export default function GooglePinnedCatalogReview({ plan, isOwner, planDirty = false }) {
    if (isOwner !== true) return null;
    if (planDirty) return <section dir="rtl" aria-label="مراجعة المنتجات المثبتة في الخطة" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm"><p>لديك تعديلات غير محفوظة في الخطة. احفظها أو أعد تحميل نسختها المحفوظة قبل قراءة المنتجات.</p><button type="button" className={`${BUTTON} mt-3`} disabled>قراءة منتجات الخطة المحفوظة</button></section>;
    try { pinnedCatalogReviewScope(plan); } catch {
        return <p dir="rtl" role="status" className="text-sm text-slate-600">احفظ خطة بإصدار صالح ومعرفات منتجات سلة الرقمية قبل مراجعة الكتالوج.</p>;
    }
    // Include all saved fields: changes in scope without a revision bump must
    // still unmount the previous read and discard its response.
    const scopeKey = JSON.stringify([plan.plan_id, plan.revision, plan.fields]);
    return <ReviewPanel key={scopeKey} plan={plan} />;
}
