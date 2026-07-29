import api from "../lib/api";

export async function startSnapchatConnection() {
    const response = await api.post("/integrations-v2/snapchat/connect/start");
    const authorizationUrl = String(response.data?.authorization_url || "").trim();
    if (!authorizationUrl) throw new Error("snapchat_authorization_url_missing");

    let parsed;
    try {
        parsed = new URL(authorizationUrl);
    } catch {
        throw new Error("snapchat_authorization_url_invalid");
    }
    if (
        parsed.protocol !== "https:"
        || parsed.hostname !== "accounts.snapchat.com"
        || parsed.pathname !== "/login/oauth2/authorize"
    ) {
        throw new Error("snapchat_authorization_url_untrusted");
    }
    return {
        authorization_url: authorizationUrl,
        expires_at: response.data?.expires_at || null,
        provider: response.data?.provider || "snapchat_ads",
        scopes: Array.isArray(response.data?.scopes) ? response.data.scopes : [],
    };
}
