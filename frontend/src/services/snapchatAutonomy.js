import api from "../lib/api";

const BASE = "/ads-manager/ai-monitor/snapchat/autonomy";

export async function listSnapchatAutonomy() {
    return (await api.get(BASE)).data;
}

export async function saveSnapchatAutonomy(accountId, payload) {
    return (await api.put(`${BASE}/${encodeURIComponent(accountId)}`, payload)).data;
}

export async function revokeSnapchatAutonomy(accountId, revision) {
    return (await api.post(`${BASE}/${encodeURIComponent(accountId)}/revoke`, {
        expected_revision: revision,
    })).data;
}

export async function getSnapchatAutonomyExecutions(accountId) {
    return (await api.get(`${BASE}/${encodeURIComponent(accountId)}/executions`)).data;
}

export function nativeAmountToMicro(value, { allowZero = false } = {}) {
    const text = String(value ?? "").trim();
    if (!/^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?$/.test(text)) {
        throw new Error("أدخل مبلغًا صحيحًا بحد أقصى ست خانات عشرية.");
    }
    const [whole, fraction = ""] = text.split(".");
    const micro = BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, "0"));
    if (micro > 50000000000000n || micro < (allowZero ? 0n : 1n)) {
        throw new Error("مبلغ التفويض خارج الحدود المتاحة.");
    }
    return Number(micro);
}

export function microToNativeAmount(value) {
    if (!Number.isSafeInteger(value) || value < 0) return "";
    const micro = BigInt(value);
    const fraction = String(micro % 1000000n).padStart(6, "0").replace(/0+$/, "");
    return `${micro / 1000000n}${fraction ? `.${fraction}` : ""}`;
}

export function autonomyError(error) {
    return error?.response?.data?.detail?.message || error?.message || "تعذر حفظ التفويض؛ حدّث الصفحة وحاول مرة أخرى.";
}
