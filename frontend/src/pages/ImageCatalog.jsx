import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    Package, MagnifyingGlass, Trash, UploadSimple, Image as ImageIcon,
    ImageBroken, Plus, Warning, CheckCircle, ArrowsClockwise, X,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { API_BASE } from "../lib/api";

/**
 * /image-catalog — "إدارة صور المنتجات"
 *
 * The merchant manages the global product-image catalog that powers
 * automatic image enrichment in /product-preparation. Any image uploaded
 * (either here OR via the inline "إضافة صورة" button on a preparation
 * page) is stored in `product_image_catalog` and re-used on the next
 * Salla orders PDF that contains the same product_name.
 */

function CatalogImage({ nameNorm, version }) {
    const [errored, setErrored] = useState(false);
    const url = `${API_BASE}/preparation/image-catalog/image/${encodeURIComponent(nameNorm)}${version ? `?v=${version}` : ""}`;
    if (errored) {
        return (
            <div className="w-14 h-14 rounded-lg bg-slate-100 flex items-center justify-center text-slate-400 flex-shrink-0">
                <ImageBroken size={20} />
            </div>
        );
    }
    return (
        <img
            src={url}
            alt="product"
            onError={() => setErrored(true)}
            className="w-14 h-14 rounded-lg object-cover border border-slate-200 flex-shrink-0"
            loading="lazy"
            data-testid={`catalog-img-${nameNorm.slice(0, 12)}`}
        />
    );
}

function ConfirmDeleteModal({ open, item, onConfirm, onCancel }) {
    if (!open) return null;
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4" data-testid="catalog-delete-modal">
            <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6" style={{ fontFamily: "Tajawal" }}>
                <div className="flex items-start gap-3 mb-4">
                    <div className="w-10 h-10 rounded-full bg-rose-100 text-rose-600 flex items-center justify-center flex-shrink-0">
                        <Warning size={22} weight="fill" />
                    </div>
                    <div className="flex-1">
                        <h3 className="font-extrabold text-lg text-slate-900">حذف صورة المنتج</h3>
                        <p className="text-sm text-slate-600 mt-1 leading-relaxed">
                            هل أنت متأكد من حذف صورة "<span className="font-bold">{item?.product_name}</span>" من الكتالوج؟
                            <br />
                            ملف التجهيز القادم لن يجد صورة لهذا المنتج تلقائياً.
                        </p>
                    </div>
                </div>
                <div className="flex gap-2 justify-end">
                    <button
                        type="button"
                        onClick={onCancel}
                        className="px-4 py-2 rounded-lg bg-slate-100 text-slate-700 font-bold hover:bg-slate-200"
                        data-testid="catalog-delete-cancel-btn"
                    >إلغاء</button>
                    <button
                        type="button"
                        onClick={onConfirm}
                        className="px-4 py-2 rounded-lg font-bold text-white bg-rose-600 hover:bg-rose-700"
                        data-testid="catalog-delete-confirm-btn"
                    >نعم، احذف</button>
                </div>
            </div>
        </div>
    );
}

function UploadModal({ open, onClose, onUploaded, existing }) {
    const fileRef = useRef(null);
    const [busy, setBusy] = useState(false);
    const [productName, setProductName] = useState("");
    const [productId, setProductId] = useState("");
    const [sku, setSku] = useState("");
    const [preview, setPreview] = useState(null);
    const isEdit = !!existing;

    useEffect(() => {
        if (open) {
            setProductName(existing?.product_name || "");
            setProductId(existing?.product_id || "");
            setSku(existing?.sku || "");
            setPreview(null);
            if (fileRef.current) fileRef.current.value = "";
        }
    }, [open, existing]);

    const handleFile = (file) => {
        if (!file) return;
        if (!file.type.startsWith("image/")) {
            toast.error("الملف يجب أن يكون صورة (PNG/JPG/WEBP)");
            return;
        }
        const reader = new FileReader();
        reader.onload = (e) => setPreview(e.target.result);
        reader.readAsDataURL(file);
    };

    const submit = async () => {
        const file = fileRef.current?.files?.[0];
        const name = productName.trim();
        if (!isEdit && !file) {
            toast.error("اختر صورة المنتج أولاً");
            return;
        }
        if (!name) {
            toast.error("اسم المنتج مطلوب");
            return;
        }
        setBusy(true);
        try {
            const fd = new FormData();
            if (file) fd.append("file", file);
            const params = new URLSearchParams();
            params.set("product_name", name);
            if (productId.trim()) params.set("product_id", productId.trim());
            if (sku.trim()) params.set("sku", sku.trim());
            // When editing, the URL slug is the old name_norm to keep the existing row.
            // When adding new, we use the lowercase trimmed name as the slug.
            const slug = isEdit
                ? existing.name_norm
                : name.toLowerCase().split(/\s+/).filter(Boolean).join(" ");
            await api.put(
                `/preparation/image-catalog/${encodeURIComponent(slug)}?${params.toString()}`,
                fd,
                { headers: { "Content-Type": "multipart/form-data" } },
            );
            toast.success(isEdit ? "تم تحديث صورة المنتج" : "تمت إضافة الصورة إلى الكتالوج");
            onUploaded?.();
            onClose();
        } catch (err) {
            toast.error(err?.response?.data?.detail || "فشل الحفظ");
        } finally {
            setBusy(false);
        }
    };

    if (!open) return null;
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4" data-testid="catalog-upload-modal">
            <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6" style={{ fontFamily: "Tajawal" }}>
                <div className="flex items-start justify-between gap-3 mb-4">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-full bg-indigo-100 text-indigo-600 flex items-center justify-center flex-shrink-0">
                            <ImageIcon size={22} weight="bold" />
                        </div>
                        <div>
                            <h3 className="font-extrabold text-lg text-slate-900">
                                {isEdit ? "تعديل صورة المنتج" : "إضافة صورة منتج جديد"}
                            </h3>
                            <p className="text-xs text-slate-500 mt-0.5">
                                {isEdit ? "استبدل الصورة أو حدّث المعلومات" : "ستُستخدم هذه الصورة تلقائياً في كل ملفات التجهيز القادمة."}
                            </p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        className="p-1 rounded text-slate-500 hover:bg-slate-100"
                        data-testid="catalog-upload-close-btn"
                    ><X size={18} /></button>
                </div>

                <div className="space-y-3">
                    <div>
                        <label className="block text-sm font-bold text-slate-700 mb-1">اسم المنتج *</label>
                        <input
                            type="text"
                            value={productName}
                            onChange={(e) => setProductName(e.target.value)}
                            disabled={isEdit}
                            placeholder="مثال: قلادة روز"
                            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:bg-slate-100 disabled:text-slate-600"
                            data-testid="catalog-input-name"
                            style={{ fontFamily: "Tajawal" }}
                        />
                        {isEdit && (
                            <p className="text-[11px] text-slate-500 mt-1">لتغيير الاسم، احذف الصورة وأضفها بالاسم الجديد.</p>
                        )}
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className="block text-sm font-bold text-slate-700 mb-1">رقم المنتج (اختياري)</label>
                            <input
                                type="text"
                                value={productId}
                                onChange={(e) => setProductId(e.target.value)}
                                placeholder="Product ID"
                                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                                data-testid="catalog-input-product-id"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-bold text-slate-700 mb-1">SKU (اختياري)</label>
                            <input
                                type="text"
                                value={sku}
                                onChange={(e) => setSku(e.target.value)}
                                placeholder="SKU"
                                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                                data-testid="catalog-input-sku"
                            />
                        </div>
                    </div>
                    <div>
                        <label className="block text-sm font-bold text-slate-700 mb-1">
                            صورة المنتج {isEdit ? "(اتركها فارغة للإبقاء على الحالية)" : "*"}
                        </label>
                        <input
                            ref={fileRef}
                            type="file"
                            accept="image/*"
                            onChange={(e) => handleFile(e.target.files?.[0])}
                            className="w-full text-sm file:mr-3 file:py-2 file:px-3 file:rounded-lg file:border-0 file:font-bold file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100"
                            data-testid="catalog-file-input"
                        />
                        {preview && (
                            <div className="mt-2 flex items-center gap-3">
                                <img src={preview} alt="preview" className="w-20 h-20 rounded-lg object-cover border border-slate-200" />
                                <span className="text-xs text-slate-500">معاينة الصورة الجديدة</span>
                            </div>
                        )}
                        {!preview && isEdit && (
                            <div className="mt-2 flex items-center gap-3">
                                <CatalogImage nameNorm={existing.name_norm} />
                                <span className="text-xs text-slate-500">الصورة الحالية</span>
                            </div>
                        )}
                    </div>
                    <div className="text-[11px] text-slate-500 bg-slate-50 border border-slate-200 rounded-lg p-2 leading-relaxed">
                        الصورة يتم تصغيرها تلقائياً إلى 800px (أقصى ضلع) ويعاد ترميزها JPEG لتقليل المساحة. الحد الأقصى للحجم 8MB.
                    </div>
                </div>

                <div className="flex gap-2 justify-end mt-4">
                    <button
                        type="button"
                        onClick={onClose}
                        className="px-4 py-2 rounded-lg bg-slate-100 text-slate-700 font-bold hover:bg-slate-200"
                        data-testid="catalog-upload-cancel-btn"
                    >إلغاء</button>
                    <button
                        type="button"
                        onClick={submit}
                        disabled={busy}
                        className="px-4 py-2 rounded-lg font-bold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50"
                        data-testid="catalog-upload-submit-btn"
                    >{busy ? "جاري الحفظ…" : (isEdit ? "حفظ التعديلات" : "إضافة إلى الكتالوج")}</button>
                </div>
            </div>
        </div>
    );
}

function formatDate(iso) {
    if (!iso) return "—";
    try {
        const d = new Date(iso);
        return d.toLocaleDateString("ar-SA", { year: "numeric", month: "short", day: "numeric" });
    } catch {
        return iso;
    }
}

export default function ImageCatalog() {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [query, setQuery] = useState("");
    const [imgVersion, setImgVersion] = useState(0);
    const [showUpload, setShowUpload] = useState(false);
    const [editing, setEditing] = useState(null);
    const [deleting, setDeleting] = useState(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/preparation/image-catalog");
            setItems(data.items || []);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل تحميل الكتالوج");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const handleDelete = async () => {
        if (!deleting) return;
        try {
            await api.delete(`/preparation/image-catalog/${encodeURIComponent(deleting.name_norm)}`);
            toast.success(`تم حذف "${deleting.product_name}"`);
            setDeleting(null);
            await load();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل الحذف");
        }
    };

    const handleUploaded = async () => {
        setImgVersion(v => v + 1);
        await load();
    };

    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return items;
        return items.filter(it =>
            (it.product_name || "").toLowerCase().includes(q) ||
            (it.product_id || "").toLowerCase().includes(q) ||
            (it.sku || "").toLowerCase().includes(q)
        );
    }, [items, query]);

    return (
        <div className="p-4 sm:p-6 space-y-6" data-testid="image-catalog-page" style={{ fontFamily: "Tajawal" }}>
            {/* Header */}
            <div className="flex items-center justify-between gap-3 flex-wrap">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                        <ImageIcon size={28} weight="bold" className="text-indigo-600" />
                        إدارة صور المنتجات
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        الصور المحفوظة هنا تُستخدم تلقائياً عند تجهيز الطلبات لأي منتج لا يحتوي على صورة في PDF.
                    </p>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                    <div className="text-xs text-slate-500 bg-slate-100 rounded-lg px-3 py-1.5" data-testid="catalog-count">
                        إجمالي الصور: <span className="font-bold text-slate-700">{items.length}</span>
                    </div>
                    <button
                        type="button"
                        onClick={load}
                        disabled={loading}
                        className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-50 text-slate-700 text-sm font-bold"
                        data-testid="catalog-refresh-btn"
                        title="تحديث القائمة"
                    >
                        <ArrowsClockwise size={14} weight="bold" className={loading ? "animate-spin" : ""} />
                        تحديث
                    </button>
                    <button
                        type="button"
                        onClick={() => { setEditing(null); setShowUpload(true); }}
                        className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold"
                        data-testid="catalog-add-btn"
                    >
                        <Plus size={14} weight="bold" />
                        إضافة صورة
                    </button>
                </div>
            </div>

            {/* Search */}
            <div className="relative max-w-md">
                <MagnifyingGlass size={16} weight="bold" className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
                <input
                    type="text"
                    placeholder="ابحث بالاسم، رقم المنتج، أو SKU…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    className="w-full pr-9 pl-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    data-testid="catalog-search-input"
                    style={{ fontFamily: "Tajawal" }}
                />
            </div>

            {/* List */}
            {loading ? (
                <div className="text-center py-12 text-slate-500" data-testid="catalog-loading">
                    <ArrowsClockwise size={28} className="animate-spin mx-auto mb-2" />
                    جاري التحميل…
                </div>
            ) : items.length === 0 ? (
                <div className="border-2 border-dashed border-slate-300 rounded-xl p-10 text-center bg-white" data-testid="catalog-empty">
                    <Package size={48} weight="bold" className="mx-auto text-slate-300 mb-3" />
                    <div className="font-extrabold text-slate-900">الكتالوج فارغ</div>
                    <p className="text-sm text-slate-500 mt-1">
                        أضف صورة أول منتج، أو ارفع PDF طلبات سلة من صفحة "تجهيز المنتجات" ثم أضف صور المنتجات الناقصة من هناك — ستُحفظ هنا تلقائياً.
                    </p>
                    <button
                        type="button"
                        onClick={() => { setEditing(null); setShowUpload(true); }}
                        className="mt-4 inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold"
                        data-testid="catalog-empty-add-btn"
                    >
                        <Plus size={14} weight="bold" />
                        إضافة أول صورة
                    </button>
                </div>
            ) : filtered.length === 0 ? (
                <div className="text-center py-8 text-slate-500" data-testid="catalog-no-results">
                    لا توجد نتائج مطابقة لبحثك.
                </div>
            ) : (
                <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
                    <div className="overflow-x-auto">
                        <table className="mezan-table w-full text-sm" data-testid="catalog-table">
                            <thead className="bg-slate-50 text-slate-600 text-xs">
                                <tr>
                                    <th className="text-right px-4 py-3 font-bold">الصورة</th>
                                    <th className="text-right px-4 py-3 font-bold">اسم المنتج</th>
                                    <th className="text-right px-4 py-3 font-bold">رقم المنتج</th>
                                    <th className="text-right px-4 py-3 font-bold">SKU</th>
                                    <th className="text-right px-4 py-3 font-bold">آخر تحديث</th>
                                    <th className="text-right px-4 py-3 font-bold">إجراءات</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {filtered.map((it) => (
                                    <tr key={it.name_norm} className="hover:bg-slate-50" data-testid={`catalog-row-${it.name_norm.slice(0, 12)}`}>
                                        <td className="px-4 py-3">
                                            <CatalogImage nameNorm={it.name_norm} version={imgVersion} />
                                        </td>
                                        <td className="px-4 py-3">
                                            <div className="font-bold text-slate-900">{it.product_name}</div>
                                        </td>
                                        <td className="px-4 py-3 text-slate-600 num">{it.product_id || "—"}</td>
                                        <td className="px-4 py-3 text-slate-600 num">{it.sku || "—"}</td>
                                        <td className="px-4 py-3 text-slate-500 text-xs">{formatDate(it.updated_at)}</td>
                                        <td className="px-4 py-3">
                                            <div className="flex items-center gap-1">
                                                <button
                                                    type="button"
                                                    onClick={() => { setEditing(it); setShowUpload(true); }}
                                                    className="p-1.5 rounded-md bg-slate-100 hover:bg-indigo-100 text-slate-700 hover:text-indigo-700"
                                                    title="تعديل / استبدال الصورة"
                                                    data-testid={`catalog-edit-btn-${it.name_norm.slice(0, 12)}`}
                                                >
                                                    <UploadSimple size={14} weight="bold" />
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => setDeleting(it)}
                                                    className="p-1.5 rounded-md bg-slate-100 hover:bg-rose-100 text-slate-700 hover:text-rose-700"
                                                    title="حذف"
                                                    data-testid={`catalog-delete-btn-${it.name_norm.slice(0, 12)}`}
                                                >
                                                    <Trash size={14} weight="bold" />
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            <UploadModal
                open={showUpload}
                onClose={() => { setShowUpload(false); setEditing(null); }}
                onUploaded={handleUploaded}
                existing={editing}
            />
            <ConfirmDeleteModal
                open={!!deleting}
                item={deleting}
                onConfirm={handleDelete}
                onCancel={() => setDeleting(null)}
            />

            {/* Info banner */}
            {items.length > 0 && (
                <div className="bg-indigo-50 border border-indigo-200 rounded-xl p-3 text-xs text-indigo-900 flex items-start gap-2" data-testid="catalog-info-banner">
                    <CheckCircle size={16} weight="bold" className="flex-shrink-0 mt-0.5" />
                    <div>
                        كل صورة في هذا الكتالوج ستظهر تلقائياً بدلاً من علامة "بدون صورة" عند رفع أي ملف PDF يحتوي على نفس اسم المنتج في صفحة <span className="font-bold">تجهيز المنتجات</span>.
                    </div>
                </div>
            )}
        </div>
    );
}
