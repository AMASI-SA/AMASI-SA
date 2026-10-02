import React, { act } from "react";
import { createRoot } from "react-dom/client";

import PreparationFilesRegistry from "./PreparationFilesRegistry";
import { listPreparationFiles } from "../../services/orderReviewEngine";

jest.mock("../../services/orderReviewEngine", () => ({
    listPreparationFiles: jest.fn(),
    recoverStalePreparationFiles: jest.fn(),
    repairPreparationBatchCustomerOptions: jest.fn(),
    reviewedPreparationBatchPdfUrl: (batchId) => `/api/reviewed-preparation-batches-v1/batches/${encodeURIComponent(batchId)}/pdf`,
}));

test("exposes a stable authenticated PDF link without changing the download control", async () => {
    listPreparationFiles.mockResolvedValue({
        items: [{
            batch_id: "batch/id",
            file_number: "PF-20260828-0079",
            file_name: "ملف خالد.pdf",
            file_title: "اختبار AMS13067 - 3 قطع - خالد",
            allocated_quantity: 3,
            order_count: 3,
            file_date_display: "2026/8/28",
            responsible_employee_name: "خالد",
        }],
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const previousActEnvironment = globalThis.IS_REACT_ACT_ENVIRONMENT;
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    try {
        await act(async () => root.render(<PreparationFilesRegistry />));
        expect(listPreparationFiles).toHaveBeenCalledWith({ limit: 100 });
        const link = container.querySelector('[data-testid="download-preparation-file-pdf"]');
        expect(link).not.toBeNull();
        expect(link.tagName).toBe("A");
        expect(link.getAttribute("href")).toBe(
            "/api/reviewed-preparation-batches-v1/batches/batch%2Fid/pdf",
        );
        expect(link.getAttribute("download")).toBe("ملف خالد.pdf");
        expect(link.textContent).toContain("تحميل PDF");
    } finally {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    }
});
