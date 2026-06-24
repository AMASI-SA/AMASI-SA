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

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const FIELD_CLS =
  "bg-zinc-950 border-zinc-800 text-zinc-100 placeholder:text-zinc-500";

// 7 days back inclusive
function defaultRange() {
  const today = new Date();
  const past = new Date(today);
  past.setDate(today.getDate() - 6);
  const fmt = (d) => d.toISOString().slice(0, 10);
  return { from: fmt(past), to: fmt(today) };
}

export default function AdsV2Report() {
  const [range, setRange] = useState(defaultRange());
  const [report, setReport] = useState({ day: null, account: null, provider: null });
  const [recon, setRecon] = useState(null);
  const [syncRunning, setSyncRunning] = useState(false);
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

  const runSync = async () => {
    setSyncRunning(true);
    try {
      const dates = [];
      const d1 = new Date(range.from);
      const d2 = new Date(range.to);
      for (let d = new Date(d1); d <= d2; d.setDate(d.getDate() + 1)) {
        dates.push(d.toISOString().slice(0, 10));
      }
      const r = await axios.post(
        `${API}/ads-v2/sync/run`, { dates },
        { headers: authHeaders() },
      );
      const data = r.data?.data;
      toast.success(`تمت المزامنة: ${data?.ok_count} نجاح، ${data?.fail_count} فشل`);
      await loadAll();
    } catch (e) {
      toast.error("فشلت المزامنة");
    } finally {
      setSyncRunning(false);
    }
  };

  useEffect(() => { loadAll(); }, [loadAll]);

  const fmt = (n) => Number(n || 0).toLocaleString("ar-SA", { maximumFractionDigits: 2 });
  const totals = report.day?.totals || { spend_sar: 0, bank_fee_sar: 0, gross_sar: 0 };

  return (
    <div className="p-6 max-w-7xl mx-auto" dir="rtl"
        data-testid="ads-v2-report-page">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold text-zinc-100">تقرير الإعلانات</h1>
            <Badge className="bg-blue-500/15 text-blue-300 border-blue-500/30">
              V2 · Phase 1
            </Badge>
          </div>
          <p className="text-sm text-zinc-400 mt-1">
            كل الأرقام تأتي من <code className="text-zinc-200">ads_daily</code> فقط (Single Source of Truth).
          </p>
        </div>
      </div>

      {/* Controls */}
      <Card className="bg-zinc-900 border-zinc-800 mb-6">
        <CardContent className="py-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div>
              <Label className="text-zinc-300 text-xs">من</Label>
              <Input
                type="date" value={range.from}
                onChange={(e) => setRange({ ...range, from: e.target.value })}
                className={FIELD_CLS}
                data-testid="report-from"
              />
            </div>
            <div>
              <Label className="text-zinc-300 text-xs">إلى</Label>
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
                data-testid="report-refresh"
              >
                تحديث
              </Button>
            </div>
            <div className="flex items-end">
              <Button
                onClick={runSync} disabled={syncRunning}
                data-testid="run-sync-btn"
              >
                {syncRunning ? "جاري المزامنة..." : "مزامنة الفترة الآن"}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Totals */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <StatCard label="إجمالي الصرف" value={`${fmt(totals.spend_sar)} SAR`} />
        <StatCard label="إجمالي العمولة البنكية" value={`${fmt(totals.bank_fee_sar)} SAR`} />
        <StatCard label="الإجمالي مع العمولة" value={`${fmt(totals.gross_sar)} SAR`} accent />
      </div>

      <Tabs defaultValue="day" className="w-full">
        <TabsList className="grid grid-cols-4 w-full bg-zinc-900 mb-6">
          <TabsTrigger value="day" data-testid="tab-by-day">حسب اليوم</TabsTrigger>
          <TabsTrigger value="account" data-testid="tab-by-account">حسب الحساب</TabsTrigger>
          <TabsTrigger value="provider" data-testid="tab-by-provider">حسب المنصة</TabsTrigger>
          <TabsTrigger value="recon" data-testid="tab-recon">المطابقة (Reconciliation)</TabsTrigger>
        </TabsList>

        <TabsContent value="day">
          <ReportTable
            rows={report.day?.data || []}
            cols={["date", "spend_sar", "bank_fee_sar", "gross_sar", "accounts_count", "impressions", "clicks"]}
            headers={["التاريخ", "صرف SAR", "عمولة SAR", "إجمالي SAR", "حسابات", "مشاهدات", "نقرات"]}
          />
        </TabsContent>

        <TabsContent value="account">
          <ReportTable
            rows={report.account?.data || []}
            cols={["display_name", "provider", "currency_native", "spend_sar", "bank_fee_sar", "gross_sar", "days_count", "latest_date"]}
            headers={["الحساب", "المنصة", "العملة", "صرف SAR", "عمولة SAR", "إجمالي SAR", "أيام", "آخر تاريخ"]}
          />
        </TabsContent>

        <TabsContent value="provider">
          <ReportTable
            rows={report.provider?.data || []}
            cols={["provider", "accounts_count", "days_count", "spend_sar", "bank_fee_sar", "gross_sar"]}
            headers={["المنصة", "حسابات", "أيام", "صرف SAR", "عمولة SAR", "إجمالي SAR"]}
          />
        </TabsContent>

        <TabsContent value="recon">
          <ReconciliationView recon={recon} />
        </TabsContent>
      </Tabs>

      {/* SSOT badge */}
      <p className="text-xs text-zinc-500 mt-4 text-center">
        Source: <code className="text-zinc-400">{report.day?.meta?.ssot || "ads_daily"}</code>
        {" · "}Layer: <code className="text-zinc-400">{report.day?.meta?.source_layer}</code>
      </p>
    </div>
  );
}

function StatCard({ label, value, accent }) {
  return (
    <Card className={`${accent ? "bg-emerald-500/10 border-emerald-500/30" : "bg-zinc-900 border-zinc-800"}`}>
      <CardContent className="py-4">
        <p className={`text-xs ${accent ? "text-emerald-300" : "text-zinc-400"}`}>{label}</p>
        <p className={`text-2xl font-bold mt-1 ${accent ? "text-emerald-100" : "text-zinc-100"}`}>{value}</p>
      </CardContent>
    </Card>
  );
}

function ReportTable({ rows, cols, headers }) {
  return (
    <Card className="bg-zinc-900 border-zinc-800">
      <CardContent className="p-0">
        {rows.length === 0 ? (
          <p className="text-zinc-500 text-sm p-8 text-center">لا بيانات.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="border-zinc-800">
                {headers.map((h) => (
                  <TableHead key={h} className="text-zinc-400">{h}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r, i) => (
                <TableRow key={i} className="border-zinc-800">
                  {cols.map((c) => (
                    <TableCell key={c} className="text-zinc-200 font-mono text-sm">
                      {typeof r[c] === "number"
                        ? r[c].toLocaleString("ar-SA", { maximumFractionDigits: 2 })
                        : (r[c] || "—")}
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

function ReconciliationView({ recon }) {
  if (!recon) return <p className="text-zinc-500 text-sm">جاري التحميل...</p>;
  const s = recon.summary || {};
  return (
    <div className="space-y-4">
      <Card className="bg-zinc-900 border-zinc-800">
        <CardHeader>
          <CardTitle className="text-zinc-100 text-base">ملخص المطابقة</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-6 gap-3 text-sm">
            <SummaryStat label="إجمالي" value={s.rows_total} />
            <SummaryStat label="بها anomaly" value={s.rows_with_anomalies} tone={s.rows_with_anomalies > 0 ? "warn" : null} />
            <SummaryStat label="Late reporting" value={s.rows_late_reporting} tone={s.rows_late_reporting > 0 ? "warn" : null} />
            <SummaryStat label="Drift > 5%" value={s.rows_drift_above_5pct} tone={s.rows_drift_above_5pct > 0 ? "warn" : null} />
            <SummaryStat label="Drift > 15%" value={s.rows_drift_above_15pct} tone={s.rows_drift_above_15pct > 0 ? "err" : null} />
            <SummaryStat label="بدون FX" value={s.rows_missing_fx} tone={s.rows_missing_fx > 0 ? "err" : null} />
          </div>
        </CardContent>
      </Card>

      <Card className="bg-zinc-900 border-zinc-800">
        <CardContent className="p-0">
          {(recon.data || []).length === 0 ? (
            <p className="text-zinc-500 text-sm p-8 text-center">لا بيانات.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="border-zinc-800">
                  <TableHead className="text-zinc-400">التاريخ</TableHead>
                  <TableHead className="text-zinc-400">المنصة</TableHead>
                  <TableHead className="text-zinc-400">الصرف الأصلي</TableHead>
                  <TableHead className="text-zinc-400">SAR</TableHead>
                  <TableHead className="text-zinc-400">FX</TableHead>
                  <TableHead className="text-zinc-400">Drift %</TableHead>
                  <TableHead className="text-zinc-400">Flags</TableHead>
                  <TableHead className="text-zinc-400">Confidence</TableHead>
                  <TableHead className="text-zinc-400">Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(recon.data || []).map((r) => (
                  <TableRow key={`${r.account_id}-${r.date}`} className="border-zinc-800">
                    <TableCell className="text-zinc-200 font-mono">{r.date}</TableCell>
                    <TableCell className="text-zinc-300">{r.provider}</TableCell>
                    <TableCell className="text-zinc-200 font-mono">
                      {Number(r.spend_native).toFixed(2)} {r.currency_native}
                    </TableCell>
                    <TableCell className="text-zinc-100 font-mono">
                      {Number(r.spend_sar).toFixed(2)}
                    </TableCell>
                    <TableCell className="text-zinc-400 text-xs">
                      {Number(r.fx_rate).toFixed(4)} <span className="text-zinc-600">({r.fx_source})</span>
                    </TableCell>
                    <TableCell className={
                      r.drift_pct >= 15 ? "text-red-300" :
                      r.drift_pct >= 5 ? "text-amber-300" : "text-zinc-400"
                    }>
                      {Number(r.drift_pct).toFixed(2)}%
                    </TableCell>
                    <TableCell>
                      {(r.anomaly_flags || []).length === 0 ? (
                        <span className="text-zinc-600">—</span>
                      ) : (
                        <div className="flex gap-1 flex-wrap">
                          {(r.anomaly_flags || []).map((f) => (
                            <Badge key={f} className="bg-amber-500/15 text-amber-300 border-amber-500/30 text-xs">
                              {f}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge className={r.confidence === "final"
                        ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                        : "bg-blue-500/15 text-blue-300 border-blue-500/30"}>
                        {r.confidence}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge className="bg-zinc-800 text-zinc-300 border-zinc-700">
                        {r.review_status}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SummaryStat({ label, value, tone }) {
  const cls =
    tone === "err" ? "text-red-300" :
    tone === "warn" ? "text-amber-300" : "text-zinc-100";
  return (
    <div>
      <p className="text-zinc-500 text-xs">{label}</p>
      <p className={`text-xl font-bold mt-0.5 ${cls}`}>{value ?? 0}</p>
    </div>
  );
}
