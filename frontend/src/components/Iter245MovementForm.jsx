// Iter-246 — Unified Iter-245 financial-movement form, embeddable.
//
// Used by the legacy UnifiedEntryScreen so that the merchant has ONE
// single entry point.  Selecting "فاتورة مورد", "مصروف عام" or the new
// "أصل ثابت" op simply renders this component with the matching
// `movementType`.  No duplicate route, no duplicate UI.
//
// Behaviour highlights (per user request):
//   • Categories are filtered by movement_type at fetch time, so each
//     op only sees its own categories.
//   • When a supplier is picked, only their linked categories are
//     offered in a flat list (leaf name only, full path saved
//     behind-the-scenes).
//   • Supplier invoices on PURCHASE roots require a per-line items
//     table (multi-row), with live total reconciliation.
//   • Date input is normalised to YYYY-MM-DD with `dir="ltr"` so the
//     OS picker renders cleanly under RTL layouts.

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const TERMS = [
    { value: "cash", label: "نقدي" },
    { value: "credit", label: "آجل" },
    { value: "partial", label: "سداد جزئي" },
];

const WITHDRAWALS = [
    { value: "cash", label: "سحب نقدي" },
    { value: "transfer", label: "تحويل بنكي" },
    { value: "pos", label: "دفع شبكة (POS)" },
];

// Iter-246c — Supplier invoices ALWAYS require a line-items table.
// We no longer gate by category root; the merchant requested the table
// to appear the moment the user picks "فاتورة مورد".
function _isSupplierInvoice(movementType) {
    return movementType === "supplier_invoice";
}

const fmt = (n) =>
    Number(n || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

const errMsg = (e, fb) =>
    e?.response?.data?.detail || e?.message || fb;

function todayISO() {
    return new Date().toISOString().slice(0, 10);
}

// Build a sorted (depth → name) flat list ready for the picker.
// Returns [{ node, depth }]
function flatten(rows) {
    const byParent = {};
    rows.forEach((n) => {
        const k = n.parent_id || "_root_";
        if (!byParent[k]) byParent[k] = [];
        byParent[k].push(n);
    });
    const out = [];
    function walk(parent, depth) {
        const kids = (byParent[parent || "_root_"] || []).slice()
            .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
        for (const node of kids) {
            out.push({ node, depth });
            walk(node.id, depth + 1);
        }
    }
    walk(null, 0);
    return out;
}

export default function Iter245MovementForm({
    movementType,                     // "supplier_invoice" | "general_expense" | "fixed_asset"
    title = "حركة مالية موحَّدة",
    onSaved,                          // optional callback(newMovement)
}) {
    const needsSupplier = movementType === "supplier_invoice";

    const [docDate, setDocDate] = useState(todayISO());
    const [docNumber, setDocNumber] = useState("");
    const [notes, setNotes] = useState("");

    const [suppliers, setSuppliers] = useState([]);
    const [supplierId, setSupplierId] = useState("");
    const [showAllCats, setShowAllCats] = useState(false);

    const [categories, setCategories] = useState([]);   // full tree filtered by movement_type
    const [categoryId, setCategoryId] = useState("");

    const [paymentTerms, setPaymentTerms] = useState("cash");
    const [totalAmount, setTotalAmount] = useState("");
    const [paidAmount, setPaidAmount] = useState("");

    const [accounts, setAccounts] = useState([]);
    const [accountId, setAccountId] = useState("");
    const [withdrawal, setWithdrawal] = useState("");
    const [reference, setReference] = useState("");
    const [attachment, setAttachment] = useState(null);

    // Iter-246c — Merchant's op→account / op→withdrawal allow-lists.
    const [allowedAccountIds, setAllowedAccountIds] = useState(null);
    const [allowedMethods, setAllowedMethods] = useState(null);

    const [lineItems, setLineItems] = useState([
        { description: "", quantity: "", unit_price: "" },
    ]);
    const [saving, setSaving] = useState(false);

    // ── Reset on movementType change ─────────────────────────────
    useEffect(() => {
        setSupplierId(""); setCategoryId(""); setShowAllCats(false);
        setTotalAmount(""); setPaidAmount(""); setAccountId("");
        setWithdrawal(""); setReference(""); setAttachment(null);
        setLineItems([{ description: "", quantity: "", unit_price: "" }]);
        setDocNumber(""); setNotes("");
    }, [movementType]);

    // ── Fetch categories filtered by op type + suppliers (when used) ─
    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                const requests = [
                    api.get("/expense-category-tree", {
                        params: {
                            include_inactive: false,
                            movement_type: movementType,
                        },
                    }),
                    api.get("/settings"),
                ];
                if (needsSupplier) {
                    requests.push(api.get("/suppliers", {
                        params: { status: "active" },
                    }));
                }
                const [catRes, setRes, supRes] = await Promise.all(requests);
                if (!alive) return;
                setCategories(catRes.data.items || []);
                // Iter-246c — Apply allow-lists.
                const bindings =
                    setRes?.data?.operation_account_bindings || {};
                const wms =
                    setRes?.data?.operation_withdrawal_methods || {};
                const accBind = bindings[movementType];
                setAllowedAccountIds(
                    Array.isArray(accBind) && accBind.length
                        ? new Set(accBind.filter((x) => x !== "__none__"))
                        : null);
                const wmBind = wms[movementType];
                setAllowedMethods(
                    Array.isArray(wmBind) && wmBind.length
                        ? new Set(wmBind) : null);
                if (needsSupplier) {
                    setSuppliers(supRes?.data?.items || []);
                }
            } catch (e) {
                toast.error(errMsg(e, "فشل تحميل البيانات"));
            }
        })();
        return () => { alive = false; };
    }, [movementType, needsSupplier]);

    // ── Compute numeric paid amount based on terms ───────────────
    const numericPaid = useMemo(() => {
        const t = Number(totalAmount || 0);
        if (paymentTerms === "credit") return 0;
        if (paymentTerms === "cash") return t;
        return Number(paidAmount || 0);
    }, [paymentTerms, totalAmount, paidAmount]);

    // Iter-246d — Amount used to query the «accounts-with-availability»
    // endpoint.  We want accounts to appear the moment the merchant
    // picks «جزئي» — even before they've typed the partial figure —
    // so we fall back to the full total as a hint amount.  The actual
    // sufficiency check still uses `numericPaid` at save time.
    const accountsQueryAmount = useMemo(() => {
        if (paymentTerms === "credit") return 0;
        const t = Number(totalAmount || 0);
        if (paymentTerms === "partial") {
            const p = Number(paidAmount || 0);
            return p > 0 ? p : t;       // hint with total until user types
        }
        return t;                        // cash → full total
    }, [paymentTerms, totalAmount, paidAmount]);

    // ── Fetch accounts only when cash actually leaves an account ─
    useEffect(() => {
        if (paymentTerms === "credit") { setAccounts([]); return; }
        if (accountsQueryAmount <= 0) { setAccounts([]); return; }
        let alive = true;
        (async () => {
            try {
                const { data } = await api.get(
                    "/financial-movements/accounts-with-availability",
                    { params: { amount: accountsQueryAmount } });
                if (!alive) return;
                let items = data.items || [];
                // Iter-246c — Honour the merchant's per-op account
                // allow-list at the UI layer too (backend enforces).
                if (allowedAccountIds) {
                    items = items.filter((a) =>
                        allowedAccountIds.has(a.id));
                }
                setAccounts(items);
            } catch (e) {
                toast.error(errMsg(e, "فشل تحميل الحسابات"));
            }
        })();
        return () => { alive = false; };
    }, [accountsQueryAmount, paymentTerms, allowedAccountIds]);

    const selectedSupplier = suppliers.find((s) => s.id === supplierId);
    const supplierCatIds = useMemo(
        () => new Set(selectedSupplier?.category_ids || []),
        [selectedSupplier]);

    // ── Category picker mode ─────────────────────────────────────
    // • Supplier picked + not "show all" → FLAT list of supplier's
    //   linked leaves (display: leaf name only — full path saved on
    //   the server because the doc stores the canonical category id).
    // • Otherwise → indented tree picker scoped to movement_type.
    const supplierLinkedCats = useMemo(() => {
        if (!needsSupplier || !selectedSupplier || showAllCats) return null;
        if (supplierCatIds.size === 0) return [];
        return categories.filter((c) => supplierCatIds.has(c.id));
    }, [needsSupplier, selectedSupplier, showAllCats,
        supplierCatIds, categories]);

    const treeFlat = useMemo(() => flatten(categories), [categories]);

    const selectedCat = categories.find((c) => c.id === categoryId);

    // Iter-246c — Supplier invoices ALWAYS show the line-items table,
    // independent of which category root was picked.  The merchant
    // explicitly requested this to avoid header-only totals on
    // purchases.
    const isPurchaseInvoice = _isSupplierInvoice(movementType);

    const selectedAcc = accounts.find((a) => a.id === accountId);
    const isBank = (selectedAcc?.account_type || "").toLowerCase() === "bank";

    // ── Line items helpers ───────────────────────────────────────
    function updateLine(idx, key, val) {
        setLineItems((rows) =>
            rows.map((r, i) => (i === idx ? { ...r, [key]: val } : r)));
    }
    function addLine() {
        setLineItems((rows) =>
            [...rows, { description: "", quantity: "", unit_price: "" }]);
    }
    function removeLine(idx) {
        setLineItems((rows) =>
            rows.length <= 1 ? rows : rows.filter((_, i) => i !== idx));
    }
    const lineSum = useMemo(
        () => lineItems.reduce(
            (s, r) => s + Number(r.quantity || 0) * Number(r.unit_price || 0),
            0), [lineItems]);

    // Auto-fill totalAmount from line items when on a purchase invoice
    // so the merchant doesn't have to manually sync the two values.
    useEffect(() => {
        if (!isPurchaseInvoice) return;
        const v = lineSum.toFixed(2);
        if (v !== Number(totalAmount || 0).toFixed(2)) {
            setTotalAmount(lineSum > 0 ? v : "");
        }
    }, [lineSum, isPurchaseInvoice, totalAmount]);

    async function onFileChange(e) {
        const f = e.target.files?.[0];
        if (!f) return setAttachment(null);
        if (f.size > 5 * 1024 * 1024) {
            toast.error("الحد الأقصى 5MB");
            e.target.value = "";
            return;
        }
        const reader = new FileReader();
        reader.onload = () => {
            const b64 = (reader.result || "").toString().split(",")[1];
            setAttachment({
                filename: f.name,
                content_type: f.type,
                base64: b64,
            });
        };
        reader.readAsDataURL(f);
    }

    async function save() {
        if (!categoryId) return toast.error("اختر التصنيف");
        if (needsSupplier && !supplierId)
            return toast.error("اختر المورد");

        // Iter-246c — Supplier-invoice strict validation BEFORE
        // computing the total so we surface row-level errors clearly.
        let total = Number(totalAmount || 0);
        if (movementType === "supplier_invoice") {
            const filled = lineItems.filter(
                (r) => (r.description || "").trim()
                    || Number(r.quantity) > 0
                    || Number(r.unit_price) > 0);
            if (filled.length === 0) {
                return toast.error(
                    "أضف صفاً واحداً على الأقل في جدول الأصناف");
            }
            for (let i = 0; i < filled.length; i++) {
                const r = filled[i];
                if (!(r.description || "").trim()) {
                    return toast.error(
                        `الصف #${i + 1}: اسم الصنف مطلوب`);
                }
                if (!(Number(r.quantity) > 0)) {
                    return toast.error(
                        `الصف #${i + 1}: الكمية يجب أن تكون > 0`);
                }
                if (!(Number(r.unit_price) > 0)) {
                    return toast.error(
                        `الصف #${i + 1}: سعر الوحدة يجب أن يكون > 0`);
                }
            }
            // Recompute server-side total from filled rows.
            total = filled.reduce(
                (s, r) =>
                    s + Number(r.quantity || 0) * Number(r.unit_price || 0),
                0);
        }
        if (total <= 0) return toast.error("الإجمالي مطلوب");

        const payload = {
            movement_type: movementType,
            doc_date: docDate,
            doc_number: docNumber || null,
            notes: notes || null,
            supplier_id: supplierId || null,
            category_id: categoryId,
            payment_terms: paymentTerms,
            total_amount: total,
            paid_amount: paymentTerms === "partial"
                ? Number(paidAmount || 0) : 0,
            paid_from_account_id: paymentTerms === "credit"
                ? null : accountId || null,
            withdrawal_method: isBank ? withdrawal || null : null,
            reference_number: reference || null,
            attachment: (withdrawal === "transfer" && attachment) || null,
            line_items: movementType === "supplier_invoice"
                ? lineItems
                    .filter((r) => (r.description || "").trim()
                        && Number(r.quantity) > 0
                        && Number(r.unit_price) > 0)
                    .map((r) => ({
                        description: r.description.trim(),
                        quantity: Number(r.quantity),
                        unit_price: Number(r.unit_price),
                    }))
                : [],
        };

        setSaving(true);
        try {
            const res = await api.post("/financial-movements", payload);
            toast.success("تم اعتماد العملية وإنشاء القيد المحاسبي ✅");
            setDocNumber(""); setNotes(""); setTotalAmount("");
            setPaidAmount(""); setAttachment(null);
            setLineItems([
                { description: "", quantity: "", unit_price: "" },
            ]);
            onSaved?.(res.data);
        } catch (e) {
            toast.error(errMsg(e, "فشل الحفظ"));
        } finally {
            setSaving(false);
        }
    }

    return (
        <div className="space-y-4" dir="rtl"
             data-testid="iter245-movement-form">
            <header className="border-b pb-2">
                <h2 className="text-lg font-bold text-emerald-700">
                    {title}
                </h2>
                <p className="text-xs text-slate-500 mt-0.5">
                    النظام الموحَّد (Iter-245). كل عملية تُنشئ قيد
                    Double-Entry تلقائياً وتُسجَّل في «قائمة الحركات
                    المالية».
                </p>
            </header>

            {/* Doc date + doc number */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <Field label="تاريخ المستند">
                    <input
                        type="date"
                        dir="ltr"
                        value={docDate}
                        onChange={(e) => setDocDate(e.target.value)}
                        className="w-full border rounded px-3 py-2 text-sm font-mono text-right"
                        style={{ direction: "ltr" }}
                        data-testid="iter245-mv-date" />
                    <p className="text-[11px] text-slate-500 mt-1">
                        الصيغة: YYYY-MM-DD ({docDate})
                    </p>
                </Field>
                <Field label="رقم المستند (اختياري)">
                    <input
                        value={docNumber}
                        onChange={(e) => setDocNumber(e.target.value)}
                        className="w-full border rounded px-3 py-2 text-sm"
                        placeholder="رقم الفاتورة / الإيصال"
                        data-testid="iter245-mv-docnum" />
                </Field>
            </div>

            {/* Supplier (only for supplier_invoice) */}
            {needsSupplier && (
                <Field label="المورد">
                    <select
                        value={supplierId}
                        onChange={(e) => {
                            setSupplierId(e.target.value);
                            setCategoryId("");
                        }}
                        className="w-full border rounded px-3 py-2 text-sm"
                        data-testid="iter245-mv-supplier">
                        <option value="">— اختر المورد —</option>
                        {suppliers.map((s) => (
                            <option key={s.id} value={s.id}>
                                {s.company_name}
                                {s.contact_person
                                    ? ` (${s.contact_person})` : ""}
                            </option>
                        ))}
                    </select>
                    {selectedSupplier && (
                        <label className="text-xs flex items-center gap-1 mt-2 text-emerald-700">
                            <input
                                type="checkbox"
                                checked={showAllCats}
                                onChange={(e) =>
                                    setShowAllCats(e.target.checked)}
                                data-testid="iter245-mv-showall" />
                            إظهار جميع تصنيفات المشتريات (تجاوز تخصصات المورد)
                        </label>
                    )}
                </Field>
            )}

            {/* Category picker */}
            <Field label="التصنيف">
                {supplierLinkedCats ? (
                    // FLAT supplier-linked picker — leaf name only.
                    supplierLinkedCats.length === 0 ? (
                        <div className="text-xs text-amber-700 border border-amber-300 rounded bg-amber-50 px-3 py-2">
                            هذا المورد بدون تصنيفات. فعّل «إظهار جميع التصنيفات»
                            أو أضف تصنيفات إلى المورد من شاشة الموردين.
                        </div>
                    ) : (
                        <select
                            value={categoryId}
                            onChange={(e) => setCategoryId(e.target.value)}
                            className="w-full border rounded px-3 py-2 text-sm"
                            data-testid="iter245-mv-cat-leaf">
                            <option value="">— اختر التصنيف —</option>
                            {supplierLinkedCats.map((c) => (
                                <option key={c.id} value={c.id}>
                                    {c.name}
                                </option>
                            ))}
                        </select>
                    )
                ) : (
                    // Indented tree picker scoped to movement_type.
                    <select
                        value={categoryId}
                        onChange={(e) => setCategoryId(e.target.value)}
                        className="w-full border rounded px-3 py-2 text-sm"
                        data-testid="iter245-mv-cat-tree">
                        <option value="">— اختر التصنيف —</option>
                        {treeFlat.map(({ node, depth }) => (
                            <option key={node.id} value={node.id}>
                                {"".padStart(depth * 2, "·")} {node.name}
                            </option>
                        ))}
                    </select>
                )}
                {selectedCat && (
                    <p className="text-xs text-slate-500 mt-1"
                       data-testid="iter245-mv-cat-path">
                        المسار الكامل (يُحفظ تلقائياً):{" "}
                        <span className="font-bold">
                            {(selectedCat.path || []).join(" › ")}
                        </span>
                    </p>
                )}
            </Field>

            {/* Line items table — purchase invoices only */}
            {isPurchaseInvoice && (
                <div className="border rounded-lg p-3 bg-amber-50">
                    <h3 className="text-sm font-bold mb-2 text-amber-900">
                        🛒 تفاصيل أصناف الفاتورة
                    </h3>
                    <table className="min-w-full text-xs">
                        <thead className="bg-amber-100">
                            <tr className="text-right">
                                <th className="p-2 text-right">الصنف / المنتج</th>
                                <th className="p-2 text-right">الكمية</th>
                                <th className="p-2 text-right">سعر الوحدة</th>
                                <th className="p-2 text-right">الإجمالي</th>
                                <th className="p-2 w-12"></th>
                            </tr>
                        </thead>
                        <tbody data-testid="iter245-mv-lines-body">
                            {lineItems.map((r, i) => {
                                const tot = Number(r.quantity || 0)
                                    * Number(r.unit_price || 0);
                                return (
                                    <tr key={i} className="border-t border-amber-200">
                                        <td className="p-1">
                                            <input
                                                value={r.description}
                                                onChange={(e) =>
                                                    updateLine(i, "description",
                                                        e.target.value)}
                                                placeholder="مثال: عباية كلوش"
                                                className="w-full border rounded px-2 py-1 text-xs"
                                                data-testid={`iter245-mv-line-desc-${i}`} />
                                        </td>
                                        <td className="p-1">
                                            <input
                                                type="number"
                                                step="0.01"
                                                value={r.quantity}
                                                onChange={(e) =>
                                                    updateLine(i, "quantity",
                                                        e.target.value)}
                                                className="w-24 border rounded px-2 py-1 text-xs"
                                                data-testid={`iter245-mv-line-qty-${i}`} />
                                        </td>
                                        <td className="p-1">
                                            <input
                                                type="number"
                                                step="0.01"
                                                value={r.unit_price}
                                                onChange={(e) =>
                                                    updateLine(i, "unit_price",
                                                        e.target.value)}
                                                className="w-28 border rounded px-2 py-1 text-xs"
                                                data-testid={`iter245-mv-line-price-${i}`} />
                                        </td>
                                        <td className="p-1 font-mono font-bold">
                                            {fmt(tot)}
                                        </td>
                                        <td className="p-1 text-center">
                                            <button
                                                type="button"
                                                onClick={() => removeLine(i)}
                                                disabled={lineItems.length <= 1}
                                                className="text-red-600 text-xs disabled:opacity-30"
                                                data-testid={`iter245-mv-line-remove-${i}`}>
                                                حذف
                                            </button>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                    <div className="flex items-center justify-between mt-2 gap-2">
                        <button
                            type="button"
                            onClick={addLine}
                            className="bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-1.5 text-xs rounded font-bold"
                            data-testid="iter245-mv-add-line">
                            + إضافة صنف
                        </button>
                        <span className="text-sm font-bold text-amber-900"
                              data-testid="iter245-mv-lines-sum">
                            مجموع الأصناف: {fmt(lineSum)} ر.س
                        </span>
                    </div>
                    <p className="text-[11px] text-amber-700 mt-2">
                        💡 يتم احتساب إجمالي الفاتورة تلقائياً من مجموع الأصناف.
                    </p>
                </div>
            )}

            {/* Amount + terms */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <Field label="الإجمالي (ر.س) *">
                    <input
                        type="number"
                        step="0.01"
                        value={totalAmount}
                        onChange={(e) => setTotalAmount(e.target.value)}
                        readOnly={isPurchaseInvoice}
                        className={`w-full border rounded px-3 py-2 text-sm font-mono ${
                            isPurchaseInvoice ? "bg-slate-100" : ""
                        }`}
                        data-testid="iter245-mv-total" />
                    {isPurchaseInvoice && (
                        <p className="text-[10px] text-slate-500 mt-1">
                            يُحسب تلقائياً من جدول الأصناف.
                        </p>
                    )}
                </Field>
                <Field label="طريقة السداد">
                    <select
                        value={paymentTerms}
                        onChange={(e) => setPaymentTerms(e.target.value)}
                        className="w-full border rounded px-3 py-2 text-sm"
                        data-testid="iter245-mv-terms">
                        {TERMS.map((t) => (
                            <option key={t.value} value={t.value}>
                                {t.label}
                            </option>
                        ))}
                    </select>
                </Field>
                {paymentTerms === "partial" && (
                    <Field label="المبلغ المدفوع">
                        <input
                            type="number"
                            step="0.01"
                            value={paidAmount}
                            onChange={(e) => setPaidAmount(e.target.value)}
                            className="w-full border rounded px-3 py-2 text-sm font-mono"
                            data-testid="iter245-mv-paid" />
                    </Field>
                )}
            </div>

            {/* Account picker — only when cash leaves an account */}
            {paymentTerms !== "credit" && (
                <Field label="الحساب الدافع">
                    <select
                        value={accountId}
                        onChange={(e) => setAccountId(e.target.value)}
                        className="w-full border rounded px-3 py-2 text-sm"
                        data-testid="iter245-mv-account">
                        <option value="">— اختر الحساب —</option>
                        {accounts.map((a) => (
                            <option
                                key={a.id} value={a.id}
                                disabled={!a.is_sufficient}
                                style={!a.is_sufficient ? { color: "#aaa" } : null}>
                                {a.name} — {fmt(a.available_balance)} ر.س
                                {!a.is_sufficient ? " (غير كافٍ)" : ""}
                            </option>
                        ))}
                    </select>
                    {accounts.length === 0 && accountsQueryAmount > 0 && (
                        <p className="text-[11px] text-slate-500 mt-1">
                            لا توجد حسابات متاحة لهذا النوع من العمليات.
                            تأكد من «إعدادات ربط العمليات بالحسابات».
                        </p>
                    )}
                    {accountsQueryAmount === 0 && (
                        <p className="text-[11px] text-amber-700 mt-1">
                            ⏳ أضف صنفاً أولاً (الإجمالي = 0) لتظهر الحسابات.
                        </p>
                    )}
                </Field>
            )}

            {/* Bank withdrawal method */}
            {paymentTerms !== "credit" && isBank && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 border-t pt-3">
                    <Field label="طريقة السحب">
                        <select
                            value={withdrawal}
                            onChange={(e) => setWithdrawal(e.target.value)}
                            className="w-full border rounded px-3 py-2 text-sm"
                            data-testid="iter245-mv-withdrawal">
                            <option value="">— اختر —</option>
                            {WITHDRAWALS
                              .filter((w) =>
                                  !allowedMethods || allowedMethods.has(w.value))
                              .map((w) => (
                                <option key={w.value} value={w.value}>
                                    {w.label}
                                </option>
                            ))}
                        </select>
                        {allowedMethods && (
                            <p className="text-[10px] text-violet-700 mt-1">
                                طرق الدفع المسموحة (من الإعدادات):{" "}
                                {Array.from(allowedMethods).join("، ")}
                            </p>
                        )}
                    </Field>
                    {(withdrawal === "transfer" || withdrawal === "pos") && (
                        <Field label="رقم المرجع (اختياري)">
                            <input
                                value={reference}
                                onChange={(e) => setReference(e.target.value)}
                                className="w-full border rounded px-3 py-2 text-sm"
                                data-testid="iter245-mv-reference" />
                        </Field>
                    )}
                    {withdrawal === "transfer" && (
                        <Field label="صورة الإيصال (اختياري ≤ 5MB)">
                            <input
                                type="file"
                                accept="image/*,application/pdf"
                                onChange={onFileChange}
                                className="w-full text-sm"
                                data-testid="iter245-mv-attachment" />
                            {attachment && (
                                <p className="text-xs text-emerald-700 mt-1">
                                    ✓ {attachment.filename}
                                </p>
                            )}
                        </Field>
                    )}
                </div>
            )}

            <Field label="ملاحظات">
                <textarea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    rows={2}
                    className="w-full border rounded px-3 py-2 text-sm"
                    data-testid="iter245-mv-notes" />
            </Field>

            <div className="flex justify-end gap-2 pt-2 border-t">
                <button
                    type="button"
                    onClick={save}
                    disabled={saving}
                    className="bg-emerald-600 hover:bg-emerald-700 text-white px-6 py-2.5 rounded-lg font-bold disabled:opacity-50"
                    data-testid="iter245-mv-save">
                    {saving ? "جارٍ الحفظ..." : "💾 اعتماد العملية"}
                </button>
            </div>
        </div>
    );
}

function Field({ label, children }) {
    return (
        <div>
            <label className="block text-xs font-semibold mb-1 text-slate-700">
                {label}
            </label>
            {children}
        </div>
    );
}
