const EXPLICIT_TIME_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i;

export function parseServerDateTime(value) {
  if (typeof value !== "string") return Number.NaN;

  const trimmed = value.trim();
  if (!trimmed) return Number.NaN;

  const isoLike = trimmed.includes("T")
    ? trimmed
    : trimmed.replace(" ", "T");
  const normalized = EXPLICIT_TIME_ZONE.test(isoLike)
    ? isoLike
    : isoLike + "Z";

  return Date.parse(normalized);
}

export function isRecentServerDateTime(
  value,
  nowMs = Date.now(),
  maxAgeMs = 30 * 60 * 1_000,
) {
  const parsed = parseServerDateTime(value);
  return Number.isFinite(parsed) && nowMs - parsed < maxAgeMs;
}
