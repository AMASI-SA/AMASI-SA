import http from "../../lib/api";
import { operationalApi } from "./api";

jest.mock("../../lib/api", () => ({ post: jest.fn() }));

beforeEach(() => http.post.mockClear());

test("delivery preview forwards its cancellation signal and exact owner-scoped body", async () => {
    const controller = new AbortController();
    const body = { order_id: "old-order", order_number: "1001", party_type: "courier", party_id: "carrier",
        expected_session_scope: "owner-session", visible: true };
    const preview = { can_confirm: true, preview_hash: "a".repeat(64) };
    http.post.mockResolvedValueOnce({ data: preview });
    expect(await operationalApi.previewDeliveryAfterStart(body, { signal: controller.signal })).toBe(preview);
    expect(http.post).toHaveBeenCalledWith("/operational-balances/delivery-after-start/preview", body,
        { timeout: 30000, signal: controller.signal });
});

test("delivery confirmation sends one explicit write without a preview cancellation signal", async () => {
    const body = { order_id: "old-order", order_number: "1001", party_type: "courier", party_id: "carrier",
        expected_session_scope: "owner-session", visible: true, request_id: "request-one",
        expected_preview_hash: "a".repeat(64), confirmed_after_start: true };
    const confirmation = { ok: true, pending_reconciliation: true };
    http.post.mockResolvedValueOnce({ data: confirmation });
    expect(await operationalApi.confirmDeliveryAfterStart(body)).toBe(confirmation);
    expect(http.post).toHaveBeenCalledTimes(1);
    expect(http.post).toHaveBeenCalledWith("/operational-balances/delivery-after-start/confirm", body);
});
