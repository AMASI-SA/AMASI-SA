import { CHILD_SIZE_VALUES, newCreationOption } from "./productCreationOptions";

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
                        <button type="button" onClick={() => onChange(options.filter((_, position) => position !== index))} aria-label={`حذف الخيار ${index + 1}`} className="rounded-lg px-3 py-2 text-sm font-bold text-rose-700">حذف</button>
                    </div>
                    <label className="flex items-center gap-2 text-sm font-bold">
                        <input type="checkbox" checked={option.required} onChange={(event) => update(index, { required: event.target.checked })} />
                        إجابة مطلوبة للخيار {index + 1}
                    </label>
                    {option.type === "choice" && <>
                        <label className="block space-y-1 text-sm font-bold">
                            <span>قيم الخيار {index + 1} — كل قيمة في سطر</span>
                            <textarea rows={5} value={option.values_text} onChange={(event) => update(index, { values_text: event.target.value })} className={INPUT} placeholder={"S\nM\nL"} required />
                        </label>
                        <button type="button" onClick={() => update(index, { name: option.name.trim() ? option.name : "المقاس", values_text: CHILD_SIZE_VALUES.join("\n") })} className="rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-bold text-indigo-800">استخدام مقاسات سنة إلى 10 سنوات</button>
                    </>}
                    {option.type === "text" && <p className="text-xs text-slate-500">سيظهر للعميل حقل لكتابة النص عند الطلب.</p>}
                </fieldset>
            ))}
            <div className="flex flex-wrap gap-2">
                <button type="button" disabled={options.length >= 20} onClick={() => onChange([...options, newCreationOption("text")])} className="rounded-xl border px-3 py-2 text-sm font-bold disabled:opacity-50">إضافة حقل نص</button>
                <button type="button" disabled={options.length >= 20} onClick={() => onChange([...options, newCreationOption("choice")])} className="rounded-xl border px-3 py-2 text-sm font-bold disabled:opacity-50">إضافة قائمة اختيارات</button>
            </div>
        </fieldset>
    );
}
