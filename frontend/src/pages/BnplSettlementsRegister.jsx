/**
 * Iter-221 — تسجيل تسويات Tabby و Tamara (Phase 2b UI).
 *
 * صفحة مخصصة لتسجيل التسويات الأسبوعية يدوياً عبر:
 *     POST /api/bnpl/settlements/register
 *
 * تعتمد كذلك على:
 *     GET /api/bnpl/settlements/registration-overview
 *     GET /api/bnpl/settlements/registered
 *     GET /api/bnpl/settlements/registered/{txn_group_id}
 *     GET /api/accounts                  (لاختيار البنك المستلم)
 *
 * المخرجات تنعكس فوراً في:
 *   - المركز المالي (FinancialPosition.jsx)
 *   - الأصول والحسابات (Accounts.jsx)
 *   - سجل الحركات المالية (LedgerTransactionsPage.jsx)
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const errMsg = (e, fb) =>
    e?.response?.data?.detail
    || e?.response?.data?.error
    || e?.message
    || fb;

const fmt = (n) =>
    (Number(n) || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

const todayISO = () => new Date().toISOString().slice(0, 10);

const PROVIDERS = {
    tabby:  { name: "Tabby",  badge: "🟣", color: "violet" },
    tamara: { name: "Tamara", badge: "🩷", color: "rose" },
};

const STATUS_COLORS = {
    green:  { bg: "bg-emerald-50", border: "border-emerald-200",
              text: "text-emerald-700", label: "مطابق" },
    yellow: { bg: "bg-amber-50",   border: "border-amber-200",
              text: "text-amber-700", label: "فرق بسيط" },
    red:    { bg: "bg-rose-50",    border: "border-rose-200",
              text: "text-rose-700", label: "يحتاج مراجعة" },
};


function StatusPill({ status }) {
    const c = STATUS_COLORS[status] || STATUS_COLORS.green;
    return (
        <span
            data-testid={`match-status-${status}`}
            className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full
                        text-[11px] font-bold ${c.bg} ${c.border} ${c.text}
                        border`}
        >
            <span className="w-1.5 h-1.5 rounded-full bg-current"></span>
            {c.label}
        </span>
    );
}


function MetricCell({ label, value, tone = "slate", testid }) {
    const toneCls = {
        slate:   "text-slate-900",
        emerald: "text-emerald-700",
        rose:    "text-rose-700",
        amber:   "text-amber-700",
        violet:  "text-violet-700",
    }[tone];
    return (
        <div className="flex flex-col gap-0.5" data-testid={testid}>
            <div className="text-[10px] text-slate-500 font-semibold">
                {label}
            </div>
            <div className={`num text-base font-extrabold ${toneCls}`}>
                {value}
            </div>
        </div>
    );
}


function ProviderOverviewCard({ row, onOpenAdd }) {
    const meta = PROVIDERS[row.provider] || { name: row.provider, badge: "💳" };
    const last = row.last_settlement;
    const diffTone =
        Math.abs(row.difference) < 0.5 ? "emerald"
        : row.match_status === "yellow" ? "amber"
        : "rose";
    return (
        <div
            className="rounded-2xl border border-slate-200 bg-white p-5
                       shadow-sm hover:shadow transition-shadow"
            data-testid={`provider-card-${row.provider}`}
        >
            <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                    <h3 className="text-xl font-extrabold text-slate-900
                                   flex items-center gap-2">
                        <span className="text-2xl">{meta.badge}</span>
                        {meta.name}
                    </h3>
                    <div className="mt-1">
                        <StatusPill status={row.match_status} />
                    </div>
                </div>
                <button
                    type="button"
                    onClick={() => onOpenAdd(row.provider)}
                    className="px-3 py-1.5 bg-slate-900 hover:bg-slate-800
                               text-white text-xs font-bold rounded-lg
                               flex items-center gap-1.5"
                    data-testid={`add-settlement-btn-${row.provider}`}
                >
                    <span className="text-base">＋</span>
                    إضافة تسوية {meta.name}
                </button>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-3">
                <MetricCell
                    label="الذمم الحالية"
                    value={`${fmt(row.current_receivable)} ر.س`}
                    tone={row.current_receivable > 0 ? "violet" : "slate"}
                    testid={`metric-current-receivable-${row.provider}`}
                />
                <MetricCell
                    label="المتوقع استلامه"
                    value={`${fmt(row.expected_total)} ر.س`}
                    testid={`metric-expected-${row.provider}`}
                />
                <MetricCell
                    label="تم استلامه"
                    value={`${fmt(row.received_total)} ر.س`}
                    tone={row.received_total > 0 ? "emerald" : "slate"}
                    testid={`metric-received-${row.provider}`}
                />
                <MetricCell
                    label="الفرق"
                    value={`${row.difference >= 0 ? "" : "−"}${fmt(Math.abs(row.difference))} ر.س`}
                    tone={diffTone}
                    testid={`metric-diff-${row.provider}`}
                />
            </div>

            <div className="bg-slate-50 rounded-xl p-3 border border-slate-100">
                <div className="text-[10px] font-bold text-slate-500 mb-1">
                    آخر تسوية مسجَّلة
                </div>
                {last ? (
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1
                                    text-xs text-slate-700">
                        <span className="font-bold num">
                            {fmt(last.amount)} ر.س
                        </span>
                        <span>·</span>
                        <span className="text-slate-500">
                            مرجع: <span className="font-mono text-slate-700">
                                {last.reference || "—"}
                            </span>
                        </span>
                        {last.settlement_date && (
                            <>
                                <span>·</span>
                                <span className="text-slate-500 num">
                                    {last.settlement_date}
                                </span>
                            </>
                        )}
                        {last.bank_account_name && (
                            <>
                                <span>·</span>
                                <span className="text-slate-500">
                                    {last.bank_account_name}
                                </span>
                            </>
                        )}
                    </div>
                ) : (
                    <div className="text-xs text-slate-400">
                        لا توجد تسويات مسجَّلة بعد لهذا المزوّد.
                    </div>
                )}
            </div>
        </div>
    );
}


function AddSettlementModal({ provider, banks, onClose, onSaved }) {
    const meta = PROVIDERS[provider] || { name: provider, badge: "💳" };
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState({
        settlement_reference: "",
        settlement_date: todayISO(),
        bank_account_id: banks?.[0]?.id || "",
        transferred_amount: "",
        commission: "",
        commission_vat: "",
        settlement_fee: "",
        notes: "",
    });

    const total = useMemo(() => {
        return [
            form.transferred_amount, form.commission,
            form.commission_vat, form.settlement_fee,
        ].reduce((s, v) => s + (parseFloat(v) || 0), 0);
    }, [form]);

    const set = (k) => (e) =>
        setForm((f) => ({ ...f, [k]: e.target.value }));

    const save = async () => {
        if (!form.settlement_reference.trim()) {
            toast.error("رقم التسوية مطلوب");
            return;
        }
        if (!form.bank_account_id) {
            toast.error("اختر البنك المستلم");
            return;
        }
        if (total <= 0) {
            toast.error("يجب إدخال مبلغ تسوية موجب");
            return;
        }
        setSaving(true);
        try {
            const payload = {
                provider,
                bank_account_id: form.bank_account_id,
                transferred_amount: parseFloat(form.transferred_amount) || 0,
                commission: parseFloat(form.commission) || 0,
                commission_vat: parseFloat(form.commission_vat) || 0,
                settlement_fee: parseFloat(form.settlement_fee) || 0,
                settlement_reference: form.settlement_reference.trim(),
                settlement_date: form.settlement_date || null,
                notes: form.notes || "",
            };
            const { data } = await api.post(
                "/bnpl/settlements/register", payload,
            );
            if (data?.skipped) {
                toast.info("تم تسجيل هذه التسوية مسبقاً — لم يُنشأ قيد جديد.");
            } else {
                toast.success("تم تسجيل التسوية وإنشاء القيد بنجاح.");
            }
            onSaved(data.txn_group_id);
        } catch (e) {
            toast.error(errMsg(e, "فشل تسجيل التسوية"));
        } finally {
            setSaving(false);
        }
    };

    return (
        <div
            className="fixed inset-0 bg-black/40 z-50 flex items-center
                       justify-center p-4"
            onClick={onClose}
            data-testid="add-settlement-modal"
        >
            <div
                className="bg-white rounded-2xl shadow-2xl w-full max-w-xl
                           max-h-[90vh] overflow-y-auto"
                onClick={(e) => e.stopPropagation()}
                dir="rtl"
            >
                <div className="p-5 border-b border-slate-200 sticky top-0
                                bg-white z-10">
                    <h2 className="text-lg font-extrabold text-slate-900
                                   flex items-center gap-2">
                        <span className="text-2xl">{meta.badge}</span>
                        إضافة تسوية {meta.name}
                    </h2>
                    <p className="text-xs text-slate-500 mt-1">
                        ستنشئ هذه العملية قيداً متوازناً في الدفتر العام.
                    </p>
                </div>

                <div className="p-5 space-y-4">
                    <FormField label="رقم التسوية / المرجع" required>
                        <input
                            type="text"
                            value={form.settlement_reference}
                            onChange={set("settlement_reference")}
                            placeholder="مثال: TBY-W42-001"
                            className="w-full px-3 py-2 rounded-lg border
                                       border-slate-300 text-sm font-mono
                                       focus:outline-none focus:ring-2
                                       focus:ring-slate-900/10"
                            data-testid="input-settlement-reference"
                        />
                    </FormField>

                    <div className="grid grid-cols-2 gap-3">
                        <FormField label="تاريخ التسوية">
                            <input
                                type="date"
                                value={form.settlement_date}
                                onChange={set("settlement_date")}
                                className="w-full px-3 py-2 rounded-lg border
                                           border-slate-300 text-sm num"
                                data-testid="input-settlement-date"
                            />
                        </FormField>
                        <FormField label="البنك / الصندوق المستلم" required>
                            <select
                                value={form.bank_account_id}
                                onChange={set("bank_account_id")}
                                className="w-full px-3 py-2 rounded-lg border
                                           border-slate-300 text-sm bg-white"
                                data-testid="input-bank-account"
                            >
                                <option value="">— اختر —</option>
                                {(banks || []).map((b) => (
                                    <option key={b.id} value={b.id}>
                                        {b.name}
                                        {b.account_type === "cash"
                                            ? " (صندوق)"
                                            : ""}
                                    </option>
                                ))}
                            </select>
                        </FormField>
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                        <FormField label="المبلغ المحوَّل">
                            <NumInput
                                value={form.transferred_amount}
                                onChange={set("transferred_amount")}
                                placeholder="0.00"
                                testid="input-transferred-amount"
                            />
                        </FormField>
                        <FormField label="العمولة">
                            <NumInput
                                value={form.commission}
                                onChange={set("commission")}
                                placeholder="0.00"
                                testid="input-commission"
                            />
                        </FormField>
                        <FormField label="ضريبة العمولة (VAT)">
                            <NumInput
                                value={form.commission_vat}
                                onChange={set("commission_vat")}
                                placeholder="0.00"
                                testid="input-commission-vat"
                            />
                        </FormField>
                        <FormField label="رسوم التسوية">
                            <NumInput
                                value={form.settlement_fee}
                                onChange={set("settlement_fee")}
                                placeholder="0.00"
                                testid="input-settlement-fee"
                            />
                        </FormField>
                    </div>

                    <FormField label="ملاحظات (اختياري)">
                        <textarea
                            rows={2}
                            value={form.notes}
                            onChange={set("notes")}
                            className="w-full px-3 py-2 rounded-lg border
                                       border-slate-300 text-sm"
                            data-testid="input-notes"
                        />
                    </FormField>

                    <div className="bg-slate-50 rounded-xl border
                                    border-slate-200 p-3 flex items-baseline
                                    justify-between">
                        <span className="text-xs font-bold text-slate-600">
                            إجمالي ما سيُغلق من الذمم
                        </span>
                        <span className="num text-lg font-extrabold
                                         text-slate-900"
                              data-testid="modal-total">
                            {fmt(total)} ر.س
                        </span>
                    </div>
                </div>

                <div className="p-4 border-t border-slate-200 flex justify-end
                                gap-2 sticky bottom-0 bg-white">
                    <button
                        type="button"
                        onClick={onClose}
                        className="px-4 py-2 rounded-lg border
                                   border-slate-300 text-sm font-bold
                                   text-slate-700 hover:bg-slate-50"
                        data-testid="modal-cancel"
                    >
                        إلغاء
                    </button>
                    <button
                        type="button"
                        onClick={save}
                        disabled={saving}
                        className="px-4 py-2 rounded-lg bg-slate-900
                                   hover:bg-slate-800 text-white text-sm
                                   font-bold disabled:opacity-60"
                        data-testid="modal-save"
                    >
                        {saving ? "جارٍ الحفظ…" : "حفظ وإنشاء القيد"}
                    </button>
                </div>
            </div>
        </div>
    );
}


function FormField({ label, children, required }) {
    return (
        <label className="block">
            <span className="text-[11px] font-bold text-slate-600 mb-1
                             inline-block">
                {label}
                {required && <span className="text-rose-600 mr-0.5">*</span>}
            </span>
            {children}
        </label>
    );
}


function NumInput({ value, onChange, placeholder, testid }) {
    return (
        <input
            type="number"
            step="0.01"
            min="0"
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            className="w-full px-3 py-2 rounded-lg border border-slate-300
                       text-sm num focus:outline-none focus:ring-2
                       focus:ring-slate-900/10"
            data-testid={testid}
        />
    );
}


function LedgerEntryModal({ txnGroupId, onClose }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                const { data } = await api.get(
                    `/bnpl/settlements/registered/${txnGroupId}`,
                );
                if (alive) setData(data);
            } catch (e) {
                if (alive) toast.error(errMsg(e, "فشل تحميل القيد"));
            } finally {
                if (alive) setLoading(false);
            }
        })();
        return () => { alive = false; };
    }, [txnGroupId]);

    return (
        <div
            className="fixed inset-0 bg-black/40 z-50 flex items-center
                       justify-center p-4"
            onClick={onClose}
            data-testid="ledger-entry-modal"
        >
            <div
                className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl
                           max-h-[90vh] overflow-y-auto"
                onClick={(e) => e.stopPropagation()}
                dir="rtl"
            >
                <div className="p-5 border-b border-slate-200 sticky top-0
                                bg-white z-10">
                    <h2 className="text-lg font-extrabold text-slate-900">
                        تفاصيل القيد المحاسبي
                    </h2>
                    <div className="text-[11px] text-slate-500 font-mono mt-0.5">
                        {txnGroupId}
                    </div>
                </div>

                <div className="p-5">
                    {loading ? (
                        <div className="text-center text-slate-500 py-8">
                            جارٍ التحميل…
                        </div>
                    ) : !data ? (
                        <div className="text-center text-slate-400 py-8">
                            لم نتمكن من تحميل القيد.
                        </div>
                    ) : (
                        <>
                            <div className="grid grid-cols-2 sm:grid-cols-4
                                            gap-3 mb-4 bg-slate-50 rounded-xl
                                            p-3 border border-slate-200">
                                <MetricCell
                                    label="المزوّد"
                                    value={
                                        (PROVIDERS[data.meta?.provider]?.name)
                                        || data.meta?.provider}
                                />
                                <MetricCell
                                    label="رقم التسوية"
                                    value={data.meta?.settlement_reference}
                                />
                                <MetricCell
                                    label="التاريخ"
                                    value={data.meta?.settlement_date || "—"}
                                />
                                <MetricCell
                                    label="البنك"
                                    value={data.meta?.bank_account_name
                                            || "—"}
                                />
                            </div>

                            <div className="overflow-x-auto border
                                            border-slate-200 rounded-xl">
                                <table className="w-full text-xs">
                                    <thead className="bg-slate-50">
                                        <tr className="text-slate-600
                                                       font-bold">
                                            <th className="text-right px-3 py-2">
                                                #</th>
                                            <th className="text-right px-3 py-2">
                                                الحساب</th>
                                            <th className="text-right px-3 py-2">
                                                النوع الفرعي</th>
                                            <th className="text-left px-3 py-2 num">
                                                مدين</th>
                                            <th className="text-left px-3 py-2 num">
                                                دائن</th>
                                            <th className="text-right px-3 py-2">
                                                ملاحظة</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {(data.entries || []).map((e, i) => (
                                            <tr
                                                key={e.id || i}
                                                className="border-t
                                                           border-slate-100"
                                                data-testid={`ledger-leg-${i}`}
                                            >
                                                <td className="px-3 py-2
                                                               text-slate-500 num">
                                                    {e.entry_no}
                                                </td>
                                                <td className="px-3 py-2
                                                               text-slate-900
                                                               font-semibold">
                                                    {e.entity_type}.{e.entity_id}
                                                </td>
                                                <td className="px-3 py-2
                                                               text-slate-500">
                                                    {e.sub_account || "—"}
                                                </td>
                                                <td className="px-3 py-2
                                                               text-emerald-700
                                                               num text-left">
                                                    {e.side === "debit"
                                                      ? fmt(e.amount) : ""}
                                                </td>
                                                <td className="px-3 py-2
                                                               text-rose-700
                                                               num text-left">
                                                    {e.side === "credit"
                                                      ? fmt(e.amount) : ""}
                                                </td>
                                                <td className="px-3 py-2
                                                               text-slate-600">
                                                    {e.notes || ""}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                    <tfoot>
                                        <tr className="border-t
                                                       border-slate-300 bg-slate-50
                                                       font-extrabold">
                                            <td className="px-3 py-2"
                                                colSpan={3}>
                                                الإجمالي
                                            </td>
                                            <td className="px-3 py-2
                                                           text-left num
                                                           text-emerald-800">
                                                {fmt(data.debit_total)}
                                            </td>
                                            <td className="px-3 py-2
                                                           text-left num
                                                           text-rose-800">
                                                {fmt(data.credit_total)}
                                            </td>
                                            <td className="px-3 py-2">
                                                {data.balanced
                                                  ? "✅ متوازن"
                                                  : "⚠ غير متوازن"}
                                            </td>
                                        </tr>
                                    </tfoot>
                                </table>
                            </div>
                        </>
                    )}
                </div>

                <div className="p-4 border-t border-slate-200 flex justify-end
                                sticky bottom-0 bg-white">
                    <button
                        type="button"
                        onClick={onClose}
                        className="px-4 py-2 rounded-lg bg-slate-900
                                   hover:bg-slate-800 text-white text-sm
                                   font-bold"
                        data-testid="ledger-modal-close"
                    >
                        إغلاق
                    </button>
                </div>
            </div>
        </div>
    );
}


export default function BnplSettlementsRegister() {
    const [overview, setOverview] = useState({ providers: [] });
    const [recent, setRecent] = useState([]);
    const [banks, setBanks] = useState([]);
    const [loading, setLoading] = useState(true);
    const [addFor, setAddFor] = useState(null);
    const [showEntry, setShowEntry] = useState(null);

    const load = async () => {
        setLoading(true);
        try {
            const [{ data: ov }, { data: list }, { data: accs }] = await Promise.all([
                api.get("/bnpl/settlements/registration-overview"),
                api.get("/bnpl/settlements/registered", {
                    params: { limit: 25 },
                }),
                api.get("/accounts"),
            ]);
            setOverview(ov || { providers: [] });
            setRecent(list?.items || []);
            const allBanks = (Array.isArray(accs) ? accs : (accs?.items || []))
                .filter((a) => ["bank", "cash"].includes(a.account_type));
            setBanks(allBanks);
        } catch (e) {
            toast.error(errMsg(e, "فشل تحميل البيانات"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    return (
        <div className="space-y-6" dir="rtl" data-testid="bnpl-register-page">
            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold
                                   text-slate-900">
                        تسويات تمارا وتابي
                    </h1>
                    <p className="text-xs text-slate-500 mt-1">
                        تسجيل التسويات الأسبوعية وإنشاء القيود المحاسبية في
                        الدفتر العام، مع مطابقة فورية مع المتوقَّع.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={load}
                    className="px-3 py-2 rounded-lg border border-slate-300
                               text-xs font-bold text-slate-700 hover:bg-slate-50
                               disabled:opacity-60"
                    disabled={loading}
                    data-testid="refresh-btn"
                >
                    {loading ? "جارٍ التحديث…" : "تحديث"}
                </button>
            </div>

            {/* Overview cards */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {(overview.providers || []).map((row) => (
                    <ProviderOverviewCard
                        key={row.provider}
                        row={row}
                        onOpenAdd={setAddFor}
                    />
                ))}
                {loading && !overview.providers?.length && (
                    <div className="text-center text-slate-400 py-10
                                    border border-dashed border-slate-300
                                    rounded-2xl col-span-full">
                        جارٍ التحميل…
                    </div>
                )}
            </div>

            {/* Recent settlements list */}
            <div className="bg-white border border-slate-200 rounded-2xl p-5">
                <h2 className="text-base font-extrabold text-slate-900 mb-3">
                    آخر التسويات المسجَّلة
                </h2>
                {recent.length === 0 ? (
                    <div className="text-center text-slate-400 py-6 text-sm">
                        لا توجد تسويات مسجَّلة بعد.
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead>
                                <tr className="text-slate-500 font-bold
                                               border-b border-slate-200">
                                    <th className="text-right py-2 px-2">
                                        المزوّد</th>
                                    <th className="text-right py-2 px-2">
                                        المرجع</th>
                                    <th className="text-right py-2 px-2">
                                        التاريخ</th>
                                    <th className="text-right py-2 px-2">
                                        البنك</th>
                                    <th className="text-left py-2 px-2 num">
                                        المُحوَّل</th>
                                    <th className="text-left py-2 px-2 num">
                                        العمولة</th>
                                    <th className="text-left py-2 px-2 num">
                                        VAT</th>
                                    <th className="text-left py-2 px-2 num">
                                        رسوم</th>
                                    <th className="text-left py-2 px-2 num">
                                        إجمالي مُغلَق</th>
                                    <th className="text-center py-2 px-2"></th>
                                </tr>
                            </thead>
                            <tbody>
                                {recent.map((r) => {
                                    const meta = PROVIDERS[r.provider] || {};
                                    return (
                                        <tr
                                            key={r.txn_group_id}
                                            className="border-b border-slate-100
                                                       hover:bg-slate-50"
                                            data-testid={`recent-row-${r.settlement_reference}`}
                                        >
                                            <td className="py-2 px-2 font-bold
                                                           text-slate-900">
                                                {meta.badge} {meta.name || r.provider}
                                            </td>
                                            <td className="py-2 px-2 font-mono
                                                           text-slate-700">
                                                {r.settlement_reference}
                                            </td>
                                            <td className="py-2 px-2 text-slate-500
                                                           num">
                                                {r.settlement_date || (r.posted_at || "").slice(0, 10)}
                                            </td>
                                            <td className="py-2 px-2 text-slate-600">
                                                {r.bank_account_name || "—"}
                                            </td>
                                            <td className="py-2 px-2 num text-left
                                                           text-emerald-700">
                                                {fmt(r.transferred_amount)}
                                            </td>
                                            <td className="py-2 px-2 num text-left
                                                           text-slate-600">
                                                {fmt(r.commission)}
                                            </td>
                                            <td className="py-2 px-2 num text-left
                                                           text-slate-600">
                                                {fmt(r.commission_vat)}
                                            </td>
                                            <td className="py-2 px-2 num text-left
                                                           text-slate-600">
                                                {fmt(r.settlement_fee)}
                                            </td>
                                            <td className="py-2 px-2 num text-left
                                                           font-bold text-slate-900">
                                                {fmt(r.total_closed)}
                                            </td>
                                            <td className="py-2 px-2 text-center">
                                                <button
                                                    type="button"
                                                    className="text-violet-700
                                                               text-[11px]
                                                               font-bold hover:underline"
                                                    onClick={() =>
                                                        setShowEntry(r.txn_group_id)
                                                    }
                                                    data-testid={`view-entry-${r.settlement_reference}`}
                                                >
                                                    عرض القيد
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

            {addFor && (
                <AddSettlementModal
                    provider={addFor}
                    banks={banks}
                    onClose={() => setAddFor(null)}
                    onSaved={(txnGroupId) => {
                        setAddFor(null);
                        load();
                        if (txnGroupId) setShowEntry(txnGroupId);
                    }}
                />
            )}
            {showEntry && (
                <LedgerEntryModal
                    txnGroupId={showEntry}
                    onClose={() => setShowEntry(null)}
                />
            )}
        </div>
    );
}
