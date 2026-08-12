import { useRef, useState } from "react";
import {
    ArrowRight,
    ChartLineUp,
    ChatCircleDots,
    CheckCircle,
    ClipboardText,
    Clock,
    Eye,
    ImageSquare,
    LockKey,
    Megaphone,
    Package,
    Plug,
    ShieldCheck,
    ShoppingCart,
    UsersThree,
    WarningCircle,
} from "@phosphor-icons/react";

import { formatRiyadhDateTime } from "../../lib/tzUtils";

import {
    Confidence,
    EmptyState,
    MetricCard,
    Panel,
    PreviewModeBanner,
    SafetyChecklist,
    StatusPill,
    WriteLockBanner,
} from "./CustomerIntelligencePrimitives";

function money(value, currency = "SAR") {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "—";
    const formatted = new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(numeric);
    return currency === "SAR" ? `${formatted} ر.س` : `${formatted} ${currency}`;
}

function percent(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "—";
    return `${Math.round(numeric * 100)}%`;
}

function liveMessageLabel(message) {
    if (message.direction === "outbound" && message.sender === "employee") {
        return "رد الموظف من واتساب";
    }
    const labels = {
        text: "رسالة نصية واردة",
        image: "صورة واردة",
        audio: "رسالة صوتية واردة",
        document: "مستند وارد",
        interactive: "تفاعل وارد",
    };
    return labels[message.kind] || "رسالة واردة";
}

function liveMessageDeliveryLabel(message) {
    if (message.direction !== "outbound") return "مستلمة";
    const labels = {
        sent: "أُرسل",
        delivered: "تم التسليم",
        read: "تمت القراءة",
        failed: "تعذر الإرسال",
    };
    return labels[message.delivery_state] || "رد موظف";
}

function liveMessageBody(message) {
    if (!message.content_available) return "تعذر عرض محتوى الرسالة المشفّر.";
    if (message.body) return message.body;
    if (message.caption) return message.caption;
    if (message.kind === "document" && message.filename) return message.filename;
    const placeholders = {
        image: "تم حفظ مرجع الصورة بأمان، والمعاينة غير متاحة في هذه المرحلة.",
        audio: "تم حفظ مرجع الرسالة الصوتية بأمان، والتشغيل غير متاح في هذه المرحلة.",
        document: "تم حفظ مرجع المستند بأمان، والتنزيل غير متاح في هذه المرحلة.",
        interactive: "تم استلام تفاعل من واتساب.",
        text: "لا يوجد نص قابل للعرض.",
    };
    return placeholders[message.kind] || "لا يوجد محتوى قابل للعرض.";
}

function LiveInboxMessage({ message }) {
    const isMedia = ["image", "audio", "document"].includes(message.kind);
    const employeeEcho = message.direction === "outbound" && message.sender === "employee";
    return (
        <article
            className={`max-w-[88%] rounded-xl border p-4 sm:max-w-3xl ${
                employeeEcho
                    ? "mr-auto border-violet-200 bg-violet-50"
                    : "ml-auto border-emerald-200 bg-emerald-50"
            }`}
            data-testid="customer-intelligence-live-message"
            data-message-kind={message.kind}
            data-message-direction={message.direction}
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className={`flex items-center gap-2 text-xs font-extrabold ${
                    employeeEcho ? "text-violet-800" : "text-emerald-800"
                }`}>
                    {message.kind === "image"
                        ? <ImageSquare size={18} weight="duotone" />
                        : <ChatCircleDots size={18} weight="duotone" />}
                    {liveMessageLabel(message)}
                </div>
                <StatusPill
                    status={message.delivery_state === "failed" ? "blocked" : "open"}
                    label={liveMessageDeliveryLabel(message)}
                />
            </div>
            <p
                className={`mt-3 whitespace-pre-wrap break-words text-sm leading-7 ${
                    employeeEcho ? "text-violet-950" : "text-emerald-950"
                }`}
                dir="auto"
            >
                {liveMessageBody(message)}
            </p>
            {isMedia && message.mime_type && (
                <div className={`mt-2 font-mono text-[11px] ${
                    employeeEcho ? "text-violet-700" : "text-emerald-700"
                }`} dir="ltr">
                    {message.mime_type}
                </div>
            )}
            <time
                className={`num mt-3 block text-[11px] font-bold ${
                    employeeEcho ? "text-violet-700" : "text-emerald-700"
                }`}
                dateTime={message.occurred_at || undefined}
            >
                {formatRiyadhDateTime(message.occurred_at)}
            </time>
        </article>
    );
}

function PendingReplySuggestion({
    suggestion,
    onReview = null,
    onReject = null,
    onEscalate = null,
}) {
    const [draft, setDraft] = useState(suggestion.text);
    const [busyAction, setBusyAction] = useState("");
    const [feedback, setFeedback] = useState("");
    const actionLock = useRef(false);

    const runAction = async (action, callback) => {
        if (typeof callback !== "function" || actionLock.current) return;
        actionLock.current = true;
        setBusyAction(action);
        setFeedback("");
        try {
            await callback(suggestion.conversation_id, suggestion.id, {
                text: draft.trim(),
                version: suggestion.version,
            });
            setFeedback(action === "review"
                ? "تم حفظ مراجعة الموظف دون إرسال الرسالة."
                : action === "reject"
                    ? "تم رفض الاقتراح دون إرسال الرسالة."
                    : "تم تصعيد المحادثة لمراجعة بشرية دون إرسال الرسالة.");
        } catch (_error) {
            setFeedback("تعذر حفظ الإجراء. لم تُرسل أي رسالة إلى واتساب.");
        } finally {
            actionLock.current = false;
            setBusyAction("");
        }
    };

    return (
        <section
            className="mt-5 rounded-xl border-2 border-violet-200 bg-violet-50 p-4"
            data-testid="customer-intelligence-pending-reply-suggestion"
            data-suggestion-status="pending_approval"
        >
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="font-black text-violet-950">اقتراح رد من ذكاء ميزان</div>
                    <p className="mt-1 text-xs font-bold leading-5 text-violet-800">
                        يحتاج اعتماد موظف. راجع النص وعدّله قبل أي اعتماد مستقبلي.
                    </p>
                </div>
                <StatusPill status="needs_review" label="بانتظار اعتماد الموظف" />
            </div>

            <label
                className="mt-4 block text-xs font-extrabold text-slate-700"
                htmlFor={`reply-suggestion-${suggestion.id}`}
            >
                نص الرد المقترح
            </label>
            <textarea
                id={`reply-suggestion-${suggestion.id}`}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                rows={5}
                className="mt-2 w-full resize-y rounded-xl border border-violet-200 bg-white p-3 text-sm leading-7 text-slate-950 outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-100"
                dir="auto"
                data-testid="customer-intelligence-reply-suggestion-editor"
            />
            <p className="mt-2 text-[11px] font-bold leading-5 text-slate-500">
                تعديل النص لا يرسله، وضغط Enter يضيف سطرًا فقط.
            </p>

            <div className="mt-4 grid gap-2 sm:grid-cols-3">
                <button
                    type="button"
                    onClick={() => runAction("review", onReview)}
                    disabled={busyAction !== "" || !draft.trim() || typeof onReview !== "function"}
                    className="min-h-11 rounded-lg border border-emerald-300 bg-white px-3 text-sm font-extrabold text-emerald-800 transition hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50"
                    data-testid="customer-intelligence-review-suggestion"
                >
                    {busyAction === "review" ? "جارٍ الحفظ…" : "حفظ المراجعة"}
                </button>
                <button
                    type="button"
                    onClick={() => runAction("reject", onReject)}
                    disabled={busyAction !== "" || typeof onReject !== "function"}
                    className="min-h-11 rounded-lg border border-rose-300 bg-white px-3 text-sm font-extrabold text-rose-800 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50"
                    data-testid="customer-intelligence-reject-suggestion"
                >
                    {busyAction === "reject" ? "جارٍ الرفض…" : "رفض الاقتراح"}
                </button>
                <button
                    type="button"
                    onClick={() => runAction("escalate", onEscalate)}
                    disabled={busyAction !== "" || typeof onEscalate !== "function"}
                    className="min-h-11 rounded-lg border border-amber-300 bg-white px-3 text-sm font-extrabold text-amber-900 transition hover:bg-amber-50 disabled:cursor-not-allowed disabled:opacity-50"
                    data-testid="customer-intelligence-escalate-suggestion"
                >
                    {busyAction === "escalate" ? "جارٍ التصعيد…" : "تصعيد لموظف"}
                </button>
            </div>

            <button
                type="button"
                disabled
                aria-disabled="true"
                title="الإرسال مقفل حاليًا في سياسة الأمان"
                className="mt-3 inline-flex min-h-11 w-full cursor-not-allowed items-center justify-center gap-2 rounded-lg bg-slate-300 px-4 text-sm font-black text-slate-600 opacity-80"
                data-testid="customer-intelligence-approve-and-send"
            >
                <LockKey size={18} weight="duotone" />
                اعتماد وإرسال — الإرسال مقفل حاليًا
            </button>
            {feedback && (
                <p
                    className="mt-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold leading-5 text-slate-700"
                    role="status"
                    data-testid="customer-intelligence-suggestion-feedback"
                >
                    {feedback}
                </p>
            )}
        </section>
    );
}

function CreateReplySuggestion({ conversationId, onCreate = null }) {
    const [creating, setCreating] = useState(false);
    const [error, setError] = useState("");
    const createLock = useRef(false);

    const create = async () => {
        if (createLock.current || typeof onCreate !== "function") return;
        createLock.current = true;
        setCreating(true);
        setError("");
        try {
            await onCreate(conversationId);
        } catch (_requestError) {
            setError("تعذر إنشاء الاقتراح. لم تُرسل أي رسالة إلى واتساب.");
        } finally {
            createLock.current = false;
            setCreating(false);
        }
    };

    return (
        <section
            className="mt-5 rounded-xl border border-violet-200 bg-violet-50 p-4"
            data-testid="customer-intelligence-create-suggestion-panel"
        >
            <div className="font-black text-violet-950">لا يوجد اقتراح جاهز للمراجعة</div>
            <p className="mt-1 text-xs font-bold leading-5 text-violet-800">
                يمكنك طلب اقتراح من الذكاء لهذه المحادثة. لن يؤدي ذلك إلى إرسال أي رد للعميل.
            </p>
            <button
                type="button"
                onClick={create}
                disabled={creating || typeof onCreate !== "function"}
                className="mt-3 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-violet-700 px-4 text-sm font-black text-white transition hover:bg-violet-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-600"
                data-testid="customer-intelligence-create-suggestion"
            >
                <ChatCircleDots size={18} weight="duotone" />
                {creating ? "جارٍ إنشاء الاقتراح…" : "إنشاء اقتراح بالذكاء"}
            </button>
            {error && (
                <p
                    className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold leading-5 text-rose-900"
                    role="alert"
                    data-testid="customer-intelligence-create-suggestion-error"
                >
                    {error}
                </p>
            )}
        </section>
    );
}

export function OverviewPanel({ model, writesLocked, policyKeys }) {
    const metrics = model.overview?.metrics || [];
    const alerts = model.overview?.alerts || [];
    return (
        <div className="space-y-5" data-testid="customer-intelligence-panel-overview">
            <div className="grid gap-3 xl:grid-cols-2">
                <PreviewModeBanner />
                <WriteLockBanner locked={writesLocked} />
            </div>

            <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5" aria-label="مؤشرات تجريبية">
                {metrics.map((metric) => <MetricCard key={metric.key || metric.label} metric={metric} />)}
            </section>

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,.8fr)]">
                <Panel
                    title="ما الذي يراه ذكاء ميزان؟"
                    subtitle="تحويل المحادثة إلى نية واعتراض وفرصة وخطوة تالية قابلة للمراجعة."
                    Icon={Eye}
                    testid="customer-intelligence-overview-signals"
                >
                    <div className="grid gap-3 sm:grid-cols-2">
                        {[
                            ["النية", "اختيار هدية ثم الشراء بعد الراتب"],
                            ["الاعتراض", "السعر وموعد التوصيل"],
                            ["تفضيل مستنتج", "اللون الكحلي — بدرجة ثقة"],
                            ["الخطوة التالية", "تحقق ثم مراجعة متابعة واحدة"],
                        ].map(([label, value]) => (
                            <div key={label} className="rounded-xl border border-slate-100 bg-slate-50 p-3">
                                <div className="text-[11px] font-extrabold text-slate-500">{label}</div>
                                <div className="mt-1 text-sm font-bold leading-6 text-slate-900">{value}</div>
                            </div>
                        ))}
                    </div>
                </Panel>

                <Panel
                    title="مستوى التشغيل الحالي"
                    subtitle="يُرفع لكل قدرة بشكل مستقل بعد ثبوت الجودة والأمان."
                    Icon={ShieldCheck}
                    testid="customer-intelligence-operating-level"
                >
                    <div className="rounded-xl border border-violet-200 bg-violet-50 p-4">
                        <div className="text-xs font-extrabold text-violet-700">المستوى</div>
                        <div className="mt-1 text-2xl font-black text-violet-950">
                            {model.workspace?.operating_level ?? 1} · {model.workspace?.operating_level_label || "اقتراح فقط"}
                        </div>
                    </div>
                    <p className="mt-3 text-xs font-semibold leading-6 text-slate-600">
                        الذكاء يقرأ ويصنف ويقترح، والموظف يراجع. لا توجد حاليًا خطوة تنفيذ
                        تلقائي أو يدوي من هذه الصفحة.
                    </p>
                </Panel>
            </div>

            <Panel
                title="حواجز الأمان"
                subtitle="القيم التالية يجب أن تبقى false بالكامل في وضع المعاينة."
                Icon={LockKey}
                testid="customer-intelligence-safety-policy"
            >
                <SafetyChecklist policy={model.safety_policy} keys={policyKeys} />
            </Panel>

            <div className="grid gap-3 lg:grid-cols-2">
                {alerts.map((alert) => (
                    <div
                        key={alert.id || alert.title}
                        className={`flex items-start gap-3 rounded-xl border p-4 ${
                            alert.severity === "safe"
                                ? "border-emerald-200 bg-emerald-50 text-emerald-950"
                                : "border-blue-200 bg-blue-50 text-blue-950"
                        }`}
                    >
                        {alert.severity === "safe"
                            ? <CheckCircle size={21} weight="duotone" className="mt-0.5 shrink-0" />
                            : <Eye size={21} weight="duotone" className="mt-0.5 shrink-0" />}
                        <div>
                            <div className="font-extrabold">{alert.title}</div>
                            <p className="mt-1 text-xs leading-5 opacity-80">{alert.detail}</p>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

export function ConversationsPanel({
    inbox,
    error = "",
    onCreateSuggestion = null,
    onReviewSuggestion = null,
    onRejectSuggestion = null,
    onEscalateSuggestion = null,
}) {
    const conversations = inbox?.conversations || [];
    const [selectedId, setSelectedId] = useState("");
    const explicitlySelected = conversations.some((row) => row.id === selectedId);
    const selectedConversation = conversations.find((row) => row.id === selectedId)
        || conversations[0]
        || null;
    const connected = inbox?.connection?.status === "connected";

    // The parent renders the single canonical inbox error banner. Avoid a
    // second empty/error state here for the same failed request.
    if (error) return null;

    return (
        <div className="space-y-5" data-testid="customer-intelligence-panel-conversations" data-live-inbox="true">
            <section
                className={`rounded-xl border p-4 ${
                    connected
                        ? "border-emerald-200 bg-emerald-50 text-emerald-950"
                        : "border-amber-200 bg-amber-50 text-amber-950"
                }`}
                data-testid="customer-intelligence-live-connection"
            >
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="flex items-start gap-3">
                        {connected
                            ? <CheckCircle size={24} weight="duotone" className="mt-0.5 shrink-0 text-emerald-700" />
                            : <WarningCircle size={24} weight="duotone" className="mt-0.5 shrink-0 text-amber-700" />}
                        <div>
                            <div className="font-extrabold">
                                {connected ? "واتساب متصل ويستقبل الرسائل" : "واتساب غير متصل للاستقبال"}
                            </div>
                            <p className="mt-1 text-xs leading-5 opacity-80">
                                صندوق وارد حقيقي للقراءة فقط. الإرسال والرد التلقائي وكل إجراءات التجارة مغلقة.
                            </p>
                        </div>
                    </div>
                    <div className="flex flex-wrap gap-2 text-xs font-bold">
                        <span className="rounded-full border border-current/10 bg-white/70 px-3 py-1.5">
                            <span className="num">{inbox?.conversation_count || 0}</span> محادثة
                        </span>
                        <span className="rounded-full border border-current/10 bg-white/70 px-3 py-1.5">
                            <span className="num">{inbox?.message_count || 0}</span> رسالة
                        </span>
                    </div>
                </div>
                {(inbox?.content_unavailable_count || 0) > 0 && (
                    <div
                        className="mt-3 rounded-lg border border-amber-300 bg-amber-100 px-3 py-2 text-xs font-bold leading-5 text-amber-950"
                        data-testid="customer-intelligence-content-unavailable-warning"
                    >
                        تعذر عرض محتوى <span className="num">{inbox.content_unavailable_count}</span> رسالة
                        محفوظة. الربط ما زال يستقبل، ويجب مراجعة إعداد تشفير العملاء في Backend.
                    </div>
                )}
            </section>

            {!connected ? (
                <EmptyState
                    title="قناة واتساب غير جاهزة للاستقبال"
                    detail="عند اكتمال الربط ستظهر الرسائل الواردة هنا تلقائيًا."
                />
            ) : !conversations.length ? (
                <EmptyState
                    title="لا توجد رسائل واردة حتى الآن"
                    detail="الربط متصل، وستظهر أول محادثة بعد وصول رسالة جديدة."
                />
            ) : (
                <div
                    className="grid gap-5 lg:grid-cols-[minmax(300px,.75fr)_minmax(0,1.25fr)]"
                    data-testid="customer-intelligence-responsive-inbox"
                >
                    <div className={explicitlySelected ? "hidden lg:block" : "block"}>
                        <Panel
                            title="محادثات واتساب"
                            subtitle="الأحدث أولًا · اختر محادثة لعرض الرسائل المحفوظة المتاحة"
                            Icon={UsersThree}
                            testid="customer-intelligence-live-conversation-list"
                        >
                        <div className="space-y-2" role="list" aria-label="محادثات واتساب الواردة">
                            {conversations.map((conversation) => {
                                const active = conversation.id === selectedConversation?.id;
                                return (
                                    <div key={conversation.id} role="listitem">
                                        <button
                                            type="button"
                                            onClick={() => setSelectedId(conversation.id)}
                                            className={`w-full rounded-xl border p-3 text-right transition ${
                                                active
                                                    ? "border-emerald-500 bg-emerald-50 shadow-sm"
                                                    : "border-slate-200 bg-white hover:border-emerald-300 hover:bg-emerald-50/40"
                                            }`}
                                            aria-pressed={active}
                                            data-testid="customer-intelligence-live-conversation"
                                        >
                                            <div className="flex items-start justify-between gap-3">
                                                <div className="min-w-0">
                                                    <div className="truncate font-extrabold text-slate-950">
                                                        {conversation.customer_name || "عميل واتساب"}
                                                    </div>
                                                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-600" dir="auto">
                                                        {conversation.last_message || "لا توجد رسالة قابلة للعرض"}
                                                    </p>
                                                </div>
                                                <StatusPill status={conversation.status} />
                                            </div>
                                            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] font-bold text-slate-500">
                                                <time className="num" dateTime={conversation.last_message_at || undefined}>
                                                    {formatRiyadhDateTime(conversation.last_message_at)}
                                                </time>
                                                <span><span className="num">{conversation.message_count}</span> رسالة</span>
                                            </div>
                                        </button>
                                    </div>
                                );
                            })}
                        </div>
                        {inbox?.has_more && (
                            <p className="mt-3 text-center text-xs font-bold text-slate-500">
                                توجد محادثات أقدم غير معروضة في هذه الصفحة.
                            </p>
                        )}
                        </Panel>
                    </div>

                    <div className={explicitlySelected ? "block" : "hidden lg:block"}>
                        <button
                            type="button"
                            onClick={() => setSelectedId("")}
                            className="mb-3 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-extrabold text-slate-700 lg:hidden"
                            data-testid="customer-intelligence-back-to-conversations"
                        >
                            <ArrowRight size={18} weight="bold" />
                            رجوع إلى المحادثات
                        </button>
                        <Panel
                            title={selectedConversation?.customer_name || "محادثة واتساب"}
                            subtitle="رسائل العميل وردود الموظف المحفوظة في ميزان · بتوقيت الرياض"
                            Icon={ChatCircleDots}
                            testid="customer-intelligence-live-message-stream"
                            actions={<StatusPill status={selectedConversation?.status} />}
                        >
                        {selectedConversation?.messages?.length ? (
                            <>
                                {selectedConversation.message_count > selectedConversation.messages.length && (
                                    <p
                                        className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-900"
                                        data-testid="customer-intelligence-message-window-notice"
                                    >
                                        يعرض أحدث <span className="num">{selectedConversation.messages.length}</span> من <span className="num">{selectedConversation.message_count}</span> رسالة.
                                    </p>
                                )}
                                <div className="space-y-3" aria-label="سجل رسائل العميل وردود الموظف">
                                    {selectedConversation.messages.map((message) => (
                                        <LiveInboxMessage key={message.id} message={message} />
                                    ))}
                                </div>
                            </>
                        ) : (
                            <EmptyState title="لا توجد رسالة قابلة للعرض في هذه المحادثة" />
                        )}
                            {selectedConversation?.reply_suggestion && (
                                <PendingReplySuggestion
                                    key={selectedConversation.reply_suggestion.id}
                                    suggestion={selectedConversation.reply_suggestion}
                                    onReview={onReviewSuggestion}
                                    onReject={onRejectSuggestion}
                                    onEscalate={onEscalateSuggestion}
                                />
                            )}
                            {!selectedConversation?.reply_suggestion && (
                                <CreateReplySuggestion
                                    conversationId={selectedConversation?.id}
                                    onCreate={onCreateSuggestion}
                                />
                            )}
                            <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs font-bold leading-6 text-slate-600">
                                لا يتم إرسال أي رد عند فتح الشاشة أو تعديل النص أو ضغط Enter.
                            </div>
                        </Panel>
                    </div>
                </div>
            )}
        </div>
    );
}

export function CustomersPanel({ model }) {
    const profile = model.customer_profile || {};
    return (
        <div className="grid gap-5 lg:grid-cols-[minmax(280px,.75fr)_minmax(0,1.25fr)]" data-testid="customer-intelligence-panel-customers">
            <Panel
                title={profile.display_name || "عميل تجريبي"}
                subtitle="ملف مصطنع لا يحتوي رقم هاتف أو بريدًا حقيقيًا."
                Icon={UsersThree}
                testid="customer-intelligence-customer-card"
            >
                <div className="flex flex-wrap gap-2">
                    {(profile.labels || []).map((label) => (
                        <span key={label} className="rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-xs font-bold text-violet-800">
                            {label}
                        </span>
                    ))}
                </div>
                <dl className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
                    {[
                        ["مرحلة العميل", profile.lifecycle],
                        ["حالة التواصل", profile.consent_status],
                        ["الميزانية", profile.inferred_budget],
                        ["آخر طلب", profile.last_order_status],
                    ].map(([label, value]) => (
                        <div key={label} className="rounded-lg bg-slate-50 p-3">
                            <dt className="text-[11px] font-bold text-slate-500">{label}</dt>
                            <dd className="mt-1 text-sm font-extrabold text-slate-900">{value || "—"}</dd>
                        </div>
                    ))}
                </dl>
                <div className="mt-4">
                    <Confidence value={profile.purchase_probability} label="احتمال شراء تجريبي" />
                </div>
            </Panel>

            <div className="space-y-5">
                <Panel
                    title="تفضيلات واستنتاجات"
                    subtitle="كل استنتاج يحمل دليلًا ودرجة ثقة، ولا يتحول تلقائيًا إلى حقيقة."
                    Icon={Eye}
                    testid="customer-intelligence-customer-preferences"
                >
                    <div className="grid gap-4 sm:grid-cols-2">
                        <div>
                            <div className="mb-2 text-xs font-extrabold text-slate-500">منتجات مفضلة</div>
                            {(profile.preferred_products || []).map((item) => (
                                <div key={item} className="mb-2 rounded-lg border border-slate-200 p-3 text-sm font-bold">{item}</div>
                            ))}
                        </div>
                        <div>
                            <div className="mb-2 text-xs font-extrabold text-slate-500">مواصفات مفضلة</div>
                            {(profile.preferred_attributes || []).map((item) => (
                                <div key={item} className="mb-2 rounded-lg border border-slate-200 p-3 text-sm font-bold">{item}</div>
                            ))}
                        </div>
                    </div>
                </Panel>

                <Panel
                    title="الأدلة والخطوة التالية"
                    subtitle="لا تُعرض بيانات اتصال حساسة في بطاقة العميل."
                    Icon={ClipboardText}
                    testid="customer-intelligence-customer-evidence"
                >
                    <ul className="space-y-2">
                        {(profile.evidence || []).map((item) => (
                            <li key={item} className="flex items-start gap-2 rounded-lg bg-slate-50 p-3 text-sm leading-6">
                                <CheckCircle size={18} className="mt-1 shrink-0 text-emerald-600" />
                                {item}
                            </li>
                        ))}
                    </ul>
                    <div className="mt-4 rounded-lg border border-violet-200 bg-violet-50 p-3 text-sm font-bold leading-6 text-violet-950">
                        {profile.next_best_action || "لا توجد خطوة مقترحة."}
                    </div>
                </Panel>
            </div>
        </div>
    );
}

export function FollowUpsPanel({ model }) {
    const rows = model.follow_ups || [];
    return (
        <Panel
            title="المتابعات الذكية"
            subtitle="كل متابعة هنا اقتراح غير مجدول، ولا تُرسل دون موافقة وسياسة تواصل."
            Icon={Clock}
            testid="customer-intelligence-panel-followups"
        >
            {!rows.length ? <EmptyState title="لا توجد متابعات مقترحة" /> : (
                <div className="grid gap-4 lg:grid-cols-2">
                    {rows.map((row) => (
                        <article key={row.id} className="rounded-xl border border-slate-200 p-4">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div className="font-black text-slate-950">{row.customer_name}</div>
                                <StatusPill status={row.status} />
                            </div>
                            <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                                <div className="rounded-lg bg-slate-50 p-3">
                                    <div className="font-bold text-slate-500">الموعد المقترح</div>
                                    <div className="mt-1 font-extrabold text-slate-900">{row.due_label}</div>
                                </div>
                                <div className="rounded-lg bg-slate-50 p-3">
                                    <div className="font-bold text-slate-500">الحد الأقصى</div>
                                    <div className="mt-1 font-extrabold text-slate-900">{row.attempts_allowed ?? 0} محاولة</div>
                                </div>
                            </div>
                            <p className="mt-3 text-sm leading-6 text-slate-700">{row.reason}</p>
                            <div className="mt-3 rounded-lg border border-dashed border-violet-200 bg-violet-50 p-3">
                                <div className="text-[11px] font-extrabold text-violet-700">نص مقترح غير قابل للإرسال</div>
                                <p className="mt-1 text-sm leading-6 text-violet-950">{row.proposed_message}</p>
                            </div>
                        </article>
                    ))}
                </div>
            )}
        </Panel>
    );
}

export function SalesOpportunitiesPanel({ model }) {
    const rows = model.sales_opportunities || [];
    return (
        <Panel
            title="فرص البيع"
            subtitle="ترتيب أولويات للموظف؛ لا يطبق عروضًا ولا ينفذ طلبات."
            Icon={ChartLineUp}
            testid="customer-intelligence-panel-sales-opportunities"
        >
            {!rows.length ? <EmptyState title="لا توجد فرص بيع" /> : (
                <div className="grid gap-4 lg:grid-cols-2">
                    {rows.map((row) => (
                        <article key={row.id} className="rounded-xl border border-slate-200 p-4">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                    <div className="font-black">{row.product}</div>
                                    <div className="mt-1 text-xs text-slate-500">{row.customer_name}</div>
                                </div>
                                <StatusPill status={row.stage} />
                            </div>
                            <div className="mt-4 grid grid-cols-2 gap-3">
                                <div className="rounded-lg bg-emerald-50 p-3 text-emerald-900">
                                    <div className="text-[11px] font-bold">قيمة تقديرية</div>
                                    <div className="mt-1 font-mono text-lg font-black">{money(row.estimated_value_sar)}</div>
                                </div>
                                <div className="rounded-lg bg-violet-50 p-3 text-violet-900">
                                    <div className="text-[11px] font-bold">احتمال تجريبي</div>
                                    <div className="mt-1 font-mono text-lg font-black">{percent(row.probability)}</div>
                                </div>
                            </div>
                            <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm">
                                <span className="font-extrabold">العائق: </span>{row.blocker}
                            </div>
                            <div className="mt-2 rounded-lg border border-violet-200 p-3 text-sm">
                                <span className="font-extrabold">الخطوة المقترحة: </span>{row.next_step}
                            </div>
                        </article>
                    ))}
                </div>
            )}
        </Panel>
    );
}

export function OrderDraftsPanel({ model, writesLocked }) {
    const cart = model.conversation_cart || {};
    const items = cart.items || [];
    const validations = Object.entries(cart.validation || {});
    const offers = model.approved_offers || [];
    return (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(300px,.75fr)]" data-testid="customer-intelligence-panel-order-drafts">
            <Panel
                title="سلة المحادثة"
                subtitle="تمثيل واجهة لمسودة فقط؛ لم تُقرأ الأسعار أو المخزون من مصدر رسمي."
                Icon={ShoppingCart}
                testid="customer-intelligence-conversation-cart"
                actions={<StatusPill status={cart.status} />}
            >
                <div className="overflow-x-auto rounded-xl border border-slate-200">
                    <table className="mezan-table min-w-[620px] text-sm">
                        <thead>
                            <tr>
                                <th>المنتج</th>
                                <th>المواصفات</th>
                                <th>الكمية</th>
                                <th>السعر التجريبي</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((item) => (
                                <tr key={item.id}>
                                    <td className="font-bold">{item.product_name}</td>
                                    <td>{item.variant}</td>
                                    <td className="num">{item.quantity}</td>
                                    <td className="num">{money(item.unit_price_sar, cart.currency)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>

                <div className="mt-4 grid gap-2 sm:grid-cols-3">
                    <div className="rounded-lg bg-slate-50 p-3">
                        <div className="text-[11px] text-slate-500">المجموع</div>
                        <div className="mt-1 font-mono font-black">{money(cart.subtotal_sar, cart.currency)}</div>
                    </div>
                    <div className="rounded-lg bg-slate-50 p-3">
                        <div className="text-[11px] text-slate-500">الشحن</div>
                        <div className="mt-1 font-mono font-black">{money(cart.shipping_sar, cart.currency)}</div>
                    </div>
                    <div className="rounded-lg bg-violet-50 p-3 text-violet-950">
                        <div className="text-[11px]">الإجمالي التجريبي</div>
                        <div className="mt-1 font-mono text-lg font-black">{money(cart.total_sar, cart.currency)}</div>
                    </div>
                </div>

                <div className="mt-4 grid gap-2 sm:grid-cols-2">
                    {validations.map(([key, passed]) => (
                        <div key={key} className={`flex items-center gap-2 rounded-lg border p-3 text-xs font-bold ${passed ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-900"}`}>
                            {passed ? <CheckCircle size={17} /> : <WarningCircle size={17} />}
                            <span dir="ltr">{key}</span>
                            <span className="ms-auto">{passed ? "متحقق" : "غير متحقق"}</span>
                        </div>
                    ))}
                </div>

                <div
                    className="mt-5 rounded-xl border-2 border-dashed border-rose-300 bg-rose-50 p-4"
                    data-testid="customer-intelligence-fake-payment-link"
                >
                    <div className="flex items-center gap-2 font-extrabold text-rose-900">
                        <LockKey size={20} weight="duotone" />
                        رابط دفع وهمي وغير قابل للفتح
                    </div>
                    <code className="mt-3 block overflow-x-auto rounded-lg bg-white p-3 text-left text-xs text-rose-700" dir="ltr">
                        {cart.fake_payment_link}
                    </code>
                    <p className="mt-2 text-xs font-bold leading-5 text-rose-800">
                        النطاق .invalid مقصود لمنع الاستخدام. لا يوجد عنصر رابط أو زر دفع.
                    </p>
                </div>
            </Panel>

            <div className="space-y-5">
                <WriteLockBanner locked={writesLocked} />
                <Panel
                    title="عروض معتمدة للمعاينة"
                    subtitle="ليست أكواد خصم ولا تُطبق على أسعار حقيقية."
                    Icon={Megaphone}
                    testid="customer-intelligence-approved-offers"
                >
                    {!offers.length ? <EmptyState title="لا توجد عروض" /> : offers.map((offer) => (
                        <article key={offer.id} className="mb-3 rounded-xl border border-emerald-200 bg-emerald-50 p-4 last:mb-0">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div className="font-extrabold text-emerald-950">{offer.name}</div>
                                <StatusPill status={offer.status} />
                            </div>
                            <p className="mt-2 text-xs leading-5 text-emerald-800">{offer.eligibility}</p>
                            <p className="mt-2 text-xs font-bold leading-5 text-slate-700">{offer.margin_effect}</p>
                        </article>
                    ))}
                </Panel>
            </div>
        </div>
    );
}

export function MarketOpportunitiesPanel({ model }) {
    const products = model.product_opportunities || [];
    const competitors = model.competitor_signals || [];
    return (
        <div className="grid gap-5 xl:grid-cols-2" data-testid="customer-intelligence-panel-market-opportunities">
            <Panel
                title="فرص المنتجات"
                subtitle="إشارات طلب لا تنشئ منتجًا ولا تعدّل المخزون."
                Icon={Package}
                testid="customer-intelligence-product-opportunities"
            >
                {!products.length ? <EmptyState title="لا توجد فرص منتجات" /> : products.map((row) => (
                    <article key={row.id} className="mb-4 rounded-xl border border-violet-200 p-4 last:mb-0">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="font-black">{row.title}</div>
                            <StatusPill status={row.status} />
                        </div>
                        <div className="mt-4 grid grid-cols-3 gap-2 text-center">
                            <div className="rounded-lg bg-violet-50 p-3">
                                <div className="font-mono text-xl font-black text-violet-900">{row.demand_score ?? "—"}</div>
                                <div className="mt-1 text-[10px] text-violet-700">درجة الطلب</div>
                            </div>
                            <div className="rounded-lg bg-slate-50 p-3">
                                <div className="font-mono text-xl font-black">{row.distinct_customers ?? "—"}</div>
                                <div className="mt-1 text-[10px] text-slate-500">عملاء مختلفون</div>
                            </div>
                            <div className="rounded-lg bg-slate-50 p-3">
                                <div className="font-mono text-xl font-black">{row.mentions ?? "—"}</div>
                                <div className="mt-1 text-[10px] text-slate-500">مرات الظهور</div>
                            </div>
                        </div>
                        <p className="mt-3 text-sm leading-6 text-slate-700">{row.reason}</p>
                        <div className="mt-3"><Confidence value={row.confidence} /></div>
                        <div className="mt-3 rounded-lg bg-amber-50 p-3 text-xs font-bold leading-5 text-amber-900">
                            التوصية: {row.recommendation}
                        </div>
                    </article>
                ))}
            </Panel>

            <Panel
                title="إشارات المنافسين"
                subtitle="الذكر المتكرر لا يحوّل المتجر تلقائيًا إلى منافس معتمد."
                Icon={Eye}
                testid="customer-intelligence-competitor-signals"
            >
                {!competitors.length ? <EmptyState title="لا توجد إشارات منافسين" /> : competitors.map((row) => (
                    <article key={row.id} className="mb-4 rounded-xl border border-amber-200 p-4 last:mb-0">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="font-black">{row.display_name}</div>
                            <StatusPill status={row.status} />
                        </div>
                        <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm">
                            <span className="font-extrabold">مرتبط بـ: </span>{row.linked_product}
                        </div>
                        <p className="mt-3 text-sm leading-6 text-slate-700">{row.evidence}</p>
                        <div className="mt-3 flex items-center justify-between rounded-lg bg-amber-50 p-3 text-xs font-bold text-amber-900">
                            <span>{row.next_step}</span>
                            <span className="num shrink-0">{row.mentions ?? 0} ذكر</span>
                        </div>
                    </article>
                ))}
            </Panel>
        </div>
    );
}

export function ObjectionsPanel({ model }) {
    const rows = model.workspace?.objections || [];
    return (
        <Panel
            title="الاعتراضات والمشكلات"
            subtitle="تلخيص تجريبي لأسباب عدم إكمال الطلب، مع توصيات لا تُطبق تلقائيًا."
            Icon={WarningCircle}
            testid="customer-intelligence-panel-objections"
        >
            {!rows.length ? <EmptyState title="لا توجد اعتراضات مصنفة" /> : (
                <div className="grid gap-4 lg:grid-cols-3">
                    {rows.map((row) => (
                        <article key={row.id || row.label} className="rounded-xl border border-slate-200 p-4">
                            <div className="flex items-center justify-between gap-3">
                                <div className="font-black">{row.label}</div>
                                <span className="rounded-full bg-rose-100 px-3 py-1 font-mono text-sm font-black text-rose-800">{row.count ?? 0}</span>
                            </div>
                            <div className="mt-3 inline-flex rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-bold text-amber-800">
                                {row.trend}
                            </div>
                            <p className="mt-3 text-sm leading-6 text-slate-700">{row.evidence}</p>
                            <div className="mt-3 rounded-lg border border-violet-200 bg-violet-50 p-3 text-xs font-bold leading-5 text-violet-950">
                                {row.recommendation}
                            </div>
                        </article>
                    ))}
                </div>
            )}
        </Panel>
    );
}

export function CampaignImpactPanel({ model }) {
    const rows = model.workspace?.campaign_impact || [];
    return (
        <Panel
            title="أثر الحملات على المحادثات"
            subtitle="ربط تجريبي للنية والاعتراض بالحملة؛ لا توجد نسبة مبيعات أو ROAS حقيقية."
            Icon={Megaphone}
            testid="customer-intelligence-panel-campaign-impact"
        >
            {!rows.length ? <EmptyState title="لا يوجد إسناد حملات" /> : (
                <div className="overflow-x-auto rounded-xl border border-slate-200">
                    <table className="mezan-table min-w-[760px] text-sm">
                        <thead>
                            <tr>
                                <th>الحملة</th>
                                <th>المصدر</th>
                                <th>المحادثات</th>
                                <th>مؤهلة</th>
                                <th>طلبات مدفوعة</th>
                                <th>أبرز اعتراض</th>
                                <th>جودة البيانات</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((row) => (
                                <tr key={row.id}>
                                    <td className="font-bold">{row.campaign_name}</td>
                                    <td dir="ltr">{row.source}</td>
                                    <td className="num">{row.conversations ?? 0}</td>
                                    <td className="num">{row.qualified ?? 0}</td>
                                    <td className="num">{row.paid_orders ?? 0}</td>
                                    <td>{row.top_objection || "—"}</td>
                                    <td><StatusPill status={row.data_quality} /></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs font-bold leading-6 text-amber-900">
                لا يُسمح للذكاء بتعديل حملة بناءً على هذه المعاينة. يلزم إسناد موثوق، بيانات
                ربحية مكتملة، حدود ميزانية، واعتماد مستقل.
            </div>
        </Panel>
    );
}

export function KnowledgePanel({ model }) {
    const entries = model.knowledge?.entries || [];
    return (
        <div className="space-y-5" data-testid="customer-intelligence-panel-knowledge">
            <Panel
                title="المعرفة المعتمدة والتعلم"
                subtitle="الحقائق التشغيلية منفصلة عن السياسات المعتمدة والاستنتاجات التحليلية."
                Icon={ClipboardText}
                testid="customer-intelligence-knowledge-entries"
            >
                {!entries.length ? <EmptyState title="لا توجد معرفة في المعاينة" /> : (
                    <div className="grid gap-4 lg:grid-cols-2">
                        {entries.map((entry) => (
                            <article key={entry.id} className="rounded-xl border border-slate-200 p-4">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <div className="font-black">{entry.title}</div>
                                    <StatusPill status={entry.status} />
                                </div>
                                <p className="mt-3 text-sm leading-7 text-slate-700">{entry.body}</p>
                                <div className="mt-3 text-[11px] font-bold text-slate-500">
                                    المصدر: {entry.source || "غير محدد"}
                                </div>
                            </article>
                        ))}
                    </div>
                )}
            </Panel>
            <div className="rounded-xl border border-violet-200 bg-violet-50 p-4 text-sm font-bold leading-7 text-violet-950">
                {model.knowledge?.learning_policy}
            </div>
        </div>
    );
}

export function QualityPanel({ model }) {
    const metrics = model.quality?.metrics || [];
    const reviews = model.quality?.recent_reviews || [];
    return (
        <div className="space-y-5" data-testid="customer-intelligence-panel-quality">
            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="مقاييس الجودة">
                {metrics.map((metric) => (
                    <article key={metric.key || metric.label} className="rounded-xl border border-slate-200 bg-white p-4">
                        <div className="text-xs font-extrabold text-slate-500">{metric.label}</div>
                        <div className="mt-2 font-mono text-2xl font-black">{metric.display || metric.value || "—"}</div>
                        <p className="mt-2 text-[11px] font-semibold leading-5 text-slate-500">{metric.hint}</p>
                    </article>
                ))}
            </section>
            <Panel
                title="مراجعات الجودة التجريبية"
                subtitle="مثال لكيفية تسجيل الخطأ وتصحيح الموظف بدل التعلم العشوائي."
                Icon={CheckCircle}
                testid="customer-intelligence-quality-reviews"
            >
                {!reviews.length ? <EmptyState title="لا توجد مراجعات جودة" /> : reviews.map((review) => (
                    <article key={review.id} className="mb-3 rounded-xl border border-amber-200 bg-amber-50 p-4 last:mb-0">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="font-extrabold text-amber-950">{review.finding}</div>
                            <StatusPill status={review.outcome} />
                        </div>
                        <div className="mt-3 rounded-lg bg-white p-3 text-sm font-bold leading-6 text-slate-800">
                            التصحيح: {review.correction}
                        </div>
                    </article>
                ))}
            </Panel>
        </div>
    );
}

export function IntegrationsPanel({ model, policyKeys, writesLocked }) {
    const rows = model.workspace?.integrations || [];
    return (
        <div className="space-y-5" data-testid="customer-intelligence-panel-integrations">
            <WriteLockBanner locked={writesLocked} />
            <Panel
                title="تكاملات مركز العملاء"
                subtitle="الحالات أدناه لا تثبت اتصالًا فعليًا ولا تمنح أي صلاحية."
                Icon={Plug}
                testid="customer-intelligence-integration-cards"
            >
                {!rows.length ? <EmptyState title="لا توجد تكاملات في المعاينة" /> : (
                    <div className="grid gap-4 lg:grid-cols-3">
                        {rows.map((row) => (
                            <article key={row.id} className="rounded-xl border border-slate-200 p-4">
                                <div className="flex items-start justify-between gap-2">
                                    <div className="font-black leading-6">{row.name}</div>
                                    <StatusPill status={row.status} />
                                </div>
                                <p className="mt-3 text-xs leading-6 text-slate-600">{row.detail}</p>
                            </article>
                        ))}
                    </div>
                )}
            </Panel>
            <Panel
                title="صلاحيات التنفيذ"
                subtitle="المركز يتوقف على أقل صلاحية، ولا يستنتج الاتصال من وجود بيانات قديمة."
                Icon={ShieldCheck}
                testid="customer-intelligence-integration-policy"
            >
                <SafetyChecklist policy={model.safety_policy} keys={policyKeys} />
            </Panel>
        </div>
    );
}

export function AuditPanel({ model }) {
    const rows = model.audit_preview || [];
    return (
        <Panel
            title="سجل الإجراءات التجريبي"
            subtitle="يوضح ما اقترحه النظام ومصدره ونتيجته، دون بيانات عميل حساسة."
            Icon={ShieldCheck}
            testid="customer-intelligence-panel-audit"
        >
            {!rows.length ? <EmptyState title="لا توجد أحداث في سجل المعاينة" /> : (
                <div className="overflow-x-auto rounded-xl border border-slate-200">
                    <table className="mezan-table min-w-[720px] text-sm">
                        <thead>
                            <tr>
                                <th>الإجراء</th>
                                <th>الفاعل</th>
                                <th>المصدر</th>
                                <th>النتيجة</th>
                                <th>الوقت</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((row) => (
                                <tr key={row.id}>
                                    <td className="font-bold">{row.action}</td>
                                    <td>{row.actor}</td>
                                    <td dir="ltr">{row.source}</td>
                                    <td><StatusPill status="preview_only" label={row.result} /></td>
                                    <td>{row.occurred_at || "وقت تجريبي غير مسجل"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </Panel>
    );
}
