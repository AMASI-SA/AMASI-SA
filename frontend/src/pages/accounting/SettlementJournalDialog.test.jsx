import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react-dom/test-utils";
import SettlementJournalDialog from "./SettlementJournalDialog";
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
