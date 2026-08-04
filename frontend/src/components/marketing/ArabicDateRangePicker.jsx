import { useEffect, useMemo, useRef, useState } from "react";
import {
    CalendarBlank,
    CaretDoubleLeft,
    CaretDoubleRight,
    CaretLeft,
    CaretRight,
} from "@phosphor-icons/react";

const MONTHS_AR = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
];
const WEEKDAYS_AR = ["ح", "ن", "ث", "ر", "خ", "ج", "س"];

function validISO(value) {
    return /^\d{4}-\d{2}-\d{2}$/.test(String(value || ""));
}

function parseISODate(value) {
    if (!validISO(value)) return new Date();
    const [year, month, day] = value.split("-").map(Number);
    return new Date(Date.UTC(year, month - 1, day));
}

function toISO(date) {
    return [
        date.getUTCFullYear(),
        String(date.getUTCMonth() + 1).padStart(2, "0"),
        String(date.getUTCDate()).padStart(2, "0"),
    ].join("-");
}

function addDays(date, amount) {
    const next = new Date(date.getTime());
    next.setUTCDate(next.getUTCDate() + amount);
    return next;
}

function addMonths(date, amount) {
    return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + amount, 1));
}

function startOfMonth(date) {
    return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1));
}

function endOfMonth(date) {
    return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0));
}

function todayUTC() {
    const now = new Date();
    return new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
}

function monthGrid(monthDate) {
    const start = startOfMonth(monthDate);
    const gridStart = addDays(start, -start.getUTCDay());
    return Array.from({ length: 42 }, (_, index) => addDays(gridStart, index));
}

function compareISO(left, right) {
    return String(left || "").localeCompare(String(right || ""));
}

function formatRangeLabel(from, to) {
    if (!validISO(from) || !validISO(to)) return "اختيار الفترة";
    const start = parseISODate(from);
    const end = parseISODate(to);
    const startLabel = `${start.getUTCDate()} ${MONTHS_AR[start.getUTCMonth()]}`;
    const endLabel = `${end.getUTCDate()} ${MONTHS_AR[end.getUTCMonth()]} ${end.getUTCFullYear()}`;
    return from === to ? endLabel : `${startLabel} — ${endLabel}`;
}

export function adsDatePreset(id, anchorDate = todayUTC()) {
    const today = new Date(anchorDate.getTime());
    const todayIso = toISO(today);
    if (id === "today") return { dateFrom: todayIso, dateTo: todayIso };
    if (id === "yesterday") {
        const yesterday = toISO(addDays(today, -1));
        return { dateFrom: yesterday, dateTo: yesterday };
    }
    if (id === "last7") return { dateFrom: toISO(addDays(today, -6)), dateTo: todayIso };
    if (id === "last30") return { dateFrom: toISO(addDays(today, -29)), dateTo: todayIso };
    if (id === "monthToDate") return { dateFrom: toISO(startOfMonth(today)), dateTo: todayIso };
    if (id === "previousMonth") {
        const previous = addMonths(today, -1);
        return { dateFrom: toISO(startOfMonth(previous)), dateTo: toISO(endOfMonth(previous)) };
    }
    if (id === "last3Months") return { dateFrom: toISO(addMonths(startOfMonth(today), -2)), dateTo: todayIso };
    return { dateFrom: todayIso, dateTo: todayIso };
}

const PRESETS = [
    ["today", "اليوم"],
    ["yesterday", "أمس"],
    ["last7", "آخر 7 أيام"],
    ["last30", "آخر 30 يومًا"],
    ["previousMonth", "الشهر السابق"],
    ["monthToDate", "من بداية الشهر"],
    ["last3Months", "آخر 3 أشهر"],
];

function CalendarMonth({ monthDate, from, to, onSelect }) {
    const days = useMemo(() => monthGrid(monthDate), [monthDate]);
    const month = monthDate.getUTCMonth();
    const today = toISO(todayUTC());

    return (
        <section className="min-w-0">
            <h3 className="mb-3 text-center text-lg font-black text-slate-950">
                {MONTHS_AR[month]} {monthDate.getUTCFullYear()}
            </h3>
            <div className="grid grid-cols-7 gap-1 text-center text-xs font-black text-slate-500">
                {WEEKDAYS_AR.map((day, index) => <div key={`${day}-${index}`} className="py-1">{day}</div>)}
            </div>
            <div className="mt-1 grid grid-cols-7 gap-1">
                {days.map((day) => {
                    const value = toISO(day);
                    const currentMonth = day.getUTCMonth() === month;
                    const selectedStart = value === from;
                    const selectedEnd = value === to;
                    const inRange = validISO(from) && validISO(to)
                        && compareISO(value, from) >= 0
                        && compareISO(value, to) <= 0;
                    return (
                        <button
                            key={value}
                            type="button"
                            onClick={() => onSelect(value)}
                            className={[
                                "relative flex h-9 items-center justify-center rounded-full text-sm font-bold transition",
                                currentMonth ? "text-slate-800" : "text-slate-300",
                                inRange ? "bg-amber-100" : "hover:bg-slate-100",
                                selectedStart || selectedEnd ? "bg-amber-400 text-slate-950 ring-2 ring-amber-200" : "",
                                value === today ? "after:absolute after:bottom-0.5 after:h-1 after:w-1 after:rounded-full after:bg-blue-600" : "",
                            ].join(" ")}
                            aria-label={value}
                            aria-pressed={selectedStart || selectedEnd}
                        >
                            {day.getUTCDate()}
                        </button>
                    );
                })}
            </div>
        </section>
    );
}

export default function ArabicDateRangePicker({ valueFrom, valueTo, onApply }) {
    const rootRef = useRef(null);
    const nativeFromRef = useRef(null);
    const nativeToRef = useRef(null);
    const nativeSyncQueued = useRef(false);
    const [open, setOpen] = useState(false);
    const [draftFrom, setDraftFrom] = useState(valueFrom);
    const [draftTo, setDraftTo] = useState(valueTo);
    const [awaitingEnd, setAwaitingEnd] = useState(false);
    const [monthCursor, setMonthCursor] = useState(
        () => startOfMonth(parseISODate(valueFrom)),
    );

    useEffect(() => {
        if (open) return;
        setDraftFrom(valueFrom);
        setDraftTo(valueTo);
        setMonthCursor(startOfMonth(parseISODate(valueFrom)));
        setAwaitingEnd(false);
    }, [open, valueFrom, valueTo]);

    useEffect(() => {
        if (nativeFromRef.current && nativeFromRef.current.value !== valueFrom) {
            nativeFromRef.current.value = valueFrom;
        }
        if (nativeToRef.current && nativeToRef.current.value !== valueTo) {
            nativeToRef.current.value = valueTo;
        }
    }, [valueFrom, valueTo]);

    useEffect(() => {
        const close = (event) => {
            if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
        };
        document.addEventListener("mousedown", close);
        return () => document.removeEventListener("mousedown", close);
    }, []);

    function scheduleNativeCompatibilityApply() {
        if (nativeSyncQueued.current) return;
        nativeSyncQueued.current = true;
        queueMicrotask(() => {
            nativeSyncQueued.current = false;
            const dateFrom = nativeFromRef.current?.value;
            const dateTo = nativeToRef.current?.value;
            if (!validISO(dateFrom) || !validISO(dateTo)) return;
            onApply?.(
                compareISO(dateFrom, dateTo) <= 0
                    ? { dateFrom, dateTo }
                    : { dateFrom: dateTo, dateTo: dateFrom },
            );
        });
    }

    function selectDate(value) {
        if (!awaitingEnd) {
            setDraftFrom(value);
            setDraftTo(value);
            setAwaitingEnd(true);
            return;
        }
        if (compareISO(value, draftFrom) < 0) {
            setDraftFrom(value);
            setDraftTo(draftFrom);
        } else {
            setDraftTo(value);
        }
        setAwaitingEnd(false);
    }

    function choosePreset(id) {
        const range = adsDatePreset(id);
        setDraftFrom(range.dateFrom);
        setDraftTo(range.dateTo);
        setMonthCursor(startOfMonth(parseISODate(range.dateFrom)));
        setAwaitingEnd(false);
    }

    function apply() {
        if (!validISO(draftFrom) || !validISO(draftTo)) return;
        const normalized = compareISO(draftFrom, draftTo) <= 0
            ? { dateFrom: draftFrom, dateTo: draftTo }
            : { dateFrom: draftTo, dateTo: draftFrom };
        onApply?.(normalized);
        setOpen(false);
    }

    return (
        <div ref={rootRef} className="relative" data-testid="ads-arabic-date-range-picker">
            <div className="sr-only" aria-hidden="true" data-testid="ads-native-date-compatibility">
                <input
                    ref={nativeFromRef}
                    type="date"
                    defaultValue={valueFrom}
                    onChange={scheduleNativeCompatibilityApply}
                    tabIndex={-1}
                    data-mezan-native-date="from"
                />
                <input
                    ref={nativeToRef}
                    type="date"
                    defaultValue={valueTo}
                    onChange={scheduleNativeCompatibilityApply}
                    tabIndex={-1}
                    data-mezan-native-date="to"
                />
            </div>

            <button
                type="button"
                onClick={() => setOpen((value) => !value)}
                className="flex h-11 w-full min-w-64 items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 text-right text-sm font-black text-slate-800 transition hover:border-amber-300 hover:bg-white"
                aria-expanded={open}
            >
                <span className="flex items-center gap-2">
                    <CalendarBlank size={20} weight="duotone" />
                    {formatRangeLabel(valueFrom, valueTo)}
                </span>
                <span className="text-xs text-slate-400">تغيير</span>
            </button>

            {open && (
                <div className="absolute right-0 top-[calc(100%+0.65rem)] z-[90] w-[min(94vw,900px)] overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-2xl" dir="rtl">
                    <div className="grid lg:grid-cols-[180px_minmax(0,1fr)]">
                        <aside className="border-b border-slate-200 bg-slate-50 p-3 lg:border-b-0 lg:border-l">
                            <div className="mb-2 px-2 text-xs font-black text-slate-500">فترات جاهزة</div>
                            <div className="grid grid-cols-2 gap-1 lg:grid-cols-1">
                                {PRESETS.map(([id, label]) => (
                                    <button
                                        key={id}
                                        type="button"
                                        onClick={() => choosePreset(id)}
                                        className="rounded-xl px-3 py-2 text-right text-sm font-bold text-slate-700 hover:bg-white hover:text-amber-700"
                                    >
                                        {label}
                                    </button>
                                ))}
                            </div>
                        </aside>

                        <div className="p-4 sm:p-5">
                            <div className="mb-4 flex items-center justify-between gap-2">
                                <div className="flex items-center gap-1">
                                    <button type="button" onClick={() => setMonthCursor((date) => addMonths(date, -12))} className="rounded-lg p-2 hover:bg-slate-100" aria-label="السنة السابقة"><CaretDoubleRight size={19} /></button>
                                    <button type="button" onClick={() => setMonthCursor((date) => addMonths(date, -1))} className="rounded-lg p-2 hover:bg-slate-100" aria-label="الشهر السابق"><CaretRight size={19} /></button>
                                </div>
                                <div className="text-center text-xs font-black text-slate-500">
                                    {awaitingEnd ? "اختر تاريخ نهاية الفترة" : "اختر تاريخ البداية أو استخدم فترة جاهزة"}
                                </div>
                                <div className="flex items-center gap-1">
                                    <button type="button" onClick={() => setMonthCursor((date) => addMonths(date, 1))} className="rounded-lg p-2 hover:bg-slate-100" aria-label="الشهر التالي"><CaretLeft size={19} /></button>
                                    <button type="button" onClick={() => setMonthCursor((date) => addMonths(date, 12))} className="rounded-lg p-2 hover:bg-slate-100" aria-label="السنة التالية"><CaretDoubleLeft size={19} /></button>
                                </div>
                            </div>

                            <div className="grid gap-6 sm:grid-cols-2">
                                <CalendarMonth monthDate={monthCursor} from={draftFrom} to={draftTo} onSelect={selectDate} />
                                <CalendarMonth monthDate={addMonths(monthCursor, 1)} from={draftFrom} to={draftTo} onSelect={selectDate} />
                            </div>
                        </div>
                    </div>

                    <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-slate-50 px-4 py-3 sm:px-6">
                        <div className="text-sm font-black text-slate-700">
                            الفترة: {formatRangeLabel(draftFrom, draftTo)}
                        </div>
                        <div className="flex gap-2">
                            <button type="button" onClick={() => setOpen(false)} className="rounded-xl border border-slate-300 bg-white px-5 py-2.5 text-sm font-black text-slate-700 hover:bg-slate-100">إلغاء</button>
                            <button type="button" onClick={apply} className="rounded-xl bg-slate-950 px-6 py-2.5 text-sm font-black text-white hover:bg-slate-800">حفظ وتطبيق</button>
                        </div>
                    </footer>
                </div>
            )}
        </div>
    );
}

export { formatRangeLabel, monthGrid };
