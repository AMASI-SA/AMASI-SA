import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import api, { formatApiErrorDetail } from "../lib/api";

const API = "/price-change-requests-v1";
const EMPTY_ACTION = { busy: false, error: "", stale: false, blocked: false, attempted: null };
const STATUS = {
    pending: { label: "بانتظار موافقتك", style: "bg-amber-50 text-amber-900 border-amber-200" },
    approved: { label: "تم اعتماد السعر الجديد", style: "bg-emerald-50 text-emerald-900 border-emerald-200" },
    rejected: { label: "تم رفض تغيير السعر", style: "bg-slate-100 text-slate-700 border-slate-200" },
};

function readRequest(data, requestId) {
    const request = data?.request;
    const validMoney = amount => Number.isSafeInteger(amount) && amount >= 0;
    const validQuantity = quantity => quantity == null || (typeof quantity === "number" && Number.isFinite(quantity) && quantity > 0);
    if (data?.ok !== true || request?.id !== requestId || !Object.hasOwn(STATUS, request.status)
        || !["product_price", "service_price"].includes(request.change_type)
        || !(request.before_halalas === null || validMoney(request.before_halalas))
        || !(request.removes_override === true ? request.after_halalas === null : validMoney(request.after_halalas))
        || !validQuantity(request.before_quantity) || !validQuantity(request.after_quantity)) {
        const error = new Error("تعذر التحقق من تفاصيل طلب تغيير السعر. أعد تحميل الطلب.");
        error.code = "invalid_price_change_response";
        throw error;
    }
    return request;
}

function errorMessage(error, fallback) {
    const detail = error.response?.data?.detail;
    if (typeof detail?.message === "string") return detail.message;
    if (typeof detail === "string" || Array.isArray(detail) || typeof detail?.msg === "string") return formatApiErrorDetail(detail);
    return error.code === "invalid_price_change_response" ? error.message : fallback;
}

function money(halalas) {
    return `${(halalas / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س.`;
}

function dateTime(value) {
    if (!value) return "غير متوفر";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "غير متوفر" : date.toLocaleString("ar-SA-u-ca-gregory", {
        timeZone: "Asia/Riyadh", year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
}

function confirmedDecision() {
    window.dispatchEvent(new Event("mezan:price-change-decided"));
}

export default function ProductPriceChangeRequest() {
    const { requestId = "" } = useParams();
    const { user, loading: authLoading, authStatus } = useAuth();
    const owner = user?.is_owner === true && !authLoading && (!authStatus || authStatus === "authenticated");
    const viewKey = JSON.stringify([user?.id, owner, requestId]);
    const activeKey = useRef(viewKey);
    activeKey.current = viewKey;
    const mounted = useRef(true);
    const loadSequence = useRef(0);
    const mutation = useRef(null);
    const [state, setState] = useState({ key: "", request: null, loading: true, error: "" });
    const [actionState, setActionState] = useState({ key: "", ...EMPTY_ACTION });
    const current = state.key === viewKey ? state : { request: null, loading: true, error: "" };
    const action = actionState.key === viewKey ? actionState : EMPTY_ACTION;
    const request = current.request;
    const isCurrent = key => mounted.current && activeKey.current === key;

    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; loadSequence.current += 1; };
    }, []);

    const load = useCallback(async ({ signal } = {}) => {
        if (!owner || !requestId || activeKey.current !== viewKey) return null;
        const sequence = ++loadSequence.current;
        setState(previous => ({ key: viewKey, request: previous.key === viewKey ? previous.request : null, loading: true, error: "" }));
        try {
            const { data } = await api.get(`${API}/${encodeURIComponent(requestId)}`, { signal, timeout: 15_000 });
            const row = readRequest(data, requestId);
            if (!mounted.current || activeKey.current !== viewKey || loadSequence.current !== sequence) return null;
            setState({ key: viewKey, request: row, loading: false, error: "" });
            setActionState(previous => {
                if (previous.key !== viewKey) return previous;
                if (row.status !== "pending") return { key: viewKey, ...EMPTY_ACTION };
                return { ...previous, blocked: false, error: previous.stale || previous.attempted ? previous.error : "" };
            });
            return row;
        } catch (error) {
            if (!mounted.current || activeKey.current !== viewKey || loadSequence.current !== sequence || signal?.aborted) return null;
            setState(previous => ({ ...previous, key: viewKey, loading: false,
                error: errorMessage(error, "تعذر تحميل حالة الطلب. حاول تحديثها مرة أخرى.") }));
            return null;
        }
    }, [owner, requestId, viewKey]);

    useEffect(() => {
        setActionState({ key: viewKey, ...EMPTY_ACTION });
        const controller = new AbortController();
        load({ signal: controller.signal });
        return () => { controller.abort(); loadSequence.current += 1; };
    }, [load, viewKey]);

    const canDecide = owner && request?.id === requestId && request?.status === "pending"
        && !current.loading && !current.error && !action.busy && !action.blocked;

    const refresh = async () => {
        const row = await load();
        if (row && row.status !== "pending" && isCurrent(viewKey)) confirmedDecision();
    };

    const decide = async decision => {
        if (!canDecide || mutation.current?.key === viewKey || (decision === "approve" && action.stale)
            || (action.attempted && action.attempted !== decision)) return;
        const attempt = { key: viewKey };
        mutation.current = attempt;
        setActionState({ key: viewKey, ...action, busy: true, error: "" });
        try {
            // The backend makes the immutable request idempotent. Do not let
            // the generic auth interceptor replay this owner decision.
            const { data } = await api.post(`${API}/${encodeURIComponent(requestId)}/decision`, { decision }, {
                timeout: 20_000, _mezanAuthRetried: true,
            });
            const row = readRequest(data, requestId);
            if (row.status !== (decision === "approve" ? "approved" : "rejected")) throw new Error("لم يصل تأكيد القرار.");
            if (!isCurrent(viewKey)) return;
            setState({ key: viewKey, request: row, loading: false, error: "" });
            setActionState({ key: viewKey, ...EMPTY_ACTION });
            confirmedDecision();
        } catch (error) {
            if (!isCurrent(viewKey)) return;
            const code = error.response?.data?.detail?.code;
            const status = error.response?.status;
            if (code === "price_change_stale") {
                setActionState({ key: viewKey, ...EMPTY_ACTION, stale: true,
                    error: "تغيّر السعر المعتمد بعد إنشاء هذا الطلب. لا يمكن اعتماد السعر المقترح من هذا الطلب؛ راجع السعر الحالي أو ارفض الطلب." });
            } else if (status >= 400 && status < 500 && code !== "price_change_already_decided") {
                setActionState({ key: viewKey, ...EMPTY_ACTION, blocked: status === 401 || status === 403 || status === 404,
                    error: errorMessage(error, "تعذر حفظ القرار. أعد تحميل الطلب قبل المحاولة.") });
            } else {
                // A lost response is not a failed decision. Read back once;
                // only an explicit retry may resend that same decision.
                setActionState({ key: viewKey, ...EMPTY_ACTION, busy: true, attempted: decision,
                    error: "لم يصل تأكيد القرار؛ جارٍ التحقق من حالته." });
                const row = await load();
                if (!isCurrent(viewKey)) return;
                if (row && row.status !== "pending") {
                    setActionState({ key: viewKey, ...EMPTY_ACTION });
                    confirmedDecision();
                } else {
                    setActionState({ key: viewKey, ...EMPTY_ACTION, attempted: decision,
                        error: row ? "الطلب ما زال بانتظار تأكيد القرار. يمكنك إعادة إرسال القرار نفسه." : "تعذر التأكد من حفظ القرار. حدّث حالة الطلب قبل أي محاولة أخرى." });
                }
            }
        } finally {
            if (mutation.current === attempt) mutation.current = null;
            if (isCurrent(viewKey)) setActionState(previous => previous.key === viewKey ? { ...previous, busy: false } : previous);
        }
    };

    if (!owner) {
        return <div dir="rtl" role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-6 text-amber-950">
            {authLoading ? "جاري التحقق من الصلاحية…" : "مراجعة تغيير الأسعار متاحة لمالك المتجر فقط."}
        </div>;
    }

    const service = request?.change_type === "service_price";
    const targetName = service ? request.service_name || request.service_id || "الخدمة" : request?.product_name || request?.product_id || "المنتج";
    const status = STATUS[request?.status];
    const optionQuantity = request?.after_quantity != null || request?.before_quantity != null;

    return <section dir="rtl" className="mx-auto max-w-3xl space-y-5" data-testid="price-change-request-page">
        <header className="flex flex-wrap items-center justify-between gap-3">
            <h1 className="text-2xl font-extrabold text-slate-900">طلب تغيير {service ? "سعر الخدمة" : "سعر المنتج"}</h1>
            <Link to="/alerts" className="text-sm font-bold text-indigo-700 hover:underline">العودة للتنبيهات</Link>
        </header>
        {current.loading && <p role="status" className="text-sm text-slate-600">جاري تحميل حالة الطلب…</p>}
        {current.error && <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">{current.error}</div>}
        {request && <article className="space-y-5 rounded-2xl border border-slate-200 bg-white p-5 sm:p-6">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-xl font-extrabold text-slate-900 break-words">{targetName}</h2>
                    {service && request.product_name && <p className="mt-1 text-sm text-slate-600">المنتج: {request.product_name}</p>}
                    {(request.option_name || request.value_name) && <p className="mt-1 text-sm text-slate-600">الخيار: {[request.option_name, request.value_name].filter(Boolean).join(" — ")}</p>}
                    {request.variant_id && !request.option_name && <p className="mt-1 text-xs text-slate-500">رمز الخيار: <bdi>{request.variant_id}</bdi></p>}
                </div>
                <span className={`rounded-full border px-3 py-1 text-xs font-bold ${status.style}`} role="status" data-testid="price-change-request-status">
                    {request.removes_override && request.status === "approved" ? "تم اعتماد إلغاء السعر المسجل" : status.label}
                </span>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                    <p className="text-xs text-slate-600">{optionQuantity ? "سعر الوحدة المعتمد عند تقديم الطلب" : "السعر المعتمد عند تقديم الطلب"}</p>
                    <p className="mt-2 text-xl font-extrabold text-slate-800" data-testid="price-change-before"><bdi>{request.before_halalas === null ? "غير محدد" : money(request.before_halalas)}</bdi></p>
                    {request.before_quantity != null && <p className="mt-2 text-sm text-slate-700" data-testid="price-change-before-quantity">الكمية المرتبطة بالخيار: <bdi>{request.before_quantity}</bdi></p>}
                </div>
                <div className="rounded-xl border border-indigo-200 bg-indigo-50 p-4">
                    <p className="text-xs text-indigo-800">{request.removes_override ? "التغيير المقترح" : optionQuantity ? "سعر الوحدة المقترح" : "السعر المقترح"}</p>
                    <p className="mt-2 text-xl font-extrabold text-indigo-950" data-testid="price-change-after"><bdi>{request.removes_override ? "إلغاء السعر المسجل" : money(request.after_halalas)}</bdi></p>
                    {request.after_quantity != null && <p className="mt-2 text-sm text-indigo-900" data-testid="price-change-after-quantity">الكمية المقترحة للخيار: <bdi>{request.after_quantity}</bdi></p>}
                </div>
            </div>
            <dl className="grid gap-3 text-sm sm:grid-cols-2">
                <div><dt className="text-slate-500">طلبه الموظف</dt><dd className="mt-1 font-bold text-slate-900">{request.requested_by_name || "غير متوفر"}</dd></div>
                <div><dt className="text-slate-500">وقت الطلب — الرياض</dt><dd className="mt-1 text-slate-800">{dateTime(request.created_at)}</dd></div>
                {request.invoice_id && <div><dt className="text-slate-500">فاتورة المورد</dt><dd className="mt-1 break-all text-slate-800"><bdi>{request.invoice_id}</bdi></dd></div>}
                {request.decided_at && <div><dt className="text-slate-500">وقت القرار — الرياض</dt><dd className="mt-1 text-slate-800">{dateTime(request.decided_at)}</dd></div>}
            </dl>
            <p className="text-sm leading-relaxed text-slate-600">
                {request.removes_override ? "الموافقة تلغي السعر المسجل" : "الموافقة تحفظ السعر الجديد"} في بيانات {service ? "الخدمة" : "المنتج"}.
                {request.invoice_id && " سعر فاتورة المورد يبقى كما سجّله الموظف."}
            </p>
            {action.error && <p role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm leading-relaxed text-amber-950">{action.error}</p>}
            {request.status === "pending" && <div className="space-y-3 border-t border-slate-100 pt-5">
                <p className="font-bold text-slate-900">{request.removes_override
                    ? <>هل توافق على إلغاء السعر المسجل لـ «{targetName}»؟</>
                    : <>هل توافق على تغيير سعر «{targetName}» إلى <bdi>{money(request.after_halalas)}</bdi>{request.after_quantity != null && <> لكل وحدة، بكمية <bdi>{request.after_quantity}</bdi> لهذا الخيار</>}؟</>}</p>
                <div className="flex flex-wrap gap-3">
                    <button type="button" onClick={() => decide("approve")}
                        disabled={!canDecide || action.stale || action.attempted === "reject"}
                        className="rounded-lg bg-indigo-700 px-5 py-2.5 text-sm font-bold text-white hover:bg-indigo-800 disabled:cursor-not-allowed disabled:opacity-50"
                        data-testid="price-change-approve">{action.attempted === "approve" ? "إعادة إرسال الموافقة" : request.removes_override ? "موافق، اعتماد التغيير" : "موافق، اعتماد السعر"}</button>
                    <button type="button" onClick={() => decide("reject")}
                        disabled={!canDecide || action.attempted === "approve"}
                        className="rounded-lg border border-slate-300 px-5 py-2.5 text-sm font-bold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                        data-testid="price-change-reject">{action.attempted === "reject" ? "إعادة إرسال الرفض" : "رفض التغيير"}</button>
                </div>
                {action.busy && <p role="status" className="text-sm text-slate-600">جاري حفظ القرار والتحقق منه…</p>}
            </div>}
        </article>}
        <button type="button" onClick={refresh} disabled={current.loading || action.busy}
            className="text-sm font-bold text-indigo-700 hover:underline disabled:opacity-50" data-testid="price-change-refresh">
            تحديث حالة الطلب
        </button>
    </section>;
}
