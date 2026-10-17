import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import DashboardPanel from "./DashboardPanel";

let host;
let root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(() => {
    act(() => root.unmount());
    host.remove();
    delete global.IS_REACT_ACT_ENVIRONMENT;
});

function StatefulBody() {
    const [count, setCount] = useState(0);
    return <button onClick={() => setCount(count + 1)}>تفاصيل {count}</button>;
}

test("collapsing keeps the header and totals visible, preserves body state, and does not fold another card", () => {
    act(() => root.render(<>
        <DashboardPanel title="الطلبات"><div><h2>أحدث الطلبات</h2><span>32 طلب · متوسط 266.21 ر.س</span></div><StatefulBody /></DashboardPanel>
        <DashboardPanel title="الأرباح"><div><h2>الأرباح</h2></div><p>صافي الأرباح</p></DashboardPanel>
    </>));
    const [orders, profit] = host.querySelectorAll("section");
    const toggle = orders.querySelector("[aria-controls]");
    const body = document.getElementById(toggle.getAttribute("aria-controls"));
    act(() => body.querySelector("button").click());
    act(() => toggle.click());
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-label")).toBe("فتح بطاقة الطلبات");
    expect(body.hidden).toBe(true);
    expect(orders.querySelector("h2").closest("[hidden]")).toBeNull();
    expect(orders.firstElementChild.textContent).toContain("32 طلب · متوسط 266.21 ر.س");
    expect(profit.querySelector("[aria-expanded]").getAttribute("aria-expanded")).toBe("true");
    act(() => toggle.click());
    expect(body.hidden).toBe(false);
    expect(body.textContent).toBe("تفاصيل 1");
});

test("header actions are independent and updated data does not reopen a collapsed card", () => {
    const refresh = jest.fn();
    const card = (amount) => <DashboardPanel title="الإعلانات"><header><h2>الإعلانات</h2><button onClick={refresh}>تحديث</button></header><p>{amount}</p></DashboardPanel>;
    act(() => root.render(card(100)));
    act(() => host.querySelector("header button").click());
    expect(refresh).toHaveBeenCalledTimes(1);
    const toggle = host.querySelector("[aria-controls]");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    act(() => toggle.click());
    act(() => root.render(card(200)));
    expect(host.querySelector("[hidden]").textContent).toBe("200");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
});

test("shared cards outside the dashboard keep their original section structure", () => {
    act(() => root.render(<DashboardPanel data-testid="plain"><header>عنوان</header><p>محتوى</p></DashboardPanel>));
    expect(host.querySelector("section").children).toHaveLength(2);
    expect(host.querySelector("[aria-controls]")).toBeNull();
});
