/**
 * Iter-135 — Saudi Arabia local-date helpers.
 *
 * MEZAN's merchants live in Riyadh (UTC+3, no DST).  Many UI inputs
 * default their date field to "today" using `new Date().toISOString()`
 * — which returns UTC.  Between 00:00 and 03:00 Saudi time that's
 * still YESTERDAY in UTC, so the user silently records the entry one
 * day late.
 *
 * Always import from this module; never call `new Date().toISOString()`
 * directly in component code.  An ESLint rule enforces this in
 * `eslint.config.js` (forbid-utc-date) and a CI grep guard catches
 * any remaining drift.
 */

const RIYADH_OFFSET_MS = 3 * 60 * 60 * 1000; // UTC+3, no DST

/** YYYY-MM-DD for "today" in Asia/Riyadh. */
export const todaySA = () =>
    new Date(Date.now() + RIYADH_OFFSET_MS).toISOString().slice(0, 10);

/** YYYY-MM-DDTHH:mm for "now" in Asia/Riyadh (input[type=datetime-local]). */
export const nowSA = () =>
    new Date(Date.now() + RIYADH_OFFSET_MS).toISOString().slice(0, 16);

/** YYYY-MM-DD for the FIRST day of "this month" in Asia/Riyadh. */
export const monthStartSA = () =>
    `${todaySA().slice(0, 8)}01`;

/** A filesystem-safe timestamp (YYYY-MM-DD-HH-mm) in Asia/Riyadh. */
export const fileStampSA = () =>
    new Date(Date.now() + RIYADH_OFFSET_MS)
        .toISOString()
        .slice(0, 16)
        .replace(/[T:]/g, "-");
