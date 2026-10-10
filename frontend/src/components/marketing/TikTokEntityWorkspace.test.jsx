import React, { act } from "react";
import { createRoot } from "react-dom/client";
import TikTokEntityWorkspace from "./TikTokEntityWorkspace";
import api from "../../lib/api";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
jest.mock("../../services/tiktokIntegrationsV2", () => ({ syncTikTokReporting: jest.fn() }));

function report(id, type = "campaign") {
    return { entities: [{ entity_id: id, entity_name: `Real ${id}`, account_id: "70001",
        account_name: "Account", campaign_id: "campaign-1", adgroup_id: type === "adgroup" ? id : null,
        status: "ENABLE", spend_sar: 10.25, conversions: 2, impressions: 100, clicks: 5, ctr_pct: 5 }],
        campaign_pagination: { page: 1, pages: 1, total: 1 } };
}

let container, root;
beforeEach(() => {
    api.get.mockReset();
    container = document.createElement("div"); document.body.appendChild(container);
    root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test("displays real identity and drills into the campaign's adgroups", async () => {
    api.get.mockResolvedValueOnce({ data: report("campaign-1") }).mockResolvedValueOnce({ data: report("adgroup-1", "adgroup") });
    await act(async () => root.render(<TikTokEntityWorkspace dateFrom="2026-10-03" dateTo="2026-10-09" />));
    expect(container.textContent).toContain("Real campaign-1");
    expect(container.textContent).toContain("تحويلات TikTok");
    const drill = [...container.querySelectorAll("button")].find((button) => button.textContent === "عرض المجموعات");
    await act(async () => drill.click());
    expect(api.get).toHaveBeenLastCalledWith("/integrations-v2/tiktok_ads/workspace", { params: {
        from_date: "2026-10-03", to_date: "2026-10-09", entity_type: "adgroup", page: 1, limit: 25,
        campaign_query: "", campaign_id: "campaign-1", adgroup_id: undefined,
    } });
    expect(container.textContent).toContain("Real adgroup-1");
    expect(container.querySelector("table").textContent).not.toContain("Real campaign-1");
});

test("late campaign response cannot overwrite a newer ads selection", async () => {
    let resolveOld;
    api.get.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
        .mockResolvedValueOnce({ data: report("ad-1", "ad") });
    await act(async () => root.render(<TikTokEntityWorkspace dateFrom="2026-10-03" dateTo="2026-10-09" />));
    const ads = [...container.querySelectorAll("button")].find((button) => button.textContent === "الإعلانات");
    await act(async () => ads.click());
    await act(async () => resolveOld({ data: report("old-campaign") }));
    expect(container.textContent).toContain("Real ad-1");
    expect(container.textContent).not.toContain("Real old-campaign");
});
