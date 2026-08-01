const fs = require("fs");
const path = require("path");

function read(relativePath) {
    return fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
}

test("root and authenticated public redirects land on Mezan 2 dashboard", () => {
    const app = read("src/App.js");
    expect(app).toContain('if (user) return <Navigate to="/dashboard-v2" replace />;');
    expect(app).toContain('<Route path="/" element={<ProtectedRoute><Navigate to="/dashboard-v2" replace /></ProtectedRoute>} />');
    expect(app).toContain('<Route path="*" element={<Navigate to="/dashboard-v2" replace />} />');
    expect(app).not.toContain('<Route path="/" element={<ProtectedRoute><Layout><Dashboard /></Layout></ProtectedRoute>} />');
});

test("legacy dashboard remains reachable only through its explicit legacy route", () => {
    const app = read("src/App.js");
    const sidebar = read("src/components/Sidebar.jsx");
    expect(app).toContain('<Route path="/legacy-dashboard" element={<ProtectedRoute><Layout><Dashboard /></Layout></ProtectedRoute>} />');
    expect(sidebar).toContain('{ to: "/legacy-dashboard", label: "لوحة التحكم القديمة", icon: House, testid: "nav-dashboard" }');
    expect(sidebar).not.toContain('{ to: "/", label: "لوحة التحكم", icon: House, testid: "nav-dashboard" }');
});
