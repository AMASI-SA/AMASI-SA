import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import fixture from "../../services/__fixtures__/googlePriorSeasonReview.json";
import GooglePriorSeasonReview from "./GooglePriorSeasonReview";
jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));
const copy = (value) => JSON.parse(JSON.stringify(value));
let container, root;
beforeEach(() => { globalThis.IS_REACT_ACT_ENVIRONMENT = true; api.get.mockReset(); api.get.mockResolvedValue({ data: copy(fixture.response) }); container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<GooglePriorSeasonReview plan={copy(fixture.plan)} isOwner ownerId="owner" {...props} />)); }
async function read() { await act(async () => container.querySelector("button").click()); }
test("explicit saved-interval review uses actual writer fixture and shows bounded gross observations", async () => {
    await render(); expect(api.get).not.toHaveBeenCalled(); expect(container.textContent).toContain("2025-11-01"); await read();
    expect(container.textContent).toContain("عملة الطلب: SAR"); expect(container.querySelector("tbody").textContent).toBe("112");
    expect(container.textContent).toContain("مطابقة الموسم الهجري أو العام السابق غير مثبتة");
    expect(container.textContent).toContain("مرجع ذكره المالك، لم يُتحقق منه");
    expect(container.textContent).toContain("لا نحسب النمو أو الربحية"); expect(container.textContent).toContain("لا تُجمع هذه الفترة");
    expect(container.textContent).toContain("لا تمنح تفويض إنفاق");
});
test.each([false, undefined, "true"])("non-exact owner %s has no surface", async (isOwner) => { await render({ isOwner }); expect(container.textContent).toBe(""); expect(api.get).not.toHaveBeenCalled(); });
test.each(["dirty", "missing", "invalid", "ownerId"])("unproved saved scope %s cannot request", async (kind) => {
    const plan = copy(fixture.plan); let props = {};
    if (kind === "dirty") props.planDirty = true;
    if (kind === "missing") plan.fields.prior_season_context = null;
    if (kind === "invalid") plan.revision = 0;
    if (kind === "ownerId") props.ownerId = undefined;
    await render({ plan, ...props }); expect(container.querySelector("button")).toBe(null); expect(api.get).not.toHaveBeenCalled();
});
test.each(["revision", "fields", "prior", "dirty", "owner", "ownerId", "planId"])("changed %s aborts and withholds late response", async (kind) => {
    let finish; api.get.mockImplementation(() => new Promise((resolve) => { finish = resolve; })); await render(); await read();
    const signal = api.get.mock.calls[0][1].signal, plan = copy(fixture.plan); let props = {};
    if (kind === "revision") plan.revision = 2;
    if (kind === "fields") plan.fields.instruction = "other";
    if (kind === "prior") plan.fields.prior_season_context.alignment_note = "تعديل";
    if (kind === "dirty") props.planDirty = true;
    if (kind === "owner") props.isOwner = false;
    if (kind === "ownerId") props.ownerId = "another-owner";
    if (kind === "planId") plan.plan_id = "other";
    await render({ plan, ...props }); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: copy(fixture.response) })); expect(container.textContent).not.toContain("عملة الطلب: SAR");
});
test("unmount aborts pending read", async () => {
    api.get.mockReturnValue(new Promise(() => {})); await render(); await read(); const signal = api.get.mock.calls[0][1].signal;
    await act(async () => root.render(null)); expect(signal.aborted).toBe(true);
});
test("double click sends one request and masks prior result while rereading", async () => {
    await render(); await read(); expect(container.textContent).toContain("عملة الطلب: SAR");
    api.get.mockReturnValue(new Promise(() => {})); await act(async () => { container.querySelector("button").click(); container.querySelector("button").click(); });
    expect(api.get).toHaveBeenCalledTimes(2); expect(container.textContent).not.toContain("عملة الطلب: SAR");
});
test.each(["failure", "stale", "unknown"])("failed or unproved %s response displays no quantities", async (kind) => {
    if (kind === "failure") api.get.mockRejectedValue(new Error("private provider text"));
    else { const data = copy(fixture.response); if (kind === "stale") data.plan_revision = 2; else data.calendar_alignment_verification = "verified"; api.get.mockResolvedValue({ data }); }
    await render(); await read(); expect(container.querySelector('[role="alert"]')).not.toBe(null);
    expect(container.textContent).not.toContain("عملة الطلب: SAR"); expect(container.textContent).not.toContain("private provider");
});
test("empty and truncated evidence do not mean zero sales", async () => {
    const data = copy(fixture.response); data.observations.truncated = true; data.observations.products[0].currency_groups = [];
    api.get.mockResolvedValue({ data }); await render(); await read(); expect(container.textContent).toContain("حُجبت المقادير الجزئية"); expect(container.textContent).toContain("لا يعني ذلك عدم وجود مبيعات");
});
