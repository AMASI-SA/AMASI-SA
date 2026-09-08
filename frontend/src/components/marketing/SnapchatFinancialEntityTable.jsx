import { useEffect, useState } from "react";
import { CaretDown, CaretLeft, MagnifyingGlass, PencilSimple } from "@phosphor-icons/react";

import { snapchatBidLabel } from "../../services/snapchatCampaignManagement";

const LEVEL_LABELS = {
    campaign: "الحملات",
    ad_group: "المجموعات الإعلانية",
    ad: "الإعلانات",
};

function number(value) {
    if (value === null || value === undefined || value === "") return "—";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString("en-US") : "—";
}

function money(value) {
    if (value?.amount === null || value?.amount === undefined || value?.amount === "") return "—";
    const parsed = Number(value.amount);
    if (!Number.isFinite(parsed)) return "—";
    return `${parsed.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${value.currency || ""}`.trim();
}

function ratio(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? `${parsed.toFixed(2)}×` : "—";
}

function commerce(row) {
    return row?.commerce_outcomes || {};
}

function profitability(row) {
    return row?.commerce_profitability || {};
}

function sallaCpa(row) {
    const orders = Number(commerce(row).orders);
    const spend = Number(row?.delivery?.spend_sar?.amount);
    return {
        amount: commerce(row).status === "complete" && orders > 0 && Number.isFinite(spend)
            ? spend / orders
            : null,
        currency: "SAR",
    };
}

function settingsBudget(settings, level) {
    if (settings?.quality?.settings_status !== "settings_complete") return "غير متاح";
    if (level === "campaign") {
        if (settings?.ad_squad_daily_budget_sum_availability !== "available") return "غير متاح";
        return money({ amount: settings?.ad_squad_daily_budget_sum_account_currency ?? settings?.campaign_aggregate?.daily_budget_sum_account_currency ?? settings?.ad_squads_daily_budget_usd, currency: settings?.account_currency });
    }
    if (settings?.daily_budget_availability !== "available") return "غير متاح";
    return money({ amount: settings?.daily_budget_account_currency, currency: settings?.account_currency });
}

function bidSummary(settings) {
    if (settings?.quality?.settings_status !== "settings_complete") return "غير متاح";
    const strategy = String(settings?.bid_strategy || "").toUpperCase();
    if (!strategy) {
        const values = Array.isArray(settings?.ad_squad_bid_strategies) ? settings.ad_squad_bid_strategies : [];
        return values.length ? values.join("، ") : "غير متاح";
    }
    if (strategy === "AUTO_BID") return "AUTO_BID · دون Target Cost";
    const amount = settings?.bid_account_currency;
    return amount === null || amount === undefined
        ? strategy
        : `${strategy} · ${snapchatBidLabel(strategy)} ${money({ amount, currency: settings?.account_currency })}`;
}

function SecondaryMetrics({ row }) {
    const values = [
        ["ظهور Snapchat", row?.delivery?.impressions],
        ["مشاهدات Snapchat", row?.delivery?.views],
        ["نقرات/سحب Snapchat", row?.delivery?.clicks],
        ["وصول Snapchat", row?.delivery?.reach],
        ["تكرار Snapchat", row?.delivery?.frequency],
        ["إضافة للسلة في Snapchat", row?.platform_outcomes?.add_to_cart],
        ["بدء الدفع في Snapchat", row?.platform_outcomes?.start_checkout],
    ];
    return (
        <div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-4" data-testid="snapchat-secondary-metrics">
            {values.map(([label, value]) => (
                <div key={label} className="rounded-xl border border-slate-200 bg-white p-3">
                    <div className="text-[10px] font-black text-slate-500">{label}</div>
                    <div className="mt-1 font-mono text-sm font-black text-slate-900">{number(value)}</div>
                </div>
            ))}
        </div>
    );
}

export default function SnapchatFinancialEntityTable({
    report,
    settingsByEntityId = {},
    loading = false,
    onOpenChildren,
    onManageEntity,
    onPageChange,
    onControlsChange,
}) {
    const rows = report?.rows || [];
    const level = report?.entity_level || "campaign";
    const totals = report?.totals || null;
    const page = Number(report?.page || 1);
    const pages = Number(report?.pages || 0);
    const total = Number(report?.total || 0);
    const filteredTotal = Number(report?.filtered_total ?? total);
    const filters = report?.filters || {};
    const sort = report?.sort || {};
    const canOpenChildren = ["campaign", "ad_group"].includes(level);
    const childLabel = level === "campaign" ? "Ad Squads" : "Ads";
    const [query, setQuery] = useState(filters.search || "");
    const [activeOnly, setActiveOnly] = useState(filters.active_only === true);
    const [sortValue, setSortValue] = useState(`${sort.by || "default"}:${sort.direction || "desc"}`);
    const [expandedId, setExpandedId] = useState("");

    useEffect(() => {
        setQuery(filters.search || "");
        setActiveOnly(filters.active_only === true);
        setSortValue(`${sort.by || "default"}:${sort.direction || "desc"}`);
    }, [filters.active_only, filters.search, sort.by, sort.direction]);

    useEffect(() => {
        const timer = window.setTimeout(() => {
            const [sortBy, sortDirection] = sortValue.split(":");
            const changed = query.trim() !== String(filters.search || "")
                || activeOnly !== (filters.active_only === true)
                || sortBy !== (sort.by || "default")
                || sortDirection !== (sort.direction || "desc");
            if (changed) onControlsChange?.({
                search: query.trim(), activeOnly, sortBy, sortDirection,
            });
        }, 300);
        return () => window.clearTimeout(timer);
    }, [activeOnly, filters.active_only, filters.search, onControlsChange, query, sort.by, sort.direction, sortValue]);

    const coreColumnCount = 17;
    return (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white" data-testid="snapchat-financial-entity-table">
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-4">
                <div>
                    <h3 className="text-lg font-black text-slate-950">{LEVEL_LABELS[level] || level}</h3>
                    <p className="mt-1 text-xs font-bold text-slate-500">صفحة خادمية bounded · {report?.contract_version || "—"}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <label className="relative block min-w-[230px]">
                        <MagnifyingGlass size={16} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input data-testid="snapchat-server-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="بحث خادمي بالاسم أو المعرّف" className="h-9 w-full rounded-xl border border-slate-200 bg-slate-50 pr-9 pl-3 text-xs font-bold" />
                    </label>
                    <button type="button" onClick={() => setActiveOnly((value) => !value)} aria-pressed={activeOnly} className={`h-9 rounded-xl border px-3 text-xs font-black ${activeOnly ? "border-emerald-300 bg-emerald-50 text-emerald-700" : "border-slate-200 text-slate-600"}`}>النشط فقط</button>
                    <select data-testid="snapchat-server-sort" value={sortValue} onChange={(event) => setSortValue(event.target.value)} className="h-9 rounded-xl border border-slate-200 bg-white px-3 text-xs font-black">
                        <option value="default:desc">الافتراضي: النشط ثم الصرف</option>
                        <option value="spend:desc">صرف Snapchat: الأعلى</option>
                        <option value="spend:asc">صرف Snapchat: الأقل</option>
                        <option value="name:asc">الاسم: تصاعدي</option>
                        <option value="name:desc">الاسم: تنازلي</option>
                    </select>
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-black">{number(filteredTotal)} من {number(total)}</span>
                </div>
            </header>
            <div className="overflow-x-auto">
                <table className="min-w-[1680px] w-full text-right text-xs" data-testid="snapchat-financial-columns">
                    <thead className="bg-slate-50 text-slate-600"><tr>
                        {["الكيان", "الحالة", "طلبات سلة", "مبيعات سلة", "صرف Snapchat", "CPA سلة", "تكلفة المنتجات", "ربح المساهمة", "هامش الربح", "ROAS سلة", "مشتريات Snapchat", "ROAS Snapchat", level === "campaign" ? "ميزانية Ad Squads الإجمالية" : "الميزانية اليومية", "Bid Strategy / Target Cost / Max Bid", "جودة البيانات", "تعديل / حالة", canOpenChildren ? childLabel : "التفاصيل"].map((label) => <th key={label} className="px-3 py-3 font-black">{label}</th>)}
                    </tr></thead>
                    <tbody className="divide-y divide-slate-100">
                        {rows.map((row) => {
                            const id = row?.entity?.id;
                            const settings = settingsByEntityId[id];
                            const profit = profitability(row);
                            const expanded = expandedId === id;
                            return [
                                <tr key={`${id}:main`} className="hover:bg-slate-50" data-testid={`snapchat-financial-row-${id}`}>
                                    <td className="px-3 py-4"><div className="max-w-[220px] truncate text-sm font-black" title={row.entity.name}>{row.entity.name}</div><div className="font-mono text-[10px] text-slate-400">{id}</div></td>
                                    <td className="px-3 py-4"><span className={`rounded-full px-2 py-1 font-black ${row.entity.active ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>{row.entity.status || "—"}</span></td>
                                    <td className="px-3 py-4 font-mono">{commerce(row).status === "complete" ? number(commerce(row).orders) : "—"}</td>
                                    <td className="px-3 py-4 font-mono">{commerce(row).status === "complete" ? money(commerce(row).revenue) : "—"}</td>
                                    <td className="px-3 py-4 font-mono font-black">{money(row?.delivery?.spend)}</td>
                                    <td className="px-3 py-4 font-mono">{money(sallaCpa(row))}</td>
                                    <td className="px-3 py-4 font-mono">{money(profit.product_cost)}</td>
                                    <td className="px-3 py-4 font-mono">{money(profit.contribution_profit)}</td>
                                    <td className="px-3 py-4 font-mono">{profit.profit_margin_pct == null ? "—" : `${Number(profit.profit_margin_pct).toFixed(2)}%`}</td>
                                    <td className="px-3 py-4 font-mono">{commerce(row).status === "complete" ? ratio(commerce(row).roas) : "—"}</td>
                                    <td className="px-3 py-4 font-mono">{number(row?.platform_outcomes?.conversions)}</td>
                                    <td className="px-3 py-4 font-mono">{ratio(row?.platform_outcomes?.roas)}</td>
                                    <td className="px-3 py-4 font-mono">{settingsBudget(settings, level)}</td>
                                    <td className="px-3 py-4 font-mono">{bidSummary(settings)}</td>
                                    <td className="px-3 py-4"><span className={`rounded-full px-2 py-1 font-black ${row.quality.coverage_status === "complete" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}>{row.quality.sync_status}</span><div className="mt-2 max-w-[210px] break-words font-mono text-[9px] text-slate-500">{settings?.quality?.settings_status || "settings_not_loaded"} · {settings?.provider_entity_id || "provider_id unavailable"} · {settings?.ad_account_id || "account unavailable"}</div>{settings?.quality?.reason && <div className="mt-1 max-w-[210px] text-[9px] font-bold text-rose-700">{settings.quality.reason}</div>}</td>
                                    <td className="px-3 py-4"><button type="button" data-testid={`manage-${id}`} onClick={() => onManageEntity?.(row)} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 font-black text-amber-800"><PencilSimple size={14} className="inline" /> تعديل / حالة</button></td>
                                    <td className="px-3 py-4"><div className="flex gap-2"><button type="button" aria-expanded={expanded} onClick={() => setExpandedId(expanded ? "" : id)} className="rounded-lg border border-slate-200 px-2 py-2 font-black"><CaretDown size={14} className="inline" /> مقاييس</button>{canOpenChildren && <button type="button" data-testid={`open-${id}`} onClick={() => onOpenChildren?.(row)} className="rounded-lg border border-emerald-200 px-2 py-2 font-black text-emerald-700">{childLabel} <CaretLeft size={14} className="inline" /></button>}</div></td>
                                </tr>,
                                expanded && <tr key={`${id}:secondary`} className="bg-slate-50"><td colSpan={coreColumnCount}><SecondaryMetrics row={row} /></td></tr>,
                            ];
                        })}
                        {!loading && !rows.length && <tr><td colSpan={coreColumnCount} className="px-4 py-12 text-center font-black text-slate-400">لا توجد كيانات ضمن المرشحات الحالية.</td></tr>}
                    </tbody>
                    {totals && <tfoot className="border-t-2 border-slate-300 bg-slate-50 font-black"><tr>
                        <td className="px-3 py-4">إجمالي النطاق المرشّح</td><td />
                        <td className="px-3 py-4">{commerce(totals).status === "complete" ? number(commerce(totals).orders) : "—"}</td>
                        <td className="px-3 py-4">{commerce(totals).status === "complete" ? money(commerce(totals).revenue) : "—"}</td>
                        <td className="px-3 py-4">{money(totals?.delivery?.spend)}</td>
                        <td className="px-3 py-4">{money(sallaCpa(totals))}</td>
                        <td className="px-3 py-4">{money(profitability(totals).product_cost)}</td>
                        <td className="px-3 py-4">{money(profitability(totals).contribution_profit)}</td>
                        <td className="px-3 py-4">{profitability(totals).profit_margin_pct == null ? "—" : `${Number(profitability(totals).profit_margin_pct).toFixed(2)}%`}</td>
                        <td className="px-3 py-4">{commerce(totals).status === "complete" ? ratio(commerce(totals).roas) : "—"}</td>
                        <td className="px-3 py-4">{number(totals?.platform_outcomes?.conversions)}</td><td className="px-3 py-4">{ratio(totals?.platform_outcomes?.roas)}</td>
                        <td /><td /><td className="px-3 py-4">{totals.quality.sync_status}</td><td /><td />
                    </tr></tfoot>}
                </table>
            </div>
            <footer className="flex items-center justify-between gap-3 border-t border-slate-200 px-4 py-3 text-xs font-black text-slate-600" data-testid="snapchat-server-pagination">
                <span>الصفحة {page} من {pages || 1} · المعروض {rows.length}</span>
                <div className="flex gap-2"><button type="button" disabled={loading || page <= 1} onClick={() => onPageChange?.(page - 1)} className="rounded-lg border border-slate-200 px-3 py-2 disabled:opacity-35">السابق</button><button type="button" disabled={loading || !pages || page >= pages} onClick={() => onPageChange?.(page + 1)} className="rounded-lg border border-slate-200 px-3 py-2 disabled:opacity-35">التالي</button></div>
            </footer>
        </section>
    );
}
