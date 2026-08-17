const PROVIDERS = [
    { key: "snapchat", label: "Snapchat" },
    { key: "tiktok", label: "TikTok" },
    { key: "meta", label: "Meta" },
    { key: "google", label: "Google Ads" },
];

const optionalNumber = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
};

const fmtSar = (value) => Number(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
});

const fmtInt = (value) => Number(value).toLocaleString("en-US", {
    maximumFractionDigits: 0,
});

export function buildAdsExecutiveMetricRows(value = {}) {
    const providers = value?.providers || {};
    const total = value?.total || {};
    const valuesFor = (field) => Object.fromEntries(
        PROVIDERS.map(({ key }) => [key, optionalNumber(providers?.[key]?.[field])]),
    );
    const costPerSallaOrderValues = Object.fromEntries(PROVIDERS.map(({ key }) => {
        const spend = optionalNumber(providers?.[key]?.spend_sar);
        const orders = optionalNumber(providers?.[key]?.salla_orders);
        return [key, spend !== null && spend > 0 && orders !== null && orders > 0
            ? spend / orders
            : null];
    }));
    const totalSpend = optionalNumber(total.spend_sar);
    const totalOrders = optionalNumber(total.salla_orders);
    const totalCostPerSallaOrder = (
        totalSpend !== null && totalSpend > 0 && totalOrders !== null && totalOrders > 0
            ? totalSpend / totalOrders
            : null
    );

    return [
        {
            key: "spend",
            label: "الصرف",
            source: "المنصة الإعلانية",
            format: "sar",
            values: valuesFor("spend_sar"),
            total: optionalNumber(total.spend_sar),
        },
        {
            key: "sales",
            label: "المبيعات / العائد",
            source: "سلة",
            format: "sar",
            values: valuesFor("salla_sales_sar"),
            total: optionalNumber(total.salla_sales_sar),
        },
        {
            key: "orders",
            label: "الطلبات",
            source: "سلة",
            format: "int",
            values: valuesFor("salla_orders"),
            total: optionalNumber(total.salla_orders),
        },
        {
            key: "cost_per_order",
            label: "متوسط تكلفة الطلب",
            source: "صرف المنصة ÷ طلبات سلة",
            format: "sar",
            values: costPerSallaOrderValues,
            total: totalCostPerSallaOrder,
        },
        {
            key: "roas",
            label: "ROAS الفعلي",
            source: "مبيعات سلة ÷ صرف المنصة",
            format: "ratio",
            values: valuesFor("actual_roas"),
            total: optionalNumber(total.actual_roas),
        },
    ];
}

function formatValue(value, format) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return "—";
    }
    if (format === "int") return fmtInt(value);
    if (format === "ratio") return `${Number(value).toFixed(2)}×`;
    return fmtSar(value);
}

export default function AdsExecutiveBreakdownTable({ data }) {
    const rows = buildAdsExecutiveMetricRows(data);
    const coverage = data?.coverage || {};

    return (
        <div dir="rtl" data-testid="ads-executive-breakdown-content">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 pb-2">
                <div>
                    <div className="text-xs font-extrabold text-rose-900">
                        📣 أداء المنصات المعتمد
                    </div>
                    <div className="mt-1 text-[10px] text-slate-500">
                        الطلبات والمبيعات من سلة؛ ومتوسط تكلفة الطلب = صرف المنصة ÷ طلبات سلة.
                    </div>
                </div>
                <div className="flex flex-wrap gap-1 text-[9px] font-bold">
                    <span className="rounded bg-emerald-50 px-2 py-1 text-emerald-700">
                        سلة: الطلبات والمبيعات
                    </span>
                    <span className="rounded bg-rose-50 px-2 py-1 text-rose-700">
                        المنصة: الصرف وتكلفة الطلب
                    </span>
                </div>
            </div>

            <div className="overflow-x-auto">
                <table
                    className="w-full min-w-[920px] text-[11px]"
                    data-testid="ads-executive-breakdown-table"
                >
                    <thead className="bg-emerald-100/80 text-slate-700">
                        <tr>
                            <th className="p-2 text-right font-extrabold">المقياس</th>
                            {PROVIDERS.map(({ key, label }) => (
                                <th key={key} className="p-2 text-center font-extrabold">
                                    {label}
                                </th>
                            ))}
                            <th className="p-2 text-center font-extrabold">الإجمالي</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row) => (
                            <tr
                                key={row.key}
                                className="border-t border-slate-100 even:bg-slate-50/60"
                            >
                                <td className="p-2 font-bold text-slate-800">
                                    <div>{row.label}</div>
                                    <div className="mt-0.5 text-[9px] font-semibold text-slate-400">
                                        المصدر: {row.source}
                                    </div>
                                </td>
                                {PROVIDERS.map(({ key }) => (
                                    <td
                                        key={key}
                                        className="p-2 text-center font-mono font-bold text-slate-700"
                                    >
                                        {formatValue(row.values[key], row.format)}
                                    </td>
                                ))}
                                <td className="p-2 text-center font-mono font-extrabold text-emerald-800">
                                    {formatValue(row.total, row.format)}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            <div className="mt-2 flex flex-wrap justify-between gap-2 text-[10px] leading-relaxed text-slate-500">
                <span>
                    ROAS الفعلي = مبيعات سلة المنسوبة للمنصة ÷ صرف المنصة بعد التحويل إلى الريال.
                </span>
                {Number(coverage.salla_unattributed_orders || 0) > 0 && (
                    <span className="font-bold text-amber-700">
                        {fmtInt(coverage.salla_unattributed_orders)} طلب من سلة بلا مصدر إعلاني واضح ولم يُوزع على المنصات.
                    </span>
                )}
            </div>
        </div>
    );
}
