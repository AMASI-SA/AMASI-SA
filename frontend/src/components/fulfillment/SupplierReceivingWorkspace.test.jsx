import { renderToStaticMarkup } from "react-dom/server";

jest.mock("../../services/supplierReceiving", () => ({
    closeSupplierReceivingSession: jest.fn(),
    loadSupplierReceivingCatalog: jest.fn(),
    newSupplierReceivingRequestId: jest.fn(() => "supplier-receiving:test-1"),
    openSupplierReceivingSession: jest.fn(),
    scanSupplierReceivingPiece: jest.fn(),
}));

import SupplierReceivingWorkspace, {
    formatReceivingDate,
    supplierDisplayName,
} from "./SupplierReceivingWorkspace";


test("supplier receiving stage exposes governed barcode session controls", () => {
    const markup = renderToStaticMarkup(<SupplierReceivingWorkspace />);

    expect(markup).toContain('data-testid="supplier-receiving-workspace"');
    expect(markup).toContain("استلام منتجات المورد بالباركود");
    expect(markup).toContain('data-testid="supplier-receiving-open-form"');
    expect(markup).toContain("لا ينشئ فاتورة أو مديونية");
    expect(markup).toContain("لا يرسل شيئًا إلى قيود أو سلة");
    expect(markup).toContain("من المورد");
});

test("supplier session helpers keep supplier and Riyadh display stable", () => {
    expect(supplierDisplayName({
        supplier: { company_name: "مورد أماسي" },
    })).toBe("مورد أماسي");
    expect(supplierDisplayName(null)).toBe("مورد غير محدد");
    expect(formatReceivingDate("invalid")).toBe("—");
    expect(formatReceivingDate("2026-08-04T10:00:00Z")).not.toBe("—");
});
