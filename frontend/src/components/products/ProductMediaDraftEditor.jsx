import { useEffect, useMemo, useState } from "react";
import {
    ArrowDown, ArrowUp, CheckCircle, FloppyDisk, ImageSquare,
    PaperPlaneTilt, Plus, SpinnerGap, Trash, UploadSimple, WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import ProductMediaAiProposalPanel from "./ProductMediaAiProposalPanel";
import ProductPreparationImageProfile from "./ProductPreparationImageProfile";
import {
    approveProductMediaDraft,
    deleteProductMediaUpload,
    getProductMediaControl,
    publishProductMediaDraft,
    saveProductMediaDraft,
    uploadProductMediaFile,
} from "../../services/mezanProductsV2";

function previewUrl(value) {
    const url = String(value || "");
    if (typeof window === "undefined" || !url) return url;
    try {
        const parsed = new URL(url, window.location.origin);
        if (parsed.pathname.includes("/api/products-v2/media-upload/file/")) {
            return `${window.location.origin}${parsed.pathname}${parsed.search}`;
        }
    } catch { /* preserve original URL */ }
    return url;
}

function cloneImages(rows = []) {
    return rows.map((row, index) => ({
        id: row.id || null,
        url: previewUrl(row.url),
        alt: row.alt || "",
        is_main: Boolean(row.is_main || row.main || index === 0),
        sort: index + 1,
        source: row.source || (row.id ? "salla" : "external_url"),
        upload_token: row.upload_token || null,
        filename: row.filename || null,
    }));
}

function normalizeMain(rows) {
    let found = false;
    return rows.map((row, index) => {
        const main = row.is_main && !found;
        if (main) found = true;
        return { ...row, is_main: main || (!found && index === 0), sort: index + 1 };
    });
}

export default function ProductMediaDraftEditor({ productId, images = [], onPublished }) {
    const [state, setState] = useState(null);
    const [draftImages, setDraftImages] = useState(() => cloneImages(images));
    const [reason, setReason] = useState("");
    const [newUrl, setNewUrl] = useState("");
    const [busy, setBusy] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [editing, setEditing] = useState(false);

    async function load() {
        if (!productId) return;
        try {
            const result = await getProductMediaControl(productId);
            setState(result);
            const visible = result.draft?.images?.length ? result.draft.images : result.current_images || images;
            setDraftImages(cloneImages(visible));
            setReason(result.draft?.reason || "");
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر تحميل إدارة صور المنتج");
        }
    }

    useEffect(() => {
        setEditing(false);
        setNewUrl("");
        load();
    }, [productId]); // eslint-disable-line react-hooks/exhaustive-deps

    const current = state?.current_images || images || [];
    const draft = state?.draft || null;
    const permissions = state?.permissions || {};
    const changed = useMemo(() => JSON.stringify(cloneImages(current)) !== JSON.stringify(cloneImages(draftImages)), [current, draftImages]);

    function move(index, delta) {
        const target = index + delta;
        if (target < 0 || target >= draftImages.length) return;
        const next = [...draftImages];
        [next[index], next[target]] = [next[target], next[index]];
        setDraftImages(normalizeMain(next));
    }

    function setMain(index) {
        setDraftImages(draftImages.map((row, rowIndex) => ({ ...row, is_main: rowIndex === index })));
    }

    async function remove(index) {
        if (draftImages.length <= 1) return toast.error("يجب أن يبقى للمنتج صورة واحدة على الأقل");
        const row = draftImages[index];
        if (row.upload_token) {
            try { await deleteProductMediaUpload(productId, row.upload_token); } catch { /* TTL cleanup remains */ }
        }
        const next = draftImages.filter((_, rowIndex) => rowIndex !== index);
        setDraftImages(normalizeMain(next));
    }

    function addUrl() {
        const url = newUrl.trim();
        if (!/^https?:\/\//i.test(url)) return toast.error("أدخل رابط صورة صحيح يبدأ بـ http أو https");
        if (draftImages.length >= 10) return toast.error("سلة تسمح بحد أقصى 10 صور للمنتج");
        if (draftImages.some((row) => row.url.split("?", 1)[0] === url.split("?", 1)[0])) return toast.error("الصورة موجودة بالفعل");
        setDraftImages([...draftImages, { id: null, url, alt: "", is_main: draftImages.length === 0, sort: draftImages.length + 1, source: "external_url" }]);
        setNewUrl("");
    }

    async function uploadFile(file) {
        if (!file) return;
        if (draftImages.length >= 10) return toast.error("سلة تسمح بحد أقصى 10 صور للمنتج");
        setUploading(true);
        try {
            const result = await uploadProductMediaFile(productId, file);
            const uploaded = { ...result.image, url: previewUrl(result.image?.url) };
            setDraftImages((rows) => normalizeMain([...rows, { ...uploaded, is_main: rows.length === 0 }]));
            toast.success("تم رفع الصورة مؤقتًا داخل ميزان وإضافتها للمسودة");
        } catch (error) {
            const code = error?.response?.data?.detail?.code;
            toast.error(code === "image_too_large" ? "حجم الصورة أكبر من 5 MB" : code === "unsupported_image_type" || code === "image_signature_mismatch" ? "الصيغة المسموحة JPG أو PNG أو WEBP" : "تعذر رفع الصورة");
        } finally {
            setUploading(false);
        }
    }

    async function save() {
        setBusy(true);
        try {
            const result = await saveProductMediaDraft(productId, { images: normalizeMain(draftImages), reason: reason.trim() || "تعديل صور المنتج من ميزان" });
            setState((currentState) => ({ ...currentState, draft: result.draft }));
            setDraftImages(cloneImages(result.draft.images));
            toast.success("تم حفظ مسودة الصور دون تعديل سلة");
        } catch (error) {
            toast.error(error?.response?.data?.detail?.code || "تعذر حفظ مسودة الصور");
        } finally { setBusy(false); }
    }

    async function approve() {
        if (!draft) return;
        setBusy(true);
        try {
            const result = await approveProductMediaDraft(productId, draft.id);
            setState((currentState) => ({ ...currentState, draft: result.draft }));
            toast.success("تم اعتماد مسودة الصور");
        } catch (error) { toast.error(error?.response?.data?.detail?.code || "تعذر اعتماد مسودة الصور"); }
        finally { setBusy(false); }
    }

    async function publish() {
        if (!draft) return;
        setBusy(true);
        try {
            await publishProductMediaDraft(productId, draft.id);
            toast.success("تم نشر صور المنتج إلى سلة وتسجيل النسخة السابقة");
            await load();
            setEditing(false);
            onPublished?.();
        } catch (error) { toast.error(error?.response?.data?.detail?.message || "تعذر نشر الصور إلى سلة"); }
        finally { setBusy(false); }
    }

    if (!editing) {
        return <div className="space-y-4">
            <section className="rounded-2xl border p-3 sm:p-4" data-testid="product-media-summary">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="font-black"><ImageSquare className="ml-1 inline" /> الصور الحالية ({current.length})</h2><p className="mt-1 text-xs text-slate-500">التعديل يمر بمسودة واعتماد قبل إرسال أي شيء إلى سلة.</p></div><button type="button" onClick={() => setEditing(true)} disabled={!permissions.edit} className="rounded-xl bg-violet-700 px-4 py-2 font-black text-white disabled:opacity-40">إدارة الصور</button></div>
                <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5">{current.map((row) => <div key={row.id || row.url} className="relative"><img src={previewUrl(row.url)} alt={row.alt || ""} className="aspect-square w-full rounded-xl border object-cover" />{row.is_main && <span className="absolute right-2 top-2 rounded-full bg-emerald-600 px-2 py-1 text-[10px] font-black text-white">الرئيسية</span>}</div>)}</div>
                {draft && <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-900">توجد مسودة صور بحالة: {draft.status}</div>}
            </section>
            <ProductPreparationImageProfile productId={productId} />
            <ProductMediaAiProposalPanel productId={productId} images={draft?.images?.length ? draft.images : current} />
        </div>;
    }

    return <div className="space-y-4">
        <section className="rounded-2xl border border-violet-200 bg-violet-50/30 p-3 sm:p-4" data-testid="product-media-editor">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="font-black"><ImageSquare className="ml-1 inline" /> إدارة صور المنتج</h2><p className="text-xs text-slate-500">المعروض الآن مسودة داخل ميزان حتى الاعتماد والنشر.</p></div><button type="button" onClick={() => { setEditing(false); load(); }} className="rounded-xl border bg-white px-4 py-2 font-bold">إغلاق التحرير</button></div>
            <div className="mt-4 grid gap-2 lg:grid-cols-[1fr_auto_auto]"><input value={newUrl} onChange={(event) => setNewUrl(event.target.value)} placeholder="ألصق رابط صورة جديدة…" className="min-w-0 rounded-xl border bg-white p-3" dir="ltr" /><button type="button" onClick={addUrl} className="rounded-xl border border-violet-300 bg-white px-4 py-3 font-black text-violet-800"><Plus className="inline" /> إضافة من رابط</button><label className="cursor-pointer rounded-xl bg-violet-700 px-4 py-3 text-center font-black text-white"><input type="file" accept="image/jpeg,image/png,image/webp" className="hidden" disabled={uploading} onChange={(event) => { uploadFile(event.target.files?.[0]); event.target.value = ""; }} />{uploading ? <SpinnerGap className="inline animate-spin" /> : <UploadSimple className="inline" />} رفع من الجهاز</label></div>
            <p className="mt-2 text-[11px] text-slate-500">JPG أو PNG أو WEBP · الحد الأقصى 5 MB · الحفظ مؤقت لمدة 7 أيام.</p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{draftImages.map((row, index) => <article key={`${row.id || row.url}-${index}`} className="overflow-hidden rounded-2xl border bg-white"><div className="relative"><img src={previewUrl(row.url)} alt={row.alt || ""} className="aspect-square w-full object-cover" />{row.is_main && <span className="absolute right-2 top-2 rounded-full bg-emerald-600 px-2 py-1 text-[10px] font-black text-white">الرئيسية</span>}{row.source === "temporary_upload" && <span className="absolute left-2 top-2 rounded-full bg-violet-700 px-2 py-1 text-[10px] font-black text-white">مؤقتة</span>}</div><div className="space-y-3 p-3"><label className="block text-xs font-bold text-slate-600">ALT<input value={row.alt || ""} onChange={(event) => setDraftImages(draftImages.map((item, rowIndex) => rowIndex === index ? { ...item, alt: event.target.value } : item))} className="mt-1 w-full rounded-xl border p-2 text-sm" /></label><label className="flex items-center gap-2 text-xs font-bold"><input type="radio" name={`main-${productId}`} checked={row.is_main} onChange={() => setMain(index)} /> جعلها الصورة الرئيسية</label><div className="grid grid-cols-3 gap-2"><button type="button" onClick={() => move(index, -1)} disabled={index === 0} className="rounded-lg border p-2 disabled:opacity-30"><ArrowUp className="mx-auto" /></button><button type="button" onClick={() => move(index, 1)} disabled={index === draftImages.length - 1} className="rounded-lg border p-2 disabled:opacity-30"><ArrowDown className="mx-auto" /></button><button type="button" onClick={() => remove(index)} className="rounded-lg border border-rose-200 p-2 text-rose-600"><Trash className="mx-auto" /></button></div></div></article>)}</div>
            <label className="mt-4 block text-xs font-bold text-slate-600">سبب التعديل<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="مثال: تحسين الصورة الرئيسية وALT" className="mt-1 w-full rounded-xl border bg-white p-3" /></label>
            <div className="mt-4 flex flex-wrap justify-end gap-2"><button type="button" disabled={busy || uploading || !permissions.edit || !changed} onClick={save} className="rounded-xl bg-slate-900 px-5 py-3 font-black text-white disabled:opacity-40">{busy ? <SpinnerGap className="inline animate-spin" /> : <FloppyDisk className="inline" />} حفظ مسودة</button>{draft?.status === "draft" && <button type="button" disabled={busy || !permissions.approve} onClick={approve} className="rounded-xl bg-amber-500 px-5 py-3 font-black text-white disabled:opacity-40"><CheckCircle className="inline" /> اعتماد</button>}{draft?.status === "approved" && <button type="button" disabled={busy || !permissions.publish} onClick={publish} className="rounded-xl bg-emerald-700 px-5 py-3 font-black text-white disabled:opacity-40"><PaperPlaneTilt className="inline" /> نشر إلى سلة</button>}</div>
            {!permissions.publish && <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900"><WarningCircle className="ml-1 inline" /> النشر يتطلب صلاحية products.media.publish.</div>}
        </section>
        <ProductPreparationImageProfile productId={productId} />
        <ProductMediaAiProposalPanel productId={productId} images={draftImages} />
    </div>;
}
