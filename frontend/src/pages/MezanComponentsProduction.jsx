import { useEffect, useMemo, useState } from "react";
import { Cube, MagnifyingGlass, Plus, SpinnerGap, Wrench } from "@phosphor-icons/react";
import {
  addMezanComponentPreview,
  filterMezanComponents,
  getMezanComponentWorkspace,
  saveMezanComponentPreviewCost,
  summarizeMezanComponents,
} from "../services/mezanComponentCatalog";

const EMPTY_FORM = { name: "", code: "", kind: "service", unit: "job", unit_cost: "" };

export default function MezanComponentsProduction() {
  const [workspace, setWorkspace] = useState({ components: [], products: [], meta: {} });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [costDraft, setCostDraft] = useState("");

  async function load(preferredId = "") {
    setLoading(true);
    setError("");
    try {
      const result = await getMezanComponentWorkspace();
      const components = result?.components || [];
      setWorkspace(result || { components: [], products: [], meta: {} });
      const nextId = preferredId || selectedId || components[0]?.id || "";
      setSelectedId(nextId);
      const selected = components.find((row) => row.id === nextId) || components[0];
      setCostDraft(selected?.reference_cost?.amount ?? "");
    } catch (err) {
      setError(err?.response?.data?.detail?.message || err?.message || "تعذر تحميل كتالوج المكونات.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const components = workspace.components || [];
  const visible = useMemo(() => filterMezanComponents(components, { query, filter: "all" }), [components, query]);
  const summary = useMemo(() => summarizeMezanComponents(components), [components]);
  const selected = components.find((row) => row.id === selectedId) || components[0] || null;

  async function createComponent(event) {
    event.preventDefault();
    if (!form.name.trim() || !form.code.trim()) return;
    setSaving(true);
    try {
      const result = await addMezanComponentPreview({
        name: form.name.trim(),
        code: form.code.trim().toUpperCase(),
        kind: form.kind,
        unit: form.unit,
        unit_cost: form.unit_cost === "" ? null : Number(form.unit_cost),
      });
      if (!result.ok) throw new Error(result.code || "تعذر إنشاء المكوّن");
      setForm(EMPTY_FORM);
      setAddOpen(false);
      await load(result.resource?.id || "");
    } catch (err) {
      setError(err?.message || "تعذر إنشاء المكوّن.");
    } finally {
      setSaving(false);
    }
  }

  async function saveCost() {
    if (!selected) return;
    setSaving(true);
    try {
      const amount = costDraft === "" ? null : Number(costDraft);
      const result = await saveMezanComponentPreviewCost(selected.id, amount);
      if (!result.ok) throw new Error(result.code || "تعذر حفظ التكلفة");
      await load(selected.id);
    } catch (err) {
      setError(err?.message || "تعذر حفظ التكلفة.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="flex min-h-[55vh] items-center justify-center" dir="rtl"><SpinnerGap size={30} className="animate-spin text-violet-700" /></div>;
  }

  return (
    <div className="space-y-5" dir="rtl">
      <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-black text-slate-950">مكونات وتكاليف Mezan OS</h1>
            <p className="mt-1 text-sm text-slate-500">مكوّن مركزي واحد يمكن ربطه بخيارات مئات المنتجات، وتعديل تكلفته من مكان واحد.</p>
          </div>
          <button onClick={() => setAddOpen(true)} className="rounded-xl bg-violet-700 px-5 py-3 font-black text-white"><Plus className="ml-2 inline" />إضافة مكوّن أو خدمة</button>
        </div>
      </section>

      {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-800">{error}</div>}

      <section className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-2xl border bg-white p-4"><div className="text-xs text-slate-500">إجمالي المكونات</div><div className="mt-1 text-2xl font-black">{summary.total}</div></div>
        <div className="rounded-2xl border bg-white p-4"><div className="text-xs text-slate-500">خدمات العمل</div><div className="mt-1 text-2xl font-black">{summary.labor_services}</div></div>
        <div className="rounded-2xl border bg-white p-4"><div className="text-xs text-slate-500">تحتاج تكلفة</div><div className="mt-1 text-2xl font-black">{summary.missing_cost}</div></div>
      </section>

      {!components.length ? (
        <section className="rounded-3xl border border-dashed border-violet-300 bg-violet-50/40 p-10 text-center">
          <Cube size={56} className="mx-auto text-violet-600" />
          <h2 className="mt-4 text-xl font-black">ابدأ بإضافة أول مكوّن أو خدمة</h2>
          <p className="mx-auto mt-2 max-w-xl text-sm leading-7 text-slate-600">مثال: تطريز الاسم، نقش الاسم، طلاء ذهبي، تغليف هدية، علبة، سلسلة أو خامة. بعد إنشائه يمكنك ربطه بأي قيمة خيار داخل المنتجات.</p>
          <button onClick={() => setAddOpen(true)} className="mt-5 rounded-xl bg-violet-700 px-6 py-3 font-black text-white"><Plus className="ml-2 inline" />إضافة أول مكوّن</button>
        </section>
      ) : (
        <section className="grid gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
          <aside className="overflow-hidden rounded-3xl border bg-white shadow-sm">
            <div className="border-b p-4"><label className="flex items-center gap-2 rounded-xl border bg-slate-50 px-3 py-2"><MagnifyingGlass className="text-slate-400" /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="ابحث بالاسم أو الرمز…" className="min-w-0 flex-1 bg-transparent outline-none" /></label></div>
            <div className="max-h-[650px] overflow-auto divide-y">{visible.map((row) => <button key={row.id} onClick={() => { setSelectedId(row.id); setCostDraft(row.reference_cost?.amount ?? ""); }} className={`w-full p-4 text-right ${selected?.id === row.id ? "bg-violet-50" : "hover:bg-slate-50"}`}><div className="font-black">{row.name}</div><div className="mt-1 text-xs text-slate-500">{row.code} · {row.track_inventory ? "مكوّن مخزني" : "خدمة عمل"}</div></button>)}</div>
          </aside>

          {selected && <main className="space-y-5 rounded-3xl border bg-white p-5 shadow-sm">
            <div className="flex items-center gap-3 border-b pb-4"><div className="rounded-2xl bg-violet-100 p-3 text-violet-700">{selected.track_inventory ? <Cube size={28} /> : <Wrench size={28} />}</div><div><h2 className="text-xl font-black">{selected.name}</h2><p className="text-xs text-slate-500">{selected.code}</p></div></div>
            <section className="rounded-2xl border p-4"><h3 className="font-black">التكلفة المركزية</h3><p className="mt-1 text-xs text-slate-500">تعديلها يحدّث جميع خيارات المنتجات المرتبطة للطلبات القادمة.</p><div className="mt-4 flex gap-3"><input type="number" min="0" step="0.01" value={costDraft} onChange={(e) => setCostDraft(e.target.value)} className="min-w-0 flex-1 rounded-xl border p-3" placeholder="التكلفة بالريال" /><button onClick={saveCost} disabled={saving} className="rounded-xl bg-emerald-700 px-5 font-black text-white disabled:opacity-50">حفظ</button></div></section>
            <section className="rounded-2xl border p-4"><h3 className="font-black">المنتجات المرتبطة</h3><p className="mt-1 text-sm text-slate-500">{selected.product_usages?.length || 0} ربط بخيارات المنتجات.</p>{(selected.product_usages || []).map((usage) => <div key={usage.id} className="mt-3 rounded-xl bg-slate-50 p-3"><div className="font-bold">{usage.product_name}</div><div className="text-xs text-slate-500">{usage.condition?.option_name}: {usage.condition?.value_name}</div></div>)}</section>
          </main>}
        </section>
      )}

      {addOpen && <div className="fixed inset-0 z-[160] flex items-center justify-center bg-slate-950/50 p-4"><form onSubmit={createComponent} className="w-full max-w-lg rounded-3xl bg-white p-6 shadow-2xl"><h2 className="text-xl font-black">إضافة مكوّن أو خدمة</h2><div className="mt-5 grid gap-3"><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="الاسم، مثال: تطريز الاسم" className="rounded-xl border p-3" /><input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="الرمز، مثال: EMBROIDERY_NAME" className="rounded-xl border p-3" /><select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value, unit: e.target.value === "stock_component" ? "piece" : "job" })} className="rounded-xl border p-3"><option value="service">خدمة عمل</option><option value="stock_component">مكوّن مخزني</option></select><input type="number" min="0" step="0.01" value={form.unit_cost} onChange={(e) => setForm({ ...form, unit_cost: e.target.value })} placeholder="التكلفة الحالية بالريال" className="rounded-xl border p-3" /></div><div className="mt-6 flex justify-end gap-2"><button type="button" onClick={() => setAddOpen(false)} className="rounded-xl border px-4 py-3 font-bold">إلغاء</button><button disabled={saving || !form.name.trim() || !form.code.trim()} className="rounded-xl bg-violet-700 px-5 py-3 font-black text-white disabled:opacity-50">إنشاء</button></div></form></div>}
    </div>
  );
}
