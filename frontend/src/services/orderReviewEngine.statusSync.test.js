jest.mock("../lib/api", () => ({
    __esModule: true,
    default: { post: jest.fn(), get: jest.fn() },
    API_BASE: "https://mezansalla.test/api",
}));

import api from "../lib/api";
import { reconcilePreparationBatchStatuses } from "./orderReviewEngine";

const RESPONSE = {
    ok: true,
    batch_id: "batch/id",
    order_status_sync: {
        state: "confirmed", confirmed_count: 1, partial_count: 0, pending_count: 0,
        orders: [{ order_number: "300001", state: "confirmed" }],
    },
};

beforeEach(() => {
    jest.resetAllMocks();
    api.get.mockResolvedValue({ data: { batch_id: "batch/id", order_numbers: ["300001"] } });
});

test("reads the batch plan then posts only its requested order status continuation", async () => {
    api.post.mockResolvedValue({ data: RESPONSE });
    await expect(reconcilePreparationBatchStatuses("batch/id")).resolves.toEqual(RESPONSE);
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith("/reviewed-preparation-batches-v1/batches/batch%2Fid/status-plan");
    expect(api.post).toHaveBeenCalledWith("/reviewed-preparation-batches-v1/batches/batch%2Fid/reconcile-status", { order_number: "300001" });
});

test("awaits each order and returns the cumulative final summary", async () => {
    api.get.mockResolvedValue({ data: { batch_id: "batch/id", order_numbers: ["300001", "300002"] } });
    const first = { ...RESPONSE, order_status_sync: {
        state: "pending", confirmed_count: 1, partial_count: 0, pending_count: 1,
        orders: [{ order_number: "300001", state: "confirmed" }, { order_number: "300002", state: "pending" }],
    } };
    const last = { ...RESPONSE, order_status_sync: {
        state: "confirmed", confirmed_count: 2, partial_count: 0, pending_count: 0,
        orders: [{ order_number: "300001", state: "confirmed" }, { order_number: "300002", state: "confirmed" }],
    } };
    let finishFirst;
    let signalStarted;
    const started = new Promise((resolve) => { signalStarted = resolve; });
    api.post.mockImplementationOnce(() => new Promise((resolve) => {
        finishFirst = resolve;
        signalStarted();
    }));
    api.post.mockResolvedValueOnce({ data: last });
    const request = reconcilePreparationBatchStatuses("batch/id");
    await started;
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][1]).toEqual({ order_number: "300001" });
    finishFirst({ data: first });
    await expect(request).resolves.toEqual(last);
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][1]).toEqual({ order_number: "300002" });
});

test.each([
    {},
    { batch_id: "other", order_numbers: ["300001"] },
    { batch_id: "batch/id", order_numbers: [] },
    { batch_id: "batch/id", order_numbers: ["300001", "300001"] },
    { batch_id: "batch/id", order_numbers: [""] },
    { batch_id: "batch/id", order_numbers: Array.from({ length: 1501 }, (_, index) => String(index)) },
])("never posts after an invalid or unbounded plan", async (data) => {
    api.get.mockResolvedValue({ data });
    await expect(reconcilePreparationBatchStatuses("batch/id"))
        .rejects.toMatchObject({ code: "preparation_status_unconfirmed" });
    expect(api.post).not.toHaveBeenCalled();
});

test("never posts after a failed plan read", async () => {
    api.get.mockRejectedValue(new Error("PRIVATE plan failure"));
    await expect(reconcilePreparationBatchStatuses("batch/id"))
        .rejects.toMatchObject({ code: "preparation_status_unconfirmed" });
    expect(api.post).not.toHaveBeenCalled();
});

test("supports an existing 51-order file while sending one order per request", async () => {
    const numbers = Array.from({ length: 51 }, (_, index) => String(300001 + index));
    api.get.mockResolvedValue({ data: { batch_id: "batch/id", order_numbers: numbers } });
    let confirmed = 0;
    api.post.mockImplementation(async (_path, body) => {
        expect(body.order_number).toBe(numbers[confirmed]);
        confirmed += 1;
        return { data: { ...RESPONSE, order_status_sync: {
            state: confirmed === numbers.length ? "confirmed" : "pending",
            confirmed_count: confirmed, partial_count: 0, pending_count: numbers.length - confirmed,
            orders: numbers.map((number, index) => ({ order_number: number, state: index < confirmed ? "confirmed" : "pending" })),
        } } };
    });
    const result = await reconcilePreparationBatchStatuses("batch/id");
    expect(result.order_status_sync.confirmed_count).toBe(51);
    expect(api.post).toHaveBeenCalledTimes(51);
    expect(api.post.mock.calls.map((call) => call[1].order_number)).toEqual(numbers);
});

test("stops after an interrupted order and never starts the following order", async () => {
    const orders = ["300001", "300002", "300003"];
    api.get.mockResolvedValue({ data: { batch_id: "batch/id", order_numbers: orders } });
    api.post.mockResolvedValueOnce({ data: { ...RESPONSE, order_status_sync: {
        state: "pending", confirmed_count: 1, partial_count: 0, pending_count: 2,
        orders: orders.map((number, index) => ({ order_number: number, state: index ? "pending" : "confirmed" })),
    } } });
    api.post.mockRejectedValueOnce(new Error("PRIVATE lost response"));
    await expect(reconcilePreparationBatchStatuses("batch/id"))
        .rejects.toMatchObject({ code: "preparation_status_unconfirmed" });
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls.map((call) => call[1].order_number)).toEqual(["300001", "300002"]);
});

test.each([
    {},
    { ...RESPONSE, batch_id: "other-batch" },
    { ...RESPONSE, ok: false },
    { ...RESPONSE, order_status_sync: null },
    { ...RESPONSE, order_status_sync: { ...RESPONSE.order_status_sync, confirmed_count: 12 } },
    { ...RESPONSE, order_status_sync: { ...RESPONSE.order_status_sync, state: "unknown" } },
])("rejects an unconfirmed or mismatched success response", async (data) => {
    api.post.mockResolvedValue({ data });
    await expect(reconcilePreparationBatchStatuses("batch/id"))
        .rejects.toMatchObject({ code: "preparation_status_unconfirmed" });
    expect(api.post).toHaveBeenCalledTimes(1);
});

test.each([
    [401, "preparation_status_session_expired"],
    [403, "preparation_status_forbidden"],
    [404, "preparation_status_file_not_found"],
    [409, "preparation_status_conflict"],
    [500, "preparation_status_unconfirmed"],
    [undefined, "preparation_status_unconfirmed"],
])("normalizes HTTP %s without leaking response details or retrying", async (status, code) => {
    api.post.mockRejectedValue({ message: "PRIVATE exception", response: { status, data: { detail: "PRIVATE response" } } });
    let caught;
    try {
        await reconcilePreparationBatchStatuses("batch/id");
    } catch (error) { caught = error; }
    expect(caught).toBeInstanceOf(Error);
    expect(caught.code).toBe(code);
    expect(caught.message).not.toContain("PRIVATE");
    expect(api.post).toHaveBeenCalledTimes(1);
});
