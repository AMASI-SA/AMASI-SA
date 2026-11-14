import api from "../lib/api";
import { isPreviewDemoEnvironment } from "../demo/orderPreviewFixtures";
import {
    applyOrderProductsProjectionRepair,
    previewOrderProductsProjectionRepair,
} from "./orderEngine";

jest.mock("../lib/api", () => ({ __esModule: true, default: { post: jest.fn() } }));
jest.mock("../demo/orderPreviewFixtures", () => ({ isPreviewDemoEnvironment: jest.fn() }));

const manifest = "a".repeat(64);
const plan = (number = "order / 1") => ({
    repair_version: 1, mode: "dry_run", ready: true, manifest_sha: manifest,
    order_numbers: [number], pending_orders: 1,
    orders: [{ order_number: number, action: "replace_products", reasons: [],
        before: [{ order_item_id: "line-old", product_id: "product-a", quantity: "1" }],
        after: [], removed_item_ids: ["line-old"], reduced_quantities: [] }],
});

beforeEach(() => {
    jest.resetAllMocks();
    isPreviewDemoEnvironment.mockReturnValue(false);
    api.post.mockResolvedValue({ data: plan() });
});

test("preview scopes one encoded order with no query or caller-supplied body", async () => {
    expect(await previewOrderProductsProjectionRepair(" order / 1 ")).toEqual(plan());
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0]).toEqual([
        "/orders-v2/order%20%2F%201/products-projection-repair/preview", {},
        { timeout: 120_000 },
    ]);
});

test("apply sends only the reviewed hash and disables the API's automatic auth replay", async () => {
    const applied = { ...plan(), mode: "applied", applied_orders: 1 };
    api.post.mockResolvedValueOnce({ data: applied });
    expect(await applyOrderProductsProjectionRepair(" order / 1 ", manifest)).toEqual(applied);
    expect(api.post.mock.calls[0]).toEqual([
        "/orders-v2/order%20%2F%201/products-projection-repair/apply", { manifest_sha: manifest },
        { timeout: 120_000, _mezanAuthRetried: true },
    ]);
});

test.each(["", "a".repeat(63), "A".repeat(64), null, { manifest_sha: manifest }])(
    "an invalid review cannot reach apply: %p", async value => {
        await expect(applyOrderProductsProjectionRepair("order / 1", value)).rejects.toThrow(/فحص/);
        expect(api.post).not.toHaveBeenCalled();
    },
);

test("empty order and demo are refused without network calls", async () => {
    await expect(previewOrderProductsProjectionRepair(" ")).rejects.toThrow(/رقم الطلب/);
    isPreviewDemoEnvironment.mockReturnValue(true);
    await expect(previewOrderProductsProjectionRepair("order / 1")).rejects.toThrow(/التجريبية/);
    await expect(applyOrderProductsProjectionRepair("order / 1", manifest)).rejects.toThrow(/التجريبية/);
    expect(api.post).not.toHaveBeenCalled();
});

test.each([
    { ...plan(), order_numbers: ["another-order"] },
    { ...plan(), orders: [...plan().orders, ...plan().orders] },
    { ...plan(), manifest_sha: "not-a-review" },
    { ...plan(), pending_orders: 2 },
    { ...plan(), orders: [{ ...plan().orders[0], after: [{ order_item_id: "line-a", quantity: "0" }] }] },
])("malformed or differently scoped response is not an actionable review", async data => {
    api.post.mockResolvedValue({ data });
    await expect(previewOrderProductsProjectionRepair("order / 1")).rejects.toThrow(/فحص/);
});

test("structured refusal stays safe and does not expose provider details", async () => {
    api.post.mockRejectedValue({ response: { status: 409, data: {
        detail: { code: "repair_manifest_changed", message: "private provider details" },
    } } });
    await expect(applyOrderProductsProjectionRepair("order / 1", manifest)).rejects.toMatchObject({
        code: "repair_manifest_changed", message: expect.stringContaining("أعد فحص"),
    });
    expect(api.post).toHaveBeenCalledTimes(1);
});

test.each([{}, { response: { status: 503, data: { detail: "private failure" } } }])(
    "uncertain apply outcome requests a new preview without retry", async error => {
        api.post.mockRejectedValue(error);
        await expect(applyOrderProductsProjectionRepair("order / 1", manifest))
            .rejects.toThrow("تعذّر تأكيد نتيجة التصحيح");
        expect(api.post).toHaveBeenCalledTimes(1);
    },
);
