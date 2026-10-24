import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { useSourceAdditionReview } from "../../hooks/useSourceAdditionReview";
import { SourceAdditionReviewButton, SourceAdditionReviewNotice } from "./SourceAdditionReview";
import { getSourceAdditions, reviewSourceAddition } from "../../services/sourceAdditionReview";
jest.mock("../../services/sourceAdditionReview", () => ({ getSourceAdditions: jest.fn(), reviewSourceAddition: jest.fn(), sourceAdditionError: (e) => e?.message || "failed" }));
const pending = () => ({ revision: 2, source_fingerprint: "source-a", items: [{ order_item_id: "salla:A:new", eligible: true, reviewed: false, blocked_reason: null }] });
const completed = () => ({ ...pending(), revision: 3, items: pending().items.map((r) => ({ ...r, eligible: false, reviewed: true })) });
function deferred() { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function Harness({ number = "A", actor = "owner", enabled = true, refreshKey = "", ids = ["salla:A:old", "salla:A:new"] }) {
    const review = useSourceAdditionReview({ orderNumber: number, actorKey: actor, enabled, refreshKey });
    return <><SourceAdditionReviewNotice review={review} />{ids.map((id) => <section key={id} data-id={id}><SourceAdditionReviewButton itemId={id} review={review} /></section>)}</>;
}
let host; let root;
async function render(props = {}) { await act(async () => root.render(<Harness {...props} />)); }
async function click() { await act(async () => host.querySelector('section button').click()); }
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true; jest.resetAllMocks();
    getSourceAdditions.mockResolvedValue(pending()); reviewSourceAddition.mockResolvedValue({ ok: true, order_item_id: "salla:A:new" });
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
test("one page request, only new line button, explicit source-fenced approval", async () => {
    await render(); expect(getSourceAdditions).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[data-id="salla:A:old"]').textContent).toBe(""); expect(host.querySelectorAll('section button')).toHaveLength(1);
    getSourceAdditions.mockResolvedValueOnce(completed()); await click();
    expect(reviewSourceAddition).toHaveBeenCalledWith("A", "salla:A:new", pending()); expect(host.textContent).toContain("تمت مراجعة المنتج المضاف");
    expect(host.querySelectorAll('section button')).toHaveLength(0);
});
test("duplicate taps and second product cannot submit while saving", async () => {
    const save = deferred(); reviewSourceAddition.mockReturnValue(save.promise);
    getSourceAdditions.mockResolvedValue({ ...pending(), items: [...pending().items, { ...pending().items[0], order_item_id: "salla:A:new2" }] });
    await render({ ids: ["salla:A:new", "salla:A:new2"] });
    await act(async () => { host.querySelectorAll('section button')[0].click(); host.querySelectorAll('section button')[1].click(); });
    expect(reviewSourceAddition).toHaveBeenCalledTimes(1); expect([...host.querySelectorAll('section button')].every((b) => b.disabled)).toBe(true);
    getSourceAdditions.mockResolvedValue(completed()); await act(async () => save.resolve({ ok: true }));
});
test("uncertain transport reads committed state without automatic POST retry", async () => {
    await render(); reviewSourceAddition.mockRejectedValue(new Error("timeout")); getSourceAdditions.mockResolvedValueOnce(completed()); await click();
    expect(reviewSourceAddition).toHaveBeenCalledTimes(1); expect(getSourceAdditions).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("تمت مراجعة المنتج المضاف"); expect(host.querySelector('[role="alert"]')).toBeNull();
});
test("conflict reloads and requires explicit new retry", async () => {
    await render(); reviewSourceAddition.mockRejectedValue(new Error("source changed")); await click();
    expect(reviewSourceAddition).toHaveBeenCalledTimes(1); expect(host.textContent).toContain("source changed"); expect(host.querySelector('section button').disabled).toBe(true);
    await act(async () => host.querySelector('[role="alert"] button').click()); expect(host.querySelector('section button').disabled).toBe(false);
});
test("GET failure visible; retry restores eligibility", async () => {
    getSourceAdditions.mockRejectedValueOnce(new Error("read failed")); await render(); expect(host.textContent).toContain("read failed"); expect(host.querySelector('section button')).toBeNull();
    await act(async () => host.querySelector('[role="alert"] button').click()); expect(host.querySelector('section button')).not.toBeNull();
});
test.each(["order", "actor", "permission"])("late GET cannot leak across %s change", async (change) => {
    const read = deferred(); getSourceAdditions.mockReturnValueOnce(read.promise); await render(); getSourceAdditions.mockResolvedValue({ ...pending(), items: [] });
    await render(change === "order" ? { number: "B" } : change === "actor" ? { actor: "other" } : { enabled: false }); await act(async () => read.resolve(pending()));
    expect(host.querySelector('section button')).toBeNull();
});
test.each(["order", "actor", "permission"])("late POST cannot refresh across %s change", async (change) => {
    await render(); const save = deferred(); reviewSourceAddition.mockReturnValueOnce(save.promise); await click(); getSourceAdditions.mockResolvedValue({ ...pending(), items: [] });
    await render(change === "order" ? { number: "B" } : change === "actor" ? { actor: "other" } : { enabled: false }); const count = getSourceAdditions.mock.calls.length;
    await act(async () => save.resolve({ ok: true })); expect(getSourceAdditions).toHaveBeenCalledTimes(count); expect(host.querySelector('section button')).toBeNull();
});
test("source refresh once and disabled candidate cannot submit", async () => {
    getSourceAdditions.mockResolvedValue({ ...pending(), items: pending().items.map((r) => ({ ...r, eligible: false, blocked_reason: "stock" })) });
    await render(); await render({ refreshKey: "new-items" }); await render({ refreshKey: "new-items" }); expect(getSourceAdditions).toHaveBeenCalledTimes(2);
    await click(); expect(reviewSourceAddition).not.toHaveBeenCalled();
});
test("stage blocker is explained in Arabic without an action", async () => {
    getSourceAdditions.mockResolvedValue({ ...pending(), items: pending().items.map(r => ({ ...r,
        eligible: false, blocked_reason: "source_addition_order_stage_ineligible",
        blocked_message: "حالة الطلب الحالية لا تسمح باعتماده.", addition_notice: "أُضيف بعد أن أصبح الطلب: تمت المراجعة." })) });
    await render();
    expect(host.querySelector('section button')).toBeNull();
    expect(host.textContent).toContain("حالة الطلب الحالية");
    expect(host.textContent).toContain("أُضيف بعد أن أصبح الطلب: تمت المراجعة");
    expect(host.textContent).not.toContain("source_addition_order_stage_ineligible");
});
test("history note remains visible after approval", async () => {
    const note = "أُضيف بعد أن أصبح الطلب: قيد التنفيذ.";
    getSourceAdditions.mockResolvedValue({ ...completed(), items: completed().items.map(r => ({ ...r, addition_notice: note })) });
    await render(); expect(host.textContent).toContain(note);
    expect(host.textContent).toContain("تمت مراجعة المنتج المضاف");
});
