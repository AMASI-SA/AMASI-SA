import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import DashboardAdsProfitValue, { dashboardAdsDisplayProfit } from "./DashboardAdsProfitValue";

const known = (value) => typeof value === "number" && Number.isFinite(value);
const number = (value, digits = 2, maximumDigits = digits) => value.toLocaleString("en-US", {
    minimumFractionDigits: digits, maximumFractionDigits: maximumDigits,
});

function MetricValue({ value, available, suffix = "ر.س", digits = 2, maximumDigits = digits }) {
    const complete = known(value);
    const amount = complete ? value : available;
    if (!known(amount)) return <span className="font-medium text-slate-400">—</span>;
    return <span><bdi dir="ltr" className="tabular-nums">{number(amount, digits, maximumDigits)}{suffix && ` ${suffix}`}</bdi>
        {!complete && <span className="mr-1 text-[9px] font-medium text-amber-700">المتاح</span>}
    </span>;
}

function SpendValue({ report }) {
    if (report.adSpendApplicable === false) return <span className="font-medium text-slate-400">—</span>;
    return <span className="inline-flex flex-col items-end">
        {report.currency === "USD" && known(report.nativeSpend)
            && <bdi dir="ltr" className="text-[10px] tabular-nums">USD {number(report.nativeSpend)}</bdi>}
        <MetricValue value={report.spendSar} available={report.knownSpendSar} />
    </span>;
}

function FinancialRows({ stats = {}, detailed = true }) {
    const report = stats.profit || {};
    const displayedProfit = dashboardAdsDisplayProfit(report);
    const profitReady = displayedProfit.value !== null;
    const advertising = report.adSpendApplicable !== false;
    const cpa = advertising && known(stats.cpa) && stats.cpa >= 0 ? stats.cpa : null;
    const roas = advertising && known(report.roas) && report.roas >= 0 ? report.roas : null;
    const rows = [
        ["إجمالي المبيعات", <MetricValue value={stats.sales} available={stats.knownSales} />],
        ["الصرف الإعلاني", <SpendValue report={report} />],
        ...(detailed ? [
            ["عدد الطلبات / القطع", <><MetricValue value={stats.orders} available={stats.knownOrders} suffix="طلب" digits={0} />
                <span className="mx-1 font-normal text-slate-300">·</span>
                <MetricValue value={report.pieces} available={report.knownPieces} suffix="قطعة" digits={0} maximumDigits={2} /></>],
        ] : []),
        ["تكلفة الطلب", <MetricValue value={cpa} />],
        ["العائد الإعلاني", <MetricValue value={roas} suffix="×" />],
        ["إجمالي التكاليف", <MetricValue value={report.totalCost} available={report.knownTotalCost} />],
        ["منها عمولات البنك", <MetricValue
            value={known(report.bankFees) && report.bankFees >= 0 ? report.bankFees : null}
            available={known(report.knownBankFees) && report.knownBankFees >= 0 ? report.knownBankFees : null} />],
        ["الربح قبل التشغيل", <DashboardAdsProfitValue profit={report} inline currency
            unavailableReason={stats.accountUnassigned ? "بيانات الربح غير مكتملة" : undefined} />, true],
    ];
    return <dl className="text-[11px] leading-5">{rows.map(([label, value, emphasis]) =>
        <div key={label} className={`flex items-center justify-between gap-2 border-t py-1 ${emphasis
            ? "mt-0.5 border-emerald-200 font-extrabold" : "border-slate-100"}`}>
            <dt className={emphasis ? "text-slate-900" : "shrink-0 font-medium text-slate-600"}>{label}</dt>
            <dd className={`m-0 text-left font-bold ${emphasis && profitReady
                ? displayedProfit.value < 0 ? "text-rose-700" : "text-emerald-700" : "text-slate-900"}`}>{value}</dd>
        </div>)}</dl>;
}

function timeLabel(value) {
    const parsed = value && new Date(value);
    return parsed && Number.isFinite(parsed.getTime()) ? new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Riyadh", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).format(parsed) : null;
}

function PeriodCard({ segment }) {
    const morning = segment.id === "morning";
    const notStarted = segment.status === "not_started";
    const cutoff = timeLabel(segment.cutoffAt);
    const current = segment.cutoffAt && segment.endAt
        && new Date(segment.cutoffAt).getTime() < new Date(segment.endAt).getTime();
    const endLabel = current && !notStarted && cutoff ? cutoff : morning ? "12:00" : "24:00";
    return <section className={`rounded-lg border p-2.5 ${notStarted
        ? "border-dashed border-slate-200 bg-slate-50/60" : "border-slate-200 bg-white"}`}
        data-testid={`dashboard-ads-period-${segment.id}`} data-state={segment.status}>
        <header className="mb-1.5 flex items-center justify-between gap-2">
            <h4 className="text-[11px] font-extrabold text-slate-800">{morning ? "الفترة الصباحية" : "الفترة المسائية"}</h4>
            <bdi dir="ltr" className="rounded bg-slate-100 px-1.5 text-[10px] font-semibold text-slate-600">
                {morning ? "00:00" : "12:00"} – {endLabel}
            </bdi>
        </header>
        {notStarted ? <p className="mb-0 mt-3 py-3 text-center text-[11px] text-slate-400">{morning ? "لم تبدأ بيانات الفترة بعد" : "تبدأ عند 12 ظهرًا"}</p>
            : segment.status === "unavailable" ? <p className="my-3 text-center text-[11px] text-slate-400">بيانات الفترة غير متاحة</p>
                : <>
                    <FinancialRows stats={segment} />
                    {segment.status === "partial" && <p className="mb-0 mt-1 text-[9px] leading-4 text-amber-700">بعض بيانات الفترة غير مكتملة.</p>}
                </>}
    </section>;
}

function positionFor(anchor, measuredHeight = 0) {
    const width = Math.max(0, Math.min(360, window.innerWidth - 20));
    const maxHeight = Math.max(0, window.innerHeight - 20);
    const height = Math.min(measuredHeight, maxHeight);
    const below = anchor.bottom + 8;
    const top = below + height <= window.innerHeight - 10 ? below : anchor.top - height - 8;
    return {
        left: Math.max(10, Math.min(anchor.left, window.innerWidth - width - 10)),
        top: Math.max(10, Math.min(top, window.innerHeight - height - 10)),
        width, maxHeight,
    };
}

/** Opens locally from the existing snapshots; hovering never fetches new data. */
export default function DashboardAdsAccountRow({ label, stats = {}, fromDate, toDate,
    active = true, onActivate, children, ...props }) {
    const anchor = useRef(null);
    const popup = useRef(null);
    const hideTimer = useRef(null);
    const popupId = useId();
    const [position, setPosition] = useState(null);
    const [pinned, setPinned] = useState(false);
    const shown = active && Boolean(position);
    const report = stats.profit || {};
    const segments = Array.isArray(stats.segments) ? stats.segments : [];
    const singleDay = !toDate || fromDate === toDate;
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
    useEffect(() => { close(); }, [fromDate, toDate, label]);
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
    }, [shown, stats, position?.width]);
    return <>
        <tr {...props} ref={anchor} tabIndex={0} aria-label={`تفاصيل ${label}`} aria-haspopup="dialog"
            aria-expanded={shown} aria-controls={shown ? popupId : undefined}
            className={`${props.className || ""} cursor-help hover:bg-yellow-50/70 focus-visible:outline focus-visible:outline-2 focus-visible:outline-yellow-500 ${shown ? "bg-yellow-50/70" : ""}`}
            onMouseEnter={show} onMouseLeave={leave} onFocus={show} onBlur={onBlur}
            onClick={(event) => { event.preventDefault(); toggle(); }}
            onKeyDown={(event) => {
                if (event.key === "Escape") { event.preventDefault(); close(); }
                if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
                if (event.key === "Tab" && pinned && !event.shiftKey) { event.preventDefault(); popup.current?.focus(); }
            }}>
            {children}
        </tr>
        {shown && createPortal(<div ref={popup} id={popupId} role="dialog" aria-modal="false"
            aria-labelledby={`${popupId}-title`} dir="rtl" tabIndex={-1} data-testid="dashboard-ads-account-tooltip"
            className="fixed z-[9999] rounded-xl border border-slate-200 bg-white p-3 text-right text-xs text-slate-900 shadow-xl"
            style={{ ...position, boxSizing: "border-box", overflowY: "auto" }}
            onMouseEnter={cancelHide} onMouseLeave={leave} onBlur={onBlur}
            onKeyDown={(event) => {
                if (event.key === "Escape") { event.preventDefault(); anchor.current?.focus(); close(); }
                if (event.key === "Tab" && event.shiftKey && event.target === popup.current) {
                    event.preventDefault(); anchor.current?.focus();
                }
            }}>
            <div className="mb-1 flex items-start justify-between gap-2">
                <h3 id={`${popupId}-title`} className="m-0 text-xs font-black leading-5">{label}</h3>
                <button type="button" onClick={close} className="rounded px-1 text-[10px] text-slate-400 hover:text-slate-700" aria-label="إغلاق تفاصيل الحساب">إغلاق</button>
            </div>
            <div className="mb-2.5 text-[10px] text-slate-500">توقيت الرياض · <bdi dir="ltr">{fromDate}</bdi>
                {toDate && toDate !== fromDate && <> إلى <bdi dir="ltr">{toDate}</bdi></>}</div>
            {singleDay && <div className="space-y-2">
                {segments.length ? segments.map((segment) => <PeriodCard key={segment.id} segment={segment} />)
                    : <p className="rounded-lg bg-slate-50 p-3 text-center text-[11px] text-slate-500">بانتظار تفاصيل فترتَي اليوم.</p>}
            </div>}
            <section className="mt-2 rounded-lg border border-emerald-200 bg-emerald-50/50 p-2.5" data-testid="dashboard-ads-daily-summary">
                <h4 className="mb-1.5 mt-0 text-[11px] font-extrabold text-emerald-800">{singleDay ? "إجمالي اليوم" : "إجمالي الفترة"}</h4>
                <FinancialRows stats={stats} detailed={!singleDay} />
                {singleDay && <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-slate-600">
                    <span>طلبات سلة <MetricValue value={stats.orders} available={stats.knownOrders} suffix="" digits={0} /></span>
                    <span>القطع <MetricValue value={report.pieces} available={report.knownPieces} suffix="" digits={0} maximumDigits={2} /></span>
                </div>}
            </section>
            <p className="mb-0 mt-2 text-[9px] leading-4 text-slate-500">{stats.accountUnassigned
                ? "مساهمة الطلبات = المبيعات − تكلفة المنتجات والشحن وعمولات الدفع."
                : report.adSpendApplicable === false
                ? "صافي الأرباح = المبيعات − إجمالي التكاليف."
                : "صافي الأرباح = المبيعات − الصرف الإعلاني − إجمالي التكاليف."}</p>
            <p className="mb-0 mt-1 text-[9px] leading-4 text-slate-500">{stats.accountUnassigned
                ? "الصرف وعمولات البنك يُحتسبان على حسابات سناب مرة واحدة. المصروفات التشغيلية تُخصم في الصافي الإجمالي."
                : "عمولات البنك المعروضة ضمن إجمالي التكاليف ومخصومة مرة واحدة. الأرباح قبل المصروفات التشغيلية."}</p>
            {report.provisional && report.complete && <p className="mb-0 mt-1 text-[9px] leading-4 text-amber-700">صافي الأرباح تقديري حتى اكتمال بيانات الصرف.</p>}
        </div>, document.body)}
    </>;
}
