import { useEffect, useMemo, useRef, useState } from "react";
import {
    GOOGLE_BIDDING_LABELS, GOOGLE_CAMPAIGN_LABELS, GOOGLE_STATUS_LABELS,
    addGoogleVideo, createGoogleDraft, createGoogleProposal, executeGoogleProposal,
    generateGoogleAiDraft, getGoogleAssets, getGoogleDraft, getGoogleProducts, getGoogleProposal,
    googleCampaignCapabilities, googleCapabilityCanCreate, googleCapabilityReason,
    googleCapabilityType, googleErrorMessage, googleIdempotencyKey,
    googleProposalCanApprove, googleProposalNeedsReconcile, isGoogleRequestCancelled,
    lookupGoogleGeo, lookupGoogleLanguages, lookupGoogleOptions, reconcileGoogleProposal,
    updateGoogleDraft, uploadGoogleImage,
} from "../../services/googleAds";

const INPUT = "w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900 disabled:bg-slate-100 disabled:text-slate-500";
const BUTTON = "rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-bold disabled:opacity-40";
const PRIMARY = "rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-bold text-white disabled:opacity-40";
const MAX_GOOGLE_PRODUCTS = 8;
const ASSET_FIELDS = [
    ["image_assets", "صور أفقية", "IMAGE"], ["square_image_assets", "صور مربعة", "IMAGE"],
    ["portrait_image_assets", "صور عمودية", "IMAGE"], ["logo_assets", "شعار مربع", "IMAGE"],
    ["tall_portrait_image_assets", "صور عمودية 9:16", "IMAGE"],
    ["landscape_logo_assets", "شعار أفقي", "IMAGE"], ["video_assets", "فيديو YouTube", "YOUTUBE_VIDEO"],
];
const ACTION_LABELS = {
    create: "إنشاء حملة", create_campaign: "إنشاء حملة", rename: "تعديل الاسم", pause: "إيقاف", enable: "تفعيل",
    set_budget: "تعديل الميزانية", set_bidding: "تعديل المزايدة", set_targeting: "تعديل الاستهداف",
    update_ad: "تعديل محتوى الإعلان", remove: "حذف", duplicate: "نسخ الحملة",
    add_group: "إضافة مجموعة", add_ad: "إضافة إعلان", rename_group: "تعديل اسم المجموعة",
    pause_group: "إيقاف المجموعة", enable_group: "تفعيل المجموعة", set_group_bidding: "تعديل مزايدة المجموعة",
    remove_group: "حذف المجموعة", remove_ad: "حذف الإعلان", update_group_assets: "تعديل مواد المجموعة",
    set_conversion_goals: "تعديل أهداف التحويل",
    set_keywords: "تعديل الكلمات المفتاحية", set_channels: "تعديل قنوات العرض",
    pause_ad: "إيقاف الإعلان", enable_ad: "تفعيل الإعلان",
    upload_image: "إضافة صورة إعلان", upload_html5: "إضافة إعلان HTML5 جاهز", attach_video: "إضافة فيديو YouTube", upload_asset: "إضافة مادة إعلان", create_asset: "إضافة مادة إعلان", asset: "إضافة مادة إعلان",
};
const FORMAT_LABELS = {
    AUTO: "الصيغة المناسبة للنوع", RSA: "إعلان بحث متجاوب", DSA: "بحث ديناميكي",
    RESPONSIVE_DISPLAY: "إعلان عرض متجاوب", MULTI_ASSET: "صور ونصوص متعددة",
    UPLOADED_IMAGE: "إعلان صورة مرفوعة", UPLOADED_HTML5: "إعلان HTML5 جاهز",
    VIDEO: "إعلان فيديو", CAROUSEL: "بطاقات متتابعة", PRODUCT: "إعلان منتج",
    APP: "إعلان تطبيق", HOTEL: "إعلان فندق",
};
const CHANNEL_LABELS = {
    YOUTUBE_IN_STREAM: "YouTube أثناء المشاهدة", YOUTUBE_IN_FEED: "خلاصة YouTube",
    YOUTUBE_SHORTS: "YouTube Shorts", DISCOVER: "Discover", GMAIL: "Gmail", DISPLAY: "شبكة العرض",
};
const CONVERSION_LABELS = {
    PURCHASE: "الشراء", ADD_TO_CART: "إضافة للسلة", BEGIN_CHECKOUT: "بدء الدفع", PAGE_VIEW: "زيارة الصفحة",
    SIGNUP: "التسجيل", CONTACT: "التواصل", SUBMIT_LEAD_FORM: "إرسال نموذج", DOWNLOAD: "تنزيل",
    APP_INSTALL: "تثبيت التطبيق", DEFAULT: "التحويل الافتراضي", OTHER: "تحويل آخر",
};
const ORIGIN_LABELS = { WEBSITE: "الموقع", APP: "التطبيق", CALL_FROM_ADS: "اتصال من الإعلان", GOOGLE_HOSTED: "جوجل", STORE: "المتجر", YOUTUBE_HOSTED: "YouTube", LOCAL_SERVICES_ADS: "الخدمات المحلية" };
const TARGETING_LABELS = { geo_target_ids: "المواقع الجغرافية", language_ids: "اللغات", audience_ids: "الجمهور", user_list_ids: "جمهور إعادة الاستهداف" };
const SUMMARY_LABELS = {
    target_name: "الحملة أو العنصر", group: "محتوى المجموعة", initial_status: "الحالة عند الإنشاء",
    name: "الاسم", campaign_name: "اسم الحملة", campaign_type: "نوع الحملة", status: "الحالة",
    operation_status: "الحالة", budget_native: "الميزانية", budget_mode: "نوع الميزانية",
    budget: "الميزانية", bidding: "المزايدة", strategy: "استراتيجية المزايدة",
    target_cpa_native: "تكلفة التحويل المستهدفة", target_roas: "العائد المستهدف",
    max_cpc_native: "أقصى تكلفة للنقرة", start_date: "تاريخ البداية", end_date: "تاريخ النهاية",
    business_name: "اسم النشاط", headlines: "العناوين", descriptions: "الأوصاف", long_headlines: "العناوين الطويلة",
    final_urls: "روابط الوصول", final_url: "رابط الوصول", groups: "المجموعات", group_count: "عدد المجموعات",
    keywords: "الكلمات المفتاحية", assets: "مواد الإعلان", asset_count: "عدد المواد", type: "نوع المادة",
    ad_format: "صيغة الإعلان", shopping: "منتجات التسوق", app: "التطبيق", hotel: "الفنادق",
    search: "إعدادات البحث", ai_max: "AI Max للبحث", merchant_name: "حساب المنتجات",
    app_store: "متجر التطبيق", subtype: "نوع حملة التطبيق", feed_label: "تغذية المنتجات",
    channels: "قنوات العرض", ...TARGETING_LABELS,
    conversion_goals: "أهداف التحويل", cpc_bid_native: "تكلفة نقرة المجموعة", percent_cpc_bid: "نسبة نقرة الفنادق",
    search_themes: "مواضيع البحث", image_assets: "الصور الأفقية", square_image_assets: "الصور المربعة",
    portrait_image_assets: "الصور العمودية", tall_portrait_image_assets: "الصور 9:16", logo_assets: "الشعار",
    landscape_logo_assets: "الشعار الأفقي", video_assets: "فيديو YouTube", dimensions: "أبعاد الصورة", bytes: "حجم المادة",
};

export function googleDefaultGroup(type = "SEARCH", index = 1) {
    return {
        name: type === "PERFORMANCE_MAX" ? `مجموعة مواد ${index}` : `مجموعة إعلانية ${index}`,
        ad_format: type === "SEARCH" ? "RSA" : type === "DISPLAY" ? "RESPONSIVE_DISPLAY"
            : type === "SHOPPING" ? "PRODUCT" : type === "APP" ? "APP" : type === "HOTEL" ? "HOTEL" : "MULTI_ASSET",
        headlines: [], descriptions: [], long_headlines: [], final_urls: [], keywords: [],
        image_assets: [], square_image_assets: [], portrait_image_assets: [], logo_assets: [],
        landscape_logo_assets: [], video_assets: [], tall_portrait_image_assets: [], audience_ids: [], user_list_ids: [],
        product_offer_ids: [], hotel_ids: [], carousel_cards: [], channels: [], search_themes: [],
    };
}

export function googleDefaultDraft(accountId, type = "SEARCH") {
    return {
        account_id: accountId, campaign_type: type, name: "", budget_native: "", budget_mode: "DAILY",
        bidding: { strategy: type === "SHOPPING" ? "MANUAL_CPC" : type === "HOTEL" ? "PERCENT_CPC" : type === "APP" ? "TARGET_CPA" : "MAXIMIZE_CONVERSIONS" },
        geo_target_ids: [], language_ids: [], groups: [googleDefaultGroup(type)],
        eu_political_advertising: false, product_ids: [], product_offer_selections: [],
        ...(type === "SEARCH" ? { search: { target_search_network: true, target_content_network: false }, ai_max: { enabled: false, text_customization: false, final_url_expansion: false } } : {}),
        ...(type === "SHOPPING" ? { shopping: { merchant_id: "", feed_label: "", campaign_priority: 0 } } : {}),
        ...(type === "PERFORMANCE_MAX" ? { brand_guidelines_enabled: true } : {}),
        ...(type === "APP" ? { app: { subtype: "INSTALLS", app_id: "", app_store: "GOOGLE_APP_STORE", conversion_action_ids: [] } } : {}),
        ...(type === "HOTEL" ? { hotel: { hotel_center_id: "" } } : {}),
    };
}

function listItems(response) { return Array.isArray(response) ? response : response?.items || []; }
function mezanProductId(product) { return String(product.product_id || product.id || "").trim(); }
function googleOfferIsEligible(product) { return product.approved !== false && (product.approved === true || ["ELIGIBLE", "ELIGIBLE_LIMITED"].includes(product.status)); }
function creativeFieldsFor(type, format) {
    if (["SHOPPING", "HOTEL"].includes(type)) return [];
    if (type === "SEARCH") return format === "DSA" ? ["descriptions", "final_urls"] : ["headlines", "descriptions", "final_urls"];
    if (type === "APP") return ["headlines", "descriptions", "final_urls", "image_assets", "square_image_assets", "portrait_image_assets", "video_assets"];
    if (format === "UPLOADED_HTML5") return ["final_urls", "media_bundle_asset"];
    if (format === "UPLOADED_IMAGE") return ["final_urls", "image_assets"];
    if (type === "DEMAND_GEN") {
        const common = ["headlines", "descriptions", "final_urls", "business_name", "logo_assets"];
        if (format === "VIDEO") return [...common, "long_headlines", "image_assets", "video_assets"];
        if (["PRODUCT", "CAROUSEL"].includes(format)) return common;
        return [...common, "image_assets", "square_image_assets", "portrait_image_assets", "tall_portrait_image_assets"];
    }
    return ["headlines", "descriptions", "long_headlines", "final_urls", "business_name", "image_assets", "square_image_assets", "portrait_image_assets", "logo_assets", "landscape_logo_assets", "video_assets"].filter((field) => type !== "DISPLAY" || field !== "portrait_image_assets");
}
function assetId(asset) { return asset.resource_name || asset.id; }
function optionId(item) { return String(item.id || item.resource_name || item.criterion_id || item.code || ""); }
function optionLabel(item) { return item.name || item.label || item.title || item.canonical_name || "عنصر محفوظ"; }
function entityVerified(proposal) { return ["completed", "verified"].includes(proposal?.status) && proposal?.verified === true; }
function valueText(value, key) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "boolean") return value ? "نعم" : "لا";
    if (Array.isArray(value)) {
        if (key === "groups") return `${value.length} مجموعة · ${value.map((item) => item.name).filter(Boolean).join("، ")}`;
        if (key === "keywords") return value.map((item) => `${item.text} · ${{ EXACT: "مطابقة تامة", PHRASE: "مطابقة عبارة", BROAD: "مطابقة واسعة" }[item.match_type] || "مطابقة محددة"}${item.negative ? " · مستبعدة" : ""}`).join("\n");
        if (key === "conversion_goals") return value.map((item) => `${CONVERSION_LABELS[item.category] || "تحويل"} · ${ORIGIN_LABELS[item.origin] || "مصدر التحويل"} · ${item.biddable ? "داخل المزايدة" : "مستبعد من المزايدة"}`).join("\n");
        if (key.endsWith("_ids") || key.endsWith("_assets") || key === "assets") return `${value.length} عنصر مختار`;
        return value.map((item) => typeof item === "object" ? item.text || item.headline || item.name || "عنصر" : valueText(item, key)).join("، ") || "لا يوجد";
    }
    if (typeof value === "object") {
        if (key === "dimensions") return `${value.width} × ${value.height}`;
        return Object.entries(value).filter(([field]) => SUMMARY_LABELS[field]).map(([field, item]) => `${SUMMARY_LABELS[field]}: ${valueText(item, field)}`).join(" · ") || "إعدادات محفوظة في المعاينة";
    }
    if (String(value).startsWith("customers/")) return "عنصر مرتبط بالحساب المختار";
    return GOOGLE_CAMPAIGN_LABELS[value] || GOOGLE_BIDDING_LABELS[value] || GOOGLE_STATUS_LABELS[value]
        || FORMAT_LABELS[value] || CHANNEL_LABELS[value] || ({ DAILY: "يومية", TOTAL: "إجمالية", IMAGE: "صورة", MEDIA_BUNDLE: "إعلان HTML5 جاهز", YOUTUBE_VIDEO: "فيديو YouTube", INSTALLS: "تثبيت", ENGAGEMENT: "تفاعل", PRE_REGISTRATION: "تسجيل مسبق", GOOGLE_APP_STORE: "Google Play", APPLE_APP_STORE: "App Store" })[value] || String(value);
}

function localRasterPreviewUrl(value) {
    if (typeof value !== "string" || !/^blob:https?:\/\/[A-Za-z0-9.:-]+\/[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}$/.test(value)) return null;
    try { return new URL(value).origin === window.location.origin ? value : null; } catch { return null; }
}

function FrozenGoogleCreative({ creative, assets, label }) {
    return <section aria-label={label} className="mt-3 space-y-3 rounded-xl border border-slate-200 p-3">
        <h4 className="text-sm font-bold">{label}</h4>
        <div className="grid gap-3 sm:grid-cols-2">{["name", "status", "ad_format", "business_name", "headlines", "long_headlines", "descriptions", "final_urls", "keywords", "search_themes"].filter((field) => creative[field]?.length).map((field) => <div key={field} className="text-sm"><p className="font-bold">{SUMMARY_LABELS[field]}</p><p className="mt-1 whitespace-pre-wrap break-words text-slate-600">{valueText(creative[field], field)}</p></div>)}
        {[...ASSET_FIELDS, ["media_bundle_asset", "إعلان HTML5 جاهز"]].flatMap(([field, assetLabel]) => (Array.isArray(creative[field]) ? creative[field] : creative[field] ? [creative[field]] : []).map((reference, assetIndex) => { const asset = assets.find((item) => assetId(item) === reference); return <div key={`${field}:${reference}`} className="rounded-lg bg-slate-50 p-2">{asset?.preview_url && <img alt={asset.name || assetLabel} src={asset.preview_url} className="h-24 w-full rounded-lg object-contain" />}<p className="mt-1 text-xs font-bold">{assetLabel} · {asset?.name || `مادة محفوظة ${assetIndex + 1}`}</p></div>; }))}</div>
        {(creative.carousel_cards || []).map((card, index) => <FrozenGoogleCreative key={index} label={`بطاقة ${index + 1}`} assets={assets} creative={{ headlines: [card.headline], final_urls: [card.final_url], image_assets: card.image_asset ? [card.image_asset] : [], square_image_assets: card.square_image_asset ? [card.square_image_asset] : [], portrait_image_assets: card.portrait_image_asset ? [card.portrait_image_asset] : [] }} />)}
    </section>;
}

export function GoogleProposalReview({ proposal, summary, assets = [], productNames = {}, googleProducts = [], productMerchantId, materialPreview, approved, onApprovalChange, onExecute, onRefresh, onReconcile, onEdit, busy = false, executionStarted = false, canExecute = true }) {
    const safeMaterialPreview = localRasterPreviewUrl(materialPreview);
    const planned = { ...(proposal?.before?.name ? { target_name: proposal.before.name } : {}), ...(summary || {}), ...(proposal?.planned || {}) };
    if (["pause", "pause_group", "pause_ad"].includes(proposal?.action)) planned.status = "PAUSED";
    if (["enable", "enable_group", "enable_ad"].includes(proposal?.action)) planned.status = "ENABLED";
    if (["remove", "remove_group", "remove_ad"].includes(proposal?.action)) planned.status = "REMOVED";
    const frozenGroups = Array.isArray(planned.groups) ? planned.groups : planned.group ? [planned.group] : [];
    const frozenProductIds = planned.product_ids || planned.draft?.product_ids || [];
    const frozenOfferSelections = planned.product_offer_selections || planned.draft?.product_offer_selections || [];
    const frozenProducts = planned.products || [];
    const frozenProductOffers = planned.product_offers || [];
    const offerName = (pair, index) => {
        const frozenOffer = frozenProductOffers.find((item) => String(item.offer_id) === String(pair.offer_id) && String(item.merchant_id) === String(pair.merchant_id));
        if (frozenOffer?.name) return frozenOffer.name;
        const offer = googleProducts.find((item) => String(item.offer_id) === String(pair.offer_id) && String(item.merchant_id || productMerchantId || "") === String(pair.merchant_id));
        return offer?.title || offer?.name || `عرض جوجل محفوظ ${index + 1}`;
    };
    const rows = Object.entries(planned).filter(([key]) => SUMMARY_LABELS[key]);
    const canApprove = googleProposalCanApprove(proposal, executionStarted);
    const pending = googleProposalNeedsReconcile(proposal, executionStarted);
    const expiry = proposal?.expires_at ? new Date(proposal.expires_at).toLocaleString("ar-SA") : null;
    const tooEarly = proposal?.reconcile_after && Date.parse(proposal.reconcile_after) > Date.now();
    return <section data-testid="google-proposal-review" className="space-y-4 rounded-2xl border border-emerald-200 bg-white p-5" dir="rtl" aria-label="معاينة العملية المعتمدة">
        <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-black">{ACTION_LABELS[proposal?.action] || "معاينة العملية"}</h3><span className="rounded-lg bg-slate-100 px-3 py-1 text-xs font-bold">{GOOGLE_STATUS_LABELS[proposal?.status] || "جارٍ التحقق من الحالة"}</span></div>
        <p className="text-sm text-slate-600">{proposal?.account_name || "الحساب المختار"} · عملة التنفيذ: <strong>{proposal?.currency || "عملة الحساب"}</strong>{expiry && ` · انتهاء المعاينة: ${expiry}`}</p>
        <div className="overflow-x-auto rounded-xl border border-slate-200"><table className="w-full min-w-[450px] text-right text-sm"><thead className="bg-slate-50"><tr><th className="p-3">الإعداد</th><th className="p-3">قبل</th><th className="p-3">المطلوب</th></tr></thead><tbody>{rows.map(([key, value]) => <tr key={key} className="border-t border-slate-100"><th className="p-3 font-bold">{SUMMARY_LABELS[key]}</th><td className="p-3">{valueText(proposal?.before?.[key], key)}</td><td className="max-w-xl whitespace-pre-wrap break-words p-3">{valueText(value, key)}</td></tr>)}{!rows.length && <tr><td colSpan={3} className="p-4">{proposal?.summary || "تم تثبيت تفاصيل العملية في هذه المعاينة."}</td></tr>}</tbody></table></div>
        {frozenProductIds.length > 0 && <section className="space-y-3 rounded-xl border border-violet-200 bg-violet-50 p-4" aria-label="منتجات النسخة المعتمدة"><h4 className="text-sm font-bold">منتجات ميزان وعروض جوجل في هذه المعاينة</h4>{frozenProductIds.map((id, index) => {
            const offers = frozenOfferSelections.filter((pair) => pair.product_id === id);
            return <div key={id} className="rounded-lg bg-white p-3 text-sm"><p className="font-bold">{frozenProducts.find((item) => String(item.product_id) === String(id))?.name || productNames[id] || `منتج ميزان محفوظ ${index + 1}`}</p>{offers.length > 0 ? offers.map((pair, offerIndex) => <p key={`${pair.merchant_id}:${pair.offer_id}`} className="mt-1 text-slate-600">منتج جوجل المقابل: {offerName(pair, offerIndex)}</p>) : <p className="mt-1 text-xs text-slate-500">ارتباط بمحتوى المنتج في ميزان؛ لم يُختر عرض Merchant Center مقابل.</p>}</div>;
        })}<p className="text-xs text-slate-600">ارتباط المنتجات يحدد محتوى الحملة واختياراتها؛ لا يمثل مبيعات أو إيرادًا منسوبًا للإعلان.</p></section>}
        {frozenGroups.map((group, index) => <details key={index} className="rounded-xl border p-3" open><summary className="cursor-pointer text-sm font-bold">{group.name || `مجموعة ${index + 1}`} · المحتوى المعتمد</summary><FrozenGoogleCreative creative={{ ...group, name: group.ad_name, status: group.ad_status || group.status }} assets={assets} label={`الإعلان الرئيسي للمجموعة ${index + 1}`} />{(group.ads || []).map((ad, adIndex) => <FrozenGoogleCreative key={adIndex} creative={ad} assets={assets} label={`إعلان إضافي ${adIndex + 1} للمجموعة ${index + 1} · المحتوى المعتمد`} />)}</details>)}
        {safeMaterialPreview && <img src={safeMaterialPreview} alt="الصورة المحددة في المعاينة" className="max-h-64 w-full rounded-xl object-contain" />}
        {proposal?.reason && <p className="text-sm text-slate-600">سبب العملية: {proposal.reason}</p>}
        {proposal?.ai_source && <p className="rounded-xl bg-violet-50 p-3 text-xs leading-6 text-violet-900">توصية ذكاء ميزان من تحليل محفوظ للفترة {proposal.ai_source.date_from} ← {proposal.ai_source.date_to}. المعاينة لا تغيّر الحملة؛ التنفيذ يتبع اعتمادك لهذه النسخة.</p>}
        {(proposal?.warnings || []).map((warning, index) => <p key={index} className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{typeof warning === "string" ? warning : warning.message || warning.title}</p>)}
        {proposal?.financial_bound && <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">الميزانية المعتمدة: {proposal.financial_bound.maximum_native || proposal.financial_bound.budget_native || "كما في المعاينة"} {proposal.currency} · {proposal.financial_bound.period === "lifetime" || proposal.financial_bound.budget_mode === "TOTAL" ? "إجمالية لعمر الحملة" : "متوسط يومي وفق قواعد جوجل"}.</p>}
        {proposal?.safe_error?.message && <p role="alert" className="rounded-xl bg-rose-50 p-3 text-sm text-rose-800">{proposal.safe_error.message}</p>}
        {canApprove && <><label className="flex items-start gap-3 text-sm font-semibold"><input type="checkbox" className="mt-1 h-4 w-4" aria-label="اعتماد معاينة جوجل المحددة" disabled={busy || !canExecute} checked={approved} onChange={(event) => onApprovalChange(event.target.checked)} />أعتمد هذه النسخة المحددة والإعدادات ومواد الإعلان وعملة التنفيذ المعروضة.</label><div className="flex flex-wrap gap-2"><button type="button" className={PRIMARY} disabled={!approved || busy || !canExecute} onClick={onExecute}>اعتماد وتنفيذ في جوجل</button>{onEdit && <button type="button" className={BUTTON} disabled={busy} onClick={onEdit}>العودة للتعديل</button>}</div></>}
        {entityVerified(proposal) && <p role="status" className="rounded-xl bg-emerald-50 p-3 text-sm font-bold text-emerald-900">تأكدت الإعدادات بقراءة مستقلة من جوجل.{["create", "create_campaign"].includes(proposal.action) && " الحملة موقوفة؛ يمكن تفعيلها من إدارة الحملة بعد مراجعة جاهزيتها."}</p>}
        {pending && <p role="status" className="rounded-xl bg-amber-50 p-3 text-sm font-bold text-amber-900">تحتاج العملية قراءة النتيجة من جوجل. قد تكون نُفّذت بالفعل؛ استخدم تحديث الحالة أو التحقق لإكمالها.{proposal.reconcile_after && ` يتاح التحقق بعد ${new Date(proposal.reconcile_after).toLocaleTimeString("ar-SA")}.`}</p>}
        <div className="flex flex-wrap gap-2"><button type="button" className={BUTTON} disabled={busy} onClick={onRefresh}>تحديث حالة العملية</button>{pending && <button type="button" className={BUTTON} disabled={busy || tooEarly} onClick={onReconcile}>التحقق من النتيجة في جوجل</button>}</div>
    </section>;
}

function Field({ label, children, note }) { return <label className="block space-y-1.5 text-sm font-bold text-slate-800"><span>{label}</span>{children}{note && <span className="block text-xs font-normal leading-5 text-slate-500">{note}</span>}</label>; }
function TextList({ label, value = [], onChange, disabled, maxLength, note }) {
    return <Field label={label} note={note}><textarea className={INPUT} rows={Math.min(5, Math.max(3, value.length))} disabled={disabled} aria-label={label} value={value.join("\n")} onChange={(event) => onChange(event.target.value.split("\n"))} />{maxLength && <span className={`block text-xs ${value.some((line) => line.length > maxLength) ? "text-rose-700" : "text-slate-500"}`}>{value.filter((line) => line.trim()).length} نص · حتى {maxLength} حرف لكل سطر</span>}</Field>;
}
function MultiOptions({ label, items = [], value = [], onChange, disabled, empty = "لا توجد خيارات متاحة على هذا الحساب." }) {
    function toggle(id) { onChange(value.includes(id) ? value.filter((entry) => entry !== id) : [...value, id]); }
    return <fieldset className="space-y-2"><legend className="text-sm font-bold">{label}</legend><div className="max-h-44 space-y-2 overflow-y-auto rounded-xl border border-slate-200 p-3">{items.map((item) => { const id = optionId(item); return <label key={id} className="flex items-start gap-2 text-sm"><input type="checkbox" aria-label={`${label}: ${optionLabel(item)}`} checked={value.includes(id)} disabled={disabled || item.eligible === false} onChange={() => toggle(id)} /><span>{optionLabel(item)}{item.reason && <span className="block text-xs text-amber-800">{item.reason}</span>}</span></label>; })}{!items.length && <p className="text-xs text-slate-500">{empty}</p>}{value.filter((id) => !items.some((item) => optionId(item) === id)).map((id, index) => <div key={id} className="flex items-center justify-between gap-2 text-xs text-slate-600"><span>اختيار محفوظ {index + 1}</span><button type="button" disabled={disabled} onClick={() => toggle(id)}>إزالة</button></div>)}</div></fieldset>;
}

function ConversionGoalOptions({ items = [], value = [], onChange, disabled }) {
    function choose(item, biddable) {
        const next = items.map((entry) => {
            const saved = value.find((goal) => goal.category === entry.category && goal.origin === entry.origin);
            return { category: entry.category, origin: entry.origin, biddable: entry.category === item.category && entry.origin === item.origin ? biddable : saved?.biddable === true };
        });
        onChange(next);
    }
    return <fieldset className="space-y-3 rounded-2xl border p-4"><legend className="px-2 font-bold">أهداف التحويل</legend><p className="text-xs leading-6 text-slate-500">عند عدم تحديد أهداف، تتبع الحملة أهداف حساب جوجل الافتراضية. اختيار هدف يحدد التحويلات الداخلة في المزايدة لهذه الحملة.</p>{items.map((item) => <label key={`${item.category}:${item.origin}`} className="flex items-center gap-2 text-sm"><input type="checkbox" aria-label={`هدف جوجل: ${CONVERSION_LABELS[item.category] || item.label || "تحويل"} · ${ORIGIN_LABELS[item.origin] || "مصدر التحويل"}`} disabled={disabled} checked={value.some((goal) => goal.category === item.category && goal.origin === item.origin && goal.biddable)} onChange={(event) => choose(item, event.target.checked)} />{CONVERSION_LABELS[item.category] || item.label || "تحويل"} · {ORIGIN_LABELS[item.origin] || "مصدر التحويل"}{!value.length && item.biddable && <span className="text-xs text-slate-500">افتراضي للحساب</span>}</label>)}{!items.length && <p className="text-xs text-amber-800">لم تظهر أهداف تحويل صالحة للحساب. راجع تتبع التحويلات وربط الحساب.</p>}{value.length > 0 && <button type="button" className={BUTTON} disabled={disabled} onClick={() => onChange([])}>اتباع أهداف الحساب الافتراضية</button>}</fieldset>;
}

function KeywordsEditor({ value = [], onChange, disabled, suffix = "1", dsa = false }) {
    function edit(index, patch) { onChange(value.map((item, position) => position === index ? { ...item, ...patch } : item)); }
    return <fieldset className="space-y-2"><legend className="font-bold">الكلمات المفتاحية والمستبعدة</legend>{value.map((word, index) => <div key={index} className="grid grid-cols-[1fr_auto_auto_auto] items-center gap-2"><input aria-label={`كلمة جوجل ${suffix}-${index + 1}`} className={INPUT} disabled={disabled} value={word.text} onChange={(event) => edit(index, { text: event.target.value })} /><select aria-label={`مطابقة كلمة ${index + 1}`} className={INPUT} disabled={disabled} value={word.match_type} onChange={(event) => edit(index, { match_type: event.target.value })}><option value="EXACT">تامة</option><option value="PHRASE">عبارة</option><option value="BROAD">واسعة</option></select><label className="flex items-center gap-1 text-xs"><input type="checkbox" disabled={disabled || dsa} checked={word.negative} onChange={(event) => edit(index, { negative: event.target.checked })} />مستبعدة</label><button type="button" disabled={disabled} aria-label={`حذف كلمة ${index + 1}`} onClick={() => onChange(value.filter((_, position) => position !== index))}>×</button></div>)}<button type="button" className={BUTTON} disabled={disabled || value.length >= 500} onClick={() => onChange([...value, { text: "", match_type: "PHRASE", negative: dsa }])}>إضافة كلمة مفتاحية</button></fieldset>;
}

function AssetPicker({ label, kind, assets = [], value = [], onChange, disabled }) {
    const list = assets.filter((asset) => kind === "YOUTUBE_VIDEO" ? String(asset.type).includes("VIDEO") : String(asset.type).includes("IMAGE"));
    return <fieldset className="space-y-2"><legend className="text-sm font-bold">{label} · {value.length} مختار</legend><div className="grid max-h-56 grid-cols-2 gap-2 overflow-y-auto rounded-xl border border-slate-200 p-2 sm:grid-cols-3">{list.map((asset) => { const id = assetId(asset); const selected = value.includes(id); return <button key={id} type="button" disabled={disabled} aria-label={`${label}: ${asset.name || "مادة إعلان"}`} aria-pressed={selected} onClick={() => onChange(selected ? value.filter((entry) => entry !== id) : [...value, id])} className={`overflow-hidden rounded-xl border p-2 text-right disabled:opacity-50 ${selected ? "border-emerald-600 bg-emerald-50 ring-1 ring-emerald-600" : "border-slate-200"}`}>{asset.preview_url ? <img src={asset.preview_url} alt={asset.name || label} className="mb-2 h-20 w-full rounded-lg object-contain" loading="lazy" /> : <div className="mb-2 flex h-20 items-center justify-center rounded-lg bg-slate-100 text-xs text-slate-500">{kind === "YOUTUBE_VIDEO" ? "YouTube" : "صورة"}</div>}<span className="block truncate text-xs font-bold">{asset.name || "مادة إعلان"}</span>{asset.dimensions && <span className="block text-[11px] text-slate-500">{asset.dimensions.width} × {asset.dimensions.height}</span>}</button>; })}{!list.length && <p className="col-span-full p-2 text-xs text-slate-500">أضف {kind === "YOUTUBE_VIDEO" ? "فيديو YouTube" : "صورًا"} من مكتبة المواد أسفل النموذج.</p>}{value.filter((id) => !list.some((asset) => assetId(asset) === id)).map((id, index) => <button key={id} type="button" className="rounded-lg bg-emerald-50 p-2 text-xs" disabled={disabled} onClick={() => onChange(value.filter((entry) => entry !== id))}>مادة محفوظة {index + 1} · إزالة</button>)}</div></fieldset>;
}

function AdditionalGoogleAdEditor({ ad, label, type, assets, disabled, onChange, onRemove }) {
    const fields = creativeFieldsFor(type, ad.ad_format);
    const changeCard = (index, patch) => onChange({ carousel_cards: ad.carousel_cards.map((card, position) => position === index ? { ...card, ...patch } : card) });
    return <fieldset aria-label={label} className="space-y-3 rounded-xl border border-violet-200 bg-violet-50 p-4">
        <legend className="px-2 text-sm font-bold">{label} · {FORMAT_LABELS[ad.ad_format] || "صيغة الإعلان المحفوظة"}</legend>
        <div className="grid gap-3 md:grid-cols-2">
            <Field label={`اسم ${label}`}><input aria-label={`اسم ${label}`} className={INPUT} disabled={disabled} value={ad.name || ""} onChange={(event) => onChange({ name: event.target.value || null })} /></Field>
            <Field label={`حالة ${label}`}><select aria-label={`حالة ${label}`} className={INPUT} disabled={disabled} value={ad.status || "ENABLED"} onChange={(event) => onChange({ status: event.target.value })}><option value="ENABLED">مفعّلة</option><option value="PAUSED">موقوفة</option></select></Field>
            {[["headlines", "عناوين", ad.ad_format === "VIDEO" || ["PRODUCT", "CAROUSEL"].includes(ad.ad_format) ? 40 : 30], ["descriptions", "أوصاف", 90], ["long_headlines", "عناوين طويلة", 90], ["final_urls", "روابط الوصول"]].filter(([field]) => fields.includes(field) || ad[field]?.length).map(([field, title, maxLength]) => <TextList key={field} label={`${title} ${label}`} value={ad[field] || []} maxLength={maxLength} disabled={disabled} onChange={(value) => onChange({ [field]: value })} />)}
            {(fields.includes("business_name") || ad.business_name) && <Field label={`اسم النشاط ${label}`}><input aria-label={`اسم النشاط ${label}`} maxLength={25} className={INPUT} disabled={disabled} value={ad.business_name || ""} onChange={(event) => onChange({ business_name: event.target.value || null })} /></Field>}
        </div>
        <div className="grid gap-3 md:grid-cols-2">{ASSET_FIELDS.filter(([field]) => fields.includes(field) || ad[field]?.length).map(([field, title, kind]) => <AssetPicker key={field} label={`${title} ${label}`} kind={kind} assets={assets} value={ad[field] || []} disabled={disabled} onChange={(value) => onChange({ [field]: value })} />)}</div>
        {(fields.includes("media_bundle_asset") || ad.media_bundle_asset) && <Field label={`مادة HTML5 ${label}`}><select aria-label={`مادة HTML5 ${label}`} className={INPUT} disabled={disabled} value={ad.media_bundle_asset || ""} onChange={(event) => onChange({ media_bundle_asset: event.target.value || null })}><option value="">اختر المادة</option>{assets.filter((asset) => asset.type === "MEDIA_BUNDLE").map((asset) => <option key={assetId(asset)} value={assetId(asset)}>{asset.name || "إعلان HTML5"}</option>)}{ad.media_bundle_asset && !assets.some((asset) => assetId(asset) === ad.media_bundle_asset) && <option value={ad.media_bundle_asset}>مادة HTML5 محفوظة</option>}</select></Field>}
        {(ad.carousel_cards || []).map((card, cardIndex) => <div key={cardIndex} className="space-y-3 rounded-xl border bg-white p-3"><TextList label={`عنوان بطاقة ${cardIndex + 1} ${label}`} value={[card.headline || ""]} maxLength={40} disabled={disabled} onChange={(value) => changeCard(cardIndex, { headline: value[0] || "" })} /><Field label={`رابط بطاقة ${cardIndex + 1} ${label}`}><input type="url" className={INPUT} disabled={disabled} value={card.final_url || ""} onChange={(event) => changeCard(cardIndex, { final_url: event.target.value })} /></Field>{[["image_asset", "صورة أفقية"], ["square_image_asset", "صورة مربعة"], ["portrait_image_asset", "صورة عمودية"]].map(([field, title]) => <AssetPicker key={field} label={`${title} بطاقة ${cardIndex + 1} ${label}`} kind="IMAGE" assets={assets} value={card[field] ? [card[field]] : []} disabled={disabled} onChange={(value) => changeCard(cardIndex, { [field]: value[value.length - 1] || null })} />)}</div>)}
        <button type="button" className="text-sm font-bold text-rose-700" aria-label={`إزالة ${label}`} disabled={disabled} onClick={onRemove}>إزالة الإعلان الإضافي</button>
    </fieldset>;
}

function cleanDraft(draft) {
    const output = { ...draft, name: draft.name.trim(), budget_native: String(draft.budget_native), bidding: { ...draft.bidding } };
    ["start_date", "end_date"].forEach((field) => { if (!output[field]) delete output[field]; });
    ["target_cpa_native", "target_roas", "max_cpc_native"].forEach((field) => { if (!output.bidding[field]) delete output.bidding[field]; });
    if (output.campaign_type === "SEARCH") output.language_ids = [];
    output.groups = draft.groups.map((group) => {
        const next = { ...group, name: group.name.trim(), ...Object.fromEntries(["headlines", "descriptions", "long_headlines", "final_urls", "search_themes"].map((field) => [field, (group[field] || []).map((item) => item.trim()).filter(Boolean)])), keywords: (group.keywords || []).filter((word) => word.text.trim()).map((word) => ({ ...word, text: word.text.trim() })), carousel_cards: (group.carousel_cards || []).filter((card) => card.headline || card.final_url) };
        ["business_name", "cpc_bid_native", "percent_cpc_bid", "media_bundle_asset"].forEach((field) => { if (!next[field]) delete next[field]; });
        return next;
    });
    if (output.shopping && !output.shopping.feed_label) { output.shopping = { ...output.shopping }; delete output.shopping.feed_label; }
    if (output.shopping && !output.shopping.merchant_id) delete output.shopping;
    if (output.app && !output.app.app_id) delete output.app;
    if (output.hotel && !output.hotel.hotel_center_id) delete output.hotel;
    return output;
}

export default function GoogleCampaignComposer({ selection, readiness = {}, onClose, onCompleted, onDraftSaved }) {
    const baseInitial = selection.initialDraft || { ...googleDefaultDraft(selection.accountId, selection.campaignType || "SEARCH"), ...(selection.campaignContext || {}) };
    const initial = { ...baseInitial, groups: [...baseInitial.groups] };
    if (["add_ad", "update_ad"].includes(selection.action)) initial.groups = initial.groups.map((group) => {
        const next = { ...group, keywords: [], audience_ids: [], user_list_ids: [], product_offer_ids: [], hotel_ids: [], channels: [], search_themes: [], disable_ai_max_search_term_matching: false };
        ["cpc_bid_native", "percent_cpc_bid"].forEach((field) => delete next[field]); return next;
    });
    const [draft, setDraft] = useState(initial);
    const [draftRecord, setDraftRecord] = useState(null);
    const [dirty, setDirty] = useState(!selection.draftId);
    const [action, setAction] = useState(selection.mode === "edit" ? selection.action || "rename" : "create");
    const [changes, setChanges] = useState({ name: selection.name || "", budget_native: "", bidding: { strategy: "MAXIMIZE_CONVERSIONS" }, geo_target_ids: [], language_ids: [], group_bid_kind: selection.campaignType === "HOTEL" ? "percent_cpc_bid" : "cpc_bid_native", group_bid_value: "", conversion_goals: [], keywords: baseInitial.groups[0]?.keywords || [], channels: baseInitial.groups[0]?.channels || [], ...(selection.changes || {}) });
    const [reason, setReason] = useState(selection.reason || "");
    const [assets, setAssets] = useState([]);
    const [languages, setLanguages] = useState([]);
    const [geoQuery, setGeoQuery] = useState("");
    const [geoOptions, setGeoOptions] = useState([]);
    const [lookupState, setLookupState] = useState({ audiences: [], user_lists: [], conversion_actions: [], merchants: [], products: [], apps: [], hotels: [] });
    const [lookupError, setLookupError] = useState("");
    const [proposal, setProposal] = useState(selection.proposal || null);
    const [proposalSummary, setProposalSummary] = useState(null);
    const [approvedDigest, setApprovedDigest] = useState(null);
    const [executionStarted, setExecutionStarted] = useState(!!selection.proposal && !googleProposalCanApprove(selection.proposal));
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [aiPrompt, setAiPrompt] = useState("");
    const [aiCeiling, setAiCeiling] = useState("");
    const [aiEvidence, setAiEvidence] = useState(null);
    const [productPickerOpen, setProductPickerOpen] = useState(false);
    const [productQuery, setProductQuery] = useState("");
    const [productResults, setProductResults] = useState([]);
    const [productNames, setProductNames] = useState({});
    const [productsLoading, setProductsLoading] = useState(false);
    const [productLookupError, setProductLookupError] = useState("");
    const [moreProducts, setMoreProducts] = useState(false);
    const [assetName, setAssetName] = useState("");
    const [imageFile, setImageFile] = useState(null);
    const [html5File, setHtml5File] = useState(null);
    const [imagePreview, setImagePreview] = useState(null);
    const [videoInput, setVideoInput] = useState("");
    const [uploadKey, setUploadKey] = useState(0);
    const mounted = useRef(true);
    const inflight = useRef(false);
    const writeController = useRef(null);
    const nonce = useRef(googleIdempotencyKey());
    const draftNonce = useRef(googleIdempotencyKey());
    const notified = useRef(new Set());
    const callback = useRef({ onCompleted, onDraftSaved });
    useEffect(() => { callback.current = { onCompleted, onDraftSaved }; }, [onCompleted, onDraftSaved]);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; writeController.current?.abort(); }; }, []);
    useEffect(() => {
        if (!imageFile || !["image/jpeg", "image/png", "image/gif"].includes(imageFile.type) || typeof URL.createObjectURL !== "function") { setImagePreview(null); return undefined; }
        const url = URL.createObjectURL(imageFile); setImagePreview(url);
        return () => URL.revokeObjectURL(url);
    }, [imageFile]);
    const accountId = selection.accountId;
    const isCampaignEditor = selection.mode !== "edit";
    const entityAction = ["add_group", "add_ad", "rename_group", "pause_group", "enable_group", "set_group_bidding", "remove_group", "remove_ad", "update_group_assets", "set_conversion_goals", "set_keywords", "set_channels", "pause_ad", "enable_ad"].includes(action);
    const entityCreative = ["add_group", "add_ad", "update_group_assets"].includes(action);
    const capabilities = googleCampaignCapabilities(readiness, accountId);
    const capability = capabilities.find((item) => googleCapabilityType(item) === draft.campaign_type);
    const currentCapability = capabilities.find((item) => googleCapabilityType(item) === (selection.campaignType || draft.campaign_type));
    const canCreate = readiness.management_enabled !== false && googleCapabilityCanCreate(capability);
    const permissions = readiness.permissions || {};
    const accountFacts = (readiness.accounts || []).find((item) => String(item.account_id || item.id) === String(accountId)) || {};
    const conversionGoals = accountFacts.customer_conversion_goals || accountFacts.conversion_goals || readiness.customer_conversion_goals || [];
    const canApprove = (permissions.can_execute === true || permissions.owner === true) && permissions.can_approve !== false && permissions.approve !== false;
    const type = draft.campaign_type;
    const selectedProductIds = draft.product_ids || [];
    const productOfferSelections = Object.fromEntries((draft.product_offer_selections || []).map((pair) => [pair.product_id, pair]));
    const merchantId = draft.shopping?.merchant_id || "";
    const audienceOptions = lookupState.audiences.filter((item) => !String(item.type).includes("USER_LIST"));
    const userListOptions = [...lookupState.user_lists, ...lookupState.audiences.filter((item) => String(item.type).includes("USER_LIST"))];
    const editorLocked = busy || !!proposal;
    const strategies = capability?.bidding_strategies || ["MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE", "TARGET_CPA", "TARGET_ROAS", "MAXIMIZE_CLICKS", "MANUAL_CPC"];
    const managementStrategies = currentCapability?.bidding_strategies || strategies;

    useEffect(() => {
        let active = true; const controller = new AbortController();
        setLookupError("");
        Promise.allSettled([
            getGoogleAssets({ accountId, signal: controller.signal }),
            lookupGoogleLanguages({ accountId, signal: controller.signal }),
            ...["audiences", "merchants", "apps", "hotels", "user_lists", "conversion_actions"].map((kind) => lookupGoogleOptions({ accountId, kind, signal: controller.signal })),
        ]).then((results) => {
            if (!active) return;
            const failed = results.filter((item) => item.status === "rejected" && !isGoogleRequestCancelled(item.reason));
            setLookupError(failed.length ? "تعذر تحميل بعض المواد أو خيارات الحساب. حدّث الخيارات لإكمال الحقول الناقصة." : "");
            if (results[0].status === "fulfilled") setAssets(listItems(results[0].value));
            if (results[1].status === "fulfilled") setLanguages(listItems(results[1].value));
            setLookupState((current) => ({ ...current, ...Object.fromEntries(["audiences", "merchants", "apps", "hotels", "user_lists", "conversion_actions"].map((kind, index) => [kind, results[index + 2].status === "fulfilled" ? listItems(results[index + 2].value) : []])) }));
        });
        return () => { active = false; controller.abort(); };
    }, [accountId, uploadKey]);

    useEffect(() => {
        if (!selection.draftId) return undefined;
        let active = true; const controller = new AbortController();
        setBusy(true);
        getGoogleDraft(selection.draftId, { signal: controller.signal }).then((record) => {
            if (!active) return;
            if (String(record.draft?.account_id || record.account_id) !== String(accountId)) throw new Error("foreign_draft");
            setDraft(record.draft); setDraftRecord(record); setDirty(false);
        }).catch((failure) => { if (active && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure, "تعذر فتح المسودة المحفوظة لهذا الحساب.")); }).finally(() => { if (active) setBusy(false); });
        return () => { active = false; controller.abort(); };
    }, [selection.draftId, accountId]);

    useEffect(() => {
        if (geoQuery.trim().length < 2) return undefined;
        let active = true; const controller = new AbortController();
        const timer = setTimeout(() => {
            lookupGoogleGeo({ accountId, query: geoQuery.trim(), signal: controller.signal }).then((response) => { if (active) setGeoOptions(listItems(response)); }).catch((failure) => { if (active && !isGoogleRequestCancelled(failure)) setLookupError("تعذر البحث عن المواقع الجغرافية. حاول مجددًا."); });
        }, 300);
        return () => { active = false; clearTimeout(timer); controller.abort(); };
    }, [accountId, geoQuery]);

    useEffect(() => {
        if (!merchantId) { setLookupState((current) => ({ ...current, products: [] })); return undefined; }
        let active = true; const controller = new AbortController();
        lookupGoogleOptions({ accountId, kind: "products", merchantId, signal: controller.signal }).then((response) => { if (active) setLookupState((current) => ({ ...current, products: listItems(response) })); }).catch((failure) => { if (active && !isGoogleRequestCancelled(failure)) setLookupError("تعذر تحميل منتجات الحساب المختار."); });
        return () => { active = false; controller.abort(); };
    }, [accountId, merchantId]);

    useEffect(() => {
        if (!productPickerOpen) return undefined;
        let active = true; const controller = new AbortController();
        setProductsLoading(true); setProductLookupError(""); setProductResults([]);
        const read = () => getGoogleProducts({ query: productQuery, limit: 50, signal: controller.signal }).then((response) => {
            if (!active) return;
            const rows = listItems(response).filter((product) => mezanProductId(product) && product.source === "mezan_products_v2");
            setProductResults(rows); setMoreProducts(response.has_more === true || response.complete === false);
            setProductNames((current) => ({ ...current, ...Object.fromEntries(rows.map((product) => [mezanProductId(product), product.name || "منتج المتجر"])) }));
        }).catch((failure) => { if (active && !isGoogleRequestCancelled(failure)) setProductLookupError(googleErrorMessage(failure, "تعذر البحث في منتجات المتجر. حاول مجددًا.")); }).finally(() => { if (active) setProductsLoading(false); });
        const timer = productQuery.trim() ? setTimeout(read, 300) : null;
        if (!timer) read();
        return () => { active = false; if (timer) clearTimeout(timer); controller.abort(); };
    }, [accountId, productPickerOpen, productQuery]);

    function invalidate() { setProposal(null); setProposalSummary(null); setApprovedDigest(null); setExecutionStarted(false); nonce.current = googleIdempotencyKey(); setError(""); setNotice(""); }
    function changeDraft(update) {
        invalidate();
        setDraft((current) => {
            const next = typeof update === "function" ? update(current) : { ...current, ...update };
            if (next.shopping?.merchant_id !== current.shopping?.merchant_id) {
                next.product_offer_selections = [];
                next.groups = next.groups.map((group) => ({ ...group, product_offer_ids: [] }));
            }
            if (next.ai_max && (!next.ai_max.enabled || !next.ai_max.text_customization)) next.ai_max = { ...next.ai_max, final_url_expansion: false };
            if (next.campaign_type === "APP" && next.app?.subtype !== current.app?.subtype) next.groups = next.groups.map((group) => ({ ...group, final_urls: next.app.subtype === "ENGAGEMENT" ? group.final_urls : [], audience_ids: next.app.subtype === "INSTALLS" ? group.audience_ids : [], user_list_ids: next.app.subtype === "ENGAGEMENT" ? group.user_list_ids : [] }));
            return next;
        });
        setDirty(true);
    }
    function changeGroup(index, patch) { changeDraft((current) => ({ ...current, groups: current.groups.map((group, groupIndex) => groupIndex === index ? { ...group, ...patch } : group) })); }
    function changeGroupFormat(index, format) {
        const allowed = creativeFieldsFor(type, format);
        changeDraft((current) => ({ ...current, groups: current.groups.map((group, position) => {
            if (position !== index) return group;
            const next = { ...group, ad_format: format };
            ["headlines", "descriptions", "long_headlines", "final_urls", "image_assets", "square_image_assets", "portrait_image_assets", "tall_portrait_image_assets", "logo_assets", "landscape_logo_assets", "video_assets"].forEach((field) => { if (!allowed.includes(field)) next[field] = []; });
            if (!allowed.includes("business_name")) delete next.business_name;
            if (format !== "UPLOADED_HTML5") delete next.media_bundle_asset;
            if (format !== "CAROUSEL") next.carousel_cards = [];
            if (format === "DSA") next.keywords = next.keywords.filter((item) => item.negative);
            return next;
        }) }));
        setNotice("عُرضت الحقول المناسبة للصيغة المختارة. أُزيلت الحقول التي لا تدعمها هذه الصيغة.");
    }
    function changeManagement(patch) { invalidate(); setChanges((current) => ({ ...current, ...patch })); }
    function selectProduct(id) {
        const selected = selectedProductIds.includes(id);
        if (!selected && selectedProductIds.length >= MAX_GOOGLE_PRODUCTS) return;
        const productIds = selected ? selectedProductIds.filter((value) => value !== id) : [...selectedProductIds, id];
        const pairs = (draft.product_offer_selections || []).filter((pair) => productIds.includes(pair.product_id));
        changeDraft((current) => ({ ...current, product_ids: productIds, product_offer_selections: pairs, groups: current.groups.map((group, index) => index === 0 && pairs.length !== (current.product_offer_selections || []).length ? { ...group, product_offer_ids: [...new Set(pairs.map((pair) => pair.offer_id))] } : group) }));
    }
    function selectProductOffer(productId, offerId) {
        const pairs = (draft.product_offer_selections || []).filter((pair) => pair.product_id !== productId && selectedProductIds.includes(pair.product_id) && pair.merchant_id === merchantId);
        if (offerId) pairs.push({ product_id: productId, merchant_id: merchantId, offer_id: offerId });
        changeDraft((current) => ({ ...current, product_offer_selections: pairs, groups: current.groups.map((group, index) => index === 0 ? { ...group, product_offer_ids: [...new Set(pairs.map((pair) => pair.offer_id))] } : group) }));
    }
    async function request(work, receive) {
        if (inflight.current) return null;
        inflight.current = true; setBusy(true); setError(""); setNotice(""); writeController.current = new AbortController();
        try { const response = await work(writeController.current.signal); if (mounted.current) { receive?.(response); return response; } return null; }
        catch (failure) { if (mounted.current && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure)); return null; }
        finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    function receiveProposal(next) {
        setProposal(next); setApprovedDigest(null);
        if (entityVerified(next) && !notified.current.has(next.proposal_id)) {
            notified.current.add(next.proposal_id);
            const asset = next.asset || next.after?.asset || (String(next.after?.resource_name || "").includes("/assets/") ? next.after : null);
            if (asset && assetId(asset)) { setAssets((current) => [...current.filter((item) => assetId(item) !== assetId(asset)), asset]); setImageFile(null); setHtml5File(null); setVideoInput(""); setUploadKey((value) => value + 1); }
            callback.current.onCompleted?.(next);
        }
    }
    async function save(event) {
        event?.preventDefault();
        const value = cleanDraft(draft);
        return request((signal) => draftRecord ? updateGoogleDraft(draftRecord.draft_id || draftRecord.id, value, draftRecord.revision, { signal }) : createGoogleDraft(value, draftNonce.current, { signal }), (record) => {
            setDraftRecord(record); setDirty(false); setNotice("حُفظت المسودة. يمكنك العودة لها من قائمة المسودات."); callback.current.onDraftSaved?.(record);
        });
    }
    function prepare(event) {
        event.preventDefault();
        if (isCampaignEditor && (!canCreate || !draftRecord || dirty)) { setError("احفظ النسخة الحالية من المسودة قبل تجهيز المعاينة."); return; }
        const payload = { action, account_id: accountId, reason: reason.trim(), idempotency_key: nonce.current };
        if (isCampaignEditor) { payload.draft_id = draftRecord.draft_id || draftRecord.id; payload.draft_revision = draftRecord.revision; }
        else { payload.campaign_id = selection.campaignId; payload.changes = action === "rename" ? { name: changes.name.trim() } : action === "set_budget" ? { budget_native: String(changes.budget_native) } : action === "set_bidding" ? { bidding: changes.bidding } : action === "set_targeting" ? { geo_target_ids: changes.geo_target_ids, ...(selection.campaignType === "SEARCH" ? {} : { language_ids: changes.language_ids }), ...(selection.groupId ? { group_id: selection.groupId } : {}) } : action === "update_ad" ? { ad_group_ad_resource: selection.adResource, group: cleanDraft(draft).groups[0] } : action === "duplicate" ? { name: changes.name.trim() } : action === "remove" && selection.adResource ? { ad_group_ad_resource: selection.adResource } : {}; }
        if (entityAction) {
            const group = cleanDraft(draft).groups[0];
            payload.changes = action === "add_group" ? { group }
                : ["add_ad", "update_group_assets"].includes(action) ? { group_id: String(selection.groupId), group }
                : action === "rename_group" ? { group_id: String(selection.groupId), name: changes.name.trim() }
                : action === "set_group_bidding" ? { group_id: String(selection.groupId), [changes.group_bid_kind]: String(changes.group_bid_value) }
                : ["remove_ad", "pause_ad", "enable_ad"].includes(action) ? { ad_group_ad_resource: selection.adResource }
                : action === "set_conversion_goals" ? { conversion_goals: changes.conversion_goals }
                : action === "set_keywords" ? { group_id: String(selection.groupId), keywords: changes.keywords.filter((word) => word.text.trim()).map((word) => ({ ...word, text: word.text.trim() })) }
                : action === "set_channels" ? { group_id: String(selection.groupId), channels: changes.channels }
                : { group_id: String(selection.groupId) };
        }
        request((signal) => createGoogleProposal(payload, { signal }), (response) => { setProposalSummary(isCampaignEditor ? cleanDraft(draft) : payload.changes); receiveProposal(response); setExecutionStarted(false); });
    }
    function execute() {
        if (!canApprove || !googleProposalCanApprove(proposal, executionStarted) || approvedDigest !== proposal.confirmation_digest || inflight.current) return;
        setExecutionStarted(true); setApprovedDigest(null);
        request((signal) => executeGoogleProposal(proposal.proposal_id, proposal.confirmation_digest, { signal }), receiveProposal);
    }
    function refreshProposal() { request((signal) => getGoogleProposal(proposal.proposal_id, { signal }), receiveProposal); }
    function reconcile() { request((signal) => reconcileGoogleProposal(proposal.proposal_id, { signal }), receiveProposal); }
    function aiDraft() {
        if (!aiPrompt.trim() || !(Number(aiCeiling) > 0)) return;
        const offerSelections = (draft.product_offer_selections || []).map((pair) => ({ ...pair }));
        request((signal) => generateGoogleAiDraft({ account_id: accountId, prompt: aiPrompt.trim(), budget_ceiling_native: aiCeiling, campaign_type: type, product_ids: [...selectedProductIds], product_offer_selections: offerSelections }, { signal }), (response) => {
            if (!response.draft || String(response.draft.account_id) !== String(accountId)) { setError("لم يُرجع الذكاء مسودة صالحة للحساب المختار."); return; }
            if (response.draft.campaign_type !== type) { setError("لم يطابق اقتراح الذكاء نوع الحملة المختار. حدّد النوع والطلب ثم أعد تجهيز المسودة."); return; }
            const productIds = Array.isArray(response.product_ids) ? response.product_ids : Array.isArray(response.draft.product_ids) ? response.draft.product_ids : selectedProductIds;
            if (productIds.length > MAX_GOOGLE_PRODUCTS) { setError("تجاوز اقتراح الذكاء حد المنتجات الممكنة لهذه المسودة."); return; }
            setDraft({ ...response.draft, product_ids: [...productIds], product_offer_selections: Array.isArray(response.draft.product_offer_selections) ? response.draft.product_offer_selections : offerSelections }); setDraftRecord(null); setDirty(true); draftNonce.current = googleIdempotencyKey(); invalidate(); setAiEvidence(response); setNotice("جُهّزت مسودة الذكاء. راجعها وعدّلها ثم احفظها قبل المعاينة والاعتماد.");
        });
    }
    function chooseType(nextType) {
        const next = googleDefaultDraft(accountId, nextType);
        changeDraft({ ...next, name: draft.name, budget_native: draft.budget_native, geo_target_ids: draft.geo_target_ids, language_ids: draft.language_ids, product_ids: [...selectedProductIds] });
    }
    function chooseApp(value) {
        const app = lookupState.apps.find((item) => String(item.app_id || item.id) === value);
        changeDraft({ app: { ...draft.app, app_id: value, app_store: app?.app_store || draft.app.app_store } });
    }
    function appFromUrl(value) {
        try { const url = new URL(value); const id = url.hostname === "play.google.com" ? url.searchParams.get("id") : /(?:^|\.)apps\.apple\.com$/.test(url.hostname) ? url.pathname.match(/\/id(\d+)/)?.[1] : ""; if (id) changeDraft({ app: { ...draft.app, app_id: id, app_store: url.hostname === "play.google.com" ? "GOOGLE_APP_STORE" : "APPLE_APP_STORE" } }); } catch { /* A partial store URL remains editable until it is valid. */ }
    }
    function prepareImage() {
        if (!imageFile) return;
        request((signal) => uploadGoogleImage({ accountId, file: imageFile, name: assetName || imageFile.name, idempotencyKey: nonce.current, signal }), (response) => { receiveProposal(response); setExecutionStarted(false); setProposalSummary({ name: assetName || imageFile.name, type: "IMAGE" }); });
    }
    function prepareHtml5() {
        if (!html5File) return;
        request((signal) => uploadGoogleImage({ accountId, file: html5File, name: assetName || html5File.name, assetType: "HTML5", idempotencyKey: nonce.current, signal }), (response) => {
            receiveProposal(response); setExecutionStarted(false); setProposalSummary({ name: assetName || html5File.name, type: "MEDIA_BUNDLE", bytes: html5File.size });
        });
    }
    function prepareVideo() {
        const value = videoInput.trim(); let id = value;
        try { const url = new URL(value); id = url.hostname === "youtu.be" ? url.pathname.slice(1) : /(?:^|\.)youtube\.com$/.test(url.hostname) ? url.searchParams.get("v") || url.pathname.match(/\/(?:shorts|embed)\/([^/?]+)/)?.[1] : ""; } catch { /* A plain YouTube video ID is accepted. */ }
        if (!/^[A-Za-z0-9_-]{11}$/.test(id)) { setError("أدخل رابط فيديو YouTube صحيحًا أو معرّف الفيديو المكوّن من 11 حرفًا."); return; }
        request((signal) => addGoogleVideo({ accountId, youtubeVideoId: id, name: assetName || "فيديو الحملة", idempotencyKey: nonce.current, signal }), (response) => { receiveProposal(response); setExecutionStarted(false); setProposalSummary({ name: assetName || "فيديو الحملة", type: "YOUTUBE_VIDEO" }); });
    }
    const targetingValue = isCampaignEditor ? draft : changes;
    function setTargeting(patch) { isCampaignEditor ? changeDraft(patch) : changeManagement(patch); }
    const assetFields = useMemo(() => ASSET_FIELDS, []);
    const showCreative = !["SHOPPING", "HOTEL"].includes(type);

    return <section className="space-y-4 rounded-3xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6" dir="rtl" data-testid="google-campaign-composer">
        <header className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-black">{isCampaignEditor ? selection.draftId ? "استكمال مسودة جوجل" : "إنشاء حملة جوجل" : "إدارة حملة جوجل"}</h2><p className="mt-1 text-xs text-slate-500">{selection.accountName || "الحساب المختار"} · {selection.currency || "عملة الحساب"}{selection.name && ` · ${selection.name}`}</p></div><button type="button" onClick={onClose} className={BUTTON} disabled={busy}>إغلاق محرر جوجل</button></header>
        <p className="rounded-xl bg-blue-50 p-3 text-sm leading-6 text-blue-900">احفظ المسودة، ثم راجع نسخة ثابتة قبل الاعتماد. تُنشأ الحملة كاملة وموقوفة، ويكون التفعيل عملية مستقلة حسب تفويض الحساب.</p>
        <p className="rounded-xl bg-amber-50 p-3 text-xs leading-6 text-amber-900">الميزانية اليومية متوسط يومي لدى جوجل. قد يصل الصرف في اليوم إلى ضعف المتوسط في أغلب الحملات، مع حد شهري 30.4 ضعف المتوسط إذا بقي ثابتًا. تتغير الحدود عند تعديل الميزانية وبعض أنواع الحملات؛ راجع أثر الإنفاق قبل الاعتماد.</p>
        {error && <p role="alert" className="rounded-xl bg-rose-50 p-3 text-sm font-bold text-rose-800">{error}</p>}{notice && <p role="status" className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-900">{notice}</p>}{lookupError && <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-amber-50 p-3 text-sm text-amber-900"><span>{lookupError}</span><button type="button" disabled={busy} onClick={() => setUploadKey((current) => current + 1)} className={BUTTON}>تحديث الخيارات</button></div>}
        {!proposal && ((isCampaignEditor && type !== "APP") || action === "set_conversion_goals") && <ConversionGoalOptions items={conversionGoals} value={isCampaignEditor ? draft.conversion_goals || [] : changes.conversion_goals} disabled={editorLocked} onChange={(value) => isCampaignEditor ? changeDraft({ conversion_goals: value }) : changeManagement({ conversion_goals: value })} />}
        {!proposal && isCampaignEditor && type === "APP" && <Field label="هدف تحسين التطبيق"><select aria-label="هدف تحسين تطبيق جوجل" className={INPUT} disabled={editorLocked} value={draft.app?.goal_type || ""} onChange={(event) => changeDraft({ app: { ...draft.app, goal_type: event.target.value || undefined } })}><option value="">الهدف المناسب لنوع حملة التطبيق</option><option value="OPTIMIZE_INSTALLS_TARGET_INSTALL_COST">زيادة التثبيت بتكلفة مستهدفة</option><option value="OPTIMIZE_IN_APP_CONVERSIONS_TARGET_INSTALL_COST">تحويلات التطبيق مع زيادة التثبيت</option><option value="OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST">تحويلات التطبيق بتكلفة مستهدفة</option><option value="OPTIMIZE_RETURN_ON_ADVERTISING_SPEND">زيادة عائد إنفاق التطبيق</option><option value="OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME">زيادة التسجيل المسبق</option></select></Field>}
        {!proposal && entityAction && <section className="space-y-4 rounded-2xl border border-emerald-200 p-4" aria-label="عملية عنصر جوجل">
            <h3 className="font-black">{ACTION_LABELS[action]} · {selection.name || "الحملة المختارة"}</h3>
            {action === "set_keywords" && <KeywordsEditor value={changes.keywords} disabled={busy} suffix="إدارة" onChange={(value) => changeManagement({ keywords: value })} />}
            {action === "set_channels" && <MultiOptions label="قنوات المجموعة" items={Object.entries(CHANNEL_LABELS).map(([id, name]) => ({ id, name }))} value={changes.channels} disabled={busy} onChange={(value) => changeManagement({ channels: value })} />}
            {action === "rename_group" && <Field label="اسم المجموعة الجديد"><input aria-label="اسم مجموعة جوجل الجديد" className={INPUT} disabled={busy} value={changes.name} onChange={(event) => changeManagement({ name: event.target.value })} /></Field>}
            {action === "set_group_bidding" && <div className="grid gap-3 sm:grid-cols-2"><Field label="نوع مزايدة المجموعة"><select aria-label="نوع مزايدة مجموعة جوجل" className={INPUT} disabled={busy} value={changes.group_bid_kind} onChange={(event) => changeManagement({ group_bid_kind: event.target.value })}><option value="cpc_bid_native">تكلفة نقرة · {selection.currency}</option>{selection.campaignType === "HOTEL" && <option value="percent_cpc_bid">نسبة تكلفة النقرة · 0.1 تعني 10%</option>}<option value="target_cpa_native">تكلفة تحويل مستهدفة</option><option value="target_roas">عائد مستهدف · 3 تعني 300%</option></select></Field><Field label="قيمة المزايدة"><input aria-label="قيمة مزايدة مجموعة جوجل" className={INPUT} type="number" min="0.000001" max={changes.group_bid_kind === "percent_cpc_bid" ? "0.999999" : undefined} step="0.01" disabled={busy} value={changes.group_bid_value} onChange={(event) => changeManagement({ group_bid_value: event.target.value })} /></Field></div>}
            {["remove_group", "remove_ad"].includes(action) && <p className="rounded-xl bg-rose-50 p-3 text-sm text-rose-900">ستُحذف {action === "remove_group" ? "المجموعة" : "نسخة الإعلان"} المحددة. راجع العملية قبل الاعتماد.</p>}
        </section>}
        {!proposal && (isCampaignEditor || entityCreative || action === "update_ad") && draft.groups.map((group, index) => <section key={`advanced-${index}`} className="grid gap-3 rounded-2xl border p-4 md:grid-cols-2" aria-label={`إعدادات إضافية للمجموعة ${index + 1}`}>
            {(isCampaignEditor || action === "add_group") && draft.bidding.strategy === "MANUAL_CPC" && <Field label={`تكلفة نقرة المجموعة ${index + 1} · ${selection.currency}`}><input aria-label={`تكلفة نقرة مجموعة جوجل ${index + 1}`} type="number" min="0.01" step="0.01" className={INPUT} disabled={editorLocked} value={group.cpc_bid_native || ""} onChange={(event) => changeGroup(index, { cpc_bid_native: event.target.value })} /></Field>}
            {(isCampaignEditor || action === "add_group") && type === "HOTEL" && draft.bidding.strategy === "PERCENT_CPC" && <Field label={`نسبة نقرة المجموعة ${index + 1} · 0.1 تعني 10%`}><input aria-label={`نسبة نقرة فنادق جوجل ${index + 1}`} type="number" min="0.000001" max="0.999999" step="0.01" className={INPUT} disabled={editorLocked} value={group.percent_cpc_bid || ""} onChange={(event) => changeGroup(index, { percent_cpc_bid: event.target.value })} /></Field>}
            {group.ad_format === "UPLOADED_HTML5" && <Field label={`إعلان HTML5 جاهز للمجموعة ${index + 1}`}><select aria-label={`مادة HTML5 جوجل ${index + 1}`} className={INPUT} disabled={editorLocked} value={group.media_bundle_asset || ""} onChange={(event) => changeGroup(index, { media_bundle_asset: event.target.value })}><option value="">اختر الإعلان من مكتبة الحساب</option>{assets.filter((asset) => String(asset.type).includes("MEDIA_BUNDLE")).map((asset) => <option key={assetId(asset)} value={assetId(asset)}>{asset.name || "إعلان HTML5"}</option>)}</select><p className="text-xs text-amber-900">يلزم حساب مؤهّل لدى جوجل ومادة HTML5 جاهزة ومعتمدة.</p></Field>}
            {type === "SEARCH" && draft.ai_max?.enabled && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={group.disable_ai_max_search_term_matching === true} disabled={editorLocked} onChange={(event) => changeGroup(index, { disable_ai_max_search_term_matching: event.target.checked })} />إيقاف توسعة مطابقة البحث لهذه المجموعة</label>}
            {!Object.keys(group).some((field) => ["cpc_bid_native", "percent_cpc_bid", "media_bundle_asset"].includes(field)) && !["MANUAL_CPC", "PERCENT_CPC"].includes(draft.bidding.strategy) && group.ad_format !== "UPLOADED_HTML5" && !draft.ai_max?.enabled && <p className="text-xs text-slate-500">تتبع مزايدة هذه المجموعة إعدادات الحملة.</p>}
        </section>)}
        {isCampaignEditor && !proposal && <>
            <details className="rounded-2xl border border-violet-200 bg-violet-50 p-4" onToggle={(event) => setProductPickerOpen(event.currentTarget.open)}>
                <summary className="cursor-pointer font-black text-violet-900">جهّز المسودة بذكاء ميزان</summary>
                <div className="mt-4 space-y-4">
                    <section className="space-y-3 rounded-xl border border-violet-200 bg-white p-3" aria-label="منتجات حملة جوجل من ميزان">
                        <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-bold">منتجات المتجر · اختياري</h3><span className="text-xs text-slate-500">{selectedProductIds.length} من {MAX_GOOGLE_PRODUCTS}</span></div>
                        <p className="text-xs leading-6 text-slate-500">اختر المنتجات من كتالوج ميزان لتوجيه محتوى الحملة وروابطها. يمكنك تجهيز حملة عامة للعلامة التجارية بترك الاختيار فارغًا.</p>
                        <Field label="ابحث في منتجات المتجر"><input aria-label="بحث منتجات جوجل في ميزان" maxLength={100} className={INPUT} disabled={busy} value={productQuery} onChange={(event) => setProductQuery(event.target.value)} placeholder="اسم المنتج" /></Field>
                        {productLookupError && <p role="alert" className="text-xs text-amber-900">{productLookupError}</p>}
                        {productsLoading && <p role="status" className="text-xs text-slate-500">جارٍ البحث في منتجات ميزان…</p>}
                        <div className="grid max-h-60 gap-2 overflow-y-auto sm:grid-cols-2">{productResults.map((product) => {
                            const id = mezanProductId(product), checked = selectedProductIds.includes(id);
                            return <label key={id} className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${checked ? "border-violet-400 bg-violet-50" : "border-slate-200"}`}><input type="checkbox" aria-label={`منتج لذكاء جوجل: ${product.name || "منتج المتجر"}`} disabled={busy || !checked && selectedProductIds.length >= MAX_GOOGLE_PRODUCTS} checked={checked} onChange={() => selectProduct(id)} /><span>{product.name || "منتج المتجر"}</span></label>;
                        })}</div>
                        {!productsLoading && !productLookupError && !productResults.length && <p className="text-xs text-slate-500">لم تظهر منتجات مطابقة لهذا البحث.</p>}
                        {moreProducts && <p className="text-xs text-slate-500">تظهر أول 50 نتيجة؛ استخدم اسمًا أدق للوصول إلى بقية المنتجات.</p>}
                        {selectedProductIds.length > 0 && <div className="flex flex-wrap gap-2" aria-label="منتجات جوجل المختارة">{selectedProductIds.map((id, index) => <button key={id} type="button" className="rounded-lg bg-violet-100 px-3 py-2 text-xs font-bold text-violet-900" disabled={busy} onClick={() => selectProduct(id)} aria-label={`إزالة المنتج المختار ${index + 1}`}>{productNames[id] || `منتج محفوظ ${index + 1}`} · إزالة</button>)}</div>}
                        {merchantId && selectedProductIds.map((id, index) => <Field key={id} label={`منتج جوجل المقابل لـ ${productNames[id] || `المنتج المختار ${index + 1}`}`} note="اختيار العرض يضبط منتجات المجموعة الأولى. راجع قائمة منتجات المجموعة أسفل النموذج."><select aria-label={`منتج جوجل المقابل للمنتج ${index + 1}`} className={INPUT} disabled={busy} value={productOfferSelections[id]?.merchant_id === merchantId ? productOfferSelections[id].offer_id : ""} onChange={(event) => selectProductOffer(id, event.target.value)}><option value="">اختر المنتج المقابل</option>{lookupState.products.map((product) => <option key={product.offer_id} value={product.offer_id} disabled={!googleOfferIsEligible(product)}>{product.title || product.name || "منتج حساب جوجل"}{!googleOfferIsEligible(product) ? " · غير مؤهّل" : ""}</option>)}</select></Field>)}
                    </section>
                    <div className="grid gap-3 md:grid-cols-[1fr_220px]">
                        <Field label="طلبك لذكاء ميزان"><textarea aria-label="طلب حملة جوجل بالذكاء" rows={3} className={INPUT} disabled={busy} value={aiPrompt} onChange={(event) => setAiPrompt(event.target.value)} placeholder="جهّز حملة للمنتجات المختارة في السعودية، مع إبراز ميزاتها وخياراتها" /></Field>
                        <Field label={`أقصى ميزانية يومية · ${selection.currency || "عملة الحساب"}`}><input type="number" min="0.01" step="0.01" aria-label="سقف ميزانية مسودة الذكاء" className={INPUT} disabled={busy} value={aiCeiling} onChange={(event) => setAiCeiling(event.target.value)} /></Field>
                        <p className="text-xs text-violet-900 md:col-span-2">نوع مسودة الذكاء: {GOOGLE_CAMPAIGN_LABELS[type] || "النوع المختار"} · المنتجات والميزانية تظل قابلة للمراجعة قبل الاعتماد.</p>
                        <button type="button" disabled={busy || !aiPrompt.trim() || !(Number(aiCeiling) > 0) || permissions.can_draft === false} onClick={aiDraft} className="rounded-xl bg-violet-700 px-4 py-3 text-sm font-bold text-white disabled:opacity-40 md:col-span-2">تجهيز مسودة قابلة للتحرير</button>
                    </div>
                </div>
            </details>
            {aiEvidence && <section aria-label="أدلة مسودة الذكاء" className="space-y-2 rounded-xl bg-violet-50 p-4 text-sm"><p>{aiEvidence.explanation || "راجع الإعدادات المقترحة وحقائق المنتج قبل الاعتماد."}</p>{(aiEvidence.limitations || []).map((item, index) => <p key={index} className="text-amber-900">{typeof item === "string" ? item : item.message || item.detail}</p>)}{Array.isArray(aiEvidence.evidence) && aiEvidence.evidence.map((item, index) => <p key={index} className="text-xs text-slate-600">{typeof item === "string" ? item : item.label || item.summary || item.source}</p>)}</section>}
            {aiEvidence?.review_notes?.length > 0 && <section aria-label="ملاحظات مسودة الذكاء" className="space-y-2 rounded-xl bg-violet-50 p-4 text-sm"><h3 className="font-bold">ملاحظات للمراجعة</h3>{aiEvidence.review_notes.map((note, index) => <p key={index}>{note}</p>)}</section>}
            {aiEvidence?.missing_requirements?.length > 0 && <section className="space-y-2 rounded-xl bg-amber-50 p-4 text-sm text-amber-900" aria-label="متطلبات مسودة الذكاء"><h3 className="font-bold">أكمل هذه المتطلبات قبل معاينة الحملة</h3>{aiEvidence.missing_requirements.map((item, index) => <p key={index}>{typeof item === "string" ? item : item.message || item.detail || "أكمل إعدادات النوع والمواد المطلوبة."}</p>)}</section>}
            <div className="grid gap-4 md:grid-cols-2"><Field label="نوع الحملة"><select aria-label="نوع حملة جوجل" className={INPUT} disabled={editorLocked} value={type} onChange={(event) => chooseType(event.target.value)}>{capabilities.map((item) => <option key={googleCapabilityType(item)} value={googleCapabilityType(item)} disabled={!googleCapabilityCanCreate(item)}>{item.label || GOOGLE_CAMPAIGN_LABELS[googleCapabilityType(item)] || "مسار متخصص"}{!googleCapabilityCanCreate(item) ? " · غير متاح" : ""}</option>)}</select></Field><Field label="اسم الحملة"><input aria-label="اسم حملة جوجل" maxLength={128} className={INPUT} disabled={editorLocked} value={draft.name} onChange={(event) => changeDraft({ name: event.target.value })} /></Field><Field label={`ميزانية الحملة · ${selection.currency || "عملة الحساب"}`}><input aria-label="ميزانية حملة جوجل" inputMode="decimal" type="number" min="0.01" step="0.01" className={INPUT} disabled={editorLocked} value={draft.budget_native} onChange={(event) => changeDraft({ budget_native: event.target.value })} /></Field><Field label="نوع الميزانية"><select aria-label="نوع ميزانية جوجل" className={INPUT} disabled={editorLocked} value={draft.budget_mode} onChange={(event) => changeDraft({ budget_mode: event.target.value })}>{(capability?.budget_modes || ["DAILY"]).map((mode) => <option key={mode} value={mode}>{mode === "DAILY" ? "ميزانية يومية" : "ميزانية إجمالية"}</option>)}</select></Field><Field label="استراتيجية المزايدة"><select aria-label="استراتيجية مزايدة جوجل" className={INPUT} disabled={editorLocked} value={draft.bidding.strategy} onChange={(event) => changeDraft({ bidding: { strategy: event.target.value } })}>{strategies.map((strategy) => <option key={strategy} value={strategy}>{GOOGLE_BIDDING_LABELS[strategy] || "استراتيجية متخصصة"}</option>)}</select></Field>{["TARGET_CPA", "MAXIMIZE_CONVERSIONS"].includes(draft.bidding.strategy) && <Field label={draft.bidding.strategy === "TARGET_CPA" ? "تكلفة التحويل المستهدفة (مطلوبة)" : "تكلفة التحويل المستهدفة (اختياري)"}><input aria-label="تكلفة تحويل جوجل المستهدفة" type="number" step="0.01" min="0.01" className={INPUT} disabled={editorLocked} value={draft.bidding.target_cpa_native || ""} onChange={(event) => changeDraft({ bidding: { ...draft.bidding, target_cpa_native: event.target.value } })} /></Field>}{["TARGET_ROAS", "MAXIMIZE_CONVERSION_VALUE"].includes(draft.bidding.strategy) && <Field label="العائد المستهدف · 3 تعني 300%"><input aria-label="عائد جوجل المستهدف" type="number" step="0.01" min="0.01" className={INPUT} disabled={editorLocked} value={draft.bidding.target_roas || ""} onChange={(event) => changeDraft({ bidding: { ...draft.bidding, target_roas: event.target.value } })} /></Field>}{["MANUAL_CPC", "MAXIMIZE_CLICKS", "PERCENT_CPC"].includes(draft.bidding.strategy) && <Field label="حد تكلفة النقرة"><input aria-label="حد نقرة جوجل" type="number" step="0.01" min="0.01" className={INPUT} disabled={editorLocked} value={draft.bidding.max_cpc_native || ""} onChange={(event) => changeDraft({ bidding: { ...draft.bidding, max_cpc_native: event.target.value } })} /></Field>}<Field label={draft.budget_mode === "TOTAL" ? "تاريخ البداية (مطلوب)" : "تاريخ البداية (اختياري)"}><input type="date" aria-label="بداية حملة جوجل" className={INPUT} disabled={editorLocked} value={draft.start_date || ""} onChange={(event) => changeDraft({ start_date: event.target.value })} /></Field><Field label={draft.budget_mode === "TOTAL" ? "تاريخ النهاية (مطلوب)" : "تاريخ النهاية (اختياري)"}><input type="date" aria-label="نهاية حملة جوجل" min={draft.start_date || undefined} className={INPUT} disabled={editorLocked} value={draft.end_date || ""} onChange={(event) => changeDraft({ end_date: event.target.value })} /></Field></div>
            {!canCreate && <p role="alert" className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{googleCapabilityReason(capability)}</p>}
            {type === "SEARCH" && <section className="space-y-3 rounded-2xl border p-4"><h3 className="font-black">شبكات البحث وAI Max</h3><div className="grid gap-3 sm:grid-cols-2">{[["target_search_network", "شركاء بحث جوجل"], ["target_content_network", "شبكة العرض مع البحث"]].map(([field, label]) => <label key={field} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.search?.[field] === true} disabled={editorLocked} onChange={(event) => changeDraft({ search: { ...draft.search, [field]: event.target.checked } })} />{label}</label>)}{[["enabled", "تفعيل AI Max"], ["text_customization", "تخصيص النص تلقائيًا"], ["final_url_expansion", "توسيع رابط الوصول"]].map(([field, label]) => <label key={field} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.ai_max?.[field] === true} disabled={editorLocked || field !== "enabled" && !draft.ai_max?.enabled} onChange={(event) => changeDraft({ ai_max: field === "enabled" && !event.target.checked ? { enabled: false, text_customization: false, final_url_expansion: false } : { ...draft.ai_max, [field]: event.target.checked } })} />{label}</label>)}</div>{draft.groups.some((group) => group.ad_format === "DSA") && <div className="grid gap-3 sm:grid-cols-2"><Field label="نطاق الموقع للبحث الديناميكي"><input aria-label="نطاق البحث الديناميكي" className={INPUT} disabled={editorLocked} value={draft.search?.dynamic_search_domain || ""} onChange={(event) => changeDraft({ search: { ...draft.search, dynamic_search_domain: event.target.value } })} placeholder="example.com" /></Field><Field label="لغة محتوى البحث الديناميكي"><select aria-label="لغة البحث الديناميكي" className={INPUT} disabled={editorLocked} value={draft.search?.dynamic_search_language_code || ""} onChange={(event) => changeDraft({ search: { ...draft.search, dynamic_search_language_code: event.target.value } })}><option value="">اختر اللغة</option>{languages.filter((item) => item.code || item.language_code).map((item) => <option key={optionId(item)} value={item.code || item.language_code}>{optionLabel(item)}</option>)}</select></Field></div>}</section>}
            {["SHOPPING", "PERFORMANCE_MAX", "DEMAND_GEN"].includes(type) && <section className="space-y-3 rounded-2xl border p-4"><h3 className="font-black">منتجات Merchant Center</h3>{type !== "SHOPPING" && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!draft.shopping} disabled={editorLocked} onChange={(event) => changeDraft((current) => { const next = { ...current }; if (event.target.checked) next.shopping = { merchant_id: "", feed_label: "", campaign_priority: 0 }; else delete next.shopping; return next; })} />استخدام تغذية المنتجات في هذه الحملة</label>}{draft.shopping && <div className="grid gap-3 sm:grid-cols-2"><Field label="حساب المنتجات المرتبط"><select aria-label="حساب منتجات جوجل" className={INPUT} disabled={editorLocked} value={merchantId} onChange={(event) => changeDraft({ shopping: { ...draft.shopping, merchant_id: event.target.value } })}><option value="">اختر حساب المنتجات</option>{lookupState.merchants.map((item) => <option key={item.merchant_id} value={item.merchant_id} disabled={item.link_verified === false}>{optionLabel(item)}{item.link_verified === false ? " · يحتاج ربطًا" : ""}</option>)}</select></Field><Field label="تغذية المنتجات"><select aria-label="تغذية منتجات جوجل" className={INPUT} disabled={editorLocked || !merchantId} value={draft.shopping.feed_label || ""} onChange={(event) => changeDraft({ shopping: { ...draft.shopping, feed_label: event.target.value } })}><option value="">كل منتجات الحساب</option>{[...new Set(lookupState.products.map((item) => item.feed_label).filter(Boolean))].map((label) => <option key={label} value={label}>{label}</option>)}</select></Field>{type === "SHOPPING" && <Field label="أولوية حملة التسوق"><select aria-label="أولوية تسوق جوجل" className={INPUT} disabled={editorLocked} value={draft.shopping.campaign_priority} onChange={(event) => changeDraft({ shopping: { ...draft.shopping, campaign_priority: Number(event.target.value) } })}><option value={0}>منخفضة</option><option value={1}>متوسطة</option><option value={2}>مرتفعة</option></select></Field>}</div>}{draft.shopping && !lookupState.merchants.length && <p className="text-sm text-amber-900">يتطلب المسار حساب Merchant Center مرتبطًا ومنتجات معتمدة.</p>}</section>}
            {type === "APP" && <section className="grid gap-3 rounded-2xl border p-4 sm:grid-cols-2"><h3 className="font-black sm:col-span-2">التطبيق والتحويلات</h3><Field label="نوع حملة التطبيق"><select aria-label="نوع حملة تطبيق جوجل" className={INPUT} disabled={editorLocked} value={draft.app?.subtype || "INSTALLS"} onChange={(event) => changeDraft({ app: { ...draft.app, subtype: event.target.value } })}><option value="INSTALLS">تثبيت التطبيق</option><option value="ENGAGEMENT">تفاعل داخل التطبيق</option><option value="PRE_REGISTRATION">تسجيل مسبق</option></select></Field><Field label="التطبيق"><select aria-label="تطبيق حملة جوجل" className={INPUT} disabled={editorLocked} value={draft.app?.app_id || ""} onChange={(event) => chooseApp(event.target.value)}><option value="">اختر التطبيق</option>{lookupState.apps.map((item) => <option key={item.app_id || item.id} value={item.app_id || item.id}>{optionLabel(item)}</option>)}</select></Field><Field label="رابط التطبيق في Google Play أو App Store" note={draft.app?.app_id ? "تم التعرف على التطبيق من رابط المتجر." : "يمكن استخدام رابط تطبيق جديد عند عدم ظهوره بالقائمة."}><input type="url" aria-label="رابط تطبيق جوجل" className={INPUT} disabled={editorLocked} onChange={(event) => appFromUrl(event.target.value)} placeholder="https://play.google.com/store/apps/details?id=…" /></Field><Field label="متجر التطبيق"><select aria-label="متجر تطبيق جوجل" className={INPUT} disabled={editorLocked} value={draft.app?.app_store || "GOOGLE_APP_STORE"} onChange={(event) => changeDraft({ app: { ...draft.app, app_store: event.target.value } })}><option value="GOOGLE_APP_STORE">Google Play</option><option value="APPLE_APP_STORE">App Store</option></select></Field><div className="sm:col-span-2"><MultiOptions label="تحويلات التطبيق المستهدفة" items={(lookupState.apps.find((item) => String(item.app_id || item.id) === draft.app?.app_id)?.conversion_actions || readiness.conversion_actions || lookupState.conversion_actions)} value={draft.app?.conversion_action_ids || []} disabled={editorLocked} onChange={(value) => changeDraft({ app: { ...draft.app, conversion_action_ids: value } })} /></div></section>}
            {type === "HOTEL" && <Field label="حساب Hotel Center المرتبط"><select aria-label="حساب فنادق جوجل" className={INPUT} disabled={editorLocked} value={draft.hotel?.hotel_center_id || ""} onChange={(event) => changeDraft({ hotel: { hotel_center_id: event.target.value } })}><option value="">اختر حساب الفنادق</option>{lookupState.hotels.filter((item) => item.hotel_center_id).map((item) => <option key={item.hotel_center_id} value={item.hotel_center_id}>{optionLabel(item)}</option>)}</select></Field>}
        </>}
        {!isCampaignEditor && !entityAction && !proposal && <div className="grid gap-4 sm:grid-cols-2">
            <Field label="العملية"><select aria-label="عملية إدارة جوجل" className={INPUT} disabled={editorLocked} value={action} onChange={(event) => { invalidate(); setAction(event.target.value); }}>{(selection.adId ? ["update_ad", "pause_ad", "enable_ad", "remove_ad"] : ["rename", "pause", "enable", "set_budget", "set_bidding", "set_targeting", "set_conversion_goals", "duplicate", "remove"]).map((item) => <option key={item} value={item} disabled={currentCapability?.actions && !currentCapability.actions.includes(item) || Array.isArray(permissions.allowed_actions) && !permissions.allowed_actions.includes(item)}>{ACTION_LABELS[item]}</option>)}</select></Field>
            {["rename", "duplicate"].includes(action) && <Field label={action === "duplicate" ? "اسم الحملة المنسوخة" : "اسم الحملة"}><input aria-label="اسم إدارة جوجل" className={INPUT} disabled={editorLocked} value={changes.name} onChange={(event) => changeManagement({ name: event.target.value })} /></Field>}
            {action === "set_budget" && <Field label={`${selection.budgetMode === "TOTAL" ? "الميزانية الإجمالية الجديدة" : "متوسط الميزانية اليومية الجديد"} · ${selection.currency || "عملة الحساب"}`}><input aria-label="ميزانية إدارة جوجل" type="number" min="0.01" step="0.01" className={INPUT} disabled={editorLocked} value={changes.budget_native} onChange={(event) => changeManagement({ budget_native: event.target.value })} /></Field>}
            {action === "set_bidding" && <><Field label="المزايدة الجديدة"><select aria-label="مزايدة إدارة جوجل" className={INPUT} disabled={editorLocked} value={changes.bidding.strategy} onChange={(event) => changeManagement({ bidding: { strategy: event.target.value } })}>{managementStrategies.map((strategy) => <option key={strategy} value={strategy}>{GOOGLE_BIDDING_LABELS[strategy] || "استراتيجية متخصصة"}</option>)}</select></Field>{[["TARGET_CPA", "target_cpa_native", "تكلفة التحويل المستهدفة"], ["TARGET_ROAS", "target_roas", "العائد المستهدف"], ["MANUAL_CPC", "max_cpc_native", "حد تكلفة النقرة"]].filter(([strategy]) => strategy === changes.bidding.strategy).map(([, field, label]) => <Field key={field} label={label}><input aria-label={label} type="number" min="0.01" step="0.01" className={INPUT} disabled={editorLocked} value={changes.bidding[field] || ""} onChange={(event) => changeManagement({ bidding: { ...changes.bidding, [field]: event.target.value } })} /></Field>)}</>}
            {action === "remove" && <p className="rounded-xl bg-rose-50 p-3 text-sm text-rose-900 sm:col-span-2">حذف الحملة إجراء دائم لدى جوجل. للإيقاف المؤقت اختر «إيقاف».</p>}
            {action === "duplicate" && <p className="text-sm text-slate-600 sm:col-span-2">تُنسخ بنية الحملة وموادها إلى حملة موقوفة بعد مراجعة المتطلبات.</p>}
        </div>}
        {!proposal && (isCampaignEditor || action === "set_targeting") && <section className="grid gap-4 rounded-2xl border p-4 md:grid-cols-2"><h3 className="font-black md:col-span-2">المواقع واللغات</h3><div className="space-y-3"><Field label="البحث عن دولة أو مدينة"><input aria-label="بحث مواقع جوجل" className={INPUT} disabled={editorLocked} value={geoQuery} onChange={(event) => setGeoQuery(event.target.value)} placeholder="السعودية، الرياض…" /></Field><MultiOptions label="المواقع الجغرافية" items={geoOptions} value={targetingValue.geo_target_ids || []} onChange={(value) => setTargeting({ geo_target_ids: value })} disabled={editorLocked} empty="ابحث باسم الدولة أو المدينة ثم اختر المواقع المطلوبة." /></div>{(isCampaignEditor ? type : selection.campaignType) === "SEARCH" ? <p className="self-center text-sm leading-7 text-slate-600">تطابق جوجل لغة إعلانات البحث وصفحات الوصول تلقائيًا. اكتب النصوص وصفحة الوصول بلغة العملاء المطلوبة؛ لا يُرسل استهداف يدوي للغة.</p> : <MultiOptions label="لغات العملاء" items={languages} value={targetingValue.language_ids || []} onChange={(value) => setTargeting({ language_ids: value })} disabled={editorLocked} />}</section>}
        {!proposal && (isCampaignEditor || entityCreative || action === "update_ad") && <div className="space-y-4">
            {draft.groups.map((group, index) => {
                const fields = creativeFieldsFor(type, group.ad_format);
                const groupControls = isCampaignEditor || action === "add_group" || action === "update_group_assets";
                const isAppEngagement = type === "APP" && draft.app?.subtype === "ENGAGEMENT";
                return <section key={index} className="space-y-4 rounded-2xl border border-slate-200 p-4" aria-label={`محرر مجموعة جوجل ${index + 1}`}>
                    <header className="flex items-center justify-between gap-3"><h3 className="font-black">{type === "PERFORMANCE_MAX" ? "مجموعة مواد الإعلان" : "المجموعة الإعلانية"} {index + 1}</h3>{isCampaignEditor && draft.groups.length > 1 && <button type="button" className="text-sm font-bold text-rose-700" disabled={editorLocked} onClick={() => changeDraft((current) => ({ ...current, groups: current.groups.filter((_, position) => position !== index) }))}>إزالة المجموعة</button>}</header>
                    <div className="grid gap-3 md:grid-cols-2">
                        <Field label={`اسم المجموعة ${index + 1}`}><input aria-label={`اسم مجموعة جوجل ${index + 1}`} className={INPUT} disabled={editorLocked} value={group.name} onChange={(event) => changeGroup(index, { name: event.target.value })} /></Field>
                        <Field label={`صيغة الإعلان ${index + 1}`}><select aria-label={`صيغة إعلان جوجل ${index + 1}`} className={INPUT} disabled={editorLocked} value={group.ad_format || "AUTO"} onChange={(event) => changeGroupFormat(index, event.target.value)}>{(capability?.formats || [googleDefaultGroup(type).ad_format]).map((format) => <option key={format} value={format}>{FORMAT_LABELS[format] || "صيغة متخصصة"}</option>)}</select></Field>
                        {fields.includes("headlines") && <TextList label={`عناوين المجموعة ${index + 1}`} value={group.headlines} maxLength={group.ad_format === "VIDEO" || ["PRODUCT", "CAROUSEL"].includes(group.ad_format) ? 40 : 30} disabled={editorLocked} onChange={(value) => changeGroup(index, { headlines: value })} note="كل عنوان في سطر مستقل." />}
                        {fields.includes("descriptions") && <TextList label={`أوصاف المجموعة ${index + 1}`} value={group.descriptions} maxLength={90} disabled={editorLocked} onChange={(value) => changeGroup(index, { descriptions: value })} note="كل وصف في سطر مستقل." />}
                        {fields.includes("long_headlines") && <TextList label={`عناوين طويلة للمجموعة ${index + 1}`} value={group.long_headlines} maxLength={90} disabled={editorLocked} onChange={(value) => changeGroup(index, { long_headlines: value })} />}
                        {fields.includes("final_urls") && (type !== "APP" || isAppEngagement) && <TextList label={`روابط الوصول للمجموعة ${index + 1}`} value={group.final_urls} disabled={editorLocked} onChange={(value) => changeGroup(index, { final_urls: value })} note={isAppEngagement ? "رابط التطبيق الشامل للوصول إلى الوجهة داخل التطبيق." : "رابط صفحة المنتج أو صفحة الهبوط، في كل سطر."} />}
                        {type === "APP" && !isAppEngagement && <p className="self-center text-xs text-slate-500">وجهة الإعلان هي التطبيق المختار في متجر التطبيقات.</p>}
                        {group.ad_format === "DSA" && <p className="text-xs text-slate-500">يولّد جوجل العناوين من الموقع. أضف الأوصاف وحدّد نطاق الموقع ولغته في إعدادات البحث.</p>}
                        {fields.includes("business_name") && <Field label={`اسم النشاط للمجموعة ${index + 1}`}><input aria-label={`اسم نشاط جوجل ${index + 1}`} maxLength={25} className={INPUT} disabled={editorLocked} value={group.business_name || ""} onChange={(event) => changeGroup(index, { business_name: event.target.value })} /></Field>}
                        {type === "SEARCH" && groupControls && <div className="md:col-span-2"><KeywordsEditor value={group.keywords || []} suffix={String(index + 1)} dsa={group.ad_format === "DSA"} disabled={editorLocked} onChange={(value) => changeGroup(index, { keywords: value })} /></div>}
                        {type === "PERFORMANCE_MAX" && groupControls && <TextList label={`مواضيع البحث للمجموعة ${index + 1}`} value={group.search_themes || []} maxLength={80} disabled={editorLocked} onChange={(value) => changeGroup(index, { search_themes: value })} note="حتى 50 موضوعًا؛ كل موضوع في سطر مستقل. يحدد جوجل مدى استخدام الإشارة." />}
                        {groupControls && (["PERFORMANCE_MAX", "DEMAND_GEN"].includes(type) || type === "APP" && draft.app?.subtype === "INSTALLS") && <MultiOptions label={type === "PERFORMANCE_MAX" ? "إشارات جمهور المجموعة" : "جمهور المجموعة"} items={audienceOptions} value={group.audience_ids || []} disabled={editorLocked} onChange={(value) => changeGroup(index, { audience_ids: value })} />}
                        {groupControls && (["SEARCH", "DISPLAY"].includes(type) || isAppEngagement) && <MultiOptions label="إعادة الاستهداف" items={userListOptions} value={group.user_list_ids || []} disabled={editorLocked} onChange={(value) => changeGroup(index, { user_list_ids: value })} />}
                        {groupControls && draft.shopping && <div className="md:col-span-2"><MultiOptions label={`منتجات المجموعة ${index + 1}`} items={lookupState.products.map((item) => ({ ...item, id: item.offer_id, name: item.title || item.name, eligible: googleOfferIsEligible(item) }))} value={group.product_offer_ids || []} disabled={editorLocked} onChange={(value) => changeGroup(index, { product_offer_ids: value })} empty="اختر حساب المنتجات أولًا. تظهر المنتجات المؤهّلة حسب بيانات جوجل." /></div>}
                        {groupControls && type === "HOTEL" && <MultiOptions label="فنادق المجموعة" items={lookupState.hotels.flatMap((item) => item.hotels || (item.hotel_id ? [{ ...item, id: item.hotel_id }] : []))} value={group.hotel_ids || []} disabled={editorLocked} onChange={(value) => changeGroup(index, { hotel_ids: value })} />}
                        {groupControls && type === "DEMAND_GEN" && <MultiOptions label="قنوات Demand Gen" items={Object.entries(CHANNEL_LABELS).map(([id, name]) => ({ id, name }))} value={group.channels || []} disabled={editorLocked} onChange={(value) => changeGroup(index, { channels: value })} />}
                    </div>
                    {assetFields.some(([field]) => fields.includes(field)) && <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">{assetFields.filter(([field]) => fields.includes(field)).map(([field, label, kind]) => <AssetPicker key={field} label={`${label} للمجموعة ${index + 1}`} kind={kind} assets={assets} value={group[field] || []} disabled={editorLocked} onChange={(value) => changeGroup(index, { [field]: value })} />)}</div>}
                    {group.ad_format === "CAROUSEL" && <fieldset className="space-y-3"><legend className="font-bold">بطاقات الإعلان المتتابعة · 2 إلى 10 بطاقات</legend>{(group.carousel_cards || []).map((card, cardIndex) => <div key={cardIndex} className="grid gap-3 rounded-xl bg-slate-50 p-3 md:grid-cols-2"><Field label={`عنوان البطاقة ${cardIndex + 1}`}><input aria-label={`عنوان بطاقة جوجل ${cardIndex + 1}`} maxLength={40} className={INPUT} disabled={editorLocked} value={card.headline || ""} onChange={(event) => changeGroup(index, { carousel_cards: group.carousel_cards.map((item, position) => position === cardIndex ? { ...item, headline: event.target.value } : item) })} /></Field><Field label={`رابط البطاقة ${cardIndex + 1}`}><input type="url" className={INPUT} disabled={editorLocked} value={card.final_url || ""} onChange={(event) => changeGroup(index, { carousel_cards: group.carousel_cards.map((item, position) => position === cardIndex ? { ...item, final_url: event.target.value } : item) })} /></Field>{[["image_asset", "صورة أفقية"], ["square_image_asset", "صورة مربعة"], ["portrait_image_asset", "صورة عمودية"]].map(([field, label]) => <AssetPicker key={field} label={`${label} للبطاقة ${cardIndex + 1}`} kind="IMAGE" assets={assets} value={card[field] ? [card[field]] : []} disabled={editorLocked} onChange={(value) => changeGroup(index, { carousel_cards: group.carousel_cards.map((item, position) => { if (position !== cardIndex) return item; const next = { ...item }; if (value.length) next[field] = value[value.length - 1]; else delete next[field]; return next; }) })} />)}<button type="button" className="text-sm text-rose-700" disabled={editorLocked} onClick={() => changeGroup(index, { carousel_cards: group.carousel_cards.filter((_, position) => position !== cardIndex) })}>إزالة البطاقة</button></div>)}<button type="button" className={BUTTON} disabled={editorLocked || (group.carousel_cards || []).length >= 10} onClick={() => changeGroup(index, { carousel_cards: [...(group.carousel_cards || []), { headline: "", final_url: "" }] })}>إضافة بطاقة</button></fieldset>}
                    {showCreative && <details className="rounded-xl bg-slate-50 p-4"><summary className="cursor-pointer text-sm font-bold">معاينة النصوص والمادة الأولى</summary><article className="mt-3 max-w-xl rounded-xl border bg-white p-4">{assets.find((asset) => assetId(asset) === group.image_assets?.[0])?.preview_url && <img alt="معاينة مادة المجموعة" src={assets.find((asset) => assetId(asset) === group.image_assets?.[0]).preview_url} className="mb-3 max-h-52 w-full rounded-xl object-contain" />}<p className="text-xs text-emerald-700">إعلان · {group.business_name || selection.accountName}</p><h4 className="mt-2 text-lg font-bold text-blue-700">{group.headlines?.filter(Boolean).slice(0, 3).join(" | ") || "عنوان الإعلان"}</h4><p className="mt-2 whitespace-pre-wrap text-sm">{group.descriptions?.filter(Boolean).join(" ") || "وصف الإعلان"}</p><p className="mt-2 break-all text-xs text-slate-500" dir="ltr">{group.final_urls?.[0]}</p></article><p className="mt-2 text-xs text-slate-500">معاينة تقريبية؛ يختار جوجل ترتيب النصوص والمواد حسب موضع العرض.</p></details>}
                    {(group.ads || []).map((ad, adIndex) => <AdditionalGoogleAdEditor key={adIndex} ad={ad} type={type} assets={assets} label={`إعلان إضافي ${adIndex + 1} للمجموعة ${index + 1}`} disabled={editorLocked} onChange={(patch) => changeGroup(index, { ads: group.ads.map((item, position) => position === adIndex ? { ...item, ...patch } : item) })} onRemove={() => changeGroup(index, { ads: group.ads.filter((_, position) => position !== adIndex) })} />)}
                </section>;
            })}
            {isCampaignEditor && <button type="button" disabled={editorLocked || draft.groups.length >= 100} className={BUTTON} onClick={() => changeDraft((current) => ({ ...current, groups: [...current.groups, googleDefaultGroup(type, current.groups.length + 1)] }))}>{type === "PERFORMANCE_MAX" ? "إضافة مجموعة مواد" : "إضافة مجموعة إعلانية"}</button>}
        </div>}
        {isCampaignEditor && !proposal && <details className="rounded-2xl border border-slate-200 p-4">
            <summary className="cursor-pointer font-black">مكتبة المواد · صور وفيديو وإعلان HTML5 جاهز</summary>
            <p className="mt-2 text-xs leading-5 text-slate-500">تُعرض معاينة مستقلة للمادة قبل إرسالها إلى جوجل. بعد التحقق تصبح متاحة في اختيارات المجموعة.</p>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
                <Field label="اسم المادة"><input aria-label="اسم مادة جوجل" maxLength={100} className={INPUT} disabled={busy} value={assetName} onChange={(event) => { invalidate(); setAssetName(event.target.value); }} /></Field>
                <Field label="صورة الإعلان · حتى 5 ميغابايت"><input key={`image-${uploadKey}`} type="file" accept="image/jpeg,image/png,image/gif" aria-label="رفع صورة جوجل" className={INPUT} disabled={busy} onChange={(event) => { invalidate(); setImageFile(null); const file = event.target.files?.[0]; if (file && !["image/jpeg", "image/png", "image/gif"].includes(file.type)) { setError("اختر صورة PNG أو JPG أو GIF."); return; } if (file?.size > 5 * 1024 * 1024) { setError("حجم الصورة يجب ألا يتجاوز 5 ميغابايت."); return; } setImageFile(file || null); }} /></Field>
                <button type="button" className={BUTTON} disabled={busy || !imageFile} onClick={prepareImage}>معاينة إضافة الصورة</button>
                <Field label="رابط فيديو YouTube"><input aria-label="فيديو YouTube لجوجل" className={INPUT} disabled={busy} value={videoInput} onChange={(event) => { invalidate(); setVideoInput(event.target.value); }} placeholder="https://www.youtube.com/watch?v=…" /></Field>
                <button type="button" className={BUTTON} disabled={busy || !videoInput.trim()} onClick={prepareVideo}>معاينة إضافة الفيديو</button>
                <Field label="ملف إعلان HTML5 جاهز · ZIP حتى 5 ميغابايت" note="يحتاج حسابًا مؤهّلًا لدى جوجل لإعلانات HTML5. بعد الاعتماد والتحقق يظهر الملف في اختيار إعلان HTML5 الجاهز."><input key={`html5-${uploadKey}`} type="file" accept=".zip,application/zip,application/x-zip-compressed" aria-label="رفع إعلان HTML5 جوجل" className={INPUT} disabled={busy} onChange={(event) => { invalidate(); setHtml5File(null); const file = event.target.files?.[0]; if (file && !/\.zip$/i.test(file.name)) { setError("اختر ملف ZIP لإعلان HTML5 الجاهز."); return; } if (file?.size > 5 * 1024 * 1024) { setError("حجم ملف HTML5 يجب ألا يتجاوز 5 ميغابايت."); return; } setHtml5File(file || null); }} /></Field>
                <button type="button" className={BUTTON} disabled={busy || !html5File} onClick={prepareHtml5}>معاينة إضافة إعلان HTML5</button>
            </div>
        </details>}
        {!proposal && <form onSubmit={prepare} className="space-y-4 rounded-2xl bg-slate-50 p-4"><Field label="سبب الإنشاء أو التغيير"><textarea aria-label="سبب عملية جوجل" className={INPUT} rows={2} required minLength={5} maxLength={500} disabled={busy} value={reason} onChange={(event) => { invalidate(); setReason(event.target.value); }} /></Field><div className="flex flex-wrap gap-3">{isCampaignEditor && <button type="button" className={BUTTON} disabled={busy || !draft.name.trim() || permissions.can_draft === false} onClick={save}>{busy ? "جارٍ الحفظ…" : draftRecord ? "حفظ تعديلات المسودة" : "حفظ المسودة"}</button>}<button type="submit" className={PRIMARY} disabled={busy || isCampaignEditor && (!canCreate || dirty || !draftRecord) || !isCampaignEditor && (permissions.can_manage === false || currentCapability?.manage === false)}>{busy ? "جارٍ تجهيز المعاينة…" : "معاينة العملية في جوجل"}</button>{isCampaignEditor && <span className="self-center text-xs text-slate-500">{dirty ? "احفظ التعديلات لفتح المعاينة." : "هذه النسخة محفوظة وجاهزة للمراجعة."}</span>}</div></form>}
        {proposal && <GoogleProposalReview proposal={proposal} summary={proposalSummary} assets={assets} productNames={productNames} googleProducts={lookupState.products} productMerchantId={merchantId} materialPreview={["upload_image", "upload_asset"].includes(proposal.action) ? imagePreview : null} approved={approvedDigest === proposal.confirmation_digest} onApprovalChange={(value) => setApprovedDigest(value ? proposal.confirmation_digest : null)} onExecute={execute} onRefresh={refreshProposal} onReconcile={reconcile} onEdit={invalidate} busy={busy} canExecute={canApprove} executionStarted={executionStarted} />}
        {proposal && entityVerified(proposal) && (proposal.asset || proposal.after?.asset || proposal.after?.resource_name && !proposal.created_campaign_id) && <button type="button" className={PRIMARY} onClick={invalidate}>العودة للمسودة واختيار المادة</button>}
        {!canApprove && <p className="text-sm text-amber-900">يمكنك تجهيز المسودة حسب صلاحياتك. اعتماد الإنفاق والتنفيذ يحتاج صلاحية الاعتماد على الحساب.</p>}
    </section>;
}

