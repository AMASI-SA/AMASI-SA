import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import api from "../../lib/api";
import { useOptionalAuth } from "../../context/AuthContext";
import {\n    getAccountingAccess,\n    getAccountingModuleStatus,\n    getAccountingSettlementContext,\n    getAccountingSettlementDrafts,\n} from "../../services/accountingModule";
import AccountingWorkspace from "./AccountingWorkspace";

// Jest 27 predates conditional subpath exports. Resolve the installed package's
// declared CommonJS target while keeping the actual router and navigation.
jest.mock("react-router/dom", () => {
    // jsdom 16 lacks the browser encoding globals used by React Router 7.
    const { TextEncoder, TextDecoder } = require("util");
    if (!global.TextEncoder) global.TextEncoder = TextEncoder;
    if (!global.TextDecoder) global.TextDecoder = TextDecoder;
    const path = require("path");
    const packagePath = require.resolve("react-router/package.json");
    let target = require(packagePath).exports["./dom"];
    while (target && typeof target === "object") {
        target = target.require || target.node || target.default;
    }
    if (typeof target !== "string") throw new Error("react-router/dom CommonJS export missing");
    return require(path.resolve(path.dirname(packagePath), target));
}, { virtual: true });

jest.mock("../../lib/api", () => ({ get: jest.fn() }));
jest.mock("../../context/AuthContext", () => ({ useOptionalAuth: jest.fn() }));
jest.mock("../../services/accountingModule", () => ({
    getAccountingAccess: jest.fn(), getAccountingModuleStatus: jest.fn(),
}));
// These independent operational panels are outside report navigation. Keep
// AccountingHome, AccountingReports, permission checks and router real.
jest.mock("./AccountingCourierBankBindings", () => () => null);
jest.mock("./AccountingPermissionsDialog", () => () => null);
jest.mock("./AccountingSettlements", () => () => null);
jest.mock("./AccountingBankReceipts", () => () => null);
jest.mock("./AccountingWriteControl", () => () => null);
jest.mock("./AccountingPeriods", () => () => null);
jest.mock("./AccountingCustomerAdvances", () => () => null);\njest.mock("./AccountingDailyAutomationActions", () => () => null);\njest.mock("./AccountingDailyAutomationStatus", () => ({ status }) => (\n    <a href="/integrations-v2?workspace=financial&page=journals-reports">{status?.tasks?.[0]?.title || "reports"}</a>\n));

let root, node;
beforeEach(() => {
    jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    useOptionalAuth.mockReturnValue({ user: { id: "synthetic-accountant", role: "staff" } });
    getAccountingAccess.mockResolvedValue({ is_owner: false,
        permissions: ["accounting.home.view", "accounting.journals_reports.view"] });
    getAccountingModuleStatus.mockResolvedValue({ tasks: [{ id: "review-reports", page: "journals-reports",
        title: "Synthetic report review", detail: "Synthetic fixture" }] });
    getAccountingSettlementContext.mockResolvedValue({ bindings: [], banks: [] });
    getAccountingSettlementDrafts.mockResolvedValue({ items: [] });
    api.get.mockImplementation((url) => {
        if (String(url).includes("/reports/financial-position")) {
            return Promise.resolve({ data: { status: "needs_opening_balance", reason: "approved_opening_required" } });
        }
        return Promise.resolve({ data: { items: [] } });
    });
});
afterEach(() => { act(() => root.unmount()); node.remove(); });

test("home report task navigates within MZ2 to its isolated reader, without shared report links", async () => {
    await act(async () => root.render(<MemoryRouter initialEntries={["/integrations-v2?workspace=financial&page=home"]}>
        <AccountingWorkspace />
    </MemoryRouter>));
    const link = node.querySelector('a[href="/integrations-v2?workspace=financial&page=journals-reports"]');
    expect(link).not.toBeNull();
    expect(api.get).not.toHaveBeenCalled();
    await act(async () => link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 })));
    expect(node.querySelector('[data-testid="accounting-page-journals-reports"]')).not.toBeNull();
    expect(node.querySelector('[data-testid="accounting-home-page"]')).toBeNull();
    expect(node.querySelector('[data-testid="accounting-partial-journals-reports"]')).toBeNull();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith("/financial-provider-apps/accounting-module/reports/financial-position", { params: {} });
    expect(node.textContent).toContain("بانتظار رصيد افتتاحي معتمد");
    for (const href of ["/transactions", "/financial-position-ledger", "/accounting/reconciliation"]) {
        expect(node.querySelector(`a[href="${href}"]`)).toBeNull();
    }
});

test("a member without report permission never fetches financial data", async () => {
    getAccountingAccess.mockResolvedValue({ is_owner: false, permissions: ["accounting.home.view"] });
    await act(async () => root.render(<MemoryRouter initialEntries={["/integrations-v2?workspace=financial&page=journals-reports"]}>
        <AccountingWorkspace />
    </MemoryRouter>));
    expect(node.querySelector('[data-testid="accounting-permission-denied"]')).not.toBeNull();
    expect(node.querySelector('[data-testid="accounting-page-journals-reports"]')).toBeNull();
    expect(api.get).not.toHaveBeenCalled();
    expect(getAccountingModuleStatus).not.toHaveBeenCalled();
});
