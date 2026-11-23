import api from "../lib/api";
import { createReviewedPreparationBatch } from "./orderReviewEngine";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ReviewedOrders from "../pages/ReviewedOrders";

jest.mock("../lib/api", () => ({ __esModule: true, API_BASE: "https://synthetic.test/api",
    default: { get: jest.fn(), post: jest.fn() } }));

const scope = "a".repeat(64);
const selections = [{ group_key: "bundle-card", quantity: 1, revision: "b".repeat(64), order_number: "synthetic-order" }];
const options = { schema: 2, contract: "preparation_assignment_v2", session_scope: scope,
    actor: { id: "owner" }, capabilities: { atomic_publication: true, defer: true } };

beforeEach(() => {
    api.get.mockReset(); api.post.mockReset(); localStorage.clear();
    window.__mezanPreparationFileMetadata = { fileTitle: "دفعة البكج", responsibleEmployeeId: "employee",
        expectedQuantity: 2, selectedProductCount: 1, scheduleMode: "required", requiredDueAt: "2026-12-01T10:00:00.000Z" };
});

function commit(body) {
    return { data: {
        ...options, client_request_id: body.client_request_id, state: "COMMITTED",
        command: body,
        receipt: { request_fingerprint: "c".repeat(64), responsible_employee_id: "employee",
            supplier_assignment: { mode: "defer" }, destination: "waiting", dispatch: null,
            piece_count: 2, physical_quantity: 2, commercial_quantity: 1, piece_ids: ["leaf-a", "leaf-b"],
            file: { batch_id: "batch", file_number: "PF-SYN", client_request_id: body.client_request_id,
                status: "ready", file_name: "two.pdf", file_title: body.file_title,
                selected_product_count: 1, allocated_quantity: 1, order_count: 1,
                responsible_employee_id: "employee", schedule_mode: "required", required_due_at: body.required_due_at },
            batch: { batch_id: "batch", status: "ready", allocated_quantity: 1, physical_quantity: 2,
                piece_ids: ["leaf-a", "leaf-b"], selections },
        },
    } };
}

test("supported bundle selection uses one atomic deferred assignment without legacy publication", async () => {
    api.get.mockResolvedValue({ data: options });
    api.post.mockImplementation(async (_path, body) => commit(body));
    const result = await createReviewedPreparationBatch({ clientRequestId: "request-bundle-1", selections,
        supplierBundle: true, physicalQuantity: 2, deferSupplierConfirmed: true });
    expect(api.get).toHaveBeenCalledWith("/preparation-assignment-v2/options");
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith("/preparation-assignment-v2/requests", expect.objectContaining({
        client_request_id: "request-bundle-1", expected_session_scope: scope,
        expected_quantity: 1, selected_product_count: 1, selections,
        supplier_assignment: { mode: "defer" }, schedule_mode: "required",
    }));
    expect(result).toMatchObject({ batch_id: "batch", physical_quantity: 2,
        assignment_contract: "preparation_assignment_v2", schedule_committed: true });
});

test("ordinary selection retains the current draft batch and finalize flow", async () => {
    api.post.mockResolvedValueOnce({ data: { id: "draft" } })
        .mockResolvedValueOnce({ data: { batch_id: "ordinary-batch" } })
        .mockResolvedValueOnce({ data: { batch_id: "ordinary-batch", file_number: "PF-ORDINARY" } });
    await createReviewedPreparationBatch({ clientRequestId: "request-ordinary", selections: [{ group_key: "ordinary", quantity: 1 }] });
    expect(api.get).not.toHaveBeenCalled();
    expect(api.post.mock.calls.map(([path]) => path)).toEqual([
        "/preparation-file-registry-v1/drafts", "/reviewed-preparation-batches-v1/batches",
        "/preparation-file-registry-v1/finalize/request-ordinary",
    ]);
});

async function withPage(check) {
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container); const previousAct = globalThis.IS_REACT_ACT_ENVIRONMENT;
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    try { await act(async () => root.render(<ReviewedOrders />)); await check(container); }
    finally { await act(async () => root.unmount()); container.remove(); globalThis.IS_REACT_ACT_ENVIRONMENT = previousAct; }
}

test("the reviewed page requires explicit defer and counts the selected pair as two physical pieces", async () => {
    const product = { group_key: "bundle-card", revision: "b".repeat(64), name: "بكج", piece_level: true,
        bundle_supplier: true, physical_quantity: 2, quantity: 1, remaining_quantity: 1,
        unit_index: 1, unit_total: 1, source_lines: [{ order_number: "synthetic-order" }] };
    api.get.mockImplementation(async (path) => ({ data: path === "/reviewed-products-v1/catalog"
        ? { products: [product], categories: [], summary: {}, selection_grain: "physical_piece" } : options }));
    api.post.mockImplementation(async (_path, body) => commit(body));
    const confirm = jest.spyOn(window, "confirm").mockReturnValue(true);
    const anchorClick = jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    try {
        await withPage(async (container) => {
            expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/reviewed-products-v1/catalog"]);
            expect(api.post).not.toHaveBeenCalled();
            expect(container.textContent).toContain("بكج يحتوي 2 قطعة");
            await act(async () => container.querySelector('[data-testid="reviewed-piece-checkbox"]').click());
            const bar = container.querySelector('[data-testid="reviewed-preparation-selection-bar"]');
            const create = [...bar.querySelectorAll("button")].find((node) => node.textContent === "إنشاء وتحميل الملف");
            expect(bar.textContent).toContain("البطاقات المحددة1");
            expect(bar.textContent).toContain("قطع هذا الملف2");
            expect(create.disabled).toBe(true);
            await act(async () => create.click());
            expect(api.post).not.toHaveBeenCalled();
            await act(async () => bar.querySelector('[data-testid="supplier-bundle-defer-confirmation"]').click());
            expect(create.disabled).toBe(false);
            await act(async () => create.click());
            expect(api.post).toHaveBeenCalledTimes(1);
            expect(api.post.mock.calls[0][1]).toMatchObject({ expected_quantity: 1, selections,
                supplier_assignment: { mode: "defer" }, expected_session_scope: scope });
            expect(container.querySelector('[data-testid="reviewed-preparation-batch-success"]').textContent).toContain("2 قطعة");
            expect(container.textContent).toContain("ينتظر إرساله للمورد");
        });
    } finally { confirm.mockRestore(); anchorClick.mockRestore(); }
});

test("a catalog outage does not hide explicit recovery or trigger assignment lookups", async () => {
    api.get.mockRejectedValue(new Error("catalog unavailable"));
    await withPage(async (container) => {
        expect(container.querySelector('[data-testid="supplier-bundle-preparation-recovery"]')).not.toBeNull();
        expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/reviewed-products-v1/catalog"]);
        expect(api.post).not.toHaveBeenCalled();
    });
});
