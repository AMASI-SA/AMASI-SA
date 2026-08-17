import api from "../lib/api";


function detailMessage(error, fallback) {
    const detail = error?.response?.data?.detail;
    const code = typeof detail === "string" ? detail : detail?.code;
    const messages = {
        fulfillment_permission_required: "لا تملك صلاحية تنفيذ هذه العملية.",
        employee_store_not_linked: "حساب الموظف غير مرتبط بالمتجر.",
        store_courier_not_found: "الموصل المحدد غير موجود في موظفي المتجر.",
        store_courier_account_inactive: "حساب الموصل المحدد غير نشط.",
        store_courier_not_eligible: "الموظف المحدد لا يملك دور مندوب توصيل المتجر.",
        store_courier_shipment_not_found: "لم نجد شحنة تحمل رقم الطلب الموجود في الباركود.",
        store_courier_label_required: "هذه الشحنة ليست عبر مندوب المتجر ولا يمكن تنفيذها من هنا.",
        store_courier_label_not_ready: "اطبع بوليصة مندوب المتجر أولًا ثم صوّر الباركود.",
        store_courier_order_not_completed: "الطلب لم يكتمل في التجميع والعنونة بعد.",
        store_courier_assignment_conflict: "أُسندت الشحنة في اللحظة نفسها. حدّث القائمة قبل المحاولة مجددًا.",
        store_courier_stage_invalid: "حالة قائمة شحنات الموصل غير صحيحة.",
        store_courier_shipment_not_assigned: "لم تُسند هذه الشحنة إلى موصل بعد.",
        store_courier_shipment_assigned_to_another: "هذه الشحنة مسندة إلى موصل آخر.",
        store_courier_pickup_barcode_mismatch: "الباركود المصوّر لا يخص الطلب المحدد.",
        store_courier_pickup_state_invalid: "هذه الشحنة ليست بانتظار استلام الموصل الآن.",
        store_courier_pickup_conflict: "تغيّرت حالة الشحنة أثناء الاستلام. حدّث القائمة ثم حاول مجددًا.",
        store_courier_pickup_required: "استلم الشحنة وصوّر QR أولًا قبل تسجيل تم التوصيل.",
        store_courier_delivery_state_invalid: "لا يمكن إكمال الشحنة لأنها ليست في جاري التوصيل.",
        store_courier_delivery_conflict: "تغيّرت حالة الشحنة أثناء تأكيد التوصيل. حدّث القائمة ثم حاول مجددًا.",
    };
    if (code === "store_courier_already_assigned") {
        return detail?.courier_name
            ? `الشحنة مسندة مسبقًا إلى ${detail.courier_name}.`
            : "الشحنة مسندة مسبقًا إلى موصل آخر.";
    }
    if (code === "store_courier_shipment_assigned_to_another") {
        return detail?.courier_name
            ? `هذه الشحنة مسندة إلى ${detail.courier_name}.`
            : messages[code];
    }
    return messages[code] || detail?.message || code || error?.message || fallback;
}


const BASE_PATH = "/ai-store-operations/access/store-courier-dispatch";


export async function listStoreCouriers() {
    try {
        return (await api.get(`${BASE_PATH}/couriers`)).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تحميل قائمة الموصلين."));
    }
}


export async function listStoreCourierAssignments({ courierUserId = "", limit = 100 } = {}) {
    try {
        return (await api.get(`${BASE_PATH}/assignments`, {
            params: {
                courier_user_id: courierUserId || undefined,
                limit,
            },
        })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تحميل الشحنات المسندة."));
    }
}


export async function assignStoreCourierShipment(courierUserId, barcode) {
    const courierId = String(courierUserId || "").trim();
    const scannedBarcode = String(barcode || "").trim();
    if (!courierId) throw new Error("اختر الموصل أولًا.");
    if (!scannedBarcode) throw new Error("صوّر باركود الشحنة أولًا.");
    try {
        return (await api.post(`${BASE_PATH}/assign`, {
            courier_user_id: courierId,
            barcode: scannedBarcode,
        })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر إسناد الشحنة إلى الموصل."));
    }
}


export async function listMyStoreCourierShipments({ stage = "waiting", limit = 100 } = {}) {
    const normalizedStage = ["waiting", "delivering", "delivered", "all"].includes(stage)
        ? stage
        : "waiting";
    try {
        return (await api.get(`${BASE_PATH}/my-shipments`, {
            params: { stage: normalizedStage, limit },
        })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تحميل شحنات الموصل."));
    }
}


export async function pickupStoreCourierShipment(orderNumber, barcode) {
    const normalizedOrder = String(orderNumber || "").trim();
    const scannedBarcode = String(barcode || "").trim();
    if (!normalizedOrder) throw new Error("رقم الطلب مطلوب.");
    if (!scannedBarcode) throw new Error("صوّر QR الموجود على البوليصة.");
    try {
        return (await api.post(
            `${BASE_PATH}/my-shipments/${encodeURIComponent(normalizedOrder)}/pickup`,
            { barcode: scannedBarcode },
        )).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر استلام الشحنة وبدء التوصيل."));
    }
}


export async function completeStoreCourierShipment(orderNumber, note = "") {
    const normalizedOrder = String(orderNumber || "").trim();
    if (!normalizedOrder) throw new Error("رقم الطلب مطلوب.");
    try {
        return (await api.post(
            `${BASE_PATH}/my-shipments/${encodeURIComponent(normalizedOrder)}/delivered`,
            { note: String(note || "").trim() || null },
        )).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تسجيل تم التوصيل."));
    }
}
