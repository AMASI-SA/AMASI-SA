import api from "../lib/api";
import { startTikTokConnection } from "./tiktokIntegrationsV2";

jest.mock("../lib/api", () => ({
    post: jest.fn(),
}));

describe("TikTok Integrations V2 client", () => {
    beforeEach(() => {
        api.post.mockReset();
    });

    test("accepts only the official TikTok Ads authorization host", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://ads.tiktok.com/marketing_api/auth?app_id=1&state=signed",
                provider: "tiktok_ads",
            },
        });
        const result = await startTikTokConnection();
        expect(result.provider).toBe("tiktok_ads");
        expect(result.authorization_url).toContain("https://ads.tiktok.com/");
    });

    test("rejects an untrusted authorization host", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://evil.example/marketing_api/auth?state=stolen",
            },
        });
        await expect(startTikTokConnection()).rejects.toThrow(
            "tiktok_authorization_url_untrusted",
        );
    });
});
