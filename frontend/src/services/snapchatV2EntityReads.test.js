jest.mock("../lib/api", () => ({
    __esModule: true,
    default: { get: jest.fn() },
}));

import api from "../lib/api";
import {
    boundedControls,
    readSnapchatEntityPage,
    resetSnapchatEntityReadCoalescerForTests,
} from "./snapchatV2EntityReads";

describe("Snapchat V2 bounded entity reads", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        resetSnapchatEntityReadCoalescerForTests();
    });

    test("coalesces an identical in-flight read and sends bounded server controls", async () => {
        let resolve;
        api.get.mockReturnValue(new Promise((done) => { resolve = done; }));
        const input = {
            level: "campaign",
            dateFrom: "2026-09-01",
            dateTo: "2026-09-01",
            controls: { page: 2, pageSize: 25, search: "winter", activeOnly: true, sortBy: "spend", sortDirection: "asc" },
        };
        const first = readSnapchatEntityPage(input);
        const second = readSnapchatEntityPage(input);

        expect(first).toBe(second);
        expect(api.get).toHaveBeenCalledTimes(1);
        expect(api.get).toHaveBeenCalledWith(
            "/integrations-v2/snapchat-v2/campaigns",
            { params: expect.objectContaining({
                page: 2,
                page_size: 25,
                search: "winter",
                active_only: true,
                sort_by: "spend",
                sort_direction: "asc",
            }) },
        );
        resolve({ data: { request_id: "request-1" } });
        await first;
    });

    test("caps page size and rejects unsupported sort claims", () => {
        expect(boundedControls({ pageSize: 9_999, sortBy: "salla_sales" })).toEqual({
            page: 1,
            page_size: 100,
            search: "",
            active_only: false,
            sort_by: "default",
            sort_direction: "desc",
        });
    });
});
