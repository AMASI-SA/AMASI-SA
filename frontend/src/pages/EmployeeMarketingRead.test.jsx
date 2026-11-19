import { act } from "react";
import { createRoot } from "react-dom/client";
import EmployeeMarketingRead from "./EmployeeMarketingRead";
import api from "../lib/api";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));

test("the grants workspace reads only the selected page and clears records on a new selection", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const host = document.createElement("div");
    const root = createRoot(host);
    api.get.mockResolvedValueOnce({ data: { columns: ["الحساب"], rows: [["الإنماء"]], has_more: false } });
    await act(async () => root.render(<EmployeeMarketingRead page={{ id: "apps.advertising", label: "الحسابات الإعلانية" }} />));
    expect(api.get.mock.calls[0][0]).toBe("/web-page-access-v1/marketing-read/apps.advertising");
    expect(host.textContent).toContain("الإنماء");
    api.get.mockRejectedValueOnce(new Error("not allowed"));
    await act(async () => root.render(<EmployeeMarketingRead page={{ id: "marketing.costs", label: "العمولات" }} />));
    expect(host.textContent).not.toContain("الإنماء");
    expect(host.querySelector("[role=alert]").textContent).toContain("تعذّر تحميل");
    await act(async () => root.unmount());
});

test("routed platform tabs read exactly the selected subpage and mark native previews", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const host = document.createElement("div");
    const root = createRoot(host);
    api.get.mockClear();
    api.get.mockResolvedValue({ data: { columns: ["العنصر"], rows: [["قراءة"]], notice: "معاينة تعليمية ببيانات تجريبية." } });
    for (const id of ["marketing.meta.ai", "marketing.google.accounts", "marketing.tiktok.content", "intelligence.drafts"]) {
        await act(async () => root.render(<EmployeeMarketingRead page={{ id, section: id.split(".")[0], label: id }} />));
        expect(api.get.mock.lastCall[0]).toBe("/web-page-access-v1/marketing-read/" + id);
        expect(host.textContent).toContain("معاينة تعليمية ببيانات تجريبية");
    }
    expect(api.get).toHaveBeenCalledTimes(4);
    await act(async () => root.unmount());
});

test("platform, customer and tab selectors cannot be interchanged in direct links", () => {
    const { grantedPageAt } = require("../lib/webPageAccess");
    const grants = [
        { id: "marketing.meta.accounts", to: "/ads-manager?provider=meta&tab=accounts" },
        { id: "intelligence.followups", to: "/customer-intelligence?tab=followups" },
    ];
    const match = (href) => grantedPageAt(grants, new URL(href, "https://mezan.invalid"))?.id;
    expect(match("/ads-manager?tab=accounts&provider=meta")).toBe("marketing.meta.accounts");
    expect(match("/customer-intelligence?tab=followups")).toBe("intelligence.followups");
    for (const href of ["/ads-manager?provider=meta", "/ads-manager?provider=google&tab=accounts",
        "/ads-manager?provider=meta&tab=ai", "/ads-manager?provider=meta&tab=accounts&tab=ai",
        "/customer-intelligence", "/customer-intelligence?tab=customers"]) {
        expect(match(href)).toBeUndefined();
    }
});

test("provider focus stays inside its authorized integration workspace and requests that provider", async () => {
    const { grantedPageAt } = require("../lib/webPageAccess");
    const { ADVERTISING_PROVIDER_IDS } = require("../lib/integrationWorkspaces");
    const apps = {id: "apps.integrations", to: "/integrations-v2", label: "التطبيقات"};
    const advertising = {id: "apps.advertising", to: "/integrations-v2?workspace=accounts", label: "الحسابات"};
    const match = (pages, href) => grantedPageAt(pages, new URL(href, "https://mezan.invalid"))?.id;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const root = createRoot(document.createElement("div"));
    api.get.mockClear();
    api.get.mockResolvedValue({data: {columns: ["التطبيق"], rows: [["إعلانات ميتا"]]}});
    for (const provider of ADVERTISING_PROVIDER_IDS) {
        const filter = "?provider=" + provider;
        expect(match([apps], apps.to + filter)).toBe(apps.id);
        expect(match([advertising], apps.to + filter)).toBeUndefined();
        expect(match([advertising], advertising.to + "&provider=" + provider)).toBe(advertising.id);
        expect(match([apps], advertising.to + "&provider=" + provider)).toBeUndefined();
        await act(async () => root.render(<EmployeeMarketingRead page={apps} search={filter}/>));
        expect(api.get.mock.lastCall[0]).toBe("/web-page-access-v1/marketing-read/apps.integrations");
        expect(api.get.mock.lastCall[1].params.q).toBe(provider);
    }
    for (const search of ["?provider=other", "?provider=google_ads&provider=meta_ads", "?workspace=financial&provider=google_ads",
        "?provider=google_ads&tab=admin", "?workspace=accounts&workspace=accounts&provider=google_ads"]) {
        expect(match([apps, advertising], apps.to + search)).toBeUndefined();
    }
    await act(async () => root.unmount());
});
