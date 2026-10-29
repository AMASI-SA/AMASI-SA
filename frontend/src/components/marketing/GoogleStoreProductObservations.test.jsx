import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleStoreProductObservations from "./GoogleStoreProductObservations";
import { getGoogleStoreProductObservations } from "../../services/googleStoreProductObservations";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));
const plan = { plan_id: "winter", revision: 1, fields: { pinned_product_ids: ["123", "456"], excluded_product_ids: ["456"], instruction: "الشتاء" } };
const periods = { last_14_days: ["2026-10-15", "2026-10-28"], previous_14_days: ["2026-10-01", "2026-10-14"], last_30_days: ["2026-09-29", "2026-10-28"], last_90_days: ["2026-07-31", "2026-10-28"] };
function result() {
    return { plan_id: "winter", plan_revision: 1, product_ids_reviewed: ["123"], catalog_missing_or_unverified_ids: [], evidence: null, execution_authorized: false, provider_writes: 0,
        observations: { mode: "stored_product_line_observations", metric_basis: "lower_bound_on_qualifying_saved_gross_line_observations_not_sales", as_of: "2026-10-29T12:00:00+00:00",
            windows: Object.fromEntries(Object.entries(periods).map(([key, [from, to]]) => [key, { from, to, timezone: "Asia/Riyadh" }])),
            coverage_complete: false, refunds_reconciled: false, snapshot_consistent: false, source_snapshot_immutable: false, platform_conversions_added: false,
            execution_authorized: false, provider_writes: 0, source_writes: 0, truncated: false,
            products: [{ product_id: "123", sales_native: null, profitability: null, growth_14_days_percent: null, success_classification: "not_established",
                currency_groups: [{ currency_code: "SAR", account_scope: "owner_store_projection", platform_account_id: null,
                    windows: Object.fromEntries(Object.keys(periods).map((key) => [key, key === "previous_14_days" ? { observed_orders: null, observed_lines: null, observed_units: null } : { observed_orders: 1, observed_lines: 1, observed_units: "2" }])) }] }] } };
}
let container, root;
beforeEach(() => { globalThis.IS_REACT_ACT_ENVIRONMENT = true; api.get.mockReset(); api.get.mockResolvedValue({ data: result() }); container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<GoogleStoreProductObservations plan={plan} isOwner {...props} />)); }
async function read() { await act(async () => container.querySelector("button").click()); }

test("explicit read shows bounded useful units, native currency and no growth or profit claim", async () => {
    await render(); expect(api.get).not.toHaveBeenCalled(); await read();
    expect(api.get.mock.calls[0][0]).toBe("/integrations-v2/google_ads/commercial-plans/winter/store-observations");
    expect(api.get.mock.calls[0][1].params).toEqual({ expected_revision: 1 });
    expect(container.textContent).toContain("كميات خام مرصودة"); expect(container.textContent).toContain("SAR");
    expect(container.textContent).toContain("ليس إجمالي مبيعات المتجر"); expect(container.textContent).toContain("النوافذ متداخلة ولا تُجمع");
    expect(container.textContent).toContain("حساب المنصة الأصلي غير مثبت"); expect(container.textContent).toContain("غير مرصود");
    expect(container.querySelectorAll("tbody td")[3].textContent).toBe("2");
});
test.each([false, undefined])("non-owner cannot request %s", async (isOwner) => { await render({ isOwner }); expect(api.get).not.toHaveBeenCalled(); expect(container.textContent).toBe(""); });
test("dirty plan has no read action", async () => { await render({ planDirty: true }); expect(container.querySelector("button")).toBe(null); });
test.each(["revision", "fields", "dirty", "owner"])("scope change %s aborts and rejects late result", async (change) => {
    let finish; api.get.mockImplementation(() => new Promise((resolve) => { finish = resolve; })); await render(); await read(); const signal = api.get.mock.calls[0][1].signal;
    const props = change === "revision" ? { plan: { ...plan, revision: 2 } } : change === "fields" ? { plan: { ...plan, fields: { ...plan.fields, instruction: "الصيف" } } } : change === "dirty" ? { planDirty: true } : { isOwner: false };
    await render(props); expect(signal.aborted).toBe(true); await act(async () => finish({ data: result() })); expect(container.textContent).not.toContain("عملة الطلب: SAR");
});
test("empty evidence is not zero sales", async () => { const data = result(); data.observations.products[0].currency_groups = []; api.get.mockResolvedValue({ data }); await render(); await read(); expect(container.textContent).toContain("لا يعني ذلك عدم وجود مبيعات"); });
test("truncated response cannot expose partial counts", async () => { const data = result(); data.observations.truncated = true; api.get.mockResolvedValue({ data }); await expect(getGoogleStoreProductObservations(plan)).rejects.toMatchObject({ code: "GOOGLE_STORE_OBSERVATIONS_UNPROVED" }); });
test.each(["coverage", "profit", "product", "currency", "dates", "account", "conversion", "zero"])("invalid response %s fails closed", async (kind) => {
    const data = result(), out = data.observations, product = out.products[0], group = product.currency_groups[0];
    if (kind === "coverage") out.coverage_complete = true;
    if (kind === "profit") product.profitability = "3";
    if (kind === "product") product.product_id = "foreign";
    if (kind === "currency") product.currency_groups.push({ ...group });
    if (kind === "dates") out.windows.last_14_days.to = "2026-10-29";
    if (kind === "account") group.platform_account_id = "invented";
    if (kind === "conversion") out.platform_conversions_added = true;
    if (kind === "zero") group.windows.last_14_days.observed_orders = 0;
    api.get.mockResolvedValue({ data }); await expect(getGoogleStoreProductObservations(plan)).rejects.toMatchObject({ code: "GOOGLE_STORE_OBSERVATIONS_UNPROVED" });
});
test("foreign response plan is not displayed", async () => { const data = result(); data.plan_revision = 2; api.get.mockResolvedValue({ data }); await render(); await read(); expect(container.querySelector('[role="alert"]')).not.toBe(null); expect(container.textContent).not.toContain("عملة الطلب: SAR"); });
