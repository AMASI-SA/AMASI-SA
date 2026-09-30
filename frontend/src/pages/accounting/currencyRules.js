import catalog from "./currencyCodes.json";

export const CURRENCY_CODES = catalog.codes;
export const isCurrencyCode = value => CURRENCY_CODES.includes(value);

export function fxTimeInRiyadh(value) {
    if (!value) return "";
    if (!/(Z|[+-]\d\d:\d\d)$/i.test(value)) return value.slice(0, 16);
    const instant = Date.parse(value);
    return Number.isFinite(instant) ? new Date(instant + 3 * 60 * 60 * 1000).toISOString().slice(0, 16) : "";
}

// Denomination changes invalidate FX evidence; amounts are never recalculated here.
export function currencyChange(currency) {
    return { original_currency: currency, fx_rate_to_sar: currency === "SAR" ? "1" : "", fx_at: "", fx_source: "", fx_evidence_file_id: "" };
}

export function accountFx(row, id, account) {
    const saved = row.account_fx?.[id] || row;
    if (saved.original_currency !== account?.currency) return currencyChange(account?.currency || "");
    return Object.fromEntries(Object.keys(currencyChange("")).map(key => [key, saved[key] ?? ""]));
}
