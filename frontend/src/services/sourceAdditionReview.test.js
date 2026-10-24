import api from "../lib/api";
import { getSourceAdditions, reviewSourceAddition, sourceAdditionError } from "./sourceAdditionReview";
jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
const data = { revision: 2, source_fingerprint: "fingerprint", items: [{ order_item_id: "salla:A:line", eligible: true, reviewed: false }] };
beforeEach(() => jest.resetAllMocks());
test("GET encodes order and validates envelope", async () => {
    api.get.mockResolvedValue({ data }); expect(await getSourceAdditions("A/B")).toEqual(data);
    expect(api.get).toHaveBeenCalledWith("/order-reviews-v1/A%2FB/source-additions"); expect(api.post).not.toHaveBeenCalled();
});
test.each([null, { ...data, revision: -1 }, { ...data, items: [{ order_item_id: "x", eligible: "true", reviewed: false }] }, { ...data, items: [...data.items, ...data.items] }])("malformed or duplicate capabilities fail closed", async (invalid) => {
    api.get.mockResolvedValue({ data: invalid }); await expect(getSourceAdditions("A")).rejects.toThrow();
});
test("POST carries only concurrency fences", async () => {
    api.post.mockResolvedValue({ data: { ok: true, order_item_id: "salla:A:line/2", revision: 3 } });
    await reviewSourceAddition("A", "salla:A:line/2", { ...data, price: 10, employee_id: "ignored" });
    expect(api.post).toHaveBeenCalledWith("/order-reviews-v1/A/items/salla%3AA%3Aline%2F2/review-addition", { expected_revision: 2, source_fingerprint: "fingerprint" });
});
test("mismatched POST result cannot report success", async () => {
    api.post.mockResolvedValue({ data: { ok: true, order_item_id: "wrong" } });
    await expect(reviewSourceAddition("A", "line", data)).rejects.toThrow();
});
test("inventory rejection is not presented as a stale page", () => {
    const error = { response: { status: 409, data: { detail: { code: "component_insufficient_stock" } } } };
    expect(sourceAdditionError(error)).toContain("مخزون أحد مكونات التجهيز غير كافٍ");
    error.response.data.detail.code = "component_plan_conflict";
    expect(sourceAdditionError(error)).toContain("لم يتم اعتماد المنتج");
});
