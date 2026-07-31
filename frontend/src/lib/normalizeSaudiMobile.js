const text = (value) => String(value || "").trim();

/**
 * Convert supported Saudi mobile formats to the canonical 9665XXXXXXXX form.
 */
export function normalize_saudi_mobile(value) {
  let digits = text(value).replace(/\D/g, "");

  if (digits.startsWith("00966")) digits = digits.slice(2);
  if (digits.startsWith("9660")) digits = `966${digits.slice(4)}`;

  if (/^9665\d{8}$/.test(digits)) return digits;
  if (/^05\d{8}$/.test(digits)) return `966${digits.slice(1)}`;
  if (/^5\d{8}$/.test(digits)) return `966${digits}`;

  return digits;
}
