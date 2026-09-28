import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react-dom/test-utils";
import SettlementJournalDialog from "./SettlementJournalDialog";
// Keep the real Radix dialog. Jest 27 cannot resolve this new conditional
// subpath export, so read the installed package's own CommonJS declaration.
jest.mock("@radix-ui/primitive/is-development", () => {
    const path = require("path");
    const packagePath = require.resolve("@radix-ui/primitive/package.json");
    let target = require(packagePath).exports["./is-development"];
    while (target && typeof target === "object") {
        target = target.require || target.node || target.default;
    }
    if (typeof target !== "string") throw new Error("Radix development CommonJS export missing");
    return require(path.resolve(path.dirname(packagePath), target));
}, { virtual: true });
// Match the application Vite alias without changing global Jest configuration.
jest.mock("@/lib/utils", () => require("../../lib/utils"), { virtual: true });

let root, container;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div"); document.body.appendChild(container);
    root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); });
test("opens the selected posted group without navigating to the legacy ledger", () => {
    const ledger = { txn_group_id: "synthetic-group", entries: [
        {id: "d", account_name: "Test bank", side: "debit", amount: 10},
        {id: "c", account_name: "Test receivable", side: "credit", amount: 10},
    ] };
    act(() => root.render(<SettlementJournalDialog ledger={ledger} />));
    act(() => container.querySelector("button").click());
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog.textContent).toContain("synthetic-group");
    expect(dialog.textContent).toContain("Test receivable");
    expect(dialog.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(dialog.textContent.match(/10.00 SAR/g)).toHaveLength(2);
    expect(container.querySelector("a")).toBeNull();
});
test("unposted or empty groups cannot open a journal", () => {
    act(() => root.render(<SettlementJournalDialog ledger={{entries: []}} />));
    expect(container.querySelector("button")).toBeNull();
    act(() => root.render(<SettlementJournalDialog ledger={{txn_group_id: "empty", entries: []}} />));
    expect(container.querySelector("button")).toBeNull();
});

test.each(["not_ready", "needs_opening_balance"])("%s blocks a stored group even if stale entries were returned", status => {
    const ledger = { status, txn_group_id: "blocked-synthetic-group", entries: [
        { id: "stale-bank", account_name: "Stale legacy bank", side: "debit", amount: 987654 },
        { id: "stale-credit", account_name: "Stale legacy receivable", side: "credit", amount: 987654 },
    ] };
    act(() => root.render(<SettlementJournalDialog ledger={ledger} />));
    expect(container.querySelector('[role="status"]').textContent).toContain("عرض القيد غير جاهز");
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(container.textContent).not.toContain("987,654");
    expect(container.textContent).not.toContain("Stale legacy");
});

test("a readiness downgrade closes an already displayed journal and hides its entries", () => {
    const ledger = { status: "available", txn_group_id: "synthetic-group", entries: [
        { id: "d", account_name: "Synthetic bank", side: "debit", amount: 25 },
        { id: "c", account_name: "Synthetic payable", side: "credit", amount: 25 },
    ] };
    act(() => root.render(<SettlementJournalDialog ledger={ledger} />));
    act(() => container.querySelector("button").click());
    expect(document.querySelector('[role="dialog"]').textContent).toContain("Synthetic bank");
    act(() => root.render(<SettlementJournalDialog ledger={{ ...ledger, status: "needs_opening_balance" }} />));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector('[role="status"]')).not.toBeNull();
});
