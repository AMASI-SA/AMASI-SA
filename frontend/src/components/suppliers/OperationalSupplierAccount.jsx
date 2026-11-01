import { Link } from "react-router-dom";
import { X } from "@phosphor-icons/react";

export function formatOperationalAmount(value) {
    const match = /^(-?)(\d+)\.(\d{2})$/.exec(String(value ?? ""));
    if (!match) return "غير متاح";
    return `${match[1]}${BigInt(match[2]).toLocaleString("en-US")}.${match[3]}`;
}

function stamp(value) {
    if (!value || !Number.isFinite(Date.parse(value))) return "غير متاح";
    return new Date(value).toLocaleString("ar-SA", { timeZone: "Asia/Riyadh" });
}

function Balance({ value, label, currency }) {
    return <div className="rounded-xl bg-slate-50 p-3"><div className="text-xs font-bold text-slate-600">{label}</div><div className="mt-1 font-black tabular-nums text-slate-950">{formatOperationalAmount(value)} {currency}</div></div>;
}

export function OperationalSupplierSummary({ workspace }) {
    return <section className="space-y-3 rounded-2xl border border-emerald-200 bg-white p-4" data-testid="operational-supplier-summary">
        <div><h2 className="font-black text-emerald-950">حسابات الموردين التشغيلية</h2><p className="mt-1 text-sm font-bold text-slate-600">منذ بدء التشغيل: {stamp(workspace.started_at)} · آخر احتساب: {stamp(workspace.as_of)}</p></div>
        <p className="text-sm font-semibold text-slate-600">تظهر فواتير الاستلام المعتمدة بعد بدء التشغيل، حتى لو كان طلب العميل أقدم. الرصيد الافتتاحي محفوظ بصورة مستقلة.</p>
        {!workspace.as_of && <p role="status" className="text-sm font-bold text-amber-900">لم تكتمل قراءة الفواتير التشغيلية بعد.</p>}
        {(workspace.invoice_provenance_incomplete || workspace.source_issues_count > 0) && <p role="status" className="text-sm font-bold text-amber-900">توجد بيانات تحتاج مراجعة؛ راجع تنبيهات الأرصدة التشغيلية قبل اعتماد الإجماليات.</p>}
        {Object.entries(workspace.summaries || {}).map(([currency, total]) => <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" key={currency}>
            <Balance label="المتبقي علينا للموردين" value={total.payable} currency={currency} />
            <Balance label="المستحق لنا لدى الموردين" value={total.receivable} currency={currency} />
            <Balance label="المسدد المخصص" value={total.settled} currency={currency} />
            <div className="rounded-xl bg-slate-50 p-3"><div className="text-xs font-bold text-slate-600">فواتير استلام محتسبة · {currency}</div><div className="mt-1 font-black">{total.invoice_count ?? "غير متاح"}</div></div>
        </div>)}
        {!Object.keys(workspace.summaries || {}).length && <p className="text-sm font-bold text-slate-600">لا توجد أرصدة موردين محسوبة للعرض حتى الآن.</p>}
    </section>;
}

export function OperationalSupplierCard({ accounts = [], invoiceCount = 0, unavailable = false }) {
    if (unavailable) return <p className="mt-4 text-sm font-bold text-amber-900">تعذّر تحديث الحساب التشغيلي؛ أعد المحاولة.</p>;
    return <div className="mt-4 space-y-2 border-t border-slate-100 pt-3" data-testid="operational-supplier-card">
        <p className="text-xs font-black text-emerald-800">الرصيد التشغيلي · فواتير الاستلام المحتسبة: {invoiceCount}</p>
        {accounts.map(account => <div className="grid grid-cols-2 gap-2" key={account.currency}>
            <Balance value={account.outstanding_payable} label="المتبقي علينا" currency={account.currency} />
            <Balance value={account.outstanding_receivable} label="المستحق لنا" currency={account.currency} />
        </div>)}
        {!accounts.length && <p className="text-xs font-bold text-slate-500">لا توجد أرصدة تشغيلية مثبتة لهذا المورد.</p>}
    </div>;
}

export default function OperationalSupplierDetail({ supplier, workspace, onClose }) {
    const accounts = (workspace.accounts || []).filter(row => row.supplier_id === supplier.id);
    const invoices = (workspace.invoices || []).filter(row => row.supplier_id === supplier.id);
    const openings = (workspace.openings || []).filter(row => row.supplier_id === supplier.id);
    return <div className="fixed inset-0 z-[110] flex items-end justify-center bg-slate-950/60 sm:items-center sm:p-4" data-testid="operational-supplier-detail">
        <section role="dialog" aria-modal="true" aria-label={`الحساب التشغيلي للمورد ${supplier.company_name}`} className="max-h-[94vh] w-full max-w-5xl overflow-y-auto rounded-t-3xl bg-white shadow-2xl sm:rounded-3xl">
            <header className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-slate-200 bg-white p-5"><div><h2 className="text-xl font-black">{supplier.company_name} · الحساب التشغيلي</h2><p className="mt-1 text-xs font-bold text-slate-500">آخر احتساب: {stamp(workspace.as_of)}</p></div><button type="button" onClick={onClose} aria-label="إغلاق الحساب التشغيلي" className="rounded-xl border p-2"><X size={21} /></button></header>
            <div className="space-y-5 p-5">
                <OperationalSupplierCard accounts={accounts} invoiceCount={invoices.length} />
                {workspace.source_issues_count > 0 && <p role="status" className="rounded-xl bg-amber-50 p-3 text-sm font-bold text-amber-900">توجد بيانات فواتير تحتاج مراجعة؛ راجع تنبيهات الأرصدة التشغيلية قبل اعتماد الحساب.</p>}
                {workspace.permissions?.record_movements && <Link to="/operational-balances" className="inline-flex min-h-11 items-center rounded-xl bg-emerald-800 px-4 text-sm font-black text-white">فتح الأرصدة والحركات التشغيلية للسداد</Link>}
                <section><h3 className="font-black">الرصيد الافتتاحي</h3><p className="mt-1 text-xs font-bold text-slate-500">رصيد سابق موثق؛ يظهر منفصلًا عن فواتير الاستلام الجديدة.</p>{openings.map(row => <p className="mt-2 text-sm font-bold" key={row.id}>{row.direction === "payable" ? "علينا" : "لنا"}: {formatOperationalAmount(row.amount)} {row.currency} · المسدد: {formatOperationalAmount(row.settled)} · المتبقي: {formatOperationalAmount(row.outstanding)}</p>)}{!openings.length && <p className="mt-2 text-sm text-slate-500">لا يوجد رصيد افتتاحي مسجل لهذا المورد.</p>}</section>
                <section><h3 className="font-black">فواتير الاستلام المحتسبة منذ بدء التشغيل</h3><div className="mt-3 overflow-x-auto"><table className="w-full text-right text-sm"><thead><tr>{["الفاتورة", "تاريخ الاعتماد", "القيمة المحتسبة", "المرتجع", "المؤكد علينا", "المسدد المخصص", "المتبقي", "العملة"].map(label => <th className="border-b p-2" key={label}>{label}</th>)}</tr></thead><tbody>{invoices.map(row => <tr key={`${row.id}:${row.currency}`} data-testid="operational-supplier-invoice"><td className="border-b p-2 font-bold">{row.invoice_number || "رقم الفاتورة غير متاح"}</td><td className="border-b p-2">{stamp(row.approved_at)}</td>{["gross_amount", "returned_amount", "confirmed", "settled", "outstanding"].map(field => <td className="border-b p-2 tabular-nums" key={field}>{formatOperationalAmount(row[field])}</td>)}<td className="border-b p-2">{row.currency}</td></tr>)}</tbody></table></div>{!invoices.length && <p className="mt-3 text-sm font-bold text-slate-500">لا توجد فواتير استلام محتسبة لهذا المورد حتى آخر تحديث.</p>}{workspace.invoice_provenance_incomplete && <p role="status" className="mt-3 text-sm font-bold text-amber-900">بعض مبالغ الاستلام تحتاج استكمال ربط رقم الفاتورة؛ الرصيد معروض كما هو مثبت في الأرصدة التشغيلية.</p>}</section>
            </div>
        </section>
    </div>;
}
