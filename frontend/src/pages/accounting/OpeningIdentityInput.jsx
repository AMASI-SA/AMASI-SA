const REFERENCED_CATEGORIES = new Set([
    "provider_receivable", "courier_cod_receivable", "courier_payable",
    "store_driver_cod_receivable", "store_driver_fee_payable",
    "employee_advance", "employee_custody", "employee_salary_payable",
    "supplier_payable", "customer_receivable",
]);

export default function OpeningIdentityInput({ line, index, context, onChange }) {
    if (!REFERENCED_CATEGORIES.has(line.category)) {
        return <input required aria-label={`معرف كيان السطر ${index + 1}`}
            value={line.entity_id} onChange={(event) => onChange({ entity_id: event.target.value })}
            className="rounded border px-2 py-2 text-xs" placeholder="معرف الحساب الافتتاحي" />;
    }
    const choices = context?.entities?.[line.category] || [];
    return <select required aria-label={`هوية الطرف للسطر ${index + 1}`}
        value={line.entity_id} disabled={!context} className="rounded border px-2 py-2 text-xs"
        onChange={(event) => {
            const item = choices.find((choice) => choice.id === event.target.value);
            onChange({ entity_id: item?.id || "", label: item?.name || "" });
        }}>
        <option value="">{context ? "اختر الطرف المسجل في المتجر" : "تعذر التحقق من هويات الأطراف"}</option>
        {choices.map((item) => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
    </select>;
}
