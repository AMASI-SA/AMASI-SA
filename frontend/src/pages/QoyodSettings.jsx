/**
 * Qoyod Settings — FINAL one-time setup page.
 *
 * After this page is fully filled & saved, the operator should not need
 * to revisit it during normal operation. The save button is GATED by
 * `/setup/validate` so the merchant cannot leave the page in a broken
 * state (missing branch/tax/payment-method mapping → block save).
 *
 * Sections (rendered top→bottom):
 *   1. Setup Status banner (validation summary, expandable issue list)
 *   2. API Key + Test Connection
 *   3. Webhook Token (Make.com inbound auth)  — unchanged
 *   4. Master switches (enabled / auto_send / auto_receipt / dry_run)
 *   5. Core IDs (Branch / Tax / Default Customer)
 *   6. Payment Method Mapping (the most important table)
 *   7. Inventory Accounts (only when product_type = inventory)
 *   8. Advanced — trigger statuses, invoice date source, product type
 *   9. Capability flags
 *  10. Setup Guide (expandable — where to find IDs in Qoyod)
 *  11. Sticky save bar
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
// Iter-290i — Searchable picker for قيود reference lists.
import { SearchableSelect } from "../components/ui/searchable-select";
import { RefreshCw } from "lucide-react";

const API = process.env.REACT_APP_BACKEND_URL + "/api";

const PRODUCT_TYPE_OPTIONS = [
  { value: "service",     label: "منتجات بدون إدارة مخزون في قيود — موصى به لربط ميزان" },
  { value: "inventory",   label: "مخزنية (Inventory) — للمتاجر بمستودع وSKUs" },
  { value: "per_product", label: "حسب إعداد كل منتج (Per Product)" },
];

// Hardcoded fallback ONLY when Salla API + observed orders are both
// unavailable. Real options come from `/salla-order-statuses`.
const FALLBACK_TRIGGER_STATUSES = [
  { slug: "completed", name: "تم التنفيذ" },
];

const INVOICE_DATE_OPTIONS = [
  { value: "trigger_status_date", label: "تاريخ انتقال الطلب للحالة المؤهلة (مُوصى به)" },
  { value: "completed_at", label: "تاريخ تنفيذ الطلب (completed_at)" },
  { value: "paid_at",      label: "تاريخ الدفع (paid_at)" },
  { value: "created_at",   label: "تاريخ الإنشاء (created_at)" },
];

// Receiving-bank rows approved by the merchant.  They are always visible
// in the Qoyod payment mapping table so routing is explicit and auditable;
// the generic bank_transfer row is not used as a substitute for them.
const RECEIVING_BANK_MAPPING_ROWS = [
  { salla_method: "bank_rajhi", qoyod_account_id: "94",
    posting_mode: "paid_receipt", label_ar: "بنك الراجحي" },
  { salla_method: "bank_ahli", qoyod_account_id: "95",
    posting_mode: "paid_receipt", label_ar: "البنك الأهلي" },
  { salla_method: "bank_inma", qoyod_account_id: "8",
    posting_mode: "paid_receipt", label_ar: "بنك الإنماء" },
];

const GENERIC_BANK_TRANSFER_KEYS = new Set([
  "bank", "bank_transfer", "wire_transfer", "تحويل_بنكي",
]);

const isGenericBankTransferMappingKey = (key) =>
  GENERIC_BANK_TRANSFER_KEYS.has(
    String(key || "").trim().toLowerCase().replace(/\s+/g, "_"));

// ─── Building blocks ────────────────────────────────────────────────
function Section({ title, subtitle, children, tone = "default" }) {
  const toneCls = tone === "danger"
    ? "border-rose-300 bg-rose-50/30"
    : tone === "success"
      ? "border-emerald-300 bg-emerald-50/30"
      : "border-slate-200 bg-white";
  return (
    <section className={`rounded-xl border ${toneCls} p-4 md:p-5 mb-4`}>
      <h3 className="text-base font-extrabold text-slate-800">{title}</h3>
      {subtitle && (
        <p className="text-[12px] text-slate-500 mt-0.5 mb-3">{subtitle}</p>
      )}
      <div className="space-y-3 mt-3">{children}</div>
    </section>
  );
}

function ToggleRow({ label, hint, checked, onChange, testid, disabled }) {
  return (
    <label className={`flex items-start justify-between gap-3 p-2 rounded-lg
                        ${disabled ? "opacity-50" : "hover:bg-slate-50 cursor-pointer"}`}>
      <span className="flex-1">
        <span className="block text-sm font-bold text-slate-700">{label}</span>
        {hint && <span className="block text-xs text-slate-500 mt-0.5">{hint}</span>}
      </span>
      <input
        type="checkbox"
        checked={!!checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        data-testid={testid}
        className="mt-1 w-5 h-5 accent-emerald-600 cursor-pointer disabled:cursor-not-allowed"
      />
    </label>
  );
}

function IDInput({
  label, value, onChange, placeholder, suggestions = [],
  testid, unsupportedHint, datalistId, disabled, required,
  invalid,
}) {
  return (
    <label className="block">
      <span className="text-xs font-bold text-slate-700">
        {label}
        {required && <span className="text-rose-600 mr-0.5">*</span>}
      </span>
      <input
        type="text"
        value={value || ""}
        onChange={(e) => onChange(e.target.value || null)}
        data-testid={testid}
        disabled={disabled}
        placeholder={placeholder}
        list={datalistId}
        className={`mt-1 w-full px-3 py-2 border rounded-lg text-sm
                    disabled:bg-slate-100 font-mono
                    ${invalid ? "border-rose-400 bg-rose-50/40"
                              : "border-slate-300"}`}
      />
      {datalistId && suggestions.length > 0 && (
        <datalist id={datalistId}>
          {suggestions.map((s) => (
            <option key={s.id || s.value} value={s.id || s.value}>
              {s.name_ar || s.name || s.label || `${s.rate ?? ""}%`}
            </option>
          ))}
        </datalist>
      )}
      {unsupportedHint && (
        <span className="text-[11px] text-amber-700 block mt-1">
          {unsupportedHint}
        </span>
      )}
    </label>
  );
}

// ─── Webhook Token UI (kept verbatim — already polished) ───────────
function WebhookTokenSection() {
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [revealedToken, setRevealedToken] = useState(null);
  const [copied, setCopied] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/integrations/qoyod/webhook-token`);
      setMeta(data?.meta || null);
    } catch (_e) {
      setMeta(null);
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const generate = async () => {
    if (meta?.configured) {
      if (!window.confirm(
        "هل تريد إعادة التوليد؟ سيتم إبطال الـ Token الحالي فوراً. " +
        "تأكّد من تحديث Make.com قبل وصول أي طلب جديد.")) return;
    }
    setGenerating(true);
    try {
      const { data } = await axios.post(
        `${API}/integrations/qoyod/webhook-token/generate`);
      if (data?.token) {
        setRevealedToken(data.token);
        setCopied(false);
        toast.success("تم التوليد — انسخ القيمة فوراً، لن تظهر مرة أخرى");
        await load();
      } else {
        toast.error("لم يتم استلام Token من الخادم");
      }
    } catch (e) {
      toast.error("فشل توليد Token");
    } finally { setGenerating(false); }
  };

  const copyToken = async () => {
    if (!revealedToken) return;
    try {
      await navigator.clipboard.writeText(revealedToken);
      setCopied(true);
      toast.success("تم النسخ إلى الحافظة");
    } catch (_) { toast.error("تعذّر النسخ — انسخ يدوياً"); }
  };

  const revoke = async () => {
    if (!window.confirm(
      "إبطال Webhook Token الحالي؟ Make.com لن يستطيع الإرسال بعد ذلك."
    )) return;
    try {
      await axios.delete(`${API}/integrations/qoyod/webhook-token`);
      toast.success("تم إبطال الـ Token");
      await load();
    } catch (_) { toast.error("فشل الإبطال"); }
  };

  if (loading) {
    return (
      <Section title="Webhook Token (Make.com → ميزان)">
        <div className="text-sm text-slate-500" data-testid="webhook-token-loading">
          جاري التحميل…
        </div>
      </Section>
    );
  }

  return (
    <Section title="Webhook Token (Make.com → ميزان)"
             subtitle="القيمة المُستخدمة كـ shared secret بين Make.com وميزان">
      {revealedToken && (
        <div className="rounded-lg border-2 border-amber-400 bg-amber-50 p-3 space-y-2"
             data-testid="webhook-token-revealed">
          <div className="flex items-start gap-2">
            <span className="text-amber-700 text-lg">⚠</span>
            <div className="flex-1">
              <div className="text-sm font-extrabold text-amber-900">
                هذه القيمة لن تظهر مرة أخرى — انسخها الآن والصقها في Make.com.
              </div>
            </div>
          </div>
          <div className="flex gap-2 items-stretch">
            <code className="flex-1 px-3 py-2 text-xs font-mono break-all
                              bg-white border border-amber-300 rounded select-all"
                  dir="ltr" data-testid="webhook-token-plaintext">
              {revealedToken}
            </code>
            <button onClick={copyToken}
              className={`px-3 py-2 text-sm font-bold rounded text-white
                          ${copied ? "bg-emerald-600 hover:bg-emerald-700"
                                   : "bg-slate-900 hover:bg-black"}`}
              data-testid="btn-copy-webhook-token">
              {copied ? "✓ تم النسخ" : "📋 نسخ"}
            </button>
            <button onClick={() => { setRevealedToken(null); setCopied(false); }}
              className="px-3 py-2 text-sm font-bold rounded bg-slate-200 hover:bg-slate-300"
              data-testid="btn-dismiss-webhook-token">
              إغلاق
            </button>
          </div>
        </div>
      )}

      {meta?.configured ? (
        <div className="flex items-center justify-between bg-emerald-50 border border-emerald-200 rounded-lg p-3"
             data-testid="webhook-token-configured">
          <div>
            <div className="text-sm font-bold text-emerald-800">
              Webhook Token مفعّل ومُشفَّر
            </div>
            <div className="text-xs text-emerald-700 font-mono mt-1"
                 data-testid="webhook-token-fingerprint">
              Fingerprint: {meta.fingerprint || "—"}
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={generate} disabled={generating}
              className="px-3 py-2 text-sm font-bold rounded-lg bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-50"
              data-testid="btn-regenerate-webhook-token">
              {generating ? "جاري التوليد…" : "إعادة التوليد"}
            </button>
            <button onClick={revoke}
              className="px-3 py-2 text-sm font-bold rounded-lg bg-rose-100 text-rose-700 hover:bg-rose-200"
              data-testid="btn-revoke-webhook-token">
              إبطال
            </button>
          </div>
        </div>
      ) : (
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-4"
             data-testid="webhook-token-empty">
          <div className="text-sm text-slate-700 mb-3">
            لم يتم توليد Webhook Token بعد. هذا الـ Token يسمح لـ Make.com بإرسال
            طلبات سلة إلى مسار قيود داخل ميزان.
          </div>
          <button onClick={generate} disabled={generating}
            className="px-4 py-2 text-sm font-bold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
            data-testid="btn-generate-webhook-token">
            {generating ? "جاري التوليد…" : "🔑 توليد Webhook Token"}
          </button>
        </div>
      )}
    </Section>
  );
}

// ─── Setup Status banner ───────────────────────────────────────────
function SetupStatusBanner({ validation, onJumpTo }) {
  if (!validation) return null;
  const blockers = validation.issues.filter((i) => i.severity === "blocker");
  const warnings = validation.issues.filter((i) => i.severity === "warning");
  const ok = validation.ok;

  return (
    <div
      className={`rounded-xl border p-3 md:p-4 mb-4 ${ok
        ? "bg-emerald-50 border-emerald-300"
        : "bg-rose-50 border-rose-300"}`}
      data-testid="setup-status-banner">
      <div className="flex items-start gap-3">
        <span className={`text-2xl ${ok ? "text-emerald-700" : "text-rose-700"}`}>
          {ok ? "✅" : "⛔"}
        </span>
        <div className="flex-1">
          <div className={`text-sm font-extrabold ${ok ? "text-emerald-900" : "text-rose-900"}`}>
            {ok
              ? "الإعداد مكتمل وجاهز للحفظ"
              : `لا يمكن إكمال الإعداد — ${blockers.length} مشكلة حاجبة`}
          </div>
          {!ok && (
            <p className="text-xs text-rose-700 mt-0.5">
              أكمل الحقول المطلوبة أدناه. زر الحفظ سيُفعَّل تلقائياً بعد حلّ الكل.
            </p>
          )}
          {ok && warnings.length > 0 && (
            <p className="text-xs text-emerald-700 mt-0.5">
              {warnings.length} تحذير اختياري — يمكنك الحفظ والمتابعة.
            </p>
          )}
          {(blockers.length + warnings.length) > 0 && (
            <ul className="mt-2 space-y-1" data-testid="setup-issues-list">
              {validation.issues.map((iss, i) => (
                <li key={i}
                    className={`text-[12px] flex items-start gap-1.5
                                ${iss.severity === "blocker" ? "text-rose-800" : "text-amber-800"}`}
                    data-testid={`setup-issue-${iss.code}`}>
                  <span>{iss.severity === "blocker" ? "🔴" : "🟡"}</span>
                  <span className="flex-1">{iss.message}</span>
                  {iss.field && onJumpTo && (
                    <button
                      type="button"
                      onClick={() => onJumpTo(iss.field)}
                      className="text-[11px] text-sky-700 hover:underline shrink-0"
                      data-testid={`btn-jump-to-${iss.field}`}>
                      انتقل
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Payment Method Mapping table ─────────────────────────────────
function PaymentMethodMappingTable({
  mapping, onChange, catalogue, used, accountsSuggestions, accountsUnsupported,
  // Iter-290i.2 — Account picker (replaces raw text input).
  // `accountsList` is the full {id,name,code,type,kind}[] fetched from
  // qoyod reference-lists; `accountsListUnavailable` is true when the
  // fetch failed (so we never label saved ids as "missing").
  accountsList = [],
  accountsListUnavailable = false,
  accountsUnavailableReason = null,
}) {
  // ── Iter-293 helpers — posting_mode + COD family detection ─────────
  // Keep these in sync with backend/payment_methods.py (`is_cod_family`
  // + PAYMENT_METHOD_ALIASES). The UI does its own normalisation to
  // (a) lock the COD row before the user saves, and (b) NOT mark COD
  // rows as "blocker — needs account".
  const COD_DIRECT_KEYS = ["cod"];
  const COD_ALIAS_KEYS = [
    "cash_on_delivery", "cash",
    "الدفع_عند_الاستلام", "النوع_عند_الاستلام",
    "الدفع_نقدا_عند_الاستلام", "نقد_عند_الاستلام", "نقدًا_عند_الاستلام",
  ];
  const isCodKey = (k) => {
    const norm = String(k || "").trim().toLowerCase().replace(/\s+/g, "_");
    return COD_DIRECT_KEYS.includes(norm) || COD_ALIAS_KEYS.includes(norm);
  };
  const isBankTransferKey = (k) => {
    const norm = String(k || "").trim().toLowerCase().replace(/\s+/g, "_");
    return norm === "bank_transfer" || norm === "bank"
        || norm === "wire_transfer" || norm === "تحويل_بنكي"
        || ["bank_rajhi", "bank_ahli", "bank_inma"].includes(norm);
  };
  const isSpecificReceivingBankKey = (k) => {
    const norm = String(k || "").trim().toLowerCase().replace(/\s+/g, "_");
    return ["bank_rajhi", "bank_ahli", "bank_inma"].includes(norm);
  };
  const isGenericBankTransferKey = isGenericBankTransferMappingKey;
  const POSTING_MODE_OPTIONS = [
    { value: "paid_receipt", label: "مدفوع — ينشئ سند قبض" },
    { value: "credit_invoice_only", label: "آجل — فاتورة فقط (بدون سند)" },
    { value: "disabled", label: "غير مفعّل" },
  ];
  // Derive the effective posting_mode for a row, applying the COD lock.
  // This is the same rule the backend enforces via `coerce_cod_rows` —
  // we run it client-side so the UI shows the truth even before save.
  const effectiveMode = (key, row) => {
    if (isCodKey(key)) return "credit_invoice_only";
    const raw = row?.posting_mode || "paid_receipt";
    return ["paid_receipt", "credit_invoice_only", "disabled"].includes(raw)
      ? raw : "paid_receipt";
  };

  // Indexed access for fast lookup
  const usedByKey = useMemo(() => {
    const m = new Map();
    for (const u of used || []) m.set(u.key, u);
    return m;
  }, [used]);
  const labelFor = (key) => {
    const bankRow = RECEIVING_BANK_MAPPING_ROWS.find(
      (row) => row.salla_method === key);
    if (bankRow) return bankRow.label_ar;
    const fromCat = (catalogue || []).find((c) => c.key === key);
    if (fromCat) return fromCat.label_ar;
    const fromUsed = usedByKey.get(key);
    if (fromUsed && fromUsed.label_ar) return fromUsed.label_ar;
    return key;
  };

  // Mandatory rows = every USED key (must be mapped) +
  // every CANONICAL key already present in mapping (so editing doesn't drop them).
  const mappingByKey = useMemo(() => {
    const m = new Map();
    for (const row of RECEIVING_BANK_MAPPING_ROWS) {
      m.set(row.salla_method, row);
    }
    for (const row of mapping || []) {
      const k = (row.salla_method || "").toLowerCase();
      // Generic bank transfer is not a configurable destination.  Routing
      // is allowed only after the actual receiving bank is identified.
      if (k && !isGenericBankTransferKey(k)) m.set(k, row);
    }
    return m;
  }, [mapping]);

  const visibleKeys = useMemo(() => {
    const out = new Set();
    for (const row of RECEIVING_BANK_MAPPING_ROWS) {
      out.add(row.salla_method);
    }
    for (const u of used || []) {
      if (u.key && !isGenericBankTransferKey(u.key)) out.add(u.key);
    }
    for (const k of mappingByKey.keys()) out.add(k);
    return [...out].sort();
  }, [used, mappingByKey]);

  const allCatalogueKeys = (catalogue || []).map((c) => c.key);
  const addableKeys = allCatalogueKeys.filter(
    (k) => !visibleKeys.includes(k) && !isGenericBankTransferKey(k));
  const [selectedAddKey, setSelectedAddKey] = useState("");

  // Iter-293 — Update account_id OR posting_mode on a row. For COD
  // rows we IGNORE attempted mode changes (the UI dropdown is also
  // disabled, but defense in depth) AND clear any stale account_id.
  const updateRow = (key, { account_id, posting_mode } = {}) => {
    const next = [...(mapping || [])];
    const idx = next.findIndex(
      (r) => (r.salla_method || "").toLowerCase() === key);
    const codLock = isCodKey(key);
    const baseRow = idx >= 0 ? next[idx] : {
      salla_method: key, qoyod_account_id: "", label_ar: labelFor(key),
    };
    const newRow = { ...baseRow };
    if (codLock) {
      newRow.posting_mode = "credit_invoice_only";
      newRow.qoyod_account_id = null;
    } else {
      if (posting_mode !== undefined) {
        // Iter-293.1 — bank_transfer can never be credit_invoice_only.
        // Defense-in-depth: even if the dropdown's filter is bypassed,
        // we coerce the value back to paid_receipt before persisting.
        const safeMode = (isBankTransferKey(key)
                          && posting_mode === "credit_invoice_only")
          ? "paid_receipt" : posting_mode;
        newRow.posting_mode = safeMode;
        // Disabled / credit_invoice_only don't need an account, clear it.
        if (safeMode !== "paid_receipt") newRow.qoyod_account_id = null;
      }
      if (account_id !== undefined) {
        newRow.qoyod_account_id = account_id || "";
      }
    }
    if (idx >= 0) next[idx] = newRow; else next.push(newRow);
    onChange(next);
  };

  const removeRow = (key) => {
    const next = (mapping || []).filter(
      (r) => (r.salla_method || "").toLowerCase() !== key);
    onChange(next);
  };

  const addRow = () => {
    if (!selectedAddKey) return;
    const next = [...(mapping || []), {
      salla_method: selectedAddKey,
      qoyod_account_id: "",
      label_ar: labelFor(selectedAddKey),
    }];
    onChange(next);
    setSelectedAddKey("");
  };

  return (
    <div data-testid="payment-method-mapping-table">
      {visibleKeys.length === 0 && (
        <div className="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded p-3 mb-2"
             data-testid="payment-methods-empty">
          لا توجد طرق دفع مرصودة بعد. أضف يدوياً أي طريقة دفع تستخدمها من قائمة
          الإضافة في الأسفل.
        </div>
      )}

      {visibleKeys.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full text-sm" dir="rtl">
            <thead className="bg-slate-50 text-slate-700 text-xs">
              <tr>
                <th className="text-right font-bold px-3 py-2 w-1/4">
                  طريقة الدفع في سلة
                </th>
                <th className="text-right font-bold px-3 py-2 w-56">
                  وضع الترحيل لقيود
                </th>
                <th className="text-right font-bold px-3 py-2">
                  حساب قيود
                </th>
                <th className="text-right font-bold px-3 py-2 w-24">الحالة</th>
                <th className="w-12"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visibleKeys.map((key) => {
                const row = mappingByKey.get(key);
                const accId = row?.qoyod_account_id || "";
                const mode = effectiveMode(key, row);
                const codLock = isCodKey(key);
                const bankTransferRow = isBankTransferKey(key);
                const specificBankRow = isSpecificReceivingBankKey(key);
                const genericBankRow = isGenericBankTransferKey(key);
                const usedRow = usedByKey.get(key);
                const isUsed = !!usedRow;
                const resolvedViaAlias =
                  !accId && usedRow?.mapped_via === "alias"
                  && usedRow?.matched_key;
                // Iter-293 — A row "needs an account" ONLY when its
                // posting_mode is paid_receipt. COD and disabled rows
                // are intentionally accountless.
                const needsAccount = mode === "paid_receipt";
                const missing = needsAccount && isUsed && !accId
                                && !resolvedViaAlias;
                return (
                  <tr key={key}
                      className={`${missing ? "bg-rose-50/40"
                                 : codLock ? "bg-amber-50/40"
                                 : specificBankRow ? "bg-emerald-50/30"
                                 : genericBankRow ? "bg-orange-50/40"
                                 : resolvedViaAlias ? "bg-sky-50/40" : ""}`}
                      data-testid={`pm-row-${key}`}>
                    <td className="px-3 py-2 align-middle">
                      <div className="font-bold text-slate-800">{labelFor(key)}</div>
                      <div className="text-[10px] text-slate-500 font-mono">
                        {key}
                        {usedRow?.native_examples?.length > 0
                          && usedRow.native_examples[0] !== key && (
                          <span
                            data-testid={`pm-native-${key}`}
                            title="القيمة الأصلية من سلة"
                            className="mr-2 inline-block px-1.5 py-0.5 rounded
                                       bg-slate-100 text-slate-700 font-bold"
                            dir="auto">
                            من سلة: «{usedRow.native_examples[0]}»
                          </span>
                        )}
                        {isUsed && (
                          <span className="mr-2 inline-block px-1.5 py-0.5 rounded
                                           bg-amber-100 text-amber-800 font-extrabold">
                            مُستخدم ({usedRow?.count ?? 0})
                          </span>
                        )}
                        {resolvedViaAlias && (
                          <span
                            data-testid={`pm-alias-hint-${key}`}
                            title={`يُحلّ تلقائياً إلى ${usedRow.matched_key}`}
                            className="mr-2 inline-block px-1.5 py-0.5 rounded
                                       bg-sky-100 text-sky-800 font-extrabold">
                            عبر {labelFor(usedRow.matched_key)}
                          </span>
                        )}
                        {genericBankRow && (
                          <span
                            title="حالياً مربوط بحساب عام مؤقت — لا يعتبر جاهزاً للزكاة والضريبة حتى يتم Iter-294 (Routing حسب البنك المستلم)"
                            className="mr-2 inline-block px-1.5 py-0.5 rounded
                                       bg-orange-100 text-orange-900 font-extrabold"
                            data-testid={`pm-bank-warn-${key}`}>
                            Legacy — يحتاج Routing حسب البنك
                          </span>
                        )}
                        {specificBankRow && (
                          <span
                            title="حساب قيود محدد حسب البنك المستلم في طلب سلة"
                            className="mr-2 inline-block px-1.5 py-0.5 rounded
                                       bg-emerald-100 text-emerald-900 font-extrabold"
                            data-testid={`pm-bank-routed-${key}`}>
                            ✓ Routing مباشر حسب البنك
                          </span>
                        )}
                      </div>
                    </td>
                    {/* Posting mode picker — locked for COD rows.
                        Iter-293.1: bank_transfer rows cannot be
                        credit_invoice_only (must use receiving-bank
                        routing in Iter-294). */}
                    <td className="px-3 py-2 align-middle">
                      <select
                        value={mode}
                        disabled={codLock}
                        onChange={(e) => updateRow(key, { posting_mode: e.target.value })}
                        data-testid={`pm-mode-select-${key}`}
                        className={`w-full px-2 py-1.5 border rounded text-xs
                                    ${codLock ? "bg-amber-50 text-amber-900 cursor-not-allowed border-amber-200"
                                              : "border-slate-300 bg-white"}`}>
                        {POSTING_MODE_OPTIONS
                          .filter((o) => !(bankTransferRow && o.value === "credit_invoice_only"))
                          .map((o) => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                          ))}
                      </select>
                      {codLock && (
                        <div className="text-[10px] text-amber-700 font-bold mt-1"
                             data-testid={`pm-cod-locked-${key}`}>
                          🔒 COD لا يحتاج حساب قبض — مرحّل كفاتورة آجلة فقط
                        </div>
                      )}
                      {genericBankRow && (
                        <div className="text-[10px] text-orange-800 font-bold mt-1"
                             data-testid={`pm-bank-no-credit-${key}`}>
                          🔒 التحويل البنكي يجب أن يكون مدفوع — حسب البنك المستلم (Iter-294).
                          غير مسموح بـ "آجل".
                        </div>
                      )}
                      {mode === "disabled" && (
                        <div className="text-[10px] text-slate-500 mt-1"
                             data-testid={`pm-disabled-hint-${key}`}>
                          لن يُرحَّل إلى قيود
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 align-middle">
                      {needsAccount ? (
                        <SearchableSelect
                          options={accountsList}
                          value={accId}
                          onChange={(v) => updateRow(key, { account_id: v || "" })}
                          testid={`pm-account-select-${key}`}
                          secondaryKey="code"
                          placeholder={resolvedViaAlias
                            ? `(اختياري — يستخدم ${usedRow.matched_key})`
                            : "اختر حساب قيود..."}
                          listUnavailable={accountsListUnavailable}
                          unavailableReason={accountsUnavailableReason}
                          unavailableLabel="تعذر تحميل قائمة حسابات قيود"
                        />
                      ) : (
                        <div className="text-[11px] text-slate-400 italic px-2"
                             data-testid={`pm-no-account-${key}`}>
                          غير مطلوب
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 align-middle">
                      {!needsAccount && codLock ? (
                        <span className="text-[11px] text-amber-700 font-bold"
                              data-testid={`pm-status-${key}`}>آجل</span>
                      ) : !needsAccount && mode === "disabled" ? (
                        <span className="text-[11px] text-slate-500 font-bold"
                              data-testid={`pm-status-${key}`}>معطّل</span>
                      ) : accId ? (
                        <span className="text-[11px] text-emerald-700 font-bold"
                              data-testid={`pm-status-${key}`}>✓ مربوط</span>
                      ) : resolvedViaAlias ? (
                        <span className="text-[11px] text-sky-700 font-bold"
                              data-testid={`pm-status-${key}`}>
                          ✓ مربوط (Alias)
                        </span>
                      ) : isUsed ? (
                        <span className="text-[11px] text-rose-700 font-bold"
                              data-testid={`pm-status-${key}`}>مطلوب</span>
                      ) : (
                        <span className="text-[11px] text-slate-400"
                              data-testid={`pm-status-${key}`}>اختياري</span>
                      )}
                    </td>
                    <td className="px-2 py-2 align-middle">
                      {!isUsed && !specificBankRow && (
                        <button
                          type="button"
                          onClick={() => removeRow(key)}
                          title="إزالة الصف"
                          className="text-rose-500 hover:text-rose-700 text-sm"
                          data-testid={`pm-remove-${key}`}>
                          ✕
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Suggestions datalist (shared across all pm-input-*) */}
      <datalist id="qoyod-accounts-list">
        {(accountsSuggestions || []).map((a) => (
          <option key={a.id || a.value} value={a.id || a.value}>
            {a.name_ar || a.name}
          </option>
        ))}
      </datalist>

      {/* Add row */}
      {addableKeys.length > 0 && (
        <div className="mt-3 flex gap-2 items-stretch">
          <select
            value={selectedAddKey}
            onChange={(e) => setSelectedAddKey(e.target.value)}
            data-testid="pm-add-select"
            className="flex-1 px-3 py-2 border border-slate-300 rounded-lg text-sm bg-white">
            <option value="">— اختر طريقة دفع لإضافتها —</option>
            {addableKeys.map((k) => (
              <option key={k} value={k}>{labelFor(k)}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={addRow}
            disabled={!selectedAddKey}
            data-testid="pm-add-btn"
            className="px-4 py-2 text-sm font-bold rounded-lg bg-slate-900 text-white hover:bg-black disabled:opacity-50">
            + إضافة
          </button>
        </div>
      )}

      {/* Help hint */}
      <div className="text-[11px] text-slate-500 mt-2 leading-relaxed">
        {accountsUnsupported
          ? "ℹ️ مفتاح API الحالي لا يكشف قائمة الحسابات. أدخل Account ID يدوياً من قيود → المحاسبة → دليل الحسابات."
          : "💡 اضغط على القائمة وابحث باسم الحساب أو كود الحساب أو الـ ID."}
      </div>
    </div>
  );
}

// ─── Setup Guide (inline expandable) ──────────────────────────────
function SetupGuide() {
  const [open, setOpen] = useState(false);
  return (
    <Section title="📋 دليل البحث عن IDs في قيود"
             subtitle="كيف تستخرج كل قيمة من حسابك في قيود (legacy.qoyod.com)">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="px-3 py-1.5 text-xs font-bold rounded bg-slate-900 text-white hover:bg-black"
        data-testid="btn-toggle-setup-guide">
        {open ? "إخفاء الدليل" : "إظهار الدليل خطوة-بخطوة"}
      </button>
      {open && (
        <div className="mt-3 grid md:grid-cols-2 gap-3" data-testid="setup-guide-content">

          <GuideCard
            icon="🏢" title="Branch ID (الفرع)"
            href="https://legacy.qoyod.com/settings/branches"
            steps={[
              "افتح: قيود → الإعدادات → الفروع",
              "اضغط (تعديل) على الفرع الرئيسي",
              "انسخ الرقم من رابط الصفحة بعد كلمة /branches/",
              "ألصقه في حقل Branch ID أعلاه",
            ]}
          />

          <GuideCard
            icon="📊" title="Tax ID (ضريبة VAT 15%)"
            href="https://legacy.qoyod.com/settings/taxes"
            steps={[
              "افتح: قيود → الإعدادات → الضرائب",
              "ابحث عن «ضريبة القيمة المضافة 15%» أو «VAT 15%»",
              "اضغط تعديل وانسخ الرقم من رابط الصفحة",
              "ألصقه في حقل Tax ID",
            ]}
          />

          <GuideCard
            icon="💳" title="Account ID (الحسابات لطرق الدفع)"
            href="https://legacy.qoyod.com/accounts"
            steps={[
              "افتح: قيود → المحاسبة → دليل الحسابات",
              "ابحث عن حساب يطابق طريقة الدفع (مثلاً «مدى» أو «صندوق نقدية»)",
              "اضغط على اسم الحساب — الرقم يظهر في الرابط",
              "ألصقه في صف طريقة الدفع المناسبة",
              "للحسابات الجديدة (BNPL مثلاً): أنشئ حساب «بنوك / بوابة دفع»",
            ]}
          />

          <GuideCard
            icon="👤" title="Default Customer ID (للضيوف)"
            href="https://legacy.qoyod.com/customers"
            steps={[
              "اختياري — إذا أردت توحيد فواتير الضيوف تحت عميل واحد",
              "افتح: قيود → العملاء → اضغط (إضافة)",
              "أنشئ عميلاً اسمه «ضيف / Guest»",
              "افتحه — الرقم في الرابط بعد /customers/",
              "ألصقه في حقل Default Customer",
            ]}
          />

          <GuideCard
            icon="📦" title="Inventory Account & Cost Account"
            href="https://legacy.qoyod.com/accounts"
            steps={[
              "تحتاجهما فقط إذا اخترت نوع المنتجات = Inventory",
              "Inventory Account: حساب أصول من نوع «مخزون»",
              "Cost of Goods Sold: حساب مصروفات من نوع «تكلفة البضاعة المُباعة»",
              "احصل على كليهما من نفس صفحة دليل الحسابات",
            ]}
          />

          <GuideCard
            icon="🔑" title="API Key (مفتاح API)"
            href="https://legacy.qoyod.com/profile"
            steps={[
              "افتح: قيود → الملف الشخصي / الإعدادات → API",
              "أنشئ مفتاحاً جديداً بصلاحية الكتابة (write scope)",
              "انسخه فوراً — لن يظهر مرة أخرى",
              "ألصقه في «مفتاح Qoyod API» أعلاه ثم اضغط حفظ",
              "اضغط «اختبار الاتصال» للتأكد",
            ]}
          />
        </div>
      )}
    </Section>
  );
}

function GuideCard({ icon, title, href, steps }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3"
         data-testid={`guide-card-${title}`}>
      <div className="flex items-center justify-between">
        <h4 className="font-extrabold text-sm text-slate-900">
          <span className="ml-1">{icon}</span> {title}
        </h4>
        <a href={href} target="_blank" rel="noreferrer"
           className="text-[11px] font-bold text-sky-700 hover:underline">
          فتح في قيود ↗
        </a>
      </div>
      <ol className="text-[12px] text-slate-700 mt-2 space-y-1 list-decimal pr-4">
        {steps.map((s, i) => (<li key={i}>{s}</li>))}
      </ol>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────
export default function QoyodSettings() {
  const [loading, setLoading]     = useState(true);
  const [saving, setSaving]       = useState(false);
  const [settings, setSettings]   = useState(null);
  const [apiKey, setApiKey]       = useState("");
  const [branches, setBranches]   = useState([]);
  const [accounts, setAccounts]   = useState([]);
  const [taxes, setTaxes]         = useState([]);
  const [inventories, setInventories] = useState([]);
  const [testing, setTesting]     = useState(false);
  const [testResult, setTestResult] = useState(null);

  const [branchesMeta, setBranchesMeta] = useState({ unsupported: false });
  const [taxesMeta,    setTaxesMeta]    = useState({ unsupported: false });
  const [accountsMeta, setAccountsMeta] = useState({ unsupported: false });
  const [inventoriesMeta, setInventoriesMeta] = useState({ unsupported: false });

  const [pmCatalogue, setPmCatalogue] = useState([]);
  const [pmUsed,      setPmUsed]      = useState([]);
  const [sallaStatuses, setSallaStatuses] = useState([]);
  const [statusesSource, setStatusesSource] = useState(null);
  const [statusesError,  setStatusesError]  = useState(null);

  // ── Iter-290i — Name-first picker lists (cached on the server) ──
  // Replaces the bare numeric-id inputs with searchable dropdowns
  // populated from قيود. `referenceLists.lists` is keyed by the
  // resource name (`categories`, `unit_types`, `inventories`,
  // `accounts`, `taxes`, `branches`, `customers`). Each entry is
  // `{id, name, ...extras}`. The lists may be empty until the
  // operator clicks the refresh button.
  const [referenceLists, setReferenceLists] = useState({
    lists: {
      categories: [], unit_types: [], inventories: [],
      accounts: [], taxes: [], branches: [], customers: [],
    },
    updated_at:        null,
    cached:            false,
    fetch_errors:      null,
    fetch_diagnostics: {},
  });
  const [refreshingLists, setRefreshingLists] = useState(false);

  // Iter-290i.1 — helper that decides whether a given list is
  // "unavailable" (fetch failed / parse failed / empty due to bad
  // response) so the picker can show the right state. An EMPTY
  // list whose fetch SUCCEEDED is NOT unavailable — قيود just has
  // no rows of that kind yet.
  const listUnavailable = (key) => {
    const errs = referenceLists.fetch_errors || {};
    if (errs[key]) return true;
    const diag = (referenceLists.fetch_diagnostics || {})[key];
    return diag && diag.status === "parse_failed";
  };
  const unavailableReason = (key) => {
    const err = (referenceLists.fetch_errors || {})[key];
    if (!err) return null;
    return `${err.code || "error"}: ${err.message || ""}`.trim();
  };

  // ── Loaders ────────────────────────────────────────────────────
  const loadSettings = async () => {
    const { data } = await axios.get(`${API}/integrations/qoyod/settings`);
    setSettings(data);
    return data;
  };

  const loadCatalogs = async () => {
    const tryGet = async (path, setData, setMeta) => {
      try {
        const { data } = await axios.get(`${API}/integrations/qoyod/${path}`);
        setData(Array.isArray(data.data) ? data.data
                : (data.data?.accounts || data.data?.branches
                   || data.data?.taxes || data.data?.inventories || []));
        setMeta({
          unsupported: !!data.unsupported,
          message: data.message || (data.error && data.error.message) || null,
          code: data.error?.code || null,
        });
      } catch (_) {
        setMeta({ unsupported: false,
                  message: "تعذّر الاتصال بقيود — أدخل المعرّف يدوياً.",
                  code: "network_error" });
      }
    };
    await Promise.all([
      tryGet("qoyod-branches",    setBranches,    setBranchesMeta),
      tryGet("qoyod-accounts",    setAccounts,    setAccountsMeta),
      tryGet("qoyod-taxes",       setTaxes,       setTaxesMeta),
      tryGet("qoyod-inventories", setInventories, setInventoriesMeta),
    ]);
  };

  const loadPaymentMethods = async () => {
    try {
      const { data } = await axios.get(
        `${API}/integrations/qoyod/payment-methods/used`);
      setPmCatalogue(data.catalogue || []);
      setPmUsed(data.used || []);
    } catch (_) {
      setPmCatalogue([]);
      setPmUsed([]);
    }
  };

  const loadSallaStatuses = async () => {
    try {
      const { data } = await axios.get(
        `${API}/integrations/qoyod/salla-order-statuses`);
      setSallaStatuses(data?.statuses || []);
      setStatusesSource(data?.source || null);
      setStatusesError(data?.error || null);
    } catch (_) {
      setSallaStatuses([]);
      setStatusesSource("error");
      setStatusesError({ code: "network_error",
                         message: "تعذّر الاتصال بـ Salla" });
    }
  };

  // Iter-290i — Reference-Lists loaders (cached + refresh).
  const loadReferenceLists = async () => {
    try {
      const { data } = await axios.get(
        `${API}/integrations/qoyod/admin/reference-lists`);
      if (data?.ok) setReferenceLists(data);
    } catch (_) {
      // Silent — the picker just renders empty lists until refresh.
    }
  };

  const refreshReferenceLists = async () => {
    setRefreshingLists(true);
    try {
      const { data } = await axios.post(
        `${API}/integrations/qoyod/admin/reference-lists/refresh`);
      if (data?.ok) {
        setReferenceLists(data);
      } else {
        alert(data?.message
              || "تعذّر تحديث القوائم من قيود. تحقق من مفتاح API.");
      }
    } catch (_) {
      alert("تعذّر الاتصال بقيود لتحديث القوائم.");
    } finally {
      setRefreshingLists(false);
    }
  };

  const revalidate = async () => {
    // Server-side re-check as a fail-safe on save.
    try {
      const { data } = await axios.get(
        `${API}/integrations/qoyod/setup/validate`);
      return data?.validation || null;
    } catch (_) {
      return null;
    }
  };

  const loadAll = async () => {
    setLoading(true);
    try {
      const s = await loadSettings();
      if (s?.credentials?.fingerprint) {
        await loadCatalogs();
        // Iter-290i — pull the cached picker lists too.
        await loadReferenceLists();
      }
      await Promise.all([loadPaymentMethods(), loadSallaStatuses()]);
    } catch (_) {
      toast.error("تعذّر تحميل الإعدادات");
    } finally { setLoading(false); }
  };

  useEffect(() => { loadAll(); }, []);

  // ── Helpers ───────────────────────────────────────────────────
  const patch = (changes) => setSettings((s) => ({ ...s, ...changes }));
  const patchCaps = (changes) =>
    setSettings((s) => ({ ...s,
      capabilities: { ...(s?.capabilities || {}), ...changes } }));

  const saveCredentials = async () => {
    if (!apiKey.trim()) { toast.error("أدخل مفتاح API الخاص بقيود"); return; }
    try {
      await axios.post(`${API}/integrations/qoyod/credentials`,
        { api_key: apiKey });
      setApiKey("");
      toast.success("تم حفظ المفتاح بشكل آمن");
      await loadAll();
    } catch (_) { toast.error("فشل حفظ المفتاح"); }
  };

  const removeCredentials = async () => {
    if (!window.confirm("حذف مفتاح API؟ سيُعطّل الإرسال تلقائياً.")) return;
    try {
      await axios.delete(`${API}/integrations/qoyod/credentials`);
      toast.success("تم حذف المفتاح وتعطيل الإرسال");
      setBranches([]); setAccounts([]); setTaxes([]);
      await loadAll();
    } catch (_) { toast.error("فشل حذف المفتاح"); }
  };

  const test = async () => {
    setTesting(true); setTestResult(null);
    try {
      const { data } = await axios.post(`${API}/integrations/qoyod/test-connection`);
      setTestResult(data);
      if (data.ok) {
        toast.success("الاتصال بقيود ناجح");
        await loadCatalogs();
      } else {
        toast.error(data.error?.message || "فشل الاتصال");
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || "فشل الاختبار");
    } finally { setTesting(false); }
  };

  const save = async () => {
    if (!settings) return;
    // Client-side gate (banner already disables button, this is belt+suspenders).
    if (!validation || !validation.ok) {
      toast.error("أكمل الحقول المطلوبة قبل الحفظ");
      return;
    }
    setSaving(true);
    try {
      // Iter-293 — Sanitise payment_method_mapping with posting_mode:
      //   • COD-family rows are FORCED to credit_invoice_only with no
      //     qoyod_account_id, regardless of what's in memory (defense
      //     in depth — the table UI also locks them, and the backend
      //     `coerce_cod_rows` does the same enforcement on PUT).
      //   • Disabled rows are kept (no account needed).
      //   • paid_receipt rows must have qoyod_account_id, otherwise
      //     drop them (the validator already flagged this as a blocker).
      const codDirect = new Set(["cod"]);
      const codAliases = new Set([
        "cash_on_delivery", "cash",
        "الدفع_عند_الاستلام", "النوع_عند_الاستلام",
        "الدفع_نقدا_عند_الاستلام", "نقد_عند_الاستلام", "نقدًا_عند_الاستلام",
      ]);
      const isCodKeyLocal = (k) => {
        const n = (k || "").trim().toLowerCase().replace(/\s+/g, "_");
        return codDirect.has(n) || codAliases.has(n);
      };
      const validModes = ["paid_receipt", "credit_invoice_only", "disabled"];
      const genericBankKeys = new Set([
        "bank", "bank_transfer", "wire_transfer", "تحويل_بنكي",
      ]);
      const configuredMapping = (settings.payment_method_mapping || [])
        .filter((row) => !genericBankKeys.has(
          String(row.salla_method || "").trim().toLowerCase()
            .replace(/\s+/g, "_")));
      for (const requiredBankRow of RECEIVING_BANK_MAPPING_ROWS) {
        const exists = configuredMapping.some(
          (row) => String(row.salla_method || "").trim().toLowerCase()
            === requiredBankRow.salla_method);
        if (!exists) configuredMapping.push({ ...requiredBankRow });
      }
      const pmm = configuredMapping
        .filter((r) => (r.salla_method || "").trim())
        .map((r) => {
          const sm = (r.salla_method || "").trim().toLowerCase();
          if (isCodKeyLocal(sm)) {
            return {
              salla_method:     sm,
              qoyod_account_id: null,
              posting_mode:     "credit_invoice_only",
              label_ar:         r.label_ar || null,
            };
          }
          const mode = validModes.includes(r.posting_mode)
            ? r.posting_mode : "paid_receipt";
          return {
            salla_method:     sm,
            qoyod_account_id: mode === "paid_receipt"
              ? ((r.qoyod_account_id || "").trim() || "")
              : null,
            posting_mode:     mode,
            label_ar:         r.label_ar || null,
          };
        })
        // Drop paid_receipt rows that ended up without an account —
        // the validator already showed a blocker; nothing useful to
        // persist for them.
        .filter((r) => r.posting_mode !== "paid_receipt"
                       || (r.qoyod_account_id || "").trim());

      const patch = {
        enabled:              !!settings.enabled,
        auto_send:            !!settings.auto_send,
        auto_receipt:         !!settings.auto_receipt,
        dry_run_mode:         !!settings.dry_run_mode,
        invoice_trigger_statuses: Array.isArray(settings.invoice_trigger_statuses)
          ? settings.invoice_trigger_statuses
          : (settings.invoice_trigger_status
              ? [settings.invoice_trigger_status]
              : ["completed"]),
        invoice_date_source:  settings.invoice_date_source || "trigger_status_date",
        trigger_once_only:    settings.trigger_once_only !== false,
        default_branch_id:    (settings.default_branch_id || "").trim() || null,
        default_tax_id:       (settings.default_tax_id || "").trim() || null,
        default_customer_id:  (settings.default_customer_id || "").trim() || null,
        inventory_account_id: (settings.inventory_account_id || "").trim() || null,
        cost_account_id:      (settings.cost_account_id || "").trim() || null,
        default_product_type: settings.default_product_type || "service",
        // Iter-287 — Qoyod-required product creation defaults.
        default_product_category_id:  (settings.default_product_category_id || "").trim() || null,
        default_product_tax_id:       (settings.default_product_tax_id || "").trim() || null,
        default_product_unit_type_id: (settings.default_product_unit_type_id || "").trim() || null,
        default_sales_account_id:     (settings.default_sales_account_id || "").trim() || null,
        // Iter-290 — Qoyod-required warehouse id on every invoice line.
        default_inventory_id:         (settings.default_inventory_id || "").trim() || null,
        // Iter-293.1 — COD-fee product id (Qoyod product representing
        // the "رسوم الدفع عند الاستلام" charge). Required only when
        // incoming COD orders carry `amounts.cash_on_delivery > 0`.
        default_cod_fee_product_id:   (settings.default_cod_fee_product_id || "").trim() || null,
        // Optional shipping product id (kept for parity with backend).
        default_shipping_product_id:  (settings.default_shipping_product_id || "").trim() || null,
        // Iter-285 — Tax mode + zero-tax id (for customer_first invoicing).
        tax_mode:                     (settings.tax_mode || "customer_first"),
        zero_tax_id:                  (settings.zero_tax_id || "").trim() || null,
        // Iter-288 — Auto-adopt existing Qoyod products by SKU.
        auto_adopt_existing_qoyod_products:
          settings.auto_adopt_existing_qoyod_products !== false,
        payment_method_mapping: pmm,
        capabilities:         settings.capabilities,
        backfill_mode:        settings.backfill_mode || "now_forward_only",
      };
      await axios.put(`${API}/integrations/qoyod/settings`, patch);
      // Server-side fail-safe revalidation.
      const serverCheck = await revalidate();
      if (serverCheck && !serverCheck.ok) {
        toast.warning(
          `حُفظت الإعدادات لكن الخادم رصد ${serverCheck.context?.blocker_count || 0} مشكلة — راجع البانر`);
      } else {
        toast.success("تم حفظ الإعدادات النهائية");
      }
      await loadAll();
    } catch (e) {
      toast.error(e.response?.data?.detail || "فشل الحفظ");
    } finally { setSaving(false); }
  };

  // ── Live client-side validation (mirrors backend logic) ──────
  // Banner reflects in-memory edits BEFORE save. The server-side
  // call is still used as a fail-safe (and to surface the warning
  // about default_customer which is identical client-side anyway).
  const validation = useMemo(() => {
    if (!settings) return null;
    const issues = [];
    const branch = (settings.default_branch_id || "").toString().trim();
    const tax    = (settings.default_tax_id    || "").toString().trim();
    if (!branch) {
      issues.push({ code: "missing_branch_id", field: "default_branch_id",
        severity: "warning",
        message: "لم يُحدَّد Branch ID. اختياري إذا كان حسابك بفرع واحد فقط." });
    }
    if (!tax) {
      issues.push({ code: "missing_tax_id", field: "default_tax_id",
        severity: "blocker",
        message: "لم يُحدَّد Tax ID. ادخل قيود → الإعدادات → الضرائب → انسخ رقم معرّف ضريبة VAT 15%." });
    }
    // ── Iter-287 — Qoyod-required product creation defaults ────────
    const pCat   = (settings.default_product_category_id   || "").toString().trim();
    const pTax   = (settings.default_product_tax_id        || "").toString().trim();
    const pUnit  = (settings.default_product_unit_type_id  || "").toString().trim();
    const pAcct  = (settings.default_sales_account_id      || "").toString().trim();
    if (!pCat) {
      issues.push({ code: "missing_product_category_id",
        field: "default_product_category_id", severity: "blocker",
        message: "ناقص: التصنيف الافتراضي للمنتجات (Category ID) — انسخه من قيود → الإعدادات → التصنيفات." });
    }
    if (!pTax) {
      issues.push({ code: "missing_product_tax_id",
        field: "default_product_tax_id", severity: "blocker",
        message: "ناقص: ضريبة المنتجات الافتراضية (Product Tax ID) — يمكن استخدام نفس Tax ID للفاتورة، لكنها setting منفصل." });
    }
    if (!pUnit) {
      issues.push({ code: "missing_product_unit_type_id",
        field: "default_product_unit_type_id", severity: "blocker",
        message: "ناقص: وحدة القياس الافتراضية (Unit Type ID) — مثل قطعة / ساعة خدمة، من قيود → الإعدادات → وحدات القياس." });
    }
    if (!pAcct) {
      issues.push({ code: "missing_sales_account_id",
        field: "default_sales_account_id", severity: "blocker",
        message: "ناقص: حساب المبيعات الافتراضي (Sales Account ID) — من قيود → الحسابات → دليل الحسابات → اختر حساب الإيرادات." });
    }
    // ── Iter-290 — Qoyod requires inventory_id on every invoice line ──
    const pInv = (settings.default_inventory_id || "").toString().trim();
    if (!pInv) {
      issues.push({ code: "missing_default_inventory_id",
        field: "default_inventory_id", severity: "blocker",
        message: "ناقص: المستودع الافتراضي (Inventory ID) — قيود يطلب inventory_id على كل سطر فاتورة. أنشئ مستودعاً افتراضياً في قيود (مستودع افتراضي - ميزان) وانسخ id الخاص به." });
    }
    // Payment-method mapping completeness (based on USED methods).
    // A method counts as "mapped" if it has a direct mapping OR its
    // alias-family base provider is mapped (mirrors backend resolver,
    // Iter 2026-02-26). We trust the backend's `mapped_via` field on
    // each used row for the alias case; the in-memory user edit only
    // affects DIRECT entries so we recompute `direct` here and trust
    // `mapped_via === "alias"` for the rest.
    //
    // Iter-293 — Two new exclusions:
    //   • COD-family keys are ALWAYS credit_invoice_only and don't
    //     need an account. Never block save on them.
    //   • Rows whose posting_mode is `credit_invoice_only` or
    //     `disabled` don't need an account either.
    const codDirect = new Set(["cod"]);
    const codAliases = new Set([
      "cash_on_delivery", "cash",
      "الدفع_عند_الاستلام", "النوع_عند_الاستلام",
      "الدفع_نقدا_عند_الاستلام", "نقد_عند_الاستلام", "نقدًا_عند_الاستلام",
    ]);
    const isCodKeyVal = (k) => {
      const n = (k || "").trim().toLowerCase().replace(/\s+/g, "_");
      return codDirect.has(n) || codAliases.has(n);
    };
    const mappingByKey = new Map(
      (settings.payment_method_mapping || [])
        .map((r) => [(r.salla_method || "").toLowerCase(), r]));
    const genericBankKeys = new Set([
      "bank", "bank_transfer", "wire_transfer", "تحويل_بنكي",
    ]);
    const missing = (pmUsed || [])
      .filter((u) => u.key)
      .filter((u) => {
        // COD family: never a blocker.
        if (isCodKeyVal(u.key)) return false;
        // Generic bank is resolved per order from the actual receiving
        // bank.  It must not have (or require) a catch-all Qoyod account.
        if (genericBankKeys.has(String(u.key).trim().toLowerCase()
          .replace(/\s+/g, "_"))) return false;
        const row = mappingByKey.get(u.key);
        const mode = row?.posting_mode || "paid_receipt";
        // Non-paid_receipt modes don't need an account.
        if (mode === "credit_invoice_only" || mode === "disabled") return false;
        // Direct mapping with a real account → covered.
        if (row && (row.qoyod_account_id || "").trim()) return false;
        if (u.mapped_via === "alias") return false;
        return true;
      })
      .map((u) => u.key);
    if (missing.length > 0) {
      const labels = missing.slice(0, 5).map((k) => {
        const c = (pmCatalogue || []).find((x) => x.key === k);
        const u = (pmUsed || []).find((x) => x.key === k);
        return (c && c.label_ar) || (u && u.label_ar) || k;
      }).join(", ");
      const more = missing.length > 5 ? ` (+${missing.length - 5} غيرها)` : "";
      issues.push({
        code: "unmapped_payment_methods", field: "payment_method_mapping",
        severity: "blocker",
        message: `${missing.length} طريقة دفع مُستخدمة في طلباتك غير مربوطة بحساب قيود: ${labels}${more}.`,
      });
    }
    // Inventory mode requirements
    const ptype = settings.default_product_type || "service";
    if (ptype === "inventory") {
      if (!(settings.inventory_account_id || "").toString().trim()) {
        issues.push({ code: "missing_inventory_account",
          field: "inventory_account_id", severity: "blocker",
          message: "وضع المنتجات = Inventory يتطلب حساب المخزون (Inventory Account ID)." });
      }
      if (!(settings.cost_account_id || "").toString().trim()) {
        issues.push({ code: "missing_cost_account",
          field: "cost_account_id", severity: "blocker",
          message: "وضع المنتجات = Inventory يتطلب حساب التكلفة (COGS Account ID)." });
      }
    }
    // Optional warning
    if (!(settings.default_customer_id || "").toString().trim()) {
      issues.push({ code: "missing_default_customer",
        field: "default_customer_id", severity: "warning",
        message: "لم يُحدَّد عميل افتراضي للضيوف. سيُنشأ عميل جديد لكل طلب ضيف بدون هاتف/إيميل. اختياري." });
    }
    const blockerCount = issues.filter((i) => i.severity === "blocker").length;
    return {
      ok: blockerCount === 0,
      issues,
      context: {
        product_type: ptype,
        used_payment_methods: (pmUsed || []).map((u) => u.key).filter(Boolean),
        mapped_payment_methods: [...mappingByKey.keys()],
        missing_payment_methods: missing,
        blocker_count: blockerCount,
        warning_count: issues.length - blockerCount,
      },
    };
  }, [settings, pmUsed, pmCatalogue]);

  // ── Jump-to-field helper ────────────────────────────────────
  const jumpTo = (field) => {
    const el = document.querySelector(`[data-testid="field-${field}"]`)
            || document.querySelector(`[data-testid="${field}"]`);
    if (el && el.scrollIntoView) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      if (typeof el.focus === "function") el.focus();
    }
  };

  if (loading || !settings) {
    return <div className="p-8 text-center text-slate-500">جاري التحميل…</div>;
  }

  const hasCreds = !!settings.credentials?.fingerprint;
  const productType = settings.default_product_type || "service";
  const showInventoryAccounts = productType === "inventory";
  const blockers = (validation?.issues || []).filter((i) => i.severity === "blocker");
  const canSave = !!validation && validation.ok;
  const fieldInvalid = (field) =>
    blockers.some((i) => i.field === field);

  return (
    <div dir="rtl" className="max-w-4xl mx-auto p-4 md:p-6"
         data-testid="qoyod-settings-page">
      <header className="mb-4">
        <h1 className="text-2xl md:text-3xl font-extrabold text-slate-900">
          إعدادات تكامل قيود — الإعداد النهائي
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          صفحة إعداد لمرة واحدة. بعد اكتمالها لن تحتاج العودة إليها إلا
          عند إضافة طريقة دفع جديدة أو تغيير إعداد محاسبي.
        </p>
      </header>

      {/* 1) Setup Status Banner */}
      <SetupStatusBanner validation={validation} onJumpTo={jumpTo} />

      {/* 2) API Key */}
      <Section title="مفتاح API الخاص بقيود">
        {hasCreds ? (
          <div className="flex items-center justify-between bg-emerald-50 border border-emerald-200 rounded-lg p-3">
            <div>
              <div className="text-sm font-bold text-emerald-800">
                مفتاح محفوظ بشكل آمن
              </div>
              <div className="text-xs text-emerald-700 font-mono mt-1">
                {settings.credentials.fingerprint}
              </div>
            </div>
            <div className="flex gap-2">
              <button onClick={test} disabled={testing}
                data-testid="btn-test-connection"
                className="px-3 py-2 text-sm font-bold rounded-lg bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-50">
                {testing ? "جاري الاختبار…" : "اختبار الاتصال"}
              </button>
              <button onClick={removeCredentials}
                data-testid="btn-remove-credentials"
                className="px-3 py-2 text-sm font-bold rounded-lg bg-rose-100 text-rose-700 hover:bg-rose-200">
                حذف المفتاح
              </button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2">
            <input type="password" value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="الصق هنا مفتاح Qoyod API الخاص بك"
              dir="ltr" data-testid="input-api-key"
              className="flex-1 px-3 py-2 border border-slate-300 rounded-lg text-sm font-mono" />
            <button onClick={saveCredentials} data-testid="btn-save-credentials"
              className="px-4 py-2 text-sm font-bold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700">
              حفظ
            </button>
          </div>
        )}
        {testResult && !testResult.ok && testResult.error && (
          <div className="text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded p-2 mt-2">
            <strong>سبب الفشل:</strong> {testResult.error.message} ({testResult.error.code})
          </div>
        )}
      </Section>

      {/* 3) Webhook Token */}
      <WebhookTokenSection />

      {/* 4) Master switches */}
      <Section title="المفاتيح الرئيسية">
        <ToggleRow label="تفعيل التكامل مع قيود"
          hint="عند الإيقاف لن يُرسل ميزان أي فاتورة لقيود."
          checked={settings.enabled}
          onChange={(v) => patch({ enabled: v })}
          testid="toggle-enabled" />
        <ToggleRow label="إرسال تلقائي عند تنفيذ الطلب"
          hint="بدون هذا، يلزم الضغط يدوياً على زر الإرسال لكل طلب."
          checked={settings.auto_send}
          onChange={(v) => patch({ auto_send: v })}
          testid="toggle-auto-send" />
        <ToggleRow label="إنشاء سند قبض تلقائياً بعد الفاتورة"
          hint="إن أوقفته، تُنشأ الفاتورة فقط ويُترك السند للمراجعة اليدوية."
          checked={settings.auto_receipt}
          onChange={(v) => patch({ auto_receipt: v })}
          testid="toggle-auto-receipt" />
        <ToggleRow label="🧪 وضع التشغيل الجاف (Dry Run Mode)"
          hint="ينفّذ المسار كاملاً ويحفظ كل Payload في snapshot دون إرسال أي شيء فعلي إلى قيود."
          checked={!!settings.dry_run_mode}
          onChange={(v) => patch({ dry_run_mode: v })}
          testid="toggle-dry-run-mode" />
      </Section>

      {/* 5) Core IDs — Iter-290i: name-first pickers backed by
            qoyod_reference_lists. The numeric ids are still what
            we POST to قيود; the picker only changes the UX so
            operators don't have to copy-paste ids manually. */}
      <Section title="المعرّفات الأساسية"
               subtitle="القيم الأساسية المطلوبة لإنشاء الفواتير في قيود">
        {/* Iter-290i — Refresh-from-Qoyod button. Caches the lists
            on the server so subsequent renders are instant. */}
        <div className="mb-4 p-3 rounded-lg bg-sky-50 border border-sky-200">
          <div className="flex items-center justify-between gap-3 mb-2">
            <div className="text-xs text-sky-900">
              <div className="font-bold">📚 قوائم قيود</div>
              <div className="text-[11px] text-sky-700 mt-0.5">
                {referenceLists.updated_at ? (
                  <>آخر تحديث: <span dir="ltr" className="font-mono">{referenceLists.updated_at}</span></>
                ) : (
                  <>لم تُحدَّث القوائم بعد. اضغط الزر لجلبها من قيود.</>
                )}
              </div>
            </div>
            <button
              type="button"
              onClick={refreshReferenceLists}
              disabled={!hasCreds || refreshingLists}
              data-testid="btn-refresh-reference-lists"
              className="flex items-center gap-2 px-3 py-2 text-xs font-bold rounded-md bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshingLists ? "animate-spin" : ""}`} />
              {refreshingLists ? "جاري التحديث..." : "تحديث القوائم من قيود"}
            </button>
          </div>
          {/* Iter-290i.1 — per-list diagnostic chips. Separates
              "list never loaded" from "list loaded but empty" so
              the operator never sees a misleading "ID غير موجود". */}
          {referenceLists.fetch_diagnostics
            && Object.keys(referenceLists.fetch_diagnostics).length > 0 && (
            <details className="mt-2" data-testid="reference-lists-diagnostics">
              <summary className="text-[11px] text-sky-800 cursor-pointer hover:underline">
                تشخيص جلب القوائم ({Object.keys(referenceLists.fetch_diagnostics).length}/7)
              </summary>
              <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                {Object.entries(referenceLists.fetch_diagnostics).map(([key, d]) => {
                  const tone = d.status === "success" ? "emerald"
                             : d.status === "empty"   ? "slate"
                             : d.status === "parse_failed" ? "amber"
                             : "rose";
                  const bg = {
                    emerald: "bg-emerald-50 border-emerald-200 text-emerald-900",
                    slate:   "bg-slate-50 border-slate-200 text-slate-700",
                    amber:   "bg-amber-50 border-amber-200 text-amber-900",
                    rose:    "bg-rose-50 border-rose-200 text-rose-900",
                  }[tone];
                  const icon = d.status === "success" ? "✓"
                             : d.status === "empty"   ? "∅"
                             : "✗";
                  return (
                    <div key={key}
                         data-testid={`list-diag-${key}`}
                         className={`text-[10px] rounded border px-2 py-1 ${bg}`}>
                      <div className="font-bold flex justify-between">
                        <span>{icon} {key}</span>
                        <span className="font-mono">{d.count} عنصر</span>
                      </div>
                      {d.error && (
                        <div className="mt-0.5 text-[9px] font-mono break-words" dir="ltr">
                          {typeof d.error === "string" ? d.error
                            : `${d.error.code || ""}: ${d.error.message || ""}`}
                          {d.error.endpoint && <> · {d.error.endpoint}</>}
                          {d.error.status_code && <> · HTTP {d.error.status_code}</>}
                        </div>
                      )}
                      {d.status === "parse_failed" && d.sample_keys && d.sample_keys.length > 0 && (
                        <div className="mt-0.5 text-[9px] font-mono" dir="ltr">
                          response keys: {d.sample_keys.join(", ")}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </details>
          )}
        </div>

        <div className="grid md:grid-cols-2 gap-3">
          <div data-testid="field-default_branch_id">
            <label className="block text-xs font-bold text-slate-600 mb-1">
              الفرع الافتراضي <span className="text-slate-400">(اختياري)</span>
            </label>
            <SearchableSelect
              options={referenceLists.lists.branches}
              value={settings.default_branch_id}
              onChange={(v) => patch({ default_branch_id: v })}
              testid="select-branch"
              placeholder={referenceLists.lists.branches.length
                ? "اختر فرعاً..."
                : "اتركه فارغاً إذا كان حسابك بفرع واحد"}
              disabled={!hasCreds}
              listUnavailable={listUnavailable("branches")}
              unavailableReason={unavailableReason("branches")}
            />
          </div>

          <div data-testid="field-default_tax_id">
            <label className="block text-xs font-bold text-slate-600 mb-1">
              ضريبة القيمة المضافة الافتراضية <span className="text-rose-600">*</span>
            </label>
            <SearchableSelect
              options={referenceLists.lists.taxes}
              value={settings.default_tax_id}
              onChange={(v) => patch({ default_tax_id: v })}
              testid="select-tax"
              secondaryKey="percent"
              placeholder="اختر ضريبة..."
              disabled={!hasCreds}
              listUnavailable={listUnavailable("taxes")}
              unavailableReason={unavailableReason("taxes")}
            />
            {fieldInvalid("default_tax_id") && (
              <div className="text-xs text-rose-600 mt-1">
                مطلوب — اختر ضريبة من القائمة.
              </div>
            )}
          </div>

          <div data-testid="field-default_customer_id" className="md:col-span-2">
            <label className="block text-xs font-bold text-slate-600 mb-1">
              عميل افتراضي للطلبات الضيف <span className="text-slate-400">(اختياري)</span>
            </label>
            <SearchableSelect
              options={referenceLists.lists.customers}
              value={settings.default_customer_id}
              onChange={(v) => patch({ default_customer_id: v })}
              testid="select-default-customer"
              secondaryKey="phone"
              placeholder="عند الفراغ، يُنشَأ عميل جديد لكل طلب ضيف"
              disabled={!hasCreds}
              listUnavailable={listUnavailable("customers")}
              unavailableReason={unavailableReason("customers")}
            />
          </div>
        </div>
      </Section>

      {/* 5b) Product & Invoice Defaults — Iter-290i pickers */}
      <Section
        title="🧾 إعدادات إنشاء المنتجات والفواتير في قيود"
        subtitle={
          <>
            مطلوبة لإنشاء أي منتج جديد وأي فاتورة في قيود. قيود يرفض
            الإنشاء بدون هذه الإعدادات. اختر من القوائم — لا تحتاج
            لكتابة الأرقام يدوياً.
            <br />
            <span className="text-amber-600 dark:text-amber-400 text-xs">
              ⚠️ المستودع الافتراضي مطلوب على كل سطر فاتورة حتى لو
              كانت كل منتجاتك خدمية — قيود يرفض الفاتورة بدونه.
            </span>
          </>
        }
        tone={
          [
            "default_product_category_id",
            "default_product_tax_id",
            "default_product_unit_type_id",
            "default_sales_account_id",
            "default_inventory_id",
          ].some(fieldInvalid) ? "danger" : "default"
        }>
        <div className="grid md:grid-cols-2 gap-3">
          <div data-testid="field-default_product_category_id">
            <label className="block text-xs font-bold text-slate-600 mb-1">
              التصنيف الافتراضي <span className="text-rose-600">*</span>
            </label>
            <SearchableSelect
              options={referenceLists.lists.categories}
              value={settings.default_product_category_id}
              onChange={(v) => patch({ default_product_category_id: v })}
              testid="select-product-category"
              placeholder="اختر تصنيفاً..."
              disabled={!hasCreds}
              listUnavailable={listUnavailable("categories")}
              unavailableReason={unavailableReason("categories")}
            />
            {fieldInvalid("default_product_category_id") && (
              <div className="text-xs text-rose-600 mt-1">مطلوب</div>
            )}
          </div>

          <div data-testid="field-default_product_tax_id">
            <label className="block text-xs font-bold text-slate-600 mb-1">
              ضريبة المنتجات الافتراضية <span className="text-rose-600">*</span>
            </label>
            <SearchableSelect
              options={referenceLists.lists.taxes}
              value={settings.default_product_tax_id}
              onChange={(v) => patch({ default_product_tax_id: v })}
              testid="select-product-tax"
              secondaryKey="percent"
              placeholder="عادة نفس ضريبة الفاتورة"
              disabled={!hasCreds}
              listUnavailable={listUnavailable("taxes")}
              unavailableReason={unavailableReason("taxes")}
            />
            {fieldInvalid("default_product_tax_id") && (
              <div className="text-xs text-rose-600 mt-1">مطلوب</div>
            )}
          </div>

          <div data-testid="field-default_product_unit_type_id">
            <label className="block text-xs font-bold text-slate-600 mb-1">
              وحدة القياس الافتراضية <span className="text-rose-600">*</span>
            </label>
            <SearchableSelect
              options={referenceLists.lists.unit_types}
              value={settings.default_product_unit_type_id}
              onChange={(v) => patch({ default_product_unit_type_id: v })}
              testid="select-unit-type"
              placeholder="مثال: قطعة..."
              disabled={!hasCreds}
              listUnavailable={listUnavailable("unit_types")}
              unavailableReason={unavailableReason("unit_types")}
            />
            {fieldInvalid("default_product_unit_type_id") && (
              <div className="text-xs text-rose-600 mt-1">مطلوب</div>
            )}
          </div>

          <div data-testid="field-default_sales_account_id">
            <label className="block text-xs font-bold text-slate-600 mb-1">
              حساب المبيعات الافتراضي <span className="text-rose-600">*</span>
            </label>
            <SearchableSelect
              options={referenceLists.lists.accounts}
              value={settings.default_sales_account_id}
              onChange={(v) => patch({ default_sales_account_id: v })}
              testid="select-sales-account"
              secondaryKey="code"
              placeholder="اختر حساب الإيرادات..."
              disabled={!hasCreds}
              listUnavailable={listUnavailable("accounts")}
              unavailableReason={unavailableReason("accounts")}
            />
            {fieldInvalid("default_sales_account_id") && (
              <div className="text-xs text-rose-600 mt-1">مطلوب</div>
            )}
          </div>

          <div data-testid="field-default_inventory_id">
            <label className="block text-xs font-bold text-slate-600 mb-1">
              المستودع الافتراضي <span className="text-rose-600">*</span>
            </label>
            <SearchableSelect
              options={referenceLists.lists.inventories}
              value={settings.default_inventory_id}
              onChange={(v) => patch({ default_inventory_id: v })}
              testid="select-inventory"
              placeholder="اختر مستودعاً..."
              disabled={!hasCreds}
              listUnavailable={listUnavailable("inventories")}
              unavailableReason={unavailableReason("inventories")}
            />
            {fieldInvalid("default_inventory_id") && (
              <div className="text-xs text-rose-600 mt-1">مطلوب</div>
            )}
          </div>
        </div>

        {/* Auto-Adopt toggle (Iter-288) */}
        <div className="mt-4 p-3 rounded-lg border border-slate-200 dark:border-slate-700">
          <label className="flex items-start gap-3 cursor-pointer"
                 data-testid="toggle-auto_adopt_existing_qoyod_products">
            <input
              type="checkbox"
              className="mt-1"
              checked={settings.auto_adopt_existing_qoyod_products !== false}
              onChange={(e) => patch({
                auto_adopt_existing_qoyod_products: e.target.checked,
              })}
            />
            <div className="text-sm">
              <div className="font-semibold">
                ربط تلقائي للمنتجات الموجودة مسبقاً في قيود
                <span className="ms-2 text-xs text-slate-500">(SKU match)</span>
              </div>
              <div className="text-slate-500 mt-1">
                عند الإرسال، إذا وجد النظام SKU مطابقاً في قيود → يربط
                المنتج الموجود مباشرةً بدون إنشاء جديد. هذا الوضع المُوصى به
                عند رفع كتالوج المنتجات يدوياً إلى قيود (افتراضي مفعّل).
                لإيقافه يصبح Trust Gate صارماً — كل SKU جديد يحتاج adopt يدوي.
              </div>
            </div>
          </label>
        </div>
      </Section>

      {/* 6) Payment Method Mapping */}
      <Section
        title="💳 ربط طرق الدفع (Payment Method Mapping)"
        subtitle="كل طريقة دفع مُستخدمة في متجرك يجب أن تُربط بحساب في قيود.
                  بدون ذلك لن يُنشأ سند القبض."
        tone={fieldInvalid("payment_method_mapping") ? "danger" : "default"}>
        <div data-testid="field-payment_method_mapping">
          <PaymentMethodMappingTable
            mapping={settings.payment_method_mapping || []}
            onChange={(next) => patch({ payment_method_mapping: next })}
            catalogue={pmCatalogue}
            used={pmUsed}
            accountsSuggestions={accounts}
            accountsUnsupported={accountsMeta.unsupported || !hasCreds}
            accountsList={referenceLists.lists.accounts}
            // Iter-290i.2 — Treat an EMPTY accounts list as
            // "unavailable" too, not just as "ID not found in قيود".
            // The list may simply not have been fetched yet
            // (e.g. fresh login, or قيود-side error masked as []),
            // and the user explicitly asked NOT to flag saved ids
            // as missing in that case — show a "تعذّر تحميل" hint
            // instead so the operator clicks the refresh-lists CTA.
            accountsListUnavailable={
              listUnavailable("accounts")
              || (referenceLists.lists.accounts || []).length === 0
            }
            accountsUnavailableReason={unavailableReason("accounts")}
          />
        </div>
      </Section>

      {/* 7) Inventory Accounts — conditional */}
      {showInventoryAccounts && (
        <Section
          title="📦 حسابات المخزون (Inventory Accounts)"
          subtitle="مطلوبة فقط لأن نوع المنتجات = Inventory">
          <div className="grid md:grid-cols-2 gap-3">
            <div data-testid="field-inventory_account_id">
              <IDInput
                label="حساب المخزون (Inventory Account ID)" required
                value={settings.inventory_account_id}
                onChange={(v) => patch({ inventory_account_id: v })}
                testid="input-inventory-account"
                placeholder="مثال: 1110"
                datalistId="qoyod-accounts-list"
                suggestions={accounts}
                disabled={!hasCreds}
                invalid={fieldInvalid("inventory_account_id")}
              />
            </div>
            <div data-testid="field-cost_account_id">
              <IDInput
                label="حساب تكلفة البضاعة المباعة (COGS Account ID)" required
                value={settings.cost_account_id}
                onChange={(v) => patch({ cost_account_id: v })}
                testid="input-cost-account"
                placeholder="مثال: 5110"
                datalistId="qoyod-accounts-list"
                suggestions={accounts}
                disabled={!hasCreds}
                invalid={fieldInvalid("cost_account_id")}
              />
            </div>
          </div>
        </Section>
      )}

      {/* 8) Advanced */}
      <Section title="الإعدادات المتقدمة"
               subtitle="سياسة تشغيل الفاتورة وأنواع المنتجات">
        <div className="grid md:grid-cols-2 gap-3">
          <div className="md:col-span-2">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-bold text-slate-700">
                حالات الطلب التي تطلق إنشاء الفاتورة
              </span>
              <div className="flex items-center gap-2">
                {statusesSource === "salla_api" && (
                  <span className="text-[10px] text-emerald-700 font-bold"
                        data-testid="trigger-statuses-source-api">
                    ✓ من Salla API
                  </span>
                )}
                {statusesSource === "fallback" && (
                  <span className="text-[10px] text-amber-700 font-bold"
                        data-testid="trigger-statuses-source-fallback">
                    ⚠ من الطلبات المرصودة (Salla غير متاح)
                  </span>
                )}
                <button type="button" onClick={loadSallaStatuses}
                        data-testid="btn-reload-statuses"
                        className="text-[10px] font-bold text-sky-700 hover:underline">
                  🔄 تحديث
                </button>
              </div>
            </div>
            <p className="text-[11px] text-slate-500 mt-0.5 mb-2">
              تُنشأ الفاتورة في قيود فقط عند انتقال الطلب لأحد هذه الحالات.
              النظام يستخدم <code className="font-mono bg-slate-100 px-1 rounded">slug</code> الحالة
              من Salla — تغيير الاسم الظاهر في Salla لا يكسر التكامل.
            </p>
            {statusesError && (
              <div className="text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 mb-2"
                   data-testid="trigger-statuses-error">
                {statusesError.message}
              </div>
            )}
            <div className="grid grid-cols-2 gap-1.5"
                 data-testid="trigger-statuses-list">
              {(sallaStatuses.length > 0 ? sallaStatuses
                                          : FALLBACK_TRIGGER_STATUSES)
                .map((s) => {
                const list = Array.isArray(settings.invoice_trigger_statuses)
                  ? settings.invoice_trigger_statuses
                  : (settings.invoice_trigger_status ? [settings.invoice_trigger_status] : ["completed"]);
                const slug = (s.slug || "").toLowerCase();
                const checked = list.includes(slug);
                return (
                  <label key={slug}
                    className={`flex items-start gap-2 px-2.5 py-1.5 rounded-lg text-xs cursor-pointer border
                                ${checked ? "bg-emerald-50 border-emerald-300 text-emerald-900"
                                          : "bg-white border-slate-200 hover:bg-slate-50"}`}
                    data-testid={`trigger-status-${slug}`}>
                    <input type="checkbox" checked={checked}
                      onChange={(e) => {
                        const next = e.target.checked
                          ? Array.from(new Set([...list, slug]))
                          : list.filter((v) => v !== slug);
                        patch({ invoice_trigger_statuses: next.length ? next : ["completed"] });
                      }}
                      className="mt-0.5 h-4 w-4 accent-emerald-600" />
                    <span className="flex-1 min-w-0">
                      <div className="font-medium truncate">{s.name}</div>
                      <div className="text-[10px] font-mono text-slate-500 truncate">
                        slug: {slug}
                        {s.id && ` · id: ${s.id}`}
                        {s.is_system && " · system"}
                      </div>
                    </span>
                  </label>
                );
              })}
            </div>
            {sallaStatuses.length === 0 && (
              <div className="text-[11px] text-slate-500 italic mt-2"
                   data-testid="trigger-statuses-empty">
                لم نتمكن من جلب أي حالات. سنستخدم <code className="font-mono">completed</code> افتراضياً.
                تأكّد من اتصال Salla أو وجود طلبات مرصودة.
              </div>
            )}
          </div>

          <label className="block">
            <span className="text-xs font-bold text-slate-700">تاريخ الفاتورة المعتمد</span>
            <select value={settings.invoice_date_source || "trigger_status_date"}
              onChange={(e) => patch({ invoice_date_source: e.target.value })}
              data-testid="select-invoice-date-source"
              className="mt-1 w-full px-3 py-2 border border-slate-300 rounded-lg text-sm">
              {INVOICE_DATE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs font-bold text-slate-700">نوع المنتجات الافتراضي في قيود</span>
            <select value={settings.default_product_type}
              onChange={(e) => patch({ default_product_type: e.target.value })}
              data-testid="select-product-type"
              className="mt-1 w-full px-3 py-2 border border-slate-300 rounded-lg text-sm">
              {PRODUCT_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>

          <ToggleRow
            label="إنشاء الفاتورة لمرة واحدة فقط (Trigger Once Only)"
            hint="عند التفعيل: لن تُنشأ فاتورة جديدة لطلب أُرسل سابقاً."
            checked={settings.trigger_once_only !== false}
            onChange={(v) => patch({ trigger_once_only: v })}
            testid="toggle-trigger-once-only"
          />

          <ToggleRow
            label="ترحيل الطلبات السابقة غير المرسلة (Backfill)"
            hint="OFF افتراضياً (مُوصى به): بعد تفعيل الإنتاج، يُرسَل فقط الطلبات الجديدة. الصفوف القديمة العالقة من فترة Dry-Run تُنقَل إلى SKIPPED تلقائياً. لا تُفعّل هذا الخيار إلا إذا أردت إعادة معالجة طلبات قديمة عمداً."
            checked={settings.backfill_mode === "backfill_unsent"}
            onChange={(v) => patch({ backfill_mode: v ? "backfill_unsent" : "now_forward_only" })}
            testid="toggle-backfill-mode"
          />
        </div>
      </Section>

      {/* 9) Capability flags */}
      <Section title="صلاحيات العمليات (Capability Flags)"
               subtitle="تعطيل أي خانة يوقف هذه العملية فقط — مفيد لاختبار جزئي.">
        <ToggleRow label="إنشاء العملاء في قيود"
          checked={settings.capabilities?.create_customers}
          onChange={(v) => patchCaps({ create_customers: v })}
          testid="cap-customers" />
        <ToggleRow label="إنشاء المنتجات في قيود"
          checked={settings.capabilities?.create_products}
          onChange={(v) => patchCaps({ create_products: v })}
          testid="cap-products" />
        <ToggleRow label="إنشاء الفواتير في قيود"
          checked={settings.capabilities?.create_invoices}
          onChange={(v) => patchCaps({ create_invoices: v })}
          testid="cap-invoices" />
        <ToggleRow label="إنشاء سندات القبض في قيود"
          checked={settings.capabilities?.create_receipts}
          onChange={(v) => patchCaps({ create_receipts: v })}
          testid="cap-receipts" />
      </Section>

      {/* 10) Inline setup guide */}
      <SetupGuide />

      {/* 11) Sticky save bar */}
      <div className="sticky bottom-0 bg-white/95 backdrop-blur border-t border-slate-200
                       -mx-4 md:-mx-6 px-4 md:px-6 py-3 mt-4 flex items-center justify-between gap-3">
        <div className="text-xs"
             data-testid="save-bar-status">
          {canSave ? (
            <span className="text-emerald-700 font-bold">
              ✅ كل الحقول مكتملة — جاهز للحفظ
            </span>
          ) : (
            <span className="text-rose-700 font-bold">
              ⛔ يجب حلّ {blockers.length} مشكلة قبل الحفظ
            </span>
          )}
        </div>
        <button
          onClick={save}
          disabled={saving || !canSave}
          data-testid="btn-save-settings"
          title={!canSave ? "أكمل الحقول المطلوبة في الأعلى أولاً" : ""}
          className="px-5 py-2.5 text-sm font-extrabold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed">
          {saving ? "جاري الحفظ…" : "حفظ الإعدادات النهائية"}
        </button>
      </div>
    </div>
  );
}
