import { useEffect, useId, useRef, useState } from "react";
import * as Popover from "@radix-ui/react-popover";

const sourceUnitsFormatter = new Intl.NumberFormat("ar-SA-u-nu-latn", { maximumFractionDigits: 2 });
const platformIcons = {
    snapchat: { mark: "👻", tone: "bg-yellow-300 text-slate-950" },
    snap: { mark: "👻", tone: "bg-yellow-300 text-slate-950" },
    tiktok: { mark: "♪", tone: "bg-slate-950 text-white" },
    meta: { mark: "∞", tone: "bg-blue-600 text-white" },
    google: { mark: "G", tone: "bg-white text-blue-600 border border-slate-200" },
};

function campaignLabel(campaign, source) {
    if (campaign.attribution_status === "not_applicable" || source.account_status === "not_applicable") return "بدون حملة إعلانية";
    if (campaign.attribution_status === "unavailable") return "تعذر تحديث الحملة";
    if (campaign.attribution_status === "conflict") return "حملة غير معروفة — إسناد متعارض";
    if (campaign.attribution_status === "confirmed" && campaign.campaign_id) return campaign.campaign_name || `حملة ${campaign.campaign_id}`;
    return "حملة غير معروفة";
}

function TrafficSourceBadge({ source }) {
    const [open, setOpen] = useState(false);
    const pinned = useRef(false);
    const closeTimer = useRef(null);
    const content = useRef(null);
    const trigger = useRef(null);
    const id = useId();
    const unavailable = source.attribution_status === "unavailable";
    const platform = unavailable ? "تعذر تحديث المصدر" : source.platform_label || ({ snapchat: "سناب شات", snap: "سناب شات", tiktok: "تيك توك", meta: "ميتا", google: "جوجل", direct: "مباشر" })[source.platform] || "مصدر غير متحقق";
    const account = unavailable || source.account_status === "not_applicable" ? null : source.account_status === "confirmed" && source.account_id ? (source.account_name || `حساب ${source.account_id}`) : "حساب غير محدد";
    const icon = !unavailable && platformIcons[source.platform];
    const description = [platform, account, `${sourceUnitsFormatter.format(source.units_sold)} قطعة`].filter(Boolean).join(" · ");
    const campaigns = Array.isArray(source.campaigns) && source.campaigns.length ? source.campaigns
        : [{ campaign_id: null, units_sold: source.units_sold, attribution_status: source.attribution_status === "confirmed" ? "unknown" : source.attribution_status }];
    const cancelClose = () => clearTimeout(closeTimer.current);
    const show = () => { cancelClose(); setOpen(true); };
    const close = () => { cancelClose(); pinned.current = false; setOpen(false); };
    const leave = () => {
        cancelClose();
        if (!pinned.current) closeTimer.current = setTimeout(() => setOpen(false), 180);
    };
    useEffect(() => () => clearTimeout(closeTimer.current), []);
    return <li aria-label={description} title={description}>
        <Popover.Root open={open} onOpenChange={value => value ? show() : close()}>
            <Popover.Anchor asChild>
                <button ref={trigger} type="button" aria-label={description} aria-expanded={open} aria-controls={open ? id : undefined} aria-haspopup="dialog"
                    onMouseEnter={show} onMouseLeave={leave} onFocus={show}
                    onBlur={event => { if (!content.current?.contains(event.relatedTarget) && !pinned.current) leave(); }}
                    onClick={() => { if (pinned.current) close(); else { pinned.current = true; show(); } }}
                    className="flex items-center gap-1.5 rounded bg-slate-100 px-2 py-1 text-slate-700 focus:outline-none focus:ring-2 focus:ring-violet-500">
                    {icon ? <span aria-hidden="true" className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-base font-bold ${icon.tone}`}>{icon.mark}</span> : <span>{platform}</span>}
                    {source.attribution_status === "conflict" && <span> · إسناد متعارض</span>}
                    <strong className="whitespace-nowrap"> — {sourceUnitsFormatter.format(source.units_sold)} قطعة</strong>
                </button>
            </Popover.Anchor>
            <Popover.Portal>
                <Popover.Content ref={content} id={id} dir="rtl" aria-label={`حملات ${platform}`} sideOffset={6} collisionPadding={12}
                    onOpenAutoFocus={event => event.preventDefault()} onCloseAutoFocus={event => event.preventDefault()}
                    onInteractOutside={event => { if (trigger.current?.contains(event.target)) event.preventDefault(); }}
                    onMouseEnter={cancelClose} onMouseLeave={leave}
                    className="product-traffic-campaigns print:hidden z-[100] w-80 max-w-[calc(100vw-24px)] max-h-[min(360px,var(--radix-popover-content-available-height))] overflow-auto rounded-xl border border-slate-200 bg-white p-3 text-right text-sm text-slate-800 shadow-xl">
                    <div className="mb-2 flex items-start justify-between gap-3"><div><strong>{platform}</strong>{account && <p className="text-xs text-slate-500">{account}</p>}</div><button type="button" aria-label="إغلاق تفاصيل الحملات" onClick={close} className="rounded px-2 text-slate-500 focus:ring-2 focus:ring-violet-500">×</button></div>
                    <table className="w-full border-collapse" aria-label={`عدد قطع المنتج حسب حملات ${platform}`}>
                        <thead><tr className="border-b bg-slate-50"><th className="p-2">الحملة</th><th className="p-2 whitespace-nowrap">عدد القطع</th></tr></thead>
                        <tbody>{campaigns.map((campaign, index) => <tr key={`${campaign.campaign_id || "unknown"}:${index}`} className="border-b border-slate-100"><td className="p-2 break-words">{campaignLabel(campaign, source)}</td><td className="p-2 font-bold">{sourceUnitsFormatter.format(campaign.units_sold)}</td></tr>)}</tbody>
                        <tfoot><tr className="bg-violet-50 font-bold"><td className="p-2">الإجمالي</td><td className="p-2">{sourceUnitsFormatter.format(source.units_sold)}</td></tr></tfoot>
                    </table>
                </Popover.Content>
            </Popover.Portal>
        </Popover.Root>
    </li>;
}

// Attribution is supplied by the report. Missing attribution is never inferred as direct.
export default function ProductTrafficSources({ sources }) {
    return <div className="product-traffic-sources print:hidden text-xs" data-testid="product-traffic-sources" aria-label="القطع حسب المنصة والحساب">
        <style>{"@media print { .product-traffic-sources, .product-traffic-sources-heading, .product-traffic-campaigns { display: none !important; } }"}</style>
        {Array.isArray(sources) && sources.length ? <ul className="flex flex-wrap gap-1.5">{sources.map((source, index) => <TrafficSourceBadge key={`${source.platform}:${source.account_id || "unknown"}:${index}`} source={source} />)}</ul> : <span className="text-slate-500">مصدر القطع غير متحقق</span>}
    </div>;
}
