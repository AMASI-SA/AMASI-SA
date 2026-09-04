import { useEffect, useMemo, useState } from "react";
import {
    ChartBar,
    Check,
    Columns,
    DownloadSimple,
    Eye,
    FileText,
    Funnel,
    Package,
    PencilSimple,
    Trash,
    UploadSimple,
    WarningCircle,
    X,
} from "@phosphor-icons/react";

import {
    buildProfitabilityProductCostHref,
    rememberSnapchatRangeBeforeProductNavigation,
} from "../../campaignProfitabilityProductNavigation";
import InfiniteScrollSentinel from "./InfiniteScrollSentinel";
import { infinitePaginationState } from "./infiniteScrollPagination";

const PROFITABILITY_COLUMNS = Object.freeze([
    "product_cost",
    "profit",
    "profit_margin",
]);
const SNAPCHAT_METRIC_COLUMNS = Object.freeze([
    "paid_reach",
    "paid_frequency",
    "view_content",
    "add_to_cart",
    "start_checkout",
    "add_billing",
]);

const REQUIRED_COLUMNS = Object.freeze(["name", "status", "spend", "sales"]);
const REQUIRED_COLUMN_SET = new Set(REQUIRED_COLUMNS);
const CHECKBOX_WIDTH = 48;
const NAME_WIDTH = 300;
const STATUS_WIDTH = 120;

export const CAMPAIGN_MANAGER_DEFAULT_COLUMNS = Object.freeze([
    "name",
    "status",
    "delivery",
    "orders",
    "cpa",
    "roas",
    "spend",
    "sales",
    "snapchat_purchases",
    "snapchat_value",
    "snapchat_roas",
    "product_cost",
    "profit",
    "profit_margin",
    "impressions",
    "paid_reach",
    "paid_frequency",
    "cpm",
    "clicks",
    "cpc",
    "ctr",
    "view_content",
    "add_to_cart",
    "start_checkout",
    "add_billing",
    "budget",
    "account",
]);

export const CAMPAIGN_MANAGER_NATIVE_COLUMN_ORDER = CAMPAIGN_MANAGER_DEFAULT_COLUMNS;

const COLUMN_DEFINITIONS = Object.freeze([
    { id: "name", label: "اسم الحملة", width: NAME_WIDTH, sortable: true },
    { id: "status", label: "الحالة", width: STATUS_WIDTH, sortable: true },
    { id: "delivery", label: "حالة التسليم", width: 190, sortable: true },
    { id: "orders", label: "طلبات سلة", sublabel: "UTM ID حرفي", width: 125, sortable: true },
    { id: "cpa", label: "CPA سلة", sublabel: "يتطلب تصالح المصدرين", width: 145, sortable: true },
    { id: "roas", label: "ROAS سلة", sublabel: "مبيعات سلة ÷ صرف سناب", width: 140, sortable: true },
    { id: "spend", label: "صرف Snapchat", width: 145, sortable: true },
    { id: "sales", label: "مبيعات سلة", width: 145, sortable: true },
    { id: "snapchat_purchases", label: "مشتريات Snapchat", width: 145, sortable: true },
    { id: "snapchat_value", label: "قيمة شراء Snapchat", width: 165, sortable: true },
    { id: "snapchat_roas", label: "ROAS Snapchat", width: 145, sortable: true },
    { id: "product_cost", label: "تكلفة المنتجات", sublabel: "محرك تكلفة ميزان", width: 160, sortable: true },
    { id: "profit", label: "ربح الحملة", sublabel: "بعد المنتج والإعلان", width: 175, sortable: true },
    { id: "profit_margin", label: "هامش الربح", sublabel: "قبل رسوم الدفع والشحن", width: 150, sortable: true },
    { id: "impressions", label: "مرات الظهور المدفوعة", width: 165, sortable: true },
    { id: "paid_reach", label: "الوصول المدفوع", sublabel: "Paid Reach", width: 145, sortable: true },
    { id: "paid_frequency", label: "التكرار المدفوع", sublabel: "Paid Frequency", width: 145, sortable: true },
    { id: "cpm", label: "eCPM المدفوع", width: 135, sortable: true },
    { id: "clicks", label: "النقرات", width: 115, sortable: true },
    { id: "cpc", label: "eCPC", width: 115, sortable: true },
    { id: "ctr", label: "CTR", width: 105, sortable: true },
    { id: "view_content", label: "عرض المحتوى", sublabel: "View Content", width: 140, sortable: true },
    { id: "add_to_cart", label: "إضافة للسلة", sublabel: "Add to Cart", width: 140, sortable: true },
    { id: "start_checkout", label: "بدء الدفع", sublabel: "Start Checkout", width: 140, sortable: true },
    { id: "add_billing", label: "بيانات الدفع", sublabel: "Add Billing", width: 140, sortable: true },
    { id: "budget", label: "الميزانية اليومية", width: 150, sortable: true },
    { id: "account", label: "الحساب الإعلاني", width: 230, sortable: true },
]);

const STATUS_LABELS = Object.freeze({
    ACTIVE: "نشطة",
    active: "نشطة",
    ENABLED: "نشطة",
    enabled: "نشطة",
    PAUSED: "متوقفة",
    paused: "متوقفة",
    ARCHIVED: "مؤرشفة",
    archived: "مؤرشفة",
    DELETED: "محذوفة",
    deleted: "محذوفة",
    unknown: "غير محسومة",
});

function finiteNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function formatMoney(value) {
    const parsed = finiteNumber(value);
    if (parsed === null) return "—";
    return `${parsed.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ر.س`;
}

function formatNumber(value) {
    const parsed = finiteNumber(value);
    if (parsed === null) return "—";
    return parsed.toLocaleString("en-US", {
        maximumFractionDigits: Number.isInteger(parsed) ? 0 : 2,
    });
}

function formatRatio(value, suffix = "") {
    const parsed = finiteNumber(value);
    if (parsed === null) return "—";
    return `${parsed.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })}${suffix}`;
}

function normalizeStatus(value) {
    return String(value || "unknown").trim() || "unknown";
}

function isActiveStatus(value) {
    return ["active", "enabled", "delivering"].includes(
        normalizeStatus(value).toLowerCase(),
    );
}

function statusLabel(value) {
    const status = normalizeStatus(value);
    return STATUS_LABELS[status] || STATUS_LABELS[status.toLowerCase()] || status;
}

function deliveryLabel(campaign) {
    const raw = String(campaign.delivery_status || "").trim();
    if (raw) return raw;
    return isActiveStatus(campaign.status) ? "يتم التسليم" : "غير نشط";
}

function defaultColumnsForPlatform(platform, resultSource = "salla") {
    const columns = platform === "snapchat" && resultSource === "salla"
        ? [...CAMPAIGN_MANAGER_DEFAULT_COLUMNS]
        : CAMPAIGN_MANAGER_DEFAULT_COLUMNS.filter(
            (id) => !PROFITABILITY_COLUMNS.includes(id),
        );
    return platform === "snapchat"
        ? columns
        : columns.filter((id) => !SNAPCHAT_METRIC_COLUMNS.includes(id));
}

function columnLabel(column, platform, resultSource) {
    if (
        platform === "snapchat"
        && resultSource === "platform"
        && column.id === "sales"
    ) {
        return "قيمة مشتريات Snapchat";
    }
    return column.label;
}

function normalizeVisibleColumns(stored, availableDefinitions, defaults) {
    const allowed = new Set(availableDefinitions.map((column) => column.id));
    const requested = new Set(Array.isArray(stored) ? stored : defaults);
    REQUIRED_COLUMNS.forEach((id) => {
        if (allowed.has(id)) requested.add(id);
    });
    return availableDefinitions
        .map((column) => column.id)
        .filter((id) => requested.has(id));
}

export function campaignRowKey(campaign) {
    return `${campaign?.account_id || "unknown"}:${campaign?.campaign_id || "unknown"}`;
}

function valueForColumn(campaign, columnId) {
    switch (columnId) {
        case "name": return String(campaign.campaign_name || campaign.campaign_id || "");
        case "status": return normalizeStatus(campaign.status);
        case "delivery": return deliveryLabel(campaign);
        case "orders": return finiteNumber(campaign.salla_orders);
        case "cpa": return finiteNumber(campaign.salla_cpa_sar);
        case "roas": return finiteNumber(campaign.salla_roas);
        case "spend": return finiteNumber(campaign.snapchat_spend_sar);
        case "sales": return finiteNumber(campaign.salla_sales_sar);
        case "snapchat_purchases": return finiteNumber(campaign.snapchat_purchases);
        case "snapchat_value": return finiteNumber(campaign.snapchat_purchase_value_sar);
        case "snapchat_roas": return finiteNumber(campaign.snapchat_roas);
        case "product_cost": return finiteNumber(campaign.salla_profitability?.product_cost_sar);
        case "profit": return finiteNumber(campaign.salla_profitability?.contribution_profit_sar);
        case "profit_margin": return finiteNumber(campaign.salla_profitability?.profit_margin_pct);
        case "impressions": return finiteNumber(campaign.impressions);
        case "paid_reach": return finiteNumber(campaign.paid_reach);
        case "paid_frequency": return finiteNumber(campaign.paid_frequency);
        case "cpm": return finiteNumber(campaign.cpm_sar);
        case "clicks": return finiteNumber(campaign.swipes);
        case "cpc": return finiteNumber(campaign.cpc_sar);
        case "ctr": return finiteNumber(campaign.ctr_pct);
        case "view_content": return finiteNumber(campaign.view_content);
        case "add_to_cart": return finiteNumber(campaign.add_to_cart);
        case "start_checkout": return finiteNumber(campaign.start_checkout);
        case "add_billing": return finiteNumber(campaign.add_billing);
        case "budget": return finiteNumber(campaign.budget?.daily_native);
        case "account": return String(campaign.account_name || campaign.account_id || "");
        default: return null;
    }
}

export function sortCampaignRows(rows, sort) {
    const source = Array.isArray(rows) ? [...rows] : [];
    const key = sort?.key || "spend";
    const direction = sort?.direction === "asc" ? 1 : -1;
    return source.sort((left, right) => {
        const a = valueForColumn(left, key);
        const b = valueForColumn(right, key);
        if (a === null && b === null) return 0;
        if (a === null) return 1;
        if (b === null) return -1;
        if (typeof a === "number" && typeof b === "number") {
            return (a - b) * direction;
        }
        return String(a).localeCompare(String(b), "ar", {
            numeric: true,
            sensitivity: "base",
        }) * direction;
    });
}

export function campaignTotalsForColumn(totals, columnId) {
    const value = totals || {};
    const profitability = value.salla_profitability || {};
    switch (columnId) {
        case "name": return "إجمالي الفترة";
        case "orders": return formatNumber(value.salla_matched_orders);
        case "cpa": return formatMoney(value.salla_cpa_sar);
        case "roas": return formatRatio(value.salla_roas, "×");
        case "spend": return formatMoney(value.snapchat_spend_sar);
        case "sales": return formatMoney(value.salla_sales_sar);
        case "snapchat_purchases": return formatNumber(value.snapchat_purchases);
        case "snapchat_value": return formatMoney(value.snapchat_purchase_value_sar);
        case "snapchat_roas": return formatRatio(value.snapchat_roas, "×");
        case "product_cost": return formatMoney(profitability.product_cost_sar);
        case "profit": return formatMoney(profitability.contribution_profit_sar);
        case "profit_margin": return formatRatio(profitability.profit_margin_pct, "%");
        case "impressions": return formatNumber(value.impressions);
        case "paid_reach": return formatNumber(value.paid_reach);
        case "paid_frequency": return formatRatio(value.paid_frequency, "×");
        case "cpm": return formatMoney(value.cpm_sar);
        case "clicks": return formatNumber(value.swipes);
        case "cpc": return formatMoney(value.cpc_sar);
        case "ctr": return formatRatio(value.ctr_pct, "%");
        case "view_content": return formatNumber(value.view_content);
        case "add_to_cart": return formatNumber(value.add_to_cart);
        case "start_checkout": return formatNumber(value.start_checkout);
        case "add_billing": return formatNumber(value.add_billing);
        default: return "";
    }
}

function MetricValue({ primary, secondary = "" }) {
    return (
        <div className="font-mono">
            <div className="font-extrabold text-slate-800">{primary}</div>
            {secondary && <div className="mt-0.5 text-[10px] font-semibold text-slate-400">{secondary}</div>}
        </div>
    );
}

function ProfitMetric({ campaign, onOpen }) {
    const profitability = campaign.salla_profitability || {};
    const profit = finiteNumber(profitability.contribution_profit_sar);
    const missing = Number(profitability.missing_cost_orders || 0);
    if (!Number(campaign.salla_orders || 0)) {
        return <MetricValue primary="—" secondary="لا توجد طلبات سلة مطابقة" />;
    }
    if (profit === null) {
        return (
            <button type="button" onClick={() => onOpen?.(campaign)} className="text-right">
                <div className="font-black text-amber-700">غير محسوم</div>
                <div className="mt-1 text-[10px] font-bold text-amber-600">
                    {missing} طلب بتكلفة ناقصة · عرض التفاصيل
                </div>
            </button>
        );
    }
    return (
        <button type="button" onClick={() => onOpen?.(campaign)} className="text-right">
            <div className={`font-mono font-black ${profit >= 0 ? "text-emerald-700" : "text-rose-700"}`}>
                {formatMoney(profit)}
            </div>
            <div className="mt-1 text-[10px] font-bold text-blue-600">تفاصيل المنتجات</div>
        </button>
    );
}

function ProductCostMetric({ campaign }) {
    const profitability = campaign.salla_profitability || {};
    const cost = finiteNumber(profitability.product_cost_sar);
    const known = finiteNumber(profitability.known_product_cost_sar);
    if (!Number(campaign.salla_orders || 0)) {
        return <MetricValue primary="—" secondary="لا توجد طلبات سلة مطابقة" />;
    }
    if (cost === null) {
        return <MetricValue primary="غير مكتملة" secondary={`المعروف ${formatMoney(known)}`} />;
    }
    const secondary = profitability.cost_status === "salla_fallback"
        ? "تتضمن تكلفة سلة احتياطية"
        : "تكلفة ميزان V2";
    return <MetricValue primary={formatMoney(cost)} secondary={secondary} />;
}

function cellValue(campaign, columnId, onOpenProfit, onOpenAdSquads) {
    switch (columnId) {
        case "name": {
            const content = (
                <div className="min-w-0">
                    <div className="max-w-[270px] truncate font-extrabold text-slate-950" title={campaign.campaign_name}>
                        {campaign.campaign_name || campaign.campaign_id}
                    </div>
                    {campaign.data_status === "outside_date_range" && (
                        <div className="mt-1 text-[10px] font-bold text-amber-700">
                            لا توجد بيانات Snapchat داخل الفترة
                        </div>
                    )}
                    <div className="mt-1 max-w-[270px] truncate font-mono text-[10px] text-slate-400">
                        {campaign.campaign_id}
                    </div>
                </div>
            );
            if (!onOpenAdSquads) return content;
            return (
                <button
                    type="button"
                    onClick={() => onOpenAdSquads(campaign)}
                    className="block w-full text-right underline-offset-4 hover:text-emerald-700 hover:underline"
                    aria-label={`عرض المجموعات الإعلانية لحملة ${campaign.campaign_name || campaign.campaign_id}`}
                >
                    {content}
                </button>
            );
        }
        case "status": {
            const active = isActiveStatus(campaign.status);
            return (
                <div className="flex items-center gap-2">
                    <span
                        className={`relative inline-flex h-5 w-9 shrink-0 rounded-full ${active ? "bg-emerald-500" : "bg-slate-300"}`}
                        aria-label={active ? "الحملة نشطة" : "الحملة غير نشطة"}
                    >
                        <span className={`absolute top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-white shadow-sm transition ${active ? "right-[18px]" : "right-0.5"}`}>
                            {active && <Check size={10} weight="bold" className="text-emerald-600" />}
                        </span>
                    </span>
                    <span className="text-xs font-bold text-slate-600">{statusLabel(campaign.status)}</span>
                </div>
            );
        }
        case "delivery": {
            const active = isActiveStatus(campaign.status);
            return (
                <div className="flex items-start gap-2">
                    <span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${active ? "bg-emerald-500 ring-2 ring-emerald-100" : "bg-slate-300"}`} />
                    <div>
                        <div className="font-bold text-slate-700">{deliveryLabel(campaign)}</div>
                        {active && <div className="mt-0.5 text-[10px] font-semibold text-slate-400">قد تكون في مرحلة التعلم</div>}
                    </div>
                </div>
            );
        }
        case "orders": return <MetricValue primary={formatNumber(campaign.salla_orders)} secondary="سلة · UTM ID" />;
        case "cpa": return <MetricValue primary={formatMoney(campaign.salla_cpa_sar)} secondary="سلة ÷ سناب" />;
        case "roas": return <MetricValue primary={formatRatio(campaign.salla_roas, "×")} secondary="سلة" />;
        case "spend": return <MetricValue primary={formatMoney(campaign.snapchat_spend_sar)} secondary="Snapchat" />;
        case "sales": return <MetricValue primary={formatMoney(campaign.salla_sales_sar)} secondary="سلة" />;
        case "snapchat_purchases": return <MetricValue primary={formatNumber(campaign.snapchat_purchases)} secondary="Snapchat" />;
        case "snapchat_value": return <MetricValue primary={formatMoney(campaign.snapchat_purchase_value_sar)} secondary="Snapchat" />;
        case "snapchat_roas": return <MetricValue primary={formatRatio(campaign.snapchat_roas, "×")} secondary="Snapchat" />;
        case "product_cost": return <ProductCostMetric campaign={campaign} />;
        case "profit": return <ProfitMetric campaign={campaign} onOpen={onOpenProfit} />;
        case "profit_margin": {
            const profitability = campaign.salla_profitability || {};
            const margin = finiteNumber(profitability.profit_margin_pct);
            return (
                <MetricValue
                    primary={formatRatio(margin, "%")}
                    secondary={margin === null ? "غير محسوم" : "قبل رسوم الدفع والشحن"}
                />
            );
        }
        case "impressions": return <MetricValue primary={formatNumber(campaign.impressions)} />;
        case "paid_reach": return <MetricValue primary={formatNumber(campaign.paid_reach)} secondary="نافذة TOTAL" />;
        case "paid_frequency": return <MetricValue primary={formatRatio(campaign.paid_frequency, "×")} secondary="نافذة TOTAL" />;
        case "cpm": return <MetricValue primary={formatMoney(campaign.cpm_sar)} />;
        case "clicks": return <MetricValue primary={formatNumber(campaign.swipes)} />;
        case "cpc": return <MetricValue primary={formatMoney(campaign.cpc_sar)} />;
        case "ctr": return <MetricValue primary={formatRatio(campaign.ctr_pct, "%")} />;
        case "view_content": return <MetricValue primary={formatNumber(campaign.view_content)} />;
        case "add_to_cart": return <MetricValue primary={formatNumber(campaign.add_to_cart)} />;
        case "start_checkout": return <MetricValue primary={formatNumber(campaign.start_checkout)} />;
        case "add_billing": return <MetricValue primary={formatNumber(campaign.add_billing)} />;
        case "budget": {
            const amount = finiteNumber(campaign.budget?.daily_native);
            return <MetricValue primary={amount === null ? "—" : `${formatNumber(amount)} ${campaign.budget?.currency || ""}`} />;
        }
        case "account":
            return (
                <div>
                    <div className="max-w-[210px] truncate font-bold text-slate-700" title={campaign.account_name}>
                        {campaign.account_name || "حساب غير معروف"}
                    </div>
                    <div className="mt-1 max-w-[210px] truncate font-mono text-[10px] text-slate-400">
                        {campaign.account_id || "—"}
                    </div>
                </div>
            );
        default: return "—";
    }
}

function productNavigation(product) {
    const productId = product.mezan_product_id || product.salla_product_id || "";
    return {
        href: buildProfitabilityProductCostHref({
            productId,
            sku: product.sku || "",
            name: product.name || "",
        }),
        missingCost: product.cost_status
            ? product.cost_status !== "complete"
            : finiteNumber(product.product_cost_sar) === null,
    };
}

function ProfitabilityDialog({ campaign, onClose }) {
    if (!campaign) return null;
    const profitability = campaign.salla_profitability || {};
    const products = profitability.products || [];
    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/55 p-4" role="dialog" aria-modal="true" data-testid="campaign-profitability-dialog">
            <div className="max-h-[92vh] w-full max-w-6xl overflow-hidden rounded-3xl bg-white shadow-2xl" dir="rtl">
                <header className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
                    <div>
                        <div className="text-xs font-black text-emerald-700">ربحية الحملة من طلبات سلة المطابقة بدقة</div>
                        <h2 className="mt-1 text-xl font-black text-slate-950">{campaign.campaign_name || campaign.campaign_id}</h2>
                        <div className="mt-1 font-mono text-xs text-slate-400">{campaign.campaign_id}</div>
                    </div>
                    <button type="button" onClick={onClose} className="rounded-xl p-2 text-slate-500 hover:bg-slate-100" aria-label="إغلاق تفاصيل الربحية"><X size={22} /></button>
                </header>

                <div className="max-h-[calc(92vh-84px)] overflow-y-auto p-5">
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                        {[
                            ["مبيعات سلة", formatMoney(profitability.sales_sar)],
                            ["تكلفة المنتجات", formatMoney(profitability.product_cost_sar)],
                            ["الصرف الإعلاني", formatMoney(profitability.ad_spend_sar)],
                            ["ربح المساهمة", formatMoney(profitability.contribution_profit_sar)],
                            ["هامش الربح", formatRatio(profitability.profit_margin_pct, "%")],
                        ].map(([label, value]) => (
                            <div key={label} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                                <div className="text-xs font-black text-slate-500">{label}</div>
                                <div className="mt-2 font-mono text-lg font-black text-slate-950">{value}</div>
                            </div>
                        ))}
                    </div>

                    <div className="mt-4 flex items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm font-bold leading-6 text-amber-900">
                        <WarningCircle size={21} weight="fill" className="mt-0.5 shrink-0" />
                        <span>
                            الربح الحالي = مبيعات سلة − تكلفة المنتجات − الصرف الإعلاني. رسوم بوابات الدفع وBNPL وتكلفة الشحن التشغيلية ستضاف في المرحلة التالية. توزيع الصرف على المنتجات يتم حسب حصة مبيعات كل منتج داخل الحملة.
                        </span>
                    </div>

                    <div className="mt-5 overflow-x-auto rounded-2xl border border-slate-200">
                        <table className="w-max min-w-full text-right text-sm">
                            <thead className="bg-slate-50 text-slate-600">
                                <tr>
                                    <th className="min-w-[330px] px-4 py-3 font-black">المنتج</th>
                                    <th className="min-w-[100px] px-4 py-3 font-black">الكمية</th>
                                    <th className="min-w-[135px] px-4 py-3 font-black">المبيعات</th>
                                    <th className="min-w-[145px] px-4 py-3 font-black">تكلفة المنتج</th>
                                    <th className="min-w-[155px] px-4 py-3 font-black">الصرف الموزع</th>
                                    <th className="min-w-[155px] px-4 py-3 font-black">الربح</th>
                                    <th className="min-w-[115px] px-4 py-3 font-black">الهامش</th>
                                </tr>
                            </thead>
                            <tbody>
                                {products.map((product) => {
                                    const navigation = productNavigation(product);
                                    return (
                                        <tr key={product.identity} className="border-t border-slate-100">
                                            <td className="px-4 py-4">
                                                <div className="flex items-start gap-3">
                                                    {product.image_url ? (
                                                        <img src={product.image_url} alt="" className="h-12 w-12 rounded-xl object-cover" />
                                                    ) : (
                                                        <span className="rounded-xl bg-slate-100 p-3 text-slate-500"><Package size={22} /></span>
                                                    )}
                                                    <div>
                                                        <div className="max-w-[230px] truncate font-black text-slate-900">{product.name}</div>
                                                        <div className="mt-1 font-mono text-[10px] text-slate-400">{product.sku || product.salla_product_id || product.identity}</div>
                                                        {product.cost_status === "salla_fallback" && (
                                                            <div className="mt-1 text-[10px] font-black text-amber-700" data-testid="campaign-product-cost-source">
                                                                التكلفة الحالية من سلة؛ أضف تكلفة ميزان
                                                            </div>
                                                        )}
                                                        {product.cost_status === "missing" && (
                                                            <div className="mt-1 text-[10px] font-black text-rose-700" data-testid="campaign-product-cost-source">
                                                                لا توجد تكلفة منتج محسومة
                                                            </div>
                                                        )}
                                                        <a
                                                            href={navigation.href}
                                                            onClick={() => rememberSnapchatRangeBeforeProductNavigation()}
                                                            className={`mt-2 inline-flex rounded-lg border px-3 py-1.5 text-xs font-black transition ${navigation.missingCost ? "border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100" : "border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100"}`}
                                                            data-testid="campaign-profitability-product-link"
                                                        >
                                                            {navigation.missingCost ? "فتح المنتج وإضافة التكلفة" : "فتح المنتج"}
                                                        </a>
                                                    </div>
                                                </div>
                                            </td>
                                            <td className="px-4 py-4 font-mono font-black">{formatNumber(product.units)}</td>
                                            <td className="px-4 py-4 font-mono font-black">{formatMoney(product.sales_sar)}</td>
                                            <td className="px-4 py-4 font-mono font-black">{formatMoney(product.product_cost_sar)}</td>
                                            <td className="px-4 py-4 font-mono font-black">{formatMoney(product.allocated_ad_spend_sar)}</td>
                                            <td className={`px-4 py-4 font-mono font-black ${finiteNumber(product.contribution_profit_sar) >= 0 ? "text-emerald-700" : "text-rose-700"}`}>{formatMoney(product.contribution_profit_sar)}</td>
                                            <td className="px-4 py-4 font-mono font-black">{formatRatio(product.profit_margin_pct, "%")}</td>
                                        </tr>
                                    );
                                })}
                                {!products.length && (
                                    <tr><td colSpan={7} className="px-6 py-12 text-center font-bold text-slate-500">لا توجد منتجات قابلة للتوزيع داخل الطلبات المطابقة.</td></tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    );
}

function csvEscape(value) {
    const text = String(value ?? "");
    return `"${text.replaceAll('"', '""')}"`;
}

function campaignCsv(rows) {
    const headers = [
        "اسم الحملة", "معرف الحملة", "الحالة", "حالة التسليم", "الحساب",
        "صرف Snapchat", "طلبات سلة", "مبيعات سلة", "ROAS سلة", "CPA سلة",
        "مشتريات Snapchat", "قيمة شراء Snapchat", "ROAS Snapchat", "CPA Snapchat",
        "تكلفة المنتجات", "ربح الحملة بعد الإعلان", "هامش الربح",
        "الظهور", "الوصول المدفوع", "التكرار المدفوع", "النقرات", "CTR", "eCPC", "eCPM",
        "عرض المحتوى", "إضافة للسلة", "بدء الدفع", "بيانات الدفع",
        "الميزانية اليومية", "عملة الميزانية",
    ];
    const body = rows.map((campaign) => [
        campaign.campaign_name,
        campaign.campaign_id,
        statusLabel(campaign.status),
        deliveryLabel(campaign),
        campaign.account_name,
        campaign.snapchat_spend_sar,
        campaign.salla_orders,
        campaign.salla_sales_sar,
        campaign.salla_roas,
        campaign.salla_cpa_sar,
        campaign.snapchat_purchases,
        campaign.snapchat_purchase_value_sar,
        campaign.snapchat_roas,
        campaign.snapchat_cpa_sar,
        campaign.salla_profitability?.product_cost_sar,
        campaign.salla_profitability?.contribution_profit_sar,
        campaign.salla_profitability?.profit_margin_pct,
        campaign.impressions,
        campaign.paid_reach,
        campaign.paid_frequency,
        campaign.swipes,
        campaign.ctr_pct,
        campaign.cpc_sar,
        campaign.cpm_sar,
        campaign.view_content,
        campaign.add_to_cart,
        campaign.start_checkout,
        campaign.add_billing,
        campaign.budget?.daily_native,
        campaign.budget?.currency,
    ]);
    return [headers, ...body].map((row) => row.map(csvEscape).join(",")).join("\n");
}

function downloadRows(rows, platform) {
    if (typeof window === "undefined" || typeof document === "undefined" || !rows.length) return;
    const blob = new Blob(["\ufeff", campaignCsv(rows)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `mezan-${platform || "ads"}-campaigns.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

function ToolButton({ Icon, label, disabled = false, onClick, testid }) {
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-xs font-bold text-slate-600 transition hover:bg-slate-100 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-35"
            data-testid={testid}
        >
            <Icon size={16} weight="duotone" />
            {label}
        </button>
    );
}

function stickyOffset(columnId) {
    if (columnId === "name") return CHECKBOX_WIDTH;
    if (columnId === "status") return CHECKBOX_WIDTH + NAME_WIDTH;
    return null;
}

function columnCellClass(columnId, background, z = "z-10") {
    const sticky = stickyOffset(columnId) !== null;
    return [
        sticky ? `sticky ${z} border-l ${background}` : "",
        "border-b border-slate-100 px-4 py-4 align-middle",
    ].join(" ");
}

export default function CampaignManagerTable({
    platform,
    platformLabel,
    resultSource = "salla",
    campaigns = [],
    totals = {},
    pagination = {},
    page = 1,
    onPageChange,
    loading = false,
    readOnly = true,
    onOpenAdSquads,
}) {
    const storageKey = `mezan-campaign-manager-columns-v3:${platform || "all"}:${resultSource}`;
    const availableDefinitions = COLUMN_DEFINITIONS.filter(
        (column) => (
            ((platform === "snapchat" && resultSource === "salla")
                || !PROFITABILITY_COLUMNS.includes(column.id))
            && (platform === "snapchat" || !SNAPCHAT_METRIC_COLUMNS.includes(column.id))
        ),
    );
    const defaultColumns = defaultColumnsForPlatform(platform, resultSource);
    const [selected, setSelected] = useState(() => new Set());
    const [showSelectedOnly, setShowSelectedOnly] = useState(false);
    const [sort, setSort] = useState({ key: "spend", direction: "desc" });
    const [profitCampaign, setProfitCampaign] = useState(null);
    const [visibleColumns, setVisibleColumns] = useState(() => {
        if (typeof window === "undefined") return defaultColumns;
        try {
            const stored = JSON.parse(window.localStorage.getItem(storageKey) || "null");
            return normalizeVisibleColumns(stored, availableDefinitions, defaultColumns);
        } catch {
            return defaultColumns;
        }
    });

    useEffect(() => {
        setSelected(new Set());
        setShowSelectedOnly(false);
        setProfitCampaign(null);
    }, [campaigns]);

    useEffect(() => {
        if (typeof window === "undefined") return;
        try {
            const stored = JSON.parse(window.localStorage.getItem(storageKey) || "null");
            setVisibleColumns(normalizeVisibleColumns(
                stored,
                availableDefinitions,
                defaultColumns,
            ));
        } catch {
            setVisibleColumns(defaultColumns);
        }
    }, [storageKey, platform, resultSource]);

    useEffect(() => {
        if (typeof window === "undefined") return;
        window.localStorage.setItem(storageKey, JSON.stringify(visibleColumns));
    }, [storageKey, visibleColumns]);

    const sortedRows = useMemo(
        () => sortCampaignRows(campaigns, sort),
        [campaigns, sort],
    );
    const rows = useMemo(
        () => showSelectedOnly
            ? sortedRows.filter((campaign) => selected.has(campaignRowKey(campaign)))
            : sortedRows,
        [selected, showSelectedOnly, sortedRows],
    );
    const allVisibleSelected = rows.length > 0
        && rows.every((campaign) => selected.has(campaignRowKey(campaign)));
    const columns = availableDefinitions.filter((column) => visibleColumns.includes(column.id));
    const paginationState = infinitePaginationState({
        pagination,
        requestedPage: page,
        loaded: campaigns.length,
    });
    const totalPages = paginationState.pages;
    const currentPage = paginationState.page;
    const infiniteScroll = platform === "snapchat";

    function toggleRow(campaign) {
        const key = campaignRowKey(campaign);
        setSelected((current) => {
            const next = new Set(current);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });
    }

    function toggleAllVisible() {
        setSelected((current) => {
            const next = new Set(current);
            if (allVisibleSelected) rows.forEach((campaign) => next.delete(campaignRowKey(campaign)));
            else rows.forEach((campaign) => next.add(campaignRowKey(campaign)));
            return next;
        });
    }

    function toggleColumn(columnId) {
        if (REQUIRED_COLUMN_SET.has(columnId)) return;
        setVisibleColumns((current) => {
            const next = current.includes(columnId)
                ? current.filter((id) => id !== columnId)
                : [...current, columnId];
            return normalizeVisibleColumns(next, availableDefinitions, defaultColumns);
        });
    }

    function changeSort(columnId) {
        setSort((current) => ({
            key: columnId,
            direction: current.key === columnId && current.direction === "desc" ? "asc" : "desc",
        }));
    }

    return (
        <section
            className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
            data-testid="campaign-manager-table"
            data-native-column-layout="true"
            dir="rtl"
        >
            <div className="flex min-h-14 items-end gap-6 overflow-x-auto border-b border-slate-200 px-4">
                <button type="button" className="relative shrink-0 px-1 pb-3 pt-4 text-sm font-black text-slate-950">
                    الحملات
                    <span className="absolute inset-x-0 bottom-0 h-0.5 bg-amber-400" />
                </button>
                <button type="button" disabled className="shrink-0 px-1 pb-3 pt-4 text-sm font-bold text-slate-400">المجموعات الإعلانية</button>
                <button type="button" disabled className="shrink-0 px-1 pb-3 pt-4 text-sm font-bold text-slate-400">الإعلانات</button>
                <span className="mr-auto pb-3 text-[11px] font-bold text-slate-400">
                    {platformLabel || "المنصة"} · قراءة فقط
                </span>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-3 py-2">
                <div className="flex flex-wrap items-center gap-1">
                    <ToolButton
                        Icon={Eye}
                        label={showSelectedOnly ? "عرض الكل" : `عرض المحدد${selected.size ? ` (${selected.size})` : ""}`}
                        disabled={!showSelectedOnly && selected.size === 0}
                        onClick={() => setShowSelectedOnly((value) => !value)}
                        testid="campaign-manager-view-selected"
                    />
                    <ToolButton Icon={PencilSimple} label="تعديل" disabled={readOnly || selected.size === 0} />
                    <ToolButton Icon={Trash} label="حذف" disabled={readOnly || selected.size === 0} />
                </div>
                <div className="flex flex-wrap items-center gap-1">
                    <details className="relative">
                        <summary className="inline-flex min-h-9 cursor-pointer list-none items-center gap-1.5 rounded-lg px-2.5 text-xs font-bold text-slate-600 hover:bg-slate-100 hover:text-slate-950">
                            <Columns size={16} weight="duotone" />
                            الأعمدة
                        </summary>
                        <div className="absolute left-0 z-30 mt-2 w-72 rounded-xl border border-slate-200 bg-white p-3 shadow-xl">
                            <div className="mb-2 flex items-center justify-between">
                                <span className="text-xs font-black text-slate-800">تخصيص الأعمدة</span>
                                <button
                                    type="button"
                                    onClick={() => setVisibleColumns(defaultColumns)}
                                    className="text-[10px] font-bold text-emerald-700 hover:underline"
                                >
                                    إعادة الافتراضي
                                </button>
                            </div>
                            <div className="grid max-h-80 gap-1 overflow-auto">
                                {availableDefinitions.map((column) => (
                                    <label key={column.id} className="flex cursor-pointer items-center justify-between gap-3 rounded-lg px-2 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50">
                                        <span>{columnLabel(column, platform, resultSource)}</span>
                                        <input
                                            type="checkbox"
                                            checked={visibleColumns.includes(column.id)}
                                            disabled={REQUIRED_COLUMN_SET.has(column.id)}
                                            onChange={() => toggleColumn(column.id)}
                                            className="h-4 w-4 accent-emerald-600"
                                        />
                                    </label>
                                ))}
                            </div>
                        </div>
                    </details>
                    <ToolButton Icon={FileText} label="التقارير" disabled />
                    <ToolButton Icon={Funnel} label="التقسيم" disabled />
                    <ToolButton Icon={UploadSimple} label="رفع" disabled />
                    <ToolButton
                        Icon={DownloadSimple}
                        label="تنزيل"
                        disabled={rows.length === 0}
                        onClick={() => downloadRows(rows, platform)}
                        testid="campaign-manager-download"
                    />
                </div>
            </div>

            <div className="overflow-x-auto" data-testid="campaign-manager-scroll">
                <table className="w-max min-w-full border-separate border-spacing-0 text-right text-xs">
                    <thead className="sticky top-0 z-20 bg-slate-50 text-slate-600">
                        <tr>
                            <th className="sticky right-0 z-40 w-12 border-b border-l border-slate-200 bg-slate-50 px-3 py-3 text-center">
                                <input
                                    type="checkbox"
                                    checked={allVisibleSelected}
                                    onChange={toggleAllVisible}
                                    aria-label="تحديد كل الحملات الظاهرة"
                                    className="h-4 w-4 accent-emerald-600"
                                />
                            </th>
                            {columns.map((column) => {
                                const offset = stickyOffset(column.id);
                                return (
                                    <th
                                        key={column.id}
                                        className={`${offset !== null ? "sticky z-30 border-l bg-slate-50" : ""} border-b border-slate-200 px-4 py-3 align-bottom`}
                                        style={{
                                            minWidth: column.width,
                                            width: column.width,
                                            ...(offset !== null ? { right: offset } : {}),
                                        }}
                                        data-column-id={column.id}
                                    >
                                        {column.sortable ? (
                                            <button
                                                type="button"
                                                onClick={() => changeSort(column.id)}
                                                className="flex w-full items-end justify-between gap-2 text-right font-black hover:text-slate-950"
                                            >
                                                <span>
                                                    <span className="block">{columnLabel(column, platform, resultSource)}</span>
                                                    {column.sublabel && <span className="mt-0.5 block text-[9px] font-semibold text-slate-400">{column.sublabel}</span>}
                                                </span>
                                                {sort.key === column.id && (
                                                    <span className="text-[10px] text-emerald-700">{sort.direction === "desc" ? "↓" : "↑"}</span>
                                                )}
                                            </button>
                                        ) : column.label}
                                    </th>
                                );
                            })}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((campaign) => {
                            const key = campaignRowKey(campaign);
                            const checked = selected.has(key);
                            const background = checked ? "bg-emerald-50" : "bg-white group-hover:bg-slate-50";
                            return (
                                <tr key={key} className={`${checked ? "bg-emerald-50/60" : "bg-white"} group hover:bg-slate-50`}>
                                    <td className={`${background} sticky right-0 z-20 border-b border-l border-slate-100 px-3 py-4 text-center`}>
                                        <input
                                            type="checkbox"
                                            checked={checked}
                                            onChange={() => toggleRow(campaign)}
                                            aria-label={`تحديد ${campaign.campaign_name || campaign.campaign_id}`}
                                            className="h-4 w-4 accent-emerald-600"
                                        />
                                    </td>
                                    {columns.map((column) => {
                                        const offset = stickyOffset(column.id);
                                        return (
                                            <td
                                                key={column.id}
                                                className={columnCellClass(column.id, background)}
                                                style={{
                                                    minWidth: column.width,
                                                    width: column.width,
                                                    ...(offset !== null ? { right: offset } : {}),
                                                }}
                                                data-column-id={column.id}
                                            >
                                                {cellValue(campaign, column.id, setProfitCampaign, onOpenAdSquads)}
                                            </td>
                                        );
                                    })}
                                </tr>
                            );
                        })}
                        {rows.length === 0 && (
                            <tr>
                                <td colSpan={columns.length + 1} className="px-6 py-16 text-center">
                                    <ChartBar size={38} weight="duotone" className="mx-auto text-slate-300" />
                                    <div className="mt-3 font-black text-slate-600">
                                        {showSelectedOnly ? "لا توجد حملات محددة لعرضها." : "لا توجد حملات موثقة ضمن الفترة أو البحث."}
                                    </div>
                                </td>
                            </tr>
                        )}
                    </tbody>
                    {rows.length > 0 && (
                        <tfoot className="bg-slate-50/95 font-black text-slate-800">
                            <tr>
                                <td className="sticky right-0 z-20 border-l border-t-2 border-slate-300 bg-slate-50 px-3 py-3" />
                                {columns.map((column) => {
                                    const offset = stickyOffset(column.id);
                                    return (
                                        <td
                                            key={column.id}
                                            className={`${offset !== null ? "sticky z-10 border-l bg-slate-50" : ""} border-t-2 border-slate-300 px-4 py-3 font-mono`}
                                            style={{
                                                ...(offset !== null ? { right: offset } : {}),
                                                minWidth: column.width,
                                                width: column.width,
                                            }}
                                            data-column-id={column.id}
                                        >
                                            {campaignTotalsForColumn(totals, column.id)}
                                        </td>
                                    );
                                })}
                            </tr>
                        </tfoot>
                    )}
                </table>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-white px-4 py-3">
                <div className="flex items-center gap-2 text-xs font-bold text-slate-500">
                    <span className="relative inline-flex h-5 w-9 rounded-full bg-slate-300">
                        <span className="absolute right-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow-sm" />
                    </span>
                    الحملات المحذوفة مستبعدة من الإجماليات
                </div>
                <div className="flex items-center gap-3">
                    <span className="text-xs font-bold text-slate-500">
                        {infiniteScroll
                            ? `تم عرض ${formatNumber(campaigns.length)} من ${formatNumber(paginationState.total)} حملة`
                            : `${formatNumber(pagination.total || campaigns.length)} حملة · الصفحة ${currentPage}${totalPages > 0 ? ` من ${totalPages}` : ""}`}
                    </span>
                    {!infiniteScroll && totalPages > 1 && (
                        <>
                            <button
                                type="button"
                                disabled={currentPage <= 1}
                                onClick={() => onPageChange?.(Math.max(1, currentPage - 1))}
                                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-black text-slate-700 disabled:opacity-35"
                            >
                                السابق
                            </button>
                            <button
                                type="button"
                                disabled={currentPage >= totalPages}
                                onClick={() => onPageChange?.(Math.min(totalPages, currentPage + 1))}
                                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-black text-slate-700 disabled:opacity-35"
                            >
                                التالي
                            </button>
                        </>
                    )}
                </div>
            </div>
            {infiniteScroll && (
                <InfiniteScrollSentinel
                    hasMore={paginationState.hasMore}
                    loading={loading}
                    loaded={campaigns.length}
                    total={paginationState.total}
                    entityLabel="حملة"
                    onLoadMore={() => onPageChange?.(currentPage + 1)}
                    testId="campaign-infinite-scroll-sentinel"
                />
            )}
            <ProfitabilityDialog campaign={profitCampaign} onClose={() => setProfitCampaign(null)} />
        </section>
    );
}
