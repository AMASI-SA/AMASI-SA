from pathlib import Path


APP = Path("frontend/src/App.js")
SIDEBAR = Path("frontend/src/components/Sidebar.jsx")
LAYOUT = Path("frontend/src/components/Layout.jsx")


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return source.replace(old, new, 1)


def patch_app() -> None:
    source = APP.read_text(encoding="utf-8")
    source = replace_once(
        source,
        'if (user) return <Navigate to="/" replace />;',
        'if (user) return <Navigate to="/dashboard-v2" replace />;',
        "authenticated public redirect",
    )
    source = replace_once(
        source,
        '<Route path="/" element={<ProtectedRoute><Layout><Dashboard /></Layout></ProtectedRoute>} />',
        '<Route path="/" element={<ProtectedRoute><Navigate to="/dashboard-v2" replace /></ProtectedRoute>} />\n            <Route path="/legacy-dashboard" element={<ProtectedRoute><Layout><Dashboard /></Layout></ProtectedRoute>} />',
        "root dashboard route",
    )
    source = replace_once(
        source,
        '<Route path="*" element={<Navigate to="/" replace />} />',
        '<Route path="*" element={<Navigate to="/dashboard-v2" replace />} />',
        "fallback route",
    )
    APP.write_text(source, encoding="utf-8")


def patch_sidebar() -> None:
    source = SIDEBAR.read_text(encoding="utf-8")
    source = replace_once(
        source,
        '{ to: "/", label: "لوحة التحكم", icon: House, testid: "nav-dashboard" },',
        '{ to: "/legacy-dashboard", label: "لوحة التحكم القديمة", icon: House, testid: "nav-dashboard" },',
        "legacy dashboard sidebar entry",
    )
    SIDEBAR.write_text(source, encoding="utf-8")


def preserve_production_dashboard_placements() -> None:
    source = LAYOUT.read_text(encoding="utf-8")
    source = replace_once(
        source,
        'import OrderUiEnhancements from "./OrderUiEnhancements";\n',
        'import OrderUiEnhancements from "./OrderUiEnhancements";\nimport DashboardAnalyticsPlacement from "./DashboardAnalyticsPlacement";\nimport DashboardSnapchatAccountsPlacement from "./DashboardSnapchatAccountsPlacement";\n',
        "production dashboard placement imports",
    )
    source = replace_once(
        source,
        '    const isMarketingPlatform = location.pathname === "/ads-manager"\n        && isMarketingPlatformProvider(marketingProvider);\n',
        '    const isMarketingPlatform = location.pathname === "/ads-manager"\n        && isMarketingPlatformProvider(marketingProvider);\n    const isLegacyDashboard = location.pathname === "/legacy-dashboard";\n    const isMezanV2Dashboard = location.pathname === "/dashboard-v2";\n    const showsDashboardAnalytics = isLegacyDashboard || isMezanV2Dashboard;\n',
        "production dashboard placement state",
    )
    source = replace_once(
        source,
        '                    {pageContent}\n                </div>\n',
        '                    {pageContent}\n                    <DashboardAnalyticsPlacement active={showsDashboardAnalytics} />\n                    <DashboardSnapchatAccountsPlacement active={showsDashboardAnalytics} />\n                </div>\n',
        "production dashboard placement render",
    )
    LAYOUT.write_text(source, encoding="utf-8")


def verify_contract() -> None:
    app = APP.read_text(encoding="utf-8")
    sidebar = SIDEBAR.read_text(encoding="utf-8")
    layout = LAYOUT.read_text(encoding="utf-8")

    required_app = (
        'if (user) return <Navigate to="/dashboard-v2" replace />;',
        '<Route path="/" element={<ProtectedRoute><Navigate to="/dashboard-v2" replace /></ProtectedRoute>} />',
        '<Route path="/legacy-dashboard" element={<ProtectedRoute><Layout><Dashboard /></Layout></ProtectedRoute>} />',
        '<Route path="*" element={<Navigate to="/dashboard-v2" replace />} />',
    )
    required_layout = (
        'import MezanV2NavigationShell, {',
        'import MarketingPlatformWorkspace, {',
        'import GoogleAdsAllPlatformsCard from "./GoogleAdsAllPlatformsCard";',
        'import DashboardAnalyticsPlacement from "./DashboardAnalyticsPlacement";',
        'import DashboardSnapchatAccountsPlacement from "./DashboardSnapchatAccountsPlacement";',
        '<MezanV2NavigationShell',
        '<DashboardAnalyticsPlacement active={showsDashboardAnalytics} />',
        '<DashboardSnapchatAccountsPlacement active={showsDashboardAnalytics} />',
        'className="min-h-screen"',
    )

    missing = [marker for marker in required_app if marker not in app]
    missing += [marker for marker in required_layout if marker not in layout]
    if missing:
        raise SystemExit(f"Production Mezan 2 shell contract missing: {missing}")

    if '{ to: "/legacy-dashboard", label: "لوحة التحكم القديمة", icon: House, testid: "nav-dashboard" }' not in sidebar:
        raise SystemExit("Legacy dashboard sidebar contract missing")
    if 'className="min-h-screen lg:ps-64"' in layout:
        raise SystemExit("Permanent legacy sidebar offset remains")
    if 'function MezanV2Navigation(' in layout:
        raise SystemExit("Legacy purple V2 navigation remains")

    print("PROD_MEZAN2_PREVIEW_SHELL_PATCHED_AND_VERIFIED")


def main() -> None:
    patch_app()
    patch_sidebar()
    preserve_production_dashboard_placements()
    verify_contract()


if __name__ == "__main__":
    main()
