import api from "../lib/api";
import { issueCompletedOrderCarrierLabel, refreshCompletedOrderCarrierLabel } from "./fulfillmentV2";
import { issueShippingLabel, verifyShippingLabel } from "./orderEngine";

jest.mock("../lib/api", () => ({ post: jest.fn() }));
jest.mock("../demo/orderPreviewFixtures", () => ({ isPreviewDemoEnvironment: () => false }));

beforeEach(() => api.post.mockReset());

test.each([
    [issueCompletedOrderCarrierLabel, ""], [refreshCompletedOrderCarrierLabel, "/refresh"],
    [issueShippingLabel, ""], [verifyShippingLabel, "/refresh"],
])("web shipping actions use only the current order contract", async (action, suffix) => {
    const pending = { order_number: "123/4", contract_version: 1, ready: false, print_state: "pending" };
    api.post.mockResolvedValue({ data: pending });
    await expect(action("123/4")).resolves.toEqual(pending);
    expect(api.post.mock.calls).toEqual([[`/fulfillment-v2/completed/123%2F4/carrier-label/v1${suffix}`]]);
});

test.each([
    { contract_version: 0 }, { order_number: "another-order" }, { ready: true, print_state: "pending" },
])("a mismatched result never opens a label or retries an old endpoint", async mismatch => {
    api.post.mockResolvedValue({ data: {
        order_number: "123", contract_version: 1, ready: true, print_state: "ready", ...mismatch,
    } });
    await expect(verifyShippingLabel("123")).rejects.toThrow();
    expect(api.post).toHaveBeenCalledTimes(1);
});
