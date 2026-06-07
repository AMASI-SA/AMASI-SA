/**
 * مركز الإدخال المالي — Financial Input Hub
 *
 * Iter-97 — One-stop entry point so the data-entry user no longer has to
 * navigate between Liabilities / Operating-Expenses / Shipping / Transfers
 * pages. Each tab below is a thin wrapper around an EXISTING endpoint:
 *
 *   1. التزام جديد            → POST /api/liabilities
 *   2. سداد التزام            → POST /api/liabilities/{id}/pay
 *   3. مصروف يومي             → POST /api/operating-expenses/daily   (Iter-94)
 *   4. مديونية على الغير      → POST /api/liabilities (kind=receivable, Iter-97)
 *   5. سلفة موظف              → POST /api/liabilities (kind=salary_advance)
 *   6. دفعة شركة شحن          → POST /api/shipping-accounts/{co}/payments (Iter-95)
 *   7. تحويل COD              → POST /api/transfers (Iter-96)
 *
 * No new collections. No new screens. All seven flows share one shell.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
    Receipt, CreditCard, Truck, Wallet, ArrowsLeftRight,
    HandCoins, Coins, PaperPlaneRight, ListChecks, Bank,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";


const today = () => new Date().toISOString().slice(0, 10);


function fmt(v) {
    return Number(v || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
}


function Field({ label, required, children, full, hint }) {
    return (
        <div className={full ? "sm:col-span-2" : ""}>
            <label className="block text-xs font-bold text-slate-700 mb-1.5">
                {label}{required && <span className="text-rose-600 mr-1">*</span>}
            </label>
            {children}
            {hint && <div className="text-[11px] text-slate-500 mt-1">{hint}</div>}
        </div>
    );
}


const inputCls =
    "w-full px-3 py-2.5 text-sm border border-slate-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-violet-500";


function SectionCard({ title, Icon, hint, children, onSubmit, busy, submitLabel = "حفظ" }) {
    return (
        <form
            onSubmit={(e) => { e.preventDefault(); onSubmit(); }}
            className="bg-white border border-slate-200 rounded-xl p-5 space-y-4"
        >
            <div className="flex items-center gap-2 pb-3 border-b border-slate-100">
                {Icon && <Icon size={22} weight="duotone" className="text-violet-700" />}
                <div>
                    <h2 className="font-bold text-base text-slate-900">{title}</h2>
                    {hint && <p className="text-xs text-slate-500 mt-0.5">{hint}</p>}
                </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">{children}</div>
            <div className="flex justify-end pt-2">
                <button
                    type="submit"
                    disabled={busy}
                    className="px-5 py-2.5 rounded-lg bg-slate-900 text-white text-sm font-bold hover:bg-slate-800 disabled:opacity-50"
                    data-testid="hub-submit-btn"
                >
                    {busy ? "جاري الحفظ…" : submitLabel}
                </button>
            </div>
        </form>
    );
}


// ── Tab 1: New liability (ad / supplier) ────────────────────────────
function NewLiabilityForm({ onSaved }) {
    const [form, setForm] = useState({
        kind: "supplier",
        ad_provider: "snapchat",
        ad_account_label: "",
        supplier_name: "",
        expected_amount: "",
        due_date: today(),
        description: "",
        notes: "",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    const submit = async () => {
        if (!form.expected_amount || Number(form.expected_amount) <= 0) {
            toast.error("أدخل مبلغاً صحيحاً");
            return;
        }
        if (form.kind === "supplier" && !form.supplier_name.trim()) {
            toast.error("اسم المورد/الجهة مطلوب");
            return;
        }
        setBusy(true);
        try {
            const body = {
                kind: form.kind,
                expected_amount: Number(form.expected_amount),
                due_date: form.due_date,
                description: form.description || "",
                notes: form.notes || "",
            };
            if (form.kind === "ad_account") {
                body.ad_provider = form.ad_provider;
                body.ad_account_label = form.ad_account_label || "";
            }
            if (form.kind === "supplier") body.supplier_name = form.supplier_name.trim();
            await api.post("/liabilities", body);
            toast.success("تم تسجيل الالتزام");
            setForm({ ...form, expected_amount: "", supplier_name: "", ad_account_label: "", description: "", notes: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر الحفظ");
        } finally { setBusy(false); }
    };

    return (
        <SectionCard
            title="تسجيل التزام جديد"
            Icon={Receipt}
            hint="فاتورة إعلانية، مستحق لمورد أو شركة شحن، أي التزام مالي على الشركة"
            onSubmit={submit}
            busy={busy}
        >
            <Field label="نوع الالتزام" required>
                <select value={form.kind} onChange={(e) => set("kind", e.target.value)} className={inputCls} data-testid="liab-kind">
                    <option value="supplier">مورد / جهة عامة</option>
                    <option value="ad_account">حساب إعلاني (سناب/تيك توك/ميتا)</option>
                </select>
            </Field>
            {form.kind === "ad_account" ? (
                <>
                    <Field label="المنصة" required>
                        <select value={form.ad_provider} onChange={(e) => set("ad_provider", e.target.value)} className={inputCls} data-testid="liab-ad-provider">
                            <option value="snapchat">Snapchat</option>
                            <option value="tiktok">TikTok</option>
                            <option value="meta">Meta</option>
                        </select>
                    </Field>
                    <Field label="اسم الحساب" full>
                        <input value={form.ad_account_label} onChange={(e) => set("ad_account_label", e.target.value)} className={inputCls} data-testid="liab-ad-label" placeholder="حساب التايجر سناب" />
                    </Field>
                </>
            ) : (
                <Field label="اسم المورد / الجهة" required full>
                    <input value={form.supplier_name} onChange={(e) => set("supplier_name", e.target.value)} className={inputCls} data-testid="liab-supplier-name" placeholder="مثلاً: مطبعة الأنوار، شركة التغليف..." />
                </Field>
            )}
            <Field label="المبلغ (ر.س)" required>
                <input type="number" step="0.01" min="0" value={form.expected_amount} onChange={(e) => set("expected_amount", e.target.value)} className={`${inputCls} num`} data-testid="liab-amount" />
            </Field>
            <Field label="تاريخ الاستحقاق" required>
                <input type="date" value={form.due_date} onChange={(e) => set("due_date", e.target.value)} className={inputCls} data-testid="liab-due-date" />
            </Field>
            <Field label="الوصف" full>
                <input value={form.description} onChange={(e) => set("description", e.target.value)} className={inputCls} placeholder="فاتورة مارس، أو وصف مختصر" data-testid="liab-desc" />
            </Field>
            <Field label="ملاحظات" full>
                <input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="liab-notes" />
            </Field>
        </SectionCard>
    );
}


// ── Tab 2: Pay liability ────────────────────────────────────────────
function PayLiabilityForm({ openLiabilities, banks, onSaved }) {
    const [form, setForm] = useState({
        liability_id: "", paid_from_account_id: "", amount: "", payment_date: today(), notes: "",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const selected = openLiabilities.find((l) => l.id === form.liability_id);

    const submit = async () => {
        if (!form.liability_id) { toast.error("اختر الالتزام"); return; }
        if (!form.paid_from_account_id) { toast.error("اختر الحساب البنكي"); return; }
        const amt = Number(form.amount);
        if (!amt || amt <= 0) { toast.error("أدخل مبلغاً صحيحاً"); return; }
        if (selected && amt > Number(selected.remaining_amount) + 0.01) {
            toast.error(`المبلغ أكبر من المتبقي (${fmt(selected.remaining_amount)})`);
            return;
        }
        setBusy(true);
        try {
            await api.post(`/liabilities/${form.liability_id}/pay`, {
                amount: amt,
                paid_from_account_id: form.paid_from_account_id,
                payment_date: form.payment_date,
                notes: form.notes,
            });
            toast.success("تم تسجيل السداد وخصمه من البنك");
            setForm({ ...form, amount: "", notes: "", liability_id: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };

    return (
        <SectionCard
            title="سداد التزام قائم"
            Icon={CreditCard}
            hint="يخصم تلقائياً من رصيد البنك المختار ويُحدِّث المركز المالي"
            onSubmit={submit}
            busy={busy}
            submitLabel="تسجيل السداد"
        >
            <Field label="الالتزام" required full>
                <select value={form.liability_id} onChange={(e) => set("liability_id", e.target.value)} className={inputCls} data-testid="pay-liability-id">
                    <option value="">— اختر التزاماً مفتوحاً —</option>
                    {openLiabilities.map((l) => (
                        <option key={l.id} value={l.id}>
                            {l.description || l.kind} — متبقٍ {fmt(l.remaining_amount)} ر.س
                            {l.is_overdue ? " ⚠️ متأخر" : ""}
                        </option>
                    ))}
                </select>
            </Field>
            <Field label="الحساب البنكي" required>
                <select value={form.paid_from_account_id} onChange={(e) => set("paid_from_account_id", e.target.value)} className={inputCls} data-testid="pay-account">
                    <option value="">— اختر بنكاً —</option>
                    {banks.map((b) => (
                        <option key={b.id} value={b.id}>{b.name} (الرصيد: {fmt(b.current_balance)} ر.س)</option>
                    ))}
                </select>
            </Field>
            <Field label="مبلغ السداد (ر.س)" required>
                <input type="number" step="0.01" min="0" value={form.amount} onChange={(e) => set("amount", e.target.value)} className={`${inputCls} num`} data-testid="pay-amount" />
            </Field>
            <Field label="تاريخ السداد" required>
                <input type="date" value={form.payment_date} onChange={(e) => set("payment_date", e.target.value)} className={inputCls} data-testid="pay-date" />
            </Field>
            <Field label="ملاحظات" full>
                <input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="pay-notes" />
            </Field>
        </SectionCard>
    );
}


// ── Tab 3: Daily expense (reuses Iter-94 endpoint) ───────────────────
function DailyExpenseForm({ banks, onSaved }) {
    const [form, setForm] = useState({
        date: today(), expense_type: "", description: "", amount: "",
        paid_from_account_id: "", payment_method: "", notes: "",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const submit = async () => {
        if (!form.expense_type.trim()) { toast.error("نوع المصروف مطلوب"); return; }
        if (!Number(form.amount)) { toast.error("أدخل المبلغ"); return; }
        setBusy(true);
        try {
            await api.post("/operating-expenses/daily", {
                date: form.date,
                expense_type: form.expense_type.trim(),
                description: form.description.trim(),
                amount: Number(form.amount),
                paid_from_account_id: form.paid_from_account_id || null,
                payment_method: form.payment_method.trim(),
                notes: form.notes.trim(),
            });
            if (form.paid_from_account_id) {
                toast.success("تم تسجيل المصروف وخصمه من البنك");
            } else {
                toast.warning("تم تسجيل المصروف نقداً (لن يُخصم من أي بنك).");
            }
            setForm({ ...form, expense_type: "", description: "", amount: "", notes: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };
    return (
        <SectionCard title="مصروف يومي" Icon={Wallet} hint="عند اختيار حساب بنكي يُخصم المبلغ تلقائياً" onSubmit={submit} busy={busy}>
            <Field label="التاريخ" required><input type="date" value={form.date} onChange={(e) => set("date", e.target.value)} className={inputCls} data-testid="dexp-date" /></Field>
            <Field label="نوع المصروف" required><input value={form.expense_type} onChange={(e) => set("expense_type", e.target.value)} className={inputCls} placeholder="وقود، صيانة، اشتراك…" data-testid="dexp-type" /></Field>
            <Field label="المبلغ (ر.س)" required><input type="number" step="0.01" min="0" value={form.amount} onChange={(e) => set("amount", e.target.value)} className={`${inputCls} num`} data-testid="dexp-amount" /></Field>
            <Field label="الحساب المدفوع منه">
                <select value={form.paid_from_account_id} onChange={(e) => set("paid_from_account_id", e.target.value)} className={inputCls} data-testid="dexp-account">
                    <option value="">— نقدي (بدون حساب) —</option>
                    {banks.map((b) => <option key={b.id} value={b.id}>{b.name} ({fmt(b.current_balance)} ر.س)</option>)}
                </select>
            </Field>
            <Field label="الوصف" full><input value={form.description} onChange={(e) => set("description", e.target.value)} className={inputCls} data-testid="dexp-desc" /></Field>
            <Field label="ملاحظات" full><input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="dexp-notes" /></Field>
        </SectionCard>
    );
}


// ── Tab 4: Receivable (Iter-97) ─────────────────────────────────────
function ReceivableForm({ onSaved }) {
    const [form, setForm] = useState({
        counterparty_name: "", counterparty_type: "person",
        expected_amount: "", due_date: today(),
        description: "", notes: "",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const submit = async () => {
        if (!form.counterparty_name.trim()) { toast.error("اسم الجهة مطلوب"); return; }
        if (!Number(form.expected_amount)) { toast.error("أدخل المبلغ"); return; }
        setBusy(true);
        try {
            await api.post("/liabilities", {
                kind: "receivable",
                counterparty_name: form.counterparty_name.trim(),
                counterparty_type: form.counterparty_type,
                expected_amount: Number(form.expected_amount),
                due_date: form.due_date,
                description: form.description,
                notes: form.notes,
            });
            toast.success("تم تسجيل المديونية ضمن الأصول المستحقة التحصيل");
            setForm({ ...form, counterparty_name: "", expected_amount: "", description: "", notes: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };
    return (
        <SectionCard title="مديونية على شخص أو جهة" Icon={HandCoins} hint="مبلغ مستحق على الغير — يُحسب كأصل مستحق التحصيل" onSubmit={submit} busy={busy}>
            <Field label="نوع الجهة" required>
                <select value={form.counterparty_type} onChange={(e) => set("counterparty_type", e.target.value)} className={inputCls} data-testid="rec-type">
                    <option value="customer">عميل</option>
                    <option value="employee">موظف (عهدة)</option>
                    <option value="person">شخص</option>
                    <option value="company">جهة/شركة</option>
                </select>
            </Field>
            <Field label="الاسم" required>
                <input value={form.counterparty_name} onChange={(e) => set("counterparty_name", e.target.value)} className={inputCls} data-testid="rec-name" />
            </Field>
            <Field label="المبلغ (ر.س)" required>
                <input type="number" step="0.01" min="0" value={form.expected_amount} onChange={(e) => set("expected_amount", e.target.value)} className={`${inputCls} num`} data-testid="rec-amount" />
            </Field>
            <Field label="تاريخ الاستحقاق" required>
                <input type="date" value={form.due_date} onChange={(e) => set("due_date", e.target.value)} className={inputCls} data-testid="rec-due" />
            </Field>
            <Field label="الوصف" full>
                <input value={form.description} onChange={(e) => set("description", e.target.value)} className={inputCls} placeholder="مثال: قرض قصير، عهدة معدّات…" data-testid="rec-desc" />
            </Field>
            <Field label="ملاحظات" full><input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="rec-notes" /></Field>
        </SectionCard>
    );
}


// ── Tab 5: Salary advance ───────────────────────────────────────────
function AdvanceForm({ employees, banks, onSaved }) {
    const [form, setForm] = useState({
        employee_salary_id: "", paid_from_account_id: "", expected_amount: "",
        due_date: today(), description: "", notes: "",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const submit = async () => {
        if (!form.employee_salary_id) { toast.error("اختر الموظف"); return; }
        if (!form.paid_from_account_id) { toast.error("اختر الحساب البنكي"); return; }
        if (!Number(form.expected_amount)) { toast.error("أدخل المبلغ"); return; }
        setBusy(true);
        try {
            await api.post("/liabilities", {
                kind: "salary_advance",
                employee_salary_id: form.employee_salary_id,
                paid_from_account_id: form.paid_from_account_id,
                expected_amount: Number(form.expected_amount),
                due_date: form.due_date,
                description: form.description,
                notes: form.notes,
            });
            toast.success("تم صرف السلفة وخصمها من البنك. ستُخصم من راتب الشهر القادم تلقائياً.");
            setForm({ ...form, expected_amount: "", description: "", notes: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };
    return (
        <SectionCard title="سلفة موظف" Icon={Coins} hint="تُخصم فوراً من البنك وتُستهلَك تلقائياً من راتب الشهر القادم" onSubmit={submit} busy={busy}>
            <Field label="الموظف" required full>
                <select value={form.employee_salary_id} onChange={(e) => set("employee_salary_id", e.target.value)} className={inputCls} data-testid="adv-employee">
                    <option value="">— اختر موظفاً —</option>
                    {employees.map((e) => <option key={e.id} value={e.id}>{e.name} ({fmt(e.monthly_amount)} ر.س/شهر)</option>)}
                </select>
            </Field>
            <Field label="الحساب البنكي" required>
                <select value={form.paid_from_account_id} onChange={(e) => set("paid_from_account_id", e.target.value)} className={inputCls} data-testid="adv-bank">
                    <option value="">— اختر بنكاً —</option>
                    {banks.map((b) => <option key={b.id} value={b.id}>{b.name} ({fmt(b.current_balance)} ر.س)</option>)}
                </select>
            </Field>
            <Field label="المبلغ (ر.س)" required>
                <input type="number" step="0.01" min="0" value={form.expected_amount} onChange={(e) => set("expected_amount", e.target.value)} className={`${inputCls} num`} data-testid="adv-amount" />
            </Field>
            <Field label="تاريخ الصرف" required>
                <input type="date" value={form.due_date} onChange={(e) => set("due_date", e.target.value)} className={inputCls} data-testid="adv-date" />
            </Field>
            <Field label="الوصف" full><input value={form.description} onChange={(e) => set("description", e.target.value)} className={inputCls} data-testid="adv-desc" /></Field>
            <Field label="ملاحظات" full><input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="adv-notes" /></Field>
        </SectionCard>
    );
}


// ── Tab 6: Shipping payment ─────────────────────────────────────────
function ShippingPaymentForm({ banks, onSaved }) {
    const [form, setForm] = useState({
        company_name: "", amount: "", payment_date: today(),
        invoice_number: "", paid_from_account_id: "", note: "",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const submit = async () => {
        if (!form.company_name.trim()) { toast.error("اسم شركة الشحن مطلوب"); return; }
        if (!Number(form.amount)) { toast.error("أدخل المبلغ"); return; }
        setBusy(true);
        try {
            await api.post(`/shipping-accounts/${encodeURIComponent(form.company_name.trim())}/payments`, {
                amount: Number(form.amount),
                payment_date: form.payment_date,
                invoice_number: form.invoice_number,
                paid_from_account_id: form.paid_from_account_id || null,
                note: form.note,
            });
            if (form.paid_from_account_id) {
                toast.success("تم تسجيل الدفعة وخصمها من البنك");
            } else {
                toast.warning("تم تسجيل الدفعة بدون ربطها بحساب بنكي، لذلك لن تؤثر على رصيد البنك.");
            }
            setForm({ ...form, amount: "", invoice_number: "", note: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };
    return (
        <SectionCard title="دفعة شركة شحن" Icon={Truck} hint="تُسدَّد للمستحقات الآجلة للشركة وتُخصم من البنك المختار" onSubmit={submit} busy={busy}>
            <Field label="شركة الشحن" required>
                <input list="ship-co" value={form.company_name} onChange={(e) => set("company_name", e.target.value)} className={inputCls} placeholder="سمسا، أيميل، مندوب الرياض…" data-testid="ship-company" />
                <datalist id="ship-co">
                    <option value="سمسا" />
                    <option value="أيميل" />
                    <option value="مندوب الرياض" />
                    <option value="Aramex" />
                    <option value="SPL" />
                </datalist>
            </Field>
            <Field label="رقم الفاتورة">
                <input value={form.invoice_number} onChange={(e) => set("invoice_number", e.target.value)} className={inputCls} data-testid="ship-invoice" />
            </Field>
            <Field label="المبلغ (ر.س)" required>
                <input type="number" step="0.01" min="0" value={form.amount} onChange={(e) => set("amount", e.target.value)} className={`${inputCls} num`} data-testid="ship-amount" />
            </Field>
            <Field label="تاريخ الدفع" required>
                <input type="date" value={form.payment_date} onChange={(e) => set("payment_date", e.target.value)} className={inputCls} data-testid="ship-date" />
            </Field>
            <Field label="الحساب البنكي" hint="يُنصح بشدة باختيار حساب بنكي ليُخصم المبلغ ويظهر الأثر في المركز المالي">
                <select value={form.paid_from_account_id} onChange={(e) => set("paid_from_account_id", e.target.value)} className={inputCls} data-testid="ship-account">
                    <option value="">— بدون ربط بحساب —</option>
                    {banks.map((b) => <option key={b.id} value={b.id}>{b.name} ({fmt(b.current_balance)} ر.س)</option>)}
                </select>
            </Field>
            <Field label="ملاحظات" full><input value={form.note} onChange={(e) => set("note", e.target.value)} className={inputCls} data-testid="ship-note" /></Field>
        </SectionCard>
    );
}


// ── Tab 7: COD transfer ─────────────────────────────────────────────
function CodTransferForm({ accounts, banks, onSaved }) {
    const codAccounts = accounts.filter((a) => a.normalized_payment_method === "cash_on_delivery");
    const [companies, setCompanies] = useState([]);
    const [form, setForm] = useState({
        from_account_id: "", to_account_id: "", transfer_date: today(),
        reference: "", shipping_company: "", notes: "",
        // Iter-98 Net-COD
        cod_gross_collected: "", shipping_fee_deducted: "",
        shipping_fee_settles_against: "shipping_payable",
    });
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    useEffect(() => {
        api.get("/shipping-accounts/companies")
            .then((r) => setCompanies(r.data?.items || []))
            .catch(() => setCompanies([]));
    }, []);

    const gross = Number(form.cod_gross_collected) || 0;
    const fee = Number(form.shipping_fee_deducted) || 0;
    const net = Math.max(0, Math.round((gross - fee) * 100) / 100);
    const useNet = gross > 0;

    const submit = async () => {
        if (!form.from_account_id) return toast.error("اختر حساب الدفع عند الاستلام");
        if (!form.to_account_id) return toast.error("اختر البنك الوجهة");
        if (!form.shipping_company.trim()) return toast.error("اختر شركة الشحن");
        if (!gross) return toast.error("أدخل إجمالي COD المحصَّل");
        if (fee > gross) return toast.error("الرسوم أكبر من الإجمالي");
        setBusy(true);
        try {
            await api.post("/transfers", {
                from_account_id: form.from_account_id,
                to_account_id: form.to_account_id,
                amount: net,
                transfer_date: form.transfer_date,
                reference: form.reference,
                shipping_company: form.shipping_company.trim(),
                notes: form.notes,
                cod_gross_collected: gross,
                shipping_fee_deducted: fee,
                shipping_fee_settles_against: form.shipping_fee_settles_against,
            });
            toast.success(`تم: COD ${gross} − رسوم ${fee} = صافي ${net} ر.س محوَّل للبنك`);
            setForm({ ...form, cod_gross_collected: "", shipping_fee_deducted: "", reference: "", notes: "" });
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };

    return (
        <SectionCard title="تحويل من الدفع عند الاستلام إلى بنك" Icon={ArrowsLeftRight} hint="إجمالي ما حصَّلته شركة الشحن، رسومها المخصومة، والصافي المحوَّل" onSubmit={submit} busy={busy}>
            <Field label="من حساب الدفع عند الاستلام" required>
                <select value={form.from_account_id} onChange={(e) => set("from_account_id", e.target.value)} className={inputCls} data-testid="cod-from">
                    <option value="">— اختر حساب COD —</option>
                    {codAccounts.map((a) => <option key={a.id} value={a.id}>{a.name} (متوقَّع: {fmt(a.expected_orders_balance)} ر.س)</option>)}
                </select>
            </Field>
            <Field label="إلى بنك" required>
                <select value={form.to_account_id} onChange={(e) => set("to_account_id", e.target.value)} className={inputCls} data-testid="cod-to">
                    <option value="">— اختر بنكاً —</option>
                    {banks.map((b) => <option key={b.id} value={b.id}>{b.name} ({fmt(b.current_balance)} ر.س)</option>)}
                </select>
            </Field>
            <Field label="شركة الشحن" required full>
                <select value={form.shipping_company} onChange={(e) => set("shipping_company", e.target.value)} className={inputCls} data-testid="cod-shipping">
                    <option value="">— اختر شركة شحن —</option>
                    {companies.map((c) => (
                        <option key={c.canonical} value={c.display}>
                            {c.display}{c.usage_count > 0 ? `  (${c.usage_count} مرة)` : ""}
                        </option>
                    ))}
                </select>
                <div className="text-[11px] text-slate-500 mt-1">القائمة موحَّدة تلقائياً — لا تكرار في الأسماء.</div>
            </Field>
            <Field label="إجمالي COD المحصَّل (ر.س)" required hint="ما حصَّلته شركة الشحن من العملاء فعلاً">
                <input type="number" step="0.01" min="0" value={form.cod_gross_collected} onChange={(e) => set("cod_gross_collected", e.target.value)} className={`${inputCls} num`} data-testid="cod-gross" />
            </Field>
            <Field label="رسوم شركة الشحن المخصومة (ر.س)" hint="اتركها 0 إذا لم تخصم شركة الشحن أي رسوم">
                <input type="number" step="0.01" min="0" value={form.shipping_fee_deducted} onChange={(e) => set("shipping_fee_deducted", e.target.value)} className={`${inputCls} num`} data-testid="cod-fee" />
            </Field>
            <Field label="صافي المحوَّل للبنك (ر.س)" hint="يُحسب تلقائياً">
                <input type="number" value={net.toFixed(2)} readOnly className={`${inputCls} num bg-slate-100 font-bold`} data-testid="cod-net" />
            </Field>
            {fee > 0 && (
                <Field label="معالجة الرسوم" full>
                    <select value={form.shipping_fee_settles_against} onChange={(e) => set("shipping_fee_settles_against", e.target.value)} className={inputCls} data-testid="cod-settle-mode">
                        <option value="shipping_payable">يخصم من ديون شركة الشحن (الافتراضي — الأصح محاسبياً)</option>
                        <option value="expense">مصروف شحن جديد (إذا لم تكن هناك فاتورة سابقة)</option>
                    </select>
                </Field>
            )}
            <Field label="تاريخ التحويل" required>
                <input type="date" value={form.transfer_date} onChange={(e) => set("transfer_date", e.target.value)} className={inputCls} data-testid="cod-date" />
            </Field>
            <Field label="رقم المرجع">
                <input value={form.reference} onChange={(e) => set("reference", e.target.value)} className={inputCls} data-testid="cod-ref" />
            </Field>
            <Field label="ملاحظات" full>
                <input value={form.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} data-testid="cod-notes" />
            </Field>
        </SectionCard>
    );
}


// ── Main Hub ────────────────────────────────────────────────────────
const TABS = [
    { id: "new-liab",   label: "التزام جديد",        Icon: Receipt,         testid: "hub-tab-new-liab" },
    { id: "pay-liab",   label: "سداد التزام",        Icon: CreditCard,      testid: "hub-tab-pay-liab" },
    { id: "daily-exp",  label: "مصروف يومي",         Icon: Wallet,          testid: "hub-tab-daily-exp" },
    { id: "receivable", label: "مديونية على الغير",   Icon: HandCoins,       testid: "hub-tab-receivable" },
    { id: "advance",    label: "سلفة موظف",          Icon: Coins,           testid: "hub-tab-advance" },
    { id: "shipping",   label: "دفعة شركة شحن",      Icon: Truck,           testid: "hub-tab-shipping" },
    { id: "cod",        label: "تحويل COD",          Icon: ArrowsLeftRight, testid: "hub-tab-cod" },
];


export default function FinancialInputHub() {
    const [tab, setTab] = useState("new-liab");
    const [accounts, setAccounts] = useState([]);
    const [employees, setEmployees] = useState([]);
    const [openLiabilities, setOpenLiabilities] = useState([]);
    const [loading, setLoading] = useState(true);

    const banks = useMemo(
        () => accounts.filter((a) => a.account_type === "bank" && a.status !== "hidden" && a.status !== "inactive"),
        [accounts],
    );

    const load = async () => {
        setLoading(true);
        try {
            const [accRes, empRes, unpaidRes, partialRes] = await Promise.all([
                api.get("/accounts"),
                api.get("/operating-expenses/salaries"),
                api.get("/liabilities?status=unpaid&limit=500"),
                api.get("/liabilities?status=partial&limit=500"),
            ]);
            const raw = accRes.data?.accounts || accRes.data?.items || (Array.isArray(accRes.data) ? accRes.data : []);
            setAccounts(raw);
            setEmployees((empRes.data?.items || []).filter((e) => e.status === "active"));
            const openL = [
                ...(unpaidRes.data?.items || []),
                ...(partialRes.data?.items || []),
            ].filter((l) => ["salary", "ad_account", "supplier"].includes(l.kind));
            setOpenLiabilities(openL);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر تحميل البيانات");
        } finally { setLoading(false); }
    };

    useEffect(() => { load(); }, []);

    return (
        <div dir="rtl" data-testid="financial-input-hub-page">
            <div className="mb-5">
                <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900">مركز الإدخال المالي</h1>
                <p className="text-sm text-slate-500 mt-1">
                    نقطة الإدخال الوحيدة لكل العمليات اليومية. كل ما تُدخله ينعكس فوراً على المركز المالي والأصول والالتزامات.
                </p>
            </div>

            {/* Tab bar */}
            <div className="mb-5 -mx-2 overflow-x-auto">
                <div className="px-2 flex gap-2 min-w-max">
                    {TABS.map((t) => {
                        const active = tab === t.id;
                        const Icon = t.Icon;
                        return (
                            <button
                                key={t.id}
                                onClick={() => setTab(t.id)}
                                data-testid={t.testid}
                                className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-bold transition ${
                                    active ? "bg-slate-900 text-white shadow-md" : "bg-white border border-slate-200 text-slate-700 hover:bg-slate-50"
                                }`}
                            >
                                <Icon size={16} weight={active ? "fill" : "duotone"} />
                                <span>{t.label}</span>
                            </button>
                        );
                    })}
                </div>
            </div>

            {loading ? (
                <div className="bg-white border border-slate-200 rounded-xl p-10 text-center text-slate-500">
                    جاري التحميل…
                </div>
            ) : (
                <>
                    {tab === "new-liab"   && <NewLiabilityForm onSaved={load} />}
                    {tab === "pay-liab"   && <PayLiabilityForm openLiabilities={openLiabilities} banks={banks} onSaved={load} />}
                    {tab === "daily-exp"  && <DailyExpenseForm banks={banks} onSaved={load} />}
                    {tab === "receivable" && <ReceivableForm onSaved={load} />}
                    {tab === "advance"    && <AdvanceForm employees={employees} banks={banks} onSaved={load} />}
                    {tab === "shipping"   && <ShippingPaymentForm banks={banks} onSaved={load} />}
                    {tab === "cod"        && <CodTransferForm accounts={accounts} banks={banks} onSaved={load} />}
                </>
            )}

            <div className="mt-4 text-center text-[11px] text-slate-400">
                <ListChecks size={14} className="inline-block mr-1" />
                كل العمليات تستخدم الـ APIs الموجودة — لا توجد طبقة جديدة أو تكرار في الحسابات.
            </div>
        </div>
    );
}
