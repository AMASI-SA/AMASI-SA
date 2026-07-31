import React from "react";
import { render, screen } from "@testing-library/react";
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

test("renders the Meta account selection and reporting control inside IntegrationCardV2", () => {
    render(
        <IntegrationCardV2
            integration={{ ...base, provider: "meta_ads" }}
            onTest={() => {}}
            onSync={() => {}}
            onSettings={() => {}}
        />,
    );
    expect(screen.getByTestId("meta-reporting-control-host")).toBeInTheDocument();
    expect(screen.getByTestId("meta-reporting-control")).toBeInTheDocument();
});

test("does not render the Meta control for Snapchat", () => {
    render(
        <IntegrationCardV2
            integration={{ ...base, provider: "snapchat_ads" }}
            snapchatScope={{ selection: { accounts: [] }, summary: null }}
            onTest={() => {}}
            onSync={() => {}}
            onSettings={() => {}}
        />,
    );
    expect(screen.queryByTestId("meta-reporting-control-host")).not.toBeInTheDocument();
});
