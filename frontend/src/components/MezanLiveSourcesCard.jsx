import { useEffect, useState } from "react";
import { BarChart3 } from "lucide-react";
import api from "../lib/api";
import DashboardPanel from "./DashboardPanel";

export default function MezanLiveSourcesCard({ onData }) {
    const [data, setData] = useState(null);
    const [page, setPage] = useState(0);
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
                if (!stopped) { setData(response.data); setFailed(false); onData?.(response.data.realtime || null); }
            } catch (_) {
                if (!stopped) { setFailed(true); onData?.(null); }
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
    }, [onData]);
    const pageCount = Math.max(1, Math.ceil((data?.sources.length || 0) / 5));
    const currentPage = Math.min(page, pageCount - 1);
    return <DashboardPanel title="مصادر الزيارات — تتبع ميزان"
        data-testid="mezan-live-sources" className="mezan-live-sources-compact overflow-hidden rounded-2xl border border-teal-200 bg-white shadow-sm">
        <div className="flex min-h-14 items-center gap-2 bg-teal-700 px-4 py-3 text-white">
            <BarChart3 className="h-5 w-5 shrink-0" />
            <h2 className="text-sm font-black">مصادر الزيارات — تتبع ميزان</h2>
        </div>
        <div className="space-y-2 px-3 py-2 text-sm" dir="rtl">
            <p className="text-[11px] text-slate-500" title="مستقلة عن فلتر التاريخ">آخر 30 دقيقة</p>
            {failed && <p role="alert" className="rounded bg-amber-50 p-2 text-amber-800">
                تعذر تحديث مصادر الزيارات. {data ? "المعروض آخر قراءة ناجحة وليس بيانات مباشرة." : "البيانات غير متاحة؛ لا تعني صفر زيارات."}
            </p>}
            {!data && !failed && <p role="status">جاري قراءة أحداث ميزان…</p>}
            {data && <>
                <p className="font-bold">إجمالي الجلسات: {data.total_sessions}</p>
                {data.total_sessions === 0 && <p role="status">لم تُرصد أحداث خلال النافذة؛ هذا لا يثبت عدم وجود زوار.</p>}
                <table className="w-full text-right text-xs">
                    <caption className="sr-only">مصادر جلسات ميزان خلال آخر 30 دقيقة</caption>
                    <thead><tr className="border-b text-slate-500"><th className="py-1">المصدر</th><th>الجلسات</th><th>النسبة</th></tr></thead>
                    <tbody>{data.sources.slice(currentPage * 5, currentPage * 5 + 5).map(row => <tr key={row.key} className="border-b border-slate-100">
                        <th className="py-1 font-medium">{row.label}
                            {row.paid_evidence_sessions > 0 && <span className="sr-only">منها {row.paid_evidence_sessions} بإشارة إعلان في الرابط</span>}
                        </th><td>{row.sessions}</td><td>{row.percent}%</td>
                    </tr>)}</tbody>
                </table>
                {pageCount > 1 && <nav aria-label="صفحات مصادر الزيارات" className="flex items-center justify-between gap-2 text-xs">
                    <button type="button" className="rounded border px-3 py-1 disabled:opacity-40" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>السابق</button>
                    <span aria-live="polite">{currentPage + 1} / {pageCount}</span>
                    <button type="button" className="rounded border px-3 py-1 disabled:opacity-40" disabled={currentPage === pageCount - 1} onClick={() => setPage(currentPage + 1)}>التالي</button>
                </nav>}
            </>}

        </div>
    </DashboardPanel>;
}
