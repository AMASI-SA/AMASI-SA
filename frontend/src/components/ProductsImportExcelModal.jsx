// Iter-250b · P2 (Phase 2) — Excel products import modal.
// Two-step UX (preview → confirm) mirroring the categories modal.
import { useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const errMsg = (e, fb) =>
  e?.response?.data?.detail || e?.message || fb;

export default function ProductsImportExcelModal({ onClose, onImported }) {
  const [file, setFile]       = useState(null);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [phase, setPhase]     = useState("idle");

  async function runPreview() {
    if (!file) { toast.error("اختر ملف Excel أولاً"); return; }
    setPhase("previewing"); setLoading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api.post(
        "/products/import/preview", fd,
        { headers: { "Content-Type": "multipart/form-data" } });
      setPreview(r.data);
    } catch (e) {
      toast.error(errMsg(e, "فشل قراءة الملف"));
      setPhase("idle");
    } finally { setLoading(false); }
  }

  async function runConfirm() {
    if (!file) return;
    setPhase("confirming"); setLoading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api.post(
        "/products/import/confirm", fd,
        { headers: { "Content-Type": "multipart/form-data" } });
      const s = r.data.summary || {};
      toast.success(
        `تم إنشاء ${s.created} منتج، تحديث ${s.updated} منتج موجود.`);
      setPhase("done");
      onImported?.();
    } catch (e) {
      toast.error(errMsg(e, "فشل تنفيذ الاستيراد"));
      setPhase("idle");
    } finally { setLoading(false); }
  }

  const t = preview?.totals || {};
  const samples = preview?.samples || {};

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
         onClick={onClose} data-testid="prod-import-overlay">
      <div className="bg-white rounded-xl max-w-4xl w-full max-h-[92vh] flex flex-col"
           onClick={(e) => e.stopPropagation()} dir="rtl"
           data-testid="prod-import-modal">
        <div className="px-5 py-4 border-b bg-emerald-50 rounded-t-xl flex items-center justify-between">
          <div>
            <h2 className="text-lg font-extrabold text-emerald-900">
              📥 استيراد المنتجات من Excel
            </h2>
            <p className="text-xs text-emerald-700 mt-1">
              أعمدة: <b>A:</b> رقم المنتج · <b>B:</b> الاسم ·
              <b> C:</b> التصنيف (مسارات بـ " &gt; " بفاصلة بين التصنيفات) ·
              <b> D:</b> الصور · <b>F:</b> سعر التكلفة.
            </p>
          </div>
          <button type="button" onClick={onClose}
                  className="px-3 py-1 rounded bg-white border text-sm"
                  data-testid="prod-import-close">إغلاق</button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          <div className="flex items-center gap-2">
            <input type="file" accept=".xlsx,.xlsm"
                   onChange={(e) => {
                     setFile(e.target.files?.[0] || null);
                     setPreview(null); setPhase("idle");
                   }}
                   className="flex-1 border rounded px-3 py-2 text-sm"
                   data-testid="prod-import-file" />
            <button type="button" onClick={runPreview}
                    disabled={!file || loading}
                    className="bg-emerald-600 disabled:bg-slate-300 text-white px-4 py-2 rounded text-sm font-semibold"
                    data-testid="prod-import-preview-btn">
              {phase === "previewing" && loading
                ? "جارٍ التحليل…" : "تحليل الملف"}
            </button>
          </div>

          {preview && (
            <div className="space-y-3" data-testid="prod-import-preview-block">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                <Stat label="إجمالي الصفوف"  value={t.total_rows} />
                <Stat label="جديد"           value={t.new}      tone="emerald" />
                <Stat label="سيتم تحديثه"   value={t.update}    tone="indigo" />
                <Stat label="مسارات تصنيف فريدة" value={t.unique_category_paths} tone="slate" />
                <Stat label="بدون تصنيف"   value={t.no_category}     tone="amber" />
                <Stat label="بدون تكلفة"   value={t.no_cost}         tone="amber" />
                <Stat label="متعدد التصنيف" value={t.multi_category} tone="indigo" />
                <Stat label="مكرر في الملف" value={t.duplicates_in_file} tone="rose" />
              </div>

              <div className="bg-slate-50 border border-slate-200 rounded p-3 text-[12px] space-y-1">
                <div>📦 المنتجات بدون تصنيف ستُربط تلقائياً بـ
                  <b className="text-amber-800"> «غير مصنف»</b> تحت جذر «المنتجات المستوردة».</div>
                <div>💰 المنتجات بدون تكلفة ستُحفظ مع علم
                  <b className="text-amber-800"> needs_cost</b> — التكلفة ستتحدث من فواتير المورد لاحقاً.</div>
                <div>🌳 التصنيفات الموجودة في ملف المنتجات وغير موجودة في الشجرة
                  سيتم إنشاؤها تلقائياً تحت الجذر.</div>
              </div>

              {samples.new?.length > 0 && (
                <details className="border rounded" open>
                  <summary className="cursor-pointer p-2 bg-emerald-50 text-emerald-900 text-xs font-bold">
                    عيّنة من المنتجات الجديدة ({samples.new.length})
                  </summary>
                  <ul className="p-2 text-xs space-y-2 max-h-60 overflow-y-auto">
                    {samples.new.map((s, i) => (
                      <li key={i} className="flex gap-2 items-center border-b pb-1">
                        {s.image_url ? (
                          <img src={s.image_url} alt="" className="w-10 h-10 rounded object-cover"
                               onError={(e) => { e.target.style.display = "none"; }} />
                        ) : (
                          <div className="w-10 h-10 rounded bg-slate-100"></div>
                        )}
                        <div className="flex-1 min-w-0">
                          <div className="font-bold truncate">{s.name}</div>
                          <div className="text-[10px] text-slate-500 font-mono">#{s.product_id}</div>
                          {s.cat_paths?.[0] && (
                            <div className="text-[10px] text-indigo-700">
                              {s.cat_paths[0].join(" › ")}
                            </div>
                          )}
                        </div>
                        <div className="text-left text-[11px]">
                          {s.cost != null ? (
                            <span className="font-mono text-emerald-800">{s.cost}</span>
                          ) : (
                            <span className="text-amber-700">بحاجة لتكلفة</span>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {samples.duplicates?.length > 0 && (
                <details className="border rounded">
                  <summary className="cursor-pointer p-2 bg-rose-50 text-rose-900 text-xs font-bold">
                    مكرر في الملف ({samples.duplicates.length})
                  </summary>
                  <ul className="p-2 text-xs space-y-1 max-h-32 overflow-y-auto">
                    {samples.duplicates.map((s, i) => (
                      <li key={i}>صف {s.row}: #{s.product_id} — {s.name}</li>
                    ))}
                  </ul>
                </details>
              )}

              {samples.category_paths_first20?.length > 0 && (
                <details className="border rounded">
                  <summary className="cursor-pointer p-2 bg-indigo-50 text-indigo-900 text-xs font-bold">
                    أول 20 مسار تصنيف
                  </summary>
                  <ul className="p-2 text-[11px] space-y-0.5 max-h-32 overflow-y-auto">
                    {samples.category_paths_first20.map((p, i) => (
                      <li key={i}>• {p}</li>
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
            <button type="button" onClick={onClose}
                    className="px-4 py-2 text-sm rounded bg-white border"
                    data-testid="prod-import-cancel">
              {phase === "done" ? "إغلاق" : "إلغاء"}
            </button>
            {preview && phase !== "done" && (
              <button type="button" onClick={runConfirm}
                      disabled={loading || !preview}
                      className="px-5 py-2 text-sm rounded bg-emerald-600 disabled:bg-slate-300 text-white font-semibold"
                      data-testid="prod-import-confirm">
                {phase === "confirming" && loading
                  ? "جارٍ الاستيراد…"
                  : `✅ تأكيد الاستيراد (${t.new || 0} جديد، ${t.update || 0} تحديث)`}
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
