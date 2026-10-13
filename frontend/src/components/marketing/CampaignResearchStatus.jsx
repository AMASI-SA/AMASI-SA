export default function CampaignResearchStatus({ research }) {
    if (research?.status !== "not_connected" || research?.performed !== false) return null;
    return <p data-testid="campaign-research-status" className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-xs leading-6 text-amber-900">
        التوصيات الحالية مستمرة بناءً على الأدلة المتاحة. فحص المتجر والسوق والمنافسين بالمتصفح لم يُربط بعد.
    </p>;
}
