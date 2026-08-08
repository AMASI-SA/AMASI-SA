import { renderToStaticMarkup } from "react-dom/server";

jest.mock("../../services/preparationWorkService", () => ({
    getMyPreparationWork: jest.fn(),
    getPreparationManagerSummary: jest.fn(),
}));

import PreparationWorkDashboard, {
    fileEstimatedDueAt,
    filePieces,
    filePiecesAreReady,
    inProgressFiles,
    riyadhDateInputValue,
} from "./PreparationWorkDashboard";


test("piece selection stays inside its preparation batch", () => {
    const pieces = [
        { piece_id: "a", batch_id: "batch-1" },
        { piece_id: "b", batch_id: "batch-2" },
        { piece_id: "c", batch_id: "batch-1" },
    ];

    expect(filePieces(pieces, "batch-1").map((row) => row.piece_id)).toEqual([
        "a",
        "c",
    ]);
});


test("automatic file deadline uses the latest piece estimate", () => {
    const pieces = [
        {
            piece_id: "a",
            batch_id: "batch-1",
            estimated_due_at: "2026-08-05T10:00:00Z",
        },
        {
            piece_id: "b",
            batch_id: "batch-1",
            estimated_due_at: "2026-08-05T14:00:00Z",
        },
        {
            piece_id: "c",
            batch_id: "batch-2",
            estimated_due_at: "2026-08-07T14:00:00Z",
        },
    ];

    expect(fileEstimatedDueAt(pieces, "batch-1")).toBe(
        "2026-08-05T14:00:00.000Z",
    );
});


test("Riyadh date input uses an ISO-like local business date", () => {
    const value = riyadhDateInputValue(new Date("2026-08-03T21:30:00Z"));
    expect(value).toBe("2026-08-04");
});


test("file details stay guarded until every expected piece is materialized", () => {
    expect(filePiecesAreReady({
        expected_piece_count: 1,
        piece_count: 0,
        piece_registry_status: "recovery_required",
    })).toBe(false);
    expect(filePiecesAreReady({
        expected_piece_count: 1,
        piece_count: 1,
        piece_registry_status: "ready",
    })).toBe(true);
});


test("in-progress stage excludes files still waiting in the employee my-products page", () => {
    const files = [
        { file_number: "PF-100", execution_status: "assigned" },
        { file_number: "PF-101", execution_status: "in_progress" },
    ];

    expect(inProgressFiles(files).map((file) => file.file_number)).toEqual(["PF-101"]);
});


test("in-progress dashboard starts with piece details", () => {
    const markup = renderToStaticMarkup(<PreparationWorkDashboard />);

    expect(markup).toContain('data-testid="preparation-work-dashboard"');
    expect(markup).toContain("جارٍ تحميل منتجاتك");
    expect(markup).toContain("تفاصيل القطع");
    expect(markup).toContain("منتجات غير مسندة");
    expect(markup).not.toContain("بدء التنفيذ");
});


test("standalone dashboard renders my products without fulfillment tabs", () => {
    const markup = renderToStaticMarkup(
        <PreparationWorkDashboard initialView="my-products" standalone />,
    );

    expect(markup).toContain("جارٍ تحميل إدارة منتجاتي");
    expect(markup).not.toContain("تفاصيل القطع");
});
