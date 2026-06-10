/**
 * Refund Audit (Iter-117) — Unified Tabby + Tamara comparison.
 *
 * Shows merchants whether refunds (full + partial) from each BNPL
 * provider are fully reflected in:
 *   1. payment_transactions    (provider truth via API)
 *   2. payment_refunds         (our reconstructed per-refund rows)
 *   3. unified_orders          (the single source of truth used by
 *                               Dashboard / Reports / Profits /
 *                               Settlements pages)
 *
 * Delta should be 0 across the board.  If not, the page tells the
 * merchant exactly which delta is off and how to fix it.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
    ArrowLeft, ArrowsClockwise, CheckCircle, WarningCircle,
    Receipt, ChartBar,
} from "@phosphor-icons/react";
import api from "../lib/api";

const errMsg = (e, fb) =>
    e?.response?.data?.error
    || e?.response?.data?.detail
    || e?.message
    || fb;

const formatMoney = (n) =>
    (Number(n) || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

const formatInt = (n) =>
    (Number(n) || 0).toLocaleString("en-US");

const PROVIDER_LABEL = {
    tabby: { name: "Tabby", color: "from-violet-50 to-white", icon: "🟣" },
    tamara: { name: "Tamara", color: "from-rose-50 to-white", icon: "🩷" },
};

const VERDICT_TONE = {
    ok: { cls: "bg-emerald-100 text-emerald-900 border-emerald-300", icon: CheckCircle },
    missing_records: { cls: "bg-amber-100 text-amber-900 border-amber-300", icon: WarningCircle },
    dashboard_drift: { cls: "bg-rose-100 text-rose-900 border-rose-300", icon: WarningCircle },
    amount_mismatch: { cls: "bg-orange-100 text-orange-900 border-orange-300", icon: WarningCircle },
};

function StatTile({ label, value, hint, tone = "slate", testid }) {
    const toneCls = {
        slate: "bg-slate-50 border-slate-200 text-slate-900",
        emerald: "bg-emerald-50 border-emerald-200 text-emerald-900",
        rose: "bg-rose-50 border-rose-200 text-rose-900",
        amber: "bg-amber-50 border-amber-200 text-amber-900",
        sky: "bg-sky-50 border-sky-200 text-sky-900",
    }[tone] || "bg-slate-50 border-slate-200 text-slate-900";
    return (
        <div className={`rounded-xl border p-3 ${toneCls}`} data-testid={testid}>
            <div className="text-[10px] font-bold uppercase opacity-70 mb-1">{label}</div>
            <div className="text-lg font-extrabold num leading-tight">{value}</div>
            {hint && <div className="text-[10px] opacity-60 mt-1">{hint}</div>}
        </div>
    );
}

function ProviderCard({ data, onDiagnose, diagnosing }) {
    const meta = PROVIDER_LABEL[data.provider] || { name: data.provider, color: "from-slate-50 to-white", icon: "💳" };
    const verdict = VERDICT_TONE[data.verdict] || VERDICT_TONE.ok;
    const VerdictIcon = verdict.icon;
    const txn = data.transactions || {};
    const refunds = data.refund_records || {};
    const unified = data.unified_orders || {};
    const deltas = data.deltas || {};
    const hasDelta = deltas.records_vs_status !== 0
        || Math.abs(deltas.amount_ptx_vs_refunds) >= 0.01
        || Math.abs(deltas.amount_unified_vs_ptx) >= 0.01;

    return (
        <div
            className={`rounded-2xl border border-slate-200 bg-gradient-to-l ${meta.color} p-5`}
            data-testid={`refund-audit-card-${data.provider}`}
        >
            <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
                <h3 className="text-xl font-extrabold text-slate-900 flex items-center gap-2">
                    <span className="text-2xl">{meta.icon}</span> {meta.name}
                </h3>
                <span className={`px-3 py-1 rounded-full border text-xs font-bold flex items-center gap-1 ${verdict.cls}`} data-testid={`refund-audit-verdict-${data.provider}`}>
                    <VerdictIcon size={14} weight="fill" />
                    {data.verdict === "ok" ? "Delta = 0" : "يحتاج معالجة"}
                </span>
            </div>

            <p className="text-sm text-slate-700 bg-white/70 border border-slate-200 rounded-lg px-3 py-2 mb-4">
                {data.message}
            </p>

            {/* Transactions side */}
            <div className="mb-3">
                <div className="text-xs font-bold text-slate-500 mb-2 flex items-center gap-1">
                    <Receipt size={14} weight="duotone" /> payment_transactions (مصدر API)
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <StatTile label="استرجاع كامل" value={formatInt(txn.full_refund)} tone="rose" testid={`refund-audit-${data.provider}-full`} />
                    <StatTile label="استرجاع جزئي" value={formatInt(txn.partial_refund)} tone="amber" testid={`refund-audit-${data.provider}-partial`} />
                    <StatTile label="بدون استرجاع" value={formatInt(txn.no_refund)} tone="slate" testid={`refund-audit-${data.provider}-none`} />
                    <StatTile label="إجمالي المسترجع (ر.س)" value={formatMoney(txn.refunded_amount_sum)} tone="emerald" testid={`refund-audit-${data.provider}-ptx-amount`} />
                </div>
            </div>

            {/* Refund records side */}
            <div className="mb-3">
                <div className="text-xs font-bold text-slate-500 mb-2 flex items-center gap-1">
                    <Receipt size={14} weight="duotone" /> payment_refunds (سجلات قاعدة البيانات)
                </div>
                <div className="grid grid-cols-2 gap-2">
                    <StatTile label="عدد السجلات" value={formatInt(refunds.count)} tone="slate" testid={`refund-audit-${data.provider}-records`} />
                    <StatTile label="مبلغ السجلات (ر.س)" value={formatMoney(refunds.amount_sum)} tone="emerald" testid={`refund-audit-${data.provider}-records-amount`} />
                </div>
            </div>

            {/* Unified orders side */}
            <div className="mb-3">
                <div className="text-xs font-bold text-slate-500 mb-2 flex items-center gap-1">
                    <ChartBar size={14} weight="duotone" /> unified_orders (التقارير والأرباح)
                </div>
                <div className="grid grid-cols-2 gap-2">
                    <StatTile label="طلبات تحوي مسترجع" value={formatInt(unified.rows_with_refund)} tone="slate" testid={`refund-audit-${data.provider}-unified-rows`} />
                    <StatTile label="مبلغ مخصوم من الأرباح" value={formatMoney(unified.refund_amount_sum)} tone="emerald" testid={`refund-audit-${data.provider}-unified-amount`} />
                </div>
            </div>

            {/* Deltas */}
            <div className="mt-4 p-3 bg-white border-2 border-dashed border-slate-300 rounded-lg">
                <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
                    <div className="text-xs font-bold text-slate-700">⚖ المقارنة (Delta = 0 يعني تطابق تام)</div>
                    {hasDelta && (
                        <button
                            type="button"
                            onClick={() => onDiagnose(data.provider)}
                            disabled={diagnosing === data.provider}
                            className="px-3 py-1 bg-rose-600 text-white text-[11px] font-bold rounded-lg hover:bg-rose-700 disabled:opacity-50 flex items-center gap-1"
                            data-testid={`refund-audit-diagnose-${data.provider}`}
                        >
                            {diagnosing === data.provider ? "جاري التشخيص…" : "🔍 تشخيص الفرق"}
                        </button>
                    )}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
                    <div className="flex justify-between items-center bg-slate-50 px-2 py-1.5 rounded">
                        <span className="text-slate-600">سجلات vs حالة:</span>
                        <span className={`font-bold num ${deltas.records_vs_status === 0 ? "text-emerald-700" : "text-rose-700"}`} data-testid={`refund-audit-${data.provider}-delta-records`}>
                            {deltas.records_vs_status}
                        </span>
                    </div>
                    <div className="flex justify-between items-center bg-slate-50 px-2 py-1.5 rounded">
                        <span className="text-slate-600">مبلغ ptx vs refunds:</span>
                        <span className={`font-bold num ${Math.abs(deltas.amount_ptx_vs_refunds) < 0.01 ? "text-emerald-700" : "text-rose-700"}`} data-testid={`refund-audit-${data.provider}-delta-ptx`}>
                            {formatMoney(deltas.amount_ptx_vs_refunds)}
                        </span>
                    </div>
                    <div className="flex justify-between items-center bg-slate-50 px-2 py-1.5 rounded">
                        <span className="text-slate-600">unified vs ptx:</span>
                        <span className={`font-bold num ${Math.abs(deltas.amount_unified_vs_ptx) < 0.01 ? "text-emerald-700" : "text-rose-700"}`} data-testid={`refund-audit-${data.provider}-delta-unified`}>
                            {formatMoney(deltas.amount_unified_vs_ptx)}
                        </span>
                    </div>
                </div>
            </div>
        </div>
    );
}

// Classification labels (Arabic) for the Diagnose Delta tool
const CLASS_LABELS = {
    expected_full: { label: "كامل ✓", cls: "bg-emerald-100 text-emerald-800" },
    expected_partial: { label: "جزئي ✓", cls: "bg-sky-100 text-sky-800" },
    multiple_partials: { label: "تجزئة متعددة", cls: "bg-amber-100 text-amber-800" },
    orphan_no_ptx: { label: "بدون معاملة!", cls: "bg-rose-200 text-rose-900" },
    orphan_zero_ptx: { label: "ptx=0 لكن السجل موجود", cls: "bg-rose-100 text-rose-800" },
    duplicate: { label: "مكرر!", cls: "bg-fuchsia-200 text-fuchsia-900" },
};

export default function RefundAudit() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [fixing, setFixing] = useState(false);
    const [diagnosing, setDiagnosing] = useState(null);   // provider being loaded
    const [diagnosis, setDiagnosis] = useState(null);     // { provider, rows, counts }

    const load = async () => {
        try {
            const { data } = await api.get("/bnpl/refund-audit");
            if (data?.success === false) {
                toast.error(`خطأ: ${data.error || "غير معروف"}`);
            } else {
                setData(data);
            }
        } catch (e) {
            toast.error(errMsg(e, "تعذّر تحميل التدقيق"));
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    };

    const fixUnifiedRefunds = async () => {
        if (!window.confirm(
            "سيتم نسخ مبالغ المسترجعات من payment_transactions إلى " +
            "unified_orders لكلا المزودين. هذه العملية آمنة و idempotent. " +
            "متابعة؟",
        )) return;
        setFixing(true);
        try {
            const { data: r } = await api.post(
                "/bnpl/auto-sync/fix-unified-refunds",
                null, { timeout: 90000 },
            );
            if (r?.success) {
                toast.success(
                    `✅ تم تحديث ${r.total} طلب · Tabby: ${r.tabby_orders_updated} · Tamara: ${r.tamara_orders_updated}`,
                    { duration: 7000 },
                );
                await load();
            } else {
                toast.error(`فشل: ${r?.error || "غير معروف"}`);
            }
        } catch (e) {
            toast.error(errMsg(e, "تعذّرت العملية"));
        } finally {
            setFixing(false);
        }
    };

    const diagnoseProvider = async (provider) => {
        setDiagnosing(provider);
        setDiagnosis(null);
        try {
            const { data: r } = await api.get(`/bnpl/refund-audit/diagnose/${provider}`);
            if (r?.success) {
                setDiagnosis(r);
            } else {
                toast.error(`فشل التشخيص: ${r?.error || "غير معروف"}`);
            }
        } catch (e) {
            toast.error(errMsg(e, "تعذّر التشخيص"));
        } finally {
            setDiagnosing(null);
        }
    };

    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                const { data: d } = await api.get("/bnpl/refund-audit");
                if (!alive) return;
                if (d?.success === false) {
                    toast.error(`خطأ: ${d.error || "غير معروف"}`);
                } else {
                    setData(d);
                }
            } catch (e) {
                if (alive) toast.error(errMsg(e, "تعذّر تحميل التدقيق"));
            } finally {
                if (alive) setLoading(false);
            }
        })();
        return () => { alive = false; };
    }, []);

    const handleRefresh = () => { setRefreshing(true); load(); };

    return (
        <div className="space-y-6" dir="rtl" data-testid="refund-audit-page">
            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl font-extrabold text-slate-900">
                        📋 تدقيق المسترجعات الموحَّد
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        مقارنة شاملة بين Tabby و Tamara: حالة الطلبات · سجلات DB ·
                        ومبالغ المسترجعات المنعكسة في الأرباح والتقارير. Delta = 0 ⇒ تطابق تام.
                    </p>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                    <Link
                        to="/integrations/bnpl/diagnostics"
                        className="text-xs text-slate-600 hover:text-slate-900 flex items-center gap-1"
                        data-testid="refund-audit-back"
                    >
                        <ArrowLeft size={14} /> صفحة التشخيص
                    </Link>
                    {data && !data.all_ok && (
                        <button
                            type="button"
                            onClick={fixUnifiedRefunds}
                            disabled={fixing}
                            className="px-4 py-2 bg-amber-600 text-white text-sm font-bold rounded-lg hover:bg-amber-700 disabled:opacity-50 flex items-center gap-2"
                            data-testid="refund-audit-fix-unified"
                            title="نسخ مبالغ المسترجعات من ptx إلى unified_orders"
                        >
                            <ArrowsClockwise size={16} className={fixing ? "animate-spin" : ""} />
                            {fixing ? "جاري الإصلاح…" : "إصلاح unified_orders"}
                        </button>
                    )}
                    <button
                        type="button"
                        onClick={handleRefresh}
                        disabled={refreshing}
                        className="px-4 py-2 bg-slate-900 text-white text-sm font-bold rounded-lg hover:bg-slate-800 disabled:opacity-50 flex items-center gap-2"
                        data-testid="refund-audit-refresh"
                    >
                        <ArrowsClockwise size={16} className={refreshing ? "animate-spin" : ""} />
                        {refreshing ? "جاري التحديث…" : "تدقيق الآن"}
                    </button>
                </div>
            </div>

            {/* Loading */}
            {loading && (
                <div className="text-center text-slate-500 py-12">جاري حساب التدقيق…</div>
            )}

            {/* Global verdict */}
            {data && (
                <div
                    className={
                        "rounded-2xl border-2 p-5 flex items-start gap-3 " +
                        (data.all_ok
                            ? "bg-emerald-50 border-emerald-300"
                            : "bg-amber-50 border-amber-300")
                    }
                    data-testid="refund-audit-global"
                >
                    {data.all_ok
                        ? <CheckCircle size={32} weight="fill" className="text-emerald-700 flex-shrink-0" />
                        : <WarningCircle size={32} weight="fill" className="text-amber-700 flex-shrink-0" />}
                    <div>
                        <div className="text-lg font-extrabold text-slate-900">
                            {data.all_ok ? "✓ كل المسترجعات متطابقة" : "⚠ توجد فروقات"}
                        </div>
                        <div className="text-sm text-slate-700 mt-1">{data.global_verdict}</div>
                    </div>
                </div>
            )}

            {/* Totals (combined) */}
            {data?.totals && (
                <div className="rounded-2xl border border-slate-200 bg-white p-5">
                    <h2 className="text-lg font-extrabold text-slate-900 mb-3">
                        📊 الإجمالي الموحَّد (Tabby + Tamara)
                    </h2>
                    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
                        <StatTile label="استرجاع كامل" value={formatInt(data.totals.full_refund)} tone="rose" testid="refund-audit-total-full" />
                        <StatTile label="استرجاع جزئي" value={formatInt(data.totals.partial_refund)} tone="amber" testid="refund-audit-total-partial" />
                        <StatTile label="عدد سجلات DB" value={formatInt(data.totals.refund_records)} tone="slate" testid="refund-audit-total-records" />
                        <StatTile label="مبلغ سجلات DB" value={formatMoney(data.totals.refund_amount_sum)} hint="ر.س" tone="emerald" testid="refund-audit-total-records-amount" />
                        <StatTile label="مبلغ ptx (API)" value={formatMoney(data.totals.refunded_amount_in_ptx)} hint="ر.س" tone="sky" testid="refund-audit-total-ptx" />
                        <StatTile label="مخصوم من الأرباح" value={formatMoney(data.totals.unified_refund_amount_sum)} hint="ر.س — unified" tone="emerald" testid="refund-audit-total-unified" />
                    </div>
                </div>
            )}

            {/* Per-provider cards */}
            {data?.providers && (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {data.providers.map((p) => (
                        <ProviderCard
                            key={p.provider}
                            data={p}
                            onDiagnose={diagnoseProvider}
                            diagnosing={diagnosing}
                        />
                    ))}
                </div>
            )}

            {/* Diagnose Delta — drill-down table */}
            {diagnosis && (
                <div
                    className="rounded-2xl border-2 border-rose-300 bg-rose-50 p-5"
                    data-testid="refund-audit-diagnosis"
                >
                    <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                        <h3 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                            🔍 تشخيص فروقات {PROVIDER_LABEL[diagnosis.provider]?.name || diagnosis.provider}
                        </h3>
                        <button
                            type="button"
                            onClick={() => setDiagnosis(null)}
                            className="px-3 py-1 bg-slate-200 text-slate-700 text-xs font-bold rounded-lg hover:bg-slate-300"
                            data-testid="refund-audit-diagnosis-close"
                        >
                            إغلاق ✕
                        </button>
                    </div>

                    {/* Counts summary */}
                    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 mb-4">
                        {Object.entries(diagnosis.counts || {}).map(([k, v]) => {
                            const meta = CLASS_LABELS[k] || { label: k, cls: "bg-slate-100 text-slate-700" };
                            return (
                                <div key={k} className="rounded-lg border border-slate-200 bg-white p-2 text-center">
                                    <div className="text-2xl font-extrabold text-slate-900 num">{v}</div>
                                    <div className={`text-[10px] font-bold inline-block px-1.5 py-0.5 rounded mt-1 ${meta.cls}`}>{meta.label}</div>
                                </div>
                            );
                        })}
                    </div>

                    {/* Drill-down table */}
                    <div className="bg-white border border-slate-200 rounded-lg overflow-x-auto">
                        <table className="mezan-table compact w-full text-xs">
                            <thead>
                                <tr>
                                    <th className="p-2">الحالة</th>
                                    <th className="p-2">Order Ref</th>
                                    <th className="p-2">Payment ID</th>
                                    <th className="p-2">Refund ID</th>
                                    <th className="p-2">Refund Amount</th>
                                    <th className="p-2">ptx.refunded</th>
                                    <th className="p-2">ptx.amount</th>
                                    <th className="p-2">ptx.status</th>
                                    <th className="p-2">عدد على نفس الدفعة</th>
                                    <th className="p-2">تاريخ الاسترجاع</th>
                                </tr>
                            </thead>
                            <tbody>
                                {(diagnosis.rows || []).map((r, i) => {
                                    const meta = CLASS_LABELS[r.classification] || { label: r.classification, cls: "bg-slate-100 text-slate-700" };
                                    return (
                                        <tr key={r.refund_id + i} data-testid={`refund-audit-diagnosis-row-${i}`}>
                                            <td className="p-2">
                                                <span className={`px-2 py-0.5 rounded text-[10px] font-bold whitespace-nowrap ${meta.cls}`}>
                                                    {meta.label}
                                                </span>
                                            </td>
                                            <td className="p-2 font-mono text-[10px]">{r.order_reference_id || "—"}</td>
                                            <td className="p-2 font-mono text-[10px]">{(r.payment_id || "—").slice(0, 14)}…</td>
                                            <td className="p-2 font-mono text-[10px]">{(r.refund_id || "—").slice(0, 14)}…</td>
                                            <td className="p-2 num font-bold text-rose-700">{formatMoney(r.refund_amount)}</td>
                                            <td className="p-2 num">{r.transaction_refunded_amount == null ? "—" : formatMoney(r.transaction_refunded_amount)}</td>
                                            <td className="p-2 num">{r.transaction_amount == null ? "—" : formatMoney(r.transaction_amount)}</td>
                                            <td className="p-2 text-slate-600">{r.transaction_status || "—"}</td>
                                            <td className="p-2 num text-center">{r.refunds_on_same_payment}</td>
                                            <td className="p-2 text-[10px] text-slate-500">{r.refunded_at?.slice(0, 16) || "—"}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    <div className="mt-3 text-xs text-slate-700 bg-white border border-slate-200 rounded-lg p-3">
                        <strong className="text-slate-900">📖 شرح التصنيفات:</strong>
                        <ul className="mt-1 space-y-0.5 list-disc list-inside">
                            <li><strong>كامل ✓ / جزئي ✓</strong> — سجل سليم يطابق payment_transactions.</li>
                            <li><strong>تجزئة متعددة</strong> — أكثر من refund record على نفس الدفعة (سبب طبيعي لـ records &gt; status).</li>
                            <li><strong>ptx=0 لكن السجل موجود</strong> — السجل في DB لكن المعاملة تشير إلى 0 → غالباً ناتج من webhook قديم لم يتم تحديث ptx بعده.</li>
                            <li><strong>بدون معاملة!</strong> — refund موجود بدون أي payment_transaction أب → فاسد.</li>
                            <li><strong>مكرر!</strong> — نفس <code>provider_refund_id</code> ظاهر مرتين → بحاجة لحذف يدوي.</li>
                        </ul>
                    </div>
                </div>
            )}
        </div>
    );
}
