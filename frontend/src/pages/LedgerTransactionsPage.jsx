/**
 * Iter-216 — Full-page financial transactions ledger.
 *
 * Sister page to the inline "آخر الحركات المالية" panel in
 * UnifiedEntryScreen. Differences:
 *   • Dedicated route at /transactions.
 *   • 15 rows per page with pagination arrows.
 *   • Header counters: total transactions, totals of debits/credits
 *     for the visible page.
 *   • Same row-click → opens the shared TxnDetailModal (creator,
 *     reverser, debit/credit breakdown, reversal flow).
 *
 * The backend `GET /api/ledger/entries` already returns
 * `posted_by_name`, `reversed_by_name`, and supports `skip`/`limit`,
 * so this page is a thin client-side aggregator that groups legs by
 * `txn_group_id` and paginates groups (not legs).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import api from "../lib/api";
import {
    TxnDetailModal, txnLabel, fmtNum, timeAgo,
} from "./UnifiedEntryScreen";

const PAGE_SIZE = 15;
// We over-fetch legs so that after group-by we still have ≥ PAGE_SIZE
// groups available. A balanced 2-leg group is the common case, so 4×
// is comfortably safe for AM/PM postings (which can have 3 legs).
const FETCH_MULTIPLIER = 6;

function groupLegsByTxn(items) {
    const groups = new Map();
    for (const e of items) {
        const gid = e.txn_group_id;
        if (!gid) continue;
        if (!groups.has(gid)) {
            groups.set(gid, {
                txn_group_id: gid,
                posted_at: e.posted_at,
                txn_type: (e.metadata && e.metadata.txn_type)
                    || e.entry_type,
                notes: e.notes || (e.metadata && e.metadata.notes) || "",
                posted_by_name: e.posted_by_name || "",
                reversed_by_name: e.reversed_by_name || "",
                reversed_at: e.reversed_at || null,
                legs: [],
            });
        }
        const g = groups.get(gid);
        if (!g.posted_by_name && e.posted_by_name) {
            g.posted_by_name = e.posted_by_name;
        }
        if (!g.reversed_by_name && e.reversed_by_name) {
            g.reversed_by_name = e.reversed_by_name;
            g.reversed_at = e.reversed_at || g.reversed_at;
        }
        g.legs.push({
            entry_id: e.id,
            side: e.side,
            amount: Number(e.amount || 0),
            entity_type: e.entity_type,
            entity_id: e.entity_id,
            sub_account: e.sub_account,
            status: e.status,
            reversed_by_entry_id: e.reversed_by_entry_id || null,
        });
        if (e.posted_at && (!g.posted_at || e.posted_at > g.posted_at)) {
            g.posted_at = e.posted_at;
        }
    }
    return Array.from(groups.values())
        .map((g) => ({
            ...g,
            total_debit: g.legs.filter((l) => l.side === "debit")
                .reduce((s, l) => s + l.amount, 0),
            total_credit: g.legs.filter((l) => l.side === "credit")
                .reduce((s, l) => s + l.amount, 0),
        }))
        .sort((a, b) => (b.posted_at || "").localeCompare(a.posted_at || ""));
}

export default function LedgerTransactionsPage() {
    const [page, setPage] = useState(0);
    const [rows, setRows] = useState([]);
    const [legsTotal, setLegsTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [selectedTxn, setSelectedTxn] = useState(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            // Pull a deep slice so we have enough legs to materialise
            // (page+1)·PAGE_SIZE groups + a comfortable buffer.
            const need = (page + 1) * PAGE_SIZE * FETCH_MULTIPLIER;
            const { data } = await api.get("/ledger/entries", {
                params: { limit: Math.min(500, need) },
            });
            setLegsTotal(data?.total || 0);
            setRows(groupLegsByTxn(data?.items || []));
        } catch (err) {
            console.error("load ledger entries failed", err);
            setRows([]);
        } finally {
            setLoading(false);
        }
    }, [page]);

    useEffect(() => { load(); }, [load]);

    const totalGroups = rows.length;
    const totalPages = Math.max(1, Math.ceil(totalGroups / PAGE_SIZE));
    const pageRows = useMemo(
        () => rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
        [rows, page],
    );

    const pageDebit = pageRows.reduce(
        (s, g) => s + (g.total_debit || 0), 0);
    const pageCredit = pageRows.reduce(
        (s, g) => s + (g.total_credit || 0), 0);

    return (
        <div className="min-h-screen bg-gradient-to-br from-slate-50 to-sky-50/30 py-6 px-4"
            data-testid="ledger-txns-page">
            <div className="max-w-6xl mx-auto">
                {/* Header */}
                <div className="bg-white rounded-2xl shadow-sm p-5 mb-4 flex flex-wrap items-center justify-between gap-3"
                    style={{ fontFamily: "Tajawal" }}>
                    <div>
                        <h1 className="text-xl font-extrabold text-slate-900">
                            📜 سجل الحركات المالية
                        </h1>
                        <p className="text-xs text-slate-500 mt-1">
                            كل الحركات المسجّلة في الدفتر الموحّد —
                            اضغط على أي صف لمشاهدة المدين والدائن
                            ومن أضافها أو عكسها.
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <div className="text-[11px] text-slate-500">
                            إجمالي القيود في الدفتر:{" "}
                            <span className="font-extrabold text-slate-800 num">
                                {legsTotal.toLocaleString("en-US")}
                            </span>
                        </div>
                        <button
                            onClick={load}
                            disabled={loading}
                            className="px-3 py-1.5 rounded-lg bg-sky-50 hover:bg-sky-100 text-sky-700 text-xs font-bold border border-sky-200 disabled:opacity-50"
                            data-testid="ledger-txns-refresh-btn">
                            {loading ? "..." : "🔄 تحديث"}
                        </button>
                    </div>
                </div>

                {/* Page totals strip */}
                <div className="grid grid-cols-3 gap-3 mb-4 text-xs">
                    <div className="bg-white rounded-xl p-3 border border-slate-200">
                        <div className="text-slate-500 mb-1">حركات هذه الصفحة</div>
                        <div className="text-lg font-extrabold text-slate-800 num"
                            data-testid="ledger-txns-page-count">
                            {pageRows.length}
                        </div>
                    </div>
                    <div className="bg-emerald-50 rounded-xl p-3 border border-emerald-200">
                        <div className="text-emerald-700 mb-1">إجمالي المدين</div>
                        <div className="text-lg font-extrabold text-emerald-800 num"
                            data-testid="ledger-txns-page-debit">
                            {fmtNum(pageDebit)} <span className="text-[10px]">ر.س</span>
                        </div>
                    </div>
                    <div className="bg-rose-50 rounded-xl p-3 border border-rose-200">
                        <div className="text-rose-700 mb-1">إجمالي الدائن</div>
                        <div className="text-lg font-extrabold text-rose-800 num"
                            data-testid="ledger-txns-page-credit">
                            {fmtNum(pageCredit)} <span className="text-[10px]">ر.س</span>
                        </div>
                    </div>
                </div>

                {/* Table */}
                <div className="bg-white rounded-2xl shadow-sm overflow-hidden">
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead className="bg-slate-50 text-slate-500 border-b border-slate-200">
                                <tr>
                                    <th className="text-right py-3 px-3 font-bold">الوقت</th>
                                    <th className="text-right py-3 px-3 font-bold">نوع العملية</th>
                                    <th className="text-right py-3 px-3 font-bold">الوصف</th>
                                    <th className="text-right py-3 px-3 font-bold">بواسطة</th>
                                    <th className="text-right py-3 px-3 font-bold">الحالة</th>
                                    <th className="text-left py-3 px-3 font-bold">المبلغ (ر.س)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {pageRows.length === 0 && (
                                    <tr>
                                        <td colSpan={6} className="py-10 text-center text-slate-400 text-sm"
                                            data-testid="ledger-txns-empty">
                                            {loading ? "جاري التحميل…" : "لا توجد حركات لعرضها."}
                                        </td>
                                    </tr>
                                )}
                                {pageRows.map((g) => {
                                    const isReversed = !!g.reversed_by_name;
                                    return (
                                        <tr key={g.txn_group_id}
                                            onClick={() => setSelectedTxn(g)}
                                            className={`border-b border-slate-100 cursor-pointer transition-colors ${
                                                isReversed
                                                    ? "bg-rose-50/40 hover:bg-rose-50"
                                                    : "hover:bg-slate-50"
                                            }`}
                                            data-testid={`ledger-txn-row-${g.txn_group_id}`}>
                                            <td className="py-2.5 px-3 text-slate-600 whitespace-nowrap">
                                                {timeAgo(g.posted_at)}
                                            </td>
                                            <td className="py-2.5 px-3 font-bold text-slate-800 whitespace-nowrap">
                                                {txnLabel(g.txn_type)}
                                            </td>
                                            <td className="py-2.5 px-3 text-slate-600 truncate max-w-[280px]"
                                                title={g.notes}>
                                                {g.notes || "—"}
                                            </td>
                                            <td className="py-2.5 px-3 text-slate-700 whitespace-nowrap">
                                                {g.posted_by_name || "—"}
                                            </td>
                                            <td className="py-2.5 px-3 whitespace-nowrap">
                                                {isReversed ? (
                                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-rose-100 text-rose-800 text-[10px] font-bold"
                                                        title={`عكسها: ${g.reversed_by_name}`}>
                                                        ↩︎ معكوس · {g.reversed_by_name}
                                                    </span>
                                                ) : (
                                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-emerald-100 text-emerald-800 text-[10px] font-bold">
                                                        ✓ معتمد
                                                    </span>
                                                )}
                                            </td>
                                            <td className={`text-left py-2.5 px-3 num font-extrabold ${
                                                isReversed
                                                    ? "text-rose-700 line-through decoration-rose-400"
                                                    : "text-emerald-700"
                                            }`}>
                                                {fmtNum(g.total_debit)}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    {/* Pagination footer */}
                    <div className="bg-slate-50 border-t border-slate-200 px-4 py-3 flex items-center justify-between text-xs">
                        <div className="text-slate-500"
                            data-testid="ledger-txns-page-range">
                            صفحة <span className="num font-bold text-slate-800">{page + 1}</span>
                            {" "}من{" "}
                            <span className="num font-bold text-slate-800">{totalPages}</span>
                            <span className="mx-2 text-slate-300">·</span>
                            عرض <span className="num font-bold text-slate-800">
                                {totalGroups === 0 ? 0 : page * PAGE_SIZE + 1}
                            </span>–<span className="num font-bold text-slate-800">
                                {Math.min((page + 1) * PAGE_SIZE, totalGroups)}
                            </span> من <span className="num font-bold text-slate-800">{totalGroups}</span>
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                onClick={() => setPage((p) => Math.max(0, p - 1))}
                                disabled={page === 0 || loading}
                                className="w-9 h-9 rounded-lg bg-white border border-slate-300 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center text-slate-700 font-bold"
                                data-testid="ledger-txns-prev-btn"
                                title="الصفحة السابقة">
                                →
                            </button>
                            <span className="px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-slate-700 font-bold min-w-[42px] text-center num"
                                data-testid="ledger-txns-page-current">
                                {page + 1}
                            </span>
                            <button
                                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                                disabled={page >= totalPages - 1 || loading}
                                className="w-9 h-9 rounded-lg bg-white border border-slate-300 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center text-slate-700 font-bold"
                                data-testid="ledger-txns-next-btn"
                                title="الصفحة التالية">
                                ←
                            </button>
                        </div>
                    </div>
                </div>

                <p className="text-[10px] text-slate-400 text-center mt-3">
                    تم تحميل {rows.length} حركة من إجمالي {legsTotal.toLocaleString("en-US")} قيد محاسبي في الدفتر.
                    لعرض حركات أقدم استخدم أزرار الترقيم أعلاه.
                </p>
            </div>

            <TxnDetailModal
                key={selectedTxn?.txn_group_id || "none"}
                txn={selectedTxn}
                onClose={() => setSelectedTxn(null)}
                onReversed={() => {
                    setSelectedTxn(null);
                    load();
                }}
            />
        </div>
    );
}
