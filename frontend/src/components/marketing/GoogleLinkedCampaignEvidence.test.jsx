import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import fixture from "../../services/__fixtures__/googleLinkedCampaignEvidence.json";
import GoogleLinkedCampaignEvidence from "./GoogleLinkedCampaignEvidence";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn(), post: jest.fn() } }));
const clone = (value) => JSON.parse(JSON.stringify(value));
let container, root;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockReset(); api.put.mockReset(); api.post.mockReset(); api.get.mockResolvedValue({ data: clone(fixture.response) });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<GoogleLinkedCampaignEvidence plan={fixture.plan} isOwner {...props} />)); }
async function read() { await act(async () => container.querySelector("button").click()); }

test("explicit owner read displays one campaign row shared by two products and no financial authority", async () => {
    await render(); expect(api.get).not.toHaveBeenCalled(); await read();
    expect(container.querySelectorAll("[data-context-id]")).toHaveLength(1);
    const row = container.querySelector("[data-context-id]");
    expect(row.textContent).toContain("123، 456"); expect(row.textContent).toContain("12.5 SAR");
    expect(container.textContent).toContain("مشتريات المنصة ليست طلبات متجر مستقلة");
    expect(container.textContent).toContain("الربحية غير مثبتة"); expect(container.textContent).toContain("لا تضمن نجاحًا على جوجل");
    expect(container.textContent).toContain("أيام سياق مؤهلة لهذا الربط: 0");
    expect(container.textContent).toContain("الـ14 يومًا السابقة");
    expect(api.put).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});

test.each([false, undefined, null, 1])("non-owner %s cannot read", async (isOwner) => {
    await render({ isOwner }); expect(container.textContent).toBe(""); expect(api.get).not.toHaveBeenCalled();
});
test("dirty plan cannot request context", async () => { await render({ planDirty: true }); expect(container.querySelector("button")).toBeNull(); expect(api.get).not.toHaveBeenCalled(); });

test.each(["revision", "same-revision-fields", "plan", "owner", "dirty"])("%s scope change cancels and discards a late result", async (kind) => {
    let finish; api.get.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await read(); const signal = api.get.mock.calls[0][1].signal;
    const change = kind === "owner" ? { isOwner: false } : kind === "dirty" ? { planDirty: true }
        : { plan: { ...fixture.plan, ...(kind === "revision" ? { revision: 4 } : kind === "plan" ? { plan_id: "summer" } : { fields: { ...fixture.plan.fields, instruction: "الصيف" } }) } };
    await render(change); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: fixture.response })); expect(container.querySelector("[data-context-id]")).toBeNull();
});

test("one in-flight request and cancellation on unmount", async () => {
    let finish; api.get.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await read(); await read(); expect(api.get).toHaveBeenCalledTimes(1); expect(container.querySelector("button").disabled).toBe(true);
    const signal = api.get.mock.calls[0][1].signal;
    await act(async () => root.render(null)); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: fixture.response })); expect(container.textContent).toBe("");
});

test.each([404, 409, "malformed"])("%s evidence locks until saved scope reload", async (kind) => {
    if (kind === "malformed") api.get.mockResolvedValue({ data: { ...fixture.response, execution_authorized: true } });
    else api.get.mockRejectedValue({ response: { status: kind } });
    await render(); await read(); expect(container.querySelector('[role="alert"]')).not.toBeNull(); expect(container.querySelector("button").disabled).toBe(true);
    expect(container.querySelector("[data-context-id]")).toBeNull();
    await render({ plan: { ...fixture.plan, revision: 4 } }); expect(container.querySelector("button").disabled).toBe(false);
});

test("missing source metrics display unknown and never zero", async () => {
    const data = clone(fixture.response), context = data.campaign_contexts[0];
    context.metrics.provider_reported_purchases = null; context.metric_provenance.provider_reported_purchases = "zero_or_source_omission_unproved";
    api.get.mockResolvedValue({ data }); await render(); await read();
    expect(container.querySelector("[data-context-id]").children[2].textContent).toBe("غير مثبت");
});

test("Meta and TikTok association gaps remain explicit without fabricated performance", async () => {
    const data = clone(fixture.response); data.campaign_contexts = []; data.campaign_windows = [];
    for (const [index, product] of data.products.entries()) {
        product.context_refs = []; product.status = "verified_association_only";
        product.associations = [{ provider: index ? "tiktok" : "meta", account_id: "account", campaign_id: "campaign", event_id: `event-${product.product_id}`, source: "manual", meaning: "intended_product_association_only", feed_mapping_verified: false, metrics_status: "source_fields_unproved", missing: index ? ["persisted_native_currency", "persisted_native_timezone"] : ["persisted_native_timezone", "atomic_campaign_snapshot_completeness"] }];
    }
    api.get.mockResolvedValue({ data }); await render(); await read();
    expect(container.textContent).toContain("اكتمال لقطة الحملة غير مثبت");
    expect(container.textContent).toContain("عملة الحساب الأصلية غير مثبتة");
    expect(container.querySelector("[data-context-id]")).toBeNull(); expect(container.textContent).not.toContain("12.5");
});

test("separate accounts and currencies remain separate rows without a combined total", async () => {
    const data = clone(fixture.response), second = clone(data.campaign_contexts[0]);
    second.context_id = "b".repeat(64); second.account_id = "other-account"; second.currency = "USD"; second.metrics.spend_native = 20;
    data.campaign_contexts.push(second);
    data.campaign_windows.push(...data.campaign_windows.map((window) => ({ ...clone(window), account_id: second.account_id, currency: second.currency, context_refs: window.context_refs.map(() => second.context_id) })));
    for (const product of data.products) {
        product.context_refs.push(second.context_id);
        product.associations.push(...product.associations.map((association) => ({ ...clone(association), account_id: second.account_id })));
    }
    api.get.mockResolvedValue({ data }); await render(); await read();
    const rows = [...container.querySelectorAll("[data-context-id]")]; expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("12.5 SAR"); expect(rows[1].textContent).toContain("20 USD");
    expect(container.textContent).not.toContain("32.5");
});

test("empty saved selection says no selected products without inventing activity", async () => {
    const data = { ...fixture.response, products: [], campaign_contexts: [], campaign_windows: [] };
    api.get.mockResolvedValue({ data }); await render({ plan: { ...fixture.plan, fields: { ...fixture.plan.fields, pinned_product_ids: [] } } }); await read();
    expect(container.textContent).toContain("لا توجد منتجات مثبتة غير مستبعدة"); expect(container.querySelector("[data-context-id]")).toBeNull();
});
