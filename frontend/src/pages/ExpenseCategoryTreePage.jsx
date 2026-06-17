// Iter-244 — Expense Categories Tree page (flat rendering).
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const errMsg = (e, fb) =>
  e?.response?.data?.detail || e?.message || fb;

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
              />
            ))}
          </ul>
        )}
      </div>
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

function DisplayRow({ node, inactive, startAddChild, setEditing, toggleStatus }) {
  return (
    <>
      <span
        className="text-sm font-semibold flex-1"
        data-testid={"cat-name-" + node.id}
      >
        {node.name}
        {inactive && (
          <span className="ms-2 text-[10px] bg-gray-200 text-gray-600 px-1.5 py-0.5 rounded">
            متوقف
          </span>
        )}
      </span>
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
