const fs = require("fs");
const path = require("path");

function read(relativePath) {
    return fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
}

test("root and authenticated public redirects use the environment-aware dashboard route", () => {
    const app = read("src/App.js");
    expect(app).toContain('if (user) return <Navigate to={getDefaultDashboardPath()} replace />;');
    expect(app).toContain('<Route path="/" element={<ProtectedRoute><Navigate to={getDefaultDashboardPath()} replace /></ProtectedRoute>} />');
    expect(app).toContain('<Route path="*" element={<Navigate to={getDefaultDashboardPath()} replace />} />');
    expect(app).not.toContain('<Route path="/" element={<ProtectedRoute><Layout><Dashboard /></Layout></ProtectedRoute>} />');
});

test("legacy dashboard remains reachable only through its explicit legacy route", () => {
    const app = read("src/App.js");
    const sidebar = read("src/components/Sidebar.jsx");
    expect(app).toContain('<Route path="/legacy-dashboard" element={<ProtectedRoute><Layout><Dashboard /></Layout></ProtectedRoute>} />');
    expect(sidebar).toContain('{ to: "/legacy-dashboard", label: "لوحة التحكم القديمة", icon: House, testid: "nav-dashboard" }');
    expect(sidebar).not.toContain('{ to: "/", label: "لوحة التحكم", icon: House, testid: "nav-dashboard" }');
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
