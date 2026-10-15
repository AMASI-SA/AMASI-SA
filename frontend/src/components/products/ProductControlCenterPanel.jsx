import { useEffect, useMemo, useState } from "react";
import {
    CheckCircle, ClockCounterClockwise, FloppyDisk, MagnifyingGlass,
    PaperPlaneTilt, ShieldCheck, Sparkle, SpinnerGap, WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../../lib/api";
import VisualHtmlEditor from "./VisualHtmlEditor";
import {
    approveProductControlDraft, getProductControlCenter, getSallaCategoryCatalog,
    publishProductControlDraft, saveProductControlDraft,
} from "../../services/mezanProductsV2";

const EMPTY = {
    name: "", price: "", sale_price: "", salla_cost_price: "", sale_starts_at: "", sale_ends_at: "",
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
        salla_cost_price: product?.salla_cost_price ?? product?.cost_price_from_salla ?? "",
        sale_starts_at: dateInput(product?.sale_starts_at),
        sale_ends_at: dateInput(product?.sale_ends_at),
        status: product?.status || "active",
        short_description: product?.short_description || "",
        description: product?.description_html || product?.description || "",
        categories: (product?.categories || []).map((row) => row?.id || row?.name || row).filter(Boolean).join(","),
        google_category: product?.google_category || "",
        local_category: product?.local_category || "",
        seo_title: seo.title || "",
        seo_description: seo.description || "",
        keywords: (seo.keywords || []).join(", "),
        slug: seo.slug || product?.slug || "",
    };
}

function mergeDraft(product = {}, changes = {}) {
    const merged = { ...product, ...changes };
    const baseSeo = product?.seo && typeof product.seo === "object" ? product.seo : {};
    const draftSeo = changes?.seo && typeof changes.seo === "object" ? changes.seo : {};
    merged.seo = { ...baseSeo, ...draftSeo };
    if (Object.prototype.hasOwnProperty.call(changes, "description")) merged.description_html = changes.description;
    return merged;
}

function buildChanges(form, original) {
    const changes = {};
    const text = (value) => String(value ?? "").trim();
    if (text(form.name) !== text(original.name)) changes.name = text(form.name);
    if (String(form.price) !== String(original.price)) changes.price = form.price === "" ? null : Number(form.price);
    if (String(form.sale_price) !== String(original.sale_price)) changes.sale_price = form.sale_price === "" ? null : Number(form.sale_price);
    if (String(form.salla_cost_price) !== String(original.salla_cost_price)) changes.salla_cost_price = form.salla_cost_price === "" ? null : Number(form.salla_cost_price);
    if (form.sale_starts_at !== original.sale_starts_at) changes.sale_starts_at = form.sale_starts_at || null;
    if (form.sale_ends_at !== original.sale_ends_at) changes.sale_ends_at = form.sale_ends_at || null;
    if (form.status !== original.status) changes.status = form.status;
    if (form.short_description !== original.short_description) changes.short_description = form.short_description;
    if (form.description !== original.description) changes.description = form.description;
    if (form.categories !== original.categories) changes.categories = form.categories.split(",").map((value) => value.trim()).filter(Boolean);
    if (form.google_category !== original.google_category) changes.google_category = text(form.google_category) || null;
    if (form.local_category !== original.local_category) changes.local_category = text(form.local_category) || null;
    const seo = {};
    if (form.seo_title !== original.seo_title) seo.title = form.seo_title;
    if (form.seo_description !== original.seo_description) seo.description = form.seo_description;
    if (form.keywords !== original.keywords) seo.keywords = form.keywords.split(",").map((value) => value.trim()).filter(Boolean);
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

function CategoryPicker({ value, items, loading, onChange }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState("");
    const selected = String(value || "").split(",").map((row) => row.trim()).filter(Boolean);
    const byId = useMemo(() => Object.fromEntries(items.map((row) => [String(row.id), row])), [items]);
    const visible = items.filter((row) => `${row.name} ${row.path}`.toLowerCase().includes(query.toLowerCase())).slice(0, 100);
    function toggle(id) {
        const key = String(id);
        const next = selected.includes(key) ? selected.filter((row) => row !== key) : [...selected, key];
        onChange(next.join(","));
    }
    return <div
        className="relative"
        tabIndex={-1}
        onMouseLeave={() => setOpen(false)}
        onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
        }}
        onKeyDown={(event) => {
            if (event.key === "Escape") setOpen(false);
        }}
    >
        <div className="text-xs font-black text-slate-600">تصنيفات سلة</div>
        <button type="button" aria-expanded={open} onClick={() => setOpen((row) => !row)} className="mt-1 min-h-12 w-full rounded-xl border bg-white p-3 text-right text-sm">
            {!selected.length ? "اختر التصنيفات…" : selected.map((id) => byId[id]?.path || byId[id]?.name || `تصنيف ${id}`).join("، ")}
        </button>
        {open && <div className="absolute z-50 mt-2 w-full overflow-hidden rounded-2xl border bg-white shadow-2xl">
            <label className="flex items-center gap-2 border-b p-3"><MagnifyingGlass className="text-slate-400" /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث باسم التصنيف…" className="min-w-0 flex-1 outline-none" /></label>
            <div className="max-h-72 overflow-auto p-2">
                {loading ? <div className="p-6 text-center"><SpinnerGap className="inline animate-spin" /></div> : visible.map((row) => <label key={row.id} className="flex cursor-pointer items-center gap-3 rounded-xl p-3 hover:bg-slate-50"><input type="checkbox" checked={selected.includes(String(row.id))} onChange={() => toggle(row.id)} /><span><b>{row.name}</b><small className="block text-slate-400">{row.path}</small></span></label>)}
                {!loading && !visible.length && <div className="p-5 text-center text-sm text-slate-400">لا توجد نتيجة.</div>}
            </div>
        </div>}
    </div>;
}

function GoogleCategoryPicker({ value, items, loading, error, version, onChange }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState("");
    const selected = useMemo(() => {
        const current = String(value || "").trim();
        if (!current) return null;
        return items.find((row) => String(row.id) === current || String(row.path) === current) || null;
    }, [items, value]);
    const visible = useMemo(() => {
        const needle = query.trim().toLocaleLowerCase("ar");
        const rows = needle
            ? items.filter((row) => `${row.id} ${row.name} ${row.path}`.toLocaleLowerCase("ar").includes(needle))
            : items;
        return rows.slice(0, 100);
    }, [items, query]);

    function choose(row) {
        onChange(String(row.id));
        setQuery("");
        setOpen(false);
    }

    return <div
        className="relative"
        tabIndex={-1}
        onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
        }}
        onKeyDown={(event) => {
            if (event.key === "Escape") setOpen(false);
        }}
    >
        <div className="flex items-center justify-between gap-2 text-xs font-black text-slate-600">
            <span>Google Product Category</span>
            {version && <span className="font-normal text-slate-400">Google {version}</span>}
        </div>
        <button
            type="button"
            aria-expanded={open}
            data-testid="google-product-category-picker"
            onClick={() => setOpen((current) => !current)}
            className="mt-1 min-h-12 w-full rounded-xl border bg-white p-3 text-right text-sm"
        >
            {loading
                ? "جاري تحميل تصنيفات Google…"
                : selected
                    ? <span><b>{selected.name}</b><small className="mt-1 block text-slate-400">{selected.path} · ID {selected.id}</small></span>
                    : value
                        ? <span className="text-amber-700">القيمة الحالية غير موجودة في القائمة الرسمية: {value}</span>
                        : "اختر تصنيف Google…"}
        </button>
        {error && <div className="mt-1 text-xs font-bold text-rose-600">{error}</div>}
        {open && <div className="absolute z-[60] mt-2 w-full min-w-[360px] overflow-hidden rounded-2xl border bg-white shadow-2xl">
            <label className="flex items-center gap-2 border-b p-3">
                <MagnifyingGlass className="text-slate-400" />
                <input
                    autoFocus
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="ابحث بالعربي أو برقم Google ID…"
                    className="min-w-0 flex-1 outline-none"
                />
            </label>
            <div className="max-h-80 overflow-auto p-2">
                {loading && <div className="p-6 text-center"><SpinnerGap className="inline animate-spin" /></div>}
                {!loading && visible.map((row) => <button
                    type="button"
                    key={row.id}
                    onClick={() => choose(row)}
                    className={`block w-full rounded-xl p-3 text-right hover:bg-violet-50 ${String(row.id) === String(value) ? "bg-violet-50" : ""}`}
                >
                    <div className="flex items-start justify-between gap-3">
                        <span><b>{row.name}</b><small className="mt-1 block text-slate-400">{row.path}</small></span>
                        <span className="shrink-0 rounded-md bg-slate-100 px-2 py-1 text-[11px] font-bold text-slate-500">ID {row.id}</span>
                    </div>
                </button>)}
                {!loading && !visible.length && <div className="p-5 text-center text-sm text-slate-400">لا توجد نتيجة في Taxonomy الرسمية.</div>}
            </div>
            <div className="flex items-center justify-between gap-3 border-t bg-slate-50 p-2 text-xs text-slate-500">
                <span>{items.length.toLocaleString("en-US")} تصنيف من Google</span>
                {value && <button type="button" onClick={() => { onChange(""); setOpen(false); }} className="rounded-lg border bg-white px-3 py-1 font-bold text-rose-600">مسح التصنيف</button>}
            </div>
        </div>}
    </div>;
}

function ChangeDiff({ before = {}, after = {} }) {
    const rows = Object.keys(after);
    if (!rows.length) return null;
    return <div className="mt-4 space-y-2">{rows.map((field) => <div key={field} className="grid gap-2 rounded-xl border bg-slate-50 p-3 text-xs md:grid-cols-[140px_1fr_1fr]"><b>{field}</b><div><span className="text-slate-400">قبل:</span> {JSON.stringify(before[field] ?? "")}</div><div><span className="text-violet-500">بعد:</span> <b>{JSON.stringify(after[field] ?? "")}</b></div></div>)}</div>;
}

export default function ProductControlCenterPanel({ productId, product, onPublished }) {
    const [state, setState] = useState(null);
    const [form, setForm] = useState(EMPTY);
    const [original, setOriginal] = useState(EMPTY);
    const [reason, setReason] = useState("");
    const [busy, setBusy] = useState(false);
    const [tab, setTab] = useState("history");
    const [categories, setCategories] = useState([]);
    const [categoriesLoading, setCategoriesLoading] = useState(false);
    const [googleTaxonomy, setGoogleTaxonomy] = useState([]);
    const [googleTaxonomyLoading, setGoogleTaxonomyLoading] = useState(false);
    const [googleTaxonomyError, setGoogleTaxonomyError] = useState("");
    const [googleTaxonomyVersion, setGoogleTaxonomyVersion] = useState("");

    async function load() {
        if (!productId) return;
        try {
            const result = await getProductControlCenter(productId);
            setState(result);
            const currentProduct = result.product || product || {};
            const current = hydrate(currentProduct);
            const visible = hydrate(mergeDraft(currentProduct, result.draft?.changes || {}));
            setOriginal(current);
            setForm(visible);
            setReason(result.draft?.reason || "");
        } catch (error) { toast.error(error?.response?.data?.detail?.message || "تعذر تحميل مركز التحكم بالمنتج"); }
    }

    useEffect(() => {
        setTab("history");
        load();
    }, [productId]); // eslint-disable-line react-hooks/exhaustive-deps
    useEffect(() => {
        let live = true; setCategoriesLoading(true);
        getSallaCategoryCatalog().then((result) => { if (live) setCategories(result.items || []); }).catch(() => {}).finally(() => { if (live) setCategoriesLoading(false); });
        return () => { live = false; };
    }, []);
    useEffect(() => {
        let live = true;
        setGoogleTaxonomyLoading(true);
        setGoogleTaxonomyError("");
        api.get("/products-v2/google-taxonomy")
            .then((response) => {
                if (!live) return;
                const result = response.data || {};
                setGoogleTaxonomy(result.items || []);
                setGoogleTaxonomyVersion(result.version || "");
            })
            .catch((error) => {
                if (!live) return;
                setGoogleTaxonomyError(error?.response?.data?.detail?.message || "تعذر تحميل تصنيفات Google الرسمية.");
            })
            .finally(() => { if (live) setGoogleTaxonomyLoading(false); });
        return () => { live = false; };
    }, []);

    const changes = useMemo(() => buildChanges(form, original), [form, original]);
    const notice = useMemo(() => discountNotice(form), [form]);
    const draft = state?.draft || null;
    const protectedFields = state?.protected_fields || [];
    const draftFields = Object.keys(draft?.changes || {});
    const googleTaxonomyOnlyDraft = draftFields.length === 1 && draftFields[0] === "google_category";

    async function saveDraft() {
        if (!Object.keys(changes).length) return toast.info("لا توجد تغييرات لحفظها");
        setBusy(true);
        try {
            const result = await saveProductControlDraft(productId, { changes, source: "human", reason: reason.trim() || "تعديل من Product Control Center" });
            setState((current) => ({ ...current, draft: result.draft }));
            toast.success("تم حفظ المسودة وإبقاء قيمها ظاهرة");
        } catch (error) { toast.error(error?.response?.data?.detail?.code || "تعذر حفظ المسودة"); }
        finally { setBusy(false); }
    }
    async function approve() { if (!draft) return; setBusy(true); try { const result = await approveProductControlDraft(productId, draft.id); setState((current) => ({ ...current, draft: result.draft })); toast.success("تم اعتماد المسودة"); } finally { setBusy(false); } }
    async function publish() {
        if (!draft) return;
        setBusy(true);
        try {
            const result = await publishProductControlDraft(productId, draft.id);
            const sallaResponse = result?.revision?.salla_response || {};
            if (sallaResponse?.reason === "google_taxonomy_mezan_managed") {
                toast.success("تم اعتماد تصنيف Google في ميزان. لم يُرسل إلى Salla لأن الـAPI العام لا يدعم كتابة هذا الحقل.");
            } else if (sallaResponse?.reason === "google_taxonomy_already_matches") {
                toast.success("تصنيف Google مطابق أصلًا في Salla وتم توثيق المطابقة.");
            } else {
                toast.success("تم نشر التعديل إلى سلة وتسجيل المراجعة");
            }
            await load();
            onPublished?.();
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر تنفيذ التعديل");
        } finally { setBusy(false); }
    }

    const field = (key, label, input) => <label className="block text-xs font-black text-slate-600">{label}{input || <input value={form[key]} onChange={(event) => setForm((row) => ({ ...row, [key]: event.target.value }))} className="mt-1 w-full rounded-xl border p-3 text-sm font-normal" />}</label>;

    return <section className="overflow-hidden rounded-2xl border border-violet-200 bg-white" data-testid="product-control-center">
        <div className="flex justify-between border-b bg-violet-50 p-4"><div><h2 className="font-black"><Sparkle className="ml-1 inline text-violet-700" />Product Control Center</h2><p className="text-xs text-slate-500">تعديل محكوم مع مسودة واعتماد. تكاليف ميزان مستقلة.</p></div><div className="flex gap-2"><button onClick={() => setTab("edit")} className={`rounded-lg px-3 py-2 text-xs font-black ${tab === "edit" ? "bg-violet-700 text-white" : "border"}`}>تحرير</button><button onClick={() => setTab("history")} className={`rounded-lg px-3 py-2 text-xs font-black ${tab === "history" ? "bg-violet-700 text-white" : "border"}`}><ClockCounterClockwise className="inline" /> السجل</button></div></div>
        {tab === "edit" ? <div className="space-y-5 p-4">
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900"><ShieldCheck className="ml-1 inline" />الحقول المحمية: {protectedFields.slice(0, 6).join("، ")}…</div>
            {draft && <div className="rounded-xl border border-violet-200 bg-violet-50 p-3 text-xs font-bold text-violet-900">القيم الظاهرة الآن هي قيم المسودة {draft.status === "approved" ? "المعتمدة" : "المحفوظة"}، وليست بيانات سلة القديمة.</div>}
            {notice && <div className={`rounded-xl border p-3 text-xs font-bold ${notice.tone === "rose" ? "bg-rose-50 text-rose-800" : "bg-amber-50 text-amber-900"}`}><WarningCircle className="ml-1 inline" />{notice.text}</div>}
            <div className="grid gap-4 md:grid-cols-2">
                {field("name", "اسم المنتج")}{field("status", "حالة المنتج", <select value={form.status} onChange={(e) => setForm((r) => ({ ...r, status: e.target.value }))} className="mt-1 w-full rounded-xl border p-3"><option value="active">نشط</option><option value="inactive">مخفي</option><option value="out_of_stock">نفد</option></select>)}
                {field("price", "السعر الأساسي", <input type="number" value={form.price} onChange={(e) => setForm((r) => ({ ...r, price: e.target.value }))} className="mt-1 w-full rounded-xl border p-3" />)}{field("sale_price", "السعر المخفض", <input type="number" value={form.sale_price} onChange={(e) => setForm((r) => ({ ...r, sale_price: e.target.value }))} className="mt-1 w-full rounded-xl border p-3" />)}
                {field("salla_cost_price", "سعر التكلفة في سلة", <input type="number" min="0" step="0.01" value={form.salla_cost_price} onChange={(e) => setForm((r) => ({ ...r, salla_cost_price: e.target.value }))} className="mt-1 w-full rounded-xl border border-emerald-300 bg-emerald-50/30 p-3" />)}
                <div className="self-end rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900"><ShieldCheck className="ml-1 inline" />هذا الحقل يحدّث تكلفة سلة فقط عند النشر، ولا يغيّر تكلفة ميزان.</div>
                {field("sale_starts_at", "تاريخ بداية التخفيض", <input type="date" value={form.sale_starts_at} onChange={(e) => setForm((r) => ({ ...r, sale_starts_at: e.target.value }))} className="mt-1 w-full rounded-xl border p-3" />)}{field("sale_ends_at", "تاريخ نهاية التخفيض", <input type="date" value={form.sale_ends_at} onChange={(e) => setForm((r) => ({ ...r, sale_ends_at: e.target.value }))} className="mt-1 w-full rounded-xl border p-3" />)}
                <CategoryPicker value={form.categories} items={categories} loading={categoriesLoading} onChange={(value) => setForm((r) => ({ ...r, categories: value }))} />
                <GoogleCategoryPicker value={form.google_category} items={googleTaxonomy} loading={googleTaxonomyLoading} error={googleTaxonomyError} version={googleTaxonomyVersion} onChange={(value) => setForm((r) => ({ ...r, google_category: value }))} />
                {field("local_category", "التصنيف المحلي في ميزان")}{field("slug", "رابط المنتج Slug")}{field("seo_title", "عنوان SEO")}{field("keywords", "كلمات SEO")}
            </div>
            <div className="rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs text-sky-900"><b>Google Category:</b> يديره ميزان حاليًا ويُجهّز لاستخدامه في Google Product Feed. لا يرسل ميزان PUT غير موثّق إلى Salla لهذا الحقل.</div>
            {field("short_description", "الوصف المختصر", <textarea rows={3} value={form.short_description} onChange={(e) => setForm((r) => ({ ...r, short_description: e.target.value }))} className="mt-1 w-full rounded-xl border p-3" />)}
            {field("seo_description", "وصف SEO", <textarea rows={3} value={form.seo_description} onChange={(e) => setForm((r) => ({ ...r, seo_description: e.target.value }))} className="mt-1 w-full rounded-xl border p-3" />)}
            <label className="block text-xs font-black text-slate-600">وصف المنتج<VisualHtmlEditor resetKey={productId} value={form.description} onChange={(description) => setForm((r) => ({ ...r, description }))} /></label>
            <label className="block text-xs font-black text-slate-600">سبب التعديل<input value={reason} onChange={(e) => setReason(e.target.value)} className="mt-1 w-full rounded-xl border p-3" /></label>
            {Object.keys(changes).length > 0 && <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4"><b>معاينة التغييرات قبل الحفظ</b><ChangeDiff before={original} after={changes} /></div>}
            <div className="flex flex-wrap justify-end gap-2"><button disabled={busy || !Object.keys(changes).length} onClick={saveDraft} className="rounded-xl bg-slate-900 px-5 py-3 font-black text-white"><FloppyDisk className="inline" /> حفظ مسودة</button>{draft?.status === "draft" && <button onClick={approve} className="rounded-xl bg-amber-500 px-5 py-3 font-black text-white"><CheckCircle className="inline" /> اعتماد</button>}{draft?.status === "approved" && <button onClick={publish} className="rounded-xl bg-emerald-700 px-5 py-3 font-black text-white"><PaperPlaneTilt className="inline" /> {googleTaxonomyOnlyDraft ? "اعتماد تصنيف Google في ميزان" : "نشر إلى سلة"}</button>}</div>
            {draft && <div className="rounded-xl border bg-slate-50 p-3 text-xs"><b>حالة المسودة:</b> {draft.status}</div>}
        </div> : <div className="p-4 text-sm">آخر مسودة: {draft?.status || "لا توجد"}</div>}
    </section>;
}