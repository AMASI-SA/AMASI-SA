import { useEffect, useMemo, useState } from "react";
import {
    CheckCircle, ClockCounterClockwise, FloppyDisk, PaperPlaneTilt,
    ShieldCheck, Sparkle, SpinnerGap, WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    approveProductControlDraft,
    getProductControlCenter,
    publishProductControlDraft,
    saveProductControlDraft,
} from "../../services/mezanProductsV2";

const EMPTY = {
    name: "", price: "", sale_price: "", sale_starts_at: "", sale_ends_at: "",
    status: "active", short_description: "", description: "", categories: "",
    google_category: "", local_category: "", seo_title: "", seo_description: "",
    keywords: "", slug: "",
};

function dateInput(value) {
    if (!value) return "";
    const text = String(value);
    return /^\d{4}-\d{2}-\d{2}/.test(text) ? text.slice(0, 10) : "";
}

function hydrate(product) {
    const seo = product?.seo || {};
    return {
        name: product?.name || "",
        price: product?.price ?? "",
        sale_price: product?.sale_price ?? "",
        sale_starts_at: dateInput(product?.sale_starts_at),
        sale_ends_at: dateInput(product?.sale_ends_at),
        status: product?.status || "active",
        short_description: product?.short_description || "",
        description: product?.description_html || product?.description || "",
        categories: (product?.categories || []).map((row) => row?.id || row?.name || row).filter(Boolean).join(", "),
        google_category: product?.google_category || "",
        local_category: product?.local_category || "",
        seo_title: seo.title || "",
        seo_description: seo.description || "",
        keywords: (seo.keywords || []).join(", "),
        slug: seo.slug || "",
    };
}

function buildChanges(form, original) {
    const changes = {};
    const text = (value) => String(value ?? "").trim();
    if (text(form.name) !== text(original.name)) changes.name = text(form.name);
    if (String(form.price) !== String(original.price)) changes.price = form.price === "" ? null : Number(form.price);
    if (String(form.sale_price) !== String(original.sale_price)) changes.sale_price = form.sale_price === "" ? null : Number(form.sale_price);
    if (form.sale_starts_at !== original.sale_starts_at) changes.sale_starts_at = form.sale_starts_at || null;
    if (form.sale_ends_at !== original.sale_ends_at) changes.sale_ends_at = form.sale_ends_at || null;
    if (form.status !== original.status) changes.status = form.status;
    if (form.short_description !== original.short_description) changes.short_description = form.short_description;
    if (form.description !== original.description) changes.description = form.description;
    if (form.categories !== original.categories) changes.categories = form.categories.split(",").map((v) => v.trim()).filter(Boolean);
    if (form.google_category !== original.google_category) changes.google_category = text(form.google_category) || null;
    if (form.local_category !== original.local_category) changes.local_category = text(form.local_category) || null;
    const seo = {};
    if (form.seo_title !== original.seo_title) seo.title = form.seo_title;
    if (form.seo_description !== original.seo_description) seo.description = form.seo_description;
    if (form.keywords !== original.keywords) seo.keywords = form.keywords.split(",").map((v) => v.trim()).filter(Boolean);
    if (Object.keys(seo).length) changes.seo = seo;
    if (form.slug !== original.slug) changes.slug = text(form.slug) || null;
    return changes;
}

function discountNotice(form) {
    if (!form.sale_price || !form.sale_ends_at) return null;
    const end = new Date(`${form.sale_ends_at}T23:59:59`);
    if (Number.isNaN(end.getTime())) return null;
    const days = Math.ceil((end.getTime() - Date.now()) / 86400000);
    if (days < 0) return { tone: "rose", text: "انتهى التخفيض. راجع السعر المخفض أو أزل تاريخ الانتهاء." };
    if (days <= 3) return { tone: "amber", text: `التخفيض سينتهي خلال ${days} يوم.` };
    return null;
}

function ChangeDiff({ before = {}, after = {} }) {
    const rows = Object.keys(after);
    if (!rows.length) return null;
    return <div className="mt-4 space-y-2">{rows.map((field) => <div key={field} className="grid gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs md:grid-cols-[140px_1fr_1fr]"><div className="font-black text-slate-700">{field}</div><div><span className="text-slate-400">قبل:</span> <span className="break-all">{JSON.stringify(before[field] ?? "")}</span></div><div><span className="text-violet-500">بعد:</span> <span className="break-all font-bold">{JSON.stringify(after[field] ?? "")}</span></div></div>)}</div>;
}

export default function ProductControlCenterPanel({ productId, product, onPublished }) {
    const [state, setState] = useState(null);
    const [form, setForm] = useState(EMPTY);
    const [original, setOriginal] = useState(EMPTY);
    const [reason, setReason] = useState("");
    const [busy, setBusy] = useState(false);
    const [tab, setTab] = useState("edit");

    async function load() {
        if (!productId) return;
        try {
            const result = await getProductControlCenter(productId);
            setState(result);
            const next = hydrate(result.product || product);
            setForm(next);
            setOriginal(next);
        } catch (error) { toast.error(error?.response?.data?.detail?.message || "تعذر تحميل مركز التحكم بالمنتج"); }
    }

    useEffect(() => { load(); }, [productId]); // eslint-disable-line react-hooks/exhaustive-deps

    const changes = useMemo(() => buildChanges(form, original), [form, original]);
    const notice = useMemo(() => discountNotice(form), [form]);
    const draft = state?.draft || null;
    const protectedFields = state?.protected_fields || [];

    async function saveDraft() {
        if (!Object.keys(changes).length) return toast.info("لا توجد تغييرات لحفظها");
        setBusy(true);
        try {
            const result = await saveProductControlDraft(productId, { changes, source: "human", reason: reason.trim() || "تعديل من Product Control Center" });
            setState((current) => ({ ...current, draft: result.draft }));
            toast.success("تم حفظ المسودة دون تعديل سلة");
        } catch (error) { toast.error(error?.response?.data?.detail?.code || "تعذر حفظ المسودة"); }
        finally { setBusy(false); }
    }

    async function approve() {
        if (!draft) return;
        setBusy(true);
        try {
            const result = await approveProductControlDraft(productId, draft.id);
            setState((current) => ({ ...current, draft: result.draft }));
            toast.success("تم اعتماد المسودة");
        } catch (error) { toast.error(error?.response?.data?.detail?.code || "تعذر اعتماد المسودة"); }
        finally { setBusy(false); }
    }

    async function publish() {
        if (!draft) return;
        setBusy(true);
        try {
            await publishProductControlDraft(productId, draft.id);
            toast.success("تم نشر التعديل إلى سلة وتسجيل المراجعة");
            await load();
            onPublished?.();
        } catch (error) { toast.error(error?.response?.data?.detail?.message || error?.response?.data?.detail?.code || "تعذر النشر إلى سلة"); }
        finally { setBusy(false); }
    }

    const field = (key, label, input) => <label className="block text-xs font-black text-slate-600">{label}{input || <input value={form[key]} onChange={(e) => setForm((row) => ({ ...row, [key]: e.target.value }))} className="mt-1 w-full rounded-xl border border-slate-200 p-3 text-sm font-normal text-slate-950 outline-none focus:border-violet-500" />}</label>;

    return <section className="overflow-hidden rounded-2xl border border-violet-200 bg-white" data-testid="product-control-center">
        <div className="flex flex-col gap-3 border-b border-violet-100 bg-gradient-to-l from-violet-50 to-white p-4 md:flex-row md:items-center md:justify-between"><div><div className="flex items-center gap-2"><Sparkle className="text-violet-700" weight="fill" /><h2 className="font-black text-slate-950">Product Control Center</h2></div><p className="mt-1 text-xs text-slate-500">تعديل محكوم للمنتج مع مسودة واعتماد وسجل تغييرات. محرك التكاليف مستقل ومحمي.</p></div><div className="flex gap-2"><button onClick={() => setTab("edit")} className={`rounded-lg px-3 py-2 text-xs font-black ${tab === "edit" ? "bg-violet-700 text-white" : "border"}`}>تحرير</button><button onClick={() => setTab("history")} className={`rounded-lg px-3 py-2 text-xs font-black ${tab === "history" ? "bg-violet-700 text-white" : "border"}`}><ClockCounterClockwise className="ml-1 inline" />السجل</button></div></div>
        {tab === "edit" ? <div className="space-y-5 p-4">
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs leading-6 text-emerald-900"><ShieldCheck className="ml-1 inline" weight="fill" />الحقول المحمية لا تدخل في أي نشر: {protectedFields.slice(0, 6).join("، ")}… وتبقى تكاليف المنتج والخيارات والمكونات في نظام ميزان الحالي.</div>
            {notice && <div className={`rounded-xl border p-3 text-xs font-bold ${notice.tone === "rose" ? "border-rose-200 bg-rose-50 text-rose-800" : "border-amber-200 bg-amber-50 text-amber-900"}`}><WarningCircle className="ml-1 inline" />{notice.text}</div>}
            <div className="grid gap-4 md:grid-cols-2">
                {field("name", "اسم المنتج")}
                {field("status", "حالة المنتج", <select value={form.status} onChange={(e) => setForm((row) => ({ ...row, status: e.target.value }))} className="mt-1 w-full rounded-xl border p-3 text-sm"><option value="active">نشط</option><option value="inactive">مخفي</option><option value="out_of_stock">نفد</option></select>)}
                {field("price", "السعر الأساسي", <input type="number" min="0" step="0.01" value={form.price} onChange={(e) => setForm((row) => ({ ...row, price: e.target.value }))} className="mt-1 w-full rounded-xl border p-3 text-sm" />)}
                {field("sale_price", "السعر المخفض", <input type="number" min="0" step="0.01" value={form.sale_price} onChange={(e) => setForm((row) => ({ ...row, sale_price: e.target.value }))} className="mt-1 w-full rounded-xl border p-3 text-sm" />)}
                {field("sale_starts_at", "تاريخ بداية التخفيض", <input type="date" value={form.sale_starts_at} onChange={(e) => setForm((row) => ({ ...row, sale_starts_at: e.target.value }))} className="mt-1 w-full rounded-xl border p-3 text-sm" />)}
                {field("sale_ends_at", "تاريخ نهاية التخفيض", <input type="date" value={form.sale_ends_at} onChange={(e) => setForm((row) => ({ ...row, sale_ends_at: e.target.value }))} className="mt-1 w-full rounded-xl border p-3 text-sm" />)}
                {field("categories", "تصنيفات سلة — IDs مفصولة بفاصلة")}{field("google_category", "Google Product Category")}{field("local_category", "التصنيف المحلي في ميزان")}{field("slug", "رابط المنتج Slug")}{field("seo_title", "عنوان SEO")}{field("keywords", "كلمات SEO — مفصولة بفاصلة")}
            </div>
            {field("short_description", "الوصف المختصر", <textarea rows={3} value={form.short_description} onChange={(e) => setForm((row) => ({ ...row, short_description: e.target.value }))} className="mt-1 w-full rounded-xl border p-3 text-sm" />)}
            {field("seo_description", "وصف SEO", <textarea rows={3} value={form.seo_description} onChange={(e) => setForm((row) => ({ ...row, seo_description: e.target.value }))} className="mt-1 w-full rounded-xl border p-3 text-sm" />)}
            {field("description", "وصف المنتج HTML", <textarea rows={10} value={form.description} onChange={(e) => setForm((row) => ({ ...row, description: e.target.value }))} className="mt-1 w-full rounded-xl border p-3 font-mono text-xs" dir="ltr" />)}
            <label className="block text-xs font-black text-slate-600">سبب التعديل<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="مثال: تحسين الوصف بعد انخفاض الإضافة للسلة" className="mt-1 w-full rounded-xl border p-3 text-sm font-normal" /></label>
            {Object.keys(changes).length > 0 && <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4"><div className="font-black text-amber-950">معاينة التغييرات قبل الحفظ</div><ChangeDiff before={original} after={changes} /></div>}
            <div className="flex flex-wrap justify-end gap-2"><button disabled={busy || !Object.keys(changes).length} onClick={saveDraft} className="rounded-xl bg-slate-900 px-5 py-3 text-sm font-black text-white disabled:opacity-40"><FloppyDisk className="ml-1 inline" />حفظ مسودة</button>{draft?.status === "draft" && <button disabled={busy} onClick={approve} className="rounded-xl bg-amber-500 px-5 py-3 text-sm font-black text-white"><CheckCircle className="ml-1 inline" />اعتماد</button>}{draft?.status === "approved" && <button disabled={busy} onClick={publish} className="rounded-xl bg-emerald-700 px-5 py-3 text-sm font-black text-white">{busy ? <SpinnerGap className="ml-1 inline animate-spin" /> : <PaperPlaneTilt className="ml-1 inline" />}نشر إلى سلة</button>}</div>
            {draft && <div className="rounded-xl border bg-slate-50 p-3 text-xs"><span className="font-black">حالة المسودة:</span> {draft.status} · المصدر: {draft.source || "human"}</div>}
        </div> : <div className="p-4">{!(state?.draft || state?.product) ? <div className="text-sm text-slate-400">لا يوجد سجل بعد.</div> : <div className="space-y-3"><div className="rounded-xl border p-3 text-sm"><ClockCounterClockwise className="ml-1 inline" /> آخر مسودة: {draft?.status || "لا توجد"}</div><div className="rounded-xl border border-violet-100 bg-violet-50 p-3 text-xs leading-6"><WarningCircle className="ml-1 inline" /> كل نشر يحتفظ بالقيم قبل وبعد واستجابة سلة. التكاليف لا تدخل في سجل نشر المحتوى لأنها تدار من محرك التكلفة المستقل.</div></div>}</div>}
    </section>;
}
