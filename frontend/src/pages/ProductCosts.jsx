import { useEffect, useMemo, useRef, useState } from "react";
import {
    Package, Plus, MagnifyingGlass, PencilSimple, Trash, UploadSimple,
    Warning, ArrowsClockwise, Storefront, X, CheckCircle, Tag, ChartLineUp,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

/**
 * ProductCosts — `/product-costs` (iteration 19).
 *
 * Catalogue page where the merchant maintains per-SKU purchase costs so
 * the dashboard's "total_product_cost" reflects REAL profit (not just a
 * manual aggregate from /daily-costs).
 *
 * Tabs:
 *   • "كل المنتجات" (catalogue) — search, edit, delete, add.
 *   • "بدون تكلفة" (missing) — order line items whose SKU has no cost
 *     yet, with "Add Now" inline action.
 *
 * Uses /api/product-costs/* under the hood.
 */

const fmtMoney = (v) =>
    Number.isFinite(Number(v)) ? Number(v).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    }) : "0.00";

const blankForm = {
    sku: "", product_id: "", product_name: "",
    supplier_name: "", supplier_country: "", supplier_notes: "",
    cost_price: "", currency: "SAR",
};

function AddEditModal({ open, initial, onClose, onSaved }) {
    const [form, setForm] = useState(blankForm);
    const [saving, setSaving] = useState(false);
    const isEdit = !!(initial && initial.id);
    useEffect(() => {
        setForm(initial ? { ...blankForm, ...initial } : blankForm);
    }, [initial, open]);
    if (!open) return null;

    const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

    const submit = async () => {
        if (!form.sku?.trim() || !form.product_name?.trim()) {
            toast.error("SKU واسم المنتج مطلوبان");
            return;
        }
        if (!form.cost_price || isNaN(Number(form.cost_price)) || Number(form.cost_price) < 0) {
            toast.error("تكلفة المنتج يجب أن تكون رقماً موجباً");
            return;
        }
        setSaving(true);
        try {
            const payload = {
                ...form,
                cost_price: Number(form.cost_price),
                currency: form.currency || "SAR",
            };
            if (isEdit) {
                await api.put(`/product-costs/${initial.id}`, payload);
                toast.success("تم تحديث المنتج");
            } else {
                await api.post("/product-costs/", payload);
                toast.success("تمت إضافة المنتج");
            }
            onSaved();
            onClose();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setSaving(false); }
    };

    return (
        <div
            className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4"
            data-testid="product-cost-modal"
            onClick={(e) => e.target === e.currentTarget && onClose()}
        >
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
                <div className="px-6 py-4 border-b border-border flex items-center justify-between gap-3 sticky top-0 bg-white">
                    <h2 className="text-xl font-extrabold" style={{ fontFamily: "Tajawal" }}>
                        {isEdit ? "تعديل تكلفة المنتج" : "إضافة منتج جديد"}
                    </h2>
                    <button
                        onClick={onClose}
                        className="p-1 hover:bg-accent rounded-lg"
                        data-testid="product-cost-modal-close-btn"
                    >
                        <X size={20} />
                    </button>
                </div>
                <div className="p-6 space-y-4">
                    <div>
                        <label className="text-xs font-bold text-muted-foreground mb-1 block">
                            SKU <span className="text-red-500">*</span>
                        </label>
                        <input
                            type="text"
                            value={form.sku}
                            onChange={set("sku")}
                            disabled={isEdit}
                            dir="ltr"
                            placeholder="NECK001"
                            className="w-full px-3 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand disabled:bg-accent/30 disabled:cursor-not-allowed"
                            data-testid="product-cost-sku-input"
                        />
                        {isEdit && (
                            <div className="text-[10px] text-muted-foreground mt-1">
                                لا يمكن تعديل SKU بعد الإضافة (مفتاح الربط).
                            </div>
                        )}
                    </div>
                    <div>
                        <label className="text-xs font-bold text-muted-foreground mb-1 block">
                            اسم المنتج <span className="text-red-500">*</span>
                        </label>
                        <input
                            type="text"
                            value={form.product_name}
                            onChange={set("product_name")}
                            placeholder="سلسال مضيء"
                            className="w-full px-3 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand"
                            data-testid="product-cost-name-input"
                        />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className="text-xs font-bold text-muted-foreground mb-1 block">
                                تكلفة الشراء <span className="text-red-500">*</span>
                            </label>
                            <input
                                type="number"
                                step="0.01"
                                min="0"
                                value={form.cost_price}
                                onChange={set("cost_price")}
                                placeholder="18.00"
                                className="w-full px-3 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand tabular-nums"
                                data-testid="product-cost-price-input"
                            />
                        </div>
                        <div>
                            <label className="text-xs font-bold text-muted-foreground mb-1 block">
                                العملة
                            </label>
                            <select
                                value={form.currency}
                                onChange={set("currency")}
                                className="w-full px-3 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand"
                                data-testid="product-cost-currency-select"
                            >
                                <option value="SAR">SAR (ر.س)</option>
                                <option value="USD">USD</option>
                                <option value="AED">AED</option>
                            </select>
                        </div>
                    </div>
                    <div>
                        <label className="text-xs font-bold text-muted-foreground mb-1 block">
                            Product ID (Salla — اختياري)
                        </label>
                        <input
                            type="text"
                            value={form.product_id}
                            onChange={set("product_id")}
                            dir="ltr"
                            placeholder="123456789"
                            className="w-full px-3 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand"
                            data-testid="product-cost-product-id-input"
                        />
                        <div className="text-[10px] text-muted-foreground mt-1">
                            احتياطي للحالات النادرة التي تأتي فيها الطلبات بدون SKU.
                        </div>
                    </div>

                    {/* ── Supplier block (iteration 20) — manual-only ────────
                        Per merchant decision: supplier data is NEVER imported
                        from Excel. The accounting (profit) logic ignores
                        these fields entirely — they're for catalog management
                        only. */}
                    <div className="border-t border-border pt-4 space-y-3">
                        <div className="text-xs font-bold text-muted-foreground flex items-center gap-2 flex-wrap">
                            <Storefront size={14} weight="fill" className="text-amber-600" />
                            <span>بيانات المورد (إدارة يدوية)</span>
                            <span className="text-[10px] px-1.5 py-0.5 bg-blue-100 text-blue-800 rounded-full font-bold">
                                لا تؤثر على احتساب الربح
                            </span>
                        </div>
                        <div>
                            <label className="text-xs font-bold text-muted-foreground mb-1 block">
                                اسم المورد (اختياري)
                            </label>
                            <input
                                type="text"
                                value={form.supplier_name}
                                onChange={set("supplier_name")}
                                placeholder="مورد سلسال"
                                className="w-full px-3 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand"
                                data-testid="product-cost-supplier-input"
                            />
                        </div>
                        <div>
                            <label className="text-xs font-bold text-muted-foreground mb-1 block">
                                بلد المورد (اختياري)
                            </label>
                            <input
                                type="text"
                                value={form.supplier_country}
                                onChange={set("supplier_country")}
                                placeholder="الصين / تركيا / السعودية…"
                                className="w-full px-3 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand"
                                data-testid="product-cost-supplier-country-input"
                            />
                        </div>
                        <div>
                            <label className="text-xs font-bold text-muted-foreground mb-1 block">
                                ملاحظات المورد (اختياري)
                            </label>
                            <textarea
                                rows={2}
                                value={form.supplier_notes}
                                onChange={set("supplier_notes")}
                                placeholder="مدة التوريد، شروط الدفع، أو أي ملاحظات…"
                                className="w-full px-3 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand resize-y"
                                data-testid="product-cost-supplier-notes-input"
                            />
                        </div>
                    </div>
                </div>
                <div className="px-6 py-4 border-t border-border flex items-center justify-end gap-2 sticky bottom-0 bg-white">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 text-sm border border-border rounded-lg hover:bg-accent"
                    >
                        إلغاء
                    </button>
                    <button
                        onClick={submit}
                        disabled={saving}
                        className="px-4 py-2 bg-brand text-white rounded-lg font-bold text-sm hover:opacity-90 disabled:opacity-50"
                        data-testid="product-cost-save-btn"
                    >
                        {saving ? "جاري الحفظ…" : (isEdit ? "حفظ التعديل" : "إضافة")}
                    </button>
                </div>
            </div>
        </div>
    );
}

function SummaryCards({ summary }) {
    if (!summary) return null;
    const cards = [
        { label: "صرف اليوم على المنتجات", value: summary.today_total, hint: "تكلفة المنتجات المباعة اليوم" },
        { label: "صرف الشهر على المنتجات", value: summary.month_total, hint: `منذ ${summary.month_start}` },
        { label: "متوسط تكلفة المنتج", value: summary.avg_cost, hint: `عبر ${summary.active_products} منتج نشط` },
        { label: "عدد المنتجات النشطة", value: summary.active_products, money: false },
    ];
    return (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3" data-testid="product-cost-summary-grid">
            {cards.map((c, i) => (
                <div key={i} className="rounded-xl border border-border bg-white p-4">
                    <div className="text-xs text-muted-foreground font-semibold mb-1">{c.label}</div>
                    <div className="text-2xl font-extrabold text-foreground tabular-nums" style={{ fontFamily: "Tajawal" }}>
                        {c.money === false ? c.value : `${fmtMoney(c.value)} ر.س`}
                    </div>
                    {c.hint && <div className="text-[10px] text-muted-foreground mt-1">{c.hint}</div>}
                </div>
            ))}
        </div>
    );
}

function CatalogueTab({ items, total, search, setSearch, onEdit, onDelete, loading }) {
    if (loading) return (
        <div className="text-center py-12 text-muted-foreground" data-testid="catalogue-loading">
            جاري التحميل…
        </div>
    );
    return (
        <div className="space-y-4">
            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
                <div className="relative flex-1 max-w-md">
                    <MagnifyingGlass size={16} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <input
                        type="text"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="ابحث بالاسم أو SKU أو المورد…"
                        className="w-full pr-10 pl-3 py-2 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-brand"
                        data-testid="catalogue-search-input"
                    />
                </div>
                <div className="text-xs text-muted-foreground">
                    {total} منتج
                </div>
            </div>

            {items.length === 0 ? (
                <div className="rounded-xl border-2 border-dashed border-border bg-white p-8 text-center" data-testid="catalogue-empty-state">
                    <Package size={48} weight="duotone" className="text-brand mx-auto mb-3" />
                    <h3 className="text-lg font-bold mb-2">
                        {search ? "لا توجد نتائج" : "لا توجد منتجات بعد"}
                    </h3>
                    <p className="text-sm text-muted-foreground">
                        {search
                            ? "جرّب كلمة بحث أخرى"
                            : 'اضغط "إضافة منتج" أو "استيراد Excel" للبدء.'}
                    </p>
                </div>
            ) : (
                <div className="overflow-x-auto rounded-xl border border-border bg-white">
                    <table className="w-full text-sm" data-testid="catalogue-table">
                        <thead className="bg-accent/40 text-xs">
                            <tr>
                                <th className="px-3 py-2 text-start font-bold">اسم المنتج</th>
                                <th className="px-3 py-2 text-start font-bold" dir="ltr">SKU</th>
                                <th className="px-3 py-2 text-start font-bold">المورد</th>
                                <th className="px-3 py-2 text-end font-bold">التكلفة</th>
                                <th className="px-3 py-2 text-center font-bold w-32">إجراءات</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-border">
                            {items.map((it) => (
                                <tr key={it.id} className="hover:bg-accent/20" data-testid={`catalogue-row-${it.sku}`}>
                                    <td className="px-3 py-3 font-semibold">{it.product_name}</td>
                                    <td className="px-3 py-3 text-xs tabular-nums" dir="ltr">{it.sku}</td>
                                    <td className="px-3 py-3 text-xs text-muted-foreground">{it.supplier_name || "—"}</td>
                                    <td className="px-3 py-3 text-end font-bold tabular-nums">
                                        {fmtMoney(it.cost_price)} <span className="text-xs text-muted-foreground">{it.currency || "SAR"}</span>
                                    </td>
                                    <td className="px-3 py-3">
                                        <div className="flex items-center justify-center gap-1">
                                            <button
                                                onClick={() => onEdit(it)}
                                                className="p-1.5 hover:bg-blue-50 hover:text-blue-600 rounded-lg transition-colors"
                                                title="تعديل"
                                                data-testid={`catalogue-edit-btn-${it.sku}`}
                                            >
                                                <PencilSimple size={16} />
                                            </button>
                                            <button
                                                onClick={() => onDelete(it)}
                                                className="p-1.5 hover:bg-red-50 hover:text-red-600 rounded-lg transition-colors"
                                                title="حذف"
                                                data-testid={`catalogue-delete-btn-${it.sku}`}
                                            >
                                                <Trash size={16} />
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

function MissingTab({ missing, onQuickAdd, loading }) {
    if (loading) return (
        <div className="text-center py-12 text-muted-foreground" data-testid="missing-loading">
            جاري التحميل…
        </div>
    );
    if (!missing || missing.count === 0) return (
        <div className="rounded-xl border-2 border-dashed border-emerald-200 bg-emerald-50/30 p-8 text-center" data-testid="missing-empty-state">
            <CheckCircle size={48} weight="duotone" className="text-emerald-600 mx-auto mb-3" />
            <h3 className="text-lg font-bold mb-1 text-emerald-900">كل المنتجات لها تكلفة ✓</h3>
            <p className="text-sm text-emerald-800/80">
                لا توجد منتجات بدون تكلفة في آخر 60 يوم.
            </p>
        </div>
    );
    return (
        <div className="space-y-3">
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 flex items-start gap-2" data-testid="missing-warning-banner">
                <Warning size={18} weight="fill" className="text-amber-600 flex-shrink-0 mt-0.5" />
                <div>
                    <strong>{missing.count} منتج بدون تكلفة</strong> في الطلبات الأخيرة (آخر {missing.window_days} يوم).
                    صافي الربح لن يكون دقيقاً حتى تُحدِّد تكلفة هذه المنتجات.
                </div>
            </div>
            <div className="overflow-x-auto rounded-xl border border-border bg-white">
                <table className="w-full text-sm" data-testid="missing-table">
                    <thead className="bg-accent/40 text-xs">
                        <tr>
                            <th className="px-3 py-2 text-start font-bold">اسم المنتج</th>
                            <th className="px-3 py-2 text-start font-bold" dir="ltr">SKU</th>
                            <th className="px-3 py-2 text-end font-bold">عدد الطلبات</th>
                            <th className="px-3 py-2 text-end font-bold">إجمالي الكمية</th>
                            <th className="px-3 py-2 text-center font-bold w-32">إجراء</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                        {missing.items.map((m, i) => (
                            <tr key={i} className="hover:bg-accent/20" data-testid={`missing-row-${m.sku || m.product_id || i}`}>
                                <td className="px-3 py-3 font-semibold">{m.name || "(بدون اسم)"}</td>
                                <td className="px-3 py-3 text-xs tabular-nums" dir="ltr">{m.sku || "—"}</td>
                                <td className="px-3 py-3 text-end tabular-nums font-bold">{m.occurrences}</td>
                                <td className="px-3 py-3 text-end tabular-nums">{Number(m.total_quantity).toFixed(0)}</td>
                                <td className="px-3 py-3 text-center">
                                    <button
                                        onClick={() => onQuickAdd(m)}
                                        className="text-xs px-3 py-1.5 bg-brand text-white rounded-lg font-bold hover:opacity-90"
                                        data-testid={`missing-quickadd-btn-${m.sku || m.product_id || i}`}
                                    >
                                        + إضافة تكلفة
                                    </button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

export default function ProductCosts() {
    const [tab, setTab] = useState("catalogue"); // catalogue | missing
    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [search, setSearch] = useState("");
    const [loadingCat, setLoadingCat] = useState(true);
    const [loadingMissing, setLoadingMissing] = useState(false);
    const [missing, setMissing] = useState(null);
    const [summary, setSummary] = useState(null);
    const [modalOpen, setModalOpen] = useState(false);
    const [modalInitial, setModalInitial] = useState(null);
    const fileRef = useRef(null);
    const [importing, setImporting] = useState(false);
    const [updateExisting, setUpdateExisting] = useState(true);
    const [importModalOpen, setImportModalOpen] = useState(false);
    const [recomputing, setRecomputing] = useState(false);

    const loadCatalogue = async (q = search) => {
        setLoadingCat(true);
        try {
            const params = new URLSearchParams({ is_active: "true" });
            if (q) params.set("search", q);
            const { data } = await api.get(`/product-costs/?${params.toString()}`);
            setItems(data.items || []);
            setTotal(data.total || 0);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setLoadingCat(false); }
    };

    const loadMissing = async () => {
        setLoadingMissing(true);
        try {
            const { data } = await api.get("/product-costs/missing");
            setMissing(data);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setLoadingMissing(false); }
    };

    const loadSummary = async () => {
        try {
            const { data } = await api.get("/product-costs/summary");
            setSummary(data);
        } catch {
            /* non-critical */
        }
    };

    useEffect(() => { loadCatalogue(); loadSummary(); }, []);

    // Debounce search
    useEffect(() => {
        const t = setTimeout(() => loadCatalogue(search), 350);
        return () => clearTimeout(t);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [search]);

    // Lazy-load missing on tab switch
    useEffect(() => {
        if (tab === "missing" && missing === null) loadMissing();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [tab]);

    const openAdd = (preset) => {
        setModalInitial(preset || null);
        setModalOpen(true);
    };

    const onSaved = () => {
        loadCatalogue();
        loadSummary();
        if (tab === "missing") loadMissing();
    };

    const onDelete = async (it) => {
        if (!window.confirm(`حذف تكلفة المنتج "${it.product_name}" (${it.sku})؟`)) return;
        try {
            await api.delete(`/product-costs/${it.id}`);
            toast.success("تم الحذف");
            loadCatalogue();
            loadSummary();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    const onImport = async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;
        e.target.value = "";
        setImporting(true);
        setImportModalOpen(false);
        try {
            const fd = new FormData();
            fd.append("file", file);
            const { data } = await api.post(
                `/product-costs/import?update_existing=${updateExisting ? "true" : "false"}`,
                fd, { headers: { "Content-Type": "multipart/form-data" } },
            );
            const errs = (data.errors || []).length;
            const skipped = data.skipped || 0;
            const metaCols = (data.meta_columns_preserved || []).length;
            const parts = [
                `${data.created} جديد`,
                `${data.updated} محدّث`,
            ];
            if (skipped) parts.push(`${skipped} مُتخطى`);
            if (errs) parts.push(`${errs} خطأ`);
            const metaHint = metaCols > 0
                ? ` (تم حفظ ${metaCols} عمود إضافي في meta للمستقبل)`
                : "";
            toast.success(`تم الاستيراد: ${parts.join(" • ")}${metaHint}`,
                { duration: 8000 });
            await loadCatalogue();
            await loadSummary();
            if (missing) loadMissing();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر استيراد الملف");
        } finally { setImporting(false); }
    };

    const onRecompute = async () => {
        setRecomputing(true);
        toast.loading("جاري إعادة احتساب التكاليف على الطلبات…", { id: "recompute" });
        try {
            const { data } = await api.post("/product-costs/recompute");
            toast.success(`تم تحديث ${data.orders_updated} طلب`, { id: "recompute", duration: 5000 });
            loadMissing();
            loadSummary();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail), { id: "recompute" });
        } finally { setRecomputing(false); }
    };

    return (
        <div className="space-y-6" data-testid="product-costs-page">
            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
                <div>
                    <h1 className="text-3xl sm:text-4xl lg:text-5xl font-extrabold tracking-tight flex items-center gap-3 flex-wrap" style={{ fontFamily: "Tajawal" }}>
                        <Package size={36} weight="fill" className="text-brand" />
                        تكاليف المنتجات
                    </h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        أضف تكلفة شراء كل منتج (SKU) ليحتسب النظام صافي الربح الحقيقي.
                    </p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <input
                        ref={fileRef}
                        type="file"
                        accept=".xlsx,.xls"
                        className="hidden"
                        onChange={onImport}
                        data-testid="product-costs-import-input"
                    />
                    <button
                        type="button"
                        onClick={() => setImportModalOpen(true)}
                        disabled={importing}
                        className="px-3 py-2 border border-border rounded-lg font-bold text-sm inline-flex items-center gap-2 hover:bg-accent disabled:opacity-50"
                        data-testid="product-costs-import-btn"
                    >
                        <UploadSimple size={16} weight="bold" />
                        {importing ? "جاري الاستيراد…" : "استيراد Excel"}
                    </button>
                    <button
                        type="button"
                        onClick={onRecompute}
                        disabled={recomputing}
                        className="px-3 py-2 border border-border rounded-lg font-bold text-sm inline-flex items-center gap-2 hover:bg-accent disabled:opacity-50"
                        title="إعادة احتساب تكلفة المنتجات على كل الطلبات السابقة"
                        data-testid="product-costs-recompute-btn"
                    >
                        <ArrowsClockwise size={16} weight="bold" className={recomputing ? "animate-spin" : ""} />
                        {recomputing ? "جاري التحديث…" : "إعادة الاحتساب"}
                    </button>
                    <button
                        type="button"
                        onClick={() => openAdd(null)}
                        className="px-3 py-2 bg-brand text-white rounded-lg font-bold text-sm inline-flex items-center gap-2 hover:opacity-90"
                        data-testid="product-costs-add-btn"
                    >
                        <Plus size={16} weight="bold" />
                        إضافة منتج
                    </button>
                </div>
            </div>

            <SummaryCards summary={summary} />

            {/* Tabs */}
            <div className="flex items-center gap-2 border-b border-border" data-testid="product-costs-tabs">
                <button
                    onClick={() => setTab("catalogue")}
                    className={`px-4 py-2 font-bold text-sm border-b-2 -mb-px transition-colors ${tab === "catalogue" ? "border-brand text-brand" : "border-transparent text-muted-foreground hover:text-foreground"}`}
                    data-testid="tab-catalogue"
                >
                    كل المنتجات
                </button>
                <button
                    onClick={() => setTab("missing")}
                    className={`px-4 py-2 font-bold text-sm border-b-2 -mb-px transition-colors inline-flex items-center gap-2 ${tab === "missing" ? "border-brand text-brand" : "border-transparent text-muted-foreground hover:text-foreground"}`}
                    data-testid="tab-missing"
                >
                    بدون تكلفة
                    {missing && missing.count > 0 && (
                        <span className="text-[10px] px-1.5 py-0.5 bg-amber-100 text-amber-800 rounded-full font-bold">
                            {missing.count}
                        </span>
                    )}
                </button>
            </div>

            {tab === "catalogue" ? (
                <CatalogueTab
                    items={items} total={total}
                    search={search} setSearch={setSearch}
                    onEdit={(it) => openAdd(it)}
                    onDelete={onDelete}
                    loading={loadingCat}
                />
            ) : (
                <MissingTab
                    missing={missing}
                    loading={loadingMissing}
                    onQuickAdd={(m) => openAdd({
                        sku: m.sku || "",
                        product_id: m.product_id || "",
                        product_name: m.name || "",
                        cost_price: "",
                        currency: "SAR",
                    })}
                />
            )}

            <AddEditModal
                open={modalOpen}
                initial={modalInitial}
                onClose={() => setModalOpen(false)}
                onSaved={onSaved}
            />

            {/* Import options modal (iteration 20) — collects the
                update_existing flag BEFORE opening the OS file picker,
                so the merchant can choose whether duplicate SKUs are
                updated or skipped. */}
            {importModalOpen && (
                <div
                    className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4"
                    data-testid="product-costs-import-modal"
                    onClick={(e) => e.target === e.currentTarget && setImportModalOpen(false)}
                >
                    <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg">
                        <div className="px-6 py-4 border-b border-border flex items-center justify-between gap-3">
                            <h2 className="text-xl font-extrabold flex items-center gap-2" style={{ fontFamily: "Tajawal" }}>
                                <UploadSimple size={22} weight="fill" className="text-brand" />
                                استيراد ملف Excel من سلة
                            </h2>
                            <button
                                onClick={() => setImportModalOpen(false)}
                                className="p-1 hover:bg-accent rounded-lg"
                                data-testid="product-costs-import-modal-close"
                            >
                                <X size={20} />
                            </button>
                        </div>
                        <div className="p-6 space-y-4 text-sm">
                            <div className="rounded-lg bg-blue-50 border border-blue-200 p-3 text-blue-900 leading-relaxed">
                                <strong>الأعمدة المُستوردة فقط:</strong>
                                <ul className="list-disc list-inside mt-1 space-y-0.5">
                                    <li><strong>SKU</strong> (كود المنتج / Reference / Product Code…)</li>
                                    <li><strong>اسم المنتج</strong></li>
                                    <li><strong>التكلفة</strong> (Cost / سعر التكلفة / الكلفة…)</li>
                                </ul>
                                <div className="mt-2 text-xs text-blue-800/80">
                                    💡 كل الأعمدة الأخرى تُحفظ تلقائياً في حقل <code>meta</code> للاستخدام المستقبلي ولا تؤثر على احتساب الربح.
                                </div>
                            </div>

                            <div className="rounded-lg bg-amber-50 border border-amber-200 p-3 text-amber-900 leading-relaxed">
                                <strong>المورد لا يُستورد من Excel.</strong>
                                <div className="text-xs text-amber-800/80 mt-1">
                                    أضف بيانات المورد (اسم، بلد، ملاحظات) يدوياً من زر "تعديل" لكل منتج.
                                </div>
                            </div>

                            <label className="flex items-start gap-3 cursor-pointer p-3 rounded-lg border border-border hover:bg-accent/30">
                                <input
                                    type="checkbox"
                                    checked={updateExisting}
                                    onChange={(e) => setUpdateExisting(e.target.checked)}
                                    className="w-5 h-5 rounded border-border accent-brand mt-0.5 flex-shrink-0"
                                    data-testid="product-costs-update-existing-checkbox"
                                />
                                <div>
                                    <div className="font-bold">
                                        تحديث المنتجات الموجودة بنفس SKU
                                    </div>
                                    <div className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                                        {updateExisting
                                            ? "✓ إذا وُجد منتج بنفس SKU، سيتم تحديث الاسم والتكلفة."
                                            : "✗ إذا وُجد منتج بنفس SKU، سيتم تجاهله (يظهر في عدد \"المُتخطى\")."}
                                    </div>
                                </div>
                            </label>
                        </div>
                        <div className="px-6 py-4 border-t border-border flex items-center justify-end gap-2">
                            <button
                                onClick={() => setImportModalOpen(false)}
                                className="px-4 py-2 text-sm border border-border rounded-lg hover:bg-accent"
                            >
                                إلغاء
                            </button>
                            <button
                                onClick={() => fileRef.current?.click()}
                                className="px-4 py-2 bg-brand text-white rounded-lg font-bold text-sm hover:opacity-90 inline-flex items-center gap-2"
                                data-testid="product-costs-import-pick-file-btn"
                            >
                                <UploadSimple size={16} weight="bold" />
                                اختيار الملف…
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
