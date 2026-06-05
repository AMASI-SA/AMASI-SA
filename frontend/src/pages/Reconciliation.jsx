import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
    Scales, CheckCircle, Clock, TrendUp, CreditCard, ArrowRight,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const fmtMoney = (v, ccy = "SAR") => {
    const num = Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const cc = ccy === "SAR" ? "ر.س" : ccy;
    // U+2068..U+2069 FSI/PDI keep the number+currency rendered as a single
    // bidi-isolated atomic unit, so the browser never splits it onto two
    // visual lines in an RTL table cell.
    return `\u2068${num}\u00A0${cc}\u2069`;
};
const fmtDate = (s) => {
    if (!s) return "—";
    const [y, m, d] = String(s).split("-");
    return y && m && d ? `${d}-${m}-${y}` : s;
};

function KpiCard({ title, value, sub, tone = "slate", Icon, testid }) {
    const tones = {
        slate:   "bg-slate-50 border-slate-200 text-slate-900",
        emerald: "bg-emerald-50 border-emerald-200 text-emerald-900",
        amber:   "bg-amber-50 border-amber-200 text-amber-900",
        sky:     "bg-sky-50 border-sky-200 text-sky-900",
    };
    return (
        <div className={`rounded-xl border p-4 ${tones[tone]}`} data-testid={testid}>
            <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold opacity-80">{title}</span>
                {Icon && <Icon size={20} weight="duotone" className="opacity-70" />}
            </div>
            <div className="num text-2xl sm:text-3xl font-extrabold">{value}</div>
            {sub && <div className="text-[11px] opacity-70 mt-1">{sub}</div>}
        </div>
    );
}

function RateBar({ pct }) {
    const clamped = Math.max(0, Math.min(100, Number(pct || 0)));
    const tone = clamped >= 80 ? "bg-emerald-500" : clamped >= 40 ? "bg-amber-500" : "bg-rose-500";
    return (
        <div className="flex items-center gap-2 min-w-[120px]">
            <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden max-w-[100px]">
                <div className={`h-full ${tone}`} style={{ width: `${clamped.toFixed(1)}%` }} />
            </div>
            <span className="num text-xs font-bold text-muted-foreground">{clamped.toFixed(1)}%</span>
        </div>
    );
}

export default function Reconciliation() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = async () => {
        try {
            const { data: d } = await api.get("/reconciliation/summary");
            setData(d);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر التحميل");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    if (loading) return <div className="p-10 text-center text-muted-foreground">جاري التحميل...</div>;
    if (!data) return null;

    const t = data.totals;

    return (
        <div className="space-y-6" data-testid="reconciliation-page">
            <header>
                <h1 className="text-3xl sm:text-4xl font-extrabold text-foreground flex items-center gap-3" style={{ fontFamily: "Tajawal" }}>
                    <Scales size={32} className="text-brand" weight="duotone" />
                    المطابقة والتسويات
                </h1>
                <p className="text-muted-foreground mt-1 text-sm">
                    قارن المتوقع من الطلبات مع المحوّل الفعلي لكل منصة دفع. النسبة العامة الحالية: <span className="font-bold text-foreground num">{t.collection_rate.toFixed(1)}%</span>
                </p>
            </header>

            {/* Summary cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                <KpiCard
                    title="إجمالي المتوقع"
                    value={fmtMoney(t.expected)}
                    sub="من جميع الطلبات بكل المنصات"
                    tone="slate"
                    Icon={CreditCard}
                    testid="kpi-total-expected"
                />
                <KpiCard
                    title="إجمالي المحوّل"
                    value={fmtMoney(t.transferred)}
                    sub="وصل إلى البنوك فعلياً"
                    tone="emerald"
                    Icon={CheckCircle}
                    testid="kpi-total-transferred"
                />
                <KpiCard
                    title="إجمالي المعلّق"
                    value={fmtMoney(t.pending)}
                    sub="في انتظار التحويل البنكي"
                    tone="amber"
                    Icon={Clock}
                    testid="kpi-total-pending"
                />
                <KpiCard
                    title="نسبة التحصيل"
                    value={`${t.collection_rate.toFixed(1)}%`}
                    sub="من المتوقع → المحوّل"
                    tone="sky"
                    Icon={TrendUp}
                    testid="kpi-collection-rate"
                />
            </div>

            {/* Main table */}
            <div className="bg-white rounded-xl border border-border overflow-hidden" data-testid="reconciliation-table">
                <div className="px-5 py-3 border-b border-border bg-slate-50 flex items-center justify-between">
                    <h2 className="font-bold text-foreground">المطابقة لكل منصة دفع</h2>
                    <span className="text-xs text-muted-foreground">{data.platforms.length} منصة</span>
                </div>
                {data.platforms.length === 0 ? (
                    <div className="p-10 text-center text-muted-foreground">
                        لا توجد منصات دفع بعد. <Link to="/accounts" className="text-brand font-bold hover:underline">انتقل إلى الأصول</Link> وزامن طرق الدفع.
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm" style={{ minWidth: 1100 }}>
                            <thead className="bg-slate-50/50 text-xs text-muted-foreground">
                                <tr>
                                    <th className="text-right px-3 py-2.5 font-bold whitespace-nowrap">المنصة</th>
                                    <th className="text-right px-3 py-2.5 font-bold whitespace-nowrap">المتوقع</th>
                                    <th className="text-right px-3 py-2.5 font-bold whitespace-nowrap">المحوّل</th>
                                    <th className="text-right px-3 py-2.5 font-bold whitespace-nowrap">المعلّق</th>
                                    <th className="text-right px-3 py-2.5 font-bold whitespace-nowrap">الرصيد الحالي</th>
                                    <th className="text-right px-3 py-2.5 font-bold whitespace-nowrap">نسبة التحصيل</th>
                                    <th className="text-right px-3 py-2.5 font-bold whitespace-nowrap">آخر تحويل</th>
                                    <th className="text-right px-3 py-2.5 font-bold whitespace-nowrap">عدد التحويلات</th>
                                    <th className="text-right px-3 py-2.5 font-bold"></th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.platforms.map((p) => (
                                    <tr key={p.account_id} className="border-t border-border hover:bg-accent/20 transition" data-testid={`recon-row-${p.normalized_payment_method}`}>
                                        <td className="px-3 py-3 whitespace-nowrap">
                                            <Link to={`/reconciliation/${p.account_id}`} className="font-bold text-foreground hover:text-brand">
                                                {p.name}
                                            </Link>
                                            <div className="text-[11px] text-muted-foreground">{p.orders_count} طلب</div>
                                        </td>
                                        <td className="px-3 py-3 num font-bold whitespace-nowrap">{fmtMoney(p.expected, p.currency)}</td>
                                        <td className="px-3 py-3 num font-bold text-emerald-700 whitespace-nowrap">{fmtMoney(p.transferred, p.currency)}</td>
                                        <td className={`px-3 py-3 num font-bold whitespace-nowrap ${p.pending > 0 ? "text-amber-700" : p.pending < 0 ? "text-rose-700" : "text-muted-foreground"}`}>
                                            {fmtMoney(p.pending, p.currency)}
                                        </td>
                                        <td className="px-3 py-3 num font-bold text-sky-700 whitespace-nowrap">{fmtMoney(p.current_balance, p.currency)}</td>
                                        <td className="px-3 py-3 whitespace-nowrap" style={{ minWidth: 130 }}><RateBar pct={p.collection_rate} /></td>
                                        <td className="px-3 py-3 text-xs whitespace-nowrap">
                                            {p.last_transfer_at ? (
                                                <div>
                                                    <div className="num font-bold text-foreground">{fmtDate(p.last_transfer_at)}</div>
                                                    {p.last_transfer_to_bank && <div className="text-[10px] text-muted-foreground">إلى {p.last_transfer_to_bank}</div>}
                                                </div>
                                            ) : <span className="text-muted-foreground">لا يوجد</span>}
                                        </td>
                                        <td className="px-3 py-3 num text-center font-bold text-foreground whitespace-nowrap">{p.transfers_count || 0}</td>
                                        <td className="px-3 py-3 whitespace-nowrap">
                                            <Link to={`/reconciliation/${p.account_id}`} className="inline-flex items-center gap-1 text-xs text-brand hover:underline font-bold" data-testid={`recon-detail-${p.normalized_payment_method}`}>
                                                التفاصيل <ArrowRight size={12} />
                                            </Link>
                                        </td>
                                    </tr>
                                ))}
                                {/* Totals row */}
                                <tr className="border-t-2 border-border bg-slate-50/70 font-bold">
                                    <td className="px-3 py-3 whitespace-nowrap">الإجمالي</td>
                                    <td className="px-3 py-3 num whitespace-nowrap">{fmtMoney(t.expected)}</td>
                                    <td className="px-3 py-3 num text-emerald-700 whitespace-nowrap">{fmtMoney(t.transferred)}</td>
                                    <td className="px-3 py-3 num text-amber-700 whitespace-nowrap">{fmtMoney(t.pending)}</td>
                                    <td className="px-3 py-3 num text-sky-700 whitespace-nowrap">{fmtMoney(data.platforms.reduce((s, p) => s + (p.current_balance || 0), 0))}</td>
                                    <td className="px-3 py-3 whitespace-nowrap" style={{ minWidth: 130 }}><RateBar pct={t.collection_rate} /></td>
                                    <td></td>
                                    <td className="px-3 py-3 num text-center whitespace-nowrap">{data.platforms.reduce((s, p) => s + (p.transfers_count || 0), 0)}</td>
                                    <td></td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}
