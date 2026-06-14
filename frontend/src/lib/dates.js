/**
 * Saudi Arabia local-date helpers (Asia/Riyadh, UTC+3, no DST).
 *
 * This module is the COMPACT public surface that components import.
 * The full timezone utility surface lives in `lib/tzUtils.js`
 * (Iter-177). This file simply re-exports the most-used helpers
 * with their original short names so legacy callers keep working.
 *
 * Never call `new Date().toISOString()` directly in component code
 * for any date that participates in daily/monthly aggregations —
 * use these helpers instead. They always render in Riyadh
 * regardless of the browser's local timezone.
 */
import {
    todayISO,
    yesterdayISO,
    addDaysISO,
    toRiyadhISO,
    formatRiyadh,
    formatRiyadhDateTime,
    formatRiyadhArabicLong,
    riyadhTodayRangeUTC,
    riyadhYesterdayRangeUTC,
    riyadhLastNDaysRangeUTC,
    riyadhThisMonthRangeUTC,
    riyadhThisYearRangeUTC,
    riyadhStartOfDayUTC,
    riyadhEndOfDayUTC,
    RIYADH_PERIODS,
    DEFAULT_TIMEZONE,
} from "./tzUtils";

const RIYADH_OFFSET_MS = 3 * 60 * 60 * 1000;

/** YYYY-MM-DD for "today" in Asia/Riyadh. */
export const todaySA = todayISO;

/** YYYY-MM-DD for "yesterday" in Asia/Riyadh. */
export const yesterdaySA = yesterdayISO;

/** YYYY-MM-DDTHH:mm for "now" in Asia/Riyadh
 * (input[type=datetime-local] friendly). */
export const nowSA = () =>
    new Date(Date.now() + RIYADH_OFFSET_MS).toISOString().slice(0, 16);

/** YYYY-MM-DD for the FIRST day of "this month" in Asia/Riyadh. */
export const monthStartSA = () => `${todaySA().slice(0, 8)}01`;

/** YYYY-MM for "this month" in Asia/Riyadh (datetime[type=month]). */
export const monthISO_SA = () => todaySA().slice(0, 7);

/** YYYY-MM-DD for "first of January, this year" in Asia/Riyadh. */
export const yearStartSA = () => `${todaySA().slice(0, 4)}-01-01`;

/** A filesystem-safe timestamp (YYYY-MM-DD-HH-mm) in Asia/Riyadh. */
export const fileStampSA = () =>
    new Date(Date.now() + RIYADH_OFFSET_MS)
        .toISOString()
        .slice(0, 16)
        .replace(/[T:]/g, "-");

// Re-exports for callers that want the richer surface.
export {
    addDaysISO,
    toRiyadhISO,
    formatRiyadh,
    formatRiyadhDateTime,
    formatRiyadhArabicLong,
    riyadhTodayRangeUTC,
    riyadhYesterdayRangeUTC,
    riyadhLastNDaysRangeUTC,
    riyadhThisMonthRangeUTC,
    riyadhThisYearRangeUTC,
    riyadhStartOfDayUTC,
    riyadhEndOfDayUTC,
    RIYADH_PERIODS,
    DEFAULT_TIMEZONE,
};
