import { useEffect, useRef, useState } from "react";
import SnapchatMediaUploadPanel from "./SnapchatMediaUploadPanel";
import { getSnapchatManagementReadiness } from "../../services/snapchatCampaignManagement";
import { listProductsV2 } from "../../services/mezanProductsV2";
import {
    approveSnapchatPublication, executeSnapchatPublication, getSnapchatPublication,
    getSnapchatPublicationByKey, listSnapchatPublications, previewSnapchatPublication,
    publicationAmountToMicro, publicationError, reconcileSnapchatPublication, reviewSnapchatPublication,
} from "../../services/snapchatCampaignPublication";

const EMPTY = { campaignName: "", groupName: "", creativeName: "", adName: "",
    headline: "", url: "", budget: "", start: "", country: "", pixel: "",
    window: "SWIPE_28DAY_VIEW_1DAY", product: "", variant: "", reason: "" };
const STAGES = ["الحملة", "المجموعة", "الإبداع", "الإعلان"];
const NAME_FIELDS = ["campaign_name", "group_name", "creative_name", "ad_name"];
const STATUS = { previewed: "معاينة بانتظار الاعتماد", approved: "معتمدة بانتظار التنفيذ",
    running: "جارٍ الإنشاء النشط والتحقق", interrupted: "العملية تحتاج مراجعة أو استئنافًا", completed: "تم الإنشاء والتحقق بحالة نشطة" };
const productId = (product) => String(product?.salla_product_id || product?.mezan_product_id || product?.id || "");
const cls = "mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 disabled:bg-slate-100";

function Field({ label, value, onChange, type = "text", testId, maxLength }) {
    return <label className="block text-sm">{label}<input className={cls} type={type} required
        value={value} maxLength={maxLength} data-testid={testId}
        onChange={(event) => onChange(event.target.value)} /></label>;
}

export default function SnapchatCampaignPublicationPanel({ ownerId, accountId = "", onChanged }) {
    const [accounts, setAccounts] = useState([]);
    const [executionEnabled, setExecutionEnabled] = useState(false);
    const [activationEnabled, setActivationEnabled] = useState(false);
    const [selectedAccount, setSelectedAccount] = useState(accountId);
    const [form, setForm] = useState(EMPTY);
    const [products, setProducts] = useState([]);
    const [query, setQuery] = useState("");
    const [media, setMedia] = useState(null);
    const [profile, setProfile] = useState("");
    const [job, setJob] = useState(null);
    const [jobs, setJobs] = useState([]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [following, setFollowing] = useState(false);
    const scope = JSON.stringify([String(ownerId || ""), selectedAccount]);
    const currentScope = useRef(scope);
    const displayedOwner = useRef(ownerId);
    const displayedScope = useRef(scope);
    currentScope.current = scope;
    const pendingPreview = useRef(null);
    const busyRef = useRef(false);
    const account = accounts.find((item) => item.account_id === selectedAccount);
    const product = products.find((item) => productId(item) === form.product);
    const allowed = Boolean(ownerId && account?.management_allowed && account?.creative_allowed);
    const editable = !job || job.status === "previewed";
    const same = (context) => JSON.stringify([context?.ownerId, context?.accountId]) === currentScope.current;

    useEffect(() => { setSelectedAccount(accountId); }, [accountId]);
    useEffect(() => {
        let live = true;
        displayedOwner.current = ownerId;
        setAccounts([]); setJobs([]); setExecutionEnabled(false); setActivationEnabled(false);
        if (!ownerId) return undefined;
        Promise.all([getSnapchatManagementReadiness(), listSnapchatPublications()])
            .then(([readiness, rows]) => {
                if (live) { setAccounts(readiness.accounts || []); setJobs(rows);
                    setExecutionEnabled(readiness.execution_enabled === true); }
                if (live) setActivationEnabled(readiness.activation_enabled === true);
            }).catch((failure) => { if (live) setError(publicationError(failure)); });
        return () => { live = false; };
    }, [ownerId]);
    useEffect(() => {
        pendingPreview.current = null;
        displayedScope.current = scope;
        setForm({ ...EMPTY }); setMedia(null); setProfile(""); setJob(null);
        setFollowing(false); setError(""); setNotice(""); setProducts([]);
        busyRef.current = false; setBusy(false);
    }, [ownerId, selectedAccount]);

    async function search() {
        const captured = currentScope.current;
        try {
            const result = await listProductsV2({ page: 1, perPage: 30, query, status: "active" });
            if (captured === currentScope.current) setProducts(result.items || []);
        } catch (failure) { if (captured === currentScope.current) setError(publicationError(failure)); }
    }

    async function perform(action, { follow = false } = {}) {
        if (busyRef.current) return;
        const captured = currentScope.current;
        busyRef.current = captured; setBusy(true); setError("");
        try {
            const value = await action();
            if (captured !== currentScope.current) return;
            if (value?.publication_id) {
                setJob(value);
                if (follow) setFollowing(true);
                setJobs((rows) => [value, ...rows.filter((row) => row.publication_id !== value.publication_id)]);
                if (value.status === "completed") onChanged?.();
            }
        } catch (failure) {
            if (captured === currentScope.current) setError(publicationError(failure));
        } finally {
            if (busyRef.current === captured) busyRef.current = false;
            if (captured === currentScope.current) setBusy(false);
        }
    }

    useEffect(() => {
        if (!following || !job?.publication_id) return undefined;
        const captured = currentScope.current;
        const id = job.publication_id;
        let live = true, timer, attempts = 0;
        const read = async () => {
            try {
                const value = await getSnapchatPublication(id);
                if (!live || captured !== currentScope.current) return;
                setJob(value);
                if (["completed", "interrupted"].includes(value.status)) {
                    setFollowing(false);
                    if (value.status === "completed") onChanged?.();
                    return;
                }
            } catch (failure) {
                if (!live || captured !== currentScope.current) return;
                setError(publicationError(failure)); setFollowing(false); return;
            }
            if (++attempts < 120) timer = setTimeout(read, 1500);
            else { setFollowing(false); setNotice("ما زالت العملية محفوظة؛ حدّث النتيجة لاحقًا."); }
        };
        timer = setTimeout(read, 500);
        return () => { live = false; clearTimeout(timer); };
    }, [following, job?.publication_id, onChanged]);

    function field(key, value) {
        setForm((current) => ({ ...current, [key]: value, ...(key === "product" ? { variant: "" } : {}) }));
        pendingPreview.current = null; setJob(null);
    }

    async function preview(event) {
        event.preventDefault();
        await perform(async () => {
            if (!allowed || !media?.ready_for_creative || !profile || !product) {
                throw new Error("اختر الحساب والمنتج والفيديو الجاهز والملف العام أولًا.");
            }
            if (!pendingPreview.current) {
                pendingPreview.current = {
                    account_id: selectedAccount, currency: account.currency, product_id: form.product,
                    product_variant_id: form.variant || null, media_upload_id: media.upload_id,
                    profile_id: profile, pixel_id: form.pixel, campaign_name: form.campaignName,
                    group_name: form.groupName, creative_name: form.creativeName, ad_name: form.adName,
                    headline: form.headline, destination_url: form.url,
                    daily_budget_micro: publicationAmountToMicro(form.budget),
                    start_time: new Date(form.start).toISOString(), country: form.country,
                    conversion_window: form.window, reason: form.reason,
                    idempotency_key: "mzpub-" + window.crypto.randomUUID(),
                };
            }
            return previewSnapchatPublication(pendingPreview.current);
        });
    }

    function reopen(value) {
        if (value.account_id !== selectedAccount) { setError("اختر حساب هذه العملية أولًا."); return; }
        setJob(value); setFollowing(false); setError(""); setNotice("");
    }

    if (displayedOwner.current !== ownerId || displayedScope.current !== scope) {
        return <section dir="rtl" role="status">جارٍ تحميل نطاق المالك الحالي…</section>;
    }
    return <section dir="rtl" className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4"
        data-testid="snapchat-publication-panel">
        <h3 className="text-base font-black">إنشاء حملة متكاملة نشطة مباشرةً</h3>
        <p className="text-sm text-slate-600">تنشأ الحملة والمجموعة والإعلان نشطة مباشرةً، وقد يبدأ الصرف بميزانية المجموعة التي تدخلها. الهدف مبيعات وتحسين الشراء، والمزايدة والمواضع تلقائية، والزر «تسوق الآن». راجع الخطة كاملة قبل اعتماد الإنشاء والتشغيل.</p>
        {!executionEnabled && <p role="status" className="text-sm">تنفيذ عمليات Snapchat متوقف حاليًا.</p>}
        {!activationEnabled && <p role="status" className="text-sm">التشغيل المباشر غير متاح حاليًا؛ لا يمكن إنشاء هذه الخطة حتى يتاح.</p>}
        {error && <p role="alert" className="text-sm text-rose-700">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        <label className="block text-sm">الحساب الإعلاني<select className={cls} value={selectedAccount}
            disabled={busy} data-testid="publication-account"
            onChange={(event) => setSelectedAccount(event.target.value)}>
            <option value="">اختر الحساب</option>
            {accounts.map((item) => <option key={item.account_id} value={item.account_id}>{item.display_name} · {item.currency}</option>)}
        </select></label>
        {ownerId && <form onSubmit={preview} className="space-y-4">
            <fieldset disabled={busy || !editable} className="space-y-3">
                <div className="flex items-end gap-2"><label className="flex-1 text-sm">بحث المنتجات<input
                    className={cls} value={query} onChange={(event) => setQuery(event.target.value)} /></label>
                    <button type="button" onClick={search} className="min-h-11 rounded-lg border px-4">بحث</button></div>
                <label className="block text-sm">المنتج<select className={cls} required value={form.product}
                    data-testid="publication-product" onChange={(event) => field("product", event.target.value)}>
                    <option value="">اختر المنتج</option>
                    {products.map((item) => <option key={productId(item)} value={productId(item)}>{item.name}</option>)}
                </select></label>
                {product?.variants?.length > 0 && <label className="block text-sm">الخيار<select className={cls}
                    value={form.variant} onChange={(event) => field("variant", event.target.value)}>
                    <option value="">المنتج كاملًا</option>{product.variants.map((item) => {
                        const id = String(item.id || item.variant_id || "");
                        return <option key={id} value={id}>{item.display_name || item.name || item.sku || id}</option>;
                    })}</select></label>}
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="اسم الحملة" value={form.campaignName} onChange={(v) => field("campaignName", v)} maxLength={200} />
                    <Field label="اسم المجموعة" value={form.groupName} onChange={(v) => field("groupName", v)} maxLength={200} />
                    <Field label="اسم الإبداع" value={form.creativeName} onChange={(v) => field("creativeName", v)} maxLength={200} />
                    <Field label="اسم الإعلان" value={form.adName} onChange={(v) => field("adName", v)} maxLength={200} />
                    <Field label="العنوان" value={form.headline} onChange={(v) => field("headline", v)} maxLength={34} />
                    <Field label="رابط المنتج HTTPS" type="url" value={form.url} onChange={(v) => field("url", v)} />
                    <Field label={"ميزانية المجموعة اليومية · " + (account?.currency || "اختر الحساب")}
                        value={form.budget} onChange={(v) => field("budget", v)} testId="publication-budget" />
                    <Field label="وقت البدء بالتوقيت المحلي لجهازك" type="datetime-local" value={form.start}
                        onChange={(v) => field("start", v)} testId="publication-start" />
                    <Field label="رمز البلد من حرفين" value={form.country} onChange={(v) => field("country", v)} maxLength={2} testId="publication-country" />
                    <label className="block text-sm">Snap Pixel<select className={cls} required value={form.pixel}
                        onChange={(event) => field("pixel", event.target.value)}>
                        <option value="">اختر Pixel</option>{(account?.pixels || []).map((item) =>
                            <option key={item.pixel_id} value={item.pixel_id}>{item.display_name}</option>)}
                    </select></label>
                    <label className="block text-sm">نافذة التحويل<select className={cls} value={form.window}
                        onChange={(event) => field("window", event.target.value)}>
                        <option value="SWIPE_28DAY_VIEW_1DAY">نقر 28 يومًا ومشاهدة يوم</option>
                        <option value="SWIPE_7DAY">نقر 7 أيام</option>
                    </select></label>
                    <Field label="سبب إنشاء الحملة" value={form.reason} onChange={(v) => field("reason", v)} maxLength={500} />
                </div>
                <SnapchatMediaUploadPanel ownerId={ownerId} accountId={selectedAccount}
                    accountName={account?.display_name || ""} allowed={allowed} disabled={busy || !editable || !executionEnabled}
                    profileId={profile} onProfileChange={(id, context) => { if (same(context)) { setProfile(id); setJob(null); pendingPreview.current = null; } }}
                    onClear={(context) => { if (same(context)) { setMedia(null); setJob(null); } }}
                    onReady={(value, context) => { if (same(context) && value.ready_for_creative === true
                        && value.account_id === selectedAccount) { setMedia(value); setJob(null); pendingPreview.current = null; } }} />
            </fieldset>
            <button type="submit" disabled={busy || !editable || !allowed || !media?.ready_for_creative || !profile}
                data-testid="publication-preview" className="min-h-11 rounded-lg bg-amber-300 px-4 font-bold">معاينة الحملة كاملة</button>
            {pendingPreview.current && !job && <button type="button" disabled={busy} className="min-h-11 border px-4"
                onClick={() => perform(() => getSnapchatPublicationByKey(pendingPreview.current.idempotency_key))}>استعادة نتيجة المعاينة</button>}
        </form>}
        {job && <article className="space-y-3 rounded-xl bg-slate-50 p-3" data-testid="publication-progress">
            <strong>{STATUS[job.status] || job.status}</strong>
            <p className="text-sm">ميزانية المجموعة: {(Number(job.plan?.daily_budget_micro) / 1_000_000).toLocaleString("ar-SA")} {job.plan?.currency}
                {" · "}{job.plan?.country?.toUpperCase()}{" · "}{job.plan?.start_time}</p>
            <p className="text-sm">{job.plan?.product_name} · {job.plan?.headline} · {job.plan?.destination_url}</p>
            <p className="text-sm">الخيار: {job.plan?.product_variant_id || "المنتج كاملًا"} · نافذة التحويل: {job.plan?.conversion_window}</p>
            <p className="break-all text-xs">Pixel: {job.plan?.pixel_id} · الملف العام: {job.plan?.profile_id} · الفيديو الموثق: {job.plan?.media_id}</p>
            <p className="text-xs">يضيف ميزان رمزًا فريدًا إلى أسماء هذه العملية. سبب الإنشاء: {job.plan?.reason}</p>
            <ol className="grid gap-2 sm:grid-cols-4">{(job.steps || []).map((step, index) =>
                <li key={step.action} className="rounded-lg border p-2">{STAGES[index]} · {job.plan?.[NAME_FIELDS[index]]}
                    <span className="block text-xs">{step.status === "completed" ? (step.configured_status === "ACTIVE" ? "تم التحقق · نشط" : "تم التحقق") : job.status === "running" && job.current_step === index ? "جارٍ الإنشاء" : "بانتظار الإنشاء"}</span>
                    <code className="block break-all text-xs" dir="ltr">{step.provider_entity_id || "بانتظار التحقق"}</code></li>)}</ol>
            {job.failure?.code && <p role="alert">{job.failure.code} · افحص النتيجة قبل الاستئناف.</p>}
            {job.status === "previewed" && <button type="button" disabled={busy || !job.confirm_token}
                data-testid="publication-approve" className="min-h-11 rounded-lg bg-amber-300 px-4"
                onClick={() => perform(() => approveSnapchatPublication(job))}>اعتماد الإنشاء والتشغيل المباشر</button>}
            {job.status !== "completed" && job.status !== "running" && <button type="button" disabled={busy}
                data-testid="publication-review" className="min-h-11 rounded-lg border px-4"
                onClick={() => perform(() => reviewSnapchatPublication(job.publication_id))}>مراجعة الخطة المتبقية وتجديد اعتمادها</button>}
            {["approved", "interrupted"].includes(job.status) && <button type="button" disabled={busy || !executionEnabled || !activationEnabled || job.delivery_outcome_unknown}
                data-testid="publication-execute" className="min-h-11 rounded-lg bg-emerald-100 px-4"
                onClick={() => perform(() => executeSnapchatPublication(job.publication_id), { follow: true })}>{job.status === "approved" ? "إنشاء الحملة النشطة" : "استئناف العملية"}</button>}
            <button type="button" disabled={busy} className="min-h-11 rounded-lg border px-4"
                onClick={() => perform(() => getSnapchatPublication(job.publication_id))}>تحديث النتيجة</button>
            {job.status === "interrupted" && <button type="button" disabled={busy} data-testid="publication-reconcile"
                className="min-h-11 rounded-lg border px-4"
                onClick={() => perform(() => reconcileSnapchatPublication(job.publication_id))}>فحص النتيجة</button>}
            {job.delivery_outcome_unknown && <p role="alert">قد يكون الإعلان نشطًا وبدأ الصرف. افحص النتيجة؛ لن يعيد ميزان إرسال طلب إنشاء غير محسوم.</p>}
            {job.steps?.some((step) => step.configured_status === "ACTIVE") && job.status !== "completed" && <p role="status">المستويات التي اكتمل إنشاؤها نشطة وتبقى ظاهرة في سجل هذه العملية.</p>}
            {job.status === "completed" && <p role="status">أُنشئت الحملة والمجموعة والإعلان نشطة مباشرةً.
                مراجعة الإبداع: {job.review?.creative_review_status || "غير متاحة"} · تجهيز الإبداع: {job.review?.creative_packaging_status || "غير متاح"}
                · مراجعة الإعلان: {job.review?.ad_review_status || "غير متاحة"} · حالة التسليم لدى Snapchat: {JSON.stringify(job.review?.ad_delivery_status ?? "غير متاحة")}.</p>}
        </article>}
        <div className="space-y-2"><p className="text-sm font-bold">العمليات المحفوظة لهذا الحساب</p>
            {jobs.filter((item) => item.account_id === selectedAccount).map((item) => <button
                type="button" key={item.publication_id} disabled={busy} onClick={() => reopen(item)}
                className="block min-h-11 w-full rounded-lg border px-3 text-right">
                {item.plan?.campaign_name} · {STATUS[item.status] || item.status}</button>)}</div>
    </section>;
}

