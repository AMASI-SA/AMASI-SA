import { forwardRef } from "react";

/**
 * Hardened, locale-safe date input. Use this everywhere in the app instead of
 * a raw <input type="date">.
 *
 * Why:
 *  - Forces ISO YYYY-MM-DD via `lang="en-CA"` and `dir="ltr"` so the browser
 *    never displays a malformed Arabic-locale string (the bug that showed
 *    "292026/05/" instead of "2026-05-29").
 *  - Bounds the picker between 2020 and 2099 to prevent year typos.
 *  - Visually keeps Arabic alignment with `text-align: right` on the value
 *    while preserving LTR direction for the underlying widget.
 *
 * All props are forwarded to the underlying <input> so it remains a drop-in
 * replacement.
 */
const DateInput = forwardRef(function DateInput(
    { value, onChange, className = "", style, ...rest },
    ref
) {
    return (
        <input
            ref={ref}
            type="date"
            value={value || ""}
            onChange={onChange}
            lang="en-CA"
            dir="ltr"
            min="2020-01-01"
            max="2099-12-31"
            className={`w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand ${className}`}
            style={{ direction: "ltr", textAlign: "right", ...(style || {}) }}
            {...rest}
        />
    );
});

/** Strict client-side ISO date validator. Returns true iff value is YYYY-MM-DD
 *  AND represents a real calendar day (e.g. rejects 2026-02-30). */
export function isValidISODate(value) {
    if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const d = new Date(`${value}T00:00:00Z`);
    return !isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value;
}

export default DateInput;
