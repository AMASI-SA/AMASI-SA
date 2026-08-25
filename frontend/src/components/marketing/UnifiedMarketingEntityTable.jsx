import { useEffect, useMemo, useState } from "react";
import { CaretLeft, MagnifyingGlass, Package, PencilSimple, X } from "@phosphor-icons/react";
import { buildProfitabilityProductCostHref } from "../../campaignProfitabilityProductNavigation";

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
    const parsed = Number(value?.amount);
    if (!Number.isFinite(parsed)) return "—";
    return `${parsed.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ${value?.currency || ""}`.trim();
}

function ratio(value) {
    if (value === null || value === undefined || value === "") return "—";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? `${parsed.toFixed(2)}×` : "—";
}

function commerce(row) {
    return row?.commerce_outcomes || {};
}

function sallaCostPerOrder(row) {
    const orders = Number(commerce(row).orders);
    const spend = Number(row?.delivery?.spend_sar?.amount);
    if (commerce(row).status !== "complete" || !Number.isFinite(orders) || orders <= 0 || !Number.isFinite(spend)) {
        return { amount: null, currency: "SAR" };
    }
    return { amount: spend / orders, currency: "SAR" };
}

function snapchatCostPerPurchase(row) {
    const purchases = Number(row?.platform_outcomes?.conversions);
    const spend = Number(row?.delivery?.spend?.amount);
    const currency = row?.delivery?.spend?.currency || "USD";
    if (!Number.isFinite(purchases) || purchases <= 0 || !Number.isFinite(spend)) {
        return { amount: null, currency };
    }
    return { amount: spend / purchases, currency };
}

function profitability(row) {
    return row?.commerce_profitability || {};
}

function abandonedCarts(row) {
    return row?.abandoned_cart_outcomes || {};
}

function AbandonedCartsDialog({ row, onClose }) {
    if (!row) return null;
    const value = abandonedCarts(row);
    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/55 p-4" role="dialog" aria-modal="true" data-testid="unified-campaign-abandoned-carts-dialog">
            <div className="max-h-[88vh] w-full max-w-4xl overflow-hidden rounded-3xl bg-white shadow-2xl" dir="rtl">
                <header className="flex items-start justify-between border-b border-slate-200 px-5 py-4"><div><div className="text-xs font-black text-violet-700">سلات سلة المرتبطة بمعرف الحملة</div><h2 className="mt-1 text-xl font-black">{row.entity.name}</h2></div><button type="button" onClick={onClose} className="rounded-xl p-2 hover:bg-slate-100" aria-label="إغلاق"><X size={22} /></button></header>
                <div className="max-h-[calc(88vh-80px)] overflow-y-auto p-5">
                    <div className="grid gap-3 sm:grid-cols-3">{[["السلات المتروكة", number(value.abandoned_carts)], ["السلات المستعادة", number(value.recovered_carts)], ["قيمة المتروكة", money(value.abandoned_value)]].map(([label, metric]) => <div key={label} className="rounded-2xl border border-slate-200 bg-slate-50 p-4"><div className="text-xs font-black text-slate-500">{label}</div><div className="mt-2 font-mono text-lg font-black">{metric}</div></div>)}</div>
                    <p className="mt-4 rounded-2xl border border-violet-200 bg-violet-50 p-4 text-sm font-bold text-violet-900">هذه السلات دليل نية شراء مرتبط بمعرف الحملة، وليست مبيعات أو ربحًا للحملة حتى يتحول السجل إلى طلب مالي مؤهل في سلة.</p>
                    <div className="mt-5 overflow-hidden rounded-2xl border border-slate-200"><table className="w-full text-right text-sm"><thead className="bg-slate-50"><tr><th className="px-4 py-3 font-black">المنتج</th><th className="px-4 py-3 font-black">عدد السلات</th><th className="px-4 py-3 font-black">الكمية</th><th className="px-4 py-3 font-black">القيمة</th></tr></thead><tbody>{(value.top_products || []).map((product) => <tr key={product.product_id} className="border-t border-slate-100"><td className="px-4 py-4 font-black">{product.name || product.product_id}</td><td className="px-4 py-4 font-mono">{number(product.abandoned_carts)}</td><td className="px-4 py-4 font-mono">{number(product.units)}</td><td className="px-4 py-4 font-mono">{money(product.value)}</td></tr>)}{!(value.top_products || []).length && <tr><td colSpan={4} className="px-5 py-10 text-center font-bold text-slate-500">لا توجد منتجات في سلات مرتبطة بهذه الحملة خلال الفترة.</td></tr>}</tbody></table></div>
                </div>
            </div>
        </div>
    );
}

function ProfitabilityDialog({ row, onClose }) {
    if (!row) return null;
    const value = profitability(row);
    const products = value.products || [];
    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/55 p-4" role="dialog" aria-modal="true" data-testid="unified-campaign-profitability-dialog">
            <div className="max-h-[92vh] w-full max-w-6xl overflow-hidden rounded-3xl bg-white shadow-2xl" dir="rtl">
                <header className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
                    <div><div className="text-xs font-black text-emerald-700">المنتجات والربحية من طلبات سلة المطابقة</div><h2 className="mt-1 text-xl font-black">{row.entity.name}</h2></div>
                    <button type="button" onClick={onClose} className="rounded-xl p-2 hover:bg-slate-100" aria-label="إغلاق"><X size={22} /></button>
                </header>
                <div className="max-h-[calc(92vh-84px)] overflow-y-auto p-5">
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                        {[["مبيعات سلة", value.sales], ["تكلفة المنتجات", value.product_cost], ["الصرف الإعلاني", value.ad_spend], ["ربح المساهمة", value.contribution_profit], ["هامش الربح", { amount: value.profit_margin_pct, currency: "%" }]].map(([label, metric]) => <div key={label} className="rounded-2xl border border-slate-200 bg-slate-50 p-4"><div className="text-xs font-black text-slate-500">{label}</div><div className="mt-2 font-mono text-lg font-black">{money(metric)}</div></div>)}
                    </div>
                    <p className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm font-bold text-amber-900">الربح = مبيعات سلة − تكلفة المنتجات − صرف الحملة. لا يشمل حاليًا رسوم الدفع أو BNPL أو الشحن أو المصاريف التشغيلية.</p>
                    <div className="mt-5 overflow-x-auto rounded-2xl border border-slate-200">
                        <table className="w-max min-w-full text-right text-sm"><thead className="bg-slate-50 text-slate-600"><tr>{["المنتج", "الكمية", "المبيعات", "التكلفة", "الصرف الموزع", "الربح", "الهامش"].map((label) => <th key={label} className="px-4 py-3 font-black">{label}</th>)}</tr></thead>
                            <tbody>{products.map((product) => {
                                const missing = product.cost_status !== "complete";
                                const href = buildProfitabilityProductCostHref({ productId: product.mezan_product_id || product.salla_product_id, sku: product.sku, name: product.name });
                                return <tr key={product.identity} className="border-t border-slate-100"><td className="min-w-[330px] px-4 py-4"><div className="flex gap-3">{product.image_url ? <img src={product.image_url} alt="" className="h-12 w-12 rounded-xl object-cover" /> : <Package size={28} className="text-slate-400" />}<div><div className="font-black">{product.name}</div><div className="font-mono text-[10px] text-slate-400">{product.sku || product.salla_product_id}</div><a href={href} className={`mt-2 inline-flex rounded-lg border px-3 py-1.5 text-xs font-black ${missing ? "border-amber-300 bg-amber-50 text-amber-800" : "border-blue-200 bg-blue-50 text-blue-700"}`}>{missing ? "فتح المنتج وإضافة التكلفة" : "فتح المنتج"}</a></div></div></td><td className="px-4 py-4 font-mono">{number(product.units)}</td><td className="px-4 py-4 font-mono">{money(product.sales)}</td><td className="px-4 py-4 font-mono">{money(product.product_cost)}</td><td className="px-4 py-4 font-mono">{money(product.allocated_ad_spend)}</td><td className="px-4 py-4 font-mono">{money(product.contribution_profit)}</td><td className="px-4 py-4 font-mono">{product.profit_margin_pct == null ? "—" : `${Number(product.profit_margin_pct).toFixed(2)}%`}</td></tr>;
                            })}{!products.length && <tr><td colSpan={7} className="px-6 py-12 text-center font-bold text-slate-500">لا توجد منتجات في الطلبات المطابقة لهذه الحملة.</td></tr>}</tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    );
}

export default function UnifiedMarketingEntityTable({
    report,
    loading = false,
    onOpenChildren,
    onManageEntity,
}) {
    const allRows = report?.rows || [];
    const level = report?.entity_level || "campaign";
    const totals = report?.totals || null;
    const canOpenChildren = ["campaign", "ad_group"].includes(level);
    const childLabel = level === "campaign" ? "Ad Squads" : "Ads";
    const [query, setQuery] = useState("");
    const [activeOnly, setActiveOnly] = useState(false);
    const [page, setPage] = useState(1);
    const [profitRow, setProfitRow] = useState(null);
    const [cartRow, setCartRow] = useState(null);
    const pageSize = 25;
    const filteredRows = useMemo(() => {
        const needle = query.trim().toLocaleLowerCase();
        return allRows.filter((row) => {
            if (activeOnly && row.entity.active !== true) return false;
            if (!needle) return true;
            return [
                row.entity.name,
                row.entity.id,
                row.entity.status,
                row.entity.campaign_id,
                row.entity.ad_group_id,
            ].some((value) => String(value || "").toLocaleLowerCase().includes(needle));
        });
    }, [activeOnly, allRows, query]);
    const pages = Math.ceil(filteredRows.length / pageSize);
    const rows = filteredRows.slice((page - 1) * pageSize, page * pageSize);

    useEffect(() => {
        setPage(1);
    }, [activeOnly, query, report]);

    return (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white" data-testid="unified-marketing-entity-table">
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-4">
                <div>
                    <h3 className="text-lg font-black text-slate-950">{LEVEL_LABELS[level] || level}</h3>
                    <p className="mt-1 text-xs font-bold text-slate-500">
                        Unified Marketing Data Contract · {report?.contract_version || "—"}
                    </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <label className="relative block min-w-[230px]">
                        <MagnifyingGlass size={16} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="بحث بالاسم أو المعرف" className="h-9 w-full rounded-xl border border-slate-200 bg-slate-50 pr-9 pl-3 text-xs font-bold outline-none focus:border-emerald-400" />
                    </label>
                    <button type="button" onClick={() => setActiveOnly((value) => !value)} aria-pressed={activeOnly} className={`h-9 rounded-xl border px-3 text-xs font-black ${activeOnly ? "border-emerald-300 bg-emerald-50 text-emerald-700" : "border-slate-200 bg-white text-slate-600"}`}>النشط فقط</button>
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-black text-slate-700">{number(filteredRows.length)} من {number(allRows.length)}</span>
                    <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-black text-amber-800">
                        القرارات: غير مفعلة
                    </span>
                </div>
            </header>
            <div className="overflow-x-auto">
                <table className="min-w-[2450px] w-full text-right text-xs">
                    <thead className="bg-slate-50 text-slate-600">
                        <tr>
                            <th className="px-4 py-3 font-black">الكيان</th>
                            <th className="px-4 py-3 font-black">الحالة</th>
                            <th className="px-4 py-3 font-black">الصرف</th>
                            <th className="px-4 py-3 font-black">الظهور</th>
                            <th className="px-4 py-3 font-black">المشاهدات</th>
                            <th className="px-4 py-3 font-black">النقرات/السحب</th>
                            <th className="px-4 py-3 font-black">الوصول</th>
                            <th className="px-4 py-3 font-black">تكرار المشاهدة</th>
                            <th className="px-4 py-3 font-black">إضافة للسلة</th>
                            <th className="px-4 py-3 font-black">بدء الدفع</th>
                            <th className="px-4 py-3 font-black">مشتريات Snapchat</th>
                            <th className="px-4 py-3 font-black">تكلفة الطلب حسب Snapchat</th>
                            <th className="px-4 py-3 font-black">قيمة Snapchat</th>
                            <th className="px-4 py-3 font-black">ROAS Snapchat</th>
                            <th className="px-4 py-3 font-black">طلبات سلة</th>
                            <th className="px-4 py-3 font-black">تكلفة الطلب حسب سلة</th>
                            <th className="px-4 py-3 font-black">مبيعات سلة</th>
                            <th className="px-4 py-3 font-black">ROAS سلة</th>
                            {level === "campaign" && <th className="px-4 py-3 font-black">المنتجات والربح</th>}
                            {level === "campaign" && <th className="px-4 py-3 font-black">السلات المتروكة</th>}
                            <th className="px-4 py-3 font-black">جودة البيانات</th>
                            <th className="px-4 py-3 font-black">الإدارة</th>
                            {canOpenChildren && <th className="px-4 py-3 font-black">التفاصيل</th>}
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                        {rows.map((row) => (
                            <tr key={`${row.entity.level}:${row.entity.id}`} className="hover:bg-slate-50">
                                <td className="px-4 py-4">
                                    <div className="max-w-[260px] truncate text-sm font-black text-slate-950" title={row.entity.name}>{row.entity.name}</div>
                                    <div className="mt-1 font-mono text-[10px] text-slate-400">{row.entity.id}</div>
                                </td>
                                <td className="px-4 py-4">
                                    <span className={`rounded-full px-2 py-1 font-black ${row.entity.active === true ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>
                                        {row.entity.status || (row.entity.active ? "ACTIVE" : "—")}
                                    </span>
                                </td>
                                <td className="px-4 py-4 font-mono font-black" dir="ltr">{money(row.delivery.spend)}</td>
                                <td className="px-4 py-4 font-mono">{number(row.delivery.impressions)}</td>
                                <td className="px-4 py-4 font-mono">{number(row.delivery.views)}</td>
                                <td className="px-4 py-4 font-mono">{number(row.delivery.clicks)}</td>
                                <td className="px-4 py-4 font-mono">{number(row.delivery.reach)}</td>
                                <td className="px-4 py-4 font-mono">{row.delivery.frequency == null ? "—" : `${Number(row.delivery.frequency).toFixed(2)}×`}</td>
                                <td className="px-4 py-4 font-mono">{number(row.platform_outcomes.add_to_cart)}</td>
                                <td className="px-4 py-4 font-mono">{number(row.platform_outcomes.start_checkout)}</td>
                                <td className="px-4 py-4 font-mono">{number(row.platform_outcomes.conversions)}</td>
                                <td className="px-4 py-4 font-mono" dir="ltr">{money(snapchatCostPerPurchase(row))}</td>
                                <td className="px-4 py-4 font-mono" dir="ltr">{money(row.platform_outcomes.revenue)}</td>
                                <td className="px-4 py-4 font-mono">{ratio(row.platform_outcomes.roas)}</td>
                                <td className="px-4 py-4 font-mono">{commerce(row).status === "complete" ? number(commerce(row).orders) : "—"}</td>
                                <td className="px-4 py-4 font-mono" dir="ltr">{money(sallaCostPerOrder(row))}</td>
                                <td className="px-4 py-4 font-mono" dir="ltr">{commerce(row).status === "complete" ? money(commerce(row).revenue) : "—"}</td>
                                <td className="px-4 py-4 font-mono">{commerce(row).status === "complete" ? ratio(commerce(row).roas) : "—"}</td>
                                {level === "campaign" && <td className="px-4 py-4"><button type="button" onClick={() => setProfitRow(row)} className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 font-black text-emerald-800">{profitability(row).missing_cost_orders ? `${profitability(row).missing_cost_orders} تكلفة ناقصة` : `${number(profitability(row).product_count)} منتجات`}</button></td>}
                                {level === "campaign" && <td className="px-4 py-4">{abandonedCarts(row).status === "complete" ? <button type="button" onClick={() => setCartRow(row)} className="rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 font-black text-violet-800">{number(abandonedCarts(row).abandoned_carts)} متروكة</button> : "—"}</td>}
                                <td className="px-4 py-4">
                                    <span className={`rounded-full px-2 py-1 font-black ${row.quality.coverage_status === "complete" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}>
                                        {row.quality.sync_status}
                                    </span>
                                </td>
                                <td className="px-4 py-4">
                                    <button
                                        type="button"
                                        onClick={() => onManageEntity?.(row)}
                                        className="inline-flex items-center gap-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 font-black text-amber-800 hover:bg-amber-100"
                                    >
                                        <PencilSimple size={14} weight="bold" /> تعديل / حالة
                                    </button>
                                </td>
                                {canOpenChildren && (
                                    <td className="px-4 py-4">
                                        <button
                                            type="button"
                                            onClick={() => onOpenChildren?.(row)}
                                            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-2 font-black text-emerald-700 hover:bg-emerald-50"
                                        >
                                            {childLabel} <CaretLeft size={14} weight="bold" />
                                        </button>
                                    </td>
                                )}
                            </tr>
                        ))}
                        {!loading && rows.length === 0 && (
                            <tr>
                                <td colSpan={canOpenChildren ? 15 : 14} className="px-4 py-10 text-center font-black text-slate-400">
                                    لا توجد facts مؤكدة لهذا المستوى في الفترة المختارة.
                                </td>
                            </tr>
                        )}
                    </tbody>
                    {totals && (
                        <tfoot className="border-t-2 border-slate-300 bg-slate-50 font-black">
                            <tr>
                                <td className="px-4 py-4">إجمالي الفترة</td>
                                <td className="px-4 py-4" />
                                <td className="px-4 py-4 font-mono" dir="ltr">{money(totals.delivery.spend)}</td>
                                <td className="px-4 py-4 font-mono">{number(totals.delivery.impressions)}</td>
                                <td className="px-4 py-4 font-mono">{number(totals.delivery.views)}</td>
                                <td className="px-4 py-4 font-mono">{number(totals.delivery.clicks)}</td>
                                <td className="px-4 py-4 font-mono">{number(totals.delivery.reach)}</td>
                                <td className="px-4 py-4 font-mono">{totals.delivery.frequency == null ? "—" : `${Number(totals.delivery.frequency).toFixed(2)}×`}</td>
                                <td className="px-4 py-4 font-mono">{number(totals.platform_outcomes.add_to_cart)}</td>
                                <td className="px-4 py-4 font-mono">{number(totals.platform_outcomes.start_checkout)}</td>
                                <td className="px-4 py-4 font-mono">{number(totals.platform_outcomes.conversions)}</td>
                                <td className="px-4 py-4 font-mono" dir="ltr">{money(snapchatCostPerPurchase(totals))}</td>
                                <td className="px-4 py-4 font-mono" dir="ltr">{money(totals.platform_outcomes.revenue)}</td>
                                <td className="px-4 py-4 font-mono">{ratio(totals.platform_outcomes.roas)}</td>
                                <td className="px-4 py-4 font-mono">{commerce(totals).status === "complete" ? number(commerce(totals).orders) : "—"}</td>
                                <td className="px-4 py-4 font-mono" dir="ltr">{money(sallaCostPerOrder(totals))}</td>
                                <td className="px-4 py-4 font-mono" dir="ltr">{commerce(totals).status === "complete" ? money(commerce(totals).revenue) : "—"}</td>
                                <td className="px-4 py-4 font-mono">{commerce(totals).status === "complete" ? ratio(commerce(totals).roas) : "—"}</td>
                                {level === "campaign" && <td className="px-4 py-4" />}
                                {level === "campaign" && <td className="px-4 py-4" />}
                                <td className="px-4 py-4">{totals.quality.sync_status}</td>
                                <td className="px-4 py-4" />
                                {canOpenChildren && <td className="px-4 py-4" />}
                            </tr>
                        </tfoot>
                    )}
                </table>
            </div>
            {filteredRows.length > pageSize && (
                <footer className="flex items-center justify-between gap-3 border-t border-slate-200 px-4 py-3 text-xs font-black text-slate-600">
                    <span>الصفحة {page} من {pages}</span>
                    <div className="flex gap-2">
                        <button type="button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} className="rounded-lg border border-slate-200 px-3 py-2 disabled:opacity-35">السابق</button>
                        <button type="button" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))} className="rounded-lg border border-slate-200 px-3 py-2 disabled:opacity-35">التالي</button>
                    </div>
                </footer>
            )}
            <ProfitabilityDialog row={profitRow} onClose={() => setProfitRow(null)} />
            <AbandonedCartsDialog row={cartRow} onClose={() => setCartRow(null)} />
        </section>
    );
}
