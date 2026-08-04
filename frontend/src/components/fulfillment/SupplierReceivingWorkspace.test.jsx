import { act } from "react";
import { createRoot } from "react-dom/client";
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
    SupplierPieceCameraScanner,
    supplierDisplayName,
} from "./SupplierReceivingWorkspace";
import { loadSupplierReceivingCatalog } from "../../services/supplierReceiving";


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

test("supplier camera scanner exposes a clear mobile QR capture surface", () => {
    const markup = renderToStaticMarkup(
        <SupplierPieceCameraScanner onDetected={() => {}} onClose={() => {}} />,
    );

    expect(markup).toContain('data-testid="supplier-receiving-camera-dialog"');
    expect(markup).toContain("تصوير QR القطعة");
    expect(markup).toContain("الكاميرا الخلفية");
    expect(markup).toContain("سيستلمها ميزان تلقائيًا");
    expect(markup).toContain("إغلاق");
});

test("an open supplier session shows the camera launch button", async () => {
    loadSupplierReceivingCatalog.mockResolvedValue({
        suppliers: [],
        sessions: [],
        active_session_scans: [],
        eligible_piece_count: 1,
        active_session: {
            id: "session-1",
            reference: "SR-TEST-1",
            scan_count: 0,
            opened_by_name: "خالد",
            opened_at: "2026-08-04T10:00:00Z",
            supplier: { company_name: "مورد أماسي" },
        },
    });
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    try {
        await act(async () => {
            root.render(<SupplierReceivingWorkspace />);
            await new Promise((resolve) => window.setTimeout(resolve, 0));
        });

        const cameraButton = container.querySelector('[data-testid="supplier-receiving-camera-button"]');
        expect(cameraButton).not.toBeNull();
        expect(cameraButton.textContent).toContain("فتح الكاميرا");
    } finally {
        act(() => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});
