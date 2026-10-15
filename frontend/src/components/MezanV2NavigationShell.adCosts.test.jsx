import fs from "fs";
import path from "path";

jest.mock("react-router-dom", () => ({
    Link: ({ children }) => children,
}));

import {
    activeNavigationSection,
    isMezanV2Route,
    isNavigationItemActive,
    MEZAN_V2_NAV_SECTIONS,
} from "./MezanV2NavigationShell";

const marketing = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "marketing");

test("marketing navigation owns bank commission and exchange rate settings", () => {
    const item = marketing.items.find(
        (row) => row.to === "/ads-manager/cost-settings",
    );

    expect(item).toEqual({
        to: "/ads-manager/cost-settings",
        label: "العمولات وسعر الصرف",
        exactSearch: true,
    });
    expect(isMezanV2Route("/ads-manager/cost-settings")).toBe(true);
    expect(isNavigationItemActive(
        { pathname: "/ads-manager/cost-settings", search: "" },
        item,
    )).toBe(true);
    expect(activeNavigationSection({
        pathname: "/ads-manager/cost-settings",
        search: "",
    })?.id).toBe("marketing");
});

test("legacy ad cost component is removed and only the Mezan 2 page remains", () => {
    const root = path.resolve(__dirname, "..");
    const legacyPath = path.join(root, "pages", "AdsCurrencySettings.jsx");
    const v2Page = fs.readFileSync(
        path.join(root, "pages", "AdsCostSettingsV2.jsx"),
        "utf8",
    );
    const v2Service = fs.readFileSync(
        path.join(root, "services", "adsAccountCostSettingsV2.js"),
        "utf8",
    );

    expect(fs.existsSync(legacyPath)).toBe(false);
    expect(v2Page).toContain("mezan2-ad-cost-settings-page");
    expect(v2Service).toContain("/ads-manager/account-cost-settings");
    expect(v2Page).not.toContain('api.get("/counterparties');
    expect(v2Page).not.toContain('api.put("/ads-currency-settings');
    expect(v2Service).not.toContain("/counterparties");
    expect(v2Service).not.toContain("/ads-currency-settings");
});
