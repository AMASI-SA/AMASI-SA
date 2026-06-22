// Iter-250b · P2 (Phase 2) — Products catalogue page.
// Read-only list backed by `db.products` (imported from Excel).
// The autocomplete inside the supplier-invoice form (Phase 3) will
// hit the same /api/products/list endpoint.
// Phase 4 — Visual audit: click a row to reveal the last 5 cost
// history records (supplier, invoice, qty, unit price, total).
import { useEffect, useState, Fragment } from "react";
import { toast } from "sonner";
import api from "../lib/api";
import ProductsImportExcelModal from
  "../components/ProductsImportExcelModal";

const fmt = (v) => v == null ? "—" :
  Number(v).toLocaleString("en-US", {
    minimumFractionDigits: 2, maximumFractionDigits: 2 });

const fmtDate = (v) => {
  if (!v) return "—";
  try {
    return new Date(v).toLocaleDateString("en-CA");
  } catch (_e) { return v; }
};

function CostHistoryPanel({ productId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.get(`/products/${productId}/cost-history`, {
      params: { limit: 5 },
    })
      .then((r) => { if (alive) setData(r.data); })
      .catch(() => { if (alive) toast.error("فشل تحميل سجل التكلفة"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [productId]);

  if (loading) {
    return (
      <div className="p-4 text-center text-xs text-slate-500"
           data-testid="cost-history-loading">
        جارٍ تحميل سجل التكلفة…
      </div>
    );
  }
  if (!data) return null;
  const items = data.items || [];
  return (
    <div className="bg-slate-50 border-t-2 border-emerald-200 p-4"
         data-testid={`cost-history-${productId}`}>
      <div className="flex items-center justify-between mb-2">
        <h4 className="font-bold text-sm text-emerald-900">
          📜 آخر {items.length} سجل تكلفة
        </h4>
        <span className="text-[11px] text-slate-500">
          إجمالي السجلات: {data.total_count} •
          آخر: <b className="text-emerald-700 font-mono">
            {fmt(data.product?.cost_current)}
          </b> •
          متوسط مرجح: <b className="text-indigo-700 font-mono">
            {fmt(data.product?.cost_avg)}
          </b>
        </span>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-slate-500 p-2"
           data-testid="cost-history-empty">
          لا يوجد سجل تكلفة بعد لهذا المنتج.
        </p>
      ) : (
        <div className="overflow-x-auto bg-white rounded border border-slate-200">
          <table className="min-w-full text-xs">
            <thead className="bg-slate-100">
              <tr className="text-right">
                <th className="p-2">التاريخ</th>
                <th className="p-2">المورد</th>
                <th className="p-2">رقم فاتورة المورد</th>
                <th className="p-2 text-center">الكمية</th>
                <th className="p-2 text-center">سعر الوحدة</th>
                <th className="p-2 text-center">إجمالي السطر</th>
                <th className="p-2">المصدر</th>
              </tr>
            </thead>
            <tbody>
              {items.map((h, idx) => (
                <tr key={idx} className="border-t border-slate-200"
                    data-testid={`cost-history-row-${idx}`}>
                  <td className="p-2 font-mono">
                    {fmtDate(h.invoice_date || h.at)}
                  </td>
                  <td className="p-2">
                    {h.supplier_name || (
                      <span className="text-slate-400">—</span>
                    )}
                  </td>
                  <td className="p-2 font-mono text-slate-600">
                    {h.doc_number || (
                      <span className="text-slate-400">
                        {h.supplier_invoice_id
                          ? `#${h.supplier_invoice_id.slice(0,8)}`
                          : "—"}
                      </span>
                    )}
                  </td>
                  <td className="p-2 text-center font-mono">
                    {h.quantity != null ? fmt(h.quantity) : "—"}
                  </td>
                  <td className="p-2 text-center font-mono text-emerald-800">
                    {fmt(h.unit_cost)}
                  </td>
                  <td className="p-2 text-center font-mono text-indigo-800 font-bold">
                    {h.total_cost != null ? fmt(h.total_cost) : "—"}
                  </td>
                  <td className="p-2 text-[10px]">
                    <span className={`px-1.5 py-0.5 rounded border ${
                      h.source === "supplier-invoice"
                        ? "bg-emerald-100 text-emerald-800 border-emerald-300"
                        : "bg-slate-100 text-slate-700 border-slate-300"
                    }`}>
                      {h.source === "supplier-invoice" ? "فاتورة مورد"
                       : h.source === "excel-import" ? "استيراد Excel"
                       : h.source === "quick-create" ? "إنشاء سريع"
                       : (h.source || "—")}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function ProductsPage() {
  const [items, setItems]   = useState([]);
  const [total, setTotal]   = useState(0);
  const [loading, setLoading] = useState(true);
  const [q, setQ]           = useState("");
  const [needsCost, setNeedsCost] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [expandedId, setExpandedId] = useState(null);

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
                <Fragment key={p.id}>
                <tr
                    className={`border-t hover:bg-emerald-50 cursor-pointer ${
                      expandedId === p.id ? "bg-emerald-50" : ""
                    }`}
                    onClick={() =>
                      setExpandedId(expandedId === p.id ? null : p.id)}
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
                    <div className="flex items-center gap-1">
                      <span className="text-emerald-600 text-xs">
                        {expandedId === p.id ? "▼" : "◀"}
                      </span>
                      <span className="truncate" title={p.name}>{p.name}</span>
                    </div>
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
                {expandedId === p.id && (
                  <tr>
                    <td colSpan={7} className="p-0">
                      <CostHistoryPanel productId={p.id} />
                    </td>
                  </tr>
                )}
                </Fragment>
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
