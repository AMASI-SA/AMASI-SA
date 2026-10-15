const fs = require("fs");
const path = require("path");

function read(relativePath) {
    return fs.readFileSync(path.join(__dirname, relativePath), "utf8");
}

test("Production integration card hosts the bounded TikTok reporting control", () => {
    const source = read("IntegrationCardV2.jsx");

    expect(source).toContain(
        'import TikTokReportingSyncControl from "./TikTokReportingSyncControl";',
    );
    expect(source).toContain(
        'const showTikTokReporting = integration.provider === "tiktok_ads";',
    );
    expect(source).toContain('data-testid="tiktok-reporting-control-host"');
    expect(source).toContain(
        '<TikTokReportingSyncControl integration={integration} />',
    );
    expect(source).toContain(
        'const showMetaReporting = integration.provider === "meta_ads";',
    );
    expect(source).toContain('data-testid="meta-reporting-control-host"');
});
