import React, { act } from "react";
import { createRoot } from "react-dom/client";
import PaginatedReportTable from "./PaginatedReportTable";

global.IS_REACT_ACT_ENVIRONMENT = true;
let host, root;
const records = count => Array.from({ length: count }, (_, index) => ({ id: index + 1, name: `صف ${index + 1}` }));
const render = async props => act(async () => root.render(
    <PaginatedReportTable label="جدول التجربة" rows={records(27)} {...props}>{rows => <table>
        <tbody>{rows.map(row => <tr key={row.id}><td>{row.name}</td></tr>)}</tbody>
        <tfoot><tr><td>9999.99</td></tr></tfoot>
    </table>}</PaginatedReportTable>
));
const pageRows = () => [...host.querySelectorAll("tbody tr")].map(row => row.textContent);
const next = () => host.querySelector('[aria-label="الصفحة التالية — جدول التجربة"]');
const previous = () => host.querySelector('[aria-label="الصفحة السابقة — جدول التجربة"]');

beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test("13-row pages have accessible arrows below the table and preserve the full total", async () => {
    await render();
    expect(pageRows()).toHaveLength(13);
    expect(pageRows()[0]).toBe("صف 1");
    expect(previous().disabled).toBe(true);
    const table = host.querySelector("table"), controls = host.querySelector("nav");
    expect(table.compareDocumentPosition(controls) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await act(async () => next().click());
    expect(pageRows()).toHaveLength(13);
    expect(pageRows()[0]).toBe("صف 14");
    expect(host.querySelector("tfoot").textContent).toBe("9999.99");
    await act(async () => next().click());
    expect(pageRows()).toEqual(["صف 27"]);
    expect(next().disabled).toBe(true);
    expect(controls.textContent).toContain("الصفحة 3 من 3");
    await act(async () => previous().click());
    expect(pageRows()[0]).toBe("صف 14");
});

test("10-row exception resets for a changed filter and clamps when records shrink", async () => {
    await render({ pageSize: 10, resetKey: "all" });
    expect(pageRows()).toHaveLength(10);
    await act(async () => next().click());
    expect(pageRows()[0]).toBe("صف 11");
    await render({ pageSize: 10, resetKey: "selected", rows: records(14) });
    expect(pageRows()[0]).toBe("صف 1");
    await act(async () => next().click());
    await render({ pageSize: 10, resetKey: "selected", rows: records(3) });
    expect(pageRows()).toEqual(["صف 1", "صف 2", "صف 3"]);
    expect(previous().disabled).toBe(true);
    expect(next().disabled).toBe(true);
});

test("print expansion preserves the selected page and empty results disable both arrows", async () => {
    await render();
    await act(async () => next().click());
    await render({ showAll: true });
    expect(pageRows()).toHaveLength(27);
    await render({ showAll: false });
    expect(pageRows()).toHaveLength(13);
    expect(pageRows()[0]).toBe("صف 14");
    await render({ rows: [] });
    expect(pageRows()).toHaveLength(0);
    expect(previous().disabled).toBe(true);
    expect(next().disabled).toBe(true);
    expect(host.querySelector("nav").textContent).toContain("عرض 0–0 من 0");
});
