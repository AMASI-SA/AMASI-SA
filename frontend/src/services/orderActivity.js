import api from "../lib/api";

function messageFromError(error, fallback) {
    const detail = error?.response?.data?.detail;

    if (typeof detail === "string" && detail.trim()) {
        return detail;
    }

    if (
        detail &&
        typeof detail === "object" &&
        typeof detail.message === "string"
    ) {
        return detail.message;
    }

    return error?.message || fallback;
}

export async function getOrderActivity(orderNumber) {
    const normalized = String(orderNumber || "").trim();

    if (!normalized) {
        return {
            events: [],
            payments: [],
            events_count: 0,
            payments_count: 0,
        };
    }

    try {
        const { data } = await api.get(
            `/orders-v2/${encodeURIComponent(normalized)}/activity`
        );

        return data || {};
    } catch (error) {
        throw new Error(
            messageFromError(
                error,
                "تعذر تحميل سجل الطلب."
            )
        );
    }
}

export async function refreshOrderActivity(orderNumber) {
    const normalized = String(orderNumber || "").trim();

    if (!normalized) {
        throw new Error("رقم الطلب غير متاح.");
    }

    try {
        const { data } = await api.post(
            `/orders-v2/${encodeURIComponent(normalized)}/activity/refresh`
        );

        return data || {};
    } catch (error) {
        throw new Error(
            messageFromError(
                error,
                "تعذر تحديث سجل الطلب من سلة."
            )
        );
    }
}
