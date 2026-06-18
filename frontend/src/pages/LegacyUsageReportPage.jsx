// Iter-246 — Legacy systems usage report.
// Read-only audit of the four legacy screens that the merchant wants
// to deprecate.  Empty data ↔ safe to retire.

import { useEffect, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const errMsg = (e, fb) =>
  e?.response?.data?.detail || e?.message || fb;

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toISOString().slice(0, 10) + " · " + d.toISOString().slice(11, 16);
  } catch {
    return iso;
  }
}

export default function LegacyUsageReportPage() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      const { data } = await api.get("/legacy-usage-report");
      setReport(data);
    } catch (e) {
      toast.error(errMsg(e, "فشل تحميل التقرير"));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  return (
    <div className="space-y-5 p-2" dir="rtl"
         data-testid="legacy-usage-report-page">
      <header className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">
            🕰️ تقرير استخدام الأنظمة القديمة
          </h1>
          <p className="text-sm text-gray-600 mt-1 leading-7">
            هذا تقرير قراءة فقط يفحص الشاشات القديمة التي تم استبدالها
            بنظام «حركة مالية موحَّدة (Iter-245)». يساعدك على تحديد
            متى يمكنك إيقاف أي شاشة بأمان.
            <br/>
            <span className="font-bold text-rose-700">
              لا تُحذف أي بيانات ولا تُعدَّل أي قيود.
            </span>
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="bg-gray-100 hover:bg-gray-200 px-4 py-2 rounded text-sm font-bold"
          data-testid="legacy-report-refresh"
        >
          {loading ? "جارٍ التحديث..." : "تحديث"}
        </button>
      </header>

      {loading && !report && (
        <p className="text-center text-gray-500 py-12">جارٍ التحميل...</p>
      )}

      {report && (
        <>
          <SummaryCards summary={report.summary}
                        generatedAt={report.generated_at} />

          <div className="space-y-4">
            {report.screens.map((s) => (
              <ScreenCard key={s.screen} screen={s} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function SummaryCards({ summary, generatedAt }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
      <Card label="إجمالي السجلات القديمة"
            value={summary.total_legacy_records}
            color="bg-slate-100 text-slate-900"
            testid="summary-total"/>
      <Card label="🟢 شاشات لا تزال نشطة"
            value={summary.active_screens.length}
            color="bg-emerald-50 text-emerald-800"
            testid="summary-active"
            subtitle={
              summary.active_screens.length
                ? "تستقبل بيانات في آخر 7 أيام"
                : "لا توجد بيانات جديدة في الأسبوع الماضي"
            } />
      <Card label="🔴 شاشات يمكن إيقافها"
            value={summary.dead_screens.length}
            color="bg-rose-50 text-rose-800"
            testid="summary-dead"
            subtitle={
              summary.dead_screens.length
                ? "بدون بيانات في آخر 7 أيام، لكن لديها سجلات تاريخية"
                : "كل الشاشات إما نشطة أو فارغة"
            } />
      <p className="md:col-span-3 text-[11px] text-gray-500 mt-1">
        ⏱️ تم توليد التقرير في: {fmtDate(generatedAt)}
      </p>
    </div>
  );
}

function Card({ label, value, subtitle, color, testid }) {
  return (
    <div className={"rounded-lg border p-4 " + color}
         data-testid={testid}>
      <p className="text-xs font-bold opacity-80">{label}</p>
      <p className="text-3xl font-extrabold mt-1 num">{value}</p>
      {subtitle && (
        <p className="text-[11px] mt-1 opacity-80">{subtitle}</p>
      )}
    </div>
  );
}

function ScreenCard({ screen: s }) {
  const isActive = s.is_active;
  const isEmpty = s.total_records === 0;
  const statusBadge = isEmpty
    ? { label: "⚪ فارغة — جاهزة للإيقاف", c: "bg-gray-200 text-gray-700" }
    : isActive
      ? { label: "🟢 نشطة — لا تُلغَ بعد", c: "bg-emerald-100 text-emerald-800" }
      : { label: "🔴 خامدة — يمكن إيقافها", c: "bg-rose-100 text-rose-800" };

  return (
    <div className="border rounded-lg p-4 bg-white shadow-sm"
         data-testid={"screen-card-" + s.screen}>
      <div className="flex items-start justify-between flex-wrap gap-2">
        <div>
          <h3 className="font-bold text-lg">{s.screen_label}</h3>
          <p className="text-xs text-gray-500 mt-1">
            المسار القديم: <code className="bg-gray-100 px-1">{s.ui_path}</code>
            {" "}— البديل: <code className="bg-emerald-50 px-1 text-emerald-700">{s.replaced_by}</code>
          </p>
        </div>
        <span className={"text-xs font-bold px-3 py-1 rounded " + statusBadge.c}
              data-testid={"screen-status-" + s.screen}>
          {statusBadge.label}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4 text-sm">
        <Metric label="إجمالي السجلات" value={s.total_records}
                testid={"metric-total-" + s.screen} />
        <Metric label="آخر 30 يوماً" value={s.last_30d}
                testid={"metric-30d-" + s.screen} />
        <Metric label="آخر 7 أيام" value={s.last_7d}
                testid={"metric-7d-" + s.screen}
                highlight={s.last_7d > 0} />
        <Metric label="آخر نشاط" value={fmtDate(s.last_activity)}
                testid={"metric-last-" + s.screen}
                small />
      </div>

      {s.collections.length > 1 && (
        <details className="mt-3"
                 data-testid={"screen-collections-" + s.screen}>
          <summary className="text-xs text-blue-700 cursor-pointer">
            عرض المجموعات الفرعية ({s.collections.length})
          </summary>
          <table className="w-full mt-2 text-xs border">
            <thead className="bg-gray-50">
              <tr className="text-right">
                <th className="p-2">المجموعة</th>
                <th className="p-2">الإجمالي</th>
                <th className="p-2">آخر 30 يوم</th>
                <th className="p-2">آخر 7 أيام</th>
                <th className="p-2">آخر نشاط</th>
              </tr>
            </thead>
            <tbody>
              {s.collections.map((c) => (
                <tr key={c.collection} className="border-t">
                  <td className="p-2 font-mono">{c.collection}</td>
                  <td className="p-2 num">{c.total}</td>
                  <td className="p-2 num">{c.last_30d}</td>
                  <td className="p-2 num">{c.last_7d}</td>
                  <td className="p-2">{fmtDate(c.last_activity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

function Metric({ label, value, testid, highlight, small }) {
  return (
    <div data-testid={testid}>
      <p className="text-[10px] font-bold text-gray-500">{label}</p>
      <p className={
        (small ? "text-sm" : "text-2xl")
        + " font-extrabold num "
        + (highlight ? "text-rose-700" : "text-slate-800")
      }>
        {value}
      </p>
    </div>
  );
}
