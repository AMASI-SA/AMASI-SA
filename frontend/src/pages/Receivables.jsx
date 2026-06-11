/**
 * الذمم المدينة والتحصيلات — Iter-104
 *
 * Reuses `liabilities(kind=receivable)` + the new
 * `POST /api/liabilities/{id}/collect` (Iter-104 backend) which
 * credits a bank account with the collected amount.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
    Coin, Plus, Trash, MagnifyingGlass, ArrowsDownUp,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";
import { todaySA } from "../lib/dates";


const inputCls =
    "w-full px-3 py-2.5 text-sm border border-slate-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-violet-500";

const fmt = (v) =>
    Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const todayIso = () => todaySA();

const STATUS_LABEL = {
    unpaid:  { label: "مفتوحة",      tone: "bg-rose-50 text-rose-800 border-rose-200" },
    partial: { label: "محصَّلة جزئياً", tone: "bg-amber-50 text-amber-800 border-amber-200" },
    paid:    { label: "محصَّلة",       tone: "bg-emerald-50 text-emerald-800 border-emerald-200" },
};


function CreateForm({ onCreated }) {
    const [form, setForm] = useState({
        counterparty_name: "",
        counterparty_type: "person",
        expected_amount: "",
        due_date: todayIso(),
        description: "",
        notes: "",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    const submit = async (e) => {
        e?.preventDefault?.();
        if (!form.counterparty_name.trim()) { toast.error("أدخل اسم العميل/الجهة"); return; }
        const amt = Number(form.expected_amount);
        if (!amt || amt <= 0) { toast.error("أدخل مبلغاً صحيحاً"); return; }
        setBusy(true);
        try {
            await api.post("/liabilities", {
                kind: "receivable",
                counterparty_name: form.counterparty_name.trim(),
                counterparty_type: form.counterparty_type,
                expected_amount: amt,
                due_date: form.due_date,
                description: form.description.trim() || "ذمة مدينة",
                notes: form.notes.trim(),
            });
            toast.success("تم تسجيل الذمة المدينة");
            setForm({ ...form, counterparty_name: "", expected_amount: "", description: "", notes: "" });
            onCreated();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التسجيل");
        } finally { setBusy(false); }
    };

    return (
        <form onSubmit={submit} className="bg-white border border-slate-200 rounded-xl p-5 space-y-3" data-testid="recv-create-form">
            <div className="flex items-center gap-2 pb-3 border-b border-slate-100">
                <Plus size={20} weight="duotone" className="text-violet-700" />
                <h2 className="font-bold text-base text-slate-900">ذمة مدينة جديدة</h2>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">اسم العميل/الجهة *</label>
                    <input value={form.counterparty_name} onChange={(e) => set("counterparty_name", e.target.value)} className={inputCls} placeholder="أحمد محمد، شركة س" data-testid="recv-counterparty" />
                </div>
                <div>
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">النوع</label>
                    <select value={form.counterparty_type} onChange={(e) => set("counterparty_type", e.target.value)} className={inputCls} data-testid="recv-type">
                        <option value="person">شخص</option>
                        <option value="company">شركة</option>
                        <option value="employee">موظف</option>
                    </select>
                </div>
                <div>
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">المبلغ المستحق (ر.س) *</label>
                    <input type="number" min="0" step="0.01" value={form.expected_amount} onChange={(e) => set("expected_amount", e.target.value)} className={`${inputCls} num`} data-testid="recv-amount" />
                </div>
                <div>
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">تاريخ الاستحقاق</label>
                    <input type="date" value={form.due_date} onChange={(e) => set("due_date", e.target.value)} className={inputCls} data-testid="recv-due" />
                </div>
                <div className="sm:col-span-2">
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">الغرض/الفاتورة</label>
                    <input value={form.description} onChange={(e) => set("description", e.target.value)} className={inputCls} placeholder="مثال: بيع نقدي مؤجل، فاتورة 1234" data-testid="recv-description" />
                </div>
                <div className="sm:col-span-2">
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">ملاحظات</label>
                    <input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="recv-notes" />
                </div>
            </div>
            <div className="flex justify-end pt-1">
                <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-bold hover:bg-slate-800 disabled:opacity-50" data-testid="recv-submit">
                    {busy ? "جاري الحفظ…" : "تسجيل الذمة"}
                </button>
            </div>
        </form>
    );
}


function CollectDialog({ row, banks, open, onClose, onSaved }) {
    const [form, setForm] = useState({
        paid_from_account_id: "",
        amount: "",
        payment_date: todayIso(),
        notes: "",
    });
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open || !row) return;
        setForm({
            paid_from_account_id: banks[0]?.id || "",
            amount: String(row.remaining_amount || ""),
            payment_date: todayIso(),
            notes: "",
        });
    }, [open, row, banks]);

    if (!open || !row) return null;

    const submit = async (e) => {
        e?.preventDefault?.();
        if (!form.paid_from_account_id) { toast.error("اختر البنك"); return; }
        const amt = Number(form.amount);
        if (!amt || amt <= 0) { toast.error("أدخل مبلغاً"); return; }
        setBusy(true);
        try {
            await api.post(`/liabilities/${row.id}/collect`, {
                amount: amt,
                paid_from_account_id: form.paid_from_account_id,
                payment_date: form.payment_date,
                notes: form.notes.trim(),
            });
            toast.success(`تم تحصيل ${fmt(amt)} ر.س وإضافتها للبنك`);
            onSaved();
            onClose();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التحصيل");
        } finally { setBusy(false); }
    };

    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="recv-collect-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-md my-8">
                <form onSubmit={submit}>
                    <div className="flex items-center justify-between p-5 border-b border-slate-100">
                        <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                            <ArrowsDownUp size={22} weight="duotone" className="text-emerald-700" />
                            تحصيل من {row.counterparty_name}
                        </h2>
                        <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                    </div>
                    <div className="p-5 space-y-3">
                        <div className="text-xs text-slate-600 bg-slate-50 rounded p-3 space-y-1">
                            <div>المبلغ الأصلي: <b className="num">{fmt(row.expected_amount)}</b> ر.س</div>
                            <div>المحصَّل سابقاً: <b className="num text-emerald-700">{fmt(row.paid_amount)}</b> ر.س</div>
                            <div>المتبقي: <b className="num text-rose-700">{fmt(row.remaining_amount)}</b> ر.س</div>
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">يُضاف إلى البنك *</label>
                            <select value={form.paid_from_account_id} onChange={(e) => setForm({ ...form, paid_from_account_id: e.target.value })} className={inputCls} data-testid="recv-collect-bank">
                                <option value="">— اختر —</option>
                                {banks.map((b) => (
                                    <option key={b.id} value={b.id}>{b.name} (الرصيد: {fmt(b.current_balance)})</option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">المبلغ (ر.س) *</label>
                            <input type="number" min="0" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className={`${inputCls} num`} data-testid="recv-collect-amount" />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">تاريخ التحصيل</label>
                            <input type="date" value={form.payment_date} onChange={(e) => setForm({ ...form, payment_date: e.target.value })} className={inputCls} data-testid="recv-collect-date" />
                        </div>
                    </div>
                    <div className="p-5 border-t border-slate-100 flex justify-end gap-2">
                        <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-bold">إلغاء</button>
                        <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-emerald-700 text-white text-sm font-bold hover:bg-emerald-800 disabled:opacity-50" data-testid="recv-collect-submit">
                            {busy ? "جاري…" : "تأكيد التحصيل"}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}


export default function Receivables() {
    const [list, setList] = useState([]);
    const [banks, setBanks] = useState([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("");
    const [collectFor, setCollectFor] = useState(null);

    const load = async () => {
        setLoading(true);
        try {
            const [liabRes, accRes] = await Promise.all([
                api.get("/liabilities?kind=receivable&limit=500"),
                api.get("/accounts?type=bank&limit=200"),
            ]);
            setList(liabRes.data?.items || []);
            setBanks(accRes.data?.items || accRes.data?.accounts || []);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التحميل");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        return list.filter((l) => {
            if (statusFilter && l.status !== statusFilter) return false;
            if (q) {
                const hay = `${l.counterparty_name || ""} ${l.description || ""} ${l.notes || ""}`.toLowerCase();
                if (!hay.includes(q)) return false;
            }
            return true;
        });
    }, [list, statusFilter, search]);

    const totals = useMemo(() => {
        let invoiced = 0, collected = 0, remaining = 0;
        for (const l of filtered) {
            invoiced += Number(l.expected_amount || 0);
            collected += Number(l.paid_amount || 0);
            remaining += Number(l.remaining_amount || 0);
        }
        return { invoiced, collected, remaining };
    }, [filtered]);

    const remove = async (l) => {
        if (Number(l.paid_amount || 0) > 0) {
            toast.error("لا يمكن حذف ذمة محصَّلة جزئياً أو كلياً");
            return;
        }
        if (!window.confirm(`حذف الذمة المدينة لـ ${l.counterparty_name}؟`)) return;
        try {
            await api.delete(`/liabilities/${l.id}`);
            toast.success("تم الحذف");
            load();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        }
    };

    return (
        <div dir="rtl" data-testid="receivables-page" className="space-y-5">
            <div>
                <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                    <Coin size={28} weight="duotone" className="text-violet-700" />
                    الذمم المدينة والتحصيلات
                </h1>
                <p className="text-sm text-slate-500 mt-1">
                    الذمم المدينة = أموال مستحقَّة لك على عملاء أو جهات. التحصيل يُضاف فوراً إلى البنك المختار.
                </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                    <div className="text-xs font-bold text-slate-600 mb-1">إجمالي الذمم</div>
                    <div className="num text-2xl font-extrabold text-slate-900">{fmt(totals.invoiced)} ر.س</div>
                </div>
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                    <div className="text-xs font-bold text-emerald-800 mb-1">المحصَّل</div>
                    <div className="num text-2xl font-extrabold text-emerald-900">{fmt(totals.collected)} ر.س</div>
                </div>
                <div className="rounded-xl border border-rose-200 bg-rose-50 p-4">
                    <div className="text-xs font-bold text-rose-800 mb-1">المتبقي للتحصيل</div>
                    <div className="num text-2xl font-extrabold text-rose-900">{fmt(totals.remaining)} ر.س</div>
                </div>
            </div>

            <CreateForm onCreated={load} />

            <div className="bg-white border border-slate-200 rounded-xl">
                <div className="p-4 border-b border-slate-100 flex flex-col sm:flex-row gap-3 sm:items-center">
                    <div className="relative sm:w-72">
                        <MagnifyingGlass size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="بحث باسم العميل أو الغرض…" className={`${inputCls} pr-8`} data-testid="recv-search" />
                    </div>
                    <div className="flex gap-1">
                        {[
                            { v: "",        label: "الكل" },
                            { v: "unpaid",  label: "مفتوحة" },
                            { v: "partial", label: "جزئية" },
                            { v: "paid",    label: "محصَّلة" },
                        ].map((b) => (
                            <button key={b.v || "all"} onClick={() => setStatusFilter(b.v)} data-testid={`recv-filter-${b.v || "all"}`}
                                className={`px-3 py-1.5 rounded-lg text-xs font-bold ${statusFilter === b.v ? "bg-slate-900 text-white" : "bg-white border border-slate-200 text-slate-700 hover:bg-slate-50"}`}>
                                {b.label}
                            </button>
                        ))}
                    </div>
                </div>

                {loading ? (
                    <div className="p-10 text-center text-slate-500 text-sm">جاري التحميل…</div>
                ) : filtered.length === 0 ? (
                    <div className="p-10 text-center text-slate-500 text-sm" data-testid="recv-empty">
                        لا توجد ذمم. ابدأ بإضافة ذمة جديدة أعلاه.
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="mezan-table w-full text-sm">
                            <thead className="bg-slate-50 text-slate-600 text-xs">
                                <tr>
                                    <th className="text-right p-3 font-bold">العميل/الجهة</th>
                                    <th className="text-right p-3 font-bold">الغرض</th>
                                    <th className="text-right p-3 font-bold">الاستحقاق</th>
                                    <th className="text-right p-3 font-bold">المبلغ</th>
                                    <th className="text-right p-3 font-bold">المحصَّل</th>
                                    <th className="text-right p-3 font-bold">المتبقي</th>
                                    <th className="text-right p-3 font-bold">الحالة</th>
                                    <th className="text-right p-3 font-bold w-32">إجراءات</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filtered.map((l) => {
                                    const s = STATUS_LABEL[l.status] || STATUS_LABEL.unpaid;
                                    const collectable = (l.remaining_amount || 0) > 0;
                                    return (
                                        <tr key={l.id} className="border-t border-slate-100 hover:bg-slate-50/60" data-testid={`recv-row-${l.id}`}>
                                            <td className="p-3 font-bold text-slate-900">{l.counterparty_name}</td>
                                            <td className="p-3 text-xs text-slate-700">{l.description || "—"}</td>
                                            <td className={`p-3 text-xs ${l.is_overdue ? "text-rose-700 font-bold" : "text-slate-600"}`}>
                                                {l.due_date}
                                                {l.is_overdue && <span className="mr-1">⚠️</span>}
                                            </td>
                                            <td className="p-3 num font-bold text-slate-900">{fmt(l.expected_amount)}</td>
                                            <td className="p-3 num text-emerald-700">{fmt(l.paid_amount)}</td>
                                            <td className="p-3 num font-bold text-rose-700">{fmt(l.remaining_amount)}</td>
                                            <td className="p-3"><span className={`px-2 py-0.5 rounded text-[11px] font-bold border ${s.tone}`}>{s.label}</span></td>
                                            <td className="p-3">
                                                <div className="flex items-center gap-1">
                                                    {collectable && (
                                                        <button onClick={() => setCollectFor(l)} className="px-2 py-1 rounded text-[11px] font-bold bg-emerald-700 text-white hover:bg-emerald-800" data-testid={`recv-collect-${l.id}`}>
                                                            تحصيل
                                                        </button>
                                                    )}
                                                    <button onClick={() => remove(l)} className="p-1.5 rounded hover:bg-rose-50 text-slate-600 hover:text-rose-700" title="حذف" data-testid={`recv-delete-${l.id}`} disabled={Number(l.paid_amount || 0) > 0}>
                                                        <Trash size={14} />
                                                    </button>
                                                </div>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            <CollectDialog row={collectFor} banks={banks} open={!!collectFor} onClose={() => setCollectFor(null)} onSaved={load} />
        </div>
    );
}
