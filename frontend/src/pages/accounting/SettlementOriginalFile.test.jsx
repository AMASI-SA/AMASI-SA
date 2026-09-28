import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react-dom/test-utils";
import api from "../../lib/api";
import SettlementOriginalFile from "./SettlementOriginalFile";

jest.mock("../../lib/api", () => ({ get: jest.fn() }));
let node, root;
beforeEach(() => {
    node = document.createElement("div"); document.body.appendChild(node);
    root = createRoot(node);
});
afterEach(() => { act(() => root.unmount()); node.remove(); jest.clearAllMocks(); });
const render = async () => { await act(async () => root.render(<SettlementOriginalFile draftId="owned-file" />)); };
const click = async (name) => {
    const button = [...node.querySelectorAll("button")].find(b => b.textContent === name);
    await act(async () => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
};
test("opens authenticated original and renders text safely with content hash", async () => {
    api.get.mockResolvedValue({ data: { filename: "original.xlsx", size: 123, sha256: "source-hash",
        sheets: [{ name: "Sheet", rows: [["<script>alert(1)</script>"]] }] } });
    await render(); await click("فتح الملف الأصلي");
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining("/drafts/owned-file/original/preview"));
    expect(node.textContent).toContain("source-hash");
    expect(node.textContent).toContain("<script>alert(1)</script>");
    expect(node.querySelector("script")).toBeNull();
});
test("missing original explains unavailability without a reconstructed download", async () => {
    api.get.mockRejectedValue({ response: { data: { detail: { message: "الأصل غير متاح" } } } });
    await render(); await click("فتح الملف الأصلي");
    expect(node.querySelector('[role="alert"]').textContent).toContain("الأصل غير متاح");
    expect(node.querySelector("a")).toBeNull();
});


