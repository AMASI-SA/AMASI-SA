/**
 * فواتير المشتريات — Purchase Invoices (Iter-103)
 *
 * Workflow:
 *   1. Pick a supplier from the unified counterparties registry.
 *   2. Add line items (product_name, sku, qty, unit_price).
 *   3. Submit → backend auto-creates a `liabilities` (kind=supplier) row
 *      so the amount instantly flows into the Financial Position.
 *   4. Pay the linked liability from the Financial Input Hub.
 *
 * NO inventory side effects, NO product_costs auto-update. Quantities
 * are recorded for the paper trail only.
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
    Receipt, Plus, Trash, FileText, MagnifyingGlass, PencilSimple,
    CaretRight, ArrowRight,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";


const inputCls =
    "w-full px-3 py-2.5 text-sm border border-slate-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-violet-500";

const fmt = (v) =>
    Number(v || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

const todayIso = () => new Date().toISOString().slice(0, 10);

const STATUS_LABEL = {
    unpaid:  { label: "غير مسدَّدة",  tone: "bg-rose-50 text-rose-800 border-rose-200" },
    partial: { label: "مدفوعة جزئياً", tone: "bg-amber-50 text-amber-800 border-amber-200" },
    paid:    { label: "مسدَّدة",      tone: "bg-emerald-50 text-emerald-800 border-emerald-200" },
};


// ── Create / Edit dialog ────────────────────────────────────────────
function InvoiceDialog({ open, suppliers, editing, onClose, onSaved }) {
    const [form, setForm] = useState(() => emptyForm());
    const [busy, setBusy] = useState(false);

    function emptyForm() {
        return {
            supplier_counterparty_id: "",
            invoice_number: "",
            invoice_date: todayIso(),
            due_date: "",
            tax_amount: "0",
            notes: "",
            lines: [{ product_name: "", sku: "", quantity: "1", unit_price: "" }],
        };
    }

    useEffect(() => {
        if (!open) return;
        if (editing) {
            setForm({
                supplier_counterparty_id: editing.supplier_counterparty_id,
                invoice_number: editing.invoice_number || "",
                invoice_date: editing.invoice_date,
                due_date: editing.due_date || "",
                tax_amount: String(editing.tax_amount ?? 0),
                notes: editing.notes || "",
                lines: editing.lines?.length
                    ? editing.lines.map((l) => ({
                        product_name: l.product_name || "",
                        sku: l.sku || "",
                        quantity: String(l.quantity ?? 1),
                        unit_price: String(l.unit_price ?? 0),
                    }))
                    : [{ product_name: "", sku: "", quantity: "1", unit_price: "" }],
            });
        } else {
            setForm(emptyForm());
        }
    }, [open, editing]);

    const setLine = (idx, key, val) => {
        setForm((f) => {
            const copy = f.lines.slice();
            copy[idx] = { ...copy[idx], [key]: val };
            return { ...f, lines: copy };
        });
    };
    const addLine = () => setForm((f) => ({
        ...f, lines: [...f.lines, { product_name: "", sku: "", quantity: "1", unit_price: "" }],
    }));
    const removeLine = (idx) => setForm((f) => ({
        ...f, lines: f.lines.length === 1 ? f.lines : f.lines.filter((_, i) => i !== idx),
    }));

    const totals = useMemo(() => {
        const subtotal = form.lines.reduce((acc, l) => {
            const q = Number(l.quantity || 0);
            const p = Number(l.unit_price || 0);
            return acc + q * p;
        }, 0);
        const tax = Number(form.tax_amount || 0);
        return {
            subtotal: Math.round(subtotal * 100) / 100,
            tax: Math.round(tax * 100) / 100,
            total: Math.round((subtotal + tax) * 100) / 100,
        };
    }, [form.lines, form.tax_amount]);

    const submit = async (e) => {
        e?.preventDefault?.();
        if (!form.supplier_counterparty_id) { toast.error("اختر مورداً"); return; }
        const cleanLines = form.lines.filter(
            (l) => l.product_name.trim() && Number(l.quantity) > 0 && Number(l.unit_price) >= 0,
        );
        if (cleanLines.length === 0) { toast.error("أضف بنداً واحداً صالحاً على الأقل"); return; }

        const body = {
            supplier_counterparty_id: form.supplier_counterparty_id,
            invoice_number: form.invoice_number.trim() || null,
            invoice_date: form.invoice_date,
            due_date: form.due_date || null,
            tax_amount: Number(form.tax_amount || 0),
            notes: form.notes.trim(),
            lines: cleanLines.map((l) => ({
                product_name: l.product_name.trim(),
                sku: l.sku.trim() || null,
                quantity: Number(l.quantity),
                unit_price: Number(l.unit_price),
            })),
        };

        setBusy(true);
        try {
            if (editing) {
                await api.put(`/purchase-invoices/${editing.id}`, body);
                toast.success("تم تعديل الفاتورة");
            } else {
                await api.post("/purchase-invoices", body);
                toast.success("تم إنشاء الفاتورة وإضافة المبلغ كالتزام على المورد");
            }
            onSaved();
            onClose();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذّر الحفظ");
        } finally { setBusy(false); }
    };

    if (!open) return null;

    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="pinv-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-3xl my-8">
                <form onSubmit={submit}>
                    <div className="flex items-center justify-between p-5 border-b border-slate-100">
                        <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                            <Receipt size={22} weight="duotone" className="text-violet-700" />
                            {editing ? "تعديل فاتورة شراء" : "فاتورة شراء جديدة"}
                        </h2>
                        <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                    </div>

                    <div className="p-5 space-y-4">
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            <div className="sm:col-span-2">
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">المورد *</label>
                                <select
                                    value={form.supplier_counterparty_id}
                                    onChange={(e) => setForm((f) => ({ ...f, supplier_counterparty_id: e.target.value }))}
                                    className={inputCls}
                                    disabled={!!editing}
                                    data-testid="pinv-supplier"
                                >
                                    <option value="">— اختر مورداً مسجَّلاً —</option>
                                    {suppliers.map((s) => (
                                        <option key={s.id} value={s.id}>{s.name}</option>
                                    ))}
                                </select>
                                {suppliers.length === 0 && (
                                    <div className="mt-1 text-[11px] text-rose-600">
                                        لا يوجد موردون. أضف مورداً من{" "}
                                        <Link to="/counterparties" className="font-bold underline">قائمة الأطراف الموحَّدة</Link>.
                                    </div>
                                )}
                            </div>
                            <div>
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">رقم الفاتورة</label>
                                <input value={form.invoice_number} onChange={(e) => setForm({ ...form, invoice_number: e.target.value })} className={inputCls} placeholder="PO-1234" data-testid="pinv-number" />
                            </div>
                            <div>
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">تاريخ الفاتورة *</label>
                                <input type="date" value={form.invoice_date} onChange={(e) => setForm({ ...form, invoice_date: e.target.value })} className={inputCls} data-testid="pinv-date" />
                            </div>
                            <div>
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">تاريخ الاستحقاق</label>
                                <input type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} className={inputCls} data-testid="pinv-due" />
                            </div>
                        </div>

                        {/* Line items */}
                        <div>
                            <div className="flex items-center justify-between mb-2">
                                <label className="block text-xs font-bold text-slate-700">بنود الفاتورة *</label>
                                <button
                                    type="button" onClick={addLine}
                                    className="flex items-center gap-1 px-2 py-1 rounded text-xs font-bold text-violet-700 hover:bg-violet-50"
                                    data-testid="pinv-add-line"
                                >
                                    <Plus size={12} /> إضافة بند
                                </button>
                            </div>
                            <div className="border border-slate-200 rounded-lg overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead className="bg-slate-50 text-slate-600 text-[11px]">
                                        <tr>
                                            <th className="text-right p-2 font-bold">اسم المنتج</th>
                                            <th className="text-right p-2 font-bold w-28">SKU</th>
                                            <th className="text-right p-2 font-bold w-20">الكمية</th>
                                            <th className="text-right p-2 font-bold w-28">سعر الوحدة</th>
                                            <th className="text-right p-2 font-bold w-28">الإجمالي</th>
                                            <th className="w-10"></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {form.lines.map((ln, i) => {
                                            const t = (Number(ln.quantity || 0) * Number(ln.unit_price || 0)) || 0;
                                            return (
                                                <tr key={i} className="border-t border-slate-100" data-testid={`pinv-line-${i}`}>
                                                    <td className="p-1.5"><input value={ln.product_name} onChange={(e) => setLine(i, "product_name", e.target.value)} className={`${inputCls} text-xs`} placeholder="كرتون 30×30" data-testid={`pinv-line-${i}-name`} /></td>
                                                    <td className="p-1.5"><input value={ln.sku} onChange={(e) => setLine(i, "sku", e.target.value)} className={`${inputCls} text-xs`} data-testid={`pinv-line-${i}-sku`} /></td>
                                                    <td className="p-1.5"><input type="number" min="0" step="0.01" value={ln.quantity} onChange={(e) => setLine(i, "quantity", e.target.value)} className={`${inputCls} text-xs num`} data-testid={`pinv-line-${i}-qty`} /></td>
                                                    <td className="p-1.5"><input type="number" min="0" step="0.01" value={ln.unit_price} onChange={(e) => setLine(i, "unit_price", e.target.value)} className={`${inputCls} text-xs num`} data-testid={`pinv-line-${i}-price`} /></td>
                                                    <td className="p-1.5 num text-xs font-bold text-slate-900">{fmt(t)}</td>
                                                    <td className="p-1.5 text-center">
                                                        <button type="button" onClick={() => removeLine(i)} className="text-slate-400 hover:text-rose-600 disabled:opacity-30" disabled={form.lines.length === 1} data-testid={`pinv-line-${i}-remove`}>
                                                            <Trash size={14} />
                                                        </button>
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            <div>
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">الضريبة (ر.س)</label>
                                <input type="number" step="0.01" min="0" value={form.tax_amount} onChange={(e) => setForm({ ...form, tax_amount: e.target.value })} className={`${inputCls} num`} data-testid="pinv-tax" />
                            </div>
                            <div>
                                <label className="block text-xs font-bold text-slate-700 mb-1.5">ملاحظات</label>
                                <input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className={inputCls} data-testid="pinv-notes" />
                            </div>
                        </div>

                        <div className="rounded-lg bg-violet-50 border border-violet-200 p-3 text-xs space-y-1">
                            <div className="flex items-center justify-between text-violet-900">
                                <span>المجموع قبل الضريبة</span>
                                <b className="num">{fmt(totals.subtotal)} ر.س</b>
                            </div>
                            <div className="flex items-center justify-between text-violet-900">
                                <span>الضريبة</span>
                                <b className="num">{fmt(totals.tax)} ر.س</b>
                            </div>
                            <div className="flex items-center justify-between text-violet-950 text-sm pt-2 border-t border-violet-200">
                                <span className="font-bold">الإجمالي (سيُضاف كالتزام على المورد)</span>
                                <b className="num text-base" data-testid="pinv-grand-total">{fmt(totals.total)} ر.س</b>
                            </div>
                        </div>
                    </div>

                    <div className="p-5 border-t border-slate-100 flex justify-end gap-2">
                        <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-bold">
                            إلغاء
                        </button>
                        <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-bold hover:bg-slate-800 disabled:opacity-50" data-testid="pinv-submit">
                            {busy ? "جاري الحفظ…" : (editing ? "حفظ التعديل" : "إنشاء الفاتورة")}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}


// ── Supplier statement panel ────────────────────────────────────────
function SupplierStatement({ cpId, open, onClose }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (!open || !cpId) return;
        setLoading(true);
        api.get(`/purchase-invoices/supplier/${cpId}/statement`)
            .then((r) => setData(r.data))
            .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
            .finally(() => setLoading(false));
    }, [open, cpId]);

    if (!open) return null;
    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="pinv-statement-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-3xl my-8">
                <div className="flex items-center justify-between p-5 border-b border-slate-100">
                    <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                        <FileText size={22} weight="duotone" className="text-violet-700" />
                        كشف حساب مورد
                    </h2>
                    <button onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                </div>
                <div className="p-5">
                    {loading || !data ? (
                        <div className="text-center text-slate-500 text-sm py-8">جاري التحميل…</div>
                    ) : (
                        <>
                            <div className="mb-4">
                                <div className="text-xs text-slate-500">المورد</div>
                                <div className="text-base font-extrabold text-slate-900">{data.supplier.name}</div>
                            </div>
                            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5">
                                <div className="rounded-lg bg-slate-50 border border-slate-200 p-3">
                                    <div className="text-[11px] text-slate-600 font-bold">إجمالي فواتير الشراء</div>
                                    <div className="num text-lg font-extrabold text-slate-900 mt-1">{fmt(data.totals.total_invoiced)} ر.س</div>
                                </div>
                                <div className="rounded-lg bg-emerald-50 border border-emerald-200 p-3">
                                    <div className="text-[11px] text-emerald-800 font-bold">إجمالي المسدَّد</div>
                                    <div className="num text-lg font-extrabold text-emerald-900 mt-1">{fmt(data.totals.total_paid)} ر.س</div>
                                </div>
                                <div className="rounded-lg bg-rose-50 border border-rose-200 p-3" data-testid="pinv-statement-balance">
                                    <div className="text-[11px] text-rose-800 font-bold">الرصيد المتبقي له</div>
                                    <div className="num text-lg font-extrabold text-rose-900 mt-1">{fmt(data.totals.balance_owed)} ر.س</div>
                                </div>
                            </div>
                            <div className="overflow-x-auto border border-slate-200 rounded-lg">
                                <table className="w-full text-sm">
                                    <thead className="bg-slate-50 text-slate-600 text-xs">
                                        <tr>
                                            <th className="text-right p-2 font-bold">رقم</th>
                                            <th className="text-right p-2 font-bold">تاريخ</th>
                                            <th className="text-right p-2 font-bold">الإجمالي</th>
                                            <th className="text-right p-2 font-bold">المسدَّد</th>
                                            <th className="text-right p-2 font-bold">المتبقي</th>
                                            <th className="text-right p-2 font-bold">الحالة</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.invoices.length === 0 && (
                                            <tr><td colSpan={6} className="p-6 text-center text-slate-500 text-xs">لا توجد فواتير بعد</td></tr>
                                        )}
                                        {data.invoices.map((i) => {
                                            const s = STATUS_LABEL[i.status] || STATUS_LABEL.unpaid;
                                            return (
                                                <tr key={i.id} className="border-t border-slate-100 hover:bg-slate-50/60">
                                                    <td className="p-2 text-xs">{i.invoice_number || "—"}</td>
                                                    <td className="p-2 text-xs">{i.invoice_date}</td>
                                                    <td className="p-2 num text-xs font-bold text-slate-900">{fmt(i.total)}</td>
                                                    <td className="p-2 num text-xs text-emerald-700">{fmt(i.paid_amount)}</td>
                                                    <td className="p-2 num text-xs font-bold text-rose-700">{fmt(i.remaining_amount)}</td>
                                                    <td className="p-2"><span className={`px-2 py-0.5 rounded text-[11px] font-bold border ${s.tone}`}>{s.label}</span></td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}


// ── Main page ───────────────────────────────────────────────────────
export default function PurchaseInvoices() {
    const [invoices, setInvoices] = useState([]);
    const [suppliers, setSuppliers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("");
    const [supplierFilter, setSupplierFilter] = useState("");
    const [dlg, setDlg] = useState({ open: false, editing: null });
    const [stmt, setStmt] = useState({ open: false, cpId: null });

    const load = async () => {
        setLoading(true);
        try {
            const [invRes, cpRes] = await Promise.all([
                api.get("/purchase-invoices?limit=500"),
                api.get("/counterparties?kind=supplier"),
            ]);
            setInvoices(invRes.data?.items || []);
            setSuppliers((cpRes.data?.items || []).filter((c) => c.kind === "supplier" || c.kind === "general"));
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التحميل");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        return invoices.filter((i) => {
            if (statusFilter && i.status !== statusFilter) return false;
            if (supplierFilter && i.supplier_counterparty_id !== supplierFilter) return false;
            if (q) {
                const hay = `${i.invoice_number || ""} ${i.supplier_name || ""} ${i.notes || ""}`.toLowerCase();
                if (!hay.includes(q)) return false;
            }
            return true;
        });
    }, [invoices, search, statusFilter, supplierFilter]);

    const totals = useMemo(() => {
        let invoiced = 0, paid = 0, owed = 0;
        for (const i of filtered) {
            invoiced += Number(i.total || 0);
            paid     += Number(i.paid_amount || 0);
            owed     += Number(i.remaining_amount || 0);
        }
        return { invoiced, paid, owed };
    }, [filtered]);

    const remove = async (inv) => {
        if (!window.confirm(`حذف فاتورة ${inv.invoice_number || ""} للمورد ${inv.supplier_name}؟`)) return;
        try {
            await api.delete(`/purchase-invoices/${inv.id}`);
            toast.success("تم حذف الفاتورة وإزالة الالتزام المرتبط");
            load();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        }
    };

    return (
        <div dir="rtl" data-testid="purchase-invoices-page" className="space-y-5">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                        <Receipt size={28} weight="duotone" className="text-violet-700" />
                        فواتير المشتريات
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        كل فاتورة تنشئ التزاماً تلقائياً على المورد. السداد يتم من{" "}
                        <Link to="/financial-input-hub" className="text-violet-700 font-bold">مركز الإدخال المالي</Link>.
                    </p>
                </div>
                <button
                    onClick={() => setDlg({ open: true, editing: null })}
                    className="self-start px-4 py-2.5 rounded-lg bg-slate-900 text-white text-sm font-bold hover:bg-slate-800 flex items-center gap-2"
                    data-testid="pinv-new-btn"
                >
                    <Plus size={16} /> فاتورة شراء جديدة
                </button>
            </div>

            {/* KPIs */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                    <div className="text-xs font-bold text-slate-600 mb-1">إجمالي الفواتير (بعد التصفية)</div>
                    <div className="num text-2xl font-extrabold text-slate-900">{fmt(totals.invoiced)} ر.س</div>
                </div>
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                    <div className="text-xs font-bold text-emerald-800 mb-1">المسدَّد منها</div>
                    <div className="num text-2xl font-extrabold text-emerald-900">{fmt(totals.paid)} ر.س</div>
                </div>
                <div className="rounded-xl border border-rose-200 bg-rose-50 p-4">
                    <div className="text-xs font-bold text-rose-800 mb-1">المتبقي للموردين</div>
                    <div className="num text-2xl font-extrabold text-rose-900">{fmt(totals.owed)} ر.س</div>
                </div>
            </div>

            {/* Filters */}
            <div className="bg-white border border-slate-200 rounded-xl p-3 flex flex-col sm:flex-row gap-3 sm:items-center">
                <div className="relative sm:w-72">
                    <MagnifyingGlass size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="بحث برقم الفاتورة أو المورد…" className={`${inputCls} pr-8`} data-testid="pinv-search" />
                </div>
                <select value={supplierFilter} onChange={(e) => setSupplierFilter(e.target.value)} className={`${inputCls} sm:w-56`} data-testid="pinv-filter-supplier">
                    <option value="">كل الموردين</option>
                    {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                <div className="flex gap-1">
                    {[
                        { v: "",        label: "الكل" },
                        { v: "unpaid",  label: "غير مسدَّدة" },
                        { v: "partial", label: "جزئية" },
                        { v: "paid",    label: "مسدَّدة" },
                    ].map((b) => (
                        <button key={b.v || "all"} onClick={() => setStatusFilter(b.v)} data-testid={`pinv-filter-${b.v || "all"}`}
                            className={`px-3 py-1.5 rounded-lg text-xs font-bold ${statusFilter === b.v ? "bg-slate-900 text-white" : "bg-white border border-slate-200 text-slate-700 hover:bg-slate-50"}`}>
                            {b.label}
                        </button>
                    ))}
                </div>
            </div>

            {/* Table */}
            <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
                {loading ? (
                    <div className="p-10 text-center text-slate-500 text-sm">جاري التحميل…</div>
                ) : filtered.length === 0 ? (
                    <div className="p-10 text-center text-slate-500 text-sm" data-testid="pinv-empty">
                        لا توجد فواتير. ابدأ بإنشاء فاتورة شراء جديدة.
                    </div>
                ) : (
                    <table className="w-full text-sm">
                        <thead className="bg-slate-50 text-slate-600 text-xs">
                            <tr>
                                <th className="text-right p-3 font-bold">رقم الفاتورة</th>
                                <th className="text-right p-3 font-bold">المورد</th>
                                <th className="text-right p-3 font-bold">التاريخ</th>
                                <th className="text-right p-3 font-bold">الإجمالي</th>
                                <th className="text-right p-3 font-bold">المسدَّد</th>
                                <th className="text-right p-3 font-bold">المتبقي</th>
                                <th className="text-right p-3 font-bold">الحالة</th>
                                <th className="text-right p-3 font-bold w-36">إجراءات</th>
                            </tr>
                        </thead>
                        <tbody>
                            {filtered.map((i) => {
                                const s = STATUS_LABEL[i.status] || STATUS_LABEL.unpaid;
                                const payable = (i.remaining_amount || 0) > 0;
                                return (
                                    <tr key={i.id} className="border-t border-slate-100 hover:bg-slate-50/60" data-testid={`pinv-row-${i.id}`}>
                                        <td className="p-3 font-bold text-slate-900">{i.invoice_number || "—"}</td>
                                        <td className="p-3">
                                            <button onClick={() => setStmt({ open: true, cpId: i.supplier_counterparty_id })} className="text-violet-700 hover:underline font-bold" data-testid={`pinv-supplier-link-${i.id}`}>
                                                {i.supplier_name} <CaretRight size={11} className="inline" />
                                            </button>
                                        </td>
                                        <td className="p-3 text-xs text-slate-600">{i.invoice_date}</td>
                                        <td className="p-3 num font-bold text-slate-900">{fmt(i.total)}</td>
                                        <td className="p-3 num text-emerald-700">{fmt(i.paid_amount)}</td>
                                        <td className="p-3 num font-bold text-rose-700">{fmt(i.remaining_amount)}</td>
                                        <td className="p-3"><span className={`px-2 py-0.5 rounded text-[11px] font-bold border ${s.tone}`}>{s.label}</span></td>
                                        <td className="p-3">
                                            <div className="flex items-center gap-1">
                                                {payable && (
                                                    <Link to="/financial-input-hub" className="px-2 py-1 rounded text-[11px] font-bold bg-violet-700 text-white hover:bg-violet-800" title="سداد من مركز الإدخال" data-testid={`pinv-pay-${i.id}`}>
                                                        سداد <ArrowRight size={10} className="inline" />
                                                    </Link>
                                                )}
                                                <button onClick={() => setDlg({ open: true, editing: i })} className="p-1.5 rounded hover:bg-slate-100 text-slate-600 hover:text-violet-700" title="تعديل" data-testid={`pinv-edit-${i.id}`} disabled={i.paid_amount > 0}>
                                                    <PencilSimple size={14} />
                                                </button>
                                                <button onClick={() => remove(i)} className="p-1.5 rounded hover:bg-rose-50 text-slate-600 hover:text-rose-700" title="حذف" data-testid={`pinv-delete-${i.id}`} disabled={i.paid_amount > 0}>
                                                    <Trash size={14} />
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}
            </div>

            <InvoiceDialog
                open={dlg.open}
                suppliers={suppliers}
                editing={dlg.editing}
                onClose={() => setDlg({ open: false, editing: null })}
                onSaved={load}
            />
            <SupplierStatement
                open={stmt.open}
                cpId={stmt.cpId}
                onClose={() => setStmt({ open: false, cpId: null })}
            />
        </div>
    );
}
