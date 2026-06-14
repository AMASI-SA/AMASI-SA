/**
 * Asia/Riyadh date/time utilities for the frontend.
 *
 * The MEZAN merchant operates on Saudi calendar days. Bare
 * `new Date()` is timezone-agnostic — it renders in the
 * BROWSER's timezone, which may differ from Riyadh (UTC+3).
 * Daily / monthly UI filters MUST be computed in Riyadh time so
 * the numbers match the backend (which uses tz_utils.py).
 *
 * Pattern:
 *   • For DISPLAY use `formatRiyadh*()` helpers.
 *   • For FILTER bounds use `riyadhTodayRangeUTC()` etc — these
 *     return ISO strings the backend interprets unambiguously.
 *   • Never compare Date objects across timezones directly.
 */

export const DEFAULT_TIMEZONE = "Asia/Riyadh";

// Saudi observes no DST; constant +03:00 offset.
const RIYADH_OFFSET_MS = 3 * 60 * 60 * 1000;

// ────────────────────────────────────────────────────────────────
// Current "now" in Riyadh wall-clock
// ────────────────────────────────────────────────────────────────
/** Current instant as a JS Date (UTC under the hood). */
export const now = () => new Date();

/**
 * Riyadh "today" as a YYYY-MM-DD string. Always reflects the
 * calendar day a Saudi merchant sees, regardless of the browser
 * timezone.
 */
export function todayISO() {
    const utcMs = Date.now();
    const ksaMs = utcMs + RIYADH_OFFSET_MS;
    const d = new Date(ksaMs);
    const y = d.getUTCFullYear();
    const m = String(d.getUTCMonth() + 1).padStart(2, "0");
    const day = String(d.getUTCDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
}

/** Riyadh "yesterday" as YYYY-MM-DD. */
export function yesterdayISO() {
    return addDaysISO(todayISO(), -1);
}

/** Add N days to a YYYY-MM-DD string (calendar arithmetic). */
export function addDaysISO(iso, n) {
    const [y, m, d] = iso.split("-").map(Number);
    const dt = new Date(Date.UTC(y, m - 1, d));
    dt.setUTCDate(dt.getUTCDate() + n);
    const yy = dt.getUTCFullYear();
    const mm = String(dt.getUTCMonth() + 1).padStart(2, "0");
    const dd = String(dt.getUTCDate()).padStart(2, "0");
    return `${yy}-${mm}-${dd}`;
}

// ────────────────────────────────────────────────────────────────
// UTC-ISO boundary helpers (for backend range queries)
// ────────────────────────────────────────────────────────────────
/** ISO of 00:00 Riyadh on `dateIso` expressed in UTC. */
export function riyadhStartOfDayUTC(dateIso = todayISO()) {
    const [y, m, d] = dateIso.split("-").map(Number);
    // Riyadh midnight = UTC midnight - 3h on the SAME calendar day.
    // Use Date.UTC to avoid browser-tz interference.
    const utc = new Date(Date.UTC(y, m - 1, d, 0, 0, 0) - RIYADH_OFFSET_MS);
    return utc.toISOString();
}

/** ISO of 23:59:59.999 Riyadh on `dateIso` expressed in UTC. */
export function riyadhEndOfDayUTC(dateIso = todayISO()) {
    const [y, m, d] = dateIso.split("-").map(Number);
    const utc = new Date(
        Date.UTC(y, m - 1, d, 23, 59, 59, 999) - RIYADH_OFFSET_MS
    );
    return utc.toISOString();
}

/** [start, end] ISO range covering today's full day in Riyadh. */
export function riyadhTodayRangeUTC() {
    return [riyadhStartOfDayUTC(), riyadhEndOfDayUTC()];
}

export function riyadhYesterdayRangeUTC() {
    const y = yesterdayISO();
    return [riyadhStartOfDayUTC(y), riyadhEndOfDayUTC(y)];
}

export function riyadhLastNDaysRangeUTC(n) {
    if (!Number.isInteger(n) || n < 1) {
        throw new Error("n must be a positive integer");
    }
    const today = todayISO();
    const start = addDaysISO(today, -(n - 1));
    return [riyadhStartOfDayUTC(start), riyadhEndOfDayUTC(today)];
}

export function riyadhThisMonthRangeUTC() {
    const t = todayISO();
    const [y, m] = t.split("-").map(Number);
    const firstDay = `${y}-${String(m).padStart(2, "0")}-01`;
    // Last day of month
    const lastDay = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const lastIso = `${y}-${String(m).padStart(2, "0")}-${String(lastDay).padStart(2, "0")}`;
    return [riyadhStartOfDayUTC(firstDay), riyadhEndOfDayUTC(lastIso)];
}

export function riyadhThisYearRangeUTC() {
    const [y] = todayISO().split("-");
    return [riyadhStartOfDayUTC(`${y}-01-01`), riyadhEndOfDayUTC(`${y}-12-31`)];
}

// ────────────────────────────────────────────────────────────────
// Display helpers
// ────────────────────────────────────────────────────────────────
const RIYADH_LOCALE_OPTS = { timeZone: DEFAULT_TIMEZONE };

/**
 * Format any Date / ISO string as a localized string in Riyadh.
 * Uses Intl.DateTimeFormat for proper localization.
 *
 * Usage:
 *   formatRiyadh(iso, { dateStyle: "medium" })
 *   formatRiyadh(iso, { dateStyle: "short", timeStyle: "short" })
 */
export function formatRiyadh(input, opts = { dateStyle: "short" }) {
    if (!input) return "—";
    try {
        const d = input instanceof Date ? input : new Date(input);
        if (Number.isNaN(d.getTime())) return "—";
        return new Intl.DateTimeFormat("en-GB", {
            ...opts,
            timeZone: DEFAULT_TIMEZONE,
        }).format(d);
    } catch {
        return "—";
    }
}

/** YYYY-MM-DD in Riyadh from any Date / ISO string input. */
export function toRiyadhISO(input) {
    if (!input) return "";
    const d = input instanceof Date ? input : new Date(input);
    if (Number.isNaN(d.getTime())) return "";
    // Shift the underlying timestamp by Riyadh offset then read UTC
    // components — gives the wall-clock date in Riyadh.
    const ksa = new Date(d.getTime() + RIYADH_OFFSET_MS);
    const y = ksa.getUTCFullYear();
    const m = String(ksa.getUTCMonth() + 1).padStart(2, "0");
    const day = String(ksa.getUTCDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
}

/** Format with date + 24h time in Riyadh — e.g. "14/02/2026, 16:30". */
export function formatRiyadhDateTime(input) {
    return formatRiyadh(input, {
        dateStyle: "short",
        timeStyle: "short",
        hour12: false,
    });
}

/** Format as a long date in Arabic for friendly headings. */
export function formatRiyadhArabicLong(input) {
    if (!input) return "—";
    try {
        const d = input instanceof Date ? input : new Date(input);
        if (Number.isNaN(d.getTime())) return "—";
        return new Intl.DateTimeFormat("ar-SA-u-ca-gregory", {
            dateStyle: "long",
            timeZone: DEFAULT_TIMEZONE,
            numberingSystem: "latn",
        }).format(d);
    } catch {
        return "—";
    }
}

// ────────────────────────────────────────────────────────────────
// Convenience labels
// ────────────────────────────────────────────────────────────────
export const RIYADH_PERIODS = {
    today:      { label: "اليوم",         getRange: riyadhTodayRangeUTC },
    yesterday:  { label: "أمس",           getRange: riyadhYesterdayRangeUTC },
    last_7:     { label: "آخر 7 أيام",    getRange: () => riyadhLastNDaysRangeUTC(7) },
    last_30:    { label: "آخر 30 يوم",    getRange: () => riyadhLastNDaysRangeUTC(30) },
    this_month: { label: "الشهر الحالي",   getRange: riyadhThisMonthRangeUTC },
    this_year:  { label: "السنة الحالية",  getRange: riyadhThisYearRangeUTC },
};
