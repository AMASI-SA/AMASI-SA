import { useCallback, useEffect, useRef, useState } from "react";
import {
    Image as ImageIcon, UploadSimple, Trash, Pencil, Plus,
    Warning, MagnifyingGlass,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { API_BASE } from "../lib/api";

/**
 * /product-images — "إدارة صور المنتجات".
 *
 * Lists every entry in the user's Product Image Catalog. Each row supports:
 *   - View the saved image
 *   - Replace (re-upload) the image
 *   - Delete the entry
 *
 * The catalog grows organically when the merchant uploads custom images
 * via the preparation page. This page provides:
 *   1. Visibility into what's been auto-saved.
 *   2. The ability to PROACTIVELY add images for products before they
 *      appear in any orders.pdf — so the next file picks them up.
 *
 * Naming: `name_norm` is the URL slug (lowercased, whitespace-collapsed
 * product name). The product_name field retains the original casing.
 */

function ConfirmDelete({ open, name, onConfirm, onCancel }) {
    if (!open) return null;
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4" data-testid="img-delete-modal">
            <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6" style={{ fontFamily: "Tajawal" }}>
                <div className="flex items-start gap-3 mb-4">
                    <div className="w-10 h-10 rounded-full bg-rose-100 text-rose-600 flex items-center justify-center flex-shrink-0">
                        <Warning size={22} weight="fill" />
                    </div>
                    <div className="flex-1">
                        <h3 className="font-extrabold text-lg text-slate-900">حذف الصورة</h3>
                        <p className="text-sm text-slate-600 mt-1">
                            هل أنت متأكد من حذف صورة المنتج <span className="font-bold">"{name}"</span> من الكتالوج؟
                        </p>
                    </div>
                </div>
                <div className="flex gap-2 justify-end">
                    <button type="button" onClick={onCancel} className="px-4 py-2 rounded-lg bg-slate-100 text-slate-700 font-bold hover:bg-slate-200">إلغاء</button>
                    <button type="button" onClick={onConfirm} className="px-4 py-2 rounded-lg font-bold text-white bg-rose-600 hover:bg-rose-700" data-testid="img-delete-confirm-btn">نعم، احذف</button>
                </div>
            </div>
        </div>
    );
}

function AddItemModal({ open, onClose, onSaved }) {
    const fileRef = useRef(null);
    const [name, setName] = useState("");
    const [productId, setProductId] = useState("");
    const [sku, setSku] = useState("");
    const [file, setFile] = useState(null);
    const [busy, setBusy] = useState(false);

    if (!open) return null;

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!name.trim() || !file) {
            toast.error("الاسم والصورة مطلوبان");
            return;
        }
        setBusy(true);
        try {
            const fd = new FormData();
            fd.append("file", file);
            // URL slug = name_norm equivalent
            const slug = name.trim().toLowerCase().replace(/\s+/g, " ");
            const params = new URLSearchParams({ product_name: name.trim() });
            if (productId.trim()) params.set("product_id", productId.trim());
            if (sku.trim()) params.set("sku", sku.trim());
            await api.put(
                `/preparation/image-catalog/${encodeURIComponent(slug)}?${params.toString()}`,
                fd,
                { headers: { "Content-Type": "multipart/form-data" } },
            );
            toast.success("تمت إضافة الصورة إلى الكتالوج");
            setName(""); setProductId(""); setSku(""); setFile(null);
            if (fileRef.current) fileRef.current.value = "";
            onSaved?.();
            onClose();
        } catch (err) {
            toast.error(err?.response?.data?.detail || "فشل حفظ الصورة");
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4" style={{ fontFamily: "Tajawal" }}>
            <form onSubmit={handleSubmit} className="bg-white rounded-xl shadow-xl max-w-lg w-full p-6 space-y-4" data-testid="img-add-modal">
                <h3 className="font-extrabold text-lg text-slate-900">إضافة صورة منتج جديدة</h3>
                <div>
                    <label className="block text-xs font-bold text-slate-700 mb-1">اسم المنتج <span className="text-rose-600">*</span></label>
                    <input
                        type="text"
                        value={name}
                        onChange={e => setName(e.target.value)}
                        placeholder="مثال: قلادة روز بالاسم مطلي ذهب"
                        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        data-testid="img-add-name"
                        required
                    />
                </div>
                <div className="grid grid-cols-2 gap-3">
                    <div>
                        <label className="block text-xs font-bold text-slate-700 mb-1">رقم المنتج (اختياري)</label>
                        <input type="text" value={productId} onChange={e => setProductId(e.target.value)} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" data-testid="img-add-pid" />
                    </div>
                    <div>
                        <label className="block text-xs font-bold text-slate-700 mb-1">SKU (اختياري)</label>
                        <input type="text" value={sku} onChange={e => setSku(e.target.value)} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" data-testid="img-add-sku" />
                    </div>
                </div>
                <div>
                    <label className="block text-xs font-bold text-slate-700 mb-1">الصورة <span className="text-rose-600">*</span></label>
                    <input
                        ref={fileRef}
                        type="file"
                        accept="image/*"
                        onChange={e => setFile(e.target.files?.[0] || null)}
                        className="w-full text-sm"
                        data-testid="img-add-file"
                        required
                    />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                    <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-slate-100 text-slate-700 font-bold hover:bg-slate-200">إلغاء</button>
                    <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-bold" data-testid="img-add-submit">
                        {busy ? "جاري الحفظ…" : "حفظ"}
                    </button>
                </div>
            </form>
        </div>
    );
}

export default function ProductImages() {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [confirmDel, setConfirmDel] = useState(null);  // {name_norm, product_name}
    const [addOpen, setAddOpen] = useState(false);
    const replaceRefs = useRef({});

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/preparation/image-catalog");
            setItems(data.items || []);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل التحميل");
        } finally {
            setLoading(false);
        }
    }, []);
    useEffect(() => { load(); }, [load]);

    const handleDelete = async () => {
        const target = confirmDel;
        setConfirmDel(null);
        if (!target) return;
        try {
            await api.delete(`/preparation/image-catalog/${encodeURIComponent(target.name_norm)}`);
            toast.success(`تم حذف "${target.product_name}"`);
            await load();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل الحذف");
        }
    };

    const handleReplace = async (item, file) => {
        if (!file) return;
        try {
            const fd = new FormData();
            fd.append("file", file);
            const params = new URLSearchParams({ product_name: item.product_name });
            if (item.product_id) params.set("product_id", item.product_id);
            if (item.sku) params.set("sku", item.sku);
            await api.put(
                `/preparation/image-catalog/${encodeURIComponent(item.name_norm)}?${params.toString()}`,
                fd,
                { headers: { "Content-Type": "multipart/form-data" } },
            );
            toast.success("تم تحديث الصورة");
            await load();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل التحديث");
        }
    };

    const filtered = items.filter(it => {
        if (!search.trim()) return true;
        const q = search.trim().toLowerCase();
        return (
            (it.product_name || "").toLowerCase().includes(q) ||
            (it.product_id || "").toLowerCase().includes(q) ||
            (it.sku || "").toLowerCase().includes(q)
        );
    });

    return (
        <div className="p-4 sm:p-6 space-y-6" data-testid="product-images-page" style={{ fontFamily: "Tajawal" }}>
            <div className="flex items-center justify-between gap-3 flex-wrap">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                        <ImageIcon size={28} weight="bold" className="text-indigo-600" />
                        إدارة صور المنتجات
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        كتالوج الصور المحفوظة. عند ظهور أي منتج بدون صورة في ملف تجهيز، تُجلَب الصورة من هنا تلقائياً.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={() => setAddOpen(true)}
                    className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white font-bold"
                    data-testid="img-add-btn"
                >
                    <Plus size={16} weight="bold" /> إضافة صورة
                </button>
            </div>

            <div className="relative max-w-md">
                <MagnifyingGlass size={16} className="absolute top-1/2 -translate-y-1/2 start-3 text-slate-400" />
                <input
                    type="text"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    placeholder="بحث باسم المنتج أو رقمه أو SKU…"
                    className="w-full rounded-lg border border-slate-300 ps-9 pe-3 py-2 text-sm"
                    data-testid="img-search"
                />
            </div>

            {loading ? (
                <div className="text-center text-slate-500 py-12" data-testid="img-loading">جاري التحميل…</div>
            ) : filtered.length === 0 ? (
                <div className="text-center text-slate-500 py-16 bg-white border border-dashed border-slate-300 rounded-xl" data-testid="img-empty">
                    <ImageIcon size={40} className="mx-auto mb-3 text-slate-300" />
                    {items.length === 0
                        ? "الكتالوج فارغ. ارفع صور المنتجات يدوياً أو من صفحة \"تجهيز المنتجات\" — ستُحفظ تلقائياً هنا."
                        : "لا توجد نتائج مطابقة للبحث."}
                </div>
            ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" data-testid="img-grid">
                    {filtered.map(it => (
                        <div key={it.name_norm} className="bg-white border border-slate-200 rounded-xl overflow-hidden flex flex-col" data-testid={`img-card-${it.name_norm.slice(0, 20)}`}>
                            <div className="aspect-square bg-slate-50 flex items-center justify-center">
                                <img
                                    src={`${API_BASE}/preparation/image-catalog/image/${encodeURIComponent(it.name_norm)}?v=${it.updated_at || ""}`}
                                    alt={it.product_name}
                                    className="max-w-full max-h-full object-contain"
                                    loading="lazy"
                                />
                            </div>
                            <div className="p-3 flex-1 flex flex-col">
                                <div className="font-extrabold text-sm text-slate-900 line-clamp-2 min-h-[2.5rem]" title={it.product_name}>
                                    {it.product_name}
                                </div>
                                <div className="text-[11px] text-slate-500 mt-1 space-y-0.5">
                                    {it.product_id ? <div>رقم: <span className="font-mono">{it.product_id}</span></div> : null}
                                    {it.sku ? <div>SKU: <span className="font-mono">{it.sku}</span></div> : null}
                                    {it.updated_at ? <div>محدّث: {new Date(it.updated_at).toLocaleDateString("en-GB")}</div> : null}
                                </div>
                                <div className="flex gap-1.5 mt-3 pt-3 border-t border-slate-100">
                                    <input
                                        ref={el => { replaceRefs.current[it.name_norm] = el; }}
                                        type="file"
                                        accept="image/*"
                                        className="hidden"
                                        onChange={e => handleReplace(it, e.target.files?.[0])}
                                    />
                                    <button
                                        type="button"
                                        onClick={() => replaceRefs.current[it.name_norm]?.click()}
                                        className="flex-1 inline-flex items-center justify-center gap-1 px-2 py-1.5 rounded-md bg-indigo-50 hover:bg-indigo-100 text-indigo-700 text-xs font-bold border border-indigo-200"
                                        data-testid={`img-replace-${it.name_norm.slice(0, 20)}`}
                                    >
                                        <UploadSimple size={12} weight="bold" /> تعديل
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => setConfirmDel({ name_norm: it.name_norm, product_name: it.product_name })}
                                        className="inline-flex items-center justify-center px-2 py-1.5 rounded-md bg-rose-50 hover:bg-rose-100 text-rose-700 text-xs font-bold border border-rose-200"
                                        data-testid={`img-delete-${it.name_norm.slice(0, 20)}`}
                                    >
                                        <Trash size={12} weight="bold" />
                                    </button>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            <ConfirmDelete
                open={!!confirmDel}
                name={confirmDel?.product_name}
                onConfirm={handleDelete}
                onCancel={() => setConfirmDel(null)}
            />
            <AddItemModal open={addOpen} onClose={() => setAddOpen(false)} onSaved={load} />
        </div>
    );
}
