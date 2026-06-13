// Iter-161 Phase 4 — Employees (Ledger-only view)
//
// Reads ALL balances strictly from /api/accounting/employees/list (which
// derives from general_ledger). Replaces legacy `/employees` view that
// read from `liabilities` + `operating_salaries.balance`.
//
// Click a row → Employee Detail Drawer with:
//   • Live balances (3 sub-accounts)
//   • Full ledger statement (entries grouped by txn_group_id)
//   • "Reverse" button per posted entry → creates mirror entry
//   • "➕ حركة جديدة" button → /new-transaction with employee pre-selected

import React, { useState, useEffect } from "react";
import api from "../lib/api";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";

const fmt = (n) => Number(n || 0).toLocaleString(
    "en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function EmployeesLedger() {
    const [rows, setRows] = useState([]);
    const [totals, setTotals] = useState({});
    const [loading, setLoading] = useState(true);
    const [open, setOpen] = useState(null); // selected employee for drawer
    const navigate = useNavigate();

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/accounting/employees/list");
            setRows(data.employees || []);
            setTotals(data.totals || {});
        } catch (e) {
            toast.error("فشل تحميل قائمة الموظفين");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);

    return (
        <div className="p-6 max-w-6xl mx-auto" data-testid="employees-ledger">
            <div className="bg-white rounded-2xl shadow-lg p-6">
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h1 className="text-2xl font-extrabold text-slate-900">
                            👥 الموظفون (نظام Ledger)
                        </h1>
                        <p className="text-xs text-slate-500 mt-1">
                            كل الأرصدة محسوبة من قيود `general_ledger` فقط (Phase 4)
                        </p>
                    </div>
                    <button onClick={() => navigate("/new-transaction")}
                        className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-bold rounded-lg"
                        data-testid="emp-new-txn-btn">
                        ➕ حركة مالية جديدة
                    </button>
                </div>

                {/* Totals summary */}
                <div className="grid grid-cols-4 gap-3 mb-4">
                    <SummaryCard label="رواتب مستحقة (نحن مدينون)"
                        value={fmt(totals.salary_payable)} color="rose" />
                    <SummaryCard label="سلف مفتوحة (هم مدينون لنا)"
                        value={fmt(totals.advance)} color="amber" />
                    <SummaryCard label="عهد مفتوحة"
                        value={fmt(totals.custody)} color="sky" />
                    <SummaryCard label="صافي المركز"
                        value={fmt(totals.net_position)}
                        color={totals.net_position >= 0 ? "emerald" : "rose"} />
                </div>

                {loading ? (
                    <div className="text-center py-12 text-slate-400">جاري التحميل...</div>
                ) : rows.length === 0 ? (
                    <div className="text-center py-12 text-slate-400">لا يوجد موظفون</div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead className="bg-slate-50 border-b border-slate-200">
                                <tr className="text-slate-600">
                                    <th className="text-right py-2 px-2 font-bold">الاسم</th>
                                    <th className="text-left py-2 px-2 font-bold">الراتب الشهري</th>
                                    <th className="text-left py-2 px-2 font-bold text-rose-700">مستحق له</th>
                                    <th className="text-left py-2 px-2 font-bold text-amber-700">سلفة</th>
                                    <th className="text-left py-2 px-2 font-bold text-sky-700">عهدة</th>
                                    <th className="text-left py-2 px-2 font-bold">صافي</th>
                                    <th className="py-2 px-2"></th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map(r => (
                                    <tr key={r.id}
                                        className="border-b border-slate-100 hover:bg-slate-50 cursor-pointer"
                                        onClick={() => setOpen(r)}
                                        data-testid={`emp-row-${r.id}`}>
                                        <td className="py-2 px-2 font-bold text-slate-900">{r.name}</td>
                                        <td className="text-left py-2 px-2 num">{fmt(r.monthly_amount)}</td>
                                        <td className="text-left py-2 px-2 num font-bold text-rose-700">{fmt(r.salary_payable)}</td>
                                        <td className="text-left py-2 px-2 num font-bold text-amber-700">{fmt(r.advance)}</td>
                                        <td className="text-left py-2 px-2 num font-bold text-sky-700">{fmt(r.custody)}</td>
                                        <td className={`text-left py-2 px-2 num font-extrabold ${r.net_position >= 0 ? "text-emerald-700" : "text-rose-700"}`}>
                                            {fmt(r.net_position)}
                                        </td>
                                        <td className="text-left py-2 px-2 text-slate-400 text-xs">عرض ←</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {open && <EmployeeDrawer employee={open} onClose={() => { setOpen(null); load(); }} />}
        </div>
    );
}

function SummaryCard({ label, value, color }) {
    return (
        <div className={`bg-${color}-50 border border-${color}-200 rounded-lg p-3`}>
            <div className="text-[10px] text-slate-600 font-bold mb-1">{label}</div>
            <div className={`text-lg font-extrabold text-${color}-700 num`}>{value} ر.س</div>
        </div>
    );
}

function EmployeeDrawer({ employee, onClose }) {
    const [summary, setSummary] = useState(null);
    const [entries, setEntries] = useState([]);
    const [loading, setLoading] = useState(true);

    const load = async () => {
        setLoading(true);
        try {
            const [s, e] = await Promise.all([
                api.get(`/accounting/employees/${employee.id}/financial-summary`),
                api.get(`/ledger/entries?entity_type=employee&entity_id=${employee.id}&limit=300`),
            ]);
            setSummary(s.data);
            setEntries(e.data?.items || []);
        } catch (err) {
            toast.error("فشل تحميل التفاصيل");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, [employee.id]);

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
                 data-testid="emp-drawer">
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-xl font-extrabold text-slate-900">
                        👤 {employee.name}
                    </h2>
                    <button onClick={onClose} className="text-slate-500 hover:text-slate-800 text-2xl">×</button>
                </div>

                {loading ? (
                    <div className="text-center py-8 text-slate-400">جاري التحميل...</div>
                ) : (
                    <>
                        {/* Balances */}
                        {summary && (
                            <div className="grid grid-cols-3 gap-2 mb-4">
                                <BalanceCard label="مستحق له" value={summary.salary_payable.outstanding_debt} color="rose" />
                                <BalanceCard label="سلفة" value={summary.advance.net_balance} color="amber" />
                                <BalanceCard label="عهدة" value={summary.custody.net_balance} color="sky" />
                            </div>
                        )}

                        <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 mb-4">
                            <div className="text-xs text-emerald-900">
                                <strong>صافي المركز:</strong>{" "}
                                <span className={summary?.net_position >= 0 ? "text-emerald-700 font-bold" : "text-rose-700 font-bold"}>
                                    {fmt(summary?.net_position)} ر.س
                                </span>
                            </div>
                            <div className="text-[10px] text-slate-500 mt-1">
                                صافي = (مستحق له) − (سلفة + عهدة) — موجب يعني نحن ندين له، سالب يعني هو مدين لنا.
                            </div>
                        </div>

                        {/* Ledger entries grouped */}
                        <h3 className="text-sm font-extrabold text-slate-800 mb-2">
                            📋 السجل الكامل ({groupList.length} عملية، {entries.length} قيد)
                        </h3>
                        {groupList.length === 0 && (
                            <div className="text-center py-6 text-slate-400 text-xs">لا توجد قيود</div>
                        )}
                        {groupList.map(g => (
                            <div key={g.id} className="border border-slate-200 rounded-lg p-2 mb-2 bg-slate-50"
                                 data-testid={`emp-txn-${g.id.slice(0, 8)}`}>
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
                                                className="text-[10px] text-amber-700 underline mr-2 hover:text-amber-900"
                                                data-testid={`emp-reverse-${e.entry_no}`}>
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

function BalanceCard({ label, value, color }) {
    return (
        <div className={`bg-${color}-50 border border-${color}-200 rounded-lg p-2 text-center`}>
            <div className="text-[10px] text-slate-600 font-bold mb-0.5">{label}</div>
            <div className={`text-base font-extrabold text-${color}-700 num`}>{fmt(value)}</div>
        </div>
    );
}
