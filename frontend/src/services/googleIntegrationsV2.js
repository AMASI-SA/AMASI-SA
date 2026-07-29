import api from "../lib/api";

export async function startGoogleConnection() {
    const response = await api.post("/integrations-v2/google/connect/start");
    const authorizationUrl = String(response.data?.authorization_url || "").trim();
    if (!authorizationUrl) throw new Error("google_authorization_url_missing");
    let parsed;
    try {
        parsed = new URL(authorizationUrl);
    } catch {
        throw new Error("google_authorization_url_invalid");
    }
    if (parsed.protocol !== "https:" || parsed.hostname !== "accounts.google.com") {
        throw new Error("google_authorization_url_untrusted");
    }
    return {
        authorization_url: authorizationUrl,
        expires_at: response.data?.expires_at || null,
        providers: Array.isArray(response.data?.providers) ? response.data.providers : [],
    };
}
