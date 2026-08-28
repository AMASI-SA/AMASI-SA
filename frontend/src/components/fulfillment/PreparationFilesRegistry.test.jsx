import { render, screen } from "@testing-library/react";

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

    render(<PreparationFilesRegistry />);

    const link = await screen.findByTestId("download-preparation-file-pdf");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute(
        "href",
        "/api/reviewed-preparation-batches-v1/batches/batch%2Fid/pdf",
    );
    expect(link).toHaveAttribute("download", "ملف خالد.pdf");
    expect(link).toHaveTextContent("تحميل PDF");
});
