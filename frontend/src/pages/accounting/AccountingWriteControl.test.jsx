import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import AccountingWriteControl from "./AccountingWriteControl";
jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn() }));
let root, node;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
});
afterEach(() => { act(() => root.unmount()); node.remove(); jest.clearAllMocks(); });
test("viewer sees paused state while no control or replay is available", async () => {
    api.get.mockResolvedValue({data:{paused:true,can_manage:false,pending_events:2}});
    await act(async () => root.render(<AccountingWriteControl />));
    expect(node.textContent).toContain("القراءة متاحة");
    expect(node.querySelector("input")).toBeNull();
    expect(node.querySelector("button")).toBeNull();
    expect(api.put).not.toHaveBeenCalled();
});
test("owner must give a reason and replay remains disabled while paused", async () => {
    api.get.mockResolvedValue({data:{paused:true,can_manage:true,revision:3,pending_events:2}});
    await act(async () => root.render(<AccountingWriteControl />));
    const buttons = [...node.querySelectorAll("button")];
    expect(buttons.find(b => b.textContent.includes("استئناف")).disabled).toBe(true);
    expect(buttons.find(b => b.textContent.includes("50")).disabled).toBe(true);
});
test("state failure is explicit and offers no speculative pause/resume", async () => {
    api.get.mockRejectedValue(new Error("offline"));
    await act(async () => root.render(<AccountingWriteControl />));
    expect(node.querySelector('[role="alert"]').textContent).toContain("تعذر التحقق");
    expect(node.querySelector("button")).toBeNull();
});
test("owner resumes with the observed revision then explicitly replays saved events", async () => {
    api.get.mockResolvedValue({data:{paused:true,can_manage:true,revision:3,pending_events:2}});
    api.put.mockResolvedValue({data:{paused:false,revision:4}});
    api.post.mockResolvedValue({data:{processed:2,pending_events:0}});
    await act(async () => root.render(<AccountingWriteControl />));
    const input = node.querySelector("input");
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,"review complete");
        input.dispatchEvent(new Event("input",{bubbles:true}));
    });
    api.get.mockResolvedValue({data:{paused:false,can_manage:true,revision:4,pending_events:2}});
    await act(async () => [...node.querySelectorAll("button")].find(b=>b.textContent.includes("استئناف")).click());
    expect(api.put).toHaveBeenCalledWith(expect.stringContaining("write-control"),{
        paused:false,revision:3,reason:"review complete"});
    expect(api.post).not.toHaveBeenCalled();
    api.get.mockResolvedValue({data:{paused:false,can_manage:true,revision:4,pending_events:0}});
    await act(async () => [...node.querySelectorAll("button")].find(b=>b.textContent.includes("50")).click());
    expect(api.post).toHaveBeenCalledWith(expect.stringContaining("write-control/replay"));
    expect(node.textContent).toContain("تمت معالجة 2");
});
