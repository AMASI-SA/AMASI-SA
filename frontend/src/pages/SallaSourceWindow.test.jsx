import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { toast } from "sonner";
import api from "../lib/api";
import SallaIntegration from "./SallaIntegration";

jest.mock("react-router-dom", () => ({
    useLocation: () => ({ search: "" }), useNavigate: () => jest.fn(),
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock("./SallaWebhookRecovery", () => ({ SallaWebhookRecoveryLink: () => null }));

let host, root, logs;
beforeEach(() => {
    jest.resetAllMocks();
    jest.useFakeTimers();
    // UTC is still the 23rd: the source window must use the Riyadh day.
    jest.setSystemTime(new Date("2030-04-23T22:30:00Z"));
    global.IS_REACT_ACT_ENVIRONMENT = true;
    logs = [];
    api.get.mockImplementation(async url => ({ data: url.includes("sync/logs") ? { logs }
        : url === "/salla/status" ? { connected: true, status: "connected" }
            : url === "/salla/config" ? { client_id: "synthetic-client" } : {} }));
    api.post.mockResolvedValue({ data: { accepted: true } });
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(() => {
    act(() => root.unmount());
    host.remove();
    jest.useRealTimers();
});
async function render() { await act(async () => root.render(<SallaIntegration />)); }
function get(id) { return host.querySelector(`[data-testid="${id}"]`); }
async function change(id, value) {
    await act(async () => {
        const input = get(id);
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
}
async function click(id) { await act(async () => get(id).click()); }

test("30-day action submits supported inclusive Riyadh creation bounds", async () => {
    await render();
    expect(api.post).not.toHaveBeenCalled();
    await click("salla-sync-orders-btn");
    expect(api.post).toHaveBeenCalledWith("/salla/sync/orders", {
        from_date: "2030-03-26", to_date: "2030-04-24", recover_missing_order_clock: true,
    });
});

test("selected short source window is operable while historical import stays intact", async () => {
    await render();
    await change("salla-source-from-date", "2030-04-20");
    await change("salla-source-to-date", "2030-04-24");
    await click("salla-source-window-btn");
    expect(api.post).toHaveBeenCalledWith("/salla/sync/orders", {
        from_date: "2030-04-20", to_date: "2030-04-24", recover_missing_order_clock: true,
    });
    expect(get("salla-date-range-sync").querySelector('input[disabled]').value).toBe("2026-07-01");
    expect(host.textContent).toContain("لا يثبت حصر تغييرات جميع الطلبات الأقدم");
});

test.each([
    ["2030-03-01", "2030-04-24"], ["2030-04-24", "2030-04-20"],
    ["2030-04-24", "2030-04-25"],
])("invalid recovery window cannot launch source work (%s to %s)", async (start, end) => {
    await render();
    await change("salla-source-from-date", start);
    await change("salla-source-to-date", end);
    await click("salla-source-window-btn");
    expect(api.post).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith("حدد فترة من يوم إلى 31 يومًا، تنتهي اليوم أو قبله");
});

test.each([
    { status: "partial", incomplete_count: 7, source_clock_missing_count: 7 },
    { status: "completed", truncated: true, pages_fetched: 40, source_total_pages: 5000 },
    { status: "completed", coverage_complete: false, coverage_reason: "provider_modified_since_filter_unavailable" },
    { status: "completed", errors_count: 1 },
    { status: "completed", incomplete_count: 1 },
    { status: "partial", incomplete_count: 1, pending_component_count: 1 },
])("partial evidence cannot render a completed badge, including historical logs (%j)", async fields => {
    logs = [{ id: "synthetic-log", kind: "orders", skipped: 1200, ...fields }];
    await render();
    const table = get("salla-sync-logs");
    expect(table.textContent).toContain("جزئي");
    expect(table.textContent).not.toContain("✓ مكتمل");
    expect(table.textContent).toContain("1200");
    if (fields.truncated) {
        expect(table.textContent).toContain("40 / 5000");
        expect(table.textContent).toContain("لم تُقرأ جميع الصفحات");
    }
    if (fields.source_clock_missing_count) {
        expect(table.textContent).toContain("وقت تحديث بعض الطلبات غير مثبت من سلة");
    }
    if (fields.pending_component_count) {
        expect(table.textContent).toContain("بعض الطلبات تحتاج استكمال اعتماد المكونات");
    }
});

test("complete bounded creation scan retains honest coverage and completed badge", async () => {
    logs = [{ id: "synthetic-complete", kind: "orders", status: "completed", updated: 3,
        unchanged: 2, coverage_complete: true, coverage_reason: "creation_window_only",
        from_date: "2030-04-20", to_date: "2030-04-24" }];
    await render();
    expect(get("salla-sync-logs").textContent).toContain("✓ مكتمل");
    expect(get("salla-sync-logs").textContent).toContain("طلبات منشأة من 2030-04-20 إلى 2030-04-24");
});

test("an existing running job blocks both manual recovery buttons", async () => {
    logs = [{ id: "synthetic-running", kind: "orders", status: "running", started_at: new Date().toISOString() }];
    await render();
    expect(get("salla-source-window-btn").disabled).toBe(true);
    expect(get("salla-sync-orders-btn").disabled).toBe(true);
    await click("salla-source-window-btn");
    expect(api.post).not.toHaveBeenCalled();
});
