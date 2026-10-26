import DashboardTableScroll from "./DashboardTableScroll";
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


function accountAmount(value) {
    if (!(typeof value === "number" || typeof value === "string" && value.trim() !== "")) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function accountRows(accounts) {
    if (!Array.isArray(accounts)) return [];
    const seen = new Set();
    return accounts.filter((row) => {
        const id = row?.account_id;
        if (typeof id !== "string" || !id.trim() || seen.has(id)) return false;
        seen.add(id);
        return true;
    });
}

function SnapchatAccountExpenses({ accounts }) {
    const entries = accountRows(accounts);
    if (!entries.length) return <p className="mb-3 rounded-lg bg-slate-50 p-3 text-xs text-slate-500" data-testid="ads-executive-snapchat-account-state">
        {Array.isArray(accounts) && accounts.length === 0
            ? "لا توجد حسابات سناب مفعّلة لهذه الفترة"
            : "بانتظار بيانات حسابات سناب للفترة المحددة"}
    </p>;
    return <div className="mb-3" data-testid="ads-executive-snapchat-account-expenses">
        <p className="mb-2 text-xs font-extrabold text-amber-900">صرف حسابات سناب المستقل</p>
        <DashboardTableScroll label="صرف كل حساب سناب">
            <table className="w-full min-w-[540px] text-[11px]" data-testid="ads-executive-snapchat-account-table">
                <thead className="bg-amber-50 text-slate-700"><tr>
                    <th className="p-2 text-right">حساب سناب</th>
                    <th className="p-2 text-center">الصرف بالعملة الأصلية</th>
                    <th className="p-2 text-center">الصرف بالريال السعودي</th>
                    <th className="p-2 text-center">حالة البيانات</th>
                </tr></thead>
                <tbody>{entries.map((row) => {
                    const quality = row.quality || {};
                    const sar = accountAmount(row.total_sar);
                    const available = row.projection_timezone === "Asia/Riyadh"
                        && quality.amount_available === true && sar !== null
                        && quality.status !== "incomplete" && quality.data_state !== "unknown_incomplete";
                    const provisional = available && (quality.provisional === true || quality.status === "provisional");
                    const currency = typeof row.currency === "string" && /^[A-Z]{3}$/.test(row.currency) ? row.currency : null;
                    const native = available && currency ? accountAmount(row.total_native) : null;
                    const name = typeof row.display_name === "string" ? row.display_name.trim() : "";
                    const label = currency === "USD" ? "سناب الدولار" : currency === "SAR" ? "سناب السعودي" : "حساب سناب";
                    const observed = typeof quality.observed_at === "string" && Number.isFinite(Date.parse(quality.observed_at))
                        ? quality.observed_at : null;
                    return <tr key={row.account_id} data-testid={"snapchat-expense-" + row.account_id} className="border-t border-amber-100">
                        <td className="p-2 font-bold text-slate-800"><div>{label}</div>{name && <div className="max-w-[240px] truncate text-[10px] font-normal text-slate-500" title={name}>{name}</div>}</td>
                        <td dir="ltr" className="p-2 text-center font-mono">{native === null ? "—" : fmtSar(native)}{currency && " " + currency}</td>
                        <td dir="ltr" className="p-2 text-center font-mono font-bold">{available ? fmtSar(sar) + " ر.س" : "غير مكتمل"}</td>
                        <td className="p-2 text-center"><div>{!available ? "غير مكتمل" : provisional ? "أولي" : "مكتمل"}</div>{observed && <time className="text-[9px] text-slate-500" dateTime={observed}>{new Date(observed).toLocaleString("ar-SA", { timeZone: "Asia/Riyadh", hour: "2-digit", minute: "2-digit" })}</time>}</td>
                    </tr>;
                })}</tbody>
            </table>
        </DashboardTableScroll>
    </div>;
}

export default function AdsExecutiveBreakdownTable({ data, snapchatAccounts }) {
    const rows = buildAdsExecutiveMetricRows(data);
    const coverage = data?.coverage || {};
    const splitSnapchat = snapchatAccounts !== undefined;
    const providers = splitSnapchat ? PROVIDERS.filter(({ key }) => key !== "snapchat") : PROVIDERS;

    return (
        <div dir="rtl" data-testid="ads-executive-breakdown-content">
            {splitSnapchat && <SnapchatAccountExpenses accounts={snapchatAccounts} />}
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

            <DashboardTableScroll label="أداء المنصات الإعلانية">
                <table
                    className="w-full min-w-[920px] text-[11px]"
                    data-testid="ads-executive-breakdown-table"
                >
                    <thead className="bg-emerald-100/80 text-slate-700">
                        <tr>
                            <th className="p-2 text-right font-extrabold">المقياس</th>
                            {providers.map(({ key, label }) => (
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
                                {providers.map(({ key }) => (
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
            </DashboardTableScroll>

            <div className="mt-2 flex flex-wrap justify-between gap-2 text-[10px] leading-relaxed text-slate-500">
                {splitSnapchat && <span>الإجمالي يشمل حسابات سناب وبقية المنصات.</span>}
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
