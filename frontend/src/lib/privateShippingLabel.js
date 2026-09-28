import api from "./api";

const PRIVATE_LABEL = /^\/api\/special-orders-v1\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/label$/i;

export function requiresPrivateLabel(snapshot = {}) {
    return snapshot.label_requires_authorization === true || PRIVATE_LABEL.test(String(snapshot.label_url || ""));
}

/** Use the authenticated API client only for a fixed, same-backend private route.
 * Never send a token to a carrier URL supplied in a snapshot.
 */
export async function openShippingLabel(snapshot, printWindow) {
    if (!snapshot?.ready || !snapshot?.label_url) throw new Error("البوليصة غير جاهزة.");
    const target = printWindow || window.open("about:blank", "_blank");
    if (!target) throw new Error("اسمح بفتح نافذة الطباعة، ثم أعد المحاولة.");
    target.opener = null;
    let blobUrl;
    try {
        if (requiresPrivateLabel(snapshot)) {
            if (!PRIVATE_LABEL.test(String(snapshot.label_url))) throw new Error("رابط البوليصة الخاصة غير صالح.");
            const response = await api.get(snapshot.label_url.slice(4), { responseType: "blob" });
            const blob = response.data;
            const mime = String(blob?.type || "").split(";")[0].trim().toLowerCase();
            if (!(blob instanceof Blob) || !["application/pdf", "image/png", "image/jpeg", "image/webp"].includes(mime)
                || blob.size <= 0 || blob.size > 8 * 1024 * 1024) {
                throw new Error("تعذّر التحقق من ملف البوليصة الخاصة.");
            }
            blobUrl = URL.createObjectURL(blob);
            target.location.replace(blobUrl);
            // Keep the object URL alive while the actual viewer is open; slow
            // mobile devices must not lose their label to an arbitrary timeout.
            const watch = window.setInterval(() => {
                if (target.closed) {
                    URL.revokeObjectURL(blobUrl);
                    window.clearInterval(watch);
                }
            }, 1000);
        } else {
            const url = new URL(snapshot.label_url, window.location.origin);
            if (!["https:", "http:"].includes(url.protocol)) throw new Error("رابط البوليصة غير صالح.");
            target.location.replace(url.href);
        }
        return true;
    } catch (error) {
        if (blobUrl) URL.revokeObjectURL(blobUrl);
        target.close();
        throw error;
    }
}
