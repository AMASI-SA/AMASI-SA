// Iter-245 — Unified Financial Movement entry screen.
// Supports: supplier_invoice, general_expense, fixed_asset.
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const TYPES = [
  { value: "supplier_invoice", label: "فاتورة مورد" },
  { value: "general_expense", label: "مصروف عام" },
  { value: "fixed_asset", label: "أصل ثابت" },
];

const TERMS = [
  { value: "credit", label: "آجل" },
  { value: "cash", label: "نقدي" },
  { value: "partial", label: "سداد جزئي" },
];

const WITHDRAWALS = [
  { value: "cash", label: "سحب نقدي" },
  { value: "transfer", label: "تحويل بنكي" },
  { value: "pos", label: "دفع شبكة (POS)" },
];

const PURCHASE_ROOTS = ["تكاليف المنتجات"];
const fmt = (n) =>
  Number(n || 0).toLocaleString("en-US", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
const errMsg = (e, fb) =>
  e?.response?.data?.detail || e?.message || fb;

function flattenCats(items) {
  const byParent = {};
  items.forEach((n) => {
    const k = n.parent_id || "_root_";
    if (!byParent[k]) byParent[k] = [];
    byParent[k].push(n);
  });
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

export default function FinancialMovementNewPage() {
  const [movementType, setMovementType] = useState("supplier_invoice");
  const [docDate, setDocDate] = useState(
    new Date().toISOString().slice(0, 10));
  const [docNumber, setDocNumber] = useState("");
  const [notes, setNotes] = useState("");

  const [suppliers, setSuppliers] = useState([]);
  const [supplierId, setSupplierId] = useState("");
  const [showAllCats, setShowAllCats] = useState(false);

  const [categories, setCategories] = useState([]);
  const [categoryId, setCategoryId] = useState("");

  const [paymentTerms, setPaymentTerms] = useState("cash");
  const [totalAmount, setTotalAmount] = useState("");
  const [paidAmount, setPaidAmount] = useState("");

  const [accounts, setAccounts] = useState([]);
  const [accountId, setAccountId] = useState("");
  const [withdrawal, setWithdrawal] = useState("");
  const [reference, setReference] = useState("");
  const [attachment, setAttachment] = useState(null);

  const [lineItems, setLineItems] = useState([
    { description: "", quantity: "", unit_price: "" },
  ]);
  const [saving, setSaving] = useState(false);

  // ── Fetch suppliers/categories ─────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const [s, c] = await Promise.all([
          api.get("/suppliers?status=active"),
          api.get("/expense-category-tree?include_inactive=false"),
        ]);
        setSuppliers(s.data.items || []);
        setCategories(c.data.items || []);
      } catch (e) {
        toast.error(errMsg(e, "فشل تحميل البيانات"));
      }
    })();
  }, []);

  // ── Refresh accounts when amount changes ───────────────────────
  const numericPaid = useMemo(() => {
    const t = Number(totalAmount || 0);
    if (paymentTerms === "credit") return 0;
    if (paymentTerms === "cash") return t;
    return Number(paidAmount || 0);
  }, [paymentTerms, totalAmount, paidAmount]);

  useEffect(() => {
    if (numericPaid <= 0) {
      setAccounts([]);
      return;
    }
    (async () => {
      try {
        const { data } = await api.get(
          "/financial-movements/accounts-with-availability",
          { params: { amount: numericPaid } });
        setAccounts(data.items || []);
      } catch (e) {
        toast.error(errMsg(e, "فشل تحميل الحسابات"));
      }
    })();
  }, [numericPaid]);

  const selectedSupplier = suppliers.find((s) => s.id === supplierId);
  const supplierCatIds = new Set(selectedSupplier?.category_ids || []);

  const filteredCats = useMemo(() => {
    const flat = flattenCats(categories);
    if (movementType !== "supplier_invoice" || showAllCats
        || !selectedSupplier || supplierCatIds.size === 0) {
      return flat;
    }
    // Keep only the supplier's categories and their ancestors.
    const keep = new Set();
    categories.forEach((c) => {
      if (supplierCatIds.has(c.id)) {
        keep.add(c.id);
        (c.path_ids || []).forEach((id) => keep.add(id));
      }
    });
    return flat.filter((r) => keep.has(r.node.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categories, supplierId, showAllCats, movementType]);

  const selectedCat = categories.find((c) => c.id === categoryId);
  const isPurchase = useMemo(() => {
    if (movementType !== "supplier_invoice") return false;
    if (!selectedCat) return false;
    return PURCHASE_ROOTS.includes((selectedCat.path || [])[0]);
  }, [movementType, selectedCat]);

  const selectedAcc = accounts.find((a) => a.id === accountId);
  const isBank = (selectedAcc?.account_type || "").toLowerCase() === "bank";

  // ── Line items helpers ────────────────────────────────────────
  function updateLine(idx, key, val) {
    setLineItems((rows) =>
      rows.map((r, i) => (i === idx ? { ...r, [key]: val } : r)));
  }
  function addLine() {
    setLineItems((rows) =>
      [...rows, { description: "", quantity: "", unit_price: "" }]);
  }
  function removeLine(idx) {
    setLineItems((rows) => rows.filter((_, i) => i !== idx));
  }
  const lineSum = useMemo(
    () => lineItems.reduce(
      (s, r) => s + Number(r.quantity || 0) * Number(r.unit_price || 0),
      0), [lineItems]);

  async function onFileChange(e) {
    const f = e.target.files?.[0];
    if (!f) return setAttachment(null);
    if (f.size > 5 * 1024 * 1024) {
      toast.error("الحد الأقصى 5MB");
      e.target.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const b64 = (reader.result || "").toString().split(",")[1];
      setAttachment({
        filename: f.name,
        content_type: f.type,
        base64: b64,
      });
    };
    reader.readAsDataURL(f);
  }

  async function save() {
    if (!categoryId) return toast.error("اختر التصنيف");
    const total = Number(totalAmount || 0);
    if (total <= 0) return toast.error("الإجمالي مطلوب");
    if (movementType === "supplier_invoice" && !supplierId)
      return toast.error("اختر المورد");

    const payload = {
      movement_type: movementType,
      doc_date: docDate,
      doc_number: docNumber || null,
      notes: notes || null,
      supplier_id: supplierId || null,
      category_id: categoryId,
      payment_terms: paymentTerms,
      total_amount: total,
      paid_amount: paymentTerms === "partial"
        ? Number(paidAmount || 0) : 0,
      paid_from_account_id: paymentTerms === "credit"
        ? null : accountId || null,
      withdrawal_method: isBank ? withdrawal || null : null,
      reference_number: reference || null,
      attachment: (withdrawal === "transfer" && attachment) || null,
      line_items: isPurchase
        ? lineItems
            .filter((r) => r.description && Number(r.quantity) > 0)
            .map((r) => ({
              description: r.description,
              quantity: Number(r.quantity),
              unit_price: Number(r.unit_price),
            }))
        : [],
    };

    setSaving(true);
    try {
      await api.post("/financial-movements", payload);
      toast.success("تم اعتماد العملية وإنشاء القيد المحاسبي");
      // Reset
      setDocNumber(""); setNotes(""); setTotalAmount("");
      setPaidAmount(""); setAttachment(null);
      setLineItems([{ description: "", quantity: "", unit_price: "" }]);
    } catch (e) {
      toast.error(errMsg(e, "فشل الحفظ"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5" dir="rtl"
         data-testid="financial-movement-new-page">
      <header>
        <h1 className="text-2xl font-bold">حركة مالية جديدة</h1>
        <p className="text-sm text-gray-600 mt-1">
          النظام الموحَّد الجديد (Iter-245). كل عملية تُنشئ قيد
          Double-Entry تلقائياً في الـ Ledger.
        </p>
      </header>

      <div className="bg-white border rounded-lg p-5 space-y-4">
        {/* Type + date */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Field label="نوع العملية">
            <select value={movementType}
              onChange={(e) => setMovementType(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm"
              data-testid="mv-type-select">
              {TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </Field>
          <Field label="تاريخ المستند">
            <input type="date" value={docDate}
              onChange={(e) => setDocDate(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm"
              data-testid="mv-date-input" />
          </Field>
          <Field label="رقم المستند (اختياري)">
            <input value={docNumber}
              onChange={(e) => setDocNumber(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm"
              data-testid="mv-doc-number" />
          </Field>
        </div>

        {/* Supplier (only for supplier_invoice) */}
        {movementType === "supplier_invoice" && (
          <Field label="المورد">
            <select value={supplierId}
              onChange={(e) => {
                setSupplierId(e.target.value);
                setCategoryId("");
              }}
              className="w-full border rounded px-3 py-2 text-sm"
              data-testid="mv-supplier-select">
              <option value="">— اختر المورد —</option>
              {suppliers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.company_name} ({s.contact_person})
                </option>
              ))}
            </select>
            {selectedSupplier && (
              <label className="text-xs flex items-center gap-1 mt-2 text-emerald-700">
                <input type="checkbox" checked={showAllCats}
                  onChange={(e) => setShowAllCats(e.target.checked)}
                  data-testid="mv-show-all-cats" />
                إظهار جميع التصنيفات (تجاوز تخصصات المورد)
              </label>
            )}
          </Field>
        )}

        {/* Category */}
        <Field label="التصنيف">
          <select value={categoryId}
            onChange={(e) => setCategoryId(e.target.value)}
            className="w-full border rounded px-3 py-2 text-sm"
            data-testid="mv-category-select">
            <option value="">— اختر التصنيف —</option>
            {filteredCats.map(({ node, depth }) => (
              <option key={node.id} value={node.id}
                disabled={node.parent_id == null
                          && filteredCats.some((r) =>
                              r.node.parent_id === node.id)}>
                {"".padStart(depth * 2, "·")} {node.name}
              </option>
            ))}
          </select>
          {selectedCat && (
            <p className="text-xs text-gray-500 mt-1"
               data-testid="mv-category-path">
              المسار: {(selectedCat.path || []).join(" / ")}
            </p>
          )}
        </Field>

        {/* Line items for purchases */}
        {isPurchase && (
          <div className="border rounded p-3 bg-amber-50">
            <h3 className="text-sm font-bold mb-2">
              تفاصيل أصناف الفاتورة
            </h3>
            <table className="min-w-full text-xs">
              <thead className="bg-amber-100">
                <tr className="text-right">
                  <th className="p-2">الصنف</th>
                  <th className="p-2">الكمية</th>
                  <th className="p-2">سعر الوحدة</th>
                  <th className="p-2">الإجمالي</th>
                  <th className="p-2"></th>
                </tr>
              </thead>
              <tbody data-testid="mv-line-items-body">
                {lineItems.map((r, i) => {
                  const tot = Number(r.quantity || 0)
                              * Number(r.unit_price || 0);
                  return (
                    <tr key={i} className="border-t">
                      <td className="p-1">
                        <input value={r.description}
                          onChange={(e) =>
                            updateLine(i, "description", e.target.value)}
                          className="w-full border rounded px-2 py-1"
                          data-testid={`mv-line-desc-${i}`} />
                      </td>
                      <td className="p-1">
                        <input type="number" step="0.01" value={r.quantity}
                          onChange={(e) =>
                            updateLine(i, "quantity", e.target.value)}
                          className="w-24 border rounded px-2 py-1"
                          data-testid={`mv-line-qty-${i}`} />
                      </td>
                      <td className="p-1">
                        <input type="number" step="0.01"
                          value={r.unit_price}
                          onChange={(e) =>
                            updateLine(i, "unit_price", e.target.value)}
                          className="w-28 border rounded px-2 py-1"
                          data-testid={`mv-line-price-${i}`} />
                      </td>
                      <td className="p-1 font-mono font-bold">
                        {fmt(tot)}
                      </td>
                      <td className="p-1">
                        <button type="button"
                          onClick={() => removeLine(i)}
                          className="text-red-600 text-xs">
                          حذف
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="flex items-center justify-between mt-2">
              <button type="button" onClick={addLine}
                className="bg-emerald-600 text-white px-3 py-1 text-xs rounded"
                data-testid="mv-add-line-btn">
                + صنف
              </button>
              <span className="text-sm font-bold"
                    data-testid="mv-lines-sum">
                مجموع الأصناف: {fmt(lineSum)} ر.س
              </span>
            </div>
            <p className="text-[11px] text-amber-700 mt-2">
              يجب أن يطابق مجموع الأصناف الإجمالي أدناه.
            </p>
          </div>
        )}

        {/* Amount + terms */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Field label="الإجمالي *">
            <input type="number" step="0.01" value={totalAmount}
              onChange={(e) => setTotalAmount(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm"
              data-testid="mv-total-input" />
          </Field>
          <Field label="طريقة السداد">
            <select value={paymentTerms}
              onChange={(e) => setPaymentTerms(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm"
              data-testid="mv-terms-select">
              {TERMS.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </Field>
          {paymentTerms === "partial" && (
            <Field label="المبلغ المدفوع">
              <input type="number" step="0.01" value={paidAmount}
                onChange={(e) => setPaidAmount(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm"
                data-testid="mv-paid-input" />
            </Field>
          )}
        </div>

        {/* Account picker with availability greying */}
        {paymentTerms !== "credit" && (
          <Field label="الحساب الدافع">
            <select value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm"
              data-testid="mv-account-select">
              <option value="">— اختر الحساب —</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}
                  disabled={!a.is_sufficient}
                  style={!a.is_sufficient ? { color: "#aaa" } : null}>
                  {a.name} — {fmt(a.available_balance)} ر.س
                  {!a.is_sufficient ? " (غير كافٍ)" : ""}
                </option>
              ))}
            </select>
          </Field>
        )}

        {/* Bank-specific withdrawal method */}
        {paymentTerms !== "credit" && isBank && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label="طريقة السحب">
              <select value={withdrawal}
                onChange={(e) => setWithdrawal(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm"
                data-testid="mv-withdrawal-select">
                <option value="">— اختر —</option>
                {WITHDRAWALS.map((w) => (
                  <option key={w.value} value={w.value}>
                    {w.label}
                  </option>
                ))}
              </select>
            </Field>
            {(withdrawal === "transfer" || withdrawal === "pos") && (
              <Field label="رقم المرجع (اختياري)">
                <input value={reference}
                  onChange={(e) => setReference(e.target.value)}
                  className="w-full border rounded px-3 py-2 text-sm"
                  data-testid="mv-reference-input" />
              </Field>
            )}
            {withdrawal === "transfer" && (
              <Field label="صورة الإيصال (اختياري ≤5MB)">
                <input type="file"
                  accept="image/*,application/pdf"
                  onChange={onFileChange}
                  className="w-full text-sm"
                  data-testid="mv-receipt-input" />
                {attachment && (
                  <p className="text-xs text-emerald-700 mt-1">
                    ✓ {attachment.filename}
                  </p>
                )}
              </Field>
            )}
          </div>
        )}

        <Field label="ملاحظات">
          <textarea value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            className="w-full border rounded px-3 py-2 text-sm"
            data-testid="mv-notes-input" />
        </Field>

        <div className="flex justify-end gap-2">
          <button type="button" onClick={save} disabled={saving}
            className="bg-emerald-600 text-white px-6 py-2 rounded font-semibold disabled:opacity-50"
            data-testid="mv-save-btn">
            {saving ? "جارٍ الحفظ..." : "💾 اعتماد العملية"}
          </button>
        </div>
      </div>
    </div>
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
