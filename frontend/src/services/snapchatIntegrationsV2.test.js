import api from "../lib/api";
import { startSnapchatConnection } from "./snapchatIntegrationsV2";

jest.mock("../lib/api", () => ({
    post: jest.fn(),
}));

describe("Snapchat Integrations V2 client", () => {
    beforeEach(() => {
        api.post.mockReset();
    });

    test("accepts only the official Snapchat authorization endpoint", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://accounts.snapchat.com/login/oauth2/authorize?client_id=1&state=signed",
                provider: "snapchat_ads",
                scopes: ["snapchat-marketing-api", "snapchat-offline-conversions-api"],
            },
        });
        const result = await startSnapchatConnection();
        expect(result.provider).toBe("snapchat_ads");
        expect(result.scopes).toContain("snapchat-marketing-api");
        expect(result.authorization_url).toContain("https://accounts.snapchat.com/");
    });

    test("rejects an untrusted authorization host", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://evil.example/login/oauth2/authorize?state=stolen",
            },
        });
        await expect(startSnapchatConnection()).rejects.toThrow(
            "snapchat_authorization_url_untrusted",
        );
    });

    test("rejects a trusted host with the wrong path", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://accounts.snapchat.com/phishing?state=stolen",
            },
        });
        await expect(startSnapchatConnection()).rejects.toThrow(
            "snapchat_authorization_url_untrusted",
        );
    });
});
