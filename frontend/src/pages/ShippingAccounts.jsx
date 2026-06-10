import { useEffect, useState } from "react";
import { Truck, Plus, Trash, X, Receipt } from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { formatMoney, todayISO } from "../lib/format";
import DateInput from "../components/DateInput";

export default function ShippingAccounts() {
    const [data, setData] = useState({ accounts: [], totals: { total_owed: 0, total_paid: 0, remaining: 0 } });
    const [accounts, setAccounts] = useState([]);   // Iter-95: bank accounts for payment source
    const [loading, setLoading] = useState(true);
    const [expanded, setExpanded] = useState(null);  // company name currently expanded
    const [payments, setPayments] = useState({});    // {companyName: [...]}
    const [modal, setModal] = useState(null);        // {company} | null
    const [form, setForm] = useState({ amount: "", payment_date: todayISO(), invoice_number: "", note: "", paid_from_account_id: "" });
    const [submitting, setSubmitting] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            const [shipRes, accRes] = await Promise.all([
                api.get("/shipping-accounts"),
                api.get("/accounts"),
            ]);
            setData(shipRes.data);
            const raw = accRes.data?.accounts || accRes.data?.items || (Array.isArray(accRes.data) ? accRes.data : []);
            setAccounts(raw.filter((a) => a.account_type === "bank" && a.status !== "hidden" && a.status !== "inactive"));
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setLoading(false); }
    };

    useEffect(() => { load(); }, []);

    const togglePayments = async (companyName) => {
        if (expanded === companyName) {
            setExpanded(null);
            return;
        }
        setExpanded(companyName);
        if (!payments[companyName]) {
            try {
                const { data: d } = await api.get(`/shipping-accounts/${encodeURIComponent(companyName)}/payments`);
                setPayments((prev) => ({ ...prev, [companyName]: d.payments || [] }));
            } catch (err) {
                toast.error(formatApiErrorDetail(err.response?.data?.detail));
            }
        }
    };

    const openModal = (company) => {
        setForm({ amount: "", payment_date: todayISO(), invoice_number: "", note: "", paid_from_account_id: "" });
        setModal({ company });
    };

    const savePayment = async () => {
        if (!form.amount || Number(form.amount) <= 0) {
            toast.error("أدخل مبلغاً أكبر من الصفر");
            return;
        }
        setSubmitting(true);
        try {
            await api.post(`/shipping-accounts/${encodeURIComponent(modal.company)}/payments`, {
                amount: Number(form.amount),
                payment_date: form.payment_date,
                invoice_number: form.invoice_number || "",
                note: form.note || "",
                paid_from_account_id: form.paid_from_account_id || null,
            });
            // Iter-95: explicit warning when no bank account is linked
            if (!form.paid_from_account_id) {
                toast.warning(
                    "تم تسجيل الدفعة بدون ربطها بحساب بنكي، لذلك لن تؤثر على رصيد البنك."
                );
            } else {
                toast.success("تم تسجيل الدفعة وخصم المبلغ من رصيد الحساب البنكي");
            }
            setModal(null);
            // refresh: invalidate cache for this company + reload totals
            setPayments((prev) => { const c = { ...prev }; delete c[modal.company]; return c; });
            await load();
            if (expanded === modal.company) {
                const { data: d } = await api.get(`/shipping-accounts/${encodeURIComponent(modal.company)}/payments`);
                setPayments((prev) => ({ ...prev, [modal.company]: d.payments || [] }));
            }
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setSubmitting(false); }
    };

    const deletePayment = async (companyName, paymentId) => {
        if (!window.confirm("حذف هذه الدفعة؟")) return;
        try {
            await api.delete(`/shipping-accounts/payments/${paymentId}`);
            toast.success("تم حذف الدفعة");
            setPayments((prev) => ({
                ...prev,
                [companyName]: (prev[companyName] || []).filter((p) => p.id !== paymentId),
            }));
            await load();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    if (loading) {
        return (
            <div className="space-y-4">
                <div className="h-24 rounded-xl bg-white border border-border animate-pulse" />
                <div className="h-48 rounded-xl bg-white border border-border animate-pulse" />
            </div>
        );
    }

    return (
        <div className="space-y-6" data-testid="shipping-accounts-page">
            {/* Header */}
            <div>
                <h1 className="text-3xl md:text-4xl font-bold text-foreground" style={{ fontFamily: "Tajawal" }}>
                    حسابات شركات الشحن الآجلة
                </h1>
                <p className="text-sm text-muted-foreground mt-2">
                    شركات الشحن التي لا تُخصم تكلفتها من حوالة سلة، وتُسجَّل كذمم مستحقة على المتجر.
                </p>
            </div>

            {/* Totals strip */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <Card title="إجمالي المستحق" value={data.totals.total_owed} testid="totals-owed" tone="neutral" />
                <Card title="المدفوع" value={data.totals.total_paid} testid="totals-paid" tone="success" />
                <Card title="المتبقي" value={data.totals.remaining} testid="totals-remaining" tone="warn" />
            </div>

            {/* Accounts list */}
            {data.accounts.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border bg-white p-10 text-center" data-testid="empty-accounts">
                    <Truck size={42} className="mx-auto text-muted-foreground mb-3" />
                    <p className="font-semibold text-lg mb-1">لا توجد شركات شحن آجلة بعد</p>
                    <p className="text-sm text-muted-foreground">
                        فعّل خيار "آجل" بجانب أي شركة شحن من <span className="font-bold">الإعدادات</span>، ثم ارفع ملف Excel سلة لتتراكم المستحقات هنا.
                    </p>
                </div>
            ) : (
                <div className="space-y-3">
                    {data.accounts.map((acc) => (
                        <div key={acc.name} className="rounded-xl border border-border bg-white overflow-hidden" data-testid={`account-${acc.name}`}>
                            <div className="grid grid-cols-1 md:grid-cols-12 gap-3 p-5 items-center">
                                <div className="md:col-span-3">
                                    <div className="flex items-center gap-2.5">
                                        <div className="w-10 h-10 rounded-lg bg-amber-50 text-amber-700 flex items-center justify-center">
                                            <Truck size={20} weight="bold" />
                                        </div>
                                        <div>
                                            <h3 className="font-bold text-lg leading-tight">{acc.name}</h3>
                                            <p className="text-xs text-muted-foreground">
                                                {acc.orders_count > 0 ? `${acc.orders_count} طلب × ${formatMoney(acc.cost_per_order)} ر.س` : "لا توجد طلبات بعد"}
                                            </p>
                                        </div>
                                    </div>
                                </div>
                                <Stat label="مستحق" value={acc.total_owed} testid={`owed-${acc.name}`} />
                                <Stat label="مدفوع" value={acc.total_paid} testid={`paid-${acc.name}`} tone="success" />
                                <Stat label="متبقي" value={Math.abs(acc.remaining)} testid={`remaining-${acc.name}`} tone={acc.remaining > 0 ? "warn" : (acc.remaining < 0 ? "credit" : "success")} suffix={acc.remaining < 0 ? "(رصيد دائن)" : ""} />
                                <div className="md:col-span-3 flex items-center gap-2 justify-start md:justify-end">
                                    <button
                                        type="button"
                                        onClick={() => openModal(acc.name)}
                                        className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-brand text-white text-sm font-bold hover:opacity-90 transition-opacity"
                                        data-testid={`add-payment-btn-${acc.name}`}
                                    >
                                        <Plus size={16} weight="bold" /> دفعة
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => togglePayments(acc.name)}
                                        className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-sm font-semibold hover:bg-accent transition-colors"
                                        data-testid={`toggle-payments-${acc.name}`}
                                    >
                                        {expanded === acc.name ? "إخفاء" : "السجل"}
                                    </button>
                                </div>
                            </div>

                            {/* Progress bar */}
                            <div className="px-5 pb-3">
                                <div className="h-2 w-full bg-accent/50 rounded-full overflow-hidden">
                                    <div
                                        className="h-full bg-emerald-500 transition-all"
                                        style={{ width: acc.total_owed > 0 ? `${Math.min(100, (acc.total_paid / acc.total_owed) * 100)}%` : "0%" }}
                                    />
                                </div>
                                <div className="text-xs text-muted-foreground mt-1.5 text-end">
                                    {acc.total_owed > 0 ? `${Math.round((acc.total_paid / acc.total_owed) * 100)}% مدفوع` : "—"}
                                </div>
                            </div>

                            {/* Payments table (expandable) */}
                            {expanded === acc.name && (
                                <div className="border-t border-border bg-accent/20" data-testid={`payments-list-${acc.name}`}>
                                    {(payments[acc.name] || []).length === 0 ? (
                                        <p className="p-5 text-center text-sm text-muted-foreground">لا توجد دفعات مسجَّلة بعد</p>
                                    ) : (
                                        <div className="overflow-x-auto">
                                            <table className="mezan-table w-full text-sm">
                                                <thead>
                                                    <tr className="text-xs font-bold text-muted-foreground uppercase">
                                                        <th className="text-start px-5 py-3">التاريخ</th>
                                                        <th className="text-end px-5 py-3">المبلغ</th>
                                                        <th className="text-start px-5 py-3">رقم الفاتورة</th>
                                                        <th className="text-start px-5 py-3">ملاحظة</th>
                                                        <th className="px-5 py-3"></th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {(payments[acc.name] || []).map((p) => (
                                                        <tr key={p.id} className="border-t border-border hover:bg-white/60">
                                                            <td className="px-5 py-3 font-semibold" dir="ltr">{p.payment_date}</td>
                                                            <td className="px-5 py-3 text-end font-bold text-emerald-700 num">{formatMoney(p.amount)} ر.س</td>
                                                            <td className="px-5 py-3" dir="ltr">{p.invoice_number || "—"}</td>
                                                            <td className="px-5 py-3 text-muted-foreground">{p.note || "—"}</td>
                                                            <td className="px-5 py-3 text-end">
                                                                <button
                                                                    type="button"
                                                                    onClick={() => deletePayment(acc.name, p.id)}
                                                                    className="p-1.5 rounded-md hover:bg-red-50 hover:text-red-600 transition-colors"
                                                                    title="حذف الدفعة"
                                                                    data-testid={`delete-payment-${p.id}`}
                                                                >
                                                                    <Trash size={16} />
                                                                </button>
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {/* Add Payment Modal */}
            {modal && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4" onClick={() => setModal(null)}>
                    <div
                        className="bg-white rounded-2xl shadow-xl max-w-md w-full p-6 space-y-4"
                        onClick={(e) => e.stopPropagation()}
                        data-testid="payment-modal"
                    >
                        <div className="flex items-start justify-between">
                            <div>
                                <h2 className="text-xl font-bold flex items-center gap-2">
                                    <Receipt size={20} weight="bold" />
                                    تسجيل دفعة لشركة {modal.company}
                                </h2>
                                <p className="text-xs text-muted-foreground mt-1">سيتم خصمها من الذمم المستحقة لهذه الشركة.</p>
                            </div>
                            <button type="button" onClick={() => setModal(null)} className="p-1.5 rounded-md hover:bg-accent" data-testid="close-payment-modal">
                                <X size={18} />
                            </button>
                        </div>

                        <div className="space-y-3">
                            <div>
                                <label className="block text-sm font-semibold mb-1.5">المبلغ (ر.س)</label>
                                <input
                                    type="number" min={0} step="0.01"
                                    value={form.amount}
                                    onChange={(e) => setForm({ ...form, amount: e.target.value })}
                                    placeholder="0.00"
                                    className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand num"
                                    data-testid="payment-amount"
                                    dir="ltr"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-semibold mb-1.5">تاريخ الدفع</label>
                                <DateInput
                                    value={form.payment_date}
                                    onChange={(e) => setForm({ ...form, payment_date: e.target.value })}
                                    data-testid="payment-date"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-semibold mb-1.5">
                                    الحساب المدفوع منه
                                    <span className="text-rose-600 mr-1">*</span>
                                </label>
                                <select
                                    value={form.paid_from_account_id}
                                    onChange={(e) => setForm({ ...form, paid_from_account_id: e.target.value })}
                                    className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                    data-testid="payment-paid-from-account"
                                >
                                    <option value="">— بدون ربط بحساب (لن تؤثر على رصيد البنك) —</option>
                                    {accounts.map((a) => (
                                        <option key={a.id} value={a.id}>
                                            {a.name}
                                            {typeof a.current_balance === "number"
                                                ? `  (الرصيد: ${a.current_balance.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س)`
                                                : ""}
                                        </option>
                                    ))}
                                </select>
                                {!form.paid_from_account_id && (
                                    <div className="mt-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2" data-testid="no-account-warning">
                                        ⚠️ بدون اختيار حساب لن يَنقص رصيد أي بنك، والمركز المالي لن يعكس هذه الدفعة.
                                    </div>
                                )}
                            </div>
                            <div>
                                <label className="block text-sm font-semibold mb-1.5">رقم الفاتورة (اختياري)</label>
                                <input
                                    type="text"
                                    value={form.invoice_number}
                                    onChange={(e) => setForm({ ...form, invoice_number: e.target.value })}
                                    placeholder="مثلاً INV-2026-0123"
                                    className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                    data-testid="payment-invoice"
                                    dir="ltr"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-semibold mb-1.5">ملاحظة (اختياري)</label>
                                <textarea
                                    rows={2}
                                    value={form.note}
                                    onChange={(e) => setForm({ ...form, note: e.target.value })}
                                    placeholder="أي تفاصيل إضافية…"
                                    className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand resize-none"
                                    data-testid="payment-note"
                                />
                            </div>
                        </div>

                        <div className="flex items-center justify-end gap-2 pt-2">
                            <button
                                type="button"
                                onClick={() => setModal(null)}
                                className="px-4 py-2.5 rounded-lg border border-border text-sm font-semibold hover:bg-accent transition-colors"
                            >إلغاء</button>
                            <button
                                type="button"
                                onClick={savePayment}
                                disabled={submitting}
                                className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-brand text-white text-sm font-bold hover:opacity-90 transition-opacity disabled:opacity-60"
                                data-testid="save-payment-btn"
                            >
                                <Receipt size={16} weight="bold" />
                                {submitting ? "جاري الحفظ…" : "حفظ الدفعة"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function Card({ title, value, testid, tone = "neutral" }) {
    const tones = {
        neutral: "border-border",
        success: "border-emerald-200 bg-emerald-50/50",
        warn: "border-amber-200 bg-amber-50/50",
    };
    return (
        <div className={`rounded-xl border bg-white p-5 ${tones[tone]}`} data-testid={testid}>
            <p className="text-sm text-muted-foreground font-semibold">{title}</p>
            <p className="text-2xl md:text-3xl font-bold mt-2 num">
                {formatMoney(value)} <span className="text-base text-muted-foreground font-semibold">ر.س</span>
            </p>
        </div>
    );
}

function Stat({ label, value, testid, tone = "neutral", suffix = "" }) {
    const colors = {
        neutral: "text-foreground",
        success: "text-emerald-700",
        warn: "text-amber-700",
        credit: "text-sky-700",
    };
    return (
        <div className="md:col-span-2" data-testid={testid}>
            <p className="text-xs text-muted-foreground font-semibold">{label}</p>
            <p className={`text-lg md:text-xl font-bold num ${colors[tone]}`}>
                {formatMoney(value)} <span className="text-xs text-muted-foreground font-semibold">ر.س</span>
            </p>
            {suffix ? <p className="text-[10px] text-sky-700 font-bold mt-0.5">{suffix}</p> : null}
        </div>
    );
}
