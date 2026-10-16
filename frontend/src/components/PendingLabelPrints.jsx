import { useEffect, useLayoutEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { pendingLabelPrint, pendingPrintScope, subscribePendingLabelPrint } from "../lib/openCurrentCarrierLabel";

export default function PendingLabelPrints() {
    const { user, loading, authStatus } = useAuth();
    const [jobs, setJobs] = useState([]);
    const scope = !loading && authStatus !== "unavailable" ? pendingPrintScope(user) : null;
    useLayoutEffect(() => { pendingLabelPrint.setScope(scope); setJobs(pendingLabelPrint.snapshot()); return () => pendingLabelPrint.setScope(null); }, [scope]);
    useEffect(() => subscribePendingLabelPrint(setJobs), []);
    useEffect(() => {
        const stop = () => pendingLabelPrint.setScope(null);
        window.addEventListener("mezan:auth-ending", stop);
        return () => window.removeEventListener("mezan:auth-ending", stop);
    }, []);
    if (!scope || !jobs.length) return null;
    return <aside dir="rtl" aria-label="طلبات طباعة الشحن" className="fixed bottom-3 left-3 z-50 max-h-64 w-80 overflow-auto rounded-xl border bg-white p-3 shadow-lg">
        {jobs.map(job => <div key={job.orderNumber} className="border-b py-2">
            <div role="status">طلب {job.orderNumber}: {job.message || "بانتظار رابط البوليصة؛ المتابعة مستمرة"}</div>
            {job.state !== "waiting" && <button type="button" className="m-2 underline" onClick={() => pendingLabelPrint.request(job.orderNumber, job.source).catch(() => {})}>
                {job.state === "ready" ? "فتح ملف الطباعة" : "إعادة المحاولة"}
            </button>}
            <button type="button" className="m-2 underline" onClick={() => pendingLabelPrint.cancel(job.orderNumber)}>إلغاء طلب الطباعة</button>
        </div>)}
    </aside>;
}
