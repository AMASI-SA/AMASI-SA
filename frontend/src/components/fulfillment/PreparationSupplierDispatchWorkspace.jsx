import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    Check,
    CheckCircle,
    Minus,
    Package,
    PaperPlaneTilt,
    Plus,
    SpinnerGap,
    Storefront,
    UserSwitch,
    WarningCircle,
    XCircle,
} from "@phosphor-icons/react";

import {
    getPreparationSupplierWorkspace,
    getUnassignedPreparationPieces,
    markSupplierDispatchReady,
    newPreparationDispatchRequestId,
    reassignPreparationPieces,
    rejectPreparationPieces,
    sendPreparationPiecesToSupplier,
} from "../../services/preparationSupplierDispatch";

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

function SummaryCard({ value, label, tone = "slate" }) {
    const styles = {
        violet: "border-violet-200 bg-violet-50 text-violet-950",
        amber: "border-amber-200 bg-amber-50 text-amber-950",
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-950",
        slate: "border-slate-200 bg-slate-50 text-slate-950",
    };
    return (
        <div className={`rounded-2xl border p-3 ${styles[tone]}`}>
            <div className="text-2xl font-black tabular-nums">{Number(value || 0)}</div>
            <div className="mt-1 text-[11px] font-extrabold">{label}</div>
        </div>
    );
}

function ProductImage({ product }) {
    return product?.selected_image_url ? (
        <img src={product.selected_image_url} alt="" className="h-16 w-16 rounded-xl border border-slate-200 object-cover" />
    ) : (
        <div className="flex h-16 w-16 items-center justify-center rounded-xl bg-slate-100 text-slate-400"><Package size={24} /></div>
    );
}

function QuantityControl({ product, value, onChange }) {
    const available = Number(product?.available_quantity || 0);
    const current = Math.max(0, Math.min(available, Number(value || 0)));
    const set = (next) => onChange(Math.max(0, Math.min(available, Number(next || 0))));
    return (
        <div className="flex items-center gap-1" dir="ltr">
            <button type="button" onClick={() => set(current - 1)} className="flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white" aria-label="إنقاص الكمية"><Minus size={15} /></button>
            <input type="number" inputMode="numeric" min="0" max={available} value={current} onChange={(event) => set(event.target.value)} className="h-9 w-16 rounded-lg border border-slate-200 text-center font-black outline-none focus:border-violet-500" />
            <button type="button" onClick={() => set(current + 1)} className="flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white" aria-label="زيادة الكمية"><Plus size={15} /></button>
            <button type="button" onClick={() => set(available)} className="h-9 rounded-lg bg-slate-900 px-2 text-[11px] font-black text-white">كامل</button>
        </div>
    );
}

function NewFilesView({ data, loading, error, onRefresh, onChanged }) {
    const files = (data?.files || []).filter((file) => file.is_new);
    const suppliers = Array.isArray(data?.suppliers) ? data.suppliers : [];
    const [selected, setSelected] = useState({});
    const [supplierByFile, setSupplierByFile] = useState({});
    const [busyFile, setBusyFile] = useState("");
    const [actionError, setActionError] = useState("");

    const fileSelection = (file) => dispatchSelections(
        file.products,
        selected[file.file_number] || {},
    );
    const setQuantity = (fileNumber, groupKey, quantity) => {
        setSelected((current) => ({
            ...current,
            [fileNumber]: {
                ...(current[fileNumber] || {}),
                [groupKey]: quantity,
            },
        }));
    };
    const resetFile = (fileNumber) => {
        setSelected((current) => ({ ...current, [fileNumber]: {} }));
    };

    const send = async (file) => {
        const selections = fileSelection(file);
        const supplierId = supplierByFile[file.file_number] || "";
        if (!selections.length || !supplierId || busyFile) return;
        setBusyFile(file.file_number);
        setActionError("");
        try {
            await sendPreparationPiecesToSupplier({
                client_request_id: newPreparationDispatchRequestId(),
                file_number: file.file_number,
                supplier_id: supplierId,
                selections,
                note: null,
            });
            resetFile(file.file_number);
            await onChanged();
        } catch (sendError) {
            setActionError(sendError.message || "تعذّر رفع المنتجات إلى المورد.");
        } finally {
            setBusyFile("");
        }
    };

    const reject = async (file) => {
        const selections = fileSelection(file);
        if (!selections.length || busyFile) return;
        setBusyFile(file.file_number);
        setActionError("");
        try {
            await rejectPreparationPieces({
                client_request_id: newPreparationDispatchRequestId("preparation-reject"),
                file_number: file.file_number,
                selections,
                reason: "ليس من اختصاص الموظف",
            });
            resetFile(file.file_number);
            await onChanged();
        } catch (rejectError) {
            setActionError(rejectError.message || "تعذّر رفض المنتجات.");
        } finally {
            setBusyFile("");
        }
    };

    if (loading && !data) {
        return <div className="flex min-h-48 items-center justify-center gap-2 font-black text-violet-700"><SpinnerGap className="animate-spin" /> جارٍ تحميل الملفات الجديدة…</div>;
    }
    return (
        <div className="space-y-5" data-testid="preparation-new-employee-files">
            <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                <SummaryCard value={data?.summary?.new_files} label="ملفات جديدة لم تُرفع" tone="violet" />
                <SummaryCard value={data?.summary?.available_to_send} label="قطع بانتظار المورد" tone="amber" />
                <SummaryCard value={data?.summary?.sent} label="مرسلة للمورد" />
                <SummaryCard value={data?.summary?.ready} label="جاهزة للاستلام" tone="emerald" />
            </div>
            <div className="flex items-center justify-between gap-3">
                <div><h3 className="text-lg font-black text-slate-950">الملفات الجديدة المسندة إليّ</h3><p className="mt-1 text-xs font-bold text-slate-500">اختر المنتج والكمية ثم المورد. الرفض ينقل القطع للمدير دون حذفها.</p></div>
                <button type="button" onClick={onRefresh} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 px-3 text-xs font-black"><ArrowClockwise className={loading ? "animate-spin" : ""} />تحديث</button>
            </div>
            {(error || actionError) && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">{actionError || error}</div>}
            {!files.length && !error ? (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-9 text-center"><CheckCircle size={36} className="mx-auto text-emerald-600" /><div className="mt-3 font-black text-slate-800">لا توجد ملفات جديدة بانتظار الرفع للمورد</div></div>
            ) : (
                <div className="space-y-4">
                    {files.map((file) => {
                        const selections = fileSelection(file);
                        const selectedQuantity = selections.reduce((sum, row) => sum + row.quantity, 0);
                        return (
                            <article key={file.file_number} className="overflow-hidden rounded-2xl border border-violet-200 bg-white shadow-sm">
                                <header className="bg-violet-50 p-4">
                                    <div className="flex flex-wrap items-start justify-between gap-3">
                                        <div><div className="flex flex-wrap items-center gap-2"><h4 className="font-black text-slate-950">{file.file_title || file.file_number}</h4><span className="rounded-full bg-white px-2.5 py-1 text-xs font-black text-violet-700">{file.file_number}</span><span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-black text-amber-800">جديد لم يُرفع</span></div><div className="mt-2 text-xs font-bold text-slate-600">{file.available_quantity} قطعة متاحة · {file.sent_quantity} مرسلة · {file.ready_quantity} جاهزة</div></div>
                                        <div className="rounded-xl bg-white px-3 py-2 text-xs font-black text-violet-900">المحدد: {selectedQuantity} قطعة</div>
                                    </div>
                                </header>
                                <div className="divide-y divide-slate-100">
                                    {(file.products || []).filter((product) => product.available_quantity > 0).map((product) => {
                                        const value = selected[file.file_number]?.[product.group_key] || 0;
                                        return (
                                            <div key={product.group_key} className={`grid gap-3 p-4 sm:grid-cols-[72px_minmax(0,1fr)_auto] sm:items-center ${value > 0 ? "bg-violet-50/40" : ""}`}>
                                                <ProductImage product={product} />
                                                <div className="min-w-0"><div className="font-black text-slate-900">{product.product_name}</div><div className="mt-1 text-xs font-bold text-slate-500">{product.sku || "بدون SKU"} · المتاح {product.available_quantity}</div><div className="mt-2 flex flex-wrap gap-1">{(product.services || []).filter((service) => service.status !== "completed").map((service) => <span key={service.service_id} className="rounded-full bg-amber-50 px-2 py-1 text-[10px] font-black text-amber-800">{service.service_name || "خدمة"}</span>)}</div></div>
                                                <QuantityControl product={product} value={value} onChange={(quantity) => setQuantity(file.file_number, product.group_key, quantity)} />
                                            </div>
                                        );
                                    })}
                                </div>
                                <footer className="grid gap-3 border-t border-slate-100 bg-slate-50 p-4 lg:grid-cols-[minmax(220px,1fr)_auto_auto]">
                                    <select value={supplierByFile[file.file_number] || ""} onChange={(event) => setSupplierByFile((current) => ({ ...current, [file.file_number]: event.target.value }))} className="min-h-12 rounded-xl border border-slate-200 bg-white px-3 text-sm font-black outline-none focus:border-violet-500">
                                        <option value="">اختر المورد للقطع المحددة</option>
                                        {suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.company_name}</option>)}
                                    </select>
                                    <button type="button" onClick={() => reject(file)} disabled={!selections.length || busyFile === file.file_number} className="inline-flex min-h-12 items-center justify-center gap-2 rounded-xl border border-rose-200 bg-white px-4 text-sm font-black text-rose-700 disabled:opacity-50"><XCircle size={20} />رفض: ليس من اختصاصي</button>
                                    <button type="button" onClick={() => send(file)} disabled={!selections.length || !supplierByFile[file.file_number] || busyFile === file.file_number} className="inline-flex min-h-12 items-center justify-center gap-2 rounded-xl bg-violet-700 px-5 text-sm font-black text-white disabled:opacity-50">{busyFile === file.file_number ? <SpinnerGap className="animate-spin" /> : <PaperPlaneTilt size={20} weight="fill" />}رفع للمورد</button>
                                </footer>
                            </article>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

function SupplierAccountsView({ data, loading, error, onRefresh, onChanged }) {
    const accounts = Array.isArray(data?.supplier_accounts) ? data.supplier_accounts : [];
    const [busy, setBusy] = useState("");
    const [actionError, setActionError] = useState("");
    const markReady = async (dispatchId) => {
        if (!dispatchId || busy) return;
        setBusy(dispatchId);
        setActionError("");
        try {
            await markSupplierDispatchReady(dispatchId, "أكد المورد جاهزية القطع");
            await onChanged();
        } catch (readyError) {
            setActionError(readyError.message || "تعذّر تأكيد الجاهزية.");
        } finally {
            setBusy("");
        }
    };
    if (loading && !data) return <div className="flex min-h-48 items-center justify-center gap-2 font-black text-emerald-700"><SpinnerGap className="animate-spin" /> جارٍ تحميل حسابات الموردين…</div>;
    return (
        <div className="space-y-5" data-testid="preparation-supplier-accounts">
            <div className="flex items-center justify-between gap-3"><div><h3 className="text-lg font-black text-slate-950">حسابات الموردين التشغيلية</h3><p className="mt-1 text-xs font-bold text-slate-500">يعرض ما رفعته لكل مورد وما أكّد جاهزيته وما تم استلامه.</p></div><button type="button" onClick={onRefresh} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 px-3 text-xs font-black"><ArrowClockwise className={loading ? "animate-spin" : ""} />تحديث</button></div>
            {(error || actionError) && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">{actionError || error}</div>}
            {!accounts.length && !error ? <div className="rounded-2xl border border-dashed border-slate-300 p-9 text-center"><Storefront size={36} className="mx-auto text-slate-400" /><div className="mt-3 font-black text-slate-800">لم ترفع قطعًا إلى مورد حتى الآن</div></div> : (
                <div className="grid gap-4 xl:grid-cols-2">
                    {accounts.map((account) => (
                        <article key={account.supplier_id} className="overflow-hidden rounded-2xl border border-emerald-200 bg-white shadow-sm">
                            <header className="bg-emerald-50 p-4"><div className="flex items-center gap-3"><span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-emerald-700 text-white"><Storefront size={24} /></span><div><h4 className="font-black text-slate-950">{account.supplier_name}</h4><p className="mt-1 text-xs font-bold text-emerald-800">حساب تشغيلي داخل ميزان</p></div></div><div className="mt-4 grid grid-cols-3 gap-2"><SummaryCard value={account.sent_quantity} label="مرسلة" /><SummaryCard value={account.ready_quantity} label="جاهزة" tone="emerald" /><SummaryCard value={account.received_quantity} label="مستلمة" tone="violet" /></div></header>
                            <div className="divide-y divide-slate-100">{(account.products || []).map((product) => <div key={product.group_key} className="flex items-center gap-3 p-4"><ProductImage product={product} /><div className="min-w-0 flex-1"><div className="font-black text-slate-900">{product.product_name}</div><div className="mt-1 text-xs font-bold text-slate-500">مرسل {product.sent_quantity} · جاهز {product.ready_quantity} · مستلم {product.received_quantity}</div></div></div>)}</div>
                            {(account.dispatches || []).some((dispatch) => dispatch.status === "sent") && <footer className="space-y-2 border-t border-slate-100 bg-slate-50 p-3">{account.dispatches.filter((dispatch) => dispatch.status === "sent").map((dispatch) => <div key={dispatch.id} className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-3"><div className="text-xs font-bold text-slate-600"><b className="text-slate-900">{dispatch.file_number}</b> · {dispatch.piece_count} قطعة</div><button type="button" onClick={() => markReady(dispatch.id)} disabled={busy === dispatch.id} className="inline-flex min-h-10 items-center gap-2 rounded-xl bg-emerald-700 px-3 text-xs font-black text-white disabled:opacity-50">{busy === dispatch.id ? <SpinnerGap className="animate-spin" /> : <Check size={17} />}تأكيد أن المورد جهّزها</button></div>)}</footer>}
                        </article>
                    ))}
                </div>
            )}
        </div>
    );
}

function UnassignedManagerView({ onChanged }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [employeeByGroup, setEmployeeByGroup] = useState({});
    const [busy, setBusy] = useState("");
    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try { setData(await getUnassignedPreparationPieces()); }
        catch (loadError) { setError(loadError.message || "تعذّر تحميل غير المسندة."); }
        finally { setLoading(false); }
    }, []);
    useEffect(() => { load(); }, [load]);
    const assign = async (item) => {
        const key = `${item.file_number}:${item.group_key}`;
        const employeeId = employeeByGroup[key] || "";
        if (!employeeId || busy) return;
        setBusy(key);
        setError("");
        try {
            await reassignPreparationPieces({ client_request_id: newPreparationDispatchRequestId("preparation-reassign"), piece_ids: item.piece_ids, responsible_employee_id: employeeId, note: `إعادة إسناد من الملف ${item.file_number}` });
            await Promise.all([load(), onChanged()]);
        } catch (assignError) { setError(assignError.message || "تعذّر إعادة الإسناد."); }
        finally { setBusy(""); }
    };
    if (loading && !data) return <div className="flex min-h-48 items-center justify-center gap-2 font-black text-violet-700"><SpinnerGap className="animate-spin" /> جارٍ تحميل المنتجات غير المسندة…</div>;
    const items = data?.items || [];
    return (
        <div className="space-y-5" data-testid="preparation-unassigned-manager-queue">
            <div className="grid grid-cols-2 gap-3"><SummaryCard value={data?.summary?.unassigned_products} label="منتجات غير مسندة" tone="amber" /><SummaryCard value={data?.summary?.unassigned_pieces} label="إجمالي القطع" tone="violet" /></div>
            <div className="flex items-center justify-between"><div><h3 className="text-lg font-black text-slate-950">منتجات لم تُسند إلى موظف</h3><p className="mt-1 text-xs font-bold text-slate-500">ظهرت هنا لأن الموظف رفضها قبل رفعها للمورد.</p></div><button type="button" onClick={load} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 px-3 text-xs font-black"><ArrowClockwise className={loading ? "animate-spin" : ""} />تحديث</button></div>
            {error && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">{error}</div>}
            {!items.length && !error ? <div className="rounded-2xl border border-dashed border-slate-300 p-9 text-center"><CheckCircle size={36} className="mx-auto text-emerald-600" /><div className="mt-3 font-black text-slate-800">لا توجد منتجات غير مسندة</div></div> : <div className="space-y-3">{items.map((item) => { const key = `${item.file_number}:${item.group_key}`; return <article key={key} className="grid gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 lg:grid-cols-[72px_minmax(0,1fr)_minmax(220px,320px)_auto] lg:items-center"><ProductImage product={item} /><div><div className="font-black text-slate-950">{item.product_name}</div><div className="mt-1 text-xs font-bold text-slate-600">{item.file_number} · {item.quantity} قطعة · رفضها {item.rejected_by_employee_name || "موظف"}</div><div className="mt-1 text-xs font-black text-rose-700">{item.rejection_reason}</div></div><select value={employeeByGroup[key] || ""} onChange={(event) => setEmployeeByGroup((current) => ({ ...current, [key]: event.target.value }))} className="min-h-11 rounded-xl border border-amber-200 bg-white px-3 text-sm font-black"><option value="">اختر الموظف الجديد</option>{(data?.employees || []).map((employee) => <option key={employee.id} value={employee.id}>{employee.name}</option>)}</select><button type="button" onClick={() => assign(item)} disabled={!employeeByGroup[key] || busy === key} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 text-sm font-black text-white disabled:opacity-50">{busy === key ? <SpinnerGap className="animate-spin" /> : <UserSwitch size={19} />}إعادة الإسناد</button></article>; })}</div>}
        </div>
    );
}

export default function PreparationSupplierDispatchWorkspace({ view = "new-files", onDataChanged = async () => {} }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try { setData(await getPreparationSupplierWorkspace({ limit: 200 })); }
        catch (loadError) { setError(loadError.message || "تعذّر تحميل دورة المورد."); }
        finally { setLoading(false); }
    }, []);
    useEffect(() => { if (view !== "unassigned") load(); }, [load, view]);
    const changed = useCallback(async () => { await Promise.all([load(), onDataChanged()]); }, [load, onDataChanged]);
    const content = useMemo(() => {
        if (view === "supplier-accounts") return <SupplierAccountsView data={data} loading={loading} error={error} onRefresh={load} onChanged={changed} />;
        if (view === "unassigned") return <UnassignedManagerView onChanged={onDataChanged} />;
        return <NewFilesView data={data} loading={loading} error={error} onRefresh={load} onChanged={changed} />;
    }, [changed, data, error, load, loading, onDataChanged, view]);
    return content;
}
