// Iter-293 — COD Receipts Diagnostic Report (Read-Only).
//
// Lists every Qoyod invoice that was created from a COD-family order
// but ALSO has a Qoyod invoice_payment / receipt id attached — meaning
// the old pipeline wrongly booked it as paid. The accountant uses the
// list to manually delete the wrong payment in Qoyod.
//
// This page does NOT call Qoyod. It does NOT mutate anything. It just
// reads `/api/integrations/qoyod/admin/cod-receipts-report`.

import React, { useEffect, useState } from "react";
import axios from "axios";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { toast } from "sonner";

const API = process.env.REACT_APP_BACKEND_URL + "/api";

const STAT_TONE = {
  ok:       "bg-emerald-50 text-emerald-900 border-emerald-200",
  mismatch: "bg-rose-50 text-rose-900 border-rose-300",
  warn:     "bg-amber-50 text-amber-900 border-amber-200",
};

function StatCard({ label, value, tone = "ok", testid }) {
  return (
    <div className={`rounded-xl border px-5 py-4 ${STAT_TONE[tone] || STAT_TONE.ok}`}
         data-testid={testid}>
      <div className="text-[11px] uppercase tracking-wide font-bold opacity-70">{label}</div>
      <div className="text-3xl font-extrabold mt-1" dir="ltr">{value}</div>
    </div>
  );
}

export default function QoyodCodReceiptsReport() {
  const [loading, setLoading] = useState(false);
  const [report, setReport]   = useState(null);
  const [filters, setFilters] = useState({ from: "", to: "", limit: 500 });

  const load = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filters.from)  params.set("from",  filters.from);
      if (filters.to)    params.set("to",    filters.to);
      if (filters.limit) params.set("limit", filters.limit);
      const url = `${API}/integrations/qoyod/admin/cod-receipts-report?${params.toString()}`;
      const { data } = await axios.get(url);
      setReport(data);
    } catch (e) {
      toast.error(e?.response?.data?.detail || e.message || "فشل التحميل");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* on mount */ /* eslint-disable-line */ }, []);

  return (
    <div className="space-y-6" dir="rtl" data-testid="qoyod-cod-receipts-report">
      <div>
        <h1 className="text-2xl font-extrabold text-slate-900">
          تقرير تشخيصي — COD المُرحَّل كمدفوع
        </h1>
        <p className="text-sm text-slate-600 mt-1">
          يعرض كل الطلبات التي طريقة دفعها هي{" "}
          <span className="font-mono font-bold">cash on delivery (COD)</span>{" "}
          ومُرتبطة بسند قبض / Invoice Payment في قيود (وهو وضع محاسبي خاطئ —
          المفروض COD يبقى فاتورة آجلة بدون سند). <strong>هذا التقرير
          للقراءة فقط ولا يجري أي تعديل على قيود.</strong>
        </p>
      </div>

      {/* Filters */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">عوامل التصفية</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1">من تاريخ</label>
            <input
              type="date" value={filters.from}
              onChange={(e) => setFilters({ ...filters, from: e.target.value })}
              data-testid="cod-filter-from"
              className="border border-slate-300 rounded-md px-3 py-1.5 text-sm" />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1">إلى تاريخ</label>
            <input
              type="date" value={filters.to}
              onChange={(e) => setFilters({ ...filters, to: e.target.value })}
              data-testid="cod-filter-to"
              className="border border-slate-300 rounded-md px-3 py-1.5 text-sm" />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1">الحد الأقصى</label>
            <input
              type="number" min={1} max={5000} value={filters.limit}
              onChange={(e) => setFilters({ ...filters, limit: Number(e.target.value) || 500 })}
              data-testid="cod-filter-limit"
              className="w-28 border border-slate-300 rounded-md px-3 py-1.5 text-sm" />
          </div>
          <Button onClick={load} disabled={loading}
                  data-testid="cod-refresh-btn">
            {loading ? "...جارٍ التحميل" : "تحديث"}
          </Button>
        </CardContent>
      </Card>

      {/* Stats */}
      {report && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4"
             data-testid="cod-stats-grid">
          <StatCard label="إجمالي طلبات COD" value={report.total_cod}
                    tone="ok" testid="cod-stat-total" />
          <StatCard
            label="COD مع سند قبض (خطأ محاسبي)"
            value={report.with_receipt}
            tone={report.with_receipt > 0 ? "mismatch" : "ok"}
            testid="cod-stat-with-receipt" />
          <StatCard label="COD صحيح (بدون سند)"
                    value={report.without_receipt}
                    tone="ok" testid="cod-stat-without-receipt" />
        </div>
      )}

      {/* Rows */}
      {report && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              التفاصيل ({report.rows?.length || 0} صف
              {report.truncated && " — مقطوع، ارفع الحد"})
            </CardTitle>
          </CardHeader>
          <CardContent>
            {(!report.rows || report.rows.length === 0) ? (
              <div className="text-sm text-slate-500 text-center py-8"
                   data-testid="cod-empty">
                لا توجد طلبات COD في النطاق المختار.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs border" data-testid="cod-rows-table">
                  <thead className="bg-slate-50 text-slate-700">
                    <tr>
                      <th className="text-right px-2 py-2 font-bold">#طلب سلة</th>
                      <th className="text-right px-2 py-2 font-bold">فاتورة قيود</th>
                      <th className="text-right px-2 py-2 font-bold">سند قبض</th>
                      <th className="text-right px-2 py-2 font-bold">إجمالي</th>
                      <th className="text-right px-2 py-2 font-bold">مدفوع</th>
                      <th className="text-right px-2 py-2 font-bold">مستحق</th>
                      <th className="text-right px-2 py-2 font-bold">posting_mode</th>
                      <th className="text-right px-2 py-2 font-bold">التوصية</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {report.rows.map((r) => (
                      <tr key={r.id || r.salla_order_id}
                          className={r.has_receipt ? "bg-rose-50/40" : ""}
                          data-testid={`cod-row-${r.salla_order_id}`}>
                        <td className="px-2 py-2 font-mono font-bold" dir="ltr">
                          {r.salla_order_number || r.salla_order_id}
                        </td>
                        <td className="px-2 py-2 font-mono" dir="ltr">{r.qoyod_invoice_id}</td>
                        <td className="px-2 py-2 font-mono" dir="ltr">
                          {r.qoyod_invoice_payment_id || r.qoyod_receipt_id ? (
                            <Badge variant="destructive">{r.qoyod_invoice_payment_id || r.qoyod_receipt_id}</Badge>
                          ) : (
                            <span className="text-slate-400">—</span>
                          )}
                        </td>
                        <td className="px-2 py-2" dir="ltr">{r.invoice_total ?? "—"}</td>
                        <td className="px-2 py-2" dir="ltr">{r.paid_amount ?? "—"}</td>
                        <td className="px-2 py-2 font-bold" dir="ltr">{r.remaining_amount ?? "—"}</td>
                        <td className="px-2 py-2 font-mono">
                          {r.posting_mode || <span className="text-slate-400">—</span>}
                        </td>
                        <td className="px-2 py-2 text-[11px]">
                          {r.recommendation}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
