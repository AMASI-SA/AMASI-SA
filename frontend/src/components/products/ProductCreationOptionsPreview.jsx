import { useState } from "react";
import { creationOptionVisible } from "./productCreationOptions";

export default function ProductCreationOptionsPreview({ options }) {
    const [answers, setAnswers] = useState({});

    function choose(index, value) {
        setAnswers((current) => {
            const next = { ...current, [index]: value };
            options.forEach((_, position) => {
                if (!creationOptionVisible(options, position, next)) delete next[position];
            });
            return next;
        });
    }

    return <section className="space-y-3 rounded-2xl border p-4" aria-label="معاينة خيارات العميل">
        <h4 className="font-black">معاينة خيارات العميل</h4>
        <p className="text-sm text-slate-500">جرّب الاختيارات لمعاينة الحقول التي ستظهر للعميل.</p>
        {options.map((option, index) => creationOptionVisible(options, index, answers) && <label key={index} className="block space-y-1 text-sm font-bold">
            <span>{option.name} · {option.required ? "مطلوب" : "اختياري"}</span>
            {option.description && <span className="block text-xs font-normal text-slate-600">{option.description}</span>}
            {option.type === "choice"
                ? <select className="h-11 w-full rounded-xl border px-3" value={answers[index] || ""} onChange={(event) => choose(index, event.target.value)} required={option.required}><option value="">اختر {option.name}</option>{option.values.map((value, position) => <option key={value} value={value}>{value}{Number(option.value_prices?.[position]) > 0 ? ` (+${option.value_prices[position]} ر.س)` : ""}</option>)}</select>
                : <input className="h-11 w-full rounded-xl border px-3" placeholder={option.description || "يكتب العميل النص هنا"} required={option.required} readOnly />}
        </label>)}
    </section>;
}
