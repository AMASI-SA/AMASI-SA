import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleCategoryMembershipReview from "./GoogleCategoryMembershipReview";
import { getGoogleCategoryMembershipReview } from "../../services/googleCategoryMembershipReview";
jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));
const plan = { plan_id: "winter", revision: 3, fields: { category_ids: ["8"], category_url: null, pinned_product_ids: ["123", "456", "789"], excluded_product_ids: ["789"] } };
function row(product_id, proved = true) { return { product_id, salla_product_id: proved ? product_id : null, mezan_product_id: proved ? `mpv2_${product_id}` : null, membership: proved ? "observed_in_stored_source" : "unproved", matched_category_ids: proved ? ["8"] : [], observed_at: proved ? "2026-10-29T12:00:00+00:00" : null, source_ref: proved ? `category-observation:${"a".repeat(64)}` : null, category_url_status: "not_requested", category_url_matched_ids: [], catalog_visibility: "unknown", eligible_for_ad_selection: false, remote_category_coverage_complete: false, source_snapshot_immutable: false, platform_catalog_mapping_verified: false, execution_authorized: false }; }
function result() { return { plan_id: "winter", plan_revision: 3, observed_at: "2026-10-29T12:00:00+00:00", product_ids_reviewed: ["123", "456"], provider_writes: 0, execution_authorized: false, review: { scope: "pinned_products_current_stored_category_membership", category_ids_reviewed: ["8"], category_url: null, products: [row("123"), row("456", false)], remote_category_coverage_complete: false, platform_catalog_mapping_verified: false, source_writes: 0, provider_writes: 0, execution_authorized: false } }; }
let container, root;
beforeEach(() => { globalThis.IS_REACT_ACT_ENVIRONMENT = true; api.get.mockReset(); api.get.mockResolvedValue({ data: result() }); container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<GoogleCategoryMembershipReview plan={plan} isOwner {...props} />)); }
async function read() { await act(async () => container.querySelector("button").click()); }

test("explicit owner read shows positive stored membership, both IDs and optional URL without feed mapping claim", async () => {
    await render(); expect(api.get).not.toHaveBeenCalled(); await read();
    expect(api.get.mock.calls[0][0]).toBe("/integrations-v2/google_ads/commercial-plans/winter/category-review"); expect(api.get.mock.calls[0][1].params).toEqual({ expected_revision: 3 });
    expect(container.textContent).toContain("عضوية مرصودة في لقطة سلة المحفوظة: 8"); expect(container.textContent).toContain("mpv2_123");
    expect(container.textContent).toContain("رابط القسم اختياري ولم يرفق"); expect(container.textContent).toContain("كتالوجات جوجل وسناب وتيك توك وميتا غير مثبتة");
    expect(container.textContent).toContain("تعذر إثبات العضوية"); expect(container.textContent).not.toContain("789");
});
test.each([false, undefined])("nonowner is hidden %s", async (isOwner) => { await render({ isOwner }); expect(container.textContent).toBe(""); expect(api.get).not.toHaveBeenCalled(); });
test("dirty saved plan cannot read", async () => { await render({ planDirty: true }); expect(container.querySelector("button")).toBe(null); });
test.each(["revision", "fields", "dirty", "owner"])("scope %s aborts and discards late positive proof", async (change) => {
    let finish; api.get.mockImplementation(() => new Promise((resolve) => { finish = resolve; })); await render(); await read(); const signal = api.get.mock.calls[0][1].signal;
    await render(change === "revision" ? { plan: { ...plan, revision: 4 } } : change === "fields" ? { plan: { ...plan, fields: { ...plan.fields, category_ids: ["9"] } } } : change === "dirty" ? { planDirty: true } : { isOwner: false });
    expect(signal.aborted).toBe(true); await act(async () => finish({ data: result() })); expect(container.textContent).not.toContain("mpv2_123");
});
test("explicit non-observation is not remote nonmembership; hidden positives remain excluded", async () => {
    const data = result(); data.review.products[0].membership = "not_observed_in_stored_source"; data.review.products[0].matched_category_ids = []; data.review.products[0].catalog_visibility = "hidden";
    api.get.mockResolvedValue({ data }); await render(); await read(); expect(container.textContent).toContain("لا يثبت ذلك عدم العضوية"); expect(container.textContent).toContain("منتج مخفي");
});
test("supplied URL only matches explicit source field; no destination verification", async () => {
    const current = { ...plan, fields: { ...plan.fields, category_url: "https://store.example/c/winter" } }, data = result(); data.review.category_url = current.fields.category_url;
    data.review.products[0].category_url_status = "explicit_url_observed"; data.review.products[0].category_url_matched_ids = ["8"]; data.review.products[1].category_url_status = "not_verified";
    api.get.mockResolvedValue({ data }); await render({ plan: current }); await read(); expect(container.textContent).toContain("مطابق لحقل صريح"); expect(container.textContent).toContain("لم تُختبر الوجهة الحية");
});
test.each(["foreigncategory", "wrongproduct", "duplicate", "feed", "coverage", "future", "stale", "inventedurl", "wrongmezan", "authority"])("invalid evidence %s fails closed", async (kind) => {
    const data = result(), first = data.review.products[0];
    if (kind === "foreigncategory") first.matched_category_ids = ["9"];
    if (kind === "wrongproduct") first.product_id = "999";
    if (kind === "duplicate") data.review.products[1] = { ...first };
    if (kind === "feed") first.platform_catalog_mapping_verified = true;
    if (kind === "coverage") data.review.remote_category_coverage_complete = true;
    if (kind === "future") first.observed_at = "2026-10-30T12:00:00Z";
    if (kind === "stale") first.observed_at = "2026-10-27T12:00:00Z";
    if (kind === "inventedurl") first.category_url_status = "explicit_url_observed";
    if (kind === "wrongmezan") first.mezan_product_id = "mpv2_999";
    if (kind === "authority") first.eligible_for_ad_selection = true;
    api.get.mockResolvedValue({ data }); await expect(getGoogleCategoryMembershipReview(plan)).rejects.toMatchObject({ code: "GOOGLE_CATEGORY_REVIEW_UNPROVED" });
});
test("409 locks until saved scope changes", async () => { api.get.mockRejectedValue({ response: { status: 409 } }); await render(); await read(); expect(container.querySelector("button").disabled).toBe(true); await render({ plan: { ...plan, revision: 4 } }); expect(container.querySelector("button").disabled).toBe(false); });
