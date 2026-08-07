import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    ArrowRight,
    Buildings,
    Camera,
    CheckCircle,
    ClipboardText,
    Factory,
    FileText,
    MagnifyingGlass,
    Minus,
    Package,
    Plus,
    Printer,
    SpinnerGap,
    Storefront,
    UserSwitch,
    WarningCircle,
    X,
} from "@phosphor-icons/react";

import {
    getPreparationSupplierWorkspace,
    getUnassignedPreparationPieces,
    newPreparationDispatchRequestId,
    reassignPreparationPieces,
    rejectPreparationPieces,
    sendPreparationPiecesToSupplier,
} from "../../services/preparationSupplierDispatch";
import { printSupplierDispatch } from "./supplierDispatchPrint";

export function dispatchSelections(products = [], selected = {}) {
    return products
        .map((product) => ({
            group_key: String(product?.group_key || ""),
            quantity: Math.max(0, Math.min(
                Number(product?.available_quantity || 0),
                Number(selected?.[product?.group_key] || 0),
            )),
        }))
        .filter((row) => row.group_key && row.quantity > 0);
}

function SummaryCard({ value, label, detail, tone = "slate", onClick }) {
    const styles = {
        violet: "border-violet-200 bg-violet-50 text-violet-950",
        amber: "border-amber-200 bg-amber-50 text-amber-950",
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-950",
        slate: "border-slate-200 bg-slate-50 text-slate-950",
    };
    const Tag = onClick ? "button" : "div";
    return (
        <Tag
            type={onClick ? "button" : undefined}
            onClick={onClick}
            className={`min-h-[112px] rounded-2xl border p-3 text-right ${styles[tone]} ${onClick ? "transition hover:-translate-y-0.5 hover:shadow-md" : ""}`}
        >
            <div className="flex items-start justify-between gap-2">
                <div className="text-3xl font-black tabular-nums">{Number(value || 0)}</div>
                {onClick && <ArrowRight size={18} className="rotate-180 opacity-60" />}
            </div>
            <div className="mt-1 text-xs font-black">{label}</div>
            {detail && <div className="mt-1 text-[10px] font-bold leading-5 opacity-70">{detail}</div>}
        </Tag>
    );
}

function ProductImage({ product, compact = false }) {
    const size = compact ? "h-12 w-12" : "h-20 w-full";
    return product?.selected_image_url ? (
        <img src={product.selected_image_url} alt="" className={`${size} rounded-xl border border-slate-200 object-cover`} />
    ) : (
        <div className={`flex ${size} items-center justify-center rounded-xl bg-slate-100 text-slate-400`}><Package size={24} /></div>
    );
}

function QuantityControl({ product, value, onChange }) {
    const available = Number(product?.available_quantity || 0);
    const current = Math.max(0, Math.min(available, Number(value || 0)));
    const set = (next) => onChange(Math.max(0, Math.min(available, Number(next || 0))));
    return (
        <div className="grid grid-cols-[34px_minmax(0,1fr)_34px] gap-1" dir="ltr">
            <button type="button" onClick={() => set(current - 1)} className="flex h-9 items-center justify-center rounded-lg border border-slate-200 bg-white" aria-label="إنقاص الكمية"><Minus size={14} /></button>
            <input type="number" inputMode="numeric" min="0" max={available} value={current} onChange={(event) => set(event.target.value)} className="h-9 min-w-0 rounded-lg border border-slate-200 text-center text-sm font-black outline-none focus:border-violet-500" aria-label={`كمية ${product?.product_name || "المنتج"}`} />
            <button type="button" onClick={() => set(current + 1)} className="flex h-9 items-center justify-center rounded-lg border border-slate-200 bg-white" aria-label="زيادة الكمية"><Plus size={14} /></button>
            <button type="button" onClick={() => set(available)} className="col-span-3 h-8 rounded-lg bg-slate-900 px-2 text-[10px] font-black text-white">اختيار كامل الكمية</button>
        </div>
    );
}

function SectionHeader({ title, description, onBack, onRefresh, loading }) {
    return (
        <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
                {onBack && <button type="button" onClick={onBack} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-white" aria-label="العودة إلى إدارة منتجاتي"><ArrowRight size={19} /></button>}
                <div className="min-w-0"><h3 className="text-lg font-black text-slate-950">{title}</h3><p className="mt-1 text-xs font-bold leading-5 text-slate-500">{description}</p></div>
            </div>
            {onRefresh && <button type="button" onClick={onRefresh} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-black"><ArrowClockwise className={loading ? "animate-spin" : ""} />تحديث</button>}
        </div>
    );
}

function ReturnAssignmentDialog({ target, reason, onReasonChange, busy, onCancel, onConfirm }) {
    if (!target) return null;
    const valid = String(reason || "").trim().length >= 3;
    return (
        <div className="fixed inset-0 z-[120] flex items-end justify-center bg-slate-950/60 p-3 sm:items-center" role="dialog" aria-modal="true" aria-label="إرجاع إسناد المنتج للمدير">
            <div className="w-full max-w-lg rounded-2xl bg-white p-5 shadow-2xl" dir="rtl">
                <div className="flex items-start justify-between gap-3"><div><h3 className="text-lg font-black text-slate-950">إرجاع الإسناد للمدير</h3><p className="mt-1 text-xs font-bold leading-5 text-slate-500">سيُعاد كامل المتبقي من {target.product.product_name} وعدده {target.product.available_quantity} قطعة.</p></div><button type="button" onClick={onCancel} disabled={busy} className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-100"><X size={18} /></button></div>
                <label className="mt-4 block text-sm font-black text-slate-800">سبب إلغاء الإسناد <span className="text-rose-600">*</span><textarea value={reason} onChange={(event) => onReasonChange(event.target.value)} rows={4} maxLength={1000} placeholder="اكتب السبب بوضوح ليتمكن المدير من إعادة إسناده للموظف المناسب" className="mt-2 w-full resize-none rounded-xl border border-slate-200 p-3 text-sm font-bold outline-none focus:border-rose-500" /></label>
                {!valid && <div className="mt-1 text-xs font-bold text-rose-600">كتابة السبب إلزامية.</div>}
                <div className="mt-4 grid grid-cols-2 gap-2"><button type="button" onClick={onCancel} disabled={busy} className="min-h-11 rounded-xl border border-slate-200 font-black">إلغاء</button><button type="button" onClick={onConfirm} disabled={!valid || busy} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-rose-700 font-black text-white disabled:opacity-50">{busy ? <SpinnerGap className="animate-spin" /> : <UserSwitch size={19} />}إرجاع للمدير</button></div>
            </div>
        </div>
    );
}

export function WaitingReviewView({ data, loading, error, onRefresh, onChanged, onBack }) {
    const files = (data?.files || []).filter((file) => file.available_quantity > 0);
    const suppliers = Array.isArray(data?.suppliers) ? data.suppliers : [];
    const [selected, setSelected] = useState({});
    const [supplierByFile, setSupplierByFile] = useState({});
    const [busyFile, setBusyFile] = useState("");
    const [actionError, setActionError] = useState("");
    const [notice, setNotice] = useState("");
    const [returnTarget, setReturnTarget] = useState(null);
    const [returnReason, setReturnReason] = useState("");

    const fileSelection = (file) => dispatchSelections(file.products, selected[file.file_number] || {});
    const setQuantity = (fileNumber, groupKey, quantity) => setSelected((current) => ({ ...current, [fileNumber]: { ...(current[fileNumber] || {}), [groupKey]: quantity } }));
    const resetFile = (fileNumber) => setSelected((current) => ({ ...current, [fileNumber]: {} }));

    const send = async (file) => {
        const selections = fileSelection(file);
        const supplierId = supplierByFile[file.file_number] || "";
        if (!selections.length || !supplierId || busyFile) return;
        const printWindow = globalThis.window?.open?.("", "_blank") || null;
        setBusyFile(file.file_number);
        setActionError("");
        setNotice("");
        try {
            const response = await sendPreparationPiecesToSupplier({
                client_request_id: newPreparationDispatchRequestId(),
                file_number: file.file_number,
                supplier_id: supplierId,
                selections,
                note: null,
            });
            const printed = printSupplierDispatch(response.dispatch, printWindow);
            resetFile(file.file_number);
            setSupplierByFile((current) => ({ ...current, [file.file_number]: "" }));
            setNotice(printed ? "تم حفظ ملف المورد وفتح نافذة الطباعة." : "تم حفظ ملف المورد، لكن المتصفح منع نافذة الطباعة. يمكنك إعادة طباعته من قيد التنفيذ.");
            await onChanged();
        } catch (sendError) {
            printWindow?.close?.();
            setActionError(sendError.message || "تعذّر حفظ ملف المورد.");
        } finally {
            setBusyFile("");
        }
    };

    const confirmReturn = async () => {
        if (!returnTarget || String(returnReason).trim().length < 3 || busyFile) return;
        const { file, product } = returnTarget;
        setBusyFile(file.file_number);
        setActionError("");
        try {
            await rejectPreparationPieces({
                client_request_id: newPreparationDispatchRequestId("preparation-return"),
                file_number: file.file_number,
                selections: [{ group_key: product.group_key, quantity: Number(product.available_quantity || 0) }],
                reason: returnReason.trim(),
            });
            setReturnTarget(null);
            setReturnReason("");
            await onChanged();
        } catch (rejectError) {
            setActionError(rejectError.message || "تعذّر إرجاع الإسناد للمدير.");
        } finally {
            setBusyFile("");
        }
    };

    return (
        <div className="space-y-5" data-testid="preparation-waiting-review-products">
            <SectionHeader title="بانتظار المراجعة" description="المنتجات المسندة إليك ولم تُرسل إلى مورد بعد." onBack={onBack} onRefresh={onRefresh} loading={loading} />
            {(error || actionError) && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">{actionError || error}</div>}
            {notice && <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm font-bold text-emerald-900">{notice}</div>}
            {!files.length && !error ? <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-9 text-center"><CheckCircle size={36} className="mx-auto text-emerald-600" /><div className="mt-3 font-black text-slate-800">لا توجد منتجات بانتظار الإرسال للمورد</div></div> : (
                <div className="space-y-4">
                    {files.map((file) => {
                        const selections = fileSelection(file);
                        const selectedQuantity = selections.reduce((sum, row) => sum + row.quantity, 0);
                        return (
                            <article key={file.file_number} className="overflow-hidden rounded-2xl border border-violet-200 bg-white shadow-sm">
                                <header className="bg-violet-50 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex flex-wrap items-center gap-2"><h4 className="font-black text-slate-950">{file.file_title || file.file_number}</h4><span className="rounded-full bg-white px-2.5 py-1 text-xs font-black text-violet-700">{file.file_number}</span></div><div className="mt-2 text-xs font-bold text-slate-600">{file.available_quantity} قطعة بانتظار الإرسال · {file.sent_quantity} قيد التنفيذ</div></div><div className="rounded-xl bg-white px-3 py-2 text-xs font-black text-violet-900">المحدد: {selectedQuantity} قطعة</div></div></header>
                                <div className="grid grid-cols-2 gap-2 p-2 sm:gap-3 sm:p-4 lg:grid-cols-3 xl:grid-cols-4">
                                    {(file.products || []).filter((product) => product.available_quantity > 0).map((product) => {
                                        const value = selected[file.file_number]?.[product.group_key] || 0;
                                        return (
                                            <article key={product.group_key} className={`min-w-0 rounded-2xl border p-2.5 ${value > 0 ? "border-violet-400 bg-violet-50/60 ring-2 ring-violet-100" : "border-slate-200 bg-white"}`}>
                                                <ProductImage product={product} />
                                                <div className="mt-2 min-w-0"><div className="line-clamp-2 min-h-10 text-xs font-black leading-5 text-slate-900 sm:text-sm">{product.product_name}</div><div className="mt-1 truncate text-[10px] font-bold text-slate-500">{product.sku || "بدون SKU"} · {product.available_quantity} قطعة</div></div>
                                                <div className="mt-2 flex min-h-6 flex-wrap gap-1">{(product.services || []).filter((service) => service.status !== "completed").slice(0, 2).map((service) => <span key={service.service_id} className="rounded-full bg-amber-50 px-1.5 py-1 text-[9px] font-black text-amber-800">{service.service_name || "خدمة"}</span>)}</div>
                                                <div className="mt-2"><QuantityControl product={product} value={value} onChange={(quantity) => setQuantity(file.file_number, product.group_key, quantity)} /></div>
                                                <button type="button" onClick={() => { setReturnTarget({ file, product }); setReturnReason(""); }} className="mt-2 inline-flex min-h-9 w-full items-center justify-center gap-1 rounded-lg border border-rose-200 bg-white px-2 text-[10px] font-black text-rose-700"><UserSwitch size={15} />إرجاع الإسناد</button>
                                            </article>
                                        );
                                    })}
                                </div>
                                <footer className="grid gap-3 border-t border-slate-100 bg-slate-50 p-4 lg:grid-cols-[minmax(220px,1fr)_auto]">
                                    <select value={supplierByFile[file.file_number] || ""} onChange={(event) => setSupplierByFile((current) => ({ ...current, [file.file_number]: event.target.value }))} className="min-h-12 rounded-xl border border-slate-200 bg-white px-3 text-sm font-black outline-none focus:border-violet-500"><option value="">اختر المورد للمنتجات المحددة</option>{suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.company_name}</option>)}</select>
                                    <button type="button" onClick={() => send(file)} disabled={!selections.length || !supplierByFile[file.file_number] || busyFile === file.file_number} className="inline-flex min-h-12 items-center justify-center gap-2 rounded-xl bg-violet-700 px-5 text-sm font-black text-white disabled:opacity-50">{busyFile === file.file_number ? <SpinnerGap className="animate-spin" /> : <Printer size={20} weight="fill" />}حفظ وطباعة ملف المورد</button>
                                </footer>
                            </article>
                        );
                    })}
                </div>
            )}
            <ReturnAssignmentDialog target={returnTarget} reason={returnReason} onReasonChange={setReturnReason} busy={Boolean(busyFile)} onCancel={() => { setReturnTarget(null); setReturnReason(""); }} onConfirm={confirmReturn} />
        </div>
    );
}

function InProgressView({ data, loading, error, onRefresh, onBack }) {
    const accounts = (data?.supplier_accounts || []).filter((account) => (account.sent_quantity + account.ready_quantity) > 0);
    return (
        <div className="space-y-5" data-testid="preparation-products-in-progress">
            <SectionHeader title="قيد التنفيذ" description="الموردون والمنتجات الموجودة لديهم حاليًا." onBack={onBack} onRefresh={onRefresh} loading={loading} />
            {error && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">{error}</div>}
            {!accounts.length && !error ? <div className="rounded-2xl border border-dashed border-slate-300 p-9 text-center"><Storefront size={36} className="mx-auto text-slate-400" /><div className="mt-3 font-black text-slate-800">لا توجد منتجات عند الموردين حاليًا</div></div> : <div className="grid gap-4 xl:grid-cols-2">{accounts.map((account) => {
                const currentProducts = (account.products || []).filter((product) => (product.sent_quantity + product.ready_quantity) > 0);
                return <article key={account.supplier_id} className="overflow-hidden rounded-2xl border border-amber-200 bg-white shadow-sm"><header className="bg-amber-50 p-4"><div className="flex items-center gap-3"><span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-amber-700 text-white"><Storefront size={24} /></span><div className="min-w-0 flex-1"><h4 className="truncate font-black text-slate-950">{account.supplier_name}</h4><p className="mt-1 text-xs font-bold text-amber-800">{account.sent_quantity + account.ready_quantity} قطعة قيد التنفيذ</p></div></div></header><div className="grid grid-cols-2 gap-2 p-3">{currentProducts.map((product) => <div key={product.group_key} className="rounded-xl border border-slate-200 p-2"><div className="flex items-center gap-2"><ProductImage product={product} compact /><div className="min-w-0"><div className="line-clamp-2 text-xs font-black text-slate-900">{product.product_name}</div><div className="mt-1 text-[10px] font-bold text-slate-500">{product.sent_quantity + product.ready_quantity} قطعة</div></div></div></div>)}</div><footer className="space-y-2 border-t border-slate-100 bg-slate-50 p-3">{(account.dispatches || []).filter((dispatch) => ["sent", "ready"].includes(dispatch.status)).map((dispatch) => <div key={dispatch.id} className="flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white p-3"><div className="min-w-0 text-xs font-bold text-slate-600"><b className="text-slate-900">{dispatch.file_number}</b> · {dispatch.piece_count} قطعة</div><button type="button" onClick={() => printSupplierDispatch(dispatch)} className="inline-flex min-h-9 shrink-0 items-center gap-1 rounded-lg border border-violet-200 px-2 text-[11px] font-black text-violet-700"><Printer size={16} />إعادة الطباعة</button></div>)}</footer></article>;
            })}</div>}
        </div>
    );
}

function ReceivedView({ data, loading, error, onRefresh, onBack }) {
    const accounts = (data?.supplier_accounts || []).filter((account) => account.received_quantity > 0);
    return (
        <div className="space-y-5" data-testid="preparation-products-received">
            <SectionHeader title="تم الاستلام" description="ما استلمه موظف التجهيز من المورد ولم يُسلّمه لموظف الاستلام بالفرع." onBack={onBack} onRefresh={onRefresh} loading={loading} />
            <div className="grid grid-cols-2 gap-3"><SummaryCard value={data?.summary?.received_orders_awaiting_branch_handoff} label="الطلبات" detail="بانتظار التسليم للفرع" tone="emerald" /><SummaryCard value={data?.summary?.received_pieces_awaiting_branch_handoff} label="القطع المستلمة" detail="لم تُسلّم للفرع" tone="violet" /></div>
            {error && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">{error}</div>}
            {!accounts.length && !error ? <div className="rounded-2xl border border-dashed border-slate-300 p-9 text-center"><CheckCircle size={36} className="mx-auto text-emerald-600" /><div className="mt-3 font-black text-slate-800">لا توجد قطع مستلمة بانتظار التسليم للفرع</div></div> : <div className="grid gap-4 xl:grid-cols-2">{accounts.map((account) => <article key={account.supplier_id} className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4"><h4 className="font-black text-slate-950">{account.supplier_name}</h4><div className="mt-1 text-xs font-bold text-emerald-800">{account.received_quantity} قطعة مستلمة</div></article>)}</div>}
            <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold leading-6 text-amber-900"><WarningCircle size={19} className="mt-0.5 shrink-0" />إنقاص هذا العدد سيتم فقط في مرحلة تسليم القطع لموظف الاستلام بالفرع بالباركود، وهي بوابة التنفيذ التالية.</div>
        </div>
    );
}

export function MyProductsOverview({ data, onOpen }) {
    const [search, setSearch] = useState("");
    const normalizedSearch = search.trim();
    const files = Array.isArray(data?.files) ? data.files : [];
    const supplierAccounts = Array.isArray(data?.supplier_accounts)
        ? data.supplier_accounts
        : [];
    const matches = normalizedSearch
        ? files.flatMap((file) => (file.products || [])
            .filter((product) => (product.order_numbers || [])
                .some((order) => String(order).includes(normalizedSearch)))
            .map((product) => ({ ...product, file_number: file.file_number })))
        : [];
    const latestFiles = files.slice(0, 3);
    const activeSupplierAccounts = supplierAccounts
        .filter((account) => (
            Number(account?.sent_quantity || 0)
            + Number(account?.ready_quantity || 0)
            + Number(account?.received_quantity || 0)
        ) > 0)
        .slice(0, 3);
    const supplierInvoiceCount = supplierAccounts.reduce(
        (total, account) => total + (Array.isArray(account?.dispatches) ? account.dispatches.length : 0),
        0,
    );

    const summaryCards = [
        {
            key: "waiting",
            label: "بانتظار المراجعة",
            value: data?.summary?.waiting_review_products,
            detail: "منتجًا لم يُرسل للمورد",
            Icon: ClipboardText,
            tone: "amber",
            onClick: () => onOpen("waiting-review"),
        },
        {
            key: "progress",
            label: "قيد التنفيذ",
            value: data?.summary?.in_progress_products,
            detail: "قطعة لدى الموردين",
            Icon: ArrowClockwise,
            tone: "blue",
            onClick: () => onOpen("in-progress"),
        },
        {
            key: "received",
            label: "تم الاستلام",
            value: data?.summary?.received_orders_awaiting_branch_handoff,
            detail: "طلبًا لم يُسلّم للفرع",
            Icon: CheckCircle,
            tone: "emerald",
            onClick: () => onOpen("received"),
        },
        {
            key: "total",
            label: "إجمالي القطع المسندة",
            value: data?.summary?.total_assigned_pieces,
            detail: "قطعة في جميع الحالات",
            Icon: ClipboardText,
            tone: "green",
        },
    ];

    const summaryTone = {
        amber: {
            value: "text-amber-600",
            icon: "bg-amber-50 text-amber-600",
            hover: "hover:border-amber-200 hover:bg-amber-50/30",
        },
        blue: {
            value: "text-blue-600",
            icon: "bg-blue-50 text-blue-600",
            hover: "hover:border-blue-200 hover:bg-blue-50/30",
        },
        emerald: {
            value: "text-emerald-700",
            icon: "bg-emerald-50 text-emerald-700",
            hover: "hover:border-emerald-200 hover:bg-emerald-50/30",
        },
        green: {
            value: "text-emerald-800",
            icon: "bg-emerald-50 text-emerald-800",
            hover: "",
        },
    };

    const fileProgress = (file) => {
        const total = Math.max(0, Number(file?.piece_count || 0));
        const received = Math.max(0, Number(file?.received_quantity || 0));
        return total ? Math.min(100, Math.round((received / total) * 100)) : 0;
    };

    const fileStatus = (file) => {
        const total = Number(file?.piece_count || 0);
        const received = Number(file?.received_quantity || 0);
        const inProgress = Number(file?.sent_quantity || 0) + Number(file?.ready_quantity || 0);
        if (total > 0 && received >= total) {
            return { label: "تم الاستلام", className: "text-emerald-700" };
        }
        if (inProgress > 0 || received > 0) {
            return { label: "قيد التنفيذ", className: "text-blue-600" };
        }
        return { label: "بانتظار المراجعة", className: "text-amber-600" };
    };

    const SupplierIcon = ({ index }) => {
        const icons = [Buildings, Storefront, Factory];
        const Icon = icons[index % icons.length];
        return <Icon size={31} weight="duotone" />;
    };

    const SectionTitle = ({ children }) => (
        <div className="flex items-center gap-2">
            <span className="h-6 w-1 rounded-full bg-amber-500" aria-hidden="true" />
            <h3 className="text-base font-black text-slate-950 sm:text-lg">{children}</h3>
        </div>
    );

    return (
        <div className="mx-auto w-full max-w-[1180px] space-y-6 bg-white pb-4" data-testid="preparation-my-products-overview">
            <header className="text-center">
                <h1 className="text-3xl font-black tracking-tight text-emerald-800 sm:text-4xl">إدارة منتجاتي</h1>
                <p className="mt-1 text-xs font-bold text-slate-500 sm:text-base">إدارة المنتجات المسندة لك ومتابعة الموردين</p>
            </header>

            <section className="space-y-3" aria-labelledby="my-products-summary-title">
                <div id="my-products-summary-title"><SectionTitle>ملخص العمل العام</SectionTitle></div>
                <div className="grid grid-cols-2 gap-2.5 sm:gap-4 lg:grid-cols-4">
                    {summaryCards.map((card) => {
                        const Tag = card.onClick ? "button" : "div";
                        const tone = summaryTone[card.tone];
                        return (
                            <Tag
                                key={card.key}
                                type={card.onClick ? "button" : undefined}
                                onClick={card.onClick}
                                className={`min-h-[112px] rounded-xl border border-slate-200 bg-white p-3 text-right shadow-sm transition sm:min-h-[128px] sm:p-4 ${card.onClick ? tone.hover : ""}`}
                                data-testid={`my-products-summary-${card.key}`}
                            >
                                <div className="flex items-start justify-between gap-2">
                                    <div className="min-w-0">
                                        <div className="text-[11px] font-black leading-5 text-slate-900 sm:text-sm">{card.label}</div>
                                        <div className={`mt-0.5 text-3xl font-black tabular-nums sm:text-4xl ${tone.value}`}>{Number(card.value || 0)}</div>
                                    </div>
                                    <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full sm:h-12 sm:w-12 ${tone.icon}`}><card.Icon size={24} weight="duotone" /></span>
                                </div>
                                <div className="mt-1 text-[10px] font-bold leading-5 text-slate-500 sm:text-xs">{card.detail}</div>
                            </Tag>
                        );
                    })}
                </div>
            </section>

            <section className="grid grid-cols-2 gap-2.5 sm:gap-4" aria-label="إجراءات إدارة منتجاتي">
                <button type="button" onClick={() => onOpen("waiting-review")} className="flex min-h-[112px] items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-3 text-right shadow-sm transition hover:border-amber-200 hover:bg-amber-50/30 sm:min-h-[120px] sm:p-5" data-testid="my-products-open-waiting-review">
                    <div><div className="text-sm font-black text-slate-950 sm:text-xl">بانتظار المراجعة</div><div className="mt-1 text-[10px] font-bold text-slate-500 sm:text-sm">{Number(data?.summary?.waiting_review_products || 0)} قطعة غير مرسلة</div></div>
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-amber-50 text-amber-600 sm:h-14 sm:w-14"><ClipboardText size={28} weight="duotone" /></span>
                </button>
                <a href="/fulfillment-v2?stage=preparation" className="flex min-h-[112px] items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-3 text-right shadow-sm transition hover:border-emerald-200 hover:bg-emerald-50/30 sm:min-h-[120px] sm:p-5" data-testid="my-products-receive-from-supplier">
                    <div><div className="text-sm font-black text-slate-950 sm:text-xl">استلام من المورد</div><div className="mt-1 text-[10px] font-bold text-slate-500 sm:text-sm">مسح باركود وفتح الفاتورة</div></div>
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-emerald-50 text-emerald-700 sm:h-14 sm:w-14"><Camera size={28} weight="fill" /></span>
                </a>
                <div className="min-h-[112px] rounded-xl border border-slate-200 bg-white p-3 text-right shadow-sm sm:min-h-[120px] sm:p-5" data-testid="my-products-order-search">
                    <div className="flex items-center justify-between gap-3">
                        <div className="text-sm font-black text-slate-950 sm:text-xl">البحث برقم الطلب</div>
                        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-emerald-50 text-emerald-700 sm:h-12 sm:w-12"><MagnifyingGlass size={26} /></span>
                    </div>
                    <label className="mt-2 flex min-h-9 items-center gap-2 rounded-lg border border-slate-200 px-2 focus-within:border-emerald-500 sm:min-h-11">
                        <input value={search} onChange={(event) => setSearch(event.target.value)} inputMode="numeric" placeholder="أدخل رقم الطلب" className="min-w-0 flex-1 bg-transparent text-xs font-bold outline-none sm:text-sm" aria-label="البحث برقم الطلب" />
                        <Camera size={18} className="shrink-0 text-slate-700" aria-hidden="true" />
                    </label>
                </div>
                <a href="/fulfillment-v2?stage=preparation" className="flex min-h-[112px] items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-3 text-right shadow-sm transition hover:border-emerald-200 hover:bg-emerald-50/30 sm:min-h-[120px] sm:p-5" data-testid="my-products-supplier-invoices">
                    <div><div className="text-sm font-black text-slate-950 sm:text-xl">فواتير الموردين</div><div className="mt-1 text-[10px] font-black text-emerald-700 sm:text-sm">{supplierInvoiceCount} فاتورة</div></div>
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-emerald-50 text-emerald-700 sm:h-14 sm:w-14"><FileText size={28} weight="fill" /></span>
                </a>
            </section>

            {normalizedSearch && (
                <section className="rounded-xl border border-emerald-100 bg-emerald-50/30 p-3" aria-live="polite">
                    <div className="mb-2 text-xs font-black text-emerald-900">نتائج البحث</div>
                    {matches.length ? <div className="grid gap-2 sm:grid-cols-2">{matches.map((product) => <div key={`${product.file_number}:${product.group_key}`} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-3"><ProductImage product={product} compact /><div className="min-w-0"><div className="text-sm font-black text-slate-950">{product.product_name}</div><div className="mt-1 text-[11px] font-bold text-slate-500">ملف {product.file_number} · متاح {product.available_quantity} · عند المورد {Number(product.sent_quantity || 0) + Number(product.ready_quantity || 0)} · مستلم {product.received_quantity}</div></div></div>)}</div> : <div className="text-xs font-bold text-slate-500">لا توجد منتجات مسندة إليك لهذا الطلب.</div>}
                </section>
            )}

            <section id="latest-preparation-files" className="space-y-3">
                <SectionTitle>آخر ملفات التجهيز</SectionTitle>
                {latestFiles.length ? (
                    <div className="grid grid-cols-3 gap-2 sm:gap-4">
                        {latestFiles.map((file) => {
                            const progress = fileProgress(file);
                            const status = fileStatus(file);
                            return (
                                <article key={file.file_number} className="min-w-0 rounded-xl border border-slate-200 bg-white p-2.5 shadow-sm sm:p-4" data-testid="my-products-latest-file">
                                    <div className="truncate text-[10px] font-black text-slate-950 sm:text-sm">{file.file_title || file.file_number}</div>
                                    <div className="mt-1 text-[9px] font-bold text-slate-600 sm:text-xs">{Number(file.piece_count || 0)} قطعة</div>
                                    <div className="mt-1 text-[9px] font-bold text-slate-500 sm:text-xs">{Number(file.received_quantity || 0)} مستلمة</div>
                                    <div className="mt-2 flex items-center gap-2" dir="ltr"><span className="text-[9px] font-black text-slate-700 sm:text-xs">{progress}%</span><span className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-slate-200"><span className="block h-full rounded-full bg-emerald-700" style={{ width: `${progress}%` }} /></span></div>
                                    <div className={`mt-2 text-[9px] font-black sm:text-xs ${status.className}`}>{status.label}</div>
                                </article>
                            );
                        })}
                    </div>
                ) : <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm font-bold text-slate-500">لا توجد ملفات مسندة إليك.</div>}
            </section>

            <section className="space-y-3">
                <SectionTitle>حالة الموردين</SectionTitle>
                {activeSupplierAccounts.length ? (
                    <div className="grid grid-cols-3 gap-2 sm:gap-4">
                        {activeSupplierAccounts.map((account, index) => {
                            const activePieces = Number(account.sent_quantity || 0) + Number(account.ready_quantity || 0);
                            return (
                                <article key={account.supplier_id} className="flex min-w-0 items-center justify-between gap-2 rounded-xl border border-emerald-100 bg-emerald-50/50 p-2.5 sm:p-4" data-testid="my-products-supplier-status">
                                    <div className="min-w-0"><div className="truncate text-[9px] font-black text-slate-950 sm:text-sm">{account.supplier_name}</div><div className="mt-1 text-lg font-black tabular-nums text-emerald-800 sm:text-2xl">{activePieces}</div></div>
                                    <span className="shrink-0 text-emerald-700"><SupplierIcon index={index} /></span>
                                </article>
                            );
                        })}
                    </div>
                ) : <div className="rounded-xl border border-dashed border-slate-300 p-5 text-center text-xs font-bold text-slate-500">لا توجد منتجات نشطة لدى الموردين حاليًا.</div>}
            </section>
        </div>
    );
}

function UnassignedManagerView({ onChanged }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [employeeByGroup, setEmployeeByGroup] = useState({});
    const [busy, setBusy] = useState("");
    const load = useCallback(async () => { setLoading(true); setError(""); try { setData(await getUnassignedPreparationPieces()); } catch (loadError) { setError(loadError.message || "تعذّر تحميل غير المسندة."); } finally { setLoading(false); } }, []);
    useEffect(() => { load(); }, [load]);
    const assign = async (item) => {
        const key = `${item.file_number}:${item.group_key}`;
        const employeeId = employeeByGroup[key] || "";
        if (!employeeId || busy) return;
        setBusy(key); setError("");
        try { await reassignPreparationPieces({ client_request_id: newPreparationDispatchRequestId("preparation-reassign"), piece_ids: item.piece_ids, responsible_employee_id: employeeId, note: `إعادة إسناد من الملف ${item.file_number}` }); await Promise.all([load(), onChanged()]); } catch (assignError) { setError(assignError.message || "تعذّر إعادة الإسناد."); } finally { setBusy(""); }
    };
    if (loading && !data) return <div className="flex min-h-48 items-center justify-center gap-2 font-black text-violet-700"><SpinnerGap className="animate-spin" /> جارٍ تحميل المنتجات غير المسندة…</div>;
    const items = data?.items || [];
    return <div className="space-y-5" data-testid="preparation-unassigned-manager-queue"><div className="grid grid-cols-2 gap-3"><SummaryCard value={data?.summary?.unassigned_products} label="منتجات غير مسندة" tone="amber" /><SummaryCard value={data?.summary?.unassigned_pieces} label="إجمالي القطع" tone="violet" /></div><SectionHeader title="منتجات أعادها الموظفون" description="يحفظ سبب الإرجاع وسجل الإسناد السابق دون حذف." onRefresh={load} loading={loading} />{error && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">{error}</div>}{!items.length && !error ? <div className="rounded-2xl border border-dashed border-slate-300 p-9 text-center"><CheckCircle size={36} className="mx-auto text-emerald-600" /><div className="mt-3 font-black text-slate-800">لا توجد منتجات غير مسندة</div></div> : <div className="space-y-3">{items.map((item) => { const key = `${item.file_number}:${item.group_key}`; return <article key={key} className="grid gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 lg:grid-cols-[72px_minmax(0,1fr)_minmax(220px,320px)_auto] lg:items-center"><ProductImage product={item} compact /><div><div className="font-black text-slate-950">{item.product_name}</div><div className="mt-1 text-xs font-bold text-slate-600">{item.file_number} · {item.quantity} قطعة · أعادها {item.rejected_by_employee_name || "موظف"}</div><div className="mt-1 text-xs font-black text-rose-700">{item.rejection_reason}</div></div><select value={employeeByGroup[key] || ""} onChange={(event) => setEmployeeByGroup((current) => ({ ...current, [key]: event.target.value }))} className="min-h-11 rounded-xl border border-amber-200 bg-white px-3 text-sm font-black"><option value="">اختر الموظف الجديد</option>{(data?.employees || []).map((employee) => <option key={employee.id} value={employee.id}>{employee.name}</option>)}</select><button type="button" onClick={() => assign(item)} disabled={!employeeByGroup[key] || busy === key} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 text-sm font-black text-white disabled:opacity-50">{busy === key ? <SpinnerGap className="animate-spin" /> : <UserSwitch size={19} />}إعادة الإسناد</button></article>; })}</div>}</div>;
}

function EmployeeProductsWorkspace({ onDataChanged }) {
    const [data, setData] = useState(null);
    const [section, setSection] = useState("overview");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const load = useCallback(async () => { setLoading(true); setError(""); try { setData(await getPreparationSupplierWorkspace({ limit: 200 })); } catch (loadError) { setError(loadError.message || "تعذّر تحميل إدارة منتجاتي."); } finally { setLoading(false); } }, []);
    useEffect(() => { load(); }, [load]);
    const changed = useCallback(async () => { await Promise.all([load(), onDataChanged()]); }, [load, onDataChanged]);
    if (loading && !data) return <div className="flex min-h-48 items-center justify-center gap-2 font-black text-violet-700"><SpinnerGap className="animate-spin" /> جارٍ تحميل إدارة منتجاتي…</div>;
    if (section === "waiting-review") return <WaitingReviewView data={data} loading={loading} error={error} onRefresh={load} onChanged={changed} onBack={() => setSection("overview")} />;
    if (section === "in-progress") return <InProgressView data={data} loading={loading} error={error} onRefresh={load} onBack={() => setSection("overview")} />;
    if (section === "received") return <ReceivedView data={data} loading={loading} error={error} onRefresh={load} onBack={() => setSection("overview")} />;
    return <MyProductsOverview data={data} onOpen={setSection} />;
}

export default function PreparationSupplierDispatchWorkspace({ view = "my-products", onDataChanged = async () => {} }) {
    const content = useMemo(() => {
        if (view === "unassigned") return <UnassignedManagerView onChanged={onDataChanged} />;
        return <EmployeeProductsWorkspace onDataChanged={onDataChanged} />;
    }, [onDataChanged, view]);
    return content;
}
