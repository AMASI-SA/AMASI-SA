import { useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

const known = (value) => typeof value === "number" && Number.isFinite(value);
const money = (value) => known(value)
    ? value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ر.س"
    : "غير مكتمل";
const count = (value, suffix) => known(value)
    ? value.toLocaleString("en-US", { maximumFractionDigits: 2 }) + " " + suffix : "غير مكتمل";

function accountTime(value, zone) {
    if (!value || !zone) return null;
    try {
        const point = new Date(value);
        if (!Number.isFinite(point.getTime())) return null;
        return new Intl.DateTimeFormat("en-GB", {
            timeZone: zone, day: "2-digit", month: "2-digit", year: "numeric",
            hour: "2-digit", minute: "2-digit", hourCycle: "h23",
        }).format(point);
    } catch { return null; }
}

function explanation(campaign, report) {
    if (!campaign?.campaignId) return "لا يوجد معرّف حملة موثوق في بيانات هذا الطلب من سلة.";
    if (report === undefined) return "تحميل الإحصاءات...";
    const reason = report?.spend_reason || report?.status;
    if (reason === "snapchat_hours_incomplete") return "مبيعات سلة مستقلة؛ ساعات صرف سناب غير مكتملة، لذلك لا تُحسب تكلفة الطلب أو العائد من صرف جزئي.";
    if (reason === "snapchat_account_day_not_aligned") return "طلبات سلة بتوقيت الحساب؛ صرف سناب المتوفر حسب يوم الرياض لا يطابق يوم هذا الحساب.";
    if (reason === "campaign_spend_not_available") return "طلبات سلة ومبيعاتها مستقلة؛ صرف الحملة غير متوفر للفترة نفسها.";
    if (reason === "ambiguous_account") return "معرّف الحملة يطابق أكثر من حساب؛ يلزم تحديد الحساب الصحيح.";
    if (reason === "account_timezone_unavailable") return "توقيت الحساب غير متاح؛ لم يُفترض توقيت بديل.";
    if (reason === "campaign_account_unavailable") return "تعذرت مطابقة معرّف الحملة بالحساب الإعلاني.";
    if (reason === "report_unavailable" || reason === "account_read_incomplete") return "تعذر تحميل التقرير الآن. أعد فتح الصفحة؛ لا توجد أرقام تقديرية.";
    if (report?.status === "available") return "طلبات ومبيعات سلة · الصرف المسجل في ميزان";
    return "بعض بيانات التقرير غير مكتملة؛ القيم غير المعروفة لا تُستبدل بصفر.";
}

export default function CampaignHoverBadge({ badge, campaign, report }) {
    const ref = useRef(null);
    const tooltipId = useId();
    const [position, setPosition] = useState(null);
    const [pinned, setPinned] = useState(false);
    const isShown = Boolean(position);
    const show = () => {
        const rect = ref.current?.getBoundingClientRect();
        if (!rect) return;
        const width = Math.min(320, window.innerWidth - 20);
        const left = Math.max(10, Math.min(rect.left, window.innerWidth - width - 10));
        const top = rect.bottom + 380 > window.innerHeight ? Math.max(8, rect.top - 370) : rect.bottom + 8;
        setPosition({ left, top, width });
    };
    const hide = () => { if (!pinned) setPosition(null); };
    const rows = [
        ["الصرف المسجل", money(report?.spend_sar)],
        ["عدد الطلبات", count(report?.orders, "طلب")],
        ["عدد القطع", count(report?.pieces, "قطعة")],
        ["إجمالي المبيعات", money(report?.sales_sar)],
        ["تكلفة الطلب", report?.orders === 0 && known(report?.spend_sar) ? "لا توجد طلبات" : money(report?.cpa_sar)],
        ["العائد الإعلاني", report?.spend_sar === 0 ? "لا يوجد صرف" : known(report?.roas) ? report.roas.toFixed(2) + "×" : "غير مكتمل"],
    ];
    const updated = accountTime(report?.updated_at, report?.timezone);
    const covered = accountTime(report?.spend_as_of, report?.timezone);
    const displayName = campaign?.campaignDisplay || report?.campaign_name || "الحملة غير محددة";
    return <>
        <span
            ref={ref} role="button" tabIndex={0}
            title={displayName}
            aria-label={`مصدر الطلب: ${badge.label}، تقرير الحملة: ${displayName}`}
            aria-expanded={isShown} aria-describedby={isShown ? tooltipId : undefined}
            onMouseEnter={show} onMouseLeave={hide} onFocus={show}
            onBlur={() => { setPinned(false); setPosition(null); }}
            onClick={(event) => {
                event.preventDefault(); event.stopPropagation();
                if (isShown && pinned) { setPinned(false); setPosition(null); }
                else { setPinned(true); show(); }
            }}
            onKeyDown={(event) => {
                if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setPinned(false); setPosition(null); }
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault(); event.stopPropagation();
                    if (pinned) { setPinned(false); setPosition(null); }
                    else { setPinned(true); show(); }
                }
            }}
            className={`inline-flex h-5 w-5 shrink-0 cursor-help items-center justify-center rounded-full border text-[9px] font-black leading-none shadow-sm ${badge.className}`}
        >{badge.mark}</span>
        {isShown && createPortal(
            <div id={tooltipId} role="tooltip" dir="rtl"
                className="fixed z-[9999] rounded-xl border border-slate-200 bg-white p-3 text-right text-xs text-slate-900 shadow-xl"
                style={{ ...position, maxHeight: "calc(100vh - 20px)", overflowY: "auto", pointerEvents: "none" }}>
                <div className="mb-1 font-black leading-5">{displayName}</div>
                <div className="mb-2 text-[10px] text-slate-500">
                    اليوم من 12:00 ص بتوقيت الحساب: <bdi dir="ltr">{report?.timezone || "غير متاح"}</bdi>
                    {report?.date && <span> · <bdi dir="ltr">{report.date}</bdi></span>}
                </div>
                <table className="w-full border-collapse"><tbody>{rows.map(([label, value]) =>
                    <tr key={label} className="border-t border-slate-100">
                        <th className="py-1.5 text-right font-medium text-slate-600">{label}</th>
                        <td className="py-1.5 text-left font-bold" dir="ltr">{value}</td>
                    </tr>
                )}</tbody></table>
                <div className="mt-2 text-[10px] text-slate-500">{explanation(campaign, report)}</div>
                {covered && <div className="mt-1 text-[10px] text-slate-500">الصرف حتى: <bdi dir="ltr">{covered}</bdi> · قراءة أولية قابلة للتحديث</div>}
                {updated && <div className="mt-1 text-[10px] text-slate-400">آخر تحديث للصرف: <bdi dir="ltr">{updated}</bdi></div>}
            </div>, document.body
        )}
    </>;
}
