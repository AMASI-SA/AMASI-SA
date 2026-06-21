// Iter-244 + Iter-246 — Expense Categories Tree page (flat rendering)
// with `movement_types` badges + inline editor for ROOT categories.
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";
// Iter-250b P2 — Excel import modal for categories.
import CategoriesImportExcelModal from
  "../components/CategoriesImportExcelModal";

const errMsg = (e, fb) =>
  e?.response?.data?.detail || e?.message || fb;

// Iter-246 — Allowed op types + Arabic labels for badges / chips.
const MOVEMENT_TYPE_OPTIONS = [
  { value: "supplier_invoice", label: "فاتورة مورد",
    color: "bg-emerald-100 text-emerald-800 border-emerald-300" },
  { value: "general_expense", label: "مصروف عام",
    color: "bg-sky-100 text-sky-800 border-sky-300" },
  { value: "fixed_asset", label: "أصل ثابت",
    color: "bg-violet-100 text-violet-800 border-violet-300" },
];
function mtMeta(v) {
  return MOVEMENT_TYPE_OPTIONS.find((x) => x.value === v);
}

function flattenTree(items, expandedMap) {
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
      const hasKids = !!byParent[node.id]?.length;
      out.push({ node, depth, hasKids });
      if (expandedMap[node.id]) walk(node.id, depth + 1);
    }
  }
  walk(null, 0);
  return out;
}

export default function ExpenseCategoryTreePage() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState({});
  const [editing, setEditing] = useState(null);
  const [addingUnder, setAddingUnder] = useState(null);
  const [newName, setNewName] = useState("");
  // Iter-246 — movement_types editor (root only).
  const [mtEditing, setMtEditing] = useState(null);  // { id, name, selected: Set }
  // Iter-250b P2 — Excel import modal toggle.
  const [showImport, setShowImport] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const { data } = await api.get(
        "/expense-category-tree?include_inactive=true"
      );
      setItems(data.items || []);
    } catch (e) {
      toast.error(errMsg(e, "فشل تحميل التصنيفات"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const rows = useMemo(() => flattenTree(items, expanded), [items, expanded]);
  const isEmpty = items.length === 0;

  async function seedTemplate() {
    try {
      const { data } = await api.post(
        "/expense-category-tree/seed-template",
        {}
      );
      toast.success(
        `تم استيراد القوالب: ${data.inserted} تصنيف جديد` +
          (data.skipped ? ` (تخطّى ${data.skipped})` : "")
      );
      await load();
    } catch (e) {
      toast.error(errMsg(e, "فشل الاستيراد"));
    }
  }

  function startAddRoot() {
    setAddingUnder("__root__");
    setNewName("");
  }
  function startAddChild(pid) {
    setAddingUnder(pid);
    setNewName("");
    setExpanded((p) => ({ ...p, [pid]: true }));
  }
  function cancelAdd() {
    setAddingUnder(null);
    setNewName("");
  }
  async function commitAdd() {
    const name = (newName || "").trim();
    if (!name) {
      toast.error("الاسم مطلوب");
      return;
    }
    try {
      await api.post("/expense-category-tree", {
        name,
        parent_id: addingUnder === "__root__" ? null : addingUnder,
      });
      toast.success("تمت الإضافة");
      await load();
      cancelAdd();
    } catch (e) {
      toast.error(errMsg(e, "فشلت الإضافة"));
    }
  }
  async function commitRename() {
    const v = (editing?.name || "").trim();
    if (!v) {
      toast.error("الاسم مطلوب");
      return;
    }
    try {
      await api.patch(`/expense-category-tree/${editing.id}`, { name: v });
      toast.success("تم التعديل");
      setEditing(null);
      await load();
    } catch (e) {
      toast.error(errMsg(e, "فشل التعديل"));
    }
  }
  async function toggleStatus(cat) {
    const next = cat.status === "active" ? "inactive" : "active";
    try {
      await api.patch(`/expense-category-tree/${cat.id}`, { status: next });
      toast.success(next === "inactive" ? "تم الإيقاف" : "تم التفعيل");
      await load();
    } catch (e) {
      toast.error(errMsg(e, "فشل التحديث"));
    }
  }

  // Iter-246 — open the movement_types picker for a root.
  function startMtEdit(node) {
    setMtEditing({
      id: node.id,
      name: node.name,
      selected: new Set(node.movement_types || []),
    });
  }
  function toggleMt(value) {
    setMtEditing((prev) => {
      if (!prev) return prev;
      const next = new Set(prev.selected);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return { ...prev, selected: next };
    });
  }
  async function commitMt() {
    if (!mtEditing) return;
    try {
      await api.patch(`/expense-category-tree/${mtEditing.id}`, {
        movement_types: Array.from(mtEditing.selected),
      });
      toast.success("تم تحديث أنواع العمليات");
      setMtEditing(null);
      await load();
    } catch (e) {
      toast.error(errMsg(e, "فشل التحديث"));
    }
  }

  return (
    <div className="space-y-5" dir="rtl" data-testid="expense-category-tree-page">
      <header className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">شجرة تصنيفات المصروفات</h1>
          <p className="text-sm text-gray-600 mt-1">
            تصنيفات متعدّدة المستويات. الحذف ممنوع — الإيقاف فقط.
          </p>
        </div>
        <div className="flex gap-2">
          {/* Iter-250b P2 — Excel categories import. */}
          <button
            type="button"
            onClick={() => setShowImport(true)}
            className="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-semibold"
            data-testid="cat-import-excel-btn"
          >
            📥 استيراد التصنيفات من Excel
          </button>
          <button
            type="button"
            onClick={startAddRoot}
            className="bg-emerald-600 text-white px-4 py-2 rounded text-sm font-semibold"
            data-testid="cat-add-root-btn"
          >
            + إضافة تصنيف جذر
          </button>
          {isEmpty && (
            <button
              type="button"
              onClick={seedTemplate}
              className="bg-amber-500 text-white px-4 py-2 rounded text-sm font-semibold"
              data-testid="cat-seed-template-btn"
            >
              استيراد القوالب الجاهزة
            </button>
          )}
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="bg-gray-100 px-3 py-2 rounded text-sm"
            data-testid="cat-refresh-btn"
          >
            تحديث
          </button>
        </div>
      </header>

      {addingUnder === "__root__" && (
        <AddInline
          placeholder="اسم التصنيف الجذر"
          value={newName}
          onChange={setNewName}
          onSave={commitAdd}
          onCancel={cancelAdd}
          testid="add-root-input"
        />
      )}

      <div className="bg-white rounded-lg border p-4 min-h-[200px]">
        {loading && (
          <p className="text-center text-gray-500 py-6">جارٍ التحميل...</p>
        )}
        {!loading && isEmpty && (
          <div className="text-center py-10">
            <p className="text-gray-500 mb-4">لا توجد تصنيفات.</p>
            <button
              type="button"
              onClick={seedTemplate}
              className="bg-amber-500 text-white px-5 py-2 rounded font-semibold"
            >
              استيراد القوالب الجاهزة
            </button>
          </div>
        )}
        {!loading && !isEmpty && (
          <ul className="space-y-1" data-testid="cat-tree-root">
            {rows.map(({ node, depth, hasKids }) => (
              <CategoryRow
                key={node.id}
                node={node}
                depth={depth}
                hasKids={hasKids}
                isOpen={!!expanded[node.id]}
                isEditing={editing?.id === node.id}
                editing={editing}
                isAddingHere={addingUnder === node.id}
                newName={newName}
                setNewName={setNewName}
                setExpanded={setExpanded}
                setEditing={setEditing}
                startAddChild={startAddChild}
                commitAdd={commitAdd}
                cancelAdd={cancelAdd}
                commitRename={commitRename}
                toggleStatus={toggleStatus}
                startMtEdit={startMtEdit}
              />
            ))}
          </ul>
        )}
      </div>

      {/* Iter-246 — movement_types editor modal (root only). */}
      {mtEditing && (
        <MovementTypesDialog
          editing={mtEditing}
          onToggle={toggleMt}
          onSave={commitMt}
          onCancel={() => setMtEditing(null)}
        />
      )}

      {showImport && (
        <CategoriesImportExcelModal
          onClose={() => setShowImport(false)}
          onImported={() => {
            setShowImport(false);
            load();
          }}
        />
      )}
    </div>
  );
}

function CategoryRow(props) {
  const {
    node,
    depth,
    hasKids,
    isOpen,
    isEditing,
    editing,
    isAddingHere,
    newName,
    setNewName,
    setExpanded,
    setEditing,
    startAddChild,
    commitAdd,
    cancelAdd,
    commitRename,
    toggleStatus,
    startMtEdit,
  } = props;
  const inactive = node.status === "inactive";
  const padStart = depth * 22;
  return (
    <li data-testid={"cat-node-" + node.id}>
      <div
        className={
          "flex items-center gap-2 py-1.5 px-2 rounded hover:bg-gray-50 " +
          (inactive ? "opacity-50" : "")
        }
        style={{ paddingInlineStart: padStart + "px" }}
      >
        <button
          type="button"
          onClick={() =>
            setExpanded((p) => ({ ...p, [node.id]: !isOpen }))
          }
          className="w-6 text-center text-gray-500 text-xs"
          data-testid={"cat-toggle-" + node.id}
        >
          {hasKids ? (isOpen ? "▼" : "▶") : "•"}
        </button>
        {isEditing ? (
          <RenameRow
            editing={editing}
            setEditing={setEditing}
            commitRename={commitRename}
            nodeId={node.id}
          />
        ) : (
          <DisplayRow
            node={node}
            inactive={inactive}
            startAddChild={startAddChild}
            setEditing={setEditing}
            toggleStatus={toggleStatus}
            startMtEdit={startMtEdit}
          />
        )}
      </div>
      {isAddingHere && (
        <div
          style={{ paddingInlineStart: padStart + 48 + "px" }}
          className="my-1"
        >
          <AddInline
            placeholder="اسم الفرع الجديد"
            value={newName}
            onChange={setNewName}
            onSave={commitAdd}
            onCancel={cancelAdd}
            testid={"add-child-input-" + node.id}
          />
        </div>
      )}
    </li>
  );
}

function DisplayRow({ node, inactive, startAddChild, setEditing,
                     toggleStatus, startMtEdit }) {
  const isRoot = !node.parent_id;
  const mts = node.movement_types || [];
  return (
    <>
      <span
        className="text-sm font-semibold flex-1 flex items-center gap-2 flex-wrap"
        data-testid={"cat-name-" + node.id}
      >
        <span>{node.name}</span>
        {/* Iter-246 — movement_types chips. Shown on EVERY row
            (descendants inherit from root, so this stays useful even
            for leaves), but only EDITABLE on root rows. */}
        {mts.length > 0 && (
          <span
            className="flex items-center gap-1 flex-wrap"
            data-testid={"cat-mt-chips-" + node.id}
          >
            {mts.map((v) => {
              const m = mtMeta(v);
              if (!m) return null;
              return (
                <span
                  key={v}
                  className={
                    "text-[10px] font-bold px-1.5 py-0.5 rounded border " + m.color
                  }
                  data-testid={"cat-mt-chip-" + node.id + "-" + v}
                >
                  {m.label}
                </span>
              );
            })}
          </span>
        )}
        {isRoot && mts.length === 0 && (
          <span
            className="text-[10px] font-bold px-1.5 py-0.5 rounded border bg-rose-50 text-rose-700 border-rose-300"
            data-testid={"cat-mt-empty-" + node.id}
          >
            بدون ربط بأنواع العمليات
          </span>
        )}
        {inactive && (
          <span className="ms-2 text-[10px] bg-gray-200 text-gray-600 px-1.5 py-0.5 rounded">
            متوقف
          </span>
        )}
      </span>
      {isRoot && (
        <button
          type="button"
          onClick={() => startMtEdit(node)}
          className="text-xs text-violet-700 hover:underline"
          data-testid={"cat-edit-mt-" + node.id}
          title="تعديل أنواع العمليات المرتبطة بهذا الجذر"
        >
          أنواع العمليات
        </button>
      )}
      <button
        type="button"
        onClick={() => startAddChild(node.id)}
        className="text-xs text-emerald-700 hover:underline"
        data-testid={"cat-add-child-" + node.id}
      >
        + فرع
      </button>
      <button
        type="button"
        onClick={() => setEditing({ id: node.id, name: node.name })}
        className="text-xs text-blue-700 hover:underline"
        data-testid={"cat-edit-" + node.id}
      >
        تعديل
      </button>
      <button
        type="button"
        onClick={() => toggleStatus(node)}
        className="text-xs text-amber-700 hover:underline"
        data-testid={"cat-toggle-status-" + node.id}
      >
        {inactive ? "تفعيل" : "إيقاف"}
      </button>
    </>
  );
}

// Iter-246 — modal dialog for picking movement_types on a root.
function MovementTypesDialog({ editing, onToggle, onSave, onCancel }) {
  return (
    <div
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
      onClick={onCancel}
      data-testid="cat-mt-dialog-backdrop"
    >
      <div
        className="bg-white rounded-lg shadow-xl max-w-md w-full p-5"
        onClick={(e) => e.stopPropagation()}
        dir="rtl"
        data-testid="cat-mt-dialog"
      >
        <h3 className="text-lg font-bold mb-1">
          أنواع العمليات لجذر «{editing.name}»
        </h3>
        <p className="text-xs text-gray-500 mb-4 leading-6">
          اختر الأنواع التي تظهر فيها هذا الجذر وأبناؤه عند تسجيل
          حركة مالية جديدة. القيود القديمة لا تتأثر — التغيير يسري
          على العمليات الجديدة فقط.
        </p>
        <div className="space-y-2">
          {MOVEMENT_TYPE_OPTIONS.map((opt) => {
            const checked = editing.selected.has(opt.value);
            return (
              <label
                key={opt.value}
                className={
                  "flex items-center gap-2 border rounded p-2 cursor-pointer hover:bg-gray-50 " +
                  (checked ? "border-emerald-400 bg-emerald-50" : "")
                }
                data-testid={"cat-mt-opt-" + opt.value}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onToggle(opt.value)}
                  data-testid={"cat-mt-cb-" + opt.value}
                />
                <span
                  className={
                    "text-[10px] font-bold px-1.5 py-0.5 rounded border " + opt.color
                  }
                >
                  {opt.label}
                </span>
                <span className="text-xs text-gray-600">
                  ({opt.value})
                </span>
              </label>
            );
          })}
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button
            type="button"
            onClick={onCancel}
            className="bg-gray-200 px-3 py-1.5 text-sm rounded"
            data-testid="cat-mt-cancel"
          >
            إلغاء
          </button>
          <button
            type="button"
            onClick={onSave}
            className="bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-1.5 text-sm rounded font-bold"
            data-testid="cat-mt-save"
          >
            حفظ
          </button>
        </div>
      </div>
    </div>
  );
}

function RenameRow({ editing, setEditing, commitRename, nodeId }) {
  return (
    <>
      <input
        value={editing.name}
        onChange={(e) => setEditing({ ...editing, name: e.target.value })}
        className="border rounded px-2 py-1 text-sm flex-1"
        autoFocus
        data-testid={"cat-rename-input-" + nodeId}
      />
      <button
        type="button"
        onClick={commitRename}
        className="bg-emerald-600 text-white px-2 py-1 text-xs rounded"
        data-testid={"cat-rename-save-" + nodeId}
      >
        حفظ
      </button>
      <button
        type="button"
        onClick={() => setEditing(null)}
        className="bg-gray-200 px-2 py-1 text-xs rounded"
      >
        إلغاء
      </button>
    </>
  );
}

function AddInline({ placeholder, value, onChange, onSave, onCancel, testid }) {
  return (
    <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-200 rounded p-2">
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="border rounded px-2 py-1 text-sm flex-1"
        autoFocus
        onKeyDown={(e) => {
          if (e.key === "Enter") onSave();
          if (e.key === "Escape") onCancel();
        }}
        data-testid={testid}
      />
      <button
        type="button"
        onClick={onSave}
        className="bg-emerald-600 text-white px-3 py-1 text-xs rounded"
      >
        حفظ
      </button>
      <button
        type="button"
        onClick={onCancel}
        className="bg-gray-200 px-3 py-1 text-xs rounded"
      >
        إلغاء
      </button>
    </div>
  );
}
