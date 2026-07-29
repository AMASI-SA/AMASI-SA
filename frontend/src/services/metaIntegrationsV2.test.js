import api from "../lib/api";
import { startMetaConnection } from "./metaIntegrationsV2";

jest.mock("../lib/api", () => ({
    post: jest.fn(),
}));

describe("Meta Integrations V2 client", () => {
    beforeEach(() => {
        api.post.mockReset();
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
});
