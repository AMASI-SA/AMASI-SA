const STOPPED_CONNECTIONS = new Set(["needs_reauth", "expired", "error"]);
const BAD_HEALTH = new Set(["unhealthy", "error"]);
const ACTIVE_PROVENANCE = new Set(["api_connection", "legacy_integration"]);


export function integrationNeedsGlobalAlert(provider) {
    if (!ACTIVE_PROVENANCE.has(provider?.connection_provenance)) return false;
    return STOPPED_CONNECTIONS.has(provider?.connection_status)
        || BAD_HEALTH.has(provider?.health?.status);
}
