import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../context/AuthContext";
import { getGoogleMerchantChoices } from "../../services/googleMerchantObservationChoices";
import GoogleMerchantObservation from "./GoogleMerchantObservation";

const BUTTON = "rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-bold disabled:opacity-40";
const SELECT = "mt-1 w-full rounded-lg border border-slate-300 bg-white p-2 text-sm";

function OwnerSelector({ ownerId }) {
    const [choices, setChoices] = useState(null), [accountId, setAccountId] = useState(""), [merchantId, setMerchantId] = useState("");
    const [busy, setBusy] = useState(false), [error, setError] = useState("");
    const request = useRef(null), generation = useRef(0), mounted = useRef(true);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current += 1; request.current?.abort(); }; }, []);
    function clear() { setChoices(null); setAccountId(""); setMerchantId(""); }
    function cancel() {
        generation.current += 1; request.current?.abort(); request.current = null;
        setBusy(false); setError(""); clear();
    }
    async function refresh() {
        if (request.current) return;
        const current = ++generation.current, controller = new AbortController();
        request.current = controller; setBusy(true); setError(""); clear();
        const active = () => mounted.current && generation.current === current && !controller.signal.aborted;
        try {
            const result = await getGoogleMerchantChoices({ ownerId, signal: controller.signal });
            if (active()) setChoices(result);
        } catch (failure) {
            if (active()) setError([401, 403].includes(failure?.response?.status)
                ? "تعذر تأكيد صلاحية جلسة المالك. راجع تسجيل الدخول ثم حدّث القائمة."
                : "تعذر التحقق من قائمة الحسابات الحالية. راجع الربط ثم حدّث القائمة.");
        } finally {
            if (active()) { request.current = null; setBusy(false); }
        }
    }
    const selected = choices && choices.adsAccounts.some((row) => row.account_id === accountId)
        && choices.merchantAccounts.some((row) => row.merchant_id === merchantId);
    return <section dir="rtl" className="space-y-3 rounded-xl border border-slate-200 bg-white p-4" data-testid="google-merchant-observation-selector">
        <h3 className="font-bold">قراءة منتجات Merchant Center</h3>
        <p className="text-sm leading-6 text-slate-600">اختر الحسابين بنفسك من بيانات الربط المحفوظة. القائمة لا تثبت ربط الحسابين أو صلاحية قراءة المنتجات؛ تتحقق القراءة التالية منهما.</p>
        <div className="flex gap-2"><button type="button" className={BUTTON} onClick={refresh} disabled={busy}>تحديث قائمة الحسابات</button>
            {busy && <button type="button" className={BUTTON} onClick={cancel}>إلغاء تحديث القائمة</button>}</div>
        {busy && <p role="status">جارٍ التحقق من الحسابات المحفوظة…</p>}
        {error && <p role="alert" className="text-sm text-amber-900">{error}</p>}
        {choices && <>
            <p className="text-xs leading-6 text-slate-500">هذه قائمة الحسابات المحفوظة في ميزان وقت القراءة، وليست حصرًا لكل حساباتك لدى جوجل. لا تثبت استمرار الصلاحية بعد القراءة.</p>
            <div className="grid gap-3 md:grid-cols-2">
                <label className="text-sm font-bold">حساب إعلانات Google<select aria-label="حساب إعلانات Google" className={SELECT} value={accountId} onChange={(event) => setAccountId(event.target.value)}>
                    <option value="">اختر حساب إعلانات Google</option>
                    {choices.adsAccounts.map((row) => <option key={row.account_id} value={row.account_id}>{row.display_name} · {row.account_id}</option>)}
                </select></label>
                <label className="text-sm font-bold">حساب Merchant Center<select aria-label="حساب Merchant Center" className={SELECT} value={merchantId} onChange={(event) => setMerchantId(event.target.value)}>
                    <option value="">اختر حساب Merchant Center</option>
                    {choices.merchantAccounts.map((row) => <option key={row.merchant_id} value={row.merchant_id}>{row.display_name} · {row.merchant_id}</option>)}
                </select></label>
            </div>
            {(!choices.adsAccounts.length || !choices.merchantAccounts.length) && <p className="text-sm text-amber-900">لا توجد خيارات محفوظة كافية لاختيار الحسابين. راجع الربط؛ هذا لا يعني عدم وجود حسابات لديك في جوجل.</p>}
            {selected && <GoogleMerchantObservation key={JSON.stringify([ownerId, choices.observationId, accountId, merchantId])}
                isOwner ownerId={ownerId} accountId={accountId} merchantId={merchantId} />}
        </>}
    </section>;
}

export default function GoogleMerchantObservationSelector() {
    const { user, authStatus } = useAuth();
    if (authStatus !== "authenticated" || !user || typeof user.id !== "string" || !user.id || user.id.length > 128
        || /[\u0000-\u001f\u007f]/u.test(user.id) || typeof user.role !== "string" || user.role.toLowerCase() !== "owner"
        || user.disabled != null && user.disabled !== false || user.is_active != null && user.is_active !== true
        || user.deleted_at != null && user.deleted_at !== "") return null;
    return <OwnerSelector key={user.id} ownerId={user.id} />;
}
