import { act } from "react";
import { createRoot } from "react-dom/client";

import RecurringObligations from "./RecurringObligations";


jest.mock("sonner", () => ({
    toast: { success: jest.fn(), error: jest.fn() },
}));

jest.mock("../services/recurringObligations", () => ({
    loadRecurringObligationsWorkspace: async () => ({
        items: [{
            id: "rent-1",
            title: "إيجار فرع الرياض",
            expense_type: "rent",
            entity_type: "warehouse",
            entity_id: "warehouse-1",
            entity_name: "فرع الرياض",
            cycle: "semiannual",
            period_amount: 60000,
            daily_amount: 326.09,
            accrued_to_today: 14673.91,
            next_due_date: "2027-01-01",
            status: "active",
            display_status: "active",
            invoice_count: 0,
            auto_renew: true,
            start_date: "2026-07-01",
        }],
        summary: {
            daily_total: 326.09,
            due_next_30_days: 0,
            active_count: 1,
            overdue_count: 0,
        },
        options: {
            locations: [{ id: "warehouse-1", name: "فرع الرياض", code: "RUH" }],
            employees: [{ id: "employee-1", display_name: "أحمد" }],
        },
    }),
    createRecurringObligation: jest.fn(),
    updateRecurringObligation: jest.fn(),
    loadRecurringInvoices: async () => [],
    createRecurringInvoice: jest.fn(),
}));


let container;
let root;

beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => root.unmount());
    container.remove();
    globalThis.IS_REACT_ACT_ENVIRONMENT = false;
});

async function renderWorkspace() {
    await act(async () => {
        root.render(<RecurringObligations />);
        await new Promise((resolve) => setTimeout(resolve, 0));
    });
}

test("renders the approved compact recurring obligations workspace", async () => {
    await renderWorkspace();

    expect(container.textContent).toContain("الالتزامات والمصاريف الدورية");
    expect(container.textContent).toContain("إيجار فرع الرياض");
    const summary = container.querySelector('[data-testid="recurring-summary-strip"]');
    expect(summary.textContent).toContain("المصروف اليومي");
    expect(summary.textContent).toContain("326.09");
    expect(container.textContent).toContain("كل 6 أشهر");
    expect(container.textContent).toContain("2027-01-01");
});


test("opens one slim dynamic editor instead of large cards", async () => {
    await renderWorkspace();

    await act(async () => {
        container.querySelector('[data-testid="add-recurring-obligation"]').dispatchEvent(
            new MouseEvent("click", { bubbles: true }),
        );
    });

    const editor = document.querySelector('[data-testid="recurring-obligation-editor"]');
    expect(editor).not.toBeNull();
    expect(editor.textContent).toContain("نوع الالتزام");
    expect(editor.textContent).toContain("الفرع أو المستودع");
    expect(editor.textContent).toContain("شهري");
    expect(editor.textContent).toContain("كل سنتين");
});
