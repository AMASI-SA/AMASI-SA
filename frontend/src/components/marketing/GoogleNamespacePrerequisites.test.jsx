import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleNamespacePrerequisites from "./GoogleNamespacePrerequisites";
jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn() } }));
const good = { scope: "namespace_and_legacy_read_only", namespace_prerequisites_verified: true, engine_commit_verified: false,
    migration_ready: false, execution_authorized: false, funding_ready: false, business_writes: 0, provider_writes: 0,
    namespaces: { standing_policy: { required_actions_verified: true, collection_contract_verified: true, unique_index_verified: true,
        inventory_complete: true, records_checked: 2, malformed_count: 0, duplicate_scope_count: 0, create_index_for_put_verified: true,
        actions_verified: { find: true, insert: true, update: true } } } };
let container, root;
beforeEach(() => { globalThis.IS_REACT_ACT_ENVIRONMENT = true; api.get.mockReset(); api.post.mockReset(); api.put.mockReset();
    api.get.mockResolvedValue({ data: good }); container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(isOwner) { await act(async () => root.render(<GoogleNamespacePrerequisites isOwner={isOwner} />)); }
async function click() { await act(async () => container.querySelector("button").click()); }

test("owner audit stays collapsed with no automatic request and explicit read only", async () => {
    await render(true); expect(container.querySelector("details").open).toBe(false);
    expect(container.textContent).toContain("متطلبات حفظ حدود جوجل"); expect(api.get).not.toHaveBeenCalled();
    await click(); expect(api.get.mock.calls[0][0]).toBe("/integrations-v2/google_ads/automation/namespace-prerequisites");
    expect(api.post).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
    expect(container.textContent).toContain("لا يمنح الفحص تفويض تشغيل");
    expect(container.querySelectorAll("details")[1].open).toBe(false);
});

test.each([false, undefined])("non-owner sees no controls or requests %s", async (owner) => {
    await render(owner); expect(container.textContent).toBe(""); expect(api.get).not.toHaveBeenCalled();
});

test("unknown and malformed readiness never claims migration or leaks raw scope", async () => {
    await render(true); api.get.mockResolvedValue({ data: { ...good, namespace_prerequisites_verified: false,
        raw: "private_database_marker", namespaces: { unknown: { role: "private_role_marker" } } } });
    await click(); expect(container.textContent).toContain("بعض المتطلبات غير مثبتة");
    expect(container.textContent).not.toContain("private_");
    api.get.mockResolvedValue({ data: { ...good, migration_ready: true } }); await click();
    expect(container.textContent).toContain("لم تُثبت متطلبات الحفظ");
});

test("request failure sanitized and duplicate clicks do not create parallel audit", async () => {
    await render(true); let reject;
    api.get.mockImplementation(() => new Promise((resolve, fail) => { reject = fail; }));
    await act(async () => { container.querySelector("button").click(); container.querySelector("button").click(); });
    expect(api.get).toHaveBeenCalledTimes(1);
    await act(async () => reject(new Error("private_host_marker")));
    expect(container.textContent).not.toContain("private_host_marker"); expect(container.textContent).toContain("تعذر إثبات متطلبات الحفظ");
});
