import { useCallback, useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
    Plus, Bank, CreditCard, Megaphone, Wallet, ArrowLeft, Trash, X, EyeSlash,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const TYPE_META = {
    bank:             { label: "حساب بنكي",  icon: Bank },
    payment_platform: { label: "منصة دفع",   icon: CreditCard },
    ads_platform:     { label: "حساب إعلاني", icon: Megaphone },
};

const STATUS_META = {
    active:   { label: "نشط",   cls: "bg-emerald-100 text-emerald-700" },
    hidden:   { label: "مخفي",  cls: "bg-slate-100 text-slate-600" },
    inactive: { label: "موقوف", cls: "bg-amber-100 text-amber-700" },
};

const inputCls =
    "w-full px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand focus:border-brand transition";

const fmt = (v, ccy = "SAR") =>
    `${Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${ccy === "SAR" ? "ر.س" : ccy}`;

// ── Add transaction modal ────────────────────────────────────────────────────
function AddTxModal({ accountId, catalogue, onClose, onSaved }) {
    const today = new Date().toISOString().slice(0, 10);
    const [form, setForm] = useState({
        transaction_type: "income",
        amount: "",
        direction: "in",
        description: "",
        transaction_date: today,
        status: "posted",
    });
    const [busy, setBusy] = useState(false);

    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    const submit = async (e) => {
        e.preventDefault();
        const amt = parseFloat(form.amount);
        if (!amt || amt <= 0) return toast.error("المبلغ يجب أن يكون أكبر من صفر");
        setBusy(true);
        try {
            await api.post(`/accounts/${accountId}/transactions`, {
                transaction_type: form.transaction_type,
                amount: amt,
                direction: form.direction,
                description: form.description.trim(),
                transaction_date: form.transaction_date,
                status: form.status,
            });
            toast.success("تم تسجيل الحركة");
            onSaved();
            onClose();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setBusy(false);
        }
    };

    const txTypes = catalogue?.transaction_types || [];

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" data-testid="add-tx-modal">
            <div className="bg-white rounded-xl shadow-xl w-full max-w-xl overflow-hidden flex flex-col">
                <header className="flex items-center justify-between border-b border-border px-5 py-3">
                    <h2 className="font-bold text-lg">إضافة حركة</h2>
                    <button onClick={onClose} className="p-1.5 rounded hover:bg-accent" data-testid="tx-modal-close"><X size={20} /></button>
                </header>
                <form onSubmit={submit} className="px-5 py-4 space-y-4">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">نوع الحركة</label>
                            <select value={form.transaction_type} onChange={(e) => set("transaction_type", e.target.value)} className={inputCls} data-testid="tx-type-select">
                                {txTypes.filter((t) => t.key !== "opening_balance").map((t) => (
                                    <option key={t.key} value={t.key}>{t.label}</option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">الاتجاه</label>
                            <div className="flex gap-2">
                                <button type="button" onClick={() => set("direction", "in")} className={`flex-1 py-2 rounded-lg border text-sm font-bold ${form.direction === "in" ? "bg-emerald-100 text-emerald-800 border-emerald-300" : "bg-white border-border"}`} data-testid="tx-dir-in">
                                    ⬇ وارد
                                </button>
                                <button type="button" onClick={() => set("direction", "out")} className={`flex-1 py-2 rounded-lg border text-sm font-bold ${form.direction === "out" ? "bg-rose-100 text-rose-800 border-rose-300" : "bg-white border-border"}`} data-testid="tx-dir-out">
                                    ⬆ صادر
                                </button>
                            </div>
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">المبلغ</label>
                            <input type="number" step="0.01" min="0.01" value={form.amount} onChange={(e) => set("amount", e.target.value)} className={inputCls} placeholder="0.00" dir="ltr" style={{ textAlign: "right" }} data-testid="tx-amount-input" />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">التاريخ</label>
                            <input type="date" value={form.transaction_date} onChange={(e) => set("transaction_date", e.target.value)} className={inputCls} data-testid="tx-date-input" />
                        </div>
                        <div className="sm:col-span-2">
                            <label className="block text-sm font-semibold mb-1.5">الوصف</label>
                            <input value={form.description} onChange={(e) => set("description", e.target.value)} className={inputCls} placeholder="مثال: إيداع نقدي من سلة" maxLength={500} data-testid="tx-description-input" />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">الحالة</label>
                            <select value={form.status} onChange={(e) => set("status", e.target.value)} className={inputCls} data-testid="tx-status-select">
                                <option value="posted">مرحّلة</option>
                                <option value="pending">بانتظار المطابقة</option>
                                <option value="reconciled">مطابقة</option>
                            </select>
                        </div>
                    </div>
                </form>
                <footer className="border-t border-border px-5 py-3 flex justify-end gap-2">
                    <button onClick={onClose} className="px-4 py-2 text-sm border border-border rounded-lg hover:bg-accent" data-testid="tx-cancel-btn">إلغاء</button>
                    <button onClick={submit} disabled={busy} className="px-4 py-2 text-sm bg-brand text-white font-semibold rounded-lg bg-brand-hover disabled:opacity-60" data-testid="tx-save-btn">
                        {busy ? "جاري الحفظ…" : "تسجيل الحركة"}
                    </button>
                </footer>
            </div>
        </div>
    );
}

// ── Main page ────────────────────────────────────────────────────────────────
export default function AccountDetails() {
    const { id } = useParams();
    const navigate = useNavigate();
    const [account, setAccount] = useState(null);
    const [transactions, setTransactions] = useState([]);
    const [catalogue, setCatalogue] = useState(null);
    const [loading, setLoading] = useState(true);
    const [txModal, setTxModal] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [accRes, txRes, catRes] = await Promise.all([
                api.get(`/accounts/${id}`),
                api.get(`/accounts/${id}/transactions`),
                api.get("/accounts/catalogue"),
            ]);
            setAccount(accRes.data);
            setTransactions(txRes.data);
            setCatalogue(catRes.data);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
            if (err.response?.status === 404) navigate("/accounts");
        } finally {
            setLoading(false);
        }
    }, [id, navigate]);

    useEffect(() => { load(); }, [load]);

    const toggleHide = async () => {
        const next = account.status === "hidden" ? "active" : "hidden";
        try {
            await api.put(`/accounts/${id}`, { status: next });
            toast.success(next === "hidden" ? "تم إخفاء الحساب" : "تم إعادة تفعيل الحساب");
            load();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    const deleteTx = async (tx) => {
        if (!window.confirm(`حذف الحركة "${tx.description || tx.type_label}"؟`)) return;
        try {
            await api.delete(`/accounts/${id}/transactions/${tx.id}`);
            toast.success("تم حذف الحركة");
            load();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    if (loading || !account) {
        return <div className="p-10 text-center text-muted-foreground">جاري التحميل…</div>;
    }

    const meta = TYPE_META[account.account_type] || TYPE_META.bank;
    const Icon = meta.icon;
    const stat = STATUS_META[account.status] || STATUS_META.active;

    return (
        <div className="space-y-6" data-testid="account-details-page">
            <Link to="/accounts" className="inline-flex items-center gap-1.5 text-sm font-bold text-brand hover:underline" data-testid="back-to-accounts">
                <ArrowLeft size={16} /> رجوع إلى الحسابات
            </Link>

            {/* Hero card */}
            <div className="rounded-2xl bg-gradient-to-br from-emerald-50 via-white to-emerald-50 border border-emerald-200 p-6 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="flex items-center gap-3">
                        <div className="w-14 h-14 rounded-xl bg-emerald-600 text-white flex items-center justify-center shrink-0">
                            <Icon size={28} weight="duotone" />
                        </div>
                        <div>
                            <h1 className="text-2xl sm:text-3xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }} data-testid="account-name">
                                {account.name}
                            </h1>
                            <div className="flex flex-wrap items-center gap-2 mt-1.5">
                                <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold border bg-emerald-100 text-emerald-800 border-emerald-200">
                                    {meta.label}
                                </span>
                                <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold ${stat.cls}`}>
                                    {stat.label}
                                </span>
                                {account.provider_name && (
                                    <span className="text-xs text-muted-foreground">{account.provider_name}</span>
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="text-right">
                        <div className="text-xs text-muted-foreground mb-0.5">الرصيد الحالي</div>
                        <div className={`num text-3xl sm:text-4xl font-extrabold ${(account.current_balance || 0) < 0 ? "text-rose-600" : "text-emerald-700"}`} style={{ fontFamily: "Tajawal" }} data-testid="account-balance">
                            {fmt(account.current_balance, account.currency)}
                        </div>
                        <div className="text-[11px] text-muted-foreground mt-0.5">
                            افتتاحي: <span className="num">{fmt(account.opening_balance, account.currency)}</span>
                            {" "}({account.opening_balance_date})
                        </div>
                    </div>
                </div>

                <div className="flex flex-wrap gap-2 mt-5 pt-4 border-t border-emerald-200/60">
                    <button onClick={() => setTxModal(true)} className="inline-flex items-center gap-1.5 px-4 py-2 bg-brand text-white text-sm font-semibold rounded-lg bg-brand-hover" data-testid="add-tx-btn">
                        <Plus size={16} weight="bold" /> إضافة حركة
                    </button>
                    <button onClick={toggleHide} className="inline-flex items-center gap-1.5 px-4 py-2 bg-white border border-border text-sm font-semibold rounded-lg hover:bg-accent" data-testid="toggle-hide-btn">
                        <EyeSlash size={16} /> {account.status === "hidden" ? "إعادة تفعيل" : "إخفاء الحساب"}
                    </button>
                </div>
            </div>

            {/* Pending-collection breakdown — only for auto-created payment platforms */}
            {account.auto_created && account.account_type === "payment_platform" && (
                (() => {
                    const expected   = Number(account.expected_orders_balance || 0);
                    const transferredOut = Math.max(0, expected - Number(account.current_balance || 0));
                    const pending    = Number(account.current_balance || 0);
                    const pct = expected > 0 ? (transferredOut / expected) * 100 : 0;
                    return (
                        <div className="bg-white rounded-xl border border-border p-5" data-testid="pending-collection-card">
                            <div className="flex items-center justify-between mb-3">
                                <h2 className="font-bold text-foreground">حالة التحصيل من الطلبات</h2>
                                <span className="text-xs text-muted-foreground num">
                                    تم تحصيل {pct.toFixed(1)}% من المتوقع
                                </span>
                            </div>
                            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-3">
                                <div className="rounded-lg bg-slate-50 border border-slate-200 p-3" data-testid="metric-expected">
                                    <div className="text-[11px] text-muted-foreground mb-1">المتوقع من الطلبات</div>
                                    <div className="num text-xl font-extrabold text-slate-900">{fmt(expected, account.currency)}</div>
                                    <div className="text-[10px] text-muted-foreground mt-1">{Number(account.orders_count || 0).toLocaleString("en-US")} طلب</div>
                                </div>
                                <div className="rounded-lg bg-emerald-50 border border-emerald-200 p-3" data-testid="metric-transferred">
                                    <div className="text-[11px] text-emerald-900 mb-1">المحوّل للبنوك</div>
                                    <div className="num text-xl font-extrabold text-emerald-700">{fmt(transferredOut, account.currency)}</div>
                                    <div className="text-[10px] text-emerald-800 mt-1">إجمالي التحويلات الصادرة</div>
                                </div>
                                <div className="rounded-lg bg-amber-50 border border-amber-200 p-3" data-testid="metric-pending">
                                    <div className="text-[11px] text-amber-900 mb-1">المتبقي المعلّق</div>
                                    <div className={`num text-xl font-extrabold ${pending < 0 ? "text-rose-700" : "text-amber-700"}`}>{fmt(pending, account.currency)}</div>
                                    <div className="text-[10px] text-amber-800 mt-1">في انتظار التحويل البنكي</div>
                                </div>
                            </div>
                            <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden">
                                <div className="h-full bg-emerald-500 transition-all duration-500" style={{ width: `${Math.min(100, Math.max(0, pct)).toFixed(1)}%` }} />
                            </div>
                        </div>
                    );
                })()
            )}

            {/* Sub-methods breakdown — only for rollup accounts like سلة */}
            {account.sub_methods?.length > 0 && (
                <div className="bg-white rounded-xl border border-border overflow-hidden" data-testid="sub-methods-card">
                    <div className="px-5 py-3 border-b border-border bg-sky-50/60 flex items-center justify-between">
                        <h2 className="font-bold text-sky-900">تفاصيل طرق الدفع داخل هذا الحساب</h2>
                        <span className="text-xs text-sky-700">{account.sub_methods.length} طريقة دفع</span>
                    </div>
                    <table className="w-full text-sm">
                        <thead className="bg-slate-50/50 text-xs text-muted-foreground">
                            <tr>
                                <th className="text-right px-4 py-2.5 font-bold">طريقة الدفع</th>
                                <th className="text-right px-4 py-2.5 font-bold">عدد الطلبات</th>
                                <th className="text-right px-4 py-2.5 font-bold">المبلغ</th>
                                <th className="text-right px-4 py-2.5 font-bold">نسبة من الحساب</th>
                            </tr>
                        </thead>
                        <tbody>
                            {account.sub_methods.map((s) => {
                                const pct = (account.expected_orders_balance || 0) > 0
                                    ? (s.amount / account.expected_orders_balance) * 100 : 0;
                                return (
                                    <tr key={s.key} className="border-t border-border" data-testid={`sub-method-${s.key}`}>
                                        <td className="px-4 py-2.5 font-bold text-foreground">{s.display}</td>
                                        <td className="px-4 py-2.5 num">{Number(s.count).toLocaleString("en-US")}</td>
                                        <td className="px-4 py-2.5 num font-bold text-emerald-700">{fmt(s.amount, account.currency)}</td>
                                        <td className="px-4 py-2.5">
                                            <div className="flex items-center gap-2">
                                                <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden max-w-[120px]">
                                                    <div className="h-full bg-sky-500" style={{ width: `${Math.min(100, pct).toFixed(1)}%` }} />
                                                </div>
                                                <span className="num text-xs text-muted-foreground">{pct.toFixed(1)}%</span>
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            {/* Transactions table */}
            <div className="bg-white rounded-xl border border-border overflow-hidden">
                <div className="px-5 py-3 border-b border-border bg-slate-50 flex items-center justify-between">
                    <h2 className="font-bold text-foreground">سجل الحركات</h2>
                    <span className="text-xs text-muted-foreground">{transactions.length} حركة</span>
                </div>
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="bg-slate-50/50 text-xs text-muted-foreground">
                            <tr>
                                <th className="text-right px-4 py-2.5 font-bold">التاريخ</th>
                                <th className="text-right px-4 py-2.5 font-bold">النوع</th>
                                <th className="text-right px-4 py-2.5 font-bold">الوصف</th>
                                <th className="text-right px-4 py-2.5 font-bold">وارد</th>
                                <th className="text-right px-4 py-2.5 font-bold">صادر</th>
                                <th className="text-right px-4 py-2.5 font-bold">الرصيد بعد الحركة</th>
                                <th className="text-right px-4 py-2.5 font-bold">الحالة</th>
                                <th className="text-right px-4 py-2.5 font-bold">إجراءات</th>
                            </tr>
                        </thead>
                        <tbody>
                            {transactions.length === 0 && (
                                <tr><td colSpan={8} className="text-center py-10 text-muted-foreground" data-testid="tx-empty">
                                    لا توجد حركات. اضغط "إضافة حركة" لتسجيل أول حركة.
                                </td></tr>
                            )}
                            {transactions.map((tx) => (
                                <tr key={tx.id} className="border-t border-border hover:bg-slate-50/50" data-testid={`tx-row-${tx.id}`}>
                                    <td className="px-4 py-2.5 text-xs text-muted-foreground">{tx.transaction_date}</td>
                                    <td className="px-4 py-2.5 font-semibold text-xs">
                                        {tx.transaction_type === "internal_transfer" ? (
                                            <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold border ${
                                                tx.direction === "in"
                                                    ? "bg-emerald-50 text-emerald-800 border-emerald-200"
                                                    : "bg-rose-50 text-rose-800 border-rose-200"
                                            }`}>
                                                {tx.direction === "in" ? "تحويل وارد" : "تحويل صادر"}
                                                {tx.peer_account_name && (
                                                    <span className="text-muted-foreground font-normal">
                                                        · {tx.direction === "in" ? "من" : "إلى"} {tx.peer_account_name}
                                                    </span>
                                                )}
                                            </span>
                                        ) : (
                                            tx.type_label || tx.transaction_type
                                        )}
                                    </td>
                                    <td className="px-4 py-2.5 text-xs text-foreground max-w-[20ch] truncate" title={tx.description}>
                                        {tx.description || "—"}
                                    </td>
                                    <td className="px-4 py-2.5 num text-emerald-700 font-bold">
                                        {tx.direction === "in" ? fmt(tx.amount, account.currency) : "—"}
                                    </td>
                                    <td className="px-4 py-2.5 num text-rose-600 font-bold">
                                        {tx.direction === "out" ? fmt(tx.amount, account.currency) : "—"}
                                    </td>
                                    <td className="px-4 py-2.5 num font-extrabold text-foreground">{fmt(tx.balance_after, account.currency)}</td>
                                    <td className="px-4 py-2.5">
                                        <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 text-[10px] font-bold">
                                            {tx.status === "posted" ? "مرحّلة" : (tx.status === "pending" ? "بانتظار" : "مطابقة")}
                                        </span>
                                    </td>
                                    <td className="px-4 py-2.5">
                                        {tx.transaction_type !== "opening_balance" && (
                                            <button onClick={() => deleteTx(tx)} className="p-1 rounded text-rose-600 hover:bg-rose-50" data-testid={`tx-delete-${tx.id}`}>
                                                <Trash size={14} />
                                            </button>
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>

            {txModal && (
                <AddTxModal
                    accountId={id}
                    catalogue={catalogue}
                    onClose={() => setTxModal(false)}
                    onSaved={load}
                />
            )}
        </div>
    );
}
