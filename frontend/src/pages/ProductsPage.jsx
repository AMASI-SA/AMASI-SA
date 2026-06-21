// Iter-250b · P2 (Phase 2) — Products catalogue page.
// Read-only list backed by `db.products` (imported from Excel).
// The autocomplete inside the supplier-invoice form (Phase 3) will
// hit the same /api/products/list endpoint.
import { useEffect, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";
import ProductsImportExcelModal from
  "../components/ProductsImportExcelModal";

const fmt = (v) => v == null ? "—" :
  Number(v).toLocaleString("en-US", {
    minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function ProductsPage() {
  const [items, setItems]   = useState([]);
  const [total, setTotal]   = useState(0);
  const [loading, setLoading] = useState(true);
  const [q, setQ]           = useState("");
  const [needsCost, setNeedsCost] = useState(false);
  const [showImport, setShowImport] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const params = { limit: 100 };
      if (q) params.q = q;
      if (needsCost) params.needs_cost = true;
      const r = await api.get("/products/list", { params });
      setItems(r.data.items || []);
      setTotal(r.data.total || 0);
    } catch (e) {
      toast.error("فشل تحميل المنتجات");
    } finally { setLoading(false); }
  }

  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [q, needsCost]);

  return (
    <div dir="rtl" className="p-6 space-y-4">
      <header className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">المنتجات</h1>
          <p className="text-sm text-gray-600 mt-1">
            قاعدة المنتجات المستوردة من سلة. تُستخدم في البحث داخل فاتورة المورد.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowImport(true)}
          className="bg-emerald-600 text-white px-4 py-2 rounded font-semibold"
          data-testid="prod-import-btn"
        >
          📥 استيراد المنتجات من Excel
        </button>
      </header>

      <div className="bg-white border rounded-lg p-4 flex flex-wrap gap-3 items-center">
        <input
          type="text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="ابحث بالاسم أو رقم المنتج"
          className="flex-1 min-w-[260px] border rounded px-3 py-2 text-sm"
          data-testid="prod-search"
        />
        <label className="flex items-center gap-1 text-xs">
          <input
            type="checkbox"
            checked={needsCost}
            onChange={(e) => setNeedsCost(e.target.checked)}
            data-testid="prod-filter-needs-cost"
          />
          المنتجات بحاجة لتكلفة فقط
        </label>
        <span className="text-xs text-gray-500 ml-auto">
          المعروض: {items.length} / الإجمالي: {total}
        </span>
      </div>

      <div className="bg-white border rounded-lg overflow-x-auto">
        {loading ? (
          <p className="p-6 text-center text-gray-500">جارٍ التحميل…</p>
        ) : items.length === 0 ? (
          <p className="p-6 text-center text-gray-500" data-testid="prod-empty">
            لا توجد منتجات. استورد ملف Excel للبدء.
          </p>
        ) : (
          <table className="min-w-full text-sm">
            <thead className="bg-gray-100">
              <tr className="text-right">
                <th className="p-3"></th>
                <th className="p-3">الاسم</th>
                <th className="p-3">رقم المنتج</th>
                <th className="p-3">التصنيف</th>
                <th className="p-3">آخر تكلفة</th>
                <th className="p-3">متوسط التكلفة</th>
                <th className="p-3">الحالة</th>
              </tr>
            </thead>
            <tbody>
              {items.map((p) => (
                <tr key={p.id} className="border-t hover:bg-gray-50"
                    data-testid={"prod-row-" + p.id}>
                  <td className="p-3">
                    {p.image_url ? (
                      <img src={p.image_url} alt=""
                           className="w-12 h-12 rounded object-cover"
                           onError={(e) => { e.target.style.display = "none"; }} />
                    ) : (
                      <div className="w-12 h-12 rounded bg-slate-100" />
                    )}
                  </td>
                  <td className="p-3 font-bold max-w-[280px]">
                    <div className="truncate" title={p.name}>{p.name}</div>
                  </td>
                  <td className="p-3 font-mono text-xs text-slate-500">
                    #{p.product_id}
                  </td>
                  <td className="p-3 text-xs">
                    {(p.category_paths?.[0] || []).join(" › ") || (
                      <span className="text-amber-700">غير مصنف</span>
                    )}
                  </td>
                  <td className="p-3 font-mono text-emerald-800">
                    {fmt(p.cost_current)}
                  </td>
                  <td className="p-3 font-mono text-indigo-800">
                    {fmt(p.cost_avg)}
                  </td>
                  <td className="p-3">
                    {p.needs_cost ? (
                      <span className="text-[10px] px-1.5 py-0.5 rounded border font-bold bg-amber-100 text-amber-800 border-amber-300"
                            data-testid="needs-cost-badge">
                        💰 بحاجة لتكلفة
                      </span>
                    ) : (
                      <span className="text-[10px] px-1.5 py-0.5 rounded border font-bold bg-emerald-100 text-emerald-800 border-emerald-300">
                        ✓
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showImport && (
        <ProductsImportExcelModal
          onClose={() => setShowImport(false)}
          onImported={() => { setShowImport(false); load(); }}
        />
      )}
    </div>
  );
}
