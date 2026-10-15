import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle,
  Cube,
  FolderSimple,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  SpinnerGap,
  Stack,
  Tag,
  WarningCircle,
  Wrench,
  X,
} from "@phosphor-icons/react";

import MezanComponentsProduction from "./MezanComponentsProduction";
import { getMezanComponentWorkspace } from "../services/mezanComponentCatalog";
import {
  createComponentCategory,
  activeResourcesForComponentCategory,
  generatedComponentGroupName,
  resourcesForComponentCategory,
  saveComponentGroup,
  saveResourceCategories,
  updateComponentCategory,
} from "../services/mezanComponentOrganization";

export { generatedComponentGroupName, resourcesForComponentCategory };

const EMPTY_GROUP = {
  id: "",
  category_id: "",
  group_kind: "service",
  resource_ids: [],
};

function CategoryBadges({ categoryIds = [], categories = [] }) {
  const byId = new Map(categories.map((row) => [String(row.id), row]));
  if (!categoryIds.length) {
    return <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-black text-slate-500">غير مصنف</span>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {categoryIds.map((categoryId) => (
        <span key={categoryId} className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-[11px] font-black text-violet-800">
          {byId.get(String(categoryId))?.name || "تصنيف"}
        </span>
      ))}
    </div>
  );
}

function CategoryAssignment({ resource, categories, saving, onClose, onSaved }) {
  const [selected, setSelected] = useState(() => (resource.category_ids || []).map(String));
  const [error, setError] = useState("");

  function toggle(categoryId) {
    setSelected((current) => (
      current.includes(categoryId)
        ? current.filter((value) => value !== categoryId)
        : [...current, categoryId]
    ));
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      await saveResourceCategories(resource.id, selected);
      await onSaved();
      onClose();
    } catch (saveError) {
      setError(saveError.message);
    }
  }

  return (
    <div className="fixed inset-0 z-[180] flex items-end justify-center bg-slate-950/60 sm:items-center sm:p-4">
      <form onSubmit={submit} className="w-full max-w-xl rounded-t-3xl bg-white p-5 shadow-2xl sm:rounded-3xl" data-testid="component-category-assignment">
        <div className="flex items-start justify-between gap-3">
          <div><h2 className="text-xl font-black">تصنيفات {resource.name}</h2><p className="mt-1 text-xs font-bold text-slate-500">يمكن ربط العنصر بأكثر من تصنيف، مثل الكيس في المطليات والملابس.</p></div>
          <button type="button" onClick={onClose} className="rounded-xl border p-2"><X /></button>
        </div>
        {error && <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-black text-rose-900">{error}</div>}
        <div className="mt-5 grid gap-2 sm:grid-cols-2">
          {categories.map((category) => {
            const checked = selected.includes(String(category.id));
            return (
              <label key={category.id} className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 ${checked ? "border-violet-500 bg-violet-50" : "border-slate-200"}`}>
                <input type="checkbox" checked={checked} onChange={() => toggle(String(category.id))} className="h-4 w-4 accent-violet-700" />
                <span className="font-black">{category.name}</span>
              </label>
            );
          })}
        </div>
        <div className="mt-6 flex justify-end gap-2"><button type="button" onClick={onClose} className="rounded-xl border px-4 py-3 font-black">إلغاء</button><button disabled={saving} className="rounded-xl bg-violet-700 px-5 py-3 font-black text-white disabled:opacity-50">حفظ التصنيفات</button></div>
      </form>
    </div>
  );
}

export default function MezanComponentsOrganization() {
  const [workspace, setWorkspace] = useState({ components: [], categories: [], groups: [], meta: {} });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("organization");
  const [selectedCategory, setSelectedCategory] = useState("");
  const [query, setQuery] = useState("");
  const [categoryName, setCategoryName] = useState("");
  const [editingCategory, setEditingCategory] = useState(null);
  const [assigningResource, setAssigningResource] = useState(null);
  const [groupDraft, setGroupDraft] = useState(EMPTY_GROUP);

  async function load({ quiet = false } = {}) {
    if (!quiet) setLoading(true);
    setError("");
    try {
      const result = await getMezanComponentWorkspace();
      setWorkspace(result || { components: [], categories: [], groups: [], meta: {} });
      if (!selectedCategory && result?.categories?.[0]?.id) {
        setSelectedCategory(String(result.categories[0].id));
      }
    } catch (loadError) {
      setError(loadError?.response?.data?.detail?.message || loadError?.message || "تعذر تحميل تنظيم المكونات والخدمات.");
    } finally {
      if (!quiet) setLoading(false);
    }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const categories = workspace.categories || [];
  const resources = workspace.components || [];
  const groups = workspace.groups || [];
  const selectedCategoryRow = categories.find((row) => String(row.id) === selectedCategory) || null;
  const visibleResources = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return resourcesForComponentCategory(resources, selectedCategory, "all").filter((resource) => (
      !needle || `${resource.name || ""} ${resource.code || ""}`.toLowerCase().includes(needle)
    ));
  }, [resources, selectedCategory, query]);
  const groupCandidates = useMemo(() => activeResourcesForComponentCategory(
    resources,
    groupDraft.category_id,
    groupDraft.group_kind,
  ), [resources, groupDraft.category_id, groupDraft.group_kind]);
  const groupName = generatedComponentGroupName(resources, groupDraft.resource_ids);
  const visibleGroups = groups.filter((group) => !selectedCategory || String(group.category_id) === selectedCategory);

  async function saveCategory(event) {
    event.preventDefault();
    const name = (editingCategory?.name ?? categoryName).trim();
    if (!name) return;
    setSaving(true);
    setError("");
    try {
      if (editingCategory) await updateComponentCategory(editingCategory.id, name);
      else await createComponentCategory(name);
      setCategoryName("");
      setEditingCategory(null);
      await load({ quiet: true });
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  }

  function toggleGroupResource(resourceId) {
    setGroupDraft((current) => ({
      ...current,
      resource_ids: current.resource_ids.includes(resourceId)
        ? current.resource_ids.filter((value) => value !== resourceId)
        : [...current.resource_ids, resourceId],
    }));
  }

  async function submitGroup(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await saveComponentGroup(groupDraft);
      setGroupDraft({ ...EMPTY_GROUP, category_id: groupDraft.category_id, group_kind: groupDraft.group_kind });
      await load({ quiet: true });
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  }

  function editGroup(group) {
    setGroupDraft({
      id: group.id,
      category_id: String(group.category_id),
      group_kind: group.group_kind,
      resource_ids: (group.resource_ids || []).map(String),
    });
    setSelectedCategory(String(group.category_id));
  }

  if (loading) {
    return <div className="flex min-h-[60vh] items-center justify-center" dir="rtl"><SpinnerGap size={32} className="animate-spin text-violet-700" /></div>;
  }

  return (
    <main className="space-y-5" dir="rtl" data-testid="components-organization-page">
      <header className="overflow-hidden rounded-3xl bg-gradient-to-l from-slate-950 via-violet-950 to-violet-700 p-6 text-white shadow-lg">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
          <div><div className="text-xs font-black text-violet-200">Mezan 2 · Component Organization</div><h1 className="mt-1 text-3xl font-black">المكونات والخدمات</h1><p className="mt-2 max-w-3xl text-sm font-semibold leading-6 text-violet-100">صنّف خدمات ومكونات المطليات والملابس، وأنشئ قروبات جاهزة باسم مشتق تلقائيًا من عناصرها.</p></div>
          <div className="rounded-2xl border border-white/20 bg-white/10 px-4 py-3 text-xs font-bold leading-6">التنظيم يطبق على الربط الجديد فقط، ولا يعيد احتساب الطلبات أو الفواتير السابقة.</div>
        </div>
      </header>

      <nav className="flex flex-wrap gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm">
        <button onClick={() => { setTab("organization"); load({ quiet: true }); }} className={`rounded-xl px-4 py-3 text-sm font-black ${tab === "organization" ? "bg-violet-700 text-white" : "text-slate-700"}`}><Stack className="ml-1 inline" />التصنيفات والقروبات</button>
        <button onClick={() => setTab("catalog")} className={`rounded-xl px-4 py-3 text-sm font-black ${tab === "catalog" ? "bg-violet-700 text-white" : "text-slate-700"}`}><Cube className="ml-1 inline" />إدارة العناصر والتكاليف</button>
      </nav>

      {error && <div className="flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-black text-rose-900"><WarningCircle size={21} className="mt-0.5 shrink-0" />{error}</div>}

      {tab === "catalog" ? <MezanComponentsProduction /> : (
        <>
          <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              <form onSubmit={saveCategory} className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row">
                <label className="min-w-0 flex-1"><span className="mb-1 block text-xs font-black text-slate-600">{editingCategory ? "تعديل اسم التصنيف" : "إضافة تصنيف تجهيز"}</span><input value={editingCategory?.name ?? categoryName} onChange={(event) => editingCategory ? setEditingCategory({ ...editingCategory, name: event.target.value }) : setCategoryName(event.target.value)} placeholder="مثال: مطليات أو ملابس" className="min-h-12 w-full rounded-xl border border-slate-200 px-3 font-bold outline-none focus:border-violet-500" /></label>
                <button disabled={saving || !(editingCategory?.name ?? categoryName).trim()} className="min-h-12 rounded-xl bg-violet-700 px-5 font-black text-white disabled:opacity-50"><Plus className="ml-1 inline" />{editingCategory ? "حفظ الاسم" : "إضافة التصنيف"}</button>
                {editingCategory && <button type="button" onClick={() => setEditingCategory(null)} className="min-h-12 rounded-xl border px-4 font-black">إلغاء</button>}
              </form>
              <div className="text-xs font-bold text-slate-500">العنصر الواحد يمكن أن يظهر في أكثر من تصنيف.</div>
            </div>
            <div className="mt-4 flex gap-2 overflow-x-auto pb-1">
              <button onClick={() => setSelectedCategory("")} className={`shrink-0 rounded-xl border px-4 py-2.5 text-sm font-black ${!selectedCategory ? "border-violet-700 bg-violet-700 text-white" : "border-slate-200"}`}>كل التصنيفات</button>
              {categories.map((category) => (
                <div key={category.id} className={`flex shrink-0 items-center rounded-xl border ${selectedCategory === String(category.id) ? "border-violet-700 bg-violet-50" : "border-slate-200"}`}>
                  <button onClick={() => setSelectedCategory(String(category.id))} className="px-4 py-2.5 text-sm font-black">{category.name} <span className="mr-1 text-[10px] text-slate-500">{category.resource_count || 0}</span></button>
                  <button onClick={() => setEditingCategory({ ...category })} className="border-r border-slate-200 p-2.5 text-violet-700" aria-label={`تعديل ${category.name}`}><PencilSimple /></button>
                </div>
              ))}
            </div>
          </section>

          <section className="grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(360px,0.75fr)]">
            <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="text-xl font-black"><Tag className="ml-1 inline text-violet-700" />عناصر {selectedCategoryRow?.name || "كل التصنيفات"}</h2><p className="mt-1 text-xs font-bold text-slate-500">تظهر هنا الخدمات والمكونات التابعة للتصنيف المحدد فقط.</p></div><label className="relative"><MagnifyingGlass className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="بحث…" className="min-h-11 rounded-xl border pr-10 pl-3 text-sm font-bold" /></label></div>
              {!visibleResources.length ? <div className="mt-4 rounded-2xl border border-dashed p-8 text-center text-sm font-bold text-slate-500">لا توجد عناصر ضمن هذا التصنيف.</div> : <div className="mt-4 grid gap-3 md:grid-cols-2">{visibleResources.map((resource) => (
                <article key={resource.id} className={`rounded-2xl border p-4 ${resource.status === "inactive" ? "border-rose-200 bg-rose-50/50" : "border-slate-200"}`}>
                  <div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><div className="font-black text-slate-950">{resource.name}</div>{resource.status === "inactive" && <span className="rounded-full bg-rose-100 px-2 py-1 text-[10px] font-black text-rose-700">موقوف</span>}</div><div className="mt-1 text-xs font-bold text-slate-500">{resource.track_inventory ? "مكوّن" : "خدمة"} · {resource.code || "بدون رمز"}</div></div><button onClick={() => setAssigningResource(resource)} className="rounded-xl border px-3 py-2 text-xs font-black text-violet-700">تعديل التصنيفات</button></div>
                  <div className="mt-3"><CategoryBadges categoryIds={resource.category_ids || []} categories={categories} /></div>
                </article>
              ))}</div>}
            </div>

            <form onSubmit={submitGroup} className="rounded-3xl border border-violet-200 bg-violet-50/40 p-5 shadow-sm" data-testid="component-group-editor">
              <div className="flex items-start justify-between gap-3"><div><h2 className="text-xl font-black"><FolderSimple className="ml-1 inline text-violet-700" />{groupDraft.id ? "تعديل القروب" : "إنشاء قروب"}</h2><p className="mt-1 text-xs font-bold leading-5 text-slate-500">اسم القروب يتكوّن تلقائيًا من أسماء العناصر وبنفس ترتيب اختيارها.</p></div>{groupDraft.id && <button type="button" onClick={() => setGroupDraft(EMPTY_GROUP)} className="rounded-xl border bg-white p-2"><X /></button>}</div>
              <div className="mt-4 grid gap-3">
                <label className="text-xs font-black text-slate-600">التصنيف<select value={groupDraft.category_id} onChange={(event) => setGroupDraft({ ...groupDraft, category_id: event.target.value, resource_ids: [] })} className="mt-1 min-h-12 w-full rounded-xl border bg-white px-3 text-sm font-black"><option value="">اختر التصنيف</option>{categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label>
                <label className="text-xs font-black text-slate-600">نوع القروب<select value={groupDraft.group_kind} onChange={(event) => setGroupDraft({ ...groupDraft, group_kind: event.target.value, resource_ids: [] })} className="mt-1 min-h-12 w-full rounded-xl border bg-white px-3 text-sm font-black"><option value="service">قروب خدمات</option><option value="component">قروب مكونات</option></select></label>
                <div className="rounded-xl border border-violet-200 bg-white p-3"><div className="text-xs font-black text-slate-500">اسم القروب التلقائي</div><div className="mt-1 min-h-6 font-black text-violet-900">{groupName || "اختر عنصرين على الأقل"}</div></div>
                <div className="max-h-72 space-y-2 overflow-y-auto rounded-xl border border-slate-200 bg-white p-2">
                  {!groupDraft.category_id ? <div className="p-4 text-center text-xs font-bold text-slate-500">اختر التصنيف أولًا.</div> : !groupCandidates.length ? <div className="p-4 text-center text-xs font-bold text-slate-500">لا توجد عناصر مناسبة داخل التصنيف.</div> : groupCandidates.map((resource) => {
                    const checked = groupDraft.resource_ids.includes(String(resource.id));
                    const order = groupDraft.resource_ids.indexOf(String(resource.id)) + 1;
                    return <label key={resource.id} className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 ${checked ? "border-violet-500 bg-violet-50" : "border-slate-200"}`}><input type="checkbox" checked={checked} onChange={() => toggleGroupResource(String(resource.id))} className="h-4 w-4 accent-violet-700" /><span className="min-w-0 flex-1 font-black">{resource.name}</span>{checked && <span className="flex h-6 w-6 items-center justify-center rounded-full bg-violet-700 text-xs font-black text-white">{order}</span>}</label>;
                  })}
                </div>
              </div>
              <button disabled={saving || !groupDraft.category_id || groupDraft.resource_ids.length < 2} className="mt-4 min-h-12 w-full rounded-xl bg-violet-700 px-5 font-black text-white disabled:opacity-50">{saving ? <SpinnerGap className="ml-1 inline animate-spin" /> : <CheckCircle className="ml-1 inline" weight="fill" />}{groupDraft.id ? "حفظ القروب" : "إنشاء القروب"}</button>
            </form>
          </section>

          <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-xl font-black"><Wrench className="ml-1 inline text-violet-700" />القروبات الجاهزة</h2>
            {!visibleGroups.length ? <div className="mt-4 rounded-2xl border border-dashed p-8 text-center text-sm font-bold text-slate-500">لا توجد قروبات ضمن التصنيف المحدد.</div> : <div className="mt-4 grid gap-3 lg:grid-cols-2">{visibleGroups.map((group) => (
              <article key={group.id} className="rounded-2xl border border-slate-200 p-4">
                <div className="flex items-start justify-between gap-3"><div><div className="font-black text-slate-950">{group.name}</div><div className="mt-1 text-xs font-bold text-slate-500">{group.group_kind === "service" ? "قروب خدمات" : "قروب مكونات"} · {group.resources?.length || 0} عناصر</div></div><button onClick={() => editGroup(group)} className="rounded-xl border px-3 py-2 text-xs font-black text-violet-700">تعديل</button></div>
                <div className="mt-3 flex flex-wrap gap-1.5">{(group.resources || []).map((resource) => <span key={resource.id} className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-black text-slate-700">{resource.name}</span>)}</div>
              </article>
            ))}</div>}
          </section>
        </>
      )}

      {assigningResource && <CategoryAssignment resource={assigningResource} categories={categories} saving={saving} onClose={() => setAssigningResource(null)} onSaved={() => load({ quiet: true })} />}
    </main>
  );
}
