import api from "../lib/api";
import { completeOrderReview, listPendingOrderReviews } from "./orderReviewEngine";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));

beforeEach(() => {
  api.get.mockReset(); api.post.mockReset();
  window.confirm = jest.fn(() => true);
});

test("completion validates local cached quantities and posts the original revision once", async () => {
  api.get.mockResolvedValue({ data: { revision: 7, items: [{ order_item_id: "one", name: "قطعة", quantity: 2 }] } });
  const completed = { ok: true, state: "completed", completion_mode: "mezan_local_v1", salla_status_sync: "not_requested" };
  api.post.mockResolvedValue({ data: completed });
  await expect(completeOrderReview("synthetic/id", 7)).resolves.toEqual(completed);
  expect(api.get).toHaveBeenCalledTimes(1);
  expect(api.get).toHaveBeenCalledWith("/order-reviews-v1/synthetic%2Fid", { params: { local_only: true } });
  expect(window.confirm).toHaveBeenCalledTimes(1);
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post).toHaveBeenCalledWith("/order-reviews-v1/synthetic%2Fid/complete", { expected_revision: 7 });
});

test("cancelled split validation leaves review unsubmitted", async () => {
  api.get.mockResolvedValue({ data: { items: [{ order_item_id: "one", quantity: 2 }] } });
  window.confirm.mockReturnValue(false);
  await expect(completeOrderReview("synthetic", 7)).rejects.toThrow("تم إلغاء اعتماد المراجعة");
  expect(api.post).not.toHaveBeenCalled();
});

test("legacy resolution errors retain their code without retrying the completion", async () => {
  api.get.mockResolvedValue({ data: { items: [] } });
  api.post.mockRejectedValue({ response: { data: { detail: { code: "review_completion_legacy_operation_requires_resolution" } } } });
  await expect(completeOrderReview("synthetic", 7)).rejects.toMatchObject({ code: "review_completion_legacy_operation_requires_resolution" });
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.get).toHaveBeenCalledTimes(1);
});

test("pending adapter preserves authoritative total independently of the page size", async () => {
  api.get.mockResolvedValue({ data: { items: [], next_cursor: "next", total_count: 37 } });
  await expect(listPendingOrderReviews()).resolves.toEqual({ items: [], nextCursor: "next", totalCount: 37 });
});
