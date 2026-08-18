import { renderToStaticMarkup } from "react-dom/server";
import IntegrationCard from "./IntegrationCard";

function metaCard(reconnectEnabled) {
    return {
        provider: "meta_ads",
        name: "Meta Ads",
        name_ar: "إعلانات ميتا",
        connection_status: "connected",
        connection_provenance: "api_connection",
        accounts: [],
        permissions: {
            current: ["ads_read"],
            missing: ["pages_messaging"],
            unknown: false,
        },
        health: { score: 75, data_quality: "degraded" },
        ai: { can: [], cannot: [] },
        actions: {
            test_connection: { enabled: true },
            reconnect: { enabled: reconnectEnabled, href: null },
            settings: { enabled: false, href: null },
            disconnect: { enabled: false, href: null },
        },
    };
}

function reconnectButton(markup) {
    return markup.match(
        /<button[^>]*data-testid="integration-meta_ads-reconnect"[^>]*>/,
    )?.[0] || "";
}

test("Meta reconnect command is enabled even though it has no internal href", () => {
    const markup = renderToStaticMarkup(
        <IntegrationCard
            integration={metaCard(true)}
            settingsAvailable={false}
            onTest={() => {}}
            onSettings={() => {}}
        />,
    );

    expect(reconnectButton(markup)).not.toContain("disabled");
    expect(markup).toContain("إعادة الربط");
    expect(markup).toContain("pages_messaging");
});

test("Meta reconnect remains disabled when Backend explicitly disables it", () => {
    const markup = renderToStaticMarkup(
        <IntegrationCard
            integration={metaCard(false)}
            settingsAvailable={false}
            onTest={() => {}}
            onSettings={() => {}}
        />,
    );

    expect(reconnectButton(markup)).toContain("disabled");
});
