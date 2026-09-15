import React from "react";

const THREE_DECIMAL_CURRENCIES = new Set(["BHD", "KWD", "OMR"]);

function finiteNumber(value) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function numberText(value, currency = "SAR") {
    const decimals = THREE_DECIMAL_CURRENCIES.has(currency) ? 3 : 2;
    return Number(value).toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
}

export function orderCurrencyView(order = {}) {
    const totals = order?.totals && typeof order.totals === "object" ? order.totals : {};
    const currency = String(
        totals.currency
        || order.original_currency
        || order.currency
        || "SAR"
    ).trim().toUpperCase() || "SAR";
    const originalTotal = finiteNumber(
        totals.total
        ?? order.original_total_amount
        ?? order.total_amount
        ?? order.total
    );
    const sarTotal = finiteNumber(
        totals.total_sar
        ?? order.total_amount_sar
        ?? (currency === "SAR" ? originalTotal : null)
    );
    const isForeign = currency !== "SAR";
    return {
        currency,
        originalTotal,
        sarTotal,
        isForeign,
        originalText: originalTotal === null
            ? "—"
            : `${numberText(originalTotal, currency)} ${currency === "SAR" ? "ر.س" : currency}`,
        sarText: sarTotal === null ? null : `≈ ${numberText(sarTotal, "SAR")} ر.س`,
    };
}

export default function OrderCurrencyAmount({ order, className = "", compact = false }) {
    const view = orderCurrencyView(order);
    return (
        <span
            className={`inline-flex min-w-0 ${compact ? "items-end" : "flex-col items-end"} ${className}`.trim()}
            data-testid="order-currency-amount"
        >
            <span className="num whitespace-nowrap font-semibold text-teal-800" dir="ltr">
                {view.originalText}
            </span>
            {view.isForeign && view.sarText && (
                <span className="num whitespace-nowrap text-[10px] text-slate-400" dir="ltr">
                    {view.sarText}
                </span>
            )}
            {view.isForeign && !view.sarText && (
                <span className="whitespace-nowrap text-[10px] font-bold text-amber-700">
                    المعادل السعودي غير متاح
                </span>
            )}
        </span>
    );
}
