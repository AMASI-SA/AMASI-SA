import api from "../../lib/api";
import { getSnapchatAdSquadPerformance } from "../../services/snapchatAdSquadPerformance";
import { getSnapchatAdPerformance } from "../../services/snapchatAdPerformance";
import { AD_SQUAD_SORT_OPTIONS } from "./AdSquadSortControls";
import { AD_MANAGER_SORT_OPTIONS } from "./AdManagerTable";

jest.mock("../../lib/api", () => ({
    get: jest.fn(),
    interceptors: {
        request: { use: jest.fn() },
        response: { use: jest.fn() },
    },
}));

beforeEach(() => api.get.mockReset());

test("native sort controls preserve active and add orders spend and newest", () => {
    expect(AD_SQUAD_SORT_OPTIONS.map((item) => item.id)).toEqual([
        "orders", "spend", "newest", "active",
    ]);
    expect(AD_MANAGER_SORT_OPTIONS.map((item) => item.id)).toEqual([
        "orders", "spend", "newest",
    ]);
});

test("Ad Squad request filters active Campaigns and sorts before pagination", async () => {
    api.get.mockResolvedValueOnce({ data: { ad_squads: [], totals: {}, pagination: {} } });
    await getSnapchatAdSquadPerformance({ activeCampaignsOnly: true, sortBy: "orders" });
    expect(api.get).toHaveBeenCalledWith(
        "/integrations-v2/snapchat_ads/ad-squad-report",
        expect.objectContaining({
            params: expect.objectContaining({
                active_campaigns_only: true,
                sort_by: "orders",
            }),
        }),
    );
});

test("Ad request filters active Campaigns and supports spend ordering", async () => {
    api.get.mockResolvedValueOnce({ data: { ads: [], totals: {}, pagination: {} } });
    await getSnapchatAdPerformance({ activeCampaignsOnly: true, sortBy: "spend" });
    expect(api.get).toHaveBeenCalledWith(
        "/integrations-v2/snapchat_ads/ad-report",
        expect.objectContaining({
            params: expect.objectContaining({
                active_campaigns_only: true,
                sort_by: "spend",
            }),
        }),
    );
});
