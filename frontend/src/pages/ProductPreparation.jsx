import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    UploadSimple, Package, ArrowsClockwise, Eye, Trash, Warning,
    FilePdf, ListChecks, ImageBroken, Info, CheckCircle, Plus,
    Ruler, Palette, User, NotePencil,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { API_BASE } from "../lib/api";

/**
 * /product-preparation — "تجهيز المنتجات"
 *
 * Iter-36 flow (individual cards, not grouped):
 *   1. Upload Salla orders PDF → backend parses + dedups by item_key
 *   2. Each line is shown as ONE card in a responsive Grid
 *      (2/3/4/5 cols depending on viewport).
 *   3. User checkboxes the cards they want, hits
 *      "تصدير المنتجات المحددة إلى PDF". After export only those
 *      items disappear (item-level dedup, not order-level).
 *   4. Cards with no image have an inline "إضافة صورة" button — the
 *      uploaded image fills the empty siblings in this same upload
 *      (matched by product_id → SKU → name_norm) and is auto-saved
 *      to /image-catalog for next time. Existing images are never
 *      overwritten.
 */

function ConfirmModal({ open, title, description, confirmLabel, onConfirm, onCancel, danger = false }) {
    if (!open) return null;
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4" data-testid="prep-confirm-modal">
            <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6" style={{ fontFamily: "Tajawal" }}>
                <div className="flex items-start gap-3 mb-4">
                    <div className={`w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 ${danger ? "bg-rose-100 text-rose-600" : "bg-indigo-100 text-indigo-600"}`}>
                        <Warning size={22} weight="fill" />
                    </div>
                    <div className="flex-1">
                        <h3 className="font-extrabold text-lg text-slate-900">{title}</h3>
                        <p className="text-sm text-slate-600 mt-1 leading-relaxed">{description}</p>
                    </div>
                </div>
                <div className="flex gap-2 justify-end">
                    <button
                        type="button"
                        onClick={onCancel}
                        className="px-4 py-2 rounded-lg bg-slate-100 text-slate-700 font-bold hover:bg-slate-200"
                        data-testid="prep-confirm-cancel-btn"
                    >إلغاء</button>
                    <button
                        type="button"
                        onClick={onConfirm}
                        className={`px-4 py-2 rounded-lg font-bold text-white ${danger ? "bg-rose-600 hover:bg-rose-700" : "bg-indigo-600 hover:bg-indigo-700"}`}
                        data-testid="prep-confirm-ok-btn"
                    >{confirmLabel}</button>
                </div>
            </div>
        </div>
    );
}

function StatCard({ label, value, sub, testid, icon: Icon, tone = "slate" }) {
    const tones = {
        slate: "bg-white border-slate-200 text-slate-900",
        green: "bg-emerald-50 border-emerald-200 text-emerald-900",
        amber: "bg-amber-50 border-amber-200 text-amber-900",
    };
    return (
        <div className={`border rounded-xl p-4 ${tones[tone] || tones.slate}`} data-testid={testid} style={{ fontFamily: "Tajawal" }}>
            <div className="flex items-center gap-2 text-xs font-bold text-slate-500 mb-1">
                {Icon ? <Icon size={14} weight="bold" /> : null}
                {label}
            </div>
            <div className="num text-2xl font-extrabold leading-tight">{value}</div>
            {sub ? <div className="text-[11px] text-slate-500 mt-1">{sub}</div> : null}
        </div>
    );
}

/** Square product image with hard fallback if the byte stream errors. */
function CardImage({ uploadId, idx, hasImage, alt, version }) {
    const [errored, setErrored] = useState(false);
    if (!hasImage || errored) {
        return (
            <div
                className="w-full aspect-square rounded-lg bg-slate-100 flex items-center justify-center text-slate-400"
                title="لا توجد صورة"
            >
                <ImageBroken size={36} />
            </div>
        );
    }
    return (
        <img
            src={`${API_BASE}/preparation/image/${uploadId}/${idx}${version ? `?v=${version}` : ""}`}
            alt={alt || "product"}
            onError={() => setErrored(true)}
            className="w-full aspect-square rounded-lg object-cover bg-slate-50"
            loading="lazy"
        />
    );
}

/** Inline "add image" button — shown only when the card has no image.
 *  Behaviour per iter-36 backend: applies to this card + any sibling in
 *  the same upload that shares product_id / sku / name_norm AND has no
 *  image. Cards that already have images are untouched. */
function MissingImageButton({ uploadId, idx, productName, onUploaded }) {
    const inputRef = useRef(null);
    const [busy, setBusy] = useState(false);

    const handleChange = async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;
        if (!file.type.startsWith("image/")) {
            toast.error("الملف يجب أن يكون صورة (PNG/JPG/WEBP)");
            return;
        }
        setBusy(true);
        try {
            const fd = new FormData();
            fd.append("file", file);
            const { data: r } = await api.put(
                `/preparation/image/${uploadId}/${idx}?scope=product`,
                fd,
                { headers: { "Content-Type": "multipart/form-data" } },
            );
            if (r.applied_count > 1) {
                toast.success(`تمت إضافة الصورة لـ ${r.applied_count} بطاقات لنفس المنتج`);
            } else {
                toast.success("تمت إضافة الصورة لهذه البطاقة");
            }
            if (r.skipped_with_existing_image > 0) {
                toast.info(`${r.skipped_with_existing_image} بطاقات لها صور أصلاً — لم يتم استبدالها.`);
            }
            onUploaded?.();
        } catch (err) {
            toast.error(err?.response?.data?.detail || "فشل رفع الصورة");
        } finally {
            setBusy(false);
            if (inputRef.current) inputRef.current.value = "";
        }
    };

    return (
        <>
            <input
                ref={inputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={handleChange}
                data-testid={`prep-card-image-input-${idx}`}
            />
            <button
                type="button"
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); inputRef.current?.click(); }}
                disabled={busy}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-indigo-50 hover:bg-indigo-100 disabled:opacity-50 text-indigo-700 text-[11px] font-bold border border-indigo-200"
                data-testid={`prep-card-add-image-btn-${idx}`}
                title={`أضف صورة لـ "${productName || ""}"`}
            >
                <Plus size={12} weight="bold" />
                {busy ? "جاري الرفع…" : "إضافة صورة"}
            </button>
        </>
    );
}

/** A single product card. ONE per line — order info + image + options. */
function ProductCard({ row, uploadId, imgVersion, isSelected, onToggle, onUploaded }) {
    const orderNum = row.order_number;
    const opts = row.product_options || {};
    const extraOpts = Object.entries(opts).slice(0, 3);  // cap so the card stays compact

    return (
        <label
            className={`group relative flex flex-col gap-2 p-3 border-2 rounded-xl bg-white shadow-sm cursor-pointer transition-all ${
                isSelected
                    ? "border-indigo-500 ring-2 ring-indigo-200 shadow-md"
                    : "border-slate-200 hover:border-indigo-300 hover:shadow-md"
            }`}
            data-testid={`prep-card-${row.idx}`}
        >
            {/* Checkbox — top-right (RTL) corner */}
            <input
                type="checkbox"
                checked={isSelected}
                onChange={() => onToggle(row.idx)}
                className="absolute top-2 left-2 w-5 h-5 rounded border-slate-300 accent-indigo-600 z-10 bg-white shadow"
                data-testid={`prep-card-check-${row.idx}`}
                aria-label="تحديد المنتج"
            />

            {/* Order badge — top-right */}
            <div
                className="absolute top-2 right-2 z-10 px-2 py-0.5 rounded-md bg-slate-900/90 text-white text-[10px] font-bold backdrop-blur"
                data-testid={`prep-card-order-${row.idx}`}
                title="رقم الطلب"
            >
                #{orderNum}
            </div>

            {/* Image */}
            <CardImage
                uploadId={uploadId}
                idx={row.idx}
                hasImage={row.has_image}
                alt={row.product_name}
                version={imgVersion}
            />

            {/* Body */}
            <div className="flex flex-col gap-1 min-w-0">
                {/* Product name — truncate w/ tooltip */}
                <div
                    className="font-extrabold text-slate-900 text-sm truncate leading-tight"
                    title={row.product_name}
                    data-testid={`prep-card-name-${row.idx}`}
                >
                    {row.product_name || "بدون اسم"}
                </div>

                {/* Customer name (الاسم) — if present */}
                {row.customer_name ? (
                    <div className="flex items-center gap-1 text-[11px] text-slate-700 truncate" title={row.customer_name}>
                        <User size={10} weight="bold" className="flex-shrink-0 text-slate-400" />
                        <span className="truncate">{row.customer_name}</span>
                    </div>
                ) : null}

                {/* Size / Color row */}
                {(row.size || row.color) && (
                    <div className="flex flex-wrap items-center gap-1 text-[11px]">
                        {row.size ? (
                            <span
                                className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 max-w-full truncate"
                                title={`المقاس: ${row.size}`}
                                data-testid={`prep-card-size-${row.idx}`}
                            >
                                <Ruler size={10} weight="bold" />
                                <span className="truncate">{row.size}</span>
                            </span>
                        ) : null}
                        {row.color ? (
                            <span
                                className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 max-w-full truncate"
                                title={`اللون: ${row.color}`}
                                data-testid={`prep-card-color-${row.idx}`}
                            >
                                <Palette size={10} weight="bold" />
                                <span className="truncate">{row.color}</span>
                            </span>
                        ) : null}
                    </div>
                )}

                {/* Note — truncate */}
                {row.note ? (
                    <div
                        className="flex items-start gap-1 text-[11px] text-slate-600 leading-tight line-clamp-2"
                        title={row.note}
                        data-testid={`prep-card-note-${row.idx}`}
                    >
                        <NotePencil size={10} weight="bold" className="flex-shrink-0 text-slate-400 mt-0.5" />
                        <span className="break-words">{row.note}</span>
                    </div>
                ) : null}

                {/* Extra options (e.g. النوع) — generic spread, cap 3 */}
                {extraOpts.length > 0 && (
                    <div className="flex flex-wrap gap-1 text-[10px]" data-testid={`prep-card-opts-${row.idx}`}>
                        {extraOpts.map(([k, v]) => (
                            <span
                                key={k}
                                className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-slate-50 border border-slate-200 text-slate-600 max-w-full truncate"
                                title={`${k}: ${v}`}
                            >
                                <span className="font-bold text-slate-500 truncate">{k}:</span>
                                <span className="truncate">{v}</span>
                            </span>
                        ))}
                    </div>
                )}

                {/* Footer row: qty + shipping + add-image */}
                <div className="flex items-center justify-between gap-1 pt-1 border-t border-slate-100 mt-auto">
                    <div className="flex items-center gap-1 text-[10px] text-slate-500 min-w-0">
                        {row.quantity > 1 ? (
                            <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 font-bold">
                                ×{row.quantity}
                            </span>
                        ) : null}
                        <span className="truncate" title={row.shipping_company || ""}>
                            {row.shipping_company || "—"}
                        </span>
                        {row.total_products_in_order > 1 && (
                            <span className="font-bold text-slate-700">/ {row.total_products_in_order}</span>
                        )}
                    </div>
                    {!row.has_image && (
                        <MissingImageButton
                            uploadId={uploadId}
                            idx={row.idx}
                            productName={row.product_name}
                            onUploaded={onUploaded}
                        />
                    )}
                    {row.image_source === "user_upload" && (
                        <span
                            className="inline-flex items-center gap-0.5 px-1 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200 text-[9px] font-bold"
                            data-testid={`prep-card-custom-img-${row.idx}`}
                            title="صورة مرفوعة من المستخدم"
                        >
                            <CheckCircle size={8} weight="fill" />
                            مخصّصة
                        </span>
                    )}
                </div>
            </div>
        </label>
    );
}

export default function ProductPreparation() {
    const [uploading, setUploading] = useState(false);
    const [appending, setAppending] = useState(false);
    const [generating, setGenerating] = useState(false);
    const [data, setData] = useState(null);
    const [stats, setStats] = useState(null);
    const [showClearConfirm, setShowClearConfirm] = useState(false);
    const [showExcluded, setShowExcluded] = useState(false);
    const fileRef = useRef(null);
    const appendRef = useRef(null);
    const [dragOver, setDragOver] = useState(false);
    const [selectedIdx, setSelectedIdx] = useState(() => new Set());
    const [imgVersion, setImgVersion] = useState(0);

    const toggleOne = (idx) => {
        setSelectedIdx(prev => {
            const next = new Set(prev);
            if (next.has(idx)) next.delete(idx); else next.add(idx);
            return next;
        });
    };

    // Flatten ALL preview lines from all groups into a single ordered list
    // for the grid view. Keeps the backend's group ordering (by frequency).
    const flatLines = useMemo(() => {
        if (!data?.groups) return [];
        const out = [];
        for (const g of data.groups) {
            for (const ln of (g.preview_lines || [])) out.push(ln);
        }
        return out;
    }, [data]);

    const toggleAll = () => {
        if (flatLines.length === 0) return;
        setSelectedIdx(prev => {
            const allIds = flatLines.map(l => l.idx);
            const allSelected = allIds.every(i => prev.has(i));
            return allSelected ? new Set() : new Set(allIds);
        });
    };

    const refreshPreview = useCallback(async () => {
        if (!data?.upload_id) return;
        try {
            const { data: refreshed } = await api.get(`/preparation/preview/${data.upload_id}`);
            setData(refreshed);
            setImgVersion(v => v + 1);
        } catch {
            // expired upload — silent (frontend already shows the error from generate)
        }
    }, [data?.upload_id]);

    const loadStats = useCallback(async () => {
        try {
            const { data: s } = await api.get("/preparation/export-log/stats");
            setStats(s);
        } catch {
            /* stats are optional */
        }
    }, []);
    useEffect(() => { loadStats(); }, [loadStats]);

    const handleFileChange = async (file) => {
        if (!file) return;
        if (!file.name.toLowerCase().endsWith(".pdf")) {
            toast.error("يجب رفع ملف بصيغة PDF فقط");
            return;
        }
        setUploading(true);
        setData(null);
        setShowExcluded(false);
        setSelectedIdx(new Set());
        try {
            const fd = new FormData();
            fd.append("file", file);
            const { data: resp } = await api.post("/preparation/upload", fd, {
                headers: { "Content-Type": "multipart/form-data" },
            });
            setData(resp);
            toast.success(`تم استخراج ${resp.total_orders} طلب، ${resp.total_product_lines} منتج`);
            await loadStats();
        } catch (e) {
            const msg = e?.response?.data?.detail || e.message || "تعذّر رفع الملف";
            toast.error(String(msg));
        } finally {
            setUploading(false);
            if (fileRef.current) fileRef.current.value = "";
        }
    };

    /**
     * iter-41 — append another PDF to the current upload session.
     * Merges products from the new file with the existing list,
     * deduping by item_key (latest wins). Existing image uploads
     * for items NOT shared with the new file are preserved.
     */
    const handleAppendFile = async (file) => {
        if (!file) return;
        if (!file.name.toLowerCase().endsWith(".pdf")) {
            toast.error("يجب رفع ملف بصيغة PDF فقط");
            return;
        }
        if (!data?.upload_id) {
            return handleFileChange(file);   // no session yet → first upload
        }
        setAppending(true);
        try {
            const fd = new FormData();
            fd.append("file", file);
            const { data: resp } = await api.post(`/preparation/append/${data.upload_id}`, fd, {
                headers: { "Content-Type": "multipart/form-data" },
            });
            setData(resp);
            setSelectedIdx(new Set());        // selection invalidated by new indices
            setImgVersion(v => v + 1);
            const extra = resp.replaced_count > 0
                ? ` — حُدِّث ${resp.replaced_count} منتج مكرر`
                : "";
            toast.success(`أُضيف "${file.name}" — الإجمالي الآن ${resp.total_product_lines} منتج${extra}`);
        } catch (e) {
            const msg = e?.response?.data?.detail || e.message || "تعذّر إضافة الملف";
            toast.error(String(msg));
        } finally {
            setAppending(false);
            if (appendRef.current) appendRef.current.value = "";
        }
    };

    const onDrop = (e) => {
        e.preventDefault();
        setDragOver(false);
        const f = e.dataTransfer?.files?.[0];
        if (f) handleFileChange(f);
    };

    const handleGenerate = async (selectedIndicesArr = null) => {
        if (!data?.upload_id) return;
        setGenerating(true);
        try {
            const body = selectedIndicesArr === null
                ? {}
                : { selected_indices: Array.from(selectedIndicesArr) };
            const resp = await api.post(`/preparation/generate/${data.upload_id}`, body, {
                responseType: "blob",
                headers: { "Content-Type": "application/json" },
            });
            const exported = resp.headers["x-exported-orders"] || "?";
            const cards = resp.headers["x-exported-cards"] || "?";
            const items = resp.headers["x-exported-items"] || cards;
            const url = window.URL.createObjectURL(new Blob([resp.data], { type: "application/pdf" }));
            const a = document.createElement("a");
            a.href = url;
            // iter-41 — unique-per-export filename: timestamp + cards count.
            // Ensures every print download is a distinct file in the
            // merchant's Downloads folder + reflects the batch size.
            const now = new Date();
            const ts = (
                String(now.getFullYear()) +
                String(now.getMonth() + 1).padStart(2, "0") +
                String(now.getDate()).padStart(2, "0") +
                "_" +
                String(now.getHours()).padStart(2, "0") +
                String(now.getMinutes()).padStart(2, "0") +
                String(now.getSeconds()).padStart(2, "0")
            );
            a.download = `preparation_${ts}_${cards}منتج.pdf`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            toast.success(`تم إنشاء PDF: ${cards} بطاقة (${items} منتج، ${exported} طلب)`);
            setSelectedIdx(new Set());
            await loadStats();
            await refreshPreview();
        } catch (e) {
            let msg = "فشل إنشاء الملف";
            if (e?.response?.data instanceof Blob) {
                try {
                    const txt = await e.response.data.text();
                    msg = JSON.parse(txt).detail || msg;
                } catch { /* keep default */ }
            } else {
                msg = e?.response?.data?.detail || e.message || msg;
            }
            toast.error(String(msg));
        } finally {
            setGenerating(false);
        }
    };

    const handleClearLog = async () => {
        setShowClearConfirm(false);
        try {
            const { data: r } = await api.delete("/preparation/export-log", {
                data: { confirm: true },
            });
            toast.success(`تم مسح سجل التصدير (${r.deleted_count} طلب). يمكنك الآن إعادة تصدير الطلبات.`);
            await loadStats();
            await refreshPreview();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل المسح");
        }
    };

    const totalCards = flatLines.length;
    const allSelected = totalCards > 0 && selectedIdx.size === totalCards;

    return (
        <div className="p-4 sm:p-6 space-y-6" data-testid="product-preparation-page" style={{ fontFamily: "Tajawal" }}>
            {/* Header */}
            <div className="flex items-center justify-between gap-3 flex-wrap">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                        <Package size={28} weight="bold" className="text-indigo-600" />
                        تجهيز المنتجات
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">حوّل PDF طلبات سلة إلى ملف تجهيز جاهز للطباعة (4×4 بطاقة لكل صفحة).</p>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                    {stats && (
                        <div className="text-xs text-slate-500 bg-slate-100 rounded-lg px-3 py-1.5" data-testid="prep-log-summary">
                            سجل التصدير: <span className="font-bold text-slate-700">{stats.total_exported_orders}</span> طلب
                        </div>
                    )}
                    <button
                        type="button"
                        onClick={() => setShowClearConfirm(true)}
                        disabled={!stats?.total_exported_orders}
                        className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-rose-50 hover:bg-rose-100 disabled:opacity-50 disabled:cursor-not-allowed text-rose-700 text-sm font-bold border border-rose-200"
                        data-testid="prep-clear-log-btn"
                    >
                        <Trash size={14} weight="bold" />
                        مسح سجل التصدير
                    </button>
                </div>
            </div>

            {/* Upload zone */}
            <div
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={onDrop}
                onClick={() => fileRef.current?.click()}
                className={`relative border-2 border-dashed rounded-xl p-6 sm:p-10 text-center cursor-pointer transition-all ${dragOver ? "border-indigo-500 bg-indigo-50" : "border-slate-300 bg-white hover:border-indigo-400 hover:bg-slate-50"}`}
                data-testid="prep-upload-dropzone"
            >
                <input
                    ref={fileRef}
                    type="file"
                    accept="application/pdf"
                    className="hidden"
                    onChange={(e) => handleFileChange(e.target.files?.[0])}
                    data-testid="prep-file-input"
                />
                <UploadSimple size={48} weight="bold" className="mx-auto text-indigo-500 mb-3" />
                <div className="font-extrabold text-slate-900">
                    {uploading ? "جاري الرفع والقراءة…" : "اسحب ملف orders.pdf هنا أو انقر للاختيار"}
                </div>
                <div className="text-xs text-slate-500 mt-1">يقبل ملفات PDF حتى 25MB. الطلبات المُصدَّرة مسبقاً سيتم استبعادها تلقائياً.</div>
                {!!(data?.filenames?.length) && !uploading && (
                    <div className="mt-3 flex flex-wrap items-center justify-center gap-1.5" data-testid="prep-filenames-strip">
                        {data.filenames.map((fn, i) => (
                            <span
                                key={`${fn}-${i}`}
                                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-700 text-xs font-bold max-w-[260px]"
                                title={fn}
                                data-testid={`prep-filename-chip-${i}`}
                            >
                                <CheckCircle size={11} weight="bold" />
                                <span className="truncate">{fn}</span>
                            </span>
                        ))}
                        <span className="text-[10px] text-slate-500 ms-1" data-testid="prep-filenames-count">
                            ({data.filenames.length} ملف)
                        </span>
                    </div>
                )}
            </div>

            {/* iter-41 — hidden input + button for "append another PDF" */}
            {data && (
                <input
                    ref={appendRef}
                    type="file"
                    accept="application/pdf"
                    className="hidden"
                    onChange={(e) => handleAppendFile(e.target.files?.[0])}
                    data-testid="prep-append-file-input"
                />
            )}

            {/* Stats row */}
            {data && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="prep-stats-row">
                    <StatCard label="عدد الطلبات" value={data.total_orders} testid="prep-stat-orders" icon={ListChecks} />
                    <StatCard label="إجمالي البطاقات" value={totalCards || data.kept_lines} sub={`من أصل ${data.total_product_lines}`} testid="prep-stat-cards" icon={Package} tone="green" />
                    <StatCard label="طلبات مستبعدة" value={data.excluded_orders_count} sub="مُصدَّرة سابقاً" testid="prep-stat-excluded" icon={Warning} tone={data.excluded_orders_count ? "amber" : "slate"} />
                    <StatCard label="عدد المنتجات الفريدة" value={(data.groups || []).length} testid="prep-stat-products" icon={Package} />
                </div>
            )}

            {/* Action bar */}
            {data && (
                <div className="sticky top-0 z-20 -mx-4 sm:-mx-6 px-4 sm:px-6 py-2 bg-white/95 backdrop-blur border-b border-slate-200 flex items-center gap-2 flex-wrap" data-testid="prep-actions-bar">
                    <button
                        type="button"
                        onClick={() => handleGenerate(Array.from(selectedIdx))}
                        disabled={generating || selectedIdx.size === 0}
                        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold"
                        data-testid="prep-print-selected-btn"
                    >
                        <FilePdf size={16} weight="bold" />
                        {generating
                            ? "جاري الإنشاء…"
                            : selectedIdx.size > 0
                                ? `تصدير المنتجات المحددة إلى PDF (${selectedIdx.size})`
                                : "تصدير المنتجات المحددة إلى PDF"}
                    </button>
                    <button
                        type="button"
                        onClick={toggleAll}
                        disabled={generating || totalCards === 0}
                        className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-50 text-slate-700 font-bold text-sm"
                        data-testid="prep-select-all-btn"
                    >
                        {allSelected ? "إلغاء التحديد" : "تحديد الكل"}
                    </button>
                    <button
                        type="button"
                        onClick={() => appendRef.current?.click()}
                        disabled={appending || generating}
                        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-bold text-sm"
                        data-testid="prep-append-btn"
                        title="إضافة ملف PDF آخر لنفس الجلسة (المنتجات تتراكم)"
                    >
                        <Plus size={14} weight="bold" />
                        {appending ? "جاري الإضافة…" : "إضافة ملف PDF آخر"}
                    </button>
                    <button
                        type="button"
                        onClick={() => fileRef.current?.click()}
                        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold"
                        data-testid="prep-reupload-btn"
                        title="استبدال كل الملفات بملف جديد فقط"
                    >
                        <ArrowsClockwise size={14} weight="bold" /> إعادة الرفع
                    </button>
                    {data.excluded_orders_count > 0 && (
                        <button
                            type="button"
                            onClick={() => setShowExcluded(v => !v)}
                            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-amber-50 hover:bg-amber-100 text-amber-700 font-bold border border-amber-200"
                            data-testid="prep-toggle-excluded-btn"
                        >
                            <Eye size={14} weight="bold" /> {showExcluded ? "إخفاء" : "عرض"} الطلبات المستبعدة ({data.excluded_orders_count})
                        </button>
                    )}
                    {selectedIdx.size > 0 && (
                        <div className="ms-auto text-xs text-slate-600 font-bold" data-testid="prep-selection-count">
                            <span className="num">{selectedIdx.size}</span> من <span className="num">{totalCards}</span> محدّد
                        </div>
                    )}
                </div>
            )}

            {/* Excluded orders panel */}
            {data && showExcluded && data.excluded_orders?.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-xl p-4" data-testid="prep-excluded-panel">
                    <div className="flex items-center gap-2 font-bold text-amber-900 mb-2">
                        <Info size={16} weight="bold" /> هذه الطلبات تم تصديرها مسبقاً ولن تظهر في الملف الجديد:
                    </div>
                    <div className="flex flex-wrap gap-2" data-testid="prep-excluded-list">
                        {data.excluded_orders.map(n => (
                            <span key={n} className="text-xs font-mono bg-white border border-amber-300 text-amber-800 rounded px-2 py-1">#{n}</span>
                        ))}
                    </div>
                    <div className="text-[11px] text-amber-700 mt-3">
                        إذا كنت بحاجة لإعادة تصديرها، استخدم زر "مسح سجل التصدير" أعلاه.
                    </div>
                </div>
            )}

            {/* Individual product cards Grid */}
            {flatLines.length > 0 && (
                <div
                    className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5"
                    data-testid="prep-cards-grid"
                >
                    {flatLines.map(row => (
                        <ProductCard
                            key={row.item_key || `${row.order_number}-${row.idx}`}
                            row={row}
                            uploadId={data.upload_id}
                            imgVersion={imgVersion}
                            isSelected={selectedIdx.has(row.idx)}
                            onToggle={toggleOne}
                            onUploaded={refreshPreview}
                        />
                    ))}
                </div>
            )}

            {/* Empty grid state (after upload, but no remaining items) */}
            {data && flatLines.length === 0 && (
                <div className="border-2 border-dashed border-slate-300 rounded-xl p-10 text-center bg-white" data-testid="prep-empty-grid">
                    <CheckCircle size={48} weight="bold" className="mx-auto text-emerald-500 mb-3" />
                    <div className="font-extrabold text-slate-900">كل المنتجات في هذا الملف تم تصديرها سابقاً</div>
                    <p className="text-sm text-slate-500 mt-1">ارفع ملف PDF جديد أو امسح سجل التصدير لإعادة طباعة المنتجات القديمة.</p>
                </div>
            )}

            <ConfirmModal
                open={showClearConfirm}
                title="مسح سجل التصدير"
                description="هل أنت متأكد من مسح سجل التصدير؟ سيتم السماح بإعادة تصدير الطلبات القديمة. لن يتم حذف أي طلب من سلة ولن تتغير حالة الطلبات."
                confirmLabel="نعم، امسح السجل"
                onConfirm={handleClearLog}
                onCancel={() => setShowClearConfirm(false)}
                danger
            />
        </div>
    );
}
