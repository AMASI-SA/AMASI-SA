import {
    PROVIDER_ORDER,
    filterIntegrationProviders,
    normalizeIntegrationOverview,
    redactIntegrationValue,
    summarizeCapabilityStates,
} from "./integrationsV2";

test("overview always contains the ten providers in the fixed order", () => {
    const overview = normalizeIntegrationOverview({
        providers: [{ provider: "qoyod", connection_status: "connected" }],
    });

    expect(overview.providers.map((row) => row.provider)).toEqual(PROVIDER_ORDER);
    expect(overview.providers).toHaveLength(10);
    expect(overview.providers.find((row) => row.provider === "qoyod").connection_status)
        .toBe("connected");
    expect(overview.providers.find((row) => row.provider === "google_ads").connection_status)
        .toBe("not_configured");
});

test("normalization preserves unknown values instead of converting them to zero", () => {
    const overview = normalizeIntegrationOverview({
        providers: [{
            provider: "meta_ads",
            connection_status: "connected",
            data_delay_minutes: null,
            health: { status: "unknown", score: null, data_quality: "unknown" },
            accounts: [{ health_score: null, data_delay_minutes: null }],
        }],
    });
    const meta = overview.providers.find((row) => row.provider === "meta_ads");

    expect(meta.health.score).toBeNull();
    expect(meta.data_delay_minutes).toBeNull();
    expect(meta.accounts[0].health_score).toBeNull();
});

test("secret-shaped fields and bearer text are removed from frontend state", () => {
    const sentinel = "SENTINEL-DO-NOT-LEAK";
    const safe = redactIntegrationValue({
        access_token: sentinel,
        nested: {
            clientSecret: sentinel,
            message: `Bearer ${sentinel}abcdefgh`,
            query: `https://provider.example/callback?token=${sentinel}`,
            app: `app_secret=${sentinel}`,
            spaced: `refresh token: ${sentinel}`,
            safe_message: "انتهت صلاحية الربط",
        },
    });

    expect(JSON.stringify(safe)).not.toContain(sentinel);
    expect(safe.nested.safe_message).toBe("انتهت صلاحية الربط");
    expect(safe.nested.message).toBe("تم حجب تفاصيل حساسة");
});

test("advertising write actions remain policy-blocked", () => {
    const overview = normalizeIntegrationOverview({
        safety_policy: { advertising_mutations_enabled: true },
        providers: [{
            provider: "snapchat_ads",
            connection_status: "connected",
            capabilities: {
                "campaigns.create": {
                    state: "approval_required",
                    available: true,
                    approval_required: true,
                    blocked_by_policy: true,
                    reason: "approval",
                },
            },
            actions: {
                disconnect: { enabled: true },
            },
        }],
    });
    const snap = overview.providers.find((row) => row.provider === "snapchat_ads");

    expect(overview.safety_policy.advertising_mutations_enabled).toBe(false);
    expect(snap.capabilities["campaigns.create"].available).toBe(false);
    expect(snap.capabilities["campaigns.create"].blocked_by_policy).toBe(true);
});

test("filters and capability summaries support the control-centre tabs", () => {
    const overview = normalizeIntegrationOverview({
        providers: [
            {
                provider: "salla",
                name_ar: "سلة",
                connection_status: "connected",
                health: { status: "healthy", score: 95, data_quality: "good" },
            },
            {
                provider: "meta_ads",
                connection_status: "needs_reauth",
                permissions: { current: [], missing: ["ads_read"], unknown: false },
            },
            {
                provider: "google_ads",
                connection_status: "connected",
                health: { status: "unhealthy", score: 20, data_quality: "stale" },
            },
        ],
    });

    const attention = filterIntegrationProviders(overview.providers, { status: "attention" })
        .map((row) => row.provider);
    expect(attention).toContain("meta_ads");
    expect(attention).toContain("google_ads");
    expect(overview.summary.attention_required).toBe(2);
    expect(filterIntegrationProviders(overview.providers, { query: "سلة" })
        .map((row) => row.provider)).toEqual(["salla"]);
    expect(summarizeCapabilityStates({
        a: { state: "available" },
        b: { state: "approval_required" },
        c: { state: "approval_required" },
    })).toEqual({ available: 1, approval_required: 2 });
});
