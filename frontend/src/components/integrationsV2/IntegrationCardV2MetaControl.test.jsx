import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import IntegrationCardV2 from "./IntegrationCardV2";

jest.mock("../../services/metaIntegrationsV2", () => ({
    getMetaAccountSelection: jest.fn(() => new Promise(() => {})),
    saveMetaAccountSelection: jest.fn(),
    startMetaReportingSync: jest.fn(),
}));

const base = {
    name_ar: "إعلانات ميتا",
    name: "Meta Ads",
    connection_status: "connected",
    connection_provenance: "api_connection",
    accounts: [],
    health: { score: 100, data_quality: "good" },
    permissions: { current: ["ads_read"], missing: [], unknown: false },
    ai: { can: [], cannot: [] },
    actions: {
        test_connection: { enabled: true },
        sync_data: { enabled: true },
        reconnect: { enabled: false },
        settings: { enabled: false },
    },
};

function renderProvider(provider, extra = {}) {
    return renderToStaticMarkup(
        <IntegrationCardV2
            integration={{ ...base, provider }}
            onTest={() => {}}
            onSync={() => {}}
            onSettings={() => {}}
            {...extra}
        />,
    );
}

test("renders the Meta account selection and reporting control inside IntegrationCardV2", () => {
    const html = renderProvider("meta_ads");
    expect(html).toContain('data-testid="meta-reporting-control-host"');
    expect(html).toContain('data-testid="meta-reporting-control"');
    expect(html).toContain("حسابات وتقارير Meta المباشرة");
});

test("does not render the Meta control for Snapchat", () => {
    const html = renderProvider("snapchat_ads", {
        snapchatScope: { selection: { accounts: [] }, summary: null },
    });
    expect(html).not.toContain('data-testid="meta-reporting-control-host"');
});
