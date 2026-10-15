import { useCallback, useEffect, useState } from "react";
import {
    ArrowClockwise,
    ArrowRight,
    ChatCircleText,
    CheckCircle,
    InstagramLogo,
    LinkSimple,
    LockKey,
    ShieldCheck,
    Storefront,
    WarningCircle,
} from "@phosphor-icons/react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
    connectInstagramCustomerIntelligence,
    getInstagramCustomerIntelligenceSetup,
} from "../services/customerIntelligence";
import { startMetaConnection } from "../services/metaIntegrationsV2";

const ERROR_MESSAGES = {
    meta_reauthorization_required: "يجب إعادة تفويض Meta بصلاحيات Instagram المطلوبة.",
    connected_store_required: "يجب ربط متجر سلة واحد قبل تفعيل قناة Instagram.",
    instagram_candidate_not_found: "لم يعد الحساب المحدد متاحًا. حدّث القائمة وحاول مجددًا.",
    instagram_binding_conflict: "هذا الحساب مرتبط مسبقًا بمتجر آخر ولا يمكن نقله تلقائيًا.",
    instagram_channel_policy_invalid: "حالة القناة الحالية لا تطابق سياسة الاستقبال الآمن.",
    instagram_page_link_required: "حساب Instagram غير مرتبط بصفحة Facebook داخل Meta.",
    instagram_page_access_required: "تعذر الوصول إلى الصفحة المرتبطة. أعد تفويض Meta ثم حاول مجددًا.",
    instagram_asset_link_mismatch: "الحساب المختار لم يعد مرتبطًا بالصفحة المكتشفة في Meta.",
    instagram_webhook_subscription_failed: "تعذر اشتراك الحساب فعليًا في Webhooks لدى Meta.",
    meta_configuration_invalid: "إعدادات Meta في الخادم غير مكتملة.",
    owner_only: "هذه الخطوة متاحة لمالك الحساب فقط.",
};

const DIAGNOSTIC_OPERATION_LABELS = {
    graph_configuration: "تهيئة Graph API",
    load_encrypted_meta_token: "قراءة ربط Meta المشفر",
    load_meta_encryption_key: "قراءة مفتاح تشفير Meta",
    decrypt_meta_token: "فك ربط Meta المشفر",
    load_meta_app_secret: "قراءة سر تطبيق Meta",
    load_meta_app_id: "قراءة معرف تطبيق Meta",
    resolve_linked_instagram_account: "اكتشاف الصفحة وحساب Instagram المرتبط",
    validate_page_instagram_link: "التحقق من ارتباط الصفحة بالحساب",
    resolve_page_access_token: "استخراج Page Access Token",
    subscribe_instagram_account: "اشتراك حساب Instagram",
    subscribe_instagram_account_unconfirmed: "تأكيد اشتراك حساب Instagram",
    verify_instagram_account_subscription: "قراءة اشتراك حساب Instagram",
    verify_instagram_account_subscription_unconfirmed: "التحقق النهائي من اشتراك حساب Instagram",
    subscribe_linked_page_for_instagram: "اشتراك صفحة Facebook المرتبطة",
    subscribe_linked_page_for_instagram_unconfirmed: "تأكيد اشتراك صفحة Facebook المرتبطة",
    verify_linked_page_instagram_subscription: "قراءة اشتراك صفحة Facebook المرتبطة",
    verify_linked_page_instagram_subscription_unconfirmed: "التحقق النهائي من اشتراك صفحة Facebook المرتبطة",
    meta_transport: "الاتصال بخوادم Meta",
};

function diagnosticSuffix(detail) {
    if (!detail || typeof detail !== "object") return "";
    const parts = [];
    const operation = typeof detail.operation === "string"
        ? detail.operation.trim()
        : "";
    if (operation) {
        parts.push(`المرحلة: ${DIAGNOSTIC_OPERATION_LABELS[operation] || operation}`);
    }
    if (Number.isInteger(detail.http_status)) {
        parts.push(`HTTP ${detail.http_status}`);
    }
    if (Number.isInteger(detail.meta_error_code)) {
        parts.push(`Meta code ${detail.meta_error_code}`);
    }
    if (Number.isInteger(detail.error_subcode)) {
        parts.push(`subcode ${detail.error_subcode}`);
    }
    if (typeof detail.trace_id === "string" && detail.trace_id.trim()) {
        parts.push(`trace ${detail.trace_id.trim()}`);
    }
    const missingPermissions = Array.isArray(detail.missing_page_permissions)
        ? detail.missing_page_permissions
            .filter((value) => typeof value === "string" && value.trim())
            .map((value) => value.trim())
        : [];
    if (missingPermissions.length) {
        parts.push(`صلاحيات الصفحة الناقصة: ${missingPermissions.join(", ")}`);
    } else if (detail.page_subscription_permission_ready === false) {
        parts.push("صلاحيات مسار الصفحة غير مكتملة");
    }
    return parts.length ? ` — ${parts.join("، ")}` : "";
}

function errorMessage(error, fallback) {
    const detail = error?.response?.data?.detail;
    const code = typeof detail === "object" ? detail?.code : null;
    if (code && ERROR_MESSAGES[code]) {
        return `${ERROR_MESSAGES[code]}${diagnosticSuffix(detail)}`;
    }
    if (typeof detail === "string") return detail;
    if (typeof detail?.message === "string") {
        return `${detail.message}${diagnosticSuffix(detail)}`;
    }
    return fallback;
}

function SafetyItem({ Icon, title, value, enabled }) {
    return (
        <div className={`rounded-xl border p-4 ${
            enabled
                ? "border-emerald-200 bg-emerald-50"
                : "border-slate-200 bg-slate-50"
        }`}>
            <div className="flex items-center gap-2">
                <Icon
                    size={20}
                    weight="duotone"
                    className={enabled ? "text-emerald-700" : "text-slate-500"}
                />
                <div className="text-sm font-extrabold text-slate-800">{title}</div>
            </div>
            <div className={`mt-2 text-xs font-bold ${
                enabled ? "text-emerald-700" : "text-slate-500"
            }`}>
                {value}
            </div>
        </div>
    );
}

export function InstagramSetupView({
    setup,
    loading = false,
    error = "",
    selectedRef = "",
    busyAction = "",
    onSelect = () => {},
    onConnect = () => {},
    onRefresh = () => {},
    onReauthorize = () => {},
    onOpenSalla = () => {},
    onOpenInbox = () => {},
    onBack = () => {},
}) {
    const state = setup?.state;
    return (
        <div className="space-y-5" dir="rtl" data-testid="instagram-integration-page">
            <header className="overflow-hidden rounded-2xl border border-fuchsia-950 bg-slate-950 text-white">
                <div className="grid gap-5 bg-gradient-to-l from-fuchsia-950 via-rose-950 to-slate-950 p-5 sm:p-7 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                    <div className="flex items-start gap-4">
                        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-fuchsia-600 via-rose-500 to-amber-400 shadow-lg">
                            <InstagramLogo size={32} weight="duotone" />
                        </div>
                        <div>
                            <h1 className="text-2xl font-black sm:text-3xl">ربط Instagram</h1>
                            <p className="mt-2 max-w-2xl text-sm leading-6 text-rose-100">
                                استقبال رسائل وتعليقات العملاء داخل ذكاء ميزان، من دون إرسال أو رد آلي.
                            </p>
                        </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <button
                            type="button"
                            onClick={onRefresh}
                            disabled={loading || Boolean(busyAction)}
                            className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-white/20 bg-white/10 px-3 text-xs font-extrabold hover:bg-white/20 disabled:opacity-50"
                            data-testid="instagram-setup-refresh"
                        >
                            <ArrowClockwise size={17} weight="bold" />
                            تحديث
                        </button>
                        <button
                            type="button"
                            onClick={onBack}
                            className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-white px-3 text-xs font-extrabold text-slate-950 hover:bg-rose-50"
                        >
                            <ArrowRight size={17} weight="bold" />
                            كل التطبيقات
                        </button>
                    </div>
                </div>
                <div className="flex items-center gap-2 border-t border-white/10 bg-black/20 px-5 py-3 text-xs font-bold text-rose-100 sm:px-7">
                    <ShieldCheck size={18} weight="fill" />
                    الربط مقيّد تقنيًا بوضع الاستقبال فقط.
                </div>
            </header>

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="سياسة Instagram">
                <SafetyItem Icon={ChatCircleText} title="الرسائل الخاصة" value="استقبال مفعل" enabled />
                <SafetyItem Icon={InstagramLogo} title="التعليقات" value="استقبال مفعل" enabled />
                <SafetyItem Icon={LockKey} title="إرسال الرسائل" value="متوقف" enabled={false} />
                <SafetyItem Icon={LockKey} title="الرد التلقائي" value="متوقف" enabled={false} />
            </section>

            {error && (
                <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-800" role="alert">
                    <WarningCircle size={20} weight="fill" className="mt-0.5 shrink-0" />
                    {error}
                </div>
            )}

            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-7">
                {loading ? (
                    <div className="space-y-3" data-testid="instagram-setup-loading">
                        <div className="h-6 w-48 animate-pulse rounded bg-slate-200" />
                        <div className="h-20 animate-pulse rounded-xl bg-slate-100" />
                    </div>
                ) : state === "connected" ? (
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between" data-testid="instagram-setup-connected">
                        <div className="flex items-start gap-3">
                            <CheckCircle size={30} weight="fill" className="shrink-0 text-emerald-600" />
                            <div>
                                <h2 className="text-lg font-black text-slate-950">Instagram مرتبط وجاهز للاستقبال</h2>
                                <p className="mt-1 text-sm leading-6 text-slate-600">
                                    الرسائل والتعليقات الجديدة ستدخل إلى ذكاء العملاء، بينما يبقى الإرسال والرد على التعليقات والرد الآلي متوقفًا.
                                </p>
                            </div>
                        </div>
                        <button
                            type="button"
                            onClick={onOpenInbox}
                            className="shrink-0 rounded-lg bg-emerald-800 px-4 py-3 text-sm font-extrabold text-white hover:bg-emerald-900"
                        >
                            فتح ذكاء العملاء
                        </button>
                    </div>
                ) : state === "ready" ? (
                    <div data-testid="instagram-setup-ready">
                        <h2 className="text-lg font-black text-slate-950">اختر حساب Instagram المراد استقباله</h2>
                        <p className="mt-1 text-sm text-slate-600">يعرض ميزان الاسم فقط؛ تبقى هوية الحساب والرموز مخفية في الخادم.</p>
                        <div className="mt-4 grid gap-2">
                            {(setup?.candidates || []).map((candidate) => (
                                <label
                                    key={candidate.candidate_ref}
                                    className={`flex cursor-pointer items-center gap-3 rounded-xl border p-4 ${
                                        selectedRef === candidate.candidate_ref
                                            ? "border-fuchsia-400 bg-fuchsia-50"
                                            : "border-slate-200 hover:border-fuchsia-200"
                                    }`}
                                >
                                    <input
                                        type="radio"
                                        name="instagram-candidate"
                                        value={candidate.candidate_ref}
                                        checked={selectedRef === candidate.candidate_ref}
                                        onChange={() => onSelect(candidate.candidate_ref)}
                                        className="h-4 w-4 accent-fuchsia-700"
                                    />
                                    <InstagramLogo size={22} className="text-fuchsia-700" weight="duotone" />
                                    <span className="font-extrabold text-slate-800">{candidate.display_name}</span>
                                </label>
                            ))}
                        </div>
                        <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-xs font-bold leading-6 text-emerald-900">
                            بتأكيد الربط، أفعّل استقبال الرسائل والتعليقات فقط. لا أمنح ميزان صلاحية الإرسال أو الرد الآلي.
                        </div>
                        <button
                            type="button"
                            onClick={onConnect}
                            disabled={!selectedRef || Boolean(busyAction)}
                            className="mt-4 inline-flex min-h-11 items-center gap-2 rounded-lg bg-fuchsia-700 px-5 text-sm font-extrabold text-white hover:bg-fuchsia-800 disabled:cursor-not-allowed disabled:opacity-50"
                            data-testid="instagram-setup-connect"
                        >
                            <ShieldCheck size={18} weight="fill" />
                            {busyAction === "connect" ? "جاري التفعيل…" : "تفعيل الاستقبال فقط"}
                        </button>
                    </div>
                ) : state === "meta_reauthorization_required" ? (
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between" data-testid="instagram-setup-reauthorize">
                        <div>
                            <h2 className="text-lg font-black text-amber-950">يلزم تحديث صلاحيات Meta</h2>
                            <p className="mt-1 text-sm leading-6 text-amber-800">
                                أعد تفويض Meta حتى يكتشف ميزان حساب Instagram ويستقبل الرسائل والتعليقات المطلوبة.
                            </p>
                        </div>
                        <button
                            type="button"
                            onClick={onReauthorize}
                            disabled={Boolean(busyAction)}
                            className="inline-flex min-h-11 shrink-0 items-center gap-2 rounded-lg bg-blue-700 px-4 text-sm font-extrabold text-white hover:bg-blue-800 disabled:opacity-50"
                        >
                            <LinkSimple size={18} weight="bold" />
                            {busyAction === "reauthorize" ? "جاري فتح Meta…" : "إعادة تفويض Meta"}
                        </button>
                    </div>
                ) : state === "no_instagram_account" ? (
                    <div data-testid="instagram-setup-no-account">
                        <h2 className="text-lg font-black text-amber-950">لم يظهر حساب Instagram احترافي</h2>
                        <p className="mt-1 text-sm leading-6 text-slate-600">
                            تأكد أن الحساب احترافي ومرتبط بصفحة Facebook، ثم أعد تفويض Meta لتحديث الحسابات المكتشفة.
                        </p>
                        <button
                            type="button"
                            onClick={onReauthorize}
                            disabled={Boolean(busyAction)}
                            className="mt-4 inline-flex min-h-11 items-center gap-2 rounded-lg bg-blue-700 px-4 text-sm font-extrabold text-white hover:bg-blue-800 disabled:opacity-50"
                        >
                            <LinkSimple size={18} weight="bold" />
                            تحديث الربط مع Meta
                        </button>
                    </div>
                ) : state === "store_not_ready" ? (
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between" data-testid="instagram-setup-store-required">
                        <div>
                            <h2 className="text-lg font-black text-amber-950">اربط متجر سلة أولًا</h2>
                            <p className="mt-1 text-sm leading-6 text-slate-600">يحتاج ميزان متجرًا واحدًا متصلًا لربط محادثات Instagram بالمنشأة الصحيحة.</p>
                        </div>
                        <button
                            type="button"
                            onClick={onOpenSalla}
                            className="inline-flex min-h-11 shrink-0 items-center gap-2 rounded-lg bg-emerald-800 px-4 text-sm font-extrabold text-white hover:bg-emerald-900"
                        >
                            <Storefront size={18} weight="bold" />
                            إعداد سلة
                        </button>
                    </div>
                ) : (
                    <div className="text-sm font-bold text-slate-600" data-testid="instagram-setup-unavailable">
                        تعذر تحديد حالة ربط Instagram. اضغط «تحديث» للمحاولة مجددًا.
                    </div>
                )}
            </section>
        </div>
    );
}

export default function InstagramCustomerIntelligenceIntegration() {
    const navigate = useNavigate();
    const [setup, setSetup] = useState(null);
    const [selectedRef, setSelectedRef] = useState("");
    const [loading, setLoading] = useState(true);
    const [busyAction, setBusyAction] = useState("");
    const [error, setError] = useState("");

    const load = useCallback(async ({ silent = false } = {}) => {
        if (!silent) setLoading(true);
        setError("");
        try {
            const next = await getInstagramCustomerIntelligenceSetup();
            setSetup(next);
            setSelectedRef((current) => (
                next.candidates.some((candidate) => candidate.candidate_ref === current)
                    ? current
                    : next.candidates[0]?.candidate_ref || ""
            ));
        } catch (loadError) {
            setSetup(null);
            setError(errorMessage(loadError, "تعذر تحميل حالة Instagram."));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        load();
    }, [load]);

    async function connect() {
        if (!selectedRef || busyAction) return;
        setBusyAction("connect");
        setError("");
        try {
            const result = await connectInstagramCustomerIntelligence(selectedRef);
            if (!result.status) throw new Error("instagram_connect_response_invalid");
            toast.success(result.status === "no_change"
                ? "Instagram مرتبط مسبقًا بوضع الاستقبال فقط."
                : "تم ربط Instagram بوضع الاستقبال فقط.");
            await load({ silent: true });
        } catch (connectError) {
            setError(errorMessage(connectError, "تعذر تفعيل استقبال Instagram."));
        } finally {
            setBusyAction("");
        }
    }

    async function reauthorize() {
        if (busyAction) return;
        setBusyAction("reauthorize");
        setError("");
        try {
            const result = await startMetaConnection();
            window.location.assign(result.authorization_url);
        } catch (reauthorizeError) {
            setError(errorMessage(reauthorizeError, "تعذر بدء إعادة تفويض Meta."));
            setBusyAction("");
        }
    }

    return (
        <InstagramSetupView
            setup={setup}
            loading={loading}
            error={error}
            selectedRef={selectedRef}
            busyAction={busyAction}
            onSelect={setSelectedRef}
            onConnect={connect}
            onRefresh={() => load()}
            onReauthorize={reauthorize}
            onOpenSalla={() => navigate("/settings/salla")}
            onOpenInbox={() => navigate("/customer-intelligence")}
            onBack={() => navigate("/integrations-v2")}
        />
    );
}
