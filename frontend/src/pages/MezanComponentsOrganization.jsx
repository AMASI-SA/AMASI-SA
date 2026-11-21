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
import InventorySuiteFrame, { InventoryPageHeader, InventorySheet } from "../components/inventory/suite/InventorySuiteFrame";
import "./inventory-catalog-resources.css";
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
        <span key={categoryId} className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[11px] font-black text-emerald-800">
          {byId.get(String(categoryId))?.name || "تصنيف"}
        </span>
      ))}
    </div>
  );
}

function CategoryAssignment({ resource, categories, saving, onClose, onSaved }) {
  const [selected, setSelected] = useState(() => (resource.category_ids || []).map(String));
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const busy = saving || submitting;

  function toggle(categoryId) {
    setSelected((current) => current.includes(categoryId) ? current.filter((value) => value !== categoryId) : [...current, categoryId]);
  }

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    setError("");
    setSubmitting(true);
    try {
      await saveResourceCategories(resource.id, selected);
      await onSaved();
      onClose();
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSubmitting(false);
    }
  }

  return <InventorySheet open onClose={onClose} busy={busy} title={"تصنيفات " + resource.name} description="يمكن ربط العنصر بأكثر من تصنيف تجهيز." footer={<><button type="button" disabled={busy} onClick={onClose} className="mz-catalog-secondary">إلغاء</button><button type="submit" form="mezan-resource-categories" disabled={busy} className="mz-catalog-primary">{busy ? "جارٍ الحفظ…" : "حفظ التصنيفات"}</button></>}>
    <form id="mezan-resource-categories" onSubmit={submit} className="mz-component-fields" data-testid="component-category-assignment">
      {error && <div role="alert" className="mz-catalog-notice mz-catalog-error">{error}</div>}
      <fieldset disabled={busy} className="mz-component-categories"><legend>تصنيفات التجهيز</legend><div>{categories.map((category) => {
        const checked = selected.includes(String(category.id));
        return <label key={category.id} className={checked ? "is-checked" : ""}><input type="checkbox" checked={checked} onChange={() => toggle(String(category.id))}/>{category.name}</label>;
      })}</div>{!categories.length && <p className="mz-catalog-muted">أضف تصنيف تجهيز أولًا.</p>}</fieldset>
    </form>
  </InventorySheet>;
}

export default function MezanComponentsOrganization({ accessMode = null }) {
  const canWrite = accessMode !== "read";
  const [workspace, setWorkspace] = useState({ components: [], categories: [], groups: [], meta: {} });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState(() => accessMode ? "organization" : "catalog");
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
    if (!canWrite || saving) return;
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
    if (!canWrite || saving) return;
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
    return <InventorySuiteFrame active="components"><div className="mz-catalog-empty" role="status"><SpinnerGap size={32} className="animate-spin" />جارٍ تحميل المكونات…</div></InventorySuiteFrame>;
  }

  return (
    <InventorySuiteFrame active="components"><main className="mz-components-organization space-y-5" dir="rtl" data-testid="components-organization-page">
      {tab === "organization" && <InventoryPageHeader title="تصنيفات المكونات والمجموعات" description="نظّم المكونات والخدمات حسب التجهيز، واجمع العناصر المتكررة في مجموعة واحدة." />}


      <nav className="flex flex-wrap gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm">
        <button onClick={() => { setTab("organization"); load({ quiet: true }); }} className={`rounded-xl px-4 py-3 text-sm font-black ${tab === "organization" ? "bg-emerald-700 text-white" : "text-slate-700"}`}><Stack className="ml-1 inline" />التصنيفات والمجموعات</button>
        {!accessMode && <button onClick={() => setTab("catalog")} className={`rounded-xl px-4 py-3 text-sm font-black ${tab === "catalog" ? "bg-emerald-700 text-white" : "text-slate-700"}`}><Cube className="ml-1 inline" />إدارة العناصر والتكاليف</button>}
      </nav>

      {error && <div className="flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-black text-rose-900"><WarningCircle size={21} className="mt-0.5 shrink-0" />{error}</div>}

      {tab === "catalog" && !accessMode ? <MezanComponentsProduction embedded /> : (
        <>
          <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              {canWrite && <form onSubmit={saveCategory} className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row">
                <label className="min-w-0 flex-1"><span className="mb-1 block text-xs font-black text-slate-600">{editingCategory ? "تعديل اسم التصنيف" : "إضافة تصنيف تجهيز"}</span><input value={editingCategory?.name ?? categoryName} onChange={(event) => editingCategory ? setEditingCategory({ ...editingCategory, name: event.target.value }) : setCategoryName(event.target.value)} placeholder="مثال: مطليات أو ملابس" className="min-h-12 w-full rounded-xl border border-slate-200 px-3 font-bold outline-none focus:border-emerald-500" /></label>
                <button disabled={saving || !(editingCategory?.name ?? categoryName).trim()} className="min-h-12 rounded-xl bg-emerald-700 px-5 font-black text-white disabled:opacity-50"><Plus className="ml-1 inline" />{editingCategory ? "حفظ الاسم" : "إضافة التصنيف"}</button>
                {editingCategory && <button type="button" onClick={() => setEditingCategory(null)} className="min-h-12 rounded-xl border px-4 font-black">إلغاء</button>}
              </form>}
              <div className="text-xs font-bold text-slate-500">العنصر الواحد يمكن أن يظهر في أكثر من تصنيف.</div>
            </div>
            <div className="mt-4 flex gap-2 overflow-x-auto pb-1">
              <button onClick={() => setSelectedCategory("")} className={`shrink-0 rounded-xl border px-4 py-2.5 text-sm font-black ${!selectedCategory ? "border-emerald-700 bg-emerald-700 text-white" : "border-slate-200"}`}>كل التصنيفات</button>
              {categories.map((category) => (
                <div key={category.id} className={`flex shrink-0 items-center rounded-xl border ${selectedCategory === String(category.id) ? "border-emerald-700 bg-emerald-50" : "border-slate-200"}`}>
                  <button onClick={() => setSelectedCategory(String(category.id))} className="px-4 py-2.5 text-sm font-black">{category.name} <span className="mr-1 text-[10px] text-slate-500">{category.resource_count || 0}</span></button>
                  {canWrite && <button onClick={() => setEditingCategory({ ...category })} className="border-r border-slate-200 p-2.5 text-emerald-700" aria-label={`تعديل ${category.name}`}><PencilSimple /></button>}
                </div>
              ))}
            </div>
          </section>

          <section className="grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(360px,0.75fr)]">
            <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="text-xl font-black"><Tag className="ml-1 inline text-emerald-700" />عناصر {selectedCategoryRow?.name || "كل التصنيفات"}</h2><p className="mt-1 text-xs font-bold text-slate-500">تظهر هنا الخدمات والمكونات التابعة للتصنيف المحدد فقط.</p></div><label className="relative"><MagnifyingGlass className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="بحث…" className="min-h-11 rounded-xl border pr-10 pl-3 text-sm font-bold" /></label></div>
              {!visibleResources.length ? <div className="mt-4 rounded-2xl border border-dashed p-8 text-center text-sm font-bold text-slate-500">لا توجد عناصر ضمن هذا التصنيف.</div> : <div className="mt-4 grid gap-3 md:grid-cols-2">{visibleResources.map((resource) => (
                <article key={resource.id} className={`rounded-2xl border p-4 ${resource.status === "inactive" ? "border-rose-200 bg-rose-50/50" : "border-slate-200"}`}>
                  <div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><div className="font-black text-slate-950">{resource.name}</div>{resource.status === "inactive" && <span className="rounded-full bg-rose-100 px-2 py-1 text-[10px] font-black text-rose-700">موقوف</span>}</div><div className="mt-1 text-xs font-bold text-slate-500">{resource.track_inventory ? "مكوّن" : "خدمة"} · {resource.code || "بدون رمز"}</div></div>{canWrite && <button onClick={() => setAssigningResource(resource)} className="rounded-xl border px-3 py-2 text-xs font-black text-emerald-700">تعديل التصنيفات</button>}</div>
                  <div className="mt-3"><CategoryBadges categoryIds={resource.category_ids || []} categories={categories} /></div>
                </article>
              ))}</div>}
            </div>

            {canWrite && <form onSubmit={submitGroup} className="rounded-3xl border border-emerald-200 bg-emerald-50/40 p-5 shadow-sm" data-testid="component-group-editor">
              <div className="flex items-start justify-between gap-3"><div><h2 className="text-xl font-black"><FolderSimple className="ml-1 inline text-emerald-700" />{groupDraft.id ? "تعديل المجموعة" : "إنشاء مجموعة"}</h2><p className="mt-1 text-xs font-bold leading-5 text-slate-500">اسم المجموعة يتكوّن تلقائيًا من أسماء العناصر وبنفس ترتيب اختيارها.</p></div>{groupDraft.id && <button type="button" onClick={() => setGroupDraft(EMPTY_GROUP)} className="rounded-xl border bg-white p-2"><X /></button>}</div>
              <div className="mt-4 grid gap-3">
                <label className="text-xs font-black text-slate-600">التصنيف<select value={groupDraft.category_id} onChange={(event) => setGroupDraft({ ...groupDraft, category_id: event.target.value, resource_ids: [] })} className="mt-1 min-h-12 w-full rounded-xl border bg-white px-3 text-sm font-black"><option value="">اختر التصنيف</option>{categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label>
                <label className="text-xs font-black text-slate-600">نوع المجموعة<select value={groupDraft.group_kind} onChange={(event) => setGroupDraft({ ...groupDraft, group_kind: event.target.value, resource_ids: [] })} className="mt-1 min-h-12 w-full rounded-xl border bg-white px-3 text-sm font-black"><option value="service">مجموعة خدمات</option><option value="component">مجموعة مكونات</option></select></label>
                <div className="rounded-xl border border-emerald-200 bg-white p-3"><div className="text-xs font-black text-slate-500">اسم المجموعة التلقائي</div><div className="mt-1 min-h-6 font-black text-emerald-900">{groupName || "اختر عنصرين على الأقل"}</div></div>
                <div className="max-h-72 space-y-2 overflow-y-auto rounded-xl border border-slate-200 bg-white p-2">
                  {!groupDraft.category_id ? <div className="p-4 text-center text-xs font-bold text-slate-500">اختر التصنيف أولًا.</div> : !groupCandidates.length ? <div className="p-4 text-center text-xs font-bold text-slate-500">لا توجد عناصر مناسبة داخل التصنيف.</div> : groupCandidates.map((resource) => {
                    const checked = groupDraft.resource_ids.includes(String(resource.id));
                    const order = groupDraft.resource_ids.indexOf(String(resource.id)) + 1;
                    return <label key={resource.id} className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 ${checked ? "border-emerald-500 bg-emerald-50" : "border-slate-200"}`}><input type="checkbox" checked={checked} onChange={() => toggleGroupResource(String(resource.id))} className="h-4 w-4 accent-emerald-700" /><span className="min-w-0 flex-1 font-black">{resource.name}</span>{checked && <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-700 text-xs font-black text-white">{order}</span>}</label>;
                  })}
                </div>
              </div>
              <button disabled={saving || !groupDraft.category_id || groupDraft.resource_ids.length < 2} className="mt-4 min-h-12 w-full rounded-xl bg-emerald-700 px-5 font-black text-white disabled:opacity-50">{saving ? <SpinnerGap className="ml-1 inline animate-spin" /> : <CheckCircle className="ml-1 inline" weight="fill" />}{groupDraft.id ? "حفظ المجموعة" : "إنشاء المجموعة"}</button>
            </form>}
          </section>

          <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-xl font-black"><Wrench className="ml-1 inline text-emerald-700" />المجموعات الجاهزة</h2>
            {!visibleGroups.length ? <div className="mt-4 rounded-2xl border border-dashed p-8 text-center text-sm font-bold text-slate-500">لا توجد مجموعات ضمن التصنيف المحدد.</div> : <div className="mt-4 grid gap-3 lg:grid-cols-2">{visibleGroups.map((group) => (
              <article key={group.id} className="rounded-2xl border border-slate-200 p-4">
                <div className="flex items-start justify-between gap-3"><div><div className="font-black text-slate-950">{group.name}</div><div className="mt-1 text-xs font-bold text-slate-500">{group.group_kind === "service" ? "مجموعة خدمات" : "مجموعة مكونات"} · {group.resources?.length || 0} عناصر</div></div>{canWrite && <button onClick={() => editGroup(group)} className="rounded-xl border px-3 py-2 text-xs font-black text-emerald-700">تعديل</button>}</div>
                <div className="mt-3 flex flex-wrap gap-1.5">{(group.resources || []).map((resource) => <span key={resource.id} className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-black text-slate-700">{resource.name}</span>)}</div>
              </article>
            ))}</div>}
          </section>
        </>
      )}

      {assigningResource && <CategoryAssignment resource={assigningResource} categories={categories} saving={saving} onClose={() => setAssigningResource(null)} onSaved={() => load({ quiet: true })} />}
    </main></InventorySuiteFrame>
  );
}
