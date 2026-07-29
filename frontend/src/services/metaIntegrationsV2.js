import api from "../lib/api";

const META_OAUTH_PATH = /^\/v\d{1,2}\.\d\/dialog\/oauth$/;

export async function startMetaConnection() {
    const response = await api.post("/integrations-v2/meta/connect/start");
    const authorizationUrl = String(response.data?.authorization_url || "").trim();
    if (!authorizationUrl) throw new Error("meta_authorization_url_missing");

    let parsed;
    try {
        parsed = new URL(authorizationUrl);
    } catch {
        throw new Error("meta_authorization_url_invalid");
    }
    if (
        parsed.protocol !== "https:"
        || parsed.hostname !== "www.facebook.com"
        || !META_OAUTH_PATH.test(parsed.pathname)
    ) {
        throw new Error("meta_authorization_url_untrusted");
    }
    return {
        authorization_url: authorizationUrl,
        expires_at: response.data?.expires_at || null,
        provider: response.data?.provider || "meta_ads",
        scopes: Array.isArray(response.data?.scopes) ? response.data.scopes : [],
        graph_version: response.data?.graph_version || null,
    };
}
