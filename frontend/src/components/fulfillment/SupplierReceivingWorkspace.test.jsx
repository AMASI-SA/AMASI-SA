import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

jest.mock("react-router-dom", () => ({
    Link: ({ to, children }) => <a href={to}>{children}</a>,
}));

jest.mock("../../services/supplierReceiving", () => ({
    cancelSupplierReceivingSession: jest.fn(),
    closeSupplierReceivingSession: jest.fn(),
    loadSupplierReceivingCatalog: jest.fn(),
    newSupplierReceivingRequestId: jest.fn(() => "supplier-receiving:test-1"),
    openSupplierReceivingSession: jest.fn(),
    scanSupplierReceivingPiece: jest.fn(),
}));

import SupplierReceivingWorkspace, {
    buildSupplierInvoiceLines,
    formatReceivingDate,
    SupplierPieceCameraScanner,
    supplierInvoiceLineKey,
    supplierDisplayName,
} from "./SupplierReceivingWorkspace";
import {
    cancelSupplierReceivingSession,
    closeSupplierReceivingSession,
    loadSupplierReceivingCatalog,
} from "../../services/supplierReceiving";


test("supplier receiving stage exposes governed barcode session controls", () => {
    const markup = renderToStaticMarkup(<SupplierReceivingWorkspace />);

    expect(markup).toContain('data-testid="supplier-receiving-workspace"');
    expect(markup).toContain("استلام منتجات المورد بالباركود");
    expect(markup).toContain('data-testid="supplier-receiving-open-form"');
    expect(markup).toContain("فاتورة تشغيلية داخل ميزان 2");
    expect(markup).toContain("لا تنشئ مديونية أو قيدًا محاسبيًا");
    expect(markup).toContain("لا ترسل شيئًا إلى قيود أو سلة");
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

test("supplier camera stays open beside the live invoice draft", () => {
    const markup = renderToStaticMarkup(
        <SupplierPieceCameraScanner onDetected={() => {}} onClose={() => {}} />,
    );

    expect(markup).toContain('data-testid="supplier-receiving-camera-dialog"');
    expect(markup).toContain('data-testid="supplier-receiving-camera-split-layout"');
    expect(markup).toContain('data-testid="supplier-receiving-invoice-draft"');
    expect(markup).toContain("تصوير QR القطعة");
    expect(markup).toContain("الكاميرا تبقى مفتوحة");
    expect(markup).toContain("فاتورة المورد");
    expect(markup).toContain("حفظ الفاتورة وإنهاء الجلسة");
    expect(markup).toContain("إغلاق الكاميرا");
    expect(markup).toContain("إلغاء الجلسة والخروج");
});

test("invoice lines group identical scanned products and calculate totals", () => {
    const base = {
        product_id: "product-1",
        product_name: "سلسال بالاسم",
        sku: "N-1",
        services: [{ service_id: "engrave", required_quantity: 1 }],
        reference_unit_price_halalas: 1050,
        reference_price_complete: true,
    };
    const scans = [
        { ...base, piece_id: "piece-2" },
        { ...base, piece_id: "piece-1" },
    ];
    const initial = buildSupplierInvoiceLines(scans);

    expect(initial).toHaveLength(1);
    expect(initial[0].quantity).toBe(2);
    expect(initial[0].unit_price_halalas).toBe(1050);
    expect(initial[0].total_halalas).toBe(2100);

    const key = supplierInvoiceLineKey(base);
    const overridden = buildSupplierInvoiceLines(scans, { [key]: 1200 });
    expect(overridden[0].unit_price_halalas).toBe(1200);
    expect(overridden[0].total_halalas).toBe(2400);
});

test("camera invoice shows name, quantity, editable unit price and total without product images", () => {
    const markup = renderToStaticMarkup(
        <SupplierPieceCameraScanner
            onDetected={() => {}}
            onClose={() => {}}
            invoiceLines={[{
                key: "product-1",
                product_name: "تعليقة اليوم الوطني",
                sku: "ND-96",
                quantity: 3,
                unit_price_halalas: 750,
                total_halalas: 2250,
            }]}
        />,
    );

    expect(markup).toContain("اسم المنتج");
    expect(markup).toContain("عدد القطع");
    expect(markup).toContain("سعر الوحدة");
    expect(markup).toContain("الإجمالي");
    expect(markup).toContain("تعليقة اليوم الوطني");
    expect(markup).not.toContain("<img");
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

test("saving sends every grouped piece and edited unit price in one request", async () => {
    const scan = {
        piece_id: "piece-1",
        product_id: "product-1",
        product_name: "سلسال بالاسم",
        sku: "N-1",
        services: [{ service_id: "engrave", required_quantity: 1 }],
        reference_unit_price_halalas: 900,
        reference_price_complete: true,
    };
    loadSupplierReceivingCatalog.mockResolvedValue({
        suppliers: [],
        sessions: [],
        active_session_scans: [scan],
        eligible_piece_count: 0,
        active_session: {
            id: "session-save-1",
            reference: "SR-SAVE-1",
            scan_count: 1,
            opened_by_name: "خالد",
            opened_at: "2026-08-05T10:00:00Z",
            supplier: { company_name: "مورد أماسي" },
        },
    });
    closeSupplierReceivingSession.mockResolvedValue({ ok: true });
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    try {
        await act(async () => {
            root.render(<SupplierReceivingWorkspace />);
            await new Promise((resolve) => window.setTimeout(resolve, 0));
        });
        const saveButton = Array.from(container.querySelectorAll("button")).find(
            (button) => button.textContent.includes("حفظ الفاتورة وإنهاء الجلسة"),
        );
        expect(saveButton).not.toBeUndefined();
        await act(async () => {
            saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
            await new Promise((resolve) => window.setTimeout(resolve, 0));
        });

        expect(closeSupplierReceivingSession).toHaveBeenCalledWith("session-save-1", {
            note: "",
            invoice_lines: [{
                piece_ids: ["piece-1"],
                unit_price_halalas: 900,
            }],
        });
    } finally {
        act(() => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});

test("an empty session can be cancelled and exited without saving an invoice", async () => {
    loadSupplierReceivingCatalog
        .mockResolvedValueOnce({
            suppliers: [],
            sessions: [],
            active_session_scans: [],
            eligible_piece_count: 1,
            active_session: {
                id: "session-cancel-1",
                reference: "SR-CANCEL-1",
                scan_count: 0,
                opened_by_name: "أبو جبل",
                opened_at: "2026-08-05T10:00:00Z",
                supplier: { company_name: "مورد أماسي" },
            },
        })
        .mockResolvedValueOnce({
            suppliers: [],
            sessions: [],
            active_session_scans: [],
            eligible_piece_count: 1,
            active_session: null,
        });
    cancelSupplierReceivingSession.mockResolvedValue({
        ok: true,
        cancelled: true,
    });
    const originalConfirm = window.confirm;
    window.confirm = jest.fn(() => true);
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    try {
        await act(async () => {
            root.render(<SupplierReceivingWorkspace />);
            await new Promise((resolve) => window.setTimeout(resolve, 0));
        });
        const cancelButton = container.querySelector('[data-testid="supplier-receiving-cancel-session"]');
        expect(cancelButton).not.toBeNull();
        expect(cancelButton.disabled).toBe(false);

        await act(async () => {
            cancelButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
            await new Promise((resolve) => window.setTimeout(resolve, 0));
        });

        expect(window.confirm).toHaveBeenCalledWith(
            "هل تريد إلغاء الجلسة والخروج؟ لن تُحفظ فاتورة أو جلسة استلام.",
        );
        expect(cancelSupplierReceivingSession).toHaveBeenCalledWith(
            "session-cancel-1",
            { note: "" },
        );
        expect(container.querySelector('[data-testid="supplier-receiving-open-form"]')).not.toBeNull();
    } finally {
        act(() => root.unmount());
        container.remove();
        window.confirm = originalConfirm;
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});
