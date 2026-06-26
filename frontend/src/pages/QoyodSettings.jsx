/**
 * Qoyod Settings page — Day 2 of Qoyod Invoice MVP.
 *
 * Surfaces:
 *   • Master switches (enabled / auto_send / auto_receipt).
 *   • API key form + Test Connection.
 *   • Defaults section (branch / tax / invoice trigger / product type).
 *   • Capability flags (4 toggles per user spec).
 *   • Payment-method ↔ Qoyod account mapping table.
 *
 * What's intentionally NOT here (lands Day 3+):
 *   • Webhook ingestion UI.
 *   • Pipeline monitoring (invoices list, retry buttons).
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";

const API = process.env.REACT_APP_BACKEND_URL + "/api";

const PRODUCT_TYPE_OPTIONS = [
  { value: "service",     label: "خدمات (Service) — موصى به للمتاجر الرقمية" },
  { value: "inventory",   label: "مخزنية (Inventory) — للمتاجر بمستودع وSKUs" },
  { value: "per_product", label: "حسب إعداد كل منتج (Per Product)" },
];

const TRIGGER_STATUS_OPTIONS = [
  { value: "completed", label: "تم التنفيذ (completed) — الموصى به" },
  { value: "delivered", label: "تم التوصيل (delivered)" },
  { value: "paid",      label: "مدفوع (paid)" },
  { value: "shipped",   label: "تم الشحن (shipped)" },
];

const INVOICE_DATE_OPTIONS = [
  { value: "trigger_status_date", label: "تاريخ انتقال الطلب للحالة المؤهلة (مُوصى به)" },
  { value: "completed_at", label: "تاريخ تنفيذ الطلب (completed_at)" },
  { value: "paid_at",      label: "تاريخ الدفع (paid_at)" },
  { value: "created_at",   label: "تاريخ الإنشاء (created_at)" },
];

function Section({ title, children, tone = "default" }) {
  const toneCls = tone === "danger"
    ? "border-rose-300 bg-rose-50/30"
    : "border-slate-200 bg-white";
  return (
    <section className={`rounded-xl border ${toneCls} p-4 md:p-5 mb-4`}>
      <h3 className="text-base font-extrabold text-slate-800 mb-3">{title}</h3>
      <div className="space-y-3">{children}</div>
    </section>
  );
}


// ─── Webhook Token UI ──────────────────────────────────────────────
function WebhookTokenSection() {
  const [meta, setMeta] = useState(null);          // { configured, fingerprint, ... }
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  // One-time plaintext disclosure (cleared on dismiss):
  const [revealedToken, setRevealedToken] = useState(null);
  const [copied, setCopied] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(
        `${API}/integrations/qoyod/webhook-token`);
      setMeta(data?.meta || null);
    } catch (_e) {
      setMeta(null);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const generate = async () => {
    // Only require confirmation when REPLACING an existing token —
    // the first generation has nothing to revoke.
    if (meta?.configured) {
      const ok = window.confirm(
        "هل تريد إعادة التوليد؟ سيتم إبطال الـ Token الحالي فوراً. " +
        "تأكّد من تحديث Make.com قبل وصول أي طلب جديد.");
      if (!ok) return;
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
    } finally {
      setGenerating(false);
    }
  };

  const copyToken = async () => {
    if (!revealedToken) return;
    try {
      await navigator.clipboard.writeText(revealedToken);
      setCopied(true);
      toast.success("تم النسخ إلى الحافظة");
    } catch (e) {
      toast.error("تعذّر النسخ — انسخ يدوياً");
    }
  };

  const dismissReveal = () => {
    setRevealedToken(null);
    setCopied(false);
  };

  const revoke = async () => {
    if (!window.confirm(
      "إبطال Webhook Token الحالي؟ Make.com لن يستطيع إرسال الطلبات بعد ذلك حتى توليد Token جديد."
    )) return;
    try {
      await axios.delete(`${API}/integrations/qoyod/webhook-token`);
      toast.success("تم إبطال الـ Token");
      await load();
    } catch (e) {
      toast.error("فشل الإبطال");
    }
  };

  if (loading) {
    return (
      <Section title="Webhook Token (Make.com → ميزان)">
        <div className="text-sm text-slate-500"
             data-testid="webhook-token-loading">
          جاري التحميل…
        </div>
      </Section>
    );
  }

  return (
    <Section title="Webhook Token (Make.com → ميزان)">
      {/* One-time plaintext disclosure */}
      {revealedToken && (
        <div className="rounded-lg border-2 border-amber-400 bg-amber-50 p-3 space-y-2"
             data-testid="webhook-token-revealed">
          <div className="flex items-start gap-2">
            <span className="text-amber-700 text-lg">⚠</span>
            <div className="flex-1">
              <div className="text-sm font-extrabold text-amber-900">
                هذه القيمة لن تظهر مرة أخرى — انسخها الآن والصقها في Make.com.
              </div>
              <div className="text-[11px] text-amber-700 mt-1">
                لو ضاعت، اضغط &quot;إعادة التوليد&quot; لإصدار قيمة جديدة (سيتم إبطال هذه فوراً).
              </div>
            </div>
          </div>
          <div className="flex gap-2 items-stretch">
            <code className="flex-1 px-3 py-2 text-xs font-mono break-all
                              bg-white border border-amber-300 rounded select-all"
                  dir="ltr"
                  data-testid="webhook-token-plaintext">
              {revealedToken}
            </code>
            <button
              onClick={copyToken}
              className={`px-3 py-2 text-sm font-bold rounded text-white
                          ${copied ? "bg-emerald-600 hover:bg-emerald-700"
                                   : "bg-slate-900 hover:bg-black"}`}
              data-testid="btn-copy-webhook-token">
              {copied ? "✓ تم النسخ" : "📋 نسخ"}
            </button>
            <button
              onClick={dismissReveal}
              className="px-3 py-2 text-sm font-bold rounded bg-slate-200 hover:bg-slate-300"
              data-testid="btn-dismiss-webhook-token">
              إغلاق
            </button>
          </div>
        </div>
      )}

      {/* Configured state */}
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
            {meta.last_verified_at && (
              <div className="text-[11px] text-emerald-700 mt-0.5">
                آخر استعمال ناجح: {new Date(meta.last_verified_at)
                                     .toLocaleString("ar-SA")}
              </div>
            )}
          </div>
          <div className="flex gap-2">
            <button
              onClick={generate} disabled={generating}
              className="px-3 py-2 text-sm font-bold rounded-lg bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-50"
              data-testid="btn-regenerate-webhook-token">
              {generating ? "جاري التوليد…" : "إعادة التوليد"}
            </button>
            <button
              onClick={revoke}
              className="px-3 py-2 text-sm font-bold rounded-lg bg-rose-100 text-rose-700 hover:bg-rose-200"
              data-testid="btn-revoke-webhook-token">
              إبطال
            </button>
          </div>
        </div>
      ) : (
        // Not configured (or revoked) — only the Generate button.
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-4"
             data-testid="webhook-token-empty">
          <div className="text-sm text-slate-700 mb-3">
            لم يتم توليد Webhook Token بعد. هذا الـ Token يسمح لـ Make.com بإرسال
            طلبات سلة إلى مسار قيود داخل ميزان (مستقل تماماً عن Webhook التقارير).
          </div>
          <ul className="text-[12px] text-slate-600 list-disc pr-5 mb-3 space-y-0.5">
            <li>القيمة تُولَّد عشوائياً (48 بايت ≈ 384 bit) وتُحفظ مشفّرة في قاعدة البيانات.</li>
            <li>تظهر القيمة الكاملة <strong>مرة واحدة فقط</strong> بعد التوليد.</li>
            <li>لاحقاً يُعرض Fingerprint فقط (لا يمكن استرجاع القيمة الأصلية).</li>
            <li>إعادة التوليد تُبطل القيمة السابقة فوراً.</li>
          </ul>
          <button
            onClick={generate} disabled={generating}
            className="px-4 py-2 text-sm font-bold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
            data-testid="btn-generate-webhook-token">
            {generating ? "جاري التوليد…" : "🔑 توليد Webhook Token"}
          </button>
        </div>
      )}
    </Section>
  );
}

function ToggleRow({ label, hint, checked, onChange, testid }) {
  return (
    <label className="flex items-start justify-between gap-3 p-2 rounded-lg hover:bg-slate-50 cursor-pointer">
      <span className="flex-1">
        <span className="block text-sm font-bold text-slate-700">{label}</span>
        {hint && <span className="block text-xs text-slate-500 mt-0.5">{hint}</span>}
      </span>
      <input
        type="checkbox"
        checked={!!checked}
        onChange={(e) => onChange(e.target.checked)}
        data-testid={testid}
        className="mt-1 w-5 h-5 accent-emerald-600 cursor-pointer"
      />
    </label>
  );
}

export default function QoyodSettings() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]   = useState(false);
  const [settings, setSettings] = useState(null);
  const [apiKey, setApiKey]     = useState("");
  const [branches, setBranches] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [taxes, setTaxes]       = useState([]);
  const [testing, setTesting]   = useState(false);
  const [testResult, setTestResult] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/integrations/qoyod/settings`);
      setSettings(data);
      if (data.credentials?.fingerprint) {
        await loadCatalogs();
      }
    } catch (e) {
      toast.error("تعذّر تحميل الإعدادات");
    } finally {
      setLoading(false);
    }
  };

  const loadCatalogs = async () => {
    // Best-effort — silently skip if API key not configured.
    const tryGet = async (path, setter) => {
      try {
        const { data } = await axios.get(`${API}/integrations/qoyod/${path}`);
        if (data.ok) setter(data.data || []);
      } catch (_) { /* no-op */ }
    };
    await Promise.all([
      tryGet("qoyod-branches", setBranches),
      tryGet("qoyod-accounts", setAccounts),
      tryGet("qoyod-taxes",    setTaxes),
    ]);
  };

  useEffect(() => { load(); }, []);

  const patch = (changes) =>
    setSettings((s) => ({ ...s, ...changes }));

  const patchCaps = (changes) =>
    setSettings((s) => ({
      ...s,
      capabilities: { ...(s?.capabilities || {}), ...changes },
    }));

  const saveCredentials = async () => {
    if (!apiKey.trim()) {
      toast.error("أدخل مفتاح API الخاص بقيود");
      return;
    }
    try {
      await axios.post(`${API}/integrations/qoyod/credentials`, { api_key: apiKey });
      setApiKey("");
      toast.success("تم حفظ المفتاح بشكل آمن");
      await load();
    } catch (e) {
      toast.error("فشل حفظ المفتاح");
    }
  };

  const removeCredentials = async () => {
    if (!window.confirm("حذف مفتاح API؟ سيُعطّل الإرسال تلقائياً.")) return;
    try {
      await axios.delete(`${API}/integrations/qoyod/credentials`);
      toast.success("تم حذف المفتاح وتعطيل الإرسال");
      setBranches([]); setAccounts([]); setTaxes([]);
      await load();
    } catch (e) {
      toast.error("فشل حذف المفتاح");
    }
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
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    try {
      const patch = {
        enabled:              settings.enabled,
        auto_send:            settings.auto_send,
        auto_receipt:         settings.auto_receipt,
        dry_run_mode:         !!settings.dry_run_mode,
        invoice_trigger_statuses: Array.isArray(settings.invoice_trigger_statuses)
          ? settings.invoice_trigger_statuses
          : (settings.invoice_trigger_status
              ? [settings.invoice_trigger_status]
              : ["completed"]),
        invoice_date_source:  settings.invoice_date_source || "trigger_status_date",
        trigger_once_only:    settings.trigger_once_only !== false,
        default_branch_id:    settings.default_branch_id,
        default_tax_id:       settings.default_tax_id,
        default_product_type: settings.default_product_type,
        capabilities:         settings.capabilities,
      };
      await axios.put(`${API}/integrations/qoyod/settings`, patch);
      toast.success("تم حفظ الإعدادات");
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "فشل الحفظ");
    } finally {
      setSaving(false);
    }
  };

  if (loading || !settings) {
    return <div className="p-8 text-center text-slate-500">جاري التحميل…</div>;
  }

  const hasCreds = !!settings.credentials?.fingerprint;

  return (
    <div dir="rtl" className="max-w-4xl mx-auto p-4 md:p-6" data-testid="qoyod-settings-page">
      <header className="mb-5">
        <h1 className="text-2xl md:text-3xl font-extrabold text-slate-900">
          إعدادات تكامل قيود
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          ربط متجرك بقيود وإرسال فواتير الطلبات تلقائياً
        </p>
      </header>

      {/* ─── Master switches ─── */}
      <Section title="المفاتيح الرئيسية">
        <ToggleRow
          label="تفعيل التكامل مع قيود"
          hint="عند الإيقاف لن يُرسل ميزان أي فاتورة لقيود."
          checked={settings.enabled}
          onChange={(v) => patch({ enabled: v })}
          testid="toggle-enabled"
        />
        <ToggleRow
          label="إرسال تلقائي عند تنفيذ الطلب"
          hint="بدون هذا، يلزم الضغط يدوياً على زر الإرسال لكل طلب."
          checked={settings.auto_send}
          onChange={(v) => patch({ auto_send: v })}
          testid="toggle-auto-send"
        />
        <ToggleRow
          label="إنشاء سند قبض تلقائياً بعد الفاتورة"
          hint="إن أوقفته، تُنشأ الفاتورة فقط ويُترك السند للمراجعة اليدوية."
          checked={settings.auto_receipt}
          onChange={(v) => patch({ auto_receipt: v })}
          testid="toggle-auto-receipt"
        />
        <ToggleRow
          label="🧪 وضع التشغيل الجاف (Dry Run Mode)"
          hint="عند التفعيل: ينفّذ المسار كاملاً ويُحفَظ كل Payload في snapshot دون إرسال أي طلب فعلي إلى قيود. مناسب لاختبار دفعة طلبات قبل أول إرسال حقيقي."
          checked={!!settings.dry_run_mode}
          onChange={(v) => patch({ dry_run_mode: v })}
          testid="toggle-dry-run-mode"
        />
      </Section>

      {/* ─── API Key ─── */}
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
              <button
                onClick={test}
                disabled={testing}
                data-testid="btn-test-connection"
                className="px-3 py-2 text-sm font-bold rounded-lg bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-50"
              >
                {testing ? "جاري الاختبار…" : "اختبار الاتصال"}
              </button>
              <button
                onClick={removeCredentials}
                data-testid="btn-remove-credentials"
                className="px-3 py-2 text-sm font-bold rounded-lg bg-rose-100 text-rose-700 hover:bg-rose-200"
              >
                حذف المفتاح
              </button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2">
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="الصق هنا مفتاح Qoyod API الخاص بك"
              dir="ltr"
              data-testid="input-api-key"
              className="flex-1 px-3 py-2 border border-slate-300 rounded-lg text-sm font-mono"
            />
            <button
              onClick={saveCredentials}
              data-testid="btn-save-credentials"
              className="px-4 py-2 text-sm font-bold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700"
            >
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

      {/* ─── Webhook Token (Make.com inbound auth) ─── */}
      <WebhookTokenSection />

      {/* ─── Defaults ─── */}
      <Section title="الإعدادات الافتراضية">
        <div className="grid md:grid-cols-2 gap-3">
          <div className="block md:col-span-2">
            <span className="text-xs font-bold text-slate-700">
              حالات الطلب التي تطلق إنشاء الفاتورة (Invoice Trigger Statuses)
            </span>
            <p className="text-[11px] text-slate-500 mt-0.5 mb-2">
              تُنشأ الفاتورة في قيود فقط عند انتقال الطلب لأحد هذه الحالات.
              الافتراضي: «تم التنفيذ» وفقاً لمتطلبات الزكاة والضريبة.
            </p>
            <div className="grid grid-cols-2 gap-1.5 mt-1" data-testid="trigger-statuses-list">
              {TRIGGER_STATUS_OPTIONS.map((o) => {
                const list = Array.isArray(settings.invoice_trigger_statuses)
                  ? settings.invoice_trigger_statuses
                  : (settings.invoice_trigger_status ? [settings.invoice_trigger_status] : ["completed"]);
                const checked = list.includes(o.value);
                return (
                  <label key={o.value}
                         className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs cursor-pointer transition border
                                     ${checked ? "bg-emerald-50 border-emerald-300 text-emerald-900"
                                               : "bg-white border-slate-200 hover:bg-slate-50"}`}
                         data-testid={`trigger-status-${o.value}`}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) => {
                        const next = e.target.checked
                          ? Array.from(new Set([...list, o.value]))
                          : list.filter((v) => v !== o.value);
                        // Never allow an empty list — default to ["completed"].
                        patch({ invoice_trigger_statuses: next.length ? next : ["completed"] });
                      }}
                      className="h-4 w-4 accent-emerald-600"
                    />
                    <span className="font-medium">{o.label}</span>
                  </label>
                );
              })}
            </div>
          </div>
          <label className="block">
            <span className="text-xs font-bold text-slate-700">تاريخ الفاتورة المعتمد</span>
            <select
              value={settings.invoice_date_source || "trigger_status_date"}
              onChange={(e) => patch({ invoice_date_source: e.target.value })}
              data-testid="select-invoice-date-source"
              className="mt-1 w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
            >
              {INVOICE_DATE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>
          <ToggleRow
            label="إنشاء الفاتورة لمرة واحدة فقط (Trigger Once Only)"
            hint="عند التفعيل: لن تُنشأ فاتورة جديدة لطلب أُرسل سابقاً، حتى لو تغيّرت حالته."
            checked={settings.trigger_once_only !== false}
            onChange={(v) => patch({ trigger_once_only: v })}
            testid="toggle-trigger-once-only"
          />
          <label className="block md:col-span-2">
            <span className="text-xs font-bold text-slate-700">نوع المنتجات الافتراضي في قيود</span>
            <select
              value={settings.default_product_type}
              onChange={(e) => patch({ default_product_type: e.target.value })}
              data-testid="select-product-type"
              className="mt-1 w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
            >
              {PRODUCT_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs font-bold text-slate-700">الفرع الافتراضي</span>
            <select
              value={settings.default_branch_id || ""}
              onChange={(e) => patch({ default_branch_id: e.target.value || null })}
              data-testid="select-branch"
              disabled={!hasCreds}
              className="mt-1 w-full px-3 py-2 border border-slate-300 rounded-lg text-sm disabled:bg-slate-100"
            >
              <option value="">— اختر فرع —</option>
              {branches.map((b) => (
                <option key={b.id || b.value} value={b.id || b.value}>
                  {b.name_ar || b.name || b.label}
                </option>
              ))}
            </select>
            {!hasCreds && (
              <span className="text-[10px] text-slate-400">احفظ مفتاح API أولاً لجلب الفروع</span>
            )}
          </label>
          <label className="block">
            <span className="text-xs font-bold text-slate-700">ضريبة القيمة المضافة الافتراضية</span>
            <select
              value={settings.default_tax_id || ""}
              onChange={(e) => patch({ default_tax_id: e.target.value || null })}
              data-testid="select-tax"
              disabled={!hasCreds}
              className="mt-1 w-full px-3 py-2 border border-slate-300 rounded-lg text-sm disabled:bg-slate-100"
            >
              <option value="">— اختر ضريبة —</option>
              {taxes.map((t) => (
                <option key={t.id || t.value} value={t.id || t.value}>
                  {t.name_ar || t.name || `${t.rate}%`}
                </option>
              ))}
            </select>
          </label>
        </div>
      </Section>

      {/* ─── Capability flags ─── */}
      <Section title="صلاحيات العمليات (Capability Flags)">
        <p className="text-xs text-slate-500 mb-2">
          تحكم دقيق في ما يستطيع ميزان فعله في قيود. تعطيل أي خانة يوقف هذه العملية فقط.
        </p>
        <ToggleRow
          label="إنشاء العملاء في قيود"
          checked={settings.capabilities?.create_customers}
          onChange={(v) => patchCaps({ create_customers: v })}
          testid="cap-customers"
        />
        <ToggleRow
          label="إنشاء المنتجات في قيود"
          checked={settings.capabilities?.create_products}
          onChange={(v) => patchCaps({ create_products: v })}
          testid="cap-products"
        />
        <ToggleRow
          label="إنشاء الفواتير في قيود"
          checked={settings.capabilities?.create_invoices}
          onChange={(v) => patchCaps({ create_invoices: v })}
          testid="cap-invoices"
        />
        <ToggleRow
          label="إنشاء سندات القبض في قيود"
          checked={settings.capabilities?.create_receipts}
          onChange={(v) => patchCaps({ create_receipts: v })}
          testid="cap-receipts"
        />
      </Section>

      {/* ─── Save bar ─── */}
      <div className="sticky bottom-0 bg-white/90 backdrop-blur border-t border-slate-200 -mx-4 md:-mx-6 px-4 md:px-6 py-3 mt-4 flex justify-end">
        <button
          onClick={save}
          disabled={saving}
          data-testid="btn-save-settings"
          className="px-5 py-2.5 text-sm font-extrabold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {saving ? "جاري الحفظ…" : "حفظ الإعدادات"}
        </button>
      </div>
    </div>
  );
}
