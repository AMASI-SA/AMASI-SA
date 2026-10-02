// Run once in the browser console on https://mezansalla.com after normal owner login.
// Same-origin GET only. No refresh, login, sync, posting, or mutation endpoint.
// Credentials remain in the browser; output contains no tokens or record contents.
(async () => {
  const report = { check: "MZ2_POSTDEPLOY_OWNER_READONLY", started_at: new Date().toISOString(), checks: [] };
  const expectedSource = "79f307f6ff69ba658958ebcdce03341b39a55939";
  const expectedRelease = "rg5-8f098823ae66cfa6c93a7d8effc3a87b0515796f8ef49eba4e4bb94e53696ff6";
  const demand = (condition, code) => { if (!condition) throw new Error(code); };
  try {
    demand(location.origin === "https://mezansalla.com", "STOP_WRONG_ORIGIN");
    const token = localStorage.getItem("access_token");
    const headers = { Accept: "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    const fixedPaths = new Set([
      "/api/health", "/api/auth/me", "/api/accounting-module/write-control",
      "/api/accounting-module/onboarding/definitions",
      "/api/accounting-module/financial-accounts", "/api/salla/status", "/api/orders-v2?limit=1",
    ]);
    let approvedDetailPath = null;
    async function get(label, path) {
      demand(fixedPaths.has(path) || path === approvedDetailPath, "STOP_PATH_NOT_ALLOWED");
      const response = await fetch(path, {
        method: "GET", headers, credentials: "include", cache: "no-store", redirect: "error",
        signal: AbortSignal.timeout(30000),
      });
      report.checks.push({ check: label, http_status: response.status });
      demand(response.ok, `STOP_${label}_HTTP_${response.status}`);
      demand((response.headers.get("content-type") || "").includes("application/json"), `STOP_${label}_NON_JSON`);
      return response.json();
    }
    const health = await get("release_identity", "/api/health");
    demand(health.ok === true && health.release?.source_git_sha === expectedSource &&
      health.release?.release_id === expectedRelease && health.release?.verified_identity_available === true,
    "STOP_RELEASE_MISMATCH");
    report.source_git_sha = expectedSource;
    report.release_id = expectedRelease;
    const actor = await get("authenticated_auth_mongo_read", "/api/auth/me");
    demand(actor.role === "owner" || actor.is_owner === true, "STOP_OWNER_SESSION_REQUIRED");
    const before = await get("write_control_before", "/api/accounting-module/write-control");
    demand(before.paused === true && before.can_manage === true, "STOP_WRITE_CONTROL_NOT_PAUSED_OR_NOT_OWNER");
    const definitions = await get("mz2_availability", "/api/accounting-module/onboarding/definitions");
    demand(Array.isArray(definitions.sections) && definitions.live_actions_enabled === false, "STOP_MZ2_DEFINITIONS_CONTRACT");
    const accounts = await get("native_accounting_read", "/api/accounting-module/financial-accounts");
    demand(Array.isArray(accounts.items), "STOP_ACCOUNTING_RESPONSE_SHAPE");
    report.accounting_read = { response_valid: true, empty: accounts.items.length === 0 };
    const salla = await get("salla_status_read", "/api/salla/status");
    demand(typeof salla.connected === "boolean", "STOP_SALLA_RESPONSE_SHAPE");
    report.salla_connected = salla.connected;
    const orders = await get("current_carrier_list_read", "/api/orders-v2?limit=1");
    demand(Array.isArray(orders.items), "STOP_ORDERS_RESPONSE_SHAPE");
    report.current_carrier = { result: "NOT_EXERCISED_NO_EXISTING_ORDER" };
    if (orders.items.length) {
      const first = orders.items[0];
      demand(typeof first.order_number === "string" && first.order_number.length > 0, "STOP_ORDER_IDENTITY_MISSING");
      approvedDetailPath = `/api/orders-v2/${encodeURIComponent(first.order_number)}`;
      const detail = await get("current_carrier_detail_read", approvedDetailPath);
      demand(detail.order_number === first.order_number, "STOP_DETAIL_IDENTITY_MISMATCH");
      const keys = ["company", "company_code", "shipment_id", "status", "tracking_number", "tracking_url", "label_url"];
      const consistent = keys.every(key => (first.shipping?.[key] ?? null) === (detail.shipping?.[key] ?? null));
      demand(consistent, "STOP_CURRENT_CARRIER_LIST_DETAIL_DIFFERENCE");
      report.current_carrier = {
        result: first.shipping?.company && first.shipping?.shipment_id ? "READ_PATH_PASS" : "READ_PATH_PASS_CARRIER_DATA_INCOMPLETE",
        list_detail_consistent: true,
        limitation: "Read-only deployed projection smoke; not a new shipping event or provider comparison.",
      };
    }
    const after = await get("write_control_after", "/api/accounting-module/write-control");
    demand(after.paused === true && before.revision === after.revision && before.changed_at === after.changed_at,
      "STOP_WRITE_CONTROL_CHANGED");
    report.write_control = { paused_before: true, paused_after: true, revision_unchanged: true };
    report.result = orders.items.length && salla.connected && report.current_carrier.result === "READ_PATH_PASS"
      ? "READONLY_SMOKE_PASS" : "READONLY_SMOKE_PARTIAL_DATA_LIMITATION";
  } catch (error) {
    report.result = "STOP_NO_RETRY";
    report.error = String(error?.message || "CHECK_FAILED").startsWith("STOP_")
      ? error.message : "REQUEST_OR_BROWSER_ERROR";
  }
  report.finished_at = new Date().toISOString();
  report.production_financial_writes_by_this_script = 0;
  report.mutations_by_this_script = 0;
  report.full_business_uat = "NOT_PASS";
  console.log(JSON.stringify(report, null, 2));
})();
