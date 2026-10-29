import { useEffect, useRef, useState } from "react";
import { getGoogleLinkedCampaignEvidence, linkedCampaignScope } from "../../services/googleLinkedCampaignEvidence";
import { isGoogleRequestCancelled } from "../../services/googleAds";

const PROVIDERS = { snapchat_ads: "سناب", meta: "ميتا", tiktok: "تيك توك" };
const WINDOWS = { last14: "آخر 14 يومًا", previous14: "الـ14 يومًا السابقة", last30: "آخر 30 يومًا", last90: "آخر 90 يومًا" };
const MISSING = {
    persisted_native_timezone: "منطقة الحساب الزمنية غير مثبتة في المصدر المحفوظ",
    atomic_campaign_snapshot_completeness: "اكتمال لقطة الحملة غير مثبت",
    persisted_native_currency: "عملة الحساب الأصلية غير مثبتة في المصدر المحفوظ",
};
const groupKey = (row) => JSON.stringify([row.provider, row.account_id, row.campaign_id, row.currency, row.timezone, row.attribution_key]);
const metric = (value) => value === null ? "غير مثبت" : String(value);

function CampaignContexts({ result }) {
    const groups = new Map(), windowGroups = new Map();
    for (const context of result.campaign_contexts) {
        const key = groupKey(context);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(context);
    }
    for (const window of result.campaign_windows) {
        const key = groupKey(window);
        if (!windowGroups.has(key)) windowGroups.set(key, []);
        windowGroups.get(key).push(window);
    }
    return <div className="space-y-4">
        {result.campaign_contexts.length === 0 && <p>لا يوجد سياق يومي مؤهل للحملات ضمن القراءة المحدودة؛ لا يعني ذلك عدم وجود إنفاق أو مبيعات.</p>}
        {[...groups].map(([key, contexts]) => {
            const first = contexts[0], windows = windowGroups.get(key);
            return <section key={key} className="rounded border border-slate-200 p-3" aria-label={`سياق حملة ${first.campaign_id} · ${first.currency}`}>
                <h4 className="font-bold">{PROVIDERS[first.provider]} · الحساب {first.account_id} · الحملة {first.campaign_id}</h4>
                <p>العملة: {first.currency} · منطقة الحساب الزمنية: {first.timezone}</p>
                <p>إسناد المنصة: {first.action_report_time === "conversion" ? "وقت التحويل" : "وقت الظهور"} · {Object.entries(first.attribution_windows).map(([name, value]) => `${name}: ${value}`).join(" · ")}</p>
                <div className="overflow-x-auto"><table className="w-full text-right"><caption className="text-right">سياق الحملة اليومي — كل صف يُعرض مرة واحدة مهما تعددت المنتجات المرتبطة</caption><thead><tr><th>اليوم المحلي</th><th>الإنفاق المبلغ من المنصة</th><th>مشتريات مبلغ عنها من المنصة</th><th>قيمة المشتريات المبلغ عنها</th><th>مراجع المنتجات</th></tr></thead><tbody>
                    {contexts.map((context) => <tr key={context.context_id} data-context-id={context.context_id}>
                        <td>{context.date}</td><td>{metric(context.metrics.spend_native)} {first.currency}</td>
                        <td>{metric(context.metrics.provider_reported_purchases)}</td><td>{metric(context.metrics.provider_reported_purchase_value_native)} {first.currency}</td>
                        <td>{[...new Set(context.association_proofs.map((proof) => proof.product_id))].join("، ")}</td>
                    </tr>)}
                </tbody></table></div>
                <details className="mt-2"><summary>تواريخ اللقطات وفترات التغطية الجزئية</summary>
                    {contexts.map((context) => <p key={context.context_id}>{context.date}: لوحظت البيانات في {context.observed_at}، واكتملت اللقطة في {context.snapshot_finished_at}. حداثتها الحية غير مثبتة.</p>)}
                    <div className="overflow-x-auto"><table className="w-full text-right"><caption className="text-right">نوافذ مستقلة بتوقيت الحساب؛ لا نجمع مقاديرها أو نستنتج النمو</caption><thead><tr><th>الفترة</th><th>الأيام المرصودة</th><th>الأيام غير المرصودة</th></tr></thead><tbody>
                        {windows.map((window) => <tr key={window.window_id}><td>{WINDOWS[window.window_id]} ({window.date_from} — {window.date_to})</td><td>{window.observed_context_days} من {window.expected_days}</td><td>{window.missing_dates.length ? window.missing_dates.join("، ") : "لا توجد فجوة يومية في الصفوف المعروضة؛ اكتمال المصدر يبقى غير مثبت"}</td></tr>)}
                    </tbody></table></div>
                </details>
            </section>;
        })}
    </div>;
}

function Panel({ plan }) {
    const [result, setResult] = useState(null), [busy, setBusy] = useState(false), [locked, setLocked] = useState(false), [error, setError] = useState("");
    const mounted = useRef(true), request = useRef(null), inflight = useRef(false);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
    async function read() {
        if (inflight.current || locked) return;
        inflight.current = true; setBusy(true); setResult(null); setError(""); request.current = new AbortController();
        try { const data = await getGoogleLinkedCampaignEvidence(plan, { signal: request.current.signal }); if (mounted.current) setResult(data); }
        catch (failure) {
            if (mounted.current && !isGoogleRequestCancelled(failure)) {
                setLocked([404, 409].includes(failure?.response?.status) || failure?.code === "GOOGLE_LINKED_CAMPAIGN_EVIDENCE_UNPROVED");
                setError("تعذرت مراجعة سياق الحملات لهذه النسخة؛ أعد تحميل الخطة. لم تنفذ أي تغييرات.");
            }
        } finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    return <section dir="rtl" aria-label="سياق الحملات المرتبطة بمنتجات الخطة" className="rounded-xl border border-slate-200 p-4 text-sm leading-7">
        <h3 className="font-bold">سياق الحملات المرتبطة بمنتجات الخطة</h3>
        <p>قراءة جزئية لبيانات الحملات المحفوظة. الربط يوضح المنتجات المقصودة بالحملة؛ لا يثبت مطابقة كتالوج الإعلان أو أن المنتج حقق هذه النتائج.</p>
        <button type="button" onClick={read} disabled={busy || locked} className="rounded border px-3 py-2 disabled:opacity-40">{busy ? "جارٍ قراءة سياق الحملات…" : "قراءة سياق الحملات المرتبطة"}</button>
        {error && <p role="alert">{error}</p>}
        {result && <div className="mt-3 space-y-3">
            <p>نسخة الخطة: {result.plan_revision} · وقت القراءة: {result.as_of}</p>
            <p>العملات والحسابات وأيامها ونوافذ إسنادها منفصلة. مشتريات المنصة ليست طلبات متجر مستقلة، ولا توزع على كل منتج. الربحية غير مثبتة، والنتائج لا تضمن نجاحًا على جوجل.</p>
            {!result.products.length && <p>لا توجد منتجات مثبتة غير مستبعدة في هذه النسخة.</p>}
            <details><summary>روابط المنتجات والفجوات المتبقية</summary>
                {result.products.map((product) => <article key={product.product_id} className="mt-2 rounded bg-slate-50 p-2"><h4 className="font-bold">المنتج {product.product_id}</h4>
                    {!product.associations.length && <p>لا يوجد ربط حالي مثبت في المصدر المحفوظ. لا يثبت ذلك غياب أي ارتباط بعيد.</p>}
                    {product.associations.map((association) => <p key={JSON.stringify([association.provider, association.account_id, association.campaign_id, association.event_id])}>
                        {PROVIDERS[association.provider]} · الحساب {association.account_id} · الحملة {association.campaign_id}: ربط بالمنتج المقصود فقط.
                        {association.provider === "snapchat_ads" ? ` أيام سياق مؤهلة لهذا الربط: ${association.observed_context_days}.` : ` ${association.missing.map((key) => MISSING[key] || "أدلة المصدر غير مكتملة").join("؛ ")}.`}
                    </p>)}
                    <p>مراجع السياق اليومي لهذا المنتج: {product.context_refs.length}. الأرقام تظهر مرة واحدة في صفوف الحملات أدناه.</p>
                </article>)}
            </details>
            <CampaignContexts result={result} />
            <p>اللقطات قابلة للتغير، ولا يثبت هذا العرض اكتمال المصدر أو ترتيب المنتجات. لا حفظ للخطة أو ميزانية معتمدة أو تفويض تشغيل.</p>
        </div>}
    </section>;
}

export default function GoogleLinkedCampaignEvidence({ plan, isOwner, planDirty = false }) {
    if (isOwner !== true) return null;
    if (planDirty) return <p dir="rtl">احفظ الخطة أو أعد تحميل نسختها قبل مراجعة سياق الحملات.</p>;
    try { linkedCampaignScope(plan); } catch { return <p dir="rtl">راجع معرفات المنتجات ونسخة الخطة المحفوظة قبل قراءة سياق الحملات.</p>; }
    return <Panel key={JSON.stringify([plan.plan_id, plan.revision, plan.fields])} plan={plan} />;
}
