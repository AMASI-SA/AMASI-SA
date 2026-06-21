// Iter-250b · P2 (Phase 1) — Excel import modal for product
// categories. Two-step UX:
//   1) Upload the .xlsx → /preview returns counts + samples.
//   2) Operator reviews + presses "تأكيد" → /confirm runs the upsert.
// No DELETE / no MIGRATION of existing data — strictly additive.
import { useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const errMsg = (e, fb) =>
  e?.response?.data?.detail || e?.message || fb;

export default function CategoriesImportExcelModal({ onClose, onImported }) {
  const [file, setFile]       = useState(null);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [phase, setPhase]     = useState("idle"); // idle | previewing | confirming | done

  async function runPreview() {
    if (!file) {
      toast.error("اختر ملف Excel أولاً");
      return;
    }
    setPhase("previewing");
    setLoading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api.post(
        "/products/categories/import/preview",
        fd, { headers: { "Content-Type": "multipart/form-data" } });
      setPreview(r.data);
    } catch (e) {
      toast.error(errMsg(e, "فشل قراءة الملف"));
      setPhase("idle");
    } finally {
      setLoading(false);
    }
  }

  async function runConfirm() {
    if (!file) return;
    setPhase("confirming");
    setLoading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api.post(
        "/products/categories/import/confirm",
        fd, { headers: { "Content-Type": "multipart/form-data" } });
      const s = r.data.summary || {};
      toast.success(
        `تم إنشاء ${s.created} تصنيف، تم تخطّي ${s.skipped_existing} موجود مسبقاً.`
      );
      setPhase("done");
      onImported?.();
    } catch (e) {
      toast.error(errMsg(e, "فشل تنفيذ الاستيراد"));
      setPhase("idle");
    } finally {
      setLoading(false);
    }
  }

  const t = preview?.totals || {};
  const samples = preview?.samples || {};

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
      onClick={onClose}
      data-testid="cat-import-overlay"
    >
      <div
        className="bg-white rounded-xl max-w-3xl w-full max-h-[92vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
        dir="rtl"
        data-testid="cat-import-modal"
      >
        <div className="px-5 py-4 border-b bg-indigo-50 rounded-t-xl flex items-center justify-between">
          <div>
            <h2 className="text-lg font-extrabold text-indigo-900">
              📥 استيراد التصنيفات من Excel
            </h2>
            <p className="text-xs text-indigo-700 mt-1">
              أعمدة الملف: <b>A:</b> اسم التصنيف · <b>B:</b> فرعي (نعم/لا) ·
              <b> C:</b> التصنيف الاب. باقي الأعمدة تُتجاهل.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1 rounded bg-white border text-sm"
            data-testid="cat-import-close"
          >
            إغلاق
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* File picker */}
          <div className="flex items-center gap-2">
            <input
              type="file"
              accept=".xlsx,.xlsm"
              onChange={(e) => {
                setFile(e.target.files?.[0] || null);
                setPreview(null);
                setPhase("idle");
              }}
              className="flex-1 border rounded px-3 py-2 text-sm"
              data-testid="cat-import-file"
            />
            <button
              type="button"
              onClick={runPreview}
              disabled={!file || loading}
              className="bg-indigo-600 disabled:bg-slate-300 text-white px-4 py-2 rounded text-sm font-semibold"
              data-testid="cat-import-preview-btn"
            >
              {phase === "previewing" && loading ? "جارٍ التحليل…" : "تحليل الملف"}
            </button>
          </div>

          {/* Preview report */}
          {preview && (
            <div className="space-y-3" data-testid="cat-import-preview-block">
              {preview.root?.exists ? (
                <div className="bg-slate-50 border border-slate-200 rounded p-3 text-xs">
                  جذر <b>«المنتجات المستوردة»</b> موجود وسيتم الإضافة تحته.
                </div>
              ) : (
                <div className="bg-amber-50 border border-amber-200 rounded p-3 text-xs text-amber-800">
                  سيتم إنشاء جذر جديد باسم <b>«المنتجات المستوردة»</b>.
                </div>
              )}

              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                <Stat label="إجمالي الصفوف"    value={t.total_rows} />
                <Stat label="جديد"             value={t.new}        tone="emerald" />
                <Stat label="موجود مسبقاً"    value={t.existing}    tone="slate" />
                <Stat label="تصنيف رئيسي"     value={t.root_level_in_file} tone="indigo" />
                <Stat label="تصنيف فرعي"      value={t.sub_level_in_file}  tone="indigo" />
                <Stat label="فرعي بلا أب"      value={t.orphan_subs_no_parent} tone="amber" />
                <Stat label="مكرر في الملف"   value={t.duplicates_in_file}    tone="amber" />
                <Stat label="أب غير موجود"    value={t.parent_not_found_in_file_or_db} tone="rose" />
              </div>

              {(t.orphan_subs_no_parent > 0
                || t.parent_not_found_in_file_or_db > 0) && (
                <div className="bg-amber-50 border border-amber-200 rounded p-3 text-xs text-amber-900">
                  ⚠️ التصنيفات الفرعية التي ليس لها أب صالح ستُلحَق
                  بجذر «المنتجات المستوردة» مؤقتاً — تستطيع تحريكها
                  يدوياً بعد الاستيراد.
                </div>
              )}

              {samples.new?.length > 0 && (
                <details className="border rounded">
                  <summary className="cursor-pointer p-2 bg-emerald-50 text-emerald-900 text-xs font-bold">
                    عيّنة التصنيفات الجديدة ({samples.new.length})
                  </summary>
                  <ul className="p-2 text-xs space-y-1 max-h-48 overflow-y-auto">
                    {samples.new.map((s, i) => (
                      <li key={i} className="flex gap-2">
                        {s.is_sub ? "↳" : "•"}
                        <span className="font-bold">{s.name}</span>
                        {s.parent && (
                          <span className="text-slate-500">
                            (تحت: {s.parent})
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {samples.orphans?.length > 0 && (
                <details className="border rounded">
                  <summary className="cursor-pointer p-2 bg-amber-50 text-amber-900 text-xs font-bold">
                    فرعي بلا أب ({samples.orphans.length})
                  </summary>
                  <ul className="p-2 text-xs space-y-1 max-h-32 overflow-y-auto">
                    {samples.orphans.map((s, i) => (
                      <li key={i}>صف {s.raw_row_index}: {s.name}</li>
                    ))}
                  </ul>
                </details>
              )}

              {samples.parent_missing?.length > 0 && (
                <details className="border rounded">
                  <summary className="cursor-pointer p-2 bg-rose-50 text-rose-900 text-xs font-bold">
                    أب غير موجود ({samples.parent_missing.length})
                  </summary>
                  <ul className="p-2 text-xs space-y-1 max-h-32 overflow-y-auto">
                    {samples.parent_missing.map((s, i) => (
                      <li key={i}>
                        {s.category} ← الأب المفقود: <b>{s.missing_parent}</b>
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {preview.notes?.length > 0 && (
                <div className="text-[11px] text-slate-600 leading-relaxed">
                  {preview.notes.map((n, i) => <div key={i}>• {n}</div>)}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="px-5 py-3 border-t bg-slate-50 flex items-center justify-between">
          <span className="text-xs text-slate-500">
            {phase === "done" && "✅ اكتمل الاستيراد"}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm rounded bg-white border"
              data-testid="cat-import-cancel"
            >
              {phase === "done" ? "إغلاق" : "إلغاء"}
            </button>
            {preview && phase !== "done" && (
              <button
                type="button"
                onClick={runConfirm}
                disabled={loading || !preview}
                className="px-5 py-2 text-sm rounded bg-emerald-600 disabled:bg-slate-300 text-white font-semibold"
                data-testid="cat-import-confirm"
              >
                {phase === "confirming" && loading
                  ? "جارٍ الاستيراد…"
                  : `✅ تأكيد الاستيراد (${t.new || 0} جديد)`}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }) {
  const cls = {
    emerald: "bg-emerald-50 border-emerald-200 text-emerald-900",
    slate:   "bg-slate-50 border-slate-200 text-slate-700",
    indigo:  "bg-indigo-50 border-indigo-200 text-indigo-900",
    amber:   "bg-amber-50 border-amber-200 text-amber-900",
    rose:    "bg-rose-50 border-rose-200 text-rose-900",
  }[tone] || "bg-slate-50 border-slate-200 text-slate-700";
  return (
    <div className={"rounded border p-2 " + cls}>
      <div className="text-[10px] font-bold opacity-80">{label}</div>
      <div className="text-lg font-extrabold mt-0.5">{value ?? 0}</div>
    </div>
  );
}
