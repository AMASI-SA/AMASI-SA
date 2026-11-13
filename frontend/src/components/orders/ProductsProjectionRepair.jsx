import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../context/AuthContext";
import {
    applyOrderProductsProjectionRepair,
    previewOrderProductsProjectionRepair,
    productsProjectionRepairReason,
} from "../../services/orderEngine";

const number = value => Number(value).toLocaleString("en-US", { maximumFractionDigits: 6 });
const quantity = rows => rows.reduce((total, row) => total + Number(row.quantity), 0);

function Comparison({ summary }) {
    const before = summary.before || [];
    const after = summary.after || [];
    const oldLines = new Map(before.map(row => [row.order_item_id, row]));
    const newLines = new Map(after.map(row => [row.order_item_id, row]));
    const changes = [...new Set([...oldLines.keys(), ...newLines.keys()])].filter(id =>
        Number(oldLines.get(id)?.quantity || 0) !== Number(newLines.get(id)?.quantity || 0));

    return <div className="space-y-3" data-testid="products-repair-comparison">
        <div className="grid gap-3 sm:grid-cols-2">
            {[
                ["الأصناف المحتسبة حاليًا في التقارير", before],
                ["الأصناف الحالية المؤكدة من سلة", after],
            ].map(([label, rows]) => <div key={label} className="rounded-xl border border-slate-200 bg-white p-3">
                <h4 className="text-sm font-bold text-slate-800">{label}</h4>
                <p className="mt-2 text-sm text-slate-600">عدد البنود: <b className="num">{number(rows.length)}</b> · مجموع الكميات: <b className="num">{number(quantity(rows))}</b></p>
            </div>)}
        </div>
        {changes.length > 0 && <div className="max-h-64 overflow-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-right text-xs">
                <caption className="px-3 py-2 text-right font-bold text-slate-700">التغييرات المقترحة في احتساب أصناف هذا الطلب</caption>
                <thead className="bg-slate-100 text-slate-600"><tr>
                    <th className="p-3">معرّف البند في سلة</th><th className="p-3">الكمية المحتسبة</th><th className="p-3">الكمية المؤكدة</th><th className="p-3">التغيير</th>
                </tr></thead>
                <tbody>{changes.map(id => {
                    const oldLine = oldLines.get(id);
                    const newLine = newLines.get(id);
                    return <tr key={id} className="border-t border-slate-100">
                        <td className="p-3"><bdi className="num break-all">{id}</bdi></td>
                        <td className="num p-3">{oldLine ? number(oldLine.quantity) : "—"}</td>
                        <td className="num p-3">{newLine ? number(newLine.quantity) : "—"}</td>
                        <td className="p-3">{!newLine ? "استبعاد من التقارير" : !oldLine ? "إضافة إلى التقارير" : Number(newLine.quantity) < Number(oldLine.quantity) ? "تخفيض الكمية" : "زيادة الكمية"}</td>
                    </tr>;
                })}</tbody>
            </table>
        </div>}
        {!changes.length && summary.action === "replace_products" && <p className="text-sm text-slate-600">توجد فروق في بيانات الأصناف المحفوظة، مع بقاء عدد البنود والكميات كما هي.</p>}
    </div>;
}

function OrderProductsRepair({ orderNumber, disabled, onApplied }) {
    const [plan, setPlan] = useState(null);
    const [phase, setPhase] = useState("");
    const [error, setError] = useState("");
    const [message, setMessage] = useState("");
    const mounted = useRef(false);
    const request = useRef(null);

    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
            request.current = null;
        };
    }, []);

    const summary = plan?.orders?.[0];
    const canApply = plan?.ready === true && plan?.pending_orders === 1
        && summary?.action === "replace_products" && summary?.order_number === orderNumber
        && typeof plan?.manifest_sha === "string" && /^[0-9a-f]{64}$/.test(plan.manifest_sha);

    async function preview() {
        if (disabled || request.current) return;
        const current = {};
        request.current = current;
        const isCurrent = () => mounted.current && request.current === current;
        setPhase("preview");
        setPlan(null);
        setError("");
        setMessage("");
        try {
            const result = await previewOrderProductsProjectionRepair(orderNumber);
            if (isCurrent()) setPlan(result);
        } catch (failure) {
            if (isCurrent()) setError(failure?.message || "تعذّر فحص الأصناف. أعد الفحص قبل التطبيق.");
        } finally {
            if (isCurrent()) {
                request.current = null;
                setPhase("");
            }
        }
    }

    async function apply() {
        if (disabled || request.current || !canApply) return;
        const current = {};
        const reviewed = plan.manifest_sha;
        request.current = current;
        const isCurrent = () => mounted.current && request.current === current;
        setPhase("apply");
        // Consume the review before sending. Success, refusal and an unknown
        // outcome all require another explicit preview before a further write.
        setPlan(null);
        setError("");
        setMessage("");
        try {
            const result = await applyOrderProductsProjectionRepair(orderNumber, reviewed);
            if (!isCurrent()) return;
            setMessage(result.mode === "no_op"
                ? "الأصناف المحتسبة في التقارير مطابقة لأصناف سلة. لا يوجد تصحيح مطلوب."
                : "تم تصحيح الأصناف المحتسبة لهذا الطلب في التقارير. يمكنك إعادة الفحص للتحقق.");
            try {
                // The page supplies its existing local GET loader, never a
                // provider refresh or any cost/history recalculation.
                await onApplied?.();
            } catch {
                if (isCurrent()) setError("تم التصحيح، وتعذّر إعادة تحميل تفاصيل الطلب. أعد تحميل الصفحة أو افحص الأصناف من جديد.");
            }
        } catch (failure) {
            if (isCurrent()) setError(failure?.message || "تعذّر تأكيد نتيجة التصحيح. أعد فحص الأصناف قبل أي محاولة جديدة.");
        } finally {
            if (isCurrent()) {
                request.current = null;
                setPhase("");
            }
        }
    }

    return <div className="mb-4 space-y-3 rounded-xl border border-violet-200 bg-violet-50/50 p-4" data-testid="products-projection-repair" aria-busy={Boolean(phase)}>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
                <h3 className="text-sm font-extrabold text-slate-900">مطابقة أصناف التقارير لهذا الطلب</h3>
                <p className="mt-1 text-xs leading-6 text-slate-600">افحص الأصناف المحتسبة لهذا الطلب في التقارير وقارنها بأصناف سلة الحالية قبل التصحيح.</p>
            </div>
            <button type="button" onClick={preview} disabled={disabled || Boolean(phase)} data-testid="products-repair-preview"
                className="shrink-0 rounded-lg border border-violet-200 bg-white px-4 py-2 text-sm font-bold text-violet-800 hover:bg-violet-100 disabled:cursor-wait disabled:opacity-60">
                {phase === "preview" ? "جارٍ فحص الأصناف…" : "فحص أصناف سلة"}
            </button>
        </div>
        {phase === "apply" && <p role="status" className="text-sm font-bold text-violet-800">جارٍ تطبيق تصحيح الأصناف…</p>}
        {error && <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
        {message && <p role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">{message}</p>}
        {plan && <div className="space-y-3">
            {Array.isArray(summary?.before) && Array.isArray(summary?.after) && <Comparison summary={summary} />}
            {!plan.ready && <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                <p className="font-bold">لا يمكن تطبيق التصحيح الآن.</p>
                {[...new Set((summary?.reasons?.length ? summary.reasons : [null]).map(productsProjectionRepairReason))].map(reason => <p key={reason} className="mt-1">{reason}</p>)}
            </div>}
            {plan.ready && plan.pending_orders === 0 && <p role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">الأصناف المحتسبة في التقارير مطابقة للأصناف الحالية في سلة. لا يوجد تصحيح مطلوب.</p>}
            {canApply && <div className="flex flex-col items-start gap-3">
                <p className="text-xs leading-6 text-slate-600">يصحّح احتساب أصناف هذا الطلب في التقارير فقط، مع الحفاظ على التكاليف التاريخية والفواتير والتجهيز والمخزون.</p>
                <button type="button" onClick={apply} disabled={disabled || Boolean(phase)} data-testid="products-repair-apply"
                    className="rounded-lg bg-violet-700 px-4 py-2.5 text-sm font-bold text-white hover:bg-violet-800 disabled:cursor-wait disabled:opacity-60">
                    تطبيق تصحيح الأصناف
                </button>
            </div>}
        </div>}
    </div>;
}

export default function ProductsProjectionRepair({ orderNumber, disabled = false, onApplied }) {
    const { user, authStatus } = useAuth();
    const normalized = String(orderNumber || "").trim();
    if (authStatus !== "authenticated" || user?.role !== "owner"
        || typeof user?.id !== "string" || !user.id.trim() || !normalized
        || user._mobile_actor_id || user._session_client === "amasi_mobile"
        || user.disabled === true || user.is_active === false) return null;

    // A different order/account (including away-and-back navigation) remounts
    // this local workflow, discarding both the review and any late response.
    // Manual provider refresh also invalidates an older comparison.
    return <OrderProductsRepair key={JSON.stringify([normalized, user.id, disabled])}
        orderNumber={normalized} disabled={disabled} onApplied={onApplied} />;
}
