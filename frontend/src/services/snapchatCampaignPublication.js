import api from "../lib/api";

const BASE = "/integrations-v2/snapchat_ads/management/publications";

export function publicationAmountToMicro(value) {
    const text = String(value || "").trim();
    if (!/^\d{1,9}(?:\.\d{1,6})?$/.test(text)) {
        throw new Error("أدخل الميزانية بعملة الحساب وبحد أقصى ست منازل عشرية.");
    }
    const [whole, fraction = ""] = text.split(".");
    const micro = Number(whole) * 1_000_000 + Number(fraction.padEnd(6, "0"));
    if (!Number.isSafeInteger(micro) || micro <= 0) throw new Error("الميزانية غير صالحة.");
    return micro;
}

export function publicationError(error) {
    return error?.response?.data?.detail?.message
        || error?.response?.data?.detail?.code || error?.message || "تعذّرت متابعة نشر الحملة.";
}

export async function previewSnapchatPublication(request) {
    return (await api.post(BASE + "/preview", request)).data;
}
export async function listSnapchatPublications() {
    return (await api.get(BASE)).data.publications || [];
}
export async function getSnapchatPublication(id) {
    return (await api.get(BASE + "/" + encodeURIComponent(id))).data;
}
export async function getSnapchatPublicationByKey(key) {
    return (await api.get(BASE + "/current", { params: { idempotency_key: key } })).data;
}
export async function approveSnapchatPublication(job) {
    if (!job?.confirm_token) throw new Error("استعد المعاينة نفسها للحصول على رمز الاعتماد.");
    return (await api.post(BASE + "/" + encodeURIComponent(job.publication_id) + "/approve", {
        confirm_token: job.confirm_token, expected_revision: job.revision,
    })).data;
}
export async function executeSnapchatPublication(id) {
    return (await api.post(BASE + "/" + encodeURIComponent(id) + "/execute")).data;
}
export async function reconcileSnapchatPublication(id) {
    return (await api.post(BASE + "/" + encodeURIComponent(id) + "/reconcile")).data;
}
export async function reviewSnapchatPublication(id) {
    return (await api.post(BASE + "/" + encodeURIComponent(id) + "/review")).data;
}

