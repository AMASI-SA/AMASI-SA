jest.mock("react-router-dom", () => ({
    Link: ({ children }) => children,
}));

import {
    ADVERTISING_PROVIDER_IDS,
    focusedIntegrationProvider,
    integrationWorkspaceFromSearchParams,
    providersForIntegrationWorkspace,
    summarizeAdvertisingWorkspace,
} from "./integrationWorkspaces";
import {
    MEZAN_V2_NAV_SECTIONS,
    activeNavigationSection,
    isNavigationItemActive,
} from "../components/MezanV2NavigationShell";

const providers = [
    {
        provider: "snapchat_ads",
        connection_status: "connected",
        connection_provenance: "api_connection",
        health: { status: "healthy" },
        permissions: { unknown: false, missing: [] },
        accounts: [
            { external_account_id: "snap-usd", currency: "USD", timezone: "America/Los_Angeles" },
            { external_account_id: "snap-sar", currency: "SAR", timezone: "Asia/Riyadh" },
        ],
    },
    {
        provider: "meta_ads",
        connection_status: "needs_reauth",
        connection_provenance: "api_connection",
        health: { status: "degraded" },
        permissions: { unknown: false, missing: ["ads_read"] },
        accounts: [{ external_account_id: "meta-1", currency: "SAR", timezone: "Asia/Riyadh" }],
    },
    {
        provider: "tiktok_ads",
        connection_status: "data_available",
        connection_provenance: "data_feed",
        health: { status: "healthy" },
        permissions: { unknown: true, missing: [] },
        accounts: [],
    },
    {
        provider: "google_ads",
        connection_status: "not_configured",
        connection_provenance: "disconnected",
        health: { status: "unknown" },
        permissions: { unknown: true, missing: [] },
        accounts: [],
    },
    { provider: "salla", connection_status: "connected", accounts: [{ external_account_id: "store-1" }] },
];

test("advertising accounts workspace is explicit and excludes commerce apps", () => {
    const params = new URLSearchParams("workspace=accounts&provider=meta_ads");
    expect(integrationWorkspaceFromSearchParams(params)).toBe("accounts");
    expect(focusedIntegrationProvider(params, providers)).toBe("meta_ads");
    expect(providersForIntegrationWorkspace(providers, "accounts").map((row) => row.provider)).toEqual(
        ADVERTISING_PROVIDER_IDS,
    );
    expect(providersForIntegrationWorkspace(providers, "accounts").some((row) => row.provider === "salla")).toBe(false);
});

test("financial provider workspace is explicit and separate from advertising accounts", () => {
    const params = new URLSearchParams("workspace=financial");
    expect(integrationWorkspaceFromSearchParams(params)).toBe("financial");
    expect(focusedIntegrationProvider(params, providers)).toBe("");
});

test("advertising summary counts accounts, currencies, timezones and attention without accounting data", () => {
    expect(summarizeAdvertisingWorkspace(providers)).toEqual({
        providers_total: 4,
        api_connections: 2,
        connected_providers: 1,
        accounts_visible: 3,
        currencies: 2,
        timezones: 2,
        attention_required: 1,
    });
});

test("Mezan 2 applications navigation separates all apps from advertising accounts", () => {
    const apps = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "apps");
    expect(apps.items.map((item) => item.to)).toEqual([
        "/integrations-v2",
        "/integrations-v2?workspace=accounts",
        "/integrations-v2?workspace=financial",
    ]);

    const location = {
        pathname: "/integrations-v2",
        search: "?workspace=accounts",
    };
    expect(activeNavigationSection(location)?.id).toBe("apps");
    expect(apps.items.filter((item) => isNavigationItemActive(location, item)).map((item) => item.label)).toEqual([
        "الحسابات الإعلانية",
    ]);
});
