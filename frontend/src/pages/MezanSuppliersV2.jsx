import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
    Buildings,
    CheckCircle,
    ClockCounterClockwise,
    CurrencyCircleDollar,
    MagnifyingGlass,
    NotePencil,
    Plus,
    Receipt,
    SpinnerGap,
    Storefront,
    Wallet,
    WarningCircle,
    Wrench,
    X,
} from "@phosphor-icons/react";

import {
    createMezanSupplier,
    loadMezanSupplierFinancials,
    loadMezanSuppliersWorkspace,
    updateMezanSupplier,
} from "../services/mezanSuppliersV2";
import { SupplierFinancialDetail } from "../components/suppliers/SupplierPaymentWorkspace";
export { SupplierFinancialDetail } from "../components/suppliers/SupplierPaymentWorkspace";


const EMPTY_FORM = {
    company_name: "",
    contact_person: "",
    phone: "",
    email: "",
    notes: "",
    status: "active",
    service_ids: [],
};

function normalized(value) {
    return String(value || "").trim().toLowerCase();
}

export function formatSupplierHalalas(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "غير متاح";
    return new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(Number(value || 0) / 100);
}

export function supplierMatchesQuery(supplier, query) {
    const needle = normalized(query);
    if (!needle) return true;
    return normalized([
        supplier?.company_name,
        supplier?.contact_person,
        supplier?.phone,
        supplier?.email,
        ...(supplier?.service_links || []).map((row) => row?.service_name),
    ].join(" ")).includes(needle);
}

export function supplierFormFromRow(supplier = null) {
    if (!supplier) return { ...EMPTY_FORM, service_ids: [] };
    return {
        company_name: supplier.company_name || "",
        contact_person: supplier.contact_person || "",
        phone: supplier.phone || "",
        email: supplier.email || "",
        notes: supplier.notes || "",
        status: supplier.status === "inactive" ? "inactive" : "active",
        service_ids: (supplier.service_ids || []).map(String),
    };
}

function SummaryCard({ value, label, tone = "slate" }) {
    const colors = {
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-950",
        amber: "border-amber-200 bg-amber-50 text-amber-950",
        violet: "border-violet-200 bg-violet-50 text-violet-950",
        slate: "border-slate-200 bg-slate-50 text-slate-950",
    };
    return (
        <div className={`rounded-2xl border p-4 ${colors[tone]}`}>
            <div className="text-3xl font-black tabular-nums">{value == null ? "غير متاح" : Number(value)}</div>
            <div className="mt-1 text-xs font-extrabold">{label}</div>
        </div>
    );
}

function MoneySummaryCard({ value, label, tone = "slate", Icon = Wallet }) {
    const colors = {
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-950",
        amber: "border-amber-200 bg-amber-50 text-amber-950",
        violet: "border-violet-200 bg-violet-50 text-violet-950",
        slate: "border-slate-200 bg-slate-50 text-slate-950",
    };
    return (
        <div className={`rounded-2xl border p-4 ${colors[tone]}`}>
            <div className="flex items-center justify-between gap-2"><Icon size={24} weight="duotone" /><span className="text-[11px] font-black">ر.س</span></div>
            <div className="mt-2 text-2xl font-black tabular-nums sm:text-3xl">{formatSupplierHalalas(value)}</div>
            <div className="mt-1 text-xs font-extrabold">{label}</div>
        </div>
    );
}

function ServiceBadges({ links = [] }) {
    if (!links.length) return <span className="text-xs font-bold text-rose-700">لا توجد خدمات</span>;
    return (
        <div className="flex flex-wrap gap-1.5">
            {links.map((service) => (
                <span key={service.service_id} className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-[11px] font-black text-violet-800">
                    {service.service_name || service.service_code || "خدمة"}
                </span>
            ))}
        </div>
    );
}

function SupplierEditor({ supplier, services, busy, onClose, onSaved }) {
    const [form, setForm] = useState(() => supplierFormFromRow(supplier));
    const [serviceQuery, setServiceQuery] = useState("");
    const [error, setError] = useState("");
    const [submitting, setSubmitting] = useState(false);
    const selected = new Set(form.service_ids);
    const visibleServices = services.filter((service) => normalized(
        `${service.name || ""} ${service.code || ""}`,
    ).includes(normalized(serviceQuery)));

    function change(field, value) {
        setForm((current) => ({ ...current, [field]: value }));
    }

    function toggleService(serviceId) {
        const next = new Set(form.service_ids);
        if (next.has(serviceId)) next.delete(serviceId);
        else next.add(serviceId);
        change("service_ids", Array.from(next));
    }

    async function submit(event) {
        event.preventDefault();
        if (!form.company_name.trim()) {
            setError("اسم المورد مطلوب.");
            return;
        }
        if (!form.service_ids.length) {
            setError("اختر خدمة واحدة على الأقل يقدمها المورد.");
            return;
        }
        setError("");
        setSubmitting(true);
        let success = false;
        const payload = {
            company_name: form.company_name.trim(),
            contact_person: form.contact_person.trim() || null,
            phone: form.phone.trim() || null,
            email: form.email.trim() || null,
            notes: form.notes.trim() || null,
            status: form.status,
            service_ids: form.service_ids,
        };
        try {
            if (supplier?.id) await updateMezanSupplier(supplier.id, payload);
            else await createMezanSupplier(payload);
            await onSaved();
            success = true;
        } catch (saveError) {
            setError(saveError.message || "تعذّر حفظ المورد.");
        } finally {
            setSubmitting(false);
        }
        if (success) onClose();
    }

    return (
        <div className="fixed inset-0 z-[110] flex items-end justify-center bg-slate-950/60 p-0 sm:items-center sm:p-4" data-testid="mezan-supplier-editor-overlay">
            <form onSubmit={submit} className="max-h-[94vh] w-full max-w-4xl overflow-y-auto rounded-t-3xl bg-white shadow-2xl sm:rounded-3xl" data-testid="mezan-supplier-editor">
                <header className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-slate-200 bg-white px-5 py-4 sm:px-6">
                    <div>
                        <h2 className="text-xl font-black text-slate-950">{supplier ? "تعديل المورد" : "إضافة مورد جديد"}</h2>
                        <p className="mt-1 text-xs font-bold text-slate-500">المورد لن يُحفظ قبل تحديد الخدمات التي يقدمها.</p>
                    </div>
                    <button type="button" onClick={onClose} className="rounded-xl border border-slate-200 p-2 text-slate-600" aria-label="إغلاق"><X size={21} /></button>
                </header>

                <div className="space-y-5 p-5 sm:p-6">
                    {financialData.financial_status !== "available" && <p role="status" className="rounded-xl bg-amber-50 p-4 font-bold">البيانات المالية غير جاهزة. الأرصدة غير المتاحة لا تعني صفرًا.</p>}
            {error && <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-black text-rose-900"><WarningCircle size={20} className="mt-0.5 shrink-0" />{error}</div>}
                    <section className="grid gap-4 md:grid-cols-2">
                        <label className="block"><span className="mb-2 block text-xs font-black text-slate-700">اسم المورد *</span><input value={form.company_name} onChange={(event) => change("company_name", event.target.value)} className="min-h-12 w-full rounded-xl border border-slate-200 px-3 text-sm font-bold outline-none focus:border-emerald-500" data-testid="mezan-supplier-name-input" /></label>
                        <label className="block"><span className="mb-2 block text-xs font-black text-slate-700">شخص التواصل</span><input value={form.contact_person} onChange={(event) => change("contact_person", event.target.value)} className="min-h-12 w-full rounded-xl border border-slate-200 px-3 text-sm font-bold outline-none focus:border-emerald-500" /></label>
                        <label className="block"><span className="mb-2 block text-xs font-black text-slate-700">رقم الجوال</span><input value={form.phone} onChange={(event) => change("phone", event.target.value)} inputMode="tel" className="min-h-12 w-full rounded-xl border border-slate-200 px-3 text-sm font-bold outline-none focus:border-emerald-500" /></label>
                        <label className="block"><span className="mb-2 block text-xs font-black text-slate-700">البريد الإلكتروني</span><input value={form.email} onChange={(event) => change("email", event.target.value)} inputMode="email" className="min-h-12 w-full rounded-xl border border-slate-200 px-3 text-sm font-bold outline-none focus:border-emerald-500" /></label>
                        <label className="block md:col-span-2"><span className="mb-2 block text-xs font-black text-slate-700">ملاحظات</span><textarea value={form.notes} onChange={(event) => change("notes", event.target.value)} rows={3} className="w-full rounded-xl border border-slate-200 p-3 text-sm font-bold outline-none focus:border-emerald-500" /></label>
                    </section>

                    <section className="rounded-2xl border border-violet-200 bg-violet-50/40 p-4" data-testid="mezan-supplier-service-picker">
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                            <div><h3 className="font-black text-slate-950"><Wrench className="ml-1 inline text-violet-700" />الخدمات التي يقدمها المورد *</h3><p className="mt-1 text-xs font-bold text-slate-500">المصدر هو خدمات مكونات المنتجات في ميزان 2.</p></div>
                            <span className="rounded-full bg-violet-700 px-3 py-1.5 text-xs font-black text-white">{selected.size} محددة</span>
                        </div>
                        <label className="relative mt-4 block"><MagnifyingGlass className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" /><input value={serviceQuery} onChange={(event) => setServiceQuery(event.target.value)} placeholder="ابحث في الخدمات…" className="min-h-11 w-full rounded-xl border border-violet-200 bg-white pr-10 pl-3 text-sm font-bold outline-none focus:border-violet-500" /></label>
                        {!services.length ? (
                            <div className="mt-3 rounded-xl border border-dashed border-amber-300 bg-amber-50 p-4 text-sm font-bold text-amber-900">لا توجد خدمات في كتالوج مكونات المنتجات. أضف الخدمات أولًا من صفحة مكونات المنتجات.</div>
                        ) : (
                            <div className="mt-3 grid max-h-72 gap-2 overflow-y-auto sm:grid-cols-2">
                                {visibleServices.map((service) => {
                                    const checked = selected.has(service.id);
                                    return (
                                        <label key={service.id} className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 ${checked ? "border-violet-500 bg-white ring-2 ring-violet-100" : "border-slate-200 bg-white"}`}>
                                            <input type="checkbox" checked={checked} onChange={() => toggleService(service.id)} className="mt-1 h-4 w-4 accent-violet-700" />
                                            <span className="min-w-0"><span className="block font-black text-slate-900">{service.name}</span><span className="mt-1 block text-[11px] font-bold text-slate-500">{service.code || "بدون رمز"}{service.requires_preparation ? " · خدمة تجهيز" : ""}</span></span>
                                        </label>
                                    );
                                })}
                            </div>
                        )}
                    </section>

                    <label className="block max-w-xs"><span className="mb-2 block text-xs font-black text-slate-700">حالة المورد</span><select value={form.status} onChange={(event) => change("status", event.target.value)} className="min-h-12 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-black"><option value="active">نشط</option><option value="inactive">موقوف</option></select></label>
                </div>

                <footer className="sticky bottom-0 flex flex-col-reverse gap-2 border-t border-slate-200 bg-white p-4 sm:flex-row sm:justify-end sm:px-6">
                    <button type="button" onClick={onClose} disabled={busy || submitting} className="min-h-12 rounded-xl border border-slate-200 px-5 text-sm font-black text-slate-700">إلغاء</button>
                    <button type="submit" disabled={busy || submitting || !form.company_name.trim() || !form.service_ids.length} className="min-h-12 rounded-xl bg-emerald-700 px-6 text-sm font-black text-white disabled:opacity-50" data-testid="mezan-supplier-save-button">{busy || submitting ? <SpinnerGap className="ml-1 inline animate-spin" /> : <CheckCircle className="ml-1 inline" weight="fill" />}{supplier ? "حفظ التعديلات" : "إضافة المورد"}</button>
                </footer>
            </form>
        </div>
    );
}

export default function MezanSuppliersV2() {
    const [searchParams, setSearchParams] = useSearchParams();
    const requestedSupplierId = searchParams.get("supplier") || "";
    const [data, setData] = useState({ suppliers: [], services: [], summary: {} });
    const [financialData, setFinancialData] = useState({ suppliers: [], invoices: [], timeline: [], summary: {} });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");
    const [financialError, setFinancialError] = useState("");
    const [query, setQuery] = useState("");
    const [status, setStatus] = useState("active");
    const [editorSupplier, setEditorSupplier] = useState(undefined);
    const [selectedSupplierId, setSelectedSupplierId] = useState(requestedSupplierId);

    const load = useCallback(async ({ quiet = false } = {}) => {
        if (!quiet) setLoading(true);
        setError("");
        setFinancialError("");
        const [workspaceResult, financialResult] = await Promise.allSettled([
            loadMezanSuppliersWorkspace(),
            loadMezanSupplierFinancials(),
        ]);
        if (workspaceResult.status === "fulfilled") setData(workspaceResult.value);
        else setError(workspaceResult.reason?.message || "تعذّر تحميل الموردين.");
        if (financialResult.status === "fulfilled") setFinancialData(financialResult.value);
        else {
            setFinancialData({ suppliers: [], invoices: [], timeline: [], summary: {}, financial_status: "not_ready" });
            setFinancialError(financialResult.reason?.message || "تعذّر تحميل الفواتير والمديونيات.");
        }
        if (!quiet) setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    const suppliers = useMemo(() => (data.suppliers || []).filter((supplier) => (
        (status === "all" || supplier.status === status)
        && supplierMatchesQuery(supplier, query)
    )), [data.suppliers, query, status]);
    const financialBySupplier = useMemo(() => Object.fromEntries(
        (financialData.suppliers || []).map((supplier) => [supplier.id, supplier]),
    ), [financialData.suppliers]);
    const selectedSupplier = financialBySupplier[selectedSupplierId] || (data.suppliers || []).find((row) => row.id === selectedSupplierId) || null;
    const selectedInvoices = useMemo(() => (financialData.invoices || []).filter(
        (invoice) => invoice.supplier_id === selectedSupplierId,
    ), [financialData.invoices, selectedSupplierId]);
    const selectedTimeline = useMemo(() => (financialData.timeline || []).filter(
        (entry) => entry.supplier_id === selectedSupplierId,
    ), [financialData.timeline, selectedSupplierId]);

    useEffect(() => {
        if (requestedSupplierId && financialBySupplier[requestedSupplierId]) {
            setSelectedSupplierId(requestedSupplierId);
        }
    }, [financialBySupplier, requestedSupplierId]);

    function openFinancialDetail(supplierId) {
        const next = new URLSearchParams(searchParams);
        next.set("supplier", supplierId);
        setSelectedSupplierId(supplierId);
        setSearchParams(next, { replace: true });
    }

    function closeFinancialDetail() {
        const next = new URLSearchParams(searchParams);
        next.delete("supplier");
        setSelectedSupplierId("");
        setSearchParams(next, { replace: true });
    }

    async function saved() {
        setSaving(true);
        try {
            await load({ quiet: true });
        } finally {
            setSaving(false);
        }
    }

    return (
        <main className="space-y-5" dir="rtl" data-testid="mezan-suppliers-v2-page">
            <header className="overflow-hidden rounded-2xl border border-emerald-900 bg-gradient-to-l from-slate-950 via-emerald-950 to-emerald-800 p-5 text-white shadow-lg sm:p-7">
                <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
                    <div><div className="text-xs font-black text-emerald-200">Mezan 2 · Supplier Accounts</div><h1 className="mt-1 text-3xl font-black">الموردون والفواتير</h1><p className="mt-2 max-w-3xl text-sm font-semibold leading-6 text-emerald-100">مكان واحد لإدارة المورد، مراجعة الفواتير المالية المؤهلة، والسداد والدفعات المقدمة.</p></div>
                    <button type="button" onClick={() => setEditorSupplier(null)} disabled={!data?.permissions?.can_manage || !(data.services || []).length} className="inline-flex min-h-12 items-center justify-center gap-2 rounded-xl bg-white px-5 text-sm font-black text-emerald-950 disabled:opacity-50" data-testid="mezan-supplier-add-button"><Plus size={20} weight="bold" /> إضافة مورد</button>
                </div>
            </header>

            <div className="flex items-start gap-2 rounded-2xl border border-sky-200 bg-sky-50 p-4 text-sm font-bold leading-6 text-sky-950"><Storefront size={22} className="mt-0.5 shrink-0" weight="duotone" /><div><div className="font-black">موردون وفواتير ومديونيات ميزان 2 فقط</div><div>هوية المورد من سجل ميزان 2 فقط. تظهر المديونية من العقد المالي المعتمد؛ الفواتير التاريخية لا تعيد إنشاء دين سُدد خارج النظام قبل تاريخ الانتقال.</div></div></div>

            <section className="grid gap-2 rounded-2xl border border-emerald-200 bg-white p-3 sm:grid-cols-3" data-testid="mezan-supplier-financial-flow">
                <div className="rounded-xl bg-violet-50 p-3"><div className="text-xs font-black text-violet-700">1 · الاستلام</div><div className="mt-1 text-sm font-black text-slate-950">مسح المنتجات من المورد</div></div>
                <div className="rounded-xl bg-sky-50 p-3"><div className="text-xs font-black text-sky-700">2 · الفاتورة</div><div className="mt-1 text-sm font-black text-slate-950">مراجعة الأسعار ثم الحفظ</div></div>
                <div className="rounded-xl bg-emerald-50 p-3"><div className="text-xs font-black text-emerald-700">3 · حساب المورد</div><div className="mt-1 text-sm font-black text-slate-950">الرصيد الموثق والسداد والتخصيص</div></div>
            </section>

            <section className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                <MoneySummaryCard value={financialData.summary?.outstanding_halalas} label="إجمالي ديون الموردين" tone="amber" Icon={Wallet} />
                <MoneySummaryCard value={financialData.summary?.advance_halalas} label="الدفعات المقدمة للموردين" tone="violet" Icon={Receipt} />
                <MoneySummaryCard value={financialData.summary?.paid_halalas} label="إجمالي ما سُدد للموردين" tone="emerald" Icon={CurrencyCircleDollar} />
                <SummaryCard value={financialData.summary?.real_invoice_count} label="فواتير مالية مؤهلة" tone="slate" />
            </section>

            {error && <div className="flex items-start gap-2 rounded-2xl border border-rose-300 bg-rose-50 p-4 text-sm font-black text-rose-950"><WarningCircle size={21} className="mt-0.5 shrink-0" />{error}</div>}
            {financialError && <div className="flex items-start gap-2 rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm font-black text-amber-950"><WarningCircle size={21} className="mt-0.5 shrink-0" />{financialError}</div>}

            {!loading && !(data.services || []).length && (
                <section className="rounded-2xl border border-amber-300 bg-amber-50 p-5 text-amber-950" data-testid="mezan-suppliers-no-services"><div className="flex items-start gap-3"><Wrench size={25} className="mt-0.5 shrink-0" /><div><h2 className="font-black">أضف خدمات التجهيز أولًا</h2><p className="mt-1 text-sm font-bold leading-6">لا يمكن إنشاء مورد بلا خدمات. أنشئ خدمات مثل الطباعة أو الحفر أو الخياطة داخل مكونات المنتجات، ثم ارجع لربطها بالمورد.</p><Link to="/components-v2" className="mt-3 inline-flex rounded-xl bg-amber-900 px-4 py-2.5 text-xs font-black text-white">فتح مكونات المنتجات</Link></div></div></section>
            )}

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px_auto]">
                    <label className="relative"><MagnifyingGlass className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث باسم المورد أو الخدمة أو رقم الجوال…" className="min-h-12 w-full rounded-xl border border-slate-200 pr-11 pl-3 text-sm font-bold outline-none focus:border-emerald-500" /></label>
                    <select value={status} onChange={(event) => setStatus(event.target.value)} className="min-h-12 rounded-xl border border-slate-200 bg-white px-3 text-sm font-black"><option value="active">النشطون</option><option value="inactive">الموقوفون</option><option value="all">الكل</option></select>
                    <button type="button" onClick={() => load()} disabled={loading} className="min-h-12 rounded-xl border border-slate-200 px-4 text-sm font-black text-slate-700">{loading ? <SpinnerGap className="ml-1 inline animate-spin" /> : null}تحديث</button>
                </div>

                {loading ? (
                    <div className="flex min-h-52 items-center justify-center gap-2 text-emerald-700"><SpinnerGap size={25} className="animate-spin" /><span className="font-black">جارٍ تحميل الموردين…</span></div>
                ) : !suppliers.length ? (
                    <div className="mt-4 rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-9 text-center" data-testid="mezan-suppliers-empty"><Buildings size={38} className="mx-auto text-slate-400" /><div className="mt-3 font-black text-slate-800">{(data.suppliers || []).length ? "لا توجد نتائج مطابقة" : "لم تضف موردين إلى ميزان 2 بعد"}</div>{!(data.suppliers || []).length && (data.services || []).length > 0 && <button type="button" onClick={() => setEditorSupplier(null)} className="mt-4 rounded-xl bg-emerald-700 px-5 py-2.5 text-sm font-black text-white">إضافة أول مورد</button>}</div>
                ) : (
                    <div className="mt-4 grid gap-3 xl:grid-cols-2">
                        {suppliers.map((supplier) => (
                            <article key={supplier.id} className="rounded-2xl border border-slate-200 p-4" data-testid={`mezan-supplier-row-${supplier.id}`}>
                                <div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="truncate text-lg font-black text-slate-950">{supplier.company_name}</h2><span className={`rounded-full px-2.5 py-1 text-[11px] font-black ${supplier.status === "active" ? "bg-emerald-100 text-emerald-800" : "bg-slate-200 text-slate-700"}`}>{supplier.status === "active" ? "نشط" : "موقوف"}</span></div><div className="mt-1 text-xs font-bold text-slate-500">{supplier.contact_person || "بدون شخص تواصل"}{supplier.phone ? ` · ${supplier.phone}` : ""}</div></div><button type="button" onClick={() => setEditorSupplier(supplier)} disabled={!data?.permissions?.can_manage} className="shrink-0 rounded-xl border border-slate-200 p-2.5 text-emerald-800 disabled:opacity-40" aria-label={`تعديل ${supplier.company_name}`}><NotePencil size={20} /></button></div>
                                <div className="mt-4 border-t border-slate-100 pt-3"><div className="mb-2 flex items-center gap-2 text-xs font-black text-slate-700"><Wrench className="text-violet-700" />الخدمات التي يقدمها ({supplier.service_count || 0})</div><ServiceBadges links={supplier.service_links} /></div>
                                <div className="mt-4 grid grid-cols-3 gap-2 border-t border-slate-100 pt-3">
                                    <div className="rounded-xl bg-amber-50 p-2.5"><div className="text-[10px] font-black text-amber-700">الدين الحالي</div><div className="mt-1 text-sm font-black tabular-nums text-amber-950">{formatSupplierHalalas(financialBySupplier[supplier.id]?.financial?.outstanding_halalas)} ر.س</div></div>
                                    <div className="rounded-xl bg-emerald-50 p-2.5"><div className="text-[10px] font-black text-emerald-700">فواتير حقيقية</div><div className="mt-1 text-lg font-black tabular-nums text-emerald-950">{financialBySupplier[supplier.id]?.financial?.real_invoice_count ?? "غير متاح"}</div></div>
                                    <div className="rounded-xl bg-violet-50 p-2.5"><div className="text-[10px] font-black text-violet-700">دفعات مقدمة</div><div className="mt-1 text-lg font-black tabular-nums text-violet-950">{formatSupplierHalalas(financialBySupplier[supplier.id]?.financial?.advance_halalas)}</div></div>
                                </div>
                                <button type="button" onClick={() => openFinancialDetail(supplier.id)} className="mt-3 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 text-sm font-black text-white" data-testid={`mezan-supplier-financial-open-${supplier.id}`}><ClockCounterClockwise size={20} />فتح الحساب والفواتير</button>
                            </article>
                        ))}
                    </div>
                )}
            </section>

            {editorSupplier !== undefined && (
                <SupplierEditor supplier={editorSupplier} services={data.services || []} busy={saving} onClose={() => setEditorSupplier(undefined)} onSaved={saved} />
            )}
            {selectedSupplier && (
                <SupplierFinancialDetail
                    key={selectedSupplier.id}
                    supplier={selectedSupplier}
                    invoices={selectedInvoices}
                    timeline={selectedTimeline}
                    workspace={financialData}
                    onSaved={saved}
                    onClose={closeFinancialDetail}
                />
            )}
        </main>
    );
}
