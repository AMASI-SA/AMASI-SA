const selector = /^[\p{L}\p{N}_][\p{L}\p{N}_:.~-]{0,199}$/u;
const pageIds = new Set(["detail.order", "detail.analysis", "detail.account", "detail.supplier", "detail.entity", "detail.reconciliation"]);
const simplePaths = new Map([
    ["orders-v2", "detail.order"], ["analyses", "detail.analysis"],
    ["accounts", "detail.account"], ["reconciliation", "detail.reconciliation"],
]);

export function validDetailId(value) {
    return typeof value === "string" && selector.test(value) && !/[\r\n]/.test(value);
}

export function detailRouteAt(input) {
    if (!input) return null;
    let url;
    try {
        url = typeof input === "string" ? new URL(input, "https://mezan.invalid") : input;
    } catch { return null; }
    const pathname = url.pathname;
    if (typeof pathname !== "string" || !pathname.startsWith("/")) return null;
    let parts;
    try { parts = pathname.replace(/\/$/, "").slice(1).split("/").map(decodeURIComponent); } catch { return null; }
    let pageId;
    let identity;
    let kind;
    if (parts.length === 2 && parts[0] === "employee-detail" && pageIds.has(parts[1])) {
        pageId = parts[1];
    } else if (parts.length === 2 && simplePaths.has(parts[0]) && validDetailId(parts[1])) {
        pageId = simplePaths.get(parts[0]); identity = parts[1];
    } else if (parts.length === 3 && parts[0] === "suppliers" && parts[2] === "ledger-detail" && validDetailId(parts[1])) {
        pageId = "detail.supplier"; identity = parts[1];
    } else if (parts.length === 3 && parts[0] === "entity-ledger" && validDetailId(parts[2])) {
        if (parts[1] === "supplier") pageId = "detail.supplier";
        else if (["external", "external_person", "courier"].includes(parts[1])) {
            pageId = "detail.entity"; kind = parts[1] === "external" ? "external_person" : parts[1];
        } else return null;
        identity = parts[2];
    } else return null;
    const params = new URLSearchParams(url.search);
    const allowed = pageId === "detail.order" ? ["returnTo"]
        : pageId === "detail.supplier" ? ["from", "to"] : [];
    if ([...params.keys()].some(key => !allowed.includes(key) || params.getAll(key).length !== 1)) return null;
    const start = params.get("from") || undefined;
    const end = params.get("to") || undefined;
    if ([start, end].some(day => day && !/^\d{4}-\d{2}-\d{2}$/.test(day)) || (start && end && start > end)) return null;
    return { pageId, identity, kind, start, end };
}

export function detailPageIdAt(location) {
    return detailRouteAt(location)?.pageId || null;
}

export function detailHref(pageId, identity, kind) {
    if (!validDetailId(identity)) return null;
    if (pageId === "detail.supplier") return "/suppliers/" + encodeURIComponent(identity) + "/ledger-detail";
    if (pageId === "detail.entity" && ["external_person", "courier"].includes(kind)) return "/entity-ledger/" + kind + "/" + encodeURIComponent(identity);
    for (const [path, id] of simplePaths) {
        if (id === pageId) return "/" + path + "/" + encodeURIComponent(identity);
    }
    return null;
}
