import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
        post: jest.fn(),
    },
    formatApiErrorDetail: jest.fn(() => ""),
}));

jest.mock("../services/snapchatCampaignManagement", () => ({
    approveSnapchatManagementProposal: jest.fn(),
    clearSnapchatManagementPreviewResume: jest.fn(),
    createSnapchatManagementProposal: jest.fn(),
    diagnoseSnapchatManagementPixels: jest.fn(),
    executeSnapchatManagementProposal: jest.fn(),
    getSnapchatEntitySettings: jest.fn(),
    getSnapchatManagementPreviewResume: jest.fn(() => null),
    getSnapchatManagementReadiness: jest.fn(),
    listSnapchatManagementProposals: jest.fn(),
    managementError: (error, fallback) => error?.message || fallback,
    microToNativeAmount: (value) => value == null || value === "" ? null : Number(value) / 1_000_000,
    nativeAmountToMicro: (value) => value == null || value === "" ? null : Math.round(Number(value) * 1_000_000),
    pollSnapchatManagementProposal: jest.fn(),
    reconcileSnapchatManagementProposal: jest.fn(),
    resumeSnapchatManagementProposal: jest.fn(),
    rollbackSnapchatManagementProposal: jest.fn(),
    snapchatBidLabel: (strategy) => strategy === "TARGET_COST" ? "Target Cost" : strategy === "LOWEST_COST_WITH_MAX_BID" ? "Max Bid" : "Bid",
    snapchatFinancialSettingsReady: jest.fn(() => false),
}));

jest.mock("../services/mezanProductsV2", () => ({
    listProductsV2: jest.fn(() => Promise.resolve({ items: [] })),
}));

import api from "../lib/api";
import {
    createSnapchatManagementProposal,
    executeSnapchatManagementProposal,
    getSnapchatEntitySettings,
    getSnapchatManagementReadiness,
    listSnapchatManagementProposals,
} from "../services/snapchatCampaignManagement";
import SnapchatV2Page from "./SnapchatV2Page";

describe("SnapchatV2Page read-only load", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        jest.clearAllMocks();
        getSnapchatEntitySettings.mockResolvedValue([]);
        getSnapchatManagementReadiness.mockResolvedValue({
            proposal_enabled: true,
            execution_enabled: false,
            activation_enabled: false,
            accounts: [{
                account_id: "account-1",
                display_name: "AMASI",
                currency: "USD",
                management_allowed: true,
                creative_allowed: true,
                pixels: [],
            }],
        });
        listSnapchatManagementProposals.mockResolvedValue([]);
        api.get.mockImplementation((url) => {
            if (url.endsWith("/status")) {
                return Promise.resolve({
                    data: {
                        selected_account: {
                            ad_account_id: "account-1",
                            display_name: "AMASI",
                            currency: "USD",
                            timezone: "America/Los_Angeles",
                        },
                    },
                });
            }
            if (url.endsWith("/report")) {
                return Promise.resolve({
                    data: {
                        currency: "USD",
                        totals: {
                            delivery: { spend: { amount: 0, currency: "USD" } },
                            platform_outcomes: {},
                        },
                    },
                });
            }
            if (url.endsWith("/hourly")) {
                return Promise.resolve({ data: { hours: [], totals: {} } });
            }
            if (url.endsWith("/campaigns")) {
                return Promise.resolve({
                    data: {
                        unified: {
                            contract_version: "2",
                            entity_level: "campaign",
                            rows: [],
                            totals: null,
                        },
                        salla: { summary: {} },
                    },
                });
            }
            if (url.endsWith("/unified-readiness")) {
                return Promise.resolve({ data: { ready: true, reasons: [] } });
            }
            throw new Error(`Unexpected GET: ${url}`);
        });
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
    });

    test("render and opening management issue no POST, proposal creation, or execution", async () => {
        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();

        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-campaign-management-panel"] > button',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(getSnapchatManagementReadiness).toHaveBeenCalledTimes(1);
        expect(listSnapchatManagementProposals).toHaveBeenCalledTimes(1);
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });
});
