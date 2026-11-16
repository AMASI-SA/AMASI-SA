import api from "../lib/api";
import { issueCompletedOrderCarrierLabel, refreshCompletedOrderCarrierLabel } from "./fulfillmentV2";
import {
    getPreviewOrder,
    getPreviewOrderSummary,
    isPreviewDemoEnvironment,
    listPreviewOrders,
} from "../demo/orderPreviewFixtures";

export const ORDER_PAGE_SIZE = 15;

function errorMessage(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail && typeof detail === "object") {
        if (typeof detail.message === "string" && detail.message.trim()) return detail.message;
        if (detail.code === "order_not_found") return "لم يتم العثور على الطلب.";
        if (detail.code === "owner_only") return "هذه الصفحة متاحة للمالك فقط.";
    }
    return error?.message || fallback;
}

function shippingLabelErrorMessage(error) {
    const fallback = "تعذّر إصدار بوليصة الشحن من سلة.";
    const message = errorMessage(error, fallback);
    const status = Number(error?.response?.status || 0);
    if (
        status >= 500
        || /cloudflare|origin web server|invalid or incomplete response|network error|timeout|\b520\b/i.test(message)
    ) {
        return "تعذّر الاتصال بسلة أثناء تجهيز البوليصة. لم تُطبع الشحنة؛ حاول مرة أخرى.";
    }
    return message;
}

export async function listOrders({
    limit = ORDER_PAGE_SIZE,
    cursor = null,
    statusGroup = null,
    statusExact = null,
    filters = null,
} = {}) {
    if (isPreviewDemoEnvironment()) {
        return listPreviewOrders({ limit, cursor, statusExact: statusExact || statusGroup });
    }
    try {
        const params = { limit };
        if (cursor) params.cursor = cursor;
        if (statusExact) params.status_exact = statusExact;
        else if (statusGroup) params.status_group = statusGroup;
        Object.entries(filters || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && String(value).trim()) params[key] = value;
        });
        const { data } = await api.get("/orders-v2", { params });
        return {
            items: Array.isArray(data?.items) ? data.items : [],
            nextCursor: data?.next_cursor || null,
            skippedInvalid: Number(data?.skipped_invalid || 0),
            resultSummary: data?.result_summary || null,
        };
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحميل الطلبات."));
    }
}

export async function listLatestSoldProductOrders({
    limit = ORDER_PAGE_SIZE,
    cursor = null,
} = {}) {
    if (isPreviewDemoEnvironment()) {
        return listPreviewOrders({ limit, cursor });
    }
    try {
        const params = { limit };
        if (cursor) params.cursor = cursor;
        const { data } = await api.get("/orders-v2/latest-sold-products", { params });
        return {
            items: Array.isArray(data?.items) ? data.items : [],
            nextCursor: data?.next_cursor || null,
            skippedInvalid: Number(data?.skipped_invalid || 0),
        };
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحميل أحدث المنتجات المباعة."));
    }
}

export async function getOrderFilterSummary() {
    if (isPreviewDemoEnvironment()) return getPreviewOrderSummary();
    try {
        const { data } = await api.get("/orders-v2/filters/summary");
        return {
            total: Number(data?.total || 0),
            statusCards: Array.isArray(data?.status_cards) ? data.status_cards : [],
            statusCounts: data?.status_counts || {},
        };
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحميل عدادات الطلبات."));
    }
}

export async function getOrder(orderNumber) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    if (isPreviewDemoEnvironment()) {
        const order = getPreviewOrder(normalized);
        if (!order) throw new Error("لم يتم العثور على الطلب التجريبي.");
        return order;
    }
    try {
        const { data } = await api.get(`/orders-v2/${encodeURIComponent(normalized)}`);
        return data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحميل تفاصيل الطلب."));
    }
}

export async function refreshOrderFromSalla(orderNumber, { force = true } = {}) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    if (isPreviewDemoEnvironment()) {
        return {
            ok: true,
            found: true,
            updated: false,
            skipped: true,
            source: "preview",
        };
    }
    try {
        const { data } = await api.post(
            `/orders-v2/${encodeURIComponent(normalized)}/refresh-from-salla`,
            null,
            { params: { force: Boolean(force) } },
        );
        return data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحديث الطلب من سلة."));
    }
}

const PRODUCTS_REPAIR_TIMEOUT_MS = 120_000;
const PRODUCTS_REPAIR_REVIEW_PATTERN = /^[0-9a-f]{64}$/;

export function productsProjectionRepairReason(code) {
    if (["repair_manifest_changed", "repair_source_changed", "repair_compare_and_set_failed",
        "provider_source_changed_during_read", "provider_source_version_changed"].includes(code)) {
        return "تغيّرت بيانات الطلب منذ الفحص. أعد فحص الأصناف قبل التطبيق.";
    }
    if (["provider_items_differ_from_stored_source", "provider_status_differs_from_stored_source",
        "stored_source_is_stale", "source_requires_authoritative_refresh"].includes(code)) {
        return "بيانات الطلب المحفوظة لا تطابق بيانات سلة الحالية. لا يمكن تطبيق التصحيح بهذه البيانات.";
    }
    if (["products_projection_repair_owner_required", "owner_required", "foreign_order_owner"].includes(code)) {
        return "فحص أصناف التقارير وتصحيحها متاحان لمالك الحساب فقط.";
    }
    if (["order_not_found", "order_not_found_for_owner"].includes(code)) {
        return "لم يتم العثور على هذا الطلب في الحساب الحالي.";
    }
    return "تعذّر تأكيد بيانات الأصناف لهذا الطلب. أعد الفحص وراجع النتيجة قبل التطبيق.";
}

function productsRepairError(error, applying) {
    const code = error?.response?.data?.detail?.code;
    const status = Number(error?.response?.status || 0);
    let message;
    if (status === 401) {
        message = "انتهت جلسة الحساب. أعد تسجيل الدخول ثم افحص الأصناف من جديد.";
    } else if (status === 403 || status === 404 || status === 409) {
        message = productsProjectionRepairReason(code);
    } else if (applying) {
        message = "تعذّر تأكيد نتيجة التصحيح. أعد فحص الأصناف لمعرفة الحالة الحالية قبل أي محاولة جديدة.";
    } else {
        message = "تعذّر إكمال فحص أصناف سلة. أعد الفحص عند استقرار الاتصال.";
    }
    // Only stable codes cross this boundary; provider messages may contain
    // credentials or customer information and must never reach this UI.
    return Object.assign(new Error(message), { code, status });
}

function validProductsRepairLines(rows) {
    if (!Array.isArray(rows)) return false;
    const ids = new Set();
    return rows.every(row => {
        const id = row?.order_item_id;
        const quantity = row?.quantity;
        if (typeof id !== "string" || !id || ids.has(id)
            || !["string", "number"].includes(typeof quantity)
            || !Number.isFinite(Number(quantity)) || Number(quantity) <= 0) return false;
        ids.add(id);
        return true;
    });
}

function validateProductsRepairResponse(data, orderNumber, applying) {
    const row = data?.orders?.[0];
    const validScope = Array.isArray(data?.order_numbers) && data.order_numbers.length === 1
        && data.order_numbers[0] === orderNumber
        && Array.isArray(data?.orders) && data.orders.length === 1
        && row?.order_number === orderNumber;
    const validSummary = row?.action === "refused"
        ? data?.ready === false && data?.pending_orders === 0
        : ["replace_products", "no_op"].includes(row?.action)
            && validProductsRepairLines(row?.before) && validProductsRepairLines(row?.after)
            && data?.pending_orders === (row.action === "replace_products" ? 1 : 0);
    const validMode = applying
        ? data?.ready === true && ((data.mode === "applied" && data.applied_orders === 1 && data.pending_orders === 1)
            || (data.mode === "no_op" && data.applied_orders === 0 && data.pending_orders === 0))
        : data?.mode === "dry_run";
    if (!validScope || !validSummary || !validMode || typeof data?.ready !== "boolean"
        || typeof data?.manifest_sha !== "string" || !PRODUCTS_REPAIR_REVIEW_PATTERN.test(data.manifest_sha)
        || !Array.isArray(row?.reasons) || row.reasons.some(reason => typeof reason !== "string")
        || (data.ready && row.reasons.length > 0)) {
        throw productsRepairError(null, applying);
    }
    return data;
}

export async function previewOrderProductsProjectionRepair(orderNumber) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    if (isPreviewDemoEnvironment()) throw new Error("فحص أصناف سلة غير متاح في المعاينة التجريبية.");
    let data;
    try {
        ({ data } = await api.post(
            `/orders-v2/${encodeURIComponent(normalized)}/products-projection-repair/preview`,
            {}, { timeout: PRODUCTS_REPAIR_TIMEOUT_MS },
        ));
    } catch (error) {
        throw productsRepairError(error, false);
    }
    return validateProductsRepairResponse(data, normalized, false);
}

export async function applyOrderProductsProjectionRepair(orderNumber, manifestSha) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    if (typeof manifestSha !== "string" || !PRODUCTS_REPAIR_REVIEW_PATTERN.test(manifestSha)) {
        throw new Error("أعد فحص أصناف سلة قبل تطبيق التصحيح.");
    }
    if (isPreviewDemoEnvironment()) throw new Error("تصحيح أصناف سلة غير متاح في المعاينة التجريبية.");
    let data;
    try {
        ({ data } = await api.post(
            `/orders-v2/${encodeURIComponent(normalized)}/products-projection-repair/apply`,
            { manifest_sha: manifestSha },
            // api.js otherwise replays a POST after a 401/session refresh.
            // An uncertain write must instead require a new explicit preview.
            { timeout: PRODUCTS_REPAIR_TIMEOUT_MS, _mezanAuthRetried: true },
        ));
    } catch (error) {
        throw productsRepairError(error, true);
    }
    return validateProductsRepairResponse(data, normalized, true);
}

// Compatibility alias for callers that used the earlier name.
export async function openOrderFromSalla(orderNumber) {
    return refreshOrderFromSalla(orderNumber, { force: true });
}


export async function markOrderRead(orderNumber) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized || isPreviewDemoEnvironment()) return { ok: true, read: true };
    try {
        const { data } = await api.post(`/orders-v2/${encodeURIComponent(normalized)}/read`);
        return data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحديث حالة قراءة الطلب."));
    }
}

export async function issueShippingLabel(orderNumber) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    if (isPreviewDemoEnvironment()) {
        throw new Error("إصدار البوليصة غير متاح في المعاينة التجريبية.");
    }
    try {
        return await issueCompletedOrderCarrierLabel(normalized);
    } catch (error) {
        throw new Error(shippingLabelErrorMessage(error));
    }
}


export async function verifyShippingLabel(orderNumber) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    if (isPreviewDemoEnvironment()) {
        return { ok: true, ready: false, source: "preview" };
    }
    try {
        return await refreshCompletedOrderCarrierLabel(normalized);
    } catch (error) {
        throw new Error(shippingLabelErrorMessage(error));
    }
}
