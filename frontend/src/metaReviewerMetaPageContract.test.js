import fs from "fs";
import path from "path";

const read = (relative) => fs.readFileSync(path.join(__dirname, relative), "utf8");

test("keeps the Meta reviewer on the established Meta integrations URL", () => {
    const protectedRoute = read("components/ProtectedRoute.jsx");
    const ownerRoute = read("components/OwnerOnlyRoute.jsx");
    const app = read("App.js");

    expect(protectedRoute).toContain('<Navigate to="/integrations-v2?provider=meta_ads" replace />');
    expect(ownerRoute).toContain('"/integrations-v2"');
    expect(app).toContain('path="/integrations-v2"');
    expect(app).toContain("<AppsIntegrationsControlCenter />");
});

test("keeps campaign writes owner-only while reviewer readiness stays readable", () => {
    const backend = read("../../backend/integrations_control_center/__init__.py");
    const actions = read("components/integrationsV2/MetaEntityActions.jsx");

    expect(backend).toContain("attach_meta_campaign_management_routes(");
    expect(backend).toContain("router, db, current_user, _require_owner");
    expect(actions).toContain("حساب المراجع: عرض فقط دون تنفيذ");
});
