import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import ManualReviewRecovery from "./ManualReviewRecovery";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));

const candidate = { order_number: "291967952", old_operation_id: "old-operation" };
const preview = () => ({
    ...candidate, session_id: "new-approval-session", approval_hash: "approved-current-facts",
    expires_at: new Date(Date.now() + 600000).toISOString(),
    preview: {
        source: "salla_order_details_and_items", eligibility: { eligible: true },
        order: { items: [{ name: "منتج حالي", sku: "NEW-SKU", quantity: 2, options_raw: [{ name: "الاسم المطلوب", value: "هدى" }] }], customer: { name: "العميل الحالي" }, shipping: { company: "شركة الشحن الحالية" }, payment: { status: "paid" } },
        items: [{ name: "خدمة التغليف" }], components: [{ name: "مكون حالي" }],
    },
});

describe("explicit current-state manual review recovery", () => {
    let container, root, reloaded;
    beforeEach(() => {
        globalThis.IS_REACT_ACT_ENVIRONMENT = true;
        api.get.mockReset(); api.post.mockReset();
        api.get.mockResolvedValue({ data: { items: [candidate] } });
        api.post.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/prepare") ? preview() : { stage: "reviewed" } }));
        container = document.createElement("div"); document.body.appendChild(container);
        root = createRoot(container); reloaded = jest.fn();
    });
    afterEach(async () => { await act(async () => root.unmount()); container.remove(); delete globalThis.IS_REACT_ACT_ENVIRONMENT; });
    const button = (text) => [...container.querySelectorAll("button")].find((element) => element.textContent === text);
    const render = async () => { await act(async () => { root.render(<ManualReviewRecovery onRecovered={reloaded} />); }); };
    const click = async (text) => { await act(async () => { button(text).click(); }); };

    test("refresh only previews current facts; a separate explicit click creates the new approval", async () => {
        await render();
        expect(api.post).not.toHaveBeenCalled();
        expect(button("تأكيد الاستعادة للمراجعة")).toBeUndefined();
        await click("تحديث واستعادة للمراجعة");
        expect(api.post).toHaveBeenCalledTimes(1);
        expect(api.post).toHaveBeenLastCalledWith("/order-reviews-v1/291967952/manual-recovery/prepare", { old_operation_id: "old-operation" });
        for (const fact of ["منتج حالي", "NEW-SKU", "هدى", "العميل الحالي", "شركة الشحن الحالية", "خدمة التغليف", "مكون حالي"]) expect(container.textContent).toContain(fact);
        await click("تأكيد الاستعادة للمراجعة");
        expect(api.post).toHaveBeenLastCalledWith("/order-reviews-v1/291967952/manual-recovery/confirm", { session_id: "new-approval-session", approval_hash: "approved-current-facts", confirmed: true });
        expect(reloaded).toHaveBeenCalledTimes(1);
        expect(button("تأكيد الاستعادة للمراجعة")).toBeUndefined();
    });

    test("same-tick double click sends one prepare and one confirmation", async () => {
        await render();
        await act(async () => { const target = button("تحديث واستعادة للمراجعة"); target.click(); target.click(); });
        expect(api.post).toHaveBeenCalledTimes(1);
        let finish;
        api.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
        await act(async () => { const target = button("تأكيد الاستعادة للمراجعة"); target.click(); target.click(); });
        expect(api.post).toHaveBeenCalledTimes(2);
        await act(async () => finish({ data: { stage: "reviewed" } }));
        expect(reloaded).toHaveBeenCalledTimes(1);
    });

    test.each(["component_acceptance_changed", "review_completion_source_changed", "component_source_event_stale", "order_cancelled"])("guard %s is visible and invalidates the old preview", async (code) => {
        await render(); await click("تحديث واستعادة للمراجعة");
        api.post.mockRejectedValueOnce({ response: { data: { detail: { code, message: "رفضت المراجعة" } } } });
        await click("تأكيد الاستعادة للمراجعة");
        expect(container.querySelector('[role="alert"]').textContent).toContain(code);
        expect(button("تأكيد الاستعادة للمراجعة")).toBeUndefined();
        expect(reloaded).not.toHaveBeenCalled();
    });

    test("expired preview cannot be submitted", async () => {
        api.post.mockResolvedValueOnce({ data: { ...preview(), expires_at: "2000-01-01T00:00:00Z" } });
        await render(); await click("تحديث واستعادة للمراجعة"); await click("تأكيد الاستعادة للمراجعة");
        expect(api.post).toHaveBeenCalledTimes(1);
        expect(container.textContent).toContain("انتهت صلاحية المعاينة");
    });

    test("no eligible candidates exposes no action", async () => {
        api.get.mockResolvedValue({ data: { items: [] } }); await render();
        expect(container.textContent).toBe(""); expect(api.post).not.toHaveBeenCalled();
    });

    test("mismatched response identity cannot be approved", async () => {
        api.post.mockResolvedValueOnce({ data: { ...preview(), old_operation_id: "different-operation" } });
        await render(); await click("تحديث واستعادة للمراجعة");
        expect(button("تأكيد الاستعادة للمراجعة")).toBeUndefined();
        expect(container.textContent).toContain("معاينة الطلب غير مكتملة");
    });

    test.each(["network", "in_progress", "unavailable"])("%s preserves the same approval for confirmation retry", async (failure) => {
        await render(); await click("تحديث واستعادة للمراجعة");
        const originalApproval = preview().approval_hash;
        api.post.mockRejectedValueOnce(failure === "network" ? new Error("Network Error") : {
            response: { status: failure === "unavailable" ? 503 : 409, data: { detail: { code: failure === "in_progress" ? "review_completion_in_progress" : "service_unavailable" } } },
        });
        await click("تأكيد الاستعادة للمراجعة");
        expect(container.textContent).toContain("قد تكون العملية قيد الإكمال");
        expect(button("تحديث واستعادة للمراجعة").disabled).toBe(true);
        expect(reloaded).not.toHaveBeenCalled();
        await click("إعادة التحقق من الاستعادة");
        expect(api.post).toHaveBeenCalledTimes(3);
        expect(api.post.mock.calls[1]).toEqual(api.post.mock.calls[2]);
        expect(api.post.mock.calls[2][1].approval_hash).toBe(originalApproval);
        expect(reloaded).toHaveBeenCalledTimes(1);
    });

    test("reviewer metadata is not displayed as business approval facts", async () => {
        const current = preview();
        current.preview.items[0] = { name: "خدمة التغليف", reviewed_by: "internal-reviewer", reviewed_at: "internal-time", route_source: "internal-routing", direct_assembly_piece_ids: ["internal-piece"] };
        current.preview.components = { accepted: true, generation: 987, items: [{ name: "مكون حالي", _id: "internal-component" }] };
        api.post.mockResolvedValueOnce({ data: current });
        await render(); await click("تحديث واستعادة للمراجعة");
        expect(container.textContent).toContain("خدمة التغليف");
        expect(container.textContent).toContain("مكون حالي");
        expect(container.textContent).not.toContain("internal-");
        expect(container.textContent).not.toContain("987");
    });

    test("actual component decision preview displays required services and order specifications", async () => {
        const current = preview();
        current.preview.components = { accepted: true, items: [{
            name: "منتج يحتاج تغليف", sku: "COMP-SKU", quantity: 3,
            forcing_services: ["التغليف الخاص"], requires_preparation: true,
            order_specifications: [{ name: "النقش", value: "نص العميل الحالي" }],
        }] };
        api.post.mockResolvedValueOnce({ data: current });
        await render(); await click("تحديث واستعادة للمراجعة");
        for (const fact of ["منتج يحتاج تغليف", "COMP-SKU", "الخدمات المطلوبة", "التغليف الخاص", "يحتاج إلى تجهيز", "مواصفات الطلب", "النقش", "نص العميل الحالي"]) expect(container.textContent).toContain(fact);
    });

    test.each([
        ["component_plan_reapproval_required", "تحتاج الخطة إلى موافقة صريحة جديدة"],
        ["manual_review_recovery_authoritative_mismatch", "لا تطابق البيانات الحالية من سلة"],
        ["manual_review_recovery_order_ineligible", "الطلب لم يعد مؤهلًا للمراجعة"],
        ["manual_review_recovery_status_changed", "تغيّرت حالة الطلب في سلة"],
    ])("%s explains the blocked recovery in Arabic", async (code, explanation) => {
        await render(); await click("تحديث واستعادة للمراجعة");
        api.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code } } } });
        await click("تأكيد الاستعادة للمراجعة");
        expect(container.querySelector('[role="alert"]').textContent).toContain(explanation);
        expect(button("تأكيد الاستعادة للمراجعة")).toBeUndefined();
        expect(reloaded).not.toHaveBeenCalled();
    });
});
