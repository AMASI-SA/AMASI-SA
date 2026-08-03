from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:100]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


nav = Path("frontend/src/components/MezanV2NavigationShell.jsx")
text = nav.read_text(encoding="utf-8")
text = text.replace(
    '{ to: "/settings/ads-currencies", label: "العمولات وسعر الصرف", exactSearch: true },',
    '{ to: "/ads-manager/cost-settings", label: "العمولات وسعر الصرف", exactSearch: true },',
)
text = text.replace('    "/settings/ads-currencies",\n', "")
nav.write_text(text, encoding="utf-8")

replace_once(
    "frontend/src/components/Layout.jsx",
    '''    const isV2 = [
        "/dashboard-v2",
        "/orders-v2",
        "/fulfillment-v2",
        "/inventory-receiving-v2",
        "/products-v2",
        "/components-v2",
        "/integrations-v2",
        "/customer-intelligence",
        "/ads-manager",
    ].some((prefix) => location.pathname.startsWith(prefix));
    const isMezanV2 = isV2 && isMezanV2Route(location.pathname);
''',
    '''    const isMezanV2 = isMezanV2Route(location.pathname);
''',
)

app = Path("frontend/src/App.js")
text = app.read_text(encoding="utf-8")
text = text.replace(
    'import AdsCurrencySettings from "./pages/AdsCurrencySettings";\n',
    'import AdsCostSettingsV2 from "./pages/AdsCostSettingsV2";\n',
)
text = text.replace(
    '<Route path="/settings/ads-currencies" element={<ProtectedRoute><Layout><AdsCurrencySettings /></Layout></ProtectedRoute>} />',
    '<Route path="/settings/ads-currencies" element={<ProtectedRoute><Navigate to="/ads-manager/cost-settings" replace /></ProtectedRoute>} />',
)
marker = '''            <Route
                path="/ads-manager"
'''
route = '''            <Route
                path="/ads-manager/cost-settings"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><AdsCostSettingsV2 /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
'''
if route not in text:
    if marker not in text:
        raise SystemExit("ads-manager route marker not found")
    text = text.replace(marker, route + marker, 1)
app.write_text(text, encoding="utf-8")

legacy_page = Path("frontend/src/pages/AdsCurrencySettings.jsx")
if legacy_page.exists():
    legacy_page.unlink()

for test_path in (
    "frontend/src/components/MezanV2NavigationShell.adCosts.test.jsx",
    "frontend/src/components/MezanV2NavigationShell.test.jsx",
):
    file = Path(test_path)
    test = file.read_text(encoding="utf-8")
    test = test.replace("/settings/ads-currencies", "/ads-manager/cost-settings")
    file.write_text(test, encoding="utf-8")

Path("frontend/src/components/MezanV2AdCostRouteOwnership.test.jsx").write_text(
'''import fs from "fs";
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
''',
encoding="utf-8",
)

workflow = Path(".github/workflows/prod-mezan2-ad-cost-settings.yml")
wf = workflow.read_text(encoding="utf-8")
wf = wf.replace(
    '      - "frontend/src/pages/AdsCurrencySettings.jsx"\n',
    '      - "frontend/src/pages/AdsCurrencySettings.jsx"\n'
    '      - "frontend/src/App.js"\n'
    '      - "frontend/src/components/Layout.jsx"\n'
    '      - "frontend/src/components/MezanV2AdCostRouteOwnership.test.jsx"\n',
)
wf = wf.replace(
    '          src/components/MezanV2NavigationShell.adCosts.test.jsx\n',
    '          src/components/MezanV2NavigationShell.adCosts.test.jsx\n'
    '          src/components/MezanV2AdCostRouteOwnership.test.jsx\n'
    '          src/components/MezanV2NavigationShell.test.jsx\n',
)
old_guard = '''          LEGACY=frontend/src/pages/AdsCurrencySettings.jsx
          NAV=frontend/src/components/MezanV2NavigationShell.jsx

          grep -q 'mezan2-ad-cost-settings-page' "$PAGE"
          grep -q '/ads-manager/account-cost-settings' "$SERVICE"
          grep -q 'export { default } from "./AdsCostSettingsV2"' "$LEGACY"
          grep -q 'العمولات وسعر الصرف' "$NAV"
          grep -q '"/settings/ads-currencies"' "$NAV"
          ! grep -q '/counterparties' "$PAGE" "$SERVICE"
          ! grep -q '/ads-currency-settings' "$PAGE" "$SERVICE"
          ! grep -q 'AdsCurrencySettings' "$LEGACY"
'''
new_guard = '''          APP=frontend/src/App.js
          LAYOUT=frontend/src/components/Layout.jsx
          NAV=frontend/src/components/MezanV2NavigationShell.jsx

          grep -q 'mezan2-ad-cost-settings-page' "$PAGE"
          grep -q '/ads-manager/account-cost-settings' "$SERVICE"
          grep -q 'العمولات وسعر الصرف' "$NAV"
          grep -q '"/ads-manager/cost-settings"' "$NAV"
          grep -q 'path="/ads-manager/cost-settings"' "$APP"
          grep -q 'Navigate to="/ads-manager/cost-settings" replace' "$APP"
          grep -q 'const isMezanV2 = isMezanV2Route(location.pathname);' "$LAYOUT"
          test ! -e frontend/src/pages/AdsCurrencySettings.jsx
          ! grep -q '"/settings/ads-currencies"' "$NAV"
          ! grep -q 'AdsCurrencySettings' "$APP"
          ! grep -q 'const isV2 = \[' "$LAYOUT"
          ! grep -q '/counterparties' "$PAGE" "$SERVICE"
          ! grep -q '/ads-currency-settings' "$PAGE" "$SERVICE"
'''
if old_guard not in wf:
    raise SystemExit("existing frontend isolation guard not found")
workflow.write_text(wf.replace(old_guard, new_guard, 1), encoding="utf-8")

for temporary in (
    ".github/workflows/tmp-fix-mezan2-ad-cost-route-ownership.yml",
    ".github/workflows/tmp-fix-mezan2-ad-cost-route-pr.yml",
    ".github/mezan2-ad-cost-route-trigger.txt",
    "scripts/tmp_fix_mezan2_ad_cost_route.py",
):
    target = Path(temporary)
    if target.exists():
        target.unlink()
