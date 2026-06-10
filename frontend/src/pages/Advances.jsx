/**
 * عهد وسلف الموظفين والمندوبين — Iter-104
 *
 * Reuses `liabilities(kind=salary_advance)` + `operating_salaries`.
 * Creating a new advance immediately:
 *   • deducts the amount from the chosen bank,
 *   • posts an `account_transactions` row,
 *   • creates a liability that drops as the next salary is paid
 *     (existing back-end "_apply_open_advances_to_salary" logic).
 *
 * No new collections. No new balance math.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
    HandCoins, Plus, Trash, MagnifyingGlass, UserCircle,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";


const inputCls =
    "w-full px-3 py-2.5 text-sm border border-slate-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-violet-500";

const fmt = (v) =>
    Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const todayIso = () => new Date().toISOString().slice(0, 10);

const STATUS_LABEL = {
    unpaid:  { label: "مفتوحة",      tone: "bg-rose-50 text-rose-800 border-rose-200" },
    partial: { label: "مُسوَّاة جزئياً", tone: "bg-amber-50 text-amber-800 border-amber-200" },
    paid:    { label: "مُسوَّاة",       tone: "bg-emerald-50 text-emerald-800 border-emerald-200" },
};


function CreateForm({ employees, banks, onCreated }) {
    const [form, setForm] = useState({
        employee_salary_id: "",
        paid_from_account_id: "",
        amount: "",
        due_date: todayIso(),
        description: "",
        notes: "",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    const submit = async (e) => {
        e?.preventDefault?.();
        if (!form.employee_salary_id) { toast.error("اختر الموظف"); return; }
        if (!form.paid_from_account_id) { toast.error("اختر البنك"); return; }
        const amt = Number(form.amount);
        if (!amt || amt <= 0) { toast.error("أدخل مبلغاً صحيحاً"); return; }

        setBusy(true);
        try {
            await api.post("/liabilities", {
                kind: "salary_advance",
                employee_salary_id: form.employee_salary_id,
                paid_from_account_id: form.paid_from_account_id,
                expected_amount: amt,
                due_date: form.due_date,
                description: form.description.trim() || "سلفة",
                notes: form.notes.trim(),
            });
            toast.success("تم تسجيل السلفة وخصمها من البنك");
            setForm({ ...form, amount: "", description: "", notes: "" });
            onCreated();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التسجيل");
        } finally { setBusy(false); }
    };

    return (
        <form onSubmit={submit} className="bg-white border border-slate-200 rounded-xl p-5 space-y-3" data-testid="adv-create-form">
            <div className="flex items-center gap-2 pb-3 border-b border-slate-100">
                <Plus size={20} weight="duotone" className="text-violet-700" />
                <h2 className="font-bold text-base text-slate-900">سلفة جديدة</h2>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="sm:col-span-2">
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">الموظف/المندوب *</label>
                    <select value={form.employee_salary_id} onChange={(e) => set("employee_salary_id", e.target.value)} className={inputCls} data-testid="adv-employee">
                        <option value="">— اختر —</option>
                        {employees.map((e) => (
                            <option key={e.id} value={e.id}>
                                {e.name} (راتب: {fmt(e.monthly_amount)} ر.س)
                            </option>
                        ))}
                    </select>
                </div>
                <div>
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">البنك / الصندوق *</label>
                    <select value={form.paid_from_account_id} onChange={(e) => set("paid_from_account_id", e.target.value)} className={inputCls} data-testid="adv-bank">
                        <option value="">— اختر —</option>
                        {banks.map((b) => (
                            <option key={b.id} value={b.id}>{b.name} (الرصيد: {fmt(b.current_balance)})</option>
                        ))}
                    </select>
                </div>
                <div>
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">المبلغ (ر.س) *</label>
                    <input type="number" min="0" step="0.01" value={form.amount} onChange={(e) => set("amount", e.target.value)} className={`${inputCls} num`} data-testid="adv-amount" />
                </div>
                <div>
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">التاريخ *</label>
                    <input type="date" value={form.due_date} onChange={(e) => set("due_date", e.target.value)} className={inputCls} data-testid="adv-date" />
                </div>
                <div>
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">الغرض</label>
                    <input value={form.description} onChange={(e) => set("description", e.target.value)} className={inputCls} placeholder="مثال: مصاريف سفر، شراء أدوات…" data-testid="adv-description" />
                </div>
                <div className="sm:col-span-2">
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">ملاحظات</label>
                    <input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="adv-notes" />
                </div>
            </div>
            <div className="flex justify-end pt-1">
                <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-bold hover:bg-slate-800 disabled:opacity-50" data-testid="adv-submit">
                    {busy ? "جاري الحفظ…" : "تسجيل السلفة"}
                </button>
            </div>
        </form>
    );
}


export default function Advances() {
    const [list, setList] = useState([]);
    const [employees, setEmployees] = useState([]);
    const [banks, setBanks] = useState([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("");

    const load = async () => {
        setLoading(true);
        try {
            const [liabRes, empRes, accRes] = await Promise.all([
                api.get("/liabilities?kind=salary_advance&limit=500"),
                api.get("/operating-expenses/salaries?limit=500"),
                api.get("/accounts?type=bank&limit=200"),
            ]);
            setList(liabRes.data?.items || []);
            setEmployees(empRes.data?.items || empRes.data || []);
            setBanks(accRes.data?.items || accRes.data?.accounts || []);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التحميل");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);

    const empById = useMemo(() => {
        const m = {};
        employees.forEach((e) => { m[e.id] = e; });
        return m;
    }, [employees]);

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        return list.filter((l) => {
            if (statusFilter && l.status !== statusFilter) return false;
            if (q) {
                const emp = empById[l.employee_salary_id];
                const hay = `${l.description || ""} ${emp?.name || ""} ${l.notes || ""}`.toLowerCase();
                if (!hay.includes(q)) return false;
            }
            return true;
        });
    }, [list, statusFilter, search, empById]);

    const totals = useMemo(() => {
        let issued = 0, recovered = 0, outstanding = 0;
        for (const l of filtered) {
            issued     += Number(l.expected_amount || 0);
            recovered  += Number(l.paid_amount || 0);
            outstanding += Math.max(0, Number(l.remaining_amount || 0));
        }
        return { issued, recovered, outstanding };
    }, [filtered]);

    const remove = async (l) => {
        if (!window.confirm("حذف السلفة وإلغاء خصم البنك؟")) return;
        try {
            await api.delete(`/liabilities/${l.id}`);
            toast.success("تم حذف السلفة وإعادة المبلغ للبنك");
            load();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        }
    };

    return (
        <div dir="rtl" data-testid="advances-page" className="space-y-5">
            <div>
                <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                    <HandCoins size={28} weight="duotone" className="text-violet-700" />
                    عهد وسلف الموظفين والمندوبين
                </h1>
                <p className="text-sm text-slate-500 mt-1">
                    تسجيل السلف يخصم تلقائياً من البنك المختار، ويُستردّ المبلغ من راتب الموظف القادم تلقائياً.
                </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                    <div className="text-xs font-bold text-slate-600 mb-1">إجمالي السلف</div>
                    <div className="num text-2xl font-extrabold text-slate-900">{fmt(totals.issued)} ر.س</div>
                </div>
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                    <div className="text-xs font-bold text-emerald-800 mb-1">المُسوَّى</div>
                    <div className="num text-2xl font-extrabold text-emerald-900">{fmt(totals.recovered)} ر.س</div>
                </div>
                <div className="rounded-xl border border-rose-200 bg-rose-50 p-4">
                    <div className="text-xs font-bold text-rose-800 mb-1">المتبقي على الموظفين</div>
                    <div className="num text-2xl font-extrabold text-rose-900">{fmt(totals.outstanding)} ر.س</div>
                </div>
            </div>

            <CreateForm employees={employees.filter((e) => e.category === "employee" && e.status === "active")} banks={banks} onCreated={load} />

            <div className="bg-white border border-slate-200 rounded-xl">
                <div className="p-4 border-b border-slate-100 flex flex-col sm:flex-row gap-3 sm:items-center">
                    <div className="relative sm:w-72">
                        <MagnifyingGlass size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="بحث باسم الموظف أو الغرض…" className={`${inputCls} pr-8`} data-testid="adv-search" />
                    </div>
                    <div className="flex gap-1">
                        {[
                            { v: "",        label: "الكل" },
                            { v: "unpaid",  label: "مفتوحة" },
                            { v: "partial", label: "جزئية" },
                            { v: "paid",    label: "مُسوَّاة" },
                        ].map((b) => (
                            <button key={b.v || "all"} onClick={() => setStatusFilter(b.v)} data-testid={`adv-filter-${b.v || "all"}`}
                                className={`px-3 py-1.5 rounded-lg text-xs font-bold ${statusFilter === b.v ? "bg-slate-900 text-white" : "bg-white border border-slate-200 text-slate-700 hover:bg-slate-50"}`}>
                                {b.label}
                            </button>
                        ))}
                    </div>
                </div>

                {loading ? (
                    <div className="p-10 text-center text-slate-500 text-sm">جاري التحميل…</div>
                ) : filtered.length === 0 ? (
                    <div className="p-10 text-center text-slate-500 text-sm" data-testid="adv-empty">
                        لا توجد سلف. ابدأ بتسجيل سلفة جديدة أعلاه.
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="mezan-table w-full text-sm">
                            <thead className="bg-slate-50 text-slate-600 text-xs">
                                <tr>
                                    <th className="text-right p-3 font-bold">الموظف</th>
                                    <th className="text-right p-3 font-bold">الغرض</th>
                                    <th className="text-right p-3 font-bold">التاريخ</th>
                                    <th className="text-right p-3 font-bold">المبلغ</th>
                                    <th className="text-right p-3 font-bold">المُسوَّى</th>
                                    <th className="text-right p-3 font-bold">المتبقي</th>
                                    <th className="text-right p-3 font-bold">الحالة</th>
                                    <th className="text-right p-3 font-bold w-20">إجراءات</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filtered.map((l) => {
                                    const emp = empById[l.employee_salary_id];
                                    const s = STATUS_LABEL[l.status] || STATUS_LABEL.unpaid;
                                    return (
                                        <tr key={l.id} className="border-t border-slate-100 hover:bg-slate-50/60" data-testid={`adv-row-${l.id}`}>
                                            <td className="p-3 font-bold text-slate-900 flex items-center gap-1.5">
                                                <UserCircle size={16} className="text-slate-400" />
                                                {emp?.name || "—"}
                                            </td>
                                            <td className="p-3 text-xs text-slate-700">{l.description || "—"}</td>
                                            <td className="p-3 text-xs text-slate-600">{l.due_date}</td>
                                            <td className="p-3 num font-bold text-slate-900">{fmt(l.expected_amount)}</td>
                                            <td className="p-3 num text-emerald-700">{fmt(l.paid_amount)}</td>
                                            <td className="p-3 num font-bold text-rose-700">{fmt(l.remaining_amount)}</td>
                                            <td className="p-3"><span className={`px-2 py-0.5 rounded text-[11px] font-bold border ${s.tone}`}>{s.label}</span></td>
                                            <td className="p-3">
                                                <button onClick={() => remove(l)} className="p-1.5 rounded hover:bg-rose-50 text-slate-600 hover:text-rose-700" title="حذف" data-testid={`adv-delete-${l.id}`}>
                                                    <Trash size={14} />
                                                </button>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}
