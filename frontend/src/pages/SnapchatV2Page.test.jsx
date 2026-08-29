import React, { StrictMode, act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("sonner", () => ({
    toast: {
        error: jest.fn(),
        success: jest.fn(),
        warning: jest.fn(),
    },
}), { virtual: true });

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
        post: jest.fn(),
    },
    formatApiErrorDetail: jest.fn(() => ""),
}));

jest.mock("../components/marketing/SnapchatCampaignManagementPanel", () => {
    const ReactModule = require("react");
    return function MockSnapchatCampaignManagementPanel() {
        return ReactModule.createElement("div", { "data-testid": "management-panel" });
    };
});

jest.mock("../components/marketing/UnifiedMarketingEntityTable", () => {
    const ReactModule = require("react");
    return function MockUnifiedMarketingEntityTable({ report, loading }) {
        return ReactModule.createElement(
            "div",
            { "data-testid": "campaign-table" },
            report ? "campaigns-ready" : loading ? "campaigns-loading" : "campaigns-empty",
        );
    };
});

jest.mock("../components/marketing/UnifiedMarketingOrdersPanel", () => {
    const ReactModule = require("react");
    return function MockUnifiedMarketingOrdersPanel() {
        return ReactModule.createElement("div", { "data-testid": "orders-panel" });
    };
});

import api from "../lib/api";
import SnapchatV2Page from "./SnapchatV2Page";


function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}


function reportPayload(headline = 2788.878357) {
    return {
        ad_account_id: "snap-account-1",
        account_timezone: "America/Los_Angeles",
        currency: "USD",
        base_spend_native: headline - 30.58466,
        headline_spend_native: headline,
        unallocated_spend_native: 30.58466,
        hourly_breakdown_complete: false,
    };
}


function hourlyPayload(spend = 123.45) {
    return {
        ad_account_id: "snap-account-1",
        account_timezone: "America/Los_Angeles",
        currency: "USD",
        hours: [{
            local_hour: "00:00",
            hour_start_utc: "2026-08-28T07:00:00Z",
            hour_end_utc: "2026-08-28T08:00:00Z",
            spend_native: spend,
            status: "confirmed_data",
        }],
    };
}


const campaignsPayload = {
    unified: {
        entity_level: "campaign",
        rows: [],
        totals: {
            platform_outcomes: {
                conversions: 113,
                revenue: { amount: 6662.35, currency: "USD" },
                roas: 2.39,
            },
            commerce_outcomes: {
                status: "complete",
                orders: 98,
                revenue: { amount: 5754.76, currency: "USD" },
                roas: 2.06,
            },
        },
    },
    salla: { summary: {} },
};


function successfulResponse(url) {
    if (url.endsWith("/report")) return Promise.resolve({ data: reportPayload() });
    if (url.endsWith("/hourly")) return Promise.resolve({ data: hourlyPayload() });
    if (url.endsWith("/campaigns")) return Promise.resolve({ data: campaignsPayload });
    if (url.endsWith("/unified-readiness")) {
        return Promise.resolve({ data: { ready: true, period: { date_from: "2026-08-27" } } });
    }
    if (url.endsWith("/status")) {
        return Promise.resolve({
            data: {
                financial_sync_status: "complete",
                selected_account: {
                    ad_account_id: "snap-account-1",
                    currency: "USD",
                    timezone: "America/Los_Angeles",
                },
            },
        });
    }
    throw new Error(`unexpected GET ${url}`);
}


async function flushPromises() {
    await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
    });
}


async function renderPage({ strict = false } = {}) {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
        root.render(strict
            ? <StrictMode><SnapchatV2Page /></StrictMode>
            : <SnapchatV2Page />);
    });
    await flushPromises();
    return { container, root };
}


async function cleanup(container, root) {
    await act(async () => root.unmount());
    container.remove();
    globalThis.IS_REACT_ACT_ENVIRONMENT = false;
}


beforeEach(() => {
    jest.clearAllMocks();
    api.get.mockImplementation(successfulResponse);
});


test("starts report, hourly, and campaigns without waiting for status", async () => {
    const pendingStatus = deferred();
    api.get.mockImplementation((url) => (
        url.endsWith("/status") ? pendingStatus.promise : successfulResponse(url)
    ));

    const { container, root } = await renderPage();
    try {
        const requestedUrls = api.get.mock.calls.map(([url]) => url);
        expect(requestedUrls).toEqual(expect.arrayContaining([
            "/integrations-v2/snapchat-v2/status",
            "/integrations-v2/snapchat-v2/report",
            "/integrations-v2/snapchat-v2/hourly",
            "/integrations-v2/snapchat-v2/campaigns",
        ]));
        expect(container.querySelector('[data-testid="snapchat-v2-spend-headline"]').textContent)
            .toBe("2,788.88 USD");
        expect(container.textContent).toContain("123.45 USD");
        expect(container.querySelector('[data-testid="campaign-table"]').textContent).toBe("campaigns-ready");
        expect(container.querySelector('[data-testid="snapchat-v2-status-loading"]')).not.toBeNull();
        expect(api.post).not.toHaveBeenCalled();

        await act(async () => {
            pendingStatus.resolve(await successfulResponse("/status"));
        });
    } finally {
        await cleanup(container, root);
    }
});


test("status failure is confined to the status card", async () => {
    api.get.mockImplementation((url) => (
        url.endsWith("/status")
            ? Promise.reject(new Error("status unavailable"))
            : successfulResponse(url)
    ));

    const { container, root } = await renderPage();
    try {
        expect(container.querySelector('[data-testid="snapchat-v2-status-error"]').textContent)
            .toContain("تعذر تحميل حالة المزامنة");
        expect(container.querySelector('[data-testid="snapchat-v2-spend-headline"]').textContent)
            .toBe("2,788.88 USD");
        expect(container.textContent).toContain("123.45 USD");
        expect(container.querySelector('[data-testid="campaign-table"]').textContent).toBe("campaigns-ready");
        expect(container.textContent).not.toContain("تعذر تحميل بعض بيانات Snapchat V2");
        expect(api.post).not.toHaveBeenCalled();
    } finally {
        await cleanup(container, root);
    }
});


test("one data read failure does not discard successful sibling reads", async () => {
    api.get.mockImplementation((url) => (
        url.endsWith("/hourly")
            ? Promise.reject(new Error("hourly unavailable"))
            : successfulResponse(url)
    ));

    const { container, root } = await renderPage();
    try {
        expect(container.querySelector('[data-testid="snapchat-v2-spend-headline"]').textContent)
            .toBe("2,788.88 USD");
        expect(container.querySelector('[data-testid="campaign-table"]').textContent).toBe("campaigns-ready");
        expect(container.textContent).toContain("تعذر تحميل بعض بيانات Snapchat V2");
        expect(api.post).not.toHaveBeenCalled();
    } finally {
        await cleanup(container, root);
    }
});


test("StrictMode rerender does not duplicate page-load reads or issue writes", async () => {
    const { container, root } = await renderPage({ strict: true });
    try {
        const count = (suffix) => api.get.mock.calls.filter(([url]) => url.endsWith(suffix)).length;
        expect(count("/status")).toBe(1);
        expect(count("/report")).toBe(1);
        expect(count("/hourly")).toBe(1);
        expect(count("/campaigns")).toBe(1);
        expect(count("/unified-readiness")).toBe(1);
        expect(api.post).not.toHaveBeenCalled();
    } finally {
        await cleanup(container, root);
    }
});


test("an older implicit range cannot overwrite a corrected account-timezone range", async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-08-29T01:00:00Z"));
    const oldReport = deferred();
    const oldHourly = deferred();
    const oldCampaigns = deferred();
    api.get.mockImplementation((url, config = {}) => {
        if (url.endsWith("/status")) {
            return Promise.resolve({
                data: {
                    selected_account: {
                        ad_account_id: "snap-account-1",
                        currency: "USD",
                        timezone: "Asia/Riyadh",
                    },
                },
            });
        }
        if (url.endsWith("/unified-readiness")) return successfulResponse(url);
        const requestedDate = config.params?.date_from || config.params?.report_date;
        if (requestedDate === "2026-08-28") {
            if (url.endsWith("/report")) return oldReport.promise;
            if (url.endsWith("/hourly")) return oldHourly.promise;
            if (url.endsWith("/campaigns")) return oldCampaigns.promise;
        }
        if (url.endsWith("/report")) return Promise.resolve({ data: reportPayload(3000) });
        if (url.endsWith("/hourly")) return Promise.resolve({ data: hourlyPayload(200) });
        if (url.endsWith("/campaigns")) return Promise.resolve({ data: campaignsPayload });
        throw new Error(`unexpected GET ${url}`);
    });

    const { container, root } = await renderPage();
    try {
        expect(container.querySelector('[data-testid="snapchat-v2-spend-headline"]').textContent)
            .toBe("3,000.00 USD");
        expect(container.textContent).toContain("200.00 USD");

        await act(async () => {
            oldReport.resolve({ data: reportPayload(1000) });
            oldHourly.resolve({ data: hourlyPayload(50) });
            oldCampaigns.resolve({ data: campaignsPayload });
            await Promise.resolve();
        });
        expect(container.querySelector('[data-testid="snapchat-v2-spend-headline"]').textContent)
            .toBe("3,000.00 USD");
        expect(container.textContent).toContain("200.00 USD");
        expect(container.textContent).not.toContain("50.00 USD");
    } finally {
        await cleanup(container, root);
        jest.useRealTimers();
    }
});
