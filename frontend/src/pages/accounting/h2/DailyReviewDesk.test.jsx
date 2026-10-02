import React, { act } from "react";
import { createRoot } from "react-dom/client";
import DailyReviewDesk from "./DailyReviewDesk";
jest.mock("./AdvertisingPanel", () => () => <div>native advertising detail</div>);
jest.mock("./DriverPanel", () => () => <div>native driver detail</div>);
jest.mock("./ObligationsPanel", () => () => <div>native obligations detail</div>);

test("daily reviews open inline, mount only selected native reader and close without navigation", () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const node = document.createElement("div"); document.body.appendChild(node);
    const root = createRoot(node);
    const click = label => act(() => [...node.querySelectorAll("button")].find(button => button.textContent.includes(label)).click());
    try {
        act(() => root.render(<DailyReviewDesk />));
        expect(node.textContent).not.toContain("native advertising detail");
        click("الإعلانات والصرف اليومي");
        expect(node.textContent).toContain("native advertising detail");
        expect(node.querySelector('[aria-expanded="true"]').getAttribute("aria-controls")).toBe("h2-inline-review");
        click("الشحن والموصلون");
        expect(node.textContent).not.toContain("native advertising detail");
        expect(node.textContent).toContain("native driver detail");
        click("المقدمات والالتزامات");
        expect(node.textContent).toContain("native obligations detail");
        expect(node.querySelectorAll("a")).toHaveLength(0);
        click("إغلاق التفاصيل");
        expect(node.textContent).not.toContain("native obligations detail");
    } finally { act(() => root.unmount()); node.remove(); }
});
