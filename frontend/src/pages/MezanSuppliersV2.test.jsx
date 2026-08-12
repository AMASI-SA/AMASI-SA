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
    expect(markup).toContain("لا يتم استيراد أو قراءة أو ربط أي مورد أو رصيد من ميزان القديم");
    expect(markup).toContain("تأتي فقط من اعتماد الاستلام داخل ميزان 2");
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


test("supplier financial detail separates real debt from experiment invoices", () => {
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
                    experiment_mode: false,
                },
                {
                    id: "experiment-1",
                    supplier_id: "supplier-v2-1",
                    invoice_number: "SI-TEST-001",
                    total_halalas: 11_000,
                    piece_count: 2,
                    lines: [],
                    experiment_mode: true,
                },
            ]}
            timeline={[{
                id: "ledger-1",
                kind: "invoice",
                amount_halalas: 11_000,
                notes: "فاتورة مورد ميزان 2",
            }]}
            downloadBusy=""
            onDownload={jest.fn()}
            onClose={jest.fn()}
        />,
    );

    expect(markup).toContain('data-testid="mezan-supplier-real-invoice"');
    expect(markup).toContain('data-testid="mezan-supplier-experiment-invoice"');
    expect(markup).toContain("مديونية مسجلة");
    expect(markup).toContain("بلا مديونية");
    expect(markup).toContain("الرصيد من دفتر الأستاذ العام");
    expect(formatSupplierHalalas(11_000)).toBe("110.00");
});
