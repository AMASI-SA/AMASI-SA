import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
    ArrowLeft, CheckCircle, Clock, CreditCard, TrendUp, Paperclip, ArrowsLeftRight,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const fmtMoney = (v, ccy = "SAR") => {
    const num = Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const cc = ccy === "SAR" ? "ر.س" : ccy;
    return `\u2068${num}\u00A0${cc}\u2069`;
};
const fmtDate = (s) => {
    if (!s) return "—";
    const [y, m, d] = String(s).split("-");
    return y && m && d ? `${d}-${m}-${y}` : s;
};

export default function ReconciliationDetail() {
    const { accountId } = useParams();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        (async () => {
            try {
                const { data: d } = await api.get(`/reconciliation/platform/${accountId}`);
                setData(d);
            } catch (err) {
                toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر التحميل");
            } finally {
                setLoading(false);
            }
        })();
    }, [accountId]);

    if (loading) return <div className="p-10 text-center text-muted-foreground">جاري التحميل...</div>;
    if (!data) return null;

    const s = data.summary;
    const transfers = data.transfers || [];

    // Visual bar widths
    const total = Math.max(1, s.expected);
    const wTransferred = (s.transferred / total) * 100;
    const wPending = Math.max(0, (s.pending / total) * 100);

    return (
        <div className="space-y-6" data-testid="reconciliation-detail">
            <Link to="/reconciliation" className="inline-flex items-center gap-1.5 text-sm font-bold text-brand hover:underline">
                <ArrowLeft size={16} /> رجوع إلى المطابقة
            </Link>

            <header>
                <h1 className="text-3xl sm:text-4xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                    مطابقة حساب: {s.name}
                </h1>
                <p className="text-muted-foreground mt-1 text-sm num">
                    {s.orders_count} طلب · نسبة التحصيل {s.collection_rate.toFixed(1)}%
                </p>
            </header>

            {/* KPIs */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                <div className="rounded-xl bg-slate-50 border border-slate-200 p-4" data-testid="detail-expected">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-bold text-slate-700">المتوقع</span>
                        <CreditCard size={20} className="text-slate-500" weight="duotone" />
                    </div>
                    <div className="num text-2xl font-extrabold text-slate-900">{fmtMoney(s.expected, s.currency)}</div>
                </div>
                <div className="rounded-xl bg-emerald-50 border border-emerald-200 p-4" data-testid="detail-transferred">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-bold text-emerald-800">المحوّل</span>
                        <CheckCircle size={20} className="text-emerald-600" weight="duotone" />
                    </div>
                    <div className="num text-2xl font-extrabold text-emerald-700">{fmtMoney(s.transferred, s.currency)}</div>
                </div>
                <div className="rounded-xl bg-amber-50 border border-amber-200 p-4" data-testid="detail-pending">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-bold text-amber-800">المعلّق</span>
                        <Clock size={20} className="text-amber-600" weight="duotone" />
                    </div>
                    <div className={`num text-2xl font-extrabold ${s.pending < 0 ? "text-rose-700" : "text-amber-700"}`}>
                        {fmtMoney(s.pending, s.currency)}
                    </div>
                </div>
                <div className="rounded-xl bg-sky-50 border border-sky-200 p-4" data-testid="detail-rate">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-bold text-sky-800">نسبة التحصيل</span>
                        <TrendUp size={20} className="text-sky-600" weight="duotone" />
                    </div>
                    <div className="num text-2xl font-extrabold text-sky-700">{s.collection_rate.toFixed(1)}%</div>
                </div>
            </div>

            {/* Visual stacked bar */}
            <div className="bg-white rounded-xl border border-border p-5" data-testid="detail-chart">
                <h2 className="font-bold text-foreground mb-3">توزيع المتوقع</h2>
                <div className="space-y-2 text-xs">
                    <div className="flex items-center gap-3">
                        <span className="w-24 font-bold">المحوّل</span>
                        <div className="flex-1 h-4 bg-slate-100 rounded-full overflow-hidden">
                            <div className="h-full bg-emerald-500 transition-all duration-700 flex items-center justify-end pr-2 text-white text-[10px] font-bold"
                                style={{ width: `${Math.max(2, wTransferred).toFixed(1)}%` }}>
                                {s.transferred > 0 && fmtMoney(s.transferred, s.currency)}
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center gap-3">
                        <span className="w-24 font-bold">المعلّق</span>
                        <div className="flex-1 h-4 bg-slate-100 rounded-full overflow-hidden">
                            <div className="h-full bg-amber-400 transition-all duration-700 flex items-center justify-end pr-2 text-white text-[10px] font-bold"
                                style={{ width: `${Math.max(2, wPending).toFixed(1)}%` }}>
                                {s.pending > 0 && fmtMoney(s.pending, s.currency)}
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center gap-3">
                        <span className="w-24 font-bold text-muted-foreground">المتوقع</span>
                        <div className="flex-1 h-4 bg-slate-200 rounded-full overflow-hidden">
                            <div className="h-full bg-slate-400 flex items-center justify-end pr-2 text-white text-[10px] font-bold" style={{ width: "100%" }}>
                                {fmtMoney(s.expected, s.currency)}
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Transfers list */}
            <div className="bg-white rounded-xl border border-border overflow-hidden">
                <div className="px-5 py-3 border-b border-border bg-slate-50 flex items-center justify-between">
                    <h2 className="font-bold text-foreground">جدول التحويلات إلى البنوك</h2>
                    <Link to="/transfers" className="inline-flex items-center gap-1 text-xs text-brand font-bold hover:underline">
                        <ArrowsLeftRight size={12} /> تسجيل تحويل جديد
                    </Link>
                </div>
                {transfers.length === 0 ? (
                    <div className="p-10 text-center text-muted-foreground">
                        لا توجد تحويلات بعد. <Link to="/transfers" className="text-brand font-bold hover:underline">سجّل أول تحويل</Link>
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="mezan-table w-full text-sm">
                            <thead className="bg-slate-50/50 text-xs text-muted-foreground">
                                <tr>
                                    <th className="text-right px-4 py-2.5 font-bold">التاريخ</th>
                                    <th className="text-right px-4 py-2.5 font-bold">البنك المستلم</th>
                                    <th className="text-right px-4 py-2.5 font-bold">المبلغ</th>
                                    <th className="text-right px-4 py-2.5 font-bold">المرجع</th>
                                    <th className="text-right px-4 py-2.5 font-bold">المرفق</th>
                                </tr>
                            </thead>
                            <tbody>
                                {transfers.map((t) => (
                                    <tr key={t.id} className="border-t border-border hover:bg-accent/20" data-testid={`transfer-row-${t.id}`}>
                                        <td className="px-4 py-2.5 num text-xs">{fmtDate(t.transfer_date)}</td>
                                        <td className="px-4 py-2.5 font-bold text-emerald-700">{t.to_account_name}</td>
                                        <td className="px-4 py-2.5 num font-bold">{fmtMoney(t.amount, s.currency)}</td>
                                        <td className="px-4 py-2.5 text-xs text-muted-foreground">{t.reference || "—"}</td>
                                        <td className="px-4 py-2.5">
                                            {t.attachment_url ? (
                                                <a href={t.attachment_url} target="_blank" rel="noreferrer" className="text-brand hover:underline inline-flex items-center gap-1 text-xs">
                                                    <Paperclip size={12} /> فتح
                                                </a>
                                            ) : <span className="text-muted-foreground text-xs">—</span>}
                                        </td>
                                    </tr>
                                ))}
                                <tr className="border-t-2 border-border bg-emerald-50/50 font-bold">
                                    <td className="px-4 py-2.5">الإجمالي</td>
                                    <td></td>
                                    <td className="px-4 py-2.5 num text-emerald-700">{fmtMoney(s.transferred, s.currency)}</td>
                                    <td colSpan={2}></td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}
