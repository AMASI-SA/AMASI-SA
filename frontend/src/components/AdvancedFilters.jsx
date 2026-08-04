import { useEffect, useState } from "react";
import { Funnel, CaretDown, X } from "@phosphor-icons/react";
import api from "../lib/api";
import { todaySA, addDaysISO, monthStartSA, yearStartSA } from "../lib/dates";
import ArabicDateRangePicker from "./ArabicDateRangePicker";

/** Compute date range from preset key (returns {from, to} ISO strings).
 *  All boundaries are computed in Asia/Riyadh — independent of the
 *  user's browser timezone. */
function presetRange(key) {
    const today = todaySA();

    if (key === "today") {
        return { from: today, to: today };
    }
    if (key === "yesterday") {
        const y = addDaysISO(today, -1);
        return { from: y, to: y };
    }
    if (key === "last7") {
        return { from: addDaysISO(today, -6), to: today };
    }
    if (key === "last30") {
        return { from: addDaysISO(today, -29), to: today };
    }
    if (key === "this_month") {
        return { from: monthStartSA(), to: today };
    }
    if (key === "last_month") {
        const [y, m] = today.split("-").map(Number);
        const lastMonth = m === 1 ? 12 : m - 1;
        const yearOfLast = m === 1 ? y - 1 : y;
        const mm = String(lastMonth).padStart(2, "0");
        const lastDay = new Date(Date.UTC(yearOfLast, lastMonth, 0)).getUTCDate();
        return {
            from: `${yearOfLast}-${mm}-01`,
            to: `${yearOfLast}-${mm}-${String(lastDay).padStart(2, "0")}`,
        };
    }
    if (key === "this_year") {
        return { from: yearStartSA(), to: today };
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

function presetForRange(from, to) {
    return PRESETS
        .filter((item) => item.key !== "custom")
        .find((item) => {
            const range = presetRange(item.key);
            return range.from === from && range.to === to;
        })?.key || "custom";
}

/**
 * AdvancedFilters — shared filter bar for Dashboard and Mezan reports.
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
    defaultPreset = "this_month",
}) {
    const [paymentOptions, setPaymentOptions] = useState([]);
    const [shippingOptions, setShippingOptions] = useState([]);
    const [openMenu, setOpenMenu] = useState(null); // "pay" | "ship" | null

    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get("/settings");
                setPaymentOptions((data.payment_methods || []).map((p) => p.name).filter(Boolean));
                setShippingOptions((data.shipping_companies || []).map((s) => s.name).filter(Boolean));
            } catch { /* ignore */ }
        })();
    }, []);

    const toggle = (field, item) => {
        const cur = value[field] || [];
        const next = cur.includes(item) ? cur.filter((x) => x !== item) : [...cur, item];
        onChange({ ...value, [field]: next });
    };

    const applyDateRange = ({ dateFrom, dateTo }) => {
        onChange({
            ...value,
            preset: presetForRange(dateFrom, dateTo),
            from: dateFrom,
            to: dateTo,
        });
    };

    const clearAll = () => onChange({
        preset: defaultPreset,
        ...presetRange(defaultPreset),
        payment_methods: [],
        shipping_companies: [],
    });

    const activeCount = (value.payment_methods?.length || 0) + (value.shipping_companies?.length || 0);

    return (
        <div className="rounded-xl border border-border bg-white p-3 md:p-4 flex flex-wrap items-center gap-2" data-testid="advanced-filters">
            <div className="min-w-[280px] flex-1 md:max-w-xl" data-testid="filter-date-range">
                <ArabicDateRangePicker
                    valueFrom={value.from || todaySA()}
                    valueTo={value.to || value.from || todaySA()}
                    onApply={applyDateRange}
                />
            </div>

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

            {(activeCount > 0 || value.preset !== defaultPreset) && (
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

/** Default initial filter state. Pass a preset key to override (defaults to "this_month"). */
export function defaultFilters(preset = "this_month") {
    const r = presetRange(preset);
    return { preset, from: r.from, to: r.to, payment_methods: [], shipping_companies: [] };
}

export { presetForRange, presetRange };
