import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Plus, Trash, FloppyDisk, LinkSimple, LinkBreak, Ghost, ArrowsClockwise, Eye, EyeSlash, SquaresFour, Calculator, LockKey, MagnifyingGlass, Warning, UserPlus } from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { KPI_GROUPS, SPECIAL_DASHBOARD_CARDS } from "../lib/dashboardCards";
import SecretField, { StatusBadge } from "../components/SecretField";
import OrderStatusPolicySection from "../components/OrderStatusPolicySection";
import SettlementCycleSection from "../components/SettlementCycleSection";
import { useAuth } from "../context/AuthContext";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const DEFAULT_SNAP_REDIRECT = `${BACKEND_URL}/api/snapchat/oauth/callback`;

// Stable per-row id for React keys (so deleting middle rows does not re-mount inputs)
let _rowSeq = 0;
const newRowId = () => `r${Date.now().toString(36)}_${(_rowSeq += 1)}`;
const withRowIds = (arr) => (arr || []).map((it) => ({ _rid: it?._rid || newRowId(), ...it }));

export default function Settings() {
    const { user } = useAuth();
    const [payments, setPayments] = useState([]);
    const [shippings, setShippings] = useState([]);
    const [shipApproved, setShipApproved] = useState([]);
    const [codApproved, setCodApproved] = useState([]);
    const [reportIncluded, setReportIncluded] = useState([]);
    const [hiddenCards, setHiddenCards] = useState([]);
    const [netSalesConfig, setNetSalesConfig] = useState({
        deduct_payment_fees: true,
        deduct_shipping: true,
        deduct_deferred_shipping: false,
        deduct_ads: true,
        deduct_product_costs: true,
        deduct_vat: false,
        deduct_daily_expenses: false,
        deduct_operating_expenses: true,
    });
    const [hideInferred, setHideInferred] = useState(false);
    const [settlementsAllowDelete, setSettlementsAllowDelete] = useState(false);
    const [adAccountAllowDelete, setAdAccountAllowDelete] = useState(false);
    // Iter-250b · Phase 3.7 — supplier-invoice column visibility.
    const [supInvShowDiscount, setSupInvShowDiscount] = useState(false);
    const [supInvShowTax,      setSupInvShowTax]      = useState(false);
    const [supInvShowNotes,    setSupInvShowNotes]    = useState(false);
    // Iter-251 · Phase 1.5 — default receiving bank per payment provider.
    const [bankForSalla,  setBankForSalla]  = useState("");
    const [bankForTamara, setBankForTamara] = useState("");
    const [bankForTabby,  setBankForTabby]  = useState("");
    const [bankForImkan,  setBankForImkan]  = useState("");
    const [bankAccounts,  setBankAccounts]  = useState([]);
    useEffect(() => {
        let alive = true;
        api.get("/accounts", { params: { account_type: "bank" } })
            .then(({ data }) => {
                if (!alive) return;
                const items = Array.isArray(data) ? data
                    : (data?.items || []);
                setBankAccounts(items.filter(
                    (a) => a.account_type === "bank"));
            })
            .catch(() => { /* ignore */ });
        return () => { alive = false; };
    }, []);
    // iter-45 — Electronic Net status filter overrides
    const [electronicNetExcluded, setElectronicNetExcluded] = useState([]);
    const [sallaElectronicNetRef, setSallaElectronicNetRef] = useState("");
    const [syncingElectronicNet, setSyncingElectronicNet] = useState(false);
    const [discoveredStatuses, setDiscoveredStatuses] = useState([]); // [{name,count}]
    const [shippingDiscovery, setShippingDiscovery] = useState(null);
    const [autoAdding, setAutoAdding] = useState(false);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    // ── Snapchat state ────────────────────────────────────────────────
    const location = useLocation();
    const navigate = useNavigate();
    const [snapConfig, setSnapConfig] = useState({
        connected: false,
        has_credentials: false,
        client_id: "",
        redirect_uri: DEFAULT_SNAP_REDIRECT,
        ad_account_id: "",
        ad_account_name: "",
    });
    const [snapClientSecret, setSnapClientSecret] = useState("");
    const [snapSaving, setSnapSaving] = useState(false);
    const [snapConnecting, setSnapConnecting] = useState(false);
    const [snapAccounts, setSnapAccounts] = useState([]);
    const [snapLoadingAccounts, setSnapLoadingAccounts] = useState(false);
    // Multi-account selection (iteration 15): Set of ad_account_ids the
    // merchant has enabled. Loaded from `/snapchat/selected-accounts` on
    // mount and persisted via `PUT /snapchat/selected-accounts` when
    // they click "حفظ الحسابات المختارة".
    const [snapEnabledIds, setSnapEnabledIds] = useState(new Set());
    const [snapSelectionSaving, setSnapSelectionSaving] = useState(false);

    // ── Meta Ads state ─────────────────────────────────────────────────
    const [metaConfig, setMetaConfig] = useState({
        connected: false,
        app_id: "",
        app_secret_masked: "",
        access_token_masked: "",
        ad_account_id: "",
        last_sync_at: null,
        last_sync_summary: null,
        connection_status: "ok",
        last_error_message: null,
        last_error_at: null,
        token_expires_at: null,
        token_exchanged_at: null,
    });
    const [metaForm, setMetaForm] = useState({
        app_id: "",
        app_secret: "",
        access_token: "",
        ad_account_id: "",
    });
    const [metaSaving, setMetaSaving] = useState(false);
    const [metaSyncing, setMetaSyncing] = useState(false);
    const [metaTesting, setMetaTesting] = useState(false);
    // Short-lived token exchange flow (Graph API Explorer → 60-day token)
    const [shortLivedToken, setShortLivedToken] = useState("");
    const [exchangingToken, setExchangingToken] = useState(false);

    // ── App-level Login settings (Owner-only — toggle visibility of the
    //     "create new account" link on the public /login page) ──────────
    const [appConfig, setAppConfig] = useState({ show_register_link: false });
    const [appConfigSaving, setAppConfigSaving] = useState(false);

    const loadAppConfig = async () => {
        try {
            const { data } = await api.get("/app-config");
            setAppConfig({ show_register_link: !!data.show_register_link });
        } catch {
            // Non-owner gets 403 → keep defaults silently (the UI also gates the section).
        }
    };

    const toggleShowRegister = async () => {
        const next = !appConfig.show_register_link;
        setAppConfigSaving(true);
        // Optimistic update so the toggle feels instant
        setAppConfig({ show_register_link: next });
        try {
            await api.put("/app-config", { show_register_link: next });
            toast.success(next ? "تم تفعيل زر إنشاء حساب" : "تم إخفاء زر إنشاء حساب");
        } catch (err) {
            setAppConfig({ show_register_link: !next });  // rollback
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر تحديث الإعداد");
        } finally {
            setAppConfigSaving(false);
        }
    };

    /** Robust error-detail formatter: handles both string and object
     * `detail` payloads from the backend without ever exposing raw JSON. */
    const fmtMetaErr = (e, fallback = "تعذّرت العملية") => {
        const d = e?.response?.data?.detail;
        if (d && typeof d === "object") return d.message || fallback;
        if (typeof d === "string") return d;
        return fallback;
    };

    const loadMetaConfig = async () => {
        try {
            const { data } = await api.get("/meta/config");
            setMetaConfig(data);
            if (data.connected) {
                setMetaForm({
                    app_id: data.app_id || "",
                    app_secret: "",
                    access_token: "",
                    ad_account_id: data.ad_account_id || "",
                });
            }
        } catch {
            /* no config yet */
        }
    };

    const loadSnapConfig = async () => {
        try {
            const { data } = await api.get("/snapchat/config");
            setSnapConfig({
                connected: !!data.connected,
                has_credentials: !!data.has_credentials,
                client_id: data.client_id || "",
                redirect_uri: data.redirect_uri || DEFAULT_SNAP_REDIRECT,
                ad_account_id: data.ad_account_id || "",
                ad_account_name: data.ad_account_name || "",
            });
        } catch {
            // snapchat config not yet set up — treat as disconnected
        }
    };

    useEffect(() => {
        (async () => {
            try {
                const [{ data: settings }, { data: statuses }, { data: discovery }] = await Promise.all([
                    api.get("/settings"),
                    api.get("/order-statuses"),
                    api.get("/shipping-companies/discover"),
                ]);
                setPayments(withRowIds(settings.payment_methods));
                setShippings(withRowIds(settings.shipping_companies));
                setShipApproved(settings.shipping_approved_statuses || []);
                setCodApproved(settings.cod_approved_statuses || []);
                setReportIncluded(settings.report_included_statuses || []);
                setHiddenCards(settings.dashboard_hidden_cards || []);
                if (settings.net_sales_config) setNetSalesConfig(settings.net_sales_config);
                setHideInferred(!!settings.hide_inferred_date_orders);
                setSettlementsAllowDelete(!!settings.settlements_allow_delete);
                setAdAccountAllowDelete(!!settings.ad_account_allow_delete);
                // Iter-250b · Phase 3.7 — supplier-invoice column visibility.
                setSupInvShowDiscount(!!settings.supplier_invoice_show_discount);
                setSupInvShowTax(!!settings.supplier_invoice_show_tax);
                setSupInvShowNotes(!!settings.supplier_invoice_show_notes);
                // Iter-251 · Phase 1.5 — provider→bank routing.
                setBankForSalla(settings.default_bank_for_salla   || "");
                setBankForTamara(settings.default_bank_for_tamara || "");
                setBankForTabby(settings.default_bank_for_tabby   || "");
                setBankForImkan(settings.default_bank_for_imkan   || "");
                // iter-45 — Electronic Net status overrides
                setElectronicNetExcluded(settings.electronic_net_excluded_statuses || []);
                setSallaElectronicNetRef(
                    settings.salla_electronic_net_reference != null
                        ? String(settings.salla_electronic_net_reference)
                        : ""
                );
                setDiscoveredStatuses(statuses.statuses || []);
                setShippingDiscovery(discovery || null);
            } finally { setLoading(false); }
        })();
        loadSnapConfig();
        loadMetaConfig();
        loadAppConfig();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // ── Meta Ads handlers ──────────────────────────────────────────────
    const saveMetaConfig = async () => {
        // On first save, all 4 fields are required. On update, blanks mean
        // "keep existing" — only require fields the merchant hasn't connected yet.
        const first = !metaConfig.connected;
        if (first && (!metaForm.app_id.trim() || !metaForm.app_secret.trim()
                || !metaForm.access_token.trim() || !metaForm.ad_account_id.trim())) {
            toast.error("جميع الحقول مطلوبة عند الربط لأول مرة");
            return;
        }
        if (!metaForm.app_id.trim() || !metaForm.ad_account_id.trim()) {
            toast.error("Meta App ID و Ad Account ID مطلوبان");
            return;
        }
        setMetaSaving(true);
        try {
            await api.put("/meta/config", {
                app_id: metaForm.app_id.trim(),
                app_secret: metaForm.app_secret.trim(),
                access_token: metaForm.access_token.trim(),
                ad_account_id: metaForm.ad_account_id.trim(),
            });
            toast.success("تم حفظ إعدادات Meta Ads");
            // Clear the secret/token inputs after a successful save so they don't
            // get re-submitted on accident.
            setMetaForm((f) => ({ ...f, app_secret: "", access_token: "" }));
            await loadMetaConfig();
        } catch (e) {
            toast.error(fmtMetaErr(e, "فشل الحفظ"));
        } finally {
            setMetaSaving(false);
        }
    };

    const testMetaConnection = async () => {
        // Test against either a freshly-pasted token or the stored one. The
        // backend only persists the new values if the test passes.
        if (!metaForm.app_id.trim() || !metaForm.ad_account_id.trim()) {
            toast.error("Meta App ID و Ad Account ID مطلوبان للاختبار");
            return;
        }
        setMetaTesting(true);
        toast.loading("جاري اختبار الاتصال مع Meta…", { id: "meta-test" });
        try {
            const { data } = await api.post("/meta/test-connection", {
                app_id: metaForm.app_id.trim(),
                app_secret: metaForm.app_secret.trim(),
                access_token: metaForm.access_token.trim(),
                ad_account_id: metaForm.ad_account_id.trim(),
            });
            const accName = data.account?.name || data.account?.id || "حساب الإعلانات";
            toast.success(`تم الاتصال بنجاح ✓ (${accName}) — التوكن تم حفظه`,
                { id: "meta-test", duration: 6000 });
            setMetaForm((f) => ({ ...f, app_secret: "", access_token: "" }));
            await loadMetaConfig();
        } catch (e) {
            toast.error(fmtMetaErr(e, "فشل اختبار الاتصال — لم يتم حفظ التوكن"),
                { id: "meta-test", duration: 9000 });
        } finally {
            setMetaTesting(false);
        }
    };

    /**
     * Exchange a short-lived Graph API Explorer token (1-2h) for a 60-day
     * long-lived token. Workflow:
     *   1. Merchant ensures App ID + App Secret + Ad Account ID are filled
     *      (typically already saved — left blank means "use stored").
     *   2. Merchant pastes a short-lived token from
     *      https://developers.facebook.com/tools/explorer/
     *   3. Click → backend hits Meta's /oauth/access_token endpoint with
     *      grant_type=fb_exchange_token, gets the long-lived token, saves it
     *      with token_expires_at = now + 60 days.
     *   4. Frontend re-loads config so the new mask + StatusBadge + expiry
     *      reflect immediately. The short-lived input is cleared.
     */
    const exchangeShortToLongLived = async () => {
        if (!shortLivedToken.trim()) {
            toast.error("الرجاء لصق Short-lived token من Graph API Explorer");
            return;
        }
        const haveStoredCreds = metaConfig.connected && metaConfig.app_id;
        if (!haveStoredCreds && (!metaForm.app_id.trim() || !metaForm.app_secret.trim() || !metaForm.ad_account_id.trim())) {
            toast.error("Meta App ID و App Secret و Ad Account ID مطلوبة (احفظهم أولاً أو املأهم في النموذج).");
            return;
        }
        setExchangingToken(true);
        toast.loading("جاري تحويل التوكن إلى Long-lived (60 يوم)…", { id: "meta-exchange" });
        try {
            const { data } = await api.post("/meta/exchange-token", {
                short_lived_token: shortLivedToken.trim(),
                app_id: metaForm.app_id.trim(),
                app_secret: metaForm.app_secret.trim(),
                ad_account_id: metaForm.ad_account_id.trim(),
            });
            const days = data.token_expires_in_days != null
                ? `${data.token_expires_in_days} يوم`
                : "غير محدد";
            const expiresAt = data.token_expires_at
                ? new Date(data.token_expires_at).toLocaleDateString("en-GB", {
                    year: "numeric", month: "short", day: "numeric",
                })
                : "—";
            toast.success(
                `✓ تم التحويل وحفظ التوكن الجديد (${data.access_token_masked}). صالح حتى ${expiresAt} (~${days})`,
                { id: "meta-exchange", duration: 10000 },
            );
            setShortLivedToken("");
            // Clear the manual token field too — the new long-lived one is
            // already saved server-side; the form input is no longer needed.
            setMetaForm((f) => ({ ...f, access_token: "" }));
            await loadMetaConfig();
        } catch (e) {
            toast.error(fmtMetaErr(e, "فشل تحويل التوكن"),
                { id: "meta-exchange", duration: 9000 });
        } finally {
            setExchangingToken(false);
        }
    };

    const syncMetaNow = async () => {
        setMetaSyncing(true);
        try {
            const { data } = await api.post("/meta/sync", { days: 30 });
            toast.success(`تمت المزامنة: ${data.upserted} صف لـ ${data.rows} حملة`);
            await loadMetaConfig();
        } catch (e) {
            toast.error(fmtMetaErr(e, "فشل المزامنة"), { duration: 8000 });
            // Refresh config so the new connection_status reflects on UI.
            await loadMetaConfig();
        } finally {
            setMetaSyncing(false);
        }
    };

    const disconnectMeta = async () => {
        if (!window.confirm("هل تريد فصل ربط Meta Ads؟ ستحتاج لإدخال البيانات من جديد.")) return;
        try {
            await api.delete("/meta/config");
            setMetaForm({ app_id: "", app_secret: "", access_token: "", ad_account_id: "" });
            await loadMetaConfig();
            toast.success("تم فصل ربط Meta");
        } catch (e) {
            toast.error(fmtMetaErr(e, "فشل"));
        }
    };

    const reloadShippingDiscovery = async () => {
        try {
            const { data } = await api.get("/shipping-companies/discover");
            setShippingDiscovery(data);
        } catch { /* swallow */ }
    };

    const autoAddUnconfigured = async (names = null) => {
        if (autoAdding) return;
        setAutoAdding(true);
        try {
            const { data } = await api.post("/shipping-companies/autodiscover",
                names ? { names } : {});
            if (data?.added?.length) {
                toast.success(`تمت إضافة ${data.added.length} شركة شحن إلى الإعدادات. عدّل التكلفة/الآجل ثم احفظ.`);
                // Reload settings to refresh the form
                const { data: s } = await api.get("/settings");
                setShippings(withRowIds(s.shipping_companies));
                await reloadShippingDiscovery();
            } else {
                toast.info("لا توجد شركات جديدة لإضافتها.");
            }
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setAutoAdding(false);
        }
    };

    // Handle ?snapchat=success|error redirect after OAuth callback
    useEffect(() => {
        const params = new URLSearchParams(location.search);
        const status = params.get("snapchat");
        if (status === "success") {
            toast.success("تم ربط حساب Snapchat Ads بنجاح");
            loadSnapConfig();
            navigate("/settings", { replace: true });
        } else if (status === "error") {
            toast.error("فشل ربط Snapchat: " + (params.get("msg") || "خطأ غير معروف"));
            navigate("/settings", { replace: true });
        }
    }, [location.search, navigate]);

    const saveSnapConfig = async () => {
        if (!snapConfig.client_id?.trim() || !snapClientSecret?.trim() || !snapConfig.redirect_uri?.trim()) {
            toast.error("يرجى تعبئة App ID و App Secret و Redirect URI");
            return;
        }
        setSnapSaving(true);
        try {
            await api.post("/snapchat/config", {
                client_id: snapConfig.client_id.trim(),
                client_secret: snapClientSecret.trim(),
                redirect_uri: snapConfig.redirect_uri.trim(),
            });
            toast.success("تم حفظ بيانات تطبيق سناب. اضغط (الاتصال بسناب).");
            setSnapClientSecret("");
            await loadSnapConfig();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setSnapSaving(false); }
    };

    const connectSnap = async () => {
        setSnapConnecting(true);
        try {
            const { data } = await api.get("/snapchat/authorize-url");
            window.location.href = data.authorize_url;
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
            setSnapConnecting(false);
        }
    };

    const disconnectSnap = async () => {
        if (!window.confirm("سيتم حذف بيانات الاتصال والـ Refresh Token الخاصين بحساب سناب. متابعة؟")) return;
        try {
            await api.delete("/snapchat/config");
            toast.success("تم فصل حساب سناب");
            setSnapAccounts([]);
            await loadSnapConfig();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    const loadSnapAccounts = async () => {
        setSnapLoadingAccounts(true);
        try {
            // Load BOTH the available accounts from Snapchat API AND the
            // merchant's previously-enabled selection (so checkboxes default
            // to the existing state).
            const [accountsResp, selectedResp] = await Promise.all([
                api.get("/snapchat/adaccounts"),
                api.get("/snapchat/selected-accounts").catch(() => ({ data: { accounts: [] } })),
            ]);
            setSnapAccounts(accountsResp.data.adaccounts || []);
            const enabled = new Set(
                (selectedResp.data.accounts || []).map((a) => a.ad_account_id),
            );
            setSnapEnabledIds(enabled);
            if ((accountsResp.data.adaccounts || []).length === 0) {
                toast.message("لم يتم العثور على حسابات إعلانات في حسابك");
            }
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setSnapLoadingAccounts(false); }
    };

    const toggleSnapAccount = (ad_account_id) => {
        setSnapEnabledIds((prev) => {
            const next = new Set(prev);
            if (next.has(ad_account_id)) next.delete(ad_account_id);
            else next.add(ad_account_id);
            return next;
        });
    };

    const saveSnapSelectedAccounts = async () => {
        if (snapEnabledIds.size === 0) {
            const confirmEmpty = window.confirm(
                "لم تختر أي حساب — هل تريد إلغاء تفعيل جميع حسابات Snapchat؟",
            );
            if (!confirmEmpty) return;
        }
        setSnapSelectionSaving(true);
        try {
            const accountsPayload = snapAccounts
                .filter((acc) => snapEnabledIds.has(acc.ad_account_id))
                .map((acc) => ({
                    ad_account_id: acc.ad_account_id,
                    name: acc.name || "",
                    currency: acc.currency || "",
                    timezone: acc.timezone || "",
                    organization_id: acc.organization_id || "",
                    organization_name: acc.organization_name || "",
                    status: acc.status || "",
                }));
            const { data } = await api.put("/snapchat/selected-accounts", {
                accounts: accountsPayload,
            });
            toast.success(
                `تم حفظ ${data.enabled_count} حساب Snapchat — افتح صفحة "حسابات Snapchat" لعرض التفاصيل.`,
                { duration: 6000 },
            );
            await loadSnapConfig();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setSnapSelectionSaving(false); }
    };

    const selectAccount = async (acc) => {
        try {
            await api.post("/snapchat/select-adaccount", {
                ad_account_id: acc.ad_account_id,
                ad_account_name: acc.name || "",
                timezone: acc.timezone || "",
                currency: acc.currency || "",
            });
            toast.success(`تم اختيار حساب: ${acc.name || acc.ad_account_id}`);
            await loadSnapConfig();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    // iter-45 — One-click sync of the electronic-net exclusion list to
    // the Salla-compatible defaults. We then immediately reload settings
    // so the UI stays in sync without a manual refresh.
    const syncElectronicNetToSalla = async () => {
        setSyncingElectronicNet(true);
        try {
            const { data } = await api.post("/settings/electronic-net/sync-to-salla");
            setElectronicNetExcluded(data.electronic_net_excluded_statuses || []);
            toast.success("تم تطبيق القائمة الافتراضية المطابقة لسلة");
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذّرت المزامنة");
        } finally {
            setSyncingElectronicNet(false);
        }
    };

    const save = async () => {
        setSaving(true);
        try {
            await api.put("/settings", {
                payment_methods: payments.map((p) => ({
                    name: (p.name || "").trim(),
                    commission_percent: Number(p.commission_percent || 0),
                    fixed_fee: Number(p.fixed_fee || 0),
                    vat_percent: Number(p.vat_percent || 0),
                })).filter((p) => p.name),
                shipping_companies: shippings.map((s) => ({
                    name: (s.name || "").trim(),
                    cost_per_order: Number(s.cost_per_order || 0),
                    vat_percent: Number(s.vat_percent || 0),
                    is_deferred: !!s.is_deferred,
                })).filter((s) => s.name),
                shipping_approved_statuses: shipApproved,
                cod_approved_statuses: codApproved,
                report_included_statuses: reportIncluded,
                dashboard_hidden_cards: hiddenCards,
                net_sales_config: netSalesConfig,
                hide_inferred_date_orders: hideInferred,
                settlements_allow_delete: settlementsAllowDelete,
                ad_account_allow_delete: adAccountAllowDelete,
                // Iter-250b · Phase 3.7 — supplier-invoice column visibility.
                supplier_invoice_show_discount: supInvShowDiscount,
                supplier_invoice_show_tax:      supInvShowTax,
                supplier_invoice_show_notes:    supInvShowNotes,
                // Iter-251 · Phase 1.5 — default bank per provider.
                default_bank_for_salla:  bankForSalla   || null,
                default_bank_for_tamara: bankForTamara  || null,
                default_bank_for_tabby:  bankForTabby   || null,
                default_bank_for_imkan:  bankForImkan   || null,
                // iter-45 — Electronic Net overrides
                electronic_net_excluded_statuses: electronicNetExcluded,
                salla_electronic_net_reference: sallaElectronicNetRef.trim()
                    ? Number(sallaElectronicNetRef)
                    : null,
            });
            toast.success("تم حفظ الإعدادات");
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setSaving(false); }
    };

    if (loading) return <div className="p-10 text-center" data-testid="settings-loading">جاري التحميل…</div>;

    return (
        <div className="space-y-8 animate-fade-in-up" data-testid="settings-page">
            <div className="flex items-start justify-between gap-4 flex-col md:flex-row">
                <div>
                    <h1 className="text-3xl sm:text-4xl lg:text-5xl font-extrabold tracking-tight" style={{ fontFamily: "Tajawal" }}>الإعدادات</h1>
                    <p className="text-muted-foreground mt-2 text-sm sm:text-base">
                        اضبط نسب عمولات بوابات الدفع وتكاليف شركات الشحن. ستُستخدم تلقائياً في حساب الأرباح.
                    </p>
                </div>
                <button
                    onClick={save}
                    disabled={saving}
                    className="inline-flex items-center justify-center gap-2 px-5 py-3 bg-brand text-white font-bold rounded-lg bg-brand-hover transition-colors disabled:opacity-60 w-full md:w-auto"
                    data-testid="save-settings-btn"
                >
                    <FloppyDisk size={20} weight="bold" />
                    {saving ? "جاري الحفظ…" : "حفظ التغييرات"}
                </button>
            </div>

            {/* Salla direct integration entry-point — new in iter-37 (Phase 1) */}
            <button
                type="button"
                onClick={() => navigate("/settings/salla")}
                className="group w-full text-right rounded-xl border-2 border-dashed border-indigo-300 bg-gradient-to-l from-indigo-50 to-white hover:from-indigo-100 hover:border-indigo-400 transition-all p-5 flex items-center gap-4"
                data-testid="settings-salla-link-card"
                style={{ fontFamily: "Tajawal" }}
            >
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center text-white shadow-lg flex-shrink-0">
                    <LinkSimple size={26} weight="bold" />
                </div>
                <div className="flex-1 min-w-0">
                    <div className="font-extrabold text-slate-900 text-base">ربط متجر سلة المباشر (OAuth)</div>
                    <div className="text-xs text-slate-600 mt-0.5">
                        ربط Salla → النظام مباشرة عبر OAuth + Webhooks. <span className="font-bold text-emerald-700">Make و PDF و Excel تبقى تعمل كما هي.</span>
                    </div>
                </div>
                <div className="text-indigo-600 text-xs font-bold opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">فتح ←</div>
            </button>

            {/* Login / Public-access settings (Owner only) — iter-52 */}
            {user?.is_owner && (
                <div className="rounded-xl border border-border bg-white p-6" data-testid="login-settings-card">
                    <div className="flex items-start gap-3 mb-4">
                        <div className="w-10 h-10 rounded-lg bg-brand/10 text-brand flex items-center justify-center shrink-0">
                            <LockKey size={22} weight="duotone" />
                        </div>
                        <div>
                            <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>إعدادات تسجيل الدخول</h2>
                            <p className="text-sm text-muted-foreground mt-1">
                                تحكّم في ما يظهر للزوار على شاشة تسجيل الدخول.
                            </p>
                        </div>
                    </div>

                    <div className="flex items-start justify-between gap-4 p-4 rounded-lg border border-border bg-slate-50/60" data-testid="show-register-toggle-row">
                        <div className="flex items-start gap-3 flex-1 min-w-0">
                            <UserPlus size={22} className="text-brand mt-0.5 shrink-0" weight="duotone" />
                            <div className="min-w-0">
                                <div className="font-bold text-foreground">إظهار زر "إنشاء حساب جديد"</div>
                                <p className="text-xs text-muted-foreground mt-1">
                                    عند الإيقاف يختفي الرابط من شاشة تسجيل الدخول. وظيفة التسجيل تبقى متاحة في الـ Backend
                                    (لتجنّب كسر أي تكامل قائم) — فقط الواجهة العامة تُخفى.
                                    <br />
                                    الافتراضي: <span className="font-bold text-amber-700">إيقاف</span> لأن النظام مُخصّص لمتجر واحد.
                                </p>
                            </div>
                        </div>
                        <button
                            type="button"
                            role="switch"
                            aria-checked={appConfig.show_register_link}
                            onClick={toggleShowRegister}
                            disabled={appConfigSaving}
                            className={`relative inline-flex h-7 w-12 shrink-0 cursor-pointer items-center rounded-full transition-colors disabled:opacity-60 ${appConfig.show_register_link ? "bg-brand" : "bg-slate-300"}`}
                            data-testid="show-register-toggle"
                        >
                            <span
                                className={`inline-block h-5 w-5 transform rounded-full bg-white shadow-md transition-transform ${appConfig.show_register_link ? "translate-x-1" : "translate-x-6"}`}
                            />
                        </button>
                    </div>
                    <div className="mt-3 text-xs text-muted-foreground" data-testid="show-register-status">
                        الحالة الحالية:{" "}
                        <span className={`font-bold ${appConfig.show_register_link ? "text-emerald-700" : "text-rose-600"}`}>
                            {appConfig.show_register_link ? "ظاهر" : "مخفي"}
                        </span>
                    </div>
                </div>
            )}

            {/* Payment methods */}
            <div className="rounded-xl border border-border bg-white p-6">
                <div className="flex items-center justify-between mb-5">
                    <div>
                        <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>طرق الدفع وعمولاتها</h2>
                        <p className="text-sm text-muted-foreground mt-1">نسبة % + مبلغ ثابت لكل طلب + نسبة ضريبة على إجمالي العمولة</p>
                    </div>
                    <button
                        onClick={() => setPayments([...payments, { _rid: newRowId(), name: "", commission_percent: 0, fixed_fee: 0, vat_percent: 15 }])}
                        className="inline-flex items-center gap-2 px-3 py-2 border border-border rounded-lg text-sm font-semibold hover:bg-accent transition-colors"
                        data-testid="add-payment-btn"
                    >
                        <Plus size={16} weight="bold" /> إضافة
                    </button>
                </div>
                <div className="space-y-3 overflow-x-auto -mx-6 sm:mx-0 px-6 sm:px-0" data-testid="payment-methods-list">
                    {/* Header row */}
                    <div className="grid grid-cols-12 gap-3 text-xs font-semibold text-muted-foreground px-1 hidden md:grid min-w-[640px]">
                        <div className="col-span-4">اسم بوابة الدفع</div>
                        <div className="col-span-2 text-center">النسبة %</div>
                        <div className="col-span-2 text-center">مبلغ ثابت (ر.س)</div>
                        <div className="col-span-3 text-center">نسبة الضريبة على العمولة %</div>
                        <div className="col-span-1"></div>
                    </div>
                    {payments.map((p, i) => (
                        <div key={p._rid || `p-${i}`} className="grid grid-cols-12 gap-3 items-center min-w-[640px]">
                            <input
                                type="text"
                                value={p.name}
                                onChange={(e) => {
                                    const arr = [...payments];
                                    arr[i] = { ...arr[i], name: e.target.value };
                                    setPayments(arr);
                                }}
                                placeholder="اسم بوابة الدفع"
                                className="col-span-4 px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                data-testid={`payment-name-${i}`}
                            />
                            <div className="col-span-2 flex items-center border border-border rounded-lg bg-white focus-within:ring-2 focus-within:ring-brand overflow-hidden">
                                <input
                                    type="number"
                                    min={0} max={100} step="0.01"
                                    value={p.commission_percent ?? 0}
                                    onChange={(e) => {
                                        const arr = [...payments];
                                        arr[i] = { ...arr[i], commission_percent: e.target.value };
                                        setPayments(arr);
                                    }}
                                    dir="ltr"
                                    className="flex-1 min-w-0 px-3 py-2.5 text-base bg-transparent focus:outline-none num text-right"
                                    data-testid={`payment-rate-${i}`}
                                />
                                <span className="px-3 py-2.5 text-sm font-bold text-muted-foreground bg-accent/60 border-s border-border">%</span>
                            </div>
                            <div className="col-span-2 flex items-center border border-border rounded-lg bg-white focus-within:ring-2 focus-within:ring-brand overflow-hidden">
                                <input
                                    type="number"
                                    min={0} step="0.01"
                                    value={p.fixed_fee ?? 0}
                                    onChange={(e) => {
                                        const arr = [...payments];
                                        arr[i] = { ...arr[i], fixed_fee: e.target.value };
                                        setPayments(arr);
                                    }}
                                    dir="ltr"
                                    className="flex-1 min-w-0 px-3 py-2.5 text-base bg-transparent focus:outline-none num text-right"
                                    placeholder="0.00"
                                    data-testid={`payment-fixed-${i}`}
                                />
                                <span className="px-3 py-2.5 text-xs font-bold text-muted-foreground bg-accent/60 border-s border-border whitespace-nowrap">ر.س</span>
                            </div>
                            <div className="col-span-3 flex items-center border border-border rounded-lg bg-white focus-within:ring-2 focus-within:ring-brand overflow-hidden">
                                <input
                                    type="number"
                                    min={0} max={100} step="0.01"
                                    value={p.vat_percent ?? 0}
                                    onChange={(e) => {
                                        const arr = [...payments];
                                        arr[i] = { ...arr[i], vat_percent: e.target.value };
                                        setPayments(arr);
                                    }}
                                    dir="ltr"
                                    className="flex-1 min-w-0 px-3 py-2.5 text-base bg-transparent focus:outline-none num text-right"
                                    placeholder="15"
                                    data-testid={`payment-vat-${i}`}
                                />
                                <span className="px-3 py-2.5 text-sm font-bold text-muted-foreground bg-accent/60 border-s border-border">%</span>
                            </div>
                            <button
                                onClick={() => setPayments(payments.filter((_, idx) => idx !== i))}
                                className="col-span-1 p-2.5 rounded-lg border border-border hover:bg-red-50 hover:text-red-600 transition-colors"
                                title="حذف"
                                data-testid={`remove-payment-${i}`}
                            >
                                <Trash size={18} />
                            </button>
                        </div>
                    ))}
                </div>
            </div>

            {/* Shipping companies */}
            <div className="rounded-xl border border-border bg-white p-6">
                <div className="flex items-center justify-between mb-5">
                    <div>
                        <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>شركات الشحن وتكاليفها</h2>
                        <p className="text-sm text-muted-foreground mt-1">تكلفة الشحنة (ر.س) + نسبة الضريبة على الشحن</p>
                    </div>
                    <button
                        onClick={() => setShippings([...shippings, { _rid: newRowId(), name: "", cost_per_order: 0, vat_percent: 15, is_deferred: false }])}
                        className="inline-flex items-center gap-2 px-3 py-2 border border-border rounded-lg text-sm font-semibold hover:bg-accent transition-colors"
                        data-testid="add-shipping-btn"
                    >
                        <Plus size={16} weight="bold" /> إضافة
                    </button>
                </div>

                {/* Discovery banner: shows mismatches between settings and real orders */}
                {shippingDiscovery && (shippingDiscovery.unconfigured?.length > 0
                    || shippingDiscovery.configured?.some((c) => c.status === "missing_cost")) && (
                    <div className="mb-5 rounded-lg border border-amber-300 bg-amber-50 p-4" data-testid="shipping-discovery-banner">
                        <h3 className="font-bold text-amber-900 mb-2" style={{ fontFamily: "Tajawal" }}>
                            ⚠ شركات الشحن في الطلبات لا تطابق الإعدادات
                        </h3>

                        {shippingDiscovery.unconfigured?.length > 0 && (
                            <div className="mb-3">
                                <div className="text-sm font-semibold mb-1.5 text-amber-900">
                                    شركات ظهرت في طلباتك لكن غير معرَّفة:
                                </div>
                                <div className="flex flex-wrap items-center gap-2 mb-2">
                                    {shippingDiscovery.unconfigured.map((u) => (
                                        <div key={u.name} className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-white border border-amber-200 rounded-md text-xs">
                                            <span className="font-bold">{u.name}</span>
                                            <span className="text-muted-foreground">({u.orders_count} طلب • متوسط {u.avg_shipping_cost} ر.س)</span>
                                        </div>
                                    ))}
                                </div>
                                <button
                                    type="button"
                                    onClick={() => autoAddUnconfigured()}
                                    disabled={autoAdding}
                                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-amber-600 text-white text-xs font-bold hover:bg-amber-700 disabled:opacity-50 transition-colors"
                                    data-testid="auto-add-shipping-btn"
                                >
                                    {autoAdding ? "جاري الإضافة..." : `+ إضافة الكل تلقائياً (${shippingDiscovery.unconfigured.length})`}
                                </button>
                            </div>
                        )}

                        {shippingDiscovery.configured?.some((c) => c.status === "missing_cost") && (
                            <div>
                                <div className="text-sm font-semibold mb-1.5 text-amber-900">
                                    شركات معرَّفة بتكلفة 0 — تعديلها مطلوب لحساب الشحن الآجل بدقة:
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    {shippingDiscovery.configured.filter((c) => c.status === "missing_cost").map((c) => (
                                        <span key={c.name} className="inline-block px-2.5 py-1 bg-white border border-amber-200 rounded-md text-xs">
                                            <span className="font-bold">{c.name}</span>
                                            <span className="text-red-600 ms-1">(cost=0)</span>
                                        </span>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                )}
                <div className="space-y-3 overflow-x-auto -mx-6 sm:mx-0 px-6 sm:px-0" data-testid="shipping-companies-list">
                    {/* Header row */}
                    <div className="grid grid-cols-14 gap-3 text-xs font-semibold text-muted-foreground px-1 hidden md:grid min-w-[700px]">
                        <div className="col-span-5">اسم شركة الشحن</div>
                        <div className="col-span-3 text-center">تكلفة الشحنة (ر.س)</div>
                        <div className="col-span-3 text-center">نسبة الضريبة على الشحن %</div>
                        <div className="col-span-2 text-center" title="شركات لا تخصم تكلفتها من حوالة سلة">آجل</div>
                        <div className="col-span-1"></div>
                    </div>
                    {shippings.map((s, i) => (
                        <div key={s._rid || `s-${i}`} className="grid grid-cols-14 gap-3 items-center min-w-[700px]">
                            <input
                                type="text"
                                value={s.name}
                                onChange={(e) => {
                                    const arr = [...shippings];
                                    arr[i] = { ...arr[i], name: e.target.value };
                                    setShippings(arr);
                                }}
                                placeholder="اسم شركة الشحن"
                                className="col-span-5 px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                data-testid={`shipping-name-${i}`}
                            />
                            <div className="col-span-3 flex items-center border border-border rounded-lg bg-white focus-within:ring-2 focus-within:ring-brand overflow-hidden">
                                <input
                                    type="number"
                                    min={0} step="0.01"
                                    value={s.cost_per_order}
                                    onChange={(e) => {
                                        const arr = [...shippings];
                                        arr[i] = { ...arr[i], cost_per_order: e.target.value };
                                        setShippings(arr);
                                    }}
                                    dir="ltr"
                                    className="flex-1 min-w-0 px-3 py-2.5 text-base bg-transparent focus:outline-none num text-right"
                                    data-testid={`shipping-cost-${i}`}
                                />
                                <span className="px-3 py-2.5 text-sm font-bold text-muted-foreground bg-accent/60 border-s border-border whitespace-nowrap">ر.س</span>
                            </div>
                            <div className="col-span-3 flex items-center border border-border rounded-lg bg-white focus-within:ring-2 focus-within:ring-brand overflow-hidden">
                                <input
                                    type="number"
                                    min={0} max={100} step="0.01"
                                    value={s.vat_percent ?? 0}
                                    onChange={(e) => {
                                        const arr = [...shippings];
                                        arr[i] = { ...arr[i], vat_percent: e.target.value };
                                        setShippings(arr);
                                    }}
                                    dir="ltr"
                                    className="flex-1 min-w-0 px-3 py-2.5 text-base bg-transparent focus:outline-none num text-right"
                                    placeholder="15"
                                    data-testid={`shipping-vat-${i}`}
                                />
                                <span className="px-3 py-2.5 text-sm font-bold text-muted-foreground bg-accent/60 border-s border-border">%</span>
                            </div>
                            <label
                                className={`col-span-2 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg border cursor-pointer transition-colors select-none ${s.is_deferred ? "bg-amber-50 border-amber-300 text-amber-800" : "border-border bg-white hover:bg-accent/40"}`}
                                title="إذا فُعّل: لا تُخصم تكلفته من حوالة سلة، وتُسجَّل كذمم مستحقة"
                            >
                                <input
                                    type="checkbox"
                                    checked={!!s.is_deferred}
                                    onChange={(e) => {
                                        const arr = [...shippings];
                                        arr[i] = { ...arr[i], is_deferred: e.target.checked };
                                        setShippings(arr);
                                    }}
                                    className="w-4 h-4 accent-amber-500 cursor-pointer"
                                    data-testid={`shipping-deferred-${i}`}
                                />
                                <span className="text-xs font-bold">{s.is_deferred ? "آجل" : "—"}</span>
                            </label>
                            <button
                                onClick={() => setShippings(shippings.filter((_, idx) => idx !== i))}
                                className="col-span-1 p-2.5 rounded-lg border border-border hover:bg-red-50 hover:text-red-600 transition-colors"
                                title="حذف"
                                data-testid={`remove-shipping-${i}`}
                            >
                                <Trash size={18} />
                            </button>
                        </div>
                    ))}
                </div>
            </div>

            {/* NEW (Phase 3): Net Sales calculation config */}
            <div className="rounded-xl border border-border bg-white p-6" data-testid="net-sales-config-section">
                <div className="mb-5 flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg bg-brand text-white flex items-center justify-center">
                        <Calculator size={22} weight="duotone" />
                    </div>
                    <div>
                        <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>حساب صافي المبيعات</h2>
                        <p className="text-sm text-muted-foreground mt-1">
                            حدِّد البنود التي تُخصم من إجمالي المبيعات لاحتساب "صافي المبيعات" في لوحة التحكم.
                            كل خيار مستقل لتعكس تفضيلك المحاسبي.
                        </p>
                    </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {[
                        { key: "deduct_payment_fees", label: "خصم عمولات بوابات الدفع", hint: "بما فيها BNPL (تمارا/تابي/إمكان)" },
                        { key: "deduct_shipping", label: "خصم تكاليف الشحن الفوري", hint: "شركات الشحن غير الآجلة" },
                        { key: "deduct_deferred_shipping", label: "خصم تكاليف الشحن الآجل", hint: "مستحقات شركات الشحن الآجلة" },
                        { key: "deduct_ads", label: "خصم تكاليف الإعلانات", hint: "Snapchat + TikTok + Instagram + Google" },
                        { key: "deduct_product_costs", label: "خصم تكاليف المنتجات", hint: "من سجل التكاليف اليومية" },
                        { key: "deduct_vat", label: "خصم ضريبة القيمة المضافة", hint: "VAT على عمولات الدفع والشحن" },
                        { key: "deduct_operating_expenses", label: "خصم المصروفات التشغيلية", hint: "الرواتب + الإيجارات + المصروفات اليومية الأخرى" },
                    ].map((opt) => {
                        const checked = !!netSalesConfig[opt.key];
                        return (
                            <label
                                key={opt.key}
                                className={`flex items-start gap-3 px-4 py-3 rounded-lg cursor-pointer transition-colors border ${checked ? "border-emerald-300 bg-emerald-50/60" : "border-border bg-white"}`}
                                data-testid={`net-sales-toggle-${opt.key}`}
                            >
                                <input
                                    type="checkbox"
                                    className="w-4 h-4 mt-1 accent-brand"
                                    checked={checked}
                                    onChange={(e) => setNetSalesConfig({ ...netSalesConfig, [opt.key]: e.target.checked })}
                                />
                                <div className="flex-1">
                                    <div className="text-sm font-semibold">{opt.label}</div>
                                    {opt.hint && <div className="text-xs text-muted-foreground mt-0.5">{opt.hint}</div>}
                                </div>
                            </label>
                        );
                    })}
                </div>

                <div className="mt-5 p-4 bg-accent/40 rounded-lg text-xs text-muted-foreground">
                    <div className="font-bold mb-1 text-foreground">المعادلة:</div>
                    <div className="font-mono leading-relaxed" dir="ltr">
                        صافي المبيعات = إجمالي المبيعات
                        {netSalesConfig.deduct_payment_fees && <span className="text-red-600"> − عمولات الدفع</span>}
                        {netSalesConfig.deduct_shipping && <span className="text-red-600"> − شحن فوري</span>}
                        {netSalesConfig.deduct_deferred_shipping && <span className="text-red-600"> − شحن آجل</span>}
                        {netSalesConfig.deduct_ads && <span className="text-red-600"> − إعلانات</span>}
                        {netSalesConfig.deduct_product_costs && <span className="text-red-600"> − منتجات</span>}
                        {netSalesConfig.deduct_vat && <span className="text-red-600"> − VAT</span>}
                        {netSalesConfig.deduct_operating_expenses && <span className="text-red-600"> − مصروفات تشغيلية</span>}
                    </div>
                </div>
            </div>

            {/* NEW: Hide inferred-date orders from dashboard */}
            <div className="rounded-xl border border-border bg-white p-6" data-testid="hide-inferred-section">
                <div className="mb-4 flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg bg-brand text-white flex items-center justify-center">
                        <EyeSlash size={22} weight="duotone" />
                    </div>
                    <div>
                        <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>الطلبات ذات التاريخ التقريبي</h2>
                        <p className="text-sm text-muted-foreground mt-1">
                            تحكَّم في ظهور الطلبات القادمة من Make.com بدون <code className="bg-accent px-1 py-0.5 rounded text-xs">created_at</code> (تاريخ مُقدَّر).
                        </p>
                    </div>
                </div>

                <label
                    className={`flex items-start gap-3 px-4 py-3 rounded-lg cursor-pointer transition-colors border ${hideInferred ? "border-rose-300 bg-rose-50/60" : "border-border bg-white"}`}
                    data-testid="toggle-hide-inferred"
                >
                    <input
                        type="checkbox"
                        className="w-4 h-4 mt-1 accent-brand"
                        checked={hideInferred}
                        onChange={(e) => setHideInferred(e.target.checked)}
                    />
                    <div className="flex-1">
                        <div className="text-sm font-semibold">إخفاء الطلبات ذات التاريخ التقريبي من Dashboard و Reports</div>
                        <div className="text-xs text-muted-foreground mt-0.5">
                            عند التفعيل، يتم استبعاد هذه الطلبات من جميع حسابات لوحة التحكم والتقارير حتى يتم تصحيح تاريخها (مثلاً برفع ملف Excel من سلة).
                        </div>
                    </div>
                </label>

                {/* Iter-78 — toggle visibility of the delete column in
                    /payment-settlements. Defaults to OFF so the merchant
                    doesn't accidentally roll back a settlement file. */}
                <label
                    className={`flex items-start gap-3 px-4 py-3 rounded-lg cursor-pointer transition-colors border mt-3 ${settlementsAllowDelete ? "border-rose-300 bg-rose-50/60" : "border-border bg-white"}`}
                    data-testid="toggle-settlements-allow-delete"
                >
                    <input
                        type="checkbox"
                        className="w-4 h-4 mt-1 accent-brand"
                        checked={settlementsAllowDelete}
                        onChange={(e) => setSettlementsAllowDelete(e.target.checked)}
                    />
                    <div className="flex-1">
                        <div className="text-sm font-semibold">إظهار زر حذف ملفات التسويات (سله / تمارا / تابي)</div>
                        <div className="text-xs text-muted-foreground mt-0.5">
                            عند التفعيل، يظهر زر حذف بجوار كل ملف في صفحة <code className="bg-accent px-1 py-0.5 rounded">/payment-settlements</code>. عند الحذف يتم إرجاع الطلبات المرتبطة إلى النسب التقديرية. يُترك مخفياً افتراضياً لمنع الحذف العَرَضي.
                        </div>
                    </div>
                </label>

                {/* Iter-110 — toggle visibility of the delete button on each
                    Ad-Account card in /ad-accounts. The DELETE endpoint itself
                    still blocks the action when balance>0 or open debt exists,
                    so this is purely a UI safety toggle. */}
                <label
                    className={`flex items-start gap-3 px-4 py-3 rounded-lg cursor-pointer transition-colors border mt-3 ${adAccountAllowDelete ? "border-rose-300 bg-rose-50/60" : "border-border bg-white"}`}
                    data-testid="toggle-ad-account-allow-delete"
                >
                    <input
                        type="checkbox"
                        className="w-4 h-4 mt-1 accent-brand"
                        checked={adAccountAllowDelete}
                        onChange={(e) => setAdAccountAllowDelete(e.target.checked)}
                    />
                    <div className="flex-1">
                        <div className="text-sm font-semibold">إظهار زر حذف الحسابات الإعلانية</div>
                        <div className="text-xs text-muted-foreground mt-0.5">
                            عند التفعيل، يظهر زر حذف 🗑️ بجوار كل بطاقة حساب إعلاني في صفحة <code className="bg-accent px-1 py-0.5 rounded">/ad-accounts</code>. الحذف مسموح <b>فقط</b> إذا كان الرصيد = 0 والمديونية المفتوحة = 0 (السيرفر يرفض غير ذلك). يُترك مخفياً افتراضياً لمنع الحذف العَرَضي.
                        </div>
                    </div>
                </label>
                {/* Iter-250b · Phase 3.7 — supplier-invoice line-item
                    column visibility. Hidden by default to keep the
                    form clean. Toggle here to show in the form. */}
                <div className="mt-4 pt-3 border-t border-slate-200">
                    <div className="text-sm font-bold text-slate-700 mb-2">
                        🧾 إظهار/إخفاء أعمدة فاتورة المورد
                    </div>
                    <div className="text-xs text-muted-foreground mb-3">
                        تتحكم في ظهور أعمدة إضافية اختيارية داخل جدول
                        أصناف فاتورة المورد (الخصم، الضريبة، الملاحظات).
                        مخفية افتراضياً لتبسيط النموذج.
                    </div>
                    <label
                        className={`flex items-start gap-3 px-4 py-2.5 rounded-lg cursor-pointer transition-colors border ${supInvShowDiscount ? "border-emerald-300 bg-emerald-50/60" : "border-border bg-white"}`}
                        data-testid="toggle-supinv-show-discount"
                    >
                        <input
                            type="checkbox"
                            className="w-4 h-4 mt-1 accent-brand"
                            checked={supInvShowDiscount}
                            onChange={(e) => setSupInvShowDiscount(e.target.checked)}
                        />
                        <div className="flex-1">
                            <div className="text-sm font-semibold">إظهار عمود «الخصم» لكل سطر</div>
                            <div className="text-xs text-muted-foreground mt-0.5">
                                مبلغ ثابت بالريال يُخصم من إجمالي السطر قبل احتساب الضريبة.
                            </div>
                        </div>
                    </label>
                    <label
                        className={`flex items-start gap-3 px-4 py-2.5 rounded-lg cursor-pointer transition-colors border mt-2 ${supInvShowTax ? "border-emerald-300 bg-emerald-50/60" : "border-border bg-white"}`}
                        data-testid="toggle-supinv-show-tax"
                    >
                        <input
                            type="checkbox"
                            className="w-4 h-4 mt-1 accent-brand"
                            checked={supInvShowTax}
                            onChange={(e) => setSupInvShowTax(e.target.checked)}
                        />
                        <div className="flex-1">
                            <div className="text-sm font-semibold">إظهار عمود «الضريبة» لكل سطر</div>
                            <div className="text-xs text-muted-foreground mt-0.5">
                                مبلغ ضريبة ثابت بالريال يُضاف إلى إجمالي السطر بعد الخصم.
                            </div>
                        </div>
                    </label>
                    <label
                        className={`flex items-start gap-3 px-4 py-2.5 rounded-lg cursor-pointer transition-colors border mt-2 ${supInvShowNotes ? "border-emerald-300 bg-emerald-50/60" : "border-border bg-white"}`}
                        data-testid="toggle-supinv-show-notes"
                    >
                        <input
                            type="checkbox"
                            className="w-4 h-4 mt-1 accent-brand"
                            checked={supInvShowNotes}
                            onChange={(e) => setSupInvShowNotes(e.target.checked)}
                        />
                        <div className="flex-1">
                            <div className="text-sm font-semibold">إظهار عمود «الملاحظات» لكل سطر</div>
                            <div className="text-xs text-muted-foreground mt-0.5">
                                حقل نصي حر لكل سطر داخل فاتورة المورد.
                            </div>
                        </div>
                    </label>
                </div>
            </div>
            {/* Iter-251 · Phase 1.5 — Default receiving bank per
                payment provider. */}
            <div className="rounded-xl border border-border bg-white p-6"
                 data-testid="provider-default-bank-section">
                <div className="mb-3">
                    <h2 className="text-xl font-bold" style={{ fontFamily: "Tajawal" }}>
                        🏦 الحساب البنكي المستلم لكل مزود دفع
                    </h2>
                    <p className="text-xs text-muted-foreground mt-1">
                        يُستخدم في صفحة <b>مراجعة التحويلات البنكية</b> لتوجيه التسويات الواردة تلقائياً.
                        إذا تركت أي مزوّد فارغاً، أي تسوية واردة منه ستبقى بحالة <b>missing_target_bank</b>
                        حتى يختار الموظف البنك يدوياً.
                    </p>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {[
                        { key: "salla",  label: "سلة",   value: bankForSalla,  setter: setBankForSalla  },
                        { key: "tamara", label: "تمارا", value: bankForTamara, setter: setBankForTamara },
                        { key: "tabby",  label: "تابي",  value: bankForTabby,  setter: setBankForTabby  },
                        { key: "imkan",  label: "إمكان", value: bankForImkan,  setter: setBankForImkan  },
                    ].map((p) => (
                        <div key={p.key}
                             className={`border rounded-lg p-3 transition-colors ${p.value ? "border-emerald-300 bg-emerald-50/40" : "border-amber-300 bg-amber-50/30"}`}>
                            <div className="text-xs font-bold mb-1 flex items-center gap-2">
                                <span>{p.label}</span>
                                {!p.value && (
                                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-200 text-amber-900 font-extrabold">
                                        ⚠ بدون بنك
                                    </span>
                                )}
                                {p.value && (
                                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-200 text-emerald-900 font-extrabold">
                                        ✓ مفعَّل
                                    </span>
                                )}
                            </div>
                            <select
                                value={p.value}
                                onChange={(e) => p.setter(e.target.value)}
                                className="w-full border rounded-lg px-2 py-2 text-xs bg-white"
                                data-testid={`bank-for-${p.key}`}
                            >
                                <option value="">— لم يتم التحديد —</option>
                                {bankAccounts.map((b) => (
                                    <option key={b.id} value={b.id}>{b.name}</option>
                                ))}
                            </select>
                        </div>
                    ))}
                </div>
            </div>

            <div className="rounded-xl border border-border bg-white p-6" data-testid="dashboard-cards-section">
                <div className="mb-5 flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg bg-brand text-white flex items-center justify-center">
                        <SquaresFour size={22} weight="duotone" />
                    </div>
                    <div>
                        <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>تخصيص بطاقات لوحة التحكم</h2>
                        <p className="text-sm text-muted-foreground mt-1">
                            أخفِ البطاقات التي لا تحتاجها من لوحة التحكم. التغييرات تنطبق فوراً عند الحفظ.
                        </p>
                    </div>
                </div>

                <div className="flex items-center justify-between mb-4 text-sm">
                    <div className="text-muted-foreground">
                        مفعَّلة: <strong className="text-foreground">{KPI_GROUPS.flatMap((g) => g.cards).length + SPECIAL_DASHBOARD_CARDS.length - hiddenCards.length}</strong>
                        {" "}/ {KPI_GROUPS.flatMap((g) => g.cards).length + SPECIAL_DASHBOARD_CARDS.length}
                    </div>
                    <div className="flex gap-2">
                        <button type="button"
                            onClick={() => setHiddenCards([])}
                            className="text-xs px-3 py-1.5 rounded-lg border border-border hover:bg-accent font-bold"
                            data-testid="show-all-cards-btn">
                            <Eye size={14} className="inline" /> إظهار الكل
                        </button>
                        <button type="button"
                            onClick={() => setHiddenCards([
                                ...KPI_GROUPS.flatMap((g) => g.cards.map((c) => c.id)),
                                ...SPECIAL_DASHBOARD_CARDS.map((c) => c.id),
                            ])}
                            className="text-xs px-3 py-1.5 rounded-lg border border-border hover:bg-accent font-bold"
                            data-testid="hide-all-cards-btn">
                            <EyeSlash size={14} className="inline" /> إخفاء الكل
                        </button>
                    </div>
                </div>

                <div className="space-y-5">
                    {KPI_GROUPS.map((group) => (
                        <div key={group.id} className="border border-border rounded-lg p-4">
                            <h3 className="font-bold text-foreground mb-3" style={{ fontFamily: "Tajawal" }}>{group.title}</h3>
                            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                                {group.cards.map((c) => {
                                    const Icon = c.icon;
                                    const isHidden = hiddenCards.includes(c.id);
                                    return (
                                        <label
                                            key={c.id}
                                            className={`flex items-center gap-3 px-3 py-2.5 rounded-md cursor-pointer transition-colors border ${isHidden ? "border-border bg-accent/40 text-muted-foreground" : "border-emerald-200 bg-emerald-50/40"}`}
                                            data-testid={`card-toggle-${c.id}`}
                                        >
                                            <input
                                                type="checkbox"
                                                className="w-4 h-4 accent-brand"
                                                checked={!isHidden}
                                                onChange={() => {
                                                    setHiddenCards((prev) => (
                                                        prev.includes(c.id)
                                                            ? prev.filter((x) => x !== c.id)
                                                            : [...prev, c.id]
                                                    ));
                                                }}
                                            />
                                            <Icon size={18} weight="duotone" className={isHidden ? "text-muted-foreground" : "text-brand"} />
                                            <div className="flex-1">
                                                <div className="text-sm font-semibold">{c.label}</div>
                                                {c.hint && <div className="text-xs text-muted-foreground">{c.hint}</div>}
                                            </div>
                                        </label>
                                    );
                                })}
                            </div>
                        </div>
                    ))}

                    {/* Special standalone dashboard cards (iter-54) — not part
                        of the KPI grid but still toggleable via the same
                        `dashboard_hidden_cards` setting. */}
                    <div className="border border-amber-200 rounded-lg p-4 bg-amber-50/30" data-testid="special-cards-group">
                        <h3 className="font-bold text-foreground mb-3 flex items-center gap-2" style={{ fontFamily: "Tajawal" }}>
                            <SquaresFour size={18} weight="duotone" className="text-amber-700" />
                            بطاقات خاصة (أعلى لوحة التحكم)
                        </h3>
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                            {SPECIAL_DASHBOARD_CARDS.map((c) => {
                                const Icon = c.icon;
                                const isHidden = hiddenCards.includes(c.id);
                                return (
                                    <label
                                        key={c.id}
                                        className={`flex items-center gap-3 px-3 py-2.5 rounded-md cursor-pointer transition-colors border ${isHidden ? "border-border bg-accent/40 text-muted-foreground" : "border-emerald-200 bg-emerald-50/40"}`}
                                        data-testid={`card-toggle-${c.id}`}
                                    >
                                        <input
                                            type="checkbox"
                                            className="w-4 h-4 accent-brand"
                                            checked={!isHidden}
                                            onChange={() => {
                                                setHiddenCards((prev) => (
                                                    prev.includes(c.id)
                                                        ? prev.filter((x) => x !== c.id)
                                                        : [...prev, c.id]
                                                ));
                                            }}
                                        />
                                        <Icon size={18} weight="duotone" className={isHidden ? "text-muted-foreground" : "text-brand"} />
                                        <div className="flex-1">
                                            <div className="text-sm font-semibold">{c.label}</div>
                                            {c.hint && <div className="text-xs text-muted-foreground">{c.hint}</div>}
                                        </div>
                                    </label>
                                );
                            })}
                        </div>
                    </div>
                </div>
            </div>

            {/* NEW: Report-Included Order Statuses */}
            <div className="rounded-xl border border-border bg-white p-6" data-testid="report-statuses-section">
                <div className="mb-5">
                    <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>حالات الطلب المعتمدة للتقارير</h2>
                    <p className="text-sm text-muted-foreground mt-1">
                        حدِّد حالات الطلب التي تُحتسب ضمن لوحة التحكم والتقارير وحسابات الشحن الآجلة.
                        إذا تركتها فارغة، يتم احتساب <strong>جميع</strong> الطلبات بغضّ النظر عن حالتها (السلوك الافتراضي).
                    </p>
                    <p className="text-xs text-amber-700 mt-2">
                        ملاحظة: التحاليل القديمة (قبل ميزة الطلبات الموحَّدة) لا تحوي حالات للطلبات الفردية،
                        وستُستبعَد من الحسابات عند تفعيل هذا الفلتر. اضغط "إعادة معالجة" بجانب التحليل القديم لتفعيل الفلتر له.
                    </p>
                </div>

                <StatusListEditor
                    title="الحالات المُحتسَبة"
                    description="استخدم الاقتراحات أدناه (مأخوذة من طلباتك فعلياً) أو أضف حالة يدوياً."
                    values={reportIncluded}
                    onChange={setReportIncluded}
                    suggestions={discoveredStatuses.map((s) => s.name)}
                    suggestionMeta={discoveredStatuses.reduce((acc, s) => { acc[s.name] = s.count; return acc; }, {})}
                    testIdPrefix="report-included"
                />
            </div>

            {/* iter-45 — Electronic Net (صافي المدفوعات الإلكترونية) — Salla parity */}
            <div
                className="rounded-xl border-2 border-indigo-200 bg-gradient-to-br from-indigo-50/40 to-white p-6"
                data-testid="electronic-net-section"
            >
                <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                    <div>
                        <h2 className="text-2xl font-bold flex items-center gap-2" style={{ fontFamily: "Tajawal" }}>
                            <span className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-indigo-600 text-white">
                                <MagnifyingGlass size={20} weight="bold" />
                            </span>
                            صافي المدفوعات الإلكترونية — مطابقة سلة
                        </h2>
                        <p className="text-sm text-muted-foreground mt-1">
                            هذا القسم يخصّ <strong>بطاقة "صافي المدفوعات الإلكترونية" فقط</strong> في لوحة التحكم. يستبعد
                            الطلبات الملغية/المرتجعة/الفاشلة/المعلّقة لتطابق شاشة سلة → المدفوعات → غير المفوترة.
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={syncElectronicNetToSalla}
                        disabled={syncingElectronicNet}
                        className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs disabled:opacity-50"
                        data-testid="electronic-net-sync-btn"
                        title="استرجاع القائمة الافتراضية المطابقة لسلة"
                    >
                        <ArrowsClockwise size={14} weight="bold" />
                        {syncingElectronicNet ? "جارٍ…" : "مزامنة مطابقة مع سلة"}
                    </button>
                </div>

                <StatusListEditor
                    title="الحالات المستبعدة من حساب الصافي"
                    description={
                        "أي طلب تكون حالته مطابقة جزئياً لإحدى هذه الكلمات يُستبعَد من حساب " +
                        "صافي المدفوعات الإلكترونية. اتركها فارغة لتعطيل الفلتر تماماً (السلوك القديم)."
                    }
                    values={electronicNetExcluded}
                    onChange={setElectronicNetExcluded}
                    suggestions={Array.from(new Set([
                        "ملغ", "مسترد", "مرتجع", "فشل", "مرفوض", "بانتظار الدفع",
                        "cancel", "refund", "fail", "reject", "pending payment",
                        ...(discoveredStatuses.map((s) => s.name)),
                    ]))}
                    suggestionMeta={discoveredStatuses.reduce((acc, s) => { acc[s.name] = s.count; return acc; }, {})}
                    testIdPrefix="electronic-net-excluded"
                />

                <div className="my-5 border-t border-indigo-100" />

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                    <div>
                        <label className="block text-sm font-bold text-slate-700 mb-1.5">
                            رقم سلة المرجعي للمقارنة (اختياري)
                        </label>
                        <p className="text-xs text-muted-foreground mb-2 leading-relaxed">
                            انسخ "الصافي الإجمالي" من شاشة سلة → غير المفوترة لنفس الفترة.
                            سيظهر الفرق مباشرةً في زر "تفاصيل" على البطاقة.
                        </p>
                        <div className="flex items-center gap-2">
                            <input
                                type="number"
                                step="0.01"
                                value={sallaElectronicNetRef}
                                onChange={e => setSallaElectronicNetRef(e.target.value)}
                                placeholder="21715.87"
                                className="flex-1 px-3 py-2 rounded-lg border border-slate-300 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 outline-none text-sm"
                                data-testid="salla-electronic-net-ref-input"
                            />
                            <span className="text-sm text-slate-500 font-bold">ر.س</span>
                        </div>
                    </div>
                    <div className="rounded-lg bg-amber-50 border border-amber-200 p-3 text-xs text-amber-900">
                        <div className="font-bold flex items-center gap-1 mb-1">
                            <Warning size={14} weight="bold" />
                            ملاحظة مهمة
                        </div>
                        <p className="leading-relaxed">
                            هذا الفلتر يعمل على مستوى <strong>حالة الطلب</strong> فقط. الحل النهائي المطابق 100%
                            لسلة يتطلب جلب جدول معاملات الدفع من واجهة سلة Payments API
                            (سيُضاف لاحقاً ضمن Salla Direct Integration — Phase 2).
                        </p>
                    </div>
                </div>
            </div>


            {/* Phase 1: Order Status Approval settings */}
            {/* Iter-83 — Order Status Policy: drives /api/payment-gateway-metrics */}
            <OrderStatusPolicySection />

            {/* Iter-90 — Settlement Cycle settings per gateway */}
            <SettlementCycleSection />

            <div className="rounded-xl border border-border bg-white p-6" data-testid="status-approval-section">
                <div className="mb-5">
                    <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>حالات اعتماد الشحن و COD</h2>
                    <p className="text-sm text-muted-foreground mt-1">
                        حدِّد متى يصبح <strong>رصيد الشحن</strong> مستحقاً لشركة الشحن، ومتى يصبح <strong>COD</strong> محصَّلاً.
                        أي حالة غير محدَّدة هنا تظهر تحت "غير معتمد".
                    </p>
                </div>

                <StatusListEditor
                    title="حالات اعتماد رصيد الشحن"
                    description='افتراضياً: "تم التوصيل". أضف أي حالة إضافية ترى أنها تعتمد رصيد الشحن.'
                    values={shipApproved}
                    onChange={setShipApproved}
                    suggestions={Array.from(new Set([
                        ...(discoveredStatuses.map((s) => s.name)),
                        "تم التوصيل", "delivered", "completed", "تم الاستلام",
                    ]))}
                    suggestionMeta={discoveredStatuses.reduce((acc, s) => { acc[s.name] = s.count; return acc; }, {})}
                    testIdPrefix="ship-approved"
                />

                <div className="my-5 border-t border-border" />

                <StatusListEditor
                    title="حالات اعتماد COD"
                    description='افتراضياً: "تم التوصيل". المبالغ بهذه الحالات تظهر كمستحقات على شركة الشحن.'
                    values={codApproved}
                    onChange={setCodApproved}
                    suggestions={Array.from(new Set([
                        ...(discoveredStatuses.map((s) => s.name)),
                        "تم التوصيل", "delivered", "completed",
                    ]))}
                    suggestionMeta={discoveredStatuses.reduce((acc, s) => { acc[s.name] = s.count; return acc; }, {})}
                    testIdPrefix="cod-approved"
                />
            </div>

            {/* 🔐 Sensitive integration credentials — Snapchat + Meta inside a
                collapsible accordion so the long tokens don't dominate the
                page on first load. */}
            <div className="rounded-xl border-2 border-border bg-white p-4 sm:p-6 overflow-hidden" data-testid="sensitive-credentials-section">
                <div className="flex items-center gap-3 mb-2">
                    <div className="w-10 h-10 rounded-lg bg-slate-900 text-white flex items-center justify-center">
                        <LockKey size={22} weight="duotone" />
                    </div>
                    <div>
                        <h2 className="text-xl sm:text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>
                            🔐 بيانات الربط الحساسة
                        </h2>
                        <p className="text-xs sm:text-sm text-muted-foreground mt-0.5">
                            App IDs و Secrets و Access Tokens لمنصات الإعلانات — تظهر مختصرة افتراضياً. اضغط القسم لفتحه.
                        </p>
                    </div>
                </div>

                <details className="mt-4 group" data-testid="snap-credentials-details">
                    <summary className="cursor-pointer list-none flex items-center justify-between gap-3 py-3 px-4 rounded-lg bg-accent/40 hover:bg-accent/60 transition-colors">
                        <div className="flex items-center gap-2 min-w-0">
                            <div className="w-8 h-8 rounded-md flex items-center justify-center flex-shrink-0" style={{ background: "#FFFC00" }}>
                                <Ghost size={18} weight="fill" className="text-black" />
                            </div>
                            <span className="font-bold text-sm sm:text-base" style={{ fontFamily: "Tajawal" }}>Snapchat Ads — البيانات والاتصال</span>
                            {snapConfig.connected && (
                                <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 border border-emerald-300 flex-shrink-0">🟢 مربوط</span>
                            )}
                        </div>
                        <span className="text-muted-foreground text-xs transition-transform group-open:rotate-180">▼</span>
                    </summary>
                    <div className="mt-2">

            {/* Snapchat Ads integration */}
            <div className="rounded-xl border border-border bg-white p-4 sm:p-6 overflow-hidden" data-testid="snapchat-section">
                <div className="flex items-start justify-between gap-3 mb-5 flex-col md:flex-row">
                    <div className="flex items-start gap-3">
                        <div className="w-11 h-11 rounded-xl flex items-center justify-center" style={{ background: "#FFFC00" }}>
                            <Ghost size={26} weight="fill" className="text-black" />
                        </div>
                        <div>
                            <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>ربط Snapchat Ads</h2>
                            <p className="text-sm text-muted-foreground mt-1">
                                اربط حساب إعلانات سناب الخاص بك عبر OAuth لجلب تكاليف الإعلانات اليومية تلقائياً.
                            </p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        {snapConfig.connected ? (
                            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold bg-green-100 text-green-700" data-testid="snap-status-connected">
                                <LinkSimple size={14} weight="bold" /> متصل
                            </span>
                        ) : (
                            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold bg-gray-100 text-gray-700" data-testid="snap-status-disconnected">
                                <LinkBreak size={14} weight="bold" /> غير متصل
                            </span>
                        )}
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 min-w-0">
                    <div className="min-w-0">
                        <label className="block text-sm font-semibold mb-1.5">App ID (Client ID)</label>
                        <input
                            type="text"
                            value={snapConfig.client_id}
                            onChange={(e) => setSnapConfig({ ...snapConfig, client_id: e.target.value })}
                            placeholder="من Snap Business Manager → Business Details"
                            className="w-full max-w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                            data-testid="snap-client-id"
                            dir="ltr"
                        />
                    </div>
                    <div className="min-w-0">
                        <SecretField
                            label="App Secret (Client Secret)"
                            value={snapClientSecret}
                            onChange={(v) => setSnapClientSecret(v)}
                            existingMask={snapConfig.has_credentials ? "•••••••• (محفوظ)" : null}
                            placeholder="Secret يظهر مرة واحدة فقط من سناب"
                            testidPrefix="snap-client-secret"
                            rows={2}
                        />
                    </div>
                    <div className="md:col-span-2 min-w-0">
                        <label className="block text-sm font-semibold mb-1.5">Redirect URI</label>
                        <input
                            type="text"
                            value={snapConfig.redirect_uri}
                            onChange={(e) => setSnapConfig({ ...snapConfig, redirect_uri: e.target.value })}
                            className="w-full max-w-full px-3 py-2.5 text-sm sm:text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand font-mono"
                            style={{ overflowWrap: "anywhere", wordBreak: "break-all" }}
                            data-testid="snap-redirect-uri"
                            dir="ltr"
                        />
                        <p className="text-xs text-muted-foreground mt-1.5">
                            ← انسخ هذا الرابط بالضبط إلى حقل Redirect URI داخل تطبيق OAuth في Snap Business Manager.
                        </p>
                    </div>
                </div>

                <div className="mt-5 flex flex-wrap items-center gap-2">
                    <button
                        type="button"
                        onClick={saveSnapConfig}
                        disabled={snapSaving}
                        className="inline-flex items-center gap-2 px-4 py-2.5 border border-border rounded-lg text-sm font-semibold hover:bg-accent transition-colors disabled:opacity-60"
                        data-testid="snap-save-btn"
                    >
                        <FloppyDisk size={18} weight="bold" />
                        {snapSaving ? "جاري الحفظ…" : "حفظ بيانات التطبيق"}
                    </button>

                    <button
                        type="button"
                        onClick={connectSnap}
                        disabled={snapConnecting || !snapConfig.has_credentials}
                        title={!snapConfig.has_credentials ? "احفظ بيانات التطبيق أولاً" : ""}
                        className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-bold text-black transition-opacity disabled:opacity-50"
                        style={{ background: "#FFFC00" }}
                        data-testid="snap-connect-btn"
                    >
                        <Ghost size={18} weight="fill" />
                        {snapConnecting ? "جاري الانتقال إلى سناب…" : (snapConfig.connected ? "إعادة الربط" : "الاتصال بسناب")}
                    </button>

                    {snapConfig.connected && (
                        <>
                            <button
                                type="button"
                                onClick={loadSnapAccounts}
                                disabled={snapLoadingAccounts}
                                className="inline-flex items-center gap-2 px-4 py-2.5 border border-border rounded-lg text-sm font-semibold hover:bg-accent transition-colors disabled:opacity-60"
                                data-testid="snap-load-accounts-btn"
                            >
                                <ArrowsClockwise size={18} weight="bold" />
                                {snapLoadingAccounts ? "جاري الجلب…" : "جلب حسابات الإعلانات"}
                            </button>
                            <button
                                type="button"
                                onClick={disconnectSnap}
                                className="inline-flex items-center gap-2 px-4 py-2.5 border border-border rounded-lg text-sm font-semibold hover:bg-red-50 hover:text-red-600 hover:border-red-200 transition-colors"
                                data-testid="snap-disconnect-btn"
                            >
                                <LinkBreak size={18} weight="bold" />
                                فصل الحساب
                            </button>
                        </>
                    )}
                </div>

                {snapConfig.connected && snapConfig.ad_account_id && (
                    <div className="mt-4 p-3 rounded-lg bg-accent/40 text-sm" data-testid="snap-selected-account">
                        <span className="text-muted-foreground">الحساب المختار: </span>
                        <span className="font-semibold">{snapConfig.ad_account_name || snapConfig.ad_account_id}</span>
                        <span className="text-muted-foreground ms-2 text-xs" dir="ltr">({snapConfig.ad_account_id})</span>
                    </div>
                )}

                {snapAccounts.length > 0 && (
                    <div className="mt-4 border border-border rounded-lg overflow-hidden" data-testid="snap-accounts-list">
                        <div className="px-3 py-2 bg-accent/60 text-xs font-semibold text-muted-foreground flex items-center justify-between gap-3 flex-wrap">
                            <span>اختر حسابات الإعلانات المراد تفعيلها (يمكن اختيار أكثر من حساب)</span>
                            <span className="text-[11px] text-brand font-bold" data-testid="snap-enabled-count">
                                {snapEnabledIds.size} / {snapAccounts.length} مُفعَّل
                            </span>
                        </div>
                        <div className="divide-y divide-border">
                            {snapAccounts.map((acc) => {
                                const isEnabled = snapEnabledIds.has(acc.ad_account_id);
                                return (
                                    <label
                                        key={acc.ad_account_id}
                                        className={[
                                            "flex items-center justify-between gap-3 p-3 cursor-pointer transition-colors",
                                            isEnabled ? "bg-emerald-50/60 hover:bg-emerald-50" : "hover:bg-accent/30",
                                        ].join(" ")}
                                        data-testid={`snap-account-row-${acc.ad_account_id}`}
                                    >
                                        <div className="flex items-center gap-3 min-w-0 flex-1">
                                            <input
                                                type="checkbox"
                                                checked={isEnabled}
                                                onChange={() => toggleSnapAccount(acc.ad_account_id)}
                                                className="w-5 h-5 rounded border-border accent-brand flex-shrink-0"
                                                data-testid={`snap-account-toggle-${acc.ad_account_id}`}
                                            />
                                            <div className="min-w-0">
                                                <div className="font-semibold truncate flex items-center gap-2">
                                                    {acc.name || "—"}
                                                    {acc.currency && acc.currency !== "SAR" && (
                                                        <span className="text-[10px] px-1.5 py-0.5 bg-amber-100 text-amber-800 rounded-full font-bold">
                                                            {acc.currency} → SAR
                                                        </span>
                                                    )}
                                                </div>
                                                <div className="text-xs text-muted-foreground truncate" dir="ltr">
                                                    {acc.ad_account_id} · {acc.currency || ""} · {acc.status || ""}
                                                </div>
                                            </div>
                                        </div>
                                        {isEnabled && (
                                            <span className="text-xs px-2 py-1 rounded-full bg-emerald-100 text-emerald-800 font-bold whitespace-nowrap flex-shrink-0">
                                                ✓ مُفعَّل
                                            </span>
                                        )}
                                    </label>
                                );
                            })}
                        </div>
                        <div className="px-3 py-3 bg-accent/30 border-t border-border flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
                            <div className="text-xs text-muted-foreground leading-relaxed">
                                💡 جميع الحسابات المفعَّلة سيتم احتساب صرفها بتوقيت <strong>Asia/Riyadh (00:00-23:59)</strong> وتحويلها إلى SAR تلقائياً.
                            </div>
                            <button
                                type="button"
                                onClick={saveSnapSelectedAccounts}
                                disabled={snapSelectionSaving}
                                className="px-4 py-2 bg-brand text-white rounded-lg font-bold text-sm hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                                data-testid="snap-save-selected-accounts-btn"
                            >
                                {snapSelectionSaving ? "جاري الحفظ…" : "حفظ الحسابات المختارة"}
                            </button>
                        </div>
                    </div>
                )}
            </div>

            {/* Meta Ads (Facebook + Instagram) integration */}
                    </div>
                </details>

                <details className="mt-3 group" data-testid="meta-credentials-details">
                    <summary className="cursor-pointer list-none flex items-center justify-between gap-3 py-3 px-4 rounded-lg bg-accent/40 hover:bg-accent/60 transition-colors">
                        <div className="flex items-center gap-2 min-w-0">
                            <div className="w-8 h-8 rounded-md flex items-center justify-center flex-shrink-0 bg-blue-600 text-white font-extrabold text-sm">
                                f
                            </div>
                            <span className="font-bold text-sm sm:text-base" style={{ fontFamily: "Tajawal" }}>Meta Ads (Facebook + Instagram) — البيانات والاتصال</span>
                            {metaConfig.connected && metaConfig.connection_status && (
                                <StatusBadge status={metaConfig.connection_status} />
                            )}
                            {metaConfig.connected && !metaConfig.connection_status && (
                                <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 border border-emerald-300 flex-shrink-0">🟢 مربوط</span>
                            )}
                        </div>
                        <span className="text-muted-foreground text-xs transition-transform group-open:rotate-180">▼</span>
                    </summary>
                    <div className="mt-2">

            <div className="rounded-xl border border-border bg-white p-4 sm:p-6 overflow-hidden" data-testid="meta-config-section">
                <div className="mb-5 flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg bg-blue-600 text-white flex items-center justify-center font-extrabold">
                        f
                    </div>
                    <div>
                        <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>ربط Meta Ads</h2>
                        <p className="text-sm text-muted-foreground mt-1">
                            اجلب تكاليف إعلانات Facebook و Instagram تلقائياً عبر Marketing API. يومياً، يتم جلب بيانات آخر 7 أيام تلقائياً عند فتح Dashboard.
                        </p>
                    </div>
                </div>

                {metaConfig.connected && (
                    <div className="mb-4 p-3 rounded-lg bg-emerald-50 border border-emerald-200 text-sm" data-testid="meta-connected-badge">
                        <span className="font-bold text-emerald-700">✓ مربوط</span>
                        <span className="text-muted-foreground mx-2">•</span>
                        <span dir="ltr">{metaConfig.ad_account_id}</span>
                        {metaConfig.last_sync_at && (
                            <span className="ms-3 text-xs text-muted-foreground">
                                آخر مزامنة: {new Date(metaConfig.last_sync_at).toLocaleString("en-US", { dateStyle: "short", timeStyle: "short" })}
                            </span>
                        )}
                    </div>
                )}

                {metaConfig.connected && metaConfig.connection_status === "expired" && (
                    <div
                        className="mb-4 p-4 rounded-lg bg-red-50 border-2 border-red-300"
                        data-testid="meta-settings-expired-banner"
                    >
                        <div className="flex items-start gap-3">
                            <div className="text-2xl flex-shrink-0">⚠️</div>
                            <div className="flex-1 min-w-0">
                                <div className="font-bold text-red-900 mb-1" style={{ fontFamily: "Tajawal" }}>
                                    الربط منتهي الصلاحية
                                </div>
                                <div className="text-sm text-red-800 leading-relaxed">
                                    {metaConfig.last_error_message || "انتهت صلاحية ربط Meta Ads، يرجى تحديث Access Token من الحقل أدناه ثم اضغط اختبار الاتصال."}
                                </div>
                                {metaConfig.last_error_at && (
                                    <div className="text-xs text-red-700/80 mt-1">
                                        منذ: {new Date(metaConfig.last_error_at).toLocaleString("en-US", { dateStyle: "short", timeStyle: "short" })}
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                )}

                {metaConfig.connected && metaConfig.connection_status && !["ok", "expired"].includes(metaConfig.connection_status) && metaConfig.last_error_message && (
                    <div className="mb-4 p-3 rounded-lg bg-amber-50 border border-amber-200 text-sm text-amber-900" data-testid="meta-settings-warn-banner">
                        ⚠️ {metaConfig.last_error_message}
                    </div>
                )}

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 min-w-0">
                    <div className="min-w-0">
                        <label className="block text-xs font-bold text-muted-foreground mb-1.5">Meta App ID</label>
                        <input
                            type="text"
                            value={metaForm.app_id}
                            onChange={(e) => setMetaForm({ ...metaForm, app_id: e.target.value })}
                            placeholder="1234567890123456"
                            className="w-full max-w-full px-3 py-2.5 text-sm border border-border rounded-lg font-mono"
                            dir="ltr"
                            data-testid="meta-app-id-input"
                        />
                    </div>
                    <div className="min-w-0">
                        <SecretField
                            label="Meta App Secret"
                            value={metaForm.app_secret}
                            onChange={(v) => setMetaForm({ ...metaForm, app_secret: v })}
                            existingMask={metaConfig.connected ? metaConfig.app_secret_masked : null}
                            placeholder="abc123xyz…"
                            testidPrefix="meta-app-secret"
                            rows={2}
                        />
                    </div>
                    <div className="md:col-span-2 min-w-0">
                        {/* ── Short-lived → Long-lived token exchange ──────
                            Lets the merchant paste a 1-hour token from Graph API
                            Explorer and convert it to 60 days with one click.
                            The result is saved server-side immediately; no need
                            to also press "Save".                                  */}
                        <div className="rounded-lg border-2 border-dashed border-blue-300 bg-blue-50/40 p-3 sm:p-4 mb-4">
                            <div className="flex items-center gap-2 mb-2">
                                <ArrowsClockwise size={18} weight="bold" className="text-blue-700" />
                                <h3 className="text-sm sm:text-base font-bold text-blue-900" style={{ fontFamily: "Tajawal" }}>
                                    تحويل تلقائي إلى Long-lived Token (60 يوم)
                                </h3>
                            </div>
                            <p className="text-xs text-blue-900/80 leading-relaxed mb-3">
                                ألصق Short-lived Token (1-2 ساعة) من <a href="https://developers.facebook.com/tools/explorer/" target="_blank" rel="noreferrer" className="font-bold underline">Graph API Explorer</a> — مع تفعيل صلاحيات <code className="bg-white/60 px-1 rounded text-[10px]">ads_read</code> + <code className="bg-white/60 px-1 rounded text-[10px]">business_management</code>. سنحوّله ونحفظه تلقائياً لمدة 60 يوم.
                            </p>
                            <SecretField
                                label="Short-lived Token من Graph API Explorer"
                                value={shortLivedToken}
                                onChange={setShortLivedToken}
                                placeholder="EAAxxxx… (1-hour token)"
                                testidPrefix="meta-short-lived-token"
                                rows={3}
                                helper="بمجرد التحويل، يتم استبدال Access Token المحفوظ بالنسخة طويلة العمر تلقائياً."
                            />
                            <button
                                type="button"
                                onClick={exchangeShortToLongLived}
                                disabled={exchangingToken || !shortLivedToken.trim()}
                                className="mt-3 inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg font-bold text-sm transition-colors w-full sm:w-auto"
                                data-testid="meta-exchange-token-btn"
                                title="يحوّل التوكن القصير عبر Meta API ويحفظه تلقائياً بدلاً من Access Token الحالي"
                            >
                                <ArrowsClockwise size={16} weight="bold" className={exchangingToken ? "animate-spin" : ""} />
                                {exchangingToken ? "جاري التحويل…" : "تحويل إلى Long-lived Token"}
                            </button>

                            {/* Token expiry hint — only when we have the timestamp */}
                            {metaConfig.token_expires_at && (
                                <div
                                    className="mt-3 text-xs text-blue-900/80"
                                    data-testid="meta-token-expiry-info"
                                >
                                    <span className="font-bold">⏰ ينتهي التوكن الحالي:</span>{" "}
                                    {new Date(metaConfig.token_expires_at).toLocaleString("en-US", {
                                        dateStyle: "medium", timeStyle: "short",
                                    })}
                                    {(() => {
                                        const ms = new Date(metaConfig.token_expires_at).getTime() - Date.now();
                                        const days = Math.round(ms / 86400000);
                                        if (days > 7) return <span className="ms-2 text-emerald-700 font-bold">(صالح {days} يوم)</span>;
                                        if (days > 0) return <span className="ms-2 text-amber-700 font-bold">⚠️ متبقي {days} يوم فقط — جدّد الآن</span>;
                                        return <span className="ms-2 text-red-700 font-bold">❌ منتهي</span>;
                                    })()}
                                </div>
                            )}
                        </div>

                        <SecretField
                            label="Access Token (Long-lived) — أو الصق توكن جاهز يدوياً"
                            value={metaForm.access_token}
                            onChange={(v) => setMetaForm({ ...metaForm, access_token: v })}
                            existingMask={metaConfig.connected ? metaConfig.access_token_masked : null}
                            placeholder="EAAxxxxxxxxxxxxxxxxxxx"
                            testidPrefix="meta-access-token"
                            rows={4}
                            statusBadge={metaConfig.connected && metaConfig.connection_status
                                ? <StatusBadge status={metaConfig.connection_status} />
                                : null}
                            helper={
                                <>
                                    استخدم الزر أعلاه للتحويل التلقائي، أو ألصق Long-lived token جاهز هنا واضغط <strong>اختبار الاتصال</strong>.
                                </>
                            }
                        />
                    </div>
                    <div>
                        <label className="block text-xs font-bold text-muted-foreground mb-1.5">Ad Account ID</label>
                        <input
                            type="text"
                            value={metaForm.ad_account_id}
                            onChange={(e) => setMetaForm({ ...metaForm, ad_account_id: e.target.value })}
                            placeholder="act_123456789 أو 123456789"
                            className="w-full px-3 py-2.5 text-sm border border-border rounded-lg font-mono"
                            dir="ltr"
                            data-testid="meta-ad-account-input"
                        />
                    </div>
                </div>

                <div className="mt-5 flex flex-wrap gap-2">
                    <button
                        type="button"
                        onClick={testMetaConnection}
                        disabled={metaTesting || metaSaving}
                        className="inline-flex items-center gap-2 px-5 py-2.5 bg-amber-500 hover:bg-amber-600 disabled:opacity-60 text-white rounded-lg font-bold text-sm"
                        data-testid="meta-test-connection-btn"
                        title="يتحقق من Meta API ويحفظ التوكن الجديد فقط إذا نجح الاختبار"
                    >
                        <ArrowsClockwise size={16} weight="bold" className={metaTesting ? "animate-spin" : ""} />
                        {metaTesting ? "جاري الاختبار…" : "اختبار الاتصال"}
                    </button>
                    <button
                        type="button"
                        onClick={saveMetaConfig}
                        disabled={metaSaving}
                        className="inline-flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white rounded-lg font-bold text-sm"
                        data-testid="meta-save-btn"
                    >
                        <FloppyDisk size={16} weight="bold" />
                        {metaSaving ? "جاري الحفظ…" : "حفظ بدون اختبار"}
                    </button>
                    {metaConfig.connected && (
                        <>
                            <button
                                type="button"
                                onClick={syncMetaNow}
                                disabled={metaSyncing}
                                className="inline-flex items-center gap-2 px-5 py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white rounded-lg font-bold text-sm"
                                data-testid="meta-sync-btn"
                            >
                                <ArrowsClockwise size={16} weight="bold" />
                                {metaSyncing ? "جاري المزامنة…" : "مزامنة Meta الآن (30 يوم)"}
                            </button>
                            <button
                                type="button"
                                onClick={disconnectMeta}
                                className="inline-flex items-center gap-2 px-4 py-2.5 border border-red-300 text-red-700 hover:bg-red-50 rounded-lg font-bold text-sm"
                                data-testid="meta-disconnect-btn"
                            >
                                <LinkBreak size={16} weight="bold" />
                                فصل الحساب
                            </button>
                        </>
                    )}
                </div>

                {metaConfig.last_sync_summary && (
                    <details className="mt-4 text-xs">
                        <summary className="cursor-pointer text-muted-foreground hover:text-foreground font-bold">
                            تفاصيل آخر مزامنة ({metaConfig.last_sync_summary.upserted} صف)
                        </summary>
                        <pre dir="ltr" className="mt-2 p-3 bg-accent/40 rounded text-[10px] overflow-x-auto">{JSON.stringify(metaConfig.last_sync_summary, null, 2)}</pre>
                    </details>
                )}
            </div>
                    </div>
                </details>
            </div>
        </div>
    );
}


function StatusListEditor({ title, description, values, onChange, suggestions, suggestionMeta, testIdPrefix }) {
    const [draft, setDraft] = useState("");
    const add = () => {
        const v = draft.trim();
        if (!v || values.includes(v)) { setDraft(""); return; }
        onChange([...values, v]);
        setDraft("");
    };
    const remove = (v) => onChange(values.filter((x) => x !== v));
    const toggleSuggestion = (s) => {
        if (values.includes(s)) {
            remove(s);
        } else {
            onChange([...values, s]);
        }
    };
    return (
        <div data-testid={`${testIdPrefix}-editor`}>
            <h3 className="font-bold text-base mb-1">{title}</h3>
            <p className="text-xs text-muted-foreground mb-3">{description}</p>

            <div className="flex flex-wrap gap-2 mb-3" data-testid={`${testIdPrefix}-chips`}>
                {values.length === 0 && <span className="text-xs text-muted-foreground italic">لا توجد حالات معتمدة بعد</span>}
                {values.map((v) => (
                    <span key={v} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-bold bg-emerald-50 text-emerald-800 border border-emerald-200">
                        {v}
                        <button
                            type="button"
                            onClick={() => remove(v)}
                            className="hover:text-red-600 transition-colors"
                            title="إزالة"
                            data-testid={`${testIdPrefix}-remove-${v}`}
                        >×</button>
                    </span>
                ))}
            </div>

            <div className="flex items-center gap-2 mb-3">
                <input
                    type="text"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }}
                    placeholder='مثلاً: تم التوصيل، delivered…'
                    className="flex-1 px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                    data-testid={`${testIdPrefix}-input`}
                />
                <button
                    type="button"
                    onClick={add}
                    className="px-3 py-2 rounded-lg border border-border text-sm font-semibold hover:bg-accent transition-colors"
                    data-testid={`${testIdPrefix}-add-btn`}
                >+ إضافة</button>
            </div>

            {suggestions.length > 0 && (
                <div className="text-xs text-muted-foreground">
                    <span className="font-semibold ms-1">اقتراحات سريعة:</span>
                    {suggestions.map((s) => {
                        const cnt = suggestionMeta?.[s];
                        return (
                            <button
                                key={s}
                                type="button"
                                onClick={() => toggleSuggestion(s)}
                                className={`mx-1 inline-block px-2 py-1 rounded-md border text-xs font-semibold transition-colors ${values.includes(s) ? "bg-emerald-100 border-emerald-300 text-emerald-800" : "bg-accent/40 border-border hover:bg-accent"}`}
                                data-testid={`${testIdPrefix}-suggest-${s}`}
                            >{values.includes(s) ? "✓ " : "+ "}{s}{cnt !== undefined && cnt > 0 ? ` (${cnt})` : ""}</button>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
