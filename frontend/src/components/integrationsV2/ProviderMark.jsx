import { useState } from "react";
import {
    ChartLineUp, ChartPieSlice, Ghost, LinkSimple, MagnifyingGlass, Package, Plug,
    Receipt, Robot, Storefront, Truck,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import { startGoogleConnection } from "../../services/googleIntegrationsV2";

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
    const [connecting, setConnecting] = useState(false);
    const definition = PROVIDERS[provider] || { Icon: Plug, className: "bg-slate-600 text-white", label: provider };
    const { Icon } = definition;
    const dimensions = size === "sm" ? "h-9 w-9 rounded-lg" : "h-12 w-12 rounded-xl";
    const iconSize = size === "sm" ? 20 : 25;
    const isGoogle = String(provider || "").startsWith("google_");

    async function connectGoogle(event) {
        event.stopPropagation();
        if (connecting) return;
        setConnecting(true);
        try {
            const result = await startGoogleConnection();
            window.location.assign(result.authorization_url);
        } catch (error) {
            const detail = error?.response?.data?.detail;
            const message = typeof detail === "string"
                ? detail
                : detail?.message
                    || (error?.message === "google_authorization_url_untrusted"
                        ? "رفض ميزان رابط ربط Google غير موثوق."
                        : "تعذر بدء ربط حساب Google.");
            toast.error(message);
            setConnecting(false);
        }
    }

    return (
        <div className="flex shrink-0 flex-col items-center gap-1.5">
            <div className={`inline-flex items-center justify-center ${dimensions} ${definition.className}`} title={definition.label} aria-label={definition.label} data-testid={`provider-mark-${provider}`}>
                <Icon size={iconSize} weight="duotone" />
            </div>
            {isGoogle && size !== "sm" && (
                <button
                    type="button"
                    onClick={connectGoogle}
                    disabled={connecting}
                    className="inline-flex min-h-7 whitespace-nowrap items-center justify-center gap-1 rounded-lg bg-emerald-800 px-2 text-[10px] font-extrabold text-white transition hover:bg-emerald-900 disabled:cursor-wait disabled:opacity-60"
                    data-testid={`integration-${provider}-connect`}
                    aria-label={`ربط ${definition.label}`}
                >
                    <LinkSimple size={12} weight="bold" />
                    {connecting ? "جاري الربط…" : "ربط Google"}
                </button>
            )}
        </div>
    );
}
