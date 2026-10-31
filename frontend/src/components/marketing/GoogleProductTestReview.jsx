import { useEffect, useRef, useState } from "react";
import { useOptionalAuth } from "../../context/AuthContext";
import { isGoogleRequestCancelled } from "../../services/googleAds";
import { getProductFitState, saveProductFit, getProductTestReview, productTestReviewScope } from "../../services/googleProductTestReview";

const DECISIONS = { suitable_for_test: "أراه ملائمًا لمراجعة اختبار الموسم", not_suitable: "غير ملائم للتوجيه الحالي", needs_review: "يحتاج مراجعة الملاءمة" };
const LANES = { supported_test_review: "مرشح لمراجعة اختبار بأدلة جزئية", exploration_test_review: "مرشح لمراجعة اختبار استكشافي", needs_owner_fit_review: "يحتاج مراجعة ملاءمة من المالك", needs_stock_review: "يحتاج مراجعة المخزون", blocked: "محجوب عن الترشيح في هذه المراجعة" };
const PERIODS = { last_14_days: "آخر 14 يومًا", previous_14_days: "الـ14 السابقة", last_30_days: "آخر 30 يومًا", last_90_days: "آخر 90 يومًا", owner_reviewed_prior_season: "الفترة السابقة التي اختارها المالك" };
const REASONS = { qualifying_saved_current_gross_lines: "سطور طلبات حالية محفوظة ومؤهلة، ضمن قراءة جزئية", qualifying_saved_prior_gross_lines_alignment_unknown: "سطور مؤهلة للفترة السابقة؛ مطابقة الموسم غير مثبتة", effective_shared_campaign_context_not_product_performance: "سياق حملة مشتركة مرتبطة بالمنتج، وليس أداءً منسوبًا للمنتج" };
const FIELDS = "rounded border px-2 py-1";
function OrderRows({ value, windows }) {
    if (!value?.currency_groups.length) return <p>لا توجد مشاهدات مؤهلة في القراءة المحدودة؛ لا يعني ذلك صفر مبيعات أو أن المنتج جديد.</p>;
    return value.currency_groups.map((group) => <div key={group.currency_code}><p>عملة الطلب: {group.currency_code} · حساب المتجر الأصلي غير مثبت</p><div className="overflow-x-auto"><table className="w-full text-right"><thead><tr><th>الفترة المستقلة</th><th>طلبات مرصودة</th><th>سطور مرصودة</th><th>كميات خام مرصودة</th></tr></thead><tbody>{Object.entries(windows).map(([key, period]) => { const row = group.windows[key]; return <tr key={key}><td>{PERIODS[key]}: {period.from} — {period.to}</td><td>{row.observed_orders ?? "غير مرصود"}</td><td>{row.observed_lines ?? "غير مرصود"}</td><td>{row.observed_units ?? "غير مرصود"}</td></tr>; })}</tbody></table></div></div>);
}
function Result({ result }) {
    return <div data-product-test-result className="space-y-3">
        <p>القراءة الثابتة: {result.as_of} · رصد المخزون: {result.native_observed_started_at} — {result.native_observed_finished_at}. المصادر لا تمثل لقطة متزامنة.</p>
        <p>ترتيب مسارات المراجعة بحسب توافر الأدلة، وليس ترتيب أداء أو ضمان نجاح على جوجل. التجربة الاستكشافية لا تثبت أن المنتج جديد أو بلا مبيعات.</p>
        {result.review_lanes.filter((lane) => lane.product_ids.length).map((lane) => <section key={lane.decision}><h4 className="font-bold">{LANES[lane.decision]}</h4>{lane.product_ids.map((id) => { const row = result.products.find((p) => p.product_id === id); return <article key={id} data-product-decision={id} className="rounded border p-3">
            <h5 className="font-bold">المنتج {id}</h5><p>{row.season_fit.reason ? `تقدير المالك: ${row.season_fit.reason}` : "ملاءمة المنتج للموسم غير مراجعة من المالك لهذه النسخة."}</p>
            <p>{row.evidence_reasons.length ? row.evidence_reasons.map((reason) => REASONS[reason]).join(" · ") : "التاريخ غير معلوم ضمن المشاهدات المؤهلة؛ لا نستنتج انعدام الطلب."}</p>
            <p>المخزون المرصود محدود بالمنتج ومتغيراته؛ كمية الأب لا تحل محل كميات المتغيرات، وقابلية الشراء والمقاسات غير مثبتة.</p>
            <OrderRows value={row.current_store_observations} windows={result.current_windows} />
            {result.prior_windows && <><p>مطابقة الفترة السابقة للموسم الهجري أو العام الماضي غير مثبتة.</p><OrderRows value={row.prior_store_observations} windows={result.prior_windows} /></>}
            {row.campaign_context_refs.length > 0 && <p>مراجع سياق الحملات المشتركة: {row.campaign_context_refs.map((ref) => ref.slice(0, 12)).join("، ")}. تُعرض مقاييس كل سياق مرة واحدة أدناه.</p>}
            {row.campaign_associations.map((a) => <p key={a.event_id}>ربط مقصود فقط: {a.provider} · الحساب {a.account_id} · الحملة {a.campaign_id}. {a.provider === "meta" ? "توقيت المصدر واكتمال اللقطة غير مثبتين." : a.provider === "tiktok" ? "عملة المصدر وتوقيته غير مثبتين." : "الإسناد للمنتج وربط الخلاصة غير مثبتين."}</p>)}
        </article>; })}</section>)}
        {result.campaign_contexts.length > 0 && <section aria-label="سياق الحملات مرة واحدة"><h4 className="font-bold">سياق الحملات مرة واحدة</h4>{result.campaign_contexts.map((c) => <div key={c.context_id} data-campaign-context={c.context_id} className="rounded border p-2"><p>{c.provider} · الحساب {c.account_id} · الحملة {c.campaign_id} · {c.currency} · {c.timezone} · {c.date}</p><p>الإسناد: {c.action_report_time} · {c.attribution_key}</p><p>إنفاق مصدر مرصود: {c.metrics.spend_native ?? "غير معلوم"} · مشتريات أبلغت بها المنصة: {c.metrics.provider_reported_purchases ?? "غير معلوم"} · قيمة مشتريات المنصة: {c.metrics.provider_reported_purchase_value_native ?? "غير معلومة"}</p><p>هذه مقاييس الحملة المشتركة، لا تُوزّع على المنتجات ولا تُضاف كطلبات متجر مستقلة.</p></div>)}</section>}
        <p>المبالغ والعملات والفترات منفصلة ولا تُجمع. السطور الخام ليست صافي المرتجعات أو إجمالي مبيعات مكتملًا. التكاليف التاريخية والربحية والنمو ومدة التجهيز والمقاسات غير معلومة.</p>
        <p>لا يوجد اعتماد إنفاق في هذه المراجعة؛ أي اختبار يحتاج تفويضًا منفصلًا وميزانية معتمدة من المالك. لا تُنشأ أو تُفعّل حملة هنا.</p>
    </div>;
}
function defaults(row) { return { decision: row.current_owner_review ? row.decision : "needs_review", reason: row.current_owner_review ? row.reason : "", ack: false }; }
function Panel({ plan, category, selected, externalBusy, onBusyChange }) {
    const [fit, setFit] = useState(null), [drafts, setDrafts] = useState({}), [uncertain, setUncertain] = useState(false), [result, setResult] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
    const mounted = useRef(true), request = useRef(null), pending = useRef(false), busyCallback = useRef(onBusyChange);
    busyCallback.current = onBusyChange;
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); busyCallback.current?.(false); }; }, []);
    const dirty = !!fit && fit.products.some((row) => { const original = defaults(row), current = drafts[row.product_id]; return !current || original.decision !== current.decision || original.reason !== current.reason; });
    async function run(work, kind) {
        if (pending.current || externalBusy) return;
        pending.current = true; setBusy(true); busyCallback.current?.(true); setError(""); setResult(null); request.current = new AbortController();
        try { const data = await work(request.current.signal); if (!mounted.current) return;
            if (kind === "read") { setFit(data); setDrafts(Object.fromEntries(data.products.map((row) => [row.product_id, defaults(row)]))); setUncertain(false); }
            else if (kind === "save") { setFit((old) => ({ ...old, products: old.products.map((row) => row.product_id === data.product_id ? { ...row, review_revision: data.review_revision, current_owner_review: true, decision: data.decision, reason: data.reason } : row) })); setDrafts((old) => ({ ...old, [data.product_id]: { decision: data.decision, reason: data.reason, ack: false } })); }
            else setResult(data);
        } catch (failure) { if (mounted.current && !isGoogleRequestCancelled(failure)) {
            setUncertain(true); setError(kind === "save" ? "لم يتأكد حفظ المراجعة وقد تكون حُفظت. اقرأ المراجعات صراحةً قبل إعادة أي محاولة؛ لن يُعاد إرسال الحفظ تلقائيًا." : "تعذرت مطابقة المراجعة الحالية. أعد قراءة المراجعات أو تحميل الخطة ومعاينة القسم قبل المحاولة.");
        } } finally { pending.current = false; if (mounted.current) { setBusy(false); busyCallback.current?.(false); } }
    }
    function edit(id, key, value) { setDrafts((old) => ({ ...old, [id]: { ...old[id], [key]: value, ...(key !== "ack" ? { ack: false } : {}) } })); setResult(null); setError(""); }
    function read() { if (pending.current || externalBusy) return; setFit(null); setDrafts({}); setUncertain(true); return run((signal) => getProductFitState(plan, category, selected, { signal }), "read"); }
    const disabled = busy || externalBusy;
    return <section dir="rtl" aria-label="مراجعة ملاءمة المنتجات لاختبار جوجل" className="mt-3 rounded border p-3 text-sm leading-7">
        <h3 className="font-bold">مراجعة ملاءمة المنتجات لاختبار جوجل</h3><p>تُستخدم المنتجات المحددة نفسها أعلاه. تقدير الملاءمة تصريح من المالك لهذه الخطة والمنتج؛ ليس تحققًا مستقلًا أو تفويض إنفاق.</p>
        <button type="button" className={FIELDS} disabled={disabled} onClick={read}>قراءة مراجعات الملاءمة المحفوظة</button>
        {error && <p role="alert">{error}</p>}{busy && <p role="status">جارٍ تنفيذ طلب المراجعة المحدد…</p>}
        {fit && fit.products.map((row) => { const value = drafts[row.product_id]; return <fieldset key={row.product_id} data-fit-product={row.product_id} disabled={disabled || uncertain} className="mt-2 rounded border p-2"><legend>ملاءمة المنتج {row.product_id} · نسخة المراجعة {row.review_revision}</legend>
            {!row.current_owner_review && <p>الملاءمة غير مراجعة أو لم تعد مثبتة لهذه النسخة؛ لا يعني ذلك أن المنتج غير مناسب.</p>}
            <label>تقدير الملاءمة <select aria-label={`ملاءمة المنتج ${row.product_id}`} className={FIELDS} value={value.decision} onChange={(e) => edit(row.product_id, "decision", e.target.value)}>{Object.entries(DECISIONS).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
            <label className="block">سبب التقدير <textarea aria-label={`سبب ملاءمة المنتج ${row.product_id}`} maxLength={1000} value={value.reason} onChange={(e) => edit(row.product_id, "reason", e.target.value)} className={FIELDS} /></label>
            <label><input type="checkbox" aria-label={`راجعت ملاءمة المنتج ${row.product_id}`} checked={value.ack} onChange={(e) => edit(row.product_id, "ack", e.target.checked)} /> راجعت هذا التقدير وسببه؛ لا أمنح إنفاقًا.</label>
            <button type="button" className={FIELDS} disabled={disabled || uncertain || !value.ack || !value.reason.trim()} onClick={() => run((signal) => saveProductFit(plan, category, selected, row.product_id, row.review_revision, value.decision, value.reason.trim(), value.ack, { signal }), "save")}>حفظ مراجعة المنتج {row.product_id}</button>
        </fieldset>; })}
        {dirty && <p>توجد تعديلات ملاءمة غير محفوظة. احفظها أو أعد قراءة المراجعات للتراجع عنها قبل قراءة الترشيحات.</p>}
        <button type="button" className={FIELDS} disabled={disabled || !fit || uncertain || dirty} onClick={() => run((signal) => getProductTestReview(plan, category, selected, fit, { signal }), "preview")}>مراجعة مرشحي الاختبار المحددين</button>
        <p>التكاليف التاريخية والربحية ومدة التجهيز والمقاسات غير مثبتة. لا توجد ميزانية أو صلاحية إطلاق مستنتجة من هذه الخطوة.</p>
        {result && <Result result={result} />}
    </section>;
}
export default function GoogleProductTestReview({ plan, category, selectedProductIds, isOwner, planDirty = false, externalBusy = false, onBusyChange }) {
    const auth = useOptionalAuth(), generation = useRef({ user: auth?.user, value: 0 });
    if (generation.current.user !== auth?.user) generation.current = { user: auth?.user, value: generation.current.value + 1 };
    if (isOwner !== true || auth?.authStatus !== "authenticated" || auth?.loading || auth?.user?.role !== "owner" || typeof auth.user.id !== "string" || !auth.user.id) return null;
    if (planDirty) return <p dir="rtl">احفظ الخطة وأعد معاينة القسم قبل مراجعة المنتجات.</p>;
    if (!selectedProductIds?.length) return <p dir="rtl">حدد من منتج واحد إلى خمسة من الخيارات المرصودة أعلاه لمراجعة الاختبار.</p>;
    try { productTestReviewScope(plan, category, selectedProductIds); } catch { return <p dir="rtl">نطاق المنتجات والقسم والخطة غير مثبت لهذه المراجعة.</p>; }
    return <Panel key={JSON.stringify([generation.current.value, auth.user.id, plan.plan_id, plan.revision, plan.fields, category, selectedProductIds])} plan={plan} category={category} selected={selectedProductIds} externalBusy={externalBusy} onBusyChange={onBusyChange} />;
}
