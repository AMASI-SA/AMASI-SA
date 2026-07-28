import {
    ChartLineUp, ChartPieSlice, Ghost, MagnifyingGlass, Package, Plug,
    Receipt, Robot, Storefront, Truck,
} from "@phosphor-icons/react";

const PROVIDERS = {
    salla: { Icon: Storefront, className: "bg-emerald-600 text-white", label: "Salla" },
    openai: { Icon: Robot, className: "bg-slate-950 text-white", label: "OpenAI · Mezan AI" },
    snapchat_ads: { Icon: Ghost, className: "bg-yellow-300 text-slate-950", label: "Snapchat Ads" },
    tiktok_ads: { Icon: ChartPieSlice, className: "bg-slate-950 text-white", label: "TikTok Ads" },
    meta_ads: { Icon: ChartLineUp, className: "bg-blue-600 text-white", label: "Meta Ads" },
    google_analytics_4: { Icon: ChartLineUp, className: "bg-orange-500 text-white", label: "Google Analytics 4" },
    google_search_console: { Icon: MagnifyingGlass, className: "bg-blue-500 text-white", label: "Google Search Console" },
    google_merchant_center: { Icon: Package, className: "bg-sky-600 text-white", label: "Google Merchant Center" },
    google_ads: { Icon: ChartPieSlice, className: "bg-cyan-600 text-white", label: "Google Ads" },
    qoyod: { Icon: Receipt, className: "bg-violet-700 text-white", label: "Qoyod" },
    shipping_companies: { Icon: Truck, className: "bg-amber-600 text-white", label: "Shipping companies" },
};

export default function ProviderMark({ provider, size = "md" }) {
    const definition = PROVIDERS[provider] || { Icon: Plug, className: "bg-slate-600 text-white", label: provider };
    const { Icon } = definition;
    const dimensions = size === "sm" ? "h-9 w-9 rounded-lg" : "h-12 w-12 rounded-xl";
    const iconSize = size === "sm" ? 20 : 25;
    return (
        <div className={`inline-flex shrink-0 items-center justify-center ${dimensions} ${definition.className}`} title={definition.label} aria-label={definition.label} data-testid={`provider-mark-${provider}`}>
            <Icon size={iconSize} weight="duotone" />
        </div>
    );
}
