import { refreshCompletedOrderCarrierLabel } from "../services/fulfillmentV2";
import { printStoreCourierLabel } from "./storeCourierLabelPrint";

// Reserve only a blank window while the print click still has user activation.
export async function openCurrentCarrierLabel(orderNumber, isCurrent = () => true) {
    if (!isCurrent()) return;
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    try {
        const result = await refreshCompletedOrderCarrierLabel(orderNumber);
        if (!isCurrent()) {
            popup?.close();
            return;
        }
        if (result?.order_status_completed !== true) {
            throw new Error("لم تؤكد سلة تم التنفيذ؛ الطباعة مجمّدة حتى التحقق من الحالة الحالية.");
        }
        if (!result?.ready) throw new Error(result?.message || "البوليصة الحالية غير جاهزة");
        if (!popup || popup.closed) throw new Error("تعذر فتح البوليصة؛ اسمح بالنوافذ المنبثقة ثم أعد التحقق");
        if (result.label_type === "store_courier" && result.print_data?.qr_code) {
            if (!printStoreCourierLabel(popup, result.print_data)) throw new Error("تعذر فتح نافذة الطباعة");
        } else if (result.label_url) {
            popup.location.replace(result.label_url);
        } else {
            throw new Error("رابط البوليصة الحالية غير متاح");
        }
        return result;
    } catch (error) {
        popup?.close();
        throw error;
    }
}
