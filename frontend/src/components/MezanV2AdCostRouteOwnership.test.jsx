import fs from "fs";
import path from "path";

jest.mock("react-router-dom", () => ({
    Link: ({ children }) => children,
}));

import {
    activeNavigationSection,
    isMezanV2Route,
    MEZAN_V2_NAV_SECTIONS,
} from "./MezanV2NavigationShell";

const NATIVE_PATH = "/ads-manager/cost-settings";
const LEGACY_PATH = "/settings/ads-currencies";

test("ad cost settings is owned only by the Mezan 2 marketing route", () => {
    const marketing = MEZAN_V2_NAV_SECTIONS.find(
        (section) => section.id === "marketing",
    );
    expect(marketing.items.some((item) => item.to === NATIVE_PATH)).toBe(true);
    expect(marketing.items.some((item) => item.to === LEGACY_PATH)).toBe(false);
    expect(isMezanV2Route(NATIVE_PATH)).toBe(true);
    expect(isMezanV2Route(LEGACY_PATH)).toBe(false);
    expect(activeNavigationSection({ pathname: NATIVE_PATH, search: "" })?.id)
        .toBe("marketing");
});

test("App redirects the legacy URL and Layout uses the central V2 registry", () => {
    const src = path.resolve(__dirname, "..");
    const app = fs.readFileSync(path.join(src, "App.js"), "utf8");
    const layout = fs.readFileSync(
        path.join(src, "components", "Layout.jsx"),
        "utf8",
    );

    expect(app).toContain('path="/ads-manager/cost-settings"');
    expect(app).toContain("<Layout><AdsCostSettingsV2 /></Layout>");
    expect(app).toContain('path="/settings/ads-currencies"');
    expect(app).toContain(
        '<Navigate to="/ads-manager/cost-settings" replace />',
    );
    expect(app).not.toContain("AdsCurrencySettings");
    expect(layout).toContain(
        "const isMezanV2 = isMezanV2Route(location.pathname);",
    );
    expect(layout).not.toContain("const isV2 = [");
    expect(fs.existsSync(path.join(src, "pages", "AdsCurrencySettings.jsx")))
        .toBe(false);
});
