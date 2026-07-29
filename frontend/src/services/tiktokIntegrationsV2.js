import api from "../lib/api";

export async function startTikTokConnection() {
    const response = await api.post("/integrations-v2/tiktok/connect/start");
    const authorizationUrl = String(response.data?.authorization_url || "").trim();
    if (!authorizationUrl) throw new Error("tiktok_authorization_url_missing");

    let parsed;
    try {
        parsed = new URL(authorizationUrl);
    } catch {
        throw new Error("tiktok_authorization_url_invalid");
    }
    if (parsed.protocol !== "https:" || parsed.hostname !== "ads.tiktok.com") {
        throw new Error("tiktok_authorization_url_untrusted");
    }
    return {
        authorization_url: authorizationUrl,
        expires_at: response.data?.expires_at || null,
        provider: response.data?.provider || "tiktok_ads",
    };
}
