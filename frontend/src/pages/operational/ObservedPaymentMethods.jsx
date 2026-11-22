import React, { useEffect, useMemo, useRef, useState } from "react";
import { todaySA } from "../../lib/dates";
import { paymentFeeRows, policyTax } from "./paymentFeeDirectory";
import { saveObservedPaymentMethodCommission } from "../../services/accountingOnboarding";
import { ResourceIcon } from './ApprovedFinanceUI';

const fields = [
    ["commission_percent", "نسبة العمولة %", 100],
    ["fixed_fee", "الرسم الثابت بالريال", undefined],
    ["vat_percent", "ضريبة العمولة %", 100],
];
const empty = [];
const blank = { commission_percent: "", fixed_fee: "", vat_percent: "" };
const valueText = value => value == null ? "—" : String(value);

export default function ObservedPaymentMethods({ setup, onSaved, policies = empty, sourceMode, onEditPolicy, onNewPolicy, savingPolicy = false }) {
    const [editing, setEditing] = useState(null);
    const [draft, setDraft] = useState(blank);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [message, setMessage] = useState("");
    const formRef = useRef(null), firstInputRef = useRef(null), deepLinkUsed = useRef(false);
    const items = setup?.items || empty;
    const today = todaySA();
    const rows = useMemo(() => paymentFeeRows(items, policies, sourceMode, today), [items, policies, sourceMode, today]);
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
        const row = rows.find(item => item.names.includes(name));
        if (row?.scope) {
            deepLinkUsed.current = true;
            if (row.policy) onEditPolicy(row.policy);
            else document.getElementById("fee-" + row.method.key)?.focus();
            return;
        }
        const method = row?.method;
        if (method?.editable === false) return;
        if (method) {
            deepLinkUsed.current = true;
            setEditing({ key: method.key, name: method.name, revision: setup.revision });
            setDraft(Object.fromEntries(fields.map(([key]) => [key,
                method.status === "configured" && method[key] != null ? String(method[key]) : "",
            ])));
        }
    }, [setup, canEdit, rows, onEditPolicy]);
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
        <div className="fee-directory-heading"><div><h2>طرق الدفع وعمولاتها</h2>
            <p>النسبة من قيمة الطلب، والرسم الثابت لكل طلب. الضريبة على العمولة.</p></div>
            {onNewPolicy && <button className="ofp-button" type="button" disabled={savingPolicy} onClick={() => onNewPolicy()}>+ إضافة تعرفة</button>}</div>
        <div className="space-y-3 p-3">
            {setup.complete !== true && <p role="alert" className="text-amber-900">تعذر التحقق من جميع طرق الدفع. أعد قراءة الصفحة قبل تعديل العمولات.</p>}
            {rows.length ? <div className="fee-directory"><table>
                <thead><tr><th>اسم طريقة الدفع</th><th>مصدر العمولة</th><th>النسبة %</th><th>مبلغ ثابت (ر.س)</th><th>الضريبة على العمولة %</th><th>الإجراء</th></tr></thead>
                <tbody>{rows.map(row => {
                    const {method, policy} = row;
                    const stored = method.status === "configured";
                    const values = policy ? [policy.percentage, policy.fixed_amount, policyTax(policy)]
                        : fields.map(([key]) => !row.scope && stored ? valueText(method[key]) : "—");
                    return <tr key={row.key} id={"fee-" + method.key} tabIndex={-1}>
                        <th scope="row"><span className="mz-resource-label"><ResourceIcon size="sm" resource={{kind:'provider',
                            id: row.scope?.payment_method || row.scope?.provider || method.fee_scope?.payment_method || method.fee_scope?.provider || method.inherits_from || method.name,
                            name: row.label}}/><bdi>{row.label}</bdi></span>
                            {row.names.some(name => name !== row.label) && <small>{row.names.map(name => <bdi key={name}>{name}</bdi>)}</small>}</th>
                        <td className="fee-source">{policy ? <>تعرفة ميزان 2<small>من <bdi>{policy.effective_from}</bdi></small></>
                            : row.scope ? row.policies.length ? "أكثر من تعرفة أو خطة" : "تحتاج تعرفة سارية"
                            : stored ? "مكتملة · إعدادات الدفع" : "العمولة غير محددة"}</td>
                        {values.map((value, index) => <td key={fields[index][0]} data-label={fields[index][1]}>
                            <span className="fee-value"><bdi>{value}</bdi></span></td>)}
                        <td className="fee-action">{row.scope ?
                            policy ? <button type="button" className="ofp-button" disabled={savingPolicy} onClick={() => onEditPolicy(policy)}>تعديل التعرفة</button>
                            : row.policies.length ? <details><summary>عرض التعرفات</summary>{row.policies.map(p =>
                                <button key={p.id} className="ofp-button" type="button" disabled={savingPolicy} onClick={() => onEditPolicy(p)}>
                                    {p.plan_id || p.payment_method || "كل الخطط"} · <bdi>{p.percentage}% + {p.fixed_amount}</bdi>
                                </button>)}</details>
                            : <button type="button" className="ofp-button" disabled={savingPolicy} onClick={() => onNewPolicy(row.scope)}>إضافة تعرفة</button>
                            : method.editable === false
                                ? <span>{stored ? "تستخدم العمولة المحفوظة" : "تحتاج مراجعة إعداداتها"}</span>
                                : <button type="button" className="ofp-button" disabled={busy || !canEdit} onClick={() => select(method)}
                                    aria-label={`${stored ? "تعديل" : "إضافة"} عمولة ${method.name}`}>{stored ? "تعديل العمولة" : "إضافة العمولة"}</button>}</td>
                    </tr>;
                })}</tbody>
            </table></div> : setup.complete === true && <p>لا توجد طرق دفع واردة في الطلبات.</p>}
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
