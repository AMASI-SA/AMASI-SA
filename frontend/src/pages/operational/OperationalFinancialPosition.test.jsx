import { act } from "react";
import { createRoot } from "react-dom/client";
import OperationalFinancialPosition from "./OperationalFinancialPosition";
import { operationalApi, requestId } from "./api";
import { useOptionalAuth } from "../../context/AuthContext";
import { useReactToPrint } from "react-to-print";
import XLSX from "../../lib/safeSpreadsheet";
import NativePurchaseReview from "./NativePurchaseReview";
import ProviderHistoryReview from "./ProviderHistoryReview";
import CustomerWalletBalance from "./CustomerWalletBalance";
import HistoricalShippingFeeReview from "./HistoricalShippingFeeReview";
import CompletedShippingFeeReview from "./CompletedShippingFeeReview";
import { FINANCIAL_PAGES, financialSnapshotParams } from "./financialPositionViews";
const realSpreadsheetWriteFile = XLSX.writeFile;

jest.mock("./api", () => ({ operationalApi: { financialPosition: jest.fn(), refreshBalances: jest.fn(), previewDeliveryAfterStart: jest.fn(), confirmDeliveryAfterStart: jest.fn() }, requestId: jest.fn() }));
jest.mock("../../context/AuthContext", () => ({ useOptionalAuth: jest.fn() }));
jest.mock("react-to-print", () => ({ useReactToPrint: jest.fn() }));
jest.mock("./NativePurchaseReview", () => jest.fn(({ onRefresh }) => <button data-testid="purchase-review" onClick={onRefresh}>حفظ ربط المورد</button>));
jest.mock("./ProviderHistoryReview", () => jest.fn(() => <div data-testid="provider-history" />));
jest.mock("./CustomerWalletBalance", () => jest.fn(({ onSaved }) => <button data-testid="wallet-review" onClick={onSaved}>حفظ كشف المحفظة</button>));
jest.mock("./HistoricalShippingFeeReview", () => jest.fn(() => <div data-testid="historical-shipping" />));
jest.mock("./CompletedShippingFeeReview", () => jest.fn(() => <div data-testid="completed-shipping" />));
global.IS_REACT_ACT_ENVIRONMENT = true;

const payrollNote = "المجموع الجزئي لأيام الرواتب المسجلة 200.00 SAR عن يومين. إجمالي الرواتب غير متاح.";
const TABLES = {
    providers: ["providers", "balance_origin"], couriers: ["couriers", "balance_origin", "courier_fee_review"],
    drivers: ["drivers", "balance_origin", "driver_fee_review"], employees: ["employees", "employee_custody"],
    accounts: ["banks_network", "cash"], suppliers: ["suppliers"], advertising: ["ads_currency_conversion", "ads"],
    other: ["customer_wallets", "customer_other_tenders", "external", "other_balances"],
    expected: ["shipping_expected", "products_unreceived", "products_received_uninvoiced", "cod_expected", "provider_collections_expected", "unassigned_courier", "unassigned_store_driver"],
};

function rawFixture() {
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
        view: "all", revision: 7, forecast_pending: false, schema_version: 1, authority: "operational_balance_report", status: "active", session_scope: "test-owner:test-user", permissions: { reports: true },
        started_at: "2026-10-31T21:00:00Z", as_of: "2026-11-01T09:00:00Z", generated_at: "2026-11-01T09:01:00Z", currency_order: ["SAR", "USD"],
        issues: [{ code: "test-source", message: "بعض أدلة المصدر تحتاج مراجعة." }], dependencies: [{ code: "costs", status: "unavailable", message: "بانتظار اكتمال مصدر تكاليف المنتجات." }],
        currencies: {
            SAR: data,
            USD: { ...data, tables: [{ id: "cash", title: "الصناديق بالدولار", section: "actual", kind: "actual", complete: true, columns: [{ key: "name", label: "الصندوق", type: "text" }, { key: "available", label: "المتاح", type: "money" }], rows: [{ id: "cash-usd", name: "صندوق دولار", available: "777.07" }], totals: { available: "777.07" } }], deliveries: { complete: true, rows: [], notes: [] } },
        },
    };
}

function project(payload, view) {
    const result = JSON.parse(JSON.stringify(payload));
    result.view = view; result.forecast_pending = view === "summary";
    if (view === "all") return result;
    for (const data of Object.values(result.currencies)) {
        data.tables = data.tables.filter(table => (TABLES[view] || []).includes(table.id));
        if (!["couriers", "drivers", "expected"].includes(view)) delete data.deliveries;
        else if (view !== "expected" && data.deliveries) data.deliveries.rows = data.deliveries.rows.filter(row => row.party_type === (view === "couriers" ? "courier" : "store_driver"));
        if (view === "summary") {
            data.forecast_rows = []; data.metrics.forecast_monetary_position = null;
        }
    }
    return result;
}
const fixture = (view = "summary") => project(rawFixture(), view);
const serve = payload => operationalApi.financialPosition.mockImplementation(config => Promise.resolve(project(payload, config.params.view)));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
let host, root, print, mounted;
const render = async view => {
    if (view) window.history.replaceState(null, "", `/operational-balances/financial-position?view=${view}`);
    await act(async () => { root.render(<OperationalFinancialPosition />); }); mounted = true;
};
const unmount = async () => { await act(async () => root.unmount()); mounted = false; };
const button = label => [...host.querySelectorAll("button")].find(element => element.textContent.includes(label));
const click = async label => { await act(async () => button(label).click()); };
const field = label => host.querySelector(`[aria-label="${label}"]`);
const navigate = async view => { await act(async () => host.querySelector(`.ofp-sidebar a[href*="view=${view}"]`).click()); };
const change = async (element, value) => {
    await act(async () => {
        const prototype = element.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value);
        element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
};
const visibleOrders = () => [...host.querySelectorAll('[data-testid="financial-delivery-row"]')].map(row => row.cells[0].textContent);
const requestedViews = () => operationalApi.financialPosition.mock.calls.map(([config]) => config.params.view);
const workbookValues = workbook => JSON.stringify(workbook.worksheets.map(sheet => sheet.getSheetValues()));

beforeEach(() => {
    jest.clearAllMocks();
    Object.values(operationalApi).forEach(mock => mock.mockReset());
    window.history.replaceState(null, "", "/operational-balances/financial-position");
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); mounted = false;
    useOptionalAuth.mockReturnValue({ user: { id: "test-user", owner_id: "test-owner" }, loading: false, authStatus: "authenticated" });
    operationalApi.financialPosition.mockImplementation(config => Promise.resolve(fixture(config.params.view)));
    operationalApi.refreshBalances.mockResolvedValue({ status: "refreshed" });
    NativePurchaseReview.mockImplementation(({ onRefresh }) => <button data-testid="purchase-review" onClick={onRefresh}>حفظ ربط المورد</button>);
    ProviderHistoryReview.mockImplementation(() => <div data-testid="provider-history" />);
    CustomerWalletBalance.mockImplementation(({ onSaved }) => <button data-testid="wallet-review" onClick={onSaved}>حفظ كشف المحفظة</button>);
    HistoricalShippingFeeReview.mockImplementation(() => <div data-testid="historical-shipping" />);
    CompletedShippingFeeReview.mockImplementation(() => <div data-testid="completed-shipping" />);
    requestId.mockReturnValue("delivery-confirmation-request");
    print = jest.fn(); useReactToPrint.mockReturnValue(print);
    jest.spyOn(XLSX, "writeFile").mockResolvedValue(undefined);
});

afterEach(() => { if (mounted) act(() => root.unmount()); host.remove(); jest.restoreAllMocks(); });

function confirmationFixture() {
    const payload = fixture("drivers");
    payload.permissions.confirm_delivery_after_start = true;
    const table = payload.currencies.SAR.tables.find(item => item.id === "drivers");
    table.columns = [...table.columns, { key: "pending_orders", type: "exceptions", label: "طلبات غير مؤكدة" }];
    table.rows[0].pending_orders = 1;
    table.rows[0].pending_order_details = [{ order_id: "old-order", order_number: "1001",
        reasons: [{ code: "operational_delivery_timestamp_missing", message: "تاريخ التسليم يحتاج مطابقة" }],
        delivery_after_start_confirmation: { can_confirm: true, state: "available", party_type: "store_driver", party_id: "driver-one" } }];
    return payload;
}

const deliveryConfirmButton = () => document.querySelector('button[aria-label="تأكيد التوصيل بعد بداية التشغيل"]');
const openPendingDelivery = async () => act(async () => host.querySelector('[data-testid="financial-table-drivers"] .ofp-pending-orders').click());
const mockDeliveryConfirmation = payload => {
    operationalApi.previewDeliveryAfterStart.mockResolvedValue({ can_confirm: true, confirmation_state: "available", preview_hash: "a".repeat(64),
        session_scope: payload.session_scope, period_start: payload.started_at, issues: [], recorded_confirmation: null });
    operationalApi.confirmDeliveryAfterStart.mockResolvedValue({ ok: true, pending_reconciliation: true, session_scope: payload.session_scope,
        order_id: "old-order", order_number: "1001", recorded_confirmation: { id: "proof-one", confirmed_at: "2026-11-01T10:00:00Z", state: "pending_reconciliation" } });
};

test("standalone report permission supports one manual delivery confirmation and one visible refresh", async () => {
    const payload = confirmationFixture();
    useOptionalAuth.mockReturnValue(undefined);
    operationalApi.financialPosition.mockResolvedValue(payload);
    mockDeliveryConfirmation(payload);
    await render("drivers"); await openPendingDelivery();
    expect(deliveryConfirmButton()).not.toBeNull();
    const confirm = deliveryConfirmButton();
    await act(async () => { confirm.click(); confirm.click(); });
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(1);
    expect(operationalApi.financialPosition).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("تم حفظ التأكيد؛ بانتظار تحديث الأرصدة");
    expect(document.querySelector('[role="dialog"]').textContent).toContain("تم حفظ التأكيد");
    await act(async () => document.querySelector('[role="dialog"] button[aria-label="إغلاق نافذة الطلبات"]').click());
    await openPendingDelivery();
    expect(deliveryConfirmButton()).toBeNull();
    expect(document.querySelector('[role="dialog"]').textContent).toContain("تم حفظ التأكيد");
    expect(host.textContent).toContain("120.00");
    expect(operationalApi.previewDeliveryAfterStart).toHaveBeenCalledTimes(1);
});

test("a failed refresh keeps the saved confirmation and old balances without another confirmation request", async () => {
    const payload = confirmationFixture();
    useOptionalAuth.mockReturnValue(undefined);
    operationalApi.financialPosition.mockResolvedValue(payload);
    mockDeliveryConfirmation(payload);
    await render("drivers");
    operationalApi.refreshBalances.mockRejectedValueOnce({ response: { status: 408, data: { detail: { code: "operational_refresh_timeout" } } } });
    await openPendingDelivery(); await act(async () => deliveryConfirmButton().click());
    expect(host.textContent).toContain("تم حفظ التأكيد؛ بانتظار تحديث الأرصدة");
    expect(host.textContent).toContain("تُعرض آخر أرصدة محفوظة بتاريخها");
    expect(host.textContent).toContain("120.00");
    await openPendingDelivery();
    expect(deliveryConfirmButton()).toBeNull();
    await act(async () => { window.dispatchEvent(new Event("focus")); window.dispatchEvent(new Event("online")); });
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(1);
    expect(operationalApi.financialPosition).toHaveBeenCalledTimes(2);
});

test("hiding during the saved-confirmation refresh stops that follow-up without resuming it on return", async () => {
    const payload = confirmationFixture();
    const visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    useOptionalAuth.mockReturnValue(undefined);
    operationalApi.financialPosition.mockResolvedValue(payload);
    mockDeliveryConfirmation(payload);
    await render("drivers");
    let resolve;
    operationalApi.refreshBalances.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    await openPendingDelivery(); await act(async () => deliveryConfirmButton().click());
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(1);
    const signal = operationalApi.refreshBalances.mock.calls[0][0].signal;
    await act(async () => {
        visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange"));
        visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange"));
        window.dispatchEvent(new Event("online"));
        resolve({ status: "refreshed" });
    });
    expect(signal.aborted).toBe(true);
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(1);
    expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("تم حفظ التأكيد؛ بانتظار تحديث الأرصدة");
    expect(button("تحديث المصادر والأرصدة").disabled).toBe(false);
});

test("report permission controls the confirmation action independently of an owner role", async () => {
    const payload = confirmationFixture();
    payload.permissions.confirm_delivery_after_start = false;
    operationalApi.financialPosition.mockResolvedValue(payload);
    await render("drivers"); await openPendingDelivery();
    expect(deliveryConfirmButton()).toBeNull();
    expect(operationalApi.previewDeliveryAfterStart).not.toHaveBeenCalled();
});

function appliedDeliveryFixture() {
    const payload = confirmationFixture();
    payload.as_of = "2026-11-01T11:00:00Z";
    const driver = payload.currencies.SAR.tables.find(table => table.id === "drivers").rows[0];
    driver.pending_orders = 0; driver.pending_order_details = [];
    payload.currencies.SAR.deliveries.rows = [{ id: "delivery:old-order", order_id: "old-order", order_number: "1001",
        party_type: "store_driver", party_id: "driver-one", party_name: "مندوب التجربة", delivered_at: null,
        recognition_basis: "owner_confirmed_after_start", recorded_at: "2026-11-01T10:00:00Z",
        shipping_confirmed: "20.00", collection_status: "not_confirmed", driver_custody: null,
        delivery_after_start_confirmation: { can_confirm: false, state: "applied", party_type: "store_driver", party_id: "driver-one",
            confirmation_id: "proof-one", confirmed_at: "2026-11-01T10:00:00Z" } }];
    return payload;
}

test("a resolved order uses matching applied delivery proof to finish the saved confirmation notice", async () => {
    const initial = confirmationFixture(), applied = appliedDeliveryFixture();
    useOptionalAuth.mockReturnValue(undefined);
    operationalApi.financialPosition.mockResolvedValueOnce(initial).mockResolvedValue(applied);
    mockDeliveryConfirmation(initial);
    await render("drivers"); await openPendingDelivery(); await act(async () => deliveryConfirmButton().click());
    expect(host.textContent).toContain("تم حفظ التأكيد وتطبيقه في التقرير");
    expect(host.textContent).not.toContain("بانتظار تحديث الأرصدة");
    expect(host.querySelector('[data-testid="financial-table-drivers"] .ofp-pending-orders')).toBeNull();
    expect(host.querySelector('[data-testid="financial-delivery-row"]').textContent).toContain("تأكيد المالك بالتوصيل بعد بدء التشغيل");
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(1);
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
});

test.each(["missing", "id", "time", "party", "order", "basis"])("%s applied proof cannot infer success from removal of a pending row or a newer report", async change => {
    const initial = confirmationFixture(), applied = appliedDeliveryFixture();
    const row = applied.currencies.SAR.deliveries.rows[0];
    if (change === "missing") delete row.delivery_after_start_confirmation;
    if (change === "id") row.delivery_after_start_confirmation.confirmation_id = "other-proof";
    if (change === "time") row.recorded_at = "2026-11-01T10:01:00Z";
    if (change === "party") row.party_id = "other-driver";
    if (change === "order") row.order_id = "other-order";
    if (change === "basis") row.recognition_basis = "delivered_order_created_after_start";
    useOptionalAuth.mockReturnValue(undefined);
    operationalApi.financialPosition.mockResolvedValueOnce(initial).mockResolvedValue(applied);
    mockDeliveryConfirmation(initial);
    await render("drivers"); await openPendingDelivery(); await act(async () => deliveryConfirmButton().click());
    expect(host.textContent).toContain("تم حفظ التأكيد؛ بانتظار تحديث الأرصدة");
    expect(host.textContent).not.toContain("تم حفظ التأكيد وتطبيقه في التقرير");
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
});

test("saved confirmation presentation is bound to its operational period even when the actor session is unchanged", async () => {
    const initial = confirmationFixture();
    useOptionalAuth.mockReturnValue(undefined);
    operationalApi.financialPosition.mockResolvedValue(initial);
    mockDeliveryConfirmation(initial);
    await render("drivers"); await openPendingDelivery(); await act(async () => deliveryConfirmButton().click());
    const nextPeriod = confirmationFixture();
    nextPeriod.started_at = "2026-11-02T21:00:00Z";
    operationalApi.financialPosition.mockResolvedValue(nextPeriod);
    await click("تحديث المصادر والأرصدة");
    expect(host.textContent).not.toContain("تم حفظ التأكيد");
    await openPendingDelivery();
    expect(deliveryConfirmButton()).not.toBeNull();
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
});

test("summary renders four saved results before its bound forecast, without refreshing sources", async () => {
    const pending = deferred();
    operationalApi.financialPosition.mockImplementation(config => config.params.view === "forecast" ? pending.promise : Promise.resolve(fixture(config.params.view)));
    await render();
    expect(requestedViews()).toEqual(["summary", "forecast"]);
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
    expect(host.textContent).toContain("678.90");
    expect(host.querySelector('[data-testid="financial-debt-summary"]').textContent).toContain("56.78");
    expect(host.querySelector(".ofp-profit").textContent).toContain(payrollNote);
    expect(host.querySelector('[data-testid="financial-net-profit"]').textContent).toContain("غير متاح");
    expect(host.querySelector('[data-testid="financial-forecast-pending"]').getAttribute("data-testid")).toBeTruthy();
    expect(host.querySelector('[data-testid="financial-forecast-position"] .ofp-money')).toBeNull();
    expect(host.querySelector('[data-testid="financial-table-suppliers"]')).toBeNull();
    expect(host.querySelector('[data-testid="financial-deliveries"]')).toBeNull();
    expect(host.querySelector(".ofp-scope-bar").textContent).toContain("آخر احتساب محفوظ: 01/11/2026, 12:00");
    expect(operationalApi.financialPosition.mock.calls[1][0].params).toEqual({ view: "forecast", ...financialSnapshotParams(fixture()) });
    await act(async () => pending.resolve(fixture("forecast")));
    expect(host.querySelector('[data-testid="financial-forecast-pending"]')).toBeNull();
    const forecast = host.querySelector('[data-testid="financial-forecast-position"] .ofp-money');
    expect(forecast.textContent).toBe("-55.05");
    expect(forecast.classList.contains("ofp-negative")).toBe(true);
    expect(host.querySelectorAll(".ofp-summary-grid > article")).toHaveLength(4);
    expect(host.querySelectorAll(".ofp-final-metrics > div")).toHaveLength(2);
});

test("all sidebar pages and local currency changes read only their requested saved sections", async () => {
    const payload = rawFixture();
    for (const data of Object.values(payload.currencies)) {
        const known = new Set(data.tables.map(table => table.id));
        for (const id of new Set(Object.values(TABLES).flat())) if (!known.has(id)) data.tables.push({
            id, title: `قسم ${id}`, complete: false, columns: [{ key: "name", label: "الجهة", type: "text" }], rows: [{ id, name: `صف ${id}` }],
        });
    }
    serve(payload);
    await render();
    for (const page of FINANCIAL_PAGES.filter(item => item.id !== "summary")) {
        await navigate(page.id);
        expect(window.location.search).toBe(`?view=${page.id}`);
        expect(host.querySelector('nav a[aria-current="page"]').textContent).toBe(page.label);
        expect([...host.querySelectorAll('[data-testid^="financial-table-"]')].map(table => table.dataset.testid.replace("financial-table-", "")).sort()).toEqual([...TABLES[page.id]].sort());
        expect(host.querySelector('[data-testid="financial-debt-summary"]')).toBeNull();
        expect(Boolean(host.querySelector('[data-testid="financial-deliveries"]'))).toBe(["couriers", "drivers", "expected"].includes(page.id));
    }
    const count = operationalApi.financialPosition.mock.calls.length;
    await change(field("عملة التقرير"), "USD");
    await change(field("عملة التقرير"), "SAR");
    await act(async () => { window.dispatchEvent(new Event("focus")); window.dispatchEvent(new Event("online")); });
    expect(operationalApi.financialPosition).toHaveBeenCalledTimes(count);
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
    expect(requestedViews()).not.toContain("all");
    expect(requestedViews()).toEqual(["summary", "forecast", ...FINANCIAL_PAGES.slice(1).map(page => page.id)]);
});

test.each(["all", "forecast", "unknown"])("unsupported sidebar query %s opens the summary without a full report request", async view => {
    await render(view);
    expect(requestedViews()).toEqual(["summary", "forecast"]);
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
    expect(host.querySelector('nav a[aria-current="page"]').textContent).toBe("الملخص المالي");
});

test("browser history changes read the selected page and preserve independent URL parameters", async () => {
    window.history.replaceState(null, "", "/operational-balances/financial-position?view=drivers&source=bookmarked#report");
    await render();
    expect(requestedViews()).toEqual(["drivers"]);
    await navigate("employees");
    expect(window.location.search).toBe("?view=employees&source=bookmarked");
    expect(window.location.hash).toBe("#report");
    await act(async () => {
        window.history.replaceState(null, "", "/operational-balances/financial-position?view=couriers");
        window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(requestedViews()).toEqual(["drivers", "employees", "couriers"]);
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test("owner review components mount only on their related page and do no shared refresh on mount", async () => {
    useOptionalAuth.mockReturnValue({ user: { id: "test-user", role: "owner" }, loading: false, authStatus: "authenticated" });
    await render();
    for (const review of [NativePurchaseReview, ProviderHistoryReview, CustomerWalletBalance, HistoricalShippingFeeReview, CompletedShippingFeeReview]) expect(review).not.toHaveBeenCalled();
    const cases = [["providers", "provider-history"], ["suppliers", "purchase-review"], ["other", "wallet-review"],
        ["couriers", "historical-shipping"], ["drivers", "completed-shipping"]];
    for (const [view, testId] of cases) {
        await navigate(view);
        expect(host.querySelector(`[data-testid="${testId}"]`)).not.toBeNull();
        for (const id of ["provider-history", "purchase-review", "wallet-review"]) if (id !== testId) expect(host.querySelector(`[data-testid="${id}"]`)).toBeNull();
        if (!["couriers", "drivers"].includes(view)) expect(host.querySelector('[data-testid="completed-shipping"]')).toBeNull();
    }
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
    await navigate("suppliers");
    await click("حفظ ربط المورد");
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(1);
    await navigate("other");
    await click("حفظ كشف المحفظة");
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(2);
});

test.each(["revision", "as_of", "started_at", "session_scope"])("forecast with a different %s cannot replace any saved summary result", async fieldName => {
    const pending = deferred();
    operationalApi.financialPosition.mockImplementation(config => config.params.view === "forecast" ? pending.promise : Promise.resolve(fixture()));
    await render();
    const wrong = fixture("forecast");
    wrong[fieldName] = fieldName === "revision" ? 8 : fieldName === "session_scope" ? "another-actor" : "2026-11-02T00:00:00Z";
    wrong.currencies.SAR.current_rows[0].amount = "991.23";
    await act(async () => pending.resolve(wrong));
    expect(host.textContent).toContain("لم تطابق التوقعات لقطة الملخص");
    expect(host.textContent).toContain("678.90");
    expect(host.textContent).not.toContain("991.23");
    expect(host.querySelector('[data-testid="financial-forecast-position"] .ofp-money')).toBeNull();
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test("forecast failure keeps saved summary values and an explicit GET-only retry", async () => {
    operationalApi.financialPosition.mockImplementation(config => config.params.view === "forecast"
        ? Promise.reject({ response: { status: 409 } }) : Promise.resolve(fixture()));
    await render();
    expect(host.textContent).toContain("تغيرت لقطة الأرصدة");
    expect(host.textContent).toContain("678.90");
    expect(button("تصدير هذه الصفحة").disabled).toBe(false);
    operationalApi.financialPosition.mockImplementation(config => Promise.resolve(fixture(config.params.view)));
    await click("إعادة قراءة الملخص والتوقعات");
    expect(requestedViews()).toEqual(["summary", "forecast", "summary", "forecast"]);
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
    expect(host.querySelector('[data-testid="financial-forecast-position"] .ofp-money').textContent).toBe("-55.05");
});

test("a same-snapshot retry admits forecast only after its new summary GET is accepted", async () => {
    operationalApi.financialPosition.mockResolvedValueOnce(fixture()).mockRejectedValueOnce({ response: { status: 409 } });
    await render();
    const reread = deferred();
    operationalApi.financialPosition.mockReturnValueOnce(reread.promise);
    await click("إعادة قراءة الملخص والتوقعات");
    expect(requestedViews()).toEqual(["summary", "forecast", "summary"]);
    expect(host.textContent).toContain("678.90");
    await act(async () => reread.resolve(fixture()));
    expect(requestedViews()).toEqual(["summary", "forecast", "summary", "forecast"]);
    expect(host.querySelector('[data-testid="financial-forecast-pending"]')).toBeNull();
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test("forecast replaces the whole currencies payload while currency choice remains local", async () => {
    const pending = deferred();
    operationalApi.financialPosition.mockImplementation(config => config.params.view === "forecast" ? pending.promise : Promise.resolve(fixture()));
    await render();
    await change(field("عملة التقرير"), "USD");
    const completed = fixture("forecast");
    completed.currency_order = ["EUR", "SAR", "USD"];
    completed.currencies.EUR = { ...completed.currencies.SAR };
    completed.currencies.USD.current_rows = [{ id: "usd", label: "لقطة الدولار الكاملة", amount: "701.23" }];
    await act(async () => pending.resolve(completed));
    expect(field("عملة التقرير").value).toBe("USD");
    expect([...field("عملة التقرير").options].map(option => option.value)).toEqual(["EUR", "SAR", "USD"]);
    expect(host.textContent).toContain("701.23");
    expect(host.textContent).not.toContain("678.90");
    expect(requestedViews()).toEqual(["summary", "forecast"]);
});

test("an empty saved currency set still requests its bound forecast and displays a new forecast currency", async () => {
    const empty = { ...fixture(), currencies: {}, currency_order: [] };
    operationalApi.financialPosition.mockResolvedValueOnce(empty).mockResolvedValueOnce(fixture("forecast"));
    await render();
    expect(requestedViews()).toEqual(["summary", "forecast"]);
    expect(host.textContent).toContain("678.90");
    expect(field("عملة التقرير").value).toBe("SAR");
});

test("hiding consumes the forecast intent and a late result cannot update the saved summary", async () => {
    const visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    const pending = deferred();
    operationalApi.financialPosition.mockImplementation(config => config.params.view === "forecast" ? pending.promise : Promise.resolve(fixture()));
    await render();
    const signal = operationalApi.financialPosition.mock.calls[1][0].signal;
    await act(async () => {
        visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange"));
        visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange"));
        pending.resolve(fixture("forecast"));
        window.dispatchEvent(new Event("focus")); window.dispatchEvent(new Event("online"));
    });
    expect(signal.aborted).toBe(true);
    expect(requestedViews()).toEqual(["summary", "forecast"]);
    expect(host.textContent).toContain("توقفت قراءة التوقعات");
    expect(host.textContent).toContain("678.90");
    expect(host.querySelector('[data-testid="financial-forecast-position"] .ofp-money')).toBeNull();
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test("leaving summary aborts its forecast and only the new visible section can publish data", async () => {
    const pending = deferred();
    operationalApi.financialPosition.mockImplementation(config => config.params.view === "forecast" ? pending.promise : Promise.resolve(fixture(config.params.view)));
    await render();
    const signal = operationalApi.financialPosition.mock.calls[1][0].signal;
    await navigate("drivers");
    expect(signal.aborted).toBe(true);
    expect(host.textContent).not.toContain("678.90");
    // The shared physical lane cannot overlap an ignored cancellation.
    expect(requestedViews()).toEqual(["summary", "forecast"]);
    await act(async () => pending.resolve(fixture("forecast")));
    expect(requestedViews()).toEqual(["summary", "forecast", "drivers"]);
    expect(host.querySelector('[data-testid="financial-table-drivers"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="financial-debt-summary"]')).toBeNull();
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test("manual shared refresh has one POST and retains the dated report while it runs and fails", async () => {
    await render("drivers");
    const pending = deferred();
    operationalApi.refreshBalances.mockReturnValueOnce(pending.promise);
    const refresh = button("تحديث المصادر والأرصدة");
    await act(async () => { refresh.click(); refresh.click(); });
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[data-testid="financial-table-drivers"]')).not.toBeNull();
    expect(host.textContent).toContain("120.00");
    expect(host.textContent).toContain("آخر احتساب محفوظ: 01/11/2026, 12:00");
    expect(button("طباعة هذه الصفحة").disabled).toBe(false);
    expect(button("تصدير هذه الصفحة").disabled).toBe(false);
    await act(async () => pending.reject({ response: { status: 408, data: { detail: { code: "operational_refresh_timeout", message: "انتهت مهلة التحديث" } } } }));
    expect(host.textContent).toContain("تُعرض آخر أرصدة محفوظة بتاريخها");
    expect(host.textContent).toContain("انتهت مهلة التحديث");
    expect(operationalApi.financialPosition).toHaveBeenCalledTimes(2);
    expect(operationalApi.financialPosition.mock.calls[1][0].signal).toBe(operationalApi.refreshBalances.mock.calls[0][0].signal);
    await act(async () => { window.dispatchEvent(new Event("focus")); window.dispatchEvent(new Event("online")); });
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(1);
    expect(operationalApi.financialPosition).toHaveBeenCalledTimes(2);
});

test("a post-refresh GET failure preserves the report, but denied refresh or denied GET removes it", async () => {
    await render("suppliers");
    operationalApi.financialPosition.mockRejectedValueOnce(new Error("network"));
    await click("تحديث المصادر والأرصدة");
    expect(host.textContent).toContain("9,007,199,254,740,993.25");
    expect(host.textContent).toContain("تعذر إكمال الطلب");
    expect(button("تصدير هذه الصفحة").disabled).toBe(false);
    operationalApi.refreshBalances.mockRejectedValueOnce({ response: { status: 403 } });
    await click("تحديث المصادر والأرصدة");
    expect(host.querySelector("table")).toBeNull();
    expect(operationalApi.financialPosition).toHaveBeenCalledTimes(2);
    await click("إعادة قراءة الصفحة");
    operationalApi.financialPosition.mockRejectedValueOnce({ response: { status: 403 } });
    await click("تحديث المصادر والأرصدة");
    expect(host.querySelector("table")).toBeNull();
    expect(button("تصدير هذه الصفحة").disabled).toBe(true);
});

test("a refresh timeout arriving after hide never starts its follow-up GET or resumes on return", async () => {
    const visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    await render("drivers");
    const pending = deferred();
    operationalApi.refreshBalances.mockReturnValueOnce(pending.promise);
    await click("تحديث المصادر والأرصدة");
    await act(async () => {
        visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange"));
        pending.reject({ response: { status: 408, data: { detail: { code: "operational_refresh_timeout" } } } });
        visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange"));
        window.dispatchEvent(new Event("online"));
    });
    expect(operationalApi.refreshBalances.mock.calls[0][0].signal.aborted).toBe(true);
    expect(requestedViews()).toEqual(["drivers"]);
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("توقفت المحاولة عند مغادرة الصفحة");
    expect(host.textContent).toContain("120.00");
});

test("initial hidden sections perform no work until visible and never refresh sources", async () => {
    const visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    await render("drivers");
    expect(operationalApi.financialPosition).not.toHaveBeenCalled();
    await act(async () => { visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange")); });
    expect(requestedViews()).toEqual(["drivers"]);
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
    await act(async () => {
        visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange"));
        visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(requestedViews()).toEqual(["drivers"]);
});

test("GET deadline ends a blocked read and later retries wait for its physical lane within their own deadline", async () => {
    jest.useFakeTimers();
    const pending = deferred();
    operationalApi.financialPosition.mockReturnValueOnce(pending.promise);
    try {
        await render("drivers");
        const signal = operationalApi.financialPosition.mock.calls[0][0].signal;
        expect(button("طباعة هذه الصفحة").disabled).toBe(true);
        await act(async () => jest.advanceTimersByTime(65000));
        expect(signal.aborted).toBe(true);
        expect(host.textContent).toContain("انتهت مهلة هذه المحاولة");
        await click("إعادة قراءة الصفحة");
        await act(async () => jest.advanceTimersByTime(65000));
        expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
        await act(async () => pending.resolve(fixture("drivers")));
        expect(host.querySelector("table")).toBeNull();
        await click("إعادة قراءة الصفحة");
        expect(operationalApi.financialPosition).toHaveBeenCalledTimes(2);
        expect(host.textContent).toContain("120.00");
        expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
    } finally { jest.useRealTimers(); }
});

test("one manual POST and its GET share a bounded deadline while late data cannot replace the saved report", async () => {
    jest.useFakeTimers();
    const refreshDone = deferred(), reportDone = deferred();
    try {
        await render("drivers");
        operationalApi.refreshBalances.mockReturnValueOnce(refreshDone.promise);
        operationalApi.financialPosition.mockReturnValueOnce(reportDone.promise);
        await click("تحديث المصادر والأرصدة");
        await act(async () => { jest.advanceTimersByTime(80000); refreshDone.resolve({ status: "refreshed" }); });
        const signal = operationalApi.refreshBalances.mock.calls[0][0].signal;
        expect(operationalApi.financialPosition.mock.calls[1][0].signal).toBe(signal);
        await act(async () => jest.advanceTimersByTime(70000));
        expect(signal.aborted).toBe(true);
        expect(host.textContent).toContain("انتهت مهلة هذه المحاولة");
        const late = fixture("drivers"); late.currencies.SAR.tables[0].rows[0].name = "LATE-DO-NOT-SHOW";
        await act(async () => reportDone.resolve(late));
        expect(host.textContent).not.toContain("LATE-DO-NOT-SHOW");
        expect(host.textContent).toContain("مندوب التجربة");
        expect(button("طباعة هذه الصفحة").disabled).toBe(false);
        expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(1);
    } finally { jest.useRealTimers(); }
});

test.each(["draft", "frozen", "accounting_active"])("%s state cannot expose financial tables or launch forecast work", async status => {
    operationalApi.financialPosition.mockResolvedValueOnce({ ...fixture(), status });
    await render();
    expect(host.querySelector("table")).toBeNull();
    expect(button("تصدير هذه الصفحة").disabled).toBe(true);
    expect(requestedViews()).toEqual(["summary"]);
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test("authentication bootstrap and absent user block reads, then server permission still controls publication", async () => {
    useOptionalAuth.mockReturnValue({ user: null, loading: true, authStatus: "checking" });
    await render("drivers");
    expect(operationalApi.financialPosition).not.toHaveBeenCalled();
    useOptionalAuth.mockReturnValue({ user: null, loading: false, authStatus: "unauthenticated" });
    await render();
    expect(operationalApi.financialPosition).not.toHaveBeenCalled();
    useOptionalAuth.mockReturnValue({ user: { id: "ready-user" }, loading: false, authStatus: "authenticated" });
    operationalApi.financialPosition.mockResolvedValueOnce({ ...fixture("drivers"), permissions: { reports: false } });
    await render();
    expect(requestedViews()).toEqual(["drivers"]);
    expect(host.querySelector("table")).toBeNull();
    expect(host.textContent).toContain("ليست لديك صلاحية");
});

test.each([{ id: "another-user" }, { id: "test-user", created_by: "other-owner" }, { id: "test-user", role: "owner" }])("scope change hides old data and rejects a late prior response: %j", async user => {
    await render("suppliers");
    const old = deferred(), next = deferred();
    operationalApi.financialPosition.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
    await click("تحديث المصادر والأرصدة");
    const signal = operationalApi.financialPosition.mock.calls[1][0].signal;
    useOptionalAuth.mockReturnValue({ user, loading: false, authStatus: "authenticated" });
    await render();
    expect(signal.aborted).toBe(true);
    expect(host.textContent).not.toContain("9,007,199,254,740,993.25");
    expect(button("طباعة هذه الصفحة").disabled).toBe(true);
    const late = fixture("suppliers"); late.currencies.SAR.tables[0].rows[0].name = "OLD-ACTOR";
    await act(async () => old.resolve(late));
    expect(host.textContent).not.toContain("OLD-ACTOR");
    const current = fixture("suppliers"); current.session_scope = "new-actor"; current.currencies.SAR.tables[0].rows[0].name = "CURRENT-ACTOR";
    await act(async () => next.resolve(current));
    expect(host.textContent).toContain("CURRENT-ACTOR");
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(1);
});

test("current-page summary export uses the visible payload and print expands every saved summary row without hidden requests", async () => {
    const payload = rawFixture();
    const data = payload.currencies.SAR;
    data.debt_rows = Array.from({ length: 14 }, (_, index) => ({ id: `debt-${index}`, name: `جهة ${index + 1}`, receivable: "1.00", payable: "2.00" }));
    for (const key of ["profit_rows", "current_rows", "forecast_rows"]) data[key] = Array.from({ length: 14 }, (_, index) => ({ id: `${key}-${index}`, label: `${key} ${index + 1}`, amount: key === "forecast_rows" ? null : "3.00" }));
    serve(payload);
    await render();
    expect(host.querySelectorAll('[data-testid="financial-debt-summary"] tbody tr')).toHaveLength(13);
    for (const className of ["ofp-profit", "ofp-current", "ofp-forecast-summary"]) expect(host.querySelectorAll(`.${className} tbody tr`)).toHaveLength(13);
    print.mockImplementation(() => {
        const content = useReactToPrint.mock.calls.at(-1)[0].contentRef.current;
        expect(content.querySelector('[data-testid="financial-table-suppliers"]')).toBeNull();
        expect(content.querySelectorAll('[data-testid="financial-debt-summary"] tbody tr')).toHaveLength(14);
        for (const className of ["ofp-profit", "ofp-current", "ofp-forecast-summary"]) expect(content.querySelectorAll(`.${className} tbody tr`)).toHaveLength(14);
        expect(content.querySelector(".ofp-report-footer").textContent).toContain("الملخص المالي · SAR");
        expect(content.querySelector(".ofp-toolbar")).toBeNull();
    });
    await click("طباعة هذه الصفحة");
    await act(async () => useReactToPrint.mock.calls.at(-1)[0].onAfterPrint());
    expect(host.querySelectorAll('[data-testid="financial-debt-summary"] tbody tr')).toHaveLength(13);
    await click("تصدير هذه الصفحة");
    const [workbook, filename] = XLSX.writeFile.mock.calls[0];
    expect(workbook.worksheets.map(sheet => sheet.name)).toEqual(["معلومات التقرير", "الإجماليات والمركز"]);
    expect(workbookValues(workbook)).toContain("profit_rows 14");
    expect(workbookValues(workbook)).not.toContain("مورد التجربة");
    expect(filename).toMatch(/^operational-financial-position-summary-SAR-.*\.xlsx$/);
    expect(requestedViews()).toEqual(["summary", "forecast"]);
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test("current section export preserves exact cents and no unrelated sheets while full export is explicit and bound", async () => {
    await render("suppliers");
    await click("تصدير هذه الصفحة");
    const [pageWorkbook, pageName] = XLSX.writeFile.mock.calls[0];
    expect(pageName).toContain("-suppliers-SAR-");
    expect(workbookValues(pageWorkbook)).toContain("9007199254740993.25");
    expect(pageWorkbook.getWorksheet("الإجماليات والمركز")).toBeUndefined();
    expect(pageWorkbook.getWorksheet("تفاصيل الطلبات")).toBeUndefined();
    expect(requestedViews()).toEqual(["suppliers"]);
    const full = fixture("all"); full.generated_at = "2026-11-01T10:15:00Z";
    operationalApi.financialPosition.mockResolvedValueOnce(full);
    await click("تصدير جميع الأقسام");
    expect(requestedViews()).toEqual(["suppliers", "all"]);
    expect(operationalApi.financialPosition.mock.calls[1][0].params).toEqual({ view: "all", ...financialSnapshotParams(fixture("suppliers")) });
    const [workbook, filename] = XLSX.writeFile.mock.calls[1];
    expect(filename).toContain("-all-SAR-");
    expect(workbookValues(workbook)).toContain("9007199254740993.25");
    expect(workbook.getWorksheet("تفاصيل الطلبات")).toBeTruthy();
    expect(workbook.getWorksheet("الإجماليات والمركز")).toBeTruthy();
    expect(workbookValues(workbook)).toContain("13:15");
    expect(workbookValues(workbook)).toContain("12:00");
    expect(workbookValues(workbook)).toContain("جميع أقسام التقرير · SAR");
    expect(workbookValues(workbook)).not.toContain("صندوق دولار");
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test.each(["revision", "as_of", "started_at", "session_scope", "view"])("full export rejects a response with a different %s before writing a file", async fieldName => {
    await render("drivers");
    const full = fixture("all");
    full[fieldName] = fieldName === "revision" ? 8 : fieldName === "session_scope" ? "foreign-session"
        : fieldName === "view" ? "drivers" : "2026-11-03T00:00:00Z";
    operationalApi.financialPosition.mockResolvedValueOnce(full);
    await click("تصدير جميع الأقسام");
    expect(XLSX.writeFile).not.toHaveBeenCalled();
    expect(host.textContent).toContain("لم يطابق التقرير الكامل الجلسة واللقطة");
    expect(host.querySelector('[data-testid="financial-table-drivers"]')).not.toBeNull();
});

test.each(["auth", "auth-loading", "unmount", "navigation", "history", "manual-refresh", "hidden"])("a deferred full export cannot write after %s consumes its page attempt", async action => {
    const visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    await render("drivers");
    const pending = deferred();
    operationalApi.financialPosition.mockReturnValueOnce(pending.promise);
    await click("تصدير جميع الأقسام");
    const signal = operationalApi.financialPosition.mock.calls[1][0].signal;
    if (action === "auth") {
        useOptionalAuth.mockReturnValue({ user: { id: "new-user" }, loading: false, authStatus: "authenticated" });
        await render();
    } else if (action === "auth-loading") {
        useOptionalAuth.mockReturnValue({ user: { id: "test-user", owner_id: "test-owner" }, loading: true, authStatus: "authenticated" });
        await render();
    } else if (action === "unmount") await unmount();
    else if (action === "navigation") await navigate("employees");
    else if (action === "history") await act(async () => {
        window.history.replaceState(null, "", "/operational-balances/financial-position?view=employees");
        window.dispatchEvent(new PopStateEvent("popstate"));
    });
    else if (action === "manual-refresh") await click("تحديث المصادر والأرصدة");
    else await act(async () => { visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange")); });
    expect(signal.aborted).toBe(true);
    await act(async () => pending.resolve(fixture("all")));
    expect(XLSX.writeFile).not.toHaveBeenCalled();
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(action === "manual-refresh" ? 1 : 0);
});

test.each([401, 403])("full-export HTTP %s removes financial content and never writes a workbook", async status => {
    await render("drivers");
    operationalApi.financialPosition.mockRejectedValueOnce({ response: { status } });
    await click("تصدير جميع الأقسام");
    expect(XLSX.writeFile).not.toHaveBeenCalled();
    expect(host.querySelector("table")).toBeNull();
});

test.each(["page", "all"])("%s export keeps a live scope gate throughout asynchronous serialization", async kind => {
    const auth = { user: { id: "test-user", owner_id: "test-owner" }, loading: false, authStatus: "authenticated" };
    const pending = deferred();
    useOptionalAuth.mockReturnValue(auth);
    XLSX.writeFile.mockReturnValueOnce(pending.promise);
    await render("drivers");
    await click(kind === "all" ? "تصدير جميع الأقسام" : "تصدير هذه الصفحة");
    const options = XLSX.writeFile.mock.calls[0][2];
    expect(options.shouldContinue()).toBe(true);
    useOptionalAuth.mockReturnValue({ ...auth, loading: true });
    await render();
    expect(options.shouldContinue()).toBe(false);
    await act(async () => pending.resolve(false));
    expect(host.querySelector("table")).toBeNull();
});

test.each([["page", "visibility"], ["all", "visibility"], ["page", "offline"], ["all", "offline"]])("%s export stays revoked after %s returns before real serialization completes", async (kind, cause) => {
    const pending = deferred();
    const visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    const online = jest.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    const anchorClick = jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    XLSX.writeFile.mockImplementation((workbook, filename, options) => {
        jest.spyOn(workbook.xlsx, "writeBuffer").mockReturnValueOnce(pending.promise);
        return realSpreadsheetWriteFile(workbook, filename, options);
    });
    await render("drivers");
    await click(kind === "all" ? "تصدير جميع الأقسام" : "تصدير هذه الصفحة");
    const gate = XLSX.writeFile.mock.calls[0][2].shouldContinue;
    expect(gate()).toBe(true);
    await act(async () => {
        if (cause === "visibility") {
            visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange"));
            visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange"));
        } else {
            online.mockReturnValue(false); window.dispatchEvent(new Event("offline"));
            online.mockReturnValue(true); window.dispatchEvent(new Event("online"));
        }
    });
    expect(gate()).toBe(false);
    await act(async () => pending.resolve(new Uint8Array([1, 2, 3])));
    expect(anchorClick).not.toHaveBeenCalled();
    expect(document.querySelector("a[download]")).toBeNull();
    expect(host.textContent).toContain("أُلغي التصدير عند مغادرة الصفحة أو انقطاع الاتصال");
    expect(button("تصدير هذه الصفحة").disabled).toBe(false);
    expect(requestedViews()).toEqual(kind === "all" ? ["drivers", "all"] : ["drivers"]);
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test("full-export failure remains explicit and does not publish partial or automatically retried files", async () => {
    await render("drivers");
    operationalApi.financialPosition.mockRejectedValueOnce({ response: { status: 409 } });
    await click("تصدير جميع الأقسام");
    expect(host.textContent).toContain("تغيرت لقطة الأرصدة؛ أعد قراءة الصفحة");
    expect(XLSX.writeFile).not.toHaveBeenCalled();
    await act(async () => { window.dispatchEvent(new Event("focus")); window.dispatchEvent(new Event("online")); });
    expect(requestedViews()).toEqual(["drivers", "all"]);
    XLSX.writeFile.mockRejectedValueOnce(new Error("download failed"));
    await click("تصدير هذه الصفحة");
    expect(host.textContent).toContain("تعذر تصدير هذه الصفحة");
    expect(button("تصدير هذه الصفحة").disabled).toBe(false);
});

test("full export consumes an active forecast with a visible retry after export instead of a permanent loader", async () => {
    const pending = deferred();
    operationalApi.financialPosition.mockResolvedValueOnce(fixture()).mockReturnValueOnce(pending.promise);
    await render();
    const signal = operationalApi.financialPosition.mock.calls[1][0].signal;
    await click("تصدير جميع الأقسام");
    expect(signal.aborted).toBe(true);
    expect(requestedViews()).toEqual(["summary", "forecast"]);
    await act(async () => pending.resolve(fixture("forecast")));
    expect(requestedViews()).toEqual(["summary", "forecast", "all"]);
    expect(XLSX.writeFile).toHaveBeenCalledTimes(1);
    expect(host.querySelector('.ofp-forecast-pending[aria-busy="false"]')).not.toBeNull();
    expect(host.textContent).toContain("أعد قراءة الصفحة لاستكمال التوقعات بعد التصدير");
    await click("إعادة قراءة الملخص والتوقعات");
    expect(requestedViews()).toEqual(["summary", "forecast", "all", "summary", "forecast"]);
    expect(host.querySelector('[data-testid="financial-forecast-pending"]')).toBeNull();
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test.each(["loading", "scope-return"])("auth %s cannot replay a consumed manual refresh intent", async restart => {
    const auth = { user: { id: "test-user", owner_id: "test-owner" }, loading: false, authStatus: "authenticated" };
    useOptionalAuth.mockReturnValue(auth);
    await render("drivers");
    const pending = deferred();
    operationalApi.refreshBalances.mockReturnValueOnce(pending.promise);
    await click("تحديث المصادر والأرصدة");
    const signal = operationalApi.refreshBalances.mock.calls[0][0].signal;
    useOptionalAuth.mockReturnValue(restart === "loading" ? { ...auth, loading: true }
        : { ...auth, user: { id: "temporary-other-user" } });
    await render();
    useOptionalAuth.mockReturnValue(auth);
    await render();
    await act(async () => pending.resolve({ status: "refreshed" }));
    expect(signal.aborted).toBe(true);
    expect(operationalApi.refreshBalances).toHaveBeenCalledTimes(1);
    expect(requestedViews()).toEqual(["drivers", "drivers"]);
    expect(host.querySelector('[data-testid="financial-table-drivers"]')).not.toBeNull();
});

test("auth revalidation aborts forecast and leaves an explicit retry when the same snapshot returns", async () => {
    const auth = { user: { id: "test-user", owner_id: "test-owner" }, loading: false, authStatus: "authenticated" };
    const pending = deferred();
    useOptionalAuth.mockReturnValue(auth);
    operationalApi.financialPosition.mockResolvedValueOnce(fixture()).mockReturnValueOnce(pending.promise);
    await render();
    const signal = operationalApi.financialPosition.mock.calls[1][0].signal;
    useOptionalAuth.mockReturnValue({ ...auth, loading: true });
    await render();
    expect(host.querySelector("table")).toBeNull();
    expect(signal.aborted).toBe(true);
    useOptionalAuth.mockReturnValue(auth);
    await render();
    await act(async () => pending.resolve(fixture("forecast")));
    expect(requestedViews()).toEqual(["summary", "forecast", "summary"]);
    expect(host.querySelector('.ofp-forecast-pending[aria-busy="false"]')).not.toBeNull();
    expect(host.textContent).toContain("توقفت قراءة التوقعات عند إعادة التحقق");
    await click("إعادة قراءة الملخص والتوقعات");
    expect(host.querySelector('[data-testid="financial-forecast-pending"]')).toBeNull();
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test("unassigned courier and driver exceptions remain reachable on expected page and in its export", async () => {
    const payload = fixture("expected");
    for (const [id, order] of [["unassigned_courier", "U-C"], ["unassigned_store_driver", "U-D"]]) payload.currencies.SAR.tables.push({
        id, title: `جهة غير محددة ${id}`, kind: "review", complete: false, columns: [], rows: [], totals: {},
        unassigned_order_details: [{ order_id: order, order_number: order, reasons: [{ message: "لم تثبت جهة التنفيذ" }] }],
    });
    operationalApi.financialPosition.mockResolvedValueOnce(payload);
    await render("expected");
    for (const id of ["unassigned_courier", "unassigned_store_driver"]) {
        await act(async () => host.querySelector(`[data-testid="financial-table-${id}"] .ofp-pending-orders`).click());
        expect(document.querySelector('[role="dialog"]').textContent).toContain("لم تثبت جهة التنفيذ");
        await act(async () => document.querySelector('[role="dialog"] button[aria-label="إغلاق نافذة الطلبات"]').click());
    }
    await click("تصدير هذه الصفحة");
    const values = JSON.stringify(XLSX.writeFile.mock.calls[0][0].getWorksheet("طلبات تحتاج مراجعة").getSheetValues());
    expect(values).toContain("U-C"); expect(values).toContain("U-D");
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test("expected tables paginate independently and current-page print/export include every source row", async () => {
    const payload = fixture("expected");
    const template = rawFixture().currencies.SAR.tables[0];
    const rows = Array.from({ length: 27 }, (_, index) => ({ id: `claim-${index}`, name: `مطالبة ${index + 1}`, receivable: "123.45", payable: null }));
    payload.currencies.SAR.tables.push({ ...template, id: "shipping_expected", title: "أجور الشحن المتوقعة", rows },
        { ...template, id: "provider_collections_expected", title: "تحصيل المنصات غير المؤكد", rows });
    operationalApi.financialPosition.mockResolvedValueOnce(payload);
    await render("expected");
    const shipping = host.querySelector('[data-testid="financial-table-shipping_expected"]');
    const providers = host.querySelector('[data-testid="financial-table-provider_collections_expected"]');
    expect(shipping.querySelectorAll("tbody tr")).toHaveLength(13);
    expect(providers.querySelectorAll("tbody tr")).toHaveLength(10);
    await act(async () => shipping.querySelector('[aria-label="الصفحة التالية — أجور الشحن المتوقعة"]').click());
    expect(shipping.querySelector("tbody tr").textContent).toContain("مطالبة 14");
    expect(providers.querySelector("tbody tr").textContent).toContain("مطالبة 1");
    print.mockImplementation(() => {
        const content = useReactToPrint.mock.calls.at(-1)[0].contentRef.current;
        for (const id of ["shipping_expected", "provider_collections_expected"]) {
            const table = content.querySelector(`[data-testid="financial-table-${id}"]`);
            expect(table.closest(".ofp-no-print")).toBeNull();
            expect(table.querySelectorAll("tbody tr")).toHaveLength(27);
        }
    });
    await click("طباعة هذه الصفحة");
    await act(async () => useReactToPrint.mock.calls.at(-1)[0].onAfterPrint());
    expect(shipping.querySelectorAll("tbody tr")).toHaveLength(13);
    await click("تصدير هذه الصفحة");
    expect(workbookValues(XLSX.writeFile.mock.calls[0][0])).toContain("مطالبة 27");
    expect(XLSX.writeFile.mock.calls[0][0].getWorksheet("الإجماليات والمركز")).toBeUndefined();
    await change(field("عملة التقرير"), "USD"); await change(field("عملة التقرير"), "SAR");
    expect(host.querySelector('[data-testid="financial-table-shipping_expected"] tbody tr').textContent).toContain("مطالبة 1");
    expect(requestedViews()).toEqual(["expected"]);
});

test("local delivery filters and selected currency are retained in the current-page export without a new GET", async () => {
    await render("drivers");
    await change(field("تصفية طريقة الدفع"), "bank_transfer");
    await click("تصدير هذه الصفحة");
    const [workbook, filename] = XLSX.writeFile.mock.calls[0];
    const orders = JSON.stringify(workbook.getWorksheet("تفاصيل الطلبات").getSheetValues());
    expect(filename).toContain("-drivers-SAR-");
    expect(orders).toContain("1002"); expect(orders).not.toContain("1001"); expect(orders).not.toContain("2001");
    expect(orders).toContain("بانتظار اعتماد التحصيل");
    expect(orders).toContain("الحساب البنكي غير متاح");
    await change(field("عملة التقرير"), "USD");
    expect(field("تصفية طريقة الدفع").value).toBe("");
    expect(visibleOrders()).toHaveLength(0);
    expect(requestedViews()).toEqual(["drivers"]);
    expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});


test("shipping confirmation never upgrades pending bank collection or invents a store account", async () => {
    await render("drivers");
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
    await render("drivers");
    await change(field("البحث برقم الطلب"), "100");
    expect(visibleOrders()).toHaveLength(2);
    const party = [...field("تصفية جهة التنفيذ").options].find(option => option.textContent.includes("مندوب التحويل"));
    await change(field("تصفية جهة التنفيذ"), party.value);
    expect(visibleOrders()).toHaveLength(1);
    expect(visibleOrders()[0]).toContain("1002");
    await change(field("تصفية طريقة الدفع"), "cash");
    expect(visibleOrders()).toHaveLength(0);
    expect(host.textContent).toContain("لا توجد تفاصيل تطابق الفلاتر");
    expect(host.textContent).toContain("120.00");
    await click("مسح الفلاتر");
    expect(visibleOrders()).toHaveLength(2);
});


test("unknown canonical order number remains distinct from its identifier in screen and search", async () => {
    const payload = fixture("drivers");
    payload.currencies.SAR.deliveries.rows[0].order_number = null;
    payload.currencies.SAR.deliveries.rows[0].order_id = "opaque-order-identifier";
    operationalApi.financialPosition.mockResolvedValueOnce(payload);
    await render("drivers");
    const screenRow = host.querySelector('[data-testid="financial-delivery-row"]');
    expect(screenRow.cells[0].textContent).toContain("معرف الطلب: opaque-order-identifier");
    await change(field("البحث برقم الطلب"), "opaque-order");
    expect(visibleOrders()).toHaveLength(1);
});


test("prepaid execution shows the cashbox and execution date without claiming delivery", async () => {
    const payload = fixture("couriers");
    payload.currencies.SAR.deliveries.rows = [{ id: "prepaid-fixture", order_id: "prepaid-order", order_number: "P-100",
        party_type: "courier", party_name: "شركة الدفع المقدم", payment_mode: "prepaid", recognition_event: "order_completed",
        completed_at: "2026-11-01T07:00:00Z", delivered_at: null, cash_account_name: "صندوق سلة التجريبي",
        shipping_confirmed: "34.50", shipping_settled: "34.50", shipping_outstanding: "0.00" }];
    operationalApi.financialPosition.mockResolvedValue(payload);
    await render("couriers");
    const row = host.querySelector('[data-testid="financial-delivery-row"]');
    expect(row.cells[0].textContent).toContain("تم التنفيذ:");
    expect(row.cells[0].textContent).toContain("10:00");
    expect(row.cells[0].textContent).toContain("التسليم: غير متاح");
    expect(row.cells[1].textContent).toContain("مدفوع مقدمًا عند تم التنفيذ");
    expect(row.cells[1].textContent).toContain("صندوق الخصم: صندوق سلة التجريبي");
});


test.each([
    ["delivered_order_created_after_start", "طلب تم توصيله وأُنشئ بعد بدء التشغيل"],
    ["owner_confirmed_after_start", "تأكيد المالك بالتوصيل بعد بدء التشغيل"],
])("%s shows its basis and recording time without claiming a delivery date or cash", async (basis, label) => {
    const payload = fixture("drivers");
    payload.as_of = "2026-11-02T12:00:00Z";
    payload.currencies.SAR.deliveries.rows = [{ id: "status-delivery", order_id: "status-order", order_number: "D-101",
        party_type: "store_driver", party_name: "مندوب التجربة", recognition_basis: basis,
        recorded_at: "2026-11-01T07:00:00Z", delivered_at: null, collection_status: "not_confirmed",
        shipping_confirmed: "10.00", shipping_settled: "0.00", shipping_outstanding: "10.00", driver_custody: null }];
    operationalApi.financialPosition.mockResolvedValue(payload);
    await render("drivers");
    const row = host.querySelector('[data-testid="financial-delivery-row"]');
    expect(row.cells[0].textContent).toContain("التسليم: غير متاح");
    expect([...row.cells[0].querySelectorAll("small")].find(node => node.textContent.startsWith("وقت التسجيل:"))?.textContent)
        .toContain("10:00");
    expect(row.cells[1].textContent).toContain(label);
    expect(row.cells[3].textContent).toBe("التحصيل غير مؤكد");
    expect(row.cells[4].textContent).toBe("10.00");
    expect(row.cells[8].textContent).toBe("غير متاح");
});



test.each(["confirmed", "courier_collection_unverified"])("courier %s renders booked debt apart from receipt on screen and Excel", async status => {
    const response = fixture("couriers");
    const item = response.currencies.SAR.deliveries.rows.find(row => row.party_type === "courier");
    Object.assign(item, { collection_status: status, collection_amount: status === "confirmed" ? "987.65" : null,
        courier_responsibility: "231.11", collection_settled: "120.00", collection_outstanding: "111.11" });
    operationalApi.financialPosition.mockResolvedValue(response);
    await render("couriers");
    await change(field("البحث برقم الطلب"), "2001");
    const area = host.querySelector('[data-testid="financial-delivery-row"]');
    expect(area.textContent).toContain("لم يُوثّق تحصيل الشركة من العميل");
    expect(area.textContent).toContain("231.11");
    expect(area.textContent).toContain("111.11");
    expect(area.textContent).not.toContain("تحصيل مؤكد");
    expect(area.textContent).not.toContain("987.65");
    await click("تصدير هذه الصفحة");
    const [workbook] = XLSX.writeFile.mock.calls[0];
    const sheet = JSON.stringify(workbook.getWorksheet("تفاصيل الطلبات").getSheetValues());
    expect(sheet).toContain("لم يُوثّق تحصيل الشركة من العميل");
    expect(sheet).toContain("ذمة الشركة الأصلية");
    expect(sheet).toContain("231.11");
    expect(sheet).not.toContain("987.65");
});

test("customer wallet closing is displayed on the payable side with its own source date", async () => {
    const report = rawFixture();
    report.currencies.SAR.tables.push({
        id: "customer_wallets", title: "محافظ العملاء المستحقة علينا", section: "actual", complete: true,
        columns: [{ key: "name", label: "الجهة", type: "text" },
            { key: "remaining", label: "الرصيد المستحق للعملاء", type: "money", role: "payable" },
            { key: "as_of", label: "تاريخ كشف سلة", type: "text" }],
        rows: [{ id: "wallet", name: "محافظ العملاء", remaining: "125.20", as_of: "2026-11-01T08:00:00Z" }],
        show_totals: false,
    });
    report.currencies.SAR.debt_rows.push({ id: "customer_wallets", name: "محافظ العملاء", receivable: "0.00", payable: "125.20" });
    serve(report);
    await render("other");
    const walletTable = host.querySelector('[data-testid="financial-table-customer_wallets"]');
    expect(walletTable.classList.contains('ofp-table-wide')).toBe(true);
    expect(walletTable.querySelector('.ofp-negative').textContent).toBe('125.20');
    expect(walletTable.textContent).not.toContain('T08:00:00Z');
    await navigate("summary");
    const row = [...host.querySelectorAll('[data-testid="financial-debt-summary"] tbody tr')].find(item => item.textContent.includes('محافظ العملاء'));
    expect(row.cells[1].textContent).toBe('0.00');
    expect(row.cells[2].textContent).toBe('125.20');
});


test("USD advertising conversion uses supplied SAR fees and net balances above the account balances", async () => {
    const report = rawFixture();
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
    serve(report);
    await render("advertising");
    const conversion = host.querySelector('[data-testid="financial-table-ads_currency_conversion"]');
    const balances = host.querySelector('[data-testid="financial-table-ads"]');
    expect(conversion.classList.contains("ofp-table-wide")).toBe(true);
    expect(conversion.compareDocumentPosition(balances) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect([...conversion.querySelector("tbody tr").cells].map(cell => cell.textContent)).toEqual([
        "حساب إعلان بالدولار", "123.45", "463.48", "10.66", "1.60", "0.00", "475.74",
    ]);
    expect(balances.textContent).toContain("475.74");
});
