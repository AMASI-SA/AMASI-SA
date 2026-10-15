import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import IntegrationCardJs from "./IntegrationCard.js";
import IntegrationCardJsx from "./IntegrationCard.jsx";

const metaIntegration = {
    provider: "meta_ads",
    name: "Meta Ads",
    name_ar: "إعلانات ميتا",
    connection_status: "connected",
    connection_provenance: "api_connection",
    accounts: [
        {
            display_name: "أماسي",
            external_account_id: "act_799549215909312",
            currency: "SAR",
            timezone: "Asia/Riyadh",
        },
    ],
    permissions: {
        current: ["ads_read", "business_management"],
        missing: [],
        unknown: false,
    },
    health: { score: 100 },
    data_quality: "good",
    actions: {
        test_connection: { enabled: true, reason: null },
        sync_data: { enabled: true, reason: null },
        reconnect: { enabled: false, reason: null },
        settings: { enabled: false, reason: null },
        disconnect: { enabled: false, reason: null },
    },
    ai_can_do: [],
    ai_cannot_do: [],
};

const props = {
    integration: metaIntegration,
    testing: false,
    syncing: false,
    settingsAvailable: false,
    onTest: () => {},
    onSync: () => {},
    onSettings: () => {},
};

describe("IntegrationCard production entrypoints", () => {
    test.each([
        [".js", IntegrationCardJs],
        [".jsx", IntegrationCardJsx],
    ])("renders Meta reporting control through %s", (_extension, Component) => {
        const markup = renderToStaticMarkup(<Component {...props} />);
        expect(markup).toContain("حسابات وتقارير Meta المباشرة");
        expect(markup).toContain("meta-reporting-control");
    });
});
