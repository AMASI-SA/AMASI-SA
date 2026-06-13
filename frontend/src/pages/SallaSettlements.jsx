/**
 * SallaSettlements — Iter-156
 *
 * Mirrors the BNPL Settlements page pattern but for Salla. The Salla
 * parser already exists (see /app/backend/settlements_import/parsers/salla.py)
 * and writes to `settlement_files` + `settlement_entries`. This page
 * surfaces uploads, per-payment-method breakdowns (mada / credit card /
 * Apple Pay / STC Pay), and full/partial refund summaries.
 *
 * File upload uses the same /api/payment-settlements/upload endpoint
 * with `provider_hint=salla`.
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const fmt = (n) =>
    Number(n || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

const METHOD_META = {
    mada:           { label: "مدى",            icon: "🟢", color: "bg-emerald-50 text-emerald-700 border-emerald-200" },
    credit_card:    { label: "بطاقة ائتمانية", icon: "💳", color: "bg-indigo-50 text-indigo-700 border-indigo-200" },
    apple_pay:      { label: "أبل باي",        icon: "🍎", color: "bg-slate-50 text-slate-700 border-slate-300" },
    stc_pay:        { label: "STC Pay",        icon: "🟣", color: "bg-violet-50 text-violet-700 border-violet-200" },
    google_pay:     { label: "Google Pay",     icon: "🅖", color: "bg-blue-50 text-blue-700 border-blue-200" },
    wallet_recharge:{ label: "محفظة سلة",      icon: "👛", color: "bg-amber-50 text-amber-700 border-amber-200" },
    unknown:        { label: "أخرى",           icon: "❓", color: "bg-slate-50 text-slate-600 border-slate-200" },
};

export default function SallaSettlements() {
    const [data, setData] = useState({ files: [], per_method: [], totals: {} });
    const [loading, setLoading] = useState(true);
    const [uploading, setUploading] = useState(false);
    const fileInputRef = useRef(null);

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/payment-settlements/_analytics/salla");
            setData(data);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر تحميل البيانات");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const onPickFile = () => fileInputRef.current?.click();

    const onUpload = async (e) => {
        const f = e.target.files?.[0];
        if (!f) return;
        // Iter-159g — Salla invoice files do NOT contain the issue date.
        // Ask the merchant to enter it so it appears as "تاريخ التحويل"
        // in the unified settlements overview.
        const today = new Date().toISOString().slice(0, 10);
        const dateInput = window.prompt(
            "تاريخ إصدار الفاتورة من سلة (YYYY-MM-DD)\n" +
            "هذا هو التاريخ الذي سيظهر كـ «تاريخ التحويل» في جدول التسويات.",
            today,
        );
        if (dateInput === null) {
            if (fileInputRef.current) fileInputRef.current.value = "";
            return;
        }
        const invoiceDate = (dateInput || "").trim();
        if (invoiceDate && !/^\d{4}-\d{2}-\d{2}$/.test(invoiceDate)) {
            toast.error("صيغة التاريخ يجب أن تكون YYYY-MM-DD");
            if (fileInputRef.current) fileInputRef.current.value = "";
            return;
        }
        const form = new FormData();
        form.append("file", f);
        form.append("provider_hint", "salla");
        if (invoiceDate) form.append("invoice_date", invoiceDate);
        setUploading(true);
        try {
            const { data: r } = await api.post(
                "/payment-settlements/upload",
                form,
                { headers: { "Content-Type": "multipart/form-data" } },
            );
            toast.success(
                `تم استيراد ${r.rows} صف · مطابقة ${r.matched} / غير مطابقة ${r.unmatched}`,
                { duration: 6000 },
            );
            await load();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل الاستيراد");
        } finally {
            setUploading(false);
            if (fileInputRef.current) fileInputRef.current.value = "";
        }
    };

    const onDelete = async (fileId, filename) => {
        if (!window.confirm(`حذف ملف «${filename}»؟ سيتم التراجع عن جميع التطبيقات على الطلبات.`)) return;
        try {
            await api.delete(`/payment-settlements/${fileId}`);
            toast.success("تم الحذف");
            await load();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل الحذف");
        }
    };

    const t = data.totals || {};

    return (
        <div dir="rtl" data-testid="salla-settlements-page" className="space-y-5 p-4 md:p-6 max-w-7xl mx-auto">
            <div className="flex items-start justify-between gap-3 flex-wrap">
                <div>
                    <h1 className="text-2xl font-extrabold text-slate-900">تسويات سلة 🟧</h1>
                    <p className="text-xs text-slate-500 mt-1">
                        ملفات الفواتير المُصدَّرة من لوحة سلة — مع التحليل حسب طريقة الدفع والمقارنة مع المتوقع.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <input type="file" hidden ref={fileInputRef}
                           accept=".xlsx,.xls,.csv"
                           onChange={onUpload}
                           data-testid="salla-upload-input" />
                    <button onClick={onPickFile} disabled={uploading}
                            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-bold rounded-lg disabled:opacity-50"
                            data-testid="salla-upload-btn">
                        {uploading ? "جاري الرفع..." : "📤 رفع ملف فاتورة سلة"}
                    </button>
                    <Link to="/bnpl/settlements" className="text-xs text-slate-500 underline">← تسويات Tabby / Tamara</Link>
                </div>
            </div>

            {/* Totals */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                <Stat label="الملفات المستوردة" value={t.files || 0} testid="salla-stat-files" />
                <Stat label="إجمالي المبيعات" value={`${fmt(t.gross || 0)} ر.س`} tone="slate" testid="salla-stat-gross" />
                <Stat label="عمولات سلة" value={`${fmt(t.fees || 0)} ر.س`} tone="rose" testid="salla-stat-fees" />
                <Stat label="ضريبة العمولات" value={`${fmt(t.vat || 0)} ر.س`} tone="amber" testid="salla-stat-vat" />
                <Stat label="الصافي للتاجر" value={`${fmt(t.net || 0)} ر.س`} tone="emerald" bold testid="salla-stat-net" />
            </div>

            {(t.refund_full > 0 || t.refund_partial > 0) && (
                <div className="grid grid-cols-2 gap-3">
                    <Stat label="ارتدادات كاملة" value={`${fmt(t.refund_full)} ر.س`} tone="rose" testid="salla-stat-refund-full" />
                    <Stat label="ارتدادات جزئية" value={`${fmt(t.refund_partial)} ر.س`} tone="amber" testid="salla-stat-refund-partial" />
                </div>
            )}

            {/* Per payment method */}
            <div className="bg-white border border-slate-200 rounded-xl p-4">
                <h2 className="text-base font-bold text-slate-900 mb-3">📊 التوزيع حسب طريقة الدفع</h2>
                {loading ? (
                    <div className="text-center py-6 text-slate-500 text-sm">جاري التحميل...</div>
                ) : (data.per_method || []).length === 0 ? (
                    <div className="text-center py-6 text-slate-500 text-sm">
                        لم يتم رفع أي ملف بعد. اضغط <strong>📤 رفع ملف فاتورة سلة</strong> أعلاه للبدء.
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead className="bg-slate-50 text-slate-700">
                                <tr>
                                    <th className="p-2 text-right">طريقة الدفع</th>
                                    <th className="p-2 num text-right">عدد الطلبات</th>
                                    <th className="p-2 num text-right">المبيعات</th>
                                    <th className="p-2 num text-right">عمولة سلة</th>
                                    <th className="p-2 num text-right">% عمولة</th>
                                    <th className="p-2 num text-right">VAT</th>
                                    <th className="p-2 num text-right">الصافي</th>
                                </tr>
                            </thead>
                            <tbody>
                                {(data.per_method || []).map((m) => {
                                    const meta = METHOD_META[m.payment_method] || METHOD_META.unknown;
                                    return (
                                        <tr key={m.payment_method} className="border-t border-slate-100 hover:bg-slate-50" data-testid={`salla-method-${m.payment_method}`}>
                                            <td className="p-2">
                                                <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-extrabold border ${meta.color}`}>
                                                    {meta.icon} {meta.label}
                                                </span>
                                            </td>
                                            <td className="p-2 num text-right">{m.count}</td>
                                            <td className="p-2 num text-right">{fmt(m.gross)}</td>
                                            <td className="p-2 num text-right text-rose-700">{fmt(m.fees)}</td>
                                            <td className="p-2 num text-right text-rose-600">{m.effective_fee_rate}%</td>
                                            <td className="p-2 num text-right text-amber-700">{fmt(m.vat)}</td>
                                            <td className="p-2 num text-right font-extrabold text-emerald-700">{fmt(m.net)}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Files list */}
            <div className="bg-white border border-slate-200 rounded-xl p-4">
                <h2 className="text-base font-bold text-slate-900 mb-3">📁 الفواتير المستوردة ({(data.files || []).length})</h2>
                {loading ? (
                    <div className="text-center py-6 text-slate-500 text-sm">جاري التحميل...</div>
                ) : (data.files || []).length === 0 ? (
                    <div className="text-center py-6 text-slate-500 text-sm">لا توجد فواتير بعد.</div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead className="bg-slate-50 text-slate-700">
                                <tr>
                                    <th className="p-2 text-right">رقم الفاتورة</th>
                                    <th className="p-2 text-right">اسم الملف</th>
                                    <th className="p-2 num text-right">صفوف</th>
                                    <th className="p-2 num text-right">مطابقة</th>
                                    <th className="p-2 num text-right">إجمالي</th>
                                    <th className="p-2 num text-right">صافي</th>
                                    <th className="p-2 text-right">تاريخ الرفع</th>
                                    <th className="p-2 w-12"></th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.files.map((f) => (
                                    <tr key={f.id} className="border-t border-slate-100 hover:bg-slate-50" data-testid={`salla-file-${f.id}`}>
                                        <td className="p-2 num font-bold">{f.header?.invoice_number || "—"}</td>
                                        <td className="p-2 truncate max-w-xs" title={f.filename}>{f.filename}</td>
                                        <td className="p-2 num text-right">{f.rows}</td>
                                        <td className="p-2 num text-right text-emerald-700">
                                            {f.matched} / {f.matched + f.unmatched}
                                        </td>
                                        <td className="p-2 num text-right">{fmt(f.totals?.gross || 0)}</td>
                                        <td className="p-2 num text-right font-extrabold text-emerald-700">{fmt(f.totals?.net || 0)}</td>
                                        <td className="p-2 text-xs text-slate-500" dir="ltr">
                                            {(f.uploaded_at || "").slice(0, 16).replace("T", " ")}
                                        </td>
                                        <td className="p-2">
                                            <button onClick={() => onDelete(f.id, f.filename)}
                                                    className="text-rose-500 hover:text-rose-700 text-sm font-extrabold"
                                                    data-testid={`salla-delete-${f.id}`}>
                                                ×
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}

function Stat({ label, value, tone = "slate", bold = false, testid }) {
    const tones = {
        slate: "bg-slate-50 border-slate-200 text-slate-700",
        emerald: "bg-emerald-50 border-emerald-200 text-emerald-700",
        rose: "bg-rose-50 border-rose-200 text-rose-700",
        amber: "bg-amber-50 border-amber-200 text-amber-700",
    };
    return (
        <div className={`p-3 rounded-xl border ${tones[tone]}`} data-testid={testid}>
            <div className="text-[11px] opacity-80">{label}</div>
            <div className={`num mt-1 ${bold ? "text-lg font-extrabold" : "text-base font-bold"}`}>{value}</div>
        </div>
    );
}
