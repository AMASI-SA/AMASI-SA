// Iter-250b · P2 (Phase 3) — Product autocomplete + quick-create
// inside the supplier invoice line item form. Self-contained:
// exposes `<ProductLineAutocomplete value onSelect />` plus a small
// inline quick-create dialog when no match is found.
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const fmt = (v) => v == null ? "—" :
  Number(v).toLocaleString("en-US",
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function ProductLineAutocomplete({
  value,            // product object selected (or null)
  onSelect,         // (product) => void
  initialQuery,     // string — used to pre-fill the typed text
}) {
  const [q, setQ]               = useState(initialQuery || "");
  const [results, setResults]   = useState([]);
  const [open, setOpen]         = useState(false);
  const [loading, setLoading]   = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const boxRef = useRef(null);
  const timerRef = useRef(null);

  // Debounced search.
  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (!q || q.trim().length < 2) {
      setResults([]);
      return;
    }
    timerRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await api.get("/products/list",
          { params: { q: q.trim(), limit: 20 } });
        setResults(r.data.items || []);
        setOpen(true);
      } finally { setLoading(false); }
    }, 250);
    return () => timerRef.current && clearTimeout(timerRef.current);
  }, [q]);

  // Close on outside click.
  useEffect(() => {
    function handler(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  function selectProduct(p) {
    onSelect?.(p);
    setQ(p.name);
    setOpen(false);
  }

  if (value?.product_id) {
    // Selected state — compact card with "change" button.
    return (
      <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-200 rounded px-2 py-1"
           data-testid="prod-autocomplete-selected">
        {value.image_url ? (
          <img src={value.image_url} alt=""
               className="w-8 h-8 rounded object-cover"
               onError={(e) => { e.target.style.display = "none"; }} />
        ) : <div className="w-8 h-8 rounded bg-slate-100" />}
        <div className="flex-1 min-w-0">
          <div className="text-xs font-bold truncate" title={value.name}>{value.name}</div>
          <div className="text-[10px] text-slate-500 font-mono">
            #{value.product_id}
            {value.category_paths?.[0]
              && " · " + value.category_paths[0].join(" › ")}
          </div>
        </div>
        <button type="button"
                onClick={() => { onSelect?.(null); setQ(""); }}
                className="text-[10px] text-rose-600 hover:underline"
                data-testid="prod-autocomplete-change">
          تغيير
        </button>
      </div>
    );
  }

  return (
    <div className="relative" ref={boxRef}>
      <input
        type="text"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => { if (results.length > 0) setOpen(true); }}
        placeholder="اكتب اسم المنتج، رقم المنتج، SKU، الباركود"
        className="w-full border rounded px-2 py-1 text-xs"
        data-testid="prod-autocomplete-input"
        autoComplete="off"
      />
      {open && (
        <div className="absolute z-30 top-full mt-1 right-0 left-0 bg-white border border-slate-200 rounded-lg shadow-lg max-h-80 overflow-y-auto"
             data-testid="prod-autocomplete-dropdown">
          {loading && (
            <div className="p-3 text-center text-xs text-slate-500">
              جارٍ البحث…
            </div>
          )}
          {!loading && results.length === 0 && q.trim().length >= 2 && (
            <div className="p-3 text-center">
              <div className="text-xs text-slate-500 mb-2">
                لا توجد منتجات مطابقة لـ «{q}»
              </div>
              <button type="button"
                      onClick={() => setShowCreate(true)}
                      className="bg-emerald-600 text-white text-xs px-3 py-1.5 rounded font-bold"
                      data-testid="prod-quick-create-trigger">
                + إضافة منتج جديد سريع
              </button>
            </div>
          )}
          {results.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => selectProduct(p)}
              className="w-full flex items-center gap-2 p-2 hover:bg-emerald-50 border-b text-right"
              data-testid={"prod-result-" + p.id}
            >
              {p.image_url ? (
                <img src={p.image_url} alt=""
                     className="w-12 h-12 rounded object-cover flex-shrink-0"
                     onError={(e) => { e.target.style.display = "none"; }} />
              ) : <div className="w-12 h-12 rounded bg-slate-100 flex-shrink-0" />}
              <div className="flex-1 min-w-0">
                <div className="text-xs font-bold truncate">{p.name}</div>
                <div className="text-[10px] text-slate-500 font-mono">
                  #{p.product_id}
                </div>
                {p.category_paths?.[0] && (
                  <div className="text-[10px] text-indigo-700 truncate">
                    {p.category_paths[0].join(" › ")}
                  </div>
                )}
              </div>
              <div className="text-left text-[10px] space-y-0.5 flex-shrink-0">
                <div>آخر: <b className="font-mono text-emerald-800">{fmt(p.cost_current)}</b></div>
                <div>متوسط: <b className="font-mono text-indigo-800">{fmt(p.cost_avg)}</b></div>
                {p.needs_cost && (
                  <div className="text-amber-700 font-bold">💰 بحاجة تكلفة</div>
                )}
              </div>
            </button>
          ))}
          {results.length > 0 && (
            <button type="button"
                    onClick={() => setShowCreate(true)}
                    className="w-full p-2 text-center text-xs text-emerald-700 hover:bg-emerald-50 border-t font-bold"
                    data-testid="prod-quick-create-trigger-bottom">
              + لم أجده؟ إضافة منتج جديد سريع
            </button>
          )}
        </div>
      )}

      {showCreate && (
        <QuickCreateProductModal
          initialName={q}
          onClose={() => setShowCreate(false)}
          onCreated={(p) => {
            setShowCreate(false);
            selectProduct(p);
          }} />
      )}
    </div>
  );
}

function QuickCreateProductModal({ initialName, onClose, onCreated }) {
  const [name, setName]         = useState(initialName || "");
  const [productId, setProductId] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [cost, setCost]         = useState("");
  const [notes, setNotes]       = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [categories, setCategories] = useState([]);
  const [saving, setSaving]     = useState(false);

  useEffect(() => {
    (async () => {
      try {
        // Pull all `kind=product` categories so the merchant can pick
        // a parent for the new product. We reuse the expense tree
        // endpoint with a kind filter — but to keep this lightweight,
        // we hit /products/list to get the union of category_ids in
        // use. Instead, query the expense tree directly:
        const r = await api.get(
          "/expense-category-tree?include_inactive=false");
        // Filter to product subtree.
        const flat = [];
        function walk(arr, prefix = []) {
          for (const c of (arr || [])) {
            if (c.kind === "product" || (prefix.length > 0 && (prefix[0] === "المنتجات المستوردة"))) {
              flat.push({ id: c.id, label: [...prefix, c.name].join(" › ") });
            }
            walk(c.children, [...prefix, c.name]);
          }
        }
        walk(r.data?.items || []);
        setCategories(flat);
      } catch (e) { /* ignore */ }
    })();
  }, []);

  async function submit() {
    if (!name.trim()) { toast.error("اسم المنتج إلزامي"); return; }
    setSaving(true);
    try {
      const r = await api.post("/products/quick-create", {
        name: name.trim(),
        product_id: productId.trim() || undefined,
        image_url: imageUrl.trim() || undefined,
        cost: cost ? Number(cost) : undefined,
        category_id: categoryId || undefined,
        notes: notes.trim() || undefined,
      });
      toast.success("تم إضافة المنتج");
      onCreated?.(r.data.product);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "فشل إضافة المنتج");
    } finally { setSaving(false); }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
         onClick={onClose} data-testid="prod-quick-create-overlay">
      <div className="bg-white rounded-xl max-w-lg w-full"
           onClick={(e) => e.stopPropagation()} dir="rtl"
           data-testid="prod-quick-create-modal">
        <div className="px-5 py-3 border-b bg-emerald-50 rounded-t-xl">
          <h3 className="font-extrabold text-emerald-900">+ إضافة منتج جديد سريع</h3>
        </div>
        <div className="p-5 space-y-3">
          <Field label="اسم المنتج *">
            <input value={name} onChange={(e) => setName(e.target.value)}
                   className="w-full border rounded px-2 py-1.5 text-sm"
                   data-testid="qc-name" autoFocus />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="رقم المنتج (اختياري)">
              <input value={productId} onChange={(e) => setProductId(e.target.value)}
                     className="w-full border rounded px-2 py-1.5 text-sm font-mono"
                     data-testid="qc-product-id" />
            </Field>
            <Field label="تكلفة الشراء (اختياري)">
              <input type="number" step="0.01" value={cost}
                     onChange={(e) => setCost(e.target.value)}
                     className="w-full border rounded px-2 py-1.5 text-sm font-mono"
                     data-testid="qc-cost" />
            </Field>
          </div>
          <Field label="التصنيف">
            <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)}
                    className="w-full border rounded px-2 py-1.5 text-sm"
                    data-testid="qc-category">
              <option value="">— غير مصنف (افتراضي) —</option>
              {categories.map(c => (
                <option key={c.id} value={c.id}>{c.label}</option>
              ))}
            </select>
          </Field>
          <Field label="رابط صورة (اختياري)">
            <input value={imageUrl} onChange={(e) => setImageUrl(e.target.value)}
                   placeholder="https://…" type="url"
                   className="w-full border rounded px-2 py-1.5 text-sm font-mono"
                   data-testid="qc-image" />
          </Field>
          <Field label="ملاحظات (اختياري)">
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)}
                      rows={2}
                      className="w-full border rounded px-2 py-1.5 text-sm"
                      data-testid="qc-notes" />
          </Field>
        </div>
        <div className="px-5 py-3 border-t bg-slate-50 flex justify-end gap-2">
          <button type="button" onClick={onClose}
                  className="px-4 py-1.5 text-sm rounded bg-white border"
                  data-testid="qc-cancel">إلغاء</button>
          <button type="button" onClick={submit} disabled={saving || !name.trim()}
                  className="px-4 py-1.5 text-sm rounded bg-emerald-600 text-white font-bold disabled:bg-slate-300"
                  data-testid="qc-save">
            {saving ? "جارٍ الحفظ…" : "حفظ وإضافة للفاتورة"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <div className="text-[11px] font-bold text-slate-700 mb-1">{label}</div>
      {children}
    </label>
  );
}
