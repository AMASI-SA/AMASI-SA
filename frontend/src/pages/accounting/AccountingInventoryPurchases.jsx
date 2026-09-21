import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import {
    activateAccountingP03,
    approveAccountingP03OpeningInventoryCosts,
    createAccountingP03PurchaseInvoice,
    getAccountingInventoryP03Workspace,
    getAccountingP03OpeningInventoryCosts,
    postAccountingP03Cogs,
    postAccountingP03InventoryReceipt,
    postAccountingP03SupplierInvoice,
    previewAccountingP03Cogs,
    previewAccountingP03InventoryReceipt,
    previewAccountingP03SupplierInvoice,
} from "../../services/accountingModule";
import { formatMoney } from "./AccountingShared";

function errorText(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    return detail?.message || detail?.code || fallback;
}

function PreviewState({ preview }) {
    if (!preview) return null;
    if (preview.state === "eligible") {
        return (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[11px] font-bold text-emerald-900">
                المعاينة سليمة ويمكن الاعتماد.
            </div>
        );
    }
    if (preview.state === "already_posted") {
        return (
            <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-[11px] font-bold text-sky-900">
                مرحّل مسبقًا ولن يتكرر.
            </div>
        );
    }
    if (preview.state === "waiting") {
        return (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] font-bold text-amber-900">
                بانتظار دليل إضافي: {preview?.reasons?.join("، ") || "غير مكتمل"}
            </div>
        );
    }
    return (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] font-bold text-rose-900">
            {preview?.reasons?.join("، ") || "غير مؤهل للترحيل"}
        </div>
    );
}

const emptyLine = () => ({
    product_id: "",
    product_name: "",
    sku: "",
    quantity: "1",
    unit_price: "",
});

export default function AccountingInventoryPurchases({
    accountingPermissions = [],
    isOwner = false,
}) {
    const [workspace, setWorkspace] = useState(null);
    const [openingCosts, setOpeningCosts] = useState(null);
    const [openingCostValues, setOpeningCostValues] = useState({});
    const [openingCostEvidence, setOpeningCostEvidence] = useState("");
    const [openingCostReason, setOpeningCostReason] = useState("");
    const [busy, setBusy] = useState("");
    const [activationRef, setActivationRef] = useState("");
    const [receiptState, setReceiptState] = useState({});
    const [cogsState, setCogsState] = useState({});
    const [supplierState, setSupplierState] = useState({});
    const [purchase, setPurchase] = useState({
        supplier_id: "",
        invoice_number: "",
        invoice_date: new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh" }).format(new Date()),
        due_date: "",
        tax_amount: "0",
        tax_treatment: "recoverable_input_vat",
        tax_evidence_ref: "",
        notes: "",
    });
    const [lines, setLines] = useState([emptyLine()]);
    const purchaseRequest = useRef(null);

    const canPost = accountingPermissions.includes("accounting.purchases.post");
    const phase = workspace?.phase || {};
    const p03Active = phase.p03_inventory_purchases_enabled === true;
    const openingSnapshotRequired = Boolean(
        (openingCosts?.target_count || 0) > 0
        || Number(openingCosts?.opening_inventory_halalas || 0) > 0
    );
    const openingSnapshotApproved = Boolean(
        !openingSnapshotRequired
        || (
            openingCosts?.snapshot?.status === "approved"
            && openingCosts?.snapshot?.inventory_fingerprint
                === openingCosts?.inventory_fingerprint
            && Number(openingCosts?.snapshot?.total_cost_halalas || -1)
                === Number(openingCosts?.opening_inventory_halalas || 0)
        )
    );
    const openingCostInputTotal = (openingCosts?.targets || []).reduce(
        (total, row) => total
            + (Number(row.quantity || 0) * Number(openingCostValues[row.target_key] || 0)),
        0,
    );

    async function refresh() {
        const [next, opening] = await Promise.all([
            getAccountingInventoryP03Workspace(),
            getAccountingP03OpeningInventoryCosts(),
        ]);
        setWorkspace(next);
        setOpeningCosts(opening);
        const approvedByKey = Object.fromEntries(
            (opening?.snapshot?.lines || []).map((row) => [
                row.target_key,
                row.unit_cost || "",
            ]),
        );
        setOpeningCostValues(Object.fromEntries(
            (opening?.targets || []).map((row) => [
                row.target_key,
                approvedByKey[row.target_key] || "",
            ]),
        ));
        if (opening?.snapshot?.evidence_ref) {
            setOpeningCostEvidence(opening.snapshot.evidence_ref);
        }
        if (opening?.snapshot?.reason) {
            setOpeningCostReason(opening.snapshot.reason);
        }
    }

    useEffect(() => {
        refresh().catch((error) => toast.error(errorText(error, "تعذر تحميل المخزون والمشتريات")));
    }, []);

    const productMap = useMemo(
        () => Object.fromEntries((workspace?.products || []).map((row) => [
            row.mezan_product_id || row.salla_product_id,
            row,
        ])),
        [workspace],
    );

    function updateOpeningCost(targetKey, value) {
        setOpeningCostValues((current) => ({
            ...current,
            [targetKey]: value,
        }));
    }

    async function approveOpeningCosts() {
        if (!isOwner) return toast.error("اعتماد تكلفة المخزون الافتتاحي متاح للمالك فقط");
        if (!canPost) return toast.error("لا تملك صلاحية المشتريات");
        if (!openingCosts?.inventory_fingerprint) return toast.error("أعد تحميل Snapshot المخزون");
        if ((openingCosts?.blockers || []).length) {
            return toast.error("يوجد مانع يمنع اعتماد Snapshot المخزون الافتتاحي");
        }
        if (openingCostEvidence.trim().length < 3) return toast.error("أدخل مرجع دليل تكلفة المخزون الافتتاحي");
        if (openingCostReason.trim().length < 3) return toast.error("أدخل سبب اعتماد Snapshot");
        const lines = (openingCosts.targets || []).map((row) => ({
            target_key: row.target_key,
            unit_cost: openingCostValues[row.target_key],
        }));
        if (!lines.length) return toast.error("لا توجد lots مخزون افتتاحي لاعتماد تكلفتها");
        if (lines.some((row) => !(Number(row.unit_cost) > 0))) {
            return toast.error("أدخل تكلفة وحدة أكبر من صفر لكل lot افتتاحي");
        }
        setBusy("opening-costs");
        try {
            await approveAccountingP03OpeningInventoryCosts({
                inventory_fingerprint: openingCosts.inventory_fingerprint,
                evidence_ref: openingCostEvidence.trim(),
                reason: openingCostReason.trim(),
                lines,
            });
            toast.success("تم اعتماد تكلفة المخزون الافتتاحي ومطابقتها مع GL بالهللة.");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر اعتماد تكلفة المخزون الافتتاحي"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    async function activate() {
        if (!isOwner) return toast.error("تفعيل P03 متاح لمالك ميزان فقط");
        if (!canPost) return toast.error("لا تملك صلاحية المشتريات");
        if (!openingSnapshotApproved) return toast.error("اعتمد Snapshot تكلفة المخزون الافتتاحي أولًا");
        if ((openingCosts?.blockers || []).length) return toast.error("أزل موانع Snapshot المخزون قبل تفعيل P03");
        if (activationRef.trim().length < 3) return toast.error("أدخل مرجع اعتماد P03");
        setBusy("activate");
        try {
            await activateAccountingP03(activationRef.trim());
            toast.success("تم تفعيل P03 في بيئة MZ2 الحالية.");
            setActivationRef("");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر تفعيل P03"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    function changeLine(index, patch) {
        setLines((current) => current.map((line, i) => (
            i === index ? { ...line, ...patch } : line
        )));
        purchaseRequest.current = null;
    }

    function chooseProduct(index, productId) {
        const product = productMap[productId] || {};
        changeLine(index, {
            product_id: productId,
            product_name: product.name || "",
            sku: product.sku || "",
        });
    }

    async function createPurchase(event) {
        event.preventDefault();
        if (!canPost) return toast.error("لا تملك صلاحية إنشاء فاتورة شراء MZ2");
        if (!purchase.supplier_id) return toast.error("اختر المورد");
        const normalizedLines = lines.map((line) => ({
            product_id: line.product_id || null,
            product_name: String(line.product_name || "").trim(),
            sku: String(line.sku || "").trim() || null,
            quantity: Number(line.quantity),
            unit_price: line.unit_price,
        }));
        if (normalizedLines.some((line) => !line.product_name || !(line.quantity > 0) || !(Number(line.unit_price) > 0))) {
            return toast.error("أكمل اسم المنتج والكمية والتكلفة لكل بند");
        }
        const facts = {
            supplier_id: purchase.supplier_id,
            invoice_number: purchase.invoice_number.trim() || null,
            invoice_date: purchase.invoice_date,
            due_date: purchase.due_date || null,
            lines: normalizedLines,
            tax_amount: purchase.tax_amount || "0",
            tax_treatment: purchase.tax_treatment,
            tax_evidence_ref: purchase.tax_evidence_ref.trim() || null,
            notes: purchase.notes.trim(),
        };
        if (
            Number(facts.tax_amount) > 0
            && facts.tax_treatment === "recoverable_input_vat"
            && !facts.tax_evidence_ref
        ) {
            return toast.error("أدخل مرجع الفاتورة أو الدليل الضريبي لاسترداد ضريبة المدخلات");
        }
        const fingerprint = JSON.stringify(facts);
        if (purchaseRequest.current?.fingerprint !== fingerprint) {
            purchaseRequest.current = {
                fingerprint,
                id: crypto.randomUUID(),
            };
        }
        setBusy("purchase");
        try {
            const result = await createAccountingP03PurchaseInvoice({
                request_id: purchaseRequest.current.id,
                ...facts,
            });
            if (result?.duplicate) {
                toast.info("الفاتورة محفوظة مسبقًا؛ لم تتكرر.");
            } else {
                toast.success("تم إنشاء فاتورة شراء MZ2 بدون إنشاء ذمة قبل الاستلام.");
            }
            setPurchase((current) => ({
                ...current,
                invoice_number: "",
                due_date: "",
                tax_amount: "0",
                tax_evidence_ref: "",
                notes: "",
            }));
            setLines([emptyLine()]);
            purchaseRequest.current = null;
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر إنشاء فاتورة الشراء"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    function updateReceipt(receiptId, patch) {
        setReceiptState((current) => ({
            ...current,
            [receiptId]: { ...(current[receiptId] || {}), ...patch },
        }));
    }

    async function previewReceipt(row) {
        setBusy("preview-receipt:" + row.id);
        try {
            const preview = await previewAccountingP03InventoryReceipt(row.id);
            updateReceipt(row.id, { preview });
        } catch (error) {
            toast.error(errorText(error, "تعذر معاينة استلام المخزون"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    async function postReceipt(row) {
        const state = receiptState[row.id] || {};
        const reason = String(state.reason || "").trim();
        if (!p03Active) return toast.error("P03 ما زال مقفلاً");
        if (!canPost) return toast.error("لا تملك صلاحية ترحيل المشتريات");
        if (state.preview?.state !== "eligible") return toast.error("نفّذ المعاينة أولًا");
        if (reason.length < 3) return toast.error("اكتب سبب اعتماد الاستلام");
        setBusy("post-receipt:" + row.id);
        try {
            await postAccountingP03InventoryReceipt(row.id, reason);
            toast.success("تم إثبات تكلفة المخزون والضريبة وذمة المورد بقدر الكمية المستلمة.");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر ترحيل استلام المخزون"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    function updateCogs(eventId, patch) {
        setCogsState((current) => ({
            ...current,
            [eventId]: { ...(current[eventId] || {}), ...patch },
        }));
    }

    async function previewCogs(row) {
        setBusy("preview-cogs:" + row.id);
        try {
            const preview = await previewAccountingP03Cogs(row.id);
            updateCogs(row.id, { preview });
        } catch (error) {
            toast.error(errorText(error, "تعذر معاينة تكلفة البضاعة المباعة"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    async function postCogs(row) {
        const state = cogsState[row.id] || {};
        const reason = String(state.reason || "").trim();
        if (!p03Active) return toast.error("P03 ما زال مقفلاً");
        if (!canPost) return toast.error("لا تملك صلاحية ترحيل المشتريات");
        if (state.preview?.state !== "eligible") return toast.error("نفّذ المعاينة أولًا وتأكد من وجود قيد البيع");
        if (reason.length < 3) return toast.error("اكتب سبب اعتماد تكلفة البضاعة المباعة");
        setBusy("post-cogs:" + row.id);
        try {
            await postAccountingP03Cogs(row.id, reason);
            toast.success("تم إثبات تكلفة البضاعة المباعة وخفض رصيد المخزون من تكلفة الـlot الأصلية.");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر ترحيل تكلفة البضاعة المباعة"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    function updateSupplier(invoiceId, patch) {
        setSupplierState((current) => ({
            ...current,
            [invoiceId]: { ...(current[invoiceId] || {}), ...patch },
        }));
    }

    async function previewSupplierInvoice(row) {
        setBusy("preview-supplier:" + row.id);
        try {
            const preview = await previewAccountingP03SupplierInvoice(row.id);
            updateSupplier(row.id, { preview });
        } catch (error) {
            toast.error(errorText(error, "تعذر معاينة فاتورة المورد"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    async function postSupplierInvoice(row) {
        const state = supplierState[row.id] || {};
        const reason = String(state.reason || "").trim();
        if (!p03Active) return toast.error("P03 ما زال مقفلاً");
        if (!canPost) return toast.error("لا تملك صلاحية ترحيل المشتريات");
        if (state.preview?.state !== "eligible") return toast.error("نفّذ المعاينة أولًا");
        if (reason.length < 3) return toast.error("اكتب سبب الاعتماد");
        setBusy("post-supplier:" + row.id);
        try {
            await postAccountingP03SupplierInvoice(row.id, reason);
            toast.success("تم إثبات فاتورة خدمات المورد وذمته عبر MZ2.");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر ترحيل فاتورة المورد"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    if (!workspace) {
        return (
            <div className="rounded-2xl border border-slate-200 bg-white p-6 text-sm font-bold text-slate-500">
                جاري تحميل المخزون والمشتريات…
            </div>
        );
    }

    return (
        <div className="space-y-5" dir="rtl" data-testid="accounting-inventory-purchases">
            <section className={`rounded-2xl border p-5 ${p03Active ? "border-emerald-200 bg-emerald-50/60" : "border-amber-200 bg-amber-50/60"}`}>
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <h2 className="text-lg font-black text-slate-950">P03 — المخزون والمشتريات</h2>
                        <p className="mt-1 max-w-3xl text-xs font-semibold leading-6 text-slate-700">
                            Inventory V2 يبقى مصدر الاستلام والمخزون التشغيلي. MZ2 يثبت الأثر المالي فقط بعد الاستلام الفعلي، ولا يستخدم ذمم ميزان القديمة أو هويات الموردين القديمة.
                        </p>
                    </div>
                    <span className={`rounded-full border px-3 py-1 text-xs font-black ${p03Active ? "border-emerald-300 bg-white text-emerald-800" : "border-amber-300 bg-white text-amber-800"}`}>
                        {p03Active ? "P03 مفعّل" : "P03 مقفل"}
                    </span>
                </div>
                {!p03Active && (
                    <div className="mt-4 grid gap-2 border-t border-amber-200 pt-4 md:grid-cols-[1fr_auto]">
                        <input
                            value={activationRef}
                            onChange={(event) => setActivationRef(event.target.value)}
                            placeholder="مرجع اعتماد P03 — Preview/UAT"
                            className="min-h-11 rounded-xl border border-amber-200 bg-white px-3 text-sm"
                        />
                        <button
                            type="button"
                            onClick={activate}
                            disabled={!isOwner || !canPost || !openingSnapshotApproved || (openingCosts?.blockers || []).length > 0 || busy === "activate"}
                            className="min-h-11 rounded-xl bg-amber-800 px-5 text-sm font-black text-white disabled:opacity-40"
                        >
                            {busy === "activate" ? "جاري التفعيل…" : "تفعيل P03"}
                        </button>
                    </div>
                )}
            </section>

            {!p03Active && openingCosts && openingSnapshotRequired && (
                <section className="rounded-2xl border border-cyan-200 bg-cyan-50/50 p-5">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                            <h2 className="text-lg font-black text-cyan-950">Snapshot تكلفة المخزون الافتتاحي</h2>
                            <p className="mt-1 max-w-3xl text-xs font-semibold leading-6 text-cyan-900">
                                هذا الجدول يثبت تكلفة الـlots الموجودة يوم القطع فقط. مجموع التكلفة يجب أن يساوي رصيد المخزون الافتتاحي في GL بالهللة؛ وبعد الاعتماد لا تُستخدم تكلفة الكتالوج الحالية لحساب COGS.
                            </p>
                        </div>
                        <div className="rounded-xl border border-cyan-200 bg-white px-4 py-2 text-xs font-black text-cyan-900">
                            GL: {formatMoney(Number(openingCosts.opening_inventory_halalas || 0) / 100)}
                            {" · "}
                            المدخل: {formatMoney(openingCostInputTotal)}
                        </div>
                    </div>

                    {(openingCosts.blockers || []).length > 0 && (
                        <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-bold text-rose-800">
                            لا يمكن الاعتماد الآن: {(openingCosts.blockers || []).join("، ")}
                        </div>
                    )}

                    <div className="mt-4 space-y-2">
                        {(openingCosts.targets || []).map((row) => (
                            <div key={row.target_key} className="grid gap-2 rounded-xl border border-cyan-200 bg-white p-3 md:grid-cols-[1.5fr_.6fr_.8fr_1fr] md:items-center">
                                <div>
                                    <div className="font-black text-slate-900">{row.product_name || row.sku || row.target_key}</div>
                                    <div className="mt-1 text-[10px] font-semibold text-slate-500">
                                        {row.target_key} · {row.location_code || row.location_id} · {row.sku || "بلا SKU"}
                                    </div>
                                </div>
                                <div className="text-xs font-black text-slate-700">كمية {row.quantity}</div>
                                <input
                                    type="number"
                                    min="0.01"
                                    step="0.01"
                                    value={openingCostValues[row.target_key] || ""}
                                    onChange={(event) => updateOpeningCost(row.target_key, event.target.value)}
                                    placeholder="تكلفة الوحدة"
                                    className="min-h-10 rounded-lg border border-cyan-200 px-2 text-xs"
                                />
                                <div className="text-xs font-black text-cyan-900">
                                    إجمالي {formatMoney(Number(row.quantity || 0) * Number(openingCostValues[row.target_key] || 0))}
                                </div>
                            </div>
                        ))}
                    </div>

                    <div className="mt-4 grid gap-3 md:grid-cols-2">
                        <input
                            value={openingCostEvidence}
                            onChange={(event) => setOpeningCostEvidence(event.target.value)}
                            placeholder="مرجع جرد/تكلفة المخزون يوم القطع"
                            className="min-h-10 rounded-xl border border-cyan-200 bg-white px-3 text-xs"
                        />
                        <input
                            value={openingCostReason}
                            onChange={(event) => setOpeningCostReason(event.target.value)}
                            placeholder="سبب اعتماد Snapshot"
                            className="min-h-10 rounded-xl border border-cyan-200 bg-white px-3 text-xs"
                        />
                    </div>
                    <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                        <div className="text-xs font-bold text-slate-600">
                            {openingSnapshotApproved
                                ? "Snapshot معتمد ومطابق للرصيد الافتتاحي."
                                : "يجب الاعتماد قبل تفعيل P03."}
                        </div>
                        <button
                            type="button"
                            onClick={approveOpeningCosts}
                            disabled={!isOwner || !canPost || busy === "opening-costs" || (openingCosts.blockers || []).length > 0}
                            className="min-h-10 rounded-xl bg-cyan-800 px-5 text-xs font-black text-white disabled:opacity-40"
                        >
                            {busy === "opening-costs" ? "جاري الاعتماد…" : "اعتماد Snapshot المخزون"}
                        </button>
                    </div>
                </section>
            )}

            <section className="rounded-2xl border border-slate-200 bg-white p-5">
                <div className="flex flex-wrap items-end justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-black text-slate-950">فاتورة شراء للمخزون</h2>
                        <p className="mt-1 text-xs font-semibold leading-6 text-slate-500">
                            إنشاء الفاتورة لا ينشئ ذمة. الذمة وتكلفة المخزون تُثبت فقط عند استلام الكمية فعليًا.
                        </p>
                    </div>
                    <Link to="/inventory-receiving-v2" className="rounded-xl border border-emerald-300 bg-emerald-50 px-4 py-2 text-xs font-black text-emerald-900">
                        فتح استلام Inventory V2
                    </Link>
                </div>
                <form onSubmit={createPurchase} className="mt-4 space-y-4">
                    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                        <label className="text-xs font-black text-slate-700">
                            المورد
                            <select value={purchase.supplier_id} onChange={(e) => { setPurchase((x) => ({ ...x, supplier_id: e.target.value })); purchaseRequest.current = null; }} className="mt-1 min-h-10 w-full rounded-xl border border-slate-200 px-3">
                                <option value="">اختر المورد</option>
                                {(workspace.suppliers || []).map((row) => <option key={row.id} value={row.id}>{row.company_name}</option>)}
                            </select>
                        </label>
                        <label className="text-xs font-black text-slate-700">
                            رقم الفاتورة
                            <input value={purchase.invoice_number} onChange={(e) => { setPurchase((x) => ({ ...x, invoice_number: e.target.value })); purchaseRequest.current = null; }} className="mt-1 min-h-10 w-full rounded-xl border border-slate-200 px-3" />
                        </label>
                        <label className="text-xs font-black text-slate-700">
                            تاريخ الفاتورة
                            <input type="date" value={purchase.invoice_date} onChange={(e) => { setPurchase((x) => ({ ...x, invoice_date: e.target.value })); purchaseRequest.current = null; }} className="mt-1 min-h-10 w-full rounded-xl border border-slate-200 px-3" />
                        </label>
                        <label className="text-xs font-black text-slate-700">
                            الاستحقاق
                            <input type="date" value={purchase.due_date} onChange={(e) => { setPurchase((x) => ({ ...x, due_date: e.target.value })); purchaseRequest.current = null; }} className="mt-1 min-h-10 w-full rounded-xl border border-slate-200 px-3" />
                        </label>
                    </div>

                    <div className="space-y-2">
                        {lines.map((line, index) => (
                            <div key={index} className="grid gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 md:grid-cols-2 xl:grid-cols-[1.4fr_1.2fr_1fr_.6fr_.8fr_auto]">
                                <select value={line.product_id} onChange={(e) => chooseProduct(index, e.target.value)} className="min-h-10 rounded-lg border border-slate-200 bg-white px-2 text-xs">
                                    <option value="">منتج من كتالوج ميزان — اختياري</option>
                                    {(workspace.products || []).map((product) => (
                                        <option key={product.mezan_product_id || product.salla_product_id} value={product.mezan_product_id || product.salla_product_id}>
                                            {product.name}
                                        </option>
                                    ))}
                                </select>
                                <input value={line.product_name} onChange={(e) => changeLine(index, { product_name: e.target.value })} placeholder="اسم المنتج" className="min-h-10 rounded-lg border border-slate-200 bg-white px-2 text-xs" />
                                <input value={line.sku} onChange={(e) => changeLine(index, { sku: e.target.value })} placeholder="SKU" className="min-h-10 rounded-lg border border-slate-200 bg-white px-2 text-xs" />
                                <input type="number" min="1" step="1" value={line.quantity} onChange={(e) => changeLine(index, { quantity: e.target.value })} placeholder="الكمية" className="min-h-10 rounded-lg border border-slate-200 bg-white px-2 text-xs" />
                                <input type="number" min="0.01" step="0.01" value={line.unit_price} onChange={(e) => changeLine(index, { unit_price: e.target.value })} placeholder="تكلفة الوحدة" className="min-h-10 rounded-lg border border-slate-200 bg-white px-2 text-xs" />
                                <button type="button" disabled={lines.length === 1} onClick={() => { setLines((current) => current.filter((_, i) => i !== index)); purchaseRequest.current = null; }} className="min-h-10 rounded-lg border border-rose-200 px-3 text-xs font-black text-rose-700 disabled:opacity-30">
                                    حذف
                                </button>
                            </div>
                        ))}
                        <button type="button" onClick={() => { setLines((current) => [...current, emptyLine()]); purchaseRequest.current = null; }} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-black text-slate-700">
                            إضافة بند
                        </button>
                    </div>

                    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                        <label className="text-xs font-black text-slate-700">
                            الضريبة الإجمالية
                            <input type="number" min="0" step="0.01" value={purchase.tax_amount} onChange={(e) => { setPurchase((x) => ({ ...x, tax_amount: e.target.value })); purchaseRequest.current = null; }} className="mt-1 min-h-10 w-full rounded-xl border border-slate-200 px-3" />
                        </label>
                        <label className="text-xs font-black text-slate-700">
                            معالجة الضريبة
                            <select value={purchase.tax_treatment} onChange={(e) => { setPurchase((x) => ({ ...x, tax_treatment: e.target.value })); purchaseRequest.current = null; }} className="mt-1 min-h-10 w-full rounded-xl border border-slate-200 px-3">
                                <option value="recoverable_input_vat">ضريبة مدخلات قابلة للاسترداد</option>
                                <option value="included_in_inventory_cost">تضاف إلى تكلفة المخزون</option>
                            </select>
                        </label>
                        <label className="text-xs font-black text-slate-700">
                            مرجع الدليل الضريبي
                            <input
                                value={purchase.tax_evidence_ref}
                                onChange={(e) => { setPurchase((x) => ({ ...x, tax_evidence_ref: e.target.value })); purchaseRequest.current = null; }}
                                placeholder="رقم/مرجع الفاتورة الضريبية"
                                className="mt-1 min-h-10 w-full rounded-xl border border-slate-200 px-3"
                            />
                        </label>
                        <label className="text-xs font-black text-slate-700">
                            ملاحظة
                            <input value={purchase.notes} onChange={(e) => { setPurchase((x) => ({ ...x, notes: e.target.value })); purchaseRequest.current = null; }} className="mt-1 min-h-10 w-full rounded-xl border border-slate-200 px-3" />
                        </label>
                    </div>
                    <div className="flex justify-end">
                        <button disabled={!canPost || busy === "purchase"} className="min-h-11 rounded-xl bg-emerald-800 px-5 text-sm font-black text-white disabled:opacity-40">
                            {busy === "purchase" ? "جاري الحفظ…" : "إنشاء فاتورة الشراء"}
                        </button>
                    </div>
                </form>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5">
                <h2 className="text-lg font-black text-slate-950">فواتير شراء المخزون MZ2</h2>
                <div className="mt-4 space-y-2">
                    {(workspace.purchase_invoices || []).map((row) => (
                        <div key={row.id} className="grid gap-2 rounded-xl border border-slate-200 p-3 md:grid-cols-[1fr_auto] md:items-center">
                            <div>
                                <div className="font-black text-slate-900">{row.invoice_number || row.id} · {row.supplier_name}</div>
                                <div className="mt-1 text-[11px] font-semibold text-slate-500">
                                    {row.invoice_date} · إجمالي {formatMoney(row.total)} · معترف حتى الآن {formatMoney(row.recognized_payable || 0)}
                                </div>
                            </div>
                            <span className="rounded-lg bg-slate-100 px-3 py-1 text-[11px] font-black text-slate-700">{row.status}</span>
                        </div>
                    ))}
                    {!(workspace.purchase_invoices || []).length && <div className="rounded-xl bg-slate-50 p-4 text-xs font-bold text-slate-500">لا توجد فواتير شراء MZ2 بعد.</div>}
                </div>
            </section>

            <section className="rounded-2xl border border-violet-200 bg-violet-50/50 p-5">
                <div>
                    <h2 className="text-lg font-black text-violet-950">استلامات المخزون الفعلية الجاهزة للمحاسبة</h2>
                    <p className="mt-1 text-xs font-semibold leading-6 text-violet-900">
                        كل صف هنا قادم من Inventory V2 بعد وضع البضاعة فعليًا في المستودع. المعاينة تحسب تكلفة الكمية وضريبتها ثم الاعتماد يثبت ذمة المورد.
                    </p>
                </div>
                <div className="mt-4 space-y-3">
                    {(workspace.inventory_receipts || []).map((row) => {
                        const state = receiptState[row.id] || {};
                        return (
                            <div key={row.id} className="rounded-xl border border-violet-200 bg-white p-3">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <div>
                                        <div className="font-black text-slate-900">{row.product_name || row.sku || row.id} · كمية {row.quantity}</div>
                                        <div className="mt-1 text-[11px] font-semibold text-slate-500">{row.invoice_number || row.purchase_invoice_id} · {row.supplier_name || ""} · {String(row.posted_at || "").slice(0, 16)}</div>
                                    </div>
                                    {row.accounting_event_id && <span className="rounded-full bg-emerald-50 px-3 py-1 text-[10px] font-black text-emerald-800">مرحّل</span>}
                                </div>
                                {!row.accounting_event_id && (
                                    <div className="mt-3 grid gap-2 md:grid-cols-[1fr_auto_auto] md:items-center">
                                        <input value={state.reason || ""} onChange={(e) => updateReceipt(row.id, { reason: e.target.value })} placeholder="سبب اعتماد الاستلام" className="min-h-9 rounded-lg border border-slate-200 px-2 text-xs" />
                                        <button type="button" onClick={() => previewReceipt(row)} disabled={busy === "preview-receipt:" + row.id} className="min-h-9 rounded-lg border border-violet-300 px-3 text-xs font-black text-violet-900">معاينة</button>
                                        <button type="button" onClick={() => postReceipt(row)} disabled={!canPost || !p03Active || state.preview?.state !== "eligible" || busy === "post-receipt:" + row.id} className="min-h-9 rounded-lg bg-violet-800 px-3 text-xs font-black text-white disabled:opacity-40">اعتماد الاستلام</button>
                                    </div>
                                )}
                                {state.preview?.facts && (
                                    <div className="mt-2 text-xs font-black text-violet-900">
                                        مخزون {formatMoney(state.preview.facts.net_amount)} · ضريبة {formatMoney(state.preview.facts.tax_amount)} · ذمة المورد {formatMoney(state.preview.facts.gross_amount)}
                                    </div>
                                )}
                                <div className="mt-2"><PreviewState preview={state.preview} /></div>
                            </div>
                        );
                    })}
                    {!(workspace.inventory_receipts || []).length && <div className="rounded-xl bg-white p-4 text-xs font-bold text-violet-700">لا توجد استلامات MZ2 معلقة ضمن فواتير الشراء الجديدة.</div>}
                </div>
            </section>

            <section className="rounded-2xl border border-orange-200 bg-orange-50/50 p-5">
                <div>
                    <h2 className="text-lg font-black text-orange-950">تكلفة البضاعة المباعة — COGS</h2>
                    <p className="mt-1 text-xs font-semibold leading-6 text-orange-900">
                        Fulfillment V2 يحفظ الـlot الذي خرج فعليًا من المخزون. لا يُرحّل COGS حتى يظهر قيد بيع MZ2 لنفس الطلب، ثم تُستخدم تكلفة الاستلام الأصلية أو Snapshot يوم القطع. الاسترداد المالي أو فحص المرتجع وحده لا يعكس COGS؛ العكس يتطلب Restock فعليًا للمخزون.
                    </p>
                </div>
                <div className="mt-4 space-y-3">
                    {(workspace.inventory_consumptions || []).map((row) => {
                        const state = cogsState[row.id] || {};
                        const posted = Boolean(row.cogs_event_id);
                        return (
                            <div key={row.id} className="rounded-xl border border-orange-200 bg-white p-3">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <div>
                                        <div className="font-black text-slate-900">طلب {row.order_number} · دفعة {row.batch_id || "—"}</div>
                                        <div className="mt-1 text-[11px] font-semibold text-slate-500">
                                            استهلاك فعلي {String(row.consumed_at || "").slice(0, 16)} · {(row.allocations || []).length} مصدر مخزون
                                        </div>
                                    </div>
                                    {posted && (
                                        <span className="rounded-full bg-emerald-50 px-3 py-1 text-[10px] font-black text-emerald-800">
                                            COGS مرحّل {row.cogs_amount ? "· " + formatMoney(row.cogs_amount) : ""}
                                        </span>
                                    )}
                                </div>
                                {!posted && (
                                    <div className="mt-3 grid gap-2 md:grid-cols-[1fr_auto_auto]">
                                        <input
                                            value={state.reason || ""}
                                            onChange={(e) => updateCogs(row.id, { reason: e.target.value })}
                                            placeholder="سبب اعتماد COGS"
                                            className="min-h-9 rounded-lg border border-slate-200 px-2 text-xs"
                                        />
                                        <button
                                            type="button"
                                            onClick={() => previewCogs(row)}
                                            disabled={busy === "preview-cogs:" + row.id}
                                            className="min-h-9 rounded-lg border border-orange-300 px-3 text-xs font-black text-orange-900"
                                        >
                                            معاينة COGS
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => postCogs(row)}
                                            disabled={!canPost || !p03Active || state.preview?.state !== "eligible" || busy === "post-cogs:" + row.id}
                                            className="min-h-9 rounded-lg bg-orange-800 px-3 text-xs font-black text-white disabled:opacity-40"
                                        >
                                            اعتماد COGS
                                        </button>
                                    </div>
                                )}
                                {state.preview?.facts && (
                                    <div className="mt-2 text-xs font-black text-orange-900">
                                        تكلفة البضاعة المباعة {formatMoney(state.preview.facts.total_cost)} · تاريخ القيد هو تاريخ الاعتراف بالبيع
                                    </div>
                                )}
                                <div className="mt-2"><PreviewState preview={state.preview} /></div>
                            </div>
                        );
                    })}
                    {!(workspace.inventory_consumptions || []).length && (
                        <div className="rounded-xl bg-white p-4 text-xs font-bold text-orange-700">
                            لا توجد أحداث استهلاك مخزون من Fulfillment V2 حتى الآن.
                        </div>
                    )}
                </div>
            </section>

            <section className="rounded-2xl border border-sky-200 bg-sky-50/50 p-5">
                <h2 className="text-lg font-black text-sky-950">فواتير المورد من مسار التجهيز والخدمات</h2>
                <p className="mt-1 text-xs font-semibold leading-6 text-sky-900">
                    هذه ليست شراء مخزون؛ هي تكلفة مورد مرتبطة بقطع وتجهيزات مستلمة. بعد P01 لا يسمح لمسار التشغيل بكتابة القيد مباشرة، وتنتظر اعتماد المحاسب هنا.
                </p>
                <div className="mt-4 space-y-3">
                    {(workspace.supplier_invoices || []).map((row) => {
                        const state = supplierState[row.id] || {};
                        const posted = Boolean(row.ledger_txn_group_id);
                        return (
                            <div key={row.id} className="rounded-xl border border-sky-200 bg-white p-3">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <div>
                                        <div className="font-black text-slate-900">{row.invoice_number || row.id} · {(row.supplier_snapshot || {}).company_name || row.supplier_id}</div>
                                        <div className="mt-1 text-[11px] font-semibold text-slate-500">إجمالي {formatMoney((row.total_halalas || 0) / 100)} · {String(row.approved_at || "").slice(0, 16)}</div>
                                    </div>
                                    {posted && <span className="rounded-full bg-emerald-50 px-3 py-1 text-[10px] font-black text-emerald-800">مرحّل</span>}
                                </div>
                                {!posted && (
                                    <div className="mt-3 grid gap-2 md:grid-cols-[1fr_auto_auto]">
                                        <input value={state.reason || ""} onChange={(e) => updateSupplier(row.id, { reason: e.target.value })} placeholder="سبب اعتماد فاتورة المورد" className="min-h-9 rounded-lg border border-slate-200 px-2 text-xs" />
                                        <button type="button" onClick={() => previewSupplierInvoice(row)} disabled={busy === "preview-supplier:" + row.id} className="min-h-9 rounded-lg border border-sky-300 px-3 text-xs font-black text-sky-900">معاينة</button>
                                        <button type="button" onClick={() => postSupplierInvoice(row)} disabled={!canPost || !p03Active || state.preview?.state !== "eligible" || busy === "post-supplier:" + row.id} className="min-h-9 rounded-lg bg-sky-800 px-3 text-xs font-black text-white disabled:opacity-40">اعتماد الفاتورة</button>
                                    </div>
                                )}
                                <div className="mt-2"><PreviewState preview={state.preview} /></div>
                            </div>
                        );
                    })}
                    {!(workspace.supplier_invoices || []).length && <div className="rounded-xl bg-white p-4 text-xs font-bold text-sky-700">لا توجد فواتير مورد من مسار التجهيز.</div>}
                </div>
            </section>
        </div>
    );
}
