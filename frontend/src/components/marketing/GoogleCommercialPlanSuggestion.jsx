import { useEffect, useRef, useState } from "react";
import { googleErrorMessage, isGoogleRequestCancelled } from "../../services/googleAds";
import { SUGGESTION_FIELDS, suggestGoogleCommercialPlan } from "../../services/googleCommercialPlanSuggestion";

const LABELS = { season: "الموسم", starts_on: "بداية الموسم", ends_on: "نهاية الموسم", market: "السوق",
    audience: "الجمهور المستهدف", message: "الرسالة التسويقية", category_url: "رابط القسم" };
const BUTTON = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40";

export default function GoogleCommercialPlanSuggestion({ form, reviewed, disabled, onApply }) {
    const [suggestion, setSuggestion] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
    const request = useRef(null), scope = useRef(null);
    scope.current = { form, reviewed, disabled };
    useEffect(() => {
        request.current?.abort(); request.current = null;
        setSuggestion(null); setError(""); setBusy(false);
        return () => { request.current?.abort(); request.current = null; };
    }, [form, reviewed, disabled]);
    async function suggest() {
        if (disabled || request.current || !form.instruction.trim()) return;
        const original = form, controller = new AbortController(); request.current = controller;
        const current = () => request.current === controller && !controller.signal.aborted
            && scope.current.form === original && scope.current.reviewed === reviewed && !scope.current.disabled;
        setBusy(true); setError(""); setSuggestion(null);
        try {
            const result = await suggestGoogleCommercialPlan(original.instruction, { signal: controller.signal });
            if (current()) setSuggestion(result);
        } catch (failure) {
            if (current() && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure, "تعذر اقتراح حقول الخطة. يمكنك إكمالها يدويًا."));
        } finally { if (current()) { request.current = null; setBusy(false); } }
    }
    const applicable = suggestion && SUGGESTION_FIELDS.some((key) => suggestion.fields[key] !== null && !String(form[key] || "").trim());
    return <section aria-label="اقتراح حقول الخطة من التوجيه" className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm leading-7">
        <p>اقترح حقول الموسم من كلامك، ثم راجع النص الذي استند إليه كل اقتراح. المنتجات والاستبعادات والميزانية وأهداف الربحية تُدخل يدويًا.</p>
        <button type="button" className={`${BUTTON} mt-2`} disabled={disabled || busy || !form.instruction.trim()} onClick={suggest}>اقتراح حقول من التوجيه</button>
        {busy && <p role="status">جارٍ اقتراح الحقول… يمكنك تعديل الخطة لإلغاء الاقتراح الجاري.</p>}
        {error && <p role="alert" className="text-red-800">{error}</p>}
        {suggestion && !disabled && <div className="mt-3 space-y-2">
            <p className="font-bold">اقتراح للمراجعة فقط · لم يُحفظ ولم يمنح تفويضًا</p>
            {SUGGESTION_FIELDS.filter((key) => suggestion.fields[key] !== null).map((key) => <div key={key} className="rounded-lg border border-slate-200 bg-white p-2">
                <p><strong>{LABELS[key]}:</strong> <span dir={key === "category_url" ? "ltr" : undefined}>{suggestion.fields[key]}</span></p>
                <p className="text-slate-600">من توجيهك: «{suggestion.evidence[key]}»</p>
                {String(form[key] || "").trim() && <p className="text-xs text-slate-500">الحقل مكتوب حاليًا؛ لن يستبدله الاقتراح.</p>}
            </div>)}
            {suggestion.missing_fields.length > 0 && <p>حقول لم تتضح من التوجيه: {suggestion.missing_fields.map((key) => LABELS[key]).join("، ")}. أكملها يدويًا.</p>}
            <p>وجود الاقتباس لا يثبت صحة فهم التوجيه؛ راجع معنى كل حقل واتجاه الطلب والاستبعادات والتواريخ، خصوصًا المواسم الهجرية. اختيار المنتجات تلقائيًا غير متاح هنا.</p>
            <button type="button" className={BUTTON} disabled={!applicable} onClick={() => onApply(suggestion)}>تعبئة الحقول الفارغة فقط</button>
        </div>}
    </section>;
}
