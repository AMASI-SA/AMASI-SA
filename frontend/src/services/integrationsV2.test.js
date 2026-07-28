import {
    PROVIDER_ORDER,
    filterIntegrationProviders,
    normalizeIntegrationOverview,
    redactIntegrationValue,
    summarizeCapabilityStates,
} from "./integrationsV2";

test("overview always contains the eleven providers in the fixed order", () => {
    const overview = normalizeIntegrationOverview({
        providers: [{ provider: "qoyod", connection_status: "connected", connection_provenance: "legacy_integration" }],
    });
    expect(overview.providers.map((row) => row.provider)).toEqual(PROVIDER_ORDER);
    expect(overview.providers).toHaveLength(11);
    expect(overview.providers.find((row) => row.provider === "qoyod").connection_status).toBe("connected");
    expect(overview.providers.find((row) => row.provider === "google_ads").connection_status).toBe("not_configured");
});

test("OpenAI runtime card remains connected and distinguishes image readiness", () => {
    const overview = normalizeIntegrationOverview({ providers: [{
        provider: "openai",
        name_ar: "OpenAI · ذكاء ميزان",
        connection_status: "connected",
        connection_provenance: "api_connection",
        source_mode: "runtime_environment",
        permissions: { current: ["runtime_configured"], missing: [], unknown: false },
        capabilities: {
            "analysis.generate": { state: "available", available: true, reason: "analysis ready" },
            "images.execute": { state: "planned", available: false, blocked_by_policy: true, reason: "image engine pending" },
        },
        health: { status: "healthy", score: 100, data_quality: "good" },
    }] });
    const openai = overview.providers.find((row) => row.provider === "openai");
    expect(openai.connection_status).toBe("connected");
    expect(openai.connection_provenance).toBe("api_connection");
    expect(openai.capabilities["analysis.generate"].state).toBe("available");
    expect(openai.capabilities["images.execute"].state).toBe("planned");
});

test("normalization preserves unknown values instead of converting them to zero", () => {
    const overview = normalizeIntegrationOverview({ providers: [{ provider: "meta_ads", connection_status: "connected", connection_provenance: "api_connection", data_delay_minutes: null, health: { status: "unknown", score: null, data_quality: "unknown" }, accounts: [{ health_score: null, data_delay_minutes: null }] }] });
    const meta = overview.providers.find((row) => row.provider === "meta_ads");
    expect(meta.health.score).toBeNull();
    expect(meta.data_delay_minutes).toBeNull();
    expect(meta.accounts[0].health_score).toBeNull();
});

test("secret-shaped fields and bearer text are removed from frontend state", () => {
    const sentinel = "SENTINEL-DO-NOT-LEAK";
    const safe = redactIntegrationValue({ access_token: sentinel, nested: { clientSecret: sentinel, message: `Bearer ${sentinel}abcdefgh`, query: `https://provider.example/callback?token=${sentinel}`, app: `app_secret=${sentinel}`, spaced: `refresh token: ${sentinel}`, safe_message: "انتهت صلاحية الربط" } });
    expect(JSON.stringify(safe)).not.toContain(sentinel);
    expect(safe.nested.safe_message).toBe("انتهت صلاحية الربط");
    expect(safe.nested.message).toBe("تم حجب تفاصيل حساسة");
});

test("advertising write actions remain policy-blocked", () => {
    const overview = normalizeIntegrationOverview({ safety_policy: { advertising_mutations_enabled: true }, providers: [{ provider: "snapchat_ads", connection_status: "connected", connection_provenance: "legacy_integration", capabilities: { "campaigns.create": { state: "approval_required", available: true, approval_required: true, blocked_by_policy: true, reason: "approval" } }, actions: { disconnect: { enabled: true } } }] });
    const snap = overview.providers.find((row) => row.provider === "snapchat_ads");
    expect(overview.safety_policy.advertising_mutations_enabled).toBe(false);
    expect(snap.capabilities["campaigns.create"].available).toBe(false);
    expect(snap.capabilities["campaigns.create"].blocked_by_policy).toBe(true);
});

test("filters and capability summaries support the control-centre tabs", () => {
    const overview = normalizeIntegrationOverview({ providers: [
        { provider: "salla", name_ar: "سلة", connection_status: "connected", connection_provenance: "api_connection", health: { status: "healthy", score: 95, data_quality: "good" } },
        { provider: "meta_ads", connection_status: "needs_reauth", connection_provenance: "api_connection", permissions: { current: [], missing: ["ads_read"], unknown: false } },
        { provider: "google_ads", connection_status: "not_configured", connection_provenance: "disconnected", health: { status: "unhealthy", score: 20, data_quality: "stale" } },
    ] });
    const attention = filterIntegrationProviders(overview.providers, { status: "attention" }).map((row) => row.provider);
    expect(attention).toContain("meta_ads");
    expect(attention).toContain("google_ads");
    expect(overview.summary.attention_required).toBe(2);
    expect(filterIntegrationProviders(overview.providers, { query: "سلة" }).map((row) => row.provider)).toEqual(["salla"]);
    expect(filterIntegrationProviders(overview.providers, { status: "api_connection" }).map((row) => row.provider)).toEqual(["salla", "meta_ads"]);
    expect(summarizeCapabilityStates({ a: { state: "available" }, b: { state: "approval_required" }, c: { state: "approval_required" } })).toEqual({ available: 1, approval_required: 2 });
});

test("connection provenance produces exact independent counters", () => {
    const overview = normalizeIntegrationOverview({ providers: [
        { provider: "salla", connection_status: "connected", connection_provenance: "api_connection" },
        { provider: "meta_ads", connection_status: "connected", connection_provenance: "api_connection" },
        { provider: "snapchat_ads", connection_status: "connected", connection_provenance: "legacy_integration" },
        { provider: "qoyod", connection_status: "connected", connection_provenance: "legacy_integration" },
        { provider: "tiktok_ads", connection_status: "data_available", connection_provenance: "data_feed" },
    ] });
    expect(overview.summary).toMatchObject({ total: 11, connected: 4, api_connections: 2, legacy_integrations: 2, data_feeds: 1, disconnected: 5, planned: 1, unknown: 0 });
    expect(overview.summary.api_connections + overview.summary.legacy_integrations + overview.summary.data_feeds + overview.summary.disconnected + overview.summary.planned + overview.summary.unknown).toBe(overview.summary.total);
    expect(filterIntegrationProviders(overview.providers, { status: "data_feed" }).map((row) => row.provider)).toEqual(["tiktok_ads"]);
    expect(filterIntegrationProviders(overview.providers, { status: "disconnected" })).toHaveLength(5);
});

test("unknown connection provenance has its own counter and filter", () => {
    const overview = normalizeIntegrationOverview({ providers: [{ provider: "google_ads", connection_status: "data_available", connection_provenance: "unknown" }] });
    expect(overview.summary.unknown).toBe(1);
    expect(filterIntegrationProviders(overview.providers, { status: "unknown" }).map((row) => row.provider)).toEqual(["google_ads"]);
    expect(overview.summary.api_connections + overview.summary.legacy_integrations + overview.summary.data_feeds + overview.summary.disconnected + overview.summary.planned + overview.summary.unknown).toBe(overview.summary.total);
});

test("unknown permissions are not counted missing and action links stay internal", () => {
    const overview = normalizeIntegrationOverview({ providers: [{ provider: "meta_ads", connection_status: "connected", connection_provenance: "api_connection", permissions: { current: [], missing: ["ads_read"], unknown: true }, actions: { settings: { enabled: true, href: "/settings?tab=meta" }, reconnect: { enabled: true, href: "https://evil.example/steal" }, disconnect: { enabled: true, href: "javascript:alert(1)" } } }] });
    const meta = overview.providers.find((row) => row.provider === "meta_ads");
    expect(overview.summary.missing_permissions).toBe(0);
    expect(meta.actions.settings.href).toBe("/settings?tab=meta");
    expect(meta.actions.reconnect.href).toBeNull();
    expect(meta.actions.disconnect.href).toBeNull();
    expect(meta.actions.disconnect.enabled).toBe(false);
});
