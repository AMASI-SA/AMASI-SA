import { useEffect, useState } from "react";

export const AD_SQUAD_SORT_STORAGE_KEY = "mezan-snapchat-adsquad-sort-v1";
export const AD_SQUAD_SORT_EVENT = "mezan:snapchat-adsquad-sort-updated";

const OPTIONS = [
    { id: "newest", label: "الأحدث أولًا" },
    { id: "spend", label: "الأكثر صرفًا" },
    { id: "active", label: "النشطة أولًا" },
];
const VALID = new Set(OPTIONS.map((item) => item.id));

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
        // The current component state still updates even if storage is blocked.
    }
    if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(AD_SQUAD_SORT_EVENT, {
            detail: { sort_by: normalized, source: "react_controls" },
        }));
    }
    return normalized;
}

export default function AdSquadSortControls() {
    const [value, setValue] = useState(() => readAdSquadSort());

    useEffect(() => {
        const sync = (event) => {
            const next = String(event?.detail?.sort_by || readAdSquadSort());
            setValue(VALID.has(next) ? next : "newest");
        };
        window.addEventListener(AD_SQUAD_SORT_EVENT, sync);
        return () => window.removeEventListener(AD_SQUAD_SORT_EVENT, sync);
    }, []);

    return (
        <div
            className="flex flex-wrap items-center justify-between gap-3 border-x border-t border-slate-200 bg-slate-50 px-4 py-3"
            data-testid="ad-squad-native-sort-controls"
            dir="rtl"
        >
            <div className="text-sm font-black text-slate-700">ترتيب المجموعات الإعلانية</div>
            <div className="flex flex-wrap gap-2">
                {OPTIONS.map((option) => {
                    const active = value === option.id;
                    return (
                        <button
                            key={option.id}
                            type="button"
                            onClick={() => setValue(writeAdSquadSort(option.id))}
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
