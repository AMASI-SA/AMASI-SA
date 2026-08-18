const fs = require("fs");
const path = require("path");

function read(relativePath) {
    return fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
}

test("root and authenticated public redirects land on the advanced dashboard", () => {
    const app = read("src/App.js");
    expect(app).toContain('if (user) return <Navigate to="/dashboard-advanced" replace />;');
    expect(app).toContain('<Route path="/" element={<ProtectedRoute><Navigate to="/dashboard-advanced" replace /></ProtectedRoute>} />');
    expect(app).toContain('<Route path="*" element={<Navigate to="/dashboard-advanced" replace />} />');
    expect(app).not.toContain('<Route path="/" element={<ProtectedRoute><Layout><Dashboard /></Layout></ProtectedRoute>} />');
});

test("both retired dashboard routes converge on the advanced dashboard without old runtime", () => {
    const app = read("src/App.js");
    const retired = read("src/pages/Dashboard.jsx");

    expect(app).toContain('path="/dashboard-v2"');
    expect(app).toContain('<Layout><Dashboard sourceMode="mezan_v2" /></Layout>');
    expect(app).toContain('<Route path="/legacy-dashboard" element={<ProtectedRoute><Layout><Dashboard /></Layout></ProtectedRoute>} />');
    expect(retired).toContain('return <Navigate to="/dashboard-advanced" replace />;');
    expect(retired).toContain('import "./retiredDashboard.css";');
    expect(retired).not.toContain("AdvancedDashboard");

    [
        "api.get(",
        "api.post(",
        "setInterval(",
        "setTimeout(",
        "useEffect(",
        "useState(",
        "MutationObserver",
        "requestAnimationFrame",
    ].forEach((forbidden) => expect(retired).not.toContain(forbidden));
});

test("obsolete dashboard controls are removed from the visible navigation", () => {
    const css = read("src/pages/retiredDashboard.css");
    expect(css).toContain('[data-testid="advanced-dashboard-page"] > header a[href="/dashboard-v2"]');
    expect(css).toContain('[data-testid="nav-dashboard"]');
    expect(css).toContain("display: none !important");
});

test("advanced dashboard remains the only dashboard UI while keeping its governed data API", () => {
    const advanced = read("src/pages/AdvancedDashboard.jsx");
    expect(advanced).toContain('data-testid="advanced-dashboard-page"');
    expect(advanced).toContain("لوحة التحكم المتقدمة");
    expect(advanced).toContain('api.get(`/dashboard-v2?${query.toString()}`');
});

test("Mezan 2 supplier accounts use backend permissions instead of an owner-only page gate", () => {
    const app = read("src/App.js");
    const supplierStart = app.indexOf('path="/suppliers-v2"');
    const integrationStart = app.indexOf('path="/integrations-v2"', supplierStart);
    const supplierRoute = app.slice(supplierStart, integrationStart);

    expect(supplierStart).toBeGreaterThan(-1);
    expect(supplierRoute).toContain("<Layout><MezanSuppliersV2 /></Layout>");
    expect(supplierRoute).not.toContain("<OwnerOnlyRoute>");
});
