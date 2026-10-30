import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleStorageCommitReadiness from "./GoogleStorageCommitReadiness";
jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn() } }));
const good = { scope: "technical_replica_commit_readback_only", status: "verified", replica_set_verified: true,
    commit_majority_j_ack_verified: true, majority_readback_verified: true, cleanup_verified: true,
    execution_authorized: false, funding_ready: false, policy_permissions_verified: false, provider_writes: 0 };
let container, root;
beforeEach(() => { globalThis.IS_REACT_ACT_ENVIRONMENT = true; api.get.mockReset(); api.post.mockReset(); api.put.mockReset();
    api.post.mockResolvedValue({ data: good }); container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(isOwner) { await act(async () => root.render(<GoogleStorageCommitReadiness isOwner={isOwner} />)); }
async function click(index = 0) { await act(async () => container.querySelectorAll("button")[index].click()); }

test("owner diagnostic works without account and never starts automatically", async () => {
    await render(true); expect(container.textContent).toContain("التحقق من حفظ التخزين وقراءته");
    expect(api.get).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
    await click(); expect(api.post.mock.calls[0][0]).toBe("/integrations-v2/google_ads/automation/storage-commit-readiness");
    expect(api.post.mock.calls[0][1]).toEqual({}); expect(api.put).not.toHaveBeenCalled();
    expect(container.textContent).toContain("نجح حفظ السجل التقني"); expect(container.textContent).toContain("لا يمنح صلاحية تشغيل");
});

test.each([false, undefined])("non-owner has no controls or requests %s", async (isOwner) => {
    await render(isOwner); expect(container.textContent).toBe(""); expect(api.post).not.toHaveBeenCalled(); expect(api.get).not.toHaveBeenCalled();
});

test("historical receipt never becomes current capability", async () => {
    await render(true); api.get.mockResolvedValue({ data: { ...good, status: "historical_completed_receipt", commit_majority_j_ack_verified: false } });
    await click(1); expect(api.post).not.toHaveBeenCalled(); expect(container.textContent).toContain("لا يثبت جاهزية التخزين الحالية");
});

test("ambiguous response stays sanitized and cannot claim success", async () => {
    await render(true); api.post.mockResolvedValue({ data: { ...good, cleanup_verified: false } }); await click();
    expect(container.textContent).toContain("لم تُثبت جاهزية التخزين");
    api.post.mockRejectedValue(new Error("private-host")); await click();
    expect(container.textContent).toContain("لا تُعد الحفظ أو الحذف بالتخمين"); expect(container.textContent).not.toContain("private-host");
});

test("double click sends only one operation until result", async () => {
    let resolve; api.post.mockImplementation(() => new Promise((done) => { resolve = done; }));
    await render(true); await act(async () => { container.querySelector("button").click(); container.querySelector("button").click(); });
    expect(api.post).toHaveBeenCalledTimes(1);
    await act(async () => resolve({ data: good })); expect(container.textContent).toContain("نجح حفظ السجل التقني");
});
