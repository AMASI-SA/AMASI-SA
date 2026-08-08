import { renderToStaticMarkup } from "react-dom/server";

jest.mock("../../services/preparationWorkService", () => ({
    getMyPreparationWork: jest.fn(),
    getPreparationManagerSummary: jest.fn(),
    startPreparationFile: jest.fn(),
}));

import PreparationWorkDashboard, {
    fileEstimatedDueAt,
    filePieces,
    filePiecesAreReady,
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


test("file cannot start until every expected piece is materialized", () => {
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


test("dashboard starts with the standalone approved my products page", () => {
    const markup = renderToStaticMarkup(<PreparationWorkDashboard />);

    expect(markup).toContain('data-testid="preparation-work-dashboard"');
    expect(markup).toContain("جارٍ تحميل إدارة منتجاتي");
    expect(markup).not.toContain("تبدأ هذه المرحلة من «إدارة منتجاتي»");
    expect(markup).not.toContain("تفاصيل القطع");
});
