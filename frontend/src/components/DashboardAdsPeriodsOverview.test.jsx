import React, { act } from "react";
import { createRoot } from "react-dom/client";
import DashboardAdsPeriodsOverview from "./DashboardAdsPeriodsOverview";

const DAY = "2026-11-16";
function segment(id, overrides = {}) {
    const morning = id === "morning";
    return {
        id, status: morning ? "completed" : "active", orders: 2, knownOrders: 2,
        sales: 300, knownSales: 300, cpa: 25,
        startAt: `${DAY}T${morning ? "00" : "12"}:00:00+03:00`,
        endAt: morning ? `${DAY}T12:00:00+03:00` : "2026-11-17T00:00:00+03:00",
        cutoffAt: `${DAY}T${morning ? "12" : "16"}:00:00+03:00`,
        profit: { adSpendApplicable: true, currency: "SAR", spendSar: 50, knownSpendSar: 50,
            totalCost: 120, knownTotalCost: 120, roas: 6, netProfit: 130, complete: true, provisional: false },
        ...overrides,
    };
}
function stats() {
    return { orders: 4, knownOrders: 4, sales: 600, knownSales: 600, cpa: 25,
        profit: { adSpendApplicable: true, currency: "SAR", spendSar: 100, knownSpendSar: 100,
            totalCost: 240, knownTotalCost: 240, roas: 6, netProfit: 260, complete: true, provisional: false },
        segments: [segment("morning"), segment("afternoon")] };
}
function fixture() {
    const labels = ["سناب الدولار", "سناب السعودي", "جوجل", "تيك توك", "ميتا", "مباشر / بدون مصدر"];
    const records = labels.map((label, index) => ({ key: `row-${index}`, label, color: "#eab308", stats: stats() }));
    Object.assign(records[0].stats.segments[0].profit, { currency: "USD", nativeSpend: 13.333333 });
    for (const row of [records[5].stats, ...records[5].stats.segments]) {
        row.cpa = null;
        Object.assign(row.profit, { adSpendApplicable: false, spendSar: null, knownSpendSar: null,
            nativeSpend: null, netProfit: 180 });
    }
    const total = stats();
    Object.assign(total, { orders: 99, sales: 9999, cpa: 333 / 99 });
    Object.assign(total.profit, { spendSar: 333, totalCost: 444.01, roas: 30.03, netProfit: 9221.99 });
    Object.assign(total.segments[0], { orders: 30, sales: 5000, cpa: 123 / 30 });
    Object.assign(total.segments[0].profit, { spendSar: 123, totalCost: 200, roas: 40.65, netProfit: 4677 });
    Object.assign(total.segments[1], { orders: 69, sales: 4999, cpa: 210 / 69 });
    Object.assign(total.segments[1].profit, { spendSar: 210, totalCost: 244, roas: 23.8, netProfit: 4545 });
    return { records, total, fromDate: DAY, toDate: DAY, contextKey: "all" };
}

let root, host, originalFetch;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    originalFetch = globalThis.fetch;
    globalThis.fetch = jest.fn(() => { throw new Error("Overview must not fetch"); });
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(() => {
    act(() => root.unmount());
    host.remove();
    jest.useRealTimers();
    if (originalFetch === undefined) delete globalThis.fetch;
    else globalThis.fetch = originalFetch;
});
function render(props) {
    act(() => root.render(<table><tfoot><DashboardAdsPeriodsOverview {...props} data-testid="total-trigger">
        <th>الإجمالي</th><td>333.00</td><td>9,999.00</td><td>99</td><td>3.36</td><td>30.03</td><td>9,221.99</td>
    </DashboardAdsPeriodsOverview></tfoot></table>));
    return host.querySelector("tfoot > tr");
}
const click = (node) => act(() => node.dispatchEvent(new MouseEvent("click", { bubbles: true })));
const popup = () => document.querySelector('[data-testid="dashboard-ads-periods-overview"]');
const period = (id) => popup().querySelector(`[data-testid="dashboard-ads-overview-${id}"]`);
const row = (index = 0, id = "morning") => period(id).querySelector(`[data-overview-row="row-${index}"]`);
const cell = (element, metric) => element.querySelector(`[data-metric="${metric}"]`);

test("footer cells open two eight-column tables with all accounts and authoritative period/day totals", () => {
    const props = fixture();
    const trigger = render(props);
    click(trigger.cells[trigger.cells.length - 1]);
    expect(popup().getAttribute("role")).toBe("dialog");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    for (const id of ["morning", "afternoon"]) {
        expect(period(id).querySelectorAll("thead th")).toHaveLength(8);
        expect(period(id).querySelectorAll("tbody > tr")).toHaveLength(6);
        expect(period(id).querySelector("thead").textContent).toContain("العائد الإعلاني×");
        props.records.forEach((record) => expect(period(id).textContent).toContain(record.label));
    }
    expect(period("morning").textContent).toContain("00:00 – 12:00");
    expect(period("afternoon").textContent).toContain("12:00 – 16:00");
    expect(cell(row(), "spend").textContent).toContain("USD 13.33");
    expect(cell(row(), "spend").textContent).toContain("50.00 ر.س");
    expect(cell(row(), "costs").textContent).toBe("120.00");
    expect(cell(row(), "roas").textContent).toBe("6.00");
    expect(cell(period("morning").querySelector("tfoot tr"), "net").textContent).toBe("4,677.00");
    expect(cell(period("morning").querySelector("tfoot tr"), "roas").textContent).toBe("40.65");
    const day = popup().querySelector('[data-testid="dashboard-ads-overview-total"]');
    expect(cell(day, "net").textContent).toContain("9,221.99");
    expect(cell(day, "orders").textContent).toContain("99");
    expect(cell(day, "costs").querySelector("dd").textContent).toBe("444.01");
    expect(day.querySelectorAll('[data-metric="costs"]')).toHaveLength(1);
    expect(cell(day, "roas").querySelector("dd").textContent).toBe("30.03");
    expect(popup().textContent).not.toMatch(/متوسط التكاليف|تكلفة المنتجات|تكلفة الشحن|عمولات طرق الدفع/);
    expect(popup().textContent).toContain("الأرباح قبل المصروفات التشغيلية");
    expect(globalThis.fetch).not.toHaveBeenCalled();
});

test("a mapped-order contribution is labelled partial and cannot replace canonical period totals", () => {
    const props = fixture();
    Object.assign(props.records[0].stats.segments[0].profit, { complete: false, netProfit: null,
        assignedOrdersProfitAvailable: true, assignedOrdersProfit: 0 });
    click(render(props));
    expect(cell(row(), "net").textContent).toBe("0.00جزئي: الطلبات المسندة");
    expect(period("morning").querySelector("thead").textContent).toContain("الربح قبل التشغيل");
    expect(cell(period("morning").querySelector("tfoot tr"), "net").textContent).toBe("4,677.00");
    expect(globalThis.fetch).not.toHaveBeenCalled();
});

test("unassigned contribution uses its own label and never changes the canonical half-day total", () => {
    const props = fixture();
    props.records[0].label = "سناب غير محدد";
    const segment = props.records[0].stats.segments[0];
    Object.assign(segment, { accountUnassigned: true, cpa: null });
    Object.assign(segment.profit, { complete: false, netProfit: null, spendSar: null, knownSpendSar: null,
        nativeSpend: null, bankFees: null, knownBankFees: null, roas: null,
        unassignedOrdersContributionAvailable: true, unassignedOrdersContribution: 0 });
    const before = JSON.stringify(props);
    click(render(props));
    expect(cell(row(), "net").textContent).toBe("0.00مساهمة الطلبات");
    for (const key of ["spend", "cpa", "roas"]) expect(cell(row(), key).textContent).toBe("—");
    expect(cell(period("morning").querySelector("tfoot tr"), "net").textContent).toBe("4,677.00");
    expect(JSON.stringify(props)).toBe(before);
    expect(globalThis.fetch).not.toHaveBeenCalled();
});

test.each([
    [288.53, 288.53, "288.53"],
    [0, 0, "0.00"],
    [null, null, "—"],
    [undefined, undefined, "—"],
    [null, 280.16, "280.16المتاح"],
])("daily bank commissions use the existing total without adding a comparison column (%s, known %s)", (bankFees, knownBankFees, expected) => {
    const props = fixture();
    Object.assign(props.total.profit, { bankFees, knownBankFees });
    const before = JSON.parse(JSON.stringify(props));
    click(render(props));
    const day = popup().querySelector('[data-testid="dashboard-ads-overview-total"]');
    const fees = cell(day, "bankFees");
    expect(fees.querySelector("dt").textContent).toBe("منها عمولات البنك (ر.س)");
    expect(fees.querySelector("dd").textContent).toBe(expected);
    expect(cell(day, "costs").querySelector("dd").textContent).toBe("444.01");
    expect(cell(day, "net").querySelector("dd").textContent).toBe("9,221.99");
    for (const id of ["morning", "afternoon"]) {
        expect(period(id).querySelectorAll("thead th")).toHaveLength(8);
        expect(period(id).querySelector('[data-metric="bankFees"]')).toBeNull();
    }
    expect(popup().textContent).toContain("عمولات البنك المعروضة ضمن إجمالي التكاليف ومخصومة مرة واحدة");
    expect(popup().textContent).toContain("الأرباح قبل المصروفات التشغيلية");
    expect(JSON.parse(JSON.stringify(props))).toEqual(before);
    expect(globalThis.fetch).not.toHaveBeenCalled();
});

test("direct advertising, CPA, and injected ROAS remain unallocated while total costs and net remain visible", () => {
    click(render(fixture()));
    expect(cell(row(5), "spend").textContent).toBe("—");
    expect(cell(row(5), "cpa").textContent).toBe("—");
    expect(cell(row(5), "roas").textContent).toBe("—");
    expect(cell(row(5), "costs").textContent).toBe("120.00");
    expect(cell(row(5), "net").textContent).toBe("180.00");
});

test.each([[130, true, false, "130.00"], [-20, true, false, "-20.00"], [0, true, false, "0.00"],
    [130, true, true, "130.00تقديري"], [130, false, true, "—"], [null, true, false, "—"]])(
    "net uses complete signed canonical values (%s, complete=%s, provisional=%s)", (netProfit, complete, provisional, expected) => {
        const props = fixture();
        Object.assign(props.records[0].stats.segments[0].profit, { netProfit, complete, provisional });
        click(render(props));
        expect(cell(row(), "net").textContent).toBe(expected);
    },
);

test("total costs remain independent of ad spend and orders, and partial costs are explicitly labeled", () => {
    const props = fixture();
    Object.assign(props.records[0].stats.segments[0].profit, { spendSar: null, knownSpendSar: null, netProfit: null, complete: false });
    props.records[1].stats.segments[0].orders = 0;
    Object.assign(props.records[1].stats.segments[0].profit, { totalCost: 0, knownTotalCost: 0 });
    Object.assign(props.records[2].stats.segments[0].profit, { totalCost: null, knownTotalCost: 120 });
    Object.assign(props.records[3].stats.segments[0], { orders: null, knownOrders: 2 });
    Object.assign(props.records[4].stats.segments[0].profit, { totalCost: null, knownTotalCost: null });
    click(render(props));
    expect(cell(row(0), "costs").textContent).toBe("120.00");
    expect(cell(row(0), "net").textContent).toBe("—");
    expect(cell(row(1), "costs").textContent).toBe("0.00");
    expect(cell(row(2), "costs").textContent).toBe("120.00المتاح");
    expect(cell(row(3), "costs").textContent).toBe("120.00");
    expect(cell(row(4), "costs").textContent).toBe("—");
});

test.each([[12.34, "12.34"], [0, "0.00"], [null, "—"], [undefined, "—"],
    [-1, "—"], [NaN, "—"], [Infinity, "—"], ["6", "—"]])(
    "ROAS uses only its canonical value, independent of profit completeness (%s)", (roas, expected) => {
        const props = fixture();
        Object.assign(props.records[0].stats.segments[0].profit, { roas, complete: false, netProfit: null });
        click(render(props));
        expect(cell(row(), "roas").textContent).toBe(expected);
        expect(cell(row(), "net").textContent).toBe("—");
    },
);

test("future afternoon is empty even if stale input contains numeric zero metrics", () => {
    const props = fixture();
    for (const stats of [props.total, ...props.records.map((record) => record.stats)]) {
        Object.assign(stats.segments[0], { status: "active", cutoffAt: `${DAY}T10:30:00+03:00` });
        Object.assign(stats.segments[1], { status: "not_started", cutoffAt: `${DAY}T12:00:00+03:00`, orders: 0, sales: 0 });
        Object.assign(stats.segments[1].profit, { spendSar: 0, totalCost: 0, netProfit: 0, complete: true });
    }
    click(render(props));
    expect(period("morning").textContent).toContain("00:00 – 10:30");
    expect(period("afternoon").textContent).toContain("تبدأ عند 12 ظهرًا");
    expect(period("afternoon").querySelector("tfoot")).toBeNull();
    expect(period("afternoon").querySelectorAll("tbody [data-metric]")).toHaveLength(0);
    expect(period("afternoon").querySelector("tbody td").colSpan).toBe(8);
    expect(period("afternoon").textContent).not.toContain("0.00");
});

test("an incomplete scoped total remains unknown even when every displayed row has a profit", () => {
    const props = fixture();
    Object.assign(props.total.profit, { complete: false, netProfit: null, roas: null });
    Object.assign(props.total.segments[1].profit, { complete: false, netProfit: null, roas: null });
    click(render(props));
    expect(cell(row(), "net").textContent).toBe("130.00");
    expect(cell(period("afternoon").querySelector("tfoot tr"), "net").textContent).toBe("—");
    expect(cell(period("afternoon").querySelector("tfoot tr"), "roas").textContent).toBe("—");
    const day = popup().querySelector('[data-testid="dashboard-ads-overview-total"]');
    expect(cell(day, "net").querySelector("dd").textContent).toBe("—");
    expect(cell(day, "roas").querySelector("dd").textContent).toBe("—");
    expect(cell(day, "sales").textContent).toContain("9,999.00");
});

test("a date range shows its daily source summary without pretending it is two half-days", () => {
    click(render({ ...fixture(), toDate: "2026-11-17" }));
    expect(popup().querySelector('[data-testid="dashboard-ads-overview-unavailable"]')).not.toBeNull();
    expect(popup().querySelectorAll("table")).toHaveLength(0);
    expect(popup().querySelector('[data-testid="dashboard-ads-overview-total"]').textContent).toContain("إجمالي الفترة");
});

test("click pins, keyboard reaches the dialog, and Escape closes it", () => {
    const trigger = render(fixture());
    click(trigger);
    act(() => trigger.dispatchEvent(new MouseEvent("mouseout", { bubbles: true })));
    act(() => jest.advanceTimersByTime(200));
    expect(popup()).not.toBeNull();
    act(() => trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true })));
    expect(document.activeElement).toBe(popup());
    act(() => popup().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(popup()).toBeNull();
    expect(document.activeElement).toBe(trigger);
});

test("hovering every footer cell opens the overview and allows pointer travel into its portal", () => {
    const trigger = render(fixture());
    expect(trigger.cells).toHaveLength(7);
    for (const target of Array.from(trigger.cells)) {
        act(() => target.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: document.body })));
        const dialog = popup();
        expect(dialog).not.toBeNull();
        expect(trigger.getAttribute("aria-expanded")).toBe("true");
        act(() => target.dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: document.body })));
        act(() => jest.advanceTimersByTime(80));
        act(() => dialog.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: document.body })));
        act(() => jest.advanceTimersByTime(200));
        expect(popup()).toBe(dialog);
        act(() => dialog.dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: document.body })));
        act(() => jest.advanceTimersByTime(200));
        expect(popup()).toBeNull();
    }
});

test("hover survives activation rerender with new record wrappers and stable metric identities", () => {
    const props = fixture();
    function ControlledFooter() {
        const [active, setActive] = React.useState(false);
        return <table><tfoot><DashboardAdsPeriodsOverview {...props}
            records={props.records.map((record) => ({ ...record }))}
            active={active} onActivate={() => setActive(true)}>
            <th>الإجمالي</th><td>9,221.99</td>
        </DashboardAdsPeriodsOverview></tfoot></table>;
    }
    act(() => root.render(<ControlledFooter />));
    const target = host.querySelector("tfoot td");
    act(() => target.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: document.body })));
    expect(popup()).not.toBeNull();
    act(() => jest.advanceTimersByTime(200));
    expect(popup()).not.toBeNull();
});

test("stable record wrappers do not close the dialog; filter or snapshot changes do", () => {
    const props = fixture();
    click(render(props));
    render({ ...props, records: props.records.map((record) => ({ ...record })) });
    expect(popup()).not.toBeNull();
    render({ ...props, contextKey: "different-filter" });
    expect(popup()).toBeNull();
    click(render(props));
    render({ ...props, total: { ...props.total } });
    expect(popup()).toBeNull();
});

test("deactivating the footer closes its portal so account details can replace it", () => {
    const props = fixture();
    click(render(props));
    expect(popup()).not.toBeNull();
    render({ ...props, active: false });
    expect(popup()).toBeNull();
});

test("missing inputs keep the overview readable without inventing financial totals", () => {
    click(render({ fromDate: DAY, toDate: DAY }));
    expect(popup()).not.toBeNull();
    expect(period("morning").getAttribute("data-state")).toBe("unavailable");
    const day = popup().querySelector('[data-testid="dashboard-ads-overview-total"]');
    expect(cell(day, "costs").querySelector("dd").textContent).toBe("—");
    expect(cell(day, "net").querySelector("dd").textContent).toBe("—");
});
