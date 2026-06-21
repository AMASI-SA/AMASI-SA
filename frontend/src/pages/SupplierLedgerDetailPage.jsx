// Iter-250b · P1.5.s — Supplier Ledger Detail page.
//
// SSOT-strict. Read-only. All numbers come from the backend's
// `/accounting/suppliers/:id/ledger-detail` endpoint, which itself
// derives everything from `general_ledger`.
//
// Sections (in order):
//   1. Header card  (supplier identity + date-range filter + print)
//   2. Reconciliation banner (drift detection)
//   3. Period summary  (opening / invoiced / paid / closing)
//   4. Chronological timeline
//   5. Invoice cards   (collapsible — line items, GL legs, payments)
//   6. Manual entries  (audit list of GL rows without an invoice)
//   7. Drift detail    (financial_movements without a GL counterpart)
//
// Printable layout uses `react-to-print` so the user can save a clean
// PDF for the merchant's accountant.

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import { useReactToPrint } from "react-to-print";
import { toast } from "sonner";
import api from "../lib/api";

const fmt = (n) =>
    Number(n || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

const ymd = (d) => {
    if (!d) return "";
    if (typeof d === "string") return d.slice(0, 10);
    return new Date(d).toISOString().slice(0, 10);
};

const STATUS_BADGE = {
    paid:    { label: "مدفوعة",       cls: "bg-emerald-100 text-emerald-800 border-emerald-300" },
    partial: { label: "جزئية",        cls: "bg-amber-100 text-amber-800 border-amber-300" },
    unpaid:  { label: "غير مدفوعة",   cls: "bg-rose-100 text-rose-800 border-rose-300" },
};

function todayISO() { return new Date().toISOString().slice(0, 10); }
function startOfYearISO() {
    return new Date(new Date().getFullYear(), 0, 1)
        .toISOString().slice(0, 10);
}
function nDaysAgoISO(n) {
    const d = new Date(); d.setDate(d.getDate() - n);
    return d.toISOString().slice(0, 10);
}
function startOfMonthISO() {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1)
        .toISOString().slice(0, 10);
}

const PRESETS = [
    { key: "ytd",   label: "منذ بداية السنة (YTD)",
                    from: startOfYearISO, to: todayISO },
    { key: "month", label: "الشهر الحالي",
                    from: startOfMonthISO, to: todayISO },
    { key: "90d",   label: "آخر 90 يوماً",
                    from: () => nDaysAgoISO(90), to: todayISO },
    { key: "all",   label: "كل الفترات",
                    from: () => "", to: () => "" },
];

export default function SupplierLedgerDetailPage() {
    const { id } = useParams();
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();

    const initialFrom = searchParams.get("from") || startOfYearISO();
    const initialTo   = searchParams.get("to")   || todayISO();
    const [from, setFrom] = useState(initialFrom);
    const [to,   setTo]   = useState(initialTo);
    const [presetKey, setPresetKey] = useState("ytd");
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [expanded, setExpanded] = useState({});

    const printRef = useRef(null);
    const handlePrint = useReactToPrint({
        contentRef: printRef,
        documentTitle: data?.supplier?.name
            ? `دفتر المورد - ${data.supplier.name}`
            : "دفتر المورد",
    });

    const load = async (f, t) => {
        setLoading(true);
        try {
            const params = {};
            if (f) params.from = f;
            if (t) params.to   = t;
            const { data } = await api.get(
                `/accounting/suppliers/${id}/ledger-detail`,
                { params },
            );
            setData(data);
        } catch (e) {
            toast.error(
                e?.response?.data?.detail || "فشل تحميل دفتر المورد");
            setData(null);
        } finally { setLoading(false); }
    };

    useEffect(() => { load(from, to); /* eslint-disable-next-line */ }, [id]);

    const applyPreset = (k) => {
        const p = PRESETS.find(x => x.key === k);
        if (!p) return;
        const f = p.from(), t = p.to();
        setPresetKey(k); setFrom(f); setTo(t);
        const next = new URLSearchParams();
        if (f) next.set("from", f); if (t) next.set("to", t);
        setSearchParams(next, { replace: true });
        load(f, t);
    };
    const applyCustom = () => {
        setPresetKey("custom");
        const next = new URLSearchParams();
        if (from) next.set("from", from);
        if (to) next.set("to", to);
        setSearchParams(next, { replace: true });
        load(from, to);
    };

    if (loading) {
        return (
            <div className="p-8 text-center text-slate-600"
                 data-testid="sup-ledger-detail-loading">
                ⏳ يتم تحميل دفتر المورد...
            </div>
        );
    }
    if (!data) {
        return (
            <div className="p-8 text-center text-rose-700"
                 data-testid="sup-ledger-detail-error">
                تعذّر تحميل البيانات.
            </div>
        );
    }

    const { supplier, period, timeline, invoices,
             manual_entries: manualEntries, reconciliation } = data;
    const driftDetected = !!reconciliation?.drift_detected;

    return (
        <div className="p-4 md:p-6 max-w-screen-2xl mx-auto"
             dir="rtl"
             data-testid="sup-ledger-detail-page">

            {/* ── Toolbar (non-print) ───────────────────────────── */}
            <div className="flex items-center justify-between mb-4 print:hidden">
                <button onClick={() => navigate(-1)}
                        className="text-sm text-slate-600 hover:text-slate-900"
                        data-testid="sup-ledger-back-btn">
                    ← رجوع
                </button>
                <div className="flex gap-2">
                    <button onClick={() => load(from, to)}
                            className="px-3 py-1.5 rounded bg-slate-100 hover:bg-slate-200 text-sm font-bold"
                            data-testid="sup-ledger-refresh-btn">
                        ↻ تحديث
                    </button>
                    <button onClick={handlePrint}
                            className="px-3 py-1.5 rounded bg-indigo-600 text-white hover:bg-indigo-700 text-sm font-bold"
                            data-testid="sup-ledger-print-btn">
                        🖨️ طباعة / PDF
                    </button>
                </div>
            </div>

            {/* ── Filter bar (non-print) ────────────────────────── */}
            <div className="bg-white border border-slate-200 rounded-xl p-4 mb-4 print:hidden">
                <div className="flex flex-wrap items-end gap-3">
                    <div className="flex flex-wrap gap-2">
                        {PRESETS.map(p => (
                            <button key={p.key}
                                onClick={() => applyPreset(p.key)}
                                className={`px-3 py-1.5 rounded-full text-xs font-bold border transition ${presetKey === p.key
                                    ? "bg-indigo-600 text-white border-indigo-600"
                                    : "bg-white text-slate-600 border-slate-300 hover:bg-slate-50"}`}
                                data-testid={`sup-ledger-preset-${p.key}`}>
                                {p.label}
                            </button>
                        ))}
                    </div>
                    <div className="flex items-end gap-2 ml-auto">
                        <div>
                            <label className="block text-[11px] text-slate-600 mb-0.5">من</label>
                            <input type="date" value={from || ""}
                                onChange={e => setFrom(e.target.value)}
                                className="border rounded px-2 py-1 text-sm"
                                data-testid="sup-ledger-from-input"/>
                        </div>
                        <div>
                            <label className="block text-[11px] text-slate-600 mb-0.5">إلى</label>
                            <input type="date" value={to || ""}
                                onChange={e => setTo(e.target.value)}
                                className="border rounded px-2 py-1 text-sm"
                                data-testid="sup-ledger-to-input"/>
                        </div>
                        <button onClick={applyCustom}
                                className="px-4 py-1.5 rounded bg-indigo-600 text-white hover:bg-indigo-700 text-sm font-bold"
                                data-testid="sup-ledger-apply-btn">
                            تطبيق
                        </button>
                    </div>
                </div>
            </div>

            {/* ── PRINTABLE AREA ────────────────────────────────── */}
            <div ref={printRef} className="space-y-4 print:space-y-3 print:text-[12px]">

                {/* Print-only header */}
                <div className="hidden print:block mb-3 pb-2 border-b-2 border-slate-300">
                    <div className="flex items-baseline justify-between">
                        <h1 className="text-xl font-extrabold">دفتر المورد التفصيلي</h1>
                        <span className="text-xs text-slate-600">
                            تاريخ الطباعة: {todayISO()}
                        </span>
                    </div>
                    <div className="text-[11px] text-slate-600 mt-1">
                        المصدر: General Ledger (SSOT) — Read-Only
                    </div>
                </div>

                {/* Supplier identity card */}
                <div className="bg-white border-2 border-slate-300 rounded-xl p-4 print:rounded-none print:border print:p-3"
                     data-testid="sup-ledger-supplier-card">
                    <h2 className="text-xl font-extrabold text-slate-900 mb-2">
                        🏭 {supplier.name}
                    </h2>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[12px] text-slate-700">
                        {supplier.phone && (
                            <div><span className="font-bold">الهاتف:</span> {supplier.phone}</div>
                        )}
                        {supplier.email && (
                            <div><span className="font-bold">البريد:</span> {supplier.email}</div>
                        )}
                        {supplier.vat_number && (
                            <div><span className="font-bold">الرقم الضريبي:</span> {supplier.vat_number}</div>
                        )}
                        {supplier.address && (
                            <div className="col-span-2"><span className="font-bold">العنوان:</span> {supplier.address}</div>
                        )}
                    </div>
                    <div className="mt-2 pt-2 border-t border-slate-200 text-[12px]">
                        <span className="font-bold text-slate-700">الفترة: </span>
                        <span data-testid="sup-ledger-period">
                            {period.from ? period.from : "البداية"} ← {period.to ? period.to : "اليوم"}
                        </span>
                    </div>
                </div>

                {/* Drift banner */}
                {driftDetected && (
                    <div className="rounded-xl border-2 border-rose-400 bg-rose-50 p-4 print:rounded-none"
                         data-testid="sup-ledger-drift-banner">
                        <div className="font-extrabold text-rose-800 mb-1">
                            ⚠️ تباين بين السجلات اكتُشف — يحتاج مراجعة محاسبية
                        </div>
                        <ul className="text-[12px] text-rose-700 list-disc pr-5 space-y-1">
                            {reconciliation.movements_orphaned_count > 0 && (
                                <li>
                                    عدد فواتير في <code>financial_movements</code> بدون قيد محاسبي مقابل = <b>{reconciliation.movements_orphaned_count}</b>
                                    {" "}— ستظهر في قسم «تشخيص التباين» أسفل الصفحة.
                                </li>
                            )}
                            {reconciliation.gl_only_count > 0 && (
                                <li>
                                    عدد قيود محاسبية يدوية بدون فاتورة مرتبطة = <b>{reconciliation.gl_only_count}</b>
                                    {" "}— تظهر بـ Badge «قيد يدوي» في الجدول الزمني.
                                </li>
                            )}
                            {Math.abs(reconciliation.gl_balance_total
                                       - reconciliation.derived_balance_period) > 0.01
                                && !period.from && !period.to && (
                                <li>
                                    رصيد GL الكلي ({fmt(reconciliation.gl_balance_total)}) ≠
                                    الرصيد المُستَخرَج من الجدول ({fmt(reconciliation.derived_balance_period)}).
                                </li>
                            )}
                        </ul>
                    </div>
                )}

                {/* Period summary */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 print:gap-2"
                     data-testid="sup-ledger-summary">
                    <SummaryCard label="الرصيد الافتتاحي"
                                 value={fmt(period.opening_balance)}
                                 testid="sum-opening" />
                    <SummaryCard label="إجمالي الفواتير (من GL)"
                                 value={fmt(period.total_invoiced)}
                                 color="rose"
                                 testid="sum-invoiced" />
                    <SummaryCard label="إجمالي السدادات (من GL)"
                                 value={fmt(period.total_paid)}
                                 color="emerald"
                                 testid="sum-paid" />
                    <SummaryCard label="الرصيد الختامي"
                                 value={fmt(period.closing_balance)}
                                 color={Number(period.closing_balance) > 0 ? "rose" : "emerald"}
                                 testid="sum-closing" />
                </div>

                {/* Chronological timeline */}
                <Section title={`📜 الجدول الزمني (${timeline.length} حركة)`}
                         testid="sup-ledger-timeline">
                    {timeline.length === 0 ? (
                        <div className="text-center text-slate-500 py-4 text-sm">
                            لا توجد حركات في الفترة المحددة.
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-[12px] border-collapse">
                                <thead>
                                    <tr className="bg-slate-100 text-slate-700">
                                        <th className="p-2 text-right border">التاريخ</th>
                                        <th className="p-2 text-right border">النوع</th>
                                        <th className="p-2 text-right border">رقم الفاتورة</th>
                                        <th className="p-2 text-right border">ملاحظات</th>
                                        <th className="p-2 text-right border">مدين</th>
                                        <th className="p-2 text-right border">دائن</th>
                                        <th className="p-2 text-right border">الرصيد بعد</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {timeline.map((t, i) => (
                                        <tr key={t.gl_entry_id || i}
                                            className={t.is_manual
                                                ? "bg-amber-50 hover:bg-amber-100"
                                                : "hover:bg-slate-50"}
                                            data-testid={`sup-ledger-row-${i}`}>
                                            <td className="p-2 border whitespace-nowrap">
                                                {ymd(t.created_at)}
                                            </td>
                                            <td className="p-2 border">
                                                {EntryTypeLabel(t.entry_type)}
                                                {t.is_manual && (
                                                    <span className="ml-1 inline-block px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-200 text-amber-900"
                                                          data-testid="manual-badge">
                                                        قيد يدوي
                                                    </span>
                                                )}
                                            </td>
                                            <td className="p-2 border">
                                                {t.linked_movement?.doc_number || "—"}
                                            </td>
                                            <td className="p-2 border max-w-[280px] text-slate-700">
                                                {t.notes || ""}
                                            </td>
                                            <td className="p-2 border text-rose-700 text-left font-mono">
                                                {t.side === "debit" ? fmt(t.amount) : ""}
                                            </td>
                                            <td className="p-2 border text-emerald-700 text-left font-mono">
                                                {t.side === "credit" ? fmt(t.amount) : ""}
                                            </td>
                                            <td className="p-2 border text-left font-mono font-bold">
                                                {fmt(t.running_balance)}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </Section>

                {/* Invoice cards */}
                <Section title={`📄 الفواتير (${invoices.length})`}
                         testid="sup-ledger-invoices">
                    {invoices.length === 0 ? (
                        <div className="text-center text-slate-500 py-4 text-sm">
                            لا توجد فواتير مرتبطة بقيود محاسبية في هذه الفترة.
                        </div>
                    ) : invoices.map((inv) => {
                        const isOpen = expanded[inv.movement_id];
                        const sb = STATUS_BADGE[inv.status] || STATUS_BADGE.unpaid;
                        return (
                            <div key={inv.movement_id}
                                 className="border border-slate-200 rounded-lg mb-2 print:rounded-none print:break-inside-avoid"
                                 data-testid={`sup-ledger-invoice-${inv.movement_id}`}>
                                <button onClick={() => setExpanded(p => ({
                                            ...p, [inv.movement_id]: !p[inv.movement_id] }))}
                                    className="w-full p-3 flex flex-wrap items-center gap-3 text-right bg-slate-50 hover:bg-slate-100 print:bg-white"
                                    data-testid={`sup-ledger-invoice-toggle-${inv.movement_id}`}>
                                    <span className="font-bold text-slate-900">
                                        فاتورة #{inv.doc_number || "—"}
                                    </span>
                                    <span className="text-[12px] text-slate-600">
                                        {ymd(inv.doc_date)}
                                    </span>
                                    <span className={`text-[11px] px-2 py-0.5 rounded-full border font-bold ${sb.cls}`}>
                                        {sb.label}
                                    </span>
                                    <span className="ml-auto font-mono text-sm">
                                        <span className="text-slate-700">الإجمالي: </span>
                                        <span className="font-bold">{fmt(inv.total_amount)}</span>
                                        <span className="text-slate-500"> | </span>
                                        <span className="text-emerald-700">مدفوع: {fmt(inv.paid_amount)}</span>
                                        <span className="text-slate-500"> | </span>
                                        <span className="text-rose-700">متبقي: {fmt(inv.remaining)}</span>
                                    </span>
                                    <span className="text-slate-400 print:hidden">
                                        {isOpen ? "▲" : "▼"}
                                    </span>
                                </button>
                                {(isOpen || true /* always print */) && (
                                    <div className={`p-3 border-t border-slate-200 ${isOpen ? "block" : "hidden print:block"}`}>
                                        {/* Line items */}
                                        {(inv.line_items || []).length > 0 && (
                                            <div className="mb-3">
                                                <div className="text-[11px] font-bold text-slate-600 mb-1">بنود الفاتورة</div>
                                                <table className="w-full text-[12px] border-collapse">
                                                    <thead>
                                                        <tr className="bg-slate-100">
                                                            <th className="p-1.5 text-right border">الوصف</th>
                                                            <th className="p-1.5 text-right border w-20">الكمية</th>
                                                            <th className="p-1.5 text-right border w-28">سعر الوحدة</th>
                                                            <th className="p-1.5 text-right border w-28">الإجمالي</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {inv.line_items.map((li, i) => (
                                                            <tr key={i}>
                                                                <td className="p-1.5 border">{li.description}</td>
                                                                <td className="p-1.5 border text-center font-mono">{li.quantity}</td>
                                                                <td className="p-1.5 border text-left font-mono">{fmt(li.unit_price)}</td>
                                                                <td className="p-1.5 border text-left font-mono">{fmt(Number(li.quantity || 0) * Number(li.unit_price || 0))}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        )}
                                        {/* Totals strip */}
                                        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[12px] mb-3">
                                            {inv.discount > 0 && (
                                                <Mini label="الخصم" value={fmt(inv.discount)} />
                                            )}
                                            {inv.tax > 0 && (
                                                <Mini label="الضريبة" value={fmt(inv.tax)} />
                                            )}
                                            <Mini label="إجمالي الفاتورة" value={fmt(inv.total_amount)} bold />
                                            {inv.payment_terms && (
                                                <Mini label="نوع السداد" value={inv.payment_terms} />
                                            )}
                                        </div>
                                        {/* GL legs */}
                                        <div className="mb-3">
                                            <div className="text-[11px] font-bold text-slate-600 mb-1">
                                                القيود المحاسبية (من general_ledger)
                                            </div>
                                            <table className="w-full text-[12px] border-collapse">
                                                <thead>
                                                    <tr className="bg-slate-100">
                                                        <th className="p-1.5 text-right border">الحساب</th>
                                                        <th className="p-1.5 text-right border w-24">مدين</th>
                                                        <th className="p-1.5 text-right border w-24">دائن</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {inv.gl_legs.map((leg, i) => (
                                                        <tr key={i}>
                                                            <td className="p-1.5 border">
                                                                {AccountLabel(leg)}
                                                            </td>
                                                            <td className="p-1.5 border text-left font-mono">
                                                                {leg.side === "debit" ? fmt(leg.amount) : ""}
                                                            </td>
                                                            <td className="p-1.5 border text-left font-mono">
                                                                {leg.side === "credit" ? fmt(leg.amount) : ""}
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                        {/* Payments applied */}
                                        {inv.payments_applied.length > 0 && (
                                            <div>
                                                <div className="text-[11px] font-bold text-slate-600 mb-1">
                                                    السدادات المرتبطة
                                                </div>
                                                <table className="w-full text-[12px] border-collapse">
                                                    <thead>
                                                        <tr className="bg-slate-100">
                                                            <th className="p-1.5 text-right border">التاريخ</th>
                                                            <th className="p-1.5 text-right border w-32">المبلغ</th>
                                                            <th className="p-1.5 text-right border">ملاحظات</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {inv.payments_applied.map((p, i) => (
                                                            <tr key={i}>
                                                                <td className="p-1.5 border">{ymd(p.date)}</td>
                                                                <td className="p-1.5 border text-left font-mono">{fmt(p.amount)}</td>
                                                                <td className="p-1.5 border">{p.notes}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </Section>

                {/* Manual entries section */}
                {manualEntries.length > 0 && (
                    <Section title={`✍️ قيود يدوية بدون فاتورة (${manualEntries.length})`}
                             testid="sup-ledger-manual">
                        <div className="text-[12px] text-slate-600 mb-2">
                            هذه قيود موجودة في general_ledger لكنها غير مرتبطة بفاتورة في
                            <code className="mx-1 bg-slate-100 px-1 rounded">financial_movements</code>
                            (قد تكون مدخلة يدوياً أو من نظام قديم).
                        </div>
                        <table className="w-full text-[12px] border-collapse">
                            <thead>
                                <tr className="bg-amber-50">
                                    <th className="p-2 text-right border">التاريخ</th>
                                    <th className="p-2 text-right border">النوع</th>
                                    <th className="p-2 text-right border">ملاحظات</th>
                                    <th className="p-2 text-right border">مدين</th>
                                    <th className="p-2 text-right border">دائن</th>
                                </tr>
                            </thead>
                            <tbody>
                                {manualEntries.map((m, i) => (
                                    <tr key={i} className="bg-amber-50/30">
                                        <td className="p-2 border whitespace-nowrap">{ymd(m.created_at)}</td>
                                        <td className="p-2 border">{EntryTypeLabel(m.entry_type)}</td>
                                        <td className="p-2 border max-w-[400px]">{m.notes}</td>
                                        <td className="p-2 border text-left font-mono text-rose-700">
                                            {m.side === "debit" ? fmt(m.amount) : ""}
                                        </td>
                                        <td className="p-2 border text-left font-mono text-emerald-700">
                                            {m.side === "credit" ? fmt(m.amount) : ""}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </Section>
                )}

                {/* Drift diagnostic */}
                {reconciliation.movements_orphaned_count > 0 && (
                    <Section title={`🔬 تشخيص التباين — فواتير بدون قيد محاسبي (${reconciliation.movements_orphaned_count})`}
                             testid="sup-ledger-drift-section">
                        <div className="text-[12px] text-rose-700 mb-2 font-bold">
                            هذه فواتير موجودة في <code>financial_movements</code> لكن
                            لم يُنشأ لها قيد في <code>general_ledger</code>. الرجاء
                            المراجعة المحاسبية.
                        </div>
                        <table className="w-full text-[12px] border-collapse">
                            <thead>
                                <tr className="bg-rose-100">
                                    <th className="p-2 text-right border">رقم</th>
                                    <th className="p-2 text-right border">التاريخ</th>
                                    <th className="p-2 text-right border">الإجمالي</th>
                                    <th className="p-2 text-right border">المدفوع</th>
                                    <th className="p-2 text-right border">ملاحظات</th>
                                </tr>
                            </thead>
                            <tbody>
                                {reconciliation.movements_orphaned.map((o, i) => (
                                    <tr key={i} className="bg-rose-50/40">
                                        <td className="p-2 border">{o.doc_number || "—"}</td>
                                        <td className="p-2 border whitespace-nowrap">{ymd(o.doc_date)}</td>
                                        <td className="p-2 border text-left font-mono">{fmt(o.total_amount)}</td>
                                        <td className="p-2 border text-left font-mono">{fmt(o.paid_amount)}</td>
                                        <td className="p-2 border max-w-[300px]">{o.notes}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </Section>
                )}

                {/* Reconciliation small footer */}
                <div className="text-[10px] text-slate-500 mt-2 print:mt-1 border-t border-slate-200 pt-1"
                     data-testid="sup-ledger-recon-footer">
                    رصيد GL الكلي: {fmt(reconciliation.gl_balance_total)} ر.س
                    {" · "}
                    رصيد مُستخرَج من الفترة: {fmt(reconciliation.derived_balance_period)} ر.س
                    {" · "}
                    تطابق: {reconciliation.balance_match ? "✓" : "✗"}
                    {" · "}
                    حركات الفترة: {reconciliation.gl_entries_in_period}
                    {" · "}
                    قيود يدوية: {reconciliation.gl_only_count}
                    {" · "}
                    فواتير drift: {reconciliation.movements_orphaned_count}
                </div>
            </div>
        </div>
    );
}

// ─── Helpers ────────────────────────────────────────────────────────

function SummaryCard({ label, value, color = "slate", testid }) {
    const palette = {
        slate:   "bg-slate-50  border-slate-200  text-slate-900",
        rose:    "bg-rose-50   border-rose-200   text-rose-900",
        emerald: "bg-emerald-50 border-emerald-200 text-emerald-900",
        amber:   "bg-amber-50  border-amber-200  text-amber-900",
    }[color] || "bg-slate-50 border-slate-200 text-slate-900";
    return (
        <div className={`rounded-lg border ${palette} p-3 print:p-2 print:rounded-none`}
             data-testid={testid}>
            <div className="text-[11px] text-slate-600 mb-0.5">{label}</div>
            <div className="text-base font-extrabold font-mono">{value} ر.س</div>
        </div>
    );
}

function Section({ title, children, testid }) {
    return (
        <div className="bg-white border border-slate-200 rounded-xl p-3 print:rounded-none print:p-2 print:border"
             data-testid={testid}>
            <h3 className="text-sm font-extrabold text-slate-900 mb-2">{title}</h3>
            {children}
        </div>
    );
}

function Mini({ label, value, bold = false }) {
    return (
        <div className="text-[11px]">
            <span className="text-slate-500">{label}: </span>
            <span className={`${bold ? "font-bold" : ""} font-mono`}>{value}</span>
        </div>
    );
}

function EntryTypeLabel(t) {
    const m = {
        supplier_invoice:  "فاتورة مورد",
        supplier_payment:  "سداد مورد",
        purchase_return:   "مرتجع مشتريات",
        opening_balance:   "رصيد افتتاحي",
        adjustment:        "تسوية",
        reversal:          "عكس قيد",
    };
    return m[t] || t || "—";
}

function AccountLabel(leg) {
    if (!leg) return "—";
    if (leg.entity_type === "supplier")  return "ذمم الموردين";
    if (leg.entity_type === "bank")      return "بنك/صندوق";
    if (leg.entity_type === "expense")   return `مصاريف: ${leg.entity_id || ""}`;
    if (leg.entity_type === "employee")  return `موظف · ${leg.sub_account || ""}`;
    if (leg.entity_type === "purchases") return "مشتريات";
    return `${leg.entity_type} · ${leg.sub_account || ""}`;
}
