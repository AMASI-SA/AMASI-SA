import { useEffect, useMemo, useState } from "react";
import {
  Cube,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  Power,
  ArrowCounterClockwise,
  SpinnerGap,
  WarningCircle,
  Wrench,
} from "@phosphor-icons/react";
import {
  addMezanComponentPreview,
  filterMezanComponents,
  getMezanComponentWorkspace,
  saveMezanComponentPreviewCost,
  setMezanComponentStatus,
  summarizeMezanComponents,
  updateMezanComponent,
} from "../services/mezanComponentCatalog";
import {
  changeComponentKind,
  componentFormCanSave,
  componentFormFromRow,
  newComponentForm,
  toggleComponentCategory,
} from "../lib/componentCreationRules";

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
  const [workspace, setWorkspace] = useState({ components: [], categories: [], products: [], meta: {} });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("active");
  const [selectedId, setSelectedId] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [form, setForm] = useState(() => newComponentForm());
  const [editForm, setEditForm] = useState(() => newComponentForm());
  const [saving, setSaving] = useState(false);
  const [statusBusy, setStatusBusy] = useState(false);
  const [costDraft, setCostDraft] = useState("");

  async function load(preferredId = "") {
    setLoading(true);
    setError("");
    try {
      const result = await getMezanComponentWorkspace();
      const components = result?.components || [];
      setWorkspace(result || { components: [], categories: [], products: [], meta: {} });
      const nextId = preferredId || selectedId || components[0]?.id || "";
      setSelectedId(nextId);
      const selected = components.find((row) => row.id === nextId) || components[0];
      setCostDraft(selected?.track_inventory
        ? (selected?.initial_unit_cost ?? selected?.reference_cost?.amount ?? "")
        : (selected?.reference_cost?.amount ?? ""));
      setEditForm(componentFormFromRow(selected));
    } catch (err) {
      setError(err?.response?.data?.detail?.message || err?.message || "تعذر تحميل كتالوج المكونات.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const components = useMemo(() => workspace.components || [], [workspace.components]);
  const categories = useMemo(() => workspace.categories || [], [workspace.categories]);
  const visible = useMemo(
    () => filterMezanComponents(components, { query, filter: "all", status: statusFilter }),
    [components, query, statusFilter],
  );
  const summary = useMemo(() => summarizeMezanComponents(components), [components]);
  const selected = visible.find((row) => row.id === selectedId) || visible[0] || null;
  const selectedCostCopy = costCopy(selected);

  useEffect(() => {
    if (!selected || selected.id === selectedId) return;
    setSelectedId(selected.id);
    setCostDraft(selected.track_inventory
      ? (selected.initial_unit_cost ?? selected.reference_cost?.amount ?? "")
      : (selected.reference_cost?.amount ?? ""));
    setEditForm(componentFormFromRow(selected));
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  function selectComponent(row) {
    setSelectedId(row.id);
    setCostDraft(row.track_inventory
      ? (row.initial_unit_cost ?? row.reference_cost?.amount ?? "")
      : (row.reference_cost?.amount ?? ""));
    setEditForm(componentFormFromRow(row));
    setError("");
  }

  function serviceError(code, fallback) {
    const labels = {
      component_code_exists: "الرمز مستخدم في مكوّن آخر.",
      component_category_required: "اختر تصنيف تجهيز واحدًا على الأقل.",
      component_category_not_found: "أحد التصنيفات المحددة لم يعد موجودًا.",
      component_category_used_by_group: "لا يمكن إزالة التصنيف لأن العنصر مستخدم داخل مجموعة تابعة له.",
      purchase_cost_authoritative: "تكلفة هذا المكوّن معتمدة من فاتورة شراء ولا يمكن تعديلها يدويًا.",
      invalid_cost: "أدخل تكلفة صحيحة تساوي صفرًا أو أكثر.",
      invalid_component: "أدخل الاسم والرمز.",
      invalid_component_status: "حالة المكوّن أو الخدمة غير صحيحة.",
      component_inactive: "المكوّن أو الخدمة موقوفة. أعد تفعيلها أولًا.",
    };
    return labels[code] || fallback;
  }

  async function createComponent(event) {
    event.preventDefault();
    if (!componentFormCanSave(form)) {
      setError("أدخل الاسم والرمز واختر تصنيف تجهيز واحدًا على الأقل.");
      return;
    }
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
      setForm(newComponentForm());
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
    if (!selected || !componentFormCanSave(editForm)) {
      setError("أدخل الاسم والرمز واختر تصنيف تجهيز واحدًا على الأقل.");
      return;
    }
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
    if (!selected || selected.status === "inactive" || selectedCostCopy.locked) return;
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

  async function toggleStatus() {
    if (!selected || statusBusy) return;
    const stopping = selected.status !== "inactive";
    if (stopping && typeof window !== "undefined" && !window.confirm(
      "سيتم إيقاف هذا العنصر وإخفاؤه من ربط المنتجات الجديدة في ميزان والتطبيق، مع حفظ الروابط والسجلات السابقة. هل تريد المتابعة؟",
    )) return;
    setStatusBusy(true);
    setError("");
    try {
      const result = await setMezanComponentStatus(
        selected.id,
        stopping ? "inactive" : "active",
      );
      if (!result.ok) throw new Error(serviceError(result.code, "تعذر تغيير حالة العنصر"));
      await load(selected.id);
    } catch (err) {
      setError(err?.message || "تعذر تغيير حالة المكوّن أو الخدمة.");
    } finally {
      setStatusBusy(false);
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
    const canSave = componentFormCanSave(values);
    return (
      <div className="fixed inset-0 z-[160] flex items-center justify-center bg-slate-950/50 p-4">
        <form onSubmit={isEdit ? updateComponent : createComponent} className="max-h-[94vh] w-full max-w-lg overflow-y-auto rounded-3xl bg-white p-6 shadow-2xl">
          <h2 className="text-xl font-black">{isEdit ? "تعديل المكوّن أو الخدمة" : "إضافة مكوّن أو خدمة"}</h2>
          <div className="mt-5 grid gap-3">
            <label className="text-xs font-bold text-slate-500">الاسم<input value={values.name} onChange={(event) => setValues({ ...values, name: event.target.value })} placeholder="مثال: تطريز الاسم" className="mt-1 w-full rounded-xl border p-3 text-slate-900" /></label>
            <label className="text-xs font-bold text-slate-500">الرمز<input value={values.code} onChange={(event) => setValues({ ...values, code: event.target.value })} placeholder="مثال: EMBROIDERY_NAME" className="mt-1 w-full rounded-xl border p-3 text-slate-900" /></label>
            <label className="text-xs font-bold text-slate-500">النوع<select value={values.kind} onChange={(event) => setValues(changeComponentKind(values, event.target.value))} className="mt-1 w-full rounded-xl border p-3 text-slate-900"><option value="service">خدمة عمل</option><option value="stock_component">مكوّن مخزني</option></select></label>

            <fieldset className="rounded-2xl border border-slate-200 p-3">
              <legend className="px-2 text-xs font-black text-slate-600">تصنيف التجهيز — إلزامي</legend>
              {!categories.length ? (
                <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold leading-6 text-amber-900"><WarningCircle size={19} className="mt-0.5 shrink-0" />أنشئ تصنيفًا مثل «مطليات» أو «ملابس» من تبويب التصنيفات والقروبات أولًا.</div>
              ) : (
                <div className="grid gap-2 sm:grid-cols-2">
                  {categories.map((category) => {
                    const checked = (values.category_ids || []).map(String).includes(String(category.id));
                    return (
                      <label key={category.id} className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 ${checked ? "border-violet-500 bg-violet-50" : "border-slate-200"}`}>
                        <input type="checkbox" checked={checked} onChange={() => setValues(toggleComponentCategory(values, category.id))} className="h-4 w-4 accent-violet-700" />
                        <span className="font-black text-slate-900">{category.name}</span>
                      </label>
                    );
                  })}
                </div>
              )}
              <p className="mt-2 text-[11px] font-bold leading-5 text-slate-500">يمكن اختيار أكثر من تصنيف، لكن لا يمكن حفظ العنصر دون تصنيف واحد على الأقل.</p>
            </fieldset>

            {!stock && <label className="flex items-start gap-3 rounded-xl border border-violet-200 bg-violet-50 p-3 text-sm font-bold text-violet-950"><input type="checkbox" checked={values.requires_preparation} onChange={(event) => setValues({ ...values, requires_preparation: event.target.checked })} className="mt-1" /><span>تمنع شحن المنتج حتى تكتمل الخدمة<span className="mt-1 block text-xs font-normal leading-5 text-violet-700">مفعّلة تلقائيًا للخدمات الجديدة مثل القص والطلاء والتطريز والطباعة، ويمكن إلغاؤها للخدمات التي لا توقف الشحن.</span></span></label>}
            <label className="text-xs font-bold text-slate-500">{stock ? "التكلفة الأولية التقديرية" : "تكلفة الخدمة الحالية"}<input type="number" min="0" step="0.01" value={values.unit_cost} onChange={(event) => setValues({ ...values, unit_cost: event.target.value })} placeholder="التكلفة بالريال" className="mt-1 w-full rounded-xl border p-3 text-slate-900" /></label>
            {stock && <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-900">هذه تكلفة أولية فقط. بعد اعتماد فاتورة شراء للمكوّن، تعتمد تكلفة الشراء الفعلية بدلها تلقائيًا.</div>}
            <label className="text-xs font-bold text-slate-500">ملاحظات<textarea value={values.description} onChange={(event) => setValues({ ...values, description: event.target.value })} className="mt-1 min-h-20 w-full rounded-xl border p-3 text-slate-900" /></label>
          </div>
          <div className="mt-6 flex justify-end gap-2"><button type="button" onClick={() => isEdit ? setEditOpen(false) : setAddOpen(false)} className="rounded-xl border px-4 py-3 font-bold">إلغاء</button><button disabled={saving || !canSave} className="rounded-xl bg-violet-700 px-5 py-3 font-black text-white disabled:opacity-50">{saving ? "جارٍ الحفظ…" : isEdit ? "حفظ التعديلات" : "إنشاء"}</button></div>
        </form>
      </div>
    );
  };

  return (
    <div className="space-y-5" dir="rtl">
      <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm"><div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between"><div><h1 className="text-2xl font-black text-slate-950">مكونات وتكاليف Mezan OS</h1><p className="mt-1 text-sm text-slate-500">كل خدمة أو مكوّن يجب أن يتبع تصنيف تجهيز واحدًا على الأقل، ويمكن مشاركته بين عدة تصنيفات.</p></div><button onClick={() => { setForm(newComponentForm()); setAddOpen(true); }} className="rounded-xl bg-violet-700 px-5 py-3 font-black text-white"><Plus className="ml-2 inline" />إضافة مكوّن أو خدمة</button></div></section>

      {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-800">{error}</div>}

      <section className="grid gap-3 sm:grid-cols-4"><div className="rounded-2xl border bg-white p-4"><div className="text-xs text-slate-500">إجمالي العناصر</div><div className="mt-1 text-2xl font-black">{summary.total}</div></div><div className="rounded-2xl border bg-white p-4"><div className="text-xs text-slate-500">العناصر الفعالة</div><div className="mt-1 text-2xl font-black text-emerald-700">{summary.active}</div></div><div className="rounded-2xl border bg-white p-4"><div className="text-xs text-slate-500">العناصر الموقوفة</div><div className="mt-1 text-2xl font-black text-rose-700">{summary.inactive}</div></div><div className="rounded-2xl border bg-white p-4"><div className="text-xs text-slate-500">تحتاج تكلفة</div><div className="mt-1 text-2xl font-black">{summary.missing_cost}</div></div></section>

      {!components.length ? (
        <section className="rounded-3xl border border-dashed border-violet-300 bg-violet-50/40 p-10 text-center"><Cube size={56} className="mx-auto text-violet-600" /><h2 className="mt-4 text-xl font-black">ابدأ بإضافة أول مكوّن أو خدمة</h2><p className="mx-auto mt-2 max-w-xl text-sm leading-7 text-slate-600">مثال: تطريز الاسم، نقش الاسم، طلاء ذهبي، تغليف هدية، علبة، سلسلة أو خامة.</p><button onClick={() => { setForm(newComponentForm()); setAddOpen(true); }} className="mt-5 rounded-xl bg-violet-700 px-6 py-3 font-black text-white"><Plus className="ml-2 inline" />إضافة أول مكوّن</button></section>
      ) : (
        <section className="grid gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
          <aside className="overflow-hidden rounded-3xl border bg-white shadow-sm"><div className="border-b p-4"><label className="flex items-center gap-2 rounded-xl border bg-slate-50 px-3 py-2"><MagnifyingGlass className="text-slate-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث بالاسم أو الرمز…" className="min-w-0 flex-1 bg-transparent outline-none" /></label><div className="mt-3 grid grid-cols-3 gap-1 rounded-xl bg-slate-100 p-1">{[["active", "الفعالة"], ["inactive", "الموقوفة"], ["all", "الكل"]].map(([value, label]) => <button key={value} type="button" onClick={() => setStatusFilter(value)} className={`rounded-lg px-2 py-2 text-xs font-black ${statusFilter === value ? "bg-white text-violet-800 shadow-sm" : "text-slate-500"}`}>{label}</button>)}</div></div><div className="max-h-[650px] divide-y overflow-auto">{visible.map((row) => <button key={row.id} onClick={() => selectComponent(row)} className={`w-full p-4 text-right ${selected?.id === row.id ? "bg-violet-50" : "hover:bg-slate-50"}`}><div className="flex items-center justify-between gap-2"><div className="font-black">{row.name}</div>{row.status === "inactive" && <span className="rounded-full bg-rose-100 px-2 py-1 text-[10px] font-black text-rose-700">موقوف</span>}</div><div className="mt-1 text-xs text-slate-500">{row.code} · {row.track_inventory ? "مكوّن مخزني" : "خدمة عمل"}</div></button>)}{!visible.length && <div className="p-8 text-center text-sm font-bold text-slate-400">لا توجد عناصر في هذه القائمة.</div>}</div></aside>

          {selected && <main className="space-y-5 rounded-3xl border bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-4"><div className="flex items-center gap-3"><div className="rounded-2xl bg-violet-100 p-3 text-violet-700">{selected.track_inventory ? <Cube size={28} /> : <Wrench size={28} />}</div><div><div className="flex items-center gap-2"><h2 className="text-xl font-black">{selected.name}</h2>{selected.status === "inactive" && <span className="rounded-full bg-rose-100 px-2.5 py-1 text-[10px] font-black text-rose-700">موقوف</span>}</div><p className="text-xs text-slate-500">{selected.code}</p></div></div><div className="flex gap-2"><button onClick={() => { setEditForm(componentFormFromRow(selected)); setEditOpen(true); }} className="rounded-xl border border-violet-300 px-4 py-2 text-sm font-black text-violet-800"><PencilSimple className="ml-1 inline" />تعديل</button><button onClick={toggleStatus} disabled={statusBusy} className={`rounded-xl border px-4 py-2 text-sm font-black disabled:opacity-50 ${selected.status === "inactive" ? "border-emerald-300 text-emerald-800" : "border-rose-300 text-rose-800"}`}>{selected.status === "inactive" ? <ArrowCounterClockwise className="ml-1 inline" /> : <Power className="ml-1 inline" />}{statusBusy ? "جارٍ الحفظ…" : selected.status === "inactive" ? "إعادة التفعيل" : "إيقاف وإخفاء"}</button></div></div>
            {selected.status === "inactive" && <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold leading-7 text-rose-900">هذا العنصر موقوف: لا يظهر في التطبيق أو قوائم ربط المنتجات الجديدة. بقيت روابط المنتجات السابقة وتكاليفها وسجلات الطلبات محفوظة دون حذف، ويمكن إعادة تفعيله.</div>}
            <section className="rounded-2xl border p-4"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-black">{selectedCostCopy.title}</h3><span className={`rounded-full px-3 py-1 text-xs font-bold ${selectedCostCopy.locked ? "bg-emerald-100 text-emerald-800" : selected.track_inventory ? "bg-amber-100 text-amber-800" : "bg-violet-100 text-violet-800"}`}>{selectedCostCopy.badge}</span></div><p className="mt-1 text-xs leading-6 text-slate-500">{selectedCostCopy.description}</p><div className="mt-4 flex gap-3"><input disabled={selected.status === "inactive" || selectedCostCopy.locked} type="number" min="0" step="0.01" value={costDraft} onChange={(event) => setCostDraft(event.target.value)} className="min-w-0 flex-1 rounded-xl border p-3 disabled:bg-slate-100" placeholder="التكلفة بالريال" /><button onClick={saveCost} disabled={saving || selected.status === "inactive" || selectedCostCopy.locked} className="rounded-xl bg-emerald-700 px-5 font-black text-white disabled:opacity-50">حفظ</button></div></section>
            <section className="rounded-2xl border p-4"><h3 className="font-black">المنتجات المرتبطة</h3><p className="mt-1 text-sm text-slate-500">{selected.product_usages?.length || 0} ربط على مستوى المنتج أو خياراته.</p>{(selected.product_usages || []).map((usage) => <div key={usage.id} className="mt-3 rounded-xl bg-slate-50 p-3"><div className="font-bold">{usage.product_name}</div><div className="text-xs text-slate-500">{usage.source === "product" ? "مرتبط بالمنتج مباشرة" : `${usage.condition?.option_name}: ${usage.condition?.value_name}`}</div></div>)}</section>
          </main>}
        </section>
      )}

      {addOpen && formModal("add")}
      {editOpen && formModal("edit")}
    </div>
  );
}
