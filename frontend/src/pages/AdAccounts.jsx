/**
 * Ad-Account Balance & Debt Engine UI — Iter-106
 *
 * Lists every counterparty(kind=ad_account) with:
 *  • current balance         (asset)
 *  • current open debt       (liability — appears in Financial Position)
 *  • lifetime spend
 *  • debt mode (auto/manual) toggle
 *  • Topup / Spend dialogs
 *  • Per-account ledger viewer
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
    ChartLineUp, Plus, Minus, ArrowsClockwise, Clock, ListBullets,
    CheckCircle, Warning,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";


const fmt = (v) =>
    Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const todayIso = () => new Date().toISOString().slice(0, 10);

const PROVIDER_LABEL = {
    snapchat: "Snapchat", tiktok: "TikTok", meta: "Meta",
};

const LEDGER_LABEL = {
    topup:   { label: "تعبئة",            tone: "bg-emerald-50 text-emerald-800 border-emerald-200" },
    spend:   { label: "صرف يومي",         tone: "bg-violet-50 text-violet-800 border-violet-200" },
    debt:    { label: "إنشاء مديونية",    tone: "bg-rose-50 text-rose-800 border-rose-200" },
    manual:  { label: "تعديل يدوي",       tone: "bg-amber-50 text-amber-800 border-amber-200" },
    reverse: { label: "عكس حركة",         tone: "bg-slate-100 text-slate-800 border-slate-200" },
};

const inputCls =
    "w-full px-3 py-2.5 text-sm border border-slate-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-violet-500";


// ── Topup dialog ────────────────────────────────────────────────────
function TopupDialog({ row, banks, open, onClose, onSaved }) {
    const [form, setForm] = useState({
        amount: "", paid_from_account_id: "", transaction_date: todayIso(), notes: "",
    });
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open) return;
        setForm({ amount: "", paid_from_account_id: banks[0]?.id || "",
                  transaction_date: todayIso(), notes: "" });
    }, [open, banks]);

    if (!open || !row) return null;

    const submit = async (e) => {
        e?.preventDefault?.();
        const amt = Number(form.amount);
        if (!amt || amt <= 0) { toast.error("أدخل مبلغاً"); return; }
        if (!form.paid_from_account_id) { toast.error("اختر البنك"); return; }
        setBusy(true);
        try {
            const { data } = await api.post(`/ad-accounts/${row.id}/topup`, {
                ...form, amount: amt,
            });
            const msg = data.applied_to_debt > 0
                ? `سُدِّد ${fmt(data.applied_to_debt)} من المديونية + ${fmt(data.applied_to_balance)} للرصيد`
                : `أُضيف ${fmt(data.applied_to_balance)} للرصيد`;
            toast.success(msg);
            onSaved();
            onClose();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل");
        } finally { setBusy(false); }
    };

    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="adacc-topup-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-md my-8">
                <form onSubmit={submit}>
                    <div className="flex items-center justify-between p-5 border-b border-slate-100">
                        <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                            <Plus size={22} weight="duotone" className="text-emerald-700" />
                            تعبئة رصيد — {row.name}
                        </h2>
                        <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                    </div>
                    <div className="p-5 space-y-3">
                        <div className="text-xs text-slate-600 bg-slate-50 rounded p-3 space-y-1">
                            <div>الرصيد الحالي: <b className="num">{fmt(row.balance)} ر.س</b></div>
                            <div>المديونية الحالية: <b className="num text-rose-700">{fmt(row.open_debt)} ر.س</b></div>
                            {row.open_debt > 0 && (
                                <div className="text-amber-700 mt-1 text-[11px]">
                                    💡 سيُسدَّد من المديونية أولاً قبل إضافة الباقي للرصيد.
                                </div>
                            )}
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">يُخصم من البنك *</label>
                            <select value={form.paid_from_account_id} onChange={(e) => setForm({ ...form, paid_from_account_id: e.target.value })} className={inputCls} data-testid="adacc-topup-bank">
                                <option value="">— اختر —</option>
                                {banks.map((b) => (<option key={b.id} value={b.id}>{b.name} ({fmt(b.current_balance)})</option>))}
                            </select>
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">المبلغ *</label>
                            <input type="number" min="0" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className={`${inputCls} num`} data-testid="adacc-topup-amount" />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">التاريخ</label>
                            <input type="date" value={form.transaction_date} onChange={(e) => setForm({ ...form, transaction_date: e.target.value })} className={inputCls} />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">ملاحظات</label>
                            <input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className={inputCls} />
                        </div>
                    </div>
                    <div className="p-5 border-t border-slate-100 flex justify-end gap-2">
                        <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-bold">إلغاء</button>
                        <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-emerald-700 text-white text-sm font-bold hover:bg-emerald-800 disabled:opacity-50" data-testid="adacc-topup-submit">
                            {busy ? "جاري…" : "تأكيد التعبئة"}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}


// ── Spend dialog ────────────────────────────────────────────────────
function SpendDialog({ row, open, onClose, onSaved }) {
    const [form, setForm] = useState({ amount: "", spend_date: todayIso(), description: "", notes: "" });
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open) return;
        setForm({ amount: "", spend_date: todayIso(), description: "", notes: "" });
    }, [open]);

    if (!open || !row) return null;

    const submit = async (e) => {
        e?.preventDefault?.();
        const amt = Number(form.amount);
        if (!amt || amt <= 0) { toast.error("أدخل مبلغ الصرف"); return; }
        setBusy(true);
        try {
            const { data } = await api.post(`/ad-accounts/${row.id}/spend`, {
                ...form, amount: amt,
            });
            let msg = `خُصِم ${fmt(data.covered_by_balance)} من الرصيد`;
            if (data.debt_created > 0) {
                msg += ` + إنشاء مديونية ${fmt(data.debt_created)}`;
            } else if (data.uncovered > 0) {
                msg += ` (${fmt(data.uncovered)} غير مغطى — وضع يدوي)`;
            }
            toast.success(msg);
            onSaved();
            onClose();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل");
        } finally { setBusy(false); }
    };

    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="adacc-spend-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-md my-8">
                <form onSubmit={submit}>
                    <div className="flex items-center justify-between p-5 border-b border-slate-100">
                        <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                            <Minus size={22} weight="duotone" className="text-rose-700" />
                            تسجيل صرف — {row.name}
                        </h2>
                        <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                    </div>
                    <div className="p-5 space-y-3">
                        <div className="text-xs text-slate-600 bg-slate-50 rounded p-3 space-y-1">
                            <div>الرصيد الحالي: <b className="num text-emerald-700">{fmt(row.balance)}</b></div>
                            <div>الوضع: <b>{row.debt_mode === "auto" ? "تلقائي" : "يدوي"}</b></div>
                            <div className="text-[11px] text-slate-500 mt-1">
                                {row.debt_mode === "auto"
                                    ? "💡 إذا تجاوز الصرف الرصيد، سيُنشأ التزام تلقائياً بالفرق."
                                    : "⚠️ الوضع يدوي — الفرق لن يُسجَّل كدين تلقائياً."}
                            </div>
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">المبلغ *</label>
                            <input type="number" min="0" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className={`${inputCls} num`} data-testid="adacc-spend-amount" />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">تاريخ الصرف</label>
                            <input type="date" value={form.spend_date} onChange={(e) => setForm({ ...form, spend_date: e.target.value })} className={inputCls} />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">الوصف</label>
                            <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className={inputCls} placeholder="حملة X — يوم 5 يونيو" />
                        </div>
                    </div>
                    <div className="p-5 border-t border-slate-100 flex justify-end gap-2">
                        <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-bold">إلغاء</button>
                        <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-rose-700 text-white text-sm font-bold hover:bg-rose-800 disabled:opacity-50" data-testid="adacc-spend-submit">
                            {busy ? "جاري…" : "تسجيل الصرف"}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}


// ── Ledger viewer ────────────────────────────────────────────────────
function LedgerDialog({ row, open, onClose }) {
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (!open || !row) return;
        setLoading(true);
        api.get(`/ad-accounts/${row.id}/ledger`)
            .then((r) => setRows(r.data?.items || []))
            .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
            .finally(() => setLoading(false));
    }, [open, row]);

    if (!open || !row) return null;
    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="adacc-ledger-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-3xl my-8">
                <div className="flex items-center justify-between p-5 border-b border-slate-100">
                    <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                        <ListBullets size={22} weight="duotone" className="text-violet-700" />
                        سجل حركات — {row.name}
                    </h2>
                    <button onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                </div>
                <div className="p-5">
                    {loading ? <div className="text-center text-slate-500 text-sm py-8">جاري التحميل…</div> :
                     rows.length === 0 ? <div className="text-center text-slate-500 text-sm py-8">لا توجد حركات بعد</div> : (
                        <div className="overflow-x-auto border border-slate-200 rounded-lg">
                            <table className="w-full text-sm">
                                <thead className="bg-slate-50 text-slate-600 text-xs">
                                    <tr>
                                        <th className="text-right p-2 font-bold">التاريخ</th>
                                        <th className="text-right p-2 font-bold">النوع</th>
                                        <th className="text-right p-2 font-bold">المبلغ</th>
                                        <th className="text-right p-2 font-bold">الرصيد بعد</th>
                                        <th className="text-right p-2 font-bold">المديونية بعد</th>
                                        <th className="text-right p-2 font-bold">الوصف</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {rows.map((r) => {
                                        const t = LEDGER_LABEL[r.type] || LEDGER_LABEL.manual;
                                        return (
                                            <tr key={r.id} className="border-t border-slate-100" data-testid={`adacc-ledger-row-${r.id}`}>
                                                <td className="p-2 text-xs">{r.date}</td>
                                                <td className="p-2"><span className={`px-2 py-0.5 rounded text-[11px] font-bold border ${t.tone}`}>{t.label}</span></td>
                                                <td className="p-2 num text-xs font-bold text-slate-900">{fmt(r.amount)}</td>
                                                <td className="p-2 num text-xs text-emerald-700">{fmt(r.balance_after)}</td>
                                                <td className="p-2 num text-xs text-rose-700">{fmt(r.debt_after)}</td>
                                                <td className="p-2 text-xs text-slate-600">{r.description || "—"}</td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}


// ── Main page ───────────────────────────────────────────────────────
export default function AdAccounts() {
    const [items, setItems] = useState([]);
    const [totals, setTotals] = useState({ balance: 0, open_debt: 0, total_spend: 0 });
    const [banks, setBanks] = useState([]);
    const [loading, setLoading] = useState(true);
    const [topupFor, setTopupFor] = useState(null);
    const [spendFor, setSpendFor] = useState(null);
    const [ledgerFor, setLedgerFor] = useState(null);

    const load = async () => {
        setLoading(true);
        try {
            const [adRes, accRes] = await Promise.all([
                api.get("/ad-accounts"),
                api.get("/accounts?type=bank&limit=200"),
            ]);
            setItems(adRes.data?.items || []);
            setTotals(adRes.data?.totals || {});
            setBanks(accRes.data?.items || accRes.data?.accounts || []);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التحميل");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);

    const toggleMode = async (row) => {
        const next = row.debt_mode === "auto" ? "manual" : "auto";
        try {
            await api.put(`/ad-accounts/${row.id}/settings`, { debt_mode: next });
            toast.success(`الوضع الآن: ${next === "auto" ? "تلقائي" : "يدوي"}`);
            load();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        }
    };

    return (
        <div dir="rtl" data-testid="ad-accounts-page" className="space-y-5">
            <div>
                <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                    <ChartLineUp size={28} weight="duotone" className="text-violet-700" />
                    الحسابات الإعلانية والمديونية
                </h1>
                <p className="text-sm text-slate-500 mt-1">
                    رصيد المنصة الإعلانية = أصل · مديونية تلقائية تنشأ عند تجاوز الصرف للرصيد. الصرف يبقى مصروف إعلاني واحد دون تكرار.
                </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                    <div className="text-xs font-bold text-emerald-800 mb-1">إجمالي الأرصدة الإعلانية</div>
                    <div className="num text-2xl font-extrabold text-emerald-900">{fmt(totals.balance)} ر.س</div>
                </div>
                <div className="rounded-xl border border-rose-200 bg-rose-50 p-4">
                    <div className="text-xs font-bold text-rose-800 mb-1">إجمالي المديونيات الإعلانية</div>
                    <div className="num text-2xl font-extrabold text-rose-900">{fmt(totals.open_debt)} ر.س</div>
                </div>
                <div className="rounded-xl border border-violet-200 bg-violet-50 p-4">
                    <div className="text-xs font-bold text-violet-800 mb-1">إجمالي الصرف التراكمي</div>
                    <div className="num text-2xl font-extrabold text-violet-900">{fmt(totals.total_spend)} ر.س</div>
                </div>
            </div>

            {loading ? (
                <div className="p-10 text-center text-slate-500 text-sm">جاري التحميل…</div>
            ) : items.length === 0 ? (
                <div className="p-10 text-center text-slate-500 text-sm bg-white border border-slate-200 rounded-xl" data-testid="adacc-empty">
                    لا توجد حسابات إعلانية. أضف الحسابات من <b>قائمة الأطراف الموحَّدة</b> (kind = ad_account).
                </div>
            ) : (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {items.map((row) => (
                        <div key={row.id} className="bg-white border border-slate-200 rounded-xl p-5 space-y-4" data-testid={`adacc-card-${row.id}`}>
                            <div className="flex items-start justify-between gap-2">
                                <div>
                                    <div className="flex items-center gap-2">
                                        <h3 className="font-extrabold text-base text-slate-900">{row.name}</h3>
                                        <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-slate-100 text-slate-700">
                                            {PROVIDER_LABEL[row.ad_provider] || row.ad_provider}
                                        </span>
                                    </div>
                                    {row.notes && <div className="text-xs text-slate-500 mt-1">{row.notes}</div>}
                                </div>
                                <button onClick={() => toggleMode(row)} className={`px-2 py-1 rounded-lg text-[11px] font-bold border ${row.debt_mode === "auto" ? "bg-violet-50 text-violet-800 border-violet-200" : "bg-amber-50 text-amber-800 border-amber-200"}`} title="غيِّر وضع احتساب المديونية" data-testid={`adacc-mode-toggle-${row.id}`}>
                                    {row.debt_mode === "auto" ? "تلقائي" : "يدوي"}
                                </button>
                            </div>

                            <div className="grid grid-cols-3 gap-2">
                                <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 text-center">
                                    <div className="text-[10px] font-bold text-emerald-800">الرصيد</div>
                                    <div className="num text-base font-extrabold text-emerald-900 mt-1">{fmt(row.balance)}</div>
                                </div>
                                <div className="bg-rose-50 border border-rose-200 rounded-lg p-3 text-center">
                                    <div className="text-[10px] font-bold text-rose-800">المديونية</div>
                                    <div className="num text-base font-extrabold text-rose-900 mt-1">{fmt(row.open_debt)}</div>
                                </div>
                                <div className="bg-violet-50 border border-violet-200 rounded-lg p-3 text-center">
                                    <div className="text-[10px] font-bold text-violet-800">الصرف</div>
                                    <div className="num text-base font-extrabold text-violet-900 mt-1">{fmt(row.total_spend)}</div>
                                </div>
                            </div>

                            <div className="space-y-1 text-[11px] text-slate-600 border-t border-slate-100 pt-3">
                                <div className="flex items-center gap-1.5">
                                    <Clock size={11} className="text-slate-400" /> آخر تعبئة:
                                    <b className={row.last_topup ? "text-slate-900" : "text-slate-400"}>
                                        {row.last_topup ? `${row.last_topup.date} (${fmt(row.last_topup.amount)})` : "—"}
                                    </b>
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <Clock size={11} className="text-slate-400" /> آخر صرف:
                                    <b className={row.last_spend ? "text-slate-900" : "text-slate-400"}>
                                        {row.last_spend ? `${row.last_spend.date} (${fmt(row.last_spend.amount)})` : "—"}
                                    </b>
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <Clock size={11} className="text-slate-400" /> آخر مديونية:
                                    <b className={row.last_debt ? "text-rose-900" : "text-slate-400"}>
                                        {row.last_debt ? `${row.last_debt.date} (${fmt(row.last_debt.amount)})` : "—"}
                                    </b>
                                </div>
                            </div>

                            <div className="flex flex-wrap gap-2 pt-2">
                                <button onClick={() => setTopupFor(row)} className="flex-1 min-w-[100px] px-3 py-2 rounded-lg bg-emerald-700 text-white text-xs font-bold hover:bg-emerald-800" data-testid={`adacc-topup-btn-${row.id}`}>
                                    <Plus size={12} className="inline ml-1" /> تعبئة
                                </button>
                                <button onClick={() => setSpendFor(row)} className="flex-1 min-w-[100px] px-3 py-2 rounded-lg bg-rose-700 text-white text-xs font-bold hover:bg-rose-800" data-testid={`adacc-spend-btn-${row.id}`}>
                                    <Minus size={12} className="inline ml-1" /> تسجيل صرف
                                </button>
                                <button onClick={() => setLedgerFor(row)} className="px-3 py-2 rounded-lg bg-slate-100 text-slate-700 text-xs font-bold hover:bg-slate-200" data-testid={`adacc-ledger-btn-${row.id}`}>
                                    <ListBullets size={12} className="inline ml-1" /> السجل
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            <TopupDialog row={topupFor} banks={banks} open={!!topupFor} onClose={() => setTopupFor(null)} onSaved={load} />
            <SpendDialog row={spendFor} open={!!spendFor} onClose={() => setSpendFor(null)} onSaved={load} />
            <LedgerDialog row={ledgerFor} open={!!ledgerFor} onClose={() => setLedgerFor(null)} />
        </div>
    );
}
