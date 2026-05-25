/**
 * Format a number as SAR with Arabic-Indic friendly thousands separators.
 * Display LTR-friendly numerals to keep tables readable.
 */
export function formatMoney(value) {
    const n = Number(value || 0);
    return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function formatInt(value) {
    const n = Number(value || 0);
    return n.toLocaleString("en-US");
}

export function formatPercent(value) {
    const n = Number(value || 0);
    return `${n.toFixed(2)}%`;
}

export function todayISO() {
    return new Date().toISOString().slice(0, 10);
}

export function formatDateAr(iso) {
    if (!iso) return "—";
    try {
        const d = new Date(iso);
        return d.toLocaleDateString("ar-SA", { year: "numeric", month: "long", day: "numeric" });
    } catch {
        return iso;
    }
}
