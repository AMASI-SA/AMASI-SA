import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import {
    ArrowClockwise,
    ChartLineUp,
    ChatCircleDots,
    CheckCircle,
    ClipboardText,
    Clock,
    Eye,
    Megaphone,
    Package,
    Plug,
    ShieldCheck,
    ShoppingCart,
    UsersThree,
    WarningCircle,
} from "@phosphor-icons/react";

import {
    AuditPanel,
    CampaignImpactPanel,
    ConversationsPanel,
    CustomersPanel,
    FollowUpsPanel,
    IntegrationsPanel,
    KnowledgePanel,
    MarketOpportunitiesPanel,
    ObjectionsPanel,
    OrderDraftsPanel,
    OverviewPanel,
    QualityPanel,
    SalesOpportunitiesPanel,
} from "../components/customerIntelligence/CustomerIntelligencePanels";
import {
    CUSTOMER_INTELLIGENCE_WRITE_POLICY_KEYS,
    connectInstagramCustomerIntelligence,
    createCustomerIntelligenceReplySuggestion,
    customerIntelligenceWritesLocked,
    getCustomerIntelligenceInbox,
    getCustomerLearningStatus,
    getCustomerIntelligenceWorkspace,
    getInstagramCustomerIntelligenceSetup,
    normalizeCustomerIntelligenceInbox,
    normalizeCustomerIntelligenceWorkspace,
    reviewCustomerIntelligenceReplySuggestion,
} from "../services/customerIntelligence";

export const CUSTOMER_INTELLIGENCE_TABS = [
    { id: "overview", label: "نظرة عامة", Icon: Eye },
    { id: "conversations", label: "المحادثات", Icon: ChatCircleDots },
    { id: "customers", label: "العملاء", Icon: UsersThree },
    { id: "followups", label: "المتابعات", Icon: Clock },
    { id: "sales", label: "فرص البيع", Icon: ChartLineUp },
    { id: "drafts", label: "مسودات الطلبات", Icon: ShoppingCart },
    { id: "market", label: "فرص المنتجات/المنافسين", Icon: Package },
    { id: "objections", label: "الاعتراضات", Icon: WarningCircle },
    { id: "campaigns", label: "أثر الحملات", Icon: Megaphone },
    { id: "knowledge", label: "المعرفة", Icon: ClipboardText },
    { id: "quality", label: "الجودة", Icon: CheckCircle },
    { id: "integrations", label: "التكاملات", Icon: Plug },
    { id: "audit", label: "سجل الإجراءات", Icon: ShieldCheck },
];

function tabIsSupported(tab) {
    return CUSTOMER_INTELLIGENCE_TABS.some((item) => item.id === tab);
}

function LoadingPanel() {
    return (
        <div className="space-y-4" data-testid="customer-intelligence-loading">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                {[0, 1, 2, 3, 4].map((key) => (
                    <div key={key} className="h-28 animate-pulse rounded-xl bg-slate-100" />
                ))}
            </div>
            <div className="h-80 animate-pulse rounded-xl border border-slate-200 bg-white" />
        </div>
    );
}

function renderActivePanel({
    activeTab,
    model,
    writesLocked,
    inbox,
    inboxError,
    instagramSetup,
    learningStatus,
    onConnectInstagram,
    onCreateSuggestion,
    onReviewSuggestion,
    onRejectSuggestion,
    onEscalateSuggestion,
}) {
    const common = { model };
    switch (activeTab) {
        case "conversations":
            return (
                <ConversationsPanel
                    inbox={inbox}
                    error={inboxError}
                    learningStatus={learningStatus}
                    instagramSetup={instagramSetup}
                    onConnectInstagram={onConnectInstagram}
                    onCreateSuggestion={onCreateSuggestion}
                    onReviewSuggestion={onReviewSuggestion}
                    onRejectSuggestion={onRejectSuggestion}
                    onEscalateSuggestion={onEscalateSuggestion}
                />
            );
        case "customers":
            return <CustomersPanel {...common} />;
        case "followups":
            return <FollowUpsPanel {...common} />;
        case "sales":
            return <SalesOpportunitiesPanel {...common} />;
        case "drafts":
            return <OrderDraftsPanel {...common} writesLocked={writesLocked} />;
        case "market":
            return <MarketOpportunitiesPanel {...common} />;
        case "objections":
            return <ObjectionsPanel {...common} />;
        case "campaigns":
            return <CampaignImpactPanel {...common} />;
        case "knowledge":
            return <KnowledgePanel {...common} />;
        case "quality":
            return <QualityPanel {...common} />;
        case "integrations":
            return (
                <IntegrationsPanel
                    {...common}
                    writesLocked={writesLocked}
                    policyKeys={CUSTOMER_INTELLIGENCE_WRITE_POLICY_KEYS}
                />
            );
        case "audit":
            return <AuditPanel {...common} />;
        case "overview":
        default:
            return (
                <OverviewPanel
                    {...common}
                    writesLocked={writesLocked}
                    policyKeys={CUSTOMER_INTELLIGENCE_WRITE_POLICY_KEYS}
                />
            );
    }
}

export function CustomerIntelligenceCenterView({
    model,
    inbox,
    activeTab = "overview",
    onTabChange = () => {},
    onRefresh = () => {},
    loading = false,
    refreshing = false,
    error = "",
    inboxError = "",
    instagramSetup = null,
    learningStatus = null,
    onConnectInstagram = null,
    onCreateSuggestion = null,
    onReviewSuggestion = null,
    onRejectSuggestion = null,
    onEscalateSuggestion = null,
    isOwner = true,
}) {
    const normalized = normalizeCustomerIntelligenceWorkspace(model);
    const normalizedInbox = normalizeCustomerIntelligenceInbox(inbox);
    const selectedTab = isOwner && tabIsSupported(activeTab)
        ? activeTab
        : "conversations";
    const visibleTabs = isOwner
        ? CUSTOMER_INTELLIGENCE_TABS
        : CUSTOMER_INTELLIGENCE_TABS.filter((tab) => tab.id === "conversations");
    const writesLocked = customerIntelligenceWritesLocked(normalized.safety_policy);
    const liveInbox = selectedTab === "conversations";
    const titleAr = normalized.workspace.title_ar || "مركز ذكاء العملاء والمبيعات";
    const titleEn = normalized.workspace.title_en || "Customer Intelligence & Sales Center";
    const description = liveInbox
        ? "صندوق موحّد لتفاعلات واتساب وإنستغرام الواردة إلى ميزان، مع اقتراحات قابلة للمراجعة وبقاء الإرسال التلقائي مغلقًا."
        : normalized.workspace.description_ar
            || "مركز موحد لفهم العملاء وتحويل المحادثات إلى اقتراحات قابلة للمراجعة.";
    const inboxConnected = normalizedInbox.connection.status === "connected";

    return (
        <div
            className="space-y-5"
            dir="rtl"
            data-testid="customer-intelligence-center"
            data-preview-only={liveInbox ? "false" : "true"}
            data-write-mode="observe_only"
        >
            <header className="overflow-hidden rounded-xl border border-emerald-950 bg-emerald-950 text-white">
                <div className="grid gap-5 p-5 sm:p-7 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                    <div>
                        <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-violet-300/40 bg-violet-400/10 px-3 py-1 text-xs font-extrabold text-violet-100">
                            {liveInbox
                                ? <ChatCircleDots size={16} weight="fill" />
                                : <Eye size={16} weight="fill" />}
                            {liveInbox
                                ? `قنوات حية · ${inboxConnected ? "متصلة" : "قراءة فقط"}`
                                : "معاينة المالك · Owner Preview"}
                        </div>
                        <h1 className="text-2xl font-black tracking-tight sm:text-3xl">{titleAr}</h1>
                        <div className="mt-1 text-xs font-bold tracking-wide text-emerald-300">{titleEn}</div>
                        <p className="mt-3 max-w-3xl text-sm leading-7 text-emerald-100">{description}</p>
                    </div>
                    <div className="flex flex-col gap-3">
                        <div className="rounded-xl border border-emerald-700 bg-emerald-900 p-3 text-xs font-bold leading-5 text-emerald-100">
                            <div>
                                {liveInbox
                                    ? "وضع التشغيل"
                                    : `المستوى ${normalized.workspace.operating_level || 1}`}
                            </div>
                            <div className="mt-1 text-white">
                                {liveInbox
                                    ? "استقبال حقيقي · قراءة فقط"
                                    : normalized.workspace.operating_level_label || "اقتراح ومراجعة بشرية"}
                            </div>
                        </div>
                        <button
                            type="button"
                            onClick={onRefresh}
                            disabled={loading || refreshing}
                            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-emerald-600 bg-white px-4 text-sm font-extrabold text-emerald-950 transition hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-60"
                            data-testid="customer-intelligence-refresh"
                        >
                            <ArrowClockwise size={18} weight="bold" className={refreshing ? "animate-spin" : ""} />
                            {refreshing
                                ? liveInbox ? "جارٍ تحديث الرسائل…" : "جارٍ تحديث المعاينة…"
                                : liveInbox ? "تحديث الرسائل" : "تحديث المعاينة"}
                        </button>
                    </div>
                </div>
                <div className="border-t border-emerald-800 bg-emerald-900 px-5 py-3 text-xs font-semibold leading-6 text-emerald-100 sm:px-7">
                    {liveInbox ? (
                        <>
                            المعروض في هذا التبويب تفاعلات واتساب وإنستغرام الحقيقية الواردة والمحفوظة في ميزان.
                            الإرسال والرد التلقائي وإنشاء الطلبات وأي تعديل خارجي مغلق.
                        </>
                    ) : (
                        <>
                            البيانات مصطنعة من Backend ومخصّصة لإثبات البنية فقط. لا اتصال واتساب،
                            ولا طلب أو خصم أو رابط دفع حقيقي، ولا تعديل منتج أو حملة.
                        </>
                    )}
                </div>
            </header>

            {(liveInbox ? inboxError : error) && (
                <div
                    className="flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-rose-900"
                    data-testid="customer-intelligence-error"
                >
                    <WarningCircle size={22} weight="fill" className="mt-0.5 shrink-0" />
                    <div>
                        <div className="font-extrabold">
                            {liveInbox ? "تعذر تحميل تفاعلات قنوات العملاء" : "تعذر تحميل مساحة المعاينة"}
                        </div>
                        <p className="mt-1 text-xs leading-5">{liveInbox ? inboxError : error}</p>
                        <p className="mt-1 text-xs font-bold">
                            لم تُعرض بيانات بديلة. أعد التحديث بعد التحقق من Backend.
                        </p>
                    </div>
                </div>
            )}

            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white p-2 [scrollbar-width:thin]">
                <nav className="flex min-w-max gap-2" aria-label="أقسام مركز ذكاء العملاء">
                    {visibleTabs.map(({ id, label, Icon }) => {
                        const active = selectedTab === id;
                        return (
                            <button
                                key={id}
                                type="button"
                                onClick={() => onTabChange(id)}
                                className={`inline-flex min-h-11 items-center gap-2 rounded-lg px-4 text-sm font-extrabold transition ${
                                    active
                                        ? "bg-violet-700 text-white shadow-sm"
                                        : "text-slate-600 hover:bg-violet-50 hover:text-violet-800"
                                }`}
                                aria-current={active ? "page" : undefined}
                                data-testid={`customer-intelligence-tab-${id}`}
                            >
                                <Icon size={18} weight="duotone" />
                                {label}
                            </button>
                        );
                    })}
                </nav>
            </div>

            {loading ? LoadingPanel() : renderActivePanel({
                activeTab: selectedTab,
                model: normalized,
                writesLocked,
                inbox: normalizedInbox,
                inboxError,
                instagramSetup,
                learningStatus,
                onConnectInstagram,
                onCreateSuggestion,
                onReviewSuggestion,
                onRejectSuggestion,
                onEscalateSuggestion,
            })}
        </div>
    );
}

function errorMessage(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (typeof detail?.message === "string" && detail.message.trim()) return detail.message;
    return fallback;
}

export default function CustomerIntelligenceCenter() {
    const { user } = useAuth();
    const isOwner = user?.is_owner === true;
    const [searchParams, setSearchParams] = useSearchParams();
    const requestedTab = searchParams.get("tab") || "overview";
    const activeTab = isOwner && tabIsSupported(requestedTab)
        ? requestedTab
        : "conversations";
    const [model, setModel] = useState(() => normalizeCustomerIntelligenceWorkspace({}));
    const [inbox, setInbox] = useState(() => normalizeCustomerIntelligenceInbox({}));
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState("");
    const [inboxLoading, setInboxLoading] = useState(activeTab === "conversations");
    const [inboxRefreshing, setInboxRefreshing] = useState(false);
    const [inboxError, setInboxError] = useState("");
    const [instagramSetup, setInstagramSetup] = useState(null);
    const [learningStatus, setLearningStatus] = useState(null);

    const load = useCallback(async ({ refresh = false } = {}) => {
        if (refresh) setRefreshing(true);
        else setLoading(true);
        setError("");
        try {
            setModel(await getCustomerIntelligenceWorkspace());
        } catch (requestError) {
            setModel(normalizeCustomerIntelligenceWorkspace({}));
            setError(errorMessage(
                requestError,
                "تعذر الاتصال بمساحة ذكاء العملاء التجريبية.",
            ));
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, []);

    const loadInbox = useCallback(async ({ refresh = false } = {}) => {
        if (refresh) setInboxRefreshing(true);
        else setInboxLoading(true);
        setInboxError("");
        try {
            setInbox(await getCustomerIntelligenceInbox());
        } catch (requestError) {
            setInbox(normalizeCustomerIntelligenceInbox({}));
            setInboxError(errorMessage(
                requestError,
                "تعذر تحميل تفاعلات قنوات العملاء الواردة.",
            ));
        } finally {
            setInboxLoading(false);
            setInboxRefreshing(false);
        }
    }, []);

    const loadInstagramSetup = useCallback(async () => {
        if (!isOwner) return;
        try {
            setInstagramSetup(await getInstagramCustomerIntelligenceSetup());
        } catch (_requestError) {
            setInstagramSetup(null);
        }
    }, [isOwner]);

    const loadLearningStatus = useCallback(async () => {
        try {
            setLearningStatus(await getCustomerLearningStatus());
        } catch (_requestError) {
            setLearningStatus(null);
        }
    }, []);

    const connectInstagram = useCallback(async (candidateRef) => {
        await connectInstagramCustomerIntelligence(candidateRef);
        await Promise.all([
            loadInstagramSetup(),
            loadInbox({ refresh: true }),
        ]);
    }, [loadInbox, loadInstagramSetup]);

    const createSuggestion = useCallback(async (conversationId) => {
        await createCustomerIntelligenceReplySuggestion(conversationId);
        await loadInbox({ refresh: true });
    }, [loadInbox]);

    const reviewSuggestion = useCallback(async (
        decision,
        conversationId,
        suggestionId,
        review,
    ) => {
        await reviewCustomerIntelligenceReplySuggestion({
            conversationId,
            suggestionId,
            decision,
            text: review?.text,
            version: review?.version,
        });
        await loadInbox({ refresh: true });
    }, [loadInbox]);

    useEffect(() => {
        if (isOwner) {
            load();
            return;
        }
        setModel(normalizeCustomerIntelligenceWorkspace({}));
        setError("");
        setLoading(false);
    }, [isOwner, load]);

    useEffect(() => {
        if (activeTab !== "conversations") return;
        loadInbox();
        loadLearningStatus();
        if (isOwner) loadInstagramSetup();
    }, [activeTab, isOwner, loadInbox, loadInstagramSetup, loadLearningStatus]);

    useEffect(() => {
        if (isOwner || requestedTab === "conversations") return;
        const next = new URLSearchParams(searchParams);
        next.set("tab", "conversations");
        setSearchParams(next, { replace: true });
    }, [isOwner, requestedTab, searchParams, setSearchParams]);

    const selectTab = useCallback((tab) => {
        if (!isOwner && tab !== "conversations") return;
        if (tab === "conversations" && activeTab !== "conversations") {
            setInboxLoading(true);
        }
        const next = new URLSearchParams(searchParams);
        next.set("tab", tab);
        setSearchParams(next, { replace: true });
    }, [activeTab, isOwner, searchParams, setSearchParams]);

    const viewProps = useMemo(() => ({
        model,
        inbox,
        activeTab,
        onTabChange: selectTab,
        onRefresh: () => (
            activeTab === "conversations"
                ? Promise.all([
                    loadInbox({ refresh: true }),
                    loadLearningStatus(),
                    ...(isOwner ? [loadInstagramSetup()] : []),
                ])
                : load({ refresh: true })
        ),
        loading: activeTab === "conversations" ? inboxLoading : loading,
        refreshing: activeTab === "conversations" ? inboxRefreshing : refreshing,
        error,
        inboxError,
        instagramSetup,
        learningStatus,
        onConnectInstagram: connectInstagram,
        isOwner,
        onCreateSuggestion: createSuggestion,
        onReviewSuggestion: (conversationId, suggestionId, review) => (
            reviewSuggestion("approve", conversationId, suggestionId, review)
        ),
        onRejectSuggestion: (conversationId, suggestionId, review) => (
            reviewSuggestion("reject", conversationId, suggestionId, review)
        ),
        onEscalateSuggestion: (conversationId, suggestionId, review) => (
            reviewSuggestion("escalate", conversationId, suggestionId, review)
        ),
    }), [
        activeTab,
        error,
        inbox,
        inboxError,
        inboxLoading,
        inboxRefreshing,
        instagramSetup,
        learningStatus,
        isOwner,
        load,
        loadInbox,
        loadInstagramSetup,
        loadLearningStatus,
        loading,
        model,
        refreshing,
        reviewSuggestion,
        selectTab,
        createSuggestion,
        connectInstagram,
    ]);

    return <CustomerIntelligenceCenterView {...viewProps} />;
}
