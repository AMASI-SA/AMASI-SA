// Iter-244 — Suppliers page (flat rendering, no recursive components).
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const errMsg = (e, fb) =>
  e?.response?.data?.detail || e?.message || fb;

function flattenCategories(items) {
  const byParent = {};
  items.forEach((n) => {
    const k = n.parent_id || "_root_";
    if (!byParent[k]) byParent[k] = [];
    byParent[k].push(n);
  });
  Object.values(byParent).forEach((arr) =>
    arr.sort((a, b) => (a.name || "").localeCompare(b.name || ""))
  );
  const out = [];
  function walk(parent, depth) {
    const kids = byParent[parent || "_root_"] || [];
    for (const node of kids) {
      out.push({ node, depth });
      walk(node.id, depth + 1);
    }
  }
  walk(null, 0);
  return out;
}

export default function SuppliersPage() {
  const [items, setItems] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState(null);

  async function load() {
    setLoading(true);
    try {
      const params = {};
      if (search) params.search = search;
      if (statusFilter !== "all") params.status = statusFilter;
      if (categoryFilter) params.category_id = categoryFilter;
      const [s, c] = await Promise.all([
        api.get("/suppliers", { params }),
        api.get("/expense-category-tree?include_inactive=false"),
      ]);
      setItems(s.data.items || []);
      setCategories(c.data.items || []);
    } catch (e) {
      toast.error(errMsg(e, "فشل تحميل البيانات"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, statusFilter, categoryFilter]);

  const catMap = useMemo(() => {
    const m = {};
    categories.forEach((c) => {
      m[c.id] = c;
    });
    return m;
  }, [categories]);

  function openCreate() {
    setEditing(null);
    setShowModal(true);
  }
  function openEdit(s) {
    setEditing(s);
    setShowModal(true);
  }

  return (
    <div className="space-y-5" dir="rtl" data-testid="suppliers-page">
      <header className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">الموردون</h1>
          <p className="text-sm text-gray-600 mt-1">
            قاعدة موردي الشركة. الحذف ممنوع — الإيقاف فقط.
          </p>
        </div>
        <button
          type="button"
          onClick={openCreate}
          className="bg-emerald-600 text-white px-4 py-2 rounded font-semibold"
          data-testid="supplier-create-btn"
        >
          + مورد جديد
        </button>
      </header>

      <div className="bg-white border rounded-lg p-4 grid grid-cols-1 md:grid-cols-4 gap-3">
        <input
          placeholder="ابحث بالاسم أو الجوال أو البريد"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="border rounded px-3 py-2 text-sm md:col-span-2"
          data-testid="supplier-search-input"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
          data-testid="supplier-status-filter"
        >
          <option value="all">كل الحالات</option>
          <option value="active">نشط</option>
          <option value="inactive">متوقف</option>
        </select>
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
          data-testid="supplier-category-filter"
        >
          <option value="">كل التصنيفات</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {(c.path || [c.name]).join(" / ")}
            </option>
          ))}
        </select>
      </div>

      <div className="bg-white border rounded-lg overflow-x-auto">
        {loading ? (
          <p className="text-center py-8 text-gray-500">جارٍ التحميل...</p>
        ) : items.length === 0 ? (
          <p
            className="text-center py-8 text-gray-500"
            data-testid="suppliers-empty"
          >
            لا يوجد موردون.
          </p>
        ) : (
          <SuppliersTable
            items={items}
            catMap={catMap}
            openEdit={openEdit}
          />
        )}
      </div>

      {showModal && (
        <SupplierModal
          initial={editing}
          categories={categories}
          onClose={() => setShowModal(false)}
          onSaved={() => {
            setShowModal(false);
            load();
          }}
        />
      )}
    </div>
  );
}

function SuppliersTable({ items, catMap, openEdit }) {
  return (
    <table className="min-w-full text-sm">
      <thead className="bg-gray-100">
        <tr className="text-right">
          <th className="p-3">الشركة</th>
          <th className="p-3">شخص الاتصال</th>
          <th className="p-3">الجوال</th>
          <th className="p-3">البريد</th>
          <th className="p-3">التخصصات</th>
          <th className="p-3">الحالة</th>
          <th className="p-3">إجراءات</th>
        </tr>
      </thead>
      <tbody data-testid="suppliers-table-body">
        {items.map((s) => (
          <SupplierRow
            key={s.id}
            s={s}
            catMap={catMap}
            openEdit={openEdit}
          />
        ))}
      </tbody>
    </table>
  );
}

function SupplierRow({ s, catMap, openEdit }) {
  const cids = s.category_ids || [];
  const visible = cids.slice(0, 3);
  const extra = cids.length - visible.length;
  return (
    <tr
      className="border-t hover:bg-gray-50"
      data-testid={"supplier-row-" + s.id}
    >
      <td className="p-3 font-semibold">{s.company_name}</td>
      <td className="p-3">{s.contact_person}</td>
      <td className="p-3 font-mono">{s.phone}</td>
      <td className="p-3 text-xs text-gray-600">{s.email || "—"}</td>
      <td className="p-3">
        <div className="flex flex-wrap gap-1">
          {visible.map((cid) => (
            <span
              key={cid}
              className="text-[10px] bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded"
            >
              {(catMap[cid]?.path || ["?"]).join(" / ")}
            </span>
          ))}
          {extra > 0 && (
            <span className="text-[10px] text-gray-500">+{extra}</span>
          )}
          {cids.length === 0 && (
            <span className="text-[10px] text-gray-400">—</span>
          )}
        </div>
      </td>
      <td className="p-3">
        {s.status === "active" ? (
          <span className="bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded text-xs">
            نشط
          </span>
        ) : (
          <span className="bg-gray-200 text-gray-600 px-2 py-0.5 rounded text-xs">
            متوقف
          </span>
        )}
      </td>
      <td className="p-3">
        <button
          type="button"
          onClick={() => openEdit(s)}
          className="text-blue-700 text-xs hover:underline"
          data-testid={"supplier-edit-" + s.id}
        >
          تعديل
        </button>
      </td>
    </tr>
  );
}

function SupplierModal({ initial, categories, onClose, onSaved }) {
  const [companyName, setCompanyName] = useState(initial?.company_name || "");
  const [contactPerson, setContactPerson] = useState(
    initial?.contact_person || ""
  );
  const [phone, setPhone] = useState(initial?.phone || "");
  const [email, setEmail] = useState(initial?.email || "");
  const [notes, setNotes] = useState(initial?.notes || "");
  const [status, setStatus] = useState(initial?.status || "active");
  const [selectedCats, setSelectedCats] = useState(
    new Set(initial?.category_ids || [])
  );
  const [saving, setSaving] = useState(false);

  const flatCats = useMemo(() => flattenCategories(categories), [categories]);

  function toggleCat(id) {
    setSelectedCats((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function save() {
    if (!companyName.trim()) return toast.error("اسم الشركة مطلوب");
    if (!contactPerson.trim()) return toast.error("اسم شخص الاتصال مطلوب");
    if (!phone.trim()) return toast.error("رقم الجوال مطلوب");
    setSaving(true);
    try {
      const payload = {
        company_name: companyName.trim(),
        contact_person: contactPerson.trim(),
        phone: phone.trim(),
        email: email.trim() || null,
        notes: notes.trim() || null,
        category_ids: Array.from(selectedCats),
        status,
      };
      if (initial?.id) {
        await api.patch("/suppliers/" + initial.id, payload);
        toast.success("تم التحديث");
      } else {
        await api.post("/suppliers", payload);
        toast.success("تم الإنشاء");
      }
      onSaved();
    } catch (e) {
      toast.error(errMsg(e, "فشل الحفظ"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
      onClick={onClose}
      data-testid="supplier-modal-overlay"
    >
      <div
        className="bg-white rounded-xl max-w-3xl w-full max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
        dir="rtl"
        data-testid="supplier-modal"
      >
        <div className="px-5 py-4 border-b bg-emerald-50 rounded-t-xl flex items-center justify-between">
          <h2 className="text-lg font-extrabold text-emerald-800">
            {initial ? "تعديل مورد" : "مورد جديد"}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1 rounded bg-white border text-sm"
            data-testid="supplier-modal-close"
          >
            إغلاق
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label="اسم الشركة *">
              <input
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm"
                data-testid="supplier-company-input"
              />
            </Field>
            <Field label="شخص الاتصال *">
              <input
                value={contactPerson}
                onChange={(e) => setContactPerson(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm"
                data-testid="supplier-contact-input"
              />
            </Field>
            <Field label="رقم الجوال *">
              <input
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm font-mono"
                data-testid="supplier-phone-input"
              />
            </Field>
            <Field label="البريد الإلكتروني">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm"
                data-testid="supplier-email-input"
              />
            </Field>
            {initial && (
              <Field label="الحالة">
                <select
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                  className="w-full border rounded px-3 py-2 text-sm"
                  data-testid="supplier-status-select"
                >
                  <option value="active">نشط</option>
                  <option value="inactive">متوقف</option>
                </select>
              </Field>
            )}
          </div>

          <Field label="ملاحظات">
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm"
              rows={2}
              data-testid="supplier-notes-input"
            />
          </Field>

          <CategoryPicker
            flatCats={flatCats}
            selectedCats={selectedCats}
            toggleCat={toggleCat}
          />
        </div>

        <div className="px-5 py-3 border-t flex items-center justify-end gap-2 bg-gray-50">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded bg-gray-200 text-sm"
          >
            إلغاء
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="px-5 py-2 rounded bg-emerald-600 text-white font-semibold text-sm disabled:opacity-50"
            data-testid="supplier-save-btn"
          >
            {saving ? "جارٍ الحفظ..." : "حفظ"}
          </button>
        </div>
      </div>
    </div>
  );
}

function CategoryPicker({ flatCats, selectedCats, toggleCat }) {
  return (
    <div>
      <label className="block text-sm font-semibold mb-2">
        التخصصات (التصنيفات التي يعمل فيها هذا المورد)
      </label>
      <div className="border rounded p-3 max-h-72 overflow-y-auto bg-gray-50">
        {flatCats.length === 0 ? (
          <p className="text-sm text-gray-500 text-center py-4">
            لا توجد تصنيفات بعد — أنشئ التصنيفات أولاً.
          </p>
        ) : (
          <ul className="space-y-1" data-testid="supplier-category-tree">
            {flatCats.map(({ node, depth }) => (
              <CatCheckboxRow
                key={node.id}
                node={node}
                depth={depth}
                checked={selectedCats.has(node.id)}
                onToggle={toggleCat}
              />
            ))}
          </ul>
        )}
      </div>
      <p
        className="text-xs text-emerald-700 mt-2"
        data-testid="supplier-selected-count"
      >
        مُحدَّد: {selectedCats.size}
      </p>
    </div>
  );
}

function CatCheckboxRow({ node, depth, checked, onToggle }) {
  const inactive = node.status === "inactive";
  return (
    <li>
      <label
        className={
          "flex items-center gap-2 py-1 hover:bg-white rounded px-1 " +
          (inactive ? "opacity-50" : "")
        }
        style={{ paddingInlineStart: depth * 18 + "px" }}
      >
        <input
          type="checkbox"
          checked={checked}
          onChange={() => onToggle(node.id)}
          disabled={inactive}
          data-testid={"cat-checkbox-" + node.id}
        />
        <span className="text-sm">{node.name}</span>
      </label>
    </li>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <label className="block text-xs font-semibold mb-1 text-gray-700">
        {label}
      </label>
      {children}
    </div>
  );
}
