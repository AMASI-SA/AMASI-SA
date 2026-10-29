import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const ENDPOINT = "/integrations-v2/google_ads/merchant-products/observation";
const BUTTON = "rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-bold disabled:opacity-40";
const PAGE_SIZE = 25;
const FALSE_FLAGS = ["atomic_snapshot", "store_product_mapping_verified", "google_ads_channel_verified",
    "destination_verified", "category_membership_verified", "attribution_verified", "execution_authorized"];
const LINKS = [["link", "رابط المنتج الوارد"], ["mobile_link", "رابط الجوال"],
    ["canonical_link", "الرابط الأساسي الوارد"], ["ads_redirect", "رابط إعادة التوجيه الإعلاني"]];

function numericId(value) {
    return typeof value === "string" && /^[1-9][0-9]{0,18}$/.test(value)
        && (value.length < 19 || value <= "9223372036854775807");
}
function text(value, maximum) {
    return typeof value === "string" && value.length > 0 && value.length <= maximum
        && value.trim() === value && !/[\u0000-\u001f\u007f-\u009f\ud800-\udfff]/u.test(value);
}
function record(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function timestamp(value) {
    return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value));
}
function optionalText(value, maximum) { return value === null || text(value, maximum); }
function urlText(value) {
    if (value === null) return true;
    if (!text(value, 8192) || /[\s\\]/u.test(value)) return false;
    try {
        const parsed = new URL(value);
        return ["http:", "https:"].includes(parsed.protocol) && Boolean(parsed.hostname) && !parsed.username && !parsed.password;
    } catch { return false; }
}
function unproved() { throw new Error("GOOGLE_MERCHANT_OBSERVATION_UNPROVED"); }

// Consumer validation preserves the server's native identity and explicit
// limits. It does not turn a response/digest into later execution authority.
export function validateMerchantObservation(value, { ownerId, accountId, merchantId }) {
    if (!record(value) || !record(value.scope) || value.scope.owner_id !== ownerId
        || value.scope.ads_customer_id !== accountId || value.scope.merchant_id !== merchantId
        || value.source !== "merchant_processed_products_v1" || value.complete !== true
        || value.mapping_status !== "unverified" || value.provider_writes !== 0
        || FALSE_FLAGS.some((field) => value[field] !== false)
        || !Array.isArray(value.products) || value.products.length > 2000
        || !Number.isInteger(value.pages_read) || value.pages_read < 1 || value.pages_read > 10
        || !timestamp(value.started_at) || !timestamp(value.observed_at)
        || Date.parse(value.started_at) > Date.parse(value.observed_at)
        || typeof value.semantic_digest !== "string" || !/^[a-f0-9]{64}$/.test(value.semantic_digest)) unproved();
    const access = value.access_observation;
    if (!record(access) || access.ads_customer_id !== accountId || access.merchant_id !== merchantId
        || access.local_membership !== "matching_bracketing_snapshots"
        || access.native_link_set !== "matching_complete_bracketing_reads"
        || access.continuous_authorization_proved !== false || !timestamp(access.checked_at)
        || !text(access.credential_evidence_id, 128)
        || typeof access.native_link_resource !== "string") unproved();
    const prefix = `customers/${accountId}/productLinks/`;
    if (!access.native_link_resource.startsWith(prefix) || !numericId(access.native_link_resource.slice(prefix.length))) unproved();
    const seen = new Set();
    for (const row of value.products) {
        if (!record(row) || row.merchant_id !== merchantId || typeof row.legacy_local !== "boolean"
            || typeof row.archived !== "boolean" || !text(row.offer_id, 256)
            || typeof row.language_code !== "string" || !/^[a-z]{2}$/.test(row.language_code)
            || typeof row.feed_label !== "string" || !/^[A-Z0-9_-]{1,20}$/.test(row.feed_label)
            || !optionalText(row.base64_encoded_name, 2048) || !optionalText(row.item_group_id, 256)
            || !optionalText(row.version_number, 20) || LINKS.some(([key]) => !urlText(row[key]))) unproved();
        const identity = `${row.legacy_local ? "local~" : ""}${row.language_code}~${row.feed_label}~${row.offer_id}`;
        if (row.resource_name !== `accounts/${merchantId}/products/${identity}` || seen.has(row.resource_name)
            || typeof row.data_source !== "string" || !row.data_source.startsWith(`accounts/${merchantId}/dataSources/`)
            || !numericId(row.data_source.slice(`accounts/${merchantId}/dataSources/`.length))) unproved();
        seen.add(row.resource_name);
    }
    return value;
}

function errorText(error) {
    const status = error?.response?.status;
    if (status === 401 || status === 403) return "لم تثبت صلاحية القراءة لهذا الحساب. راجع تسجيل الدخول والصلاحيات ثم حاول مجددًا.";
    if (status === 404) return "قراءة منتجات Merchant Center غير متاحة حاليًا لهذا الاختيار.";
    if (status === 409 || error?.message === "GOOGLE_MERCHANT_OBSERVATION_UNPROVED") return "تغير الربط أو تعذر إثبات بياناته كاملة. راجع الحسابين المحددين ثم أعد القراءة.";
    return "تعذرت قراءة المنتجات الآن. يمكنك إعادة المحاولة؛ لم تُحفظ تغييرات إعلانية.";
}

function ObservationPanel({ ownerId, accountId, merchantId }) {
    const [result, setResult] = useState(null), [busy, setBusy] = useState(false);
    const [error, setError] = useState(""), [page, setPage] = useState(0);
    const mounted = useRef(false), controller = useRef(null), generation = useRef(0), inflight = useRef(false);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; generation.current += 1; controller.current?.abort(); };
    }, []);
    async function read() {
        if (inflight.current || !mounted.current) return;
        inflight.current = true;
        const current = ++generation.current;
        const request = new AbortController(); controller.current = request;
        const active = () => mounted.current && generation.current === current && !request.signal.aborted;
        setBusy(true); setResult(null); setError(""); setPage(0);
        try {
            const response = await api.get(ENDPOINT, { params: { account_id: accountId, merchant_id: merchantId },
                signal: request.signal, timeout: 125000, _mezanAuthRetried: true });
            // Existing api's explicit no-auth-replay option prevents a 401 from
            // causing a browser refresh POST and a second automatic GET.
            if (active()) setResult(validateMerchantObservation(response.data, { ownerId, accountId, merchantId }));
        } catch (failure) {
            if (active()) setError(errorText(failure));
        } finally {
            if (active()) { inflight.current = false; setBusy(false); }
        }
    }
    function cancel() {
        generation.current += 1; controller.current?.abort(); inflight.current = false;
        setBusy(false); setResult(null); setError("");
    }
    const rows = result?.products.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE) || [];
    return <section dir="rtl" aria-label="قراءة منتجات Merchant Center" className="rounded-xl border border-slate-200 bg-white p-4 text-sm leading-7">
        <h3 className="font-bold">قراءة منتجات Merchant Center</h3>
        <p>حساب جوجل: <bdi>{accountId}</bdi> · حساب المنتجات: <bdi>{merchantId}</bdi></p>
        <p className="text-slate-600">تُقرأ المنتجات للحسابين المعروضين عند الضغط. لا تثبت هذه القراءة مطابقتها لمنتجات ميزان أو توفر مخزونها أو أهليتها للإعلان.</p>
        <div className="mt-3 flex flex-wrap gap-2"><button type="button" className={BUTTON} disabled={busy} onClick={read}>قراءة منتجات Merchant Center</button>
            {busy && <button type="button" className={BUTTON} onClick={cancel}>إلغاء القراءة</button>}</div>
        {busy && <p role="status">جارٍ التحقق من الحسابين وقراءة المنتجات…</p>}
        {error && <p role="alert" className="mt-3 text-red-800">{error}</p>}
        {result && <div className="mt-3 space-y-3">
            <p>اكتملت قراءة {result.products.length} منتجًا · وقت القراءة: <bdi>{result.observed_at}</bdi></p>
            <p className="text-slate-600">رُوجع الربط قبل القراءة وبعدها؛ قد تتغير البيانات بين القراءتين أو بعدهما. لم تُثبت وجهة الروابط أو عضوية قسم أو نتائج مبيعات، ولا تمنح القراءة تفويضًا لتشغيل إعلان.</p>
            <details><summary className="cursor-pointer font-bold">دليل الربط ومصدر القراءة</summary>
                <p>رابط الحسابين لدى جوجل: <code dir="ltr" className="break-all">{result.access_observation.native_link_resource}</code></p>
                <p>آخر فحص: <bdi>{result.access_observation.checked_at}</bdi></p>
                <p>بصمة البيانات المقروءة: <code dir="ltr" className="break-all">{result.semantic_digest}</code></p>
            </details>
            {result.products.length === 0 ? <p role="status">لم تُرجع القراءة منتجات في حساب Merchant Center المحدد. لا يثبت ذلك خلو متجر سلة من المنتجات.</p> : <>
                <ul className="space-y-3">{rows.map((row) => <li key={row.resource_name} className="rounded-lg border border-slate-200 p-3">
                    <p className="font-bold">معرّف العرض: <bdi>{row.offer_id}</bdi></p>
                    <p>حساب المنتجات: <bdi>{row.merchant_id}</bdi> · اللغة: <bdi>{row.language_code}</bdi> · تغذية المنتجات: <bdi>{row.feed_label}</bdi></p>
                    <p>نوع هوية Merchant Center: {row.legacy_local ? "محلي" : "قياسي"} · {row.archived ? "مؤرشف" : "غير مؤرشف"}</p>
                    <details><summary className="cursor-pointer">الهوية والروابط كما وردت</summary>
                        <p>هوية المنتج: <code dir="ltr" className="break-all">{row.resource_name}</code></p>
                        <p>مصدر البيانات: <code dir="ltr" className="break-all">{row.data_source}</code></p>
                        <p>مجموعة المنتجات لدى المورد: <bdi>{row.item_group_id || "غير واردة"}</bdi> · لا تثبت مطابقة مقاسات المتجر.</p>
                        {LINKS.map(([key, label]) => <p key={key}>{label}: <code dir="ltr" className="break-all">{row[key] || "غير وارد"}</code></p>)}
                        <p>الروابط نصوص للمراجعة؛ لم تُفتح وجهتها أو يُثبت تطابقها مع منتج أو قسم في المتجر.</p>
                    </details>
                </li>)}</ul>
                {result.products.length > PAGE_SIZE && <nav aria-label="صفحات المنتجات المقروءة" className="flex items-center gap-3">
                    <button type="button" className={BUTTON} disabled={page === 0} onClick={() => setPage(page - 1)}>السابق</button>
                    <span>صفحة {page + 1} من {Math.ceil(result.products.length / PAGE_SIZE)}</span>
                    <button type="button" className={BUTTON} disabled={(page + 1) * PAGE_SIZE >= result.products.length} onClick={() => setPage(page + 1)}>التالي</button>
                </nav>}
            </>}
        </div>}
    </section>;
}

export default function GoogleMerchantObservation({ isOwner, ownerId, accountId, merchantId }) {
    if (isOwner !== true) return null;
    if (!text(ownerId, 128) || !numericId(accountId) || !numericId(merchantId)) return <section dir="rtl" aria-label="قراءة منتجات Merchant Center" className="rounded-xl border border-slate-200 p-4 text-sm">اختر صراحةً حساب جوجل وحساب Merchant Center من حسابات المالك المرتبطة لإتاحة القراءة.</section>;
    return <ObservationPanel key={JSON.stringify([ownerId, accountId, merchantId])} ownerId={ownerId} accountId={accountId} merchantId={merchantId} />;
}
