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

const payrollNote = "المجموع الجزئي لأيام الرواتب المسجلة 200.00 SAR عن يومين. إجمالي الرواتب غير متاح.";

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
        profit_rows: [{ id: "salaries", label: "الرواتب", amount: null, reason: payrollNote },
            { id: "profit", label: "صافي الربح المتوقع", amount: null, reason: "بيانات الإيراد والضريبة لم تكتمل." }],
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

test("customer wallet closing is displayed on the payable side with its own source date", async () => {
    const report = fixture();
    report.currencies.SAR.tables.push({
        id: "customer_wallets", title: "محافظ العملاء المستحقة علينا", section: "actual", complete: true,
        columns: [{ key: "name", label: "الجهة", type: "text" },
            { key: "remaining", label: "الرصيد المستحق للعملاء", type: "money", role: "payable" },
            { key: "as_of", label: "تاريخ كشف سلة", type: "text" }],
        rows: [{ id: "wallet", name: "محافظ العملاء", remaining: "125.20", as_of: "2026-11-01T08:00:00Z" }],
        show_totals: false,
    });
    report.currencies.SAR.debt_rows.push({ id: "customer_wallets", name: "محافظ العملاء", receivable: "0.00", payable: "125.20" });
    operationalApi.financialPosition.mockResolvedValueOnce(report);
    await render();
    const walletTable = host.querySelector('[data-testid="financial-table-customer_wallets"]');
    expect(walletTable.classList.contains('ofp-table-wide')).toBe(true);
    expect(walletTable.querySelector('.ofp-negative').textContent).toBe('125.20');
    expect(walletTable.textContent).not.toContain('T08:00:00Z');
    const row = [...host.querySelectorAll('[data-testid="financial-debt-summary"] tbody tr')].find(item => item.textContent.includes('محافظ العملاء'));
    expect(row.cells[1].textContent).toBe('0.00');
    expect(row.cells[2].textContent).toBe('125.20');
});

test("report tables paginate independently with 13 rows and 10 unconfirmed platform collections", async () => {
    const report = fixture();
    const suppliers = report.currencies.SAR.tables[0];
    suppliers.rows = Array.from({ length: 27 }, (_, index) => ({ ...suppliers.rows[0], id: `supplier-${index}`, name: `مورد ${index + 1}` }));
    report.currencies.SAR.tables.push({ ...suppliers, id: "provider_collections_expected", title: "تحصيل المنصات غير المؤكد", section: "forecast" });
    report.currencies.SAR.debt_rows = Array.from({ length: 14 }, (_, index) => ({ id: `debt-${index}`, name: `جهة ${index + 1}`, receivable: "1.00", payable: "2.00" }));
    report.currencies.SAR.profit_rows = Array.from({ length: 14 }, (_, index) => ({ id: `profit-${index}`, label: `بند ${index + 1}`, amount: "3.00" }));
    operationalApi.financialPosition.mockResolvedValueOnce(report);
    await render();
    const supplierTable = host.querySelector('[data-testid="financial-table-suppliers"]');
    const pendingTable = host.querySelector('[data-testid="financial-table-provider_collections_expected"]');
    expect(supplierTable.querySelectorAll("tbody tr")).toHaveLength(13);
    expect(pendingTable.querySelectorAll("tbody tr")).toHaveLength(10);
    expect(host.querySelectorAll('[data-testid="financial-debt-summary"] tbody tr')).toHaveLength(13);
    expect(host.querySelectorAll(".ofp-profit tbody tr")).toHaveLength(13);
    await act(async () => supplierTable.querySelector('[aria-label="الصفحة التالية — الموردون"]').click());
    expect(supplierTable.querySelector("tbody tr").textContent).toContain("مورد 14");
    expect(supplierTable.querySelector("tfoot").textContent).toContain("9,007,199,254,740,993.25");
    expect(pendingTable.querySelector("tbody tr").textContent).toContain("مورد 1");
    await act(async () => pendingTable.querySelector('[aria-label="الصفحة التالية — تحصيل المنصات غير المؤكد"]').click());
    expect(pendingTable.querySelector("tbody tr").textContent).toContain("مورد 11");
    await change(field("عملة التقرير"), "USD");
    await change(field("عملة التقرير"), "SAR");
    expect(host.querySelector('[data-testid="financial-table-suppliers"] tbody tr').textContent).toContain("مورد 1");
});

test("print includes every summary row and Excel retains off-page delivery details", async () => {
    const report = fixture();
    const data = report.currencies.SAR;
    const suppliers = data.tables[0];
    suppliers.rows = Array.from({ length: 27 }, (_, index) => ({ ...suppliers.rows[0], id: `supplier-${index}`, name: `مورد التصدير ${index + 1}` }));
    data.tables.push({ ...suppliers, id: "providers", title: "منصات الدفع" });
    data.tables.push({ ...suppliers, id: "provider_collections_expected", title: "تحصيل المنصات غير المؤكد", section: "forecast" });
    for (const id of ["customer_other_tenders", "customer_wallets"]) {
        data.tables.push({ ...suppliers, id, title: id, show_totals: false });
    }
    data.debt_rows = Array.from({ length: 14 }, (_, index) => ({ ...data.debt_rows[0], id: `debt-${index}`, name: `جهة ${index + 1}` }));
    data.profit_rows = Array.from({ length: 14 }, (_, index) => ({ id: `profit-${index}`, label: `بند ربح ${index + 1}`, amount: "3.00" }));
    data.current_rows = Array.from({ length: 14 }, (_, index) => ({ id: `current-${index}`, label: `بند حالي ${index + 1}`, amount: "4.00" }));
    data.forecast_rows = Array.from({ length: 14 }, (_, index) => ({ id: `forecast-${index}`, label: `بند متوقع ${index + 1}`, amount: null }));
    const delivery = data.deliveries;
    delivery.rows = Array.from({ length: 27 }, (_, index) => ({ ...delivery.rows[0], id: `delivery-${index}`, order_number: `900${String(index + 1).padStart(2, "0")}` }));
    operationalApi.financialPosition.mockResolvedValueOnce(report);
    await render();
    await act(async () => field("الصفحة التالية — الموردون").click());
    await act(async () => field("الصفحة التالية — تفاصيل الشحن والتحصيل لكل طلب").click());
    expect(visibleOrders()).toHaveLength(13);
    expect(visibleOrders()[0]).toContain("90014");
    print.mockImplementation(() => {
        const content = useReactToPrint.mock.calls.at(-1)[0].contentRef.current;
        for (const id of ["suppliers", "providers"]) {
            expect(content.querySelectorAll(`[data-testid="financial-table-${id}"] tbody tr`)).toHaveLength(27);
            expect(content.querySelectorAll(`[data-testid="financial-table-${id}"] tfoot tr`)).toHaveLength(1);
        }
        for (const id of ["customer_other_tenders", "customer_wallets"]) {
            const table = content.querySelector(`[data-testid="financial-table-${id}"]`);
            expect(table.closest(".ofp-no-print")).toBeNull();
            expect(table.querySelectorAll("tbody tr")).toHaveLength(27);
            expect(table.querySelector("tfoot")).toBeNull();
        }
        expect(content.querySelectorAll('[data-testid="financial-debt-summary"] tbody tr')).toHaveLength(14);
        expect(content.querySelectorAll('[data-testid="financial-debt-summary"] tfoot tr')).toHaveLength(1);
        for (const className of ["ofp-profit", "ofp-current", "ofp-forecast-summary"]) {
            expect(content.querySelectorAll(`.${className} tbody tr`)).toHaveLength(14);
        }
        expect(content.querySelectorAll(".ofp-print-order")).toHaveLength(0);
        expect(content.querySelector('[data-testid="financial-deliveries"]').classList.contains("ofp-no-print")).toBe(true);
    });
    await click("طباعة التقرير");
    await act(async () => useReactToPrint.mock.calls.at(-1)[0].onAfterPrint());
    expect(host.querySelectorAll('[data-testid="financial-table-suppliers"] tbody tr')).toHaveLength(13);
    expect(host.querySelectorAll('[data-testid="financial-table-provider_collections_expected"] tbody tr')).toHaveLength(10);
    expect(host.querySelectorAll('[data-testid="financial-debt-summary"] tbody tr')).toHaveLength(13);
    for (const className of ["ofp-profit", "ofp-current", "ofp-forecast-summary"]) {
        expect(host.querySelectorAll(`.${className} tbody tr`)).toHaveLength(13);
    }
    expect(host.querySelector('[data-testid="financial-table-suppliers"] tbody tr').textContent).toContain("مورد التصدير 14");
    await click("طباعة التقرير");
    await act(async () => useReactToPrint.mock.calls.at(-1)[0].onPrintError("print", new Error("print unavailable")));
    expect(host.querySelectorAll('[data-testid="financial-table-suppliers"] tbody tr')).toHaveLength(13);
    await click("تصدير Excel");
    const workbook = XLSX.writeFile.mock.calls[0][0];
    expect(JSON.stringify(workbook.worksheets.map(sheet => sheet.getSheetValues()))).toContain("مورد التصدير 27");
    expect(JSON.stringify(workbook.getWorksheet("تفاصيل الطلبات").getSheetValues())).toContain("90027");
    await change(field("البحث برقم الطلب"), "90001");
    expect(visibleOrders()).toHaveLength(1);
    expect(visibleOrders()[0]).toContain("90001");
    expect(field("الصفحة السابقة — تفاصيل الشحن والتحصيل لكل طلب").disabled).toBe(true);
});

test("print keeps financial summaries and metadata with compact warnings while hiding review details", async () => {
    const report = fixture();
    const data = report.currencies.SAR;
    for (const id of ["driver_fee_review", "courier_fee_review"]) {
        data.tables.push({ ...data.tables[1], id, kind: "review", complete: false,
            rows: [{ id: `${id}-row`, name: "تفاصيل مصدر تخص طلبًا منفردًا", payable: null }] });
    }
    report.issues = Array.from({ length: 25 }, (_, index) => ({ message: `تشخيص تفصيلي للطلب ${index}` }));
    operationalApi.financialPosition.mockResolvedValueOnce(report);
    await render();
    await click("طباعة التقرير");
    const content = useReactToPrint.mock.calls.at(-1)[0].contentRef.current;
    expect(content.querySelector('[data-testid="financial-deliveries"]').closest(".ofp-no-print")).not.toBeNull();
    for (const id of ["driver_fee_review", "courier_fee_review"]) {
        expect(content.querySelector(`[data-testid="financial-table-${id}"]`).closest(".ofp-no-print")).not.toBeNull();
    }
    for (const id of ["suppliers", "drivers", "products_unreceived"]) {
        expect(content.querySelector(`[data-testid="financial-table-${id}"]`).closest(".ofp-no-print")).toBeNull();
    }
    expect(content.querySelector(".ofp-warning strong").textContent).toContain("بعض أقسام التقرير غير مكتملة");
    const diagnostic = [...content.querySelectorAll(".ofp-warning p")].find(row => row.textContent.includes("تشخيص تفصيلي للطلب"));
    expect(diagnostic.closest(".ofp-no-print")).not.toBeNull();
    expect(content.querySelector(".ofp-scope-bar").textContent).toContain("العملة: SAR");
    expect(content.querySelector(".ofp-scope-bar").textContent).toContain("آخر احتساب");
    expect(content.querySelector(".ofp-scope-bar").textContent).toContain("منذ بدء التشغيل");
    expect(content.querySelector(".ofp-report-footer").textContent).toContain("إعداد التقرير");
    expect(content.querySelector(".ofp-profit").textContent).toContain(payrollNote);
    expect(content.querySelector('[data-testid="financial-table-products_unreceived"]').textContent).toContain("غير متاح");
    expect(content.querySelector('[data-testid="financial-table-products_unreceived"]').textContent).toContain("تكلفة المنتج لم تكتمل");
});

test("print retains a compact source warning when section completeness flags are true", async () => {
    const report = fixture();
    report.currencies.SAR.completeness = { actual: true, forecast: true, profit: true };
    operationalApi.financialPosition.mockResolvedValueOnce(report);
    await render();
    await click("طباعة التقرير");
    const warning = useReactToPrint.mock.calls.at(-1)[0].contentRef.current.querySelector(".ofp-warning");
    expect(warning.querySelector(".ofp-print-only").textContent).toContain("توجد ملاحظات على بيانات التقرير تحتاج إلى مراجعة");
});

test.each([
    ["provider_collections_expected", "456.78"],
    ["cod_expected", null],
    ["shipping_expected", "789.01"],
    ["shipping_expected", null],
])("print hides %s detail rows and retains source forecast amount %s", async (id, amount) => {
    const report = fixture();
    const data = report.currencies.SAR;
    const label = {
        provider_collections_expected: "تحصيل المنصات المتوقع",
        cod_expected: "تحصيل الدفع عند الاستلام المتوقع",
        shipping_expected: "أجور الشحن المتوقعة",
    }[id];
    const reason = "ملخص مصدر غير مكتمل للمطالبات المتوقعة.";
    data.tables.push({ id, title: label, section: "forecast", kind: "forecast", complete: false,
        columns: [{ key: "name", label: "المطالبة", type: "text" }, { key: "amount", label: "المبلغ", type: "money" }],
        rows: Array.from({ length: 27 }, (_, index) => ({ id: `${id}-${index}`, name: `مطالبة تفصيلية ${index + 1}`, amount: "123.45" })),
        totals: { amount }, notes: [reason] });
    data.forecast_rows.push({ id, label, amount, reason });
    operationalApi.financialPosition.mockResolvedValueOnce(report);
    await render();
    const table = host.querySelector(`[data-testid="financial-table-${id}"]`);
    const pageSize = id === "provider_collections_expected" ? 10 : 13;
    expect(table.querySelectorAll("tbody tr")).toHaveLength(pageSize);
    await click("طباعة التقرير");
    const content = useReactToPrint.mock.calls.at(-1)[0].contentRef.current;
    expect(table.closest(".ofp-no-print")).not.toBeNull();
    expect(table.querySelectorAll("tbody tr")).toHaveLength(pageSize);
    const forecast = content.querySelector(".ofp-forecast-summary");
    expect(forecast.closest(".ofp-no-print")).toBeNull();
    const row = [...forecast.querySelectorAll("tbody tr")].find(item => item.textContent.includes(label));
    expect(row.textContent).toContain(reason);
    expect(row.querySelector(".ofp-money").textContent).toBe(amount || "غير متاح");
    await act(async () => useReactToPrint.mock.calls.at(-1)[0].onAfterPrint());
    await click("تصدير Excel");
    const workbook = XLSX.writeFile.mock.calls[0][0];
    expect(JSON.stringify(workbook.worksheets.map(sheet => sheet.getSheetValues()))).toContain("مطالبة تفصيلية 27");
});

test("USD advertising conversion uses supplied SAR fees and net balances above the account balances", async () => {
    const report = fixture();
    const columns = [
        { key: "name", label: "الحساب", type: "text" },
        { key: "amount_usd", label: "الرصيد بالدولار", type: "money" },
        { key: "amount_sar", label: "المعادل بالريال", type: "money" },
        { key: "bank_commission", label: "العمولة البنكية", type: "money" },
        { key: "bank_commission_vat", label: "ضريبة العمولة", type: "money" },
        { key: "receivable", label: "لنا بالريال", type: "money", role: "receivable" },
        { key: "payable", label: "علينا بالريال", type: "money", role: "payable" },
    ];
    report.currencies.SAR.tables.push({ id: "ads_currency_conversion", title: "الحسابات الإعلانية بالدولار", section: "actual", complete: true, columns,
        rows: [{ id: "usd-ad", name: "حساب إعلان بالدولار", amount_usd: "123.45", amount_sar: "463.48", bank_commission: "10.66", bank_commission_vat: "1.60", receivable: "0.00", payable: "475.74" }] },
        { id: "ads", title: "أرصدة الحسابات الإعلانية", section: "actual", complete: true, columns: report.currencies.SAR.tables[0].columns,
            rows: [{ id: "ad-balance", name: "حساب إعلان بالدولار", receivable: "0.00", payable: "475.74" }] });
    operationalApi.financialPosition.mockResolvedValueOnce(report);
    await render();
    const conversion = host.querySelector('[data-testid="financial-table-ads_currency_conversion"]');
    const balances = host.querySelector('[data-testid="financial-table-ads"]');
    expect(conversion.classList.contains("ofp-table-wide")).toBe(true);
    expect(conversion.compareDocumentPosition(balances) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect([...conversion.querySelector("tbody tr").cells].map(cell => cell.textContent)).toEqual([
        "حساب إعلان بالدولار", "123.45", "463.48", "10.66", "1.60", "0.00", "475.74",
    ]);
    expect(balances.textContent).toContain("475.74");
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
    expect(host.querySelector(".ofp-profit").textContent).toContain(payrollNote);
    expect(options.contentRef.current.querySelector(".ofp-profit").textContent).toContain(payrollNote);
    await change(field("تصفية طريقة الدفع"), "bank_transfer");
    await click("تصدير Excel");
    const [workbook, filename] = XLSX.writeFile.mock.calls[0];
    expect(JSON.stringify(workbook.getWorksheet("الإجماليات والمركز").getSheetValues())).toContain(payrollNote);
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

test("unknown canonical order number remains distinct from its identifier in screen and search", async () => {
    const payload = fixture();
    payload.currencies.SAR.deliveries.rows[0].order_number = null;
    payload.currencies.SAR.deliveries.rows[0].order_id = "opaque-order-identifier";
    operationalApi.financialPosition.mockResolvedValueOnce(payload);
    await render();
    const screenRow = host.querySelector('[data-testid="financial-delivery-row"]');
    expect(screenRow.cells[0].textContent).toContain("معرف الطلب: opaque-order-identifier");
    await change(field("البحث برقم الطلب"), "opaque-order");
    expect(visibleOrders()).toHaveLength(1);
});

test("prepaid execution shows the cashbox and execution date without claiming delivery", async () => {
    const payload = fixture();
    payload.currencies.SAR.deliveries.rows = [{ id: "prepaid-fixture", order_id: "prepaid-order", order_number: "P-100",
        party_type: "courier", party_name: "شركة الدفع المقدم", payment_mode: "prepaid", recognition_event: "order_completed",
        completed_at: "2026-11-01T07:00:00Z", delivered_at: null, cash_account_name: "صندوق سلة التجريبي",
        shipping_confirmed: "34.50", shipping_settled: "34.50", shipping_outstanding: "0.00" }];
    operationalApi.financialPosition.mockResolvedValue(payload);
    await render();
    const row = host.querySelector('[data-testid="financial-delivery-row"]');
    expect(row.cells[0].textContent).toContain("تم التنفيذ:");
    expect(row.cells[0].textContent).toContain("10:00");
    expect(row.cells[0].textContent).toContain("التسليم: غير متاح");
    expect(row.cells[1].textContent).toContain("مدفوع مقدمًا عند تم التنفيذ");
    expect(row.cells[1].textContent).toContain("صندوق الخصم: صندوق سلة التجريبي");
});


test.each(["confirmed", "courier_collection_unverified"])("courier %s renders booked debt apart from receipt on screen and Excel", async status => {
    const response = fixture();
    const item = response.currencies.SAR.deliveries.rows.find(row => row.party_type === "courier");
    Object.assign(item, { collection_status: status, collection_amount: status === "confirmed" ? "987.65" : null,
        courier_responsibility: "231.11", collection_settled: "120.00", collection_outstanding: "111.11" });
    operationalApi.financialPosition.mockResolvedValue(response);
    await render();
    await change(field("البحث برقم الطلب"), "2001");
    const area = host.querySelector('[data-testid="financial-delivery-row"]');
    expect(area.textContent).toContain("لم يُوثّق تحصيل الشركة من العميل");
    expect(area.textContent).toContain("231.11");
    expect(area.textContent).toContain("111.11");
    expect(area.textContent).not.toContain("تحصيل مؤكد");
    expect(area.textContent).not.toContain("987.65");
    await click("تصدير Excel");
    const [workbook] = XLSX.writeFile.mock.calls[0];
    const sheet = JSON.stringify(workbook.getWorksheet("تفاصيل الطلبات").getSheetValues());
    expect(sheet).toContain("لم يُوثّق تحصيل الشركة من العميل");
    expect(sheet).toContain("ذمة الشركة الأصلية");
    expect(sheet).toContain("231.11");
    expect(sheet).not.toContain("987.65");
});

test("profit evidence stays partial and separate on screen print and same-snapshot export", async () => {
    const snapshot = fixture();
    snapshot.currencies.SAR.profit_rows = [{ id: "cost_of_goods", label: "تكلفة المباع", amount: "999.00",
        period_evidence: { complete: false, amount: null, known_subtotal: "9007199254740993.25", estimated_subtotal: "42.07", source_count: 2,
            basis: "accepted_supplier_invoice_net_cost_with_complete_physical_components",
            issues: [{ code: "profit_supplier_receipt_not_reconciled", source_id: "synthetic-order-1" }] } }];
    operationalApi.financialPosition.mockResolvedValueOnce(snapshot);
    await render();
    const row = host.querySelector(".ofp-profit tbody tr");
    expect(row.cells[1].textContent).toBe("غير متاح");
    const expected = ["المجموع المثبت المتاح: 9,007,199,254,740,993.25", "تقدير تكلفة غير مثبتة: 42.07",
        "عدد سجلات المصدر: 2", "صافي تكلفة فواتير المورد", "استلام المورد لم يطابق فاتورته", "synthetic-order-1",
        "المجموع المتاح جزئي", "التقدير مستقل"];
    expected.forEach(text => expect(row.textContent).toContain(text));
    expect(row.textContent).not.toContain("profit_supplier_");
    await click("طباعة التقرير");
    const printed = useReactToPrint.mock.calls.at(-1)[0].contentRef.current.querySelector(".ofp-profit").textContent;
    expected.forEach(text => expect(printed).toContain(text));
    await click("تصدير Excel");
    const workbook = XLSX.writeFile.mock.calls[0][0];
    const exported = JSON.stringify(workbook.getWorksheet("الإجماليات والمركز").getSheetValues());
    expected.forEach(text => expect(exported).toContain(text));
    expect(exported).not.toContain("999.00");
    expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
});

test("tariff, actual settlement and unmatched variance retain identical meaning on screen, print and export", async () => {
    const value = fixture();
    const columns = [
        {key:"name",label:"المنصة",type:"text"},
        {key:"tariff_fees",label:"العمولة حسب التعرفة مع ضريبتها",type:"money"},
        {key:"tariff_net",label:"صافي المدفوع حسب التعرفة",type:"money"},
        {key:"actual_settlement_fees",label:"عمولة التسويات المثبتة",type:"money"},
        {key:"fee_variance",label:"فرق العمولة الفعلية عن التعرفة",type:"money"},
    ];
    const amounts = {tariff_fees:"10.15",tariff_net:"89.85",actual_settlement_fees:"6.25",fee_variance:null};
    const note = "التسوية جزئية؛ فرق العمولة غير متاح حتى تطابق النطاق.";
    value.currencies.SAR.tables.push({id:"providers",title:"منصات الدفع",section:"fulfillment",kind:"actual",complete:true,
        columns, rows:[{id:"tabby",name:"تابي",...amounts}],totals:{name:"الإجمالي",...amounts},notes:[note]});
    operationalApi.financialPosition.mockResolvedValue(value);
    await render();
    const read = content => [...content.querySelector('[data-testid="financial-table-providers"] tbody tr').cells].map(cell=>cell.textContent);
    const screen = read(host);
    expect(screen).toEqual(["تابي","10.15","89.85","6.25","غير متاح"]);
    print.mockImplementation(() => {
        const content = useReactToPrint.mock.calls.at(-1)[0].contentRef.current;
        expect(read(content)).toEqual(screen);
        expect(content.textContent).toContain(note);
    });
    await click("طباعة التقرير");
    await act(async () => useReactToPrint.mock.calls.at(-1)[0].onAfterPrint());
    await click("تصدير Excel");
    const book = XLSX.writeFile.mock.calls[0][0];
    const rows = book.worksheets.flatMap(sheet=>sheet.getSheetValues().filter(Boolean));
    const provider = rows.find(row=>row.includes("تابي"));
    expect(provider.slice(1)).toEqual(screen);
    expect(JSON.stringify(rows)).toContain(note);
});

test("partial known tariff preserves unknown full totals and counts on screen, print and export", async () => {
    const value = fixture();
    const columns = [
        {key:"name",label:"المنصة",type:"text"},
        {key:"tariff_fees",label:"العمولة حسب التعرفة مع ضريبتها",type:"money"},
        {key:"tariff_net",label:"صافي المدفوع حسب التعرفة",type:"money"},
        {key:"tariff_known_order_count",label:"طلبات محتسبة",type:"count"},
        {key:"tariff_unresolved_order_count",label:"طلبات غير محسومة",type:"count"},
        {key:"known_tariff_fees",label:"عمولة الجزء المحتسب",type:"money"},
        {key:"known_tariff_net",label:"صافي الجزء المحتسب",type:"money"},
        {key:"actual_settlement_fees",label:"عمولة التسويات المثبتة",type:"money"},
        {key:"fee_variance",label:"فرق العمولة الفعلية عن التعرفة",type:"money"},
    ];
    const amounts = {tariff_fees:null,tariff_net:null,tariff_known_order_count:163,tariff_unresolved_order_count:null,known_tariff_fees:"10.15",known_tariff_net:"89.85",actual_settlement_fees:"6.25",fee_variance:null};
    const note = "التسوية جزئية؛ فرق العمولة غير متاح حتى تطابق النطاق.";
    value.currencies.SAR.tables.push({id:"providers",title:"منصات الدفع",section:"fulfillment",kind:"actual",complete:false,
        columns, rows:[{id:"tabby",name:"تابي",...amounts}],totals:{name:"الإجمالي",...amounts},notes:[note]});
    operationalApi.financialPosition.mockResolvedValue(value);
    await render();
    const read = content => [...content.querySelector('[data-testid="financial-table-providers"] tbody tr').cells].map(cell=>cell.textContent);
    const screen = read(host);
    expect(screen).toEqual(["تابي","غير متاح","غير متاح","163","غير متاح","10.15","89.85","6.25","غير متاح"]);
    print.mockImplementation(() => {
        const content = useReactToPrint.mock.calls.at(-1)[0].contentRef.current;
        expect(read(content)).toEqual(screen);
        expect(content.textContent).toContain(note);
    });
    await click("طباعة التقرير");
    await act(async () => useReactToPrint.mock.calls.at(-1)[0].onAfterPrint());
    await click("تصدير Excel");
    const book = XLSX.writeFile.mock.calls[0][0];
    const rows = book.worksheets.flatMap(sheet=>sheet.getSheetValues().filter(Boolean));
    const provider = rows.find(row=>row.includes("تابي"));
    expect(provider.slice(1)).toEqual(screen);
    expect(JSON.stringify(rows)).toContain(note);
});
