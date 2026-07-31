import api from "../lib/api";
import {
    getMetaAccountSelection,
    normalizeMetaAccountSelection,
    normalizeMetaReportingResult,
    saveMetaAccountSelection,
    startMetaConnection,
    startMetaReportingSync,
} from "./metaIntegrationsV2";

jest.mock("../lib/api", () => ({
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
}));

const safeEnvelope = {
    source_only: true,
    provider_write_reached: false,
    campaign_write_reached: false,
    accounting_write_reached: false,
    qoyod_write_reached: false,
};

describe("Meta Integrations V2 client", () => {
    beforeEach(() => {
        api.get.mockReset();
        api.post.mockReset();
        api.put.mockReset();
    });

    test("accepts only the official versioned Facebook OAuth endpoint", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://www.facebook.com/v25.0/dialog/oauth?client_id=1&state=signed",
                provider: "meta_ads",
                scopes: ["ads_read", "ads_management", "business_management"],
                graph_version: "v25.0",
            },
        });
        const result = await startMetaConnection();
        expect(result.provider).toBe("meta_ads");
        expect(result.scopes).toContain("ads_management");
        expect(result.graph_version).toBe("v25.0");
    });

    test("rejects an untrusted authorization host", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://evil.example/v25.0/dialog/oauth?state=stolen",
            },
        });
        await expect(startMetaConnection()).rejects.toThrow(
            "meta_authorization_url_untrusted",
        );
    });

    test("rejects Facebook with an unversioned or wrong OAuth path", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://www.facebook.com/dialog/oauth?state=stolen",
            },
        });
        await expect(startMetaConnection()).rejects.toThrow(
            "meta_authorization_url_untrusted",
        );
    });

    test("reads and saves the owner-selected Meta account scope", async () => {
        const payload = {
            ...safeEnvelope,
            provider: "meta_ads",
            discovered_count: 2,
            selected_count: 1,
            selection_required: false,
            accounts: [
                {
                    account_id: "act_111",
                    display_name: "Amasi Meta",
                    currency: "USD",
                    timezone: "Asia/Riyadh",
                    selected: true,
                    selection_status: "selected",
                },
                {
                    account_id: "act_222",
                    display_name: "Unused",
                    currency: "USD",
                    timezone: "Asia/Riyadh",
                    selected: false,
                    selection_status: "discovered",
                },
            ],
        };
        api.get.mockResolvedValue({ data: payload });
        const loaded = await getMetaAccountSelection();
        expect(loaded.selected_count).toBe(1);
        expect(loaded.accounts[0].selected).toBe(true);

        api.put.mockResolvedValue({ data: payload });
        const saved = await saveMetaAccountSelection(["act_111", "act_111"]);
        expect(api.put).toHaveBeenCalledWith(
            "/integrations-v2/meta_ads/accounts-selection",
            { account_ids: ["act_111"] },
        );
        expect(saved.selected_count).toBe(1);
    });

    test("fails closed on an unsafe account selection response", () => {
        expect(() => normalizeMetaAccountSelection({
            ...safeEnvelope,
            accounting_write_reached: true,
            accounts: [],
        })).toThrow("unsafe_meta_account_selection_response");
    });

    test("polls the background reporting job until complete", async () => {
        api.post.mockResolvedValue({
            data: {
                ...safeEnvelope,
                run_id: "run-1",
                provider: "meta_ads",
                status: "queued",
            },
        });
        api.get.mockResolvedValue({
            data: {
                ...safeEnvelope,
                run_id: "run-1",
                provider: "meta_ads",
                status: "complete",
                accounts_attempted: 1,
                accounts_complete: 1,
                rows_saved: 7,
                errors_count: 0,
            },
        });
        const result = await startMetaReportingSync({
            days: 7,
            pollIntervalMs: 0,
            timeoutMs: 1000,
        });
        expect(api.post).toHaveBeenCalledWith(
            "/integrations-v2/meta_ads/sync-async",
            { days: 7 },
        );
        expect(api.get).toHaveBeenCalledWith(
            "/integrations-v2/meta_ads/sync-async/run-1",
        );
        expect(result.status).toBe("complete");
        expect(result.rows_saved).toBe(7);
    });

    test("fails closed when reporting reaches protected writes", () => {
        expect(() => normalizeMetaReportingResult({
            ...safeEnvelope,
            run_id: "unsafe",
            status: "complete",
            campaign_write_reached: true,
        })).toThrow("unsafe_meta_reporting_response");
    });

    test("rejects an invalid reporting window", async () => {
        await expect(startMetaReportingSync({ days: 32 })).rejects.toThrow(
            "invalid_meta_reporting_days",
        );
        expect(api.post).not.toHaveBeenCalled();
    });
});
