import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import OperationalInstructionNotifications from "./OperationalInstructionNotifications";
import { enableOperationalPush } from "../../lib/operationalPush";
jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock("../../lib/operationalPush", () => ({ supportsOperationalPush: () => true, enableOperationalPush: jest.fn(), disableOperationalPush: jest.fn() }));
let node, root;
const detail = { id: "instruction-1", order_number: "123", status: "active", action_type: "delete_product", can_submit_tasks: true, note: "إزالة المنتج", tasks: [{ id: "task-1", product_name: "قميص", status: "pending" }], proposed_changes: [{ field: "المقاس", previous: "M", requested: "L" }] };
beforeEach(() => {
  jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true;
  node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
  api.get.mockImplementation(async (url) => ({ data: url.endsWith("/inbox") ? { items: [{ ...detail, action_label: "إيقاف منتج" }] } : { instruction: detail, capabilities: { can_submit_tasks: true } } }));
  api.post.mockResolvedValue({ data: { ok: true } });
});
afterEach(() => { act(() => root.unmount()); node.remove(); });
async function click(text) { await act(async () => Array.from(node.querySelectorAll("button")).find((button) => button.textContent.includes(text)).click()); }

test.each(["urgent_preparation", "delivery_instruction"])("%s acknowledges without submitting execution", async (action_type) => {
  api.get.mockResolvedValue({data:{instruction:{...detail, action_type}, capabilities:{can_acknowledge:true}}});
  await act(async()=>root.render(<OperationalInstructionNotifications instructionId="instruction-1" detailOnly />));
  await click("الملاحظة واضحة وسيتم تنفيذها");
  expect(api.post.mock.calls.map(([url])=>url)).toEqual(["/order-tracking-notes/instructions/instruction-1/acknowledge"]);
  expect(node.textContent).not.toContain("إرسال تأكيد التنفيذ");
});

test("creator closes cancellation task explicitly, with no order cancellation request",async()=>{
 api.get.mockResolvedValue({data:{instruction:{...detail,status:"waiting_customer_service_approval"}, capabilities:{can_approve_resume:true}}});
 await act(async()=>root.render(<OperationalInstructionNotifications instructionId="instruction-1" detailOnly />));
 expect(node.textContent).toContain("الإلغاء النهائي يتم من سلة");
 const approve=Array.from(node.querySelectorAll('button')).find(b=>b.textContent==='اعتماد التأكيد وإغلاق المهمة');
 expect(approve.disabled).toBe(true);expect(api.post).not.toHaveBeenCalled();
 await act(async()=>Array.from(node.querySelectorAll('input[type="checkbox"]')).at(-1).click());
 await click('اعتماد التأكيد وإغلاق المهمة');
 expect(api.post.mock.calls.map(([url])=>url)).toEqual(['/order-tracking-notes/instructions/instruction-1/approve']);
});

test("noncreator cannot close or approve even with stale instruction flags",async()=>{
 api.get.mockResolvedValue({data:{instruction:{...detail,can_approve_resume:true,can_close_instruction:true},capabilities:{}}});
 await act(async()=>root.render(<OperationalInstructionNotifications instructionId="instruction-1" detailOnly />));
 expect(node.textContent).not.toContain('اعتماد التأكيد وإغلاق المهمة');expect(node.textContent).not.toContain('إنهاء التعليمات بعد الاتفاق');
});
test("reading retains pending task; cancellation requires selected task, note and explicit execution confirmation", async () => {
  await act(async () => root.render(<OperationalInstructionNotifications />));
  expect(enableOperationalPush).not.toHaveBeenCalled();
  await click("إيقاف منتج");
  expect(api.post).not.toHaveBeenCalled();
  expect(node.textContent).toContain("بانتظار تنفيذك"); expect(node.textContent).toContain("السابق: M"); expect(node.textContent).toContain("المطلوب: L");
  const submit = Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "إرسال تأكيد التنفيذ");
  expect(submit.disabled).toBe(true);
  await act(async () => node.querySelectorAll('input[type="checkbox"]')[0].click());
  await act(async () => { const input = node.querySelector('[aria-label="توضيح التنفيذ"]'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "تمت الإزالة فعليًا"); input.dispatchEvent(new Event("input", { bubbles: true })); });
  expect(submit.disabled).toBe(true);
  await act(async () => node.querySelectorAll('input[type="checkbox"]')[1].click());
  await click("إرسال تأكيد التنفيذ");
  expect(api.post).toHaveBeenCalledWith("/order-tracking-notes/instructions/instruction-1/tasks/submit", { task_ids: ["task-1"], note: "تمت الإزالة فعليًا", confirmation: "removed_from_preparation" });
});
test("push registration only happens on explicit user click; errors do not hide in-app feed", async () => {
  enableOperationalPush.mockRejectedValue(new Error("إشعارات الهاتف لم تُفعّل على الخادم بعد."));
  await act(async () => root.render(<OperationalInstructionNotifications />));
  expect(enableOperationalPush).not.toHaveBeenCalled(); await click("تفعيل إشعارات الهاتف");
  expect(enableOperationalPush).toHaveBeenCalledTimes(1); expect(node.textContent).toContain("لم تُفعّل"); expect(node.textContent).toContain("إيقاف منتج");
});
test("lost assignment never retains old details", async () => {
  await act(async () => root.render(<OperationalInstructionNotifications />)); await click("إيقاف منتج");
  api.get.mockRejectedValue(new Error("forbidden")); await click("إيقاف منتج");
  expect(node.querySelector('[aria-label="تفاصيل التعليمات الحالية"]')).toBeNull();
  expect(node.textContent).toContain("قد يكون الإسناد تغيّر");
});

test("general note with a conversation task only acknowledges clarity, never executes", async () => {
  api.get.mockImplementation(async (url) => ({ data: url.endsWith("/inbox") ? { items: [{ id: "instruction-1", action_label: "ملاحظة" }] } : { instruction: { ...detail, action_type: "general" }, capabilities: { can_acknowledge: true, can_submit_tasks: true } } }));
  await act(async () => root.render(<OperationalInstructionNotifications />)); await click("ملاحظة");
  expect(node.textContent).not.toContain("إرسال تأكيد التنفيذ");
  await click("الملاحظة واضحة وسيتم تنفيذها");
  expect(api.post.mock.calls.map(([url]) => url)).toEqual(["/order-tracking-notes/instructions/instruction-1/acknowledge"]);
});

test("scheduled detail cannot execute before arrival even with stale capability", async () => {
  api.get.mockImplementation(async (url) => ({ data: url.endsWith("/inbox") ? { items: [{ id: "instruction-1", action_label: "إيقاف مؤجل" }] } : { instruction: { ...detail, activation_state: "scheduled" }, capabilities: { can_submit_tasks: true, can_claim_task: true } } }));
  await act(async () => root.render(<OperationalInstructionNotifications />)); await click("إيقاف مؤجل");
  expect(node.textContent).toContain("بانتظار الوصول للمرحلة المحددة");
  expect(node.textContent).not.toContain("إرسال تأكيد التنفيذ");
  expect(node.textContent).not.toContain("استلام مسؤولية تنفيذ مهمة المراجعة");
});

test("photo-required task cannot submit until evidence upload succeeds", async () => {
  api.get.mockImplementation(async (url) => ({ data: url.endsWith("/inbox") ? { items: [{ id: "instruction-1", action_label: "إثبات مطلوب" }] } : { instruction: { ...detail, required_action: "upload_photos" }, evidence: [], capabilities: { can_submit_tasks: true, can_submit_evidence: true } } }));
  await act(async () => root.render(<OperationalInstructionNotifications />)); await click("إثبات مطلوب");
  await act(async () => node.querySelectorAll('input[type="checkbox"]')[0].click());
  await act(async () => { const input = node.querySelector('[aria-label="توضيح التنفيذ"]'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "تم التنفيذ"); input.dispatchEvent(new Event("input", { bubbles: true })); });
  await act(async () => node.querySelectorAll('input[type="checkbox"]')[1].click());
  const submit = () => Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "إرسال تأكيد التنفيذ");
  expect(submit().disabled).toBe(true);
  await act(async () => { const input = node.querySelector('[aria-label="صور إثبات التنفيذ"]'); Object.defineProperty(input, "files", { configurable: true, value: [new File(["photo"], "proof.png", { type: "image/png" })] }); input.dispatchEvent(new Event("change", { bubbles: true })); });
  api.post.mockRejectedValueOnce(new Error("upload failed")); await click("رفع صور الإثبات"); expect(submit().disabled).toBe(true);
  api.post.mockResolvedValueOnce({ data: { evidence: [{ id: "e1" }] } }); await click("رفع صور الإثبات");
  await act(async () => node.querySelectorAll('input[type="checkbox"]')[1].click()); expect(submit().disabled).toBe(false);
  const uploads = api.post.mock.calls.filter(([url]) => url.endsWith("/evidence")); expect(uploads).toHaveLength(2); expect(uploads[1][1].getAll("files")).toHaveLength(1);
  expect(api.post.mock.calls.some(([url]) => url.endsWith("/tasks/submit"))).toBe(false);
});

test("inbox uses authoritative active count and completed tab, never event count", async () => {
  api.get.mockImplementation(async (url, config) => ({ data: url.endsWith("/inbox") ? { items: [{ ...detail, id: config.params.status === "active" ? "instruction-1" : "finished-1", action_label: config.params.status === "active" ? "مهمة نشطة" : "مهمة مكتملة", status: config.params.status === "active" ? "active" : "completed" }], active_count: 7, next_cursor: null } : url.endsWith("/me") ? { items: [{ id: "event1", instruction_id: "instruction-1", title: "تحديث" }, { id: "event2", instruction_id: "instruction-1", title: "تحديث" }] } : { instruction: detail, capabilities: {} } }));
  await act(async () => root.render(<OperationalInstructionNotifications />));
  expect(node.querySelector('[aria-label="عدد الطلبات النشطة"]').textContent).toBe("(7)");
  expect(api.get.mock.calls.some(([url]) => url.endsWith("/me"))).toBe(false);
  await click("مهمة نشطة");
  expect(node.querySelector('[aria-label="عدد الطلبات النشطة"]').textContent).toBe("(7)");
  await click("مكتملة");
  expect(api.get).toHaveBeenCalledWith("/order-tracking-notes/instructions/inbox", { params: { status: "completed" } });
  expect(node.textContent).toContain("مهمة مكتملة"); expect(node.textContent).not.toContain("مهمة نشطة");
});
