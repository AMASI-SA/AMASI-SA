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

            {/* ── RED ALERT when coverage < 100% ─────────────────── */}
            {!isHealthy && (
                <div
                    className="rounded-lg bg-red-100 border-2 border-red-500 p-5"
                    data-testid="ledger-health-leak-alert"
                >
                    <h3 className="text-lg font-bold text-red-800 mb-3">
                        🚨 تنبيه: اكتشف تسرّب في الـ Ledger اليوم
                    </h3>
                    <p className="text-sm text-red-700 mb-4">
                        Coverage أقل من 100%. الجداول التالية توضّح المسار
                        المسبب لكل حركة لم تُرحَّل. هذا التقرير قراءة فقط —
                        لا تصحيح تلقائي.
                    </p>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                        <BreakdownTable
                            title="حسب transaction_type"
                            data={byTxnType}
                            testid="leak-by-txn-type"
                        />
                        <BreakdownTable
                            title="حسب الـ endpoint المسبب"
                            data={byInferredEp}
                            testid="leak-by-endpoint"
                        />
                    </div>

                    <h4 className="font-semibold mb-2">
                        أول 10 معاملات غير مُرحّلة اليوم
                    </h4>
                    <UnmirroredTable
                        rows={unmirroredSample}
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
                <table className="min-w-full text-sm">
                    <thead>
                        <tr className="bg-gray-100 text-right">
                            <th className="px-3 py-2">النوع</th>
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
                                <td className="px-3 py-2 font-mono text-xs">
                                    {r.transaction_type}
                                </td>
                                <td className="px-3 py-2">
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
                                        ? <span className="text-green-700">✓ مُرحَّل</span>
                                        : <span className="text-gray-500">— لم يُرحَّل</span>
                                    }
                                </td>
                            </tr>
                        ))}
                        {recent.length === 0 && (
                            <tr><td colSpan={6}
                                    className="text-center py-4 text-gray-500">
                                لا توجد حركات.
                            </td></tr>
                        )}
                    </tbody>
                </table>
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

function BreakdownTable({ title, data, testid }) {
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
                            <td className="p-2 font-mono">{k}</td>
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
                        <th className="p-2">النوع</th>
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
                            <td className="p-2 font-mono">
                                {r.transaction_type}
                            </td>
                            <td className="p-2">
                                {r.account_name || r.account_id?.slice(0, 8) || "—"}
                            </td>
                            <td className="p-2 font-bold">
                                {fmt(r.amount)}
                            </td>
                            <td className="p-2">
                                {r.transaction_date || "—"}
                            </td>
                            {fullColumns && (
                                <td className="p-2 text-gray-600">
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
