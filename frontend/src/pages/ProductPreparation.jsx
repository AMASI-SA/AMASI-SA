import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    UploadSimple, Package, ArrowsClockwise, Eye, Trash, Warning,
    FilePdf, ListChecks, ImageBroken, Info, CheckCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { API_BASE } from "../lib/api";

/**
 * /product-preparation — "تجهيز المنتجات"
 *
 * 3-step flow:
 *   1. Upload Salla orders PDF → backend parses + groups by product
 *   2. Preview the grouped products (sorted by count desc) + see which
 *      orders were excluded because they're already in `exported_orders`
 *   3. Generate the printable 4×4 prep PDF (or clear the export log first
 *      to allow re-exporting old orders).
 *
 * The file is intentionally self-contained; it never touches the dashboard
 * or accounting calculations.
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

function ProductImage({ uploadId, idx, hasImage, alt, version }) {
    const [errored, setErrored] = useState(false);
    if (!hasImage || errored) {
        return (
            <div className="w-14 h-14 rounded-lg bg-slate-100 flex items-center justify-center text-slate-400 flex-shrink-0" title="لا توجد صورة">
                <ImageBroken size={20} />
            </div>
        );
    }
    return (
        <img
            src={`${API_BASE}/preparation/image/${uploadId}/${idx}${version ? `?v=${version}` : ""}`}
            alt={alt || "product"}
            onError={() => setErrored(true)}
            className="w-14 h-14 rounded-lg object-cover border border-slate-200 flex-shrink-0"
            loading="lazy"
        />
    );
}

/** Inline image uploader — appears on rows without an image. The uploaded
 *  image applies to all sibling rows that share the same product_name
 *  (scope=product), so the merchant uploads once per product, not per order. */
function MissingImageUploader({ uploadId, idx, productName, onUploaded }) {
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
            toast.success(
                r.applied_count > 1
                    ? `تم تطبيق الصورة على ${r.applied_count} طلبات لنفس المنتج "${productName || ""}"`
                    : "تم رفع صورة المنتج",
            );
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
                data-testid={`prep-missing-image-input-${idx}`}
            />
            <button
                type="button"
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); inputRef.current?.click(); }}
                disabled={busy}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-indigo-50 hover:bg-indigo-100 disabled:opacity-50 text-indigo-700 text-[11px] font-bold border border-indigo-200"
                data-testid={`prep-missing-image-btn-${idx}`}
                title="أضف صورة لهذا المنتج (تنطبق على كل طلباته)"
            >
                {busy ? "جاري الرفع…" : "إضافة صورة"}
            </button>
        </>
    );
}

export default function ProductPreparation() {
    const [uploading, setUploading] = useState(false);
    const [generating, setGenerating] = useState(false);
    const [data, setData] = useState(null);           // upload+preview response
    const [stats, setStats] = useState(null);
    const [showClearConfirm, setShowClearConfirm] = useState(false);
    const [showExcluded, setShowExcluded] = useState(false);
    const fileRef = useRef(null);
    const [dragOver, setDragOver] = useState(false);
    // Set of selected line `idx` values across all groups. Cleared after
    // a successful print and re-populated by the user via checkboxes.
    const [selectedIdx, setSelectedIdx] = useState(() => new Set());
    // Bump this after a per-product image upload so <img> tags re-fetch
    // and the freshly-stored image shows up immediately.
    const [imgVersion, setImgVersion] = useState(0);

    const toggleOne = (idx) => {
        setSelectedIdx(prev => {
            const next = new Set(prev);
            if (next.has(idx)) next.delete(idx); else next.add(idx);
            return next;
        });
    };

    const toggleGroup = (group) => {
        const ids = (group.preview_lines || []).map(l => l.idx);
        setSelectedIdx(prev => {
            const next = new Set(prev);
            const allSelected = ids.every(i => next.has(i));
            if (allSelected) ids.forEach(i => next.delete(i));
            else ids.forEach(i => next.add(i));
            return next;
        });
    };

    const toggleAll = () => {
        if (!data?.groups) return;
        setSelectedIdx(prev => {
            const all = [];
            data.groups.forEach(g => (g.preview_lines || []).forEach(l => all.push(l.idx)));
            const allSelected = all.every(i => prev.has(i));
            return allSelected ? new Set() : new Set(all);
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
        } catch (e) {
            // Silent — stats are an enhancement
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
            // Always POST a body — when null we send `{}` (= "print all
            // remaining"), otherwise we send the explicit selection.
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
            a.download = `product_preparation_${new Date().toISOString().slice(0, 10).replace(/-/g, "")}.pdf`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            toast.success(`تم إنشاء PDF: ${cards} بطاقة (${items} منتج، ${exported} طلب)`);
            setSelectedIdx(new Set());  // reset selection after print
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

    const totalCards = useMemo(
        () => (data?.groups || []).reduce((a, g) => a + g.count, 0),
        [data]
    );

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
                {data?.filename && !uploading && (
                    <div className="mt-3 inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-700 text-xs font-bold">
                        <CheckCircle size={12} weight="bold" /> {data.filename}
                    </div>
                )}
            </div>

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
                <div className="flex items-center gap-2 flex-wrap" data-testid="prep-actions-bar">
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
                                ? `طباعة المحدد إلى PDF (${selectedIdx.size})`
                                : "طباعة المحدد إلى PDF"}
                    </button>
                    <button
                        type="button"
                        onClick={toggleAll}
                        disabled={generating || totalCards === 0}
                        className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-50 text-slate-700 font-bold text-sm"
                        data-testid="prep-select-all-btn"
                    >
                        {selectedIdx.size > 0 && selectedIdx.size === totalCards ? "إلغاء التحديد" : "تحديد الكل"}
                    </button>
                    <button
                        type="button"
                        onClick={() => fileRef.current?.click()}
                        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold"
                        data-testid="prep-reupload-btn"
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

            {/* Product groups preview */}
            {data?.groups?.length > 0 && (
                <div className="space-y-3" data-testid="prep-groups-panel">
                    <h2 className="text-base font-extrabold text-slate-900">
                        المنتجات مرتبة حسب الأكثر مبيعاً
                    </h2>
                    <div className="space-y-2">
                        {data.groups.map((g) => (
                            <details
                                key={g.product_name}
                                className="border border-slate-200 rounded-xl bg-white overflow-hidden group"
                                data-testid={`prep-group-${g.product_name?.slice(0, 12)}`}
                            >
                                <summary className="cursor-pointer p-4 flex items-center justify-between gap-3 hover:bg-slate-50">
                                    <div className="flex items-center gap-3 min-w-0">
                                        {/* Group checkbox — toggles all rows in this product */}
                                        <input
                                            type="checkbox"
                                            checked={(g.preview_lines || []).every(l => selectedIdx.has(l.idx))}
                                            ref={el => {
                                                if (!el) return;
                                                const some = (g.preview_lines || []).some(l => selectedIdx.has(l.idx));
                                                const all = (g.preview_lines || []).every(l => selectedIdx.has(l.idx));
                                                el.indeterminate = some && !all;
                                            }}
                                            onClick={e => { e.stopPropagation(); toggleGroup(g); }}
                                            onChange={() => {}}
                                            className="w-4 h-4 rounded border-slate-300 accent-indigo-600 cursor-pointer"
                                            data-testid={`prep-group-check-${g.product_name?.slice(0, 12)}`}
                                            title="تحديد كل بطاقات هذا المنتج"
                                        />
                                        {g.preview_lines?.[0] ? (
                                            <ProductImage
                                                uploadId={data.upload_id}
                                                idx={g.preview_lines[0].idx}
                                                hasImage={g.preview_lines[0].has_image}
                                                alt={g.product_name}
                                                version={imgVersion}
                                            />
                                        ) : null}
                                        <div className="min-w-0">
                                            <div className="font-extrabold text-slate-900 truncate">{g.product_name}</div>
                                            <div className="text-xs text-slate-500 flex items-center gap-2">
                                                <span>{g.count} {g.count === 1 ? "طلب" : "طلبات"}</span>
                                                {!g.preview_lines?.[0]?.has_image && (
                                                    <MissingImageUploader
                                                        uploadId={data.upload_id}
                                                        idx={g.preview_lines[0].idx}
                                                        productName={g.product_name}
                                                        onUploaded={refreshPreview}
                                                    />
                                                )}
                                                {g.preview_lines?.[0]?.image_source === "user_upload" && (
                                                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200 text-[10px] font-bold" data-testid="prep-custom-img-badge">
                                                        صورة مخصّصة
                                                    </span>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                    <div className="inline-flex items-center justify-center min-w-[44px] h-9 rounded-lg bg-indigo-50 text-indigo-700 font-extrabold border border-indigo-200">
                                        {g.count}
                                    </div>
                                </summary>
                                <div className="border-t border-slate-200 divide-y divide-slate-100" data-testid="prep-group-rows">
                                    {(g.preview_lines || []).map((row) => (
                                        <label
                                            key={`${row.order_number}-${row.idx}`}
                                            className={`flex items-center gap-3 p-3 text-sm cursor-pointer transition-colors ${selectedIdx.has(row.idx) ? "bg-indigo-50/40" : "hover:bg-slate-50"}`}
                                            data-testid={`prep-row-${row.idx}`}
                                        >
                                            <input
                                                type="checkbox"
                                                checked={selectedIdx.has(row.idx)}
                                                onChange={() => toggleOne(row.idx)}
                                                className="w-4 h-4 rounded border-slate-300 accent-indigo-600 flex-shrink-0"
                                                data-testid={`prep-row-check-${row.idx}`}
                                            />
                                            <ProductImage uploadId={data.upload_id} idx={row.idx} hasImage={row.has_image} alt={row.product_name} version={imgVersion} />
                                            <div className="flex-1 min-w-0">
                                                <div className="flex items-baseline gap-2">
                                                    <span className="font-bold text-slate-900">#{row.order_number}</span>
                                                    {row.customer_name ? (
                                                        <span className="text-slate-600">— {row.customer_name}</span>
                                                    ) : null}
                                                    <span className="text-[11px] text-slate-500 ms-auto">{row.order_date || ""}</span>
                                                </div>
                                                {row.note ? (
                                                    <div className="text-[11px] text-slate-500 truncate" title={row.note}>ملاحظة: {row.note}</div>
                                                ) : null}
                                                <div className="text-[11px] text-slate-400 mt-0.5">
                                                    {(row.shipping_company || "—")} - {row.total_products_in_order}
                                                </div>
                                            </div>
                                        </label>
                                    ))}
                                </div>
                            </details>
                        ))}
                    </div>
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
