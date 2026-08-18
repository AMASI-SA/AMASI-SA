export function integrationReconnectEnabled(integration) {
    return integration?.actions?.reconnect?.enabled === true;
}

export async function executeIntegrationReconnect({
    integration,
    startMetaConnection,
    navigate,
    assignLocation,
}) {
    if (!integrationReconnectEnabled(integration)) {
        throw new Error("integration_reconnect_disabled");
    }

    if (integration?.provider === "meta_ads") {
        if (typeof startMetaConnection !== "function" || typeof assignLocation !== "function") {
            throw new Error("meta_reconnect_handler_missing");
        }
        const result = await startMetaConnection();
        const authorizationUrl = String(result?.authorization_url || "").trim();
        if (!authorizationUrl) {
            throw new Error("meta_authorization_url_missing");
        }
        assignLocation(authorizationUrl);
        return { provider: "meta_ads", mode: "external_oauth" };
    }

    const target = String(integration?.actions?.reconnect?.href || "").trim();
    if (!target || typeof navigate !== "function") {
        throw new Error("integration_reconnect_target_missing");
    }
    navigate(target);
    return { provider: integration.provider, mode: "internal_route" };
}
