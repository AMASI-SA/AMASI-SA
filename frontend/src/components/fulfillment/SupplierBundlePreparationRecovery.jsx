import { useLayoutEffect, useRef, useState } from "react";
import { useOptionalAuth } from "../../context/AuthContext";
import {
    inspectSupplierBundlePreparation,
    listPendingSupplierBundlePreparations,
    notifySupplierBundlePreparationCommitted,
    retrySupplierBundlePreparation,
} from "../../services/supplierBundlePreparation";

export default function SupplierBundlePreparationRecovery({ onCommitted, onNotApplied, disabled = false, onBusyChange }) {
    const auth = useOptionalAuth();
    const accountKey = JSON.stringify([auth?.authStatus, auth?.user?.id, auth?.user?.created_by,
        auth?.user?.role, auth?.user?.is_owner]);
    const accountReady = auth?.authStatus === "authenticated" && Boolean(auth.user?.id);
    const mounted = useRef(false);
    const generation = useRef(0);
    const busyRef = useRef(false);
    const context = useRef(null);
    context.current = { accountKey, accountReady, disabled, onCommitted, onNotApplied, onBusyChange };
    const [opened, setOpened] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [scope, setScope] = useState("");
    const [requests, setRequests] = useState([]);
    const [states, setStates] = useState({});

    useLayoutEffect(() => {
        const wasBusy = busyRef.current;
        mounted.current = true; generation.current += 1; busyRef.current = false;
        setBusy(false); setError(""); setOpened(false); setScope(""); setRequests([]); setStates({});
        if (wasBusy && context.current.accountKey === accountKey) context.current.onBusyChange?.(false);
        return () => { mounted.current = false; generation.current += 1; };
    }, [accountKey, disabled]);

    const run = async (action) => {
        if (!mounted.current || busyRef.current || disabled || !accountReady) return;
        const current = ++generation.current;
        const isCurrent = () => mounted.current && current === generation.current
            && context.current.accountKey === accountKey && context.current.accountReady && !context.current.disabled;
        busyRef.current = true; setBusy(true); setError("");
        if (isCurrent()) context.current.onBusyChange?.(true);
        try { if (isCurrent()) await action(isCurrent); }
        catch (cause) {
            if (isCurrent()) setError(cause?.message || "تعذّر التحقق من المحاولة. احتفظ بالمحاولة الأصلية وأعد التحقق لاحقًا.");
        } finally {
            if (isCurrent()) { busyRef.current = false; setBusy(false); context.current.onBusyChange?.(false); }
        }
    };
    const open = () => run(async (isCurrent) => {
        const result = await listPendingSupplierBundlePreparations();
        if (!isCurrent()) return;
        setScope(result.sessionScope); setRequests(result.requests); setStates({}); setOpened(true);
    });
    const check = (row, retry = false) => run(async (isCurrent) => {
        const action = retry ? retrySupplierBundlePreparation : inspectSupplierBundlePreparation;
        let result;
        try { result = await action({ sessionScope: scope, clientRequestId: row.clientRequestId, isCurrent }); }
        catch (cause) {
            if (!isCurrent()) return;
            if (cause?.notApplied === true) {
                setRequests((current) => current.filter((item) => item.clientRequestId !== row.clientRequestId));
                if (isCurrent()) context.current.onNotApplied?.();
            }
            throw cause;
        }
        if (!isCurrent()) return;
        if (result.state === "COMMITTED") {
            notifySupplierBundlePreparationCommitted(result.batch);
            if (!isCurrent()) return;
            setRequests((current) => current.filter((item) => item.clientRequestId !== row.clientRequestId));
            if (isCurrent()) await context.current.onCommitted?.(result.batch, isCurrent);
        } else {
            setStates((current) => ({ ...current, [row.clientRequestId]: result.state }));
        }
    });

    return <div className="rounded-2xl border border-slate-200 bg-white p-4" data-testid="supplier-bundle-preparation-recovery">
        <button type="button" onClick={open} disabled={busy || disabled || !accountReady}
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
