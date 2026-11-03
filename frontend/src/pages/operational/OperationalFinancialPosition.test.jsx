import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { useReactToPrint } from "react-to-print";
import { useOptionalAuth } from "../../context/AuthContext";
import XLSX from "../../lib/safeSpreadsheet";
import { operationalApi } from "./api";
import OperationalFinancialPosition from "./OperationalFinancialPosition";

jest.mock("./api", () => ({ operationalApi: { financialPosition: jest.fn() } }));
jest.mock("../../context/AuthContext", () => ({ useOptionalAuth: jest.fn() }));
jest.mock("react-to-print", () => ({ useReactToPrint: jest.fn() }));
global.IS_REACT_ACT_ENVIRONMENT = true;

function fixture() {
    const columns = [{ key: "name", label: "الجهة", type: "text" }, { key: "receivable", label: "لنا", type: "money", role: "receivable" }, { key: "payable", label: "علينا", type: "money", role: "payable" }];
    const data = {
        tables: [
            { id: "suppliers", title: "الموردون", section: "actual", kind: "actual", complete: true, columns,
                rows: [{ id: "supplier-one", name: "مورد التجربة", receivable: "0.00", payable: "9007199254740993.25" }], totals: { receivable: "0.00", payable: "9007199254740993.25" } },
            { id: "drivers", title: "مناديب المتجر", section: "fulfillment", kind: "actual", complete: true, columns,
                rows: [{ id: "driver-one", name: "مندوب التجربة", receivable: "120.00", payable: "20.00" }], totals: { receivable: "120.00", payable: "20.00" } },
            { id: "products_unreceived", title: "المنتجات غير المستلمة", section: "forecast", kind: "forecast", complete: false,
                columns: [{ key: "name", label: "المنتج", type: "text" }, { key: "quantity", label: "الكمية", type: "count" }, { key: "amount", label: "التكلفة", type: "money" }],
                rows: [{ id: "product-one", name: "منتج التجربة", quantity: 2, amount: null }], totals: { quantity: 2, amount: null }, notes: ["تكلفة المنتج لم تكتمل."] },
        ],
        debt_rows: [{ id: "supplier", name: "الموردون", receivable: "12.34", payable: "56.78" }],
        debt_totals: { receivable: "12.34", payable: "56.78" },
        current_rows: [{ id: "current", label: "صافي الأرصدة الحالية من الخادم", amount: "678.90", emphasis: "total" }],
        forecast_rows: [{ id: "forecast", label: "التغير المتوقع من الخادم", amount: null, reason: "المصدر غير مكتمل." }],
        profit_rows: [{ id: "profit", label: "صافي الربح المتوقع", amount: null, reason: "بيانات الإيراد والضريبة لم تكتمل." }],
        metrics: { forecast_monetary_position: "-55.05", net_profit: null },
        completeness: { actual: true, forecast: false, profit: false }, notes: ["الأرصدة والتوقعات منفصلة."],
        deliveries: { complete: false, notes: ["بانتظار اعتماد إيصال التحويل البنكي."], rows: [
            { id: "cash-order", order_id: "internal-one", order_number: "1001", party_id: "driver-one", party_name: "مندوب التجربة", party_type: "store_driver",
                delivered_at: "2026-11-01T07:30:00Z", payment_method: "cash", collection_destination: "driver_custody", collection_status: "confirmed",
                shipping_confirmed: "20.00", shipping_settled: "0.00", shipping_outstanding: "20.00", collection_amount: "120.00", driver_custody: "120.00", collection_settled: "0.00", collection_outstanding: "120.00", store_collection: null, customer_outstanding: "0.00" },
            { id: "bank-order", order_id: "internal-two", order_number: "1002", party_id: "driver-two", party_name: "مندوب التحويل", party_type: "store_driver",
                delivered_at: "2026-11-01T08:00:00Z", payment_method: "bank_transfer", collection_destination: "store_bank_account_unknown", collection_status: "pending_review",
                shipping_confirmed: "30.00", shipping_settled: "0.00", shipping_outstanding: "30.00", collection_amount: "300.00", driver_custody: null, collection_settled: null, collection_outstanding: null, store_collection: null, customer_outstanding: null },
            { id: "courier-order", order_number: "2001", party_id: "courier-one", party_name: "شركة التجربة", party_type: "courier", payment_method: "cod",
                collection_destination: "courier_receivable", collection_status: "confirmed", shipping_confirmed: "15.00", collection_amount: "200.00", collection_outstanding: "200.00", customer_outstanding: "200.00" },
        ] },
    };
    return {
        schema_version: 1, authority: "operational_balance_report", status: "active", session_scope: "test-owner:test-user", permissions: { reports: true },
        started_at: "2026-10-31T21:00:00Z", as_of: "2026-11-01T09:00:00Z", generated_at: "2026-11-01T09:01:00Z", currency_order: ["SAR", "USD"],
        issues: [{ code: "test-source", message: "بعض أدلة المصدر تحتاج مراجعة." }], dependencies: [{ code: "costs", status: "unavailable", message: "بانتظار اكتمال مصدر تكاليف المنتجات." }],
        currencies: {
            SAR: data,
            USD: { ...data, tables: [{ id: "cash", title: "الصناديق بالدولار", section: "actual", kind: "actual", complete: true, columns: [{ key: "name", label: "الصندوق", type: "text" }, { key: "available", label: "المتاح", type: "money" }], rows: [{ id: "cash-usd", name: "صندوق دولار", available: "777.07" }], totals: { available: "777.07" } }], deliveries: { complete: true, rows: [], notes: [] } },
        },
    };
}

let host, root, print;
const render = async () => { await act(async () => root.render(<OperationalFinancialPosition />)); };
const button = label => [...host.querySelectorAll("button")].find(element => element.textContent.includes(label));
const click = async label => { await act(async () => button(label).click()); };
const field = label => host.querySelector(`[aria-label="${label}"]`);
const change = async (element, value) => {
    await act(async () => {
        const prototype = element.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value);
        element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
};
const visibleOrders = () => [...host.querySelectorAll('[data-testid="financial-delivery-row"]')].map(row => row.cells[0].textContent);

beforeEach(() => {
    jest.clearAllMocks();
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
    useOptionalAuth.mockReturnValue({ user: { id: "test-user", owner_id: "test-owner" }, loading: false, authStatus: "authenticated" });
    operationalApi.financialPosition.mockResolvedValue(fixture());
    print = jest.fn(); useReactToPrint.mockReturnValue(print);
    jest.spyOn(XLSX, "writeFile").mockResolvedValue(undefined);
});

afterEach(() => { act(() => root.unmount()); host.remove(); jest.restoreAllMocks(); });

test("loading cannot expose balances or enable print/export before the permission-scoped response", async () => {
    let resolve;
    operationalApi.financialPosition.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    await render();
    expect(host.textContent).toContain("جارٍ تحميل التقرير");
    expect(host.querySelector('[data-testid="financial-position-content"]')).toBeNull();
    expect(button("طباعة التقرير").disabled).toBe(true);
    expect(button("تصدير Excel").disabled).toBe(true);
    await act(async () => resolve(fixture()));
    expect(host.querySelector('[data-testid="financial-position-content"]')).not.toBeNull();
    expect(button("تصدير Excel").disabled).toBe(false);
});

test("all three sections use server values, exact cents, and explicit missing-source reasons", async () => {
    await render();
    expect(host.textContent).toContain("الأرصدة الفعلية والحسابات");
    expect(host.textContent).toContain("الشحن والتحصيل والمبالغ المتوقعة");
    expect(host.textContent).toContain("الإجماليات والصافي والربح المتوقع");
    expect(host.textContent).toContain("9,007,199,254,740,993.25");
    expect(host.textContent).toContain("678.90");
    expect(host.textContent).toContain("بانتظار اكتمال مصدر تكاليف المنتجات");
    expect(host.textContent).toContain("بيانات الإيراد والضريبة لم تكتمل");
    expect(host.querySelector('[data-testid="financial-net-profit"]').textContent).toContain("غير متاح");
    const forecast = host.querySelector('[data-testid="financial-forecast-position"] .ofp-money');
    expect(forecast.textContent).toBe("-55.05");
    expect(forecast.classList.contains("ofp-negative")).toBe(true);
    expect(host.querySelector('input[type="date"]')).toBeNull();
    expect(host.textContent).not.toContain("جميع الفروع");
});

test("currency selection renders only that currency and resets detail filters", async () => {
    await render();
    await change(field("البحث برقم الطلب"), "1001");
    expect(visibleOrders()).toHaveLength(1);
    await change(field("عملة التقرير"), "USD");
    expect(host.textContent).toContain("صندوق دولار");
    expect(host.textContent).toContain("777.07");
    expect(host.textContent).not.toContain("9,007,199,254,740,993.25");
    expect(field("البحث برقم الطلب").value).toBe("");
    expect(visibleOrders()).toHaveLength(0);
    expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
});

test("shipping confirmation never upgrades pending bank collection or invents a store account", async () => {
    await render();
    const row = [...host.querySelectorAll('[data-testid="financial-delivery-row"]')].find(item => item.textContent.includes("1002"));
    expect(row.textContent).toContain("30.00");
    expect(row.textContent).toContain("بانتظار اعتماد التحصيل");
    expect(row.textContent).toContain("الحساب البنكي غير متاح");
    expect(row.querySelector(".ofp-collection-status").classList.contains("ofp-confirmed")).toBe(false);
    expect(row.cells[8].textContent).toBe("غير متاح");
    expect(host.textContent).toContain("المتبقي وفق مصدر الطلب");
    expect(host.textContent).toContain("ولا يُضاف مرة أخرى إلى تحصيل شركة الشحن أو عهدة المندوب");
});

test("delivery filters combine order, party and payment without changing report totals", async () => {
    await render();
    await change(field("البحث برقم الطلب"), "100");
    expect(visibleOrders()).toHaveLength(2);
    const party = [...field("تصفية جهة التنفيذ").options].find(option => option.textContent.includes("مندوب التحويل"));
    await change(field("تصفية جهة التنفيذ"), party.value);
    expect(visibleOrders()).toHaveLength(1);
    expect(visibleOrders()[0]).toContain("1002");
    await change(field("تصفية طريقة الدفع"), "cash");
    expect(visibleOrders()).toHaveLength(0);
    expect(host.textContent).toContain("لا توجد تفاصيل تطابق الفلاتر");
    expect(host.textContent).toContain("678.90");
    await click("مسح الفلاتر");
    expect(visibleOrders()).toHaveLength(3);
});

test("print targets only the report and export contains the selected currency and filtered delivery rows", async () => {
    await render();
    await click("طباعة التقرير");
    expect(print).toHaveBeenCalledTimes(1);
    const options = useReactToPrint.mock.calls.at(-1)[0];
    expect(options.contentRef.current.dataset.testid).toBe("financial-position-content");
    expect(options.contentRef.current.querySelector(".ofp-toolbar")).toBeNull();
    await change(field("تصفية طريقة الدفع"), "bank_transfer");
    await click("تصدير Excel");
    const [workbook, filename] = XLSX.writeFile.mock.calls[0];
    expect(filename).toMatch(/^operational-financial-position-SAR-.*\.xlsx$/);
    const rows = JSON.stringify(workbook.getWorksheet("تفاصيل الطلبات").getSheetValues());
    expect(rows).toContain("1002");
    expect(rows).not.toContain("1001");
    expect(rows).not.toContain("2001");
    expect(rows).toContain("بانتظار اعتماد التحصيل");
    expect(rows).toContain("الحساب البنكي غير متاح");
    expect(JSON.stringify(workbook.worksheets.map(sheet => sheet.getSheetValues()))).not.toContain("صندوق دولار");
});

test("refresh clears stale balances immediately and a failed refresh cannot be printed or exported", async () => {
    await render();
    let reject;
    operationalApi.financialPosition.mockReturnValueOnce(new Promise((resolve, failed) => { reject = failed; }));
    await click("تحديث التقرير");
    expect(host.textContent).not.toContain("مورد التجربة");
    expect(button("طباعة التقرير").disabled).toBe(true);
    await act(async () => reject(new Error("network")));
    expect(host.textContent).toContain("تعذر تحميل التقرير الحالي");
    expect(host.querySelector('[data-testid="financial-position-content"]')).toBeNull();
    expect(button("تصدير Excel").disabled).toBe(true);
    await click("تحديث التقرير");
    expect(host.textContent).toContain("مورد التجربة");
});

test.each(["draft", "frozen", "accounting_active"])("%s cannot expose a stale operational report", async status => {
    operationalApi.financialPosition.mockResolvedValueOnce({ ...fixture(), status });
    await render();
    expect(host.querySelector('[data-testid="financial-position-content"]')).toBeNull();
    expect(host.textContent).not.toContain("مورد التجربة");
    expect(button("تصدير Excel").disabled).toBe(true);
});

test("permission rejection and a false report permission both block all financial data", async () => {
    operationalApi.financialPosition.mockRejectedValueOnce({ response: { status: 403 } });
    await render();
    expect(host.textContent).toContain("ليست لديك صلاحية");
    expect(host.querySelector("table")).toBeNull();
    operationalApi.financialPosition.mockResolvedValueOnce({ ...fixture(), permissions: { reports: false } });
    await click("تحديث التقرير");
    expect(host.textContent).toContain("ليست لديك صلاحية");
    expect(host.querySelector("table")).toBeNull();
});

test("authentication bootstrap waits before reading the financial endpoint", async () => {
    useOptionalAuth.mockReturnValue({ user: null, loading: true, authStatus: "checking" });
    await render();
    expect(operationalApi.financialPosition).not.toHaveBeenCalled();
    useOptionalAuth.mockReturnValue({ user: { id: "ready-user" }, loading: false, authStatus: "authenticated" });
    await render();
    expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("مورد التجربة");
});

test("tenant or user switch hides the previous report and ignores a late response", async () => {
    let oldResolve, newResolve;
    operationalApi.financialPosition.mockReturnValueOnce(new Promise(resolve => { oldResolve = resolve; }));
    await render();
    useOptionalAuth.mockReturnValue({ user: { id: "other-user", owner_id: "other-owner" }, loading: false, authStatus: "authenticated" });
    operationalApi.financialPosition.mockReturnValueOnce(new Promise(resolve => { newResolve = resolve; }));
    await render();
    const other = fixture(); other.currencies.SAR.tables[0].rows[0].name = "CURRENT-TENANT";
    await act(async () => newResolve(other));
    const old = fixture(); old.currencies.SAR.tables[0].rows[0].name = "DO-NOT-SHOW-OLD-TENANT";
    await act(async () => oldResolve(old));
    expect(host.textContent).toContain("CURRENT-TENANT");
    expect(host.textContent).not.toContain("DO-NOT-SHOW-OLD-TENANT");
    useOptionalAuth.mockReturnValue({ user: { id: "third-user" }, loading: false, authStatus: "authenticated" });
    operationalApi.financialPosition.mockReturnValueOnce(new Promise(() => {}));
    await render();
    expect(host.textContent).not.toContain("CURRENT-TENANT");
    expect(button("طباعة التقرير").disabled).toBe(true);
});

test("empty active report and export failure remain explicit and do not invent records", async () => {
    operationalApi.financialPosition.mockResolvedValueOnce({ ...fixture(), currencies: {}, currency_order: [] });
    await render();
    expect(host.textContent).toContain("لا توجد بيانات عملة جاهزة");
    expect(button("تصدير Excel").disabled).toBe(true);
    await click("تحديث التقرير");
    XLSX.writeFile.mockRejectedValueOnce(new Error("download failed"));
    await click("تصدير Excel");
    expect(host.textContent).toContain("تعذر تصدير التقرير");
    expect(button("تصدير Excel").disabled).toBe(false);
});

test.each([
    { created_by: "owner-two", role: "employee" },
    { created_by: "owner-one", role: "owner" },
])("same employee identity with changed ownership or role clears the previous report: %j", async changeInScope => {
    useOptionalAuth.mockReturnValue({ user: { id: "same-staff-id", created_by: "owner-one", role: "employee" }, loading: false, authStatus: "authenticated" });
    await render();
    expect(host.textContent).toContain("مورد التجربة");
    let resolve;
    operationalApi.financialPosition.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    useOptionalAuth.mockReturnValue({ user: { id: "same-staff-id", ...changeInScope }, loading: false, authStatus: "authenticated" });
    await render();
    expect(host.textContent).not.toContain("مورد التجربة");
    expect(host.querySelector('[data-testid="financial-position-content"]')).toBeNull();
    expect(button("تصدير Excel").disabled).toBe(true);
    expect(operationalApi.financialPosition).toHaveBeenCalledTimes(2);
    const current = fixture(); current.currencies.SAR.tables[0].rows[0].name = "NEW-OWNER-REPORT";
    await act(async () => resolve(current));
    expect(host.textContent).toContain("NEW-OWNER-REPORT");
});

test("unknown canonical order number remains distinct from its identifier in screen, print and search", async () => {
    const payload = fixture();
    payload.currencies.SAR.deliveries.rows[0].order_number = null;
    payload.currencies.SAR.deliveries.rows[0].order_id = "opaque-order-identifier";
    operationalApi.financialPosition.mockResolvedValueOnce(payload);
    await render();
    const screenRow = host.querySelector('[data-testid="financial-delivery-row"]');
    expect(screenRow.cells[0].textContent).toContain("معرف الطلب: opaque-order-identifier");
    expect(host.querySelector(".ofp-print-order h4").textContent).toContain("معرف الطلب: opaque-order-identifier");
    expect(host.querySelector(".ofp-print-order h4").textContent).not.toContain("الطلب معرف الطلب");
    await change(field("البحث برقم الطلب"), "opaque-order");
    expect(visibleOrders()).toHaveLength(1);
});


test.each(["confirmed", "courier_collection_unverified"])("courier %s renders booked debt apart from receipt on screen, paper and Excel", async status => {
    const response = fixture();
    const item = response.currencies.SAR.deliveries.rows.find(row => row.party_type === "courier");
    Object.assign(item, { collection_status: status, collection_amount: status === "confirmed" ? "987.65" : null,
        courier_responsibility: "231.11", collection_settled: "120.00", collection_outstanding: "111.11" });
    operationalApi.financialPosition.mockResolvedValue(response);
    await render();
    await change(field("البحث برقم الطلب"), "2001");
    for (const area of [host.querySelector('[data-testid="financial-delivery-row"]'), host.querySelector(".ofp-print-order")]) {
        expect(area.textContent).toContain("لم يُوثّق تحصيل الشركة من العميل");
        expect(area.textContent).toContain("231.11");
        expect(area.textContent).toContain("111.11");
        expect(area.textContent).not.toContain("تحصيل مؤكد");
        expect(area.textContent).not.toContain("987.65");
    }
    await click("تصدير Excel");
    const [workbook] = XLSX.writeFile.mock.calls[0];
    const sheet = JSON.stringify(workbook.getWorksheet("تفاصيل الطلبات").getSheetValues());
    expect(sheet).toContain("لم يُوثّق تحصيل الشركة من العميل");
    expect(sheet).toContain("ذمة الشركة الأصلية");
    expect(sheet).toContain("231.11");
    expect(sheet).not.toContain("987.65");
});
