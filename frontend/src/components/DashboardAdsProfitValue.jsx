const finite = (value) => typeof value === "number" && Number.isFinite(value);

export function dashboardAdsDisplayProfit(profit = {}) {
    if (profit?.complete === true && finite(profit.netProfit)) return { value: profit.netProfit, partial: false };
    if (profit?.assignedOrdersProfitAvailable === true && finite(profit.assignedOrdersProfit)) {
        return { value: profit.assignedOrdersProfit, partial: true };
    }
    return { value: null, partial: false };
}

/** Presentation only: full-account profit and mapped-order contribution stay distinct. */
export default function DashboardAdsProfitValue({ profit = {}, inline = false, currency = false, unavailableReason }) {
    const { value, partial } = dashboardAdsDisplayProfit(profit);
    if (value === null) return <span className="font-medium text-slate-400">—
        {unavailableReason && <span className="block text-[8px] leading-3">{unavailableReason}</span>}
    </span>;
    return <span title={partial
        ? "ربح الطلبات المسندة فقط بعد خصم كامل صرف الحساب وعمولة البنك، وقبل المصروفات التشغيلية. قد تزيد الطلبات غير المسندة الربح أو تقلله."
        : undefined}>
        <bdi dir="ltr" className={`${inline ? "" : "block "}whitespace-nowrap font-bold tabular-nums ${value < 0 ? "text-rose-700" : "text-emerald-700"}`}>
            {value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}{currency && " ر.س"}
        </bdi>
        {partial ? <span className={`${inline ? "mr-1" : "block"} text-[8px] font-medium leading-3 text-amber-700`}>جزئي: الطلبات المسندة</span>
            : profit.provisional === true && <span className={`${inline ? "mr-1" : "block"} text-[8px] font-normal text-amber-700`}>تقديري</span>}
    </span>;
}
