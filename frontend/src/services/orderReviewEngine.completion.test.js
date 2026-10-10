import api from "../lib/api";
import { completeOrderReview, listPendingOrderReviews } from "./orderReviewEngine";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));

beforeEach(() => {
  api.get.mockReset(); api.post.mockReset();
  window.confirm = jest.fn(() => true);
});

const displayed = (orderNumber = "synthetic") => ({
  revision: 7, approval_token: "opaque-view-token", approval_fingerprint: "view-fingerprint",
  order: { order_number: orderNumber },
  items: [{ order_item_id: "one", name: "قطعة", quantity: 2 }],
});

test("completion posts the rendered token and revision once without fetching newer facts", async () => {
  const completed = { ok: true, state: "completed", completion_mode: "mezan_local_v1", salla_status_sync: "not_requested" };
  api.post.mockResolvedValue({ data: completed });
  await expect(completeOrderReview("synthetic/id", displayed("synthetic/id"))).resolves.toEqual(completed);
  expect(api.get).not.toHaveBeenCalled();
  expect(window.confirm).toHaveBeenCalledTimes(1);
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post).toHaveBeenCalledWith("/order-reviews-v1/synthetic%2Fid/complete", {
    expected_revision: 7, approval_token: "opaque-view-token",
  });
});

test("cancelled displayed quantity confirmation leaves review unsubmitted", async () => {
  window.confirm.mockReturnValue(false);
  await expect(completeOrderReview("synthetic", displayed())).rejects.toThrow("تم إلغاء اعتماد المراجعة");
  expect(api.post).not.toHaveBeenCalled();
  expect(api.get).not.toHaveBeenCalled();
});

test.each([undefined, 7, { ...displayed(), approval_token: null }, displayed("another-order")])(
  "missing approval evidence or wrong displayed order fails closed: %p", async (detail) => {
    await expect(completeOrderReview("synthetic", detail)).rejects.toMatchObject({ code: "review_approval_required" });
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  },
);

test.each(["component_source_event_stale", "review_completion_source_changed", "review_approval_expired"])(
  "stale approval %s requires review without automatic reload or resubmission", async (code) => {
    api.post.mockRejectedValue({ response: { data: { detail: { code } } } });
    await expect(completeOrderReview("synthetic", displayed())).rejects.toMatchObject({ code, message: expect.stringContaining("راجع المنتجات والخيارات") });
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.get).not.toHaveBeenCalled();
  },
);

test("legacy resolution errors retain their code without retrying", async () => {
  api.post.mockRejectedValue({ response: { data: { detail: { code: "review_completion_legacy_operation_requires_resolution" } } } });
  await expect(completeOrderReview("synthetic", displayed())).rejects.toMatchObject({ code: "review_completion_legacy_operation_requires_resolution" });
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.get).not.toHaveBeenCalled();
});

test("pending adapter preserves authoritative total independently of the page size", async () => {
  api.get.mockResolvedValue({ data: { items: [], next_cursor: "next", total_count: 37 } });
  await expect(listPendingOrderReviews()).resolves.toEqual({ items: [], nextCursor: "next", totalCount: 37 });
});
