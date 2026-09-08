import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
    clearSnapchatManagementPreviewResume,
    createSnapchatManagementProposal,
    diagnoseSnapchatManagementPixels,
    executeSnapchatManagementProposal,
    getSnapchatManagementPreviewResume,
    getSnapchatManagementReadiness,
    listSnapchatManagementProposals,
    managementError,
    microToNativeAmount,
    nativeAmountToMicro,
    pollSnapchatManagementProposal,
    reconcileSnapchatManagementProposal,
    resumeSnapchatManagementProposal,
    rollbackSnapchatManagementProposal,
    snapchatBidLabel,
    snapchatFinancialFieldReady,
    snapchatFinancialSettingsReady,
} from "../../services/snapchatCampaignManagement";
import { listProductsV2 } from "../../services/mezanProductsV2";
import { useOptionalAuth } from "../../context/AuthContext";

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
const BID_AMOUNT_STRATEGIES = new Set([
    "TARGET_COST",
    "LOWEST_COST_WITH_MAX_BID",
]);
const VERIFIED_CONTINUATIONS = {
    "campaign.create": {
        action: "ad_squad.create",
        label: "إنشاء مجموعة داخل هذه الحملة",
        testId: "snapchat-management-continue-ad-squad",
    },
    "ad_squad.create": {
        action: "ad.create",
        label: "إنشاء إعلان داخل هذه المجموعة",
        testId: "snapchat-management-continue-ad",
    },
};

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

export function initialForm({ action = "campaign.create", selectedCampaign, selectedAdSquad, selectedAd } = {}) {
    const targetId = action === "campaign.update"
        ? selectedCampaign?.campaign_id || ""
        : action === "ad_squad.update"
            ? selectedAdSquad?.ad_squad_id || ""
            : action === "ad.update"
                ? selectedAd?.ad_id || ""
                : "";
    const providerTargetId = action === "campaign.update"
        ? selectedCampaign?.provider_campaign_id || ""
        : action === "ad_squad.update"
            ? selectedAdSquad?.provider_ad_squad_id || ""
            : "";
    return {
        action,
        accountId: "",
        targetId,
        providerTargetId,
        parentId: action.startsWith("ad.")
            ? selectedAdSquad?.ad_squad_id || selectedAd?.ad_squad_id || ""
            : action.startsWith("ad_squad.")
                ? selectedCampaign?.campaign_id || ""
                : "",
        providerParentId: action.startsWith("ad_squad.")
            ? selectedCampaign?.provider_campaign_id || ""
            : "",
        name: "",
        startTime: localStartTime(),
        dailyBudget: action.endsWith(".update") ? "" : "50",
        bidAmount: "",
        bidStrategy: "",
        objective: "SALES",
        country: "sa",
        optimizationGoal: "PIXEL_PURCHASE",
        pixelId: "",
        conversionWindow: "SWIPE_28DAY_VIEW_1DAY",
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

function TextField({ label, value, onChange, type = "text", required = false, placeholder = "", dir = "rtl", testId, readOnly = false, disabled = false }) {
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
                readOnly={readOnly}
                disabled={disabled}
                onChange={(event) => onChange(event.target.value)}
                className={`${inputClass()} disabled:cursor-not-allowed disabled:bg-slate-100 read-only:bg-slate-100`}
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

function mergeAdvanced(payload, source, protectedFields = []) {
    const parsed = JSON.parse(source || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("الحقول الإضافية يجب أن تكون كائن JSON.");
    }
    const protectedOverride = protectedFields.find((field) => Object.prototype.hasOwnProperty.call(parsed, field));
    if (protectedOverride) {
        throw new Error(`استخدم الحقل المنفصل لـ ${protectedOverride}؛ لا يمكن تمريره من JSON الإضافي.`);
    }
    return { ...payload, ...parsed };
}

function providerValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function timestamp(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("ar-SA");
}

function proofBoolean(value) {
    if (value === true) return "true";
    if (value === false) return "false";
    return "غير متاح";
}

function currentMicro(settings, field) {
    if (settings?.quality?.settings_status !== "settings_complete") {
        return { raw: "غير متاح — فشل جلب الإعدادات", converted: "غير متاح — فشل جلب الإعدادات" };
    }
    const rawValue = settings?.[field];
    if (rawValue === null || rawValue === undefined || rawValue === "") {
        return { raw: "غير متاح — فشل جلب الإعدادات", converted: "غير متاح — فشل جلب الإعدادات" };
    }
    const raw = Number(rawValue);
    if (!Number.isFinite(raw)) {
        return { raw: "غير متاح — فشل جلب الإعدادات", converted: "غير متاح — فشل جلب الإعدادات" };
    }
    const currency = String(settings?.account_currency || "").toUpperCase();
    if (currency === "USD") {
        const usd = raw / 1_000_000;
        return {
            raw: `${raw.toLocaleString("en-US")} micro`,
            converted: `${usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 })} USD`,
        };
    }
    return {
        raw: `${raw.toLocaleString("en-US")} micro`,
        converted: currency
            ? `${(raw / 1_000_000).toLocaleString("en-US", { maximumFractionDigits: 6 })} ${currency} · USD غير متاح`
            : "عملة الحساب غير متاحة · USD غير متاح",
    };
}

function proposalFinancialFields(proposal) {
    const fields = new Set([
        ...(proposal?.preview?.changed_fields || []),
        ...(proposal?.field_changes || []).map((item) => item?.field),
    ]);
    return ["daily_budget_micro", "bid_micro", "bid_strategy"].filter((field) => fields.has(field));
}

function proposalHasFinancialChanges(proposal) {
    return proposalFinancialFields(proposal).length > 0;
}

function proposalFinancialMetadataKnown(proposal) {
    return proposal?.field_changes_known === true
        || proposal?.preview_changed_fields_known === true
        || Array.isArray(proposal?.preview?.changed_fields);
}

export function proposalSettingsProofMatchesCurrent(proposal, settings) {
    const proof = proposal?.settings_proof;
    if (!proof || !settings) return false;
    const proposalTargetId = String(proposal?.target_id || "").trim();
    const proposalProviderId = String(
        proposal?.provider_target_id || proposal?.provider_entity_id || "",
    ).trim();
    const proposalAccountId = String(proposal?.account_id || "").trim();
    const proofUnifiedId = String(proof?.unified_entity_id || "").trim();
    const proofProviderId = String(proof?.provider_entity_id || "").trim();
    const proofAccountId = String(proof?.ad_account_id || "").trim();
    const currentUnifiedId = String(settings?.unified_entity_id || "").trim();
    const currentProviderId = String(settings?.provider_entity_id || "").trim();
    const currentAccountId = String(settings?.ad_account_id || "").trim();
    if (
        !proposalTargetId
        || !proposalProviderId
        || !proposalAccountId
        || proposalTargetId !== proofUnifiedId
        || proposalTargetId !== currentUnifiedId
        || proposalProviderId !== proofProviderId
        || proposalProviderId !== currentProviderId
        || proposalAccountId !== proofAccountId
        || proposalAccountId !== currentAccountId
    ) return false;
    const proofParentId = String(proof?.provider_parent_id || "").trim();
    const currentParentId = String(settings?.provider_parent_id || "").trim();
    return !proofParentId || proofParentId === currentParentId;
}

function changeSide(change, side) {
    const direct = change?.[side];
    if (direct !== undefined) return direct;
    return change?.[`${side}_raw`]
        ?? change?.[`${side}_micro`]
        ?? change?.[`${side}_value`]
        ?? null;
}

function formatAuditValue(change, side, accountCurrency) {
    const value = changeSide(change, side);
    if (value === null || value === undefined || value === "") return "غير متاح";
    const field = String(change?.field || "");
    if (["daily_budget_micro", "bid_micro"].includes(field)) {
        const raw = Number(value);
        const rawText = Number.isFinite(raw) ? `${raw.toLocaleString("en-US")} micro` : String(value);
        return String(accountCurrency || "").toUpperCase() === "USD" && Number.isFinite(raw)
            ? `${rawText} · ${(raw / 1_000_000).toLocaleString("en-US", { maximumFractionDigits: 6 })} USD`
            : rawText;
    }
    return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function CurrentSettingsCard({ action, settings, accountId }) {
    if (!action.endsWith(".update") || !["campaign.update", "ad_squad.update"].includes(action)) return null;
    const quality = settings?.quality || {};
    const settingsReady = snapchatFinancialSettingsReady(settings, accountId);
    const settingsComplete = quality.settings_status === "settings_complete";
    const budgetUnsupported = action === "campaign.update"
        && settingsComplete
        && settings?.daily_budget_availability === "unsupported_at_provider_level";
    const campaignBudgetUnavailable = settings?.daily_budget_unavailable_message_ar
        || "غير متاح من Snapchat على هذا المستوى";
    const budget = budgetUnsupported
        ? {
            raw: campaignBudgetUnavailable,
            converted: campaignBudgetUnavailable,
        }
        : currentMicro(settings, "daily_budget_micro", "daily_budget_usd");
    const bid = currentMicro(settings, "bid_micro", "bid_usd");
    const childBudget = currentMicro(settings, "ad_squads_daily_budget_micro", "ad_squads_daily_budget_usd");
    const effectiveCurrentBidStrategy = settingsComplete ? settings?.bid_strategy : null;
    const currentAutoBid = String(effectiveCurrentBidStrategy || "").toUpperCase() === "AUTO_BID";
    const activeCountAvailable = settingsComplete
        && settings?.active_ad_squads_availability === "available";
    const strategiesAvailable = settingsComplete
        && settings?.ad_squad_bid_strategies_availability === "available";
    const campaignStrategies = strategiesAvailable
        ? Array.isArray(settings?.ad_squad_bid_strategies)
            ? settings.ad_squad_bid_strategies.join("، ") || "لا توجد (0 Ad Squads)"
            : "لا توجد (0 Ad Squads)"
        : "غير متاح — فشل جلب الإعدادات";
    const currentProviderValue = (value) => settingsComplete
        ? providerValue(value)
        : "غير متاح — فشل جلب الإعدادات";
    return (
        <section className={`rounded-2xl border p-4 ${settingsReady ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50"}`} data-testid="snapchat-management-current-settings">
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                    <h3 className="text-sm font-black">الإعدادات الحالية المقروءة من Snapchat</h3>
                    <p className="mt-1 text-[11px] font-bold text-slate-600">فتح الشاشة قراءة فقط؛ لا ينشئ proposal ولا preview ولا كتابة provider.</p>
                </div>
                <span className="rounded-full bg-white px-3 py-1 text-[10px] font-black">{quality.settings_status || "settings_not_loaded"}</span>
            </div>
            <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
                <div><span className="block text-slate-500">Unified ID</span><code dir="ltr">{settings?.unified_entity_id || "—"}</code></div>
                <div><span className="block text-slate-500">Snapchat provider ID</span><code dir="ltr">{settings?.provider_entity_id || "غير متاح — فشل جلب الإعدادات"}</code></div>
                <div><span className="block text-slate-500">Snapchat Ad Account ID</span><code dir="ltr">{settings?.ad_account_id || "غير متاح — فشل جلب الإعدادات"}</code></div>
                <div><span className="block text-slate-500">عملة الحساب</span><strong>{settings?.account_currency || "غير متاحة"}</strong></div>
                <div><span className="block text-slate-500">mapping_status</span><strong>{settings?.mapping_status || (settings?.mapping_verified ? "verified" : "غير متاح")}</strong></div>
                <div><span className="block text-slate-500">identity_contract</span><code dir="ltr">{settings?.identity_contract?.name || "غير متاح"}</code></div>
                <div><span className="block text-slate-500">Unified ID == provider ID</span><strong>{proofBoolean(settings?.identity_contract?.ids_equal)}</strong></div>
                <div><span className="block text-slate-500">الحالة الحالية</span><strong>{currentProviderValue(settings?.status)}</strong></div>
                <div><span className="block text-slate-500">الميزانية اليومية الخام</span><strong dir="ltr">{budget.raw}</strong></div>
                <div><span className="block text-slate-500">الميزانية اليومية</span><strong dir="ltr">{budget.converted}</strong></div>
                {action === "campaign.update" && (
                    <>
                        <div><span className="block text-slate-500">مجموع ميزانيات Ad Squads الخام</span><strong dir="ltr">{childBudget.raw}</strong></div>
                        <div><span className="block text-slate-500">مجموع ميزانيات Ad Squads</span><strong dir="ltr">{childBudget.converted}</strong></div>
                        <div><span className="block text-slate-500">Ad Squads النشطة</span><strong>{activeCountAvailable ? (settings?.active_ad_squads ?? "—") : "غير متاح — فشل جلب الإعدادات"}</strong></div>
                        <div><span className="block text-slate-500">استراتيجيات المزايدة المتاحة</span><strong>{campaignStrategies}</strong></div>
                    </>
                )}
                {action === "ad_squad.update" && (
                    <>
                        {currentAutoBid ? (
                            <div data-testid="snapchat-management-current-bid-label"><span className="block text-slate-500">bid_micro</span><strong>غير مستخدم مع AUTO_BID</strong></div>
                        ) : (
                            <>
                                <div><span className="block text-slate-500">{snapchatBidLabel(effectiveCurrentBidStrategy)} الخام</span><strong dir="ltr">{bid.raw}</strong></div>
                                <div data-testid="snapchat-management-current-bid-label"><span className="block text-slate-500">{snapchatBidLabel(effectiveCurrentBidStrategy)}</span><strong dir="ltr">{bid.converted}</strong></div>
                            </>
                        )}
                        <div><span className="block text-slate-500">bid_strategy</span><strong>{currentProviderValue(settings?.bid_strategy)}</strong></div>
                        <div><span className="block text-slate-500">optimization_goal</span><strong>{currentProviderValue(settings?.optimization_goal)}</strong></div>
                        <div><span className="block text-slate-500">billing_event</span><strong>{currentProviderValue(settings?.billing_event)}</strong></div>
                        <div><span className="block text-slate-500">conversion_window</span><strong>{currentProviderValue(settings?.conversion_window)}</strong></div>
                    </>
                )}
                <div><span className="block text-slate-500">freshness</span><strong dir="ltr">{quality.freshness_seconds == null ? "غير متاح" : `${Number(quality.freshness_seconds).toLocaleString("en-US")} ثانية`}</strong></div>
                <div><span className="block text-slate-500">freshness threshold</span><strong dir="ltr">{quality.freshness_threshold_seconds == null ? "غير متاح" : `${Number(quality.freshness_threshold_seconds).toLocaleString("en-US")} ثانية`}</strong></div>
                <div><span className="block text-slate-500">سبب الجودة</span><strong>{quality.reason || (settingsReady ? "قراءة حديثة من Snapchat" : "غير متاح — فشل جلب الإعدادات")}</strong></div>
                <div><span className="block text-slate-500">settings_synced_at</span><strong>{timestamp(settings?.settings_synced_at)}</strong></div>
                <div><span className="block text-slate-500">provider_updated_at</span><strong>{timestamp(settings?.provider_updated_at)}</strong></div>
            </div>
        </section>
    );
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

function activePixel(pixel) {
    return String(pixel?.status || "").toUpperCase() === "ACTIVE"
        && String(pixel?.effective_status || "").toUpperCase() === "ACTIVE";
}

function solePixelId(readiness, accountId) {
    const account = readiness?.accounts?.find((item) => item.account_id === accountId);
    const activePixels = (account?.pixels || []).filter(activePixel);
    return activePixels.length === 1 ? activePixels[0].pixel_id : "";
}

function verifiedContinuationContext(proposal) {
    const providerEntityId = String(proposal?.provider_entity_id || "").trim();
    const verifiedEntityId = String(proposal?.verified_entity_id || "").trim();
    const readbackEntityId = String(proposal?.verification?.entity_id || "").trim();
    const accountId = String(proposal?.account_id || "").trim();
    const products = Array.isArray(proposal?.products) ? proposal.products : [];
    const product = products.length === 1 ? products[0] : null;
    const productId = String(product?.product_id || "").trim();
    if (
        proposal?.status !== "completed"
        || proposal?.provider_write_reached !== true
        || proposal?.provider_write_state !== "confirmed"
        || proposal?.provider_write_uncertain !== false
        || proposal?.verification?.verified !== true
        || !providerEntityId
        || providerEntityId !== verifiedEntityId
        || providerEntityId !== readbackEntityId
        || !accountId
        || products.length !== 1
        || !productId
    ) {
        return null;
    }
    return { accountId, product, productId, verifiedEntityId };
}

function buildProposal(form) {
    if (DELIVERY_CREATE_ACTIONS.has(form.action) && !form.productId) {
        throw new Error("اختر المنتج الذي سيعلن له قبل إنشاء كيان إعلاني جديد.");
    }
    const pixelOptimization = form.action === "ad_squad.create"
        && String(form.optimizationGoal || "").startsWith("PIXEL_");
    if (pixelOptimization && !form.pixelId) {
        throw new Error("اختر Snap Pixel المرتبط بالحساب قبل معاينة تحسين الشراء.");
    }
    if (
        form.action === "campaign.update"
        && form.dailyBudget
        && form.currentSettings?.daily_budget_availability === "unsupported_at_provider_level"
    ) {
        throw new Error("غير متاح من Snapchat على هذا المستوى");
    }
    const effectiveBidStrategy = form.bidStrategy || (
        form.currentSettings?.quality?.settings_status === "settings_complete"
            ? form.currentSettings?.bid_strategy
            : ""
    );
    if (
        form.action === "ad_squad.update"
        && form.bidAmount
        && !BID_AMOUNT_STRATEGIES.has(effectiveBidStrategy)
    ) {
        throw new Error("لا يقبل bid_micro مع استراتيجية المزايدة الحالية.");
    }
    const common = {
        action: form.action,
        account_id: form.accountId,
        target_id: form.targetId || null,
        parent_id: form.parentId || null,
        provider_target_id: form.providerTargetId || null,
        provider_parent_id: form.providerParentId || null,
        settings_proof: form.currentSettings ? {
            unified_entity_id: form.currentSettings.unified_entity_id || null,
            provider_entity_id: form.currentSettings.provider_entity_id || null,
            provider_parent_id: form.currentSettings.provider_parent_id || null,
            ad_account_id: form.currentSettings.ad_account_id || null,
            account_currency: form.currentSettings.account_currency || null,
            settings_synced_at: form.currentSettings.settings_synced_at || null,
            provider_updated_at: form.currentSettings.provider_updated_at || null,
            mapping_status: form.currentSettings.mapping_status || null,
            mapping_verified: form.currentSettings.mapping_verified === true,
            quality: {
                settings_status: form.currentSettings.quality?.settings_status || null,
                freshness_seconds: form.currentSettings.quality?.freshness_seconds ?? null,
                freshness_threshold_seconds: form.currentSettings.quality?.freshness_threshold_seconds ?? null,
                reason: form.currentSettings.quality?.reason || null,
                financial_controls_allowed: form.currentSettings.quality?.financial_controls_allowed === true,
                financial_field_controls: form.currentSettings.quality?.financial_field_controls || {},
            },
        } : {},
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
            delivery_constraint: "DAILY_BUDGET",
        };
        if (pixelOptimization) payload.pixel_id = form.pixelId;
        if (pixelOptimization) payload.conversion_window = form.conversionWindow;
    } else if (form.action === "ad_squad.update") {
        if (form.name) payload.name = form.name;
        if (form.dailyBudget) payload.daily_budget_micro = nativeAmountToMicro(form.dailyBudget);
        if (form.bidAmount) payload.bid_micro = nativeAmountToMicro(form.bidAmount);
        if (form.bidStrategy) payload.bid_strategy = form.bidStrategy;
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
    const protectedFields = ["campaign.create", "campaign.update", "ad_squad.create", "ad_squad.update"].includes(form.action)
        ? ["daily_budget_micro", "bid_micro", "bid_strategy"]
        : [];
    const mergedPayload = mergeAdvanced(payload, form.advancedJson, protectedFields);
    if (
        pixelOptimization
        && mergedPayload.pixel_id
        && String(mergedPayload.pixel_id).trim() !== form.pixelId
    ) {
        throw new Error("لا يمكن استبدال Pixel المختار من الحقول الإضافية.");
    }
    if (pixelOptimization) mergedPayload.pixel_id = form.pixelId;
    if (
        pixelOptimization
        && mergedPayload.conversion_window
        && String(mergedPayload.conversion_window).trim() !== form.conversionWindow
    ) {
        throw new Error("لا يمكن استبدال نافذة التحويل المختارة من الحقول الإضافية.");
    }
    if (pixelOptimization) mergedPayload.conversion_window = form.conversionWindow;
    return { ...common, payload: mergedPayload };
}

function ProposalPreview({ proposal, readiness, busy, governedSettingsReady = true, financialSettingsReady, onApprove, onExecute, onRollback, onReconcile, onContinue }) {
    if (!proposal?.proposal_id) return null;
    const preview = proposal.preview || {};
    const verifiedEntityId = proposal.verified_entity_id || "";
    const continuation = VERIFIED_CONTINUATIONS[proposal.action] || null;
    const continuationContext = verifiedContinuationContext(proposal);
    const continuationBlocked = proposal.status === "completed"
        && continuation
        && !continuationContext;
    const governedUpdate = ["campaign.update", "ad_squad.update"].includes(proposal?.action);
    const financialMetadataUnknown = governedUpdate && !proposalFinancialMetadataKnown(proposal);
    const financialProposal = governedUpdate && proposalHasFinancialChanges(proposal);
    const canApprove = proposal.status === "previewed" && proposal.confirm_token;
    const canExecute = proposal.status === "approved"
        && readiness?.execution_enabled
        && !financialMetadataUnknown
        && governedSettingsReady
        && (!financialProposal || financialSettingsReady);
    const canRollback = readiness?.execution_enabled && (
        proposal.status === "completed"
        || (proposal.status === "failed" && proposal.provider_write_reached)
    );
    const canReconcile = proposal.status === "failed"
        && proposal.provider_write_uncertain === true;
    const pixelEligibility = proposal.pixel_eligibility || {};
    const rereadVerified = proposal.verification?.verified === true
        || proposal.provider_reread?.verified === true
        || proposal.field_changes_metadata?.provider_reread_verified === true;
    const rereadMismatches = Array.isArray(proposal.verification?.mismatched_fields)
        ? proposal.verification.mismatched_fields
        : Array.isArray(proposal.provider_reread?.mismatched_fields)
            ? proposal.provider_reread.mismatched_fields
            : [];
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
                <div className="rounded-xl bg-white p-3"><span className="block text-slate-400">الميزانية اليومية</span><strong>{preview.daily_budget_micro !== null && preview.daily_budget_micro !== undefined ? `${Number(preview.daily_budget_micro).toLocaleString("en-US")} micro · ${microToNativeAmount(preview.daily_budget_micro).toLocaleString("en-US")} ${proposal.account_currency || "عملة الحساب"}` : "بدون تغيير"}</strong></div>
                <div className="rounded-xl bg-white p-3"><span className="block text-slate-400">الحقول</span><strong>{(preview.changed_fields || []).join("، ") || "—"}</strong></div>
            </div>
            <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4" data-testid="snapchat-management-audit-metadata">
                <div className="rounded-xl bg-white p-3"><span className="block text-slate-400">وقت العملية</span><strong>{timestamp(proposal.field_changes_metadata?.occurred_at || proposal.executed_at || proposal.created_at)}</strong></div>
                <div className="rounded-xl bg-white p-3"><span className="block text-slate-400">المنفذ</span><strong>{proposal.actor_name || proposal.actor_id || proposal.field_changes_metadata?.actor_id || "—"}</strong></div>
                <div className="rounded-xl bg-white p-3"><span className="block text-slate-400">provider entity ID</span><code dir="ltr">{proposal.provider_entity_id || proposal.provider_target_id || proposal.field_changes_metadata?.provider_entity_id || "—"}</code></div>
                <div className="rounded-xl bg-white p-3"><span className="block text-slate-400">إعادة القراءة</span><strong>{rereadMismatches.length ? `غير مطابقة: ${rereadMismatches.join("، ")}` : rereadVerified ? "مطابقة مؤكدة من Snapchat" : "غير مكتملة"}</strong></div>
                <div className="rounded-xl bg-white p-3"><span className="block text-slate-400">مصدر التحقق</span><strong>{proposal.verification?.source || "Snapchat provider re-read"}</strong></div>
                <div className="rounded-xl bg-white p-3"><span className="block text-slate-400">وقت التحقق</span><strong>{timestamp(proposal.verification?.verified_at)}</strong></div>
            </div>
            {Object.keys(proposal.provider_readback || {}).length > 0 && (
                <details className="mt-3 rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs" data-testid="snapchat-management-provider-readback">
                    <summary className="cursor-pointer font-black text-sky-900">القيمة المعاد قراءتها من Snapchat</summary>
                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all text-left font-mono text-[10px]" dir="ltr">{JSON.stringify(proposal.provider_readback, null, 2)}</pre>
                </details>
            )}
            {rereadMismatches.length > 0 && (
                <div className="mt-3 rounded-xl border border-rose-200 bg-rose-100 p-3 text-xs font-black text-rose-900" data-testid="snapchat-management-reread-mismatch">
                    فشل تحقق إعادة القراءة من Snapchat: {rereadMismatches.join("، ")}
                </div>
            )}
            {(proposal.field_changes || []).length > 0 && (
                <div className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-white" data-testid="snapchat-management-field-changes">
                    <table className="w-full text-right text-xs">
                        <thead className="bg-slate-50"><tr><th className="px-3 py-2">الحقل</th><th className="px-3 py-2">قبل</th><th className="px-3 py-2">بعد</th></tr></thead>
                        <tbody>{proposal.field_changes.map((change, index) => (
                            <tr key={`${change.field || "field"}:${index}`} className="border-t border-slate-100">
                                <td className="px-3 py-2 font-mono">{change.field || "—"}</td>
                                <td className="px-3 py-2 font-mono" dir="ltr">{formatAuditValue(change, "before", proposal.account_currency)}</td>
                                <td className="px-3 py-2 font-mono" dir="ltr">{formatAuditValue(change, "after", proposal.account_currency)}</td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            )}
            {financialMetadataUnknown && (
                <div className="mt-3 flex items-center gap-2 rounded-xl bg-rose-100 p-3 text-xs font-black text-rose-900" data-testid="snapchat-management-financial-metadata-blocked">
                    <WarningCircle size={20} weight="fill" /> بيانات الحقول المتغيرة غير مكتملة؛ التنفيذ ممنوع fail-closed.
                </div>
            )}
            {financialProposal && !financialSettingsReady && (
                <div className="mt-3 flex items-center gap-2 rounded-xl bg-rose-100 p-3 text-xs font-black text-rose-900" data-testid="snapchat-management-financial-execute-blocked">
                    <WarningCircle size={20} weight="fill" /> الإعدادات الحالية غير حديثة؛ التنفيذ المالي ممنوع حتى تكتمل إعادة القراءة.
                </div>
            )}
            {proposal?.action?.endsWith(".update") && !governedSettingsReady && (
                <div className="mt-3 flex items-center gap-2 rounded-xl bg-rose-100 p-3 text-xs font-black text-rose-900" data-testid="snapchat-management-targeted-settings-blocked">
                    إعدادات الكيان المستهدفة غير مكتملة أو غير موثقة؛ الاعتماد والتنفيذ محجوبان.
                </div>
            )}
            {verifiedEntityId && (
                <div className="mt-3 rounded-xl border border-emerald-200 bg-white p-3 text-xs" data-testid="snapchat-management-verified-entity">
                    <span className="block font-black text-emerald-800">معرّف Snapchat الموثق</span>
                    <code className="mt-1 block select-all break-all text-left font-mono text-sm font-black text-slate-900" dir="ltr">
                        {verifiedEntityId}
                    </code>
                    {proposal.parent_id && (
                        <span className="mt-2 block text-slate-500">
                            الكيان الأب: <code className="select-all font-mono" dir="ltr">{proposal.parent_id}</code>
                        </span>
                    )}
                </div>
            )}
            {proposal.creates_paused && (
                <div className="mt-3 flex items-center gap-2 rounded-xl bg-emerald-100 p-3 text-xs font-black text-emerald-900">
                    <PauseCircle size={20} weight="fill" /> سيُنشأ الكيان متوقفًا؛ لا يبدأ صرف بمجرد التنفيذ.
                </div>
            )}
            {pixelEligibility.verified === true && pixelEligibility.pixel_id && (
                <div className="mt-3 rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs text-sky-950" data-testid="snapchat-management-pixel-eligibility">
                    <span className="block font-black">أهلية Snap Pixel موثقة لهذه المجموعة</span>
                    <code className="mt-1 block select-all break-all text-left font-mono font-black" dir="ltr">
                        {pixelEligibility.pixel_id}
                    </code>
                    <span className="mt-1 block font-bold">
                        {pixelEligibility.optimization_goal || "PIXEL_PURCHASE"}
                        {pixelEligibility.conversion_window ? ` · ${pixelEligibility.conversion_window}` : ""}
                    </span>
                </div>
            )}
            {verifiedEntityId && (
                <div className="mt-3 flex items-center gap-2 rounded-xl bg-emerald-100 p-3 text-xs font-black text-emerald-900" data-testid="snapchat-management-verification-confirmed">
                    <CheckCircle size={20} weight="fill" /> تحقق ميزان من الكيان بعد قراءته مرة أخرى من Snapchat.
                </div>
            )}
            {continuationBlocked && (
                <div className="mt-3 flex items-center gap-2 rounded-xl bg-rose-100 p-3 text-xs font-black text-rose-900" data-testid="snapchat-management-verified-id-blocked">
                    <WarningCircle size={20} weight="fill" /> لم يثبت ميزان تطابق معرّف الكيان؛ لا تنشئ مستوى تابعًا حتى تكتمل المصالحة.
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
                    <button type="button" disabled={busy || !governedSettingsReady} onClick={onApprove} className="min-h-10 rounded-xl bg-slate-950 px-4 text-xs font-black text-white disabled:opacity-50" data-testid="snapchat-management-approve">
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
                {canReconcile && (
                    <button type="button" disabled={busy} onClick={onReconcile} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-sky-200 bg-white px-4 text-xs font-black text-sky-800 disabled:opacity-50" data-testid="snapchat-management-reconcile">
                        <ArrowClockwise size={18} weight="bold" /> مصالحة آمنة (قراءة فقط)
                    </button>
                )}
                {continuation && continuationContext && (
                    <button
                        type="button"
                        disabled={busy}
                        onClick={() => onContinue(continuation.action, proposal)}
                        className="inline-flex min-h-10 items-center gap-2 rounded-xl bg-sky-700 px-4 text-xs font-black text-white disabled:opacity-50"
                        data-testid={continuation.testId}
                    >
                        <CheckCircle size={18} weight="fill" /> {continuation.label}
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
    selectedAd = null,
    currentSettings = null,
    initialAction = null,
    initiallyExpanded = false,
    targetedSettingsVerified = true,
    onChanged,
}) {
    // The application always supplies AuthProvider. Component tests and other
    // read-only embeddings may intentionally render this panel in isolation;
    // keep those surfaces non-owner instead of crashing before they can render.
    const user = useOptionalAuth()?.user;
    const ownerId = String(user?.id || "").trim();
    const defaultAction = entityLevel === "ads"
        ? "ad.create"
        : entityLevel === "ad_squads"
            ? "ad_squad.create"
            : "campaign.create";
    const preferredAction = ACTION_LABELS[initialAction] ? initialAction : defaultAction;
    const [expanded, setExpanded] = useState(initiallyExpanded);
    const [readiness, setReadiness] = useState(null);
    const [proposals, setProposals] = useState([]);
    const [form, setForm] = useState(() => ({
        ...initialForm({ action: preferredAction, selectedCampaign, selectedAdSquad, selectedAd }),
        currentSettings,
    }));
    const [productQuery, setProductQuery] = useState("");
    const [catalogProducts, setCatalogProducts] = useState([]);
    const [selectedCatalogProduct, setSelectedCatalogProduct] = useState(null);
    const [productsLoaded, setProductsLoaded] = useState(false);
    const [productsLoading, setProductsLoading] = useState(false);
    const [productError, setProductError] = useState("");
    const [activeProposal, setActiveProposal] = useState(null);
    const [loading, setLoading] = useState(false);
    const [operationBusy, setOperationBusy] = useState(false);
    const [resumeBusy, setResumeBusy] = useState(false);
    const [previewPending, setPreviewPending] = useState(false);
    const [pixelDiscoveryBusy, setPixelDiscoveryBusy] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const operationBusyRef = useRef(false);

    function beginOperation() {
        operationBusyRef.current = true;
        setOperationBusy(true);
    }

    function finishOperation() {
        operationBusyRef.current = false;
        setOperationBusy(false);
    }

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
            setForm((current) => {
                const nextAccountId = nextReadiness.accounts.some((item) => item.account_id === current.accountId)
                    ? current.accountId
                    : nextReadiness.accounts.find((item) => item.account_id === accountId)?.account_id
                        || nextReadiness.accounts[0]?.account_id
                        || "";
                const selectedPixels = nextReadiness.accounts.find(
                    (item) => item.account_id === nextAccountId,
                )?.pixels || [];
                return {
                    ...current,
                    accountId: nextAccountId,
                    pixelId: selectedPixels.some((item) => item.pixel_id === current.pixelId)
                        ? current.pixelId
                        : selectedPixels.length === 1 ? selectedPixels[0].pixel_id : "",
                };
            });
            return nextReadiness;
        } catch (loadError) {
            setError(managementError(loadError, "تعذّر فحص جاهزية إدارة Snapchat."));
            return null;
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
        if (!expanded || !ownerId || operationBusyRef.current) return;
        setPreviewPending(Boolean(getSnapchatManagementPreviewResume(ownerId)));
    }, [expanded, ownerId]);


    useEffect(() => {
        if (!expanded) {
            setResumeBusy(false);
        }
    }, [expanded]);

    useEffect(() => {
        setForm((current) => {
            if (current.action !== preferredAction) return current;
            const next = initialForm({ action: preferredAction, selectedCampaign, selectedAdSquad, selectedAd });
            return {
                ...next,
                ...retainedDecisionContext(current),
                currentSettings,
                accountId: current.accountId || accountId || "",
                pixelId: preferredAction === "ad_squad.create"
                    ? solePixelId(readiness, current.accountId || accountId || "")
                    : next.pixelId,
            };
        });
    }, [preferredAction, selectedCampaign, selectedAdSquad, selectedAd, currentSettings, accountId, readiness]);

    const selectedAccount = useMemo(
        () => readiness?.accounts?.find((item) => item.account_id === form.accountId) || null,
        [readiness, form.accountId],
    );

    function field(name, value) {
        setForm((current) => ({ ...current, [name]: value }));
    }

    function changeAccount(nextAccountId) {
        setForm((current) => ({
            ...current,
            accountId: nextAccountId,
            pixelId: solePixelId(readiness, nextAccountId),
        }));
    }

    function changeAction(action) {
        setActiveProposal(null);
        setNotice("");
        setError("");
        setForm((current) => {
            const next = initialForm({ action, selectedCampaign, selectedAdSquad, selectedAd });
            return {
                ...next,
                ...retainedDecisionContext(current),
                currentSettings: action.endsWith(".update") ? currentSettings : null,
                accountId: current.accountId,
                pixelId: action === "ad_squad.create"
                    ? solePixelId(readiness, current.accountId)
                    : next.pixelId,
            };
        });
    }

    function continueFromVerifiedProposal(nextAction, proposal) {
        const continuation = VERIFIED_CONTINUATIONS[proposal?.action];
        const context = verifiedContinuationContext(proposal);
        if (!continuation || continuation.action !== nextAction || !context) {
            setNotice("");
            setError("تعذّر اعتماد معرّف Snapchat الموثق مع الحساب والمنتج؛ حدّث سجل العملية ولا تستخدم رقمًا يدويًا.");
            return;
        }

        const { accountId: verifiedAccountId, product, productId, verifiedEntityId } = context;
        const productName = String(product?.product_name || "").trim();
        const matchedProduct = catalogProducts.find(
            (item) => productIdentity(item) === productId,
        ) || (productId ? {
            salla_product_id: productId,
            name: productName,
            variants: [],
        } : null);
        const expected = proposal.expected_outcome || {};

        setSelectedCatalogProduct(matchedProduct);
        setForm(() => ({
            ...initialForm({ action: nextAction, selectedCampaign, selectedAdSquad, selectedAd }),
            currentSettings: null,
            accountId: verifiedAccountId,
            pixelId: nextAction === "ad_squad.create"
                ? solePixelId(readiness, verifiedAccountId)
                : "",
            parentId: verifiedEntityId,
            productId,
            productVariantId: String(product?.product_variant_id || "").trim(),
            productName,
            salesDirection: MEASURABLE_DIRECTIONS.some(
                ([value]) => value === expected.sales_direction,
            ) ? expected.sales_direction : "increase",
            contributionProfitDirection: MEASURABLE_DIRECTIONS.some(
                ([value]) => value === expected.contribution_profit_direction,
            ) ? expected.contribution_profit_direction : "increase",
        }));
        setError("");
        setNotice("استخدم ميزان معرّف Snapchat الموثق تلقائيًا لبناء المستوى التالي.");
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

    async function resumePreview() {
        if (!ownerId || operationBusyRef.current) return;
        beginOperation();
        setResumeBusy(true);
        setError("");
        setNotice("يستأنف ميزان متابعة المعاينة السابقة بطلب صريح؛ لن ينفّذ أو يعتمد تلقائيًا.");
        try {
            const proposal = await resumeSnapchatManagementProposal({ ownerId });
            if (!proposal) {
                setPreviewPending(false);
                setNotice("");
                return;
            }
            setActiveProposal(proposal);
            setProposals((current) => [
                proposal,
                ...current.filter((row) => row.proposal_id !== proposal.proposal_id),
            ].slice(0, 12));
            setPreviewPending(false);
            setNotice("اكتملت المعاينة السابقة. راجعها ثم اعتمدها قبل التنفيذ.");
        } catch (resumeError) {
            const remainsPending = Boolean(getSnapchatManagementPreviewResume(ownerId));
            setPreviewPending(remainsPending);
            if (resumeError?.code === "snapchat_management_preview_poll_timeout") {
                setError("");
                setNotice(resumeError.message);
            } else {
                setNotice("");
                setError(managementError(resumeError, "تعذّر استئناف متابعة معاينة Snapchat."));
            }
        } finally {
            setResumeBusy(false);
            finishOperation();
        }
    }

    async function preview(event) {
        event.preventDefault();
        if (!governedSettingsReady) {
            setError("إعدادات الكيان المستهدفة غير مكتملة أو غير موثقة؛ لا يمكن إنشاء المعاينة.");
            return;
        }
        if (financialPreviewBlocked) {
            setNotice("");
            setError(campaignBudgetUnsupported
                ? "غير متاح من Snapchat على هذا المستوى"
                : invalidBidAmount
                    ? "لا يقبل bid_micro مع استراتيجية المزايدة الحالية."
                    : "لا يمكن إنشاء معاينة مالية: إعدادات Snapchat غير حديثة أو تغطيتها غير مكتملة.");
            return;
        }
        beginOperation();
        setError("");
        setPreviewPending(true);
        setNotice("بدأ تجهيز المعاينة في الخلفية. لا تغلق الصفحة ولا تنشئ معاينة أخرى.");
        try {
            const proposal = await createSnapchatManagementProposal(
                buildProposal(form),
                { ownerId },
            );
            setActiveProposal(proposal);
            setPreviewPending(false);
            setNotice("تم إنشاء معاينة فقط. راجعها ثم اعتمدها قبل التنفيذ.");
            setProposals((current) => [proposal, ...current.filter((row) => row.proposal_id !== proposal.proposal_id)].slice(0, 12));
        } catch (requestError) {
            const remainsPending = Boolean(
                getSnapchatManagementPreviewResume(ownerId),
            );
            setPreviewPending(remainsPending);
            if (requestError?.code === "snapchat_management_preview_poll_timeout") {
                setError("");
                setNotice(requestError.message);
            } else {
                setNotice("");
                setError(managementError(requestError, "تعذّر إنشاء معاينة العملية."));
            }
        } finally {
            finishOperation();
        }
    }

    async function approve() {
        beginOperation();
        setError("");
        try {
            const proposal = await approveSnapchatManagementProposal(
                activeProposal,
                { ownerId },
            );
            clearSnapchatManagementPreviewResume(ownerId);
            setPreviewPending(false);
            setActiveProposal(proposal);
            setNotice("تم اعتماد المعاينة. لم تصل أي كتابة إلى Snapchat بعد.");
        } catch (requestError) {
            setError(managementError(requestError, "تعذّر اعتماد المعاينة."));
        } finally {
            finishOperation();
        }
    }

    async function execute() {
        if (
            ["campaign.update", "ad_squad.update"].includes(activeProposal?.action)
            && !proposalFinancialMetadataKnown(activeProposal)
        ) {
            setNotice("");
            setError("لا يمكن التنفيذ: بيانات الحقول المتغيرة غير مكتملة.");
            return;
        }
        if (activeProposal?.action?.endsWith(".update") && proposalHasFinancialChanges(activeProposal) && !activeProposalFinancialSettingsReady) {
            setNotice("");
            setError("لا يمكن التنفيذ المالي: إعدادات Snapchat غير حديثة أو تغطيتها غير مكتملة.");
            return;
        }
        const proposalId = activeProposal.proposal_id;
        beginOperation();
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
            finishOperation();
        }
    }

    async function rollback() {
        beginOperation();
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
            finishOperation();
        }
    }

    async function reconcile() {
        const proposalId = activeProposal?.proposal_id;
        if (!proposalId) return;
        beginOperation();
        setError("");
        setNotice("يفحص ميزان Snapchat قراءةً فقط؛ لن يعيد الإنشاء ولن يغيّر حالة الإعلان.");
        try {
            const proposal = await reconcileSnapchatManagementProposal(proposalId);
            setActiveProposal(proposal);
            setProposals((current) => [
                proposal,
                ...current.filter((row) => row.proposal_id !== proposal.proposal_id),
            ].slice(0, 12));
            if (proposal.status === "completed" && proposal.verified_entity_id) {
                setNotice("عثر ميزان على الكيان نفسه واعتمد معرّفه بعد قراءته من Snapchat؛ لم تُنفذ كتابة جديدة.");
                onChanged?.();
            } else if (proposal.provider_write_uncertain === false) {
                setNotice("اكتملت المصالحة القراءة فقط ولم يجد ميزان كيانًا مطابقًا؛ يمكن إنشاء معاينة جديدة بعد مراجعة السبب.");
            } else {
                setNotice("");
                setError("لم تحسم القراءة النتيجة بأمان. لا تُعد الإنشاء حتى يزول التعارض.");
            }
        } catch (requestError) {
            setNotice("");
            setError(`${managementError(requestError, "تعذّرت المصالحة القراءة فقط.")} لا تُعد الإنشاء.`);
        } finally {
            finishOperation();
        }
    }

    async function discoverPixels() {
        beginOperation();
        setPixelDiscoveryBusy(true);
        setError("");
        setNotice("يفحص ميزان أصول Pixel من Snapchat قراءةً فقط؛ لن ينشئ حملة أو يغيّر الصرف.");
        try {
            await diagnoseSnapchatManagementPixels({ days: 7 });
            const refreshed = await load();
            const selectedPixels = refreshed?.accounts?.find(
                (item) => item.account_id === form.accountId,
            )?.pixels || [];
            if (selectedPixels.length < 1) {
                setNotice("");
                setError("اكتمل الفحص ولم يجد Snapchat Pixel مرتبطًا بالحساب المحدد.");
            } else {
                setNotice("اكتمل اكتشاف Pixel وتحديث قائمة المجموعة من Snapchat.");
            }
        } catch (requestError) {
            setNotice("");
            setError(managementError(requestError, "تعذّر اكتشاف Pixel من Snapchat."));
        } finally {
            setPixelDiscoveryBusy(false);
            finishOperation();
        }
    }

    const busy = operationBusy || resumeBusy;
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
    const pixelOptimization = form.action === "ad_squad.create"
        && String(form.optimizationGoal || "").startsWith("PIXEL_");
    const pixelSelectionMissing = pixelOptimization && !form.pixelId;
    const campaignBudgetUnsupported = form.action === "campaign.update"
        && currentSettings?.quality?.settings_status === "settings_complete"
        && currentSettings?.daily_budget_availability === "unsupported_at_provider_level";
    const effectiveBidStrategy = form.bidStrategy || (
        currentSettings?.quality?.settings_status === "settings_complete"
            ? currentSettings?.bid_strategy
            : ""
    );
    const invalidBidAmount = form.action === "ad_squad.update"
        && Boolean(form.bidAmount)
        && !BID_AMOUNT_STRATEGIES.has(effectiveBidStrategy);
    const bidAmountAllowed = form.action === "ad_squad.update"
        && BID_AMOUNT_STRATEGIES.has(effectiveBidStrategy);
    const financialFieldsRequested = [
        form.dailyBudget ? "daily_budget_micro" : null,
        form.bidAmount ? "bid_micro" : null,
        form.bidStrategy ? "bid_strategy" : null,
    ].filter(Boolean);
    const financialChangeRequested = isUpdate && financialFieldsRequested.length > 0;
    const financialSettingsReady = financialFieldsRequested.every(
        (fieldName) => snapchatFinancialFieldReady(currentSettings, fieldName, form.accountId),
    );
    const financialPreviewBlocked = (
        financialChangeRequested && !financialSettingsReady
    ) || invalidBidAmount || (campaignBudgetUnsupported && Boolean(form.dailyBudget));
    const governedSettingsReady = !["campaign.update", "ad_squad.update"].includes(form.action) || Boolean(
        targetedSettingsVerified
        && currentSettings?.quality?.settings_status === "settings_complete"
        && currentSettings?.mapping_verified === true
        && currentSettings?.ad_account_id === form.accountId
    );
    const activeProposalFinancialFields = proposalFinancialFields(activeProposal);
    const activeProposalSettingsBound = proposalSettingsProofMatchesCurrent(
        activeProposal,
        currentSettings,
    );
    const activeProposalFinancialSettingsReady = activeProposalSettingsBound
        && activeProposalFinancialFields.every((fieldName) => (
            snapchatFinancialFieldReady(
                activeProposal?.settings_proof,
                fieldName,
                activeProposal?.account_id,
            )
            && snapchatFinancialFieldReady(
                currentSettings,
                fieldName,
                activeProposal?.account_id,
            )
        ));
    const activeProposalGovernedSettingsReady = !activeProposal?.action?.endsWith(".update") || Boolean(
        governedSettingsReady && activeProposalSettingsBound
    );

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

                            <CurrentSettingsCard action={form.action} settings={currentSettings} accountId={form.accountId} />

                            <form onSubmit={preview} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4" data-testid="snapchat-management-form">
                                <div className="grid gap-3 md:grid-cols-2">
                                    <SelectField label="الحساب الإعلاني" value={form.accountId} onChange={changeAccount} disabled={isUpdate} testId="snapchat-management-account-select">
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
                                    {isUpdate && <TextField label="Unified ID للكيان المطلوب تعديله" value={form.targetId} onChange={() => {}} required readOnly dir="ltr" testId="snapchat-management-unified-target-id" />}
                                    {isUpdate && ["campaign.update", "ad_squad.update"].includes(form.action) && <TextField label="Snapchat provider ID الموثق" value={form.providerTargetId} onChange={() => {}} required readOnly dir="ltr" testId="snapchat-management-provider-target-id" />}
                                    {showsParent && <TextField label={form.action.startsWith("ad_squad.") ? "Unified ID للحملة الأب" : "معرّف المجموعة الأب"} value={form.parentId} onChange={(value) => field("parentId", value)} required readOnly={isUpdate} dir="ltr" testId="snapchat-management-parent-id" />}
                                    {isUpdate && form.action === "ad_squad.update" && <TextField label="Snapchat provider ID للحملة الأب" value={form.providerParentId} onChange={() => {}} required readOnly dir="ltr" testId="snapchat-management-provider-parent-id" />}
                                    {!isUpdate && <TextField label="الاسم" value={form.name} onChange={(value) => field("name", value)} required />}
                                    {isUpdate && <TextField label="اسم جديد (اختياري)" value={form.name} onChange={(value) => field("name", value)} />}
                                    {form.action === "campaign.create" && <TextField label="بداية الحملة" value={form.startTime} onChange={(value) => field("startTime", value)} type="datetime-local" required dir="ltr" />}
                                    {form.action === "campaign.create" && <SelectField label="الهدف" value={form.objective} onChange={(value) => field("objective", value)}><option value="SALES">المبيعات</option><option value="TRAFFIC">الزيارات</option><option value="LEADS">العملاء المحتملون</option><option value="AWARENESS_AND_ENGAGEMENT">الوعي والتفاعل</option><option value="APP_PROMOTION">التطبيق</option></SelectField>}
                                    {showsBudget && <TextField label={isUpdate ? `قيمة ميزانية يومية جديدة (${selectedAccount?.currency || "عملة الحساب"}) · اختياري` : `الميزانية اليومية (${selectedAccount?.currency || "عملة الحساب"})`} value={form.dailyBudget} onChange={(value) => field("dailyBudget", value)} type="number" required={!isUpdate} disabled={campaignBudgetUnsupported} dir="ltr" testId="snapchat-management-new-daily-budget" />}
                                    {form.action === "ad_squad.update" && <TextField label={bidAmountAllowed ? `قيمة ${snapchatBidLabel(effectiveBidStrategy)} جديدة (${selectedAccount?.currency || "عملة الحساب"}) · اختياري` : effectiveBidStrategy === "AUTO_BID" ? "bid_micro غير مستخدم مع AUTO_BID" : "Bid جديد غير متاح حتى تكتمل قراءة الاستراتيجية"} value={form.bidAmount} onChange={(value) => field("bidAmount", value)} type="number" disabled={!bidAmountAllowed} dir="ltr" testId="snapchat-management-new-bid" />}
                                    {form.action === "ad_squad.update" && <SelectField label="استراتيجية مزايدة جديدة · اختيارية" value={form.bidStrategy} onChange={(value) => field("bidStrategy", value)} testId="snapchat-management-new-bid-strategy"><option value="">بدون تغيير</option><option value="AUTO_BID">AUTO_BID</option><option value="TARGET_COST">TARGET_COST</option><option value="LOWEST_COST_WITH_MAX_BID">LOWEST_COST_WITH_MAX_BID</option></SelectField>}
                                    {form.action === "ad_squad.create" && <TextField label="رمز الدولة" value={form.country} onChange={(value) => field("country", value)} required dir="ltr" />}
                                    {form.action === "ad_squad.create" && <SelectField label="هدف التحسين" value={form.optimizationGoal} onChange={(value) => field("optimizationGoal", value)} testId="snapchat-management-optimization-goal"><option value="PIXEL_PURCHASE">الشراء · PIXEL_PURCHASE</option><option value="SWIPES">السحب/النقر · SWIPES</option><option value="LANDING_PAGE_VIEW">زيارة صفحة الهبوط · LANDING_PAGE_VIEW</option><option value="IMPRESSIONS">الظهور · IMPRESSIONS</option></SelectField>}
                                    {pixelOptimization && (
                                        <SelectField label="Snap Pixel · مطلوب لتحسين الشراء" value={form.pixelId} onChange={(value) => field("pixelId", value)} required testId="snapchat-management-pixel-select">
                                            <option value="" label={selectedAccount?.pixels?.length ? "اختر Pixel المرتبط بالحساب" : "لا يوجد Pixel مكتشف لهذا الحساب"} />
                                            {(selectedAccount?.pixels || []).map((pixel) => (
                                                <option
                                                    key={pixel.pixel_id}
                                                    value={pixel.pixel_id}
                                                    label={`${pixel.display_name} · ${pixel.pixel_id}${activePixel(pixel) ? "" : " · الأهلية تُفحص من Snapchat"}`}
                                                />
                                            ))}
                                        </SelectField>
                                    )}
                                    {pixelOptimization && (
                                        <SelectField label="نافذة التحويل · مطلوبة" value={form.conversionWindow} onChange={(value) => field("conversionWindow", value)} required testId="snapchat-management-conversion-window">
                                            <option value="SWIPE_28DAY_VIEW_1DAY">سحب 28 يوم + مشاهدة يوم · SWIPE_28DAY_VIEW_1DAY</option>
                                            <option value="SWIPE_7DAY">سحب 7 أيام · SWIPE_7DAY</option>
                                        </SelectField>
                                    )}
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

                                {pixelOptimization && (
                                    <div className={`rounded-xl border p-3 text-xs font-bold ${pixelSelectionMissing ? "border-rose-200 bg-rose-50 text-rose-800" : "border-sky-200 bg-sky-50 text-sky-900"}`} data-testid="snapchat-management-pixel-status">
                                        <div>
                                            {pixelSelectionMissing
                                                ? "لا يمكن إنشاء المعاينة: اختر Pixel تابعًا للحساب. سيتحقق ميزان من أهليته مباشرةً لدى Snapchat قبل المعاينة وقبل التنفيذ."
                                                : "Pixel محدد من أصول الحساب. سيثبت ميزان الارتباط والأهلية مباشرةً لدى Snapchat؛ الاكتشاف المحلي وحده لا يكفي."}
                                        </div>
                                        {pixelSelectionMissing && (
                                            <button
                                                type="button"
                                                disabled={busy}
                                                onClick={discoverPixels}
                                                className="mt-3 inline-flex min-h-10 items-center gap-2 rounded-xl bg-slate-950 px-4 text-xs font-black text-white disabled:cursor-not-allowed disabled:bg-slate-300"
                                                data-testid="snapchat-management-discover-pixels"
                                            >
                                                <ArrowClockwise size={18} className={pixelDiscoveryBusy ? "animate-spin" : ""} />
                                                {pixelDiscoveryBusy ? "جاري اكتشاف Pixel…" : "اكتشاف Pixel من Snapchat"}
                                            </button>
                                        )}
                                    </div>
                                )}

                                {campaignBudgetUnsupported && (
                                    <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-black text-rose-900" data-testid="snapchat-management-campaign-budget-unsupported">
                                        غير متاح من Snapchat على هذا المستوى
                                    </div>
                                )}
                                {invalidBidAmount && (
                                    <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-black text-rose-900" data-testid="snapchat-management-bid-strategy-blocked">
                                        لا يقبل bid_micro مع استراتيجية المزايدة الحالية.
                                    </div>
                                )}
                                {(!governedSettingsReady || financialPreviewBlocked) && !campaignBudgetUnsupported && !invalidBidAmount && (
                                    <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-black text-rose-900" data-testid="snapchat-management-financial-settings-blocked">
                                        غير متاح — فشل جلب الإعدادات. لا يمكن إنشاء معاينة أو تنفيذ مالي حتى تكتمل قراءة حديثة من Snapchat.
                                    </div>
                                )}

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
                                    <div className="flex flex-wrap items-center gap-2">
                                        {previewPending && !busy && (
                                            <button
                                                type="button"
                                                onClick={resumePreview}
                                                className="min-h-11 rounded-xl border border-amber-300 bg-white px-4 text-xs font-black text-amber-800"
                                                data-testid="snapchat-management-resume-preview"
                                            >
                                                استئناف متابعة المعاينة
                                            </button>
                                        )}
                                        <button type="submit" disabled={busy || previewPending || !form.accountId || !actionAllowed || (requiresProduct && !form.productId) || pixelSelectionMissing || !governedSettingsReady || financialPreviewBlocked} className="min-h-11 rounded-xl bg-amber-300 px-5 text-sm font-black text-slate-950 hover:bg-amber-200 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400" data-testid="snapchat-management-create-preview">
                                            إنشاء معاينة آمنة
                                        </button>
                                    </div>
                                </div>
                            </form>

                            <ProposalPreview proposal={activeProposal} readiness={readiness} busy={busy} governedSettingsReady={activeProposalGovernedSettingsReady} financialSettingsReady={activeProposalFinancialSettingsReady} onApprove={approve} onExecute={execute} onRollback={rollback} onReconcile={reconcile} onContinue={continueFromVerifiedProposal} />

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
                                                <span className="text-slate-500">{timestamp(proposal.executed_at || proposal.created_at)}</span>
                                                <span className="text-slate-500">المنفذ: {proposal.actor_name || proposal.actor_id || "—"}</span>
                                                <span className="font-mono text-[10px] text-slate-500" dir="ltr">Provider: {proposal.provider_entity_id || proposal.provider_target_id || "—"}</span>
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
