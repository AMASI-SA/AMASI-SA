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
    // Iter-135 — kept only for backward compat.  New code should use
    // `todaySA` from `../lib/dates` so 00:00-03:00 Saudi clock-time
    // doesn't silently roll back to yesterday's UTC date.
    const RIYADH_OFFSET_MS = 3 * 60 * 60 * 1000;
    return new Date(Date.now() + RIYADH_OFFSET_MS).toISOString().slice(0, 10);
}

export function formatDateAr(iso) {
    if (!iso) return "—";
    try {
        const d = new Date(iso);
        return d.toLocaleDateString("en-GB", { year: "numeric", month: "long", day: "numeric" });
    } catch {
        return iso;
    }
}
