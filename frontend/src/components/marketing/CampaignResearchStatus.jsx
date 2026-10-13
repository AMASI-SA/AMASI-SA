export default function CampaignResearchStatus({ research }) {
    const partial = research?.status === "partial" && research?.performed === true && research?.mode === "passive_html_only";
    const unavailable = research?.status === "not_observed" && research?.performed === false;
    if (!partial && !unavailable && !(research?.status === "not_connected" && research?.performed === false)) return null;
    return <p data-testid="campaign-research-status" className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-xs leading-6 text-amber-900">
        {partial ? "فُحص نص صفحة منتج أماسي وخياراتها المعلنة. السعر النهائي بعد الاختيار والدفع والبكسل والمنافسون لم تُختبر."
            : unavailable ? "لم تكتمل قراءة صفحة المنتج؛ يستمر التحليل بالأدلة المتاحة."
            : "التوصيات الحالية مستمرة بناءً على الأدلة المتاحة. فحص المتجر والسوق والمنافسين بالمتصفح لم يُربط بعد."}
    </p>;
}
