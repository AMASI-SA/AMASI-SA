import React, { useEffect, useRef, useState } from "react";
import { saveObservedPaymentMethodCommission } from "../../services/accountingOnboarding";

const fields = [
    ["commission_percent", "نسبة العمولة %", 100],
    ["fixed_fee", "الرسم الثابت بالريال", undefined],
    ["vat_percent", "ضريبة العمولة %", 100],
];
const blank = { commission_percent: "", fixed_fee: "", vat_percent: "" };
const valueText = value => value == null ? "—" : String(value);

export default function ObservedPaymentMethods({ setup, onSaved }) {
    const [editing, setEditing] = useState(null);
    const [draft, setDraft] = useState(blank);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [message, setMessage] = useState("");
    const formRef = useRef(null), firstInputRef = useRef(null), deepLinkUsed = useRef(false);
    const items = setup?.items || [];
    const canEdit = setup?.complete === true && typeof setup.revision === "string" && Boolean(setup.revision);
    const selectedMethod = items.find(method => method.key === editing?.key);
    const selectedEditable = Boolean(selectedMethod) && selectedMethod.editable !== false;

    function select(method) {
        if (method.editable === false) return;
        setEditing({ key: method.key, name: method.name, revision: setup.revision });
        setDraft(Object.fromEntries(fields.map(([key]) => [key,
            method.status === "configured" && method[key] != null ? String(method[key]) : "",
        ])));
        setError(""); setMessage("");
    }
    useEffect(() => {
        if (!canEdit || deepLinkUsed.current || typeof window === "undefined") return;
        const name = new URLSearchParams(window.location.search).get("method");
        const method = items.find(item => item.name === name && item.editable !== false);
        if (method) {
            deepLinkUsed.current = true;
            setEditing({ key: method.key, name: method.name, revision: setup.revision });
            setDraft(Object.fromEntries(fields.map(([key]) => [key,
                method.status === "configured" && method[key] != null ? String(method[key]) : "",
            ])));
        }
    }, [setup, canEdit, items]);
    useEffect(() => {
        if (!editing) return;
        formRef.current?.scrollIntoView?.({ block: "nearest" });
        firstInputRef.current?.focus();
    }, [editing?.key]);

    async function save(event) {
        event.preventDefault();
        if (busy || !editing || !canEdit || !selectedEditable) return;
        const valid = event.currentTarget.checkValidity() && fields.every(([key, , maximum]) => draft[key].trim() !== ""
            && Number.isFinite(Number(draft[key])) && Number(draft[key]) >= 0
            && (maximum == null || Number(draft[key]) <= maximum));
        if (!valid) {
            setError("أكمل العمولة والرسم الثابت والضريبة. أدخل صفرًا لأي رسم لا ينطبق.");
            return;
        }
        setBusy(true); setError(""); setMessage("");
        try {
            const result = await saveObservedPaymentMethodCommission({
                name: editing.name, expected_revision: editing.revision,
                ...Object.fromEntries(fields.map(([key]) => [key, Number(draft[key])])),
            });
            const updated = result?.payment_method_setup;
            if (!Array.isArray(updated?.items) || !updated.items.some(method => method.key === editing.key
                && method.status === "configured") || typeof updated.revision !== "string") {
                throw new Error("payment_method_save_unverified");
            }
            onSaved(updated);
            setMessage(`حُفظت عمولة ${editing.name}.`);
            setEditing(null); setDraft(blank);
        } catch (failure) {
            setError(failure?.response?.status === 409
                ? "تغيّرت إعدادات طرق الدفع. أعد قراءة الصفحة قبل الحفظ؛ احتفظنا بمدخلاتك هنا."
                : "تعذر تأكيد حفظ العمولة. احتفظنا بمدخلاتك؛ حاول مرة أخرى.");
        } finally { setBusy(false); }
    }

    if (!setup) return null;
    return <section className="ofp-table-card" aria-label="طرق الدفع المكتشفة من الطلبات" data-testid="observed-payment-methods">
        <div className="ofp-table-title"><h2>طرق الدفع الواردة في الطلبات</h2></div>
        <div className="space-y-3 p-3">
            <p>تظهر طرق الدفع الجديدة هنا تلقائيًا. أضف عمولة كل طريقة لإكمال حساب تكاليف الطلبات والأرباح.</p>
            {setup.complete !== true && <p role="alert" className="text-amber-900">تعذر التحقق من جميع طرق الدفع. أعد قراءة الصفحة قبل تعديل العمولات.</p>}
            {items.length ? <div className="ofp-table-scroll"><table>
                <thead><tr><th>طريقة الدفع</th><th>الحالة</th><th>العمولة %</th><th>الرسم الثابت ر.س</th><th>الضريبة %</th><th>الإجراء</th></tr></thead>
                <tbody>{items.map(method => <tr key={method.key}>
                    <th scope="row"><bdi>{method.name}</bdi></th>
                    <td>{method.status === "configured" ? "مكتملة" : "العمولة غير محددة"}</td>
                    {fields.map(([key]) => <td key={key}><bdi>{method.status === "configured" ? valueText(method[key]) : "—"}</bdi></td>)}
                    <td>{method.editable === false
                        ? <span>{method.status === "configured" ? "تستخدم العمولة المحفوظة" : "تحتاج مراجعة إعداداتها"}</span>
                        : <button type="button" className="ofp-button" disabled={busy || !canEdit} onClick={() => select(method)}
                        aria-label={`${method.status === "configured" ? "تعديل" : "إضافة"} عمولة ${method.name}`}>
                        {method.status === "configured" ? "تعديل العمولة" : "إضافة العمولة"}
                    </button>}</td>
                </tr>)}</tbody>
            </table></div> : setup.complete === true && <p>لا توجد طرق دفع تحتاج إعدادًا.</p>}
            {editing && selectedEditable && <form ref={formRef} aria-label={`عمولة ${editing.name}`} onSubmit={save} className="rounded-lg border p-3">
                <fieldset disabled={busy || !canEdit} className="space-y-3">
                    <legend className="font-bold">عمولة <bdi>{editing.name}</bdi></legend>
                    <div className="grid gap-3 sm:grid-cols-3">{fields.map(([key, label, maximum], index) => <label key={key} className="block">{label}
                        <input ref={index === 0 ? firstInputRef : undefined} aria-label={label} type="number" required min="0" max={maximum} step={key === "fixed_fee" ? ".01" : "any"}
                            className="block w-full rounded border p-2" value={draft[key]}
                            onChange={event => setDraft(current => ({ ...current, [key]: event.target.value }))} />
                    </label>)}</div>
                    <p className="text-sm text-slate-600">تُضاف الضريبة إلى العمولة والرسم الثابت. أدخل صفرًا للرسوم التي لا تنطبق.</p>
                    <div className="flex gap-3"><button type="submit" className="ofp-button">{busy ? "جارٍ حفظ العمولة…" : "حفظ العمولة"}</button>
                        <button type="button" onClick={() => { setEditing(null); setError(""); }}>إلغاء</button></div>
                </fieldset>
            </form>}
            {error && <p role="alert" className="text-red-700">{error}</p>}
            {message && <p role="status" className="text-emerald-800">{message}</p>}
        </div>
    </section>;
}
