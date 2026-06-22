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
// Iter-250b · P2 (Phase 3) — Product autocomplete + quick-create.
import ProductLineAutocomplete from "./ProductLineAutocomplete";

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

    // Iter-250b · P1.5.q — Custody pay-source (general_expense only).
    //   paySource: "bank" (default) | "custody"
    //   custodyEmployeeId: which employee custody to debit
    //   custodySources:    list from /accounting/custody/spendable-sources
    //                      respecting per-user permissions.
    const isGeneralExpense = movementType === "general_expense";
    const [paySource, setPaySource] = useState("bank");
    const [custodyEmployeeId, setCustodyEmployeeId] = useState("");
    const [custodySources, setCustodySources] = useState({
        loaded: false, can_spend_any: false, sources: [],
    });

    const [lineItems, setLineItems] = useState([
        { description: "", quantity: "", unit_price: "",
          discount_amount: "", tax_amount: "", notes: "" },
    ]);
    const [saving, setSaving] = useState(false);

    // ── Reset on movementType change ─────────────────────────────
    useEffect(() => {
        setSupplierId(""); setCategoryId(""); setShowAllCats(false);
        setTotalAmount(""); setPaidAmount(""); setAccountId("");
        setWithdrawal(""); setReference(""); setAttachment(null);
        setLineItems([{ description: "", quantity: "", unit_price: "",
                        discount_amount: "", tax_amount: "", notes: "" }]);
        setDocNumber(""); setNotes("");
        // Iter-250b · P1.5.q — reset pay-source picker as well.
        setPaySource("bank"); setCustodyEmployeeId("");
    }, [movementType]);

    // Iter-250b · P1.5.q — Lazy-load spendable custody sources the
    // first time the merchant flips the toggle to «عهدة موظف».
    useEffect(() => {
        if (!isGeneralExpense || paySource !== "custody") return;
        let alive = true;
        (async () => {
            try {
                const { data } = await api.get(
                    "/accounting/custody/spendable-sources");
                if (!alive) return;
                setCustodySources({
                    loaded: true,
                    can_spend_any: !!data.can_spend_any,
                    sources: Array.isArray(data.sources)
                        ? data.sources : [],
                });
                if (!data.can_spend_any
                    && Array.isArray(data.sources)
                    && data.sources.length === 1) {
                    setCustodyEmployeeId(data.sources[0].employee_id);
                }
            } catch (e) {
                if (!alive) return;
                setCustodySources({
                    loaded: true, can_spend_any: false, sources: [],
                });
            }
        })();
        return () => { alive = false; };
    }, [isGeneralExpense, paySource]);

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
            [...rows, { description: "", quantity: "", unit_price: "",
                        discount_amount: "", tax_amount: "", notes: "" }]);
    }
    function removeLine(idx) {
        setLineItems((rows) =>
            rows.length <= 1 ? rows : rows.filter((_, i) => i !== idx));
    }
    // Iter-250b · Phase 3.7 — line_total = (qty × unit_price)
    //                          − discount_amount + tax_amount
    function _lineTotal(r) {
        const preTax = Number(r.quantity || 0) * Number(r.unit_price || 0);
        return preTax
            - Number(r.discount_amount || 0)
            + Number(r.tax_amount || 0);
    }
    const lineSum = useMemo(
        () => lineItems.reduce((s, r) => s + _lineTotal(r), 0),
        [lineItems]);

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
                // Iter-250b · Phase 3.7 — discount/tax non-negativity
                // + discount must not exceed pre-tax subtotal.
                const disc = Number(r.discount_amount || 0);
                const tax  = Number(r.tax_amount || 0);
                if (disc < 0) {
                    return toast.error(
                        `الصف #${i + 1}: الخصم لا يقبل قيمة سالبة`);
                }
                if (tax < 0) {
                    return toast.error(
                        `الصف #${i + 1}: الضريبة لا تقبل قيمة سالبة`);
                }
                const preTax = Number(r.quantity) * Number(r.unit_price);
                if (disc > preTax + 0.001) {
                    return toast.error(
                        `الصف #${i + 1}: الخصم (${fmt(disc)}) يتجاوز ` +
                        `إجمالي السطر قبل الضريبة (${fmt(preTax)})`);
                }
            }
            // Recompute server-side total from filled rows.
            total = filled.reduce(
                (s, r) => s + _lineTotal(r), 0);
        }
        if (total <= 0) return toast.error("الإجمالي مطلوب");

        // Iter-250b · P1.5.q — When paying from custody, force
        // payment_terms to "cash" (custody can't be آجل/partial) and
        // route the credit leg through `custody_employee_id` instead
        // of `paid_from_account_id`. Frontend pre-validation mirrors
        // the backend guard for instant feedback.
        const isCustody = isGeneralExpense && paySource === "custody";
        if (isCustody) {
            if (!custodyEmployeeId) {
                setSaving(false);
                return toast.error("اختر موظفاً لديه رصيد عهدة");
            }
            const src = (custodySources.sources || []).find(
                s => s.employee_id === custodyEmployeeId);
            const available = src ? Number(src.available_balance) : 0;
            if (total > available + 0.001) {
                setSaving(false);
                return toast.error(
                    `رصيد العهدة غير كافٍ. المتاح: ` +
                    `${fmt(available)} ر.س`);
            }
        }

        const effectiveTerms = isCustody ? "cash" : paymentTerms;
        const payload = {
            movement_type: movementType,
            doc_date: docDate,
            doc_number: docNumber || null,
            notes: notes || null,
            supplier_id: supplierId || null,
            category_id: categoryId,
            payment_terms: effectiveTerms,
            total_amount: total,
            paid_amount: effectiveTerms === "partial"
                ? Number(paidAmount || 0) : 0,
            paid_from_account_id: isCustody
                ? null
                : (effectiveTerms === "credit" ? null : accountId || null),
            custody_employee_id: isCustody ? custodyEmployeeId : null,
            withdrawal_method: (!isCustody && isBank)
                ? (withdrawal || null) : null,
            reference_number: reference || null,
            attachment: (!isCustody && withdrawal === "transfer"
                         && attachment) || null,
            line_items: movementType === "supplier_invoice"
                ? lineItems
                    .filter((r) => (r.description || "").trim()
                        && Number(r.quantity) > 0
                        && Number(r.unit_price) > 0)
                    .map((r) => ({
                        description: r.description.trim(),
                        quantity: Number(r.quantity),
                        unit_price: Number(r.unit_price),
                        discount_amount: Number(r.discount_amount || 0),
                        tax_amount: Number(r.tax_amount || 0),
                        notes: (r.notes || "").trim() || null,
                        product_id: r.product_id || null,
                        product_sku: r.product_sku || null,
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
                { description: "", quantity: "", unit_price: "",
                  discount_amount: "", tax_amount: "", notes: "" },
            ]);
            // Iter-250b · P1.5.q — Reset custody picker & re-fetch
            // sources so the next entry sees the updated balance.
            if (isCustody) {
                setCustodyEmployeeId("");
                try {
                    const { data: refreshed } = await api.get(
                        "/accounting/custody/spendable-sources");
                    setCustodySources({
                        loaded: true,
                        can_spend_any: !!refreshed.can_spend_any,
                        sources: Array.isArray(refreshed.sources)
                            ? refreshed.sources : [],
                    });
                } catch (_e) { /* non-fatal */ }
            }
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
                                <th className="p-2 text-right">الخصم</th>
                                <th className="p-2 text-right">الضريبة</th>
                                <th className="p-2 text-right">ملاحظات</th>
                                <th className="p-2 text-right">إجمالي السطر</th>
                                <th className="p-2 w-12"></th>
                            </tr>
                        </thead>
                        <tbody data-testid="iter245-mv-lines-body">
                            {lineItems.map((r, i) => {
                                const tot = _lineTotal(r);
                                return (
                                    <tr key={i} className="border-t border-amber-200">
                                        <td className="p-1">
                                            {movementType === "supplier_invoice" ? (
                                                <ProductLineAutocomplete
                                                    value={r.product_id ? {
                                                        product_id: r.product_id,
                                                        name: r.description || r.product_name,
                                                        image_url: r.image_url,
                                                        category_paths: r.category_path ? [r.category_path] : [],
                                                    } : null}
                                                    onSelect={(p) => {
                                                        const rows = [...lineItems];
                                                        if (!p) {
                                                            // Clearing — remove product link.
                                                            rows[i] = {
                                                                ...rows[i],
                                                                product_id: null,
                                                                product_name: null,
                                                                image_url: null,
                                                                category_ids: null,
                                                                category_path: null,
                                                                description: "",
                                                            };
                                                        } else {
                                                            rows[i] = {
                                                                ...rows[i],
                                                                product_id:    p.product_id,
                                                                product_name:  p.name,
                                                                image_url:     p.image_url,
                                                                category_ids:  p.category_ids,
                                                                category_path: p.category_paths?.[0] || null,
                                                                cost_current:  p.cost_current,
                                                                cost_avg:      p.cost_avg,
                                                                description:   p.name,
                                                                // Pre-fill unit_price from last cost when empty.
                                                                unit_price:    rows[i].unit_price || (p.cost_current ?? ""),
                                                            };
                                                        }
                                                        setLineItems(rows);
                                                    }}
                                                />
                                            ) : (
                                                <input
                                                    value={r.description}
                                                    onChange={(e) =>
                                                        updateLine(i, "description",
                                                            e.target.value)}
                                                    placeholder="مثال: عباية كلوش"
                                                    className="w-full border rounded px-2 py-1 text-xs"
                                                    data-testid={`iter245-mv-line-desc-${i}`} />
                                            )}
                                            {r.product_id && (r.cost_current != null || r.cost_avg != null) && (
                                                <div className="text-[10px] text-slate-500 mt-1 font-mono">
                                                    آخر: <b className="text-emerald-800">
                                                        {Number(r.cost_current || 0).toFixed(2)}
                                                    </b>
                                                    {" "}· متوسط: <b className="text-indigo-800">
                                                        {Number(r.cost_avg || 0).toFixed(2)}
                                                    </b>
                                                </div>
                                            )}
                                        </td>
                                        <td className="p-1">
                                            <input
                                                type="number"
                                                step="0.01"
                                                min="0"
                                                value={r.quantity}
                                                onChange={(e) =>
                                                    updateLine(i, "quantity",
                                                        e.target.value)}
                                                className="w-20 border rounded px-2 py-1 text-xs"
                                                data-testid={`iter245-mv-line-qty-${i}`} />
                                        </td>
                                        <td className="p-1">
                                            <input
                                                type="number"
                                                step="0.01"
                                                min="0"
                                                value={r.unit_price}
                                                onChange={(e) =>
                                                    updateLine(i, "unit_price",
                                                        e.target.value)}
                                                className="w-24 border rounded px-2 py-1 text-xs"
                                                data-testid={`iter245-mv-line-price-${i}`} />
                                        </td>
                                        <td className="p-1">
                                            <input
                                                type="number"
                                                step="0.01"
                                                min="0"
                                                value={r.discount_amount}
                                                onChange={(e) =>
                                                    updateLine(i, "discount_amount",
                                                        e.target.value)}
                                                placeholder="0"
                                                className="w-20 border rounded px-2 py-1 text-xs text-rose-700 font-mono"
                                                data-testid={`iter245-mv-line-disc-${i}`} />
                                        </td>
                                        <td className="p-1">
                                            <input
                                                type="number"
                                                step="0.01"
                                                min="0"
                                                value={r.tax_amount}
                                                onChange={(e) =>
                                                    updateLine(i, "tax_amount",
                                                        e.target.value)}
                                                placeholder="0"
                                                className="w-20 border rounded px-2 py-1 text-xs text-indigo-700 font-mono"
                                                data-testid={`iter245-mv-line-tax-${i}`} />
                                        </td>
                                        <td className="p-1">
                                            <input
                                                type="text"
                                                value={r.notes || ""}
                                                onChange={(e) =>
                                                    updateLine(i, "notes",
                                                        e.target.value)}
                                                placeholder="—"
                                                className="w-36 border rounded px-2 py-1 text-xs"
                                                data-testid={`iter245-mv-line-notes-${i}`} />
                                        </td>
                                        <td className="p-1 font-mono font-bold text-emerald-800"
                                            data-testid={`iter245-mv-line-total-${i}`}>
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
                        disabled={isGeneralExpense && paySource === "custody"}
                        className={`w-full border rounded px-3 py-2 text-sm ${isGeneralExpense && paySource === "custody" ? "bg-slate-100 text-slate-500 cursor-not-allowed" : ""}`}
                        data-testid="iter245-mv-terms">
                        {TERMS.map((t) => (
                            <option key={t.value} value={t.value}>
                                {t.label}
                            </option>
                        ))}
                    </select>
                    {isGeneralExpense && paySource === "custody" && (
                        <p className="text-[11px] text-amber-700 mt-1">
                            🔒 الدفع من العهدة نقدي فقط — لا يدعم آجل/جزئي.
                        </p>
                    )}
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

            {/* Iter-250b · P1.5.q — Pay-source toggle (general_expense only).
                Lets the merchant choose to pay this expense from a
                bank/cash account OR from an employee's open custody
                wallet. ONLY one source per transaction. Selecting
                "عهدة" forces payment_terms = cash (no آجل / partial). */}
            {isGeneralExpense && paymentTerms !== "credit" && (
                <Field label="مصدر السداد">
                    <div className="flex gap-2 mb-2">
                        <button type="button"
                            onClick={() => setPaySource("bank")}
                            className={`flex-1 px-3 py-2 rounded text-sm font-bold border transition ${paySource === "bank"
                                ? "bg-indigo-600 text-white border-indigo-600"
                                : "bg-white text-slate-700 border-slate-300 hover:bg-slate-100"}`}
                            data-testid="iter245-paysource-bank-btn">
                            🏦 بنك / صندوق
                        </button>
                        <button type="button"
                            onClick={() => setPaySource("custody")}
                            className={`flex-1 px-3 py-2 rounded text-sm font-bold border transition ${paySource === "custody"
                                ? "bg-amber-600 text-white border-amber-600"
                                : "bg-white text-slate-700 border-slate-300 hover:bg-slate-100"}`}
                            data-testid="iter245-paysource-custody-btn">
                            👤 عهدة موظف
                        </button>
                    </div>
                    {paySource === "custody" && (
                        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3">
                            {!custodySources.loaded ? (
                                <div className="text-xs text-slate-500">
                                    ⏳ يتم تحميل قائمة العهد المتاحة...
                                </div>
                            ) : custodySources.sources.length === 0 ? (
                                <div className="text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded px-3 py-2 font-bold"
                                     data-testid="iter245-custody-no-sources">
                                    {custodySources.can_spend_any
                                        ? "🚫 لا يوجد موظفون لديهم رصيد عهدة موجب حالياً."
                                        : "🚫 لا توجد عهدة مرتبطة بحسابك حالياً، أو رصيدك = 0."}
                                </div>
                            ) : (
                                <>
                                    <label className="block text-xs font-bold text-slate-700 mb-1">
                                        اختر الموظف:
                                    </label>
                                    <select
                                        value={custodyEmployeeId}
                                        onChange={(e) => setCustodyEmployeeId(e.target.value)}
                                        className="w-full px-3 py-2 border border-amber-300 bg-white rounded text-sm"
                                        data-testid="iter245-custody-employee-select">
                                        <option value="">— اختر موظفاً —</option>
                                        {custodySources.sources.map(s => (
                                            <option key={s.employee_id}
                                                    value={s.employee_id}
                                                    data-testid={`iter245-custody-opt-${s.employee_id}`}>
                                                {s.name} · رصيد العهدة: {fmt(s.available_balance)} ر.س
                                            </option>
                                        ))}
                                    </select>
                                    {custodyEmployeeId && (() => {
                                        const sel = custodySources.sources.find(
                                            s => s.employee_id === custodyEmployeeId);
                                        if (!sel) return null;
                                        const amt = Number(totalAmount) || 0;
                                        const avail = Number(sel.available_balance);
                                        const after = avail - amt;
                                        const insufficient = amt > avail + 0.001;
                                        return (
                                            <div className={`mt-2 text-[12px] rounded px-3 py-2 ${insufficient
                                                ? "bg-rose-100 border border-rose-300 text-rose-800 font-bold"
                                                : "bg-emerald-50 border border-emerald-200 text-emerald-900"}`}
                                                 data-testid="iter245-custody-balance-preview">
                                                👤 <span className="font-bold">{sel.name}</span>
                                                <div>الرصيد الحالي: {fmt(avail)} ر.س</div>
                                                {amt > 0 && (
                                                    <div>
                                                        {insufficient
                                                            ? `⚠️ الرصيد غير كافٍ (المطلوب: ${fmt(amt)} ر.س)`
                                                            : `الرصيد بعد العملية: ${fmt(after)} ر.س`}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })()}
                                    <p className="text-[11px] text-slate-500 mt-2 leading-tight">
                                        💡 لن يتم خصم أي مبلغ من البنك أو الصندوق — يتم تخفيض رصيد العهدة فقط.
                                    </p>
                                </>
                            )}
                        </div>
                    )}
                </Field>
            )}

            {/* Account picker — only when cash leaves an account */}
            {paymentTerms !== "credit"
              && !(isGeneralExpense && paySource === "custody") && (
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
            {paymentTerms !== "credit" && isBank
              && !(isGeneralExpense && paySource === "custody") && (
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
