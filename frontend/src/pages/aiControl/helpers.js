export const OK = "ok";
export const WARN = "warn";
export const BLOCK = "block";

export const todayIso = () => new Date().toISOString().slice(0, 10);

export function addDaysIso(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
}

export const fmtInt = (n) => (Number(n) || 0).toLocaleString("en-US");
export const fmtMoney = (n) => `${(Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`;
export const fmtPct = (n) => `${(Number(n) || 0).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;

export function buildQuery(params = {}) {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && String(value).trim() !== "") q.set(key, value);
    });
    const s = q.toString();
    return s ? `?${s}` : "";
}

export function hasValue(value) {
    if (value === null || value === undefined) return false;
    if (typeof value === "string") return value.trim() !== "" && value.trim() !== "\\N";
    if (typeof value === "number") return Number.isFinite(value);
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === "object") return Object.keys(value).length > 0;
    return Boolean(value);
}

export function getPath(obj, path) {
    const parts = String(path || "").split(".").filter(Boolean);
    let current = obj;
    for (const part of parts) {
        if (current === null || current === undefined) return undefined;
        current = current[part];
    }
    return current;
}

export function getAny(obj, paths = []) {
    for (const path of paths) {
        const value = getPath(obj, path);
        if (hasValue(value)) return value;
    }
    return undefined;
}

export function deepHasKeyFragment(obj, fragments = [], depth = 0) {
    if (!obj || typeof obj !== "object" || depth > 4) return false;
    for (const [key, value] of Object.entries(obj)) {
        const lowered = String(key).toLowerCase();
        if (fragments.some((fragment) => lowered.includes(fragment)) && hasValue(value)) return true;
        if (value && typeof value === "object" && deepHasKeyFragment(value, fragments, depth + 1)) return true;
    }
    return false;
}

export function toneClass(status) {
    if (status === BLOCK) return "bg-red-50 border-red-300 text-red-800";
    if (status === WARN) return "bg-amber-50 border-amber-300 text-amber-800";
    return "bg-emerald-50 border-emerald-300 text-emerald-800";
}

export function statusLabel(status) {
    if (status === BLOCK) return "⛔ مانع";
    if (status === WARN) return "⚠️ ناقص";
    return "✅ جاهز";
}
