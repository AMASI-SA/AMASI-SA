import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowsClockwise,
  CheckCircle,
  Clock,
  Info,
  Plug,
  ShieldCheck,
  Warning,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api from "../lib/api";

function formatDate(value) {
  if (!value) return "—";
  try {
    return new Intl.DateTimeFormat("ar-SA", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: "Asia/Riyadh",
    }).format(new Date(value));
  } catch {
    return String(value);
  }
}

function StatusBadge({ observed }) {
  return observed ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-1 text-[11px] font-extrabold text-emerald-800">
      <CheckCircle size={13} weight="fill" /> يعمل — وصل من سلة
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-extrabold text-slate-700">
      <Clock size={13} weight="bold" /> لم يصل بعد
    </span>
  );
}

function SummaryCard({ label, value, tone = "slate" }) {
  const tones = {
    emerald: "border-emerald-200 bg-emerald-50 text-emerald-900",
    amber: "border-amber-200 bg-amber-50 text-amber-900",
    indigo: "border-indigo-200 bg-indigo-50 text-indigo-900",
    slate: "border-slate-200 bg-white text-slate-900",
  };
  return (
    <div className={`rounded-xl border p-4 ${tones[tone] || tones.slate}`}>
      <div className="text-xs font-bold opacity-70">{label}</div>
      <div className="mt-1 text-2xl font-extrabold font-mono">{value}</div>
    </div>
  );
}

function EventTable({ title, events }) {
  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-5 py-3">
        <h2 className="font-extrabold text-slate-900">{title}</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[780px] text-sm">
          <thead className="bg-slate-50 text-xs text-slate-600">
            <tr>
              <th className="px-4 py-3 text-right">الحدث</th>
              <th className="px-4 py-3 text-right">الحالة</th>
              <th className="px-4 py-3 text-right">آخر وصول</th>
              <th className="px-4 py-3 text-right">عدد الوصول</th>
              <th className="px-4 py-3 text-right">آخر طلب</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {events.map((event) => (
              <tr key={event.event} className="hover:bg-slate-50/70">
                <td className="px-4 py-3">
                  <div className="font-extrabold text-slate-900">{event.label}</div>
                  <code className="mt-1 block text-[11px] text-slate-500">{event.event}</code>
                </td>
                <td className="px-4 py-3"><StatusBadge observed={event.observed} /></td>
                <td className="px-4 py-3 text-xs text-slate-700">{formatDate(event.last_received_at)}</td>
                <td className="px-4 py-3 font-mono font-bold text-slate-800">{event.delivery_count || 0}</td>
                <td className="px-4 py-3 font-mono font-bold text-slate-800">{event.last_order_number || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function SallaWebhookMonitor() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async ({ manual = false } = {}) => {
    manual ? setRefreshing(true) : setLoading(true);
    setError("");
    try {
      const response = await api.get("/salla/webhook-monitor");
      setData(response.data);
      if (manual) toast.success("تم تحديث حالة أحداث Webhook");
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const message = typeof detail === "string"
        ? detail
        : (detail?.message || "تعذر تحميل مراقبة Webhook");
      setError(message);
      if (manual) toast.error(message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const orderEvents = useMemo(
    () => (data?.events || []).filter((event) => event.group === "orders"),
    [data]
  );
  const shippingEvents = useMemo(
    () => (data?.events || []).filter((event) => event.group === "shipping"),
    [data]
  );
  const pending = Math.max(
    0,
    Number(data?.total_monitored_events || 0) - Number(data?.received_events || 0)
  );

  return (
    <div className="space-y-5 p-4 sm:p-6" dir="rtl" data-testid="salla-webhook-monitor-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-indigo-100 text-indigo-700">
            <Plug size={27} weight="bold" />
          </div>
          <div>
            <h1 className="text-2xl font-extrabold text-slate-900">مراقبة Webhook — سلة</h1>
            <p className="mt-1 text-sm text-slate-500">
              حالة الأحداث التي وصلت فعليًا من سلة إلى ميزان. لا يوجد تحديث تلقائي أو مراقبة DOM.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => load({ manual: true })}
          disabled={loading || refreshing}
          className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-indigo-700 disabled:opacity-50"
          data-testid="salla-webhook-refresh-btn"
        >
          <ArrowsClockwise size={16} weight="bold" className={refreshing ? "animate-spin" : ""} />
          {refreshing ? "جاري التحديث…" : "تحديث الحالة"}
        </button>
      </div>

      <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <ShieldCheck size={19} weight="bold" className="mt-0.5 shrink-0" />
        <div>
          <div className="font-extrabold">Salla API الاحتياطي ما زال مفعّلًا</div>
          <div className="mt-1 text-xs leading-relaxed">
            لن نحذف الاعتماد على API حتى نتحقق من وصول الأحداث الأساسية وحفظ بياناتها بالشكل الصحيح.
          </div>
        </div>
      </div>

      {loading && (
        <div className="rounded-xl border border-slate-200 bg-white p-10 text-center text-sm font-bold text-slate-500">
          جاري قراءة سجل أحداث سلة…
        </div>
      )}

      {!loading && error && (
        <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700">
          <Warning size={18} weight="fill" className="shrink-0" />
          {error}
        </div>
      )}

      {!loading && !error && data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <SummaryCard label="الأحداث المطلوبة" value={data.total_monitored_events || 0} tone="indigo" />
            <SummaryCard label="وصلت فعليًا" value={data.received_events || 0} tone="emerald" />
            <SummaryCard label="لم تُختبر أو لم تصل" value={pending} tone="amber" />
            <SummaryCard label="حالة الربط" value={data.integration_status || "—"} />
          </div>

          <div className="flex items-start gap-2 rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900">
            <Info size={17} weight="bold" className="shrink-0" />
            عبارة «لم يصل بعد» لا تعني أن الحدث معطّل في سلة؛ تعني فقط أن ميزان لم يسجل وصوله حتى لحظة التحديث.
          </div>

          <EventTable title="أحداث الطلبات" events={orderEvents} />
          <EventTable title="أحداث الشحن" events={shippingEvents} />

          <div className="text-left text-[11px] text-slate-400">
            آخر قراءة: {formatDate(data.generated_at)}
          </div>
        </>
      )}
    </div>
  );
}
