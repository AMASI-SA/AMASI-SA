import api, { API_BASE } from "../lib/api";
import { confirmReviewUnitSplit } from "../reviewUnitSplitGuard";

function message(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail?.message) return detail.message;
    if (detail?.code === "review_revision_conflict") return "تم تعديل الطلب من موظف آخر. حدّث البيانات ثم أعد المحاولة.";
    if (detail?.code === "review_already_completed") return "تم اعتماد مراجعة هذا الطلب سابقًا.";
    if (detail?.code === "preparation_quantity_exceeds_remaining") return "الكمية المختارة أكبر من الكمية المتبقية.";
    if (detail?.code === "preparation_units_already_allocated") return "حجز موظف آخر بعض القطع. حدّث الصفحة وأعد الاختيار.";
    if (detail?.code === "reviewed_product_not_available") return "المنتج لم يعد متاحًا في هذه المرحلة. حدّث الصفحة.";
    if (detail?.code === "reviewed_line_changed_reload_required") return "تغيّرت بيانات المنتج بعد فتح الملف. حدّث منتجات تمت المراجعة ثم أعد تحديد الكمية. لم يتم إنشاء ملف.";
    if (detail?.code === "reviewed_line_no_longer_exportable") return "هذا المنتج لم يعد متاحًا لملف التجهيز. حدّث الصفحة ثم أعد الاختيار. لم يتم إنشاء ملف.";
    if (detail?.code === "reviewed_product_allocation_incomplete") return "تعذّر توزيع الكمية على الطلبات الحالية. حدّث الصفحة ثم أعد تحديد الكمية. لم يتم إنشاء ملف.";
    if (detail?.code === "preparation_batch_build_in_progress") return "يجري إنشاء هذا الملف بالفعل؛ انتظر لحظات ولا تكرر الحفظ.";
    if (detail?.code === "reviewed_catalog_truncated") return "لا يمكن إنشاء ملف من قائمة ناقصة. راجع تنبيه الحد التشغيلي.";
    if (detail?.code === "preparation_batch_generation_failed") return "تعذّر إنشاء ملف التجهيز ولم تُخصم أي قطعة.";
    if (detail?.code === "responsible_employee_unavailable") return "اختر موظفًا مسؤولًا نشطًا يملك صلاحية إدارة التجهيز.";
    if (detail?.code === "preparation_file_draft_conflict") return "تغيرت بيانات الملف بعد بدء الحفظ. أغلق النافذة وأعد المحاولة.";
    if (detail?.code === "preparation_file_quantity_mismatch") return "عدد القطع الفعلي لا يطابق بيانات الملف. حدّث الصفحة وأعد المحاولة.";
    return error?.message || fallback;
}

export async function listPendingOrderReviews({ limit = 15, cursor = null, search = "" } = {}) {
    try {
        const params = { limit };
        if (cursor) params.cursor = cursor;
        if (String(search || "").trim()) params.search = String(search).trim();
        const { data } = await api.get("/order-reviews-v1", { params });
        return { items: Array.isArray(data?.items) ? data.items : [], nextCursor: data?.next_cursor || null };
    } catch (error) { throw new Error(message(error, "تعذّر تحميل الطلبات بانتظار المراجعة.")); }
}

export async function listReviewedOrderReviews({ limit = 50 } = {}) {
    try {
        const { data } = await api.get("/order-reviews-v1/reviewed", { params: { limit } });
        return { items: Array.isArray(data?.items) ? data.items : [] };
    } catch (error) { throw new Error(message(error, "تعذّر تحميل الطلبات التي تمت مراجعتها.")); }
}

export async function previewAms11353IncidentRecovery() {
    const { data } = await api.get("/preparation-recovery-v1/incidents/ams11353-lost-11-20260825");
    return data;
}

export async function applyAms11353IncidentRecovery() {
    const { data } = await api.post("/preparation-recovery-v1/incidents/ams11353-lost-11-20260825/apply");
    return data;
}

export async function listReviewedProductCatalog({ limit = 500, reviewedDate = "" } = {}) {
    try {
        const params = { limit };
        if (String(reviewedDate || "").trim()) params.reviewed_date = String(reviewedDate).trim();
        const { data } = await api.get("/reviewed-products-v1/catalog", { params });
        return {
            products: Array.isArray(data?.products) ? data.products : [],
            categories: Array.isArray(data?.categories) ? data.categories : [],
            summary: data?.summary || {},
            truncated: Boolean(data?.truncated),
            historical: Boolean(data?.historical),
            reviewedDate: data?.reviewed_date || "",
        };
    } catch (error) { throw new Error(message(error, "تعذّر تحميل منتجات مرحلة تمت المراجعة.")); }
}

export async function listPreparationFileEmployees() {
    try {
        const { data } = await api.get("/preparation-file-registry-v1/employees");
        return { items: Array.isArray(data?.items) ? data.items : [] };
    } catch (error) {
        throw new Error(message(error, "تعذّر تحميل الموظفين المسؤولين."));
    }
}

export async function createPreparationFileDraft(payload) {
    try {
        return (await api.post("/preparation-file-registry-v1/drafts", payload)).data;
    } catch (error) {
        throw new Error(message(error, "تعذّر حفظ بيانات ملف التجهيز."));
    }
}

export async function finalizePreparationFile(clientRequestId) {
    try {
        return (await api.post(`/preparation-file-registry-v1/finalize/${encodeURIComponent(clientRequestId)}`)).data;
    } catch (error) {
        throw new Error(message(error, "تم إنشاء PDF، لكن تعذّر تسجيل الملف. أعد المحاولة بنفس الاختيار."));
    }
}

export async function listPreparationFiles({ limit = 30 } = {}) {
    try {
        const { data } = await api.get("/preparation-file-registry-v1/files", { params: { limit } });
        return { items: Array.isArray(data?.items) ? data.items : [] };
    } catch (error) {
        throw new Error(message(error, "تعذّر تحميل سجل ملفات التجهيز."));
    }
}

export async function recoverStalePreparationFiles() {
    try {
        return (await api.post("/preparation-file-safety-v1/recover-stale")).data;
    } catch (error) {
        throw new Error(message(error, "تعذّرت استعادة ملفات التجهيز المتعثرة."));
    }
}

export async function repairPreparationBatchCustomerOptions(batchId) {
    try {
        return (await api.post(
            `/reviewed-preparation-batches-v1/batches/${encodeURIComponent(batchId)}/repair-customer-options`,
        )).data;
    } catch (error) {
        throw new Error(message(error, "تعذّر إصلاح خيارات العميل في ملف التجهيز."));
    }
}

export async function createReviewedPreparationBatch({ clientRequestId, selections }) {
    try {
        const metadata = typeof window !== "undefined"
            ? window.__mezanPreparationFileMetadata
            : null;
        if (!metadata?.fileTitle || !metadata?.responsibleEmployeeId) {
            throw new Error("أكمل اسم الملف والموظف المسؤول قبل إنشاء PDF.");
        }
        await createPreparationFileDraft({
            client_request_id: clientRequestId,
            file_title: metadata.fileTitle,
            responsible_employee_id: metadata.responsibleEmployeeId,
            expected_quantity: Number(metadata.expectedQuantity || 0),
            selected_product_count: Number(metadata.selectedProductCount || 0),
        });
        const { data } = await api.post("/reviewed-preparation-batches-v1/batches", {
            client_request_id: clientRequestId,
            selections,
        });
        const registered = data?.file_registered === true
            && data?.registry_status === "ready"
            && data?.piece_registry_status === "ready"
            ? data
            : await finalizePreparationFile(clientRequestId);
        if (typeof window !== "undefined") {
            delete window.__mezanPreparationFileMetadata;
            window.dispatchEvent(new CustomEvent("mezan:preparation-file-created", {
                detail: registered,
            }));
        }
        return { ...data, ...registered, batch_id: registered.batch_id || data.batch_id };
    } catch (error) {
        if (error instanceof Error && !error?.response) throw error;
        const wrapped = new Error(message(error, "تعذّر إنشاء ملف التجهيز."));
        wrapped.code = error?.response?.data?.detail?.code;
        wrapped.detail = error?.response?.data?.detail;
        throw wrapped;
    }
}

export async function listReviewedPreparationBatches({ limit = 20 } = {}) {
    try {
        const { data } = await api.get("/reviewed-preparation-batches-v1/batches", { params: { limit } });
        return { items: Array.isArray(data?.items) ? data.items : [] };
    } catch (error) {
        throw new Error(message(error, "تعذّر تحميل ملفات التجهيز السابقة."));
    }
}

export function reviewedPreparationBatchPdfUrl(batchId) {
    return `${API_BASE}/reviewed-preparation-batches-v1/batches/${encodeURIComponent(batchId)}/pdf`;
}

export async function downloadReviewedPreparationBatchPdf(batchId, fileName = "") {
    try {
        const anchor = document.createElement("a");
        anchor.href = reviewedPreparationBatchPdfUrl(batchId);
        anchor.download = fileName || `preparation-${batchId}.pdf`;
        anchor.style.display = "none";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        return {
            ok: true,
            batchId,
            fileName: anchor.download,
            contentType: "application/pdf",
        };
    } catch (error) {
        throw new Error(message(error, "تم إنشاء الدفعة، لكن تعذّر تحميل ملف PDF."));
    }
}

export async function getOrderReview(orderNumber) {
    try { return (await api.get(`/order-reviews-v1/${encodeURIComponent(orderNumber)}`)).data; }
    catch (error) { throw new Error(message(error, "تعذّر تحميل بيانات المراجعة.")); }
}

export async function updateOrderReviewItem(orderNumber, orderItemId, payload) {
    try { return (await api.patch(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}`, payload)).data; }
    catch (error) { throw new Error(message(error, "تعذّر حفظ إعدادات المنتج.")); }
}

export async function completeOrderReview(orderNumber, expectedRevision) {
    try {
        // Re-read immediately before completion so the warning is based on the
        // same durable review snapshot that is about to transition to reviewed.
        const detail = (await api.get(`/order-reviews-v1/${encodeURIComponent(orderNumber)}`)).data;
        if (!confirmReviewUnitSplit(detail)) {
            throw new Error("تم إلغاء اعتماد المراجعة. افصل المنتج يدويًا أو راجع الكمية ثم اضغط «تمت المراجعة» مرة أخرى.");
        }
        return (await api.post(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/complete`, {
            expected_revision: expectedRevision,
        })).data;
    } catch (error) {
        if (error instanceof Error && !error?.response) throw error;
        throw new Error(message(error, "تعذّر اعتماد مراجعة الطلب."));
    }
}

export async function createOrderReviewOperationalItem(orderNumber, payload) {
    try { return (await api.post(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/operational-items`, payload)).data; }
    catch (error) { throw new Error(message(error, "تعذّر إضافة المنتج التشغيلي.")); }
}

export async function updateOrderReviewOperationalItemStatus(orderNumber, operationalItemId, payload) {
    try { return (await api.patch(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/operational-items/${encodeURIComponent(operationalItemId)}`, payload)).data; }
    catch (error) { throw new Error(message(error, "تعذّر تحديث حالة المنتج التشغيلي.")); }
}

export async function unlinkOrderReviewOperationalItem(orderNumber, operationalItemId, expectedRevision) {
    try { return (await api.delete(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/operational-items/${encodeURIComponent(operationalItemId)}`, { params: { expected_revision: expectedRevision } })).data; }
    catch (error) { throw new Error(message(error, "تعذّر إلغاء ربط المنتج التشغيلي.")); }
}

export async function saveOrderReviewImageChoice(orderNumber, orderItemId, payload) {
    try { return (await api.post(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}/image-choice`, payload)).data; }
    catch (error) { throw new Error(message(error, "تعذّر حفظ اختيار صورة التجهيز.")); }
}

export async function uploadOrderReviewMezanImage(orderNumber, orderItemId, payload) {
    try { return (await api.post(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}/mezan-images`, payload)).data; }
    catch (error) { throw new Error(message(error, "تعذّر رفع صورة ميزان.")); }
}

export async function deleteOrderReviewMezanImage(orderNumber, orderItemId, imageId) {
    try { return (await api.delete(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}/mezan-images/${encodeURIComponent(imageId)}`)).data; }
    catch (error) { throw new Error(message(error, "تعذّر حذف صورة ميزان.")); }
}
