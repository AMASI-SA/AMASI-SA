import api from "../lib/api";
import {
    getGoogleAdsAccountSelection,
    getGoogleAdsReportingReadiness,
    saveGoogleAdsAccountSelection,
    syncGoogleAdsReporting,
} from "./googleAdsIntegrationsV2";

jest.mock("../lib/api", () => ({
    get: jest.fn(),
    put: jest.fn(),
    post: jest.fn(),
}));

afterEach(() => jest.clearAllMocks());

test("reads Google Ads reporting readiness and selection", async () => {
    api.get
        .mockResolvedValueOnce({ data: { ready: true } })
        .mockResolvedValueOnce({ data: { selected_count: 1 } });

    await expect(getGoogleAdsReportingReadiness()).resolves.toEqual({ ready: true });
    await expect(getGoogleAdsAccountSelection()).resolves.toEqual({ selected_count: 1 });
    expect(api.get).toHaveBeenNthCalledWith(
        1,
        "/integrations-v2/google_ads/reporting-readiness",
    );
    expect(api.get).toHaveBeenNthCalledWith(
        2,
        "/integrations-v2/google_ads/accounts-selection",
    );
});

test("saves selected accounts and starts bounded reporting sync", async () => {
    api.put.mockResolvedValue({ data: { selected_count: 2 } });
    api.post.mockResolvedValue({ data: { rows_saved: 9 } });

    await saveGoogleAdsAccountSelection(["123", "456"]);
    await syncGoogleAdsReporting(7);

    expect(api.put).toHaveBeenCalledWith(
        "/integrations-v2/google_ads/accounts-selection",
        { account_ids: ["123", "456"] },
    );
    expect(api.post).toHaveBeenCalledWith(
        "/integrations-v2/google_ads/reporting-sync",
        { days: 7 },
    );
});
