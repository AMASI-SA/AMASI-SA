import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import LateDeliveryEvidence from "./LateDeliveryEvidence";
jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
let root, node;
const assignment = { id: "assignment-1", status: "delivered" };
const result = { attachment: { id: "attachment-1", assignment_id: "assignment-1", source: { delivered_at: "2026-09-01T10:00:00Z", original_proof_reference: "original", c3: { present: true, id: "c3", seal: "sealed" } }, attached_at: "2026-10-02T10:00:00Z", financial_effect: "none" }, decision: null, state: "pending" };
beforeEach(() => { jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true; node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); api.get.mockResolvedValue({ data: { items: [] } }); api.post.mockResolvedValue({ data: result }); });
afterEach(() => { act(() => root.unmount()); node.remove(); });
const mount = (value = assignment) => act(async () => root.render(<LateDeliveryEvidence assignment={value} />));
async function fill(reason = "صورة إضافية توضح التسليم", file = new File(["image"], "proof.png", { type: "image/png" })) {
    await act(async () => { const text = node.querySelector("textarea"); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(text, reason); text.dispatchEvent(new Event("input", { bubbles: true })); const input = node.querySelector('input[type="file"]'); Object.defineProperty(input, "files", { value: [file], configurable: true }); input.dispatchEvent(new Event("change", { bubbles: true })); });
}
const submit = () => act(async () => node.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
test("only delivered assignments expose late attachment; no premature calls", async () => {
    await mount({ ...assignment, status: "out_for_delivery" }); expect(node.textContent).toBe(""); expect(api.get).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});
test("upload binds exact assignment and original history stays distinct; retry reuses request id", async () => {
    api.post.mockRejectedValueOnce(new Error("lost response")); await mount();
    expect(api.get).toHaveBeenCalledWith("/store-delivery/evidence/late-delivery", { params: { assignment_id: "assignment-1" } });
    expect(node.querySelector("button").disabled).toBe(true); await fill(); await submit();
    expect(node.querySelector('[role="alert"]')).not.toBeNull(); expect(node.querySelector('[role="status"]')).toBeNull();
    const first = api.post.mock.calls[0][1]; await submit(); const second = api.post.mock.calls[1][1];
    expect(first.get("request_id")).toBe(second.get("request_id")); expect(first.get("request_id").length).toBeGreaterThanOrEqual(8);
    expect([...second.keys()].sort()).toEqual(["assignment_id", "file", "reason", "request_id"]);
    expect(second.get("assignment_id")).toBe("assignment-1"); expect(second.get("file").name).toBe("proof.png");
    expect(api.post.mock.calls.every(([url]) => url === "/store-delivery/evidence/late-delivery")).toBe(true);
    expect(node.textContent).toContain("2026-09-01T10:00:00Z"); expect(node.textContent).toContain("2026-10-02T10:00:00Z");
    expect(node.querySelector('[role="status"]').textContent).toContain("لم تتغير حالة التسوية أو أي أرصدة");
});
test("changed retry payload gets a new request identity", async () => {
    api.post.mockRejectedValue(new Error("offline")); await mount(); await fill(); await submit(); const first = api.post.mock.calls[0][1].get("request_id");
    await fill("توضيح آخر"); await submit(); expect(api.post.mock.calls[1][1].get("request_id")).not.toBe(first);
});
test("invalid file and short reason never upload", async () => {
    await mount(); await fill("ab"); await submit(); expect(api.post).not.toHaveBeenCalled();
    await fill("سبب واضح", new File(["pdf"], "proof.pdf", { type: "application/pdf" })); await submit(); expect(api.post).not.toHaveBeenCalled(); expect(node.textContent).toContain("8 ميجابايت");
});
test("malformed response never reports successful attachment", async () => {
    api.post.mockResolvedValue({ data: { state: "posted" } }); await mount(); await fill(); await submit(); expect(node.querySelector('[role="alert"]')).not.toBeNull(); expect(node.querySelector('[role="status"]')).toBeNull();
});
