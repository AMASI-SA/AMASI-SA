import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GooglePinnedCatalogReview from "./GooglePinnedCatalogReview";
import { getGoogleCatalogReview } from "../../services/googleCatalogReview";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));
const plan = { plan_id: "seasonal-focus", revision: 3, fields: { pinned_product_ids: ["123", "456", "789"], excluded_product_ids: ["789"], instruction: "الشتاء" } };
function result(changes = {}) {
    return { plan_id: plan.plan_id, plan_revision: 3, pinned_product_ids_reviewed: ["123", "456"], source_verification: "catalog_row_only", provider_writes: 0, execution_authorized: false,
        catalog: { products: [{ product_id: "123", status: "unknown", verification: "verified" }], missing_or_unverified_product_ids: ["456"],
            category_membership_verified: false, variant_availability_verified: false, source_writes: 0, execution_authorized: false }, ...changes };
}
let container, root;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true; api.get.mockReset(); api.get.mockResolvedValue({ data: result() });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<GooglePinnedCatalogReview plan={plan} isOwner {...props} />)); }
async function read() { await act(async () => container.querySelector("button").click()); }

test("render is read-free; explicit review requests exact saved revision without excluded products", async () => {
    await render(); expect(api.get).not.toHaveBeenCalled(); await read();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get.mock.calls[0][0]).toBe("/integrations-v2/google_ads/commercial-plans/seasonal-focus/catalog-review");
    expect(api.get.mock.calls[0][1].params).toEqual({ expected_revision: 3 });
    expect(container.textContent).toContain("التوفر القابل للشراء غير مثبت");
    expect(container.textContent).toContain("منتجات مفقودة أو بياناتها غير مثبتة");
    expect(container.textContent).toContain("456"); expect(container.textContent).not.toContain("789");
    expect(container.textContent).toContain("لا تُشغّل هذه المراجعة حملات ولا تحفظ تعديلًا");
});

test.each([false, undefined])("non-owner cannot load review %s", async (isOwner) => {
    await render({ isOwner }); expect(container.textContent).toBe(""); expect(api.get).not.toHaveBeenCalled();
});

test("unsaved plan and revision zero are locked without requesting catalog", async () => {
    await render({ planDirty: true }); expect(container.querySelector("button").disabled).toBe(true); expect(api.get).not.toHaveBeenCalled();
    await render({ plan: { ...plan, revision: 0 } }); expect(container.querySelector("button")).toBe(null); expect(api.get).not.toHaveBeenCalled();
});

test("hidden and out-of-stock rows are exclusions, never advertised as ready", async () => {
    const response = result(); response.catalog.products = [{ product_id: "123", verification: "verified", status: "hidden" }, { product_id: "456", verification: "verified", status: "out_of_stock" }]; response.catalog.missing_or_unverified_product_ids = [];
    api.get.mockResolvedValue({ data: response }); await render(); await read();
    expect(container.textContent).toContain("مخفي في الكتالوج"); expect(container.textContent).toContain("نفاد مخزون مسجل");
    expect(container.textContent).toContain("لا يُرشّح للإعلان"); expect(container.textContent).not.toContain("التوفر القابل للشراء غير مثبت");
});

test("unverified and nominal available rows do not become inventory proof", async () => {
    const response = result(); response.catalog.products = [{ product_id: "123", verification: "unverified", status: "unknown" }, { product_id: "456", verification: "verified", status: "available" }]; response.catalog.missing_or_unverified_product_ids = [];
    api.get.mockResolvedValue({ data: response }); await render(); await read();
    expect(container.textContent).toContain("صف الكتالوج غير مثبت"); expect(container.textContent).toContain("التوفر القابل للشراء غير مثبت");
});

test("409 locks further reads until a new saved plan is supplied", async () => {
    api.get.mockRejectedValue({ response: { status: 409 } }); await render(); await read();
    expect(container.querySelector("button").disabled).toBe(true); await read(); expect(api.get).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("أعد تحميل الخطة المحفوظة");
    await render({ plan: { ...plan, revision: 4 } }); expect(container.querySelector("button").disabled).toBe(false);
});

test("plan revision change aborts a late catalog result", async () => {
    let finish; api.get.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await read(); const signal = api.get.mock.calls[0][1].signal;
    await render({ plan: { ...plan, revision: 4 } }); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: result() }));
    expect(container.textContent).toContain("نسخة الخطة: 4"); expect(container.textContent).not.toContain("المنتج 123");
});

test("same-revision saved-field change also discards a late response", async () => {
    let finish; api.get.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await read(); const signal = api.get.mock.calls[0][1].signal;
    await render({ plan: { ...plan, fields: { ...plan.fields, instruction: "الصيف" } } }); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: result() })); expect(container.textContent).not.toContain("المنتج 123");
});

test("editing a plan aborts and removes an existing read", async () => {
    let finish; api.get.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await read(); const signal = api.get.mock.calls[0][1].signal;
    await render({ planDirty: true }); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: result() })); expect(container.textContent).not.toContain("المنتج 123");
    expect(container.querySelector("button").disabled).toBe(true);
});

test.each([
    { plan_revision: 2 }, { plan_id: "foreign-plan" }, { pinned_product_ids_reviewed: ["123", "456", "789"] },
])("mismatched response identity is not displayed: %j", async (changes) => {
    api.get.mockResolvedValue({ data: result(changes) }); await render(); await read();
    expect(container.querySelector("button").disabled).toBe(true); expect(container.textContent).not.toContain("المنتج 123");
});

test("foreign or duplicate catalog rows and missing coverage fail closed", async () => {
    const badRows = [
        [{ product_id: "999", verification: "verified", status: "unknown" }],
        [{ product_id: "123", verification: "verified", status: "unknown" }, { product_id: "123", verification: "verified", status: "unknown" }],
        [],
    ];
    for (const rows of badRows) {
        const response = result(); response.catalog.products = rows; api.get.mockResolvedValue({ data: response });
        await expect(getGoogleCatalogReview(plan)).rejects.toMatchObject({ code: "GOOGLE_CATALOG_REVIEW_UNPROVEN" });
    }
});

test("empty unexcluded pins yield an explicit empty review", async () => {
    const emptyPlan = { ...plan, fields: { pinned_product_ids: ["789"], excluded_product_ids: ["789"] } };
    const response = result({ pinned_product_ids_reviewed: [] }); response.catalog.products = []; response.catalog.missing_or_unverified_product_ids = [];
    api.get.mockResolvedValue({ data: response }); await render({ plan: emptyPlan }); await read();
    expect(container.textContent).toContain("لا توجد منتجات مثبتة غير مستبعدة");
});

test("503 remains a safe retriable read with no private response displayed", async () => {
    api.get.mockRejectedValue({ response: { status: 503, data: { private_body: "secret" } } }); await render(); await read();
    expect(container.querySelector("button").disabled).toBe(false); expect(container.textContent).toContain("تعذرت قراءة الكتالوج");
    expect(container.textContent).not.toContain("secret");
});

function stockProof(state = "available", changes = {}) {
    return { product_id: "123", state, observed_at: "2026-10-29T12:00:00+00:00", stock_digest: "a".repeat(64), scope: "stored_details_snapshot", maximum_supported_variants: 50, remote_variant_coverage_complete: false, size_suitability_verified: false, preparation_time_verified: false, ...changes };
}
test("bounded saved details proof shows its time and remaining requirements", async () => {
    const response = result(); response.catalog.products[0].status = "available"; response.catalog.inventory_reviews = [stockProof()];
    api.get.mockResolvedValue({ data: response }); await render(); await read();
    expect(container.textContent).toContain("مخزون مثبت من تفاصيل محفوظة بتاريخ");
    expect(container.textContent).toContain("2026-10-29T12:00:00+00:00");
    expect(container.textContent).toContain("اكتمال التغطية البعيدة غير مثبت");
    expect(container.textContent).toContain("وقت التجهيز");
});
test.each([{ maximum_supported_variants: 51 }, { remote_variant_coverage_complete: true }, { observed_at: "2026-10-29" }, { stock_digest: "bad" }, { product_id: "foreign" }, { preparation_time_verified: true }])("invalid inventory proof fails closed %j", async (change) => {
    const response = result(); response.catalog.products[0].status = "available"; response.catalog.inventory_reviews = [stockProof("available", change)];
    api.get.mockResolvedValue({ data: response }); await expect(getGoogleCatalogReview(plan)).rejects.toMatchObject({ code: "GOOGLE_CATALOG_REVIEW_UNPROVEN" });
});
