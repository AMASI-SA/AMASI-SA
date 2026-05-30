import { useEffect, useMemo, useState } from "react";
import { Funnel, CalendarBlank, CaretDown, X } from "@phosphor-icons/react";
import api from "../lib/api";
import { todayISO } from "../lib/format";
import DateInput from "./DateInput";

/** Compute date range from preset key (returns {from, to} ISO strings) */
function presetRange(key) {
    const t = new Date();
    const iso = (d) => d.toISOString().slice(0, 10);
    const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

    if (key === "today") {
        const d = iso(startOfDay(t));
        return { from: d, to: d };
    }
    if (key === "yesterday") {
        const y = new Date(t.getTime() - 86400000);
        const d = iso(startOfDay(y));
        return { from: d, to: d };
    }
    if (key === "last7") {
        const s = new Date(t.getTime() - 6 * 86400000);
        return { from: iso(startOfDay(s)), to: iso(startOfDay(t)) };
    }
    if (key === "last30") {
        const s = new Date(t.getTime() - 29 * 86400000);
        return { from: iso(startOfDay(s)), to: iso(startOfDay(t)) };
    }
    if (key === "this_month") {
        const s = new Date(t.getFullYear(), t.getMonth(), 1);
        return { from: iso(s), to: iso(startOfDay(t)) };
    }
    if (key === "last_month") {
        const s = new Date(t.getFullYear(), t.getMonth() - 1, 1);
        const e = new Date(t.getFullYear(), t.getMonth(), 0);
        return { from: iso(s), to: iso(e) };
    }
    if (key === "this_year") {
        const s = new Date(t.getFullYear(), 0, 1);
        return { from: iso(s), to: iso(startOfDay(t)) };
    }
    return { from: "", to: "" };
}

const PRESETS = [
    { key: "today", label: "اليوم" },
    { key: "yesterday", label: "أمس" },
    { key: "last7", label: "آخر 7 أيام" },
    { key: "last30", label: "آخر 30 يوم" },
    { key: "this_month", label: "هذا الشهر" },
    { key: "last_month", label: "الشهر الماضي" },
    { key: "this_year", label: "هذا العام" },
    { key: "custom", label: "فترة مخصَّصة" },
];

/**
 * AdvancedFilters — shared filter bar for dashboard/reports.
 *
 * Props:
 *   value: { preset, from, to, payment_methods: [], shipping_companies: [] }
 *   onChange: fn(newValue)
 *   showPaymentFilter?: bool (default true)
 *   showShippingFilter?: bool (default true)
 */
export default function AdvancedFilters({
    value,
    onChange,
    showPaymentFilter = true,
    showShippingFilter = true,
}) {
    const [paymentOptions, setPaymentOptions] = useState([]);
    const [shippingOptions, setShippingOptions] = useState([]);
    const [openMenu, setOpenMenu] = useState(null); // "preset" | "pay" | "ship" | null

    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get("/settings");
                setPaymentOptions((data.payment_methods || []).map((p) => p.name).filter(Boolean));
                setShippingOptions((data.shipping_companies || []).map((s) => s.name).filter(Boolean));
            } catch { /* ignore */ }
        })();
    }, []);

    const presetLabel = useMemo(() => {
        if (value.preset === "custom") return `${value.from || "—"} → ${value.to || "—"}`;
        return PRESETS.find((p) => p.key === value.preset)?.label || "اختر فترة";
    }, [value.preset, value.from, value.to]);

    const setPreset = (key) => {
        if (key === "custom") {
            onChange({ ...value, preset: key });
        } else {
            const r = presetRange(key);
            onChange({ ...value, preset: key, from: r.from, to: r.to });
        }
        setOpenMenu(null);
    };

    const toggle = (field, item) => {
        const cur = value[field] || [];
        const next = cur.includes(item) ? cur.filter((x) => x !== item) : [...cur, item];
        onChange({ ...value, [field]: next });
    };

    const clearAll = () => onChange({
        preset: "this_month",
        ...presetRange("this_month"),
        payment_methods: [],
        shipping_companies: [],
    });

    const activeCount = (value.payment_methods?.length || 0) + (value.shipping_companies?.length || 0);

    return (
        <div className="rounded-xl border border-border bg-white p-3 md:p-4 flex flex-wrap items-center gap-2" data-testid="advanced-filters">
            {/* Date preset dropdown */}
            <div className="relative">
                <button
                    type="button"
                    onClick={() => setOpenMenu(openMenu === "preset" ? null : "preset")}
                    className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-border text-sm font-semibold hover:bg-accent transition-colors"
                    data-testid="filter-date-btn"
                >
                    <CalendarBlank size={16} weight="bold" />
                    <span className="max-w-[200px] truncate">{presetLabel}</span>
                    <CaretDown size={14} weight="bold" />
                </button>
                {openMenu === "preset" && (
                    <div className="absolute z-20 mt-1 w-56 rounded-lg border border-border bg-white shadow-lg p-1" data-testid="filter-date-menu">
                        {PRESETS.map((p) => (
                            <button
                                key={p.key}
                                type="button"
                                onClick={() => setPreset(p.key)}
                                className={`w-full text-start px-3 py-2 rounded-md text-sm font-semibold hover:bg-accent transition-colors ${value.preset === p.key ? "bg-brand/10 text-brand" : ""}`}
                                data-testid={`filter-date-${p.key}`}
                            >{p.label}</button>
                        ))}
                    </div>
                )}
            </div>

            {/* Custom date inputs (visible only when preset=custom) */}
            {value.preset === "custom" && (
                <>
                    <DateInput
                        value={value.from || ""}
                        onChange={(e) => onChange({ ...value, from: e.target.value })}
                        className="px-3 py-2 text-sm"
                        data-testid="filter-custom-from"
                    />
                    <span className="text-muted-foreground text-sm">→</span>
                    <DateInput
                        value={value.to || ""}
                        onChange={(e) => onChange({ ...value, to: e.target.value })}
                        className="px-3 py-2 text-sm"
                        data-testid="filter-custom-to"
                    />
                </>
            )}

            {/* Payment methods multi-select */}
            {showPaymentFilter && paymentOptions.length > 0 && (
                <MultiSelect
                    label="طرق الدفع"
                    options={paymentOptions}
                    selected={value.payment_methods || []}
                    onToggle={(item) => toggle("payment_methods", item)}
                    isOpen={openMenu === "pay"}
                    onOpenChange={(b) => setOpenMenu(b ? "pay" : null)}
                    testId="filter-payment"
                />
            )}

            {/* Shipping companies multi-select */}
            {showShippingFilter && shippingOptions.length > 0 && (
                <MultiSelect
                    label="شركات الشحن"
                    options={shippingOptions}
                    selected={value.shipping_companies || []}
                    onToggle={(item) => toggle("shipping_companies", item)}
                    isOpen={openMenu === "ship"}
                    onOpenChange={(b) => setOpenMenu(b ? "ship" : null)}
                    testId="filter-shipping"
                />
            )}

            {/* Reset */}
            {(activeCount > 0 || value.preset !== "this_month") && (
                <button
                    type="button"
                    onClick={clearAll}
                    className="inline-flex items-center gap-1.5 px-2.5 py-2 rounded-lg text-xs font-semibold text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                    data-testid="filter-clear-btn"
                >
                    <X size={14} weight="bold" /> مسح
                </button>
            )}

            {activeCount > 0 && (
                <span className="ms-auto inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Funnel size={12} weight="bold" />
                    {activeCount} فلتر نشط
                </span>
            )}
        </div>
    );
}

function MultiSelect({ label, options, selected, onToggle, isOpen, onOpenChange, testId }) {
    return (
        <div className="relative">
            <button
                type="button"
                onClick={() => onOpenChange(!isOpen)}
                className={`inline-flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-semibold transition-colors ${selected.length > 0 ? "bg-brand/10 text-brand border-brand/30" : "border-border hover:bg-accent"}`}
                data-testid={`${testId}-btn`}
            >
                <span>{label}</span>
                {selected.length > 0 && <span className="text-xs font-bold">({selected.length})</span>}
                <CaretDown size={12} weight="bold" />
            </button>
            {isOpen && (
                <div className="absolute z-20 mt-1 w-64 rounded-lg border border-border bg-white shadow-lg p-1 max-h-72 overflow-y-auto" data-testid={`${testId}-menu`}>
                    {options.map((opt) => (
                        <label
                            key={opt}
                            className="flex items-center gap-2 px-3 py-2 rounded-md text-sm font-semibold hover:bg-accent transition-colors cursor-pointer"
                            data-testid={`${testId}-opt-${opt}`}
                        >
                            <input
                                type="checkbox"
                                checked={selected.includes(opt)}
                                onChange={() => onToggle(opt)}
                                className="w-4 h-4 accent-brand"
                            />
                            <span className="flex-1 truncate">{opt}</span>
                        </label>
                    ))}
                </div>
            )}
        </div>
    );
}

/** Helper hook: build URL params string from filter state */
export function filtersToQueryString(filters) {
    const params = new URLSearchParams();
    if (filters.from) params.set("from_date", filters.from);
    if (filters.to) params.set("to_date", filters.to);
    if (filters.payment_methods?.length) params.set("payment_methods", filters.payment_methods.join(","));
    if (filters.shipping_companies?.length) params.set("shipping_companies", filters.shipping_companies.join(","));
    return params.toString();
}

/** Default initial filter state: this month */
export function defaultFilters() {
    const r = presetRange("this_month");
    return { preset: "this_month", from: r.from, to: r.to, payment_methods: [], shipping_companies: [] };
}
