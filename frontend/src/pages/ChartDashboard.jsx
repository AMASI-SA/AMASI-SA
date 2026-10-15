import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowRight,
  BarChart3,
  CalendarDays,
  RefreshCw,
} from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import api from "../lib/api";
import { getDashboardAdsSpend } from "../services/dashboardAdsSpend";

const PROVIDERS = [
  { value: "all", label: "جميع المنصات" },
  { value: "snapchat", label: "سناب شات" },
  { value: "tiktok", label: "تيك توك" },
  { value: "meta", label: "Meta" },
  { value: "google", label: "Google Ads" },
];

const SERIES = [
  { key: "sales", label: "المبيعات", color: "#059669", axis: "money" },
  { key: "spend", label: "صرف الإعلانات", color: "#9f1239", axis: "money" },
  { key: "roas", label: "العائد ROAS", color: "#d4a017", axis: "ratio" },
  { key: "costPerOrder", label: "تكلفة الطلب", color: "#7c3aed", axis: "money" },
  { key: "orders", label: "عدد الطلبات", color: "#2563eb", axis: "ratio" },
];

function riyadhToday() {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Riyadh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function shiftDay(iso, amount) {
  const value = new Date(`${iso}T12:00:00Z`);
  value.setUTCDate(value.getUTCDate() + amount);
  return value.toISOString().slice(0, 10);
}

function monthStart(iso) {
  return `${iso.slice(0, 7)}-01`;
}

function rangeDays(from, to) {
  return Math.round(
    (new Date(`${to}T12:00:00Z`) - new Date(`${from}T12:00:00Z`)) / 86400000,
  ) + 1;
}

function riyadhHour() {
  return Number(new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Riyadh",
    hour: "2-digit",
    hourCycle: "h23",
  }).format(new Date()));
}

const VALID_PRESETS = new Set([
  "today", "two_days", "seven_days", "thirty_days", "this_month", "custom",
]);

function inferPreset(from, to, requested) {
  if (requested && VALID_PRESETS.has(requested)) return requested;
  const today = riyadhToday();
  if (from === today && to === today) return "today";
  if (from === shiftDay(today, -1) && to === today) return "two_days";
  if (from === shiftDay(today, -6) && to === today) return "seven_days";
  if (from === shiftDay(today, -29) && to === today) return "thirty_days";
  if (from === monthStart(today) && to === today) return "this_month";
  return "custom";
}

function datesInTwoDayChunks(from, to) {
  const chunks = [];
  let cursor = from;
  while (cursor <= to) {
    const chunkTo = shiftDay(cursor, 1) <= to ? shiftDay(cursor, 1) : to;
    chunks.push({ from: cursor, to: chunkTo });
    cursor = shiftDay(chunkTo, 1);
  }
  return chunks;
}

function mergeAdsPayloads(payloads, from, to) {
  const providers = ["snapchat", "tiktok", "meta", "google"];
  const providerTotals = Object.fromEntries(providers.map((provider) => [provider, 0]));
  const dailyMap = new Map();
  for (const payload of payloads) {
    for (const provider of providers) {
      providerTotals[provider] += Number(payload?.provider_totals_sar?.[provider] || 0);
    }
    for (const row of payload?.daily_spend || []) {
      const target = dailyMap.get(row.date) || { date: row.date };
      for (const provider of providers) {
        target[provider] = Number(target[provider] || 0) + Number(row?.[provider] || 0);
      }
      dailyMap.set(row.date, target);
    }
  }
  const latest = payloads[payloads.length - 1] || {};
  return {
    ...latest,
    date_from: from,
    date_to: to,
    chart_granularity: "day",
    hourly_spend: [],
    daily_spend: Array.from(dailyMap.values()).sort((left, right) => left.date.localeCompare(right.date)),
    provider_totals_sar: providerTotals,
    total_sar: Object.values(providerTotals).reduce((total, value) => total + value, 0),
  };
}

async function loadAdsRange(from, to) {
  if (rangeDays(from, to) <= 2) {
    return getDashboardAdsSpend({ dateFrom: from, dateTo: to });
  }
  const payloads = [];
  for (const chunk of datesInTwoDayChunks(from, to)) {
    payloads.push(await getDashboardAdsSpend({ dateFrom: chunk.from, dateTo: chunk.to }));
  }
  return mergeAdsPayloads(payloads, from, to);
}

function trimOrdersToHour(payload, maxHour) {
  if (payload?.granularity !== "hour") return payload;
  const series = (payload.series || []).filter((row) => Number(row.hour_index) <= maxHour);
  return {
    ...payload,
    series,
    summary: {
      ...(payload.summary || {}),
      sales: series.reduce((total, row) => total + Number(row.sales || 0), 0),
      orders: series.reduce((total, row) => total + Number(row.orders || 0), 0),
    },
  };
}

function trimAdsToHour(payload, maxHour) {
  const hourly = (payload?.hourly_spend || []).filter((row) => Number(row.hour_index) <= maxHour);
  if (!hourly.length) return payload;
  const providers = ["snapchat", "tiktok", "meta", "google"];
  const providerTotals = Object.fromEntries(providers.map((provider) => [
    provider,
    hourly.reduce((total, row) => total + Number(row?.[provider] || 0), 0),
  ]));
  return {
    ...payload,
    hourly_spend: hourly,
    provider_totals_sar: providerTotals,
    total_sar: Object.values(providerTotals).reduce((total, value) => total + value, 0),
  };
}
function previousRange(from, to, preset) {
  if (preset === "this_month") {
    const current = new Date(`${from}T12:00:00Z`);
    const previousMonth = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() - 1, 1, 12));
    const previousFrom = previousMonth.toISOString().slice(0, 10);
    const selectedDay = Number(to.slice(8, 10));
    const lastDay = new Date(Date.UTC(previousMonth.getUTCFullYear(), previousMonth.getUTCMonth() + 1, 0)).getUTCDate();
    return { from: previousFrom, to: `${previousFrom.slice(0, 8)}${String(Math.min(selectedDay, lastDay)).padStart(2, "0")}` };
  }
  const days = rangeDays(from, to);
  return { from: shiftDay(from, -days), to: shiftDay(from, -1) };
}

function presetRange(preset) {
  const today = riyadhToday();
  if (preset === "today") return { from: today, to: today };
  if (preset === "two_days") return { from: shiftDay(today, -1), to: today };
  if (preset === "seven_days") return { from: shiftDay(today, -6), to: today };
  if (preset === "thirty_days") return { from: shiftDay(today, -29), to: today };
  return { from: monthStart(today), to: today };
}

function number(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function percentChange(current, previous) {
  if (!Number.isFinite(current) || !Number.isFinite(previous)) return null;
  if (previous === 0) return current === 0 ? 0 : null;
  return ((current - previous) / Math.abs(previous)) * 100;
}

function adValue(point, platform) {
  if (!point) return 0;
  if (platform !== "all") return Number(point[platform] || 0);
  return ["snapchat", "tiktok", "meta", "google"].reduce(
    (total, key) => total + Number(point[key] || 0),
    0,
  );
}

function adTotal(payload, platform) {
  if (platform === "all") return Number(payload?.total_sar || 0);
  return Number(payload?.provider_totals_sar?.[platform] || 0);
}

function mergeSeries(orderPayload, adPayload, platform, dashboard) {
  const hourly = orderPayload?.granularity === "hour";
  const ads = hourly ? adPayload?.hourly_spend : adPayload?.daily_spend;
  const adMap = new Map(
    (ads || []).map((row) => [hourly ? Number(row.hour_index) : row.date, adValue(row, platform)]),
  );
  const totals = dashboard?.totals || {};
  const totalSales = Number(orderPayload?.summary?.sales || 0);
  const productCost = Number(totals.total_product_cost || 0);
  const operatingCost = Number(
    totals.total_operating_expenses || totals.total_operating_cost || totals.total_expenses || 0,
  );
  const nonAdCostRate = totalSales > 0 ? Math.min(1, (productCost + operatingCost) / totalSales) : 0;
  return (orderPayload?.series || []).map((row) => {
    const spend = Number(adMap.get(hourly ? Number(row.hour_index) : row.date) || 0);
    const sales = Number(row.sales || 0);
    const orders = Number(row.orders || 0);
    return {
      ...row,
      sales,
      spend,
      orders,
      roas: spend > 0 && orders > 0 ? sales / spend : null,
      costPerOrder: orders > 0 ? spend / orders : null,
      netProfit: sales * (1 - nonAdCostRate) - spend,
    };
  });
}

function summary(orderPayload, adPayload, platform) {
  const sales = Number(orderPayload?.summary?.sales || 0);
  const orders = Number(orderPayload?.summary?.orders || 0);
  const spend = adTotal(adPayload, platform);
  return {
    sales,
    spend,
    orders,
    roas: spend > 0 && orders > 0 ? sales / spend : null,
    costPerOrder: orders > 0 ? spend / orders : null,
    spendShare: sales > 0 ? (spend / sales) * 100 : null,
  };
}

function Delta({ current, previous, inverse = false }) {
  const delta = percentChange(current, previous);
  if (delta === null) return <span className="text-xs text-slate-400">لا توجد مقارنة</span>;
  const improved = inverse ? delta <= 0 : delta >= 0;
  return (
    <span className={`rounded-full px-2 py-1 text-xs font-black ${improved ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>
      {delta >= 0 ? "↑" : "↓"} {number(Math.abs(delta), 1)}%
    </span>
  );
}

function MetricCard({ label, value, previous, color, suffix = "ر.س", inverse = false, extra }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="text-sm font-bold text-slate-500">{label}</span>
        <span className="h-3 w-3 rounded-full" style={{ backgroundColor: color }} />
      </div>
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <b className="text-2xl font-black text-slate-900">{number(value, label === "عدد الطلبات" ? 0 : 2)}</b>
          <span className="mr-1 text-xs font-bold text-slate-400">{suffix}</span>
        </div>
        <Delta current={value} previous={previous} inverse={inverse} />
      </div>
      {extra ? <div className="mt-2 text-xs font-bold text-slate-500">{extra}</div> : null}
    </section>
  );
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload || {};
  return (
    <div dir="rtl" className="min-w-56 rounded-xl border border-slate-200 bg-white p-3 text-xs shadow-xl">
      <b className="mb-2 block text-sm">{label}</b>
      <div className="space-y-1.5">
        <div className="flex justify-between gap-5"><span>المبيعات</span><b>{number(row.sales)} ر.س</b></div>
        <div className="flex justify-between gap-5"><span>صرف الإعلانات</span><b>{number(row.spend)} ر.س</b></div>
        <div className="flex justify-between gap-5"><span>عدد الطلبات</span><b>{number(row.orders, 0)}</b></div>
        <div className="flex justify-between gap-5"><span>تكلفة الطلب</span><b>{row.orders ? `${number(row.costPerOrder)} ر.س` : "—"}</b></div>
        <div className="flex justify-between gap-5"><span>العائد ROAS</span><b>{row.orders && row.roas !== null ? `${number(row.roas)}×` : "—"}</b></div>
        <div className="flex justify-between gap-5"><span>صافي الربح التقديري</span><b>{number(row.netProfit)} ر.س</b></div>
      </div>
    </div>
  );
}

export default function ChartDashboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialFrom = searchParams.get("from") || riyadhToday();
  const initialTo = searchParams.get("to") || initialFrom;
  const initialPreset = inferPreset(initialFrom, initialTo, searchParams.get("preset"));
  const [preset, setPreset] = useState(initialPreset);
  const [range, setRange] = useState({ from: initialFrom, to: initialTo });
  const [platform, setPlatform] = useState(searchParams.get("platform") || "all");
  const [current, setCurrent] = useState(null);
  const [previous, setPrevious] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const comparison = useMemo(
    () => previousRange(range.from, range.to, preset),
    [range.from, range.to, preset],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const currentQuery = new URLSearchParams({ from_date: range.from, to_date: range.to });
      const previousQuery = new URLSearchParams({ from_date: comparison.from, to_date: comparison.to });
      const [ordersNow, ordersBefore, dashboardNow, dashboardBefore, adsNow, adsBefore] = await Promise.all([
        api.get(`/dashboard-v2/chart-orders?${currentQuery}`),
        api.get(`/dashboard-v2/chart-orders?${previousQuery}`),
        api.get(`/dashboard-v2?${currentQuery}`),
        api.get(`/dashboard-v2?${previousQuery}`),
        loadAdsRange(range.from, range.to),
        loadAdsRange(comparison.from, comparison.to),
      ]);
      setCurrent({ orders: ordersNow.data, dashboard: dashboardNow.data, ads: adsNow });
      setPrevious({ orders: ordersBefore.data, dashboard: dashboardBefore.data, ads: adsBefore });
      setSearchParams({ from: range.from, to: range.to, platform, preset }, { replace: true });
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || "تعذر تحميل بيانات الرسم البياني");
    } finally {
      setLoading(false);
    }
  }, [comparison.from, comparison.to, platform, preset, range.from, range.to, setSearchParams]);

  useEffect(() => {
    load();
  }, [load]);

  const currentHour = riyadhHour();
  const sameHourComparison = preset === "today" && range.from === riyadhToday();
  const previousForComparison = useMemo(() => ({
    orders: sameHourComparison ? trimOrdersToHour(previous?.orders, currentHour) : previous?.orders,
    ads: sameHourComparison ? trimAdsToHour(previous?.ads, currentHour) : previous?.ads,
  }), [currentHour, previous, sameHourComparison]);
  const currentSummary = useMemo(
    () => summary(current?.orders, current?.ads, platform),
    [current, platform],
  );
  const previousSummary = useMemo(
    () => summary(previousForComparison.orders, previousForComparison.ads, platform),
    [platform, previousForComparison],
  );
  const chartData = useMemo(
    () => mergeSeries(current?.orders, current?.ads, platform, current?.dashboard),
    [current, platform],
  );
  const hourly = current?.orders?.granularity === "hour";
  const todaySelected = hourly && range.from === riyadhToday();
  const comparisonLabel = sameHourComparison
    ? `مقارنة بـ ${comparison.from} حتى الساعة ${String(currentHour).padStart(2, "0")}:00`
    : `مقارنة بـ ${comparison.from} – ${comparison.to}`;
  const applyPreset = (value) => {
    setPreset(value);
    setRange(presetRange(value));
  };

  return (
    <div dir="rtl" className="space-y-5" data-testid="chart-dashboard-page">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-bold text-slate-400">لوحة التحكم المتقدمة</p>
          <h1 className="flex items-center gap-2 text-2xl font-black sm:text-3xl">
            <BarChart3 className="h-7 w-7 text-blue-700" />
            لوحة الرسم البياني
          </h1>
          <p className="mt-1 text-sm text-slate-500">{comparisonLabel} — توقيت السعودية</p>
        </div>
        <Link to="/dashboard-advanced" className="inline-flex items-center gap-2 rounded-xl border bg-white px-4 py-2 text-sm font-black">
          <ArrowRight className="h-4 w-4" />
          العودة إلى لوحة التحكم
        </Link>
      </header>

      <section className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          {[
            ["today", "اليوم"],
            ["two_days", "يومان"],
            ["seven_days", "7 أيام"],
            ["thirty_days", "30 يومًا"],
            ["this_month", "هذا الشهر"],
          ].map(([value, label]) => (
            <button key={value} onClick={() => applyPreset(value)} className={`rounded-xl px-3 py-2 text-xs font-black ${preset === value ? "bg-blue-700 text-white" : "bg-slate-100 text-slate-600"}`}>
              {label}
            </button>
          ))}
          <div className="mr-auto flex flex-wrap items-center gap-2">
            <CalendarDays className="h-4 w-4 text-slate-400" />
            <input aria-label="من تاريخ" type="date" value={range.from} max={range.to} onChange={(event) => { setPreset("custom"); setRange((value) => ({ ...value, from: event.target.value })); }} className="rounded-xl border px-3 py-2 text-xs font-bold" />
            <input aria-label="إلى تاريخ" type="date" value={range.to} min={range.from} onChange={(event) => { setPreset("custom"); setRange((value) => ({ ...value, to: event.target.value })); }} className="rounded-xl border px-3 py-2 text-xs font-bold" />
            <select aria-label="منصة الإعلانات" value={platform} onChange={(event) => setPlatform(event.target.value)} className="rounded-xl border px-3 py-2 text-xs font-bold">
              {PROVIDERS.map((provider) => <option key={provider.value} value={provider.value}>{provider.label}</option>)}
            </select>
            <button onClick={load} aria-label="تحديث الرسم" className="rounded-xl border bg-white p-2 text-blue-700">
              <RefreshCw className={`h-5 w-5 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>
      </section>

      {error ? <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-800">{String(error)}</div> : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <MetricCard label="المبيعات" value={currentSummary.sales} previous={previousSummary.sales} color="#059669" />
        <MetricCard label="صرف الإعلانات" value={currentSummary.spend} previous={previousSummary.spend} color="#9f1239" inverse />
        <MetricCard label="العائد الإعلاني" value={currentSummary.roas} previous={previousSummary.roas} color="#d4a017" suffix="×" extra={currentSummary.spendShare === null ? null : `الصرف يمثل ${number(currentSummary.spendShare, 1)}% من المبيعات`} />
        <MetricCard label="تكلفة الطلب" value={currentSummary.costPerOrder} previous={previousSummary.costPerOrder} color="#7c3aed" inverse />
        <MetricCard label="عدد الطلبات" value={currentSummary.orders} previous={previousSummary.orders} color="#2563eb" suffix="طلب" />
      </div>

      <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-black">الأداء اليومي للمبيعات والإعلانات</h2>
            <p className="text-xs text-slate-500">{hourly ? "عرض 24 ساعة" : "عرض يومي"} — مرّر على أي نقطة لعرض التفاصيل</p>
          </div>
          <div className="flex flex-wrap gap-3 text-xs font-bold">
            {SERIES.map((item) => <span key={item.key} className="flex items-center gap-1.5"><i className="h-2.5 w-5 rounded-full" style={{ backgroundColor: item.color }} />{item.label}</span>)}
          </div>
        </div>
        <div className="h-[430px] w-full" dir="ltr">
          {loading ? (
            <div className="flex h-full items-center justify-center text-sm font-bold text-slate-400">جارٍ تحميل البيانات…</div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 24, right: 12, left: 12, bottom: 12 }}>
                <CartesianGrid strokeDasharray="4 4" stroke="#e2e8f0" />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} interval={hourly ? 1 : "preserveStartEnd"} />
                <YAxis yAxisId="money" orientation="left" tick={{ fontSize: 11 }} width={72} />
                <YAxis yAxisId="ratio" orientation="right" tick={{ fontSize: 11 }} width={48} />
                <Tooltip content={<ChartTooltip />} />
                {todaySelected ? <ReferenceLine x={`${String(currentHour).padStart(2, "0")}:00`} stroke="#f59e0b" strokeDasharray="5 5" label={{ value: "قيد التحديث", fill: "#b45309", fontSize: 11 }} /> : null}
                {SERIES.map((item) => (
                  <Line key={item.key} type="monotone" dataKey={item.key} yAxisId={item.axis} name={item.label} stroke={item.color} strokeWidth={2.5} dot={{ r: 2 }} activeDot={{ r: 5 }} connectNulls />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
        {hourly && current?.orders?.data_quality?.hourly_precision_complete === false ? (
          <p className="mt-2 text-xs font-bold text-amber-700">بعض الطلبات القديمة لا تحتوي ساعة دقيقة؛ جُمعت عند بداية اليوم مع بقاء إجمالي اليوم صحيحًا.</p>
        ) : null}
      </section>
    </div>
  );
}
