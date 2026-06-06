import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
    Plus, Bank, CreditCard, Megaphone, Wallet, Eye, EyeSlash, Trash,
    PencilSimple, X, Warning, ArrowsClockwise, Lightning,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import UnifiedPaymentGatewaysCard from "../components/UnifiedPaymentGatewaysCard";

const TYPE_META = {
    bank:             { label: "حساب بنكي",   icon: Bank,       cls: "bg-emerald-100 text-emerald-800 border-emerald-200" },
    payment_platform: { label: "منصة دفع",     icon: CreditCard, cls: "bg-sky-100 text-sky-800 border-sky-200" },
    ads_platform:     { label: "حساب إعلاني",  icon: Megaphone,  cls: "bg-violet-100 text-violet-800 border-violet-200" },
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

// ── Add/Edit modal ───────────────────────────────────────────────────────────
function AccountFormModal({ initial, catalogue, banks, onClose, onSaved }) {
    const isEdit = !!initial;
    const today = new Date().toISOString().slice(0, 10);
    const [form, setForm] = useState(() => ({
        account_type: initial?.account_type || "bank",
        name: initial?.name || "",
        provider_name: initial?.provider_name || "",
        currency: initial?.currency || "SAR",
        opening_balance: initial?.opening_balance ?? "",
        opening_balance_date: initial?.opening_balance_date || today,
        default_bank_account_id: initial?.default_bank_account_id || "",
        notes: initial?.notes || "",
    }));
    const [busy, setBusy] = useState(false);

    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const suggested = catalogue?.suggested_providers?.[form.account_type] || [];

    const submit = async (e) => {
        e.preventDefault();
        if (!form.name.trim()) return toast.error("اسم الحساب مطلوب");
        setBusy(true);
        try {
            if (isEdit) {
                const { data } = await api.put(`/accounts/${initial.id}`, {
                    name: form.name.trim(),
                    provider_name: form.provider_name.trim() || null,
                    currency: form.currency,
                    default_bank_account_id: form.default_bank_account_id || null,
                    notes: form.notes,
                });
                toast.success("تم تحديث الحساب");
                onSaved(data);
            } else {
                const payload = {
                    name: form.name.trim(),
                    account_type: form.account_type,
                    provider_name: form.provider_name.trim() || null,
                    currency: form.currency,
                    opening_balance: parseFloat(form.opening_balance) || 0,
                    opening_balance_date: form.opening_balance_date,
                    notes: form.notes,
                };
                if (form.account_type === "payment_platform" && form.default_bank_account_id) {
                    payload.default_bank_account_id = form.default_bank_account_id;
                }
                const { data } = await api.post("/accounts", payload);
                toast.success("تم إنشاء الحساب");
                onSaved(data);
            }
            onClose();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" data-testid="account-form-modal">
            <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
                <header className="flex items-center justify-between border-b border-border px-5 py-3">
                    <h2 className="font-bold text-lg">{isEdit ? "تعديل حساب" : "إضافة حساب جديد"}</h2>
                    <button onClick={onClose} className="p-1.5 rounded hover:bg-accent" data-testid="account-modal-close-btn"><X size={20} /></button>
                </header>
                <form onSubmit={submit} className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
                    {!isEdit && (
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">نوع الحساب</label>
                            <div className="grid grid-cols-3 gap-2">
                                {Object.entries(TYPE_META).map(([key, m]) => {
                                    const Icon = m.icon;
                                    const active = form.account_type === key;
                                    return (
                                        <button
                                            type="button"
                                            key={key}
                                            onClick={() => set("account_type", key)}
                                            className={`flex flex-col items-center gap-1.5 px-3 py-3 rounded-lg border-2 transition ${active ? "border-brand bg-brand/5" : "border-border bg-white hover:bg-accent/40"}`}
                                            data-testid={`account-type-${key}`}
                                        >
                                            <Icon size={24} weight={active ? "duotone" : "regular"} className={active ? "text-brand" : "text-muted-foreground"} />
                                            <span className={`text-xs font-bold ${active ? "text-brand" : "text-foreground"}`}>{m.label}</span>
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">اسم الحساب</label>
                            <input value={form.name} onChange={(e) => set("name", e.target.value)} className={inputCls} placeholder="مثال: بنك الإنماء — حساب أساسي" maxLength={120} data-testid="account-name-input" />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">المزوّد / البنك / المنصة</label>
                            <input value={form.provider_name} onChange={(e) => set("provider_name", e.target.value)} className={inputCls} list="provider-suggestions" placeholder="اختر أو اكتب…" data-testid="account-provider-input" />
                            <datalist id="provider-suggestions">
                                {suggested.map((p) => <option key={p} value={p} />)}
                            </datalist>
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">العملة</label>
                            <select value={form.currency} onChange={(e) => set("currency", e.target.value)} className={inputCls} data-testid="account-currency-select">
                                <option value="SAR">ر.س — SAR</option>
                                <option value="USD">$ — USD</option>
                                <option value="AED">د.إ — AED</option>
                                <option value="EUR">€ — EUR</option>
                            </select>
                        </div>
                        {!isEdit && (
                            <>
                                <div>
                                    <label className="block text-sm font-semibold mb-1.5">الرصيد الافتتاحي</label>
                                    <input type="number" step="0.01" value={form.opening_balance} onChange={(e) => set("opening_balance", e.target.value)} className={inputCls} placeholder="0.00 (يمكن أن يكون سالباً)" dir="ltr" style={{ textAlign: "right" }} data-testid="account-opening-balance-input" />
                                </div>
                                <div>
                                    <label className="block text-sm font-semibold mb-1.5">تاريخ الرصيد الافتتاحي</label>
                                    <input type="date" value={form.opening_balance_date} onChange={(e) => set("opening_balance_date", e.target.value)} className={inputCls} data-testid="account-opening-date-input" />
                                </div>
                            </>
                        )}
                        {!isEdit && form.account_type === "payment_platform" && (
                            <div className="sm:col-span-2">
                                <label className="block text-sm font-semibold mb-1.5">الحساب البنكي الافتراضي (اختياري)</label>
                                <select value={form.default_bank_account_id} onChange={(e) => set("default_bank_account_id", e.target.value)} className={inputCls} data-testid="account-default-bank-select">
                                    <option value="">— بدون —</option>
                                    {banks.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
                                </select>
                                <p className="text-[11px] text-muted-foreground mt-1">يُستخدم لاحقاً عند تسجيل التحويلات تلقائياً.</p>
                            </div>
                        )}
                        <div className="sm:col-span-2">
                            <label className="block text-sm font-semibold mb-1.5">ملاحظات</label>
                            <input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} placeholder="ملاحظات اختيارية" maxLength={500} data-testid="account-notes-input" />
                        </div>
                    </div>
                </form>
                <footer className="border-t border-border px-5 py-3 flex justify-end gap-2">
                    <button onClick={onClose} className="px-4 py-2 text-sm border border-border rounded-lg hover:bg-accent" data-testid="account-cancel-btn">إلغاء</button>
                    <button onClick={submit} disabled={busy} className="px-4 py-2 text-sm bg-brand text-white font-semibold rounded-lg bg-brand-hover disabled:opacity-60" data-testid="account-save-btn">
                        {busy ? "جاري الحفظ…" : (isEdit ? "حفظ التعديلات" : "إنشاء الحساب")}
                    </button>
                </footer>
            </div>
        </div>
    );
}

// ── Summary card ─────────────────────────────────────────────────────────────
function SummaryCard({ title, value, icon: Icon, tone = "emerald", testid }) {
    const tones = {
        emerald: "from-emerald-50 to-emerald-100 border-emerald-200 text-emerald-800",
        sky:     "from-sky-50 to-sky-100 border-sky-200 text-sky-800",
        violet:  "from-violet-50 to-violet-100 border-violet-200 text-violet-800",
        amber:   "from-amber-50 to-amber-100 border-amber-200 text-amber-800",
    };
    return (
        <div className={`rounded-xl border bg-gradient-to-br ${tones[tone]} p-4`} data-testid={testid}>
            <div className="flex items-center gap-2 text-xs font-bold opacity-80 mb-1">
                <Icon size={16} weight="duotone" />
                {title}
            </div>
            <div className="num text-xl sm:text-2xl font-extrabold" style={{ fontFamily: "Tajawal" }}>
                {fmt(value)}
            </div>
        </div>
    );
}

// ── Main page ────────────────────────────────────────────────────────────────
export default function Accounts() {
    const [accounts, setAccounts] = useState([]);
    const [summary, setSummary] = useState(null);
    const [catalogue, setCatalogue] = useState(null);
    const [loading, setLoading] = useState(true);
    const [tab, setTab] = useState("all"); // all | bank | payment_platform | ads_platform | hidden
    const [modal, setModal] = useState(null);
    const [syncing, setSyncing] = useState(false);

    const syncPaymentMethods = async () => {
        setSyncing(true);
        try {
            const { data } = await api.post("/accounts/sync-payment-methods");
            const created = data.created || 0;
            const updated = data.updated || 0;
            if (data.synced === 0) {
                toast.info("لا توجد طرق دفع في الطلبات بعد. ارفع ملف Excel أو فعّل Make.com أولاً.");
            } else {
                toast.success(
                    `تمت مزامنة ${data.synced} طريقة دفع، وإنشاء ${created} حساب${created === 1 ? "" : "ات"} جديد${created === 1 ? "" : "ة"}، وتحديث ${updated} حساب${updated === 1 ? "" : "ات"}.`
                );
            }
            await load();
            setTab("payment_platform");
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setSyncing(false);
        }
    };

    const load = async () => {
        setLoading(true);
        try {
            const [accRes, sumRes, catRes] = await Promise.all([
                api.get("/accounts", { params: { include_hidden: true } }),
                api.get("/accounts/summary"),
                api.get("/accounts/catalogue"),
            ]);
            setAccounts(accRes.data);
            setSummary(sumRes.data);
            setCatalogue(catRes.data);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const filtered = useMemo(() => {
        if (tab === "all") return accounts.filter((a) => a.status !== "hidden");
        if (tab === "hidden") return accounts.filter((a) => a.status === "hidden");
        return accounts.filter((a) => a.account_type === tab && a.status !== "hidden");
    }, [accounts, tab]);

    const banks = useMemo(() => accounts.filter((a) => a.account_type === "bank"), [accounts]);

    const toggleHide = async (acc) => {
        const next = acc.status === "hidden" ? "active" : "hidden";
        try {
            const { data } = await api.put(`/accounts/${acc.id}`, { status: next });
            toast.success(next === "hidden" ? "تم إخفاء الحساب" : "تم إعادة تفعيل الحساب");
            setAccounts((arr) => arr.map((x) => (x.id === acc.id ? { ...x, ...data } : x)));
            load();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    const remove = async (acc) => {
        if (!window.confirm(`حذف الحساب "${acc.name}" نهائياً؟`)) return;
        try {
            await api.delete(`/accounts/${acc.id}`);
            toast.success("تم الحذف");
            setAccounts((arr) => arr.filter((x) => x.id !== acc.id));
            load();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    return (
        <div className="space-y-6" data-testid="accounts-page">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="text-3xl sm:text-4xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                        الأصول والحسابات المالية
                    </h1>
                    <p className="text-muted-foreground">إدارة أرصدة البنوك، منصات الدفع، والحسابات الإعلانية.</p>
                </div>
                <button onClick={() => setModal({})} className="inline-flex items-center gap-2 px-4 py-2.5 bg-brand text-white text-sm font-semibold rounded-lg bg-brand-hover" data-testid="accounts-add-btn">
                    <Plus size={18} weight="bold" /> إضافة حساب جديد
                </button>
                <button onClick={syncPaymentMethods} disabled={syncing} className="inline-flex items-center gap-2 px-4 py-2.5 bg-sky-700 text-white text-sm font-semibold rounded-lg hover:bg-sky-800 disabled:opacity-60" data-testid="accounts-sync-payment-methods-btn">
                    <ArrowsClockwise size={18} weight="bold" className={syncing ? "animate-spin" : ""} />
                    {syncing ? "جاري المزامنة…" : "مزامنة طرق الدفع من الطلبات"}
                </button>
            </header>

            {/* Summary */}
            {summary && (
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                    <SummaryCard title="إجمالي الأصول" value={summary.grand_total} icon={Wallet}  tone="emerald" testid="summary-grand-total" />
                    <SummaryCard title="أرصدة البنوك"  value={summary.by_type.bank} icon={Bank}    tone="sky"     testid="summary-banks" />
                    <SummaryCard title="منصات الدفع"  value={summary.by_type.payment_platform} icon={CreditCard} tone="violet"  testid="summary-payment" />
                    <SummaryCard title="الإعلانات"    value={summary.by_type.ads_platform} icon={Megaphone} tone="amber" testid="summary-ads" />
                </div>
            )}

            {/* Iter-81 — central gateways card so the merchant sees IDENTICAL
                expected per-platform numbers to Dashboard / Reports /
                Reconciliation. Clicking "مزامنة طرق الدفع" reconciles the
                stored expected_orders_balance with these live values. */}
            <UnifiedPaymentGatewaysCard testid="accounts-unified-gateways" periodLabel="كل الفترة" />

            {/* Tabs */}
            <div className="flex flex-wrap gap-1 border-b border-border" data-testid="accounts-tabs">
                {[
                    { key: "all",              label: "الكل" },
                    { key: "bank",             label: "البنوك" },
                    { key: "payment_platform", label: "منصات الدفع" },
                    { key: "ads_platform",     label: "الإعلانات" },
                    { key: "hidden",           label: "المخفية" },
                ].map((t) => {
                    const active = tab === t.key;
                    return (
                        <button
                            key={t.key}
                            onClick={() => setTab(t.key)}
                            className={`px-4 py-2 text-sm font-bold border-b-2 -mb-px transition ${active ? "border-brand text-brand" : "border-transparent text-muted-foreground hover:text-foreground"}`}
                            data-testid={`tab-${t.key}`}
                        >
                            {t.label}
                        </button>
                    );
                })}
            </div>

            {/* Table */}
            <div className="bg-white rounded-xl border border-border overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="bg-slate-50 text-xs text-muted-foreground">
                            <tr>
                                <th className="text-right px-4 py-2.5 font-bold">اسم الحساب</th>
                                <th className="text-right px-4 py-2.5 font-bold">النوع</th>
                                <th className="text-right px-4 py-2.5 font-bold">العملة</th>
                                <th className="text-right px-4 py-2.5 font-bold">الرصيد الحالي</th>
                                <th className="text-right px-4 py-2.5 font-bold">الرصيد الافتتاحي</th>
                                <th className="text-right px-4 py-2.5 font-bold">الحركات</th>
                                <th className="text-right px-4 py-2.5 font-bold">الحالة</th>
                                <th className="text-right px-4 py-2.5 font-bold">إجراءات</th>
                            </tr>
                        </thead>
                        <tbody>
                            {loading && <tr><td colSpan={8} className="text-center py-10 text-muted-foreground">جاري التحميل…</td></tr>}
                            {!loading && filtered.length === 0 && (
                                <tr><td colSpan={8} className="text-center py-10 text-muted-foreground" data-testid="accounts-empty">
                                    لا توجد حسابات في هذا التبويب. اضغط "إضافة حساب جديد" للبدء.
                                </td></tr>
                            )}
                            {!loading && filtered.map((a) => {
                                const meta = TYPE_META[a.account_type];
                                const Icon = meta?.icon || Wallet;
                                const stat = STATUS_META[a.status] || STATUS_META.active;
                                return (
                                    <tr key={a.id} className="border-t border-border hover:bg-slate-50/50" data-testid={`account-row-${a.id}`}>
                                        <td className="px-4 py-3">
                                            <Link to={`/accounts/${a.id}`} className="flex items-center gap-2 font-bold text-foreground hover:text-brand" data-testid={`account-link-${a.id}`}>
                                                <Icon size={18} weight="duotone" className="text-brand shrink-0" />
                                                <span>{a.name}</span>
                                                {a.auto_created && (
                                                    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-sky-100 text-sky-800 border border-sky-200" title="تم إنشاؤه تلقائياً من طرق الدفع في الطلبات" data-testid={`account-auto-badge-${a.id}`}>
                                                        <Lightning size={9} weight="fill" /> تلقائي
                                                    </span>
                                                )}
                                            </Link>
                                            {a.provider_name && <div className="text-[11px] text-muted-foreground pr-6">{a.provider_name}</div>}
                                            {a.auto_created && a.orders_count != null && (
                                                <div className="text-[10px] text-sky-700 pr-6 mt-0.5">
                                                    {Number(a.orders_count).toLocaleString("en-US")} طلب · رصيد متوقع التحصيل
                                                </div>
                                            )}
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold border ${meta.cls}`}>
                                                {meta.label}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 text-muted-foreground">{a.currency}</td>
                                        <td className={`px-4 py-3 num font-extrabold ${(a.current_balance || 0) < 0 ? "text-rose-600" : "text-emerald-700"}`}>
                                            {fmt(a.current_balance, a.currency)}
                                        </td>
                                        <td className="px-4 py-3 num text-muted-foreground">{fmt(a.opening_balance, a.currency)}</td>
                                        <td className="px-4 py-3">
                                            <span className="inline-flex items-center px-2 py-0.5 rounded bg-slate-100 text-slate-700 text-xs font-bold">
                                                {a.transactions_count || 0}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold ${stat.cls}`}>
                                                {stat.label}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3">
                                            <div className="flex items-center gap-1">
                                                <button onClick={() => setModal({ initial: a })} className="p-1 rounded text-brand hover:bg-brand/10" data-testid={`account-edit-${a.id}`} title="تعديل">
                                                    <PencilSimple size={16} />
                                                </button>
                                                <button onClick={() => toggleHide(a)} className="p-1 rounded text-amber-700 hover:bg-amber-50" data-testid={`account-hide-${a.id}`} title={a.status === "hidden" ? "إعادة التفعيل" : "إخفاء"}>
                                                    {a.status === "hidden" ? <Eye size={16} /> : <EyeSlash size={16} />}
                                                </button>
                                                <button onClick={() => remove(a)} className="p-1 rounded text-rose-600 hover:bg-rose-50" data-testid={`account-delete-${a.id}`} title="حذف">
                                                    <Trash size={16} />
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            </div>

            <div className="bg-amber-50/60 border border-amber-200 rounded-lg p-3 text-xs text-amber-900 flex items-start gap-2" data-testid="accounts-info">
                <Warning size={18} className="text-amber-700 shrink-0 mt-0.5" weight="duotone" />
                <div>
                    لا يمكن حذف الحساب إذا كان مرتبطاً بحركات مالية — استخدم خيار <strong>الإخفاء</strong> بدلاً منه.
                    الرصيد الافتتاحي يُنشئ حركة تلقائية ضمن سجل الحساب.
                </div>
            </div>

            {modal && catalogue && (
                <AccountFormModal
                    initial={modal.initial}
                    catalogue={catalogue}
                    banks={banks}
                    onClose={() => setModal(null)}
                    onSaved={() => load()}
                />
            )}
        </div>
    );
}
