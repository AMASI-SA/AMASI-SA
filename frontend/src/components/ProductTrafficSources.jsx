const sourceUnitsFormatter = new Intl.NumberFormat("ar-SA-u-nu-latn", { maximumFractionDigits: 2 });
const platformIcons = {
    snapchat: { mark: "👻", tone: "bg-yellow-300 text-slate-950" },
    snap: { mark: "👻", tone: "bg-yellow-300 text-slate-950" },
    tiktok: { mark: "♪", tone: "bg-slate-950 text-white" },
    meta: { mark: "∞", tone: "bg-blue-600 text-white" },
    google: { mark: "G", tone: "bg-white text-blue-600 border border-slate-200" },
};

function TrafficSourceBadge({ source }) {
    const unavailable = source.attribution_status === "unavailable";
    const platform = unavailable ? "تعذر تحديث المصدر" : source.platform_label || ({ snapchat: "سناب شات", snap: "سناب شات", tiktok: "تيك توك", meta: "ميتا", google: "جوجل", direct: "مباشر" })[source.platform] || "مصدر غير متحقق";
    const account = unavailable || source.account_status === "not_applicable" ? null : source.account_status === "confirmed" && source.account_id ? (source.account_name || `حساب ${source.account_id}`) : "حساب غير محدد";
    const icon = !unavailable && platformIcons[source.platform];
    const description = [platform, account, `${sourceUnitsFormatter.format(source.units_sold)} قطعة`].filter(Boolean).join(" · ");
    return <li aria-label={description} title={description} className="flex items-center gap-1.5 rounded bg-slate-100 px-2 py-1 text-slate-700">
        {icon ? <span aria-hidden="true" className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-base font-bold ${icon.tone}`}>{icon.mark}</span> : <span>{platform}</span>}
        {source.attribution_status === "conflict" && <span> · إسناد متعارض</span>}
        <strong className="whitespace-nowrap"> — {sourceUnitsFormatter.format(source.units_sold)} قطعة</strong>
    </li>;
}

// Platform counts use saved report facts only. No campaign hover or spend requests.
export default function ProductTrafficSources({ sources }) {
    return <div className="product-traffic-sources print:hidden text-xs" data-testid="product-traffic-sources" aria-label="القطع حسب المنصة والحساب">
        <style>{"@media print { .product-traffic-sources, .product-traffic-sources-heading { display: none !important; } }"}</style>
        {Array.isArray(sources) && sources.length ? <ul className="flex flex-wrap gap-1.5">{sources.map((source, index) => <TrafficSourceBadge key={`${source.platform}:${source.account_id || "unknown"}:${index}`} source={source} />)}</ul> : <span className="text-slate-500">مصدر القطع غير متحقق</span>}
    </div>;
}
