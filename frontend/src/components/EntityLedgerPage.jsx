// Iter-161 Phase 4 — Generic Entity Ledger Page
//
// One reusable component used by /suppliers-ledger, /externals-ledger,
// /couriers-ledger (and /employees-ledger could be migrated to it).
//
// Reads from the entity's /list endpoint, shows summary cards, a row
// table, and per-row statement drawer with reverse-entry capability.

import React, { useState, useEffect } from "react";
import api from "../lib/api";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";

const fmt = (n) => Number(n || 0).toLocaleString(
    "ar-SA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/**
 * @param {object} config
 *   listEndpoint:     "/accounting/suppliers/list"
 *   itemsKey:         "suppliers"
 *   entityType:       "supplier" (for /ledger entries)
 *   headerTitle:      "🏭 الموردون (نظام Ledger)"
 *   subAccount:       optional sub_account filter for the entries drawer
 *   summaryCards:     [{label, totalsKey, color}, ...]
 *   columns:          [{key, label, color?, isCurrency?: bool}, ...]
 *   testIdPrefix:     "sup"
 *   newTxnRoute:      "/new-transaction"
 *   noDataText:       "لا يوجد موردون"
 */
export default function EntityLedgerPage({ config }) {
    const [rows, setRows] = useState([]);
    const [totals, setTotals] = useState({});
    const [loading, setLoading] = useState(true);
    const [open, setOpen] = useState(null);
    const navigate = useNavigate();

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get(config.listEndpoint);
            setRows(data[config.itemsKey] || []);
            setTotals(data.totals || {});
        } catch (e) {
            toast.error("فشل تحميل القائمة");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, [config.listEndpoint]);

    return (
        <div className="p-6 max-w-6xl mx-auto" data-testid={`${config.testIdPrefix}-ledger-page`}>
            <div className="bg-white rounded-2xl shadow-lg p-6">
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h1 className="text-2xl font-extrabold text-slate-900">
                            {config.headerTitle}
                        </h1>
                        <p className="text-xs text-slate-500 mt-1">
                            كل الأرصدة محسوبة من قيود `general_ledger` فقط (Phase 4)
                        </p>
                    </div>
                    <button onClick={() => navigate(config.newTxnRoute || "/new-transaction")}
                        className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-bold rounded-lg">
                        ➕ حركة مالية جديدة
                    </button>
                </div>

                <div className={`grid grid-cols-${Math.min(config.summaryCards.length, 4)} gap-3 mb-4`}>
                    {config.summaryCards.map(card => (
                        <div key={card.totalsKey}
                            className={`bg-${card.color}-50 border border-${card.color}-200 rounded-lg p-3`}>
                            <div className="text-[10px] text-slate-600 font-bold mb-1">{card.label}</div>
                            <div className={`text-lg font-extrabold text-${card.color}-700 num`}>
                                {fmt(totals[card.totalsKey])} ر.س
                            </div>
                        </div>
                    ))}
                </div>

                {loading ? (
                    <div className="text-center py-12 text-slate-400">جاري التحميل...</div>
                ) : rows.length === 0 ? (
                    <div className="text-center py-12 text-slate-400">{config.noDataText}</div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead className="bg-slate-50 border-b border-slate-200">
                                <tr className="text-slate-600">
                                    <th className="text-right py-2 px-2 font-bold">الاسم</th>
                                    {config.columns.map(col => (
                                        <th key={col.key} className={`text-left py-2 px-2 font-bold ${col.color ? `text-${col.color}-700` : ""}`}>
                                            {col.label}
                                        </th>
                                    ))}
                                    <th className="py-2 px-2"></th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map(r => (
                                    <tr key={r.id}
                                        className="border-b border-slate-100 hover:bg-slate-50 cursor-pointer"
                                        onClick={() => setOpen(r)}
                                        data-testid={`${config.testIdPrefix}-row-${r.id}`}>
                                        <td className="py-2 px-2 font-bold text-slate-900">{r.name}</td>
                                        {config.columns.map(col => (
                                            <td key={col.key} className={`text-left py-2 px-2 num ${col.color ? `font-bold text-${col.color}-700` : ""}`}>
                                                {col.isCurrency !== false ? fmt(r[col.key]) : (r[col.key] || "—")}
                                            </td>
                                        ))}
                                        <td className="text-left py-2 px-2 text-slate-400 text-xs">عرض ←</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {open && <EntityDrawer entity={open}
                entityType={config.entityType}
                subAccount={config.subAccount}
                testIdPrefix={config.testIdPrefix}
                onClose={() => { setOpen(null); load(); }} />}
        </div>
    );
}

function EntityDrawer({ entity, entityType, subAccount, testIdPrefix, onClose }) {
    const [entries, setEntries] = useState([]);
    const [balance, setBalance] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = async () => {
        setLoading(true);
        try {
            const q1 = `entity_type=${entityType}&entity_id=${entity.id}&limit=300`;
            const q2 = `entity_type=${entityType}&entity_id=${entity.id}` +
                (subAccount ? `&sub_account=${subAccount}` : "");
            const [e, b] = await Promise.all([
                api.get(`/ledger/entries?${q1}`),
                api.get(`/ledger/balance?${q2}`),
            ]);
            setEntries(e.data?.items || []);
            setBalance(b.data);
        } catch (err) {
            toast.error("فشل تحميل التفاصيل");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, [entity.id]);

    const reverseEntry = async (entryId, entryNo) => {
        const reason = window.prompt(
            "سبب العكس (actual_payment / data_entry_error / duplicate_entry / accounting_settle / other):",
            "data_entry_error");
        if (!reason) return;
        const notes = window.prompt("ملاحظات (اختياري):", "") || "";
        if (!window.confirm(`عكس القيد #${entryNo}؟ القيد الأصلي سيُحفظ بحالة reversed.`)) return;
        try {
            await api.post(`/ledger/entries/${entryId}/reverse`,
                { reason_code: reason, notes });
            toast.success("تم عكس القيد");
            await load();
        } catch (e) {
            toast.error(e.response?.data?.detail || "فشل العكس");
        }
    };

    // Group entries by txn_group_id
    const groups = {};
    entries.forEach(e => {
        const k = e.txn_group_id || `single-${e.id}`;
        groups[k] = groups[k] || { entries: [], created_at: e.created_at };
        groups[k].entries.push(e);
    });
    const groupList = Object.entries(groups).map(([k, v]) => ({ id: k, ...v }))
        .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));

    return (
        <div className="fixed inset-0 z-50 flex items-start justify-end bg-black/50"
             onClick={onClose}>
            <div className="bg-white w-full max-w-2xl h-full overflow-y-auto p-6"
                 onClick={e => e.stopPropagation()}
                 data-testid={`${testIdPrefix}-drawer`}>
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-xl font-extrabold text-slate-900">{entity.name}</h2>
                    <button onClick={onClose} className="text-slate-500 hover:text-slate-800 text-2xl">×</button>
                </div>

                {loading ? (
                    <div className="text-center py-8 text-slate-400">جاري التحميل...</div>
                ) : (
                    <>
                        {balance && (
                            <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 mb-4">
                                <div className="grid grid-cols-3 gap-2 text-xs">
                                    <div>
                                        <div className="text-slate-600">إجمالي مدين</div>
                                        <div className="num font-bold text-emerald-700">{fmt(balance.debits)}</div>
                                    </div>
                                    <div>
                                        <div className="text-slate-600">إجمالي دائن</div>
                                        <div className="num font-bold text-rose-700">{fmt(balance.credits)}</div>
                                    </div>
                                    <div>
                                        <div className="text-slate-600">صافي</div>
                                        <div className={`num font-extrabold ${balance.net_balance >= 0 ? "text-emerald-700" : "text-rose-700"}`}>
                                            {fmt(balance.net_balance)}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        )}

                        <h3 className="text-sm font-extrabold text-slate-800 mb-2">
                            📋 السجل الكامل ({groupList.length} عملية، {entries.length} قيد)
                        </h3>
                        {groupList.length === 0 && (
                            <div className="text-center py-6 text-slate-400 text-xs">لا توجد قيود</div>
                        )}
                        {groupList.map(g => (
                            <div key={g.id} className="border border-slate-200 rounded-lg p-2 mb-2 bg-slate-50">
                                <div className="text-[10px] text-slate-500 mb-1">
                                    {g.created_at?.slice(0, 19).replace("T", " ")} ·
                                    {g.entries[0]?.metadata?.txn_type || "transaction"}
                                </div>
                                {g.entries.map(e => (
                                    <div key={e.id}
                                        className={`flex items-center justify-between text-xs py-1 border-b border-slate-100 ${e.status === "reversed" ? "opacity-50" : ""}`}>
                                        <div className="flex-1">
                                            <span className={`font-bold ${e.side === "debit" ? "text-emerald-700" : "text-rose-700"}`}>
                                                {e.side === "debit" ? "مدين" : "دائن"}
                                            </span>
                                            {" · "}
                                            <span className="text-slate-700">
                                                {e.sub_account ? `${e.entity_type}.${e.sub_account}` : e.entity_type}
                                            </span>
                                            {e.notes && <span className="text-slate-500 mr-1">— {e.notes}</span>}
                                        </div>
                                        <div className="num font-bold text-sm">{fmt(e.amount)}</div>
                                        {e.status === "posted" && e.entry_type !== "reversal" && (
                                            <button onClick={() => reverseEntry(e.id, e.entry_no)}
                                                className="text-[10px] text-amber-700 underline mr-2 hover:text-amber-900">
                                                عكس
                                            </button>
                                        )}
                                        {e.status === "reversed" && (
                                            <span className="text-[9px] text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded mr-2">
                                                معكوس
                                            </span>
                                        )}
                                    </div>
                                ))}
                            </div>
                        ))}
                    </>
                )}
            </div>
        </div>
    );
}
