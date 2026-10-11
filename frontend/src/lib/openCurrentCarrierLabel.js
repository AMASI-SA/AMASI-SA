import { refreshCompletedOrderCarrierLabel } from "../services/fulfillmentV2";
import { printStoreCourierLabel } from "./storeCourierLabelPrint";

// A saved URL or print payload is never authority to open a shipment artifact.
export async function openCurrentCarrierLabel(orderNumber, isCurrent = () => true) {
    const result = await refreshCompletedOrderCarrierLabel(orderNumber);
    if (!isCurrent()) return;
    if (result?.order_status_completed !== true) {
        throw new Error("لم تؤكد سلة تم التنفيذ؛ الطباعة مجمّدة حتى التحقق من الحالة الحالية.");
    }
    if (!result?.ready) throw new Error(result?.message || "البوليصة الحالية غير جاهزة");
    if (result.label_type === "store_courier" && result.print_data?.qr_code) {
        const printWindow = window.open("about:blank", "_blank");
        if (printWindow) printWindow.opener = null;
        if (!printStoreCourierLabel(printWindow, result.print_data)) {
            printWindow?.close();
            throw new Error("تعذر فتح نافذة الطباعة");
        }
    } else if (result.label_url) {
        const labelWindow = window.open("about:blank", "_blank");
        if (!labelWindow) throw new Error("تعذر فتح البوليصة؛ اسمح بالنوافذ المنبثقة ثم أعد التحقق");
        labelWindow.opener = null;
        labelWindow.location.replace(result.label_url);
    } else {
        throw new Error("رابط البوليصة الحالية غير متاح");
    }
    return result;
}

