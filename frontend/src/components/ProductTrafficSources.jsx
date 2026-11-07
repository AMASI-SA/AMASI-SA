import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import api from "../lib/api";

const sourceUnitsFormatter = new Intl.NumberFormat("ar-SA-u-nu-latn", { maximumFractionDigits: 2 });
const money = value => typeof value === "number" && Number.isFinite(value) ? `${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س` : "غير متاح";
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

function estimateReason(reason) {
    const code = String(reason || "");
    if (/range|period|31/.test(code)) return "تفاصيل الترويج متاحة لفترة لا تتجاوز ٣١ يومًا.";
    if (/refund|return/.test(code)) return "تعذر توزيع الترويج بدقة لوجود مرتجعات تحتاج تسوية.";
    if (/currency|fx/.test(code)) return "تحويل صرف الإعلان إلى الريال غير مكتمل.";
    if (/filter|cohort/.test(code)) return "بيانات الترويج لا تطابق فلاتر التقرير المختارة.";
    if (/account/.test(code)) return "الحساب الإعلاني غير متحقق.";
    if (code === "ad_identity_unverified") return "الحملة معروفة، لكن الإعلان غير متحقق؛ تكلفة الترويج غير متاحة على مستوى الإعلان.";
    if (/campaign|ad_group|ad_identity|attribution/.test(code)) return "هوية الحملة أو المجموعة أو الإعلان غير متحققة.";
    if (/spend|coverage|daily/.test(code)) return "بيانات صرف الإعلان غير مكتملة للفترة.";
    return "بيانات الصرف أو الإسناد غير مكتملة؛ لم تُفترض صفرًا.";
}

function usePromotionEstimate(open, source, context) {
    const [result, setResult] = useState(null);
    const [attempt, setAttempt] = useState(0);
    // At most one result per mounted badge. Never share a cache across tenants.
    const cache = useRef(null);
    const params = {
        from_date: context?.from_date || "", to_date: context?.to_date || "",
        statuses: context?.statuses || "default", product_id: String(context?.product_id || ""),
        platform: source.platform, account_id: source.account_id || "",
        payment_methods: context?.payment_methods || "", shipping_companies: context?.shipping_companies || "",
        report_scope: context?.report_scope || "sold",
    };
    const key = JSON.stringify(params);
    const days = (Date.parse(params.to_date) - Date.parse(params.from_date)) / 86400000 + 1;
    const notApplicable = source.account_status === "not_applicable";
    const unavailable = notApplicable ? "هذا المصدر ليس حملة إعلانية."
        : source.account_status !== "confirmed" || source.attribution_status !== "confirmed" || !source.account_id ? "تفاصيل الإعلان والترويج غير متاحة: الحساب الإعلاني غير متحقق."
        : !params.product_id || !params.from_date || !params.to_date ? "تفاصيل الترويج غير متاحة دون تحديد المنتج والفترة."
        : !Number.isFinite(days) || days < 1 || days > 31 ? "تفاصيل الترويج متاحة لفترة لا تتجاوز ٣١ يومًا." : null;
    useEffect(() => {
        if (cache.current?.key !== key) cache.current = null;
        if (!open || unavailable) return;
        if (cache.current?.key === key) { setResult(cache.current); return; }
        let current = true;
        const controller = new AbortController();
        setResult({ key, loading: true });
        const timer = setTimeout(() => {
            api.get("/dashboard-v2/product-promotion-estimates", { params: JSON.parse(key), timeout: 20000, signal: controller.signal })
                .then(response => {
                    if (!current) return;
                    const data = response.data;
                    if (!data || data.currency !== "SAR" || data.allocation_method !== "product_sales_share" || !Array.isArray(data.rows)) throw new Error("invalid_estimate_response");
                    cache.current = { key, data };
                    setResult(cache.current);
                })
                .catch(() => {
                    if (!current) return;
                    cache.current = { key, error: true };
                    setResult(cache.current);
                });
        }, 200);
        return () => { current = false; clearTimeout(timer); controller.abort(); };
    }, [open, key, unavailable, attempt]);
    return { unavailable, result: result?.key === key ? result : null, retry: () => { cache.current = null; setAttempt(value => value + 1); } };
}

function PromotionDetails({ state, source }) {
    if (state.unavailable) return <p className="mt-3 text-xs text-slate-500">{state.unavailable}</p>;
    const result = state.result;
    if (!result || result.loading) return <p role="status" className="mt-3 text-sm text-slate-500">جارٍ تحميل تفاصيل الإعلانات والترويج…</p>;
    if (result.error) return <div role="alert" className="mt-3 text-sm text-amber-800">تعذر تحميل تفاصيل الترويج. <button type="button" className="underline" onClick={state.retry}>إعادة المحاولة</button></div>;
    const data = result.data;
    const units = data.rows.reduce((sum, row) => sum + (typeof row.units_sold === "number" && Number.isFinite(row.units_sold) ? row.units_sold : NaN), 0);
    if (data.rows.length && (!Number.isFinite(units) || Math.abs(units - Number(source.units_sold)) > 0.011)) return <p role="status" className="mt-3 text-sm text-amber-800">تغيرت بيانات الفترة؛ حدّث التقرير لعرض تقدير الترويج المطابق.</p>;
    if (!data.rows.length) return <p role="status" className="mt-3 text-sm text-amber-800">{estimateReason(data.reason)}</p>;
    return <section className="mt-4 border-t pt-3" aria-label="تفاصيل الإعلان وتقدير تكلفة الترويج">
        <h3 className="font-bold">الحملة ← المجموعة الإعلانية ← الإعلان عند معرفته</h3>
        <p className="my-2 text-xs text-slate-500">توزيع تقديري حسب حصة مبيعات المنتج في الإعلان. صرف الإعلان الكامل يختلف عن نصيب هذا المنتج؛ ليس تكلفة شراء فردية مؤكدة. جميع المبالغ بالريال السعودي.</p>
        <div className="overflow-x-auto"><table className="w-full min-w-[780px] border-collapse text-xs" aria-label="صرف الإعلانات ونصيب المنتج التقديري">
            <thead className="bg-slate-50"><tr>{["الحملة", "المجموعة الإعلانية", "الإعلان أو الحملة البديلة", "قطع المنتج", "صرف الإعلان كاملًا", "نصيب المنتج التقديري", "تقدير الترويج للقطعة"].map(label => <th key={label} className="border-b p-2">{label}</th>)}</tr></thead>
            <tbody>{data.rows.map((row, index) => <tr key={`${row.campaign_id}:${row.ad_group_id}:${row.ad_id}:${index}`} className="border-b border-slate-100">
                <td className="p-2">{row.campaign_name || (row.campaign_id ? `حملة ${row.campaign_id}` : "حملة غير معروفة")}</td>
                <td className="p-2">{row.ad_group_name || (row.ad_group_id ? `مجموعة ${row.ad_group_id}` : "مجموعة غير معروفة")}</td>
                <td className="p-2">{row.ad_name || (row.ad_id ? `إعلان ${row.ad_id}` : row.campaign_name || (row.campaign_id ? `حملة ${row.campaign_id}` : "حملة غير معروفة"))}<small className="mt-1 block text-slate-500">{row.allocation_level === "campaign" || !row.ad_id ? "عرض على مستوى الحملة" : "تقدير على مستوى الإعلان"}</small>{row.reason && <small className="mt-1 block text-amber-800">{estimateReason(row.reason)}</small>}</td>
                <td className="p-2 font-bold">{sourceUnitsFormatter.format(row.units_sold)}</td>
                <td className="p-2 whitespace-nowrap">{money(row.ad_spend_sar)}</td>
                <td className="p-2 whitespace-nowrap">{money(row.estimated_product_spend_sar)}</td>
                <td className="p-2 whitespace-nowrap">{money(row.estimated_unit_promotion_cost_sar)}</td>
            </tr>)}</tbody>
        </table></div>
    </section>;
}

function TrafficSourceBadge({ source, promotionContext }) {
    const [open, setOpen] = useState(false);
    const [position, setPosition] = useState({ left: 12, top: 12 });
    const pinned = useRef(false);
    const ignoreFocus = useRef(false);
    const closeTimer = useRef(null);
    const content = useRef(null);
    const trigger = useRef(null);
    const id = useId();
    const promotion = usePromotionEstimate(open, source, promotionContext);
    const unavailable = source.attribution_status === "unavailable";
    const platform = unavailable ? "تعذر تحديث المصدر" : source.platform_label || ({ snapchat: "سناب شات", snap: "سناب شات", tiktok: "تيك توك", meta: "ميتا", google: "جوجل", direct: "مباشر" })[source.platform] || "مصدر غير متحقق";
    const account = unavailable || source.account_status === "not_applicable" ? null : source.account_status === "confirmed" && source.account_id ? (source.account_name || `حساب ${source.account_id}`) : "حساب غير محدد";
    const icon = !unavailable && platformIcons[source.platform];
    const description = [platform, account, `${sourceUnitsFormatter.format(source.units_sold)} قطعة`].filter(Boolean).join(" · ");
    const campaigns = Array.isArray(source.campaigns) && source.campaigns.length ? source.campaigns
        : [{ campaign_id: null, units_sold: source.units_sold, attribution_status: source.attribution_status === "confirmed" ? "unknown" : source.attribution_status }];
    const cancelClose = () => clearTimeout(closeTimer.current);
    const show = () => { cancelClose(); if (!ignoreFocus.current) setOpen(true); };
    const close = (restoreFocus = false) => {
        cancelClose(); pinned.current = false;
        if (restoreFocus) { ignoreFocus.current = true; trigger.current?.focus(); ignoreFocus.current = false; }
        setOpen(false);
    };
    const leave = () => { cancelClose(); if (!pinned.current) closeTimer.current = setTimeout(() => setOpen(false), 180); };
    useEffect(() => () => clearTimeout(closeTimer.current), []);
    useLayoutEffect(() => {
        if (!open) return;
        const update = () => {
            const rect = trigger.current?.getBoundingClientRect();
            if (!rect) return;
            const width = Math.max(120, Math.min(promotionContext ? 960 : 360, window.innerWidth - 24));
            const below = window.innerHeight - rect.bottom - 18;
            const above = rect.top - 18;
            const useBelow = below >= Math.min(320, above);
            setPosition({ position: "fixed", zIndex: 100, width, left: Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12)),
                maxHeight: Math.max(120, Math.min(520, useBelow ? below : above)), ...(useBelow ? { top: rect.bottom + 6 } : { bottom: window.innerHeight - rect.top + 6 }) });
        };
        const outside = event => { if (!trigger.current?.contains(event.target) && !content.current?.contains(event.target)) close(); };
        const escape = event => { if (event.key === "Escape") { event.preventDefault(); close(true); } };
        update();
        window.addEventListener("resize", update);
        window.addEventListener("scroll", update, true);
        document.addEventListener("pointerdown", outside);
        document.addEventListener("focusin", outside);
        document.addEventListener("keydown", escape);
        return () => { window.removeEventListener("resize", update); window.removeEventListener("scroll", update, true); document.removeEventListener("pointerdown", outside); document.removeEventListener("focusin", outside); document.removeEventListener("keydown", escape); };
    }, [open, Boolean(promotionContext)]);
    return <li aria-label={description} title={description}>
        <button ref={trigger} type="button" aria-label={description} aria-expanded={open} aria-controls={open ? id : undefined} aria-haspopup="dialog"
            onMouseEnter={show} onMouseLeave={leave} onFocus={show}
            onBlur={event => { if (!content.current?.contains(event.relatedTarget) && !pinned.current) leave(); }}
            onClick={() => { if (pinned.current) close(); else { pinned.current = true; show(); } }}
            className="flex items-center gap-1.5 rounded bg-slate-100 px-2 py-1 text-slate-700 focus:outline-none focus:ring-2 focus:ring-violet-500">
            {icon ? <span aria-hidden="true" className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-base font-bold ${icon.tone}`}>{icon.mark}</span> : <span>{platform}</span>}
            {source.attribution_status === "conflict" && <span> · إسناد متعارض</span>}
            <strong className="whitespace-nowrap"> — {sourceUnitsFormatter.format(source.units_sold)} قطعة</strong>
        </button>
        {open && createPortal(<div ref={content} id={id} dir="rtl" role="dialog" aria-label={`حملات ${platform}`} style={position}
            onMouseEnter={cancelClose} onMouseLeave={leave}
            className="product-traffic-campaigns print:hidden overflow-auto rounded-xl border border-slate-200 bg-white p-3 text-right text-sm text-slate-800 shadow-xl">
            <div className="mb-2 flex items-start justify-between gap-3"><div><strong>{platform}</strong>{account && <p className="text-xs text-slate-500">{account}</p>}</div><button type="button" aria-label="إغلاق تفاصيل الحملات" onClick={() => close(true)} className="rounded px-2 text-slate-500 focus:ring-2 focus:ring-violet-500">×</button></div>
            <table className="w-full border-collapse" aria-label={`عدد قطع المنتج حسب حملات ${platform}`}>
                <thead><tr className="border-b bg-slate-50"><th className="p-2">الحملة</th><th className="p-2 whitespace-nowrap">عدد القطع</th></tr></thead>
                <tbody>{campaigns.map((campaign, index) => <tr key={`${campaign.campaign_id || "unknown"}:${index}`} className="border-b border-slate-100"><td className="p-2 break-words">{campaignLabel(campaign, source)}</td><td className="p-2 font-bold">{sourceUnitsFormatter.format(campaign.units_sold)}</td></tr>)}</tbody>
                <tfoot><tr className="bg-violet-50 font-bold"><td className="p-2">الإجمالي</td><td className="p-2">{sourceUnitsFormatter.format(source.units_sold)}</td></tr></tfoot>
            </table>
            <PromotionDetails state={promotion} source={source} />
        </div>, document.body)}
    </li>;
}

// Attribution is supplied by the report. Missing attribution is never inferred as direct.
export default function ProductTrafficSources({ sources, promotionContext }) {
    return <div className="product-traffic-sources print:hidden text-xs" data-testid="product-traffic-sources" aria-label="القطع حسب المنصة والحساب">
        <style>{"@media print { .product-traffic-sources, .product-traffic-sources-heading, .product-traffic-campaigns { display: none !important; } }"}</style>
        {Array.isArray(sources) && sources.length ? <ul className="flex flex-wrap gap-1.5">{sources.map((source, index) => <TrafficSourceBadge key={`${source.platform}:${source.account_id || "unknown"}:${index}`} source={source} promotionContext={promotionContext} />)}</ul> : <span className="text-slate-500">مصدر القطع غير متحقق</span>}
    </div>;
}
