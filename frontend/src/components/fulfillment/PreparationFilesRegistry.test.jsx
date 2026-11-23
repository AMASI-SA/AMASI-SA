import React, { act } from "react";
import { createRoot } from "react-dom/client";

import PreparationFilesRegistry from "./PreparationFilesRegistry";
import {
    listPreparationFiles,
    reconcilePreparationBatchStatuses,
    recoverStalePreparationFiles,
    repairPreparationBatchCustomerOptions,
} from "../../services/orderReviewEngine";

jest.mock("../../services/orderReviewEngine", () => ({
    listPreparationFiles: jest.fn(),
    reconcilePreparationBatchStatuses: jest.fn(),
    recoverStalePreparationFiles: jest.fn(),
    repairPreparationBatchCustomerOptions: jest.fn(),
    reviewedPreparationBatchPdfUrl: (batchId) => `/api/reviewed-preparation-batches-v1/batches/${encodeURIComponent(batchId)}/pdf`,
}));

const FILE = {
    batch_id: "batch/id",
    file_number: "PF-20260828-0079",
    file_name: "ملف خالد.pdf",
    file_title: "اختبار AMS13067 - 3 قطع - خالد",
    allocated_quantity: 3,
    order_count: 3,
    file_date_display: "2026/8/28",
    responsible_employee_name: "خالد",
};
const CONFIRMED = {
    state: "confirmed", confirmed_count: 1, partial_count: 0, pending_count: 0,
    orders: [{ order_number: "300001", state: "confirmed" }],
};
let container;
let root;
let previousActEnvironment;

beforeEach(() => {
    jest.resetAllMocks();
    listPreparationFiles.mockResolvedValue({ items: [{ ...FILE, order_status_sync: null }] });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    previousActEnvironment = globalThis.IS_REACT_ACT_ENVIRONMENT;
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    globalThis.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
});

const control = () => container.querySelector('[data-testid="reconcile-preparation-file-statuses"]');
const summary = () => container.querySelector('[data-testid="preparation-file-status-sync-summary"]');
const download = () => container.querySelector('[data-testid="download-preparation-file-pdf"]');

test("keeps the authenticated PDF link and only reads on mount or registry refresh", async () => {
    await act(async () => root.render(<PreparationFilesRegistry />));
    expect(listPreparationFiles).toHaveBeenCalledWith({ limit: 100 });
    const link = container.querySelector('[data-testid="download-preparation-file-pdf"]');
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe("/api/reviewed-preparation-batches-v1/batches/batch%2Fid/pdf");
    expect(link.getAttribute("download")).toBe("ملف خالد.pdf");
    expect(link.textContent).toContain("تحميل PDF");
    expect(link.nextElementSibling).toBe(control());
    expect(container.querySelector("h2").textContent).toBe("سجل ملفات التجهيز");
    expect(summary().textContent).toContain("لم تُفحص حالات طلبات هذا الملف بعد.");
    expect(control().textContent).toContain("التحقق ونقل الطلبات إلى قيد التنفيذ");
    expect(control().disabled).toBe(false);

    await act(async () => container.querySelector('[data-testid="refresh-preparation-files-registry"]').click());
    expect(listPreparationFiles).toHaveBeenCalledTimes(2);
    await act(async () => window.dispatchEvent(new CustomEvent("mezan:preparation-file-created")));
    expect(listPreparationFiles).toHaveBeenCalledTimes(3);
    expect(reconcilePreparationBatchStatuses).not.toHaveBeenCalled();
    expect(recoverStalePreparationFiles).not.toHaveBeenCalled();
    expect(repairPreparationBatchCustomerOptions).not.toHaveBeenCalled();
});

test("renders saved reconciliation counts without sending a status update", async () => {
    listPreparationFiles.mockResolvedValue({ items: [{ ...FILE, order_status_sync: CONFIRMED }] });
    await act(async () => root.render(<PreparationFilesRegistry />));
    expect(summary().textContent).toContain("تم تأكيد حالات الطلبات في سلة.");
    expect(summary().textContent).toContain("مؤكد في سلة: 1");
    expect(reconcilePreparationBatchStatuses).not.toHaveBeenCalled();
    expect(control().disabled).toBe(false);
});

test("sends one explicit request per file and displays pending orders and partial coverage", async () => {
    let resolveRequest;
    reconcilePreparationBatchStatuses.mockImplementation(() => new Promise((resolve) => { resolveRequest = resolve; }));
    await act(async () => root.render(<PreparationFilesRegistry />));
    await act(async () => {
        control().click();
        control().click();
    });
    expect(reconcilePreparationBatchStatuses).toHaveBeenCalledTimes(1);
    expect(reconcilePreparationBatchStatuses).toHaveBeenCalledWith(FILE.batch_id);
    expect(control().disabled).toBe(true);
    expect(control().getAttribute("aria-busy")).toBe("true");
    expect(download().tagName).toBe("A");
    expect(download().getAttribute("href")).toBe("/api/reviewed-preparation-batches-v1/batches/batch%2Fid/pdf");
    expect(download().hasAttribute("disabled")).toBe(false);
    expect(download().hasAttribute("aria-disabled")).toBe(false);

    await act(async () => resolveRequest({ ok: true, batch_id: FILE.batch_id, order_status_sync: {
        state: "pending", confirmed_count: 1, partial_count: 1, pending_count: 1,
        orders: [
            { order_number: "300001", state: "confirmed" },
            { order_number: "300002", state: "partial", remaining_quantity: 2 },
            { order_number: "300003", state: "pending", reason: "preparation_assignment_source_not_eligible" },
        ],
    } }));
    expect(summary().textContent).toContain("الإسناد محفوظ؛ تحديث حالة سلة لم يكتمل");
    expect(summary().textContent).toContain("مؤكد في سلة: 1");
    expect(summary().textContent).toContain("إسناد غير مكتمل: 1");
    expect(summary().textContent).toContain("تحديث معلق: 1");
    expect(summary().textContent).toContain("الطلب #300002");
    expect(summary().textContent).toContain("متبقي إسناد 2 قطعة.");
    expect(summary().textContent).toContain("الطلب #300003");
    expect(summary().textContent).toContain("يلزم التحقق من بيانات الطلب والمخزون الحالية.");
    expect(summary().textContent).not.toContain("preparation_assignment_source_not_eligible");
    expect(control().disabled).toBe(false);
    expect(listPreparationFiles).toHaveBeenCalledTimes(1);
    expect(recoverStalePreparationFiles).not.toHaveBeenCalled();
    expect(repairPreparationBatchCustomerOptions).not.toHaveBeenCalled();
});

test.each(["private provider traceback", "constructor"])("never renders unknown pending reason %s", async (reason) => {
    reconcilePreparationBatchStatuses.mockResolvedValue({ ok: true, batch_id: FILE.batch_id, order_status_sync: {
        state: "pending", confirmed_count: 0, partial_count: 0, pending_count: 1,
        orders: [{ order_number: "300004", state: "pending", reason, customer_name: "PRIVATE CUSTOMER" }],
    } });
    await act(async () => root.render(<PreparationFilesRegistry />));
    await act(async () => control().click());
    expect(summary().textContent).toContain("الطلب #300004");
    expect(summary().textContent).toContain("تعذّر تأكيد تحديث الحالة؛ راجع الطلب ثم تحقّق من جديد.");
    expect(summary().textContent).not.toContain(reason);
    expect(summary().textContent).not.toContain("PRIVATE CUSTOMER");
});

test("reports an unconfirmed response honestly and releases the click guard", async () => {
    reconcilePreparationBatchStatuses.mockRejectedValueOnce(new Error("PRIVATE provider exception"));
    reconcilePreparationBatchStatuses.mockResolvedValueOnce({ ok: true, batch_id: FILE.batch_id, order_status_sync: CONFIRMED });
    await act(async () => root.render(<PreparationFilesRegistry />));
    await act(async () => control().click());
    expect(summary().textContent).toContain("الإسناد محفوظ؛ تحديث حالة سلة لم يكتمل");
    expect(summary().textContent).toContain("تعذّر تأكيد نتيجة تحديث حالات سلة.");
    expect(summary().textContent).not.toContain("PRIVATE");
    expect(control().disabled).toBe(false);
    expect(download().tagName).toBe("A");
    expect(download().getAttribute("download")).toBe(FILE.file_name);
    expect(download().getAttribute("href")).toBe("/api/reviewed-preparation-batches-v1/batches/batch%2Fid/pdf");
    expect(download().hasAttribute("disabled")).toBe(false);
    expect(download().hasAttribute("aria-disabled")).toBe(false);
    await act(async () => control().click());
    expect(reconcilePreparationBatchStatuses).toHaveBeenCalledTimes(2);
    expect(summary().textContent).toContain("تم تأكيد حالات الطلبات في سلة.");
    expect(summary().textContent).not.toContain("تعذّر");
});

test("shows a safe permission message and leaves other files unchanged", async () => {
    listPreparationFiles.mockResolvedValue({ items: [FILE, { ...FILE, batch_id: "other", file_number: "PF-OTHER", order_status_sync: CONFIRMED }] });
    reconcilePreparationBatchStatuses.mockRejectedValue({ code: "preparation_status_forbidden", message: "PRIVATE access detail" });
    await act(async () => root.render(<PreparationFilesRegistry />));
    await act(async () => control().click());
    expect(summary().textContent).toContain("لا تملك صلاحية تحديث حالات سلة لهذا الملف.");
    expect(container.textContent).not.toContain("PRIVATE");
    const cards = container.querySelectorAll('[data-testid="preparation-file-record"]');
    expect(cards[1].textContent).toContain("تم تأكيد حالات الطلبات في سلة.");
});

test("a registry refresh shows persisted progress after an interrupted check without resending", async () => {
    reconcilePreparationBatchStatuses.mockRejectedValue(new Error("lost reply"));
    await act(async () => root.render(<PreparationFilesRegistry />));
    await act(async () => control().click());
    expect(summary().textContent).toContain("تعذّر تأكيد نتيجة تحديث حالات سلة.");
    listPreparationFiles.mockResolvedValue({ items: [{ ...FILE, order_status_sync: CONFIRMED }] });
    await act(async () => container.querySelector('[data-testid="refresh-preparation-files-registry"]').click());
    expect(summary().textContent).toContain("تم تأكيد حالات الطلبات في سلة.");
    expect(summary().textContent).not.toContain("تعذّر");
    expect(reconcilePreparationBatchStatuses).toHaveBeenCalledTimes(1);
});
