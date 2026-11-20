import { detailPageIdAt } from "./webDetailAccess";
import { ADVERTISING_PROVIDER_IDS } from "./integrationWorkspaces";

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
export const employeeReportGroups = {
    summary: "الملخص", accounts: "البنوك والصناديق", providers: "منصات الدفع", couriers: "شركات الشحن",
    drivers: "الموصلون", employees: "الموظفون والعهد", suppliers: "الموردون", advertising: "الإعلانات",
    other: "الجهات الأخرى", expected: "المبالغ المتوقعة",
};
export const employeeSettingGroups = {
    overview: "الفترة التشغيلية", openings: "الأرصدة الافتتاحية", "opening-stock": "المخزون الافتتاحي",
    "salary-start": "بداية الرواتب", movements: "الحركات المسجلة", reports: "تقرير الحسابات",
};
const financeViews = {
    home: "home", assets: "assets", parties: "parties", log: "log", entry: "entry",
    "special-entry": "entry", refund: "refund", reviews: "review", issues: "review",
    collections: "collections", position: "position", tools: "tools", specialized: "specialized",
};

// Both finance entry points render the same authenticated views. Legacy workspace
// selectors must not turn a grant for the new home or log into an older workspace.
function financePageId(path, params) {
    const daily = path === financePath + "/daily-movements";
    if (["view", "kind", "id"].some((key) => params.getAll(key).length > 1)) return null;
    if (["workspace", "stage", "tab", "panel", "dialog", "action", "focus_id"].some((key) => params.has(key))) return null;
    if (params.has("section") && (params.get("view") !== "specialized" || params.getAll("section").length !== 1
        || !Object.hasOwn(employeeSettingGroups, params.get("section")))) return null;
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
    const detailId = detailPageIdAt(location);
    if (detailId) return pages.find((page) => page.id === detailId && !page.owner_only) || null;
    const requested = new URLSearchParams(location.search);
    // These are presentations of the same scoped, read-only inventory reader.
    // Writer and control tabs never inherit a stock-report page grant.
    if (pathname === "/inventory-v2" && requested.has("tab")) {
        const tabs = requested.getAll("tab");
        if (tabs.length !== 1 || !["stock", "reports"].includes(tabs[0])
            || ["workspace", "stage", "view", "provider", "action"].some(key => requested.has(key))) return null;
        return pages.find(page => !page.owner_only && page.id === "products.inventory") || null;
    }
    if (pathname === "/integrations-v2" && requested.has("provider")) {
        const ids = requested.getAll("provider");
        if (ids.length !== 1 || !ADVERTISING_PROVIDER_IDS.includes(ids[0])
            || [...requested.keys()].some(key => !["workspace", "provider"].includes(key))) return null;
        const workspaces = requested.getAll("workspace");
        if (workspaces.length && (workspaces.length !== 1 || workspaces[0] !== "accounts")) return null;
        const id = workspaces.length ? "apps.advertising" : "apps.integrations";
        return pages.find(page => !page.owner_only && page.id === id) || null;
    }
    if (pathname === financePath || pathname === financePath + "/daily-movements") {
        const id = financePageId(pathname, requested);
        return pages.find((page) => !page.owner_only && page.id === id) || null;
    }
    if (pathname === financePath + "/financial-position" && requested.has("view")) {
        if (requested.getAll("view").length !== 1 || !Object.hasOwn(employeeReportGroups, requested.get("view"))
            || ["workspace", "tab", "section", "action"].some(key=>requested.has(key))) return null;
        return pages.find(page=>!page.owner_only && page.id === "balances.reports") || null;
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
