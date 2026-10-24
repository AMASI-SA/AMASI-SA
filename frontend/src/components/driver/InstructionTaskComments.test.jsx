import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import InstructionTaskComments from "./InstructionTaskComments";
jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
test("comment retries reuse request identity and never call execution or release", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(crypto, "randomUUID", { configurable: true, value: () => "request-1" });
  const node = document.createElement("div"), root = createRoot(node); document.body.appendChild(node);
  api.get.mockResolvedValue({ data: { items: [] } }); api.post.mockRejectedValueOnce(new Error("response lost")).mockResolvedValue({ data: {} });
  try {
    await act(async () => root.render(<InstructionTaskComments instructionId="instruction-1" taskId="task-1" />));
    await act(async () => { const input = node.querySelector("textarea"); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "هل المطلوب المقاس الجديد؟"); input.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(async () => node.querySelector("button").click());
    expect(node.textContent).toContain("دون تكراره");
    await act(async () => node.querySelector("button").click());
    const expected = ["/order-tracking-notes/instructions/instruction-1/tasks/task-1/comments", { body: "هل المطلوب المقاس الجديد؟", client_request_id: "request-1" }];
    expect(api.post.mock.calls).toEqual([expected, expected]);
    expect(node.querySelector("textarea").value).toBe("");
  } finally { act(() => root.unmount()); node.remove(); }
});
