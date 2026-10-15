import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import AccountingPeriods from "./AccountingPeriods";

jest.mock("../../lib/api", () => ({ get: jest.fn(), put: jest.fn() }));
const base = "/financial-provider-apps/accounting-module/periods";
let root, node;
const response = (items = [], canManage = true) => ({ data: { items, can_manage: canManage } });
const button = text => [...node.querySelectorAll("button")].find(item => item.textContent.includes(text));
async function fill(label, value) {
    const input = node.querySelector(`input[aria-label="${label}"]`);
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
    });
}
async function decision(month = "2026-08") {
    await fill("الشهر المحاسبي", month);
    await fill("سبب قرار الفترة", "  reviewed month  ");
    await fill("مرجع اعتماد الفترة", "  SYN-OWNER-DECISION  ");
}
beforeEach(() => {
    jest.resetAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
});
afterEach(() => { act(() => root.unmount()); node.remove(); });

test("viewer reads closed periods but cannot submit a period decision", async () => {
    api.get.mockResolvedValue(response([{ month: "2026-08", closed: true, revision: 4, reason: "approved" }], false));
    await act(async () => root.render(<AccountingPeriods />));
    expect(node.textContent).toContain("2026-08: مقفلة");
    expect(node.querySelector("input")).toBeNull();
    expect(button("إعادة فتح")).toBeUndefined();
    expect(button("إقفال الفترة")).toBeUndefined();
    expect(api.put).not.toHaveBeenCalled();
});

test("close requires an explicit month, nonblank reason and evidence", async () => {
    api.get.mockResolvedValue(response());
    await act(async () => root.render(<AccountingPeriods />));
    expect(button("إقفال الفترة").disabled).toBe(true);
    await fill("الشهر المحاسبي", "2026-08");
    await fill("سبب قرار الفترة", "  ");
    await fill("مرجع اعتماد الفترة", "SYN-APPROVAL");
    expect(button("إقفال الفترة").disabled).toBe(true);
    await fill("سبب قرار الفترة", "Reviewed");
    await fill("مرجع اعتماد الفترة", "  ");
    expect(button("إقفال الفترة").disabled).toBe(true);
    expect(api.put).not.toHaveBeenCalled();
});

test("owner closes selected month using its observed revision and explicit evidence", async () => {
    api.get.mockResolvedValue(response([{ month: "2026-08", closed: false, revision: 7 },
        { month: "2026-09", closed: false, revision: 2 }]));
    api.put.mockResolvedValue({ data: {} });
    await act(async () => root.render(<AccountingPeriods />));
    await decision();
    api.get.mockResolvedValue(response([{ month: "2026-08", closed: true, revision: 8 }]));
    await act(async () => button("إقفال الفترة").click());
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(api.put).toHaveBeenCalledWith(base, { month: "2026-08", closed: true, revision: 7,
        reason: "reviewed month", evidence_ref: "SYN-OWNER-DECISION" });
    expect(node.querySelector('input[type="month"]').value).toBe("2026-08");
    expect(node.querySelector('input[aria-label="سبب قرار الفترة"]').value).toBe("");
    expect(node.querySelector('input[aria-label="مرجع اعتماد الفترة"]').value).toBe("");
    expect(button("إعادة فتح").disabled).toBe(true);
});

test("stale conflict neither retries nor reopens nor changes the selected month", async () => {
    api.get.mockResolvedValue(response([{ month: "2026-08", closed: false, revision: 7 }]));
    api.put.mockRejectedValue({ response: { status: 409, data: "period_changed_refresh_required" } });
    await act(async () => root.render(<AccountingPeriods />));
    await decision();
    await act(async () => button("إقفال الفترة").click());
    expect(node.querySelector('[role="alert"]').textContent).toContain("لم يُؤكد تغيير الفترة");
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(node.querySelector('input[type="month"]').value).toBe("2026-08");
    expect(button("إعادة فتح")).toBeUndefined();
    api.get.mockResolvedValue(response([{ month: "2026-08", closed: true, revision: 8 }]));
    await act(async () => button("تحديث الفترات").click());
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(button("إعادة فتح")).toBeDefined();
});

test("reopening is a separate explicit owner action with current revision", async () => {
    api.get.mockResolvedValue(response([{ month: "2026-08", closed: true, revision: 8 }]));
    api.put.mockResolvedValue({ data: {} });
    await act(async () => root.render(<AccountingPeriods />));
    await fill("الشهر المحاسبي", "2026-08");
    expect(button("إعادة فتح").disabled).toBe(true);
    await decision();
    expect(api.put).not.toHaveBeenCalled();
    await act(async () => button("إعادة فتح").click());
    expect(api.put).toHaveBeenCalledWith(base, { month: "2026-08", closed: false, revision: 8,
        reason: "reviewed month", evidence_ref: "SYN-OWNER-DECISION" });
});

test("failed state load exposes an error without speculative close or reopen", async () => {
    api.get.mockRejectedValue(new Error("offline"));
    await act(async () => root.render(<AccountingPeriods />));
    expect(node.querySelector('[role="alert"]').textContent).toContain("تعذر تحميل");
    expect(node.querySelector("input")).toBeNull();
    expect(api.put).not.toHaveBeenCalled();
});
