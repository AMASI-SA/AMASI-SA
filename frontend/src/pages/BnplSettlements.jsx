/**
 * BNPL Settlements — Phase 4
 *
 * Reads from /api/bnpl/settlements/summary (pure DB aggregation,
 * no extra provider API calls) and shows merchants their automatic
 * settlement breakdown for Tabby and Tamara, plus reconciliation
 * against the linked bank/wallet account transfers.
 *
 * All amounts come from existing data:
 *   gross_sales     ← payment_transactions.amount
 *   refunds         ← payment_transactions.refunded_amount
 *   commission      ← net_sales × commission_pct
 *   commission_vat  ← commission × 15%
 *   net_payable     ← net_sales − commission − VAT
 *   transferred     ← account_transactions where direction=out
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
    ArrowsClockwise, CheckCircle, WarningCircle,
    Bank, Calculator, Receipt,
} from "@phosphor-icons/react";
import api from "../lib/api";

const errMsg = (e, fb) =>
    e?.response?.data?.error
    || e?.response?.data?.detail
    || e?.message
    || fb;

const fmt = (n) =>
    (Number(n) || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
const fmtInt = (n) => (Number(n) || 0).toLocaleString("en-US");

const PROVIDER_META = {
    tabby:  { name: "Tabby",  icon: "🟣", color: "from-violet-50 to-white", accent: "violet" },
    tamara: { name: "Tamara", icon: "🩷", color: "from-rose-50 to-white", accent: "rose" },
};

function Row({ label, value, hint, bold, tone }) {
    const toneCls = {
        emerald: "text-emerald-700",
        rose: "text-rose-700",
        slate: "text-slate-900",
        amber: "text-amber-700",
    }[tone] || "text-slate-900";
    return (
        <div className={`flex justify-between items-baseline py-1.5 ${bold ? "border-t border-slate-200 mt-1 pt-2" : ""}`}>
            <div className="text-slate-600 text-xs">{label}</div>
            <div className={`num ${toneCls} ${bold ? "text-base font-extrabold" : "text-sm font-semibold"}`}>
                {value}
                {hint && <span className="text-[10px] text-slate-400 mr-1"> {hint}</span>}
            </div>
        </div>
    );
}

function ProviderCard({ data }) {
    const meta = PROVIDER_META[data.provider] || { name: data.provider, icon: "💳", color: "from-slate-50 to-white" };
    const t = data.totals || {};
    const b = data.bank || {};
    const fees = data.fee_rates || {};
    const delta = b.delta_overpayment || 0;

    const tone =
        Math.abs(delta) < 0.5 ? "emerald"
        : delta > 0 ? "amber"      // bank received more than due
        : "rose";                  // shortfall

    return (
        <div
            className={`rounded-2xl border border-slate-200 bg-gradient-to-l ${meta.color} p-5`}
            data-testid={`bnpl-settle-card-${data.provider}`}
        >
            <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                <h3 className="text-xl font-extrabold text-slate-900 flex items-center gap-2">
                    <span className="text-2xl">{meta.icon}</span> {meta.name}
                    <span className="text-xs text-slate-400 font-normal num">
                        ({fmtInt(t.transactions_count)} عملية)
                    </span>
                </h3>
                <div className="text-xs text-slate-600 bg-white px-2 py-1 rounded-lg border border-slate-200">
                    عمولة: <span className="font-bold num">{fmt(fees.commission_pct)}%</span>
                    {" · "} VAT: <span className="font-bold num">{fmt(fees.vat_pct)}%</span>
                </div>
            </div>

            {/* Settlement breakdown */}
            <div className="bg-white rounded-xl border border-slate-200 p-4 mb-3">
                <div className="text-xs font-bold text-slate-500 mb-1 flex items-center gap-1">
                    <Calculator size={14} weight="duotone" /> حساب التسوية
                </div>
                <Row label="إجمالي المبيعات" value={`${fmt(t.gross_sales)} ر.س`} />
                <Row label="إجمالي المسترجعات" value={`(${fmt(t.total_refunds)})`} tone="rose" />
                <Row label="صافي المبيعات" value={`${fmt(t.net_sales)} ر.س`} bold tone="slate" />
                <Row label={`عمولة المزوّد (${fmt(fees.commission_pct)}%)`} value={`(${fmt(t.commission)})`} tone="rose" />
                <Row label={`ضريبة العمولة (${fmt(fees.vat_pct)}%)`} value={`(${fmt(t.commission_vat)})`} tone="rose" />
                <Row label="صافي المستحق" value={`${fmt(t.net_payable)} ر.س`} bold tone="emerald" />
            </div>

            {/* Bank reconciliation */}
            <div className="bg-white rounded-xl border border-slate-200 p-4">
                <div className="text-xs font-bold text-slate-500 mb-1 flex items-center gap-1">
                    <Bank size={14} weight="duotone" /> المطابقة مع التحويلات البنكية
                </div>
                {b.is_linked ? (
                    <>
                        <Row label="الحساب المرتبط" value={b.linked_account_name} />
                        <Row label="صافي المستحق" value={`${fmt(t.net_payable)} ر.س`} />
                        <Row label="المحوَّل إلى البنك" value={`${fmt(b.transferred_amount)} ر.س`} tone="slate" />
                        <Row
                            label="المتبقي لدى المزوّد"
                            value={`${fmt(b.remaining_with_provider)} ر.س`}
                            bold
                            tone={tone}
                        />
                        {Math.abs(delta) >= 0.5 && (
                            <div className={`mt-2 p-2 text-xs rounded-lg border ${
                                delta > 0
                                    ? "bg-amber-50 border-amber-200 text-amber-900"
                                    : "bg-rose-50 border-rose-200 text-rose-900"
                            }`}>
                                {delta > 0
                                    ? <>⚠ زيادة في التحويلات بمبلغ <strong className="num">{fmt(delta)}</strong> ر.س — قد تكون تسوية سابقة أو خطأ في التسجيل.</>
                                    : <>📌 نقص في التحويلات — المزوّد لا يزال يحتفظ بمبلغ <strong className="num">{fmt(b.remaining_with_provider)}</strong> ر.س.</>}
                            </div>
                        )}
                    </>
                ) : (
                    <div className="text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded-lg p-3">
                        ⚠ لا يوجد حساب مرتبط لهذا المزوّد. أنشئ حساباً من النوع
                        <strong> «منصة دفع»</strong> وضع اسم المزوّد <code className="bg-white px-1">{data.provider}</code>
                        في صفحة <a href="/accounts" className="underline">الأصول والحسابات</a>.
                    </div>
                )}
            </div>
        </div>
    );
}

export default function BnplSettlements() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [fromDate, setFromDate] = useState("");
    const [toDate, setToDate] = useState("");

    const load = async (silent = false) => {
        if (!silent) setRefreshing(true);
        try {
            const params = {};
            if (fromDate) params.from = fromDate;
            if (toDate) params.to = toDate;
            const { data: r } = await api.get("/bnpl/settlements/summary", { params });
            if (r?.success === false) {
                toast.error(`خطأ: ${r.error || "غير معروف"}`);
            } else {
                setData(r);
            }
        } catch (e) {
            toast.error(errMsg(e, "تعذّر تحميل التسويات"));
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    };

    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                const { data: r } = await api.get("/bnpl/settlements/summary");
                if (alive && r?.success !== false) setData(r);
            } catch (e) {
                if (alive) toast.error(errMsg(e, "تعذّر تحميل التسويات"));
            } finally {
                if (alive) setLoading(false);
            }
        })();
        return () => { alive = false; };
    }, []);

    const reconcStatus = useMemo(() => {
        if (!data) return null;
        const anyShortfall = (data.providers || []).some(
            (p) => (p.bank?.remaining_with_provider || 0) > 0.5,
        );
        const allLinked = (data.providers || []).every((p) => p.bank?.is_linked);
        return { allLinked, anyShortfall };
    }, [data]);

    return (
        <div className="space-y-5" dir="rtl" data-testid="bnpl-settlements-page">
            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl font-extrabold text-slate-900">
                        💰 تسويات Tabby و Tamara — تلقائية
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        صافي المستحق محسوب من بيانات النظام مباشرة (عمولة + ضريبة) ومُطابَق مع التحويلات البنكية.
                        البيانات تُحدَّث تلقائياً مع كل مزامنة BNPL.
                    </p>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                    <input
                        type="date"
                        value={fromDate}
                        onChange={(e) => setFromDate(e.target.value)}
                        className="px-2 py-1.5 border border-slate-300 rounded-lg text-sm text-slate-700 num"
                        placeholder="من تاريخ"
                        data-testid="bnpl-settle-from"
                    />
                    <span className="text-slate-400">→</span>
                    <input
                        type="date"
                        value={toDate}
                        onChange={(e) => setToDate(e.target.value)}
                        className="px-2 py-1.5 border border-slate-300 rounded-lg text-sm text-slate-700 num"
                        placeholder="إلى تاريخ"
                        data-testid="bnpl-settle-to"
                    />
                    <button
                        type="button"
                        onClick={() => load(false)}
                        disabled={refreshing}
                        className="px-4 py-2 bg-emerald-700 text-white text-sm font-bold rounded-lg hover:bg-emerald-800 disabled:opacity-50 flex items-center gap-2"
                        data-testid="bnpl-settle-refresh"
                    >
                        <ArrowsClockwise size={16} className={refreshing ? "animate-spin" : ""} />
                        {refreshing ? "جاري الحساب…" : "تطبيق الفلتر"}
                    </button>
                </div>
            </div>

            {loading && <div className="text-center text-slate-500 py-12">جاري حساب التسويات…</div>}

            {data && (
                <>
                    {/* Global reconciliation banner */}
                    <div
                        className={`rounded-2xl border-2 p-4 flex items-center gap-3 ${
                            !reconcStatus?.anyShortfall
                                ? "bg-emerald-50 border-emerald-300"
                                : "bg-amber-50 border-amber-300"
                        }`}
                        data-testid="bnpl-settle-banner"
                    >
                        {!reconcStatus?.anyShortfall
                            ? <CheckCircle size={28} weight="fill" className="text-emerald-700 flex-shrink-0" />
                            : <WarningCircle size={28} weight="fill" className="text-amber-700 flex-shrink-0" />}
                        <div>
                            <div className="font-extrabold text-slate-900">
                                {!reconcStatus?.anyShortfall
                                    ? "✓ كل التسويات مكتملة — لا توجد مبالغ متبقية لدى المزودين"
                                    : "📌 توجد مبالغ متبقية لدى المزودين بانتظار التحويل"}
                            </div>
                            <div className="text-xs text-slate-600 mt-0.5">
                                آخر حساب: {new Date(data.computed_at).toLocaleString("ar-SA", { dateStyle: "short", timeStyle: "short" })}
                            </div>
                        </div>
                    </div>

                    {/* Combined totals */}
                    <div className="rounded-2xl border border-slate-200 bg-white p-5">
                        <h2 className="text-lg font-extrabold text-slate-900 mb-3 flex items-center gap-2">
                            <Receipt size={20} weight="duotone" className="text-slate-700" />
                            الإجمالي الموحَّد (Tabby + Tamara)
                        </h2>
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                <div className="text-[10px] font-bold text-slate-500 uppercase">إجمالي المبيعات</div>
                                <div className="text-xl font-extrabold text-slate-900 num" data-testid="bnpl-settle-total-gross">
                                    {fmt(data.totals?.gross_sales)}
                                </div>
                                <div className="text-[10px] text-slate-400">ر.س</div>
                            </div>
                            <div className="rounded-xl border border-rose-200 bg-rose-50 p-3">
                                <div className="text-[10px] font-bold text-rose-700 uppercase">المسترجعات</div>
                                <div className="text-xl font-extrabold text-rose-700 num">
                                    {fmt(data.totals?.total_refunds)}
                                </div>
                                <div className="text-[10px] text-rose-400">ر.س</div>
                            </div>
                            <div className="rounded-xl border border-slate-300 bg-white p-3">
                                <div className="text-[10px] font-bold text-slate-500 uppercase">عمولات + ضريبة</div>
                                <div className="text-xl font-extrabold text-slate-900 num">
                                    {fmt((data.totals?.commission || 0) + (data.totals?.commission_vat || 0))}
                                </div>
                                <div className="text-[10px] text-slate-400">ر.س</div>
                            </div>
                            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3">
                                <div className="text-[10px] font-bold text-emerald-700 uppercase">صافي المستحق</div>
                                <div className="text-xl font-extrabold text-emerald-700 num" data-testid="bnpl-settle-total-net">
                                    {fmt(data.totals?.net_payable)}
                                </div>
                                <div className="text-[10px] text-emerald-500">ر.س</div>
                            </div>
                        </div>
                        <div className="mt-3 pt-3 border-t border-slate-200 grid grid-cols-2 gap-3">
                            <div className="text-sm">
                                <span className="text-slate-500">المحوَّل إلى البنوك:</span>{" "}
                                <span className="font-bold num text-slate-900">{fmt(data.totals?.transferred_amount)}</span>{" "}
                                <span className="text-slate-400 text-xs">ر.س</span>
                            </div>
                            <div className="text-sm text-end">
                                <span className="text-slate-500">المتبقي لدى المزودين:</span>{" "}
                                <span className={`font-bold num ${
                                    Math.abs(data.totals?.remaining_with_provider || 0) < 0.5
                                        ? "text-emerald-700" : "text-amber-700"
                                }`}>
                                    {fmt(data.totals?.remaining_with_provider)}
                                </span>{" "}
                                <span className="text-slate-400 text-xs">ر.س</span>
                            </div>
                        </div>
                    </div>

                    {/* Per-provider cards */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        {(data.providers || []).map((p) => (
                            <ProviderCard key={p.provider} data={p} />
                        ))}
                    </div>
                </>
            )}
        </div>
    );
}
