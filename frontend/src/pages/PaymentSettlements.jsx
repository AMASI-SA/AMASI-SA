import { useEffect, useRef, useState } from "react";
import {
    Receipt, CloudArrowUp, FileXls, Trash, ArrowsClockwise, CheckCircle,
    Warning, ArrowRight, XCircle, ChartPieSlice, MagnifyingGlass,
    Info, FileText, Lock,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api from "../lib/api";


function ConfirmModal({ open, title, description, confirmLabel, onConfirm, onCancel, danger }) {
    if (!open) return null;
    return (
        <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onCancel}>
            <div
                className="bg-white rounded-xl shadow-2xl max-w-md w-full overflow-hidden"
                onClick={(e) => e.stopPropagation()}
                data-testid="confirm-modal"
            >
                <div className="p-5">
                    <h3 className="font-extrabold text-slate-900 text-lg mb-2">{title}</h3>
                    <p className="text-sm text-slate-600 whitespace-pre-line leading-relaxed">{description}</p>
                </div>
                <div className="bg-slate-50 px-5 py-3 flex justify-end gap-2 border-t border-slate-200">
                    <button
                        type="button"
                        onClick={onCancel}
                        className="px-4 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-bold hover:bg-slate-100"
                        data-testid="confirm-cancel-btn"
                    >
                        إلغاء
                    </button>
                    <button
                        type="button"
                        onClick={onConfirm}
                        className={`px-4 py-2 rounded-lg text-white text-sm font-bold ${
                            danger ? "bg-rose-600 hover:bg-rose-700" : "bg-indigo-600 hover:bg-indigo-700"
                        }`}
                        data-testid="confirm-ok-btn"
                    >
                        {confirmLabel}
                    </button>
                </div>
            </div>
        </div>
    );
}

/**
 * /payment-settlements — Phase 80
 *
 * Upload Salla / Tamara / Tabby / Emkan settlement files and let the backend
 * patch each matched order's `actual_*` fields. Estimated rates remain
 * the fallback for orders never seen in any settlement.
 *
 * No automatic bank transfers, no auto-adjustments — only the
 * `unified_orders.actual_*` columns + an audit row are written.
 */

const PROVIDER_META = {
    salla: { label: "سلة", color: "bg-emerald-500", chip: "bg-emerald-100 text-emerald-700" },
    tamara: { label: "تمارا", color: "bg-orange-500", chip: "bg-orange-100 text-orange-700" },
    tabby: { label: "تابي", color: "bg-violet-500", chip: "bg-violet-100 text-violet-700" },
    emkan: { label: "إمكان", color: "bg-amber-500", chip: "bg-amber-100 text-amber-700" },
};

function fmtMoney(n) {
    return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        .format(Number(n) || 0);
}


export default function PaymentSettlements() {
    const fileInputRef = useRef(null);
    const [uploading, setUploading] = useState(false);
    const [dragActive, setDragActive] = useState(false);
    const [files, setFiles] = useState([]);
    const [analytics, setAnalytics] = useState(null);
    const [loading, setLoading] = useState(true);
    const [recentUpload, setRecentUpload] = useState(null);
    const [pendingDelete, setPendingDelete] = useState(null);
    const [unmatchedView, setUnmatchedView] = useState(null);
    // Iter-78 — delete-button visibility controlled by a Settings toggle.
    const [allowDelete, setAllowDelete] = useState(false);

    const reload = async () => {
        setLoading(true);
        try {
            const [filesRes, analyticsRes, settingsRes] = await Promise.all([
                api.get("/payment-settlements"),
                api.get("/payment-settlements/_analytics/coverage"),
                api.get("/settings").catch(() => ({ data: {} })),
            ]);
            setFiles(filesRes.data?.files || []);
            setAnalytics(analyticsRes.data);
            setAllowDelete(!!settingsRes.data?.settlements_allow_delete);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "تعذّر تحميل البيانات");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const [filesRes, analyticsRes, settingsRes] = await Promise.all([
                    api.get("/payment-settlements"),
                    api.get("/payment-settlements/_analytics/coverage"),
                    api.get("/settings").catch(() => ({ data: {} })),
                ]);
                if (cancelled) return;
                setFiles(filesRes.data?.files || []);
                setAnalytics(analyticsRes.data);
                setAllowDelete(!!settingsRes.data?.settlements_allow_delete);
            } catch (e) {
                if (!cancelled) toast.error(e?.response?.data?.detail || "تعذّر تحميل البيانات");
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, []);

    async function handleUpload(file) {
        setUploading(true);
        const form = new FormData();
        form.append("file", file);
        try {
            const { data } = await api.post("/payment-settlements/upload", form, {
                headers: { "Content-Type": "multipart/form-data" },
            });
            setRecentUpload(data);
            if (data.status === "duplicate") {
                toast.info("هذا الملف تم رفعه مسبقاً — لم يتم تكرار العملية.");
            } else {
                const meta = PROVIDER_META[data.provider] || {};
                toast.success(
                    `تم تحليل ملف ${meta.label || data.provider}: ${data.matched} طلب مطابق · ${data.unmatched} غير موجود`
                );
            }
            await reload();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل رفع الملف");
        } finally {
            setUploading(false);
            if (fileInputRef.current) fileInputRef.current.value = "";
        }
    }

    const handleDelete = async () => {
        const f = pendingDelete;
        setPendingDelete(null);
        if (!f) return;
        try {
            const { data } = await api.delete(`/payment-settlements/${f.id}`);
            toast.success(`تم حذف الملف وإرجاع ${data.orders_rolled_back || 0} طلب إلى النسب التقديرية.`);
            await reload();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل الحذف");
        }
    };

    const handleViewUnmatched = async (file) => {
        try {
            const { data } = await api.get(`/payment-settlements/${file.id}`);
            setUnmatchedView({ file: data, orders: data.unmatched_orders || [] });
        } catch (e) {
            toast.error(e?.response?.data?.detail || "تعذّر التحميل");
        }
    };

    const onFileSelected = (file) => {
        if (!file) return;
        if (!file.name.toLowerCase().endsWith(".xlsx")) {
            toast.error("الملف يجب أن يكون بصيغة Excel (.xlsx) من سلة أو تمارا أو تابي أو إمكان");
            return;
        }
        handleUpload(file);
    };

    const onDrop = (e) => {
        e.preventDefault();
        setDragActive(false);
        const f = e.dataTransfer.files?.[0];
        if (f) onFileSelected(f);
    };

    return (
        <div className="p-4 sm:p-6 space-y-5" data-testid="payment-settlements-page" style={{ fontFamily: "Tajawal" }}>
            {/* Header */}
            <div className="flex items-start gap-3">
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-sky-500 to-indigo-600 flex items-center justify-center text-white shadow-lg flex-shrink-0">
                    <Receipt size={28} weight="bold" />
                </div>
                <div className="flex-1">
                    <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900">فواتير وتسويات بوابات الدفع</h1>
                    <p className="text-sm text-slate-500 mt-1 leading-relaxed">
                        ارفع ملفات Excel من <strong>سلة / تمارا / تابي / إمكان</strong> وسيقوم النظام بمطابقة كل طلب مع الرسوم والصافي الفعلي.
                        الطلبات غير المُطابَقة تبقى محسوبة بالنسب التقديرية الحالية كما هي.
                    </p>
                </div>
            </div>

            {/* Coverage cards */}
            {analytics && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3" data-testid="settlements-coverage">
                    <div className="bg-white border border-slate-200 rounded-xl p-4">
                        <div className="text-xs text-slate-500 font-bold mb-1">إجمالي الطلبات</div>
                        <div className="text-2xl font-extrabold text-slate-900">{analytics.totals.orders_total}</div>
                    </div>
                    <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4" data-testid="settlements-actual-card">
                        <div className="text-xs text-emerald-700 font-bold mb-1">طلبات بأرقام فعلية</div>
                        <div className="text-2xl font-extrabold text-emerald-700">{analytics.totals.orders_actual}</div>
                        <div className="text-[11px] text-emerald-600 mt-1">{analytics.totals.coverage_pct}% تغطية</div>
                    </div>
                    <div className="bg-amber-50 border border-amber-200 rounded-xl p-4">
                        <div className="text-xs text-amber-700 font-bold mb-1">طلبات بنسب تقديرية</div>
                        <div className="text-2xl font-extrabold text-amber-700">{analytics.totals.orders_estimated}</div>
                        <div className="text-[11px] text-amber-600 mt-1">لم تُطابق بعد</div>
                    </div>
                    <div className="bg-indigo-50 border border-indigo-200 rounded-xl p-4">
                        <div className="text-xs text-indigo-700 font-bold mb-1">إجمالي الصافي الفعلي</div>
                        <div className="text-xl font-extrabold text-indigo-700">{fmtMoney(analytics.actual_aggregates.net)}</div>
                        <div className="text-[11px] text-indigo-600 mt-1">ر.س — من ملفات التسوية</div>
                    </div>
                </div>
            )}

            {/* By-provider breakdown */}
            {analytics?.by_provider?.length > 0 && (
                <div className="bg-white border border-slate-200 rounded-xl p-4" data-testid="settlements-by-provider">
                    <h3 className="font-extrabold text-slate-900 text-sm mb-3 flex items-center gap-2">
                        <ChartPieSlice size={16} weight="bold" className="text-indigo-600" />
                        التغطية الفعلية لكل بوابة دفع
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                        {analytics.by_provider.map((p) => {
                            const meta = PROVIDER_META[p.provider] || {};
                            return (
                                <div key={p.provider} className="bg-slate-50 rounded-lg p-3 border border-slate-200" data-testid={`provider-breakdown-${p.provider}`}>
                                    <div className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-bold ${meta.chip || "bg-slate-200 text-slate-700"} mb-2`}>
                                        {meta.label || p.provider}
                                    </div>
                                    <div className="text-lg font-extrabold text-slate-900">{p.orders} طلب</div>
                                    <div className="text-xs text-slate-600 mt-0.5">رسوم فعلية: <span className="font-bold">{fmtMoney(p.fees)} ر.س</span></div>
                                    <div className="text-xs text-slate-600">صافي: <span className="font-bold">{fmtMoney(p.net)} ر.س</span></div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* Drop zone */}
            <div
                onDragEnter={(e) => { e.preventDefault(); setDragActive(true); }}
                onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
                onDragLeave={() => setDragActive(false)}
                onDrop={onDrop}
                className={`border-2 border-dashed rounded-xl p-8 text-center transition-all ${
                    dragActive ? "border-indigo-500 bg-indigo-50" : "border-slate-300 bg-slate-50 hover:bg-white"
                }`}
                data-testid="settlements-upload-zone"
            >
                <CloudArrowUp size={48} weight="duotone" className={`mx-auto mb-3 ${dragActive ? "text-indigo-600" : "text-slate-400"}`} />
                <h3 className="font-extrabold text-slate-900 mb-1">اسحب وأفلت ملف Excel هنا</h3>
                <p className="text-xs text-slate-500 mb-4">
                    يقبل النظام ملفات سلة (فواتير المدفوعات) وتمارا وتابي وإمكان. الكشف التلقائي للنوع.
                </p>
                <input
                    ref={fileInputRef}
                    type="file"
                    accept=".xlsx"
                    className="hidden"
                    onChange={(e) => onFileSelected(e.target.files?.[0])}
                    data-testid="settlements-file-input"
                />
                <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploading}
                    className="inline-flex items-center gap-1.5 px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-sm disabled:opacity-50"
                    data-testid="settlements-browse-btn"
                >
                    <FileXls size={16} weight="bold" />
                    {uploading ? "جاري الرفع والتحليل…" : "تصفّح واختر ملف"}
                </button>
            </div>

            {/* Last upload summary */}
            {recentUpload && recentUpload.status === "imported" && (
                <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4" data-testid="settlements-recent-summary">
                    <div className="flex items-start gap-3">
                        <CheckCircle size={22} weight="bold" className="text-emerald-600 flex-shrink-0 mt-0.5" />
                        <div className="flex-1">
                            <h4 className="font-extrabold text-emerald-900">
                                تم تحليل ملف <span className="text-emerald-700">{PROVIDER_META[recentUpload.provider]?.label || recentUpload.provider}</span> بنجاح
                            </h4>
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3 text-xs">
                                <div className="bg-white rounded p-2">
                                    <div className="text-slate-500">عدد الصفوف</div>
                                    <div className="font-extrabold text-slate-900 text-sm">{recentUpload.rows}</div>
                                </div>
                                <div className="bg-white rounded p-2">
                                    <div className="text-emerald-600">مطابقة</div>
                                    <div className="font-extrabold text-emerald-700 text-sm">{recentUpload.matched}</div>
                                </div>
                                <div className="bg-white rounded p-2">
                                    <div className="text-amber-600">غير مطابقة</div>
                                    <div className="font-extrabold text-amber-700 text-sm">{recentUpload.unmatched}</div>
                                </div>
                                <div className="bg-white rounded p-2">
                                    <div className="text-slate-500">صافي الملف</div>
                                    <div className="font-extrabold text-slate-900 text-sm">{fmtMoney(recentUpload.totals?.net || 0)} ر.س</div>
                                </div>
                            </div>
                            {(recentUpload.totals?.salla_purchases_count || 0) > 0 && (
                                <div className="mt-3 text-xs text-orange-900 bg-orange-50 border border-orange-200 rounded p-2 flex items-start gap-2" data-testid="recent-salla-purchases">
                                    <Warning size={14} weight="bold" className="text-orange-600 flex-shrink-0 mt-0.5" />
                                    <span>
                                        تم اكتشاف <strong>{recentUpload.totals.salla_purchases_count}</strong> عملية «شحن محفظة» بمجموع
                                        {" "}<strong className="font-mono">{fmtMoney(recentUpload.totals.salla_purchases_total)} ر.س</strong> — هذه مبالغ مدفوعة لـ سلة (مثلاً لبوليصة شحن) وتم خصمها من إجمالي المبيعات.
                                    </span>
                                </div>
                            )}
                            {recentUpload.unmatched > 0 && (
                                <div className="mt-3 text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 flex items-start gap-1.5">
                                    <Warning size={13} weight="bold" className="flex-shrink-0 mt-0.5" />
                                    <span>
                                        <strong>{recentUpload.unmatched}</strong> طلب من الملف غير موجود في قاعدة بياناتك — هذه الطلبات قد تكون قديمة جداً، أو لم تصل عبر Make.com / Excel بعد.
                                        أرقامها: <span className="font-mono">{(recentUpload.unmatched_orders || []).slice(0, 5).join(", ")}…</span>
                                    </span>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}

            {/* Files history */}
            <div className="bg-white border border-slate-200 rounded-xl overflow-hidden" data-testid="settlements-history">
                <div className="px-5 py-3 bg-slate-50 border-b border-slate-200 flex items-center justify-between flex-wrap gap-2">
                    <h3 className="font-extrabold text-slate-900 flex items-center gap-2">
                        <FileText size={18} weight="bold" className="text-slate-600" />
                        سجل ملفات التسويات المرفوعة
                    </h3>
                    <div className="flex items-center gap-2">
                        {!allowDelete && files.length > 0 && (
                            <a
                                href="/settings"
                                className="text-[10px] text-slate-500 hover:text-indigo-600 font-bold inline-flex items-center gap-1"
                                title="فعّل خيار &laquo;إظهار زر حذف ملفات التسويات&raquo; من الإعدادات"
                                data-testid="settlements-enable-delete-hint"
                            >
                                <Lock size={11} weight="bold" />
                                زر الحذف مخفي — فعّله من الإعدادات
                            </a>
                        )}
                        <button type="button" onClick={reload} className="text-xs text-indigo-600 hover:text-indigo-700 font-bold inline-flex items-center gap-1" data-testid="settlements-refresh-btn">
                            <ArrowsClockwise size={12} weight="bold" /> تحديث
                        </button>
                    </div>
                </div>
                {loading ? (
                    <div className="text-center py-8 text-slate-400">
                        <ArrowsClockwise size={20} className="animate-spin mx-auto mb-1" />
                        <span className="text-xs">جاري التحميل…</span>
                    </div>
                ) : files.length === 0 ? (
                    <div className="text-center py-10 text-slate-400" data-testid="settlements-empty">
                        <FileXls size={36} weight="duotone" className="mx-auto mb-2 opacity-40" />
                        <p className="text-sm">لم يتم رفع أي ملف بعد.</p>
                        <p className="text-xs mt-1">ارفع أول ملف تسوية من سلة أو تمارا أو تابي أو إمكان للبدء.</p>
                    </div>
                ) : (
                    <div className="overflow-x-auto" data-testid="settlements-table-scroll">
                        <table className="mezan-table compact w-full min-w-[1100px] text-xs" data-testid="settlements-files-table">
                            <thead className="bg-slate-50 text-slate-600 text-[11px] border-b border-slate-200">
                                <tr>
                                    <th className="text-right px-4 py-2 whitespace-nowrap">البوابة</th>
                                    <th className="text-right px-4 py-2 whitespace-nowrap">اسم الملف</th>
                                    <th className="text-right px-4 py-2 whitespace-nowrap">مرجع التسوية</th>
                                    <th className="text-right px-4 py-2 whitespace-nowrap">مطابقة / إجمالي</th>
                                    <th className="text-right px-4 py-2 whitespace-nowrap">الإجمالي</th>
                                    <th className="text-right px-4 py-2 whitespace-nowrap">العمولة</th>
                                    <th className="text-right px-4 py-2 whitespace-nowrap">الصافي</th>
                                    <th className="text-right px-4 py-2 whitespace-nowrap">المرتجع</th>
                                    <th className="text-right px-4 py-2 whitespace-nowrap bg-amber-50" title="مبالغ شُحنت من محفظتك في سلة (مثلاً لبوليصة شحن)">
                                        مشتريات سله
                                    </th>
                                    <th className="text-right px-4 py-2 whitespace-nowrap">رُفع في</th>
                                    {allowDelete && (
                                        <th
                                            className="text-center px-4 py-2 whitespace-nowrap sticky left-0 bg-rose-50 text-rose-700 border-l border-rose-200 z-10"
                                            data-testid="settlements-delete-col-header"
                                        >
                                            حذف
                                        </th>
                                    )}
                                </tr>
                            </thead>
                        <tbody className="divide-y divide-slate-100">
                            {files.map((f) => {
                                const meta = PROVIDER_META[f.provider] || {};
                                const ref = f.header?.statement_id || f.header?.invoice_number || "—";
                                const refundTotal = (f.totals?.refund_full || 0) + (f.totals?.refund_partial || 0);
                                return (
                                    <tr key={f.id} className="hover:bg-slate-50" data-testid={`settlement-row-${f.id}`}>
                                        <td className="px-4 py-2">
                                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${meta.chip || "bg-slate-200 text-slate-700"}`}>
                                                {meta.label || f.provider}
                                            </span>
                                        </td>
                                        <td className="px-4 py-2 font-mono text-[11px] max-w-[200px] truncate" title={f.filename}>{f.filename}</td>
                                        <td className="px-4 py-2 font-mono text-[11px] text-slate-600">{ref}</td>
                                        <td className="px-4 py-2">
                                            <span className="font-bold text-emerald-700">{f.matched}</span>
                                            <span className="text-slate-400"> / </span>
                                            <span className="text-slate-600">{f.rows}</span>
                                            {f.unmatched > 0 && (
                                                <button
                                                    type="button"
                                                    onClick={() => handleViewUnmatched(f)}
                                                    className="ms-1 inline-flex items-center gap-0.5 text-[10px] text-amber-600 hover:text-amber-700 underline"
                                                    data-testid={`view-unmatched-${f.id}`}
                                                >
                                                    +{f.unmatched} غير مطابق
                                                </button>
                                            )}
                                        </td>
                                        <td className="px-4 py-2 font-mono">{fmtMoney(f.totals?.gross || 0)}</td>
                                        <td className="px-4 py-2 font-mono text-rose-600">{fmtMoney(f.totals?.fees || 0)}</td>
                                        <td className="px-4 py-2 font-mono font-bold text-emerald-700">{fmtMoney(f.totals?.net || 0)}</td>
                                        <td className="px-4 py-2 font-mono text-amber-700">{refundTotal > 0 ? fmtMoney(refundTotal) : "—"}</td>
                                        <td
                                            className="px-4 py-2 font-mono text-orange-700 bg-amber-50/40"
                                            title={f.totals?.salla_purchases_count
                                                ? `${f.totals.salla_purchases_count} عملية شحن محفظة — مخصومة من إجمالي المبيعات`
                                                : "لا توجد عمليات شحن محفظة في هذا الملف"}
                                            data-testid={`salla-purchases-${f.id}`}
                                        >
                                            {(f.totals?.salla_purchases_total || 0) > 0 ? (
                                                <span className="font-bold">
                                                    -{fmtMoney(f.totals.salla_purchases_total)}
                                                    <span className="block text-[10px] text-orange-500 font-normal">
                                                        {f.totals.salla_purchases_count} عملية
                                                    </span>
                                                </span>
                                            ) : (
                                                <span className="text-slate-300">—</span>
                                            )}
                                        </td>
                                        <td className="px-4 py-2 text-slate-500 text-[11px]" dir="ltr">
                                            {f.uploaded_at ? new Date(f.uploaded_at).toLocaleString("en-US") : "—"}
                                        </td>
                                        {allowDelete && (
                                            <td className="px-4 py-2 sticky left-0 bg-white border-l border-rose-200 text-center" data-testid={`delete-cell-${f.id}`}>
                                                <button
                                                    type="button"
                                                    onClick={() => setPendingDelete(f)}
                                                    className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-700 text-white font-bold text-[11px] shadow-sm"
                                                    title="حذف الملف وإرجاع الطلبات إلى النسب التقديرية"
                                                    data-testid={`delete-settlement-${f.id}`}
                                                >
                                                    <Trash size={13} weight="bold" />
                                                    حذف
                                                </button>
                                            </td>
                                        )}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                    </div>
                )}
            </div>

            {/* Info footer */}
            <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 text-xs text-slate-600 leading-relaxed" data-testid="settlements-info">
                <div className="flex items-start gap-2">
                    <Info size={16} weight="bold" className="text-slate-500 flex-shrink-0 mt-0.5" />
                    <div>
                        <strong className="text-slate-900">كيف تعمل المطابقة؟</strong>
                        <ul className="list-disc list-inside mt-1 space-y-0.5">
                            <li>عند رفع الملف، نُطابق <span className="font-bold">رقم الطلب</span> مع طلباتك المخزّنة، ونحدّث الرسوم والصافي الفعلي فقط على الطلبات المطابقة.</li>
                            <li>الطلبات غير المطابقة تبقى محسوبة بالنسب التقديرية كما هي — لا حذف ولا تغيير.</li>
                            <li>الصافي الفعلي يُحدّث <span className="font-bold">الرصيد المتوقع</span> فقط للحساب المرتبط. لا يتم إنشاء تحويلات بنكية تلقائية.</li>
                            <li>لا يتم تعديل أي تسوية سابقة ولا إنشاء صفوف تلقائية في سجل التعديلات (payment_adjustments).</li>
                            <li>رفع نفس الملف مرة أخرى يتم تجاهله (sha256 dedup).</li>
                        </ul>
                    </div>
                </div>
            </div>

            <ConfirmModal
                open={!!pendingDelete}
                title="حذف ملف التسوية"
                description={`سيتم حذف هذا الملف وإرجاع الطلبات المرتبطة به (${pendingDelete?.matched || 0} طلب) إلى النسب التقديرية الحالية.\n\nملاحظة: لا يتم لمس أي تحويلات أو تسويات أخرى. يمكنك إعادة رفع الملف لاحقاً.`}
                confirmLabel="حذف الملف"
                onConfirm={handleDelete}
                onCancel={() => setPendingDelete(null)}
                danger
            />

            {/* Unmatched orders viewer */}
            {unmatchedView && (
                <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={() => setUnmatchedView(null)}>
                    <div className="bg-white rounded-xl shadow-2xl max-w-lg w-full max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()} data-testid="unmatched-modal">
                        <div className="px-5 py-3 border-b border-slate-200 flex items-center justify-between">
                            <h3 className="font-extrabold text-slate-900">طلبات غير مطابقة ({unmatchedView.orders.length})</h3>
                            <button onClick={() => setUnmatchedView(null)} className="text-slate-400 hover:text-slate-600">
                                <XCircle size={20} weight="bold" />
                            </button>
                        </div>
                        <div className="flex-1 overflow-y-auto p-4 font-mono text-xs">
                            <p className="text-[11px] text-slate-500 mb-2 font-sans leading-relaxed">
                                هذه الطلبات موجودة في ملف التسوية لكن غير موجودة في قاعدة بيانات النظام. قد تكون قديمة أو لم تصل بعد عبر Make.com / Excel.
                            </p>
                            <div className="space-y-0.5 bg-slate-50 rounded p-2">
                                {unmatchedView.orders.map((o) => <div key={o}>{o}</div>)}
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
