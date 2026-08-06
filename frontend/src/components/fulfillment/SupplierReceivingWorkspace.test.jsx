import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

const mockZxingStop = jest.fn();
const mockZxingDecodeFromVideoElement = jest.fn();

jest.mock("@zxing/browser", () => ({
    BarcodeFormat: { QR_CODE: "QR_CODE", CODE_128: "CODE_128" },
    BrowserMultiFormatReader: jest.fn().mockImplementation(() => ({
        decodeFromVideoElement: mockZxingDecodeFromVideoElement,
    })),
}));

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
import { BrowserMultiFormatReader } from "@zxing/browser";


test("supplier receiving stage exposes governed barcode session controls", () => {
    const markup = renderToStaticMarkup(<SupplierReceivingWorkspace />);

    expect(markup).toContain('data-testid="supplier-receiving-workspace"');
    expect(markup).toContain("استلام منتجات المورد بالباركود");
    expect(markup).toContain('data-testid="supplier-receiving-open-form"');
    expect(markup).toContain("فاتورة مورد محاسبية واحدة داخل ميزان 2");
    expect(markup).toContain("مديونية المورد");
    expect(markup).toContain("لا يُرسل شيء إلى قيود أو سلة");
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
    expect(markup).toContain("اعتماد فاتورة المورد وإنهاء الجلسة");
    expect(markup).toContain("إغلاق الكاميرا");
    expect(markup).toContain("إلغاء الجلسة والخروج");
});

test("supplier camera falls back to ZXing when BarcodeDetector is unavailable", async () => {
    const originalMediaDevices = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");
    const originalBarcodeDetector = globalThis.BarcodeDetector;
    const originalPlay = HTMLMediaElement.prototype.play;
    const videoTrack = { stop: jest.fn() };
    const stream = { getTracks: jest.fn(() => [videoTrack]) };
    const getUserMedia = jest.fn().mockResolvedValue(stream);
    const onDetected = jest.fn().mockResolvedValue(undefined);
    BrowserMultiFormatReader.mockImplementation(() => ({
        decodeFromVideoElement: mockZxingDecodeFromVideoElement,
    }));
    mockZxingDecodeFromVideoElement.mockImplementation(async (_video, callback) => {
        callback({ getText: () => "PIECE-QR-1" });
        return { stop: mockZxingStop };
    });
    Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: { getUserMedia },
    });
    delete globalThis.BarcodeDetector;
    HTMLMediaElement.prototype.play = jest.fn().mockResolvedValue(undefined);
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    try {
        await act(async () => {
            root.render(<SupplierPieceCameraScanner onDetected={onDetected} onClose={() => {}} />);
            await new Promise((resolve) => window.setTimeout(resolve, 20));
        });

        expect(getUserMedia).toHaveBeenCalledWith(expect.objectContaining({ video: expect.any(Object), audio: false }));
        expect(BrowserMultiFormatReader).toHaveBeenCalled();
        expect(mockZxingDecodeFromVideoElement).toHaveBeenCalled();
        expect(onDetected).toHaveBeenCalledWith("PIECE-QR-1");
        expect(container.querySelector('[data-camera-engine="zxing"]')).not.toBeNull();
        expect(container.textContent).not.toContain("هذا المتصفح لا يدعم قراءة QR بالكاميرا");
    } finally {
        await act(async () => root.unmount());
        container.remove();
        if (originalMediaDevices) {
            Object.defineProperty(navigator, "mediaDevices", originalMediaDevices);
        } else {
            delete navigator.mediaDevices;
        }
        if (originalBarcodeDetector) {
            globalThis.BarcodeDetector = originalBarcodeDetector;
        } else {
            delete globalThis.BarcodeDetector;
        }
        HTMLMediaElement.prototype.play = originalPlay;
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }

    expect(mockZxingStop).toHaveBeenCalled();
    expect(videoTrack.stop).toHaveBeenCalled();
});

test("invoice lines group identical scanned products and calculate totals", () => {
    const base = {
        product_id: "product-1",
        product_name: "سلسال بالاسم",
        sku: "N-1",
        services: [{ service_id: "engrave", required_quantity: 1, reference_unit_cost: 3.5 }],
        invoice_services: [{ service_id: "engrave", service_name: "حفر", required_quantity: 1, reference_unit_price_halalas: 350 }],
        reference_product_unit_price_halalas: 700,
        reference_product_price_complete: true,
    };
    const scans = [
        { ...base, piece_id: "piece-2" },
        { ...base, piece_id: "piece-1" },
    ];
    const initial = buildSupplierInvoiceLines(scans);

    expect(initial).toHaveLength(1);
    expect(initial[0].quantity).toBe(2);
    expect(initial[0].product_unit_price_halalas).toBe(700);
    expect(initial[0].services[0].unit_price_halalas).toBe(350);
    expect(initial[0].total_halalas).toBe(2100);

    const key = supplierInvoiceLineKey(base);
    const overridden = buildSupplierInvoiceLines(scans, {
        [key]: { product_unit_price_halalas: 800 },
    });
    expect(overridden[0].product_unit_price_halalas).toBe(800);
    expect(overridden[0].total_halalas).toBe(2300);
});

test("invoice lines do not merge pieces with different pending supplier services", () => {
    const base = {
        product_id: "product-1",
        product_name: "سلسال بالاسم",
        sku: "N-1",
        services: [
            { service_id: "engrave", required_quantity: 1 },
            { service_id: "paint", required_quantity: 1 },
        ],
        reference_product_unit_price_halalas: 500,
    };
    const lines = buildSupplierInvoiceLines([
        {
            ...base,
            piece_id: "piece-1",
            invoice_services: [{ service_id: "engrave", required_quantity: 1, reference_unit_price_halalas: 300 }],
        },
        {
            ...base,
            piece_id: "piece-2",
            invoice_services: [{ service_id: "paint", required_quantity: 1, reference_unit_price_halalas: 200 }],
        },
    ]);

    expect(lines).toHaveLength(2);
    expect(lines.map((line) => line.services[0].service_id).sort()).toEqual(["engrave", "paint"]);
});

test("camera invoice shows one compact row with image quantity unit price and total", () => {
    const markup = renderToStaticMarkup(
        <SupplierPieceCameraScanner
            onDetected={() => {}}
            onClose={() => {}}
            invoiceLines={[{
                key: "product-1",
                product_name: "تعليقة اليوم الوطني",
                sku: "ND-96",
                selected_image_url: "https://cdn.example.test/national-day.png",
                quantity: 3,
                product_unit_price_halalas: 500,
                product_reference_price_complete: true,
                services: [{
                    service_id: "cut",
                    service_name: "قص",
                    quantity_per_piece: 1,
                    unit_price_halalas: 250,
                    total_halalas: 750,
                    selected: true,
                }],
                total_halalas: 2250,
            }]}
            permissions={{ can_edit_product_price: true, can_edit_service_price: true }}
        />,
    );

    expect(markup).toContain("سعر المنتج الأساسي للقطعة");
    expect(markup).toContain("الخدمات التي نفذها المورد");
    expect(markup).toContain("سعر الوحدة");
    expect(markup).toContain("الإجمالي");
    expect(markup).toContain("تعليقة اليوم الوطني");
    expect(markup).toContain('data-testid="supplier-receiving-mobile-invoice-row"');
    expect(markup).toContain("https://cdn.example.test/national-day.png");
    expect(markup).toContain("<img");
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
        expect(container.querySelector('[data-testid="supplier-receiving-camera-button-mobile"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="supplier-receiving-mobile-active-session"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="supplier-receiving-mobile-invoice"]')).not.toBeNull();
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
        invoice_services: [{ service_id: "engrave", service_name: "حفر", required_quantity: 1, reference_unit_price_halalas: 400 }],
        reference_product_unit_price_halalas: 500,
        reference_product_price_complete: true,
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
            supplier: { company_name: "مورد أماسي", service_links: [{ service_id: "engrave" }] },
        },
        permissions: {
            can_edit_product_price: false,
            can_edit_service_price: false,
            can_add_service: false,
        },
        service_catalog: [{ id: "engrave", name: "حفر", unit_cost: 4 }],
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
        const reviewButton = Array.from(container.querySelectorAll("button")).find(
            (button) => button.textContent.includes("مراجعة الخدمات والأسعار قبل الاعتماد"),
        );
        expect(reviewButton).not.toBeUndefined();
        await act(async () => {
            reviewButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
            await new Promise((resolve) => window.setTimeout(resolve, 0));
        });
        const saveButton = container.querySelector('[data-testid="supplier-receiving-save-invoice"]');
        expect(saveButton).not.toBeNull();
        await act(async () => {
            saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
            await new Promise((resolve) => window.setTimeout(resolve, 0));
        });

        expect(closeSupplierReceivingSession).toHaveBeenCalledWith("session-save-1", {
            note: "",
            invoice_lines: [{
                piece_ids: ["piece-1"],
                product_unit_price_halalas: 500,
                services: [{
                    service_id: "engrave",
                    unit_price_halalas: 400,
                    add_to_product: false,
                }],
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
