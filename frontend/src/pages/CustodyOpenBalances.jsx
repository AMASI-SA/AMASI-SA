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

// P1.5.s.fix.custody — Auto badge for the employee net status.
function NetStatusBadge({ status }) {
    const cfg = {
        owed_to_employee:  { label: "له علينا",     cls: "bg-emerald-100 text-emerald-800 border-emerald-300" },
        owed_by_employee:  { label: "عليه للنظام", cls: "bg-rose-100 text-rose-800 border-rose-300" },
        balanced:          { label: "متوازن",       cls: "bg-slate-100 text-slate-600 border-slate-300" },
    }[status] || { label: status || "—", cls: "bg-slate-100 text-slate-500 border-slate-200" };
    return (
        <span
            className={"inline-block text-[10px] px-1.5 py-0.5 rounded border font-bold " + cfg.cls}
            data-testid={"net-status-" + (status || "none")}
        >
            {cfg.label}
        </span>
    );
}

// P1.5.s.fix.activity — Clickable summary card used in the
// custody page header. Active = a filter is currently applied.
function StatCard({ label, value, sub, tone, active, onClick, testid }) {
    const palette = {
        emerald: "bg-emerald-50 border-emerald-200 text-emerald-900",
        rose:    "bg-rose-50 border-rose-200 text-rose-900",
        slate:   "bg-slate-50 border-slate-200 text-slate-700",
        indigo:  "bg-indigo-50 border-indigo-200 text-indigo-900",
        amber:   "bg-amber-50 border-amber-200 text-amber-900",
    }[tone] || "bg-slate-50 border-slate-200 text-slate-700";
    const ring = active ? " ring-2 ring-offset-1 ring-indigo-500" : "";
    return (
        <button
            type="button"
            onClick={onClick}
            className={"text-right rounded-xl border px-3 py-2 hover:brightness-95 transition cursor-pointer " + palette + ring}
            data-testid={testid}
        >
            <div className="text-[11px] font-bold opacity-80">{label}</div>
            <div className="text-xl font-extrabold mt-0.5 num">{value}</div>
            {sub && <div className="text-[10px] font-mono opacity-70 mt-0.5">{sub}</div>}
        </button>
    );
}

// P1.5.s.fix.activity — Activity (active <=90d) badge.
function ActivityBadge({ active, days }) {
    if (active) {
        return (
            <span className="inline-block text-[10px] px-1.5 py-0.5 rounded border font-bold bg-emerald-100 text-emerald-800 border-emerald-300"
                  title={"آخر حركة: " + (days ?? "?") + " يوم"}>
                🟢 نشط
            </span>
        );
    }
    return (
        <span className="inline-block text-[10px] px-1.5 py-0.5 rounded border font-bold bg-slate-100 text-slate-600 border-slate-300"
              title={days != null ? (days + " يوم منذ آخر حركة") : "لا توجد حركات"}>
            ⚪ {days != null ? "خامل" : "بلا حركة"}
        </span>
    );
}

function ymd(v) {
    if (!v) return "—";
    const s = String(v);
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
    const d = new Date(s);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toISOString().slice(0, 10);
}

export default function CustodyOpenBalances() {
    const [loading, setLoading] = useState(true);
    const [rows, setRows] = useState([]);
    const [total, setTotal] = useState(0);
    const [totalSalary, setTotalSalary] = useState(0);
    const [totalAdvance, setTotalAdvance] = useState(0);
    const [totalNet, setTotalNet] = useState(0);
    const [hideZero, setHideZero] = useState(true);
    const [q, setQ] = useState("");
    // P1.5.s.fix.activity — Daily-monitoring filters + sort.
    const [statusFilter, setStatusFilter] = useState("all");
    const [hasCustody, setHasCustody] = useState(false);
    const [hasAdvance, setHasAdvance] = useState(false);
    const [sortKey, setSortKey] = useState("net_owed");

    const load = async () => {
        setLoading(true);
        try {
            const r = await api.get("/accounting/employees/custody/open-balances");
            setRows(r.data?.rows || []);
            setTotal(r.data?.total_open_balance || 0);
            setTotalSalary(r.data?.total_salary_owed || 0);
            setTotalAdvance(r.data?.total_advance_open || 0);
            setTotalNet(r.data?.total_net_to_employee || 0);
        } catch (e) {
            toast.error("فشل تحميل تقرير أرصدة العهد");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const filtered = useMemo(() => {
        const t = q.trim().toLowerCase();
        const rs = rows.filter(r => {
            if (hideZero
                && Math.abs(r.open_balance) < 0.01
                && Math.abs(r.salary_owed || 0) < 0.01
                && Math.abs(r.advance_open || 0) < 0.01) return false;
            if (statusFilter !== "all" && r.net_status !== statusFilter) return false;
            if (hasCustody && !(Math.abs(r.open_balance) > 0.01)) return false;
            if (hasAdvance && !((r.advance_open || 0) > 0.01)) return false;
            if (t && !(r.name || "").toLowerCase().includes(t)) return false;
            return true;
        });
        const sorters = {
            name: (a, b) => (a.name || "").localeCompare(b.name || "", "ar"),
            salary: (a, b) => (b.salary_owed || 0) - (a.salary_owed || 0),
            advance: (a, b) => (b.advance_open || 0) - (a.advance_open || 0),
            custody: (a, b) => (b.open_balance || 0) - (a.open_balance || 0),
            net_owed:    (a, b) => (b.net_to_employee || 0) - (a.net_to_employee || 0),
            net_owing:   (a, b) => (a.net_to_employee || 0) - (b.net_to_employee || 0),
        };
        return [...rs].sort(sorters[sortKey] || sorters.net_owed);
    }, [rows, q, hideZero, statusFilter, hasCustody, hasAdvance, sortKey]);

    // P1.5.s.fix.activity — Counters for the clickable summary cards.
    const counts = useMemo(() => {
        const c = {
            owed_to_employee: 0,
            owed_by_employee: 0,
            balanced: 0,
            with_custody: 0,
            with_advance: 0,
        };
        rows.forEach(r => {
            if (r.net_status === "owed_to_employee") c.owed_to_employee += 1;
            else if (r.net_status === "owed_by_employee") c.owed_by_employee += 1;
            else c.balanced += 1;
            if (Math.abs(r.open_balance || 0) > 0.01) c.with_custody += 1;
            if ((r.advance_open || 0) > 0.01) c.with_advance += 1;
        });
        return c;
    }, [rows]);

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

                {/* P1.5.s.fix.custody — Salary side panel roll-ups. */}
                <div className="mt-4 grid grid-cols-2 md:grid-cols-5 gap-3"
                     data-testid="custody-salary-summary">
                    <StatCard label="🟢 له علينا" value={counts.owed_to_employee}
                              sub={fmt(totalNet > 0 ? totalNet : 0) + " ر.س"}
                              tone="emerald"
                              active={statusFilter === "owed_to_employee"}
                              onClick={() => setStatusFilter(statusFilter === "owed_to_employee" ? "all" : "owed_to_employee")}
                              testid="card-owed-to" />
                    <StatCard label="🔴 عليه للنظام" value={counts.owed_by_employee}
                              sub={fmt(totalNet < 0 ? -totalNet : 0) + " ر.س"}
                              tone="rose"
                              active={statusFilter === "owed_by_employee"}
                              onClick={() => setStatusFilter(statusFilter === "owed_by_employee" ? "all" : "owed_by_employee")}
                              testid="card-owed-by" />
                    <StatCard label="⚪ متوازنون" value={counts.balanced}
                              tone="slate"
                              active={statusFilter === "balanced"}
                              onClick={() => setStatusFilter(statusFilter === "balanced" ? "all" : "balanced")}
                              testid="card-balanced" />
                    <StatCard label="🎒 عهد مفتوحة" value={counts.with_custody}
                              sub={fmt(total) + " ر.س"}
                              tone="indigo"
                              active={hasCustody}
                              onClick={() => setHasCustody(v => !v)}
                              testid="card-with-custody" />
                    <StatCard label="💰 سلف مفتوحة" value={counts.with_advance}
                              sub={fmt(totalAdvance) + " ر.س"}
                              tone="amber"
                              active={hasAdvance}
                              onClick={() => setHasAdvance(v => !v)}
                              testid="card-with-advance" />
                </div>

                {/* P1.5.s.fix.activity — Sort selector + extra totals row */}
                <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
                    <select
                        value={sortKey}
                        onChange={(e) => setSortKey(e.target.value)}
                        className="border rounded-lg px-3 py-2 font-bold bg-indigo-50 border-indigo-200 text-indigo-800"
                        data-testid="custody-sort-key"
                    >
                        <option value="net_owed">↕ ترتيب: الأعلى صافي علينا</option>
                        <option value="net_owing">↕ ترتيب: الأعلى صافي عليهم</option>
                        <option value="salary">↕ ترتيب: الأعلى استحقاقاً</option>
                        <option value="advance">↕ ترتيب: الأعلى سلفة</option>
                        <option value="custody">↕ ترتيب: الأعلى عهدة</option>
                        <option value="name">↕ ترتيب: أبجدي</option>
                    </select>
                    <span className="text-slate-500">
                        إجمالي الرواتب: <b className="text-indigo-800">{fmt(totalSalary)} ر.س</b>
                    </span>
                    <span className="text-slate-500">
                        إجمالي السلف: <b className="text-amber-800">{fmt(totalAdvance)} ر.س</b>
                    </span>
                    <span className="text-slate-500">
                        صافي الموظفين: <b className={totalNet > 0 ? "text-emerald-800" : totalNet < 0 ? "text-rose-800" : "text-slate-600"}>
                            {fmt(totalNet)} ر.س
                        </b>
                    </span>
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
                                {/* P1.5.s.fix.custody — Salary side panel. */}
                                <th className="text-left p-2.5 font-extrabold bg-indigo-100">
                                    الراتب المستحق
                                </th>
                                <th className="text-left p-2.5 font-extrabold bg-amber-100">
                                    سلف مفتوحة
                                </th>
                                <th className="text-left p-2.5 font-extrabold bg-violet-100">
                                    صافي الموظف
                                </th>
                                <th className="text-right p-2.5 font-extrabold bg-violet-50">
                                    الحالة
                                </th>
                                <th className="text-right p-2.5 font-extrabold">آخر حركة</th>
                                <th className="text-right p-2.5 font-extrabold">نشاط</th>
                            </tr>
                        </thead>
                        <tbody>
                            {loading && (
                                <tr><td colSpan={14} className="text-center p-6 text-slate-500">
                                    جاري التحميل…
                                </td></tr>
                            )}
                            {!loading && filtered.length === 0 && (
                                <tr><td colSpan={14} className="text-center p-6 text-slate-500"
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
                                const net = Number(r.net_to_employee || 0);
                                const netColor =
                                    net > 0.01 ? "text-emerald-800"
                                    : net < -0.01 ? "text-rose-800"
                                    : "text-slate-500";
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
                                        <td className="p-2.5 text-left num font-bold text-indigo-800 bg-indigo-50/40"
                                            data-testid={`custody-salary-${r.employee_id}`}>
                                            {fmt(r.salary_owed)}
                                        </td>
                                        <td className="p-2.5 text-left num font-bold text-amber-800 bg-amber-50/40"
                                            data-testid={`custody-advance-${r.employee_id}`}>
                                            {fmt(r.advance_open)}
                                        </td>
                                        <td className={`p-2.5 text-left num font-extrabold bg-violet-50/40 ${netColor}`}
                                            data-testid={`custody-net-${r.employee_id}`}>
                                            {fmt(net)}
                                        </td>
                                        <td className="p-2.5 text-right">
                                            <NetStatusBadge status={r.net_status} />
                                        </td>
                                        <td className="p-2.5 text-right text-[11px] whitespace-nowrap font-mono">
                                            {ymd(r.last_activity)}
                                        </td>
                                        <td className="p-2.5 text-right">
                                            <ActivityBadge active={r.is_active}
                                                           days={r.days_since_last_activity} />
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
                                    <td className="p-2.5 text-left num text-indigo-900 bg-indigo-100">
                                        {fmt(filtered.reduce((s, r) => s + (r.salary_owed || 0), 0))}
                                    </td>
                                    <td className="p-2.5 text-left num text-amber-900 bg-amber-100">
                                        {fmt(filtered.reduce((s, r) => s + (r.advance_open || 0), 0))}
                                    </td>
                                    <td className="p-2.5 text-left num text-violet-900 bg-violet-100">
                                        {fmt(filtered.reduce((s, r) => s + (r.net_to_employee || 0), 0))}
                                    </td>
                                    <td className="p-2.5"></td>
                                    <td className="p-2.5"></td>
                                    <td className="p-2.5"></td>
                                </tr>
                            </tfoot>
                        )}
                    </table>
                </div>

                <div className="mt-4 text-[11px] text-slate-500 leading-relaxed space-y-1">
                    <div>
                        💡 <strong>الرصيد المفتوح</strong> = إجمالي المدين − إجمالي الدائن لحساب
                        «عهدة الموظف» في الأستاذ العام.
                    </div>
                    <div>
                        📒 <strong>صافي الموظف</strong> = الراتب المستحق − السلف المفتوحة.
                        <span className="font-bold text-violet-800"> العهدة لا تُخصم من صافي الراتب</span>
                        — تظهر كرصيد مستقل لأنها أصلٌ تشغيلي وليست استحقاقاً.
                    </div>
                </div>
            </div>
        </div>
    );
}
