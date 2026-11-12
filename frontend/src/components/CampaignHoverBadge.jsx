import { useRef, useState } from "react";
import { createPortal } from "react-dom";

const money = (value) => typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ر.س"
    : "غير مكتمل";
const count = (value, suffix) => typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US", { maximumFractionDigits: 2 }) + " " + suffix
    : "غير مكتمل";

export default function CampaignHoverBadge({ badge, campaign, report }) {
    const ref = useRef(null);
    const [position, setPosition] = useState(null);
    const [pinned, setPinned] = useState(false);
    const isShown = Boolean(position);
    const show = () => {
        const rect = ref.current?.getBoundingClientRect();
        if (!rect) return;
        const width = Math.min(300, window.innerWidth - 20);
        const left = Math.max(10, Math.min(rect.left, window.innerWidth - width - 10));
        const top = rect.bottom + 8 + 290 > window.innerHeight
            ? Math.max(8, rect.top - 300) : rect.bottom + 8;
        setPosition({ left, top, width });
    };
    const hide = () => { if (!pinned) setPosition(null); };
    const rows = [
        ["الصرف المسجل", money(report?.spend_sar)],
        ["عدد الطلبات", count(report?.orders, "طلب")],
        ["عدد القطع", count(report?.pieces, "قطعة")],
        ["إجمالي المبيعات", money(report?.sales_sar)],
        ["تكلفة الطلب", money(report?.cpa_sar)],
        ["العائد الإعلاني", report?.roas == null ? "غير مكتمل" : report.roas.toFixed(2) + "×"],
    ];
    return (
        <>
            <span
                ref={ref}
                role="button"
                tabIndex={0}
                title={campaign?.campaignDisplay || "الحملة غير محددة"}
                aria-label={`تقرير الحملة: ${campaign?.campaignDisplay || "الحملة غير محددة"}`}
                aria-expanded={isShown}
                onMouseEnter={show}
                onMouseLeave={hide}
                onFocus={show}
                onBlur={() => { setPinned(false); setPosition(null); }}
                onClick={(event) => {
                    event.stopPropagation();
                    if (isShown && pinned) { setPinned(false); setPosition(null); }
                    else { setPinned(true); show(); }
                }}
                onKeyDown={(event) => {
                    if (event.key === "Escape") { event.stopPropagation(); setPinned(false); setPosition(null); }
                    if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault(); event.stopPropagation();
                        if (pinned) { setPinned(false); setPosition(null); }
                        else { setPinned(true); show(); }
                    }
                }}
                className={`inline-flex h-5 w-5 shrink-0 cursor-help items-center justify-center rounded-full border text-[9px] font-black leading-none shadow-sm ${badge.className}`}
            >{badge.mark}</span>
            {isShown && createPortal(
                <div
                    role="tooltip"
                    dir="rtl"
                    className="fixed z-[9999] rounded-xl border border-slate-200 bg-white p-3 text-right text-xs text-slate-900 shadow-xl"
                    style={{ left: position.left, top: position.top, width: position.width, pointerEvents: "none" }}
                >
                    <div className="mb-1 font-black leading-5">{campaign?.campaignDisplay || report?.campaign_name || "اسم الحملة غير متوفر"}</div>
                    <div className="mb-2 text-[10px] text-slate-500">اليوم حسب توقيت الحساب: {report?.timezone || "غير متاح"}{report?.date ? ` · ${report.date}` : ""}</div>
                    <table className="w-full border-collapse">
                        <tbody>{rows.map(([label, value]) => (
                            <tr key={label} className="border-t border-slate-100">
                                <th className="py-1.5 text-right font-medium text-slate-600">{label}</th>
                                <td className="py-1.5 text-left font-bold" dir="ltr">{value}</td>
                            </tr>
                        ))}</tbody>
                    </table>
                    <div className="mt-2 text-[10px] text-slate-500">
                        {report === undefined ? "تحميل الإحصاءات..." :
                         report?.status === "available" ? "طلبات سلة · صرف ميزان المسجل" :
                         report?.spend_reason === "snapchat_account_day_not_aligned"
                             ? "طلبات سلة محسوبة بتوقيت الحساب، لكن صرف سناب المحفوظ حسب يوم الرياض ولا يطابقه؛ تكلفة الطلب والعائد غير مكتملين."
                         : report?.status === "snapchat_account_day_not_aligned"
                             ? "صرف سناب محفوظ بيوم الرياض ولا يطابق يوم هذا الحساب. لا يجوز جمع الفترتين."
                         : report?.status === "account_timezone_unavailable"
                             ? "توقيت الحساب الإعلاني غير متاح."
                         : report?.status === "ambiguous_account"
                             ? "الحملة مرتبطة بأكثر من حساب، وتحتاج مطابقة معرّف الحساب."
                         : report?.status === "campaign_spend_not_available"
                             ? "صرف هذه الحملة غير متوفر في بيانات اليوم المحفوظة."
                         : report?.status === "spend_source_truncated"
                             ? "مصدر الصرف تجاوز الحد الآمن للقراءة."
                         : "بيانات غير مكتملة: تحقق من ربط الحملة والحساب والصرف"}
                    </div>
                    {report?.updated_at && <div className="mt-1 text-[10px] text-slate-400">آخر تحديث للصرف: {String(report.updated_at).slice(0, 19)}</div>}
                </div>, document.body
            )}
        </>
    );
}
