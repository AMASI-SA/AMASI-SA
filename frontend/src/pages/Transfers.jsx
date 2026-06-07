import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
    ArrowsLeftRight, Plus, X, Bank, CreditCard, Trash, Paperclip, ArrowRight,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const fmtMoney = (v, ccy = "SAR") =>
    `${Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${ccy === "SAR" ? "ر.س" : ccy}`;

const fmtDate = (s) => {
    if (!s) return "—";
    // s is "YYYY-MM-DD" — render as DD/MM/YYYY without locale calendar quirks.
    const [y, m, d] = String(s).split("-");
    return y && m && d ? `${d}/${m}/${y}` : s;
};
const fmtDateTime = (s) => {
    if (!s) return "—";
    const dt = new Date(s);
    if (isNaN(dt)) return s;
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(dt.getDate())}/${pad(dt.getMonth() + 1)}/${dt.getFullYear()} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
};

const inputCls =
    "w-full px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand focus:border-brand transition";

function AccountOption({ account }) {
    const Icon = account.account_type === "bank" ? Bank : CreditCard;
    const typeTone =
        account.account_type === "bank" ? "text-emerald-700" : "text-sky-700";
    return (
        <span className="flex items-center gap-2">
            <Icon size={16} className={typeTone} weight="duotone" />
            <span className="font-bold">{account.name}</span>
            <span className="text-muted-foreground text-xs">
                · {fmtMoney(account.current_balance, account.currency)}
            </span>
        </span>
    );
}

function TransferFormModal({ accounts, onClose, onSaved }) {
    const today = new Date().toISOString().slice(0, 10);
    const [form, setForm] = useState({
        from_account_id: "",
        to_account_id: "",
        amount: "",
        transfer_date: today,
        reference: "",
        notes: "",
        attachment_url: "",
        shipping_company: "",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    const fromAcc = accounts.find((a) => a.id === form.from_account_id);
    const toAcc = accounts.find((a) => a.id === form.to_account_id);
    const amountNum = parseFloat(form.amount) || 0;
    const fromBalance = Number(fromAcc?.current_balance || 0);
    const overdraft = fromAcc && amountNum > fromBalance + 0.001;
    // Iter-96 — shipping company is required only when transferring out of
    // the COD bucket (cash physically remitted by a courier).
    const isCodSource = fromAcc?.normalized_payment_method === "cash_on_delivery";

    // Don't allow choosing the same account twice; filter the to-list.
    const toOptions = accounts.filter((a) => a.id !== form.from_account_id);

    const submit = async (e) => {
        e.preventDefault();
        if (!form.from_account_id) return toast.error("اختر حساب المصدر");
        if (!form.to_account_id) return toast.error("اختر حساب الوجهة");
        if (!amountNum || amountNum <= 0) return toast.error("أدخل مبلغاً صحيحاً");
        if (overdraft) {
            return toast.error(
                `المبلغ ${amountNum.toLocaleString("en-US")} أكبر من الرصيد المتاح في ${fromAcc.name} (${fromBalance.toLocaleString("en-US")}).`
            );
        }
        if (isCodSource && !form.shipping_company.trim()) {
            return toast.error("اختر شركة الشحن التي حوّلت مبالغ الدفع عند الاستلام");
        }
        setBusy(true);
        try {
            const { data } = await api.post("/transfers", {
                from_account_id: form.from_account_id,
                to_account_id: form.to_account_id,
                amount: amountNum,
                transfer_date: form.transfer_date,
                reference: form.reference.trim(),
                notes: form.notes.trim(),
                attachment_url: form.attachment_url.trim() || null,
                shipping_company: isCodSource ? form.shipping_company.trim() : null,
            });
            toast.success(`تم تسجيل التحويل: ${fmtMoney(amountNum)} من ${data.from_account_name} إلى ${data.to_account_name}`);
            onSaved(data);
            onClose();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر حفظ التحويل");
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
            <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()} data-testid="transfer-modal">
                <header className="flex items-center justify-between px-5 py-3 border-b border-border bg-slate-50">
                    <h3 className="font-bold text-lg flex items-center gap-2">
                        <ArrowsLeftRight size={20} className="text-brand" weight="duotone" />
                        تحويل جديد
                    </h3>
                    <button onClick={onClose} className="p-1 hover:bg-slate-200 rounded" data-testid="transfer-close-btn">
                        <X size={18} />
                    </button>
                </header>
                <form onSubmit={submit} className="p-5 space-y-4">
                    <div>
                        <label className="block text-xs font-bold text-muted-foreground mb-1.5">من حساب *</label>
                        <select
                            className={inputCls}
                            value={form.from_account_id}
                            onChange={(e) => set("from_account_id", e.target.value)}
                            data-testid="transfer-from"
                            required
                        >
                            <option value="">— اختر الحساب المصدر —</option>
                            {accounts.map((a) => (
                                <option key={a.id} value={a.id}>
                                    {a.name} · {a.account_type === "bank" ? "بنك" : "منصة دفع"} · رصيد {fmtMoney(a.current_balance, a.currency)}
                                </option>
                            ))}
                        </select>
                        {fromAcc && (
                            <div className="text-[11px] text-muted-foreground mt-1 pr-1">
                                الرصيد الحالي: <span className="font-bold text-foreground">{fmtMoney(fromAcc.current_balance, fromAcc.currency)}</span>
                            </div>
                        )}
                    </div>

                    <div className="flex justify-center">
                        <div className="w-10 h-10 rounded-full bg-brand/10 flex items-center justify-center">
                            <ArrowRight size={18} className="text-brand rotate-90" weight="bold" />
                        </div>
                    </div>

                    <div>
                        <label className="block text-xs font-bold text-muted-foreground mb-1.5">إلى حساب *</label>
                        <select
                            className={inputCls}
                            value={form.to_account_id}
                            onChange={(e) => set("to_account_id", e.target.value)}
                            data-testid="transfer-to"
                            required
                        >
                            <option value="">— اختر حساب الوجهة —</option>
                            {toOptions.map((a) => (
                                <option key={a.id} value={a.id}>
                                    {a.name} · {a.account_type === "bank" ? "بنك" : "منصة دفع"} · رصيد {fmtMoney(a.current_balance, a.currency)}
                                </option>
                            ))}
                        </select>
                        {toAcc && (
                            <div className="text-[11px] text-muted-foreground mt-1 pr-1">
                                الرصيد بعد التحويل (تقريباً): <span className="font-bold text-emerald-700">
                                    {fmtMoney(Number(toAcc.current_balance || 0) + (parseFloat(form.amount) || 0), toAcc.currency)}
                                </span>
                            </div>
                        )}
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className="block text-xs font-bold text-muted-foreground mb-1.5">المبلغ *</label>
                            <input
                                type="number"
                                step="0.01"
                                min="0.01"
                                max={fromBalance || undefined}
                                className={`${inputCls} ${overdraft ? "border-rose-400 focus:ring-rose-500 focus:border-rose-500" : ""}`}
                                value={form.amount}
                                onChange={(e) => set("amount", e.target.value)}
                                placeholder="0.00"
                                data-testid="transfer-amount"
                                required
                            />
                            {overdraft && (
                                <div className="text-[11px] text-rose-700 mt-1 pr-1 font-bold" data-testid="overdraft-warning">
                                    ⚠ المبلغ أكبر من رصيد {fromAcc.name} المتاح ({fromBalance.toLocaleString("en-US")}).
                                </div>
                            )}
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-muted-foreground mb-1.5">تاريخ التحويل *</label>
                            <input
                                type="date"
                                className={inputCls}
                                value={form.transfer_date}
                                onChange={(e) => set("transfer_date", e.target.value)}
                                data-testid="transfer-date"
                                required
                            />
                        </div>
                    </div>

                    {isCodSource && (
                        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3" data-testid="shipping-company-section">
                            <label className="block text-xs font-bold text-amber-900 mb-1.5">
                                شركة الشحن التي حوّلت المبلغ
                                <span className="text-rose-600 mr-1">*</span>
                            </label>
                            <input
                                list="cod-shipping-companies"
                                type="text"
                                className={inputCls}
                                value={form.shipping_company}
                                onChange={(e) => set("shipping_company", e.target.value)}
                                placeholder="سمسا، أيميل، مندوب الرياض…"
                                data-testid="transfer-shipping-company"
                                required
                            />
                            <datalist id="cod-shipping-companies">
                                <option value="سمسا" />
                                <option value="أيميل" />
                                <option value="مندوب الرياض" />
                                <option value="Aramex" />
                                <option value="SPL" />
                                <option value="DHL" />
                                <option value="J&T Express" />
                            </datalist>
                            <div className="text-[11px] text-amber-800 mt-1.5">
                                يحدِّد كم تم تحصيله من كل شركة شحن لمقارنته بالمتوقَّع لاحقاً.
                            </div>
                        </div>
                    )}

                    <div>
                        <label className="block text-xs font-bold text-muted-foreground mb-1.5">رقم المرجع</label>
                        <input
                            type="text"
                            className={inputCls}
                            value={form.reference}
                            onChange={(e) => set("reference", e.target.value)}
                            placeholder="TRX-2026-001"
                            data-testid="transfer-reference"
                        />
                    </div>

                    <div>
                        <label className="block text-xs font-bold text-muted-foreground mb-1.5">رابط الفاتورة / إشعار التحويل (اختياري)</label>
                        <input
                            type="url"
                            className={inputCls}
                            value={form.attachment_url}
                            onChange={(e) => set("attachment_url", e.target.value)}
                            placeholder="https://..."
                            data-testid="transfer-attachment"
                        />
                    </div>

                    <div>
                        <label className="block text-xs font-bold text-muted-foreground mb-1.5">ملاحظات</label>
                        <textarea
                            className={inputCls}
                            rows={2}
                            value={form.notes}
                            onChange={(e) => set("notes", e.target.value)}
                            placeholder="أي تفاصيل إضافية..."
                            data-testid="transfer-notes"
                        />
                    </div>

                    <div className="flex items-center justify-end gap-2 pt-2">
                        <button type="button" onClick={onClose} className="px-4 py-2 text-sm font-bold rounded-lg border border-border hover:bg-slate-50">
                            إلغاء
                        </button>
                        <button type="submit" disabled={busy || overdraft} className="px-5 py-2 text-sm font-bold rounded-lg bg-brand text-white bg-brand-hover disabled:opacity-60 disabled:cursor-not-allowed" data-testid="transfer-submit">
                            {busy ? "جاري الحفظ..." : "تسجيل التحويل"}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}

export default function Transfers() {
    const [accounts, setAccounts] = useState([]);
    const [transfers, setTransfers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [showForm, setShowForm] = useState(false);

    const eligible = useMemo(
        () => accounts.filter((a) => ["bank", "payment_platform"].includes(a.account_type) && a.status !== "hidden"),
        [accounts]
    );

    const load = async () => {
        try {
            // Ensure default banks exist on first load (idempotent).
            await api.post("/accounts/ensure-default-banks");
            const [accRes, trRes] = await Promise.all([
                api.get("/accounts", { params: { include_hidden: false } }),
                api.get("/transfers", { params: { limit: 200 } }),
            ]);
            setAccounts(accRes.data || []);
            setTransfers(trRes.data || []);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر تحميل البيانات");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

    const onDelete = async (id) => {
        if (!window.confirm("هل أنت متأكد من حذف هذا التحويل؟ سيتم عكسه في كلا الحسابين.")) return;
        try {
            await api.delete(`/transfers/${id}`);
            toast.success("تم حذف التحويل وإعادة الأرصدة");
            load();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر الحذف");
        }
    };

    return (
        <div className="space-y-6" data-testid="transfers-page">
            <header className="flex items-start justify-between gap-3 flex-wrap">
                <div>
                    <h1 className="text-3xl sm:text-4xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                        التحويلات بين الحسابات
                    </h1>
                    <p className="text-muted-foreground mt-1 text-sm">
                        سجّل تحويلاتك من منصات الدفع (سلة / تابي / تمارا / إمكان) إلى البنوك (الإنماء / الأهلي / الراجحي). الأرصدة تتحدث تلقائياً.
                    </p>
                </div>
                <button onClick={() => setShowForm(true)} className="inline-flex items-center gap-2 px-4 py-2.5 bg-brand text-white text-sm font-semibold rounded-lg bg-brand-hover" data-testid="transfer-new-btn">
                    <Plus size={18} weight="bold" /> تحويل جديد
                </button>
            </header>

            {/* Quick legend of eligible accounts */}
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
                {eligible.map((a) => (
                    <Link
                        key={a.id}
                        to={`/accounts/${a.id}`}
                        className="bg-white rounded-lg border border-border p-3 hover:border-brand transition"
                        data-testid={`balance-card-${a.id}`}
                    >
                        <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                            {a.account_type === "bank" ? (
                                <Bank size={14} className="text-emerald-700" weight="duotone" />
                            ) : (
                                <CreditCard size={14} className="text-sky-700" weight="duotone" />
                            )}
                            <span className="truncate">{a.name}</span>
                        </div>
                        <div className="num font-bold text-foreground">{fmtMoney(a.current_balance, a.currency)}</div>
                    </Link>
                ))}
            </div>

            <div className="bg-white rounded-xl border border-border overflow-hidden" data-testid="transfers-list">
                <div className="px-5 py-3 border-b border-border bg-slate-50 flex items-center justify-between">
                    <h2 className="font-bold text-sm">سجل التحويلات</h2>
                    <span className="text-xs text-muted-foreground">{transfers.length} تحويل</span>
                </div>
                {loading ? (
                    <div className="p-10 text-center text-muted-foreground">جاري التحميل...</div>
                ) : transfers.length === 0 ? (
                    <div className="p-10 text-center text-muted-foreground">
                        لا توجد تحويلات بعد.{" "}
                        <button onClick={() => setShowForm(true)} className="text-brand font-bold hover:underline">
                            سجّل أول تحويل
                        </button>
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead className="bg-slate-50/50 text-xs text-muted-foreground">
                                <tr>
                                    <th className="text-right px-4 py-2.5 font-bold">التاريخ</th>
                                    <th className="text-right px-4 py-2.5 font-bold">من حساب</th>
                                    <th className="text-right px-4 py-2.5 font-bold">إلى حساب</th>
                                    <th className="text-right px-4 py-2.5 font-bold">المبلغ</th>
                                    <th className="text-right px-4 py-2.5 font-bold">المرجع</th>
                                    <th className="text-right px-4 py-2.5 font-bold">المرفق</th>
                                    <th className="text-right px-4 py-2.5 font-bold">المستخدم</th>
                                    <th className="text-right px-4 py-2.5 font-bold">وقت الإنشاء</th>
                                    <th className="text-right px-4 py-2.5 font-bold">إجراء</th>
                                </tr>
                            </thead>
                            <tbody>
                                {transfers.map((t) => (
                                    <tr key={t.id} className="border-t border-border hover:bg-accent/30" data-testid={`transfer-row-${t.id}`}>
                                        <td className="px-4 py-2.5 num">{fmtDate(t.transfer_date)}</td>
                                        <td className="px-4 py-2.5">
                                            <Link to={`/accounts/${t.from_account_id}`} className="font-bold text-rose-700 hover:underline">
                                                {t.from_account_name}
                                            </Link>
                                            {t.shipping_company && (
                                                <div className="text-[10px] text-amber-700 mt-0.5 inline-block bg-amber-50 rounded px-1.5 py-0.5 font-bold" data-testid={`transfer-shipping-${t.id}`}>
                                                    🚚 {t.shipping_company}
                                                </div>
                                            )}
                                        </td>
                                        <td className="px-4 py-2.5">
                                            <Link to={`/accounts/${t.to_account_id}`} className="font-bold text-emerald-700 hover:underline">
                                                {t.to_account_name}
                                            </Link>
                                        </td>
                                        <td className="px-4 py-2.5 num font-bold">{fmtMoney(t.amount, t.currency)}</td>
                                        <td className="px-4 py-2.5 text-xs text-muted-foreground">{t.reference || "—"}</td>
                                        <td className="px-4 py-2.5">
                                            {t.attachment_url ? (
                                                <a href={t.attachment_url} target="_blank" rel="noreferrer" className="text-brand hover:underline inline-flex items-center gap-1">
                                                    <Paperclip size={12} /> فتح
                                                </a>
                                            ) : <span className="text-muted-foreground text-xs">—</span>}
                                        </td>
                                        <td className="px-4 py-2.5 text-xs">{t.created_by_name || "—"}</td>
                                        <td className="px-4 py-2.5 text-[11px] text-muted-foreground">{fmtDateTime(t.created_at)}</td>
                                        <td className="px-4 py-2.5">
                                            <button onClick={() => onDelete(t.id)} className="p-1 text-rose-600 hover:bg-rose-50 rounded" title="حذف" data-testid={`transfer-delete-${t.id}`}>
                                                <Trash size={14} />
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {showForm && (
                <TransferFormModal
                    accounts={eligible}
                    onClose={() => setShowForm(false)}
                    onSaved={() => load()}
                />
            )}
        </div>
    );
}
