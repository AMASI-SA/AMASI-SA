import { accountFx } from "../currencyRules";
import CurrencyFields, { currencyChange } from "../CurrencyFields";
import React, { useState } from "react";
import { OpeningField } from "./OpeningCourierEditor";
import { advertisingBindingForRow, advertisingFieldsForBinding } from "./onboardingFinancialAdapter";

const inputClass = "mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3";
const ACCOUNT_LABELS = { bank: "بنك", cash: "صندوق", overdraft: "سحب على المكشوف", ad_prepaid_wallet: "محفظة إعلانية", ad_payable: "ذمة إعلانية" };
const FUNDING_LABELS = { prepaid: "مدفوع مقدمًا", postpaid: "دفع آجل", hybrid: "محفظة وذمة مستقلتان" };
export const DOMAIN_FIELDS = {
    banks: [["balance", "الرصيد الافتتاحي"]],
    providers: [["balance", "الرصيد المستحق لنا"]],
    employees: [["salary_payable", "راتب مستحق"], ["advance", "سلفة الموظف"], ["custody", "عهدة الموظف"]],
    suppliers: [["payable", "مستحق للمورد"], ["advance", "دفعة مقدمة للمورد"]],
    external_persons: [["receivable", "مستحق لنا على الطرف"]],
    drivers: [["cod_receivable", "COD في عهدة الموصل"], ["fee_payable", "أجرة مستحقة للموصل"]],
    advertising: [["prepaid_wallet", "محفظة مدفوعة مقدمًا"], ["payable", "مستحق للمنصة"]],
};
export function validateEntityRows(rows, fields, entities, banks = [], requireBank = false) {
    const errors = [];
    const seen = new Set();
    for (const row of rows) {
        if (!entities.some(entity => entity.id === row.entity_id) || seen.has(row.entity_id)) errors.push("اختر هوية حقيقية دون تكرار.");
        seen.add(row.entity_id);
        for (const [field] of fields) {
            if (!/^\d+(\.\d+)?$/.test(row[field] ?? "") || !Number.isFinite(Number(row[field]))) errors.push("أدخل كل مبلغ صراحة، بما فيه الصفر؛ لا تقبل القيم السالبة.");
        }
        if (!row.evidence_ref?.trim()) errors.push("دليل ناقص.");
        if (requireBank && !banks.some(bank => bank.id === row.settlement_bank_id)) errors.push("اختر بنك التسوية.");
    }
    return [...new Set(errors)];
}

export default function OpeningEntityEditor({ domain, value = [], onChange, entities = [], banks = [], financialAccounts = [], createExternalPerson, onEntityCreated }) {
    const fields = DOMAIN_FIELDS[domain] || [];
    const [person, setPerson] = useState({ name: "", phone: "", notes: "" });
    const [adding, setAdding] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [search, setSearch] = useState("");
    const [typeFilter, setTypeFilter] = useState("");
    const [currencyFilter, setCurrencyFilter] = useState("");
    const patch = (index, changes) => onChange(value.map((row, i) => i === index ? { ...row, ...changes } : row));
    const matchesSearch = entity => [entity.name, entity.label, entity.id, entity.currency, ACCOUNT_LABELS[entity.account_type], entity.platform].filter(Boolean).join(" ").toLocaleLowerCase().includes(search.trim().toLocaleLowerCase());
    const entityType = entity => entity.account_type || entity.funding_mode || entity.kind || "";
    const typeLabel = type => ACCOUNT_LABELS[type] || FUNDING_LABELS[type] || type;
    const types = [...new Set(entities.map(entityType).filter(Boolean))];
    const currencies = [...new Set(entities.map(entity => entity.currency).filter(Boolean))];
    const available = entities.filter(entity => matchesSearch(entity) && (!typeFilter || entityType(entity) === typeFilter) && (!currencyFilter || entity.currency === currencyFilter));
    const accountUsable = account => account.status === "active" && !["archived", "is_archived", "deleted", "is_deleted"].some(key => account[key] === true) && !["active", "is_active"].some(key => account[key] === false);
    function selectedEntityRow(entityId) {
        const entity = entities.find(item => item.id === entityId);
        const account = financialAccounts.find(item => item.id === entityId);
        const bindingFields = domain === "advertising" ? advertisingFieldsForBinding(entity) : [];
        return { entity_id: entityId, financial_account_id: "", prepaid_wallet_account_id: "", payable_account_id: "",
            evidence_file_id: "", binding_evidence_file_id: "", evidence_ref: "", account_fx: {}, settlement_bank_id: "", funding_account_id: "", funding_reference: "",
            ...currencyChange(domain === "banks" ? account?.currency || "" : domain === "advertising" ? "" : "SAR"),
            ...Object.fromEntries(fields.map(([field]) => [field, ""])),
            ...Object.fromEntries(bindingFields.map(([, field, , key]) => [field, entity[key]])),
            ...(bindingFields.length ? { account_fx: Object.fromEntries(bindingFields.map(([, , , key]) => [entity[key], currencyChange(financialAccounts.find(a => a.id === entity[key])?.currency || "")])) } : {}) };
    }
    function selectedAccount(row) {
        return financialAccounts.find(account => account.id === (row.entity_id || row.financial_account_id));
    }
    function chooseAccount(index, changes) {
        const id = Object.values(changes)[0];
        const account = financialAccounts.find(item => item.id === id);
        patch(index, { ...changes, account_fx: { ...value[index].account_fx, [id]: currencyChange(account?.currency || "") } });
    }
    async function create() {
        if (!person.name.trim() || !person.phone.trim()) { setError("الاسم والهاتف مطلوبان."); return; }
        setBusy(true); setError("");
        try {
            const created = await createExternalPerson(person);
            if (!created?.id) throw new Error("لم يُعد الخادم هوية الطرف.");
            onEntityCreated?.(created);
            onChange([...value, { entity_id: created.id, evidence_ref: "", receivable: "" }]);
            setAdding(false); setPerson({ name: "", phone: "", notes: "" });
        } catch (err) { setError("تعذر إنشاء الطرف. تحقق من الصلاحية والبيانات ثم أعد المحاولة."); }
        finally { setBusy(false); }
    }
    return <section dir="rtl" className="space-y-4">
        <p className="text-sm text-slate-600">اختر الجهة المكتشفة، ثم أدخل المبالغ والأدلة. وجود الجهة لا يعني وجود رصيد.</p>
        <div className="rounded-xl border bg-slate-50 p-4">
            <p className="font-bold">الجهات المتاحة: {entities.length} · بنود المسودة: {value.length}</p>
            <label className="mt-3 block text-sm">بحث في الجهات الموجودة<input aria-label="بحث في الجهات الموجودة" className={inputClass} value={search} onChange={event => setSearch(event.target.value)} placeholder="الاسم أو الهوية أو العملة" /></label>
            <div className="mt-3 flex flex-wrap gap-3">
                {types.length > 0 && <label>تصفية النوع<select aria-label="تصفية النوع" className={inputClass} value={typeFilter} onChange={e => setTypeFilter(e.target.value)}><option value="">جميع الأنواع</option>{types.map(type => <option key={type} value={type}>{typeLabel(type)}</option>)}</select></label>}
                {currencies.length > 0 && <label>تصفية العملة<select aria-label="تصفية العملة" className={inputClass} value={currencyFilter} onChange={e => setCurrencyFilter(e.target.value)}><option value="">جميع العملات</option>{currencies.map(currency => <option key={currency} value={currency}>{currency}</option>)}</select></label>}
            </div>
            {!entities.length ? <p role="status" className="mt-3 rounded-lg bg-amber-50 p-3">لا توجد هويات مؤهلة في القائمة الحالية. راجع إعداد الجهة وربطها في MZ2 وصلاحية القراءة؛ القائمة الفارغة لا تعني أن الرصيد صفر.</p>
                : !available.length ? <p role="status">لا توجد نتيجة لهذا البحث أو التصفية. بنود المسودة محفوظة كما هي.</p>
                    : <div className="mt-3 overflow-x-auto"><table className="w-full text-right text-sm"><thead><tr><th>الجهة</th><th>النوع / الربط</th><th>العملة</th><th>الهوية</th><th>الإدخال</th></tr></thead><tbody>{available.map(entity => <tr key={entity.id} className="border-t"><td className="p-2 font-semibold">{entity.name || entity.label || entity.id}</td><td>{ACCOUNT_LABELS[entity.account_type] || FUNDING_LABELS[entity.funding_mode] || entity.kind || "جهة MZ2"}</td><td dir="ltr">{entity.currency || "تُحدد في بند الرصيد"}</td><td className="break-all" dir="ltr">{entity.id}</td><td><button type="button" disabled={busy || value.some(row => row.entity_id === entity.id)} onClick={() => onChange([...value, selectedEntityRow(entity.id)])} className="rounded border px-3 py-2">{value.some(row => row.entity_id === entity.id) ? "مختارة" : "إدخال الرصيد"}</button></td></tr>)}</tbody></table></div>}
        </div>
        {domain === "banks" && <p className="rounded-lg bg-amber-50 p-3">الحساب البنكي السالب يُصنف التزام سحب على المكشوف مستقلًا. الصندوق لا يقبل السالب.</p>}
        {domain === "providers" && <p>مدى وApple Pay ضمن جهة التسوية الفعلية؛ لا رصيد منصة مكرر ولا تعلّم آلي لرسوم مستقبلية.</p>}
        {domain === "suppliers" && <p>دفعة المورد المقدمة أصل مستقل، ولا تُخصم من مستحقه. يُحفظ كل رصيد في تصنيف مستقل وفق النواة.</p>}
        {value.map((row, index) => {
            const binding = domain === "advertising" ? advertisingBindingForRow(row, entities) : null;
            const adFields = advertisingFieldsForBinding(binding);
            const rowFields = domain === "advertising" ? fields.filter(([field]) => adFields.some(([name]) => name === field) || (!binding && row[field] !== undefined && row[field] !== "")) : fields;
            return <fieldset key={index} className="grid gap-4 rounded-xl border bg-white p-4 md:grid-cols-2" disabled={busy}>
            <legend className="px-2 font-bold">جهة {index + 1}</legend>
            <label>الجهة<select aria-label={`الجهة ${index + 1}`} className={inputClass} value={row.entity_id || ""} onChange={e => patch(index, selectedEntityRow(e.target.value))}><option value="">اختر جهة موجودة</option>{row.entity_id && !entities.some(entity => entity.id === row.entity_id) && <option value={row.entity_id}>هوية محفوظة غير متاحة: {row.entity_id}</option>}{entities.filter(entity => matchesSearch(entity) || entity.id === row.entity_id).map(entity => <option key={entity.id} value={entity.id}>{entity.name || entity.label}{ACCOUNT_LABELS[entity.account_type] ? ` · ${ACCOUNT_LABELS[entity.account_type]}` : ""}{entity.currency ? ` · ${entity.currency}` : ""} · {entity.id}</option>)}</select></label>
            {rowFields.map(([field, label]) => <div key={field}><OpeningField label={`${label} ${index + 1}`} type="number" min="0" step="any" value={row[field]} onChange={v => patch(index, { [field]: v })} /><button type="button" className="mt-1 text-sm text-emerald-800 underline" onClick={() => patch(index, { [field]: "0" })}>إثبات صفر — {label}</button></div>)}
            {domain === "providers" && <label>بنك التسوية<select aria-label={`بنك التسوية ${index + 1}`} className={inputClass} value={row.settlement_bank_id || ""} onChange={e => patch(index, { settlement_bank_id: e.target.value })}><option value="">اختر البنك صراحة</option>{banks.map(bank => <option key={bank.id} value={bank.id}>{bank.name}</option>)}</select></label>}
            {domain === "providers" && !banks.length && <p role="alert">لا يوجد بنك تسوية مؤهل في المصدر المحمّل. راجع الحسابات المالية وصلاحية قراءتها؛ يبقى الربط مطلوبًا ولا يُختار بنك بديل تلقائيًا.</p>}
            {domain === "advertising" && <>
                {!binding && <p role="alert">اختر ربطًا إعلانيًا موثقًا. تعذر إثبات هوية الربط الحالي؛ لن يُستعاض عنه باسم الحساب أو مرجعه الخارجي.</p>}
                {binding && <p className="text-sm">نمط التمويل المعتمد: {FUNDING_LABELS[binding.funding_mode]}. تظهر الأرصدة التي يحددها هذا الربط فقط.</p>}
                {binding && <p className="text-sm">المنصة: <bdi>{binding.platform || "غير متاحة في المصدر"}</bdi> · حساب الإعلان: <bdi>{binding.platform_account_id || "غير متاح في المصدر"}</bdi> · هوية التكامل: <bdi>{binding.integration_account_id || "غير متاحة في المصدر"}</bdi></p>}
                {adFields.map(([, field, type, bindingKey]) => {
                    const label = type === "ad_prepaid_wallet" ? "حساب المحفظة المالي" : "حساب الذمة المالي";
                    const eligible = financialAccounts.filter(a => a.id === binding[bindingKey] && a.account_type === type && accountUsable(a));
                    return <div key={field}><label>{label}<select aria-label={`${label} ${index + 1}`} className={inputClass} value={row[field] || (financialAccounts.find(a => a.id === row.financial_account_id)?.account_type === type ? row.financial_account_id : "")} onChange={e => chooseAccount(index, { [field]: e.target.value, financial_account_id: "", evidence_file_id: "" })}><option value="">اختر الحساب الحقيقي صراحة</option>{eligible.map(a => <option key={a.id} value={a.id}>{a.name} · {a.currency} · {a.id}</option>)}</select></label>{!eligible.length && <p role="alert">{label} المرتبط غير متاح بحالة ونوع مؤهلين في المصدر المحمّل. الهوية المطلوبة: <bdi>{binding[bindingKey] || "غير موثقة"}</bdi>. راجع الربط؛ لا يُستبدل بحساب آخر.</p>}</div>;
                })}
                <label>مرجع حساب التمويل (عند انطباقه)<select aria-label={`حساب التمويل ${index + 1}`} className={inputClass} value={row.funding_account_id || ""} onChange={e => patch(index, { funding_account_id: e.target.value })}><option value="">لم يُحدد / لا ينطبق</option>{banks.map(bank => <option key={bank.id} value={bank.id}>{bank.name}</option>)}</select></label>
                {!banks.length && <p role="status">لا توجد حسابات بنكية مؤهلة لاختيار مرجع التمويل في المصدر المحمّل. عدم الاختيار لا يثبت عدم انطباق التمويل.</p>}
                <OpeningField label={`مرجع التمويل ${index + 1}`} value={row.funding_reference} onChange={v => patch(index, { funding_reference: v })} />
            </>}
            {domain === "advertising" ? adFields.map(([, field]) => {
                const label = field === "payable_account_id" ? "الذمة" : "المحفظة";
                const type = field === "payable_account_id" ? "ad_payable" : "ad_prepaid_wallet";
                const id = row[field] || (financialAccounts.find(a => a.id === row.financial_account_id)?.account_type === type ? row.financial_account_id : "");
                const account = financialAccounts.find(a => a.id === id);
                return <CurrencyFields key={field} row={accountFx(row, id, account)} label={`عملة ${label} ${index + 1}`} accountBound account={account} onChange={changes => patch(index, { account_fx: { ...row.account_fx, [id]: { ...accountFx(row, id, account), ...changes } } })} />;
            }) : <CurrencyFields row={row} label={`العملة ${index + 1}`} accountBound={domain === "banks"} account={selectedAccount(row)} onChange={changes => patch(index, changes)} />}
            <OpeningField label={`الدليل المطلوب ${index + 1}`} value={row.evidence_ref} onChange={v => patch(index, { evidence_ref: v })} />
            <button type="button" className="text-rose-800" onClick={() => onChange(value.filter((_, i) => i !== index))}>حذف الجهة من المسودة</button>
        </fieldset>; })}
        <button type="button" className="rounded-lg border px-4 py-2 font-bold" disabled={busy} onClick={() => onChange([...value, selectedEntityRow("")])}>اختيار جهة موجودة</button>
        {domain === "external_persons" && <button type="button" className="ms-2 rounded-lg border px-4 py-2" disabled={busy || !createExternalPerson} onClick={() => setAdding(!adding)}>إضافة طرف جديد</button>}
        {adding && <fieldset disabled={busy} className="space-y-3 rounded-xl border p-4"><legend>طرف خارجي جديد</legend><OpeningField label="اسم الطرف" value={person.name} onChange={v => setPerson({ ...person, name: v })} /><OpeningField label="هاتف الطرف" type="tel" value={person.phone} onChange={v => setPerson({ ...person, phone: v })} /><OpeningField label="ملاحظات الطرف" value={person.notes} onChange={v => setPerson({ ...person, notes: v })} /><button type="button" className="rounded-lg bg-emerald-800 px-4 py-2 text-white" onClick={create}>حفظ الطرف واختياره</button></fieldset>}
        {error && <p role="alert" className="text-rose-800">{error}</p>}
    </section>;
}
