import { useEffect, useState } from "react";
import { CheckCircle, CloudArrowUp, Robot, SpinnerGap } from "@phosphor-icons/react";
import { toast } from "sonner";

import {
  approveProductControlDraft,
  getProductControlCenter,
  publishProductControlDraft,
  saveProductControlDraft,
} from "../../services/mezanProductsV2";

export default function ProductControlCenter({ productId }) {
  const [data, setData] = useState(null);
  const [form, setForm] = useState({ name: "", description: "", seo_title: "", seo_description: "", local_category: "", google_category: "" });
  const [busy, setBusy] = useState(false);

  async function load() {
    if (!productId) return;
    try {
      const result = await getProductControlCenter(productId);
      setData(result);
      const product = result.product || {};
      const draft = result.draft?.changes || {};
      setForm({
        name: draft.name ?? product.name ?? "",
        description: draft.description ?? product.description_html ?? product.description ?? "",
        seo_title: draft.seo?.title ?? product.seo?.title ?? "",
        seo_description: draft.seo?.description ?? product.seo?.description ?? "",
        local_category: draft.local_category ?? product.local_category ?? "",
        google_category: draft.google_category ?? product.google_category ?? "",
      });
    } catch { toast.error("تعذر تحميل مركز التحكم بالمنتج"); }
  }

  useEffect(() => { load(); }, [productId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function saveDraft() {
    setBusy(true);
    try {
      const result = await saveProductControlDraft(productId, {
        source: "human",
        reason: "تعديل من Product Control Center",
        changes: {
          name: form.name,
          description: form.description,
          seo: { title: form.seo_title, description: form.seo_description },
          local_category: form.local_category,
          google_category: form.google_category,
        },
      });
      setData((current) => ({ ...(current || {}), draft: result.draft }));
      toast.success("تم حفظ مسودة المنتج دون التأثير على تكاليف ميزان");
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر حفظ المسودة");
    } finally { setBusy(false); }
  }

  async function approve() {
    if (!data?.draft?.id) return;
    setBusy(true);
    try {
      const result = await approveProductControlDraft(productId, data.draft.id);
      setData((current) => ({ ...current, draft: result.draft }));
      toast.success("تم اعتماد المسودة");
    } finally { setBusy(false); }
  }

  async function publish() {
    if (!data?.draft?.id) return;
    setBusy(true);
    try {
      await publishProductControlDraft(productId, data.draft.id);
      toast.success("تم نشر التعديل إلى سلة والتحقق من بقاء محرك التكاليف مستقلًا");
      await load();
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر نشر التعديل");
    } finally { setBusy(false); }
  }

  if (!data) return null;
  const status = data.draft?.status;

  return (
    <section className="rounded-2xl border border-violet-200 bg-violet-50/30 p-4" dir="rtl">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="flex items-center gap-2 font-black text-violet-950"><Robot size={22} /> مركز تحكم المنتج والذكاء الاصطناعي</h2>
          <p className="mt-1 text-xs leading-6 text-violet-800">المحتوى والتصنيفات وSEO تمر بمسودة واعتماد ونشر. تكاليف المنتج والخيارات والمكونات تبقى مملوكة لميزان ولا تُرسل إلى سلة.</p>
        </div>
        <span className="rounded-full bg-white px-3 py-1 text-xs font-black text-violet-800">{status === "approved" ? "معتمد للنشر" : status === "draft" ? "مسودة" : "اقتراح فقط"}</span>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <label className="text-xs font-bold text-slate-600">اسم المنتج<input value={form.name} onChange={(e) => setForm((v) => ({ ...v, name: e.target.value }))} className="mt-1 w-full rounded-xl border bg-white p-3 text-sm" /></label>
        <label className="text-xs font-bold text-slate-600">التصنيف المحلي<input value={form.local_category} onChange={(e) => setForm((v) => ({ ...v, local_category: e.target.value }))} className="mt-1 w-full rounded-xl border bg-white p-3 text-sm" /></label>
        <label className="text-xs font-bold text-slate-600">تصنيف Google<input value={form.google_category} onChange={(e) => setForm((v) => ({ ...v, google_category: e.target.value }))} className="mt-1 w-full rounded-xl border bg-white p-3 text-sm" /></label>
        <label className="text-xs font-bold text-slate-600">عنوان SEO<input value={form.seo_title} onChange={(e) => setForm((v) => ({ ...v, seo_title: e.target.value }))} className="mt-1 w-full rounded-xl border bg-white p-3 text-sm" /></label>
        <label className="text-xs font-bold text-slate-600 md:col-span-2">وصف SEO<textarea value={form.seo_description} onChange={(e) => setForm((v) => ({ ...v, seo_description: e.target.value }))} className="mt-1 min-h-20 w-full rounded-xl border bg-white p-3 text-sm" /></label>
        <label className="text-xs font-bold text-slate-600 md:col-span-2">وصف المنتج<textarea value={form.description} onChange={(e) => setForm((v) => ({ ...v, description: e.target.value }))} className="mt-1 min-h-40 w-full rounded-xl border bg-white p-3 text-sm" /></label>
      </div>

      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <button disabled={busy} onClick={saveDraft} className="rounded-xl border border-violet-300 bg-white px-4 py-2 text-sm font-black text-violet-800">{busy && <SpinnerGap className="ml-1 inline animate-spin" />} حفظ مسودة</button>
        {status === "draft" && <button disabled={busy} onClick={approve} className="rounded-xl bg-amber-500 px-4 py-2 text-sm font-black text-white"><CheckCircle className="ml-1 inline" /> اعتماد</button>}
        {status === "approved" && <button disabled={busy} onClick={publish} className="rounded-xl bg-emerald-700 px-4 py-2 text-sm font-black text-white"><CloudArrowUp className="ml-1 inline" /> نشر إلى سلة</button>}
      </div>
    </section>
  );
}
