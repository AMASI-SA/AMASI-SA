const METRICS = {
    spend_native: ["الصرف", "money"],
    conversion_value_native: ["قيمة التحويلات", "money"],
    cpc_native: ["تكلفة النقرة", "money"],
    revenue_native: ["الإيراد", "money"],
    cost_native: ["التكلفة", "money"],
    gross_profit_native: ["مجمل الربح", "money"],
    net_profit_native: ["صافي الربح", "money"],
    impressions: ["الظهور", "count"],
    clicks: ["النقرات", "count"],
    conversions: ["التحويلات", "count"],
    orders: ["الطلبات", "count"],
    units: ["الوحدات", "count"],
    roas: ["العائد على الإنفاق", "ratio"],
    ctr: ["نسبة النقر", "fraction"],
    ctr_pct: ["نسبة النقر", "percent"],
};

export function googleEvidenceDisplay(evidence = {}) {
    const [label, unit] = Object.hasOwn(METRICS, evidence.metric)
        ? METRICS[evidence.metric] : [evidence.metric_label || "القيمة", "unknown"];
    const raw = evidence.value;
    if (!(typeof raw === "number" || typeof raw === "string" && raw.trim() !== "") || !Number.isFinite(Number(raw))) {
        return { label, value: "—" };
    }
    const amount = Number(raw) * (unit === "fraction" ? 100 : 1);
    if (!Number.isFinite(amount)) return { label, value: "—" };
    const value = amount.toLocaleString("en-US", { maximumFractionDigits: 2 });
    const suffix = unit === "money" ? evidence.currency || evidence.currency_code || ""
        : unit === "ratio" ? "×" : unit === "fraction" || unit === "percent" ? "%" : "";
    return { label, value: unit === "money" ? `${value} ${suffix}`.trim() : `${value}${suffix}` };
}
