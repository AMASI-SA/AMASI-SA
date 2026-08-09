import {
    infinitePaginationState,
    mergePaginatedRows,
} from "./infiniteScrollPagination";

test("appends the next page while preserving existing order and replacing duplicates", () => {
    const merged = mergePaginatedRows(
        [
            { id: "campaign-1", spend: 10 },
            { id: "campaign-2", spend: 20 },
        ],
        [
            { id: "campaign-2", spend: 25 },
            { id: "campaign-3", spend: 30 },
        ],
        (row) => row.id,
    );

    expect(merged).toEqual([
        { id: "campaign-1", spend: 10 },
        { id: "campaign-2", spend: 25 },
        { id: "campaign-3", spend: 30 },
    ]);
});

test("uses the requested page to decide whether another automatic load is available", () => {
    expect(infinitePaginationState({
        pagination: { page: 1, pages: 3, total: 55 },
        requestedPage: 2,
        loaded: 50,
    })).toEqual({
        page: 2,
        pages: 3,
        total: 55,
        hasMore: true,
    });

    expect(infinitePaginationState({
        pagination: { page: 1, pages: 3, total: 55 },
        requestedPage: 3,
        loaded: 55,
    }).hasMore).toBe(false);
});
