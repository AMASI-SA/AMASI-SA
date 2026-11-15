import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const known = (value) => typeof value === "number" && Number.isFinite(value);
const money = (value) => known(value)
    ? value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ر.س"
    : "غير مكتمل";
const count = (value, suffix) => known(value)
    ? value.toLocaleString("en-US", { maximumFractionDigits: 2 }) + " " + suffix : "غير مكتمل";

function positionFor(anchor, measuredHeight = 0) {
    const width = Math.max(0, Math.min(340, window.innerWidth - 20));
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

/** A row-wide, local-only summary. Opening it never starts a network read. */
export default function DashboardAdsAccountRow({ label, stats = {}, fromDate, toDate, children, ...props }) {
    const anchor = useRef(null);
    const popup = useRef(null);
    const popupId = useId();
    const [position, setPosition] = useState(null);
    const [pinned, setPinned] = useState(false);
    const shown = Boolean(position);
    const report = stats.profit || {};
    const show = () => {
        const rect = anchor.current?.getBoundingClientRect();
        if (rect) setPosition(positionFor(rect, popup.current?.getBoundingClientRect().height));
    };
    const close = () => { setPinned(false); setPosition(null); };
    const toggle = () => {
        if (shown && pinned) close();
        else { setPinned(true); show(); }
    };
    const onBlur = (event) => {
        if (!anchor.current?.contains(event.relatedTarget) && !popup.current?.contains(event.relatedTarget)) close();
    };
    useEffect(() => { close(); }, [fromDate, toDate, label]);
    useEffect(() => {
        if (!pinned) return undefined;
        const outside = (event) => {
            if (!anchor.current?.contains(event.target) && !popup.current?.contains(event.target)) close();
        };
        document.addEventListener("pointerdown", outside);
        return () => document.removeEventListener("pointerdown", outside);
    }, [pinned]);
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
    const profitAvailable = report.available === true && known(report.netProfit);
    const rows = [
        ["الصرف المسجل", money(report.spend)],
        ["عدد الطلبات", count(stats.orders, "طلب")],
        ["عدد القطع", count(report.pieces, "قطعة")],
        ["إجمالي المبيعات", money(stats.sales)],
        ["تكلفة المنتجات", money(report.productCost)],
        ["تكلفة الشحن", money(report.shippingCost)],
        ["عمولات طرق الدفع", money(report.paymentFees)],
        ["إجمالي التكاليف", money(report.totalCost)],
        ["تكلفة الطلب", stats.orders === 0 ? "لا توجد طلبات" : money(stats.cpa)],
        ["العائد الإعلاني", report.spend === 0 ? "لا يوجد صرف مسجل"
            : known(report.roas) ? report.roas.toFixed(2) + "×" : "غير مكتمل"],
        ["صافي الأرباح", profitAvailable
            ? money(report.netProfit) + (report.provisional ? " · تقديري" : "") : "غير مكتمل", true],
    ];
    return <>
        <tr {...props} ref={anchor} tabIndex={0} aria-label={`تفاصيل ${label}`}
            aria-expanded={shown} aria-describedby={shown ? popupId : undefined}
            className={`${props.className || ""} cursor-help hover:bg-yellow-50/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-yellow-500`}
            onMouseEnter={show} onMouseLeave={() => { if (!pinned) setPosition(null); }}
            onFocus={show} onBlur={onBlur}
            onClick={(event) => { event.preventDefault(); toggle(); }}
            onKeyDown={(event) => {
                if (event.key === "Escape") { event.preventDefault(); close(); }
                if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
                if (event.key === "Tab" && pinned && !event.shiftKey) { event.preventDefault(); popup.current?.focus(); }
            }}>
            {children}
        </tr>
        {shown && createPortal(<div ref={popup} id={popupId} role="tooltip" dir="rtl"
            tabIndex={pinned ? 0 : undefined} data-testid="dashboard-ads-account-tooltip"
            className="fixed z-[9999] rounded-xl border border-slate-200 bg-white p-3 text-right text-xs text-slate-900 shadow-xl"
            style={{ ...position, boxSizing: "border-box", overflowY: "auto", pointerEvents: pinned ? "auto" : "none" }}
            onBlur={onBlur} onKeyDown={(event) => {
                if (event.key === "Escape") { event.preventDefault(); anchor.current?.focus(); close(); }
                if (event.key === "Tab" && event.shiftKey) { event.preventDefault(); anchor.current?.focus(); }
            }}>
            <div className="mb-1 flex items-start justify-between gap-2"><h3 className="font-black leading-5">{label}</h3>
                {pinned && <button type="button" onClick={close} className="rounded px-1 text-[10px] text-slate-500" aria-label="إغلاق تفاصيل الحساب">إغلاق</button>}
            </div>
            <div className="mb-2 text-[10px] text-slate-500">الفترة المحددة · توقيت الرياض · <bdi dir="ltr">{fromDate}</bdi>
                {toDate && toDate !== fromDate && <> إلى <bdi dir="ltr">{toDate}</bdi></>}</div>
            <table className="w-full border-collapse"><tbody>{rows.map(([name, value, emphasized]) =>
                <tr key={name} className={`border-t ${emphasized ? "border-slate-300 bg-slate-50" : "border-slate-100"}`}>
                    <th className={`py-1.5 text-right ${emphasized ? "font-black text-slate-900" : "font-medium text-slate-600"}`}>{name}</th>
                    <td className={`py-1.5 text-left font-bold ${emphasized && profitAvailable && report.netProfit < 0 ? "text-rose-700" : ""}`} dir="ltr">{value}</td>
                </tr>)}</tbody></table>
            <p className="mt-2 text-[10px] leading-5 text-slate-500">إجمالي التكاليف: المنتجات والشحن وعمولات طرق الدفع.</p>
            <p className="text-[10px] leading-5 text-slate-500">صافي الأرباح = المبيعات − الصرف الإعلاني − إجمالي التكاليف.</p>
            {stats.partial && <p className="mt-1 text-[10px] text-amber-700">بيانات سلة أو إسنادها للحساب غير مكتملة.</p>}
            {!known(report.totalCost) && <p className="mt-1 text-[10px] text-amber-700">التكلفة غير مكتملة؛ صافي الأرباح ينتظر اكتمالها.</p>}
            {report.provisional && profitAvailable && <p className="mt-1 text-[10px] text-amber-700">صافي الأرباح تقديري حتى اكتمال بيانات الصرف.</p>}
        </div>, document.body)}
    </>;
}
