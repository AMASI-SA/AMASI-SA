const LEGAL_NOINDEX_PATHS = new Set([
  "/privacy-policy",
  "/data-deletion",
  "/terms",
]);

export const LEGAL_NOINDEX_DIRECTIVES =
  "noindex, nofollow, noarchive, nosnippet, noimageindex";

export function normalizePublicLegalPath(pathname) {
  const raw = String(pathname || "/").split(/[?#]/, 1)[0] || "/";
  if (raw.length > 1 && raw.endsWith("/")) return raw.slice(0, -1);
  return raw;
}

export function isNoIndexLegalPath(pathname) {
  return LEGAL_NOINDEX_PATHS.has(normalizePublicLegalPath(pathname));
}

function upsertMeta(name, content) {
  let tag = document.head.querySelector(`meta[name="${name}"]`);
  if (!tag) {
    tag = document.createElement("meta");
    tag.setAttribute("name", name);
    tag.setAttribute("data-mezan-legal-noindex", "true");
    document.head.appendChild(tag);
  }
  tag.setAttribute("content", content);
  return tag;
}

export function applyPublicLegalNoIndex(pathname) {
  if (typeof document === "undefined" || !isNoIndexLegalPath(pathname)) {
    return false;
  }

  upsertMeta("robots", LEGAL_NOINDEX_DIRECTIVES);
  upsertMeta("googlebot", LEGAL_NOINDEX_DIRECTIVES);
  upsertMeta("bingbot", LEGAL_NOINDEX_DIRECTIVES);
  document.documentElement.setAttribute("data-mezan-search-index", "blocked");
  return true;
}

export { LEGAL_NOINDEX_PATHS };
