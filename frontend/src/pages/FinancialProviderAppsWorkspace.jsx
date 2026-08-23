import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
    Buildings,
    CheckCircle,
    CreditCard,
    FileText,
    Plus,
    Receipt,
    ShieldCheck,
    Truck,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import { todaySA } from "../lib/dates";
import {
    createProviderTaxInvoice,
    getFinancialProviderApps,
} from "../services/financialProviderApps";

const fmt = (value) => Number(value || 0).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
});

const KIND_LABELS = {
    payment_gateway: "بوابة دفع",
    bnpl: "دفع وتمويل",
    shipping_company: "شركة شحن",
};

function Stat({ label, value, hint, Icon }) {
    return (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-start justify-between gap-3">
                <div>
                    <div className="text-xs font-extrabold text-slate-500">{label}</div>
                    <div className="mt-2 font-mono text-3xl font-black text-slate-950">{value}</div>
                </div>
                <div className="rounded-lg bg-emerald-50 p-2 text-emerald-700">
                    <Icon size={22} weight="duotone" />
                </div>
            </div>
            <div className="mt-2 text-[11px] font-semibold text-slate-400">{hint}</div>
        </div>
    );
}

function FeeRule({ rule }) {
    const values = [];
    if (rule.min_amount !== undefined) {
        const lower = `${rule.min_inclusive === false ? ">" : "≥"} ${fmt(rule.min_amount)}`;
        const upper = rule.max_amount == null
            ? "بدون حد أعلى"
            : `${rule.max_inclusive === false ? "<" : "≤"} ${fmt(rule.max_amount)}`;
        values.push(`المبلغ ${lower} و${upper}`);
    }
    if (rule.commission_percent !== undefined) values.push(`نسبة ${rule.commission_percent}%`);
    if (rule.fixed_fee !== undefined) values.push(`ثابت ${fmt(rule.fixed_fee)} ر.س`);
    if (rule.cost_per_order !== undefined) values.push(`تكلفة/طلب ${fmt(rule.cost_per_order)} ر.س`);
    if (rule.cod_fee_percent !== undefined) values.push(`COD ${rule.cod_fee_percent}%`);
    if (rule.cod_fee_fixed_per_order !== undefined) values.push(`COD ثابت ${fmt(rule.cod_fee_fixed_per_order)} ر.س`);
    if (rule.vat_percent !== undefined) values.push(`ضريبة ${rule.vat_percent}%`);
    return (
        <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-xs">
            <div className="font-extrabold text-slate-700">{rule.name || rule.code}</div>
            <div className="mt-1 flex flex-wrap gap-1.5 text-[11px] font-bold text-slate-500">
                {values.map((value) => (
                    <span key={value} className="rounded-full border border-slate-200 bg-white px-2 py-1">
                        {value}
                    </span>
                ))}
            </div>
        </div>
    );
}

function InvoiceForm({ app, onClose, onSaved }) {
    const [form, setForm] = useState({
        invoice_number: "",
        issue_date: todaySA(),
        net_amount: "",
        vat_amount: "",
        total_amount: "",
        verification_status: "draft",
        evidence_ref: "",
        notes: "",
    });
    const [busy, setBusy] = useState(false);

    const set = (key, value) => setForm((current) => ({ ...current, [key]: value }));
    const submit = async (event) => {
        event.preventDefault();
        const payload = {
            ...form,
            net_amount: Number(form.net_amount || 0),
            vat_amount: Number(form.vat_amount || 0),
            total_amount: Number(form.total_amount || 0),
            evidence_ref: form.evidence_ref || null,
            notes: form.notes || null,
        };
        if (Math.abs(payload.net_amount + payload.vat_amount - payload.total_amount) > 0.01) {
            toast.error("الإجمالي يجب أن يساوي قبل الضريبة + الضريبة");
            return;
        }
        setBusy(true);
        try {
            await createProviderTaxInvoice(app.providerId, payload);
            toast.success("تم حفظ الفاتورة داخل حساب المزود كمستند إثبات");
            await onSaved();
            onClose();
        } catch (error) {
            const detail = error?.response?.data?.detail;
            const message = typeof detail === "string"
                ? detail
                : Array.isArray(detail)
                    ? detail.map((row) => row?.msg).filter(Boolean).join(" · ")
                    : "تعذر حفظ الفاتورة";
            toast.error(message);
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4" role="dialog" aria-modal="true" aria-label="إضافة فاتورة مزود">
            <form onSubmit={submit} className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-5 shadow-2xl">
                <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-3">
                    <div>
                        <h2 className="text-lg font-black text-slate-950">فاتورة ضريبية — {app.displayName}</h2>
                        <p className="mt-1 text-xs font-semibold text-slate-500">تُحفظ كمستند إثبات، ولا تنشئ قيدًا تلقائيًا حتى لا تتكرر التسوية.</p>
                    </div>
                    <button type="button" onClick={onClose} className="rounded-lg border px-3 py-1.5 text-xs font-extrabold">إغلاق</button>
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <label className="text-xs font-bold text-slate-700">رقم الفاتورة
                        <input required value={form.invoice_number} onChange={(e) => set("invoice_number", e.target.value)} className="mt-1 h-10 w-full rounded-lg border px-3" />
                    </label>
                    <label className="text-xs font-bold text-slate-700">تاريخ الإصدار
                        <input required type="date" value={form.issue_date} onChange={(e) => set("issue_date", e.target.value)} className="mt-1 h-10 w-full rounded-lg border px-3" />
                    </label>
                    <label className="text-xs font-bold text-slate-700">قبل الضريبة
                        <input required type="number" min="0" step="0.01" value={form.net_amount} onChange={(e) => set("net_amount", e.target.value)} className="mt-1 h-10 w-full rounded-lg border px-3 font-mono" />
                    </label>
                    <label className="text-xs font-bold text-slate-700">ضريبة الرسوم
                        <input required type="number" min="0" step="0.01" value={form.vat_amount} onChange={(e) => set("vat_amount", e.target.value)} className="mt-1 h-10 w-full rounded-lg border px-3 font-mono" />
                    </label>
                    <label className="text-xs font-bold text-slate-700">الإجمالي
                        <input required type="number" min="0.01" step="0.01" value={form.total_amount} onChange={(e) => set("total_amount", e.target.value)} className="mt-1 h-10 w-full rounded-lg border px-3 font-mono" />
                    </label>
                    <label className="text-xs font-bold text-slate-700">حالة التحقق
                        <select value={form.verification_status} onChange={(e) => set("verification_status", e.target.value)} className="mt-1 h-10 w-full rounded-lg border px-3">
                            <option value="draft">مسودة تحتاج مطابقة</option>
                            <option value="verified">متحقق منها</option>
                        </select>
                    </label>
                    <label className="text-xs font-bold text-slate-700 sm:col-span-2">مرجع المستند / رابط الحفظ {form.verification_status === "verified" ? "*" : ""}
                        <input required={form.verification_status === "verified"} value={form.evidence_ref} onChange={(e) => set("evidence_ref", e.target.value)} className="mt-1 h-10 w-full rounded-lg border px-3" placeholder="رقم ملف، مسار أرشيف، أو رابط موثوق" />
                    </label>
                    <label className="text-xs font-bold text-slate-700 sm:col-span-2">ملاحظات
                        <textarea value={form.notes} onChange={(e) => set("notes", e.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2" rows={2} />
                    </label>
                </div>
                <div className="mt-4 flex justify-end gap-2 border-t border-slate-100 pt-4">
                    <button type="button" onClick={onClose} className="rounded-lg border px-4 py-2 text-sm font-extrabold">إلغاء</button>
                    <button disabled={busy} className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-extrabold text-white disabled:opacity-50">
                        {busy ? "جاري الحفظ…" : "حفظ مستند الفاتورة"}
                    </button>
                </div>
            </form>
        </div>
    );
}

function ProviderCard({ app, onInvoice }) {
    const Icon = app.kind === "shipping_company" ? Truck : CreditCard;
    return (
        <article className="flex h-full flex-col rounded-xl border border-slate-200 bg-white p-4 sm:p-5" data-testid={`financial-provider-${app.providerCode}`}>
            <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                    <div className="rounded-xl bg-emerald-50 p-3 text-emerald-700"><Icon size={24} weight="duotone" /></div>
                    <div className="min-w-0">
                        <h2 className="truncate text-lg font-black text-slate-950">{app.displayName}</h2>
                        <div className="text-xs font-bold text-slate-400">{KIND_LABELS[app.kind] || app.kind}</div>
                    </div>
                </div>
                <span className={`rounded-full border px-2 py-1 text-[10px] font-extrabold ${app.configured ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
                    {app.configured ? "إعداد موجود" : "يحتاج إعداد"}
                </span>
            </div>

            <div className="mt-4 rounded-xl border border-sky-100 bg-sky-50 p-3 text-xs text-sky-900">
                <div className="font-extrabold">المصدر الفعلي يعلو على التقدير</div>
                <div className="mt-1 leading-5">الفاتورة أو كشف التسوية الرسمي هو المرجع. النسب أدناه للتقدير فقط عند غياب المستند.</div>
            </div>

            <div className="mt-3 space-y-2">
                {app.feeRules.length ? app.feeRules.map((rule, index) => (
                    <FeeRule key={`${rule.code}-${index}`} rule={rule} />
                )) : (
                    <div className="rounded-lg border border-dashed p-3 text-xs text-amber-700">لا توجد قاعدة رسوم مضبوطة لهذا المزود.</div>
                )}
            </div>

            {app.kind === "shipping_company" && app.settlementNettingSupported && (
                <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-[11px] font-semibold leading-5 text-amber-900">
                    تدعم المقاصة: قد تخصم الشركة الشحن وعمولة COD وضريبتها من التحصيل، وقد تكون قيمة التحويل للبنك صفرًا.
                    <div className="mt-2"><Link to="/shipping/settings" className="font-extrabold text-amber-950 underline">ضبط شرائح عمولة COD</Link></div>
                </div>
            )}

            <div className="mt-4 grid grid-cols-2 gap-2 rounded-xl border border-slate-100 p-3 text-xs">
                <div><div className="text-slate-400">الفواتير</div><div className="mt-1 font-mono font-black">{app.taxInvoiceCount}</div></div>
                <div><div className="text-slate-400">إجمالي المستندات</div><div className="mt-1 font-mono font-black">{fmt(app.taxInvoiceTotal)} ر.س</div></div>
                <div className="col-span-2 border-t border-slate-100 pt-2 text-[11px] text-slate-500">
                    {app.latestTaxInvoice
                        ? `آخر فاتورة: ${app.latestTaxInvoice.invoice_number} · ${app.latestTaxInvoice.issue_date}`
                        : "لم تُرفق فاتورة ضريبية بعد."}
                </div>
            </div>

            <div className="mt-auto grid gap-2 border-t border-slate-100 pt-4 sm:grid-cols-2">
                <button type="button" onClick={() => onInvoice(app)} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-emerald-800 px-3 text-xs font-extrabold text-white">
                    <Plus size={16} weight="bold" /> إضافة فاتورة
                </button>
                <Link to={app.operation?.href || "/new-transaction"} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 px-3 text-center text-xs font-extrabold text-slate-700">
                    <Receipt size={16} weight="bold" /> {app.operation?.label || "تسجيل حركة"}
                </Link>
            </div>
        </article>
    );
}

export default function FinancialProviderAppsWorkspace() {
    const navigate = useNavigate();
    const [model, setModel] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [invoiceApp, setInvoiceApp] = useState(null);
    const [filter, setFilter] = useState("all");

    const load = async () => {
        setLoading(true);
        setError("");
        try {
            setModel(await getFinancialProviderApps());
        } catch (_error) {
            setError("تعذر تحميل حسابات المزودين المالية.");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);
    const visible = useMemo(() => (model?.apps || []).filter((app) => (
        filter === "all"
        || (filter === "shipping" && app.kind === "shipping_company")
        || (filter === "payment" && app.kind !== "shipping_company")
    )), [model, filter]);

    if (loading) return <div className="rounded-xl border bg-white p-12 text-center text-sm font-bold text-slate-500">جاري تحميل حسابات الشحن والدفع…</div>;

    return (
        <div className="space-y-5" dir="rtl" data-testid="financial-provider-apps-workspace">
            <header className="overflow-hidden rounded-xl border border-emerald-950 bg-emerald-950 text-white">
                <div className="grid gap-5 p-5 sm:p-7 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                    <div>
                        <div className="mb-3 flex flex-wrap gap-2">
                            <button type="button" onClick={() => navigate("/integrations-v2")} className="rounded-full border border-emerald-700 bg-emerald-900 px-3 py-1 text-xs font-extrabold">كل التطبيقات</button>
                            <button type="button" onClick={() => navigate("/integrations-v2?workspace=accounts")} className="rounded-full border border-emerald-700 bg-emerald-900 px-3 py-1 text-xs font-extrabold">الحسابات الإعلانية</button>
                            <span className="rounded-full border border-white bg-white px-3 py-1 text-xs font-extrabold text-emerald-950">حسابات الشحن والدفع</span>
                        </div>
                        <h1 className="text-2xl font-black sm:text-3xl">حسابات المزودين المالية</h1>
                        <div className="mt-1 text-xs font-bold text-emerald-300">{model?.operationId || "MZ2-FIN-CUTOVER-001"}</div>
                        <p className="mt-2 max-w-3xl text-sm leading-6 text-emerald-100">سلة وتمارا وتابي وإمكان وشركات الشحن كحسابات مستقلة. الفواتير والتسويات والرسوم مرتبطة بالمزود، ولا تُستورد أرصدة أو مبيعات من ميزان القديم.</p>
                    </div>
                    <Link to="/new-transaction" className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-white px-4 text-sm font-extrabold text-emerald-950">
                        <Receipt size={19} weight="bold" /> حركة مالية جديدة
                    </Link>
                </div>
                <div className="border-t border-emerald-800 bg-emerald-900 px-5 py-3 text-xs font-semibold leading-5 text-emerald-100 sm:px-7">التحويل بين بنك/صندوق هو حركة داخلية. تحويل المزود إلى البنك هو تسوية مزود، وتحصيل شركة الشحن هو تسوية COD؛ لا تُسجّل هذه الحالات كتحويل عام حتى لا تختلط الإيرادات والرسوم.</div>
            </header>

            {error && <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-800"><WarningCircle size={20} weight="fill" />{error}</div>}

            <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                <Stat label="جميع المزودين" value={model?.summary.providers || 0} hint="كل حساب مستقل" Icon={Buildings} />
                <Stat label="الدفع والتمويل" value={model?.summary.paymentProviders || 0} hint="سلة وBNPL" Icon={CreditCard} />
                <Stat label="شركات الشحن" value={model?.summary.shippingCompanies || 0} hint="COD وتكاليف الشحن" Icon={Truck} />
                <Stat label="فواتير ضريبية" value={model?.summary.taxInvoices || 0} hint="مستندات مرتبطة بالمزود" Icon={FileText} />
                <Stat label="متحقق منها" value={model?.summary.verifiedTaxInvoices || 0} hint="لها مرجع إثبات" Icon={CheckCircle} />
            </section>

            <section className="grid gap-3 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950 sm:grid-cols-[auto_1fr]">
                <ShieldCheck size={26} weight="duotone" className="text-emerald-700" />
                <div><div className="font-black">بداية نظيفة ومحكومة</div><div className="mt-1 text-xs font-semibold leading-5 text-emerald-800">لا يوجد في هذا المسار حقل رصيد افتتاحي أو استيراد مبيعات قديمة. الأرصدة الافتتاحية تُثبت لاحقًا بمحضر قطع مستقل من كشوف البنك والمنصات وشركات الشحن والرواتب المستحقة فقط.</div></div>
            </section>

            <div className="flex flex-wrap gap-2 rounded-xl border border-slate-200 bg-white p-3">
                {[["all", "الكل"], ["payment", "سلة وطرق الدفع"], ["shipping", "شركات الشحن"]].map(([id, label]) => (
                    <button key={id} type="button" onClick={() => setFilter(id)} className={`rounded-full border px-3 py-2 text-xs font-extrabold ${filter === id ? "border-emerald-900 bg-emerald-900 text-white" : "border-slate-200 text-slate-600"}`}>{label}</button>
                ))}
            </div>

            <section className="grid gap-4 xl:grid-cols-2">
                {visible.map((app) => <ProviderCard key={app.providerId} app={app} onInvoice={setInvoiceApp} />)}
            </section>

            {invoiceApp && <InvoiceForm app={invoiceApp} onClose={() => setInvoiceApp(null)} onSaved={load} />}
        </div>
    );
}
