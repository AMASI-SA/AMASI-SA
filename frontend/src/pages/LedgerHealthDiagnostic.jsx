/**
 * Iter-240 — Ledger Health Diagnostic (Read-Only).
 *
 * Live monitoring card for the Double-Write helper:
 *  - coverage_pct, total/mirrored/unmirrored counts for today
 *  - red alert when coverage < 100% with offending transaction_type +
 *    inferred endpoint and the first 10 unmirrored rows
 *  - full unmirrored-transactions feed (since the day Iter-240
 *    went live, never historical)
 *
 * Strictly READ-ONLY. No fix buttons. No backfill controls.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const fmt = (n) =>
    (Number(n) || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

const errMsg = (e, fb) =>
    e?.response?.data?.detail || e?.response?.data?.error
    || e?.message || fb;

const fmtDate = (iso) => {
    if (!iso) return "—";
    try {
        return new Date(iso).toLocaleString("en-GB", {
            year: "numeric", month: "2-digit", day: "2-digit",
            hour: "2-digit", minute: "2-digit",
        });
    } catch { return iso; }
};

const fmtTime = (iso) => {
    if (!iso) return "—";
    try {
        return new Date(iso).toLocaleTimeString("en-GB", {
            hour: "2-digit", minute: "2-digit",
        });
    } catch { return iso; }
};

// Arabic-friendly labels for transaction_type (UX-only, not authoritative).
const TXN_TYPE_LABELS = {
    internal_transfer:     "تحويل داخلي",
    debt_payment:          "سداد التزام",
    receivable_collection: "تحصيل ذمم",
    salary_advance:        "سلفة موظف",
    expense:               "مصروف",
    courier_transfer:      "تحويل شركة شحن",
    shipping_debt_payment: "سداد شركة شحن",
    settlement:            "تسوية",
    ad_account_topup:      "تعبئة حساب إعلاني",
    opening_balance:       "رصيد افتتاحي",
};
const labelOf = (tt) => TXN_TYPE_LABELS[tt] || tt || "—";

export default function LedgerHealthDiagnostic() {
    const [health, setHealth] = useState(null);
    const [feed, setFeed] = useState(null);
    const [loading, setLoading] = useState(true);
    const [since, setSince] = useState("2026-06-17");

    const load = async () => {
        setLoading(true);
        try {
            const [h, f] = await Promise.all([
                api.get("/audit/double-write-health?last_n=20"),
                api.get(`/audit/unmirrored-transactions?since=${since}`),
            ]);
            setHealth(h.data);
            setFeed(f.data);
        } catch (e) {
            toast.error(errMsg(e, "فشل تحميل تقرير صحة الـ Ledger"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); /* eslint-disable-next-line */ }, [since]);

    const today = health?.today_summary || {};
    const coverage = today.coverage_pct;
    const isHealthy = today.is_healthy !== false;
    const byTxnType = health?.unmirrored_breakdown_today?.by_transaction_type
        || {};
    const byInferredEp = health?.unmirrored_breakdown_today?.by_inferred_endpoint
        || {};
    const unmirroredSample = health?.unmirrored_sample_today || [];
    const recent = health?.last_n_account_txns || [];
    const feedItems = feed?.items || [];

    return (
        <div className="space-y-6" dir="rtl" data-testid="ledger-health-diagnostic">
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl font-bold">
                        🩺 صحة الـ Ledger — Double-Write Monitor
                    </h1>
                    <p className="text-sm text-gray-600 mt-1">
                        تقرير قراءة فقط. لا إصلاح تلقائي، لا قيود تعويضية،
                        لا backfill. يكشف فقط أي تسرّب جديد فور حدوثه.
                    </p>
                </div>
                <div className="flex gap-2">
                    <input
                        type="date"
                        value={since}
                        onChange={(e) => setSince(e.target.value)}
                        className="border rounded px-3 py-2 text-sm"
                        data-testid="ledger-health-since-input"
                    />
                    <button
                        onClick={load}
                        disabled={loading}
                        className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
                        data-testid="ledger-health-refresh-btn"
                    >
                        {loading ? "...جارٍ التحديث" : "🔄 تحديث"}
                    </button>
                </div>
            </div>

            {/* ── Today summary card ─────────────────────────────── */}
            <div
                className={`rounded-lg border-2 p-6 ${
                    isHealthy ? "bg-green-50 border-green-400"
                              : "bg-red-50 border-red-400"
                }`}
                data-testid="ledger-health-today-card"
            >
                <div className="flex items-center justify-between flex-wrap gap-3">
                    <h2 className="text-lg font-semibold">
                        {isHealthy ? "✅ سليم" : "🚨 تسرّب مكتشف"} — مراقبة اليوم
                    </h2>
                    <div className="text-3xl font-bold"
                         data-testid="ledger-health-coverage-pct">
                        {coverage === null || coverage === undefined
                            ? "—" : `${fmt(coverage)}%`}
                    </div>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4 text-sm">
                    <Stat label="إجمالي حركات اليوم"
                          value={today.total_account_txns ?? 0}
                          testid="stat-total" />
                    <Stat label="مُرحّلة للـ Ledger"
                          value={today.mirrored ?? 0}
                          tone="ok" testid="stat-mirrored" />
                    <Stat label="غير مُرحّلة"
                          value={today.unmirrored ?? 0}
                          tone={(today.unmirrored ?? 0) > 0 ? "bad" : null}
                          testid="stat-unmirrored" />
                    <Stat label="آخر تحديث"
                          value={fmtDate(health?.generated_at)}
                          small testid="stat-generated-at" />
                </div>
            </div>

            {/* ── Daily stats card (Phase 3) ─────────────────────── */}
            <div className="rounded-lg bg-white border-2 border-blue-200 p-6"
                 data-testid="ledger-health-stats-card">
                <h2 className="text-lg font-semibold mb-4">
                    📊 إحصاءات اليوم — Double-Write
                </h2>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                    <Stat label="حركات مُرحّلة اليوم"
                          value={today.mirrored ?? 0}
                          tone="ok"
                          testid="stat-mirrored-count" />
                    <Stat label="إجمالي المبالغ المُرحّلة"
                          value={`${fmt(today.mirrored_amount_total ?? 0)} ر.س`}
                          testid="stat-mirrored-amount" />
                    <Stat label="قيود Ledger المُنشأة"
                          value={today.ledger_entries_created ?? 0}
                          testid="stat-ledger-entries" />
                    <Stat label="آخر مزامنة"
                          value={fmtTime(today.last_mirror_at)}
                          small
                          testid="stat-last-sync" />
                </div>
            </div>

            {/* ── RED ALERT when coverage < 100% ─────────────────── */}
            {!isHealthy && (
                <div
                    className="rounded-lg bg-red-100 border-4 border-red-600 p-5 shadow-lg animate-pulse"
                    data-testid="ledger-health-leak-alert"
                >
                    <div className="flex items-center gap-3 mb-3">
                        <span className="inline-block bg-red-600 text-white px-3 py-1 rounded text-sm font-extrabold tracking-wider"
                              data-testid="leak-detected-badge">
                            🚨 LEAK DETECTED
                        </span>
                        <h3 className="text-lg font-bold text-red-800">
                            اكتشف تسرّب في الـ Ledger اليوم
                        </h3>
                    </div>
                    <p className="text-sm text-red-700 mb-4">
                        Coverage أقل من 100%. الجداول التالية توضّح المسار
                        المسبب لكل حركة لم تُرحَّل. هذا التقرير قراءة فقط —
                        لا تصحيح تلقائي.
                    </p>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                        <BreakdownTable
                            title="حسب نوع العملية"
                            data={byTxnType}
                            translateKey
                            testid="leak-by-txn-type"
                        />
                        <BreakdownTable
                            title="حسب الـ endpoint المسبب"
                            data={byInferredEp}
                            testid="leak-by-endpoint"
                        />
                    </div>

                    <h4 className="font-semibold mb-2 text-red-900">
                        تفاصيل أول 10 معاملات غير مُرحّلة اليوم
                    </h4>
                    <UnmirroredTable
                        rows={unmirroredSample}
                        fullColumns
                        testid="leak-sample-table"
                    />
                </div>
            )}

            {/* ── Unmirrored feed since `since` ──────────────────── */}
            <div className="bg-white rounded-lg border p-5"
                 data-testid="ledger-health-feed-card">
                <h3 className="text-lg font-semibold mb-3">
                    قائمة المعاملات غير المُرحّلة منذ {since}
                </h3>
                <p className="text-xs text-gray-500 mb-3">
                    أي معاملة هنا تعني تسرّباً جديداً — يجب فحص الـ endpoint
                    المُشار إليه. (لا تشمل أي حركة قديمة قبل {since}.)
                </p>
                {feed && (
                    <div className="text-sm text-gray-600 mb-3">
                        نطاق الفحص: {feed.total_scanned} حركة |
                        غير مُرحّل: <span
                            className={feed.total_unmirrored > 0
                                ? "text-red-700 font-bold"
                                : "text-green-700 font-bold"}
                            data-testid="feed-total-unmirrored">
                            {feed.total_unmirrored}
                        </span>
                    </div>
                )}
                <UnmirroredTable rows={feedItems}
                                 fullColumns
                                 testid="feed-unmirrored-table" />
            </div>

            {/* ── Last N recent txns ─────────────────────────────── */}
            <div className="bg-white rounded-lg border p-5"
                 data-testid="ledger-health-recent-card">
                <h3 className="text-lg font-semibold mb-3">
                    آخر 20 حركة في النظام
                </h3>
                <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                    <thead>
                        <tr className="bg-gray-100 text-right">
                            <th className="px-3 py-2">نوع العملية</th>
                            <th className="px-3 py-2">الحساب</th>
                            <th className="px-3 py-2">المبلغ</th>
                            <th className="px-3 py-2">الاتجاه</th>
                            <th className="px-3 py-2">التاريخ</th>
                            <th className="px-3 py-2">منشَأة</th>
                            <th className="px-3 py-2">الحالة</th>
                        </tr>
                    </thead>
                    <tbody>
                        {recent.map((r) => (
                            <tr key={r.id} className="border-t"
                                data-testid={`recent-row-${r.id}`}>
                                <td className="px-3 py-2">
                                    <div>{labelOf(r.transaction_type)}</div>
                                    <div className="text-xs text-gray-400 font-mono">
                                        {r.transaction_type}
                                    </div>
                                </td>
                                <td className="px-3 py-2">
                                    {r.account_name || "—"}
                                </td>
                                <td className="px-3 py-2 font-mono">
                                    {fmt(r.amount)}
                                </td>
                                <td className="px-3 py-2">
                                    {r.direction === "in" ? "وارد" : "صادر"}
                                </td>
                                <td className="px-3 py-2">
                                    {r.transaction_date || "—"}
                                </td>
                                <td className="px-3 py-2 text-xs text-gray-500">
                                    {fmtDate(r.created_at)}
                                </td>
                                <td className="px-3 py-2">
                                    {r.mirrored
                                        ? <span className="inline-block bg-green-100 text-green-700 px-2 py-0.5 rounded text-xs font-semibold">✓ مُرحَّل</span>
                                        : <span className="inline-block bg-gray-100 text-gray-500 px-2 py-0.5 rounded text-xs">— لم يُرحَّل</span>
                                    }
                                </td>
                            </tr>
                        ))}
                        {recent.length === 0 && (
                            <tr><td colSpan={7}
                                    className="text-center py-4 text-gray-500">
                                لا توجد حركات.
                            </td></tr>
                        )}
                    </tbody>
                </table>
                </div>
            </div>
        </div>
    );
}

function Stat({ label, value, tone, small, testid }) {
    const color = tone === "ok" ? "text-green-700"
                : tone === "bad" ? "text-red-700"
                : "text-gray-800";
    return (
        <div data-testid={testid}>
            <div className="text-xs text-gray-500">{label}</div>
            <div className={`${small ? "text-sm" : "text-2xl"} font-bold ${color}`}>
                {value}
            </div>
        </div>
    );
}

function BreakdownTable({ title, data, translateKey, testid }) {
    const rows = Object.entries(data || {});
    return (
        <div data-testid={testid}>
            <h5 className="text-sm font-semibold mb-2">{title}</h5>
            <table className="min-w-full text-xs border">
                <tbody>
                    {rows.length === 0 ? (
                        <tr><td className="p-2 text-gray-500">—</td></tr>
                    ) : rows.map(([k, v]) => (
                        <tr key={k} className="border-t">
                            <td className="p-2 font-mono">
                                {translateKey ? labelOf(k) : k}
                            </td>
                            <td className="p-2 text-right font-bold">{v}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function UnmirroredTable({ rows, fullColumns, testid }) {
    if (!rows || rows.length === 0) {
        return (
            <p className="text-sm text-gray-500 text-center py-4"
               data-testid={`${testid}-empty`}>
                لا توجد معاملات غير مُرحّلة. ✓
            </p>
        );
    }
    return (
        <div className="overflow-x-auto" data-testid={testid}>
            <table className="min-w-full text-xs border">
                <thead className="bg-gray-100">
                    <tr className="text-right">
                        <th className="p-2">نوع العملية</th>
                        <th className="p-2">الحساب</th>
                        <th className="p-2">المبلغ</th>
                        <th className="p-2">التاريخ</th>
                        {fullColumns && <th className="p-2">المسار المُحتمل</th>}
                        <th className="p-2">منشَأة</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((r, i) => (
                        <tr key={r.transaction_id || r.id || i}
                            className="border-t">
                            <td className="p-2">
                                <div>{labelOf(r.transaction_type)}</div>
                                <div className="text-xs text-gray-400 font-mono">
                                    {r.transaction_type}
                                </div>
                            </td>
                            <td className="p-2 font-semibold">
                                {r.account_name || r.account_id?.slice(0, 8) || "—"}
                            </td>
                            <td className="p-2 font-bold font-mono">
                                {fmt(r.amount)}
                            </td>
                            <td className="p-2">
                                {r.transaction_date || "—"}
                            </td>
                            {fullColumns && (
                                <td className="p-2 text-gray-600 text-[11px]">
                                    {r.created_by_endpoint
                                        || r.inferred_endpoint || "—"}
                                </td>
                            )}
                            <td className="p-2 text-gray-500">
                                {fmtDate(r.created_at)}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
