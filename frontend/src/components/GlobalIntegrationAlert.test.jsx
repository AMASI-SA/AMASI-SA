import fs from "fs";
import path from "path";

import { integrationNeedsGlobalAlert } from "./globalIntegrationAlertState";


test("global alert includes stopped connected apps and ignores unconfigured ones", () => {
    expect(integrationNeedsGlobalAlert({
        connection_status: "error",
        connection_provenance: "legacy_integration",
        health: { status: "unhealthy" },
    })).toBe(true);

    expect(integrationNeedsGlobalAlert({
        connection_status: "not_connected",
        connection_provenance: "disconnected",
        health: { status: "unhealthy" },
    })).toBe(false);
});


test("Layout mounts the global integration alert above main content", () => {
    const source = fs.readFileSync(path.join(__dirname, "Layout.jsx"), "utf8");
    const alertPosition = source.indexOf("<GlobalIntegrationAlert />");
    const mainPosition = source.indexOf("<main");

    expect(alertPosition).toBeGreaterThan(-1);
    expect(alertPosition).toBeLessThan(mainPosition);
});
