import { useEffect, useState } from "react";
import { BarChart3 } from "lucide-react";
import api from "../lib/api";
import DashboardPanel from "./DashboardPanel";

export default function MezanLiveSourcesCard() {
    const [data, setData] = useState(null);
    const [failed, setFailed] = useState(false);
    useEffect(() => {
        let stopped = false;
        let busy = false;
        let controller;
        async function refresh() {
            if (stopped || busy || document.visibilityState === "hidden") return;
            busy = true;
            controller = new AbortController();
            try {
                const response = await api.get("/first-party-attribution/v1/live-sources", {
                    signal: controller.signal, timeout: 8000,
                });
                if (!Array.isArray(response.data?.sources) || !Number.isFinite(response.data?.total_sessions)
                    || !response.data?.generated_at) throw new Error("Invalid live sources response");
                if (!stopped) { setData(response.data); setFailed(false); }
            } catch (_) {
                if (!stopped) setFailed(true);
            } finally { busy = false; }
        }
        refresh();
        const timer = setInterval(refresh, 60000);
        document.addEventListener("visibilitychange", refresh);
        return () => {
            stopped = true;
            clearInterval(timer);
            controller?.abort();
            document.removeEventListener("visibilitychange", refresh);
        };
    }, []);
    return <DashboardPanel title="مصادر الزيارات — تتبع ميزان"
        data-testid="mezan-live-sources" className="overflow-hidden rounded-2xl border border-teal-200 bg-white shadow-sm">
        <div className="flex min-h-14 items-center gap-2 bg-teal-700 px-4 py-3 text-white">
            <BarChart3 className="h-5 w-5 shrink-0" />
            <h2 className="text-sm font-black">مصادر الزيارات — تتبع ميزان</h2>
        </div>
        <div className="space-y-3 p-4 text-sm" dir="rtl">
            <p className="text-xs text-slate-500">جلسات رُصد لها نشاط خلال آخر 30 دقيقة · مستقلة عن فلتر التاريخ</p>
            {failed && <p role="alert" className="rounded bg-amber-50 p-2 text-amber-800">
                تعذر تحديث مصادر الزيارات. {data ? "المعروض آخر قراءة ناجحة وليس بيانات مباشرة." : "البيانات غير متاحة؛ لا تعني صفر زيارات."}
            </p>}
            {!data && !failed && <p role="status">جاري قراءة أحداث ميزان…</p>}
            {data && <>
                <p className="font-bold">إجمالي الجلسات: {data.total_sessions}</p>
                {data.total_sessions === 0 && <p role="status">لم تُرصد أحداث خلال النافذة؛ هذا لا يثبت عدم وجود زوار.</p>}
                <table className="w-full text-right text-xs">
                    <caption className="sr-only">مصادر جلسات ميزان خلال آخر 30 دقيقة</caption>
                    <thead><tr className="border-b text-slate-500"><th className="py-2">المصدر</th><th>الجلسات</th><th>النسبة</th></tr></thead>
                    <tbody>{data.sources.map(row => <tr key={row.key} className="border-b border-slate-100">
                        <th className="py-2 font-medium">{row.label}
                            {row.paid_evidence_sessions > 0 && <span className="block text-[10px] text-teal-700">منها {row.paid_evidence_sessions} بإشارة إعلان في الرابط</span>}
                        </th><td>{row.sessions}</td><td>{row.percent}%</td>
                    </tr>)}</tbody>
                </table>
                <p>المصدر المصنّف: {data.known_source_percent == null ? "—" : `${data.known_source_percent}%`}</p>
                <p className="text-[11px] text-slate-500">آخر قراءة: {new Date(data.generated_at).toLocaleTimeString("ar-SA", { timeZone: "Asia/Riyadh" })} · الرياض</p>
                <p className="text-[11px] text-slate-500">آخر حدث ضمن النافذة: {data.last_event_at ? new Date(data.last_event_at).toLocaleTimeString("ar-SA", { timeZone: "Asia/Riyadh" }) : "لا يوجد"}</p>
            </>}
            <p className="text-[11px] leading-5 text-slate-500">المصدر من أول رابط أو إحالة مرصودة للجلسة داخل النافذة. التنقل بين الصفحات لا يكرر الجلسة. المصدر غير المتاح يبقى غير معروف؛ وغياب الإحالة لا يثبت دخولًا مباشرًا. هذه الجلسات ليست مستخدمي GA4 أو طلبات شراء، ونسبة التصنيف ليست نسبة دقة مثبتة.</p>
        </div>
    </DashboardPanel>;
}
