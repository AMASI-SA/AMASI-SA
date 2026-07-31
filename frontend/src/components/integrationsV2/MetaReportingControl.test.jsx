import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import MetaReportingControl from "./MetaReportingControl";

const safeSelection = {
    provider: "meta_ads",
    discovered_count: 2,
    selected_count: 1,
    selection_required: false,
    source_only: true,
    provider_write_reached: false,
    campaign_write_reached: false,
    accounting_write_reached: false,
    qoyod_write_reached: false,
    accounts: [
        {
            account_id: "act_111",
            display_name: "Amasi Meta Main",
            currency: "USD",
            timezone: "Asia/Riyadh",
            business_name: "AMASI",
            selected: true,
            selection_status: "selected",
        },
        {
            account_id: "act_222",
            display_name: "Unused Meta Account",
            currency: "USD",
            timezone: "Asia/Riyadh",
            selected: false,
            selection_status: "discovered",
        },
    ],
};

function integration(overrides = {}) {
    return {
        provider: "meta_ads",
        connection_status: "connected",
        connection_provenance: "api_connection",
        actions: {
            sync_data: { enabled: true, reason: null },
        },
        ...overrides,
    };
}

describe("MetaReportingControl", () => {
    test("renders owner-selected accounts and enables the seven-day sync", () => {
        const markup = renderToStaticMarkup(
            <MetaReportingControl
                integration={integration()}
                initialSelection={safeSelection}
            />,
        );
        expect(markup).toContain("حسابات وتقارير Meta المباشرة");
        expect(markup).toContain("Amasi Meta Main");
        expect(markup).toContain("Unused Meta Account");
        expect(markup).toContain("1 حساب Meta محدد");
        expect(markup).toContain("مزامنة 7 أيام");
        expect(markup).toContain('data-testid="meta-reporting-sync-seven-days"');
        expect(markup).not.toMatch(
            /disabled=""[^>]*data-testid="meta-reporting-sync-seven-days"/,
        );
    });

    test("keeps reporting disabled when no account is selected", () => {
        const selection = {
            ...safeSelection,
            selected_count: 0,
            selection_required: true,
            accounts: safeSelection.accounts.map((account) => ({
                ...account,
                selected: false,
                selection_status: "discovered",
            })),
        };
        const markup = renderToStaticMarkup(
            <MetaReportingControl
                integration={integration()}
                initialSelection={selection}
            />,
        );
        expect(markup).toContain("0 حساب Meta محدد");
        expect(markup).toMatch(
            /disabled=""[^>]*data-testid="meta-reporting-sync-seven-days"/,
        );
    });
});
