import { refreshCompletedOrderCarrierLabel } from "../services/fulfillmentV2";
import { printStoreCourierLabel } from "./storeCourierLabelPrint";

// Reserve the print window synchronously on the user's click. Creating it
// after awaiting an API response is rejected by popup blockers on some
// desktop browsers and Android WebViews.
export async function openCurrentCarrierLabel(orderNumber, isCurrent = () => true) {
    if (!isCurrent()) return;
    const printWindow = window.open("about:blank", "_blank");
    if (printWindow) {
        printWindow.opener = null;
        printWindow.document.title = "جاري جلب بوليصة الشحن";
    }
    try {
        const result = await refreshCompletedOrderCarrierLabel(orderNumber);
        if (!isCurrent()) {
            printWindow?.close();
            return;
        }
        if (result?.order_status_completed !== true) {
            throw new Error("لم تؤكد سلة تم التنفيذ؛ الطباعة مجمّدة حتى التحقق من الحالة الحالية.");
        }
        if (!result?.ready) throw new Error(result?.message || "البوليصة الحالية غير جاهزة");
        if (!printWindow) throw new Error("تعذر فتح البوليصة؛ اسمح بالنوافذ المنبثقة ثم أعد المحاولة");
        if (result.label_type === "store_courier" && result.print_data?.qr_code) {
            if (!printStoreCourierLabel(printWindow, result.print_data)) {
                throw new Error("تعذر فتح نافذة الطباعة");
            }
        } else if (result.label_url) {
            printWindow.location.replace(result.label_url);
        } else {
            throw new Error("رابط البوليصة الحالية غير متاح");
        }
        return result;
    } catch (error) {
        printWindow?.close();
        throw error;
    }
}
