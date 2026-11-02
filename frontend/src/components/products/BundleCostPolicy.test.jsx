import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import BundleCostPolicy from "./BundleCostPolicy";

jest.mock("../../lib/api", () => ({ get: jest.fn(), put: jest.fn() }));

let node, root;
const onSaved = jest.fn();
const initial = { revision: 2, bundle_revision: 3, include_parent_base: false, evidence_ref: "" };
const button = (text) => [...node.querySelectorAll("button")].find((item) => item.textContent === text);
const evidence = () => node.querySelector('input:not([type="checkbox"])');

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.resetAllMocks();
    api.get.mockResolvedValue({ data: initial });
    api.put.mockImplementation(async (_url, payload) => ({ data: { revision: payload.expected_revision + 1, bundle_revision: payload.expected_bundle_revision, include_parent_base: payload.include_parent_base, evidence_ref: payload.evidence_ref } }));
    node = document.createElement("div"); root = createRoot(node);
});
afterEach(async () => act(async () => root.unmount()));

async function render(props = {}) {
    await act(async () => root.render(<BundleCostPolicy productId="bundle" bundleRevision={3} onSaved={onSaved} {...props} />));
}
async function inputValue(value) {
    await act(async () => {
        Object.getOwnPropertyDescriptor(Object.getPrototypeOf(evidence()), "value").set.call(evidence(), value);
        evidence().dispatchEvent(new Event("input", { bubbles: true }));
    });
}
async function enable() {
    await act(async () => node.querySelector('input[type="checkbox"]').click());
    await inputValue("Owner confirmed base 15 SAR");
}

test("read is separate and explicit save sends only both revisions, flag and evidence", async () => {
    await render();
    expect(api.get).toHaveBeenCalledWith("/products-v2/bundle/bundle-reporting-cost-policy");
    expect(api.put).not.toHaveBeenCalled();
    expect(button("حفظ إعداد تكلفة البكج").disabled).toBe(true);
    await enable();
    await act(async () => button("حفظ إعداد تكلفة البكج").click());
    expect(api.put).toHaveBeenCalledWith("/products-v2/bundle/bundle-reporting-cost-policy", {
        expected_revision: 2, expected_bundle_revision: 3, include_parent_base: true,
        evidence_ref: "Owner confirmed base 15 SAR",
    });
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(node.textContent).toContain("تكاليف الطلبات السابقة محفوظة");
    expect(button("حفظ إعداد تكلفة البكج").disabled).toBe(true);
});

test("enabling requires a nonempty evidence reference", async () => {
    await render();
    await act(async () => node.querySelector('input[type="checkbox"]').click());
    await inputValue("   ");
    expect(button("حفظ إعداد تكلفة البكج").disabled).toBe(true);
    await act(async () => button("حفظ إعداد تكلفة البكج").click());
    expect(api.put).not.toHaveBeenCalled();
});

test("unsaved composition disables the form without sending a write", async () => {
    await render(); await enable(); await render({ disabled: true });
    expect(node.querySelector("fieldset").disabled).toBe(true);
    expect(node.textContent).toContain("تغييرات غير محفوظة في تركيب البكج");
    await act(async () => button("حفظ إعداد تكلفة البكج").click());
    expect(api.put).not.toHaveBeenCalled();
});

test("stale server bundle revision is visible and cannot be saved", async () => {
    api.get.mockResolvedValue({ data: { ...initial, bundle_revision: 4 } });
    await render();
    expect(node.textContent).toContain("تغير تركيب البكج");
    expect(node.querySelector("fieldset").disabled).toBe(true);
    expect(api.put).not.toHaveBeenCalled();
});

test("failed save retains the draft and does not report a saved policy", async () => {
    api.put.mockRejectedValue({ response: { data: { detail: { code: "bundle_reporting_policy_revision_conflict" } } } });
    await render(); await enable();
    await act(async () => button("حفظ إعداد تكلفة البكج").click());
    expect(node.querySelector('[role="alert"]').textContent).toContain("تغير إعداد التكلفة");
    expect(evidence().value).toBe("Owner confirmed base 15 SAR");
    expect(node.querySelector('input[type="checkbox"]').checked).toBe(true);
    expect(onSaved).not.toHaveBeenCalled();
    expect(api.put.mock.calls[0][1].expected_revision).toBe(2);
});

test("failed reads can be explicitly retried without a write", async () => {
    api.get.mockRejectedValueOnce(new Error("offline"));
    await render();
    expect(button("حفظ إعداد تكلفة البكج")).toBeUndefined();
    await act(async () => button("إعادة تحميل إعداد التكلفة").click());
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(node.querySelector('input[type="checkbox"]')).not.toBeNull();
    expect(api.put).not.toHaveBeenCalled();
});

test("old product response cannot replace the current policy", async () => {
    let oldResponse, newResponse;
    api.get.mockImplementation((url) => new Promise((resolve) => {
        if (url.includes("/old/")) oldResponse = resolve; else newResponse = resolve;
    }));
    await render({ productId: "old" });
    await render({ productId: "new", bundleRevision: 8 });
    await act(async () => oldResponse({ data: { ...initial, evidence_ref: "old evidence" } }));
    expect(evidence()).toBeNull();
    await act(async () => newResponse({ data: { ...initial, bundle_revision: 8, revision: 7 } }));
    await enable();
    await act(async () => button("حفظ إعداد تكلفة البكج").click());
    expect(api.put.mock.calls[0][0]).toBe("/products-v2/new/bundle-reporting-cost-policy");
    expect(api.put.mock.calls[0][1]).toMatchObject({ expected_revision: 7, expected_bundle_revision: 8 });
});

test("old product save response cannot notify or overwrite a new product", async () => {
    let completeSave;
    api.put.mockImplementation(() => new Promise((resolve) => { completeSave = resolve; }));
    await render({ productId: "old" }); await enable();
    await act(async () => button("حفظ إعداد تكلفة البكج").click());
    api.get.mockResolvedValue({ data: { ...initial, bundle_revision: 8, evidence_ref: "new product evidence" } });
    await render({ productId: "new", bundleRevision: 8 });
    await act(async () => completeSave({ data: { ...initial, revision: 3, include_parent_base: true, evidence_ref: "old saved" } }));
    expect(evidence().value).toBe("new product evidence");
    expect(onSaved).not.toHaveBeenCalled();
});

test("disabling retains the saved evidence in the exact payload", async () => {
    api.get.mockResolvedValue({ data: { ...initial, include_parent_base: true, evidence_ref: "original basis" } });
    await render();
    await act(async () => node.querySelector('input[type="checkbox"]').click());
    await act(async () => button("حفظ إعداد تكلفة البكج").click());
    expect(api.put.mock.calls[0][1]).toEqual({ expected_revision: 2, expected_bundle_revision: 3,
        include_parent_base: false, evidence_ref: "original basis" });
});
