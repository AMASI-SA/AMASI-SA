import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    CaretDown,
    CaretUp,
    CheckCircle,
    ClockCounterClockwise,
    LockKey,
    Megaphone,
    PauseCircle,
    PlayCircle,
    ShieldCheck,
    WarningCircle,
} from "@phosphor-icons/react";

import {
    approveSnapchatManagementProposal,
    createSnapchatManagementProposal,
    executeSnapchatManagementProposal,
    getSnapchatManagementReadiness,
    listSnapchatManagementProposals,
    managementError,
    microToNativeAmount,
    nativeAmountToMicro,
    pollSnapchatManagementProposal,
    rollbackSnapchatManagementProposal,
} from "../../services/snapchatCampaignManagement";
import { listProductsV2 } from "../../services/mezanProductsV2";

const ACTIONS = [
    ["campaign.create", "إنشاء حملة"],
    ["campaign.update", "تعديل حملة"],
    ["ad_squad.create", "إنشاء مجموعة إعلانية"],
    ["ad_squad.update", "تعديل مجموعة إعلانية"],
    ["creative.create", "إنشاء إبداع من Media ID"],
    ["ad.create", "إنشاء إعلان"],
    ["ad.update", "تعديل إعلان"],
];

const STATUS_LABELS = {
    previewed: "معاينة بانتظار الاعتماد",
    approved: "معتمد بانتظار التنفيذ",
    executing: "قيد التنفيذ",
    completed: "نُفذ وتحقق",
    failed: "فشل التنفيذ",
    rolling_back: "قيد التراجع",
    rolled_back: "تم التراجع",
};

const ACTION_LABELS = Object.fromEntries(ACTIONS);
const DELIVERY_CREATE_ACTIONS = new Set([
    "campaign.create",
    "ad_squad.create",
    "ad.create",
]);

const MEASURABLE_DIRECTIONS = [
    ["increase", "ارتفاع"],
    ["stable", "ثبات"],
    ["decrease", "انخفاض"],
];

function proposalFailureDetail(proposal) {
    return proposal?.failure?.provider_error_message
        || proposal?.failure?.message
        || proposal?.failure?.code
        || "";
}

function localStartTime() {
    const value = new Date(Date.now() + 15 * 60 * 1000);
    value.setSeconds(0, 0);
    const offset = value.getTimezoneOffset() * 60_000;
    return new Date(value.getTime() - offset).toISOString().slice(0, 16);
}

function initialForm({ action = "campaign.create", selectedCampaign, selectedAdSquad } = {}) {
    return {
        action,
        accountId: "",
        targetId: action === "ad_squad.update" ? selectedAdSquad?.ad_squad_id || "" : "",
        parentId: action.startsWith("ad.")
            ? selectedAdSquad?.ad_squad_id || ""
            : action.startsWith("ad_squad.")
                ? selectedCampaign?.campaign_id || ""
                : "",
        name: "",
        startTime: localStartTime(),
        dailyBudget: action.endsWith(".update") ? "" : "50",
        objective: "SALES",
        country: "sa",
        optimizationGoal: "PIXEL_PURCHASE",
        status: "",
        mediaId: "",
        profileId: "",
        headline: "",
        creativeType: "SNAP_AD",
        callToAction: "SHOP_NOW",
        destinationUrl: "",
        creativeId: "",
        adType: "SNAP_AD",
        productId: "",
        productVariantId: "",
        productName: "",
        salesDirection: "increase",
        contributionProfitDirection: "increase",
        userContextNote: "",
        trendOverrideReason: "",
        reason: "إدارة معتمدة من مالك الحساب عبر ميزان",
        advancedJson: "{}",
        activationAcknowledged: false,
    };
}

function inputClass() {
    return "mt-1 h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-bold text-slate-900 outline-none focus:border-amber-400";
}

function TextField({ label, value, onChange, type = "text", required = false, placeholder = "", dir = "rtl", testId }) {
    return (
        <label className="block text-xs font-black text-slate-600">
            {label}
            <input
                type={type}
                value={value}
                required={required}
                placeholder={placeholder}
                dir={dir}
                data-testid={testId}
                onChange={(event) => onChange(event.target.value)}
                className={inputClass()}
            />
        </label>
    );
}

function SelectField({ label, value, onChange, children, disabled = false, required = false, testId }) {
    return (
        <label className="block text-xs font-black text-slate-600">
            {label}
            <select
                value={value}
                onChange={(event) => onChange(event.target.value)}
                disabled={disabled}
                required={required}
                data-testid={testId}
                className={`${inputClass()} disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400`}
            >
                {children}
            </select>
        </label>
    );
}

function mergeAdvanced(payload, source) {
    const parsed = JSON.parse(source || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("الحقول الإضافية يجب أن تكون كائن JSON.");
    }
    return { ...payload, ...parsed };
}

function retainedDecisionContext(form) {
    return {
        productId: form.productId,
        productVariantId: form.productVariantId,
        productName: form.productName,
        salesDirection: form.salesDirection,
        contributionProfitDirection: form.contributionProfitDirection,
        userContextNote: form.userContextNote,
        trendOverrideReason: form.trendOverrideReason,
    };
}

function productIdentity(product) {
    return String(
        product?.salla_product_id
        || product?.mezan_product_id
        || product?.id
        || "",
    ).trim();
}

function productLabel(product) {
    const name = String(product?.name || "منتج بدون اسم").trim();
    const sku = String(product?.sku || "").trim();
    return sku ? `${name} · ${sku}` : name;
}

function buildProposal(form) {
    if (DELIVERY_CREATE_ACTIONS.has(form.action) && !form.productId) {
        throw new Error("اختر المنتج الذي سيعلن له قبل إنشاء كيان إعلاني جديد.");
    }
    const common = {
        action: form.action,
        account_id: form.accountId,
        target_id: form.targetId || null,
        parent_id: form.parentId || null,
        reason: form.reason,
        idempotency_key: `snap-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
        activation_acknowledged: form.activationAcknowledged,
        products: form.productId ? [{
            product_id: form.productId,
            product_variant_id: form.productVariantId || null,
            product_name: form.productName || null,
        }] : [],
        expected_outcome: {
            primary_goal: "grow_sales_while_protecting_contribution_profit",
            sales_direction: form.salesDirection,
            contribution_profit_direction: form.contributionProfitDirection,
            evaluation_horizons_hours: [24, 72, 168],
        },
        supporting_evidence: form.userContextNote.trim() ? [{
            kind: "user_context",
            value: form.userContextNote.trim(),
            source: "snapchat_management_panel:user",
            verification_status: "user_suggestion",
            confidence: 0,
            used_in_decision: false,
            weight: 0,
        }] : [],
        trend_override_reason: form.trendOverrideReason.trim() || null,
    };
    let payload = {};
    if (form.action === "campaign.create") {
        payload = {
            name: form.name,
            start_time: new Date(form.startTime).toISOString(),
            daily_budget_micro: nativeAmountToMicro(form.dailyBudget),
            objective_v2_properties: { objective_v2_type: form.objective },
        };
    } else if (form.action === "campaign.update") {
        if (form.name) payload.name = form.name;
        if (form.dailyBudget) payload.daily_budget_micro = nativeAmountToMicro(form.dailyBudget);
        if (form.status) payload.status = form.status;
    } else if (form.action === "ad_squad.create") {
        payload = {
            name: form.name,
            type: "SNAP_ADS",
            targeting: {
                regulated_content: false,
                geos: [{ country_code: form.country.toLowerCase() }],
            },
            placement_v2: { config: "AUTOMATIC" },
            billing_event: "IMPRESSION",
            bid_strategy: "AUTO_BID",
            optimization_goal: form.optimizationGoal,
            daily_budget_micro: nativeAmountToMicro(form.dailyBudget),
        };
    } else if (form.action === "ad_squad.update") {
        if (form.name) payload.name = form.name;
        if (form.dailyBudget) payload.daily_budget_micro = nativeAmountToMicro(form.dailyBudget);
        if (form.status) payload.status = form.status;
    } else if (form.action === "creative.create") {
        payload = {
            name: form.name,
            type: form.creativeType,
            headline: form.headline,
            top_snap_media_id: form.mediaId,
            profile_properties: { profile_id: form.profileId },
            shareable: true,
            forced_view_eligibility: "NONE",
            render_type: "STATIC",
        };
        if (form.creativeType === "WEB_VIEW") {
            payload.call_to_action = form.callToAction;
            payload.web_view_properties = {
                url: form.destinationUrl,
                allow_snap_javascript_sdk: false,
                use_immersive_mode: false,
                deep_link_urls: [],
                block_preload: true,
                web_browser_type: "SNAP",
            };
        }
    } else if (form.action === "ad.create") {
        payload = {
            name: form.name,
            creative_id: form.creativeId,
            type: form.adType,
        };
    } else if (form.action === "ad.update") {
        if (form.name) payload.name = form.name;
        if (form.status) payload.status = form.status;
    }
    return { ...common, payload: mergeAdvanced(payload, form.advancedJson) };
}

function ProposalPreview({ proposal, readiness, busy, onApprove, onExecute, onRollback }) {
    if (!proposal?.proposal_id) return null;
    const preview = proposal.preview || {};
    const canApprove = proposal.status === "previewed" && proposal.confirm_token;
    const canExecute = proposal.status === "approved" && readiness?.execution_enabled;
    const canRollback = readiness?.execution_enabled && (
        proposal.status === "completed"
        || (proposal.status === "failed" && proposal.provider_write_reached)
    );
    return (
        <article className="rounded-2xl border border-amber-300 bg-amber-50 p-4" data-testid="snapchat-management-preview">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="text-sm font-black text-slate-950">{ACTION_LABELS[proposal.action] || proposal.action}</div>
                    <div className="mt-1 text-xs font-bold text-slate-600">{STATUS_LABELS[proposal.status] || proposal.status}</div>
                </div>
                <span className="rounded-full bg-white px-3 py-1 font-mono text-[10px] font-bold text-slate-500">
                    {proposal.proposal_id.slice(0, 8)}
                </span>
            </div>
            <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-xl bg-white p-3"><span className="block text-slate-400">الاسم</span><strong>{preview.name || "—"}</strong></div>
                <div className="rounded-xl bg-white p-3"><span className="block text-slate-400">الحالة</span><strong>{preview.status || "بدون تغيير"}</strong></div>
                <div className="rounded-xl bg-white p-3"><span className="block text-slate-400">الميزانية اليومية</span><strong>{preview.daily_budget_micro ? `${microToNativeAmount(preview.daily_budget_micro).toLocaleString("en-US")} وحدة` : "—"}</strong></div>
                <div className="rounded-xl bg-white p-3"><span className="block text-slate-400">الحقول</span><strong>{(preview.changed_fields || []).join("، ") || "—"}</strong></div>
            </div>
            {proposal.creates_paused && (
                <div className="mt-3 flex items-center gap-2 rounded-xl bg-emerald-100 p-3 text-xs font-black text-emerald-900">
                    <PauseCircle size={20} weight="fill" /> سيُنشأ الكيان متوقفًا؛ لا يبدأ صرف بمجرد التنفيذ.
                </div>
            )}
            {proposal.verification?.verified && (
                <div className="mt-3 flex items-center gap-2 rounded-xl bg-emerald-100 p-3 text-xs font-black text-emerald-900">
                    <CheckCircle size={20} weight="fill" /> تحقق ميزان من الكيان بعد قراءته مرة أخرى من Snapchat.
                </div>
            )}
            {proposal.provider_write_uncertain && (
                <div className="mt-3 flex items-center gap-2 rounded-xl bg-rose-100 p-3 text-xs font-black text-rose-900">
                    <WarningCircle size={20} weight="fill" /> نتيجة وصول الكتابة غير محسومة؛ يلزم فحص Snapchat قبل إعادة المحاولة.
                </div>
            )}
            {proposal.status === "failed" && proposalFailureDetail(proposal) && (
                <div className="mt-3 flex items-center gap-2 rounded-xl bg-rose-100 p-3 text-xs font-black text-rose-900">
                    <WarningCircle size={20} weight="fill" /> {proposalFailureDetail(proposal)}
                </div>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
                {canApprove && (
                    <button type="button" disabled={busy} onClick={onApprove} className="min-h-10 rounded-xl bg-slate-950 px-4 text-xs font-black text-white disabled:opacity-50" data-testid="snapchat-management-approve">
                        اعتماد المعاينة
                    </button>
                )}
                {proposal.status === "approved" && (
                    <button type="button" disabled={busy || !canExecute} onClick={onExecute} className="inline-flex min-h-10 items-center gap-2 rounded-xl bg-emerald-600 px-4 text-xs font-black text-white disabled:cursor-not-allowed disabled:bg-slate-300" data-testid="snapchat-management-execute">
                        <PlayCircle size={18} weight="fill" /> تنفيذ ثم تحقق
                    </button>
                )}
                {canRollback && (
                    <button type="button" disabled={busy} onClick={onRollback} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-rose-200 bg-white px-4 text-xs font-black text-rose-700 disabled:opacity-50" data-testid="snapchat-management-rollback">
                        <ClockCounterClockwise size={18} weight="bold" /> تراجع متحقق
                    </button>
                )}
            </div>
        </article>
    );
}

export default function SnapchatCampaignManagementPanel({
    accountId,
    entityLevel = "campaigns",
    selectedCampaign = null,
    selectedAdSquad = null,
    onChanged,
}) {
    const preferredAction = entityLevel === "ads"
        ? "ad.create"
        : entityLevel === "ad_squads"
            ? "ad_squad.create"
            : "campaign.create";
    const [expanded, setExpanded] = useState(false);
    const [readiness, setReadiness] = useState(null);
    const [proposals, setProposals] = useState([]);
    const [form, setForm] = useState(() => initialForm({ action: preferredAction, selectedCampaign, selectedAdSquad }));
    const [productQuery, setProductQuery] = useState("");
    const [catalogProducts, setCatalogProducts] = useState([]);
    const [selectedCatalogProduct, setSelectedCatalogProduct] = useState(null);
    const [productsLoaded, setProductsLoaded] = useState(false);
    const [productsLoading, setProductsLoading] = useState(false);
    const [productError, setProductError] = useState("");
    const [activeProposal, setActiveProposal] = useState(null);
    const [loading, setLoading] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const [nextReadiness, nextProposals] = await Promise.all([
                getSnapchatManagementReadiness(),
                listSnapchatManagementProposals({ limit: 12 }),
            ]);
            setReadiness(nextReadiness);
            setProposals(nextProposals);
            setForm((current) => ({
                ...current,
                accountId: nextReadiness.accounts.some((item) => item.account_id === current.accountId)
                    ? current.accountId
                    : nextReadiness.accounts.find((item) => item.account_id === accountId)?.account_id
                        || nextReadiness.accounts[0]?.account_id
                        || "",
            }));
        } catch (loadError) {
            setError(managementError(loadError, "تعذّر فحص جاهزية إدارة Snapchat."));
        } finally {
            setLoading(false);
        }
    }, [accountId]);

    const loadProducts = useCallback(async (query = "") => {
        setProductsLoading(true);
        setProductError("");
        try {
            const result = await listProductsV2({
                page: 1,
                perPage: 30,
                query,
                status: "active",
            });
            setCatalogProducts(Array.isArray(result?.items) ? result.items : []);
        } catch (loadError) {
            setProductError(managementError(loadError, "تعذّر تحميل كتالوج المنتجات."));
        } finally {
            setProductsLoaded(true);
            setProductsLoading(false);
        }
    }, []);

    useEffect(() => {
        if (expanded && !readiness && !loading) load();
    }, [expanded, readiness, loading, load]);

    useEffect(() => {
        if (expanded && !productsLoaded && !productsLoading) loadProducts("");
    }, [expanded, productsLoaded, productsLoading, loadProducts]);

    useEffect(() => {
        setForm((current) => {
            if (current.action !== preferredAction) return current;
            const next = initialForm({ action: preferredAction, selectedCampaign, selectedAdSquad });
            return {
                ...next,
                ...retainedDecisionContext(current),
                accountId: current.accountId || accountId || "",
            };
        });
    }, [preferredAction, selectedCampaign, selectedAdSquad, accountId]);

    const selectedAccount = useMemo(
        () => readiness?.accounts?.find((item) => item.account_id === form.accountId) || null,
        [readiness, form.accountId],
    );

    function field(name, value) {
        setForm((current) => ({ ...current, [name]: value }));
    }

    function changeAction(action) {
        setActiveProposal(null);
        setNotice("");
        setError("");
        setForm((current) => ({
            ...initialForm({ action, selectedCampaign, selectedAdSquad }),
            ...retainedDecisionContext(current),
            accountId: current.accountId,
        }));
    }

    function chooseProduct(value) {
        const selected = catalogProducts.find((product) => productIdentity(product) === value)
            || (productIdentity(selectedCatalogProduct) === value ? selectedCatalogProduct : null);
        setSelectedCatalogProduct(selected);
        setForm((current) => ({
            ...current,
            productId: value,
            productName: String(selected?.name || "").trim().slice(0, 300),
            productVariantId: "",
        }));
    }

    async function preview(event) {
        event.preventDefault();
        setBusy(true);
        setError("");
        setNotice("بدأ تجهيز المعاينة في الخلفية. لا تغلق الصفحة ولا تنشئ معاينة أخرى.");
        try {
            const proposal = await createSnapchatManagementProposal(buildProposal(form));
            setActiveProposal(proposal);
            setNotice("تم إنشاء معاينة فقط. راجعها ثم اعتمدها قبل التنفيذ.");
            setProposals((current) => [proposal, ...current.filter((row) => row.proposal_id !== proposal.proposal_id)].slice(0, 12));
        } catch (requestError) {
            setNotice("");
            setError(managementError(requestError, "تعذّر إنشاء معاينة العملية."));
        } finally {
            setBusy(false);
        }
    }

    async function approve() {
        setBusy(true);
        setError("");
        try {
            const proposal = await approveSnapchatManagementProposal(activeProposal);
            setActiveProposal(proposal);
            setNotice("تم اعتماد المعاينة. لم تصل أي كتابة إلى Snapchat بعد.");
        } catch (requestError) {
            setError(managementError(requestError, "تعذّر اعتماد المعاينة."));
        } finally {
            setBusy(false);
        }
    }

    async function execute() {
        const proposalId = activeProposal.proposal_id;
        setBusy(true);
        setError("");
        setNotice("");
        try {
            const accepted = await executeSnapchatManagementProposal(proposalId);
            setActiveProposal((current) => (
                current?.proposal_id === proposalId
                    ? { ...current, status: accepted.status || "executing" }
                    : current
            ));
            setNotice("بدأ التنفيذ والتحقق في الخلفية. لا تضغط تنفيذ مرة أخرى؛ يتابع ميزان النتيجة تلقائيًا.");
            const result = await pollSnapchatManagementProposal({ proposalId });
            setProposals(result.proposals);
            setActiveProposal(result.proposal);
            if (result.proposal.status === "completed") {
                setNotice("نُفذت العملية وتحقق ميزان من النتيجة عبر قراءة Snapchat بعد الكتابة.");
                onChanged?.();
            } else {
                const detail = proposalFailureDetail(result.proposal)
                    || "أبلغ Snapchat عن فشل العملية.";
                setNotice("");
                setError(`فشل التنفيذ: ${detail} لا تُعد التنفيذ قبل معالجة السبب.`);
            }
        } catch (requestError) {
            setNotice("");
            setError(`${managementError(requestError, "تعذّر تأكيد الحالة النهائية.")} لا تضغط تنفيذ مرة أخرى؛ حدّث السجل لاحقًا.`);
        } finally {
            setBusy(false);
        }
    }

    async function rollback() {
        setBusy(true);
        setError("");
        try {
            const proposal = await rollbackSnapchatManagementProposal(
                activeProposal,
                "تراجع معتمد من مالك الحساب عبر ميزان",
            );
            setActiveProposal(proposal);
            setNotice("تم التراجع والتحقق من الحالة النهائية في Snapchat.");
            await load();
            onChanged?.();
        } catch (requestError) {
            setError(managementError(requestError, "تعذّر التراجع عن العملية."));
        } finally {
            setBusy(false);
        }
    }

    const isUpdate = form.action.endsWith(".update");
    const showsBudget = ["campaign.create", "campaign.update", "ad_squad.create", "ad_squad.update"].includes(form.action);
    const showsParent = form.action.startsWith("ad_squad.") || form.action.startsWith("ad.");
    const showsStatus = isUpdate;
    const activeRequested = form.status === "ACTIVE";
    const requiresProduct = DELIVERY_CREATE_ACTIONS.has(form.action);
    const selectedVariants = Array.isArray(selectedCatalogProduct?.variants)
        ? selectedCatalogProduct.variants
        : [];
    const selectedProductIsOutsideResults = form.productId
        && !catalogProducts.some((product) => productIdentity(product) === form.productId);
    const actionAllowed = form.action === "creative.create"
        ? selectedAccount?.creative_allowed
        : selectedAccount?.management_allowed;

    return (
        <section className="mb-4 overflow-hidden rounded-2xl border border-slate-800 bg-slate-950 text-white shadow-lg" data-testid="snapchat-campaign-management-panel">
            <button type="button" onClick={() => setExpanded((value) => !value)} className="flex w-full items-center gap-3 p-4 text-right" aria-expanded={expanded}>
                <span className="rounded-xl bg-amber-300 p-2.5 text-slate-950"><Megaphone size={22} weight="duotone" /></span>
                <span className="min-w-0 flex-1">
                    <span className="block text-sm font-black">إدارة حملات Snapchat</span>
                    <span className="mt-1 block text-[11px] font-bold text-slate-300">معاينة ← اعتماد ← تنفيذ ← تحقق ← سجل وتراجع</span>
                </span>
                <span className="inline-flex items-center gap-2 rounded-full bg-emerald-400/10 px-3 py-1 text-[11px] font-black text-emerald-200">
                    <PauseCircle size={16} weight="fill" /> الإنشاء متوقف بلا صرف
                </span>
                {expanded ? <CaretUp size={18} /> : <CaretDown size={18} />}
            </button>

            {expanded && (
                <div className="space-y-4 border-t border-white/10 bg-slate-50 p-4 text-slate-900">
                    {loading && !readiness ? (
                        <div className="flex items-center gap-2 rounded-xl bg-white p-4 text-sm font-black text-slate-600">
                            <ArrowClockwise size={18} className="animate-spin" /> فحص الدور ومفاتيح الأمان…
                        </div>
                    ) : (
                        <>
                            <div className="grid gap-3 md:grid-cols-3">
                                <div className={`rounded-xl border p-3 ${selectedAccount?.management_allowed ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50"}`}>
                                    <div className="flex items-center gap-2 text-xs font-black"><ShieldCheck size={18} weight="fill" /> دور الحساب</div>
                                    <div className="mt-1 text-xs font-bold">
                                        {selectedAccount?.management_allowed ? `الحملات · ${selectedAccount.role}` : "لا توجد صلاحية حملات موثقة"}
                                        <span className="mx-1">·</span>
                                        {selectedAccount?.creative_allowed ? `الإبداع · ${selectedAccount.creative_role}` : "الإبداع غير متاح"}
                                    </div>
                                </div>
                                <div className={`rounded-xl border p-3 ${readiness?.execution_enabled ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}>
                                    <div className="flex items-center gap-2 text-xs font-black"><LockKey size={18} weight="fill" /> مفتاح الكتابة</div>
                                    <div className="mt-1 text-xs font-bold">{readiness?.execution_enabled ? "مفتوح للعمليات المعتمدة" : "مغلق؛ المعاينة والاعتماد فقط"}</div>
                                </div>
                                <div className={`rounded-xl border p-3 ${readiness?.activation_enabled ? "border-emerald-200 bg-emerald-50" : "border-slate-200 bg-white"}`}>
                                    <div className="flex items-center gap-2 text-xs font-black"><PlayCircle size={18} weight="fill" /> مفتاح التشغيل</div>
                                    <div className="mt-1 text-xs font-bold">{readiness?.activation_enabled ? "التشغيل متاح بعد إقرار صريح" : "مغلق مستقلًا؛ الإيقاف مسموح"}</div>
                                </div>
                            </div>

                            {error && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-black text-rose-800"><WarningCircle size={18} weight="fill" className="ml-2 inline" />{error}</div>}
                            {notice && <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs font-black text-emerald-800"><CheckCircle size={18} weight="fill" className="ml-2 inline" />{notice}</div>}

                            <form onSubmit={preview} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4" data-testid="snapchat-management-form">
                                <div className="grid gap-3 md:grid-cols-2">
                                    <SelectField label="الحساب الإعلاني" value={form.accountId} onChange={(value) => field("accountId", value)} testId="snapchat-management-account-select">
                                        {!readiness?.accounts?.length && <option value="">لا يوجد حساب محدد</option>}
                                        {(readiness?.accounts || []).map((account) => <option key={account.account_id} value={account.account_id} label={`${account.display_name} · ${account.currency}`} />)}
                                    </SelectField>
                                    <SelectField label="العملية" value={form.action} onChange={changeAction} testId="snapchat-management-action-select">
                                        {ACTIONS.map(([value, label]) => <option key={value} value={value} label={label} />)}
                                    </SelectField>
                                </div>

                                <section className="space-y-3 rounded-2xl border border-emerald-200 bg-emerald-50/60 p-3" data-testid="snapchat-management-product-scope">
                                    <div>
                                        <h3 className="text-sm font-black text-slate-900">المنتج المعلن له</h3>
                                        <p className="mt-1 text-[11px] font-bold text-slate-500">
                                            {requiresProduct
                                                ? "مطلوب للإنشاء حتى يقيس ميزان مبيعات المنتج ومخزونه قبل القرار وبعده."
                                                : "اختياري في التعديل والإبداع، ويُحفظ إذا اخترته لربط القياس بالمنتج."}
                                        </p>
                                    </div>
                                    <div className="flex flex-col gap-2 sm:flex-row">
                                        <label className="min-w-0 flex-1 text-xs font-black text-slate-600">
                                            ابحث بالاسم أو SKU أو رقم سلة
                                            <input
                                                type="search"
                                                value={productQuery}
                                                onChange={(event) => setProductQuery(event.target.value)}
                                                onKeyDown={(event) => {
                                                    if (event.key === "Enter") {
                                                        event.preventDefault();
                                                        loadProducts(productQuery);
                                                    }
                                                }}
                                                className={inputClass()}
                                                data-testid="snapchat-management-product-search"
                                            />
                                        </label>
                                        <button
                                            type="button"
                                            disabled={productsLoading}
                                            onClick={() => loadProducts(productQuery)}
                                            className="mt-auto min-h-11 rounded-xl border border-emerald-300 bg-white px-4 text-xs font-black text-emerald-800 disabled:opacity-50"
                                            data-testid="snapchat-management-product-search-button"
                                        >
                                            {productsLoading ? "جارٍ البحث…" : "بحث في الكتالوج"}
                                        </button>
                                    </div>
                                    <div className="grid gap-3 md:grid-cols-2">
                                        <SelectField
                                            label={requiresProduct ? "اختر المنتج · مطلوب" : "اختر المنتج · اختياري"}
                                            value={form.productId}
                                            onChange={chooseProduct}
                                            required={requiresProduct}
                                            disabled={productsLoading}
                                            testId="snapchat-management-product-select"
                                        >
                                            <option value="" label={productsLoading ? "جارٍ تحميل المنتجات…" : "اختر من كتالوج المنتجات"} />
                                            {selectedProductIsOutsideResults && (
                                                <option value={form.productId} label={form.productName || form.productId} />
                                            )}
                                            {catalogProducts.map((product) => {
                                                const identity = productIdentity(product);
                                                return identity ? <option key={identity} value={identity} label={productLabel(product)} /> : null;
                                            })}
                                        </SelectField>
                                        {selectedVariants.length > 0 && (
                                            <SelectField
                                                label="متغير المنتج · اختياري"
                                                value={form.productVariantId}
                                                onChange={(value) => field("productVariantId", value)}
                                                testId="snapchat-management-product-variant-select"
                                            >
                                                <option value="">كل متغيرات المنتج</option>
                                                {selectedVariants.map((variant) => {
                                                    const identity = String(variant?.id || "").trim();
                                                    const label = variant?.display_name || variant?.name || variant?.sku || identity;
                                                    return identity ? <option key={identity} value={identity} label={label} /> : null;
                                                })}
                                            </SelectField>
                                        )}
                                    </div>
                                    {productError && <p className="text-xs font-black text-rose-700">{productError} أعد البحث قبل إنشاء كيان جديد.</p>}
                                </section>

                                <section className="space-y-3 rounded-2xl border border-sky-200 bg-sky-50/60 p-3" data-testid="snapchat-management-expected-outcome">
                                    <div>
                                        <h3 className="text-sm font-black text-slate-900">النتيجة المتوقعة للقياس</h3>
                                        <p className="mt-1 text-[11px] font-bold text-slate-500">هذه أهداف تسجل للمقارنة بعد 24 و72 و168 ساعة، وليست قاعدة آلية لرفع الميزانية أو إيقافها.</p>
                                    </div>
                                    <div className="grid gap-3 md:grid-cols-2">
                                        <SelectField label="اتجاه المبيعات المتوقع" value={form.salesDirection} onChange={(value) => field("salesDirection", value)} testId="snapchat-management-sales-direction">
                                            {MEASURABLE_DIRECTIONS.map(([value, label]) => <option key={value} value={value} label={label} />)}
                                        </SelectField>
                                        <SelectField label="اتجاه مكسب المساهمة المتوقع" value={form.contributionProfitDirection} onChange={(value) => field("contributionProfitDirection", value)} testId="snapchat-management-profit-direction">
                                            {MEASURABLE_DIRECTIONS.map(([value, label]) => <option key={value} value={value} label={label} />)}
                                        </SelectField>
                                    </div>
                                </section>

                                <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                                    {isUpdate && <TextField label="معرّف الكيان المطلوب تعديله" value={form.targetId} onChange={(value) => field("targetId", value)} required dir="ltr" />}
                                    {showsParent && <TextField label={form.action.startsWith("ad_squad.") ? "معرّف الحملة الأب" : "معرّف المجموعة الأب"} value={form.parentId} onChange={(value) => field("parentId", value)} required dir="ltr" />}
                                    {!isUpdate && <TextField label="الاسم" value={form.name} onChange={(value) => field("name", value)} required />}
                                    {isUpdate && <TextField label="اسم جديد (اختياري)" value={form.name} onChange={(value) => field("name", value)} />}
                                    {form.action === "campaign.create" && <TextField label="بداية الحملة" value={form.startTime} onChange={(value) => field("startTime", value)} type="datetime-local" required dir="ltr" />}
                                    {form.action === "campaign.create" && <SelectField label="الهدف" value={form.objective} onChange={(value) => field("objective", value)}><option value="SALES">المبيعات</option><option value="TRAFFIC">الزيارات</option><option value="LEADS">العملاء المحتملون</option><option value="AWARENESS_AND_ENGAGEMENT">الوعي والتفاعل</option><option value="APP_PROMOTION">التطبيق</option></SelectField>}
                                    {showsBudget && <TextField label={`الميزانية اليومية (${selectedAccount?.currency || "عملة الحساب"})${isUpdate ? " · اتركها فارغة دون تغيير" : ""}`} value={form.dailyBudget} onChange={(value) => field("dailyBudget", value)} type="number" required={!isUpdate} dir="ltr" />}
                                    {form.action === "ad_squad.create" && <TextField label="رمز الدولة" value={form.country} onChange={(value) => field("country", value)} required dir="ltr" />}
                                    {form.action === "ad_squad.create" && <SelectField label="هدف التحسين" value={form.optimizationGoal} onChange={(value) => field("optimizationGoal", value)} testId="snapchat-management-optimization-goal"><option value="PIXEL_PURCHASE">الشراء · PIXEL_PURCHASE</option><option value="SWIPES">السحب/النقر · SWIPES</option><option value="LANDING_PAGE_VIEW">زيارة صفحة الهبوط · LANDING_PAGE_VIEW</option><option value="IMPRESSIONS">الظهور · IMPRESSIONS</option></SelectField>}
                                    {form.action === "creative.create" && <TextField label="Media ID" value={form.mediaId} onChange={(value) => field("mediaId", value)} required dir="ltr" />}
                                    {form.action === "creative.create" && <TextField label="Public Profile ID" value={form.profileId} onChange={(value) => field("profileId", value)} required dir="ltr" />}
                                    {form.action === "creative.create" && <TextField label="العنوان · 34 حرفًا" value={form.headline} onChange={(value) => field("headline", value)} required />}
                                    {form.action === "creative.create" && <SelectField label="نوع الإبداع" value={form.creativeType} onChange={(value) => field("creativeType", value)}><option value="SNAP_AD">SNAP_AD</option><option value="WEB_VIEW">WEB_VIEW</option></SelectField>}
                                    {form.action === "creative.create" && form.creativeType === "WEB_VIEW" && <TextField label="رابط الوجهة" value={form.destinationUrl} onChange={(value) => field("destinationUrl", value)} required dir="ltr" />}
                                    {form.action === "creative.create" && form.creativeType === "WEB_VIEW" && <SelectField label="زر الدعوة" value={form.callToAction} onChange={(value) => field("callToAction", value)}><option value="SHOP_NOW">SHOP_NOW</option><option value="ORDER_NOW">ORDER_NOW</option><option value="VIEW">VIEW</option><option value="MORE">MORE</option></SelectField>}
                                    {form.action === "ad.create" && <TextField label="Creative ID" value={form.creativeId} onChange={(value) => field("creativeId", value)} required dir="ltr" />}
                                    {form.action === "ad.create" && <SelectField label="نوع الإعلان" value={form.adType} onChange={(value) => field("adType", value)}><option value="SNAP_AD">SNAP_AD</option><option value="REMOTE_WEBPAGE">REMOTE_WEBPAGE</option><option value="STORY">STORY</option><option value="COLLECTION">COLLECTION</option></SelectField>}
                                    {showsStatus && <SelectField label="الحالة" value={form.status} onChange={(value) => field("status", value)}><option value="">بدون تغيير</option><option value="PAUSED">إيقاف PAUSED</option><option value="ACTIVE" disabled={!readiness?.activation_enabled}>تشغيل ACTIVE</option></SelectField>}
                                </div>

                                {activeRequested && (
                                    <label className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-black text-rose-900">
                                        <input type="checkbox" checked={form.activationAcknowledged} onChange={(event) => field("activationAcknowledged", event.target.checked)} className="mt-0.5" required />
                                        أقرّ أن التشغيل قد يبدأ صرفًا فعليًا بعد الاعتماد والتنفيذ، وأن الميزانية والحالة تمت مراجعتهما.
                                    </label>
                                )}

                                <div className="grid gap-3 md:grid-cols-2">
                                    <label className="block text-xs font-black text-slate-600">
                                        ملاحظة أو سياق من المستخدم (اختياري)
                                        <textarea
                                            value={form.userContextNote}
                                            onChange={(event) => field("userContextNote", event.target.value)}
                                            rows={3}
                                            maxLength={500}
                                            className="mt-1 w-full rounded-xl border border-slate-200 bg-white p-3 text-xs outline-none focus:border-amber-400"
                                            data-testid="snapchat-management-user-context"
                                        />
                                        <span className="mt-1 block text-[10px] font-bold text-slate-400">تُحفظ كاقتراح بشري غير متحقق، بثقة صفر، ولا يستخدمها ميزان أساسًا للقرار.</span>
                                    </label>
                                    <label className="block text-xs font-black text-slate-600">
                                        سبب تجاوز اتجاه حديث (اختياري)
                                        <textarea
                                            value={form.trendOverrideReason}
                                            onChange={(event) => field("trendOverrideReason", event.target.value)}
                                            rows={3}
                                            maxLength={500}
                                            placeholder="مثال: لم أعتمد تحسن آخر يومين لأن البيانات غير مكتملة"
                                            className="mt-1 w-full rounded-xl border border-slate-200 bg-white p-3 text-xs outline-none focus:border-amber-400"
                                            data-testid="snapchat-management-trend-override"
                                        />
                                        <span className="mt-1 block text-[10px] font-bold text-slate-400">شرح مستقل للتوثيق فقط؛ لا يحوّل الاتجاه القصير إلى قاعدة ثابتة.</span>
                                    </label>
                                </div>

                                <div className="grid gap-3 md:grid-cols-2">
                                    <TextField label="سبب العملية" value={form.reason} onChange={(value) => field("reason", value)} required />
                                    <label className="block text-xs font-black text-slate-600">حقول Snapchat إضافية بصيغة JSON (اختياري)
                                        <textarea value={form.advancedJson} onChange={(event) => field("advancedJson", event.target.value)} dir="ltr" rows={3} className="mt-1 w-full rounded-xl border border-slate-200 bg-white p-3 font-mono text-xs outline-none focus:border-amber-400" />
                                    </label>
                                </div>

                                <div className="flex flex-wrap items-center justify-between gap-3">
                                    <p className="text-[11px] font-bold text-slate-500">الزر التالي ينشئ معاينة فقط؛ لا يكتب في Snapchat.</p>
                                    <button type="submit" disabled={busy || !form.accountId || !actionAllowed || (requiresProduct && !form.productId)} className="min-h-11 rounded-xl bg-amber-300 px-5 text-sm font-black text-slate-950 hover:bg-amber-200 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400" data-testid="snapchat-management-create-preview">
                                        إنشاء معاينة آمنة
                                    </button>
                                </div>
                            </form>

                            <ProposalPreview proposal={activeProposal} readiness={readiness} busy={busy} onApprove={approve} onExecute={execute} onRollback={rollback} />

                            {proposals.length > 0 && (
                                <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                    <div className="mb-3 flex items-center justify-between gap-3">
                                        <h3 className="text-sm font-black">سجل العمليات الأخير</h3>
                                        <button type="button" onClick={load} disabled={loading || busy} className="inline-flex items-center gap-1 text-xs font-black text-emerald-700"><ArrowClockwise size={16} /> تحديث</button>
                                    </div>
                                    <div className="space-y-2">
                                        {proposals.map((proposal) => (
                                            <button key={proposal.proposal_id} type="button" onClick={() => setActiveProposal(proposal)} className="flex w-full flex-wrap items-center gap-2 rounded-xl bg-slate-50 p-3 text-right text-xs hover:bg-slate-100">
                                                <span className="font-black">{ACTION_LABELS[proposal.action] || proposal.action}</span>
                                                <span className="text-slate-500">{STATUS_LABELS[proposal.status] || proposal.status}</span>
                                                <span className="mr-auto font-mono text-[10px] text-slate-400">{proposal.proposal_id.slice(0, 8)}</span>
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </>
                    )}
                </div>
            )}
        </section>
    );
}
