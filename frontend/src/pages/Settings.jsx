import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Plus, Trash, FloppyDisk, LinkSimple, LinkBreak, Ghost, ArrowsClockwise } from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const DEFAULT_SNAP_REDIRECT = `${BACKEND_URL}/api/snapchat/oauth/callback`;

export default function Settings() {
    const [payments, setPayments] = useState([]);
    const [shippings, setShippings] = useState([]);
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
        } catch {}
    };

    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get("/settings");
                setPayments(data.payment_methods || []);
                setShippings(data.shipping_companies || []);
            } finally { setLoading(false); }
        })();
        loadSnapConfig();
    }, []);

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
            const { data } = await api.get("/snapchat/adaccounts");
            setSnapAccounts(data.adaccounts || []);
            if ((data.adaccounts || []).length === 0) {
                toast.message("لم يتم العثور على حسابات إعلانات في حسابك");
            }
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setSnapLoadingAccounts(false); }
    };

    const selectAccount = async (acc) => {
        try {
            await api.post("/snapchat/select-adaccount", {
                ad_account_id: acc.ad_account_id,
                ad_account_name: acc.name || "",
            });
            toast.success(`تم اختيار حساب: ${acc.name || acc.ad_account_id}`);
            await loadSnapConfig();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
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
                })).filter((s) => s.name),
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
                    <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight" style={{ fontFamily: "Tajawal" }}>الإعدادات</h1>
                    <p className="text-muted-foreground mt-2 text-base">
                        اضبط نسب عمولات بوابات الدفع وتكاليف شركات الشحن. ستُستخدم تلقائياً في حساب الأرباح.
                    </p>
                </div>
                <button
                    onClick={save}
                    disabled={saving}
                    className="inline-flex items-center gap-2 px-5 py-3 bg-brand text-white font-bold rounded-lg bg-brand-hover transition-colors disabled:opacity-60"
                    data-testid="save-settings-btn"
                >
                    <FloppyDisk size={20} weight="bold" />
                    {saving ? "جاري الحفظ…" : "حفظ التغييرات"}
                </button>
            </div>

            {/* Payment methods */}
            <div className="rounded-xl border border-border bg-white p-6">
                <div className="flex items-center justify-between mb-5">
                    <div>
                        <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>طرق الدفع وعمولاتها</h2>
                        <p className="text-sm text-muted-foreground mt-1">نسبة % + مبلغ ثابت لكل طلب + نسبة ضريبة على إجمالي العمولة</p>
                    </div>
                    <button
                        onClick={() => setPayments([...payments, { name: "", commission_percent: 0, fixed_fee: 0, vat_percent: 15 }])}
                        className="inline-flex items-center gap-2 px-3 py-2 border border-border rounded-lg text-sm font-semibold hover:bg-accent transition-colors"
                        data-testid="add-payment-btn"
                    >
                        <Plus size={16} weight="bold" /> إضافة
                    </button>
                </div>
                <div className="space-y-3" data-testid="payment-methods-list">
                    {/* Header row */}
                    <div className="grid grid-cols-12 gap-3 text-xs font-semibold text-muted-foreground px-1 hidden md:grid">
                        <div className="col-span-4">اسم بوابة الدفع</div>
                        <div className="col-span-2 text-center">النسبة %</div>
                        <div className="col-span-2 text-center">مبلغ ثابت (ر.س)</div>
                        <div className="col-span-3 text-center">نسبة الضريبة على العمولة %</div>
                        <div className="col-span-1"></div>
                    </div>
                    {payments.map((p, i) => (
                        <div key={i} className="grid grid-cols-12 gap-3 items-center">
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
                        onClick={() => setShippings([...shippings, { name: "", cost_per_order: 0, vat_percent: 15 }])}
                        className="inline-flex items-center gap-2 px-3 py-2 border border-border rounded-lg text-sm font-semibold hover:bg-accent transition-colors"
                        data-testid="add-shipping-btn"
                    >
                        <Plus size={16} weight="bold" /> إضافة
                    </button>
                </div>
                <div className="space-y-3" data-testid="shipping-companies-list">
                    {/* Header row */}
                    <div className="grid grid-cols-12 gap-3 text-xs font-semibold text-muted-foreground px-1 hidden md:grid">
                        <div className="col-span-5">اسم شركة الشحن</div>
                        <div className="col-span-3 text-center">تكلفة الشحنة (ر.س)</div>
                        <div className="col-span-3 text-center">نسبة الضريبة على الشحن %</div>
                        <div className="col-span-1"></div>
                    </div>
                    {shippings.map((s, i) => (
                        <div key={i} className="grid grid-cols-12 gap-3 items-center">
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
            {/* Snapchat Ads integration */}
            <div className="rounded-xl border border-border bg-white p-6" data-testid="snapchat-section">
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

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className="block text-sm font-semibold mb-1.5">App ID (Client ID)</label>
                        <input
                            type="text"
                            value={snapConfig.client_id}
                            onChange={(e) => setSnapConfig({ ...snapConfig, client_id: e.target.value })}
                            placeholder="من Snap Business Manager → Business Details"
                            className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                            data-testid="snap-client-id"
                            dir="ltr"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-semibold mb-1.5">App Secret (Client Secret)</label>
                        <input
                            type="password"
                            value={snapClientSecret}
                            onChange={(e) => setSnapClientSecret(e.target.value)}
                            placeholder={snapConfig.has_credentials ? "•••••••• (محفوظ — اتركه فارغاً لعدم التغيير)" : "Secret يظهر مرة واحدة فقط من سناب"}
                            className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                            data-testid="snap-client-secret"
                            dir="ltr"
                        />
                    </div>
                    <div className="md:col-span-2">
                        <label className="block text-sm font-semibold mb-1.5">Redirect URI</label>
                        <input
                            type="text"
                            value={snapConfig.redirect_uri}
                            onChange={(e) => setSnapConfig({ ...snapConfig, redirect_uri: e.target.value })}
                            className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
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
                        <div className="px-3 py-2 bg-accent/60 text-xs font-semibold text-muted-foreground">
                            اختر حساب الإعلانات الذي تريد ربطه
                        </div>
                        <div className="divide-y divide-border">
                            {snapAccounts.map((acc) => (
                                <div key={acc.ad_account_id} className="flex items-center justify-between gap-3 p-3 hover:bg-accent/30">
                                    <div className="min-w-0">
                                        <div className="font-semibold truncate">{acc.name || "—"}</div>
                                        <div className="text-xs text-muted-foreground truncate" dir="ltr">
                                            {acc.ad_account_id} · {acc.currency || ""} · {acc.status || ""}
                                        </div>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => selectAccount(acc)}
                                        className="px-3 py-1.5 text-xs font-bold rounded-lg border border-border hover:bg-brand hover:text-white hover:border-brand transition-colors whitespace-nowrap"
                                        data-testid={`snap-select-account-${acc.ad_account_id}`}
                                    >
                                        {snapConfig.ad_account_id === acc.ad_account_id ? "مختار حالياً" : "اختيار"}
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
