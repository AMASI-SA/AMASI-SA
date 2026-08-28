jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {},
    API_BASE: "https://mezansalla.test/api",
}));

import {
    downloadReviewedPreparationBatchPdf,
    reviewedPreparationBatchPdfUrl,
} from "./orderReviewEngine";

describe("reviewed preparation batch PDF download", () => {
    test("uses the authenticated PDF endpoint directly instead of a temporary blob URL", async () => {
        const click = jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
        const append = jest.spyOn(document.body, "appendChild");

        await expect(downloadReviewedPreparationBatchPdf("batch/id", "ملف خالد.pdf")).resolves.toEqual({
            ok: true,
            batchId: "batch/id",
            fileName: "ملف خالد.pdf",
            contentType: "application/pdf",
        });

        const anchor = append.mock.calls.at(-1)[0];
        expect(anchor.href).toBe("https://mezansalla.test/api/reviewed-preparation-batches-v1/batches/batch%2Fid/pdf");
        expect(anchor.download).toBe("ملف خالد.pdf");
        expect(click).toHaveBeenCalledTimes(1);
        expect(document.body.contains(anchor)).toBe(false);

        click.mockRestore();
        append.mockRestore();
    });

    test("builds a safe endpoint for the batch id", () => {
        expect(reviewedPreparationBatchPdfUrl("batch id/1"))
            .toBe("https://mezansalla.test/api/reviewed-preparation-batches-v1/batches/batch%20id%2F1/pdf");
    });
});
