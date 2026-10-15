import { useEffect, useState } from "react";

export const AD_SQUAD_SORT_STORAGE_KEY = "mezan-snapchat-adsquad-sort-v2";
export const AD_SQUAD_SORT_EVENT = "mezan:snapchat-adsquad-sort-updated";
export const AD_SQUAD_SORT_OPTIONS = Object.freeze([
    { id: "orders", label: "الأكثر طلبًا" },
    { id: "spend", label: "الأكثر صرفًا" },
    { id: "newest", label: "الأحدث أولًا" },
    { id: "active", label: "النشطة أولًا" },
]);
const VALID = new Set(AD_SQUAD_SORT_OPTIONS.map((item) => item.id));

export function readAdSquadSort(storage = typeof window !== "undefined" ? window.localStorage : null) {
    try {
        const value = String(storage?.getItem(AD_SQUAD_SORT_STORAGE_KEY) || "newest");
        return VALID.has(value) ? value : "newest";
    } catch {
        return "newest";
    }
}

export function writeAdSquadSort(value, storage = typeof window !== "undefined" ? window.localStorage : null) {
    const normalized = VALID.has(String(value || "")) ? String(value) : "newest";
    try {
        storage?.setItem(AD_SQUAD_SORT_STORAGE_KEY, normalized);
    } catch {
        // Controlled React state remains authoritative when storage is unavailable.
    }
    if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(AD_SQUAD_SORT_EVENT, {
            detail: { sort_by: normalized, source: "react_controls" },
        }));
    }
    return normalized;
}

export default function AdSquadSortControls({ value: controlledValue = null, onChange }) {
    const [localValue, setLocalValue] = useState(() => readAdSquadSort());
    const value = VALID.has(controlledValue) ? controlledValue : localValue;

    useEffect(() => {
        if (VALID.has(controlledValue)) setLocalValue(controlledValue);
    }, [controlledValue]);

    function choose(next) {
        const normalized = writeAdSquadSort(next);
        setLocalValue(normalized);
        onChange?.(normalized);
    }

    return (
        <div
            className="flex flex-wrap items-center justify-between gap-3 border-x border-t border-slate-200 bg-slate-50 px-4 py-3"
            data-testid="ad-squad-native-sort-controls"
            dir="rtl"
        >
            <div>
                <div className="text-sm font-black text-slate-700">ترتيب المجموعات الإعلانية</div>
                <div className="mt-0.5 text-[11px] font-bold text-slate-400">يطبق الترتيب على جميع النتائج قبل تقسيم الصفحات.</div>
            </div>
            <div className="flex flex-wrap gap-2">
                {AD_SQUAD_SORT_OPTIONS.map((option) => {
                    const active = value === option.id;
                    return (
                        <button
                            key={option.id}
                            type="button"
                            onClick={() => choose(option.id)}
                            aria-pressed={active}
                            data-testid={`ad-squad-sort-${option.id}`}
                            className={[
                                "min-h-10 rounded-xl px-4 text-sm font-black transition",
                                active
                                    ? "bg-slate-950 text-white shadow-sm"
                                    : "border border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-100",
                            ].join(" ")}
                        >
                            {option.label}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}
