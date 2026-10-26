import { useEffect, useRef, useState } from "react";
import { getGoogleProducts, googleErrorMessage, isGoogleRequestCancelled } from "../../services/googleAds";
import { activateGoogleCreationPolicy, getGoogleCreationPolicy, revokeGoogleCreationPolicy, saveGoogleCreationPolicy } from "../../services/googleCreationPolicy";

const INPUT = "mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm disabled:bg-slate-100";
const BUTTON = "rounded-lg border px-3 py-2 text-sm font-bold disabled:opacity-40";
const NUMBERS = [["owner_generation", "جيل الإنشاء", 1, 2147483647], ["template_version", "إصدار القالب", 1, 2147483647],
    ["max_paused_campaigns", "أقصى حملات جديدة موقوفة", 1, 20], ["max_daily_creations", "أقصى إنشاءات في يوم الحساب", 1, 20],
    ["max_total_creations", "أقصى إنشاءات إجمالًا", 1, 100], ["catalog_max_age_seconds", "أقصى عمر لبيانات المنتج · ثانية", 60, 86400]];
const BLOCKERS = { product_not_public: "المنتج مخفي أو مؤرشف", product_out_of_stock: "المنتج غير متاح بالمخزون", product_facts_stale: "حدّث حقائق المنتج", product_copy_unproved: "راجع النص الأصلي للمنتج" };
function localDate(value) {
    if (!value || !Number.isFinite(new Date(value).getTime())) return "";
    const date = new Date(value);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
function formFrom(row) {
    const template = row.template || {};
    return { product_ids: (row.product_scopes || []).map((product) => product.product_id), owner_generation: String(row.owner_generation || ""),
        template_id: template.template_id || "", template_version: String(template.version || ""), campaign_name: template.campaign_name || "",
        group_name: template.group_name || "", headlines: (template.headlines || []).join("\n"), descriptions: (template.descriptions || []).join("\n"),
        keywords: (template.keywords || []).map((keyword) => `${keyword.match_type}: ${keyword.text}`).join("\n"),
        budget_native: row.budget_native || "", max_account_daily_budget_native: row.max_account_daily_budget_native || "",
        geo_target_ids: (row.geo_target_ids || []).join("\n"), max_paused_campaigns: String(row.max_paused_campaigns || ""),
        max_daily_creations: String(row.max_daily_creations || ""), max_total_creations: String(row.max_total_creations || ""),
        catalog_max_age_seconds: String(row.catalog_max_age_seconds || ""), expires_at: localDate(row.expires_at), auto_enable_new: row.auto_enable_new === true };
}
function lines(value) { return String(value).split("\n").map((line) => line.trim()).filter(Boolean); }
function inputError(message) { const failure = new Error(message); failure.creationInput = true; throw failure; }
function payloadFrom(form, revision) {
    const keywords = lines(form.keywords).map((line) => {
        const match = /^(EXACT|PHRASE):\s*(.+)$/.exec(line);
        if (!match) inputError("ابدأ كل كلمة مفتاحية بـ EXACT: أو PHRASE:.");
        return { text: match[2], match_type: match[1] };
    });
    const expiry = new Date(form.expires_at);
    if (!Number.isFinite(expiry.getTime())) inputError("حدد تاريخ انتهاء صالحًا.");
    return { expected_revision: revision, enabled: false, owner_generation: Number(form.owner_generation), product_ids: form.product_ids,
        template: { template_id: form.template_id.trim(), version: Number(form.template_version), ad_format: "RSA",
            campaign_name: form.campaign_name, group_name: form.group_name, headlines: lines(form.headlines), descriptions: lines(form.descriptions), keywords },
        acknowledge_template: true, budget_native: form.budget_native.trim(), max_account_daily_budget_native: form.max_account_daily_budget_native.trim(),
        geo_target_ids: form.geo_target_ids.split(/[\s,،]+/).filter(Boolean), bidding_strategy: "MAXIMIZE_CONVERSION_VALUE",
        max_paused_campaigns: Number(form.max_paused_campaigns), max_daily_creations: Number(form.max_daily_creations),
        max_total_creations: Number(form.max_total_creations), catalog_max_age_seconds: Number(form.catalog_max_age_seconds),
        auto_enable_new: form.auto_enable_new, expires_at: expiry.toISOString() };
}
function creativeText(text, product) {
    const fields = { product_name: product.name || "", product_short_name: product.product_short_name || "", sku: product.sku || "" };
    return String(text).replace(/\{(product_name|product_short_name|sku)\}/g, (_, key) => fields[key]);
}

function CreationPanel({ accountId }) {
    const [row, setRow] = useState(null), [form, setForm] = useState(null), [products, setProducts] = useState([]);
    const [query, setQuery] = useState(""), [busy, setBusy] = useState(true), [searching, setSearching] = useState(false);
    const [error, setError] = useState(""), [notice, setNotice] = useState(""), [conflict, setConflict] = useState(false), [reload, setReload] = useState(0);
    const [dirty, setDirty] = useState(false), [templateAck, setTemplateAck] = useState(false);
    const [acks, setAcks] = useState({ creation: false, creative: false, autoEnable: false });
    const mounted = useRef(true), request = useRef(null), search = useRef(null), inflight = useRef(false), searchRevision = useRef(0);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); search.current?.abort(); }; }, []);
    useEffect(() => {
        let active = true; const controller = new AbortController(); request.current = controller;
        search.current?.abort(); const initialSearchGeneration = ++searchRevision.current;
        setBusy(true); setError(""); setNotice("");
        getGoogleCreationPolicy(accountId, { signal: controller.signal }).then((result) => {
            if (active) { setRow(result); setForm(formFrom(result)); setDirty(false); setTemplateAck(false); setConflict(false); setAcks({ creation: false, creative: false, autoEnable: false }); }
        }).catch((failure) => { if (active && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure, "تعذر تحميل سياسة الإنشاء.")); })
            .finally(() => { if (active) setBusy(false); });
        getGoogleProducts({ limit: 50, signal: controller.signal }).then((result) => { if (active && initialSearchGeneration === searchRevision.current) setProducts(result.items || []); })
            .catch(() => { if (active && initialSearchGeneration === searchRevision.current) setProducts([]); });
        return () => { active = false; controller.abort(); };
    }, [accountId, reload]);
    function edit(key, value) { setForm((previous) => ({ ...previous, [key]: value })); setDirty(true); setTemplateAck(false); setNotice(""); setAcks({ creation: false, creative: false, autoEnable: false }); }
    async function findProducts() {
        search.current?.abort(); const controller = new AbortController(); search.current = controller;
        const generation = ++searchRevision.current;
        setSearching(true);
        try { const result = await getGoogleProducts({ query, limit: 50, signal: controller.signal }); if (mounted.current && generation === searchRevision.current) setProducts(result.items || []); }
        catch (failure) { if (mounted.current && !isGoogleRequestCancelled(failure)) setError("تعذر البحث في المنتجات. بقي اختيارك محفوظًا."); }
        finally { if (mounted.current && generation === searchRevision.current) setSearching(false); }
    }
    async function mutate(kind) {
        if (!row || busy || conflict || inflight.current) return;
        if (kind === "save" && !templateAck) { setError("راجع القالب واعتمده صراحةً قبل الحفظ."); return; }
        if (kind === "activate" && (dirty || row.executor_available !== true || row.effective_enabled === true || (row.configuration_blockers || []).length || !acks.creation || !acks.creative || (row.auto_enable_new && !acks.autoEnable))) return;
        inflight.current = true; setBusy(true); setError(""); setNotice("");
        const controller = new AbortController(); request.current = controller;
        try {
            let result;
            if (kind === "save") result = await saveGoogleCreationPolicy(accountId, payloadFrom(form, row.revision), { signal: controller.signal });
            else if (kind === "revoke") result = await revokeGoogleCreationPolicy(accountId, row.revision, { signal: controller.signal });
            else result = await activateGoogleCreationPolicy(accountId, row.revision, acks, { signal: controller.signal });
            if (mounted.current) { setRow(result); setForm(formFrom(result)); setDirty(false); setTemplateAck(false); setAcks({ creation: false, creative: false, autoEnable: false }); setNotice(kind === "activate" ? "اعتُمد تفويض الإنشاء لهذا الإصدار وحدوده." : kind === "revoke" ? "أُلغي تفويض الإنشاء الجديد." : "حُفظت السياسة والقالب معطّلين. لم تُنشأ أي حملة."); }
        } catch (failure) {
            if (mounted.current && !isGoogleRequestCancelled(failure)) {
                if (failure?.response?.status === 409) { setConflict(true); setError("تغيرت السياسة أو جاهزيتها. بقيت مدخلاتك ظاهرة؛ أعد التحميل قبل الحفظ أو الاعتماد."); }
                else setError(googleErrorMessage(failure, failure?.creationInput === true ? failure.message : "تعذر حفظ سياسة الإنشاء؛ بقيت مدخلاتك كما هي."));
            }
        } finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    const choices = [...products, ...(row?.product_review || []).filter((old) => !products.some((item) => item.product_id === old.product_id))];
    const canActivate = row?.executor_available === true && !row?.effective_enabled && !(row?.configuration_blockers || []).length && !dirty && !busy && !conflict;
    return <details className="rounded-2xl border bg-white p-4" aria-label="سياسة إنشاء حملات جوجل">
        <summary className="cursor-pointer font-black">سياسة إنشاء حملات جوجل</summary>
        <p className="mt-3 text-sm leading-7">إنشاء SEARCH بإعلان RSA من المنتجات والقالب المعتمدين فقط. تبدأ الحملة موقوفة. الحفظ وحده لا يفعل الإنشاء ولا يغيّر الحملات الحالية.</p>
        <p className="text-sm">{row?.effective_enabled ? "تفويض الإنشاء مفعّل ضمن الحدود المعتمدة" : "تفويض الإنشاء معطّل"}{row?.executor_available !== true && " · التشغيل غير متاح حاليًا"}</p>
        <button className={`${BUTTON} mt-3`} type="button" disabled={busy} onClick={() => setReload((value) => value + 1)}>إعادة تحميل سياسة الإنشاء</button>
        {busy && <p role="status">جارٍ قراءة أو حفظ سياسة الإنشاء…</p>}{error && <p role="alert" className="mt-3 text-red-800">{error}</p>}{notice && <p role="status" className="mt-3 text-emerald-800">{notice}</p>}
        {row?.in_flight_authorized && <p role="status" className="mt-3 text-amber-900">قد تكتمل عملية سبق تفويضها قبل الإلغاء؛ لا يبدأ الإلغاء أي إرسال جديد.</p>}
        {(row?.configuration_blockers || []).length > 0 && <ul className="mt-3 text-sm text-amber-900">{row.configuration_blockers.map((code) => <li key={code}>{BLOCKERS[code.split(":")[0]] || "راجع حقائق المنتج"} · {code.split(":").slice(1).join(":")}</li>)}</ul>}
        {form && <form className="mt-4 space-y-4" onSubmit={(event) => { event.preventDefault(); mutate("save"); }}>
            <fieldset disabled={busy || conflict} className="space-y-4">
                <div className="flex flex-wrap gap-2"><input aria-label="بحث منتجات سياسة الإنشاء" value={query} onChange={(event) => setQuery(event.target.value)} className={INPUT} /><button type="button" className={BUTTON} disabled={searching} onClick={findProducts}>بحث المنتجات</button></div>
                <fieldset className="rounded-lg border p-3"><legend className="text-sm font-bold">المنتجات المسموح بها · حتى 8</legend>{choices.map((product) => <label key={product.product_id} className="block py-1 text-sm"><input type="checkbox" checked={form.product_ids.includes(product.product_id)} disabled={!form.product_ids.includes(product.product_id) && form.product_ids.length >= 8} onChange={(event) => edit("product_ids", event.target.checked ? [...form.product_ids, product.product_id] : form.product_ids.filter((id) => id !== product.product_id))} /> {product.name || product.product_id}</label>)}{!choices.length && <p className="text-sm text-slate-500">ابحث عن منتجات متجرك؛ لا يتم اختيار أي منتج تلقائيًا.</p>}</fieldset>
                <div className="grid gap-3 sm:grid-cols-2">{NUMBERS.map(([key, label, minimum, maximum]) => <label key={key} className="text-sm font-bold">{label}<input required type="number" min={minimum} max={maximum} aria-label={label} className={INPUT} value={form[key]} onChange={(event) => edit(key, event.target.value)} /></label>)}
                    <label className="text-sm font-bold">ميزانية الحملة · عملة الحساب<input required inputMode="decimal" aria-label="ميزانية حملة الإنشاء" className={INPUT} value={form.budget_native} onChange={(event) => edit("budget_native", event.target.value)} /></label>
                    <label className="text-sm font-bold">حد مجموع متوسط الميزانيات · عملة الحساب<input required inputMode="decimal" aria-label="حد مجموع ميزانيات الإنشاء" className={INPUT} value={form.max_account_daily_budget_native} onChange={(event) => edit("max_account_daily_budget_native", event.target.value)} /></label>
                    <label className="text-sm font-bold">انتهاء التفويض · توقيت جهازك<input required type="datetime-local" aria-label="انتهاء تفويض الإنشاء" className={INPUT} value={form.expires_at} onChange={(event) => edit("expires_at", event.target.value)} /></label>
                    <label className="text-sm font-bold">معرفات الاستهداف الجغرافي<textarea required aria-label="معرفات استهداف الإنشاء" className={INPUT} value={form.geo_target_ids} onChange={(event) => edit("geo_target_ids", event.target.value)} /></label>
                </div>
                <p className="text-xs leading-6 text-slate-500">الأموال بعملة حساب جوجل الأصلية. الميزانيات متوسطات يومية وليست سقفًا للصرف الفعلي. يُعاد إثبات التمويل قبل أي تفعيل؛ لا نخفض الحملات القائمة تلقائيًا لتمويل حملة جديدة. زيادة جيل الإنشاء أو إصدار القالب قرار صريح لنية إنشاء جديدة.</p>
                <label className="block text-sm"><input type="checkbox" checked={form.auto_enable_new} onChange={(event) => edit("auto_enable_new", event.target.checked)} /> السماح بالتفعيل اللاحق للحملات الجديدة بعد إثبات ملكيتها وتوفر التمويل</label>
                <div className="grid gap-3 sm:grid-cols-2">{[["template_id", "معرف القالب"], ["campaign_name", "اسم الحملة من القالب"], ["group_name", "اسم المجموعة من القالب"]].map(([key, label]) => <label key={key} className="text-sm font-bold">{label}<input required aria-label={label} className={INPUT} value={form[key]} onChange={(event) => edit(key, event.target.value)} /></label>)}</div>
                <p className="text-xs leading-6 text-slate-500">المتغيرات المسموح بها فقط: {"{product_name}"}، {"{product_short_name}"}، {"{sku}"}. الاسم المختصر بداية الاسم الأصلي عند حدود الكلمات، دون اختراع نص أو ادعاء. يجب أن يطابق النص حد جوجل بعد الاستبدال.</p>
                {[["headlines", "عناوين RSA · 3 إلى 15"], ["descriptions", "أوصاف RSA · 2 إلى 4"], ["keywords", "الكلمات المفتاحية · PHRASE: أو EXACT:"]].map(([key, label]) => <label key={key} className="block text-sm font-bold">{label}<textarea required rows={3} aria-label={label} className={INPUT} value={form[key]} onChange={(event) => edit(key, event.target.value)} /></label>)}
                <label className="block text-sm"><input type="checkbox" aria-label="اعتماد قالب الإنشاء للحفظ" checked={templateAck} onChange={(event) => setTemplateAck(event.target.checked)} /> راجعت المنتجات وصياغة القالب ومتغيراته وأعتمد هذا الإصدار للحفظ.</label>
                <button type="submit" className={BUTTON} disabled={!templateAck}>حفظ قالب وسياسة الإنشاء معطّلين</button>
            </fieldset>
        </form>}
        {row?.revision > 0 && <section aria-label="مراجعة تفويض إنشاء جوجل" className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-7">
            <h3 className="font-bold">اعتماد السياسة المحفوظة · النسخة {row.revision}</h3>
            <p>الحساب {accountId} · {row.currency_code} · {row.time_zone} · نطاق المتجر {row.allowed_domain}</p>
            <p>ميزانية الحملة {row.budget_native} · حد المجموع {row.max_account_daily_budget_native} {row.currency_code} · الإنشاء اليومي {row.max_daily_creations} · إجمالي الإنشاء {row.max_total_creations}</p>
            <p>الاستهداف: {(row.geo_review || []).map((geo) => `${geo.name} (${geo.id})`).join("، ")} · الانتهاء: {row.expires_at ? new Date(row.expires_at).toLocaleString("ar-SA") : "—"}</p>
            {(row.product_review || []).map((product) => <article key={product.product_id} className="my-2 rounded-lg bg-white p-3"><p className="font-bold">{product.name}</p><p dir="ltr">{product.final_url}</p><ul>{(row.template?.headlines || []).map((text, index) => <li key={`h${index}`}>{creativeText(text, product)}</li>)}{(row.template?.descriptions || []).map((text, index) => <li key={`d${index}`}>{creativeText(text, product)}</li>)}</ul></article>)}
            {dirty && <p>احفظ التعديلات ثم راجع النسخة الجديدة قبل التفعيل.</p>}
            {row.executor_available !== true && <p>التفعيل غير متاح حتى اكتمال جاهزية مسار الإنشاء الآلي.</p>}
            {[['creation', 'اعتماد إنشاء الحملات تلقائيًا'], ['creative', 'اعتماد النصوص والمنتجات المحفوظة'], ...(row.auto_enable_new ? [['autoEnable', 'اعتماد التفعيل اللاحق ضمن التمويل المثبت']] : [])].map(([key, label]) => <label key={key} className="block"><input aria-label={label} type="checkbox" disabled={!canActivate} checked={acks[key]} onChange={(event) => setAcks((value) => ({ ...value, [key]: event.target.checked }))} /> {label}</label>)}
            <button type="button" className={`${BUTTON} mt-3`} disabled={!canActivate || !acks.creation || !acks.creative || (row.auto_enable_new && !acks.autoEnable)} onClick={() => mutate("activate")}>تفعيل تفويض الإنشاء ضمن هذه الحدود</button>
            <button type="button" className={`${BUTTON} mr-2 mt-3 text-red-800`} disabled={busy || conflict} onClick={() => mutate("revoke")}>إلغاء تفويض الإنشاء</button>
        </section>}
    </details>;
}

export default function GoogleCreationPolicy({ accountId, isOwner }) {
    if (isOwner !== true || !/^[0-9]{1,20}$/.test(String(accountId || ""))) return null;
    return <CreationPanel key={String(accountId)} accountId={String(accountId)} />;
}
