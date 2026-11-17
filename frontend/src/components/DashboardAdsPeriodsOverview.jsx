import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const EMPTY_TOTAL = Object.freeze({});
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const amount = (value) => finite(value) && value >= 0;
const count = (value) => Number.isSafeInteger(value) && value >= 0;
const format = (value, digits = 2) => value.toLocaleString("en-US", {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
});
const reported = (segment) => ["active", "completed", "complete", "partial"].includes(segment?.status);
const segmentFor = (stats, id) => {
    const matches = Array.isArray(stats?.segments) ? stats.segments.filter((item) => item?.id === id) : [];
    return matches.length === 1 ? matches[0] : null;
};

function Value({ value, available, integer = false, suffix = "" }) {
    const valid = integer ? count : amount;
    const complete = valid(value);
    const chosen = complete ? value : valid(available) ? available : null;
    if (chosen === null) return <span className="text-slate-400">—</span>;
    return <>
        <bdi dir="ltr" className="block whitespace-nowrap tabular-nums">{format(chosen, integer ? 0 : 2)}{suffix && ` ${suffix}`}</bdi>
        {!complete && <span className="block text-[8px] font-normal text-amber-700">المتاح</span>}
    </>;
}

function Spend({ profit = {} }) {
    if (profit.adSpendApplicable === false) return <span className="text-slate-400">—</span>;
    return <>
        {profit.currency === "USD" && amount(profit.nativeSpend)
            && <bdi dir="ltr" className="block whitespace-nowrap text-[10px] tabular-nums">USD {format(profit.nativeSpend)}</bdi>}
        <Value value={profit.spendSar} available={profit.knownSpendSar} suffix="ر.س" />
    </>;
}

function NetProfit({ profit = {} }) {
    if (profit.complete !== true || !finite(profit.netProfit)) return <span className="text-slate-400">—</span>;
    return <span className={profit.netProfit < 0 ? "text-rose-700" : "text-emerald-700"}>
        <bdi dir="ltr" className="block whitespace-nowrap font-bold tabular-nums">{format(profit.netProfit)}</bdi>
        {profit.provisional === true && <span className="block text-[8px] font-medium">تقديري</span>}
    </span>;
}

function metricEntries(stats = {}, includeBankFees = false) {
    const profit = stats?.profit || {};
    return [
        { key: "spend", label: "الصرف الإعلاني", value: <Spend profit={profit} /> },
        { key: "sales", label: "المبيعات", unit: "ر.س", value: <Value value={stats?.sales} available={stats?.knownSales} /> },
        { key: "orders", label: "الطلبات", value: <Value value={stats?.orders} available={stats?.knownOrders} integer /> },
        { key: "cpa", label: "متوسط تكلفة الطلب", unit: "ر.س", value: <Value value={profit.adSpendApplicable === false ? null : stats?.cpa} /> },
        { key: "costs", label: "إجمالي التكاليف", unit: "ر.س", value: <Value value={profit.totalCost} available={profit.knownTotalCost} /> },
        ...(includeBankFees ? [{ key: "bankFees", label: "منها عمولات البنك", unit: "ر.س",
            value: <Value value={profit.bankFees} available={profit.knownBankFees} /> }] : []),
        { key: "roas", label: "العائد الإعلاني", unit: "×", value: <Value value={profit.adSpendApplicable === false ? null : profit.roas} /> },
        { key: "net", label: "صافي الأرباح", unit: "ر.س", value: <NetProfit profit={profit} /> },
    ];
}

function MetricCells({ stats }) {
    return metricEntries(stats).map((entry) => <td key={entry.key} data-metric={entry.key}
        className="px-2 py-2 text-center font-semibold">{entry.value}</td>);
}

function timeLabel(value) {
    const date = value && new Date(value);
    return date && Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Riyadh", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).format(date) : null;
}

function PeriodTable({ id, records, total }) {
    const morning = id === "morning";
    const title = morning ? "الفترة الصباحية" : "الفترة المسائية";
    const totalSegment = segmentFor(total, id);
    const rows = records.map((record) => ({ ...record, segment: segmentFor(record.stats, id) }));
    const candidates = [totalSegment, ...rows.map((row) => row.segment)].filter(Boolean);
    const reference = candidates.find((segment) => segment.startAt && segment.endAt && segment.cutoffAt) || candidates[0];
    const notStarted = reference?.status === "not_started";
    const current = reference?.cutoffAt && reference?.endAt
        && new Date(reference.cutoffAt).getTime() < new Date(reference.endAt).getTime();
    const end = current && !notStarted ? timeLabel(reference.cutoffAt) : null;
    const unavailable = !candidates.some(reported) && !notStarted;
    const partial = !notStarted && !unavailable && (!reported(totalSegment)
        || totalSegment.status === "partial" || rows.some((row) => !reported(row.segment) || row.segment.status === "partial"));
    return <section data-testid={`dashboard-ads-overview-${id}`}
        data-state={notStarted ? "not_started" : unavailable ? "unavailable" : partial ? "partial" : reference?.status}
        className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <header className="flex items-center justify-between gap-2 bg-slate-50 px-3 py-2">
            <h4 className="m-0 text-xs font-extrabold text-slate-800">{title}</h4>
            <bdi dir="ltr" className="text-[10px] font-semibold text-slate-600">
                {morning ? "00:00" : "12:00"} – {end || (morning ? "12:00" : "24:00")}
            </bdi>
        </header>
        <div className="overflow-x-auto overscroll-x-contain" tabIndex={0} aria-label={`تمرير جدول ${title}`}>
            <table dir="rtl" className="w-full table-fixed border-collapse text-[10px] leading-4" style={{ minWidth: 720 }}>
                <caption className="sr-only">أداء المنصات في {title}</caption>
                <colgroup>{[18, 15, 13, 8, 12, 13, 9, 12].map((width, index) => <col key={index} style={{ width: `${width}%` }} />)}</colgroup>
                <thead className="bg-slate-50 text-[9px] font-bold text-slate-600"><tr>
                    <th scope="col" className="px-2 py-2 text-right">المنصة / الحساب</th>
                    {metricEntries().map((entry) => <th key={entry.key} scope="col" className="px-2 py-2 text-center">
                        {entry.label}{entry.unit && <span className="block text-[8px] font-normal">{entry.unit}</span>}
                    </th>)}
                </tr></thead>
                <tbody>
                    {notStarted ? <tr><td colSpan={8} className="px-3 py-5 text-center text-slate-400">
                        {morning ? "لم تبدأ بيانات الفترة بعد" : "تبدأ عند 12 ظهرًا"}
                    </td></tr> : rows.length ? rows.map((row) => <tr key={row.key} data-overview-row={row.key} className="border-t border-slate-100">
                        <th scope="row" className="px-2 py-2 text-right font-bold" title={row.label}>
                            <span className="flex min-w-0 items-center gap-1.5">
                                <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: row.color || "#94a3b8" }} />
                                <span className="truncate">{row.label}</span>
                            </span>
                        </th>
                        <MetricCells stats={reported(row.segment) ? row.segment : {}} />
                    </tr>) : <tr><td colSpan={8} className="px-3 py-5 text-center text-slate-400">بانتظار بيانات المنصات.</td></tr>}
                </tbody>
                {!notStarted && <tfoot className="border-t border-yellow-200 bg-yellow-50 font-bold">
                    <tr data-testid={`dashboard-ads-overview-${id}-total`}>
                        <th scope="row" className="px-2 py-2 text-right">{morning ? "إجمالي الصباح" : "إجمالي المساء"}</th>
                        <MetricCells stats={reported(totalSegment) ? totalSegment : {}} />
                    </tr>
                </tfoot>}
            </table>
        </div>
        {unavailable && <p className="m-0 border-t px-3 py-2 text-[10px] text-slate-500">بيانات الفترة غير متاحة.</p>}
        {partial && <p className="m-0 border-t px-3 py-2 text-[10px] text-amber-700">بعض بيانات الفترة غير مكتملة.</p>}
    </section>;
}

function positionFor(anchor, measuredHeight = 0) {
    const width = Math.max(0, Math.min(820, window.innerWidth - 24));
    const maxHeight = Math.max(0, window.innerHeight - 24);
    const height = Math.min(measuredHeight, maxHeight);
    const below = anchor.bottom + 8;
    const top = below + height <= window.innerHeight - 12 ? below : anchor.top - height - 8;
    return {
        left: Math.max(12, Math.min(anchor.left + (anchor.width - width) / 2, window.innerWidth - width - 12)),
        top: Math.max(12, Math.min(top, window.innerHeight - height - 12)),
        width, maxHeight,
    };
}

/** Footer-only overview of already-loaded daily and half-day account metrics. */
export default function DashboardAdsPeriodsOverview({ children, records = [], total = EMPTY_TOTAL, fromDate, toDate,
    contextKey, active = true, onActivate, className = "", ...props }) {
    const anchor = useRef(null);
    const popup = useRef(null);
    const hideTimer = useRef(null);
    const previousContext = useRef(null);
    const popupId = useId();
    const [position, setPosition] = useState(null);
    const [pinned, setPinned] = useState(false);
    const shown = active && Boolean(position);
    const rows = Array.isArray(records) ? records.filter((row) => row && typeof row.label === "string") : [];
    const singleDay = Boolean(fromDate && (!toDate || fromDate === toDate));
    const cancelHide = () => { window.clearTimeout(hideTimer.current); hideTimer.current = null; };
    const close = () => { cancelHide(); setPinned(false); setPosition(null); };
    const show = () => {
        cancelHide();
        onActivate?.();
        const rect = anchor.current?.getBoundingClientRect();
        if (rect) setPosition(positionFor(rect, popup.current?.getBoundingClientRect().height));
    };
    const leave = () => {
        cancelHide();
        if (!pinned) hideTimer.current = window.setTimeout(() => setPosition(null), 160);
    };
    const toggle = () => {
        if (shown && pinned) close();
        else { setPinned(true); show(); }
    };
    const onBlur = (event) => {
        if (!anchor.current?.contains(event.relatedTarget) && !popup.current?.contains(event.relatedTarget)) close();
    };
    useEffect(() => {
        const previous = previousContext.current;
        const next = { fromDate, toDate, contextKey, total, rows: rows.map(({ key, label, stats }) => ({ key, label, stats })) };
        previousContext.current = next;
        if (previous && (previous.fromDate !== fromDate || previous.toDate !== toDate || previous.contextKey !== contextKey
            || previous.total !== total || previous.rows.length !== next.rows.length
            || previous.rows.some((row, index) => row.key !== next.rows[index].key
                || row.label !== next.rows[index].label || row.stats !== next.rows[index].stats))) close();
    });
    useEffect(() => { if (!active) close(); }, [active]);
    useEffect(() => () => window.clearTimeout(hideTimer.current), []);
    useEffect(() => {
        if (!shown) return undefined;
        const outside = (event) => {
            if (!anchor.current?.contains(event.target) && !popup.current?.contains(event.target)) close();
        };
        const escape = (event) => { if (event.key === "Escape") close(); };
        document.addEventListener("pointerdown", outside);
        document.addEventListener("keydown", escape);
        return () => {
            document.removeEventListener("pointerdown", outside);
            document.removeEventListener("keydown", escape);
        };
    }, [shown]);
    useLayoutEffect(() => {
        if (!shown) return undefined;
        const place = () => {
            const rect = anchor.current?.getBoundingClientRect();
            if (!rect || !popup.current) return;
            const next = positionFor(rect, popup.current.getBoundingClientRect().height);
            setPosition((current) => current && Object.keys(next).every((key) => current[key] === next[key]) ? current : next);
        };
        place();
        window.addEventListener("resize", place);
        window.addEventListener("scroll", place, true);
        const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(place);
        observer?.observe(popup.current);
        return () => {
            window.removeEventListener("resize", place);
            window.removeEventListener("scroll", place, true);
            observer?.disconnect();
        };
    }, [shown, total, position?.width]);
    return <>
        <tr {...props} ref={anchor} tabIndex={0} aria-label="تفاصيل الإجمالي حسب فترات اليوم" aria-haspopup="dialog"
            aria-expanded={shown} aria-controls={shown ? popupId : undefined}
            className={`${className} cursor-help hover:bg-yellow-100/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-yellow-500`}
            onMouseEnter={show} onMouseLeave={leave} onFocus={show} onBlur={onBlur}
            onClick={(event) => { event.preventDefault(); toggle(); }}
            onKeyDown={(event) => {
                if (event.key === "Escape") { event.preventDefault(); close(); }
                if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
                if (event.key === "Tab" && pinned && !event.shiftKey) { event.preventDefault(); popup.current?.focus(); }
            }}>{children}</tr>
        {shown && createPortal(<div ref={popup} id={popupId} role="dialog" aria-modal="false"
            aria-labelledby={`${popupId}-title`} dir="rtl" tabIndex={-1} data-testid="dashboard-ads-periods-overview"
            className="fixed z-[9999] rounded-xl border border-slate-200 bg-white p-3 text-right text-slate-900 shadow-xl"
            style={{ ...position, boxSizing: "border-box", overflowY: "auto", overflowX: "hidden" }}
            onMouseEnter={cancelHide} onMouseLeave={leave} onBlur={onBlur}
            onKeyDown={(event) => {
                if (event.key === "Escape") { event.preventDefault(); anchor.current?.focus(); close(); }
                if (event.key === "Tab" && event.shiftKey && event.target === popup.current) {
                    event.preventDefault(); anchor.current?.focus();
                }
            }}>
            <div className="mb-1 flex items-start justify-between gap-2">
                <h3 id={`${popupId}-title`} className="m-0 text-sm font-black">تفاصيل إجمالي المنصات</h3>
                <button type="button" onClick={close} aria-label="إغلاق تفاصيل إجمالي المنصات"
                    className="rounded px-1 text-[10px] text-slate-500 hover:text-slate-800">إغلاق</button>
            </div>
            <div className="mb-3 text-[10px] text-slate-500">توقيت الرياض · <bdi dir="ltr">{fromDate}</bdi>
                {toDate && toDate !== fromDate && <> إلى <bdi dir="ltr">{toDate}</bdi></>}</div>
            {singleDay ? <div className="space-y-3">
                <PeriodTable id="morning" records={rows} total={total} />
                <PeriodTable id="afternoon" records={rows} total={total} />
            </div> : <p data-testid="dashboard-ads-overview-unavailable"
                className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                اختر يومًا واحدًا لعرض الصباح والمساء. يظهر أدناه إجمالي الفترة المحددة.
            </p>}
            <section data-testid="dashboard-ads-overview-total" className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50/50 p-3">
                <h4 className="mb-2 mt-0 text-xs font-extrabold text-emerald-800">{singleDay ? "إجمالي اليوم" : "إجمالي الفترة"}</h4>
                <dl className="m-0 grid grid-cols-2 gap-2 text-center sm:grid-cols-3">
                    {metricEntries(total, true).map((entry) => <div key={entry.key} data-metric={entry.key}
                        className={entry.key === "net" ? "col-span-full border-t border-emerald-200 pt-2" : ""}>
                        <dt className="mb-1 text-[9px] text-slate-600">{entry.label}{entry.unit && ` (${entry.unit})`}</dt>
                        <dd className="m-0 text-[11px] font-bold">{entry.value}</dd>
                    </div>)}
                </dl>
            </section>
            <p className="mb-0 mt-2 text-[9px] leading-4 text-slate-500">صافي الأرباح = المبيعات − الصرف الإعلاني − إجمالي التكاليف.</p>
            <p className="mb-0 mt-1 text-[9px] leading-4 text-slate-500">عمولات البنك المعروضة ضمن إجمالي التكاليف ومخصومة مرة واحدة. الأرباح قبل المصروفات التشغيلية.</p>
        </div>, document.body)}
    </>;
}
