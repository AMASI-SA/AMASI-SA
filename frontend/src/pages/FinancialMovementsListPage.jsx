// Iter-245 — Financial Movements list page.
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const TYPE_LABEL = {
  supplier_invoice: "فاتورة مورد",
  general_expense: "مصروف عام",
  fixed_asset: "أصل ثابت",
};
const TERM_LABEL = {
  credit: "آجل", cash: "نقدي", partial: "جزئي",
};
const WITH_LABEL = {
  cash: "كاش", transfer: "تحويل", pos: "شبكة",
};

const fmt = (n) =>
  Number(n || 0).toLocaleString("en-US", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
const errMsg = (e, fb) =>
  e?.response?.data?.detail || e?.message || fb;

export default function FinancialMovementsListPage() {
  const [items, setItems] = useState([]);
  const [suppliers, setSuppliers] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState(null);

  const [movType, setMovType] = useState("");
  const [supplierId, setSupplierId] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [search, setSearch] = useState("");

  async function load() {
    setLoading(true);
    try {
      const params = {};
      if (movType) params.movement_type = movType;
      if (supplierId) params.supplier_id = supplierId;
      if (categoryId) params.category_id = categoryId;
      if (fromDate) params.from_date = fromDate;
      if (toDate) params.to_date = toDate;
      const [m, s, c] = await Promise.all([
        api.get("/financial-movements", { params }),
        api.get("/suppliers?status=active"),
        api.get("/expense-category-tree?include_inactive=false"),
      ]);
      setItems(m.data.items || []);
      setSuppliers(s.data.items || []);
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
  }, [movType, supplierId, categoryId, fromDate, toDate]);

  const filtered = useMemo(() => {
    if (!search) return items;
    const q = search.toLowerCase();
    return items.filter((m) =>
      (m.doc_number || "").toLowerCase().includes(q)
      || (m.supplier_snapshot?.company_name || "").toLowerCase().includes(q)
      || (m.notes || "").toLowerCase().includes(q)
      || (m.category_path || []).join(" ").toLowerCase().includes(q)
    );
  }, [items, search]);

  const totals = useMemo(() => {
    let total = 0, paid = 0, remaining = 0;
    filtered.forEach((m) => {
      total += Number(m.total_amount || 0);
      paid += Number(m.paid_amount || 0);
      remaining += Number(m.remaining_amount || 0);
    });
    return { total, paid, remaining, count: filtered.length };
  }, [filtered]);

  return (
    <div className="space-y-5" dir="rtl"
         data-testid="financial-movements-list-page">
      <header className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">📑 قائمة الحركات المالية</h1>
          <p className="text-sm text-gray-600 mt-1">
            كل الحركات المُدخَلة عبر النظام الموحَّد الجديد (Iter-245).
          </p>
        </div>
        <a href="/financial-movement/new"
           className="bg-emerald-600 text-white px-4 py-2 rounded font-semibold text-sm"
           data-testid="goto-new-movement">
          + حركة جديدة
        </a>
      </header>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="عدد الحركات" value={totals.count} testid="stat-count" />
        <Stat label="الإجمالي" value={`${fmt(totals.total)} ر.س`} testid="stat-total" />
        <Stat label="المدفوع" value={`${fmt(totals.paid)} ر.س`} color="text-emerald-700" testid="stat-paid" />
        <Stat label="المتبقي" value={`${fmt(totals.remaining)} ر.س`} color="text-amber-700" testid="stat-remaining" />
      </div>

      {/* Filters */}
      <div className="bg-white border rounded-lg p-4 grid grid-cols-1 md:grid-cols-6 gap-3">
        <input placeholder="🔎 بحث (رقم، مورد، ملاحظة)..."
          value={search} onChange={(e) => setSearch(e.target.value)}
          className="border rounded px-3 py-2 text-sm md:col-span-2"
          data-testid="fm-search-input" />
        <select value={movType} onChange={(e) => setMovType(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
          data-testid="fm-type-filter">
          <option value="">كل الأنواع</option>
          {Object.entries(TYPE_LABEL).map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
        <select value={supplierId}
          onChange={(e) => setSupplierId(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
          data-testid="fm-supplier-filter">
          <option value="">كل الموردين</option>
          {suppliers.map((s) => (
            <option key={s.id} value={s.id}>{s.company_name}</option>
          ))}
        </select>
        <select value={categoryId}
          onChange={(e) => setCategoryId(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
          data-testid="fm-category-filter">
          <option value="">كل التصنيفات</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {(c.path || [c.name]).join(" / ")}
            </option>
          ))}
        </select>
        <div className="flex gap-1">
          <input type="date" value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            className="border rounded px-2 py-2 text-xs w-full"
            data-testid="fm-from-date" />
          <input type="date" value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            className="border rounded px-2 py-2 text-xs w-full"
            data-testid="fm-to-date" />
        </div>
      </div>

      <div className="bg-white border rounded-lg overflow-x-auto">
        {loading ? (
          <p className="text-center py-8 text-gray-500">جارٍ التحميل...</p>
        ) : filtered.length === 0 ? (
          <p className="text-center py-8 text-gray-500"
             data-testid="fm-empty">
            لا توجد حركات تطابق الفلاتر.
          </p>
        ) : (
          <table className="min-w-full text-xs">
            <thead className="bg-gray-100">
              <tr className="text-right">
                <th className="p-2">التاريخ</th>
                <th className="p-2">النوع</th>
                <th className="p-2">المورد</th>
                <th className="p-2">المسار</th>
                <th className="p-2">الإجمالي</th>
                <th className="p-2">المدفوع</th>
                <th className="p-2">المتبقي</th>
                <th className="p-2">السداد</th>
                <th className="p-2">الحساب</th>
                <th className="p-2">مرفق</th>
                <th className="p-2">الحالة المحاسبية</th>
                <th className="p-2"></th>
              </tr>
            </thead>
            <tbody data-testid="fm-table-body">
              {filtered.map((m) => (
                <tr key={m.id} className="border-t hover:bg-gray-50"
                    data-testid={"fm-row-" + m.id}>
                  <td className="p-2 font-mono">{m.doc_date}</td>
                  <td className="p-2">{TYPE_LABEL[m.movement_type]}</td>
                  <td className="p-2">
                    {m.supplier_snapshot?.company_name || "—"}
                  </td>
                  <td className="p-2 text-[11px]">
                    {(m.category_path || []).join(" / ")}
                  </td>
                  <td className="p-2 font-mono font-bold">
                    {fmt(m.total_amount)}
                  </td>
                  <td className="p-2 font-mono text-emerald-700">
                    {fmt(m.paid_amount)}
                  </td>
                  <td className="p-2 font-mono text-amber-700">
                    {fmt(m.remaining_amount)}
                  </td>
                  <td className="p-2">
                    {TERM_LABEL[m.payment_terms] || "—"}
                    {m.withdrawal_method && (
                      <span className="ms-1 text-[10px] bg-blue-50 text-blue-700 px-1 rounded">
                        {WITH_LABEL[m.withdrawal_method]}
                      </span>
                    )}
                  </td>
                  <td className="p-2 text-[11px]">
                    {m.paid_from_account_snapshot?.name || "—"}
                  </td>
                  <td className="p-2">
                    {m.receipt_attachment ? "📎" : "—"}
                  </td>
                  {/* Iter-250b · P1.5.aa — Posting status badge. */}
                  <td className="p-2">
                    {m.posting_status === "posted_to_gl" ? (
                      <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800 border border-emerald-300"
                            title={"GL entries: " + (m.gl_entries_count || 0)}
                            data-testid={"fm-posting-" + m.id}>
                        ✓ مُرحَّلة
                      </span>
                    ) : m.posting_status === "posted_failed" ? (
                      <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-100 text-rose-800 border border-rose-300"
                            title={m.posting_status_reason || ""}
                            data-testid={"fm-posting-" + m.id}>
                        ✕ فشل ترحيل
                      </span>
                    ) : (
                      <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-100 text-amber-800 border border-amber-300"
                            title={m.posting_status_reason || ""}
                            data-testid={"fm-posting-" + m.id}>
                        ⚠️ غير مُرحَّلة
                      </span>
                    )}
                  </td>
                  <td className="p-2">
                    <button onClick={() => setDetail(m)}
                      className="text-blue-700 text-xs hover:underline"
                      data-testid={"fm-detail-" + m.id}>
                      تفاصيل
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {detail && (
        <DetailModal mv={detail} onClose={() => setDetail(null)} />
      )}
    </div>
  );
}

function Stat({ label, value, color, testid }) {
  return (
    <div className="bg-white border rounded p-3" data-testid={testid}>
      <div className="text-[11px] text-gray-500">{label}</div>
      <div className={"text-xl font-extrabold " + (color || "text-gray-800")}>
        {value}
      </div>
    </div>
  );
}

function DetailModal({ mv, onClose }) {
  const [full, setFull] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/financial-movements/" + mv.id);
        setFull(data);
      } catch {
        setFull(mv);
      }
    })();
  }, [mv.id]);

  const m = full || mv;
  const att = m.receipt_attachment;
  const attUrl = att?.base64
    ? `data:${att.content_type};base64,${att.base64}` : null;

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
         onClick={onClose}
         data-testid="fm-detail-overlay">
      <div className="bg-white rounded-xl max-w-3xl w-full max-h-[90vh] overflow-y-auto"
           onClick={(e) => e.stopPropagation()} dir="rtl"
           data-testid="fm-detail-modal">
        <div className="px-5 py-4 border-b bg-emerald-50 flex justify-between rounded-t-xl">
          <h2 className="text-lg font-extrabold">
            تفاصيل الحركة — {TYPE_LABEL[m.movement_type]}
          </h2>
          <button onClick={onClose}
            className="px-3 py-1 border rounded text-sm bg-white">
            إغلاق ✕
          </button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <Row k="التاريخ" v={m.doc_date} />
          <Row k="رقم المستند" v={m.doc_number || "—"} />
          <Row k="المورد"
            v={m.supplier_snapshot
              ? `${m.supplier_snapshot.company_name} (${m.supplier_snapshot.contact_person})`
              : "—"} />
          <Row k="التصنيف (المسار)"
            v={(m.category_path || []).join(" / ")} />
          <Row k="الإجمالي" v={fmt(m.total_amount) + " ر.س"} />
          <Row k="المدفوع" v={fmt(m.paid_amount) + " ر.س"} />
          <Row k="المتبقي" v={fmt(m.remaining_amount) + " ر.س"} />
          <Row k="طريقة السداد" v={TERM_LABEL[m.payment_terms]} />
          {m.paid_from_account_snapshot && (
            <Row k="الحساب الدافع"
              v={m.paid_from_account_snapshot.name} />
          )}
          {m.withdrawal_method && (
            <Row k="طريقة السحب"
              v={WITH_LABEL[m.withdrawal_method]} />
          )}
          {m.reference_number && (
            <Row k="رقم المرجع" v={m.reference_number} />
          )}
          {m.notes && <Row k="ملاحظات" v={m.notes} />}
          {m.ledger_txn_group_id && (
            <Row k="قيد الـ Ledger"
              v={<span className="font-mono text-xs">{m.ledger_txn_group_id}</span>} />
          )}

          {m.has_line_items && (
            <div>
              <h3 className="font-bold mt-4 mb-2">تفاصيل الأصناف</h3>
              <table className="min-w-full text-xs border">
                <thead className="bg-gray-100">
                  <tr className="text-right">
                    <th className="p-2">الصنف</th>
                    <th className="p-2">الكمية</th>
                    <th className="p-2">سعر الوحدة</th>
                    <th className="p-2">الإجمالي</th>
                  </tr>
                </thead>
                <tbody>
                  {(m.line_items || []).map((li) => (
                    <tr key={li.line_id} className="border-t">
                      <td className="p-2">{li.description}</td>
                      <td className="p-2 font-mono">{li.quantity}</td>
                      <td className="p-2 font-mono">{fmt(li.unit_price)}</td>
                      <td className="p-2 font-mono font-bold">
                        {fmt(li.total)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {att && (
            <div>
              <h3 className="font-bold mt-4 mb-2">إيصال التحويل</h3>
              {att.content_type?.startsWith("image/") ? (
                <img src={attUrl} alt={att.filename}
                  className="max-h-96 border rounded" />
              ) : (
                <a href={attUrl} target="_blank" rel="noreferrer"
                  className="text-blue-700 underline">
                  📎 {att.filename}
                </a>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div className="flex justify-between border-b pb-1">
      <span className="text-gray-500 text-xs">{k}</span>
      <span className="font-semibold">{v}</span>
    </div>
  );
}
