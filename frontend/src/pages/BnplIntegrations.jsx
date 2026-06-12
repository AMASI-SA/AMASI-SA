/**
 * BNPL Integrations Settings — Iter-116
 *
 * Manages Tamara & Tabby credentials, fee settings and connection tests
 * from a single screen.  Secrets are write-only on the wire and shown
 * back as masked previews (e.g. "sk••••••7890").
 *
 * Endpoints:
 *   GET    /api/bnpl/settings
 *   GET    /api/bnpl/settings/:provider
 *   PUT    /api/bnpl/settings/:provider
 *   POST   /api/bnpl/:provider/test-connection
 *   POST   /api/bnpl/tabby/sync                ← manual sync trigger
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
    Lightning, ShieldCheck, ArrowsClockwise, Eye, EyeSlash,
    CheckCircle, XCircle, Receipt, Storefront, Copy, Link as LinkIcon,
    Stethoscope,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { todaySA } from "../lib/dates";

// Pull a human message out of an axios error.  Backend sends
// {"detail": "..."} on 4xx — that's what the user actually needs to see.
const errMsg = (e, fallback) =>
    formatApiErrorDetail(e?.response?.data?.detail) || fallback || "حدث خطأ";

const inputCls =
    "w-full px-3 py-2 rounded-lg border border-slate-300 bg-white text-sm " +
    "focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500";

function Field({ label, hint, children }) {
    return (
        <label className="flex flex-col gap-1">
            <span className="text-xs font-bold text-slate-700">{label}</span>
            {children}
            {hint && <span className="text-[11px] text-slate-500">{hint}</span>}
        </label>
    );
}

function SecretField({ label, hint, value, onChange, masked, hasValue, testid, keyType }) {
    const [reveal, setReveal] = useState(false);
    const liveTest = (keyType === "live" || keyType === "test") ? keyType : null;
    return (
        <label className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-slate-700">{label}</span>
                <div className="flex items-center gap-1">
                    {liveTest === "live" && (
                        <span className="text-[10px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 rounded-full" data-testid={`${testid}-type`}>
                            LIVE ✓
                        </span>
                    )}
                    {liveTest === "test" && (
                        <span className="text-[10px] font-bold text-rose-700 bg-rose-50 border border-rose-300 px-1.5 py-0.5 rounded-full" data-testid={`${testid}-type`}>
                            TEST ⚠
                        </span>
                    )}
                    {hasValue && (
                        <span className="text-[10px] font-bold text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded-full">
                            محفوظ
                        </span>
                    )}
                </div>
            </div>
            <div className="relative">
                <input
                    type={reveal ? "text" : "password"}
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    placeholder={hasValue ? masked : "أدخل المفتاح"}
                    className={inputCls + " pe-10"}
                    data-testid={testid}
                    autoComplete="off"
                />
                <button
                    type="button"
                    onClick={() => setReveal((v) => !v)}
                    className="absolute end-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-700"
                    data-testid={`${testid}-reveal`}
                >
                    {reveal ? <EyeSlash size={16} /> : <Eye size={16} />}
                </button>
            </div>
            {hint && <span className="text-[11px] text-slate-500">{hint}</span>}
            {hasValue && !value && (
                <span className="text-[11px] text-slate-400">
                    اتركه فارغاً للإبقاء على المفتاح المحفوظ، أو أدخل قيمة جديدة لاستبداله.
                </span>
            )}
        </label>
    );
}

function WebhookUrlBlock({ provider, webhookSecret, lastWebhookAt }) {
    const path = `/api/webhooks/${provider === "tabby" ? "tabby/payments" : "tamara/orders"}/${webhookSecret || ""}`;
    const productionUrl = `https://mezansalla.com${path}`;
    const currentUrl = `${typeof window !== "undefined" ? window.location.origin : ""}${path}`;
    const [copied, setCopied] = useState(null);

    const copy = async (text, key) => {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(key);
            toast.success("تم النسخ ✓");
            setTimeout(() => setCopied(null), 1500);
        } catch {
            toast.error("تعذّر النسخ");
        }
    };

    if (!webhookSecret) {
        return (
            <div className="rounded-xl bg-amber-50 border border-amber-200 p-3 mb-4 text-xs text-amber-900">
                جاري توليد رابط الـ Webhook…
            </div>
        );
    }

    return (
        <div className="rounded-xl bg-sky-50 border border-sky-200 p-3 mb-4" data-testid={`bnpl-${provider}-webhook-block`}>
            <h4 className="text-xs font-extrabold text-sky-900 flex items-center gap-1.5 mb-2">
                <LinkIcon size={14} weight="duotone" />
                رابط الـ Webhook الخاص بك
            </h4>

            <div className="space-y-2">
                <div>
                    <div className="text-[10px] font-bold text-emerald-800 mb-1">
                        🌐 رابط Production (لـ {provider === "tabby" ? "Tabby Merchant Dashboard" : "Tamara Partner Portal"})
                    </div>
                    <div className="flex items-stretch gap-1">
                        <code
                            className="flex-1 px-2 py-1.5 text-[11px] bg-white border border-sky-300 rounded-lg font-mono break-all select-all"
                            data-testid={`bnpl-${provider}-webhook-url-production`}
                        >
                            {productionUrl}
                        </code>
                        <button
                            type="button"
                            onClick={() => copy(productionUrl, "prod")}
                            className="px-2.5 py-1.5 bg-emerald-600 text-white rounded-lg text-xs font-bold hover:bg-emerald-700 flex items-center gap-1 flex-shrink-0"
                            data-testid={`bnpl-${provider}-webhook-copy-production`}
                        >
                            <Copy size={14} />
                            {copied === "prod" ? "نُسخ" : "نسخ"}
                        </button>
                    </div>
                </div>

                {currentUrl && currentUrl !== productionUrl && (
                    <details className="text-[10px] text-slate-600">
                        <summary className="cursor-pointer font-bold text-slate-700">
                            رابط البيئة الحالية (للاختبار)
                        </summary>
                        <div className="flex items-stretch gap-1 mt-1">
                            <code className="flex-1 px-2 py-1.5 bg-white border border-slate-300 rounded-lg font-mono break-all select-all">
                                {currentUrl}
                            </code>
                            <button
                                type="button"
                                onClick={() => copy(currentUrl, "curr")}
                                className="px-2.5 py-1.5 bg-slate-600 text-white rounded-lg text-xs font-bold hover:bg-slate-700 flex items-center gap-1 flex-shrink-0"
                            >
                                <Copy size={14} />
                                {copied === "curr" ? "نُسخ" : "نسخ"}
                            </button>
                        </div>
                    </details>
                )}
            </div>

            <div className="mt-2 text-[10px] text-sky-800/80 leading-relaxed">
                {provider === "tamara" ? (
                    <>
                        الصق هذا الرابط في خانة <b>URL</b> في صفحة إنشاء Webhook على Tamara Partner Portal.
                        تأكّد أن <b>Tamara Notification Token</b> أعلاه يطابق ما سيُعطى لك من Tamara عند تفعيل الـ Webhook،
                        وأن نوع الـ Webhook = <b>Order</b> مع تفعيل الأحداث (<b>Approved</b> مطلوب).
                    </>
                ) : (
                    <>
                        الصق هذا الرابط في خانة <b>URL</b> في إعدادات Webhooks بـ Tabby Merchant Dashboard.
                        التفعيل الكامل لمعالجة Tabby webhooks سيتم في المرحلة التالية — الرابط جاهز للتسجيل من الآن.
                    </>
                )}
            </div>

            {lastWebhookAt && (
                <div className="mt-2 text-[10px] text-emerald-700">
                    ✓ آخر إشعار مستلَم: {new Date(lastWebhookAt).toLocaleString("ar-SA")}
                </div>
            )}
        </div>
    );
}


// Iter-121 — weekday-based settlement cycle.  Canonical keys are
// English lowercase; we render the Arabic label next to a checkbox.
const WEEKDAY_OPTIONS = [
    { key: "saturday",  ar: "السبت" },
    { key: "sunday",    ar: "الأحد" },
    { key: "monday",    ar: "الاثنين" },
    { key: "tuesday",   ar: "الثلاثاء" },
    { key: "wednesday", ar: "الأربعاء" },
    { key: "thursday",  ar: "الخميس" },
    { key: "friday",    ar: "الجمعة" },
];

function WeekdayCheckboxes({ value, onChange, testidPrefix }) {
    const v = Array.isArray(value) ? value : [];
    const toggle = (k) => {
        onChange(v.includes(k) ? v.filter((x) => x !== k) : [...v, k]);
    };
    return (
        <div className="flex flex-wrap gap-1.5" data-testid={`${testidPrefix}-group`}>
            {WEEKDAY_OPTIONS.map((d) => {
                const checked = v.includes(d.key);
                return (
                    <label
                        key={d.key}
                        className={`cursor-pointer select-none px-2.5 py-1 rounded-full text-[11px] font-bold border-2 transition ${
                            checked
                                ? "bg-emerald-600 text-white border-emerald-600 shadow-sm"
                                : "bg-white text-slate-600 border-slate-300 hover:border-emerald-400"
                        }`}
                        data-testid={`${testidPrefix}-${d.key}`}
                    >
                        <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggle(d.key)}
                            className="sr-only"
                        />
                        {d.ar}
                    </label>
                );
            })}
        </div>
    );
}



// Iter-134-Auto — vendor-canonical fee presets per provider.  When
// commission_mode = "auto", the UI shows these numbers, disables the
// inputs, and saves them verbatim so the settlement engine has a
// consistent contract regardless of toggle history.
const AUTO_PRESETS = {
    tabby: {
        mdr_percent: 0.0699,
        fixed_fee_per_order: 1.0,
        vat_on_fees_percent: 0.15,
        refundable_commission_percent: 0.0499,
        settlement_fee_per_invoice: 6.0,
        settlement_fee_vat_applicable: true,
    },
    tamara: {
        mdr_percent: 0.0699,
        fixed_fee_per_order: 1.50,
        vat_on_fees_percent: 0.15,
        refundable_commission_percent: 0.0699,
        settlement_fee_per_invoice: 0.0,
        settlement_fee_vat_applicable: true,
    },
};


function ProviderCard({ provider, label, Icon, settings, onReload }) {
    const [form, setForm] = useState(() => ({
        api_token: "",
        notification_token: "",
        secret_key: "",
        merchant_code: settings.merchant_code || "",
        enabled: !!settings.enabled,
        environment: settings.environment || "production",
        activation_date: settings.activation_date || todaySA(),
        mdr_percent: settings.mdr_percent ?? 0,
        fixed_fee_per_order: settings.fixed_fee_per_order ?? 0,
        vat_on_fees_percent: settings.vat_on_fees_percent ?? 0.15,
        settlement_period_days: settings.settlement_period_days ?? 7,
        transfer_days: settings.transfer_days ?? 2,
        // Iter-121 — weekday-based settlement cycle
        invoice_weekdays:  settings.invoice_weekdays  ?? [],
        transfer_weekdays: settings.transfer_weekdays ?? [],
        settlement_fee_per_invoice: settings.settlement_fee_per_invoice ?? 0,
        // Iter-134 — per-order commission split + VAT on settlement fee
        refundable_commission_percent: settings.refundable_commission_percent
            ?? settings.mdr_percent ?? 0,
        settlement_fee_vat_applicable: settings.settlement_fee_vat_applicable ?? true,
        // Iter-134-Auto — Auto (use vendor defaults) vs Manual (merchant tuned)
        commission_mode: settings.commission_mode || "auto",
    }));
    const [busy, setBusy] = useState(false);
    const [syncing, setSyncing] = useState(false);

    const set = (k, v) => setForm((s) => ({ ...s, [k]: v }));

    // Iter-134-Auto — switching to "auto" forces vendor-canonical
    // presets so the merchant can't accidentally save stale numbers
    // while the toggle is on.  Switching to "manual" leaves the
    // current values alone so they can be tweaked.
    const setCommissionMode = (mode) => {
        if (mode === "auto") {
            const preset = AUTO_PRESETS[provider] || {};
            setForm((s) => ({ ...s, ...preset, commission_mode: "auto" }));
        } else {
            setForm((s) => ({ ...s, commission_mode: "manual" }));
        }
    };

    const isAuto = form.commission_mode === "auto";

    const save = async () => {
        setBusy(true);
        try {
            await api.put(`/bnpl/settings/${provider}`, form);
            toast.success(`تم حفظ إعدادات ${label}`);
            onReload();
        } catch (e) {
            toast.error(errMsg(e, "تعذّر الحفظ"));
        } finally {
            setBusy(false);
        }
    };

    const testConnection = async () => {
        setBusy(true);
        try {
            await api.post(`/bnpl/${provider}/test-connection`);
            toast.success(`اتصال ${label} ناجح ✓`);
            onReload();
        } catch (e) {
            toast.error(errMsg(e, `فشل الاتصال بـ ${label}`));
            onReload();
        } finally {
            setBusy(false);
        }
    };

    const runSync = async () => {
        setSyncing(true);
        try {
            const { data } = await api.post(`/bnpl/${provider}/sync`);
            const s = data.stats || {};
            const fetched = Number(s.fetched || 0);
            if (fetched === 0) {
                toast.warning(
                    `${label} أرجع 0 معاملات في النطاق المطلوب. ` +
                    `جرّب «جلب تاريخي» من صفحة التشخيص بتاريخ أقدم.`,
                    { duration: 6000 },
                );
            } else {
                toast.success(
                    `استلَم ${label} ${fetched} معاملة · ` +
                    `حُفظ: ${s.transactions_upserted || 0} · ` +
                    `مسترجعات: ${s.refunds_upserted || 0} · ` +
                    `طلبات جديدة: ${s.orders_created || 0}`,
                );
            }
            onReload();
        } catch (e) {
            toast.error(errMsg(e, "فشلت المزامنة"));
        } finally {
            setSyncing(false);
        }
    };

    const clearKeys = async () => {
        if (!window.confirm(`هل أنت متأكّد من مسح كل مفاتيح ${label}؟ الإعدادات والرسوم تبقى كما هي.`)) return;
        setBusy(true);
        try {
            await api.put(`/bnpl/settings/${provider}`, { clear_secrets: true });
            toast.success(`تم مسح مفاتيح ${label}`);
            onReload();
        } catch (e) {
            toast.error(errMsg(e, "تعذّر المسح"));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div
            className="rounded-2xl border border-slate-200 bg-white p-5"
            data-testid={`bnpl-card-${provider}`}
        >
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                    <Icon size={22} weight="duotone" className="text-emerald-700" />
                    <h3 className="text-lg font-extrabold text-slate-900">{label}</h3>
                    {settings.enabled && (
                        <span className="text-[10px] font-bold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full">
                            مفعّل
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-2 text-[11px] text-slate-500">
                    {settings.last_test_ok === true && (
                        <span className="flex items-center gap-1 text-emerald-700">
                            <CheckCircle size={14} weight="fill" /> آخر اتصال ناجح
                        </span>
                    )}
                    {settings.last_test_ok === false && (
                        <span className="flex items-center gap-1 text-rose-700">
                            <XCircle size={14} weight="fill" /> فشل آخر اتصال
                        </span>
                    )}
                </div>
            </div>

            {/* Webhook URL (Iter-116 Phase 2B) */}
            <WebhookUrlBlock
                provider={provider}
                webhookSecret={settings.webhook_secret}
                lastWebhookAt={settings.last_webhook_at}
            />

            {/* Live/Test mismatch warning (Iter-116 fix) */}
            {((provider === "tabby" && settings.secret_key_type === "test"
                && settings.environment === "production") ||
              (provider === "tamara" && settings.api_token_type === "test"
                && settings.environment === "production")) && (
                <div className="rounded-xl bg-rose-50 border-2 border-rose-300 p-3 mb-4 text-xs text-rose-900" data-testid={`bnpl-${provider}-test-key-warning`}>
                    <div className="font-extrabold mb-1 flex items-center gap-1.5">
                        <XCircle size={16} weight="fill" /> تحذير: مفتاح Test على بيئة Production
                    </div>
                    <div className="leading-relaxed">
                        مفتاحك يبدأ بـ <code>sk_test_</code> أو <code>pk_test_</code> لكن البيئة مضبوطة على <b>Production</b>.
                        لن يُرجع لك Tabby أي معاملات حقيقية بهذا المفتاح. الحلول:
                        <ul className="list-disc ps-5 mt-1 space-y-0.5">
                            <li>اذهب لـ <code>merchant.tabby.sa</code> → API Keys واحصل على مفتاح <b>Live</b> (يبدأ بـ <code>sk_live_</code>)</li>
                            <li>أو غيّر «البيئة» إلى <b>Sandbox / Test</b> أعلاه</li>
                        </ul>
                    </div>
                </div>
            )}

            {/* Credentials */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
                {provider === "tabby" && (
                    <>
                        <SecretField
                            label="Tabby Secret Key"
                            hint="sk_live_… أو sk_test_… من Tabby Merchant Dashboard"
                            value={form.secret_key || ""}
                            onChange={(v) => set("secret_key", v)}
                            masked={settings.secret_key_masked}
                            hasValue={settings.has_secret_key}
                            keyType={settings.secret_key_type}
                            testid="bnpl-tabby-secret-key"
                        />
                        <Field label="Merchant Code (اختياري)" hint="مطلوب فقط للمتاجر المتعددة">
                            <input
                                type="text"
                                value={form.merchant_code || ""}
                                onChange={(e) => set("merchant_code", e.target.value)}
                                className={inputCls}
                                data-testid="bnpl-tabby-merchant-code"
                            />
                        </Field>
                    </>
                )}
                {provider === "tamara" && (
                    <>
                        <SecretField
                            label="Tamara API Token"
                            hint="من Tamara Partner Portal → Settings → API"
                            value={form.api_token || ""}
                            onChange={(v) => set("api_token", v)}
                            masked={settings.api_token_masked}
                            hasValue={settings.has_api_token}
                            testid="bnpl-tamara-api-token"
                        />
                        <SecretField
                            label="Tamara Notification Token"
                            hint="للتحقّق من الـ Webhooks الواردة"
                            value={form.notification_token || ""}
                            onChange={(v) => set("notification_token", v)}
                            masked={settings.notification_token_masked}
                            hasValue={settings.has_notification_token}
                            testid="bnpl-tamara-notif-token"
                        />
                    </>
                )}
            </div>

            {/* Activation + state */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
                <Field label="تاريخ تفعيل الربط" hint="لن يتم جلب بيانات أقدم من هذا التاريخ تلقائياً">
                    <input
                        type="date"
                        value={form.activation_date || ""}
                        onChange={(e) => set("activation_date", e.target.value)}
                        className={inputCls}
                        data-testid={`bnpl-${provider}-activation-date`}
                    />
                </Field>
                <Field label="البيئة">
                    <select
                        value={form.environment}
                        onChange={(e) => set("environment", e.target.value)}
                        className={inputCls}
                        data-testid={`bnpl-${provider}-env`}
                    >
                        <option value="production">Production</option>
                        <option value="sandbox">Sandbox / Test</option>
                    </select>
                    {settings.api_base_url && (
                        <span className="text-[10px] text-slate-500 mt-1 font-mono break-all">
                            URL: {settings.api_base_url}
                        </span>
                    )}
                </Field>
                <Field label="حالة الربط">
                    <label className="flex items-center gap-2 mt-1.5">
                        <input
                            type="checkbox"
                            checked={!!form.enabled}
                            onChange={(e) => set("enabled", e.target.checked)}
                            className="w-4 h-4"
                            data-testid={`bnpl-${provider}-enabled`}
                        />
                        <span className="text-sm text-slate-700">تفعيل المزامنة التلقائية</span>
                    </label>
                </Field>
            </div>

            {/* Fees */}
            <div className="rounded-xl bg-slate-50 border border-slate-200 p-3 mb-4">
                <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
                    <h4 className="text-xs font-extrabold text-slate-700">
                        إعدادات الرسوم العقدية
                    </h4>
                    {/* Iter-134-Auto — Auto / Manual mode toggle */}
                    <div
                        className="inline-flex items-center bg-white border-2 border-slate-300 rounded-full p-0.5 shadow-sm"
                        role="radiogroup"
                        aria-label="commission-mode"
                        data-testid={`bnpl-${provider}-mode-toggle`}
                    >
                        <button
                            type="button"
                            onClick={() => setCommissionMode("auto")}
                            className={`px-3 py-1 rounded-full text-[11px] font-extrabold transition ${
                                isAuto
                                    ? "bg-emerald-600 text-white shadow"
                                    : "text-slate-600 hover:text-slate-900"
                            }`}
                            data-testid={`bnpl-${provider}-mode-auto`}
                            aria-checked={isAuto}
                            role="radio"
                        >
                            🤖 تلقائي
                        </button>
                        <button
                            type="button"
                            onClick={() => setCommissionMode("manual")}
                            className={`px-3 py-1 rounded-full text-[11px] font-extrabold transition ${
                                !isAuto
                                    ? "bg-amber-600 text-white shadow"
                                    : "text-slate-600 hover:text-slate-900"
                            }`}
                            data-testid={`bnpl-${provider}-mode-manual`}
                            aria-checked={!isAuto}
                            role="radio"
                        >
                            ✏️ يدوي
                        </button>
                    </div>
                </div>
                <div
                    className={`mb-3 rounded-lg px-3 py-2 text-[11px] flex items-start gap-2 ${
                        isAuto
                            ? "bg-emerald-50 border-2 border-emerald-200 text-emerald-900"
                            : "bg-amber-50 border-2 border-amber-200 text-amber-900"
                    }`}
                    data-testid={`bnpl-${provider}-mode-hint`}
                >
                    <span className="text-base">{isAuto ? "🔒" : "⚙️"}</span>
                    <span>
                        {isAuto ? (
                            <>
                                <strong>الوضع التلقائي مفعّل:</strong> القيم أدناه مأخوذة من اتفاقية{" "}
                                {provider === "tabby" ? "تابي" : "تمارا"} الرسمية وتُحدَّث تلقائياً
                                عند أي تغيير من المزوّد. اضغط «يدوي» للتعديل.
                            </>
                        ) : (
                            <>
                                <strong>الوضع اليدوي:</strong> أنت تتحكَّم في كل النسب والرسوم —
                                مفيد إن كان عقدك مع{" "}
                                {provider === "tabby" ? "تابي" : "تمارا"} يختلف عن الأسعار العامة.
                            </>
                        )}
                    </span>
                </div>
                {/* Iter-126 — point users to the unified settings page */}
                <div className="mb-3 rounded-lg bg-blue-50 border-2 border-blue-200 px-3 py-2 text-[11px] text-blue-900 flex items-start gap-2" data-testid={`bnpl-${provider}-fee-source-note`}>
                    <span className="text-base">ℹ️</span>
                    <span>
                        <strong>المصدر الموحد:</strong> نسبة العمولة (MDR)، الرسوم الثابتة لكل طلب، وVAT
                        على الرسوم تُدار من <strong>الإعدادات → طرق الدفع → {provider === "tabby" ? "تابي" : "تمارا"}</strong>،
                        وتُستخدم في كل التقارير والتسويات والمركز المالي.
                        التغيير هنا يحدّث نفس المكان تلقائياً.
                    </span>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                    <Field label="MDR %" hint="مثلاً 0.06 = 6%">
                        <input
                            type="number"
                            step="0.001"
                            value={form.mdr_percent}
                            onChange={(e) => set("mdr_percent", parseFloat(e.target.value) || 0)}
                            className={`${inputCls} ${isAuto ? "bg-slate-100 cursor-not-allowed" : ""}`}
                            disabled={isAuto}
                            readOnly={isAuto}
                            data-testid={`bnpl-${provider}-mdr`}
                        />
                    </Field>
                    <Field label="رسوم ثابتة / طلب" hint="ر.س">
                        <input
                            type="number"
                            step="0.01"
                            value={form.fixed_fee_per_order}
                            onChange={(e) => set("fixed_fee_per_order", parseFloat(e.target.value) || 0)}
                            className={`${inputCls} ${isAuto ? "bg-slate-100 cursor-not-allowed" : ""}`}
                            disabled={isAuto}
                            readOnly={isAuto}
                            data-testid={`bnpl-${provider}-fixed-fee`}
                        />
                    </Field>
                    <Field label="VAT على الرسوم" hint="مثلاً 0.15 = 15%">
                        <input
                            type="number"
                            step="0.01"
                            value={form.vat_on_fees_percent}
                            onChange={(e) => set("vat_on_fees_percent", parseFloat(e.target.value) || 0)}
                            className={`${inputCls} ${isAuto ? "bg-slate-100 cursor-not-allowed" : ""}`}
                            disabled={isAuto}
                            readOnly={isAuto}
                            data-testid={`bnpl-${provider}-vat`}
                        />
                    </Field>
                    <Field label="رسوم التسوية لكل فاتورة (ر.س)" hint="رسوم تُخصم مرة واحدة عند إنشاء كل تسوية — وليس على كل طلب.">
                        <input
                            type="number"
                            step="0.01"
                            min="0"
                            value={form.settlement_fee_per_invoice}
                            onChange={(e) => set("settlement_fee_per_invoice", parseFloat(e.target.value) || 0)}
                            className={`${inputCls} ${isAuto ? "bg-slate-100 cursor-not-allowed" : ""}`}
                            disabled={isAuto}
                            readOnly={isAuto}
                            data-testid={`bnpl-${provider}-settlement-fee`}
                            placeholder={provider === "tabby" ? "6.00" : "0.00"}
                        />
                    </Field>
                </div>

                {/* Iter-134 — Per-order commission split + VAT on settlement fee */}
                <div className="mt-4 pt-4 border-t-2 border-dashed border-slate-300">
                    <h5 className="text-sm font-extrabold text-slate-800 mb-1 flex items-center gap-2">
                        🔬 الدقة المحاسبية المتقدمة (Iter-134)
                    </h5>
                    <p className="text-[11px] text-slate-500 mb-3 leading-relaxed">
                        تُمكّنك من مطابقة فاتورة المزوّد إلى آخر ريال:
                        نسبة العمولة القابلة للاسترجاع (بعض المزودين يُعيد جزءاً فقط من العمولة عند الاسترجاع)،
                        وضريبة ١٥٪ على رسوم التسوية البنكية (مطبَّق افتراضياً وفق نظام الضريبة السعودي).
                    </p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                        <Field
                            label="العمولة القابلة للاسترجاع %"
                            hint={provider === "tabby"
                                ? "مثلاً 0.0499 = 4.99% (الباقي 2% non-refundable)"
                                : "مثلاً 0.07 = 7% (يُعاد الكل عند الاسترجاع)"}
                        >
                            <input
                                type="number"
                                step="0.0001"
                                value={form.refundable_commission_percent}
                                onChange={(e) => set("refundable_commission_percent", parseFloat(e.target.value) || 0)}
                                className={`${inputCls} ${isAuto ? "bg-slate-100 cursor-not-allowed" : ""}`}
                                disabled={isAuto}
                                readOnly={isAuto}
                                data-testid={`bnpl-${provider}-refundable-pct`}
                                placeholder={provider === "tabby" ? "0.0499" : "0.07"}
                            />
                        </Field>
                        <Field
                            label="ضريبة ١٥٪ على رسوم التسوية"
                            hint="مفعّل افتراضياً (KSA VAT لخدمات المعالجة B2B)."
                        >
                            <label className={`flex items-center gap-2 h-9 px-3 rounded-lg border border-slate-200 bg-white ${isAuto ? "opacity-60 cursor-not-allowed" : "cursor-pointer hover:bg-slate-50"}`}>
                                <input
                                    type="checkbox"
                                    checked={!!form.settlement_fee_vat_applicable}
                                    onChange={(e) => set("settlement_fee_vat_applicable", e.target.checked)}
                                    disabled={isAuto}
                                    data-testid={`bnpl-${provider}-settlement-fee-vat`}
                                    className="w-4 h-4"
                                />
                                <span className="text-xs text-slate-700">
                                    {form.settlement_fee_vat_applicable ? "خاضعة للضريبة ١٥٪" : "بدون ضريبة"}
                                </span>
                            </label>
                        </Field>
                    </div>
                </div>

                {/* Iter-121 — Weekday-based settlement cycle */}
                <div className="mt-4 pt-4 border-t-2 border-dashed border-slate-300">
                    <h5 className="text-sm font-extrabold text-slate-800 mb-2 flex items-center gap-2">
                        🗓️ دورة التسوية حسب أيام الأسبوع
                    </h5>
                    <p className="text-[11px] text-slate-500 mb-3">
                        اختر يوم/أيام إصدار الفاتورة (متى يُغلق المزود الفترة) وأيام التحويل البنكي المتوقعة بعدها.
                        تطبق هذه القاعدة على جميع التسويات بدل الاعتماد على عدد أيام ثابت.
                    </p>
                    <div className="space-y-3">
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">
                                أيام إصدار الفاتورة <span className="text-slate-400 font-normal">(يمكن اختيار أكثر من يوم)</span>
                            </label>
                            <WeekdayCheckboxes
                                value={form.invoice_weekdays}
                                onChange={(v) => set("invoice_weekdays", v)}
                                testidPrefix={`bnpl-${provider}-invoice-day`}
                            />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1.5">
                                أيام التحويل البنكي المتوقعة <span className="text-slate-400 font-normal">(يمكن اختيار أكثر من يوم)</span>
                            </label>
                            <WeekdayCheckboxes
                                value={form.transfer_weekdays}
                                onChange={(v) => set("transfer_weekdays", v)}
                                testidPrefix={`bnpl-${provider}-transfer-day`}
                            />
                        </div>
                    </div>
                </div>
            </div>

            {/* Actions */}
            <div className="flex flex-wrap items-center gap-2">
                <button
                    type="button"
                    onClick={save}
                    disabled={busy}
                    className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-bold hover:bg-slate-800 disabled:opacity-50"
                    data-testid={`bnpl-${provider}-save`}
                >
                    حفظ
                </button>
                <button
                    type="button"
                    onClick={testConnection}
                    disabled={busy}
                    className="px-4 py-2 rounded-lg bg-sky-600 text-white text-sm font-bold hover:bg-sky-700 disabled:opacity-50 flex items-center gap-1.5"
                    data-testid={`bnpl-${provider}-test`}
                >
                    <ShieldCheck size={16} /> اختبار الاتصال
                </button>
                {provider === "tabby" && settings.has_secret_key && settings.enabled && (
                    <button
                        type="button"
                        onClick={runSync}
                        disabled={syncing}
                        className="px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-bold hover:bg-emerald-700 disabled:opacity-50 flex items-center gap-1.5"
                        data-testid={`bnpl-${provider}-sync`}
                    >
                        <ArrowsClockwise size={16} className={syncing ? "animate-spin" : ""} />
                        {syncing ? "جاري المزامنة…" : "مزامنة الآن"}
                    </button>
                )}
                {settings.last_sync_at && (
                    <span className="text-[11px] text-slate-500">
                        آخر مزامنة: {new Date(settings.last_sync_at).toLocaleString("ar-SA")}
                    </span>
                )}
                <div className="flex-1" />
                {(settings.has_api_token || settings.has_secret_key ||
                  settings.has_notification_token) && (
                    <button
                        type="button"
                        onClick={clearKeys}
                        disabled={busy}
                        className="px-3 py-2 rounded-lg bg-rose-50 text-rose-700 border border-rose-200 text-xs font-bold hover:bg-rose-100 disabled:opacity-50"
                        data-testid={`bnpl-${provider}-clear`}
                    >
                        مسح المفاتيح
                    </button>
                )}
            </div>

            {settings.last_test_error && (
                <div className="mt-3 text-[11px] text-rose-700 bg-rose-50 border border-rose-200 rounded-lg p-2" data-testid={`bnpl-${provider}-error`}>
                    آخر خطأ: {settings.last_test_error}
                </div>
            )}
        </div>
    );
}

export default function BnplIntegrations() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/bnpl/settings");
            setData(data);
        } catch (e) {
            toast.error(errMsg(e, "تعذّر التحميل"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const { data } = await api.get("/bnpl/settings");
                if (!cancelled) setData(data);
            } catch (e) {
                if (!cancelled) toast.error(errMsg(e, "تعذّر التحميل"));
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, []);

    if (loading && !data) {
        return <div className="p-6 text-center text-slate-500">جاري التحميل…</div>;
    }
    if (!data) return null;

    const providers = data.providers || {};

    return (
        <div className="space-y-4" dir="rtl" data-testid="bnpl-page">
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                        <Lightning size={26} weight="duotone" className="text-amber-600" />
                        ربط Tamara و Tabby
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        مصدر بيانات مستقل للتسويات والمسترجعات. المفاتيح مشفّرة ولا تُعرض كاملة بعد الحفظ.
                    </p>
                </div>
                <Link
                    to="/integrations/bnpl/diagnostics"
                    className="px-3 py-2 rounded-lg bg-sky-600 text-white text-xs font-bold hover:bg-sky-700 flex items-center gap-1.5"
                    data-testid="open-bnpl-diagnostics"
                >
                    <Stethoscope size={16} weight="duotone" /> تقرير التشخيص الشامل
                </Link>
            </div>

            <div className="rounded-xl bg-emerald-50 border border-emerald-200 p-3 text-xs text-emerald-900 flex items-start gap-2">
                <Receipt size={18} weight="duotone" className="text-emerald-700 flex-shrink-0 mt-0.5" />
                <div>
                    <strong>كيف يعمل الربط؟</strong>
                    <ul className="list-disc ps-5 mt-1 space-y-0.5">
                        <li>المزامنة تبدأ من <b>«تاريخ تفعيل الربط»</b> فقط — لا تُسحب بيانات تاريخية تلقائياً.</li>
                        <li>كل عملية دفع جديدة تُحفظ في <code>payment_transactions</code> ويُحدَّث طلب الـ <code>unified_orders</code>.</li>
                        <li>إذا لم يجد النظام الطلب (Make توقف مثلاً) يتم إنشاؤه تلقائياً بـ <code>needs_review=true</code>.</li>
                    </ul>
                </div>
            </div>

            <ProviderCard
                key={`tabby-${providers.tabby?.last_sync_at || ""}-${providers.tabby?.has_secret_key || ""}-${providers.tabby?.enabled || ""}`}
                provider="tabby"
                label="Tabby"
                Icon={Storefront}
                settings={providers.tabby || {}}
                onReload={load}
            />
            <ProviderCard
                key={`tamara-${providers.tamara?.last_sync_at || ""}-${providers.tamara?.has_api_token || ""}-${providers.tamara?.enabled || ""}`}
                provider="tamara"
                label="Tamara"
                Icon={Storefront}
                settings={providers.tamara || {}}
                onReload={load}
            />
        </div>
    );
}
