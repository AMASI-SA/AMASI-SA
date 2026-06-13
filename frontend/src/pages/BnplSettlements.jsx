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

function ProviderCard({ data, onShowWeekly }) {
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
                <div className="flex items-center gap-2 flex-wrap">
                    <button
                        type="button"
                        onClick={() => onShowWeekly(data.provider)}
                        className="px-3 py-1 bg-slate-900 text-white text-[11px] font-bold rounded-lg hover:bg-slate-800 flex items-center gap-1"
                        data-testid={`bnpl-settle-show-weekly-${data.provider}`}
                    >
                        📅 عرض الفواتير الأسبوعية
                    </button>
                    <div className="text-xs text-slate-600 bg-white px-2 py-1 rounded-lg border border-slate-200">
                        عمولة: <span className="font-bold num">{fmt(fees.commission_pct)}%</span>
                        {" · "} VAT: <span className="font-bold num">{fmt(fees.vat_pct)}%</span>
                    </div>
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
                <Row
                    label={
                        <span>
                            عمولة المزوّد
                            <span className="text-[10px] text-slate-400 ms-1">
                                ({fmt(fees.commission_pct)}% + {fmt(fees.fixed_fee_per_order || 0)} ر.س لكل طلب × {fmtInt(t.transactions_count)})
                            </span>
                        </span>
                    }
                    value={`(${fmt(t.commission)})`}
                    tone="rose"
                />
                <Row label={`ضريبة العمولة (${fmt(fees.vat_pct)}%)`} value={`(${fmt(t.commission_vat)})`} tone="rose" />
                <Row
                    label={
                        <span>
                            رسوم التسوية{" "}
                            <span className="text-[10px] text-slate-400">
                                ({fmt(t.settlement_fee_per_invoice)} ر.س × {fmtInt(t.settlement_invoices_count)} فاتورة)
                            </span>
                        </span>
                    }
                    value={`(${fmt(t.settlement_fee)})`}
                    tone="rose"
                />
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
    // Weekly settlements table (Phase 4) — one row per weekly invoice
    const [weekly, setWeekly] = useState({});      // { tabby: [...], tamara: [...] }
    const [weeklyOpen, setWeeklyOpen] = useState(null);  // provider currently expanded
    // Iter-120 — per-period item drill-down (sales + refunds)
    const [itemsByInv, setItemsByInv] = useState({});    // { "tabby:3": {sales,refunds,...} }
    const [expandedInv, setExpandedInv] = useState(null); // "tabby:3" | null
    const [itemsLoading, setItemsLoading] = useState(false);

    const loadItems = async (provider, invoice_no, from, to) => {
        const key = `${provider}:${invoice_no}`;
        if (itemsByInv[key]) {
            setExpandedInv((cur) => cur === key ? null : key);
            return;
        }
        setItemsLoading(true);
        try {
            const { data: r } = await api.get(
                `/bnpl/settlements/items/${provider}`,
                { params: { from, to } },
            );
            if (r?.success) {
                setItemsByInv((prev) => ({ ...prev, [key]: r }));
                setExpandedInv(key);
            } else {
                toast.error(`خطأ: ${r?.error || "غير معروف"}`);
            }
        } catch (e) {
            toast.error(errMsg(e, "تعذّر تحميل تفاصيل الفترة"));
        } finally {
            setItemsLoading(false);
        }
    };

    const loadWeekly = async (provider) => {
        try {
            const params = {};
            if (fromDate) params.from = fromDate;
            if (toDate) params.to = toDate;
            const { data: r } = await api.get(`/bnpl/settlements/weekly/${provider}`, { params });
            if (r?.success) {
                // Iter-119 — also pull the auto-match map so each weekly row
                // can show whether it has a matched bank transfer.
                let matchByInv = {};
                let unmatchedTransfers = [];
                let matchTotals = null;
                let toleranceDoc = "";
                try {
                    const { data: m } = await api.get(`/bnpl/settlements/matching/${provider}`, { params });
                    if (m?.success && Array.isArray(m.invoices)) {
                        matchByInv = Object.fromEntries(
                            m.invoices.map((inv) => [inv.invoice_no, inv])
                        );
                        unmatchedTransfers = m.unmatched_transfers || [];
                        matchTotals = m.totals || null;
                        toleranceDoc = m.tolerance_doc || "";
                    }
                } catch (_) { /* matching is best-effort, never blocks weekly */ }
                setWeekly((prev) => ({
                    ...prev,
                    [provider]: { ...r, matchByInv, unmatchedTransfers, matchTotals, toleranceDoc },
                }));
                setWeeklyOpen(provider);
            } else {
                toast.error(`خطأ: ${r?.error || "غير معروف"}`);
            }
        } catch (e) {
            toast.error(errMsg(e, "تعذّر تحميل الفواتير الأسبوعية"));
        }
    };

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
                                آخر حساب: {new Date(data.computed_at).toLocaleString("en-US", { dateStyle: "short", timeStyle: "short" })}
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
                                <div className="text-[10px] font-bold text-slate-500 uppercase">عمولات + ضريبة + رسوم تسوية</div>
                                <div className="text-xl font-extrabold text-slate-900 num">
                                    {fmt(
                                        (data.totals?.commission || 0)
                                        + (data.totals?.commission_vat || 0)
                                        + (data.totals?.settlement_fee || 0),
                                    )}
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
                            <ProviderCard
                                key={p.provider}
                                data={p}
                                onShowWeekly={loadWeekly}
                            />
                        ))}
                    </div>

                    {/* Weekly settlements drill-down */}
                    {weeklyOpen && weekly[weeklyOpen] && (
                        <div
                            className="rounded-2xl border-2 border-slate-300 bg-white p-5"
                            data-testid="bnpl-settle-weekly-table"
                        >
                            <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
                                <h3 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                                    📅 الفواتير الأسبوعية —{" "}
                                    {PROVIDER_META[weeklyOpen]?.icon} {PROVIDER_META[weeklyOpen]?.name}
                                    <span className="text-xs text-slate-400 font-normal num">
                                        ({weekly[weeklyOpen].totals?.invoices_count} فاتورة · {weekly[weeklyOpen].range?.from} → {weekly[weeklyOpen].range?.to})
                                    </span>
                                </h3>
                                <div className="flex items-center gap-2">
                                    <button
                                        type="button"
                                        onClick={() => setWeeklyOpen(weeklyOpen === "tabby" ? "tamara" : "tabby")}
                                        className="px-3 py-1 bg-slate-100 text-slate-700 text-xs font-bold rounded-lg hover:bg-slate-200"
                                    >
                                        تبديل المزوّد
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => setWeeklyOpen(null)}
                                        className="px-3 py-1 bg-slate-200 text-slate-700 text-xs font-bold rounded-lg hover:bg-slate-300"
                                    >
                                        إغلاق ✕
                                    </button>
                                </div>
                            </div>

                            <div className="overflow-x-auto">
                                <table className="mezan-table compact w-full text-xs">
                                    <thead>
                                        <tr>
                                            <th className="p-2">#</th>
                                            <th className="p-2">من</th>
                                            <th className="p-2">إلى</th>
                                            <th className="p-2">تاريخ الإصدار</th>
                                            <th className="p-2">تحويل متوقع</th>
                                            <th className="p-2">العمليات</th>
                                            <th className="p-2">المبيعات</th>
                                            <th className="p-2">المسترجعات</th>
                                            <th className="p-2">صافي المبيعات</th>
                                            <th className="p-2">العمولة</th>
                                            <th className="p-2">ض. العمولة</th>
                                            <th className="p-2">رسوم التسوية</th>
                                            <th className="p-2" title="ضريبة 15% على رسوم التسوية (KSA VAT)">
                                                ض. رسوم التسوية
                                            </th>
                                            <th className="p-2">صافي المستحق</th>
                                            <th className="p-2">المحوَّل</th>
                                            <th className="p-2">المتبقي</th>
                                            <th className="p-2" data-testid="bnpl-weekly-match-header">المطابقة البنكية</th>
                                            <th className="p-2">تفاصيل</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {(weekly[weeklyOpen].rows || []).map((r) => {
                                            const match = weekly[weeklyOpen].matchByInv?.[r.invoice_no];
                                            const status = match?.match_status || "unmatched";
                                            const mt = match?.matched_transfer;
                                            const badge = {
                                                matched:   { txt: "✅ مطابق",   cls: "bg-emerald-100 text-emerald-800" },
                                                over:      { txt: "⚠ زيادة",    cls: "bg-amber-100 text-amber-800" },
                                                under:     { txt: "⚠ نقص",      cls: "bg-rose-100 text-rose-800" },
                                                unmatched: { txt: "—",          cls: "bg-slate-100 text-slate-500" },
                                            }[status] || { txt: status, cls: "bg-slate-100 text-slate-600" };
                                            // Iter-131 — show the SPECIFIC matched transfer (not the
                                            // cumulative 14-day window) in the "المحوَّل" column so
                                            // the merchant sees the exact Tabby/Tamara payout that
                                            // settled THIS invoice.
                                            // Iter-145 — also surface near-miss (over/under)
                                            // transfer amounts so the merchant doesn't see "—"
                                            // when a real transfer landed but fell just outside
                                            // the auto-match tolerance.
                                            const effTransferred = mt
                                                ? Number(mt.amount || 0)
                                                : 0;
                                            const effRemaining = Number(
                                                ((r.net_payable || 0) - effTransferred).toFixed(2),
                                            );
                                            const expKey = `${weeklyOpen}:${r.invoice_no}`;
                                            const isExpanded = expandedInv === expKey;
                                            return (
                                            <tr key={r.invoice_no} data-testid={`bnpl-weekly-row-${r.invoice_no}`}>
                                                <td className="p-2 font-bold text-slate-900">
                                                    {r.invoice_no}
                                                    {/* Iter-147 v3 — badge when row comes from official Tamara file */}
                                                    {r.data_source === "provider_official_file" && (
                                                        <span
                                                            className="ml-1 inline-block px-1 py-0.5 rounded text-[9px] font-semibold bg-blue-100 text-blue-800"
                                                            title="أرقام رسمية مستوردة من ملف تسوية تمارا — مطابقة 100%"
                                                            data-testid={`bnpl-row-official-badge-${r.invoice_no}`}
                                                        >
                                                            رسمي
                                                        </span>
                                                    )}
                                                </td>
                                                <td className="p-2 font-mono text-[10px]">{r.from}</td>
                                                <td className="p-2 font-mono text-[10px]">{r.to}</td>
                                                <td className="p-2 font-mono text-[10px] text-blue-700">
                                                    {r.issue_date || "—"}
                                                </td>
                                                <td className="p-2 font-mono text-[10px] text-emerald-700">
                                                    {r.expected_transfer_date || "—"}
                                                </td>
                                                <td className="p-2 num">{fmtInt(r.transactions_count)}</td>
                                                <td className="p-2 num">{fmt(r.gross_sales)}</td>
                                                <td className="p-2 num text-rose-700">{fmt(r.total_refunds)}</td>
                                                <td className="p-2 num font-bold">{fmt(r.net_sales)}</td>
                                                <td className="p-2 num text-rose-700">{fmt(r.commission)}</td>
                                                <td className="p-2 num text-rose-700/70">{fmt(r.commission_vat)}</td>
                                                <td className="p-2 num text-rose-700/70">{fmt(r.settlement_fee)}</td>
                                                <td className="p-2 num text-rose-700/70" data-testid={`bnpl-weekly-settle-vat-${r.invoice_no}`}>
                                                    {fmt(r.settlement_fee_vat)}
                                                </td>
                                                <td className="p-2 num font-extrabold text-emerald-700">{fmt(r.net_payable)}</td>
                                                <td
                                                    className="p-2 num"
                                                    data-testid={`bnpl-weekly-transferred-${r.invoice_no}`}
                                                    title={status === "matched"
                                                        ? "قيمة التحويل البنكي المطابق لهذه الفاتورة"
                                                        : (mt
                                                            ? "أقرب تحويل بنكي ضمن النافذة — لم يُطابَق تلقائياً بسبب فرق خارج التسامح"
                                                            : "لم يُطابَق تحويل بنكي مع هذه الفاتورة")}
                                                >
                                                    {effTransferred ? fmt(effTransferred) : "—"}
                                                </td>
                                                <td
                                                    className={`p-2 num font-bold ${Math.abs(effRemaining) < 0.5 ? "text-emerald-700" : "text-amber-700"}`}
                                                    data-testid={`bnpl-weekly-remaining-${r.invoice_no}`}
                                                >
                                                    {fmt(effRemaining)}
                                                </td>
                                                <td className="p-2">
                                                    <div className="flex flex-col items-start gap-1">
                                                        <span
                                                            className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${badge.cls}`}
                                                            data-testid={`bnpl-weekly-match-${r.invoice_no}`}
                                                        >
                                                            {badge.txt}
                                                        </span>
                                                        {mt && (
                                                            <div
                                                                className="text-[10px] text-slate-600 leading-tight"
                                                                title={mt.description || ""}
                                                            >
                                                                <span className="num font-bold">{fmt(mt.amount)}</span>{" "}
                                                                <span className="font-mono">{mt.transaction_date}</span>
                                                                {mt.peer_account_name && (
                                                                    <span className="block text-slate-400">→ {mt.peer_account_name}</span>
                                                                )}
                                                                {typeof mt.delta === "number" && Math.abs(mt.delta) > 0.005 && (
                                                                    <span className={`block font-bold ${mt.delta > 0 ? "text-amber-700" : "text-rose-700"}`}>
                                                                        Δ {fmt(mt.delta)}
                                                                    </span>
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                </td>
                                                <td className="p-2">
                                                    <button
                                                        type="button"
                                                        onClick={() => loadItems(weeklyOpen, r.invoice_no, r.from, r.to)}
                                                        className={`px-2 py-1 text-[10px] font-bold rounded-lg ${isExpanded ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
                                                        data-testid={`bnpl-weekly-details-${r.invoice_no}`}
                                                    >
                                                        {isExpanded ? "إخفاء ▲" : "تفاصيل ▼"}
                                                    </button>
                                                </td>
                                            </tr>
                                            );
                                        })}
                                        <tr className="mezan-total-row">
                                            <td className="p-2" colSpan="3">الإجمالي</td>
                                            <td className="p-2 text-[10px] text-slate-400">—</td>
                                            <td className="p-2 text-[10px] text-slate-400">—</td>
                                            <td className="p-2 num">—</td>
                                            <td className="p-2 num">{fmt(weekly[weeklyOpen].totals?.gross_sales)}</td>
                                            <td className="p-2 num text-rose-700">{fmt(weekly[weeklyOpen].totals?.total_refunds)}</td>
                                            <td className="p-2 num">{fmt(weekly[weeklyOpen].totals?.net_sales)}</td>
                                            <td className="p-2 num text-rose-700">{fmt(weekly[weeklyOpen].totals?.commission)}</td>
                                            <td className="p-2 num text-rose-700/70">{fmt(weekly[weeklyOpen].totals?.commission_vat)}</td>
                                            <td className="p-2 num text-rose-700/70">{fmt(weekly[weeklyOpen].totals?.settlement_fee)}</td>
                                            <td className="p-2 num text-rose-700/70" data-testid="bnpl-weekly-settle-vat-total">
                                                {fmt(weekly[weeklyOpen].totals?.settlement_fee_vat)}
                                            </td>
                                            <td className="p-2 num font-extrabold text-emerald-700">{fmt(weekly[weeklyOpen].totals?.net_payable)}</td>
                                            {/* Iter-145 — totals reflect ALL surfaced transfers
                                              (matched + near-miss over/under) so the merchant
                                              sees the actual bank movement, not only auto-matched. */}
                                            <td className="p-2 num" data-testid="bnpl-weekly-transferred-total">
                                                {fmt(
                                                    (weekly[weeklyOpen].rows || []).reduce((s, r) => {
                                                        const m = weekly[weeklyOpen].matchByInv?.[r.invoice_no];
                                                        return s + Number(m?.matched_transfer?.amount || 0);
                                                    }, 0)
                                                )}
                                            </td>
                                            <td className="p-2 num" data-testid="bnpl-weekly-remaining-total">
                                                {fmt(
                                                    Number(
                                                        (
                                                            Number(weekly[weeklyOpen].totals?.net_payable || 0)
                                                            - (weekly[weeklyOpen].rows || []).reduce((s, r) => {
                                                                const m = weekly[weeklyOpen].matchByInv?.[r.invoice_no];
                                                                return s + Number(m?.matched_transfer?.amount || 0);
                                                            }, 0)
                                                        ).toFixed(2)
                                                    )
                                                )}
                                            </td>
                                            <td className="p-2 text-[10px] text-slate-600">
                                                {weekly[weeklyOpen].matchTotals ? (
                                                    <span className="num">
                                                        <span className="text-emerald-700 font-bold">{weekly[weeklyOpen].matchTotals.matched_count}</span>
                                                        {" / "}
                                                        <span className="font-bold">{weekly[weeklyOpen].matchTotals.invoices_count}</span>
                                                        <span className="block">مطابقة</span>
                                                    </span>
                                                ) : "—"}
                                            </td>
                                            <td className="p-2 text-[10px] text-slate-400">—</td>
                                        </tr>
                                    </tbody>
                                </table>
                            </div>

                            <div className="mt-3 text-[11px] text-slate-500 bg-slate-50 border border-slate-200 rounded p-2">
                                📌 كل فاتورة تمثّل أسبوع التسوية القياسي للمزوّد. رسوم التسوية تُخصم مرة واحدة لكل فاتورة.
                                <span className="block mt-1">
                                    🧮 <strong>قاعدة محاسبية (Iter-120):</strong> المبيعات تُحسب بتاريخ الطلب، أما المسترجعات فتُحسب بتاريخ <strong>الاسترجاع الفعلي</strong> — لذا قد يظهر استرجاع لطلب من فترة قديمة داخل تسوية الفترة التي حدث فيها الاسترجاع.
                                </span>
                                {weekly[weeklyOpen].toleranceDoc && (
                                    <span className="block mt-1">🔍 {weekly[weeklyOpen].toleranceDoc}</span>
                                )}
                            </div>

                            {/* Iter-120 — Drill-down: sales + refunds tables for the expanded week */}
                            {expandedInv && expandedInv.startsWith(`${weeklyOpen}:`) && itemsByInv[expandedInv] && (
                                <div
                                    className="mt-4 rounded-xl border-2 border-violet-300 bg-violet-50 p-4 space-y-4"
                                    data-testid="bnpl-period-details"
                                >
                                    <h4 className="text-sm font-extrabold text-violet-900 flex items-center gap-2 flex-wrap">
                                        🔎 تفاصيل فترة الفاتورة #{expandedInv.split(":")[1]}
                                        <span className="text-xs font-mono text-violet-700">
                                            {itemsByInv[expandedInv].period?.from} → {itemsByInv[expandedInv].period?.to}
                                        </span>
                                        {itemsByInv[expandedInv].cross_period_refunds_count > 0 && (
                                            <span className="text-[10px] font-bold bg-amber-200 text-amber-900 px-2 py-0.5 rounded-full">
                                                ⚠ {itemsByInv[expandedInv].cross_period_refunds_count} استرجاع من طلبات فترات سابقة
                                            </span>
                                        )}
                                    </h4>

                                    {/* Sales table */}
                                    <div data-testid="bnpl-period-sales">
                                        <h5 className="text-xs font-extrabold text-slate-800 mb-2 flex items-center gap-2">
                                            🟢 مبيعات الفترة
                                            <span className="text-slate-500 font-normal">
                                                ({itemsByInv[expandedInv].sales?.length || 0} طلب · مجموع
                                                <span className="num font-bold"> {fmt(itemsByInv[expandedInv].sales_total)}</span> ر.س)
                                            </span>
                                        </h5>
                                        {(itemsByInv[expandedInv].sales || []).length === 0 ? (
                                            <div className="text-xs text-slate-500 bg-white border border-slate-200 rounded p-3">
                                                لا توجد مبيعات داخل هذه الفترة.
                                            </div>
                                        ) : (
                                            <div className="overflow-x-auto">
                                                <table className="mezan-table compact w-full text-xs bg-white">
                                                    <thead>
                                                        <tr>
                                                            <th className="p-2">رقم الطلب</th>
                                                            <th className="p-2">تاريخ الطلب</th>
                                                            <th className="p-2">المبلغ</th>
                                                            <th className="p-2">الحالة</th>
                                                            <th className="p-2">طريقة الدفع</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {itemsByInv[expandedInv].sales.map((s) => (
                                                            <tr key={s.id} data-testid={`bnpl-sale-${s.id}`}>
                                                                <td className="p-2 font-mono text-[10px]">{s.order_reference_id || s.order_number || s.id?.slice(0,8)}</td>
                                                                <td className="p-2 font-mono text-[10px]">{(s.order_date || "").slice(0,10)}</td>
                                                                <td className="p-2 num font-bold">{fmt(s.amount)}</td>
                                                                <td className="p-2 text-[10px] text-slate-600">{s.status || "—"}</td>
                                                                <td className="p-2 text-[10px]">{s.payment_method}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        )}
                                    </div>

                                    {/* Refunds table */}
                                    <div data-testid="bnpl-period-refunds">
                                        <h5 className="text-xs font-extrabold text-slate-800 mb-2 flex items-center gap-2">
                                            🔴 مسترجعات الفترة
                                            <span className="text-slate-500 font-normal">
                                                (حسب تاريخ الاسترجاع · {itemsByInv[expandedInv].refunds?.length || 0} استرجاع · مجموع
                                                <span className="num font-bold text-rose-700"> {fmt(itemsByInv[expandedInv].refunds_total)}</span> ر.س)
                                            </span>
                                        </h5>
                                        {(itemsByInv[expandedInv].refunds || []).length === 0 ? (
                                            <div className="text-xs text-slate-500 bg-white border border-slate-200 rounded p-3">
                                                لا توجد مسترجعات داخل هذه الفترة.
                                            </div>
                                        ) : (
                                            <div className="overflow-x-auto">
                                                <table className="mezan-table compact w-full text-xs bg-white">
                                                    <thead>
                                                        <tr>
                                                            <th className="p-2">رقم الطلب</th>
                                                            <th className="p-2">تاريخ الطلب</th>
                                                            <th className="p-2">تاريخ الاسترجاع</th>
                                                            <th className="p-2">المبلغ الأصلي</th>
                                                            <th className="p-2">مبلغ الاسترجاع</th>
                                                            <th className="p-2">طريقة الدفع</th>
                                                            <th className="p-2">سبب</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {itemsByInv[expandedInv].refunds.map((rf) => {
                                                            const odate = (rf.order_date || "").slice(0,10);
                                                            const rdate = (rf.refund_date || "").slice(0,10);
                                                            const fromOlderPeriod = odate && odate < itemsByInv[expandedInv].period?.from;
                                                            return (
                                                            <tr key={rf.id} data-testid={`bnpl-refund-${rf.id}`}>
                                                                <td className="p-2 font-mono text-[10px]">{rf.order_reference_id || rf.order_number || rf.provider_refund_id?.slice(0,8)}</td>
                                                                <td className="p-2 font-mono text-[10px]">
                                                                    {odate || "—"}
                                                                    {fromOlderPeriod && (
                                                                        <span className="block text-[9px] text-amber-700 font-bold">↩ من فترة سابقة</span>
                                                                    )}
                                                                </td>
                                                                <td className="p-2 font-mono text-[10px] font-bold text-slate-900">{rdate || "—"}</td>
                                                                <td className="p-2 num">{rf.original_order_amount != null ? fmt(rf.original_order_amount) : "—"}</td>
                                                                <td className="p-2 num font-bold text-rose-700">{fmt(rf.refund_amount)}</td>
                                                                <td className="p-2 text-[10px]">{rf.payment_method}</td>
                                                                <td className="p-2 text-[10px] text-slate-500 max-w-[12rem] truncate" title={rf.reason || ""}>
                                                                    {rf.reason || "—"}
                                                                </td>
                                                            </tr>
                                                            );
                                                        })}
                                                    </tbody>
                                                </table>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}
                            {itemsLoading && (
                                <div className="mt-3 text-xs text-slate-500 text-center" data-testid="bnpl-items-loading">
                                    جاري تحميل تفاصيل الفترة...
                                </div>
                            )}

                            {/* Iter-119 — Unmatched bank transfers section */}
                            {(weekly[weeklyOpen].unmatchedTransfers || []).length > 0 && (
                                <div
                                    className="mt-4 rounded-xl border-2 border-amber-300 bg-amber-50 p-4"
                                    data-testid="bnpl-unmatched-transfers"
                                >
                                    <h4 className="text-sm font-extrabold text-amber-900 mb-2 flex items-center gap-2">
                                        ⚠ تحويلات بنكية لم تُطابق أي فاتورة
                                        <span className="text-xs font-normal text-amber-700">
                                            ({weekly[weeklyOpen].unmatchedTransfers.length} تحويل ·
                                            <span className="num"> {fmt(weekly[weeklyOpen].matchTotals?.unmatched_transfer_total)}</span> ر.س)
                                        </span>
                                    </h4>
                                    <div className="text-[11px] text-amber-800 mb-2">
                                        هذه تحويلات خرجت من حساب {PROVIDER_META[weeklyOpen]?.name} ولم يتم ربطها بأي فاتورة أسبوعية.
                                        قد تكون خارج النافذة الزمنية أو بمبلغ بعيد عن المتوقع.
                                    </div>
                                    <table className="mezan-table compact w-full text-xs bg-white">
                                        <thead>
                                            <tr>
                                                <th className="p-2">التاريخ</th>
                                                <th className="p-2">المبلغ</th>
                                                <th className="p-2">إلى حساب</th>
                                                <th className="p-2">الوصف</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {weekly[weeklyOpen].unmatchedTransfers.map((t) => (
                                                <tr key={t.id} data-testid={`bnpl-unmatched-transfer-${t.id}`}>
                                                    <td className="p-2 font-mono text-[10px]">{t.transaction_date}</td>
                                                    <td className="p-2 num font-bold">{fmt(t.amount)}</td>
                                                    <td className="p-2">{t.peer_account_name || "—"}</td>
                                                    <td className="p-2 text-[10px] text-slate-600 max-w-md truncate">
                                                        {t.description || "—"}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>
                    )}
                </>
            )}
        </div>
    );
}
