import { useState } from "react";
import { formatMoney, formatInt } from "../lib/format";
import { buildPaymentFeeRows } from "../lib/paymentFeeRows";

const amount = value => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? Number(value) : null;
const money = value => amount(value) === null ? "غير مكتمل" : `${formatMoney(value)} ر.س`;
const OPERATING = [["رواتب الموظفين", "operating_salaries_employee"], ["مصاريف منزلية", "operating_salaries_household"], ["صدقات / زكاة", "operating_salaries_charity"], ["إيجارات", "operating_rentals_total"], ["كهرباء وماء", "operating_utilities_total"], ["تجديدات وتأمين والتزامات دورية", "operating_renewals_total"], ["مصاريف مدفوعة مقدماً", "operating_prepaid_total"], ["مصاريف يومية أخرى", "operating_daily_other_total"]];
const PROVIDERS = [["snapchat", "سناب شات"], ["tiktok", "تيك توك"], ["meta", "ميتا"], ["google", "جوجل"]];

export function financialMetrics(data, products) {
    const t = data?.totals || {};
    const q = data?.ads_v2?.spend_quality || {};
    const adsComplete = t.ads_spend_data_complete === true && q.status === "complete" && q.amount_complete === true;
    const adsProvisional = !adsComplete && t.ads_spend_amount_available === true && q.amount_available === true && t.ads_spend_provisional === true && q.provisional === true;
    const productComplete = t.product_cost_complete !== false && Array.isArray(products) && products.every(row => row.cost_status === "complete" && amount(row.total_cost) !== null);
    const productKnown = Array.isArray(products) ? products.reduce((sum, row) => sum + (amount(row.known_cost_total ?? row.total_cost) ?? 0), 0) : null;
    // Use the displayed products' same refresh result, preserving unknown selections.
    const product = productComplete ? productKnown : null;
    const ads = adsComplete || adsProvisional ? amount(t.total_ads_cost) : null;
    const costs = [product, ads, amount(t.total_shipping_cost), amount(t.total_payment_fees), amount(t.operating_expenses_total)];
    const total = data && costs.every(value => value !== null) ? costs.reduce((sum, value) => sum + value, 0) : null;
    const orders = amount(t.total_orders);
    const sales = t.sales_currency_conversion_complete === false || data?.currency_conversion?.conversion_complete === false ? null : amount(t.total_sales);
    return { product, productKnown, ads, adsProvisional, total, orders, sales, average: total !== null && orders > 0 ? total / orders : null };
}

function DetailTable({ title, headers, rows, total }) {
    return <div className="overflow-x-auto sold-financial-table mb-5"><h3 className="font-bold my-3">{title}</h3><table className="w-full text-right text-sm border-collapse" aria-label={title}><thead><tr>{headers.map(name => <th key={name} className="border p-3 bg-slate-100">{name}</th>)}</tr></thead><tbody>{rows.length ? rows.map((row, index) => <tr key={index}>{row.map((value, cell) => <td key={cell} className="border p-3">{value}</td>)}</tr>) : <tr><td colSpan={headers.length} className="p-3 border">لا توجد بنود مسجلة للفترة</td></tr>}</tbody><tfoot><tr className="font-bold bg-violet-50"><td className="p-3 border" colSpan={headers.length - 1}>الإجمالي</td><td className="p-3 border">{money(total)}</td></tr></tfoot></table></div>;
}

export function FinancialDetails({ data, section }) {
    if (!data) return <p className="text-amber-800">تفاصيل الملخص غير متاحة</p>;
    const t = data.totals || {};
    const details = {
        ads: <DetailTable title="تفاصيل صرف المنصات بالريال السعودي" headers={["المنصة", "الصرف بالريال السعودي"]} rows={PROVIDERS.map(([key, label]) => [label, money(data.ads_v2?.executive_breakdown?.providers?.[key]?.spend_sar)])} total={financialMetrics(data, []).ads} />,
        shipping: <DetailTable title="تفاصيل شركات الشحن" headers={["شركة الشحن", "عدد الشحنات", "إجمالي التكلفة"]} rows={(data.shipping_breakdown || []).map(row => [row.name, formatInt(row.orders_count), money(row.total_cost)])} total={t.total_shipping_cost} />,
        payment: <DetailTable title="تفاصيل عمولات طرق الدفع" headers={["طريقة الدفع / الحساب", "عدد الطلبات", "المبلغ الخاضع", "العمولة", "إجمالي الرسوم"]} rows={buildPaymentFeeRows(data.payment_breakdown || [], { preserveUnknown: true }).map(row => [row.name, row.kind === "ad_bank_commission" || row.ordersCount === null ? "—" : formatInt(row.ordersCount), money(row.baseAmount), row.commissionPercent == null ? "—" : `${row.commissionPercent.toFixed(2)}%`, money(row.feeAmount)])} total={t.total_payment_fees} />,
        operating: <DetailTable title="تفاصيل المصروفات التشغيلية" headers={["البند", "إجمالي التكلفة"]} rows={OPERATING.map(([label, key]) => [label, money(t[key])])} total={t.operating_expenses_total} />,
    };
    return section ? details[section] : <div data-testid="sold-financial-all-details">{Object.entries(details).map(([key, node]) => <div key={key}>{node}</div>)}</div>;
}

export default function SoldFinancialReport({ data, products, loading, error, printable = false }) {
    const [expanded, setExpanded] = useState(null);
    const t = data?.totals || {};
    const m = financialMetrics(data, products);
    const rows = [
        ["sales", "إجمالي المبيعات", m.sales], ["orders", "عدد الطلبات", m.orders],
        ["average", "متوسط التكلفة الكلية للطلب", m.average], ["products", "إجمالي تكاليف المنتجات", m.product],
        ["ads", "إجمالي تكاليف الإعلانات", m.ads], ["shipping", "إجمالي تكاليف الشحن", t.total_shipping_cost],
        ["payment", "إجمالي عمولات الدفع", t.total_payment_fees], ["operating", "إجمالي المصروفات التشغيلية", t.operating_expenses_total],
        ["total", "إجمالي التكاليف", m.total],
    ];
    return <div data-testid="sold-financial-summary" className="rounded-xl border border-emerald-200 bg-white p-5">
        <h2 className="font-extrabold text-xl mb-3">الملخص المالي للفترة</h2>
        <p className="text-xs text-slate-600 mb-4">المبيعات والطلبات والمنتجات والشحن حسب حالات الطلب المختارة. الإعلانات والمصروفات التشغيلية تخص الفترة كاملة. متوسط التكلفة الكلية = إجمالي التكاليف ÷ عدد الطلبات. جميع المبالغ بالريال السعودي.</p>
        {error && <p role="alert" className="text-amber-800">تعذر تحميل الملخص المالي؛ حدّث التقرير للمحاولة مجددًا.</p>}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">{rows.map(([key, label, value]) => {
            const expandable = ["ads", "shipping", "payment", "operating"].includes(key) && !printable;
            const provisional = m.adsProvisional && ["ads", "average", "total"].includes(key);
            return <div key={key} className="border rounded-lg p-3"><button type="button" disabled={!expandable || !data || loading} aria-expanded={expandable ? expanded === key : undefined} onClick={() => setExpanded(expanded === key ? null : key)} className="text-right w-full"><span className="block text-sm text-slate-600">{label}{expandable ? " ▾" : ""}</span><strong className="block text-lg mt-2">{loading ? "جارٍ التحميل…" : key === "average" && m.orders === 0 ? "—" : key === "orders" && value !== null ? formatInt(value) : money(value)}{provisional && " (مؤقت)"}</strong></button>{key === "products" && m.product === null && m.productKnown !== null && <small className="text-amber-800">المعروف {money(m.productKnown)} — الإجمالي غير مكتمل</small>}</div>;
        })}</div>
        {!printable && expanded && <FinancialDetails data={data} section={expanded} />}
    </div>;
}
