// Iter-244 — Suppliers page (flat rendering, no recursive components).
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";
// Iter-250b · P1.5.z — Tabbed merge with /suppliers-ledger.
// Second tab renders the financial ledger inline so we have a single
// canonical page combining management + balances + drift diagnostics.
import SuppliersLedger from "./SuppliersLedger";
// Iter-250b · P1.5.ab — Read-only Suppliers Unification Forensic.
import SuppliersUnificationForensicModal from
  "../components/SuppliersUnificationForensicModal";

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
  // Iter-250b · P1.5.z — Tab switcher. Default = management to match
  // historical behaviour of `/suppliers-new`. The `?tab=` URL hint
  // is honoured so `/suppliers-ledger` redirects can land directly
  // on the balances view.
  const initialTab = (() => {
    if (typeof window === "undefined") return "management";
    const sp = new URLSearchParams(window.location.search);
    return sp.get("tab") === "balances" ? "balances" : "management";
  })();
  const [activeTab, setActiveTab] = useState(initialTab);
  const [items, setItems] = useState([]);
  const [totals, setTotals] = useState({});
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [linkFilter, setLinkFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState(null);
  const [showForensic, setShowForensic] = useState(false);

  async function load() {
    setLoading(true);
    try {
      // Iter-250b · P1.5.ab — Unified suppliers list. Sources merged
      // server-side from db.suppliers + db.counterparties + GL/FM
      // ghosts. Filters are applied client-side because cross-source
      // queries are cheaper to filter in memory than in Mongo.
      const [u, c] = await Promise.all([
        api.get("/suppliers-unified"),
        api.get("/expense-category-tree?include_inactive=false"),
      ]);
      setItems(u.data.items || []);
      setTotals(u.data.totals || {});
      setCategories(c.data.items || []);
    } catch (e) {
      toast.error(errMsg(e, "فشل تحميل البيانات"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const catMap = useMemo(() => {
    const m = {};
    categories.forEach((c) => {
      m[c.id] = c;
    });
    return m;
  }, [categories]);

  // Iter-250b · P1.5.ab — Client-side filter across unified list.
  const filteredItems = useMemo(() => {
    const q = (search || "").trim().toLowerCase();
    return items.filter((r) => {
      if (linkFilter !== "all" && r.link_status !== linkFilter) return false;
      if (statusFilter !== "all" && r.status !== statusFilter) return false;
      if (categoryFilter
          && !(r.category_ids || []).includes(categoryFilter)) return false;
      if (q) {
        const hay = [
          r.company_name, r.contact_person, r.phone, r.email,
        ].map((x) => (x || "").toString().toLowerCase()).join(" ");
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [items, search, linkFilter, statusFilter, categoryFilter]);

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
      {/* Iter-250b · P1.5.z — Tab bar. One canonical /suppliers-new
          page now hosts BOTH the management CRUD and the financial
          ledger. */}
      <div className="flex border-b border-slate-200 gap-1"
           data-testid="suppliers-tabs">
        <button type="button"
          onClick={() => setActiveTab("management")}
          className={`px-4 py-2 text-sm font-bold border-b-2 transition ${activeTab === "management"
            ? "border-indigo-600 text-indigo-700"
            : "border-transparent text-slate-500 hover:text-slate-700"}`}
          data-testid="suppliers-tab-management">
          🛠️ إدارة الموردين
        </button>
        <button type="button"
          onClick={() => setActiveTab("balances")}
          className={`px-4 py-2 text-sm font-bold border-b-2 transition ${activeTab === "balances"
            ? "border-indigo-600 text-indigo-700"
            : "border-transparent text-slate-500 hover:text-slate-700"}`}
          data-testid="suppliers-tab-balances">
          💰 الأرصدة والدفاتر
        </button>
      </div>

      {activeTab === "balances" && (
        <div data-testid="suppliers-balances-tab">
          <SuppliersLedger />
        </div>
      )}

      {activeTab === "management" && (
      <>
      <header className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">الموردون</h1>
          <p className="text-sm text-gray-600 mt-1">
            قاعدة موحَّدة: جدول الموردين الجديد + Ledger القديم. الحذف ممنوع — الإيقاف فقط.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowForensic(true)}
            className="bg-amber-100 text-amber-800 border border-amber-300 px-3 py-2 rounded font-semibold text-sm"
            data-testid="suppliers-forensic-btn"
          >
            🔍 تقرير التوحيد (Read-Only)
          </button>
          <button
            type="button"
            onClick={openCreate}
            className="bg-emerald-600 text-white px-4 py-2 rounded font-semibold"
            data-testid="supplier-create-btn"
          >
            + مورد جديد
          </button>
        </div>
      </header>

      {/* Iter-250b · P1.5.ab — Unification summary bar. */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3"
           data-testid="suppliers-unification-summary">
        <SummaryCard label="الإجمالي"     value={totals.total ?? 0}     tone="slate" />
        <SummaryCard label="مورد جديد"   value={totals.new_only ?? 0}  tone="emerald" />
        <SummaryCard label="مربوط"       value={totals.linked ?? 0}    tone="indigo" />
        <SummaryCard label="Ledger فقط" value={totals.ledger_only ?? 0} tone="amber" />
      </div>

      <div className="bg-white border rounded-lg p-4 grid grid-cols-1 md:grid-cols-5 gap-3">
        <input
          placeholder="ابحث بالاسم أو الجوال أو البريد"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="border rounded px-3 py-2 text-sm md:col-span-2"
          data-testid="supplier-search-input"
        />
        <select
          value={linkFilter}
          onChange={(e) => setLinkFilter(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
          data-testid="supplier-link-filter"
        >
          <option value="all">كل المصادر</option>
          <option value="new_only">مورد جديد</option>
          <option value="linked">مربوط</option>
          <option value="ledger_only">Ledger فقط</option>
        </select>
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
        ) : filteredItems.length === 0 ? (
          <p
            className="text-center py-8 text-gray-500"
            data-testid="suppliers-empty"
          >
            لا يوجد موردون مطابقون للفلتر.
          </p>
        ) : (
          <SuppliersTable
            items={filteredItems}
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
      </>
      )}

      {showForensic && (
        <SuppliersUnificationForensicModal
          onClose={() => setShowForensic(false)}
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
          <th className="p-3">المصدر</th>
          <th className="p-3">شخص الاتصال</th>
          <th className="p-3">الجوال</th>
          <th className="p-3">البريد</th>
          <th className="p-3">التخصصات</th>
          <th className="p-3">المستحق (GL)</th>
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

function LinkStatusBadge({ status }) {
  const cfg = {
    new_only:    { label: "مورد جديد", cls: "bg-emerald-100 text-emerald-800 border-emerald-300" },
    linked:      { label: "مربوط",     cls: "bg-indigo-100 text-indigo-800 border-indigo-300" },
    ledger_only: { label: "Ledger فقط", cls: "bg-amber-100 text-amber-800 border-amber-300" },
  }[status] || { label: status || "?", cls: "bg-gray-100 text-gray-700 border-gray-300" };
  return (
    <span
      className={"inline-block text-[11px] px-2 py-0.5 rounded border font-bold " + cfg.cls}
      data-testid={"supplier-link-badge-" + status}
    >
      {cfg.label}
    </span>
  );
}

function SupplierRow({ s, catMap, openEdit }) {
  const cids = s.category_ids || [];
  const visible = cids.slice(0, 3);
  const extra = cids.length - visible.length;
  const owed = Number(s.outstanding_debt || 0);
  return (
    <tr
      className="border-t hover:bg-gray-50"
      data-testid={"supplier-row-" + s.id}
    >
      <td className="p-3 font-semibold">{s.company_name}</td>
      <td className="p-3">
        <LinkStatusBadge status={s.link_status} />
      </td>
      <td className="p-3">{s.contact_person || "—"}</td>
      <td className="p-3 font-mono">{s.phone || "—"}</td>
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
      <td className={"p-3 font-mono " + (owed > 0 ? "text-rose-700 font-bold" : "text-gray-400")}>
        {owed.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </td>
      <td className="p-3">
        {s.status === "active" ? (
          <span className="bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded text-xs">
            نشط
          </span>
        ) : s.status === "inactive" ? (
          <span className="bg-gray-200 text-gray-600 px-2 py-0.5 rounded text-xs">
            متوقف
          </span>
        ) : (
          <span className="bg-amber-100 text-amber-700 px-2 py-0.5 rounded text-xs">
            {s.status || "?"}
          </span>
        )}
      </td>
      <td className="p-3">
        {s.editable ? (
          <button
            type="button"
            onClick={() => openEdit({
              id: s.source_ids?.supplier_id || s.id,
              company_name: s.company_name,
              contact_person: s.contact_person,
              phone: s.phone,
              email: s.email,
              status: s.status,
              category_ids: s.category_ids || [],
              notes: s.notes,
            })}
            className="text-blue-700 text-xs hover:underline"
            data-testid={"supplier-edit-" + s.id}
          >
            تعديل
          </button>
        ) : (
          <span
            className="text-[11px] text-amber-700"
            data-testid={"supplier-readonly-" + s.id}
            title="هذا المورد موجود في Ledger فقط ولا يمكن تعديله من هنا"
          >
            للقراءة فقط
          </span>
        )}
      </td>
    </tr>
  );
}

function SummaryCard({ label, value, tone }) {
  const toneCls = {
    slate:   "bg-slate-50 border-slate-200 text-slate-700",
    emerald: "bg-emerald-50 border-emerald-200 text-emerald-800",
    indigo:  "bg-indigo-50 border-indigo-200 text-indigo-800",
    amber:   "bg-amber-50 border-amber-200 text-amber-800",
  }[tone] || "bg-slate-50 border-slate-200 text-slate-700";
  return (
    <div
      className={"rounded-lg border p-3 " + toneCls}
      data-testid={"suppliers-summary-" + (label || "")}
    >
      <div className="text-xs font-semibold opacity-80">{label}</div>
      <div className="text-2xl font-extrabold mt-1">{value}</div>
    </div>
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
