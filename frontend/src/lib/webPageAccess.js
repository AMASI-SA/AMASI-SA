export const WEB_PERMISSIONS_PATH = "/employees-v2?workspace=permissions";

export function managedWebPages(user) {
    return Array.isArray(user?.web_page_access?.pages) ? user.web_page_access.pages : null;
}

const sectionNames = { products: "المنتجات", orders: "الطلبات", accounting: "المحاسبة", balances: "الأرصدة التشغيلية" };

export function webPageSections(pages) {
    const sections = new Map();
    for (const page of pages) {
        if (!sections.has(page.section)) sections.set(page.section, {
            id: page.section, label: page.section_label || sectionNames[page.section] || page.section, pages: [],
        });
        sections.get(page.section).pages.push(page);
    }
    return [...sections.values()];
}

const financePath = "/operational-balances";
const financeAssetKinds = new Set(["bank", "cash", "provider", "ad_account", "pos_receivable"]);
const financePartyKinds = new Set(["supplier", "employee", "employee_custody", "courier", "store_driver", "external_person", "owner_withdrawal", "operating_expense"]);
const financeViews = {
    home: "home", assets: "assets", parties: "parties", log: "log", entry: "entry",
    "special-entry": "entry", refund: "refund", reviews: "review", issues: "review",
    collections: "collections", position: "position", tools: "tools",
};

// Both finance entry points render the same authenticated views. Legacy workspace
// selectors must not turn a grant for the new home or log into an older workspace.
function financePageId(path, params) {
    const daily = path === financePath + "/daily-movements";
    if (["view", "kind", "id"].some((key) => params.getAll(key).length > 1)) return null;
    if (["workspace", "stage", "tab", "panel", "dialog", "action", "focus_id", "section"].some((key) => params.has(key))) return null;
    const view = params.get("view");
    if (!view && (params.has("kind") || params.has("id"))) return null;
    if (view === "ledger") {
        const kind = params.get("kind");
        if (!params.get("id")) return null;
        if (financeAssetKinds.has(kind)) return "balances.assets";
        if (financePartyKinds.has(kind)) return "balances.parties";
        return null;
    }
    const key = view == null ? (daily ? "log" : "home") : financeViews[view];
    return typeof key === "string" ? "balances." + key : null;
}

export function grantedPageAt(pages, location) {
    if (!pages || !location) return null;
    const pathname = (location.pathname || "/").replace(/\/+$/, "") || "/";
    const requested = new URLSearchParams(location.search);
    if (pathname === financePath || pathname === financePath + "/daily-movements") {
        const id = financePageId(pathname, requested);
        return pages.find((page) => !page.owner_only && page.id === id) || null;
    }
    return pages.find((page) => !page.owner_only && [page.to, ...(page.aliases || [])].some((address) => {
        const target = new URL(address, "https://mezan.invalid");
        if (target.pathname !== pathname || (target.hash && target.hash !== location.hash)) return false;
        const routeParams = target.searchParams;
        const keys = new Set(["workspace", "stage", "tab", "view", "provider", ...routeParams.keys()]);
        if (pathname === "/integrations-v2") keys.add("page");
        if (pathname === "/ads-manager/reports") keys.delete("provider");
        return [...keys].every((key) => {
            const actual = requested.getAll(key);
            return routeParams.has(key)
                ? actual.length === 1 && actual[0] === routeParams.get(key)
                : actual.length === 0;
        });
    })) || null;
}

export function grantedWebLink(pages, href) {
    if (!pages) return true;
    return Boolean(href && grantedPageAt(pages, new URL(href, "https://mezan.invalid")));
}
