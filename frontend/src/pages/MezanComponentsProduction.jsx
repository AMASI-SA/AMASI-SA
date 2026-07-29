import { useEffect, useMemo, useState } from "react";
import { Cube, MagnifyingGlass, PencilSimple, Plus, SpinnerGap, Wrench } from "@phosphor-icons/react";
import {
  addMezanComponentPreview,
  filterMezanComponents,
  getMezanComponentWorkspace,
  saveMezanComponentPreviewCost,
  summarizeMezanComponents,
  updateMezanComponent,
} from "../services/mezanComponentCatalog";

const EMPTY_FORM = {
  name: "", code: "", kind: "service", unit: "job", unit_cost: "",
  description: "", requires_preparation: false,
};

function componentForm(component) {
  if (!component) return EMPTY_FORM;
  return {
    name: component.name || "",
    code: component.code || "",
    kind: component.track_inventory ? "stock_component" : "service",
    unit: component.unit || (component.track_inventory ? "piece" : "job"),
    unit_cost: component.track_inventory
      ? (component.initial_unit_cost ?? component.reference_cost?.amount ?? "")
      : (component.reference_cost?.amount ?? ""),
    description: component.description || "",
    requires_preparation: !component.track_inventory && component.requires_preparation === true,
  };
}

function costCopy(component) {
  if (!component?.track_inventory) {
    return {
      title: "تكلفة خدمة العمل",
      description: "هذه تكلفة مركزية حالية للخدمة، وتنعكس على جميع خيارات المنتجات المرتبطة للطلبات القادمة.",
      locked: false,
      badge: "تكلفة تشغيلية",
    };
  }
  if (component.cost_source === "purchase_invoice" && component.cost_authoritative) {
    return {
      title: "تكلفة المخزون المعتمدة",
      description: "هذه التكلفة مستمدة من فاتورة شراء معتمدة، ولا يمكن استبدالها يدويًا من هنا.",
      locked: true,
      badge: "فاتورة شراء",
    };
  }
  return {
    title: "التكلفة الأولية للمكوّن",
    description: "قيمة مؤقتة لبدء الحساب. عند اعتماد أول فاتورة شراء لهذا المكوّن تصبح تكلفة الشراء هي المصدر الرسمي.",
    locked: false,
    badge: "أولية مؤقتة",
  };
}

export default function MezanComponentsProduction() {
  const [workspace, setWorkspace] = useState({ components: [], products: [], meta: {} });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editForm, setEditForm] = useState(EMPTY_FORM);
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
      setCostDraft(selected?.track_inventory
        ? (selected?.initial_unit_cost ?? selected?.reference_cost?.amount ?? "")
        : (selected?.reference_cost?.amount ?? ""));
      setEditForm(componentForm(selected));
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
  const selectedCostCopy = costCopy(selected);

  function selectComponent(row) {
    setSelectedId(row.id);
    setCostDraft(row.track_inventory ? (row.initial_unit_cost ?? row.reference_cost?.amount ?? "") : (row.reference_cost?.amount ?? ""));
    setEditForm(componentForm(row));
    setError("");
  }

  function serviceError(code, fallback) {
    const labels = {
      component_code_exists: "الرمز مستخدم في مكوّن آخر.",
      purchase_cost_authoritative: "تكلفة هذا المكوّن معتمدة من فاتورة شراء ولا يمكن تعديلها يدويًا.",
      invalid_cost: "أدخل تكلفة صحيحة تساوي صفرًا أو أكثر.",
      invalid_component: "أدخل الاسم والرمز.",
    };
    return labels[code] || fallback;
  }

  async function createComponent(event) {
    event.preventDefault();
    if (!form.name.trim() || !form.code.trim()) return;
    setSaving(true);
    setError("");
    try {
      const result = await addMezanComponentPreview({
        ...form,
        name: form.name.trim(),
        code: form.code.trim().toUpperCase(),
        unit_cost: form.unit_cost === "" ? null : Number(form.unit_cost),
      });
      if (!result.ok) throw new Error(serviceError(result.code, "تعذر إنشاء المكوّن"));
      setForm(EMPTY_FORM);
      setAddOpen(false);
      await load(result.resource?.id || "");
    } catch (err) {
      setError(err?.message || "تعذر إنشاء المكوّن.");
    } finally {
      setSaving(false);
    }
  }

  async function updateComponent(event) {
    event.preventDefault();
    if (!selected || !editForm.name.trim() || !editForm.code.trim()) return;
    setSaving(true);
    setError("");
    try {
      const result = await updateMezanComponent(selected.id, {
        ...editForm,
        name: editForm.name.trim(),
        code: editForm.code.trim().toUpperCase(),
        unit_cost: editForm.unit_cost === "" ? null : Number(editForm.unit_cost),
      });
      if (!result.ok) throw new Error(serviceError(result.code, "تعذر تعديل المكوّن"));
      setEditOpen(false);
      await load(selected.id);
    } catch (err) {
      setError(err?.message || "تعذر تعديل المكوّن.");
    } finally {
      setSaving(false);
    }
  }

  async function saveCost() {
    if (!selected || selectedCostCopy.locked) return;
    setSaving(true);
    setError("");
    try {
      const amount = costDraft === "" ? null : Number(costDraft);
      const result = await saveMezanComponentPreviewCost(selected.id, amount);
      if (!result.ok) throw new Error(serviceError(result.code, "تعذر حفظ التكلفة"));
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

  const formModal = (mode) => {
    const isEdit = mode === "edit";
    const values = isEdit ? editForm : form;
    const setValues = isEdit ? setEditForm : setForm;
    const stock = values.kind === "stock_component";
    return (
      <div className="fixed inset-0 z-[160] flex items-center justify-center bg-slate-950/50 p-4">
        <form onSubmit={isEdit ? updateComponent : createComponent} className="w-full max-w-lg rounded-3xl bg-white p-6 shadow-2xl">
          <h2 className="text-xl font-black">{isEdit ? "تعديل المكوّن أو الخدمة" : "إضافة مكوّن أو خدمة"}</h2>
          <div className="mt-5 grid gap-3">
            <label className="text-xs font-bold text-slate-500">الاسم<input value={values.name} onChange={(e) => setValues({ ...values, name: e.target.value })} placeholder="مثال: تطريز الاسم" className="mt-1 w-full rounded-xl border p-3 text-slate-900" /></label>
            <label className="text-xs font-bold text-slate-500">الرمز<input value={values.code} onChange={(e) => setValues({ ...values, code: e.target.value })} placeholder="مثال: EMBROIDERY_NAME" className="mt-1 w-full rounded-xl border p-3 text-slate-900" /></label>
            <label className="text-xs font-bold text-slate-500">النوع<select value={values.kind} onChange={(e) => setValues({ ...values, kind: e.target.value, unit: e.target.value === "stock_component" ? "piece" : "job", requires_preparation: e.target.value === "service" ? values.requires_preparation : false })} className="mt-1 w-full rounded-xl border p-3 text-slate-900"><option value="service">خدمة عمل</option><option value="stock_component">مكوّن مخزني</option></select></label>
            {!stock && <label className="flex items-start gap-3 rounded-xl border border-violet-200 bg-violet-50 p-3 text-sm font-bold text-violet-950"><input type="checkbox" checked={values.requires_preparation} onChange={(e) => setValues({ ...values, requires_preparation: e.target.checked })} className="mt-1" /><span>هذه الخدمة تتطلب تجهيزًا قبل الشحن<span className="mt-1 block text-xs font-normal leading-5 text-violet-700">مثل القص أو التطريز. عند اختيارها تتجاوز إعداد «شحن فوري» لذلك المنتج في الطلب.</span></span></label>}
            <label className="text-xs font-bold text-slate-500">{stock ? "التكلفة الأولية التقديرية" : "تكلفة الخدمة الحالية"}<input type="number" min="0" step="0.01" value={values.unit_cost} onChange={(e) => setValues({ ...values, unit_cost: e.target.value })} placeholder="التكلفة بالريال" className="mt-1 w-full rounded-xl border p-3 text-slate-900" /></label>
            {stock && <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-900">هذه تكلفة أولية فقط. بعد اعتماد فاتورة شراء للمكوّن، تعتمد تكلفة الشراء الفعلية بدلها تلقائيًا.</div>}
            <label className="text-xs font-bold text-slate-500">ملاحظات<textarea value={values.description} onChange={(e) => setValues({ ...values, description: e.target.value })} className="mt-1 min-h-20 w-full rounded-xl border p-3 text-slate-900" /></label>
          </div>
          <div className="mt-6 flex justify-end gap-2"><button type="button" onClick={() => isEdit ? setEditOpen(false) : setAddOpen(false)} className="rounded-xl border px-4 py-3 font-bold">إلغاء</button><button disabled={saving || !values.name.trim() || !values.code.trim()} className="rounded-xl bg-violet-700 px-5 py-3 font-black text-white disabled:opacity-50">{saving ? "جارٍ الحفظ…" : isEdit ? "حفظ التعديلات" : "إنشاء"}</button></div>
        </form>
      </div>
    );
  };

  return (
    <div className="space-y-5" dir="rtl">
      <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm"><div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between"><div><h1 className="text-2xl font-black text-slate-950">مكونات وتكاليف Mezan OS</h1><p className="mt-1 text-sm text-slate-500">مكوّن مركزي واحد يمكن ربطه بخيارات مئات المنتجات، وتعديل تكلفته من مكان واحد.</p></div><button onClick={() => { setForm(EMPTY_FORM); setAddOpen(true); }} className="rounded-xl bg-violet-700 px-5 py-3 font-black text-white"><Plus className="ml-2 inline" />إضافة مكوّن أو خدمة</button></div></section>

      {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-800">{error}</div>}

      <section className="grid gap-3 sm:grid-cols-3"><div className="rounded-2xl border bg-white p-4"><div className="text-xs text-slate-500">إجمالي المكونات</div><div className="mt-1 text-2xl font-black">{summary.total}</div></div><div className="rounded-2xl border bg-white p-4"><div className="text-xs text-slate-500">خدمات العمل</div><div className="mt-1 text-2xl font-black">{summary.labor_services}</div></div><div className="rounded-2xl border bg-white p-4"><div className="text-xs text-slate-500">تحتاج تكلفة</div><div className="mt-1 text-2xl font-black">{summary.missing_cost}</div></div></section>

      {!components.length ? (
        <section className="rounded-3xl border border-dashed border-violet-300 bg-violet-50/40 p-10 text-center"><Cube size={56} className="mx-auto text-violet-600" /><h2 className="mt-4 text-xl font-black">ابدأ بإضافة أول مكوّن أو خدمة</h2><p className="mx-auto mt-2 max-w-xl text-sm leading-7 text-slate-600">مثال: تطريز الاسم، نقش الاسم، طلاء ذهبي، تغليف هدية، علبة، سلسلة أو خامة.</p><button onClick={() => setAddOpen(true)} className="mt-5 rounded-xl bg-violet-700 px-6 py-3 font-black text-white"><Plus className="ml-2 inline" />إضافة أول مكوّن</button></section>
      ) : (
        <section className="grid gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
          <aside className="overflow-hidden rounded-3xl border bg-white shadow-sm"><div className="border-b p-4"><label className="flex items-center gap-2 rounded-xl border bg-slate-50 px-3 py-2"><MagnifyingGlass className="text-slate-400" /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="ابحث بالاسم أو الرمز…" className="min-w-0 flex-1 bg-transparent outline-none" /></label></div><div className="max-h-[650px] overflow-auto divide-y">{visible.map((row) => <button key={row.id} onClick={() => selectComponent(row)} className={`w-full p-4 text-right ${selected?.id === row.id ? "bg-violet-50" : "hover:bg-slate-50"}`}><div className="font-black">{row.name}</div><div className="mt-1 text-xs text-slate-500">{row.code} · {row.track_inventory ? "مكوّن مخزني" : "خدمة عمل"}</div></button>)}</div></aside>

          {selected && <main className="space-y-5 rounded-3xl border bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between gap-3 border-b pb-4"><div className="flex items-center gap-3"><div className="rounded-2xl bg-violet-100 p-3 text-violet-700">{selected.track_inventory ? <Cube size={28} /> : <Wrench size={28} />}</div><div><h2 className="text-xl font-black">{selected.name}</h2><p className="text-xs text-slate-500">{selected.code}</p></div></div><button onClick={() => { setEditForm(componentForm(selected)); setEditOpen(true); }} className="rounded-xl border border-violet-300 px-4 py-2 text-sm font-black text-violet-800"><PencilSimple className="ml-1 inline" />تعديل</button></div>
            <section className="rounded-2xl border p-4"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-black">{selectedCostCopy.title}</h3><span className={`rounded-full px-3 py-1 text-xs font-bold ${selectedCostCopy.locked ? "bg-emerald-100 text-emerald-800" : selected.track_inventory ? "bg-amber-100 text-amber-800" : "bg-violet-100 text-violet-800"}`}>{selectedCostCopy.badge}</span></div><p className="mt-1 text-xs leading-6 text-slate-500">{selectedCostCopy.description}</p><div className="mt-4 flex gap-3"><input disabled={selectedCostCopy.locked} type="number" min="0" step="0.01" value={costDraft} onChange={(e) => setCostDraft(e.target.value)} className="min-w-0 flex-1 rounded-xl border p-3 disabled:bg-slate-100" placeholder="التكلفة بالريال" /><button onClick={saveCost} disabled={saving || selectedCostCopy.locked} className="rounded-xl bg-emerald-700 px-5 font-black text-white disabled:opacity-50">حفظ</button></div></section>
            <section className="rounded-2xl border p-4"><h3 className="font-black">المنتجات المرتبطة</h3><p className="mt-1 text-sm text-slate-500">{selected.product_usages?.length || 0} ربط على مستوى المنتج أو خياراته.</p>{(selected.product_usages || []).map((usage) => <div key={usage.id} className="mt-3 rounded-xl bg-slate-50 p-3"><div className="font-bold">{usage.product_name}</div><div className="text-xs text-slate-500">{usage.source === "product" ? "مرتبط بالمنتج مباشرة" : `${usage.condition?.option_name}: ${usage.condition?.value_name}`}</div></div>)}</section>
          </main>}
        </section>
      )}

      {addOpen && formModal("add")}
      {editOpen && formModal("edit")}
    </div>
  );
}
