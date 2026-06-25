import React, { useEffect, useState, useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Card, CardContent, CardHeader, CardTitle,
} from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Badge } from "../components/ui/badge";
import {
  Table, TableHeader, TableRow, TableHead,
  TableBody, TableCell,
} from "../components/ui/table";
import {
  Tabs, TabsList, TabsTrigger, TabsContent,
} from "../components/ui/tabs";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
  DialogFooter, DialogTrigger,
} from "../components/ui/dialog";
import { Textarea } from "../components/ui/textarea";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const FIELD_CLS =
  "bg-zinc-950 border-zinc-800 text-zinc-50 placeholder:text-zinc-500 " +
  "focus-visible:ring-zinc-700";

// ── Arabic dictionaries ────────────────────────────────────────────────
const PROVIDER_AR = {
  meta: "Meta",
  snapchat: "Snapchat",
  tiktok: "TikTok",
  google_ads: "Google Ads",
};

const REVIEW_STATUS_AR = {
  pending: "بانتظار المراجعة",
  approved: "معتمدة",
  rejected: "مرفوضة",
  reopened: "أُعيد فتحها",
  held_needs_fx: "محجوزة — يلزم سعر صرف",
  held_anomaly: "محجوزة — انحراف مرتفع",
  held_unauthorized: "محجوزة — توكن غير صالح",
  held_drift: "محجوزة — فرق ملحوظ",
};

const CONFIDENCE_AR = {
  provisional: "بيانات أولية",
  final: "بيانات نهائية",
};

const ANOMALY_AR = {
  drift_above_5pct: "فرق فوق 5%",
  drift_above_15pct: "فرق فوق 15%",
  late_reporting: "تأخر إبلاغ من المنصة",
  mismatch_vs_ads_manager: "اختلاف عن Ads Manager",
  missing_fx: "ينقص سعر صرف",
};

const DRIFT_CAUSE_AR = {
  sync_before_close: "مزامنة قبل إغلاق اليوم",
  late_reporting_window: "نافذة تحديث المنصة (24–72 ساعة)",
  ads_manager_value_differs: "قيمة Ads Manager تختلف عن المزامنة",
  post_close_provider_update: "تحديث من المنصة بعد إغلاق اليوم",
  missing_fx_rate: "سعر الصرف غير محدد",
  unclassified_drift: "سبب غير محدد — راجع السجل",
};

// match_status → user-friendly indicator (icon + label + colors)
const MATCH_STATUS_AR = {
  matched: {
    icon: "🟢",
    label: "متطابق",
    cls: "bg-emerald-500/20 text-emerald-100 border-emerald-500/40",
  },
  pending_platform: {
    icon: "🟡",
    label: "بانتظار اكتمال بيانات المنصة",
    cls: "bg-amber-500/20 text-amber-100 border-amber-500/40",
  },
  drift_review: {
    icon: "🟠",
    label: "يوجد فرق يحتاج مراجعة",
    cls: "bg-orange-500/25 text-orange-100 border-orange-500/40",
  },
  sync_failed: {
    icon: "🔴",
    label: "فشل في المزامنة",
    cls: "bg-red-500/25 text-red-100 border-red-500/40",
  },
  no_data: {
    icon: "⚪",
    label: "لا توجد بيانات",
    cls: "bg-zinc-700/30 text-zinc-200 border-zinc-600/40",
  },
};

// 7 days back inclusive
function defaultRange() {
  const today = new Date();
  const past = new Date(today);
  past.setDate(today.getDate() - 6);
  const fmt = (d) => d.toISOString().slice(0, 10);
  return { from: fmt(past), to: fmt(today) };
}

const fmtSAR = (n) =>
  Number(n || 0).toLocaleString("ar-SA", { maximumFractionDigits: 2 });

// signed SAR formatter — keeps the +/- sign for diff columns
const fmtDiff = (n) => {
  const v = Number(n || 0);
  const sign = v > 0 ? "+" : "";
  return sign + v.toLocaleString("ar-SA", { maximumFractionDigits: 2 });
};

const fmtDate = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("ar-SA", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
};

export default function AdsV2Report() {
  const [range, setRange] = useState(defaultRange());
  const [report, setReport] = useState({ day: null, account: null, provider: null });
  const [recon, setRecon] = useState(null);
  const [syncRunning, setSyncRunning] = useState(false);
  const [reconRunning, setReconRunning] = useState(false);
  const [loading, setLoading] = useState(false);

  const authHeaders = () => ({
    Authorization: `Bearer ${localStorage.getItem("token")}`,
  });

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const params = `date_from=${range.from}&date_to=${range.to}`;
      const [d, a, p, r] = await Promise.all([
        axios.get(`${API}/ads-v2/report?group_by=day&${params}`, { headers: authHeaders() }),
        axios.get(`${API}/ads-v2/report?group_by=account&${params}`, { headers: authHeaders() }),
        axios.get(`${API}/ads-v2/report?group_by=provider&${params}`, { headers: authHeaders() }),
        axios.get(`${API}/ads-v2/report/reconciliation?${params}`, { headers: authHeaders() }),
      ]);
      setReport({
        day: d.data?.data, account: a.data?.data, provider: p.data?.data,
      });
      setRecon(r.data?.data);
    } catch (e) {
      toast.error("فشل تحميل التقرير");
    } finally {
      setLoading(false);
    }
  }, [range]);

  const dateList = () => {
    const dates = [];
    const d1 = new Date(range.from);
    const d2 = new Date(range.to);
    for (let d = new Date(d1); d <= d2; d.setDate(d.getDate() + 1)) {
      dates.push(d.toISOString().slice(0, 10));
    }
    return dates;
  };

  const runSync = async () => {
    setSyncRunning(true);
    try {
      const r = await axios.post(
        `${API}/ads-v2/sync/run`, { dates: dateList() },
        { headers: authHeaders() },
      );
      const data = r.data?.data;
      toast.success(`اكتملت المزامنة: ${data?.ok_count} ناجح، ${data?.fail_count} فاشل`);
      await loadAll();
    } catch (e) {
      toast.error("فشلت المزامنة");
    } finally {
      setSyncRunning(false);
    }
  };

  const runAutoReconcile = async () => {
    setReconRunning(true);
    try {
      const r = await axios.post(
        `${API}/ads-v2/report/auto-reconcile`, { dates: dateList() },
        { headers: authHeaders() },
      );
      const data = r.data?.data;
      toast.success(
        `تمت المطابقة من المنصات: ` +
        `🟢 ${data?.matched_count || 0} متطابق · ` +
        `🟠 ${data?.drift_count || 0} يحتاج مراجعة · ` +
        `🟡 ${data?.pending_count || 0} بانتظار · ` +
        `🔴 ${data?.failed_count || 0} فشل`,
      );
      await loadAll();
    } catch (e) {
      toast.error("فشلت المطابقة من المنصات");
    } finally {
      setReconRunning(false);
    }
  };

  const saveManualValue = async ({ account_id, date, manual_value_native, note }) => {
    try {
      await axios.post(
        `${API}/ads-v2/report/manual-value`,
        { account_id, date, manual_value_native, note },
        { headers: authHeaders() },
      );
      toast.success("تم حفظ قيمة Ads Manager وإعادة حساب الفرق");
      await loadAll();
    } catch (e) {
      toast.error("فشل حفظ القيمة");
    }
  };

  useEffect(() => { loadAll(); }, [loadAll]);

  const totals = report.day?.totals || { spend_sar: 0, bank_fee_sar: 0, gross_sar: 0 };

  return (
    <div className="p-6 max-w-7xl mx-auto" dir="rtl"
        data-testid="ads-v2-report-page">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-extrabold text-zinc-50">
              تقرير الإعلانات
            </h1>
            <Badge className="bg-blue-500/20 text-blue-100 border-blue-500/40 font-semibold">
              الإصدار V2 · المرحلة 1
            </Badge>
          </div>
          <p className="text-sm text-zinc-300 mt-1 font-medium">
            جميع الأرقام مصدرها <code className="text-zinc-50 bg-zinc-800 px-1.5 py-0.5 rounded">ads_daily</code>{" "}
            فقط — مصدر بيانات موحّد للنظام بأكمله.
          </p>
        </div>
      </div>

      {/* Controls */}
      <Card className="bg-zinc-900 border-zinc-800 mb-6">
        <CardContent className="py-4">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
            <div>
              <Label className="text-zinc-200 text-xs font-semibold">من تاريخ</Label>
              <Input
                type="date" value={range.from}
                onChange={(e) => setRange({ ...range, from: e.target.value })}
                className={FIELD_CLS}
                data-testid="report-from"
              />
            </div>
            <div>
              <Label className="text-zinc-200 text-xs font-semibold">إلى تاريخ</Label>
              <Input
                type="date" value={range.to}
                onChange={(e) => setRange({ ...range, to: e.target.value })}
                className={FIELD_CLS}
                data-testid="report-to"
              />
            </div>
            <div className="flex items-end">
              <Button
                onClick={loadAll} disabled={loading}
                variant="outline"
                className="border-zinc-700 text-zinc-100 hover:bg-zinc-800 hover:text-zinc-50 font-semibold w-full"
                data-testid="report-refresh"
              >
                تحديث
              </Button>
            </div>
            <div className="flex items-end">
              <Button
                onClick={runSync} disabled={syncRunning}
                className="font-semibold w-full"
                data-testid="run-sync-btn"
              >
                {syncRunning ? "جاري المزامنة..." : "مزامنة الفترة الآن"}
              </Button>
            </div>
            <div className="flex items-end">
              <Button
                onClick={runAutoReconcile} disabled={reconRunning}
                variant="secondary"
                className="font-semibold w-full bg-blue-600 hover:bg-blue-500 text-white"
                data-testid="run-auto-reconcile-btn"
              >
                {reconRunning ? "جاري المطابقة..." : "إعادة المطابقة من المنصات"}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Totals */}
      <div className="grid grid-cols-1 md:grid-cols-5 gap-4 mb-6">
        {(() => {
          // iter-257 — Show USD spend total when present (mainly Snap).
          const byCcy = totals.spend_native_by_currency || {};
          const usd = Number(byCcy.USD || 0);
          const cards = [];
          if (usd > 0) {
            cards.push(
              <StatCard key="usd"
                label="إجمالي الصرف بالدولار"
                value={`${fmtSAR(usd)} USD`}
                testid="report-total-spend-usd"
              />
            );
          }
          cards.push(
            <StatCard key="sar" label="الصرف بالريال (قبل العمولة)"
              value={`${fmtSAR(totals.spend_sar)} SAR`}
              testid="report-total-spend-sar" />,
            <StatCard key="pct" label="نسبة العمولة البنكية"
              value={`${(Number(totals.bank_fee_pct) || 0).toFixed(2)} %`}
              testid="report-total-bank-fee-pct" />,
            <StatCard key="fee" label="قيمة العمولة البنكية"
              value={`${fmtSAR(totals.bank_fee_sar)} SAR`}
              testid="report-total-bank-fee" />,
            <StatCard key="gross" label="الإجمالي بعد العمولة"
              value={`${fmtSAR(totals.gross_sar)} SAR`} accent
              testid="report-total-gross" />,
          );
          return cards;
        })()}
      </div>

      <Tabs defaultValue="recon" className="w-full">
        <TabsList className="grid grid-cols-4 w-full bg-zinc-900 mb-6">
          <TabsTrigger
            value="recon"
            className="data-[state=active]:bg-zinc-800 data-[state=active]:text-zinc-50 text-zinc-300 font-semibold"
            data-testid="tab-recon"
          >
            المطابقة
          </TabsTrigger>
          <TabsTrigger
            value="day"
            className="data-[state=active]:bg-zinc-800 data-[state=active]:text-zinc-50 text-zinc-300 font-semibold"
            data-testid="tab-by-day"
          >
            حسب اليوم
          </TabsTrigger>
          <TabsTrigger
            value="account"
            className="data-[state=active]:bg-zinc-800 data-[state=active]:text-zinc-50 text-zinc-300 font-semibold"
            data-testid="tab-by-account"
          >
            حسب الحساب
          </TabsTrigger>
          <TabsTrigger
            value="provider"
            className="data-[state=active]:bg-zinc-800 data-[state=active]:text-zinc-50 text-zinc-300 font-semibold"
            data-testid="tab-by-provider"
          >
            حسب المنصة
          </TabsTrigger>
        </TabsList>

        <TabsContent value="recon">
          <ReconciliationView recon={recon} onSaveManual={saveManualValue} />
        </TabsContent>

        <TabsContent value="day">
          <ReportTable
            rows={report.day?.data || []}
            cols={["date", "spend_sar", "bank_fee_sar", "gross_sar", "accounts_count", "impressions", "clicks"]}
            headers={["التاريخ", "الصرف (SAR)", "العمولة (SAR)", "الإجمالي (SAR)", "عدد الحسابات", "الظهور", "النقرات"]}
          />
        </TabsContent>

        <TabsContent value="account">
          <ReportTable
            rows={report.account?.data || []}
            cols={["display_name", "provider", "currency_native", "spend_native", "spend_sar", "bank_fee_pct", "bank_fee_sar", "gross_sar", "days_count", "latest_date"]}
            headers={["الحساب", "المنصة", "العملة", "الصرف (USD/Native)", "الصرف (SAR)", "نسبة العمولة %", "العمولة (SAR)", "الإجمالي (SAR)", "عدد الأيام", "آخر تاريخ"]}
            providerCol="provider"
          />
        </TabsContent>

        <TabsContent value="provider">
          <ReportTable
            rows={report.provider?.data || []}
            cols={["provider", "accounts_count", "days_count", "spend_sar", "bank_fee_pct", "bank_fee_sar", "gross_sar"]}
            headers={["المنصة", "عدد الحسابات", "عدد الأيام", "الصرف (SAR)", "نسبة العمولة %", "العمولة (SAR)", "الإجمالي (SAR)"]}
            providerCol="provider"
          />
        </TabsContent>
      </Tabs>

      {/* SSOT footer */}
      <p className="text-xs text-zinc-400 mt-4 text-center font-medium">
        مصدر البيانات: <code className="text-zinc-200 bg-zinc-800 px-1.5 py-0.5 rounded">{report.day?.meta?.ssot || "ads_daily"}</code>
        {" · "}طبقة البيانات: <code className="text-zinc-200 bg-zinc-800 px-1.5 py-0.5 rounded">{report.day?.meta?.source_layer}</code>
      </p>
    </div>
  );
}

function StatCard({ label, value, accent, testid }) {
  return (
    <Card
      className={accent
        ? "bg-emerald-600/15 border-emerald-500/40"
        : "bg-zinc-900 border-zinc-800"}
      data-testid={testid || undefined}
    >
      <CardContent className="py-5">
        <p
          className={`text-sm font-semibold ${accent ? "text-emerald-200" : "text-zinc-300"}`}
        >
          {label}
        </p>
        <p
          className={`text-3xl font-extrabold mt-2 tracking-tight tabular-nums ${
            accent ? "text-emerald-50" : "text-zinc-50"
          }`}
        >
          {value}
        </p>
      </CardContent>
    </Card>
  );
}

function ReportTable({ rows, cols, headers, providerCol }) {
  // iter-257 — Column-specific renderers for the new fields.
  const renderCell = (c, r) => {
    if (c === providerCol) {
      return PROVIDER_AR[r[c]] || r[c] || "—";
    }
    // Bank-fee percentage — always shown with 2 decimals and a % suffix.
    if (c === "bank_fee_pct") {
      const v = Number(r[c] || 0);
      const cfg = Number(r.configured_bank_fee_pct || 0);
      // Display effective % (computed from SSOT) with the configured
      // setting as a tooltip — auditable but never recomputed here.
      return (
        <span
          title={
            cfg > 0 ? `نسبة العمولة المُعَدَّة في الإعدادات: ${cfg.toFixed(2)}%` : undefined
          }
        >
          <span className="font-mono">{v.toFixed(2)}</span>
          <span className="text-xs text-zinc-400 ms-1">%</span>
        </span>
      );
    }
    // Native USD/EUR spend with currency badge.
    if (c === "spend_native") {
      const v = Number(r[c] || 0);
      const ccy = (r.currency_native || "").toUpperCase();
      if (!ccy || ccy === "SAR") return "—";
      return (
        <span>
          <span className="font-mono">{v.toFixed(2)}</span>
          <span className="text-xs text-zinc-400 ms-1">{ccy}</span>
        </span>
      );
    }
    if (typeof r[c] === "number") {
      return r[c].toLocaleString("ar-SA", { maximumFractionDigits: 2 });
    }
    return r[c] || "—";
  };

  return (
    <Card className="bg-zinc-900 border-zinc-800">
      <CardContent className="p-0">
        {rows.length === 0 ? (
          <p className="text-zinc-300 text-sm p-8 text-center font-medium">
            لا توجد بيانات للفترة المحددة.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="border-zinc-800 hover:bg-transparent">
                {headers.map((h) => (
                  <TableHead key={h} className="text-zinc-200 font-semibold">
                    {h}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r, i) => (
                <TableRow key={i} className="border-zinc-800 hover:bg-zinc-800/40">
                  {cols.map((c) => (
                    <TableCell
                      key={c}
                      className="text-zinc-50 font-semibold tabular-nums"
                      data-testid={`report-row-${i}-${c}`}
                    >
                      {renderCell(c, r)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function ReconciliationView({ recon, onSaveManual }) {
  if (!recon) return <p className="text-zinc-300 text-sm font-medium">جاري التحميل...</p>;
  const s = recon.summary || {};
  return (
    <div className="space-y-4">
      {/* Match-status legend (the user-requested 🟢🟡🟠🔴 indicators) */}
      <Card className="bg-zinc-900 border-zinc-800">
        <CardHeader>
          <CardTitle className="text-zinc-50 text-lg font-bold">
            ملخص حالة المطابقة
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
            <MatchStatCard
              icon="🟢" label="متطابق"
              value={s.match_matched}
              tone="bg-emerald-500/15 border-emerald-500/40 text-emerald-50"
            />
            <MatchStatCard
              icon="🟡" label="بانتظار اكتمال المنصة"
              value={s.match_pending_platform}
              tone="bg-amber-500/15 border-amber-500/40 text-amber-50"
            />
            <MatchStatCard
              icon="🟠" label="يحتاج مراجعة"
              value={s.match_drift_review}
              tone="bg-orange-500/20 border-orange-500/40 text-orange-50"
            />
            <MatchStatCard
              icon="🔴" label="فشل في المزامنة"
              value={s.match_sync_failed}
              tone="bg-red-500/20 border-red-500/40 text-red-50"
            />
            <MatchStatCard
              icon="⚪" label="لا توجد بيانات"
              value={s.match_no_data}
              tone="bg-zinc-700/40 border-zinc-600/40 text-zinc-100"
            />
          </div>
          <p className="text-xs text-zinc-400 mt-4 font-medium leading-relaxed">
            <strong className="text-zinc-200">المطابقة التلقائية:</strong>{" "}
            بالضغط على زر «إعادة المطابقة من المنصات» يستعلم النظام عن قيمة كل
            يوم مباشرةً من Meta وSnapchat وTikTok، ويقارنها مع{" "}
            <code className="text-zinc-200 bg-zinc-800 px-1.5 rounded">ads_daily</code>{" "}
            دون أن يُعدّلها (المصدر يبقى ثابتاً للمراجعة).
            الإدخال اليدوي لـ Ads Manager متاح كخيار احتياطي فقط للحالات
            الاستثنائية.
          </p>
        </CardContent>
      </Card>

      <Card className="bg-zinc-900 border-zinc-800">
        <CardContent className="p-0 overflow-x-auto">
          {(recon.data || []).length === 0 ? (
            <p className="text-zinc-300 text-sm p-8 text-center font-medium">
              لا توجد بيانات.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="border-zinc-800 hover:bg-transparent">
                  <TableHead className="text-zinc-200 font-semibold">الحالة</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">التاريخ</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">المنصة</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">الحساب</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">قيمة ads_daily</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">قيمة المنصة الآن</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">قيمة Ads Manager (يدوي)</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">الفرق (SAR)</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">نسبة الفرق</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">سبب الفرق</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">حالة البيانات</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">آخر مزامنة</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">آخر فحص للمنصة</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">إجراء</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(recon.data || []).map((r) => (
                  <ReconRow key={`${r.account_id}-${r.date}`} r={r} onSaveManual={onSaveManual} />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function MatchStatCard({ icon, label, value, tone }) {
  return (
    <div className={`rounded-lg border p-3 ${tone}`}>
      <div className="flex items-center gap-2">
        <span className="text-2xl leading-none">{icon}</span>
        <div>
          <p className="text-xs font-semibold opacity-90">{label}</p>
          <p className="text-2xl font-extrabold tabular-nums leading-tight">
            {value ?? 0}
          </p>
        </div>
      </div>
    </div>
  );
}

function ReconRow({ r, onSaveManual }) {
  const ms = MATCH_STATUS_AR[r.match_status] || MATCH_STATUS_AR.no_data;
  const hasManual = r.has_manual_value;
  const hasPlatformCheck = r.has_platform_check;
  const driftDisplay = r.drift_pct;
  const diffSar = r.diff_sar;
  const driftClass =
    driftDisplay === null || driftDisplay === undefined
      ? "text-zinc-400"
      : driftDisplay >= 15
      ? "text-red-200 font-bold"
      : driftDisplay >= 5
      ? "text-amber-200 font-bold"
      : "text-emerald-200 font-bold";

  return (
    <TableRow className="border-zinc-800 hover:bg-zinc-800/40 align-top">
      <TableCell>
        <Badge
          className={`${ms.cls} font-semibold whitespace-nowrap`}
          data-testid={`match-status-${r.account_id}-${r.date}`}
        >
          <span className="me-1">{ms.icon}</span>
          {ms.label}
        </Badge>
      </TableCell>
      <TableCell className="text-zinc-50 font-bold tabular-nums">{r.date}</TableCell>
      <TableCell className="text-zinc-100 font-semibold">
        {PROVIDER_AR[r.provider] || r.provider}
      </TableCell>
      <TableCell className="text-zinc-100">
        <div className="font-semibold">{r.display_name || "—"}</div>
        <div className="text-xs text-zinc-400 font-mono">
          {r.external_account_id}
        </div>
      </TableCell>
      <TableCell className="text-zinc-50 font-bold tabular-nums">
        {Number(r.spend_sar || 0).toFixed(2)} <span className="text-xs text-zinc-400">SAR</span>
        <div className="text-xs text-zinc-400 font-mono">
          ({Number(r.spend_native || 0).toFixed(2)} {r.currency_native})
        </div>
      </TableCell>
      <TableCell className="font-semibold tabular-nums">
        {hasPlatformCheck ? (
          <>
            <span className="text-zinc-50 font-bold">
              {Number(r.platform_authoritative_sar || 0).toFixed(2)}
            </span>{" "}
            <span className="text-xs text-zinc-400">SAR</span>
            <div className="text-xs text-zinc-400 font-mono">
              ({Number(r.platform_authoritative_native || 0).toFixed(2)} {r.platform_authoritative_currency || r.currency_native})
            </div>
          </>
        ) : (
          <span className="text-zinc-400 italic text-xs">
            اضغط «إعادة المطابقة»
          </span>
        )}
      </TableCell>
      <TableCell className="font-semibold tabular-nums">
        {hasManual ? (
          <>
            <span className="text-zinc-50 font-bold">
              {Number(r.platform_manual_value_sar || 0).toFixed(2)}
            </span>{" "}
            <span className="text-xs text-zinc-400">SAR</span>
            <div className="text-xs text-zinc-400 font-mono">
              ({Number(r.platform_manual_value_native || 0).toFixed(2)} {r.currency_native})
            </div>
          </>
        ) : (
          <span className="text-zinc-500 text-xs">—</span>
        )}
      </TableCell>
      <TableCell className="tabular-nums">
        {hasPlatformCheck ? (
          <span className={
            Math.abs(diffSar || 0) < 0.01
              ? "text-emerald-200 font-bold"
              : "text-amber-100 font-bold"
          }>
            {fmtDiff(diffSar)} SAR
          </span>
        ) : (
          <span className="text-zinc-500">—</span>
        )}
      </TableCell>
      <TableCell className={`tabular-nums ${driftClass}`}>
        {(hasPlatformCheck || hasManual) && driftDisplay !== null && driftDisplay !== undefined
          ? `${Number(driftDisplay).toFixed(2)}%`
          : <span className="text-zinc-400">—</span>}
      </TableCell>
      <TableCell>
        {(r.drift_reason?.likely_causes || []).length === 0 ? (
          <span className="text-zinc-500 text-xs">—</span>
        ) : (
          <div className="flex flex-col gap-1">
            {(r.drift_reason.likely_causes || []).map((c) => (
              <span key={c} className="text-zinc-100 text-xs font-medium leading-tight">
                {DRIFT_CAUSE_AR[c] || c}
              </span>
            ))}
          </div>
        )}
      </TableCell>
      <TableCell>
        <Badge
          className={
            r.confidence === "final"
              ? "bg-emerald-500/20 text-emerald-100 border-emerald-500/40 font-semibold whitespace-nowrap"
              : "bg-blue-500/20 text-blue-100 border-blue-500/40 font-semibold whitespace-nowrap"
          }
        >
          {CONFIDENCE_AR[r.confidence] || r.confidence}
        </Badge>
      </TableCell>
      <TableCell className="text-zinc-200 text-xs font-medium whitespace-nowrap">
        {fmtDate(r.last_synced_at)}
      </TableCell>
      <TableCell className="text-zinc-200 text-xs font-medium whitespace-nowrap">
        {hasPlatformCheck ? (
          fmtDate(r.platform_last_checked_at)
        ) : (
          <span className="text-zinc-500">—</span>
        )}
      </TableCell>
      <TableCell>
        <ManualValueDialog row={r} onSave={onSaveManual} />
      </TableCell>
    </TableRow>
  );
}

function ManualValueDialog({ row, onSave }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(row.platform_manual_value_native ?? "");
  const [note, setNote] = useState("");

  const submit = async () => {
    const v = parseFloat(value);
    if (Number.isNaN(v) || v < 0) {
      toast.error("أدخل قيمة رقمية صحيحة");
      return;
    }
    await onSave({
      account_id: row.account_id,
      date: row.date,
      manual_value_native: v,
      note,
    });
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          size="sm"
          variant="outline"
          className="border-zinc-700 text-zinc-200 hover:bg-zinc-800 hover:text-zinc-50 font-medium text-xs whitespace-nowrap"
          data-testid={`manual-btn-${row.account_id}-${row.date}`}
        >
          {row.has_manual_value ? "تعديل القيمة اليدوية" : "إدخال يدوي (احتياطي)"}
        </Button>
      </DialogTrigger>
      <DialogContent className="bg-zinc-900 border-zinc-800 text-zinc-50" dir="rtl">
        <DialogHeader>
          <DialogTitle className="text-zinc-50 font-bold">
            إدخال قيمة Ads Manager يدوياً (احتياطي)
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-2">
          <p className="text-xs text-zinc-300 leading-relaxed bg-zinc-950 p-2 rounded border border-zinc-800">
            في الوضع الطبيعي، استخدم زر <strong className="text-zinc-100">«إعادة المطابقة من المنصات»</strong>{" "}
            بأعلى الصفحة ليقوم النظام بجلب القيم تلقائياً. هذا الإدخال اليدوي
            للحالات الاستثنائية فقط.
          </p>
          <p className="text-sm text-zinc-300">
            التاريخ:{" "}
            <span className="text-zinc-50 font-bold tabular-nums">{row.date}</span>
            {" · "}المنصة:{" "}
            <span className="text-zinc-50 font-bold">
              {PROVIDER_AR[row.provider] || row.provider}
            </span>
          </p>
          <p className="text-sm text-zinc-300">
            قيمة المزامنة الحالية في{" "}
            <code className="text-zinc-50 bg-zinc-800 px-1.5 rounded">ads_daily</code>:{" "}
            <span className="text-zinc-50 font-bold tabular-nums">
              {Number(row.spend_native || 0).toFixed(2)} {row.currency_native}
            </span>
          </p>
          <div>
            <Label className="text-zinc-200 text-xs font-semibold">
              قيمة Ads Manager (بالعملة الأصلية {row.currency_native})
            </Label>
            <Input
              type="number"
              step="0.01"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className={FIELD_CLS}
              placeholder="مثال: 723.43"
              data-testid="manual-value-input"
            />
          </div>
          <div>
            <Label className="text-zinc-200 text-xs font-semibold">
              ملاحظة (اختياري)
            </Label>
            <Textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className={`${FIELD_CLS} min-h-[60px]`}
              placeholder="مثال: مأخوذة من Ads Manager بتاريخ ..."
              data-testid="manual-value-note"
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setOpen(false)}
            className="border-zinc-700 text-zinc-100 hover:bg-zinc-800 hover:text-zinc-50 font-semibold"
          >
            إلغاء
          </Button>
          <Button
            onClick={submit}
            className="font-semibold"
            data-testid="manual-value-save"
          >
            حفظ وإعادة حساب الفرق
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
