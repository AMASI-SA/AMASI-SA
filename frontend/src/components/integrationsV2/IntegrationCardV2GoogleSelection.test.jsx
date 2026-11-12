import React, { act } from "react";
import { createRoot } from "react-dom/client";
import IntegrationCard from "./IntegrationCard";
import { getGoogleAccountSelection, saveGoogleAccountSelection } from "../../services/googleIntegrationsV2";

jest.mock("../../services/googleIntegrationsV2", () => ({
    getGoogleAccountSelection: jest.fn(), saveGoogleAccountSelection: jest.fn(),
}));

const integration = {
    provider: "google_ads", name: "Google Ads", name_ar: "إعلانات Google",
    connection_status: "connected", connection_provenance: "api_connection", accounts: [],
    health: {}, permissions: { current: [], missing: [] }, ai: { can: [], cannot: [] }, actions: {},
};

test.each([false, true])("actual IntegrationCard mounts the Google reporting selector in compact=%s without an eager read", async (compact) => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    getGoogleAccountSelection.mockResolvedValue({ accounts: [], expected_account_ids: [], selection_configured: false });
    const host = document.createElement("div"); document.body.appendChild(host); const root = createRoot(host);
    try {
        await act(async () => root.render(<IntegrationCard compactAdvertisingAccounts={compact} integration={integration}
            onTest={() => {}} onSettings={() => {}} />));
        expect(host.querySelector('[data-testid="google-account-selection-control"]')).not.toBeNull();
        expect(getGoogleAccountSelection).not.toHaveBeenCalled();
        await act(async () => host.querySelector('[data-testid="google-account-selection-toggle"]').click());
        expect(host.textContent).toContain("حسابات Google المعتمدة في التقارير");
        expect(getGoogleAccountSelection).toHaveBeenCalledTimes(1);
        expect(saveGoogleAccountSelection).not.toHaveBeenCalled();
    } finally {
        await act(async () => root.unmount()); host.remove(); delete global.IS_REACT_ACT_ENVIRONMENT;
    }
});
