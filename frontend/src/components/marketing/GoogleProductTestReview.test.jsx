import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import { AuthProvider, useAuth } from "../../context/AuthContext";
import GoogleProductTestReview from "./GoogleProductTestReview";
import fixture from "../../services/__fixtures__/googleProductTestReview.json";
jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn(), post: jest.fn() }, formatApiErrorDetail: jest.fn() }));
const copy = (v) => JSON.parse(JSON.stringify(v));
let container, root, actor, state, fitRead, saveRead, candidateRead;
const fitUrl = "/integrations-v2/google_ads/commercial-plans/winter/product-season-reviews";
function Refresh() { const { refreshUser } = useAuth(); return <button type="button" aria-label="refresh-auth" onClick={() => refreshUser().catch(() => {})}>refresh</button>; }
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true; actor = copy(fixture.auth_user); state = copy(fixture.initial_fit_state); fitRead = saveRead = candidateRead = null;
    api.get.mockReset(); api.put.mockReset(); api.post.mockReset();
    api.get.mockImplementation((url) => url === "/auth/me" ? Promise.resolve({ data: copy(actor) }) : url === fitUrl ? fitRead ? fitRead() : Promise.resolve({ data: copy(state) }) : Promise.reject(new Error("unexpected GET")));
    api.put.mockImplementation(() => saveRead ? saveRead() : (state = copy(fixture.fit_state), Promise.resolve({ data: copy(fixture.fit_saved) })));
    api.post.mockImplementation(() => candidateRead ? candidateRead() : Promise.resolve({ data: copy(fixture.candidate) }));
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => { root.render(<AuthProvider><Refresh /><GoogleProductTestReview plan={fixture.plan} category={fixture.category} selectedProductIds={fixture.selected_product_ids} isOwner {...props} /></AuthProvider>); await Promise.resolve(); await Promise.resolve(); }); }
const byLabel = (label) => container.querySelector(`[aria-label="${label}"]`);
const button = (text) => [...container.querySelectorAll("button")].find((node) => node.textContent === text);
const readButton = () => button("قراءة مراجعات الملاءمة المحفوظة");
const previewButton = () => button("مراجعة مرشحي الاختبار المحددين");
const fitCalls = () => api.get.mock.calls.filter(([url]) => url === fitUrl);
async function click(node) { await act(async () => node.click()); }
async function change(label, value) { const node = byLabel(label), proto = node.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLSelectElement.prototype; await act(async () => { Object.getOwnPropertyDescriptor(proto, "value").set.call(node, value); node.dispatchEvent(new Event(node.tagName === "SELECT" ? "change" : "input", { bubbles: true })); }); }
async function load() { await click(readButton()); }
async function prepare() { await change("ملاءمة المنتج 1", "suitable_for_test"); await change("سبب ملاءمة المنتج 1", fixture.fit_saved.reason); await click(byLabel("راجعت ملاءمة المنتج 1")); }
async function save() { await click(button("حفظ مراجعة المنتج 1")); }

test("real AuthProvider owner explicit GET then reviewed CAS PUT then explicit candidate POST", async () => {
    await render(); expect(api.get.mock.calls.map(([url]) => url)).toEqual(["/auth/me"]); expect(api.post).not.toHaveBeenCalled(); expect(previewButton().disabled).toBe(true);
    await load(); expect(byLabel("سبب ملاءمة المنتج 1").value).toBe(""); expect(byLabel("راجعت ملاءمة المنتج 1").checked).toBe(false); expect(api.put).not.toHaveBeenCalled();
    await prepare(); expect(previewButton().disabled).toBe(true); await save(); expect(api.put).toHaveBeenCalledTimes(1); expect(api.put.mock.calls[0][1]).toMatchObject({ expected_plan_revision: 1, expected_review_revision: 0, acknowledge_owner_review: true });
    expect(byLabel("راجعت ملاءمة المنتج 1").checked).toBe(false); expect(api.post).not.toHaveBeenCalled(); await click(previewButton());
    expect(container.querySelector('[data-product-test-result]')).not.toBe(null); expect(container.querySelectorAll('[data-campaign-context]')).toHaveLength(1);
    expect(container.querySelector('[data-product-decision="1"]').textContent).toContain("عملة الطلب: SAR"); expect(container.querySelector('[data-product-decision="1"]').textContent).toContain("عملة الطلب: USD");
    expect(container.textContent).toContain("هذه مقاييس الحملة المشتركة، لا تُوزّع على المنتجات"); expect(container.textContent).toContain("التكاليف التاريخية والربحية"); expect(container.textContent).toContain("لا يوجد اعتماد إنفاق");
});
test.each(["staff", "anonymous", "not_owner_prop", "no_context"])("unproved actual owner %s dispatches no new review requests", async (kind) => {
    if (kind === "staff") actor.role = "staff"; if (kind === "anonymous") actor = false;
    if (kind === "no_context") await act(async () => root.render(<GoogleProductTestReview plan={fixture.plan} category={fixture.category} selectedProductIds={["1"]} isOwner />)); else await render({ isOwner: kind !== "not_owner_prop" });
    expect(byLabel("مراجعة ملاءمة المنتجات لاختبار جوجل")).toBe(null); expect(fitCalls()).toHaveLength(0); expect(api.put).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});
test.each(["empty", "excluded", "dirty", "external_busy"])("selection/control %s cannot request", async (kind) => {
    await render(kind === "empty" ? { selectedProductIds: [] } : kind === "excluded" ? { selectedProductIds: ["3"] } : kind === "dirty" ? { planDirty: true } : { externalBusy: true });
    if (readButton()) expect(readButton().disabled).toBe(true); expect(fitCalls()).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
});
test("reason/decision edits clear acknowledgement and candidate result; read discards unsaved local draft", async () => {
    state = copy(fixture.fit_state); await render(); await load(); await click(previewButton()); expect(container.querySelector('[data-product-test-result]')).not.toBe(null);
    await click(byLabel("راجعت ملاءمة المنتج 1")); await change("سبب ملاءمة المنتج 1", "تعديل جديد");
    expect(byLabel("راجعت ملاءمة المنتج 1").checked).toBe(false); expect(previewButton().disabled).toBe(true); expect(container.querySelector('[data-product-test-result]')).toBe(null);
    await load(); expect(byLabel("سبب ملاءمة المنتج 1").value).toBe(fixture.fit_saved.reason); expect(api.put).not.toHaveBeenCalled();
});
test.each(["network", "conflict", "invalid_response"])("uncertain PUT %s requires explicit GET before retry and never resends", async (kind) => {
    await render(); await load(); await prepare();
    saveRead = () => kind === "invalid_response" ? Promise.resolve({ data: { ...fixture.fit_saved, review_revision: 99 } }) : Promise.reject(kind === "conflict" ? { response: { status: 409, data: { detail: { review_may_have_persisted: true } } } } : new Error("private upstream"));
    await save(); expect(api.put).toHaveBeenCalledTimes(1); expect(previewButton().disabled).toBe(true); expect(button("حفظ مراجعة المنتج 1").disabled).toBe(true); expect(container.textContent).toContain("قد تكون حُفظت"); expect(container.textContent).not.toContain("private upstream");
    await act(async () => Promise.resolve()); expect(api.put).toHaveBeenCalledTimes(1); expect(fitCalls()).toHaveLength(1);
    state = copy(fixture.fit_state); await load(); expect(fitCalls()).toHaveLength(2); expect(container.textContent).toContain("نسخة المراجعة 1");
    saveRead = () => Promise.resolve({ data: copy(fixture.rereview_saved) }); await click(byLabel("راجعت ملاءمة المنتج 1")); await save();
    expect(api.put.mock.calls[1][1].expected_review_revision).toBe(1); expect(api.put).toHaveBeenCalledTimes(2);
});
test.each(["GET", "PUT", "POST"])("identical public auth user replacement while %s pending aborts and ignores late response", async (kind) => {
    state = copy(fixture.fit_state); await render(); if (kind !== "GET") await load();
    let finish; const pending = () => new Promise((resolve) => { finish = resolve; });
    if (kind === "GET") { fitRead = pending; await load(); }
    if (kind === "PUT") { await click(byLabel("راجعت ملاءمة المنتج 1")); saveRead = pending; await save(); }
    if (kind === "POST") { candidateRead = pending; await click(previewButton()); }
    const options = kind === "GET" ? fitCalls().at(-1)[1] : kind === "PUT" ? api.put.mock.calls.at(-1)[2] : api.post.mock.calls.at(-1)[2];
    const before = fitCalls().length; await click(byLabel("refresh-auth")); expect(options.signal.aborted).toBe(true);
    await act(async () => finish({ data: copy(kind === "GET" ? fixture.fit_state : kind === "PUT" ? fixture.rereview_saved : fixture.candidate) }));
    expect(container.querySelector('[data-fit-product]')).toBe(null); expect(container.querySelector('[data-product-test-result]')).toBe(null); expect(fitCalls()).toHaveLength(before); expect(previewButton().disabled).toBe(true);
});
test.each(["revision", "fields", "category", "selection", "dirty"])("scope %s changes abort pending read and erase old state", async (kind) => {
    await render(); let finish; fitRead = () => new Promise((resolve) => { finish = resolve; }); await load(); const signal = fitCalls()[0][1].signal;
    const plan = copy(fixture.plan), category = copy(fixture.category); let props = { plan, category };
    if (kind === "revision") plan.revision = 2; if (kind === "fields") plan.fields.instruction = "تعديل"; if (kind === "category") category.response_digest = "a".repeat(64);
    if (kind === "selection") props.selectedProductIds = ["1"]; if (kind === "dirty") props.planDirty = true;
    await render(props); expect(signal.aborted).toBe(true); await act(async () => finish({ data: copy(fixture.fit_state) }));
    expect(container.querySelector('[data-fit-product]')).toBe(null); expect(container.querySelector('[data-product-test-result]')).toBe(null);
});
test("unmount aborts original request and prevents late data", async () => { await render(); let finish; fitRead = () => new Promise((resolve) => { finish = resolve; }); await load(); const signal = fitCalls()[0][1].signal; await act(async () => root.render(null)); expect(signal.aborted).toBe(true); await act(async () => finish({ data: copy(fixture.fit_state) })); expect(container.textContent).toBe(""); });
test("double click dispatches one read while busy and POST cannot overlap", async () => { await render(); fitRead = () => new Promise(() => {}); await act(async () => { readButton().click(); readButton().click(); }); expect(fitCalls()).toHaveLength(1); expect(previewButton().disabled).toBe(true); });
