import { CHILD_SIZE_VALUES, newCreationOption, removeCreationOption } from "./productCreationOptions";

const INPUT = "w-full rounded-xl border p-3 outline-none focus:border-indigo-400";

export default function ProductCreationOptionsEditor({ options, onChange, disabled }) {
    function update(index, changes) {
        onChange(options.map((option, position) => position === index ? { ...option, ...changes } : option));
    }

    return (
        <fieldset disabled={disabled} className="space-y-3 rounded-2xl border p-4 lg:col-span-2">
            <legend className="px-2 font-black">خيارات المنتج وتخصيص العميل</legend>
            <p className="text-sm text-slate-600">أضف نصًا يكتبه العميل، أو قائمة يختار منها قيمة واحدة مثل المقاس. حدد ما إذا كانت الإجابة مطلوبة.</p>
            {options.map((option, index) => (
                <fieldset key={index} className="space-y-3 rounded-xl border bg-slate-50 p-3">
                    <legend className="px-2 text-sm font-bold">{option.type === "text" ? "حقل نص" : "قائمة اختيارات"} {index + 1}</legend>
                    <div className="flex items-start gap-3">
                        <label className="flex-1 space-y-1 text-sm font-bold">
                            <span>اسم الخيار {index + 1}</span>
                            <input maxLength={100} value={option.name} onChange={(event) => update(index, { name: event.target.value })} placeholder={option.type === "text" ? "مثال: الاسم المطلوب على المنتج" : "مثال: المقاس"} className={INPUT} required />
                        </label>
                        <button type="button" onClick={() => onChange(removeCreationOption(options, index))} aria-label={`حذف الخيار ${index + 1}`} className="rounded-lg px-3 py-2 text-sm font-bold text-rose-700">حذف</button>
                    </div>
                    <label className="block space-y-1 text-sm font-bold">
                        <span>الوصف التوضيحي للخيار {index + 1} (اختياري)</span>
                        <input maxLength={1000} value={option.description || ""} onChange={(event) => update(index, { description: event.target.value })} placeholder={option.type === "text" ? "مثال: اكتب اللي تبيه على كرت الإهداء" : "مثال: اختر المقاس المناسب"} className={INPUT} />
                    </label>
                    <label className="flex items-center gap-2 text-sm font-bold">
                        <input type="checkbox" checked={option.required} onChange={(event) => update(index, { required: event.target.checked })} />
                        إجابة مطلوبة للخيار {index + 1}
                    </label>
                    {option.type === "choice" && <>
                        <div className="text-sm font-bold">قيم الخيار {index + 1} · {option.values.length} قيمة</div>
                        {option.values.map((value, valueIndex) => <div key={valueIndex} className="flex items-end gap-2">
                            <label className="flex-1 space-y-1 text-sm font-bold">
                                <span>قيمة {valueIndex + 1} للخيار {index + 1}</span>
                                <input maxLength={100} value={value} onChange={(event) => update(index, { values: option.values.map((entry, position) => position === valueIndex ? event.target.value : entry) })} className={INPUT} required />
                            </label>
                            <label className="w-36 space-y-1 text-sm font-bold">
                                <span>السعر الإضافي للقيمة {valueIndex + 1} للخيار {index + 1} (ر.س)</span>
                                <input type="number" min="0" max="100000000" step="0.01" value={option.value_prices?.[valueIndex] ?? 0} onChange={(event) => update(index, { value_prices: option.values.map((_, position) => position === valueIndex ? event.target.value : (option.value_prices?.[position] ?? 0)) })} className={INPUT} />
                            </label>
                            <button type="button" aria-label={`حذف القيمة ${valueIndex + 1} للخيار ${index + 1}`} onClick={() => update(index, { values: option.values.filter((_, position) => position !== valueIndex), value_prices: option.values.filter((_, position) => position !== valueIndex).map((_, position) => option.value_prices?.[position >= valueIndex ? position + 1 : position] ?? 0) })} className="rounded-lg px-3 py-3 text-sm font-bold text-rose-700">حذف القيمة</button>
                        </div>)}
                        <div className="flex flex-wrap gap-2">
                            <button type="button" disabled={option.values.length >= 100} onClick={() => update(index, { values: [...option.values, ""] })} className="rounded-lg border bg-white px-3 py-2 text-sm font-bold disabled:opacity-50">إضافة قيمة جديدة للخيار {index + 1}</button>
                            <button type="button" onClick={() => update(index, { name: option.name.trim() ? option.name : "المقاس", values: [...CHILD_SIZE_VALUES], value_prices: [] })} className="rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-bold text-indigo-800">استخدام مقاسات سنة إلى 10 سنوات</button>
                        </div>
                    </>}
                    {option.type === "text" && <p className="text-xs text-slate-500">سيظهر للعميل حقل لكتابة النص عند الطلب.</p>}
                    <label className="flex items-center gap-2 text-sm font-bold">
                        <input type="checkbox" checked={Boolean(option.condition)} onChange={(event) => update(index, { condition: event.target.checked ? { option_index: -1, operator: "=", value: "" } : null })} />
                        شرط ظهور الحقل {index + 1}
                    </label>
                    {option.condition && <div className="grid gap-2 sm:grid-cols-3">
                        <label className="space-y-1 text-sm font-bold"><span>الحقل المرتبط بالخيار {index + 1}</span>
                            <select className={INPUT} value={option.condition.option_index} onChange={(event) => update(index, { condition: { ...option.condition, option_index: Number(event.target.value), value: "" } })} required>
                                <option value={-1} disabled>اسم الحقل</option>
                                {options.map((parent, position) => parent.type === "choice" && position !== index ? <option key={position} value={position}>{parent.name || `الخيار ${position + 1}`}</option> : null)}
                            </select>
                        </label>
                        <label className="space-y-1 text-sm font-bold"><span>المقارنة للخيار {index + 1}</span>
                            <select className={INPUT} value={option.condition.operator} onChange={(event) => update(index, { condition: { ...option.condition, operator: event.target.value } })}><option value="=">يساوي =</option><option value="!=">لا يساوي ≠</option></select>
                        </label>
                        <label className="space-y-1 text-sm font-bold"><span>قيمة الشرط للخيار {index + 1}</span>
                            <select className={INPUT} value={option.condition.value} onChange={(event) => update(index, { condition: { ...option.condition, value: event.target.value } })} required>
                                <option value="" disabled>قيمة الحقل</option>
                                {(options[option.condition.option_index]?.values || []).filter((value) => value.trim()).map((value, position) => <option key={position} value={value.trim()}>{value.trim()}</option>)}
                            </select>
                        </label>
                    </div>}
                </fieldset>
            ))}
            <div className="flex flex-wrap gap-2">
                <button type="button" disabled={options.length >= 20} onClick={() => onChange([...options, newCreationOption("text")])} className="rounded-xl border px-3 py-2 text-sm font-bold disabled:opacity-50">إضافة حقل نص</button>
                <button type="button" disabled={options.length >= 20} onClick={() => onChange([...options, newCreationOption("choice")])} className="rounded-xl border px-3 py-2 text-sm font-bold disabled:opacity-50">إضافة قائمة اختيارات</button>
            </div>
        </fieldset>
    );
}
