// Iter-250b · P1.5.ab — Suppliers Unification Forensic modal.
// READ-ONLY diagnostic. Surfaces the relationship between
// `db.suppliers` (new) and `db.counterparties` (legacy / ledger)
// plus duplicate-suspect groups. NO writes — the user explicitly
// forbade auto-linking at this stage.
import { useEffect, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const errMsg = (e, fb) =>
  e?.response?.data?.detail || e?.message || fb;

const STATUS_LABEL = {
  new_only:    "مورد جديد",
  linked:      "مربوط",
  ledger_only: "Ledger فقط",
};

export default function SuppliersUnificationForensicModal({ onClose }) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("summary");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get("/audit/suppliers-unification-forensic");
        if (!cancelled) setReport(r.data);
      } catch (e) {
        toast.error(errMsg(e, "فشل تحميل تقرير التوحيد"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
      onClick={onClose}
      data-testid="suppliers-forensic-overlay"
    >
      <div
        className="bg-white rounded-xl max-w-5xl w-full max-h-[92vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
        dir="rtl"
        data-testid="suppliers-forensic-modal"
      >
        <div className="px-5 py-4 border-b bg-amber-50 rounded-t-xl flex items-center justify-between">
          <div>
            <h2 className="text-lg font-extrabold text-amber-900">
              🔍 تقرير توحيد الموردين (READ-ONLY)
            </h2>
            <p className="text-xs text-amber-700 mt-1">
              تشخيص فقط — لا يوجد أي تعديل على قاعدة البيانات.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1 rounded bg-white border text-sm"
            data-testid="suppliers-forensic-close"
          >
            إغلاق
          </button>
        </div>

        {loading ? (
          <div className="flex-1 flex items-center justify-center p-12 text-gray-500">
            جارٍ تحميل التقرير...
          </div>
        ) : !report ? (
          <div className="flex-1 flex items-center justify-center p-12 text-rose-700">
            لا توجد بيانات.
          </div>
        ) : (
          <>
            <div className="px-5 pt-4">
              <Summary summary={report.summary || {}} />
            </div>

            <div className="flex border-b border-slate-200 gap-1 px-5 mt-4"
                 data-testid="suppliers-forensic-tabs">
              <TabBtn id="summary" label="نظرة عامة" cur={tab} setTab={setTab} />
              <TabBtn id="new_only" label="مورد جديد فقط" cur={tab} setTab={setTab} />
              <TabBtn id="ledger_only" label="Ledger فقط" cur={tab} setTab={setTab} />
              <TabBtn id="linked" label="مربوط" cur={tab} setTab={setTab} />
              <TabBtn id="ghosts" label="GL/FM أيتام" cur={tab} setTab={setTab} />
              <TabBtn id="duplicates" label="تكرارات مُشتبه بها" cur={tab} setTab={setTab} />
            </div>

            <div className="flex-1 overflow-y-auto p-5 space-y-3">
              {tab === "summary" && <NotesBlock notes={report.notes} />}
              {tab === "new_only" && (
                <SupplierList rows={report.new_only}
                              empty="لا يوجد موردين في الجدول الجديد فقط." />
              )}
              {tab === "ledger_only" && (
                <SupplierList rows={report.ledger_only}
                              empty="لا يوجد موردين في Ledger فقط." />
              )}
              {tab === "linked" && (
                <SupplierList rows={report.linked}
                              empty="لا يوجد موردين مربوطين." />
              )}
              {tab === "ghosts" && (
                <GhostList rows={report.ghosts || []} />
              )}
              {tab === "duplicates" && (
                <DuplicatesView ds={report.duplicate_suspects || {}} />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function TabBtn({ id, label, cur, setTab }) {
  const active = cur === id;
  return (
    <button
      type="button"
      onClick={() => setTab(id)}
      className={"px-3 py-2 text-xs font-bold border-b-2 transition " +
        (active
          ? "border-amber-600 text-amber-800"
          : "border-transparent text-slate-500 hover:text-slate-700")}
      data-testid={"suppliers-forensic-tab-" + id}
    >
      {label}
    </button>
  );
}

function SummaryCell({ label, value, tone }) {
  return (
    <div className={"rounded-lg border p-3 " + tone}>
      <div className="text-[11px] font-semibold opacity-80">{label}</div>
      <div className="text-xl font-extrabold mt-1">{value ?? 0}</div>
    </div>
  );
}

function Summary({ summary }) {
  return (
    <div
      className="grid grid-cols-2 md:grid-cols-4 gap-2"
      data-testid="suppliers-forensic-summary"
    >
      <SummaryCell label="db.suppliers"      value={summary.db_suppliers_total}      tone="bg-slate-50 border-slate-200 text-slate-700" />
      <SummaryCell label="db.counterparties" value={summary.db_counterparties_total} tone="bg-slate-50 border-slate-200 text-slate-700" />
      <SummaryCell label="مربوط"      value={summary.linked}      tone="bg-indigo-50 border-indigo-200 text-indigo-800" />
      <SummaryCell label="مورد جديد فقط" value={summary.new_only} tone="bg-emerald-50 border-emerald-200 text-emerald-800" />
      <SummaryCell label="Ledger فقط"   value={summary.ledger_only} tone="bg-amber-50 border-amber-200 text-amber-800" />
      <SummaryCell label="GL/FM أيتام"  value={summary.ghosts}      tone="bg-rose-50 border-rose-200 text-rose-800" />
      <SummaryCell label="تكرارات مُشتبه بها" value={summary.duplicate_suspect_groups} tone="bg-purple-50 border-purple-200 text-purple-800" />
    </div>
  );
}

function NotesBlock({ notes }) {
  if (!notes || notes.length === 0) return null;
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-900 space-y-1">
      {notes.map((n, i) => (<div key={i}>• {n}</div>))}
    </div>
  );
}

function SupplierList({ rows, empty }) {
  if (!rows || rows.length === 0) {
    return <p className="text-sm text-gray-500 py-6 text-center">{empty}</p>;
  }
  return (
    <div className="overflow-x-auto border rounded">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-100">
          <tr className="text-right">
            <th className="p-2">الاسم</th>
            <th className="p-2">شخص الاتصال</th>
            <th className="p-2">الجوال</th>
            <th className="p-2">البريد</th>
            <th className="p-2">الحالة</th>
            <th className="p-2">فواتير بدون قيد</th>
            <th className="p-2">إجمالي بدون قيد</th>
            <th className="p-2">المصدر</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t hover:bg-gray-50"
                data-testid={"forensic-row-" + r.id}>
              <td className="p-2 font-semibold">{r.company_name || "?"}</td>
              <td className="p-2">{r.contact_person || "—"}</td>
              <td className="p-2 font-mono">{r.phone || "—"}</td>
              <td className="p-2 text-xs text-gray-600">{r.email || "—"}</td>
              <td className="p-2 text-xs">{r.status || "—"}</td>
              <td className="p-2 text-xs font-bold text-amber-800">
                {r.drift_count || 0}
              </td>
              <td className="p-2 text-xs font-mono text-amber-800">
                {Number(r.drift_total || 0).toLocaleString("en-US",
                  { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </td>
              <td className="p-2 text-[11px]">
                {STATUS_LABEL[r.link_status] || r.link_status || "?"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GhostList({ rows }) {
  if (!rows || rows.length === 0) {
    return (
      <p className="text-sm text-gray-500 py-6 text-center">
        لا يوجد مراجع GL/FM بدون سجل مورد. ✅
      </p>
    );
  }
  return (
    <div className="overflow-x-auto border rounded">
      <p className="text-xs text-rose-700 p-3 bg-rose-50 border-b">
        ⚠️ هذه IDs مرجعة في general_ledger أو financial_movements لكنها
        غير موجودة في جدول الموردين الجديد ولا في counterparties.
      </p>
      <table className="min-w-full text-sm">
        <thead className="bg-gray-100">
          <tr className="text-right">
            <th className="p-2">ID</th>
            <th className="p-2">في GL؟</th>
            <th className="p-2">في FM؟</th>
            <th className="p-2">فواتير بدون قيد</th>
            <th className="p-2">إجمالي بدون قيد</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t">
              <td className="p-2 font-mono text-xs">{r.id}</td>
              <td className="p-2">{r.appears_in_gl ? "✅" : "—"}</td>
              <td className="p-2">{r.appears_in_financial_movements ? "✅" : "—"}</td>
              <td className="p-2 font-bold">{r.drift_count || 0}</td>
              <td className="p-2 font-mono">
                {Number(r.drift_total || 0).toLocaleString("en-US",
                  { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DuplicatesView({ ds }) {
  const sections = [
    { key: "by_name",  title: "تطابق بالاسم"  },
    { key: "by_phone", title: "تطابق بالجوال" },
    { key: "by_email", title: "تطابق بالبريد" },
  ];
  const anyMatches = sections.some((s) => (ds[s.key] || []).length > 0);
  if (!anyMatches) {
    return (
      <p className="text-sm text-gray-500 py-6 text-center">
        لا يوجد تكرارات مُشتبه بها. ✅
      </p>
    );
  }
  return (
    <div className="space-y-4">
      {sections.map((sec) => {
        const groups = ds[sec.key] || [];
        if (groups.length === 0) return null;
        return (
          <div key={sec.key} className="border rounded">
            <div className="bg-purple-50 px-3 py-2 font-bold text-purple-900 text-sm">
              {sec.title} ({groups.length})
            </div>
            <div className="divide-y">
              {groups.map((g, idx) => (
                <div key={idx} className="p-3">
                  <div className="text-xs text-gray-500 mb-2">
                    قيمة المطابقة: <span className="font-mono">{g.match_value}</span>
                  </div>
                  <table className="min-w-full text-xs">
                    <thead className="bg-gray-50">
                      <tr className="text-right">
                        <th className="p-1">المصدر</th>
                        <th className="p-1">الاسم</th>
                        <th className="p-1">الجوال</th>
                        <th className="p-1">البريد</th>
                        <th className="p-1">ID</th>
                      </tr>
                    </thead>
                    <tbody>
                      {g.entries.map((e, i) => (
                        <tr key={i} className="border-t">
                          <td className="p-1">{e.source}</td>
                          <td className="p-1 font-semibold">{e.name}</td>
                          <td className="p-1 font-mono">{e.phone || "—"}</td>
                          <td className="p-1">{e.email || "—"}</td>
                          <td className="p-1 font-mono text-[10px]">{e.id}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
