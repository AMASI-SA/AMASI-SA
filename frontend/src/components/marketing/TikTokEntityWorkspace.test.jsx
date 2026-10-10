import React, { act } from "react";
import { createRoot } from "react-dom/client";
import TikTokEntityWorkspace from "./TikTokEntityWorkspace";
import api from "../../lib/api";
import { syncTikTokReporting } from "../../services/tiktokIntegrationsV2";

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
    api.post.mockReset();
    syncTikTokReporting.mockReset();
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
    expect(api.get).toHaveBeenLastCalledWith("/integrations-v2/tiktok_ads/workspace", { signal: expect.any(AbortSignal), params: {
        from_date: "2026-10-03", to_date: "2026-10-09", entity_type: "adgroup", page: 1, limit: 25,
        campaign_query: "", campaign_id: "campaign-1", adgroup_id: undefined,
        account_id: "70001",
    } });
    expect(container.textContent).toContain("Real adgroup-1");
    expect(container.querySelector("table").textContent).not.toContain("Real campaign-1");
});

test("late campaign response cannot overwrite a newer ads selection", async () => {
    let resolveOld;
    api.get.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
        .mockResolvedValueOnce({ data: report("ad-1", "ad") });
    await act(async () => root.render(<TikTokEntityWorkspace dateFrom="2026-10-03" dateTo="2026-10-09" />));
    const oldSignal = api.get.mock.calls[0][1].signal;
    const ads = [...container.querySelectorAll("button")].find((button) => button.textContent === "الإعلانات");
    await act(async () => ads.click());
    expect(oldSignal.aborted).toBe(true);
    await act(async () => resolveOld({ data: report("old-campaign") }));
    expect(container.textContent).toContain("Real ad-1");
    expect(container.textContent).not.toContain("Real old-campaign");
});

test("sync completion refreshes the latest range callback when filters change while waiting", async () => {
    let finish;
    syncTikTokReporting.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    api.get.mockResolvedValue({ data: report("campaign-1") });
    const oldRefresh = jest.fn(), currentRefresh = jest.fn();
    await act(async () => root.render(<TikTokEntityWorkspace dateFrom="2026-10-10" dateTo="2026-10-10" onSynced={oldRefresh} />));
    const sync = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("مزامنة الحملات والتقارير"));
    await act(async () => sync.click());
    await act(async () => root.render(<TikTokEntityWorkspace dateFrom="2026-10-03" dateTo="2026-10-09" onSynced={currentRefresh} />));
    await act(async () => finish({ status: "complete", errors_count: 0, date_from: "2026-09-11", date_to: "2026-10-10", hierarchy: { entity_counts: { campaign: 1, adgroup: 1, ad: 3 } } }));
    expect(currentRefresh).toHaveBeenCalledTimes(1);
    expect(oldRefresh).not.toHaveBeenCalled();
    expect(api.get.mock.calls.at(-1)[1].params.from_date).toBe("2026-10-03");
    expect(container.textContent).toContain("2026-09-11 ← 2026-10-10");
});

test("Smart Plus rows label the reporting creative ID separately from the Ads Manager ad ID", async () => {
    const data = report("creative-1", "ad");
    data.entities[0] = { ...data.entities[0], identity_level: "creative", platform_ad_id: "ad-v2-1" };
    api.get.mockResolvedValue({ data });
    await act(async () => root.render(<TikTokEntityWorkspace dateFrom="2026-10-03" dateTo="2026-10-09" />));
    const ads = [...container.querySelectorAll("button")].find((button) => button.textContent === "الإعلانات");
    await act(async () => ads.click());
    const table = container.querySelector("table");
    expect(table.textContent).toContain("تصميم Smart+");
    expect(table.textContent).toContain("معرّف التصميم: creative-1");
    expect(table.textContent).toContain("معرّف الإعلان: ad-v2-1");
});


test("one-campaign AI uses verified row and current dates without loading media", async () => {
    const data = report("campaign-1"); data.entities[0].data_complete = true;
    api.get.mockResolvedValue({ data });
    api.post.mockResolvedValue({ data: { recommendation: { focus: "tracking",
        summary: "راقب التتبع دون تعديل الميزانية.", next_step: "راجع ربط طلبات سلة." },
        context: { salla_evidence: { exact_campaign_id_records: 1, sales_sar: null, profit_sar: null } } } });
    await act(async () => root.render(<TikTokEntityWorkspace dateFrom="2026-10-03" dateTo="2026-10-09" />));
    const analyze = [...container.querySelectorAll("button")].find((button) => button.textContent === "تحليل بالذكاء");
    expect(analyze).toBeDefined();
    await act(async () => analyze.click());
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith("/integrations-v2/tiktok_ads/analyze-campaign",
        { account_id: "70001", campaign_id: "campaign-1", from_date: "2026-10-03", to_date: "2026-10-09" },
        { signal: expect.any(AbortSignal) });
    expect(container.textContent).toContain("راقب التتبع دون تعديل الميزانية.");
    expect(container.textContent).toContain("سجلات طلبات مؤكدة الإسناد");
    expect(container.querySelectorAll("video, img")).toHaveLength(0);
});

test("changing filters closes analysis and aborts its old pending request", async () => {
    const data = report("campaign-1"); data.entities[0].data_complete = true;
    api.get.mockResolvedValue({ data });
    let finish;
    api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await act(async () => root.render(<TikTokEntityWorkspace dateFrom="2026-10-03" dateTo="2026-10-09" />));
    await act(async () => [...container.querySelectorAll("button")].find((b) => b.textContent === "تحليل بالذكاء").click());
    const signal = api.post.mock.calls[0][2].signal;
    await act(async () => root.render(<TikTokEntityWorkspace dateFrom="2026-10-10" dateTo="2026-10-10" />));
    expect(signal.aborted).toBe(true);
    expect(container.querySelector('[data-testid="tiktok-native-ai-analysis"]')).toBeNull();
    await act(async () => finish({ data: { recommendation: { summary: "Stale AI text" } } }));
    expect(container.textContent).not.toContain("Stale AI text");
    expect(api.post).toHaveBeenCalledTimes(1);
});
