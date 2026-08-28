import api from "../lib/api";
import {
    getPreparationSupplierWorkspace,
    normalizeSupplierDispatchPayload,
} from "./preparationSupplierDispatch";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
        post: jest.fn(),
    },
}));


beforeEach(() => {
    jest.clearAllMocks();
});


test("employee supplier workspace always requests one card per physical piece", async () => {
    api.get.mockResolvedValue({ data: { ok: true, files: [] } });

    await expect(getPreparationSupplierWorkspace({ limit: 200 })).resolves.toEqual({
        ok: true,
        files: [],
    });
    expect(api.get).toHaveBeenCalledWith("/supplier-dispatch-v1/workspace", {
        params: { limit: 200, grain: "piece" },
    });
});


test("single preparation file uses legacy stable dispatch payload", () => {
    const payload = normalizeSupplierDispatchPayload({
        client_request_id: "supplier-dispatch:test-1",
        supplier_id: "supplier-1",
        note: null,
        files: [{
            file_number: "PF-20260820-0031",
            selections: [
                { group_key: "product:1::service:a", quantity: 2 },
                { group_key: "product:1::service:b", quantity: 1 },
            ],
        }],
    });

    expect(payload).toEqual({
        client_request_id: "supplier-dispatch:test-1",
        supplier_id: "supplier-1",
        note: null,
        file_number: "PF-20260820-0031",
        selections: [
            { group_key: "product:1::service:a", quantity: 2 },
            { group_key: "product:1::service:b", quantity: 1 },
        ],
    });
    expect(payload.files).toBeUndefined();
});


test("multiple source files keep the multi-file payload", () => {
    const payload = {
        client_request_id: "supplier-dispatch:test-2",
        supplier_id: "supplier-1",
        files: [
            { file_number: "PF-1", selections: [{ group_key: "a", quantity: 1 }] },
            { file_number: "PF-2", selections: [{ group_key: "b", quantity: 1 }] },
        ],
    };

    expect(normalizeSupplierDispatchPayload(payload)).toBe(payload);
});
