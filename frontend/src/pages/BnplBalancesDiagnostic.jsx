/**
 * BNPL Balances Diagnostic — Single Source of Truth (SSOT) inspector.
 *
 * Shows merchants the canonical balance breakdown for Tabby and
 * Tamara using ONE formula:
 *
 *     balance = gross_sales − refunds − commission − VAT
 *               − settlement_fee − transferred_to_bank
 *
 * Every page that displays a BNPL balance now reads this same
 * formula via `GET /api/bnpl/settlements/balances/canonical`.
 * If a merchant ever sees a different number on another page,
 * that page has a bug.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ArrowsClockwise, CheckCircle, Receipt } from "@phosphor-icons/react";
import api from "../lib/api";

const errMsg = (e, fb) =>
    e?.response?.data?.error || e?.response?.data?.detail || e?.message || fb;

const fmt = (n) =>
    (Number(n) || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

const PROVIDER_META = {
    tabby:  { name: "Tabby",  icon: "🟣", color: "from-violet-50 to-white" },
    tamara: { name: "Tamara", icon: "🩷", color: "from-rose-50 to-white" },
};

function ComponentRow({ label, value, tone, bold }) {
    const cls = tone === "rose" ? "text-rose-700"
              : tone === "emerald" ? "text-emerald-700"
              : tone === "slate" ? "text-slate-900"
              : "text-slate-700";
    return (
        <div className={`flex justify-between items-baseline py-1.5 ${bold ? "border-t-2 border-slate-300 mt-2 pt-2" : "border-b border-slate-100"}`}>
            <span className="text-slate-600 text-xs">{label}</span>
            <span className={`num ${cls} ${bold ? "text-lg font-extrabold" : "text-sm font-semibold"}`}>
                {value}
            </span>
        </div>
    );
}

function ProviderBreakdown({ data }) {
    const meta = PROVIDER_META[data.provider] || { name: data.provider, icon: "💳", color: "from-slate-50 to-white" };
    const c = data.components || {};
    const balance = data.balance || 0;
    const balanceTone = Math.abs(balance) < 0.5 ? "emerald"
                       : balance > 0 ? "emerald" : "rose";

    return (
        <div
            className={`rounded-2xl border border-slate-200 bg-gradient-to-l ${meta.color} p-5`}
            data-testid={`bnpl-balance-card-${data.provider}`}
        >
            <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
                <h3 className="text-xl font-extrabold text-slate-900 flex items-center gap-2">
                    <span className="text-2xl">{meta.icon}</span> {meta.name}
                </h3>
                <div className="text-xs text-slate-500 bg-white border border-slate-200 rounded px-2 py-1">
                    الحساب المرتبط: <strong className="text-slate-900">{data.account_name || "—"}</strong>
                </div>
            </div>

            <div className="bg-white rounded-xl border border-slate-200 p-4">
                <ComponentRow label="إجمالي المبيعات (gross_sales)" value={`+ ${fmt(c.gross_sales)}`} tone="slate" />
                <ComponentRow label="المسترجعات (refunds)" value={`− ${fmt(c.refunds)}`} tone="rose" />
                <ComponentRow label="صافي المبيعات (net_sales)" value={`= ${fmt(c.net_sales)}`} tone="slate" />
                <ComponentRow label="عمولة المزوّد (commission)" value={`− ${fmt(c.commission)}`} tone="rose" />
                <ComponentRow label="ضريبة العمولة (VAT)" value={`− ${fmt(c.commission_vat)}`} tone="rose" />
                <ComponentRow label="رسوم التسوية (settlement_fee)" value={`− ${fmt(c.settlement_fee)}`} tone="rose" />
                <ComponentRow label="المحوَّل إلى البنك (transferred_out)" value={`− ${fmt(c.transferred_out)}`} tone="rose" />
                <ComponentRow label="الرصيد المتبقي لدى المزوّد" value={`${fmt(balance)} ر.س`} tone={balanceTone} bold />
            </div>

            <div className="mt-3 text-[11px] text-slate-500 bg-slate-50 border border-slate-200 rounded p-2 leading-relaxed">
                <strong className="text-slate-700">📊 هذا الرصيد = SSOT</strong>{" "}
                — نفس الرقم يجب أن يظهر في صفحة «الأصول والحسابات» و «التحويلات بين الحسابات»
                و «المطابقة والتسويات» و «تسويات Tabby و Tamara».
            </div>
        </div>
    );
}

export default function BnplBalancesDiagnostic() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);

    const load = async () => {
        setRefreshing(true);
        try {
            const { data: r } = await api.get("/bnpl/settlements/balances/canonical");
            if (r?.success === false) {
                toast.error(`خطأ: ${r.error || "غير معروف"}`);
            } else {
                setData(r);
            }
        } catch (e) {
            toast.error(errMsg(e, "تعذّر التحميل"));
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    };

    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                const { data: r } = await api.get("/bnpl/settlements/balances/canonical");
                if (alive && r?.success !== false) setData(r);
            } catch (e) {
                if (alive) toast.error(errMsg(e, "تعذّر التحميل"));
            } finally {
                if (alive) setLoading(false);
            }
        })();
        return () => { alive = false; };
    }, []);

    return (
        <div className="space-y-5" dir="rtl" data-testid="bnpl-balances-diagnostic">
            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl font-extrabold text-slate-900">
                        📐 أرصدة Tabby و Tamara — المصدر الموحَّد
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        هذه الشاشة هي المرجع الوحيد لأرصدة BNPL في النظام. كل الصفحات
                        الأخرى تستخدم نفس المعادلة المعروضة هنا.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={load}
                    disabled={refreshing}
                    className="px-4 py-2 bg-slate-900 text-white text-sm font-bold rounded-lg hover:bg-slate-800 disabled:opacity-50 flex items-center gap-2"
                    data-testid="bnpl-balance-refresh"
                >
                    <ArrowsClockwise size={16} className={refreshing ? "animate-spin" : ""} />
                    {refreshing ? "جاري الحساب…" : "تحديث"}
                </button>
            </div>

            {loading && <div className="text-center text-slate-500 py-12">جاري الحساب…</div>}

            {data && (
                <>
                    {/* SSOT confirmation banner */}
                    <div
                        className="rounded-2xl border-2 border-emerald-300 bg-emerald-50 p-4 flex items-start gap-3"
                        data-testid="bnpl-balance-ssot-banner"
                    >
                        <CheckCircle size={28} weight="fill" className="text-emerald-700 flex-shrink-0 mt-1" />
                        <div>
                            <div className="font-extrabold text-slate-900">
                                ✓ مصدر موحَّد للحساب — Single Source of Truth
                            </div>
                            <div className="text-xs text-slate-700 mt-1 font-mono leading-relaxed">
                                {data.formula_doc}
                            </div>
                            <div className="text-xs text-slate-500 mt-2">
                                الإجمالي الكلي (Tabby + Tamara) = <strong className="num text-slate-900">{fmt(data.total)}</strong> ر.س
                            </div>
                        </div>
                    </div>

                    {/* Per-provider breakdown */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        {(data.balances || []).map((b) => (
                            <ProviderBreakdown key={b.provider} data={b} />
                        ))}
                    </div>

                    {/* Pages that use this SSOT */}
                    <div className="rounded-2xl border border-slate-200 bg-white p-5">
                        <h3 className="text-base font-extrabold text-slate-900 mb-3 flex items-center gap-2">
                            <Receipt size={18} weight="duotone" className="text-slate-600" />
                            الصفحات التي تستخدم هذا الرصيد
                        </h3>
                        <ul className="text-sm text-slate-700 space-y-1.5">
                            {[
                                ["/accounts", "الأصول والحسابات"],
                                ["/transfers", "التحويلات بين الحسابات"],
                                ["/reconciliation", "المطابقة والتسويات"],
                                ["/bnpl-settlements", "تسويات Tabby و Tamara"],
                                ["/reports", "التقارير المالية"],
                                ["/dashboard", "لوحة التحكم"],
                            ].map(([path, label]) => (
                                <li key={path} className="flex items-center gap-2">
                                    <CheckCircle size={14} weight="fill" className="text-emerald-600" />
                                    <a href={path} className="text-slate-700 hover:text-slate-900 underline">
                                        {label}
                                    </a>
                                    <code className="text-[10px] text-slate-400">{path}</code>
                                </li>
                            ))}
                        </ul>
                        <div className="mt-3 text-[11px] text-slate-500 bg-slate-50 border border-slate-200 rounded p-2">
                            لو رأيت رقماً مختلفاً في أي صفحة، يرجى الإبلاغ — هذا يعني أن
                            الصفحة لا تستخدم الـ SSOT.
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}
