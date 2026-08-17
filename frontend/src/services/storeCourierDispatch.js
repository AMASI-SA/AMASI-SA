import api from "../lib/api";


function detailMessage(error, fallback) {
    const detail = error?.response?.data?.detail;
    const code = typeof detail === "string" ? detail : detail?.code;
    const messages = {
        fulfillment_permission_required: "لا تملك صلاحية إدارة الموصلين.",
        store_courier_not_found: "الموصل المحدد غير موجود في موظفي المتجر.",
        store_courier_account_inactive: "حساب الموصل المحدد غير نشط.",
        store_courier_not_eligible: "الموظف المحدد لا يملك دور مندوب توصيل المتجر.",
        store_courier_shipment_not_found: "لم نجد شحنة تحمل رقم الطلب الموجود في الباركود.",
        store_courier_label_required: "هذه الشحنة ليست عبر مندوب المتجر ولا يمكن إسنادها من هنا.",
        store_courier_label_not_ready: "اطبع بوليصة مندوب المتجر أولًا ثم صوّر الباركود.",
        store_courier_order_not_completed: "الطلب لم يكتمل في التجميع والعنونة بعد.",
        store_courier_assignment_conflict: "أُسندت الشحنة في اللحظة نفسها. حدّث القائمة قبل المحاولة مجددًا.",
    };
    if (code === "store_courier_already_assigned") {
        return detail?.courier_name
            ? `الشحنة مسندة مسبقًا إلى ${detail.courier_name}.`
            : "الشحنة مسندة مسبقًا إلى موصل آخر.";
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


export async function listMyStoreCourierShipments({ limit = 100 } = {}) {
    try {
        return (await api.get(`${BASE_PATH}/my-shipments`, {
            params: { limit },
        })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تحميل شحنات الموصل."));
    }
}
