import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowsClockwise, CheckCircle, Clock, Ghost, WarningCircle, X } from "@phosphor-icons/react";
import { toast } from "sonner";

import SnapchatCampaignManagementPanel from "../components/marketing/SnapchatCampaignManagementPanel";
import SnapchatFinancialEntityTable from "../components/marketing/SnapchatFinancialEntityTable";
import UnifiedMarketingOrdersPanel from "../components/marketing/UnifiedMarketingOrdersPanel";
import api, { formatApiErrorDetail } from "../lib/api";
import { snapchatV2SpendDisplay } from "../lib/snapchatV2SpendDisplay";
import { getSnapchatEntitySettings } from "../services/snapchatCampaignManagement";
import { readSnapchatEntityPage } from "../services/snapchatV2EntityReads";

const ENTITY_TABS = [
    { id: "campaign", label: "الحملات" },
    { id: "ad_group", label: "Ad Squads" },
    { id: "ad", label: "Ads" },
];

const DEFAULT_ENTITY_CONTROLS = {
    page: 1,
    pageSize: 25,
    search: "",
    activeOnly: false,
    sortBy: "default",
    sortDirection: "desc",
};

function blockedTargetedSettings(entityType, unifiedEntityId, reason, item = null) {
    return {
        ...(item || {}),
        entity_type: entityType,
        unified_entity_id: unifiedEntityId,
        provider_entity_id: item?.provider_entity_id || null,
        quality: {
            ...(item?.quality || {}),
            settings_status: "settings_sync_failed",
            reason,
            financial_controls_allowed: false,
            financial_field_controls: {},
        },
    };
}

export function validateTargetedSettings(items, { entityType, unifiedEntityId, parentUnifiedId, accountId }) {
    const item = (Array.isArray(items) ? items : []).find(
        (candidate) => String(candidate?.unified_entity_id || "").trim() === unifiedEntityId,
    );
    if (!item) return blockedTargetedSettings(entityType, unifiedEntityId, "لم تُرجع القراءة المستهدفة الكيان المحدد.");
    const problems = [];
    if (String(item.entity_type || "") !== entityType) problems.push("entity_type لا يطابق المطلوب");
    if (!accountId || String(item.ad_account_id || "") !== accountId) problems.push("ad_account_id لا يطابق الحساب المحدد");
    if (String(item.provider_entity_id || "") !== unifiedEntityId) problems.push("provider_entity_id لا يطابق Unified ID");
    if (item.mapping_status !== "verified" || item.mapping_verified !== true) problems.push("mapping_status غير موثق");
    if (item?.identity_contract?.name !== "snapchat_v2_provider_id_is_unified_id_v1" || item?.identity_contract?.ids_equal !== true) problems.push("identity_contract غير موثق");
    if (entityType === "ad_squad" && String(item.provider_parent_id || "") !== parentUnifiedId) problems.push("provider_parent_id لا يطابق الحملة الأب");
    return problems.length
        ? blockedTargetedSettings(entityType, unifiedEntityId, `فشل تحقق القراءة المستهدفة: ${problems.join("، ")}.`, item)
        : item;
}

function localDateInTimezone(timezone) {
    try {
        return new Intl.DateTimeFormat("en-CA", {
            timeZone: timezone || "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit",
        }).format(new Date());
    } catch {
        return new Date().toISOString().slice(0, 10);
    }
}

function localTimeInTimezone(timezone, nowMs) {
    try {
        return new Intl.DateTimeFormat("en-GB", {
            timeZone: timezone || "Asia/Riyadh", hour: "2-digit", minute: "2-digit", hour12: false,
        }).format(new Date(nowMs));
    } catch {
        return "—";
    }
}

function localHourInTimezone(timezone, nowMs) {
    try {
        const value = new Intl.DateTimeFormat("en-GB", {
            timeZone: timezone || "Asia/Riyadh", hour: "2-digit", hour12: false,
        }).format(new Date(nowMs));
        const hour = Number(value);
        return Number.isFinite(hour) ? hour : null;
    } catch {
        return null;
    }
}

function money(value, currency = "USD") {
    if (value === null || value === undefined || value === "") return "—";
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "—";
    return `${amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
}

function contractMoney(value) {
    return money(value?.amount, value?.currency || "");
}

function number(value) {
    if (value === null || value === undefined || value === "") return "—";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString("en-US") : "—";
}

function statusTone(status) {
    if (["complete", "healthy", "released"].includes(status)) return "text-emerald-700 bg-emerald-50 border-emerald-200";
    if (["partial", "running", "held", "pending"].includes(status)) return "text-amber-700 bg-amber-50 border-amber-200";
    return "text-slate-700 bg-slate-50 border-slate-200";
}

function displayHourStatus(row, selectedDate, timezone, nowMs) {
    const today = localDateInTimezone(timezone);
    const currentHour = localHourInTimezone(timezone, nowMs);
    const rowHour = Number(String(row?.local_hour || "").slice(0, 2));
    if (selectedDate > today) return "future";
    if (selectedDate === today && Number.isFinite(rowHour) && currentHour !== null) {
        if (rowHour > currentHour) return "future";
        if (rowHour === currentHour) return row?.spend_native == null ? "provisional_unavailable" : "provisional";
    }
    const start = Date.parse(row?.hour_start_utc || "");
    const end = Date.parse(row?.hour_end_utc || "");
    if (!Number.isFinite(start) || !Number.isFinite(end)) return row?.status || "—";
    if (nowMs >= start && nowMs < end) return row?.spend_native == null ? "provisional_unavailable" : "provisional";
    if (nowMs < start) return "future";
    if (row?.status === "future") return "awaiting_refresh";
    return row?.status || "—";
}

function managementLevel(level) {
    if (level === "ad_group") return "ad_squads";
    if (level === "ad") return "ads";
    return "campaigns";
}

export default function SnapchatV2Page() {
    const [status, setStatus] = useState(null);
    const [readiness, setReadiness] = useState(null);
    const [readinessLoading, setReadinessLoading] = useState(true);
    const [report, setReport] = useState(null);
    const [hourly, setHourly] = useState(null);
    const [campaignContract, setCampaignContract] = useState(null);
    const [sallaSummary, setSallaSummary] = useState({});
    const [childContract, setChildContract] = useState(null);
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [appliedRange, setAppliedRange] = useState(null);
    const [entityLevel, setEntityLevel] = useState("campaign");
    const [selectedCampaign, setSelectedCampaign] = useState(null);
    const [selectedAdGroup, setSelectedAdGroup] = useState(null);
    const [managementTarget, setManagementTarget] = useState(null);
    const [settingsByEntityId, setSettingsByEntityId] = useState({});
    const [targetedSettingsOverride, setTargetedSettingsOverride] = useState(null);
    const [entityControls, setEntityControls] = useState(DEFAULT_ENTITY_CONTROLS);
    const [settingsLoading, setSettingsLoading] = useState(false);
    const [loading, setLoading] = useState(true);
    const [entityLoading, setEntityLoading] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [error, setError] = useState("");
    const [clockNow, setClockNow] = useState(() => Date.now());
    const loadEpochRef = useRef(0);
    const viewEpochRef = useRef(0);
    const settingsEpochRef = useRef(0);
    const entityRequestEpochRef = useRef(0);
    const targetedSettingsEpochRef = useRef(0);

    const account = status?.selected_account || null;
    const accountId = account?.ad_account_id || "";
    const currency = account?.currency || report?.currency || "USD";
    const accountTimezone = account?.timezone || "America/Los_Angeles";
    const activeContract = entityLevel === "campaign" ? campaignContract : childContract;

    const beginViewContext = useCallback(() => {
        const nextEpoch = viewEpochRef.current + 1;
        viewEpochRef.current = nextEpoch;
        settingsEpochRef.current += 1;
        entityRequestEpochRef.current += 1;
        targetedSettingsEpochRef.current += 1;
        setSettingsByEntityId({});
        setTargetedSettingsOverride(null);
        setSettingsLoading(false);
        return nextEpoch;
    }, []);

    const loadEntitySettings = useCallback(async ({
        entityType,
        rows = [],
        parentUnifiedId = "",
        expectedAccountId = "",
        viewEpoch,
    }) => {
        const expectedViewEpoch = Number(viewEpoch);
        if (viewEpochRef.current !== expectedViewEpoch) return;
        const settingsEpoch = settingsEpochRef.current + 1;
        settingsEpochRef.current = settingsEpoch;
        const requestIsCurrent = () => (
            viewEpochRef.current === expectedViewEpoch
            && settingsEpochRef.current === settingsEpoch
        );
        const governedRows = rows.filter((row) => ["campaign", "ad_group"].includes(row?.entity?.level));
        if (!governedRows.length) {
            if (requestIsCurrent()) {
                setSettingsByEntityId({});
                setSettingsLoading(false);
            }
            return;
        }
        setSettingsLoading(true);
        const rowIds = new Set(governedRows.map(
            (row) => String(row?.entity?.id || "").trim(),
        ).filter(Boolean));
        const unavailableForRow = (id, status = "settings_not_loaded", reason = "لم تُحمّل إعدادات الكيان من مزامنة Snapchat الأصلية.") => ({
            entity_type: entityType,
            unified_entity_id: id,
            provider_entity_id: null,
            ad_account_id: expectedAccountId || null,
            quality: {
                settings_status: status,
                freshness_seconds: null,
                freshness_threshold_seconds: 1800,
                reason,
                financial_controls_allowed: false,
                financial_field_controls: {},
            },
        });
        if (!requestIsCurrent()) return;
        setSettingsByEntityId(Object.fromEntries(
            [...rowIds].map((id) => [id, unavailableForRow(id)]),
        ));
        try {
            const items = await getSnapchatEntitySettings({
                entityType,
                unifiedEntityIds: [...rowIds],
                parentUnifiedId,
                limit: Math.min(100, Math.max(1, rowIds.size)),
            });
            const next = {};
            items.forEach((item) => {
                const key = String(item?.unified_entity_id || "").trim();
                if (!key || !rowIds.has(key)) return;
                const providerAccountId = String(item?.ad_account_id || "").trim();
                const accountBound = Boolean(
                    expectedAccountId
                    && providerAccountId
                    && providerAccountId === expectedAccountId,
                );
                next[key] = accountBound ? item : {
                    ...item,
                    quality: {
                        ...(item?.quality || {}),
                        settings_status: "settings_sync_failed",
                        reason: providerAccountId
                            ? "فشل إثبات ارتباط إعدادات الكيان بالحساب الإعلاني المحدد."
                            : "لم تُحمّل هوية الحساب الإعلاني من إعدادات Snapchat.",
                        financial_controls_allowed: false,
                        financial_field_controls: {},
                    },
                };
            });
            governedRows.forEach((row) => {
                const id = String(row?.entity?.id || "").trim();
                if (id && !next[id]) {
                    next[id] = unavailableForRow(id);
                }
            });
            if (!requestIsCurrent()) return;
            setSettingsByEntityId(next);
        } catch (_requestError) {
            const failed = Object.fromEntries(governedRows.map((row) => {
                const id = String(row?.entity?.id || "").trim();
                return [id, unavailableForRow(
                    id,
                    "settings_sync_failed",
                    "فشل جلب إعدادات Snapchat.",
                )];
            }).filter(([id]) => id));
            if (!requestIsCurrent()) return;
            setSettingsByEntityId(failed);
        } finally {
            if (requestIsCurrent()) setSettingsLoading(false);
        }
    }, []);

    const load = useCallback(async (requestedRange = null) => {
        const loadEpoch = loadEpochRef.current + 1;
        loadEpochRef.current = loadEpoch;
        const viewEpoch = beginViewContext();
        const loadRequestIsCurrent = () => loadEpochRef.current === loadEpoch;
        const viewRequestIsCurrent = () => viewEpochRef.current === viewEpoch;
        const reportRequestIsCurrent = () => loadRequestIsCurrent() && viewRequestIsCurrent();
        let readinessStarted = false;
        setLoading(true);
        setEntityLoading(false);
        setReadinessLoading(true);
        setReadiness(null);
        setError("");
        try {
            const { data: statusData } = await api.get("/integrations-v2/snapchat-v2/status");
            if (!loadRequestIsCurrent()) return;
            readinessStarted = true;
            api.get("/integrations-v2/snapchat-v2/unified-readiness")
                .then(({ data }) => {
                    if (loadRequestIsCurrent()) setReadiness(data || null);
                })
                .catch(() => {
                    if (loadRequestIsCurrent()) setReadiness({ ready: false, reasons: ["readiness_request_failed"] });
                })
                .finally(() => {
                    if (loadRequestIsCurrent()) setReadinessLoading(false);
                });
            if (!viewRequestIsCurrent()) return;
            setStatus(statusData);
            const timezone = statusData?.selected_account?.timezone || "America/Los_Angeles";
            const today = localDateInTimezone(timezone);
            const range = requestedRange || appliedRange || { dateFrom: today, dateTo: today };
            if (!dateFrom) setDateFrom(range.dateFrom);
            if (!dateTo) setDateTo(range.dateTo);
            setAppliedRange(range);
            const common = { action_report_time: "conversion", timezone: "account" };
            const initialControls = { ...DEFAULT_ENTITY_CONTROLS };
            const [reportResult, hourlyResult, campaignsResult] = await Promise.all([
                api.get("/integrations-v2/snapchat-v2/report", { params: { ...common, date_from: range.dateFrom, date_to: range.dateTo } }),
                api.get("/integrations-v2/snapchat-v2/hourly", { params: { ...common, report_date: range.dateTo } }),
                readSnapchatEntityPage({ level: "campaign", dateFrom: range.dateFrom, dateTo: range.dateTo, controls: initialControls }),
            ]);
            if (!reportRequestIsCurrent()) return;
            setReport(reportResult.data);
            setHourly(hourlyResult.data);
            const nextCampaignContract = campaignsResult.data?.unified || null;
            setCampaignContract(nextCampaignContract);
            setEntityControls(initialControls);
            setSallaSummary(campaignsResult.data?.salla?.summary || {});
            void loadEntitySettings({
                entityType: "campaign",
                rows: nextCampaignContract?.rows || [],
                expectedAccountId: statusData?.selected_account?.ad_account_id || "",
                viewEpoch,
            });
            setChildContract(null);
            setSelectedCampaign(null);
            setSelectedAdGroup(null);
            setManagementTarget(null);
            setEntityLevel("campaign");
            setClockNow(Date.now());
        } catch (requestError) {
            if (!loadRequestIsCurrent()) return;
            if (!readinessStarted) {
                setReadiness({ ready: false, reasons: ["readiness_request_failed"] });
                setReadinessLoading(false);
            }
            if (!viewRequestIsCurrent()) return;
            const message = formatApiErrorDetail(requestError.response?.data?.detail) || "تعذر تحميل بيانات Snapchat V2";
            setError(message);
            toast.error(message);
        } finally {
            if (reportRequestIsCurrent()) setLoading(false);
        }
    }, [appliedRange, beginViewContext, dateFrom, dateTo, loadEntitySettings]);

    useEffect(() => {
        load();
        // Reads Snapchat V2 through its unified adapter. Dashboard, AI, and V1 remain untouched.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        const timer = window.setInterval(() => setClockNow(Date.now()), 60_000);
        return () => window.clearInterval(timer);
    }, []);

    async function openChildren(row) {
        if (!appliedRange) return;
        const viewEpoch = beginViewContext();
        const requestIsCurrent = () => viewEpochRef.current === viewEpoch;
        setLoading(false);
        setEntityLoading(true);
        setError("");
        const nextControls = { ...DEFAULT_ENTITY_CONTROLS };
        try {
            if (row.entity.level === "campaign") {
                const { data } = await readSnapchatEntityPage({
                    level: "ad_group",
                    dateFrom: appliedRange.dateFrom,
                    dateTo: appliedRange.dateTo,
                    controls: nextControls,
                    campaignId: row.entity.id,
                });
                if (!requestIsCurrent()) return;
                setSelectedCampaign(row);
                setSelectedAdGroup(null);
                setManagementTarget(null);
                const nextChildContract = data?.unified || null;
                setChildContract(nextChildContract);
                setEntityLevel("ad_group");
                setEntityControls(nextControls);
                void loadEntitySettings({
                    entityType: "ad_squad",
                    rows: nextChildContract?.rows || [],
                    parentUnifiedId: row.entity.id,
                    expectedAccountId: accountId,
                    viewEpoch,
                });
            } else if (row.entity.level === "ad_group") {
                const campaignId = row.entity.campaign_id || selectedCampaign?.entity?.id;
                const { data } = await readSnapchatEntityPage({
                    level: "ad",
                    dateFrom: appliedRange.dateFrom,
                    dateTo: appliedRange.dateTo,
                    controls: nextControls,
                    campaignId,
                    adSquadId: row.entity.id,
                });
                if (!requestIsCurrent()) return;
                setSelectedAdGroup(row);
                setManagementTarget(null);
                setChildContract(data?.unified || null);
                setEntityLevel("ad");
                setEntityControls(nextControls);
            }
        } catch (requestError) {
            if (!requestIsCurrent()) return;
            const message = formatApiErrorDetail(requestError.response?.data?.detail) || "تعذر تحميل المستوى التفصيلي من Snapchat V2";
            setError(message);
            toast.error(message);
        } finally {
            if (requestIsCurrent()) setEntityLoading(false);
        }
    }

    async function returnToAdGroups() {
        if (selectedCampaign) await openChildren(selectedCampaign);
    }

    function returnToCampaigns() {
        const viewEpoch = beginViewContext();
        setLoading(false);
        setEntityLoading(false);
        setEntityLevel("campaign");
        setChildContract(null);
        setSelectedCampaign(null);
        setSelectedAdGroup(null);
        setManagementTarget(null);
        setEntityControls({
            page: Number(campaignContract?.page || 1),
            pageSize: Number(campaignContract?.page_size || 25),
            search: campaignContract?.filters?.search || "",
            activeOnly: campaignContract?.filters?.active_only === true,
            sortBy: campaignContract?.sort?.by || "default",
            sortDirection: campaignContract?.sort?.direction || "desc",
        });
        setClockNow(Date.now());
        void loadEntitySettings({
            entityType: "campaign",
            rows: campaignContract?.rows || [],
            expectedAccountId: accountId,
            viewEpoch,
        });
    }

    async function loadEntityPage(nextControls) {
        if (!appliedRange) return;
        const requestEpoch = entityRequestEpochRef.current + 1;
        entityRequestEpochRef.current = requestEpoch;
        const viewEpoch = viewEpochRef.current;
        const requestIsCurrent = () => (
            entityRequestEpochRef.current === requestEpoch
            && viewEpochRef.current === viewEpoch
        );
        setEntityLoading(true);
        setError("");
        try {
            const { data } = await readSnapchatEntityPage({
                level: entityLevel,
                dateFrom: appliedRange.dateFrom,
                dateTo: appliedRange.dateTo,
                controls: nextControls,
                campaignId: entityLevel === "campaign" ? "" : selectedCampaign?.entity?.id || "",
                adSquadId: entityLevel === "ad" ? selectedAdGroup?.entity?.id || "" : "",
            });
            if (!requestIsCurrent()) return;
            const nextContract = data?.unified || null;
            if (entityLevel === "campaign") {
                setCampaignContract(nextContract);
                setSallaSummary(data?.salla?.summary || {});
            } else {
                setChildContract(nextContract);
            }
            setEntityControls(nextControls);
            if (entityLevel !== "ad") {
                void loadEntitySettings({
                    entityType: entityLevel === "campaign" ? "campaign" : "ad_squad",
                    rows: nextContract?.rows || [],
                    parentUnifiedId: entityLevel === "ad_group" ? selectedCampaign?.entity?.id || "" : "",
                    expectedAccountId: accountId,
                    viewEpoch,
                });
            }
        } catch (requestError) {
            if (!requestIsCurrent()) return;
            const message = formatApiErrorDetail(requestError.response?.data?.detail) || "تعذر تحميل صفحة الكيانات من Snapchat V2";
            setError(message);
            toast.error(message);
        } finally {
            if (requestIsCurrent()) setEntityLoading(false);
        }
    }

    function changeEntityPage(page) {
        void loadEntityPage({ ...entityControls, page });
    }

    function changeEntityControls(next) {
        void loadEntityPage({ ...entityControls, ...next, page: 1 });
    }

    function manageEntity(row) {
        const unifiedEntityId = String(row?.entity?.id || "").trim();
        const entityType = row?.entity?.level === "campaign" ? "campaign" : row?.entity?.level === "ad_group" ? "ad_squad" : "";
        const parentUnifiedId = entityType === "ad_squad" ? String(selectedCampaign?.entity?.id || "").trim() : "";
        const rowParent = entityType === "ad_squad" ? String(row?.entity?.campaign_id || "").trim() : "";
        const epoch = targetedSettingsEpochRef.current + 1;
        targetedSettingsEpochRef.current = epoch;
        setManagementTarget(row);
        if (!entityType || !unifiedEntityId) {
            setTargetedSettingsOverride(null);
            return;
        }
        setTargetedSettingsOverride({
            unifiedEntityId,
            settings: blockedTargetedSettings(entityType, unifiedEntityId, "جاري جلب إعدادات الكيان المحدد…"),
        });
        if (entityType === "ad_squad" && (!parentUnifiedId || (rowParent && rowParent !== parentUnifiedId))) {
            setTargetedSettingsOverride({
                unifiedEntityId,
                settings: blockedTargetedSettings(entityType, unifiedEntityId, "فشل تحقق الحملة الأب للـAd Squad المحددة."),
            });
            return;
        }
        void getSnapchatEntitySettings({ entityType, unifiedEntityId, parentUnifiedId, limit: 1 })
            .then((items) => {
                if (targetedSettingsEpochRef.current !== epoch) return;
                setTargetedSettingsOverride({
                    unifiedEntityId,
                    settings: validateTargetedSettings(items, { entityType, unifiedEntityId, parentUnifiedId, accountId }),
                });
            })
            .catch((requestError) => {
                if (targetedSettingsEpochRef.current !== epoch) return;
                setTargetedSettingsOverride({
                    unifiedEntityId,
                    settings: blockedTargetedSettings(
                        entityType,
                        unifiedEntityId,
                        formatApiErrorDetail(requestError?.response?.data?.detail) || "فشل جلب إعدادات Snapchat المستهدفة.",
                    ),
                });
            });
    }

    function applyRange(event) {
        event.preventDefault();
        if (!dateFrom || !dateTo || dateTo < dateFrom) {
            toast.error("تحقق من فترة التقرير قبل المتابعة.");
            return;
        }
        load({ dateFrom, dateTo });
    }

    async function syncRange() {
        if (!appliedRange || !accountId) return;
        setSyncing(true);
        try {
            const { data } = await api.post("/integrations-v2/snapchat-v2/sync", {
                ad_account_id: accountId,
                date_from: appliedRange.dateFrom,
                date_to: appliedRange.dateTo,
                action_report_time: "conversion",
                run_type: "manual",
            });
            if (data?.status === "skipped" && data?.reason === "lease_unavailable") {
                toast.warning("توجد مزامنة تلقائية قيد التشغيل الآن. أعد المحاولة بعد قليل.");
            } else if (data?.status === "complete") {
                toast.success("اكتملت مزامنة Snapchat V2 بكل مستويات التقرير");
            } else {
                toast.warning("اكتملت المزامنة مع مستوى تفصيلي جزئي؛ المالي يبقى مستقلًا ومؤكدًا.");
            }
            await load(appliedRange);
        } catch (requestError) {
            toast.error(formatApiErrorDetail(requestError.response?.data?.detail) || "تعذر تشغيل مزامنة Snapchat V2");
        } finally {
            setSyncing(false);
        }
    }

    const knownHours = useMemo(() => (hourly?.hours || []).filter((row) => (
        row?.spend_native !== null && row?.spend_native !== undefined && Number.isFinite(Number(row.spend_native))
    )), [hourly]);
    const confirmedHours = useMemo(() => (hourly?.hours || []).filter((row) => row.status === "confirmed_data"), [hourly]);
    const maxHourSpend = useMemo(() => Math.max(1, ...knownHours.map((row) => Number(row.spend_native) || 0)), [knownHours]);
    const financialDisplayStatus = status?.financial_sync_status === "complete" || status?.last_success?.financial
        ? "complete"
        : (status?.financial_sync_status || "—");
    const totals = campaignContract?.totals || null;
    const sallaTotals = totals?.commerce_outcomes || {};
    const spendDisplay = snapchatV2SpendDisplay(report);
    const accountSpendNative = spendDisplay.spendNative;
    const unallocatedSpendNative = spendDisplay.unallocatedSpendNative;
    const hasUnallocatedSpend = Number.isFinite(unallocatedSpendNative)
        && Math.abs(unallocatedSpendNative) >= 0.01;
    const hourlyBreakdownComplete = spendDisplay.hourlyBreakdownComplete;
    const snapchatPurchases = totals?.platform_outcomes?.conversions;
    const snapchatPurchaseValue = totals?.platform_outcomes?.revenue;
    const snapchatRoas = totals?.platform_outcomes?.roas;
    const managementCampaign = managementTarget?.entity?.level === "campaign"
        ? managementTarget
        : selectedCampaign;
    const managementAdGroup = managementTarget?.entity?.level === "ad_group"
        ? managementTarget
        : selectedAdGroup;
    const managementAction = managementTarget
        ? `${managementTarget.entity.provider_level}.update`
        : null;
    const effectiveSettingsByEntityId = targetedSettingsOverride?.unifiedEntityId
        ? {
            ...settingsByEntityId,
            [targetedSettingsOverride.unifiedEntityId]: targetedSettingsOverride.settings,
        }
        : settingsByEntityId;
    const managementSettings = effectiveSettingsByEntityId[managementTarget?.entity?.id]
        || managementTarget?.current_settings
        || null;
    const targetedSettingsVerified = Boolean(
        managementTarget
        && targetedSettingsOverride?.unifiedEntityId === managementTarget.entity.id
        && targetedSettingsOverride?.settings?.quality?.settings_status === "settings_complete"
        && targetedSettingsOverride?.settings?.mapping_status === "verified"
        && targetedSettingsOverride?.settings?.mapping_verified === true
        && targetedSettingsOverride?.settings?.ad_account_id === accountId
    );

    return (
        <div className="space-y-5" dir="rtl" data-testid="snapchat-v2-page">
            <header className="rounded-2xl border border-yellow-300 bg-gradient-to-br from-yellow-50 to-amber-50 p-5 sm:p-7">
                <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                    <div>
                        <div className="mb-2 flex flex-wrap items-center gap-2">
                            <span className="inline-flex items-center gap-2 rounded-full border border-yellow-300 bg-white px-3 py-1 text-xs font-black text-amber-800"><Ghost size={16} weight="fill" /> Snapchat V2</span>
                            <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-black text-emerald-700">Unified Marketing Adapter</span>
                            <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-black text-amber-800">Decision Intelligence غير مربوط</span>
                        </div>
                        <h1 className="text-3xl font-black tracking-tight">إعلانات سناب شات</h1>
                        <p className="mt-2 text-sm font-semibold text-slate-600">Snapchat V2 · توقيت الحساب {accountTimezone} · الآن {localTimeInTimezone(accountTimezone, clockNow)}</p>
                    </div>
                    <form onSubmit={applyRange} className="flex flex-wrap items-end gap-2">
                        <label className="text-xs font-black text-slate-600">من<input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} className="mt-1 block rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-bold" dir="ltr" /></label>
                        <label className="text-xs font-black text-slate-600">إلى<input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} className="mt-1 block rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-bold" dir="ltr" /></label>
                        <button type="submit" disabled={loading} className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-slate-300 bg-white px-4 text-sm font-black disabled:opacity-50"><ArrowsClockwise size={17} className={loading ? "animate-spin" : ""} /> تطبيق الفترة</button>
                        <button type="button" onClick={syncRange} disabled={syncing || !appliedRange || !accountId} className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-yellow-500 px-4 text-sm font-black text-white disabled:opacity-50"><ArrowsClockwise size={17} className={syncing ? "animate-spin" : ""} />{syncing ? "جاري المزامنة" : "مزامنة V2"}</button>
                    </form>
                </div>
            </header>

            {error && <div className="flex items-center gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-700"><WarningCircle size={22} weight="fill" /> {error}</div>}

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
                <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="text-xs font-bold text-slate-500">الحساب المعتمد</div><div className="mt-2 text-lg font-black">{account?.display_name || "—"}</div><div className="mt-1 truncate text-xs text-slate-500" dir="ltr">{accountId || "—"}</div></div>
                <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-4"><div className="text-xs font-bold text-amber-700">صرف الفترة</div><div className="mt-2 text-2xl font-black text-amber-950" data-testid="snapchat-v2-spend-headline">{money(accountSpendNative, currency)}</div><div className="mt-1 text-xs font-bold text-amber-700">{appliedRange?.dateFrom || "—"} — {appliedRange?.dateTo || "—"}</div>{hasUnallocatedSpend && <div className="mt-1 text-[11px] font-bold text-amber-800" data-testid="snapchat-v2-unallocated-spend">فرق غير موزع على الساعات: {money(unallocatedSpendNative, currency)}</div>}</div>
                <div className="rounded-xl border border-violet-200 bg-violet-50 p-4"><div className="text-xs font-bold text-violet-700">نتائج Snapchat (TOTAL)</div><div className="mt-2 text-2xl font-black text-violet-950">{number(snapchatPurchases)}</div><div className="mt-1 text-xs font-bold text-violet-700">قيمة {contractMoney(snapchatPurchaseValue)} · ROAS {number(snapchatRoas)}</div></div>
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4"><div className="text-xs font-bold text-emerald-700">نتائج سلة المنسوبة لـ Snapchat</div><div className="mt-2 text-2xl font-black text-emerald-950">{sallaTotals.status === "complete" ? number(sallaTotals.orders) : "—"}</div><div className="mt-1 text-xs font-bold text-emerald-700">قيمة الطلبات {contractMoney(sallaTotals.revenue)} · ROAS {number(sallaTotals.roas)}</div>{sallaTotals.status === "complete" && <div className="mt-1 text-[11px] font-bold text-emerald-700">ربط تفصيلي {number(sallaSummary.account_period_campaign_matched_orders)} من {number(sallaSummary.snapchat_attributed_orders)}{sallaSummary.campaign_match_coverage_pct !== null && sallaSummary.campaign_match_coverage_pct !== undefined && Number.isFinite(Number(sallaSummary.campaign_match_coverage_pct)) ? ` · تغطية ${number(sallaSummary.campaign_match_coverage_pct)}%` : ""}</div>}</div>
                <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="text-xs font-bold text-slate-500">حالة المزامنة</div><div className="mt-2 flex items-center gap-2 text-xl font-black">{hourlyBreakdownComplete ? <CheckCircle className="text-emerald-600" weight="fill" /> : <Clock className="text-amber-600" />}{hourlyBreakdownComplete ? "مكتمل" : "التوزيع الساعي قيد التحديث"}</div><div className={`mt-2 inline-flex rounded-full border px-2 py-1 text-xs font-black ${statusTone(financialDisplayStatus)}`}>Financial: {financialDisplayStatus}</div></div>
                <div data-testid="snapchat-unified-readiness" className={`rounded-xl border p-4 ${readiness?.ready ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}><div className={`text-xs font-bold ${readiness?.ready ? "text-emerald-700" : "text-amber-700"}`}>جاهزية العقد الموحد</div><div className="mt-2 flex items-center gap-2 text-xl font-black">{readiness?.ready ? <CheckCircle className="text-emerald-600" weight="fill" /> : <Clock className="text-amber-600" />}{readinessLoading ? "جارٍ التحقق" : readiness?.reasons?.includes("readiness_request_failed") ? "تعذر التحقق" : readiness?.ready ? "جاهز" : "غير مكتمل"}</div><div className="mt-2 text-[11px] font-bold text-slate-600">{readiness?.period?.date_from || "آخر يوم مغلق"} · Decision Intelligence غير مربوط</div></div>
            </section>

            <section data-testid="snapchat-financial-workspace">
                <nav className="flex min-h-14 items-end gap-6 overflow-x-auto rounded-t-2xl border border-b-0 border-slate-200 bg-white px-4">
                    {ENTITY_TABS.map((tab) => {
                        const enabled = tab.id === "campaign" || (tab.id === "ad_group" && selectedCampaign) || (tab.id === "ad" && selectedAdGroup);
                        return <button key={tab.id} type="button" disabled={!enabled} onClick={() => { if (tab.id === "campaign") returnToCampaigns(); else if (tab.id === "ad_group") returnToAdGroups(); }} className={`relative shrink-0 px-1 pb-3 pt-4 text-sm font-black ${entityLevel === tab.id ? "text-slate-950" : enabled ? "text-slate-600" : "text-slate-300"}`}>{tab.label}{entityLevel === tab.id && <span className="absolute inset-x-0 bottom-0 h-0.5 bg-amber-400" />}</button>;
                    })}
                    <span className="mr-auto pb-3 text-[11px] font-bold text-slate-400">قراءة خادمية bounded · الأطفال عند الطلب فقط</span>
                </nav>
                {(selectedCampaign || selectedAdGroup) && <div className="flex flex-wrap items-center gap-2 border-x border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs font-bold text-slate-600"><button type="button" onClick={returnToCampaigns} className="rounded-lg bg-white px-3 py-1.5 text-emerald-700 shadow-sm">كل الحملات</button>{selectedCampaign && <><span>/</span><button type="button" onClick={returnToAdGroups} className="rounded-lg px-2 py-1.5 hover:bg-white">{selectedCampaign.entity.name}</button></>}{selectedAdGroup && <><span>/</span><span className="rounded-lg bg-violet-50 px-2 py-1.5 text-violet-700">{selectedAdGroup.entity.name}</span></>}</div>}
                <SnapchatFinancialEntityTable
                    report={activeContract}
                    settingsByEntityId={effectiveSettingsByEntityId}
                    loading={loading || entityLoading}
                    onOpenChildren={openChildren}
                    onManageEntity={manageEntity}
                    onPageChange={changeEntityPage}
                    onControlsChange={changeEntityControls}
                />
            </section>

            <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
                <div className="mb-4 flex items-center justify-between gap-3"><div><h2 className="text-lg font-black">الصرف بالساعة — {appliedRange?.dateTo || "—"}</h2><p className="text-xs font-semibold text-slate-500">حسب توقيت حساب Snapchat · الساعة الحالية provisional · الساعات المستقبلية لا تُعرض كصفر</p></div><div className="text-xs font-black text-slate-500">{confirmedHours.length} ساعة مؤكدة</div></div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8">
                    {(hourly?.hours || []).map((row) => {
                        const spend = Number(row.spend_native);
                        const known = row?.spend_native !== null && row?.spend_native !== undefined && Number.isFinite(spend);
                        const pct = known ? Math.max(4, (spend / maxHourSpend) * 100) : 0;
                        const effectiveStatus = displayHourStatus(row, appliedRange?.dateTo || "", accountTimezone, clockNow);
                        return <div key={row.local_hour} className="rounded-lg border border-slate-100 bg-slate-50 p-2"><div className="text-xs font-black" dir="ltr">{row.local_hour}</div><div className="mt-2 flex h-16 items-end overflow-hidden rounded bg-white">{known && <div className="w-full rounded bg-yellow-400" style={{ height: `${pct}%` }} />}</div><div className="mt-2 text-xs font-black" dir="ltr">{known ? money(spend, currency) : "—"}</div><div className="text-[10px] font-bold text-slate-400">{effectiveStatus}</div></div>;
                    })}
                </div>
            </section>

            {managementTarget && <div className="fixed inset-0 z-[120] flex justify-end bg-slate-950/55" role="dialog" aria-modal="true" data-testid="snapchat-management-drawer">
                <div className="h-full w-full max-w-4xl overflow-y-auto bg-white p-4 shadow-2xl" dir="rtl">
                    <div className="mb-3 flex items-center justify-between rounded-xl border border-slate-200 bg-slate-50 p-3">
                        <div><div className="text-xs font-black text-amber-700">إدارة مستهدفة لكيان واحد</div><div className="font-black">{managementTarget.entity.name}</div></div>
                        <button type="button" onClick={() => { targetedSettingsEpochRef.current += 1; setManagementTarget(null); setTargetedSettingsOverride(null); }} className="rounded-xl border border-slate-200 bg-white p-2" aria-label="إغلاق لوحة الإدارة"><X size={20} /></button>
                    </div>
                    <SnapchatCampaignManagementPanel
                key={`${managementTarget?.entity?.provider_level || "create"}:${managementTarget?.entity?.id || entityLevel}`}
                accountId={accountId}
                entityLevel={managementLevel(entityLevel)}
                initialAction={managementAction}
                selectedCampaign={managementCampaign ? {
                    campaign_id: managementCampaign.entity.id,
                    provider_campaign_id: managementTarget?.entity?.level === "ad_group"
                        ? managementSettings?.provider_parent_id || null
                        : managementSettings?.provider_entity_id
                            || settingsByEntityId[managementCampaign.entity.id]?.provider_entity_id
                            || null,
                    campaign_name: managementCampaign.entity.name,
                } : null}
                selectedAdSquad={managementAdGroup ? {
                    ad_squad_id: managementAdGroup.entity.id,
                    provider_ad_squad_id: managementSettings?.provider_entity_id || null,
                    ad_squad_name: managementAdGroup.entity.name,
                } : null}
                selectedAd={managementTarget?.entity?.level === "ad" ? { ad_id: managementTarget.entity.id, ad_name: managementTarget.entity.name, ad_squad_id: managementTarget.entity.ad_group_id } : null}
                currentSettings={managementSettings}
                initiallyExpanded
                targetedSettingsVerified={targetedSettingsVerified}
                onChanged={() => { toast.success("تم التحقق من تغيير Snapchat؛ سيظهر في V2 بعد تحديث كتالوج المزامنة."); load(appliedRange); }}
                    />
                </div>
            </div>}

            <UnifiedMarketingOrdersPanel report={campaignContract} campaignId={selectedCampaign?.entity?.id || null} />
        </div>
    );
}
