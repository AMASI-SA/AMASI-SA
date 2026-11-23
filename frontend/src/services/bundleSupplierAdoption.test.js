import api from "../lib/api";
import { previewBundleAdoption, applyBundleAdoption, newBundleAdoptionRequestId } from "./bundleSupplierAdoption";

jest.mock("../lib/api", () => ({ __esModule: true, default: { post: jest.fn() } }));

const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
beforeAll(() => Object.defineProperty(globalThis, "crypto", { configurable: true, value: require("crypto").webcrypto }));
afterAll(() => {
    if (cryptoDescriptor) Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
    else delete globalThis.crypto;
});
beforeEach(() => { api.post.mockResolvedValue({ data: { state: "preview" } }); });

test("preview uses only the exact line reference and safely encodes the order", async () => {
    await previewBundleAdoption("test/order", "line-1");
    expect(api.post).toHaveBeenCalledWith("/fulfillment-v2/orders/test%2Forder/bundle-adoption/preview", { order_line_id: "line-1" });
});

test("apply preserves the reviewed hash and stable idempotency key without a client cost", async () => {
    const payload = { order_line_id: "line-1", preview_hash: "a".repeat(64), client_request_id: "bundle-adoption:request-1", reason: "تصحيح الطقم" };
    await applyBundleAdoption("test/order", payload);
    expect(api.post).toHaveBeenCalledWith("/fulfillment-v2/orders/test%2Forder/bundle-adoption/apply", payload);
});

test("request IDs are bounded and distinct", () => {
    const first = newBundleAdoptionRequestId();
    expect(first).toMatch(/^bundle-adoption:[a-f0-9-]+$/);
    expect(first.length).toBeLessThanOrEqual(128);
    expect(newBundleAdoptionRequestId()).not.toBe(first);
});
