import {
    executeIntegrationReconnect,
    integrationReconnectEnabled,
} from "./integrationReconnect";

const metaIntegration = {
    provider: "meta_ads",
    actions: {
        reconnect: {
            enabled: true,
            href: null,
        },
    },
};

describe("integration reconnect command", () => {
    test("Meta reconnect stays enabled without an internal href and starts trusted OAuth", async () => {
        const startMetaConnection = jest.fn().mockResolvedValue({
            authorization_url: "https://www.facebook.com/v25.0/dialog/oauth?state=signed",
        });
        const assignLocation = jest.fn();
        const navigate = jest.fn();

        expect(integrationReconnectEnabled(metaIntegration)).toBe(true);
        await expect(executeIntegrationReconnect({
            integration: metaIntegration,
            startMetaConnection,
            assignLocation,
            navigate,
        })).resolves.toEqual({
            provider: "meta_ads",
            mode: "external_oauth",
        });

        expect(startMetaConnection).toHaveBeenCalledTimes(1);
        expect(assignLocation).toHaveBeenCalledWith(
            "https://www.facebook.com/v25.0/dialog/oauth?state=signed",
        );
        expect(navigate).not.toHaveBeenCalled();
    });

    test("non-Meta reconnect continues to use its safe internal target", async () => {
        const navigate = jest.fn();
        await expect(executeIntegrationReconnect({
            integration: {
                provider: "google_ads",
                actions: {
                    reconnect: {
                        enabled: true,
                        href: "/integrations-v2/google-ads",
                    },
                },
            },
            navigate,
        })).resolves.toEqual({
            provider: "google_ads",
            mode: "internal_route",
        });
        expect(navigate).toHaveBeenCalledWith("/integrations-v2/google-ads");
    });

    test("fails closed when reconnect is disabled or has no executable target", async () => {
        await expect(executeIntegrationReconnect({
            integration: {
                provider: "meta_ads",
                actions: { reconnect: { enabled: false, href: null } },
            },
        })).rejects.toThrow("integration_reconnect_disabled");

        await expect(executeIntegrationReconnect({
            integration: {
                provider: "google_ads",
                actions: { reconnect: { enabled: true, href: null } },
            },
            navigate: jest.fn(),
        })).rejects.toThrow("integration_reconnect_target_missing");
    });
});
