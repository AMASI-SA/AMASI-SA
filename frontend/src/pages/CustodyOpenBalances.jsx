// Iter-183 — Custody Open Balances Report
//
// Listing per-employee custody activity, fully reconciled to the
// underlying general_ledger via /api/accounting/employees/custody/open-balances.
//
// Columns mirror the seven custody movement kinds:
//   • opening balance (from migration)
//   • granted (تسليم عهدة)
//   • settled with receipts (تسوية بفواتير)
//   • returned cash (إرجاع نقدي)
//   • transferred IN  (من موظف آخر)
//   • transferred OUT (إلى موظف آخر)
//   • open balance = debits − credits

import React, { useEffect, useState, useMemo } from "react";
import api from "../lib/api";
import { toast } from "sonner";

const fmt = (v) =>
    Number(v || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

export default function CustodyOpenBalances() {
    const [loading, setLoading] = useState(true);
    const [rows, setRows] = useState([]);
    const [total, setTotal] = useState(0);
    const [hideZero, setHideZero] = useState(true);
    const [q, setQ] = useState("");

    const load = async () => {
        setLoading(true);
        try {
            const r = await api.get("/accounting/employees/custody/open-balances");
            setRows(r.data?.rows || []);
            setTotal(r.data?.total_open_balance || 0);
        } catch (e) {
            toast.error("فشل تحميل تقرير أرصدة العهد");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const filtered = useMemo(() => {
        const t = q.trim().toLowerCase();
        return rows.filter(r => {
            if (hideZero && Math.abs(r.open_balance) < 0.01) return false;
            if (t && !(r.name || "").toLowerCase().includes(t)) return false;
            return true;
        });
    }, [rows, q, hideZero]);

    return (
        <div className="p-6 max-w-7xl mx-auto" data-testid="custody-open-balances-page">
            <div className="bg-white rounded-2xl shadow-lg p-6">
                <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
                    <div>
                        <h1 className="text-2xl font-extrabold text-slate-900">
                            🎒 أرصدة العهد المفتوحة
                        </h1>
                        <p className="text-sm text-slate-500 mt-1">
                            رصيد العهدة لكل موظف، مع تفصيل التحركات المسجلة في الأستاذ العام
                            (تسليم — تسوية بفواتير — إرجاع نقدي — نقل بين موظفين).
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={load}
                        className="text-xs font-bold text-slate-600 hover:text-emerald-700 border border-slate-300 hover:border-emerald-400 rounded-lg px-3 py-1.5 transition-colors"
                        data-testid="custody-refresh-btn"
                    >🔄 تحديث</button>
                </div>

                <div className="mt-4 flex flex-wrap items-center gap-3">
                    <input
                        type="text"
                        value={q}
                        onChange={(e) => setQ(e.target.value)}
                        placeholder="🔍 بحث باسم الموظف…"
                        className="px-3 py-2 border border-slate-300 rounded-lg text-sm flex-1 min-w-[200px]"
                        data-testid="custody-search-input"
                    />
                    <label className="flex items-center gap-2 text-sm text-slate-700 font-bold">
                        <input
                            type="checkbox"
                            checked={hideZero}
                            onChange={(e) => setHideZero(e.target.checked)}
                            className="w-4 h-4 accent-emerald-600"
                            data-testid="custody-hide-zero"
                        />
                        إخفاء الأرصدة الصفرية
                    </label>
                    <div className="ms-auto bg-emerald-50 border-2 border-emerald-200 rounded-xl px-4 py-2"
                         data-testid="custody-total-card">
                        <div className="text-[11px] text-emerald-700 font-bold">إجمالي العهد المفتوحة</div>
                        <div className="text-lg font-extrabold text-emerald-900 num">
                            {fmt(total)} ر.س
                        </div>
                    </div>
                </div>

                {/* Table */}
                <div className="mt-5 overflow-x-auto border border-slate-200 rounded-xl">
                    <table className="w-full text-xs" data-testid="custody-table">
                        <thead className="bg-slate-100 text-slate-700">
                            <tr>
                                <th className="text-right p-2.5 font-extrabold">الموظف</th>
                                <th className="text-left p-2.5 font-extrabold">رصيد افتتاحي</th>
                                <th className="text-left p-2.5 font-extrabold">عهد مستلمة</th>
                                <th className="text-left p-2.5 font-extrabold">مسوّاة بفواتير</th>
                                <th className="text-left p-2.5 font-extrabold">مرتجع نقداً</th>
                                <th className="text-left p-2.5 font-extrabold">منقول إليه</th>
                                <th className="text-left p-2.5 font-extrabold">منقول منه</th>
                                <th className="text-left p-2.5 font-extrabold bg-emerald-100">
                                    الرصيد المفتوح
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {loading && (
                                <tr><td colSpan={8} className="text-center p-6 text-slate-500">
                                    جاري التحميل…
                                </td></tr>
                            )}
                            {!loading && filtered.length === 0 && (
                                <tr><td colSpan={8} className="text-center p-6 text-slate-500"
                                        data-testid="custody-empty-row">
                                    {rows.length === 0
                                        ? "لا توجد عهد مسجّلة بعد."
                                        : "لا توجد نتائج مطابقة للبحث الحالي."}
                                </td></tr>
                            )}
                            {!loading && filtered.map((r) => {
                                const ob = r.open_balance;
                                const obColor =
                                    ob > 0.01 ? "text-emerald-700"
                                    : ob < -0.01 ? "text-rose-700"
                                    : "text-slate-400";
                                return (
                                    <tr key={r.employee_id} className="border-t border-slate-100 hover:bg-slate-50"
                                        data-testid={`custody-row-${r.employee_id}`}>
                                        <td className="p-2.5 font-bold text-slate-800">{r.name}</td>
                                        <td className="p-2.5 text-left num text-slate-600">{fmt(r.opening)}</td>
                                        <td className="p-2.5 text-left num text-slate-700">{fmt(r.granted)}</td>
                                        <td className="p-2.5 text-left num text-slate-700">{fmt(r.settled_receipts)}</td>
                                        <td className="p-2.5 text-left num text-slate-700">{fmt(r.returned_cash)}</td>
                                        <td className="p-2.5 text-left num text-emerald-700">{fmt(r.transferred_in)}</td>
                                        <td className="p-2.5 text-left num text-rose-700">{fmt(r.transferred_out)}</td>
                                        <td className={`p-2.5 text-left num font-extrabold bg-emerald-50/50 ${obColor}`}
                                            data-testid={`custody-open-balance-${r.employee_id}`}>
                                            {fmt(ob)}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                        {!loading && filtered.length > 0 && (
                            <tfoot className="bg-slate-100 font-extrabold text-slate-800">
                                <tr>
                                    <td className="p-2.5 text-right">الإجمالي ({filtered.length} موظف)</td>
                                    <td className="p-2.5 text-left num">
                                        {fmt(filtered.reduce((s, r) => s + r.opening, 0))}
                                    </td>
                                    <td className="p-2.5 text-left num">
                                        {fmt(filtered.reduce((s, r) => s + r.granted, 0))}
                                    </td>
                                    <td className="p-2.5 text-left num">
                                        {fmt(filtered.reduce((s, r) => s + r.settled_receipts, 0))}
                                    </td>
                                    <td className="p-2.5 text-left num">
                                        {fmt(filtered.reduce((s, r) => s + r.returned_cash, 0))}
                                    </td>
                                    <td className="p-2.5 text-left num text-emerald-800">
                                        {fmt(filtered.reduce((s, r) => s + r.transferred_in, 0))}
                                    </td>
                                    <td className="p-2.5 text-left num text-rose-800">
                                        {fmt(filtered.reduce((s, r) => s + r.transferred_out, 0))}
                                    </td>
                                    <td className="p-2.5 text-left num text-emerald-900 bg-emerald-100">
                                        {fmt(filtered.reduce((s, r) => s + r.open_balance, 0))}
                                    </td>
                                </tr>
                            </tfoot>
                        )}
                    </table>
                </div>

                <div className="mt-4 text-[11px] text-slate-500 leading-relaxed">
                    💡 <strong>الرصيد المفتوح</strong> = إجمالي المدين − إجمالي الدائن لحساب
                    «عهدة الموظف» في الأستاذ العام. الأرقام المفصّلة في الأعمدة الستة (مستلمة /
                    مسوّاة بفواتير / مرتجع / المنقول) تساعدك على تفسير مصدر الرصيد، أما
                    العمود الأخير فهو القيمة الحقيقية المستحقة على الموظف.
                </div>
            </div>
        </div>
    );
}
