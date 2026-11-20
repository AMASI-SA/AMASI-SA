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
  ArrowRight,
  LockKey,
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

import InventorySuiteFrame, { InventoryPageHeader, InventorySheet } from "../components/inventory/suite/InventorySuiteFrame";
import "./inventory-catalog-resources.css";

function componentMoney(amount) {
  return amount == null || amount === "" ? "غير محددة" : Number(amount).toLocaleString("ar-SA", { maximumFractionDigits: 2 }) + " ر.س";
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

export default function MezanComponentsProduction({ embedded = false }) {
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
  const [mobileView, setMobileView] = useState("list");

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
      setCostDraft(selected?.track_inventory && !costCopy(selected).locked
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
    setCostDraft(selected.track_inventory && !costCopy(selected).locked
      ? (selected.initial_unit_cost ?? selected.reference_cost?.amount ?? "")
      : (selected.reference_cost?.amount ?? ""));
    setEditForm(componentFormFromRow(selected));
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  function selectComponent(row) {
    setMobileView("detail");
    setSelectedId(row.id);
    setCostDraft(row.track_inventory && !costCopy(row).locked
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
    if (saving) return;
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
    if (saving) return;
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
    if (saving || !selected || selected.status === "inactive" || selectedCostCopy.locked) return;
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


  const formModal = (mode) => {
    const isEdit = mode === "edit";
    const values = isEdit ? editForm : form;
    const setValues = isEdit ? setEditForm : setForm;
    const stock = values.kind === "stock_component";
    const lockedCost = isEdit && stock && selectedCostCopy.locked;
    const canSave = componentFormCanSave(values);
    const close = () => { if (!saving) { setAddOpen(false); setEditOpen(false); setError(""); } };
    const formId = isEdit ? "mezan-component-edit" : "mezan-component-add";
    return (
      <InventorySheet open onClose={close} busy={saving} title={isEdit ? "تعديل المكوّن أو الخدمة" : "إضافة مكوّن أو خدمة"} description="الاسم والرمز والتصنيف أولًا، ثم التكلفة والتجهيز." footer={<><button type="button" disabled={saving} onClick={close} className="mz-catalog-secondary">إلغاء</button><button type="submit" form={formId} disabled={saving || !canSave} className="mz-catalog-primary">{saving ? <SpinnerGap className="animate-spin" /> : <Plus />}{saving ? "جارٍ الحفظ…" : isEdit ? "حفظ التعديلات" : "إنشاء"}</button></>}>
        <form id={formId} onSubmit={isEdit ? updateComponent : createComponent} className="mz-component-fields">
          {error && <div role="alert" className="mz-catalog-notice mz-catalog-error"><WarningCircle size={20}/>{error}</div>}
          <fieldset disabled={saving} className="mz-component-fields">
            <div className="mz-catalog-form-grid">
              <label className="is-full">اسم المكوّن أو الخدمة *<input autoFocus required value={values.name} onChange={(event) => setValues({ ...values, name: event.target.value })} placeholder="مثل: تطريز الاسم" /></label>
              <label>الرمز *<input required dir="ltr" value={values.code} onChange={(event) => setValues({ ...values, code: event.target.value })} placeholder="EMBROIDERY_NAME" /></label>
              <label>النوع<select value={values.kind} onChange={(event) => setValues(changeComponentKind(values, event.target.value))}><option value="service">خدمة عمل</option><option value="stock_component">مكوّن مخزني</option></select></label>
            </div>
            <fieldset className="mz-component-categories">
              <legend>تصنيف التجهيز *</legend>
              {!categories.length ? <p className="mz-catalog-muted">أنشئ تصنيفًا مثل «مطليات» أو «ملابس» من تبويب التصنيفات والمجموعات أولًا.</p> : <div>{categories.map((category) => {
                const checked = (values.category_ids || []).map(String).includes(String(category.id));
                return <label key={category.id} className={checked ? "is-checked" : ""}><input type="checkbox" checked={checked} onChange={() => setValues(toggleComponentCategory(values, category.id))}/>{category.name}</label>;
              })}</div>}
              <p className="mz-catalog-muted">اختر تصنيفًا واحدًا أو أكثر.</p>
            </fieldset>
            {!stock && <label className="mz-component-switch"><input type="checkbox" checked={values.requires_preparation} onChange={(event) => setValues({ ...values, requires_preparation: event.target.checked })}/><span>يتطلب إكمال الخدمة قبل الشحن<small>مثل القص والطلاء والتطريز والطباعة.</small></span></label>}
            <div className="mz-catalog-form-grid"><label className="is-full">{lockedCost ? "تكلفة المخزون المعتمدة" : stock ? "التكلفة الأولية التقديرية" : "تكلفة الخدمة الحالية"}<input type="number" min="0" step="0.01" disabled={lockedCost} value={lockedCost ? (selected?.reference_cost?.amount ?? values.unit_cost) : values.unit_cost} onChange={(event) => setValues({ ...values, unit_cost: event.target.value })} placeholder="التكلفة بالريال" /></label></div>
            {stock && <div className={"mz-catalog-notice " + (lockedCost ? "" : "mz-catalog-warning")}>{lockedCost ? <LockKey size={19}/> : <WarningCircle size={19}/>}<span>{lockedCost ? "هذه التكلفة معتمدة من فاتورة شراء، وتبقى محمية من التعديل اليدوي." : "قيمة تقديرية للبدء. تحل تكلفة الشراء المعتمدة محلها عند اعتماد أول فاتورة شراء."}</span></div>}
            <div className="mz-catalog-form-grid"><label className="is-full">ملاحظات <span className="mz-catalog-muted">اختياري</span><textarea value={values.description} onChange={(event) => setValues({ ...values, description: event.target.value })} /></label></div>
          </fieldset>
        </form>
      </InventorySheet>
    );
  };

  const content = <div className="mz-components-workspace" dir="rtl" data-testid="components-catalog-workspace">
    <InventoryPageHeader title={embedded ? "دليل المكونات والخدمات" : "المكونات والخدمات"} description="أضف العنصر وصنّفه وحدد تكلفته، ثم اربطه بالمنتجات وخياراتها.">
      <button onClick={() => { setError(""); setForm(newComponentForm()); setAddOpen(true); }} className="mz-catalog-primary"><Plus size={18}/>إضافة مكوّن أو خدمة</button>
    </InventoryPageHeader>
    {error && !addOpen && !editOpen && <div role="alert" className="mz-catalog-notice mz-catalog-error"><WarningCircle size={20}/>{error}<button type="button" onClick={() => load(selectedId)}>إعادة المحاولة</button></div>}
    <section className="mz-components-summary" aria-label="ملخص المكونات">
      <div><span>إجمالي العناصر</span><strong>{summary.total}</strong></div><div><span>العناصر الفعالة</span><strong>{summary.active}</strong></div><div><span>العناصر الموقوفة</span><strong>{summary.inactive}</strong></div><div><span>تحتاج تكلفة</span><strong>{summary.missing_cost}</strong></div>
    </section>
    <section className="mz-catalog-tools">
      <label className="mz-catalog-search"><MagnifyingGlass size={21}/><input aria-label="بحث المكونات بالاسم أو الرمز" value={query} onChange={(event) => { setQuery(event.target.value); setMobileView("list"); }} placeholder="ابحث بالاسم أو الرمز…"/></label>
      <div className="mz-catalog-status-tabs" aria-label="حالة المكونات">{[["active", "الفعالة"], ["inactive", "الموقوفة"], ["all", "الكل"]].map(([value, label]) => <button key={value} type="button" aria-pressed={statusFilter === value} onClick={() => { setStatusFilter(value); setMobileView("list"); }}>{label}</button>)}</div>
    </section>
    {loading ? <div className="mz-catalog-empty" role="status"><SpinnerGap size={30} className="animate-spin"/>جارٍ تحميل المكونات…</div> : !components.length ? <section className="mz-catalog-list mz-catalog-empty"><Cube size={48}/><h2>ابدأ بأول مكوّن أو خدمة</h2><p>أضف خامة مخزنية أو خدمة مثل النقش والتطريز والتغليف.</p><button onClick={() => { setForm(newComponentForm()); setAddOpen(true); }} className="mz-catalog-primary"><Plus size={18}/>إضافة أول مكوّن</button></section> : <section className="mz-catalog-layout">
      <section className="mz-catalog-list" data-mobile-hidden={mobileView === "detail"} aria-label="قائمة المكونات">
        <div className="mz-catalog-section-title"><h2>المكونات والخدمات <span>{visible.length}</span></h2><span>الأسماء والرموز والتكاليف</span></div>
        {!visible.length ? <div className="mz-catalog-empty"><MagnifyingGlass size={35}/><p>لا توجد عناصر تطابق البحث.</p></div> : <div className="mz-catalog-table-scroll"><table className="mz-catalog-table"><thead><tr><th>العنصر</th><th>الرمز</th><th>التكلفة الحالية</th><th>الحالة</th></tr></thead><tbody>{visible.map((row) => <tr key={row.id} className={selected?.id === row.id ? "is-selected" : ""} onClick={() => selectComponent(row)}><td><button type="button" className="mz-catalog-product" aria-label={"عرض " + row.name} onClick={() => selectComponent(row)}><span className="mz-component-icon">{row.track_inventory ? <Cube size={23}/> : <Wrench size={23}/>}</span><span><strong>{row.name}</strong><small>{row.track_inventory ? "مكوّن مخزني" : "خدمة عمل"} · {row.code || "بدون رمز"}</small></span></button></td><td><bdi>{row.code || "—"}</bdi></td><td className="mz-catalog-price">{componentMoney(row.reference_cost?.amount)}</td><td><span className={"mz-catalog-state state-" + (row.status === "inactive" ? "inactive" : "active")}>{row.status === "inactive" ? "موقوف" : "فعّال"}</span></td></tr>)}</tbody></table></div>}
      </section>
      <aside className="mz-catalog-inspector" data-mobile-hidden={mobileView === "list"} aria-label="تفاصيل المكوّن">
        <button type="button" className="mz-catalog-mobile-back" onClick={() => setMobileView("list")}><ArrowRight size={18}/>العودة إلى المكونات</button>
        {selected ? <><div className="mz-catalog-identity"><span className="mz-component-icon">{selected.track_inventory ? <Cube size={29}/> : <Wrench size={29}/>}</span><div><h2>{selected.name}</h2><p><bdi>{selected.code}</bdi></p><span className={"mz-catalog-state state-" + (selected.status === "inactive" ? "inactive" : "active")}>{selected.status === "inactive" ? "موقوف" : "فعّال"}</span></div></div>
          <div className="mz-catalog-detail-tabs"><button type="button" onClick={() => { setError(""); setEditForm(componentFormFromRow(selected)); setEditOpen(true); }}><PencilSimple size={16}/>تعديل العنصر</button><button type="button" onClick={toggleStatus} disabled={statusBusy || saving}>{selected.status === "inactive" ? <ArrowCounterClockwise size={16}/> : <Power size={16}/>}{statusBusy ? "جارٍ الحفظ…" : selected.status === "inactive" ? "إعادة التفعيل" : "إيقاف وإخفاء"}</button></div>
          <div className="mz-catalog-detail-content">
            {selected.status === "inactive" && <div className="mz-catalog-notice mz-catalog-warning">العنصر موقوف عن الربط الجديد. الروابط والسجلات السابقة محفوظة.</div>}
            <section className="mz-component-cost-box"><header><h3>{selectedCostCopy.title}</h3><span className="mz-catalog-state">{selectedCostCopy.locked && <LockKey size={13}/>} {selectedCostCopy.badge}</span></header><p>{selectedCostCopy.description}</p><div><input aria-label="تكلفة المكوّن الحالية" disabled={saving || selected.status === "inactive" || selectedCostCopy.locked} type="number" min="0" step="0.01" value={costDraft} onChange={(event) => setCostDraft(event.target.value)} placeholder="التكلفة بالريال"/><button onClick={saveCost} disabled={saving || selected.status === "inactive" || selectedCostCopy.locked} className="mz-catalog-primary">{saving ? "جارٍ الحفظ…" : "حفظ"}</button></div></section>
            <section><h3>تصنيفات التجهيز</h3><p className="mz-catalog-muted">{(selected.category_ids || []).map((id) => categories.find((category) => String(category.id) === String(id))?.name).filter(Boolean).join("، ") || "غير مصنف"}</p></section>
            <section><h3>المنتجات المرتبطة <span>({selected.product_usages?.length || 0})</span></h3>{!(selected.product_usages || []).length && <p className="mz-catalog-muted">لا توجد روابط منتجات لهذا العنصر.</p>}{(selected.product_usages || []).map((usage) => <article key={usage.id} className="mz-component-usage"><strong>{usage.product_name}</strong><p>{usage.source === "product" ? "مرتبط بالمنتج مباشرة" : [usage.condition?.option_name, usage.condition?.value_name].filter(Boolean).join(": ")}</p></article>)}</section>
            {selected.description && <section className="mt-4"><h3>ملاحظات</h3><p className="mz-catalog-muted">{selected.description}</p></section>}
          </div></> : <div className="mz-catalog-empty">اختر عنصرًا لعرض التفاصيل.</div>}
      </aside>
    </section>}
    {addOpen && formModal("add")}
    {editOpen && formModal("edit")}
  </div>;

  return embedded ? content : <InventorySuiteFrame active="components">{content}</InventorySuiteFrame>;
}
