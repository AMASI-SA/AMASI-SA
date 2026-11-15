import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import DashboardAdsAccountRow from "./DashboardAdsAccountRow";

const day = "2026-11-15";
function sampleStats() {
    const profit = { spendSar: 100, knownSpendSar: 100, nativeSpend: 26.666667,
        currency: "USD", pieces: 5, productCost: 150, shippingCost: 30,
        paymentFees: 20, totalCost: 200, netProfit: 200, roas: 5,
        complete: true, provisional: true };
    const base = { orders: 3, sales: 500, cpa: 100 / 3, knownOrders: 3, knownSales: 500,
        partial: false, profit };
    return { ...base, segments: [
        { ...base, id: "morning", status: "completed", startAt: `${day}T00:00:00+03:00`,
            endAt: `${day}T12:00:00+03:00`, cutoffAt: `${day}T12:00:00+03:00` },
        { ...base, id: "afternoon", status: "active", startAt: `${day}T12:00:00+03:00`,
            endAt: "2026-11-16T00:00:00+03:00", cutoffAt: `${day}T15:30:00+03:00` },
    ] };
}

let root, host;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(() => {
    act(() => root.unmount());
    host.remove();
    jest.useRealTimers();
});

function renderRow(stats = sampleStats(), props = {}) {
    act(() => root.render(<table><tbody><DashboardAdsAccountRow label="سناب الدولار" stats={stats}
        fromDate={day} toDate={day} {...props}>
        <th>سناب الدولار</th><td>100.00</td><td>500.00</td><td>3</td><td>33.33</td>
    </DashboardAdsAccountRow></tbody></table>));
    return host.querySelector("tbody tr");
}
const details = () => document.querySelector('[data-testid="dashboard-ads-account-tooltip"]');
const enter = (element) => act(() => element.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
const click = (element) => act(() => element.dispatchEvent(new MouseEvent("click", { bubbles: true })));

test.each([0, 1, 2, 3, 4])("hovering table cell %s opens the two Riyadh periods and daily totals", (index) => {
    const row = renderRow();
    expect(details()).toBeNull();
    enter(row.children[index]);
    const popup = details();
    expect(popup.textContent).toContain("الفترة الصباحية");
    expect(popup.textContent).toContain("الفترة المسائية");
    expect(popup.textContent).toContain("00:00 – 12:00");
    expect(popup.textContent).toContain("12:00 – 15:30");
    expect(popup.textContent).toContain("إجمالي اليوم");
    expect(popup.textContent).toContain("USD 26.67");
    expect(popup.textContent).toContain("عمولات طرق الدفع");
    expect(popup.textContent).toContain("صافي الأرباح");
    expect(popup.textContent).not.toMatch(/التشغيلية|التشغيل|عمولة البنك/);
    expect(row.getAttribute("aria-expanded")).toBe("true");
});

test("the afternoon remains an empty card before noon, even with a malformed zero payload", () => {
    const stats = sampleStats();
    stats.segments[0].cutoffAt = `${day}T03:00:00+03:00`;
    stats.segments[0].status = "active";
    stats.segments[1].status = "not_started";
    stats.segments[1].orders = stats.segments[1].sales = 0;
    const row = renderRow(stats);
    enter(row.children[3]);
    const afternoon = details().querySelector('[data-testid="dashboard-ads-period-afternoon"]');
    expect(afternoon.textContent).toContain("تبدأ عند 12 ظهرًا");
    expect(afternoon.querySelector("dl")).toBeNull();
    expect(afternoon.textContent).not.toContain("0.00");
    expect(details().textContent).toContain("00:00 – 03:00");
});

test("missing profit stays unknown while explicitly known partial costs are labeled", () => {
    const stats = sampleStats();
    stats.profit = { ...stats.profit, complete: false, netProfit: null, totalCost: null, knownTotalCost: 120 };
    stats.segments[0].profit = stats.profit;
    stats.segments[0].status = "partial";
    enter(renderRow(stats));
    const summary = details().querySelector('[data-testid="dashboard-ads-daily-summary"]');
    expect(summary.textContent).toContain("120.00 ر.سالمتاح");
    const profit = [...summary.querySelectorAll("dl > div")].find((row) => row.textContent.includes("صافي الأرباح"));
    expect(profit.querySelector("dd").textContent).toBe("—");
    expect(profit.textContent).not.toContain("0.00");
});

test("midnight keeps the morning's noon boundary and source quantities retain their precision", () => {
    const stats = sampleStats();
    stats.segments[0].status = "not_started";
    stats.segments[0].cutoffAt = `${day}T00:00:00+03:00`;
    stats.segments[1].status = "not_started";
    stats.profit.pieces = 1.5;
    enter(renderRow(stats));
    const morning = details().querySelector('[data-testid="dashboard-ads-period-morning"]');
    expect(morning.textContent).toContain("00:00 – 12:00");
    expect(morning.querySelector("dl")).toBeNull();
    expect(details().querySelector('[data-testid="dashboard-ads-daily-summary"]').textContent).toContain("القطع 1.5");
});

test("the pointer can move into the hover card to read and scroll it, and Escape dismisses it", () => {
    const row = renderRow();
    enter(row);
    const popup = details();
    act(() => row.dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: popup })));
    enter(popup);
    act(() => jest.advanceTimersByTime(200));
    expect(details()).toBe(popup);
    act(() => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(details()).toBeNull();
});

test("touch click pins the card and keyboard navigation can reach and close the dialog", () => {
    const row = renderRow();
    click(row.children[1]);
    act(() => row.dispatchEvent(new MouseEvent("mouseout", { bubbles: true })));
    act(() => jest.advanceTimersByTime(200));
    expect(details()).not.toBeNull();
    act(() => row.focus());
    act(() => row.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true })));
    expect(document.activeElement).toBe(details());
    act(() => details().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(details()).toBeNull();
    expect(document.activeElement).toBe(row);
});

test("changing the selected date closes the previous period's pinned card", () => {
    click(renderRow());
    expect(details()).not.toBeNull();
    renderRow(sampleStats(), { fromDate: "2026-11-14", toDate: "2026-11-14" });
    expect(details()).toBeNull();
});

test("moving to another account replaces the pinned account rather than stacking dialogs", () => {
    function Rows() {
        const [active, setActive] = useState(null);
        return <table><tbody>{["الأول", "الثاني"].map((label) => <DashboardAdsAccountRow key={label}
            label={label} stats={sampleStats()} fromDate={day} toDate={day}
            active={active === label} onActivate={() => setActive(label)}><td>{label}</td></DashboardAdsAccountRow>)}</tbody></table>;
    }
    act(() => root.render(<Rows />));
    const rows = host.querySelectorAll("tbody tr");
    click(rows[0]);
    enter(rows[1]);
    expect(document.querySelectorAll('[data-testid="dashboard-ads-account-tooltip"]')).toHaveLength(1);
    expect(details().querySelector("h3").textContent).toBe("الثاني");
});
