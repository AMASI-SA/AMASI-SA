// Exact decimal UI arithmetic. Does not replace the server's valuation checks.
export function scaledDecimal(value, scale = 6) {
    if (typeof value !== "string" || !new RegExp(`^\\d+(?:\\.\\d{1,${scale}})?$`).test(value)) return null;
    const [whole, fraction = ""] = value.split(".");
    if (whole.length > 18) return null;
    return BigInt(whole) * 10n ** BigInt(scale) + BigInt(fraction.padEnd(scale, "0"));
}

export function inventoryTotal(quantity, cost) {
    const q = scaledDecimal(quantity), c = scaledDecimal(cost);
    if (q === null || c === null || q <= 0n || c <= 0n) return "";
    const pennies = (q * c + 5000000000n) / 10000000000n;
    return `${pennies / 100n}.${String(pennies % 100n).padStart(2, "0")}`;
}
