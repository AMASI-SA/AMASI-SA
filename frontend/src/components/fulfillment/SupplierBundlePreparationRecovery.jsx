import { useState } from "react";
import {
    inspectSupplierBundlePreparation,
    listPendingSupplierBundlePreparations,
    notifySupplierBundlePreparationCommitted,
    retrySupplierBundlePreparation,
} from "../../services/supplierBundlePreparation";

export default function SupplierBundlePreparationRecovery({ onCommitted, onNotApplied, disabled = false, onBusyChange }) {
    const [opened, setOpened] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [scope, setScope] = useState("");
    const [requests, setRequests] = useState([]);
    const [states, setStates] = useState({});

    const run = async (action) => {
        if (busy || disabled) return;
        setBusy(true); setError(""); onBusyChange?.(true);
        try { await action(); }
        catch (cause) { setError(cause?.message || "تعذّر التحقق من المحاولة. احتفظ بالمحاولة الأصلية وأعد التحقق لاحقًا."); }
        finally { setBusy(false); onBusyChange?.(false); }
    };
    const open = () => run(async () => {
        const result = await listPendingSupplierBundlePreparations();
        setScope(result.sessionScope); setRequests(result.requests); setStates({}); setOpened(true);
    });
    const check = (row, retry = false) => run(async () => {
        const action = retry ? retrySupplierBundlePreparation : inspectSupplierBundlePreparation;
        let result;
        try { result = await action({ sessionScope: scope, clientRequestId: row.clientRequestId }); }
        catch (cause) {
            if (cause?.notApplied === true) {
                setRequests((current) => current.filter((item) => item.clientRequestId !== row.clientRequestId));
                onNotApplied?.();
            }
            throw cause;
        }
        if (result.state === "COMMITTED") {
            notifySupplierBundlePreparationCommitted(result.batch);
            setRequests((current) => current.filter((item) => item.clientRequestId !== row.clientRequestId));
            await onCommitted?.(result.batch);
        } else {
            setStates((current) => ({ ...current, [row.clientRequestId]: result.state }));
        }
    });

    return <div className="rounded-2xl border border-slate-200 bg-white p-4" data-testid="supplier-bundle-preparation-recovery">
        <button type="button" onClick={open} disabled={busy || disabled}
            className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-2 text-sm font-extrabold text-violet-900 disabled:opacity-50">
            {busy ? "جارٍ التحقق…" : "التحقق من محاولات البكج السابقة"}
        </button>
        {error && <p className="mt-3 text-sm font-bold text-rose-800" role="alert">{error}</p>}
        {opened && <div className="mt-3 space-y-3">
            <p className="text-xs leading-6 text-slate-600">إذا انقطع الاتصال أثناء إنشاء ملف بكج، تحقّق من المحاولة الأصلية هنا. التحقق يعرض النتيجة دون إنشاء ملف جديد.</p>
            {requests.length === 0 && <p className="text-sm font-bold text-slate-600">لا توجد محاولات معلقة محفوظة لهذا الحساب في هذا المتصفح.</p>}
            {requests.map((row) => <div key={row.clientRequestId} className="rounded-xl border border-amber-200 bg-amber-50 p-3">
                <h3 className="text-sm font-black text-amber-950">{row.fileTitle}</h3>
                <p className="mt-1 text-xs text-amber-900">{row.physicalQuantity} قطعة من {row.commercialQuantity} بطاقة</p>
                {states[row.clientRequestId] === "ABSENT" && <p className="mt-2 text-xs text-amber-900">لم يصل طلب إنشاء الملف. يمكنك إرسال المحاولة الأصلية المحفوظة.</p>}
                {states[row.clientRequestId] === "PENDING" && <p className="mt-2 text-xs text-amber-900">لم تكتمل نتيجة الملف بعد. يمكنك التحقق مجددًا أو إعادة المحاولة الأصلية.</p>}
                <div className="mt-3 flex flex-wrap gap-2">
                    <button type="button" onClick={() => check(row)} disabled={busy || disabled}
                        className="rounded-lg border border-amber-300 bg-white px-3 py-2 text-xs font-black disabled:opacity-50">التحقق من نتيجة الملف</button>
                    {["ABSENT", "PENDING"].includes(states[row.clientRequestId]) && <button type="button" onClick={() => check(row, true)} disabled={busy || disabled}
                        className="rounded-lg bg-amber-800 px-3 py-2 text-xs font-black text-white disabled:opacity-50">إعادة المحاولة الأصلية</button>}
                </div>
            </div>)}
        </div>}
    </div>;
}
