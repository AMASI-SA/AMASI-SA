import { act } from "react";
import { createRoot } from "react-dom/client";
import { loadMezanSupplierFinancials, loadMezanSuppliersWorkspace } from "../services/mezanSuppliersV2";
import { renderToStaticMarkup } from "react-dom/server";

jest.mock("react-router-dom", () => ({
    Link: ({ to, children }) => <a href={to}>{children}</a>,
    useSearchParams: () => [new URLSearchParams(), jest.fn()],
}));

jest.mock("../services/mezanSuppliersV2", () => ({
    createMezanSupplier: jest.fn(),
    loadMezanSupplierFinancials: jest.fn(() => new Promise(() => {})),
    loadMezanSuppliersWorkspace: jest.fn(() => new Promise(() => {})),
    updateMezanSupplier: jest.fn(),
}));

jest.mock("../services/supplierReceiving", () => ({
    downloadSupplierReceivingInvoicePdf: jest.fn(),
}));

import MezanSuppliersV2, {
    SupplierFinancialDetail,
    formatSupplierHalalas,
    supplierFormFromRow,
    supplierMatchesQuery,
} from "./MezanSuppliersV2";


test("Mezan 2 supplier page states the independent governed supplier contract", () => {
    const markup = renderToStaticMarkup(<MezanSuppliersV2 />);

    expect(markup).toContain('data-testid="mezan-suppliers-v2-page"');
    expect(markup).toContain("الموردون");
    expect(markup).toContain("موردون وفواتير ومديونيات ميزان 2 فقط");
    expect(markup).toContain("1 · الاستلام");
    expect(markup).toContain("2 · الفاتورة");
    expect(markup).toContain("3 · حساب المورد");
    expect(markup).toContain("هوية المورد من سجل ميزان 2 فقط");
    expect(markup).toContain("الفواتير التاريخية لا تعيد إنشاء دين");
    expect(markup).toContain("إجمالي ديون الموردين");
    expect(markup).toContain('data-testid="mezan-supplier-add-button"');
});


test("supplier editor model preserves only explicit Mezan 2 service ids", () => {
    expect(supplierFormFromRow({
        company_name: "مورد الحفر",
        service_ids: ["engrave"],
        status: "active",
    })).toMatchObject({
        company_name: "مورد الحفر",
        service_ids: ["engrave"],
        status: "active",
    });
    expect(supplierFormFromRow(null).service_ids).toEqual([]);
});


test("supplier search includes linked service names", () => {
    const supplier = {
        company_name: "مورد أماسي",
        service_links: [{ service_name: "حفر الاسم" }],
    };
    expect(supplierMatchesQuery(supplier, "حفر")).toBe(true);
    expect(supplierMatchesQuery(supplier, "طباعة")).toBe(false);
});


test("supplier financial detail separates eligible balances from historical invoices", () => {
    const markup = renderToStaticMarkup(
        <SupplierFinancialDetail
            supplier={{
                id: "supplier-v2-1",
                company_name: "مورد ميزان 2",
                financial: {
                    outstanding_halalas: 11_000,
                    invoiced_halalas: 11_000,
                    paid_halalas: 0,
                    real_invoice_count: 1,
                    experiment_invoice_count: 1,
                },
            }}
            invoices={[
                {
                    id: "real-1",
                    supplier_id: "supplier-v2-1",
                    invoice_number: "SI-001",
                    total_halalas: 11_000,
                    piece_count: 2,
                    lines: [],
                    financial_eligible: true,
                    paid_halalas: 3000,
                    outstanding_halalas: 8000,
                    payment_status: "partial",
                },
                {
                    id: "experiment-1",
                    supplier_id: "supplier-v2-1",
                    invoice_number: "SI-TEST-001",
                    total_halalas: 11_000,
                    piece_count: 2,
                    lines: [],
                    financial_eligible: false,
                    exclusion_reason: "history_only",
                },
            ]}
            timeline={[{
                id: "ledger-1",
                kind: "invoice",
                amount_halalas: 11_000,
                notes: "فاتورة مورد ميزان 2",
            }]}
            workspace={{ financial_status: "available" }}
            downloadBusy=""
            onDownload={jest.fn()}
            onClose={jest.fn()}
        />,
    );

    expect(markup).toContain('data-testid="mezan-supplier-real-invoice"');
    expect(markup).toContain('data-testid="mezan-supplier-history-invoice"');
    expect(markup).toContain("مسددة جزئيًا");
    expect(markup).toContain("80.00");
    expect(markup).toContain("لا ينشئ رصيدًا مستحقًا");
    expect(markup).toContain("دون مقاصة تلقائية");
    expect(formatSupplierHalalas(null)).toBe("غير متاح");
    expect(formatSupplierHalalas(11_000)).toBe("110.00");
});


test("financial reload failure clears stale balances and blocks an existing payment form", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const supplier = { id: "supplier-v2", company_name: "مورد ميزان", status: "active" };
    loadMezanSuppliersWorkspace.mockResolvedValue({ suppliers: [supplier], services: [], summary: {} });
    loadMezanSupplierFinancials.mockResolvedValueOnce({
        financial_status: "available", payment_available: true,
        payment_accounts: [{ id: "bank-v2", name: "بنك" }],
        suppliers: [{ ...supplier, financial: { outstanding_halalas: 100000, advance_halalas: 0 } }],
        invoices: [], timeline: [], summary: {},
    }).mockRejectedValueOnce(new Error("فشل تحديث الحساب المالي"));
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
        await act(async () => root.render(<MezanSuppliersV2 />));
        await act(async () => container.querySelector('[data-testid="mezan-supplier-financial-open-supplier-v2"]').click());
        await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "دفع مبلغ للمورد").click());
        expect(container.querySelector('form button[type="submit"]').disabled).toBe(false);
        expect(container.textContent).toContain("1,000.00");
        await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "تحديث").click());
        expect(container.textContent).toContain("فشل تحديث الحساب المالي");
        expect(container.textContent).not.toContain("1,000.00");
        expect(container.querySelector('form button[type="submit"]').disabled).toBe(true);
        expect(container.querySelector('[data-testid="mezan-supplier-financial-detail"]')).not.toBeNull();
    } finally {
        await act(async () => root.unmount());
        container.remove();
    }
});
