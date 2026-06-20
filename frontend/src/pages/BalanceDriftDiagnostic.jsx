/**
 * Iter-250b P1.5 — Balance Drift Diagnostic (READ-ONLY).
 *
 * Surfaces GET /api/diagnostics/balance-drift for every
 * bank / cash / payment_platform account, side-by-side:
 *
 *   • stored_current_balance         (accounts.current_balance)
 *   • ledger_main_net                (sub=main)
 *   • ledger_balance_net             (sub=balance — BNPL bridge)
 *   • ssot_value                     (account_balance_ssot)
 *   • account_transactions_walk      (legacy)
 *   • displayed_balance              (UI headline)
 *
 * Highlights three problems:
 *   1. drift_ssot_vs_stored   ≠ 0 → SSOT vs cache mismatch
 *   2. drift_ssot_vs_walk     ≠ 0 → ledger vs account_transactions mismatch
 *   3. ITER249_BNPL_HIDDEN flag    → BNPL settlements hidden from feed
 *
 * STRICT READ-ONLY: no buttons that mutate, no admin actions.
 */
import { useEffect, useState, useMemo } from "react";
import {
    Stethoscope, Warning, CheckCircle, XCircle, Bank,
    CopySimple, DownloadSimple, Spinner, ArrowsLeftRight,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const errMsg = (e, fb) =>
    formatApiErrorDetail(e?.response?.data?.detail) || fb || "حدث خطأ";

const fmt = (n) => {
    if (n === null || n === undefined) return "—";
    return Number(n).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
};

const TYPE_LABEL = {
    bank: "بنك", cash: "صندوق نقدي", payment_platform: "منصة دفع",
};

const STATUS_BADGE = {
    ok: { cls: "bg-emerald-100 text-emerald-800 border-emerald-200",
          label: "✅ متطابق", icon: CheckCircle },
    drift: { cls: "bg-amber-100 text-amber-800 border-amber-200",
             label: "⚠️ انحراف", icon: Warning },
    ITER249_BNPL_HIDDEN: {
        cls: "bg-rose-100 text-rose-800 border-rose-200",
        label: "🔴 BNPL مخفي (Iter-249)", icon: XCircle,
    },
};

function copyJson(obj) {
    try {
        navigator.clipboard.writeText(JSON.stringify(obj, null, 2));
        toast.success("تم نسخ JSON");
    } catch {
        toast.error("فشل النسخ");
    }
}

function downloadJson(obj, filename) {
    const blob = new Blob([JSON.stringify(obj, null, 2)],
        { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

function SummaryCard({ label, value, color, testid }) {
    return (
        <div
            className={`rounded-xl border p-4 ${color}`}
            data-testid={testid}>
            <div className="text-xs font-bold mb-1 opacity-70">
                {label}
            </div>
            <div className="num text-2xl font-extrabold">
                {typeof value === "number" ? value.toLocaleString("en-US") : value}
            </div>
        </div>
    );
}

function DriftCell({ value, tolerance }) {
    if (value === null || value === undefined) {
        return <span className="text-slate-400">—</span>;
    }
    const abs = Math.abs(value);
    let cls = "text-emerald-700";
    if (abs > tolerance) cls = "text-rose-700 font-bold";
    else if (abs > 0.001) cls = "text-amber-700";
    return (
        <span className={`num ${cls}`}>
            {value > 0 ? "+" : ""}{fmt(value)}
        </span>
    );
}

export default function BalanceDriftDiagnostic() {
    const [loading, setLoading] = useState(true);
    const [data, setData] = useState(null);
    const [includeZero, setIncludeZero] = useState(false);
    const [accountType, setAccountType] = useState("all");
    const [tolerance, setTolerance] = useState(0.02);
    const [expandedRow, setExpandedRow] = useState(null);

    const load = async () => {
        setLoading(true);
        try {
            const params = new URLSearchParams({
                account_type: accountType,
                include_zero_drift: includeZero ? "true" : "false",
                tolerance: String(tolerance),
            });
            const { data } = await api.get(
                `/diagnostics/balance-drift?${params.toString()}`);
            setData(data);
        } catch (e) {
            toast.error(errMsg(e, "فشل تحميل التشخيص"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, [
        accountType, includeZero, tolerance,
    ]);

    const rows = data?.accounts || [];
    const summary = data?.summary;

    const totalHidden = useMemo(() =>
        rows.reduce((s, r) => s + (r.feed_hidden_net_amount || 0), 0),
        [rows]);

    return (
        <div
            className="space-y-6"
            dir="rtl"
            data-testid="balance-drift-page">
            {/* Header */}
            <div className="rounded-2xl bg-gradient-to-br from-slate-900 to-slate-700 text-white p-6 shadow-lg">
                <div className="flex items-start justify-between gap-4 flex-wrap">
                    <div className="flex items-center gap-3">
                        <div className="w-14 h-14 rounded-xl bg-white/10 flex items-center justify-center">
                            <Stethoscope size={32} weight="duotone" />
                        </div>
                        <div>
                            <h1
                                className="text-2xl sm:text-3xl font-extrabold"
                                style={{ fontFamily: "Tajawal" }}
                                data-testid="page-title">
                                تشخيص انحراف الأرصدة (Iter-250b · P1.5)
                            </h1>
                            <p className="text-sm text-slate-200 mt-1">
                                مقارنة Read-Only بين Ledger SSOT vs Stored vs Displayed
                                لكل حساب بنك/كاش/منصة دفع
                            </p>
                            <div className="mt-2 inline-flex items-center gap-2 px-2.5 py-1 rounded-full bg-emerald-500/20 text-emerald-100 text-xs font-bold border border-emerald-400/30">
                                <CheckCircle size={14} weight="bold" />
                                100% Read-Only · لا تعديل في DB
                            </div>
                        </div>
                    </div>
                    <div className="flex gap-2">
                        <button
                            onClick={() => data && copyJson(data)}
                            disabled={!data}
                            data-testid="copy-json-btn"
                            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-xs font-bold disabled:opacity-40">
                            <CopySimple size={14} /> Copy JSON
                        </button>
                        <button
                            onClick={() => data && downloadJson(
                                data,
                                `balance-drift-${Date.now()}.json`)}
                            disabled={!data}
                            data-testid="download-json-btn"
                            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-xs font-bold disabled:opacity-40">
                            <DownloadSimple size={14} /> Download
                        </button>
                    </div>
                </div>
            </div>

            {/* Filters */}
            <div className="bg-white rounded-xl border border-slate-200 p-4 flex flex-wrap items-center gap-4">
                <div className="flex items-center gap-2">
                    <label className="text-sm font-bold text-slate-700">نوع الحساب:</label>
                    <select
                        value={accountType}
                        onChange={(e) => setAccountType(e.target.value)}
                        data-testid="account-type-filter"
                        className="px-3 py-1.5 text-sm border border-slate-300 rounded-lg">
                        <option value="all">الكل</option>
                        <option value="bank">بنك</option>
                        <option value="cash">صندوق نقدي</option>
                        <option value="payment_platform">منصة دفع</option>
                    </select>
                </div>
                <div className="flex items-center gap-2">
                    <label className="text-sm font-bold text-slate-700">tolerance (ر.س):</label>
                    <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={tolerance}
                        onChange={(e) => setTolerance(parseFloat(e.target.value) || 0)}
                        data-testid="tolerance-input"
                        className="w-20 px-2 py-1.5 text-sm border border-slate-300 rounded-lg num" />
                </div>
                <label className="flex items-center gap-2 text-sm font-bold text-slate-700 cursor-pointer">
                    <input
                        type="checkbox"
                        checked={includeZero}
                        onChange={(e) => setIncludeZero(e.target.checked)}
                        data-testid="include-zero-toggle"
                        className="w-4 h-4" />
                    عرض الحسابات السليمة (zero-drift) أيضاً
                </label>
                <button
                    onClick={load}
                    disabled={loading}
                    data-testid="reload-btn"
                    className="mr-auto inline-flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-slate-900 text-white text-sm font-bold hover:bg-slate-700 disabled:opacity-40">
                    {loading ? <Spinner size={14} className="animate-spin" /> : <ArrowsLeftRight size={14} />}
                    إعادة الفحص
                </button>
            </div>

            {/* Summary cards */}
            {summary && (
                <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-3">
                    <SummaryCard
                        label="إجمالي الحسابات"
                        value={summary.total_accounts}
                        color="bg-slate-50 border-slate-200 text-slate-800"
                        testid="sum-total" />
                    <SummaryCard
                        label="✅ سليمة"
                        value={summary.ok}
                        color="bg-emerald-50 border-emerald-200 text-emerald-800"
                        testid="sum-ok" />
                    <SummaryCard
                        label="⚠️ انحراف"
                        value={summary.drift}
                        color="bg-amber-50 border-amber-200 text-amber-800"
                        testid="sum-drift" />
                    <SummaryCard
                        label="🔴 BNPL مخفي"
                        value={summary.iter249_bnpl_hidden}
                        color="bg-rose-50 border-rose-200 text-rose-800"
                        testid="sum-iter249" />
                    <SummaryCard
                        label="إجمالي المبلغ المخفي (ر.س)"
                        value={fmt(summary.total_hidden_amount)}
                        color="bg-purple-50 border-purple-200 text-purple-800"
                        testid="sum-hidden-amount" />
                </div>
            )}

            {/* Notes */}
            {data?.notes && (
                <div className="bg-sky-50 border border-sky-200 rounded-xl p-4">
                    <div className="flex items-start gap-2">
                        <Warning size={18} className="text-sky-700 shrink-0 mt-0.5" />
                        <div className="text-xs text-sky-900 space-y-1">
                            {data.notes.map((n, i) => (
                                <div key={i}>• {n}</div>
                            ))}
                        </div>
                    </div>
                </div>
            )}

            {/* Main table */}
            <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
                <div className="px-5 py-3 border-b border-slate-200 flex items-center justify-between">
                    <h2 className="font-bold text-slate-800 flex items-center gap-2">
                        <Bank size={18} />
                        الحسابات ({rows.length})
                    </h2>
                    <div className="text-xs text-slate-500">
                        tolerance: <span className="num font-bold">{fmt(data?.tolerance ?? tolerance)}</span> ر.س
                    </div>
                </div>

                {loading && (
                    <div className="p-10 text-center text-slate-500">
                        <Spinner size={32} className="animate-spin mx-auto mb-2" />
                        جاري التحميل…
                    </div>
                )}

                {!loading && rows.length === 0 && (
                    <div className="p-10 text-center text-slate-500" data-testid="empty-state">
                        {summary?.total_accounts === 0
                            ? "لا توجد حسابات لعرضها."
                            : `✅ جميع الحسابات (${summary?.total_accounts || 0}) سليمة — لا انحرافات. فعّل خيار "عرض الحسابات السليمة" لرؤيتها.`}
                    </div>
                )}

                {!loading && rows.length > 0 && (
                    <div className="overflow-x-auto" data-testid="drift-table-wrap">
                        <table className="w-full text-sm">
                            <thead className="bg-slate-50 text-xs">
                                <tr>
                                    <th className="px-3 py-2 text-right font-bold">الحساب</th>
                                    <th className="px-3 py-2 text-right font-bold">النوع</th>
                                    <th className="px-3 py-2 text-right font-bold">Stored<br/><span className="opacity-60">(current_balance)</span></th>
                                    <th className="px-3 py-2 text-right font-bold">Ledger main<br/><span className="opacity-60">(sub=main)</span></th>
                                    <th className="px-3 py-2 text-right font-bold">Ledger balance<br/><span className="opacity-60">(BNPL bridge)</span></th>
                                    <th className="px-3 py-2 text-right font-bold">SSOT<br/><span className="opacity-60">(canonical)</span></th>
                                    <th className="px-3 py-2 text-right font-bold">Account_tx walk<br/><span className="opacity-60">(legacy)</span></th>
                                    <th className="px-3 py-2 text-right font-bold">Displayed<br/><span className="opacity-60">(UI)</span></th>
                                    <th className="px-3 py-2 text-right font-bold">Δ SSOT-Stored</th>
                                    <th className="px-3 py-2 text-right font-bold">Δ SSOT-Walk</th>
                                    <th className="px-3 py-2 text-right font-bold">Hidden tx</th>
                                    <th className="px-3 py-2 text-right font-bold">Status</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((r) => {
                                    const sb = STATUS_BADGE[r.status] || STATUS_BADGE.drift;
                                    const Icon = sb.icon;
                                    const isExpanded = expandedRow === r.id;
                                    return (
                                        <>
                                            <tr
                                                key={r.id}
                                                className="border-t border-slate-100 hover:bg-slate-50 cursor-pointer"
                                                onClick={() => setExpandedRow(isExpanded ? null : r.id)}
                                                data-testid={`drift-row-${r.id}`}>
                                                <td className="px-3 py-2 font-bold">
                                                    {r.name}
                                                    {r.provider_name && (
                                                        <span className="text-[10px] text-slate-500 block">
                                                            {r.provider_name}
                                                        </span>
                                                    )}
                                                </td>
                                                <td className="px-3 py-2 text-xs text-slate-600">
                                                    {TYPE_LABEL[r.account_type] || r.account_type}
                                                </td>
                                                <td className="px-3 py-2 num text-slate-700">{fmt(r.stored_current_balance)}</td>
                                                <td className="px-3 py-2 num text-slate-700">{fmt(r.ledger_main_net)}<br/><span className="text-[10px] text-slate-400">{r.ledger_main_row_count} سطر</span></td>
                                                <td className={`px-3 py-2 num ${r.ledger_balance_net !== 0 ? "text-rose-700 font-bold" : "text-slate-500"}`}>
                                                    {fmt(r.ledger_balance_net)}<br/><span className="text-[10px] text-slate-400">{r.ledger_balance_row_count} سطر</span>
                                                </td>
                                                <td className="px-3 py-2 num font-bold text-emerald-700">{fmt(r.ssot_value)}</td>
                                                <td className="px-3 py-2 num text-slate-700">{fmt(r.account_transactions_walk)}<br/><span className="text-[10px] text-slate-400">{r.account_transactions_row_count} سطر</span></td>
                                                <td className="px-3 py-2 num font-bold">{fmt(r.displayed_balance)}</td>
                                                <td className="px-3 py-2"><DriftCell value={r.drift_ssot_vs_stored} tolerance={data?.tolerance ?? 0.02} /></td>
                                                <td className="px-3 py-2"><DriftCell value={r.drift_ssot_vs_walk} tolerance={data?.tolerance ?? 0.02} /></td>
                                                <td className="px-3 py-2">
                                                    {r.feed_hidden_tx_count > 0 ? (
                                                        <span className="num text-rose-700 font-bold" data-testid={`hidden-count-${r.id}`}>
                                                            {r.feed_hidden_tx_count}
                                                            <span className="block text-[10px] opacity-70">
                                                                {fmt(r.feed_hidden_net_amount)} ر.س
                                                            </span>
                                                        </span>
                                                    ) : (
                                                        <span className="text-slate-400">0</span>
                                                    )}
                                                </td>
                                                <td className="px-3 py-2">
                                                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold border ${sb.cls}`}>
                                                        <Icon size={12} weight="bold" />
                                                        {sb.label}
                                                    </span>
                                                </td>
                                            </tr>
                                            {isExpanded && (
                                                <tr className="bg-slate-50">
                                                    <td colSpan="12" className="px-5 py-4">
                                                        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
                                                            <div>
                                                                <div className="font-bold text-slate-700 mb-1">Opening Balance</div>
                                                                <div className="num">{fmt(r.opening_balance)} ر.س</div>
                                                            </div>
                                                            <div>
                                                                <div className="font-bold text-slate-700 mb-1">Expected Orders</div>
                                                                <div className="num">{fmt(r.expected_orders_balance)} ر.س</div>
                                                            </div>
                                                            <div>
                                                                <div className="font-bold text-slate-700 mb-1">Ledger (main + balance)</div>
                                                                <div className="num">{fmt(r.ledger_main_plus_balance)} ر.س</div>
                                                            </div>
                                                            <div>
                                                                <div className="font-bold text-slate-700 mb-1">Δ feed vs displayed</div>
                                                                <DriftCell value={r.drift_ledger_main_vs_displayed} tolerance={data?.tolerance ?? 0.02} />
                                                            </div>
                                                            {r.feed_hidden_sub_account_entry_types?.length > 0 && (
                                                                <div className="col-span-2 lg:col-span-4">
                                                                    <div className="font-bold text-rose-700 mb-1">
                                                                        🔴 Entry types المخفية في sub_account ≠ &quot;main&quot;:
                                                                    </div>
                                                                    <div className="flex flex-wrap gap-1.5">
                                                                        {r.feed_hidden_sub_account_entry_types.map((t) => (
                                                                            <span key={t} className="px-2 py-0.5 bg-rose-100 text-rose-800 rounded text-[10px] font-bold">
                                                                                {t}
                                                                            </span>
                                                                        ))}
                                                                    </div>
                                                                </div>
                                                            )}
                                                            {r.ssot_error && (
                                                                <div className="col-span-2 lg:col-span-4 bg-rose-100 text-rose-900 p-2 rounded">
                                                                    SSOT Error: <code>{r.ssot_error}</code>
                                                                </div>
                                                            )}
                                                            <div className="col-span-2 lg:col-span-4 text-[10px] text-slate-500 font-mono">
                                                                ID: {r.id}
                                                            </div>
                                                        </div>
                                                    </td>
                                                </tr>
                                            )}
                                        </>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Recommendation footer */}
            {summary?.iter249_bnpl_hidden > 0 && (
                <div className="bg-rose-50 border border-rose-300 rounded-xl p-5" data-testid="iter249-warning">
                    <div className="flex items-start gap-3">
                        <XCircle size={24} className="text-rose-700 shrink-0 mt-0.5" weight="duotone" />
                        <div>
                            <h3 className="font-bold text-rose-900 mb-1">
                                ⚠️ تم اكتشاف {summary.iter249_bnpl_hidden} حساب مع تسويات BNPL مخفية
                            </h3>
                            <div className="text-sm text-rose-800">
                                إجمالي المبلغ المخفي:{" "}
                                <span className="num font-extrabold">
                                    {fmt(summary.total_hidden_amount)} ر.س
                                </span>
                            </div>
                            <p className="text-xs text-rose-700 mt-2">
                                السبب الجذري: <code className="bg-white px-1 rounded">bnpl/settlement_bridge.py</code> يكتب
                                <code className="bg-white px-1 rounded mx-1">sub_account=&quot;balance&quot;</code>
                                بينما UI feed يفلتر
                                <code className="bg-white px-1 rounded mx-1">sub_account=&quot;main&quot;</code> فقط.
                            </p>
                            <p className="text-xs text-rose-700 mt-1">
                                🔧 الحل المقترَح: راجع
                                <code className="bg-white px-1 rounded mx-1">/app/docs/ITER250B_P1.5_BALANCE_SSOT_RFC.md</code>
                                (F1 Quick Fix + Phase 3 Clean Fix).
                            </p>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
