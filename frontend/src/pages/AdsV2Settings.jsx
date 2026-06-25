import React, { useEffect, useState, useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Card, CardContent, CardHeader, CardTitle,
} from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Switch } from "../components/ui/switch";
import { Badge } from "../components/ui/badge";
import {
  Tabs, TabsList, TabsTrigger, TabsContent,
} from "../components/ui/tabs";
import {
  Select, SelectTrigger, SelectValue,
  SelectContent, SelectItem,
} from "../components/ui/select";
import {
  Table, TableHeader, TableRow, TableHead,
  TableBody, TableCell,
} from "../components/ui/table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger,
} from "../components/ui/dialog";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// ──────────────────────────────────────────────────────────────────────
// Styled input wrappers — enforce white text on dark backgrounds across
// all Ads V2 forms. Centralized so a single edit fixes every field.
// ──────────────────────────────────────────────────────────────────────
const FIELD_CLS =
  "bg-zinc-950 border-zinc-800 text-zinc-100 placeholder:text-zinc-500 " +
  "focus-visible:ring-zinc-700";

const LABEL_CLS = "text-zinc-300 text-xs font-medium";

const FInput = React.forwardRef((props, ref) => (
  <Input
    ref={ref}
    {...props}
    className={`${FIELD_CLS} ${props.className || ""}`}
  />
));
FInput.displayName = "FInput";

const FLabel = ({ children, className = "", ...rest }) => (
  <Label className={`${LABEL_CLS} ${className}`} {...rest}>
    {children}
  </Label>
);

const FSelectTrigger = React.forwardRef(({ children, ...props }, ref) => (
  <SelectTrigger
    ref={ref}
    {...props}
    className={`${FIELD_CLS} ${props.className || ""}`}
  >
    {children}
  </SelectTrigger>
));
FSelectTrigger.displayName = "FSelectTrigger";

const FSelectContent = ({ children, ...props }) => (
  <SelectContent
    {...props}
    className={`bg-zinc-950 border-zinc-800 text-zinc-100 ${props.className || ""}`}
  >
    {children}
  </SelectContent>
);

const FSelectItem = ({ children, ...props }) => (
  <SelectItem
    {...props}
    className={`text-zinc-100 focus:bg-zinc-800 focus:text-zinc-100 ${props.className || ""}`}
  >
    {children}
  </SelectItem>
);

// ──────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────
const PROVIDER_LABEL = {
  meta: "Meta",
  snapchat: "Snapchat",
  tiktok: "TikTok",
  google_ads: "Google Ads",
};

// Arabic labels for sync_status / connection_status / token health
const STATUS_AR = {
  active: "نشط",
  paused: "موقوف مؤقتاً",
  error: "خطأ",
  token_expired: "انتهت صلاحية التوكن",
  unauthorized: "توكن غير صالح",
  discovered: "مُكتشف",
  missing: "غير مربوط",
  token_invalid: "توكن غير صالح",
  ok: "سليم",
};

const STATUS_TONE = {
  active: "bg-emerald-500/20 text-emerald-100 border-emerald-500/40",
  paused: "bg-zinc-500/20 text-zinc-100 border-zinc-500/40",
  error: "bg-red-500/20 text-red-100 border-red-500/40",
  token_expired:
    "bg-amber-500/20 text-amber-100 border-amber-500/40",
  unauthorized:
    "bg-red-500/20 text-red-100 border-red-500/40",
  discovered:
    "bg-blue-500/20 text-blue-100 border-blue-500/40",
  missing: "bg-zinc-500/20 text-zinc-100 border-zinc-500/40",
  token_invalid:
    "bg-red-500/20 text-red-100 border-red-500/40",
};

const tone = (s) => STATUS_TONE[s] || "bg-zinc-500/20 text-zinc-100 border-zinc-500/40";
const statusAr = (s) => STATUS_AR[s] || s || "—";

// ── 3-tier per-account status dictionaries ─────────────────────────
const TOKEN_STATUS_AR = {
  ok:           { label: "سليم",            cls: "bg-emerald-500/20 text-emerald-100 border-emerald-500/40" },
  expired:      { label: "منتهي",           cls: "bg-amber-500/20 text-amber-100 border-amber-500/40" },
  needs_relink: { label: "يحتاج إعادة ربط", cls: "bg-orange-500/25 text-orange-100 border-orange-500/40" },
  missing:      { label: "غير مربوط",       cls: "bg-zinc-500/20 text-zinc-100 border-zinc-500/40" },
};

const CONNECTION_STATUS_AR = {
  connected:   { label: "متصل",                cls: "bg-emerald-500/20 text-emerald-100 border-emerald-500/40" },
  unreachable: { label: "تعذر الاتصال",         cls: "bg-red-500/25 text-red-100 border-red-500/40" },
  timeout:     { label: "انتهت مهلة الاتصال",   cls: "bg-amber-500/20 text-amber-100 border-amber-500/40" },
  api_error:   { label: "خطأ من API",          cls: "bg-orange-500/25 text-orange-100 border-orange-500/40" },
  unknown:     { label: "لم يتم الفحص بعد",     cls: "bg-zinc-500/20 text-zinc-200 border-zinc-500/40" },
};

const SYNC_RUN_STATUS_AR = {
  synced:          { label: "تمت المزامنة بنجاح",   cls: "bg-emerald-500/20 text-emerald-100 border-emerald-500/40" },
  awaiting_first:  { label: "بانتظار أول مزامنة",   cls: "bg-blue-500/20 text-blue-100 border-blue-500/40" },
  no_data:         { label: "لا توجد بيانات",        cls: "bg-amber-500/20 text-amber-100 border-amber-500/40" },
  last_failed:     { label: "آخر مزامنة فشلت",       cls: "bg-red-500/25 text-red-100 border-red-500/40" },
  disabled:        { label: "المزامنة موقوفة",       cls: "bg-zinc-500/20 text-zinc-200 border-zinc-500/40" },
};

// Specific reasons — translated for the merchant (no bare "خطأ" allowed)
const REASON_AR = {
  ok:                          "كل شيء سليم",
  no_data_for_date:            "لا توجد بيانات لهذا التاريخ",
  no_data_for_account:         "الحساب لا يحتوي على بيانات صرف",
  account_not_found:           "الحساب غير موجود أو لا صلاحية له",
  access_denied:               "Access Denied — صلاحيات غير كافية",
  token_no_access_to_account:  "التوكن لا يملك صلاحية هذا الحساب",
  organization_mismatch:       "Organization مختلفة عن المرتبطة بالتوكن",
  api_rate_limit:              "تجاوزت حدّ استدعاءات API — أعد المحاولة بعد قليل",
  api_http_error:              "ردّ HTTP غير متوقّع من API",
  token_expired:               "انتهت صلاحية التوكن — يحتاج إعادة ربط",
  token_needs_relink:          "التوكن يحتاج إعادة ربط",
  token_missing:               "لا يوجد توكن مربوط",
  account_inactive:            "الحساب غير مفعّل",
  awaiting_first_sync:         "لم تتم أول مزامنة بعد",
  last_sync_failed:            "آخر محاولة مزامنة فشلت — راجع السجل",
  network_or_timeout:          "خطأ شبكة أو انتهاء مهلة",
  sync_disabled:               "المزامنة موقوفة لهذا الحساب",
  no_call_yet:                 "لم يُجرَ أي اتصال بعد",
};

// Event names → friendly Arabic labels
const EVENT_AR = {
  sync_run:               "مزامنة ناجحة",
  sync_failed:            "فشل في المزامنة",
  reconciliation_checked: "مطابقة من المنصة",
  token_expired:          "انتهاء صلاحية التوكن",
  token_alert:            "تنبيه التوكن",
  account_created:        "إنشاء الحساب",
  account_modified:       "تعديل الحساب",
  account_disabled:       "إيقاف الحساب",
  account_relinked_v1:    "إعادة ربط",
  fx_changed:             "تغيير سعر الصرف",
  bank_fee_changed:       "تغيير العمولة البنكية",
  review_approved:        "اعتماد المراجعة",
  review_rejected:        "رفض المراجعة",
};
const eventAr = (e) => EVENT_AR[e] || e || "—";
const reasonAr = (r) => REASON_AR[r] || r || "—";

// ──────────────────────────────────────────────────────────────────────
// Main component
// ──────────────────────────────────────────────────────────────────────
export default function AdsV2Settings() {
  const [snapshot, setSnapshot] = useState(null);
  const [discovery, setDiscovery] = useState(null);
  const [loading, setLoading] = useState(true);
  const [discoverLoading, setDiscoverLoading] = useState(false);

  const token = () => localStorage.getItem("token");
  const authHeaders = () => ({
    Authorization: `Bearer ${token()}`,
  });

  const loadSnapshot = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/ads-v2/settings`, {
        headers: authHeaders(),
      });
      setSnapshot(r.data?.data || null);
    } catch (e) {
      toast.error("فشل تحميل الإعدادات");
    } finally {
      setLoading(false);
    }
  }, []);

  const runDiscovery = async () => {
    setDiscoverLoading(true);
    try {
      const r = await axios.post(
        `${API}/ads-v2/settings/accounts/discover`,
        {},
        { headers: authHeaders() },
      );
      setDiscovery(r.data?.data || null);
      toast.success("تم اكتشاف الحسابات");
    } catch (e) {
      toast.error("فشل الاكتشاف");
    } finally {
      setDiscoverLoading(false);
    }
  };

  const linkAccount = async (acct, providerBlock) => {
    try {
      await axios.post(
        `${API}/ads-v2/settings/accounts`,
        { ...acct, v1_token_ref: providerBlock.v1_token_ref },
        { headers: authHeaders() },
      );
      toast.success(`تم ربط ${acct.display_name}`);
      await runDiscovery();
      await loadSnapshot();
    } catch (e) {
      toast.error("فشل الربط");
    }
  };

  const patchAccount = async (id, patch) => {
    try {
      const r = await axios.patch(
        `${API}/ads-v2/settings/accounts/${id}`,
        patch,
        { headers: authHeaders() },
      );
      if (r.data?.data?.updated) {
        toast.success("تم الحفظ");
        await loadSnapshot();
      }
    } catch (e) {
      toast.error("فشل الحفظ");
    }
  };

  const deleteAccount = async (id) => {
    if (!window.confirm("هل تريد إيقاف هذا الحساب؟ (Soft delete)"))
      return;
    try {
      await axios.delete(
        `${API}/ads-v2/settings/accounts/${id}`,
        { headers: authHeaders() },
      );
      toast.success("تم الإيقاف");
      await loadSnapshot();
    } catch (e) {
      toast.error("فشل الإيقاف");
    }
  };

  useEffect(() => {
    loadSnapshot();
    runDiscovery();
  }, [loadSnapshot]);

  if (loading && !snapshot) {
    return (
      <div className="p-8 text-zinc-400">
        جاري التحميل...
      </div>
    );
  }

  const accounts = snapshot?.accounts || [];
  const activity = snapshot?.recent_activity || [];

  return (
    <div className="p-6 max-w-7xl mx-auto" dir="rtl"
        data-testid="ads-v2-settings-page">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold text-zinc-100">
              إعدادات الإعلانات
            </h1>
            <Badge className="bg-blue-500/20 text-blue-100 border-blue-500/40 font-semibold">
              الإصدار V2 · المرحلة 0
            </Badge>
          </div>
          <p className="text-sm text-zinc-300 mt-1 font-medium">
            كل إعدادات منصات الإعلانات في مكان واحد. لا تأثير على V1.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={loadSnapshot}
            data-testid="reload-snapshot-btn"
          >
            تحديث
          </Button>
          <Button
            onClick={runDiscovery}
            disabled={discoverLoading}
            data-testid="discover-btn"
          >
            {discoverLoading ? "جاري الفحص..." : "اكتشاف الحسابات"}
          </Button>
        </div>
      </div>

      {/* Phase-0 invariant banner */}
      <div className="mb-6 p-3 rounded-lg border border-amber-500/40 bg-amber-500/10 text-amber-100 text-sm font-medium">
        <span className="font-bold">تنبيه المرحلة 0:</span>{" "}
        هذه الصفحة قراءة وتجهيز فقط. لا يتم إنشاء قيود محاسبية، ولا
        تعديل على V1، ولا حذف لأي توكن.
      </div>

      <Tabs defaultValue="accounts" className="w-full">
        <TabsList className="grid grid-cols-4 w-full bg-zinc-900 mb-6">
          <TabsTrigger value="accounts" data-testid="tab-accounts">
            الحسابات والربط
          </TabsTrigger>
          <TabsTrigger value="currency" data-testid="tab-currency">
            العملة وسعر الصرف
          </TabsTrigger>
          <TabsTrigger value="bank-fees" data-testid="tab-bank-fees">
            العمولات البنكية
          </TabsTrigger>
          <TabsTrigger value="review" data-testid="tab-review">
            إعدادات المراجعة
          </TabsTrigger>
        </TabsList>

        {/* ─── TAB 1: Accounts & Linking ─── */}
        <TabsContent value="accounts" className="space-y-6">
          <AccountsTab
            accounts={accounts}
            discovery={discovery}
            onLink={linkAccount}
            onPatch={patchAccount}
            onDelete={deleteAccount}
          />
        </TabsContent>

        {/* ─── TAB 2: Currency & FX ─── */}
        <TabsContent value="currency">
          <CurrencyTab
            accounts={accounts}
            onPatch={patchAccount}
          />
        </TabsContent>

        {/* ─── TAB 3: Bank Fees ─── */}
        <TabsContent value="bank-fees">
          <BankFeesTab
            accounts={accounts}
            onPatch={patchAccount}
          />
        </TabsContent>

        {/* ─── TAB 4: Review Settings ─── */}
        <TabsContent value="review">
          <ReviewSettingsTab
            accounts={accounts}
            onPatch={patchAccount}
          />
        </TabsContent>
      </Tabs>

      {/* Recent activity */}
      <Card className="mt-6 bg-zinc-900 border-zinc-800">
        <CardHeader>
          <CardTitle className="text-zinc-50 text-base font-bold">
            آخر الأحداث
          </CardTitle>
        </CardHeader>
        <CardContent>
          {activity.length === 0 ? (
            <p className="text-zinc-300 text-sm font-medium">لا توجد أحداث بعد.</p>
          ) : (
            <div className="space-y-2" data-testid="recent-activity">
              {activity.map((e) => (
                <ActivityRow key={e.id} ev={e} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Activity-row — summarises the event in Arabic instead of raw JSON
// ──────────────────────────────────────────────────────────────────────
function ActivityRow({ ev }) {
  const det = ev.details || {};
  let summary = "";
  if (ev.event === "sync_run") {
    const s = Number(det.spend_native || 0).toFixed(2);
    const cur = det.currency_native || "SAR";
    const sar = Number(det.spend_sar || 0).toFixed(2);
    summary = `${ev.date || ""} · صرف ${s} ${cur} (${sar} SAR)`;
  } else if (ev.event === "sync_failed") {
    const code = (det.status && det.status.code) || "غير معروف";
    summary = `${ev.date || ""} · سبب الفشل: ${reasonAr(code)}`;
  } else if (ev.event === "reconciliation_checked") {
    const ms = det.match_status || "—";
    const diff = det.diff_sar;
    summary = `${ev.date || ""} · النتيجة: ${ms}${
      diff != null ? ` · فرق ${Number(diff).toFixed(2)} SAR` : ""
    }`;
  } else if (ev.event === "account_created") {
    summary = `${PROVIDER_LABEL[det.provider] || det.provider} · ${det.display_name || det.external_account_id || ""}`;
  } else if (ev.event === "fx_changed" || ev.event === "bank_fee_changed") {
    summary = `حقول معدّلة: ${(det.fields_changed || []).join("، ")}`;
  } else {
    summary = ev.note || "";
  }
  return (
    <div className="flex items-start justify-between text-sm border-b border-zinc-800 pb-2 gap-3">
      <div className="flex items-start gap-2 min-w-0">
        <Badge className="bg-zinc-800 text-zinc-100 border-zinc-700 font-semibold whitespace-nowrap">
          {eventAr(ev.event)}
        </Badge>
        <span className="text-zinc-200 font-medium truncate">{summary}</span>
      </div>
      <span className="text-zinc-400 text-xs whitespace-nowrap font-medium">
        {new Date(ev.at).toLocaleString("ar-SA")}
      </span>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Diagnose button + dialog (POST /settings/accounts/{id}/diagnose)
// ──────────────────────────────────────────────────────────────────────
function DiagnoseButton({ accountId }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState(null);

  const authHeaders = () => ({
    Authorization: `Bearer ${localStorage.getItem("token")}`,
  });

  const run = async () => {
    setLoading(true);
    try {
      const r = await axios.post(
        `${API}/ads-v2/settings/accounts/${accountId}/diagnose`,
        {},
        { headers: authHeaders() },
      );
      setReport(r.data?.data || null);
    } catch (e) {
      toast.error("فشل التشخيص");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (v) run();
      }}
    >
      <DialogTrigger asChild>
        <Button
          size="sm"
          variant="outline"
          className="border-zinc-700 text-zinc-100 hover:bg-zinc-800 hover:text-zinc-50 font-semibold text-xs"
          data-testid={`diagnose-btn-${accountId}`}
        >
          تشخيص
        </Button>
      </DialogTrigger>
      <DialogContent className="bg-zinc-900 border-zinc-800 text-zinc-50 max-w-3xl max-h-[85vh] overflow-y-auto" dir="rtl">
        <DialogHeader>
          <DialogTitle className="text-zinc-50 font-bold">
            تقرير تشخيصي للحساب
          </DialogTitle>
        </DialogHeader>
        {loading || !report ? (
          <p className="text-zinc-300 text-sm font-medium py-6 text-center">
            جاري الفحص الحي مع المنصة...
          </p>
        ) : (
          <DiagnosticReport report={report} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function DiagnosticReport({ report }) {
  const s = report.status || {};
  const tk = TOKEN_STATUS_AR[s.token] || TOKEN_STATUS_AR.missing;
  const cn = CONNECTION_STATUS_AR[s.connection] || CONNECTION_STATUS_AR.unknown;
  const sr = SYNC_RUN_STATUS_AR[s.sync_run] || SYNC_RUN_STATUS_AR.disabled;
  const stats = report.stats || {};
  const probe = report.api_probe || {};
  const apiCode = (probe.status && probe.status.code) || "—";

  return (
    <div className="space-y-4 py-2">
      {/* Identity */}
      <div className="bg-zinc-950 rounded p-3 border border-zinc-800">
        <p className="text-sm">
          <span className="text-zinc-300 font-semibold">المنصة:</span>{" "}
          <span className="text-zinc-50 font-bold">
            {PROVIDER_LABEL[report.provider] || report.provider}
          </span>
          {" · "}
          <span className="text-zinc-300 font-semibold">الحساب:</span>{" "}
          <span className="text-zinc-50 font-bold">{report.display_name}</span>
          {" · "}
          <span className="text-zinc-400 font-mono text-xs">
            {report.external_account_id}
          </span>
        </p>
      </div>

      {/* 3-tier status */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <DiagBlock title="حالة التوكن" badge={tk} />
        <DiagBlock title="حالة الاتصال" badge={cn}
            sub={s.connection_reason ? reasonAr(s.connection_reason) : null} />
        <DiagBlock title="حالة المزامنة" badge={sr} />
      </div>

      {/* Primary reason callout */}
      <div className="bg-zinc-950 rounded p-3 border border-amber-500/30">
        <p className="text-sm">
          <span className="text-amber-200 font-bold">السبب الحقيقي: </span>
          <span className="text-zinc-50 font-semibold">{reasonAr(s.reason)}</span>
        </p>
      </div>

      {/* Stats */}
      <Card className="bg-zinc-950 border-zinc-800">
        <CardHeader className="pb-2">
          <CardTitle className="text-zinc-100 text-sm font-bold">
            إحصاءات
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <DiagStat label="عدد الأيام المخزّنة (٣٠ يوم)"
                value={stats.days_in_last_30d ?? 0} />
            <DiagStat label="أيام بها صرف"
                value={stats.days_with_spend ?? 0} />
            <DiagStat label="إجمالي السجلات"
                value={stats.total_daily_rows ?? 0} />
            <DiagStat label="آخر تاريخ تمت مزامنته"
                value={stats.last_synced_date || "—"} />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3 text-sm">
            <DiagStat label="بدأت آخر مزامنة"
                value={fmtDateTime(stats.last_sync_started_at)} />
            <DiagStat label="انتهت آخر مزامنة"
                value={fmtDateTime(stats.last_sync_finished_at)} />
          </div>
          {stats.last_sync_error && (
            <p className="text-xs text-red-200 mt-3 font-semibold bg-red-500/10 p-2 rounded border border-red-500/30">
              آخر رسالة خطأ: {reasonAr(stats.last_sync_error) || stats.last_sync_error}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Live API probe */}
      <Card className="bg-zinc-950 border-zinc-800">
        <CardHeader className="pb-2">
          <CardTitle className="text-zinc-100 text-sm font-bold">
            فحص حي مع API المنصة
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <DiagStat label="نتيجة الفحص"
                value={probe.ok ? "نجح ✓" : "فشل ✗"} />
            <DiagStat label="رمز الـ API"
                value={apiCode === "ok" ? "ok" : reasonAr(apiCode)} />
            <DiagStat label="تاريخ الفحص (Yesterday)"
                value={probe.date_tested || "—"} />
            <DiagStat label="وقت الفحص"
                value={fmtDateTime(probe.called_at)} />
            {probe.fetched_spend_native !== undefined && (
              <DiagStat label="صرف اليوم المُختبَر"
                  value={`${Number(probe.fetched_spend_native).toFixed(2)} ${probe.currency_native || ""}`} />
            )}
          </div>
          {probe.status?.body && (
            <pre className="mt-3 text-[10px] text-zinc-300 bg-zinc-900 p-2 rounded border border-zinc-800 overflow-x-auto font-mono leading-relaxed">
              {typeof probe.status.body === "string"
                ? probe.status.body
                : JSON.stringify(probe.status.body, null, 2)}
            </pre>
          )}
        </CardContent>
      </Card>

      {/* Last events */}
      <Card className="bg-zinc-950 border-zinc-800">
        <CardHeader className="pb-2">
          <CardTitle className="text-zinc-100 text-sm font-bold">
            آخر ١٠ أحداث
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {(report.last_events || []).length === 0 ? (
            <p className="text-zinc-400 text-sm">لا توجد أحداث بعد.</p>
          ) : (
            (report.last_events || []).map((ev) => (
              <ActivityRow key={ev.id} ev={ev} />
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function DiagBlock({ title, badge, sub }) {
  return (
    <div className="bg-zinc-950 rounded p-3 border border-zinc-800">
      <p className="text-xs text-zinc-300 font-semibold mb-2">{title}</p>
      <Badge className={`${badge.cls} font-semibold whitespace-nowrap`}>
        {badge.label}
      </Badge>
      {sub && (
        <p className="text-xs text-zinc-400 mt-2 font-medium leading-tight">
          {sub}
        </p>
      )}
    </div>
  );
}

function DiagStat({ label, value }) {
  return (
    <div>
      <p className="text-xs text-zinc-400 font-semibold">{label}</p>
      <p className="text-zinc-50 font-bold tabular-nums">{value}</p>
    </div>
  );
}

function fmtDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("ar-SA");
  } catch {
    return iso;
  }
}

// ──────────────────────────────────────────────────────────────────────
// Tab 1: Accounts
// ──────────────────────────────────────────────────────────────────────
function AccountsTab({ accounts, discovery, onLink, onPatch, onDelete }) {
  return (
    <div className="space-y-6">
      {/* Linked accounts */}
      <Card className="bg-zinc-900 border-zinc-800">
        <CardHeader>
          <CardTitle className="text-zinc-100 text-base flex items-center justify-between">
            <span>الحسابات المربوطة بـ V2</span>
            <Badge className="bg-zinc-800 text-zinc-300 border-zinc-700">
              {accounts.length}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {accounts.length === 0 ? (
            <p className="text-zinc-500 text-sm">
              لم يتم ربط أي حساب بعد. اضغط <strong>اكتشاف الحسابات</strong>
              {" "}ثم اختر الحسابات التي تريد ربطها.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="border-zinc-800">
                  <TableHead className="text-zinc-200 font-semibold">المنصة</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">الحساب</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">حالة التوكن</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">حالة الاتصال</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">حالة المزامنة</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">السبب الحقيقي</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">تفعيل المزامنة</TableHead>
                  <TableHead className="text-zinc-200 font-semibold text-end">إجراءات</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accounts.map((a) => {
                  const s = a._status || {};
                  const tk = TOKEN_STATUS_AR[s.token] || TOKEN_STATUS_AR.missing;
                  const cn = CONNECTION_STATUS_AR[s.connection] || CONNECTION_STATUS_AR.unknown;
                  const sr = SYNC_RUN_STATUS_AR[s.sync_run] || SYNC_RUN_STATUS_AR.disabled;
                  return (
                    <TableRow
                      key={a.id}
                      className="border-zinc-800"
                      data-testid={`account-row-${a.id}`}
                    >
                      <TableCell className="text-zinc-50 font-semibold">
                        {PROVIDER_LABEL[a.provider] || a.provider}
                      </TableCell>
                      <TableCell className="text-zinc-100">
                        <div className="font-semibold">{a.display_name}</div>
                        <div className="text-xs text-zinc-400 font-mono">
                          {a.external_account_id}
                        </div>
                        {a.organization_name && (
                          <div className="text-xs text-zinc-400">
                            {a.organization_name}
                          </div>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge className={`${tk.cls} font-semibold whitespace-nowrap`}
                            data-testid={`token-status-${a.id}`}>
                          {tk.label}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge className={`${cn.cls} font-semibold whitespace-nowrap`}
                            data-testid={`conn-status-${a.id}`}>
                          {cn.label}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge className={`${sr.cls} font-semibold whitespace-nowrap`}
                            data-testid={`sync-run-status-${a.id}`}>
                          {sr.label}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-zinc-100 text-xs font-medium max-w-[220px]">
                        {reasonAr(s.reason)}
                      </TableCell>
                      <TableCell>
                        <Switch
                          checked={!!a.sync_enabled}
                          onCheckedChange={(v) =>
                            onPatch(a.id, { sync_enabled: v })
                          }
                          data-testid={`sync-toggle-${a.id}`}
                        />
                      </TableCell>
                      <TableCell className="text-end">
                        <div className="flex items-center justify-end gap-2">
                          <DiagnoseButton accountId={a.id} />
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-red-300 hover:text-red-200"
                            onClick={() => onDelete(a.id)}
                            data-testid={`delete-btn-${a.id}`}
                          >
                            إيقاف
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Discovered accounts (not yet linked) */}
      {discovery && (
        <Card className="bg-zinc-900 border-zinc-800">
          <CardHeader>
            <CardTitle className="text-zinc-100 text-base">
              الحسابات المُكتشفة من V1 (متاحة للربط)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {Object.entries(discovery).map(([provider, block]) => (
              <ProviderDiscoveryBlock
                key={provider}
                provider={provider}
                block={block}
                onLink={onLink}
              />
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function ProviderDiscoveryBlock({ provider, block, onLink }) {
  const accounts = block.accounts || [];
  const status = block.connection_status;
  return (
    <div className="border border-zinc-800 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-zinc-50 font-bold text-base">
            {PROVIDER_LABEL[provider] || provider}
          </h3>
          <Badge className={`${tone(status)} font-semibold`}>
            {statusAr(status)}
          </Badge>
        </div>
        <span className="text-xs text-zinc-300 font-semibold">
          {accounts.length} حساب متاح
        </span>
      </div>
      {block.error && (
        <div className="text-xs text-red-200 mb-2 font-semibold">
          خطأ: {statusAr(block.error.reason) || "غير معروف"}
          {block.error.body && (
            <div className="text-zinc-400 mt-1 font-mono">
              {String(block.error.body).slice(0, 200)}
            </div>
          )}
        </div>
      )}
      {accounts.length === 0 ? (
        <p className="text-xs text-zinc-300 font-medium">
          {status === "missing"
            ? "لا يوجد توكن مربوط في V1. لن نطلب صلاحيات جديدة — أبلغني إذا تريد ربطه."
            : "لا توجد حسابات متاحة من هذه المنصة."}
        </p>
      ) : (
        <div className="space-y-2">
          {accounts.map((a) => (
            <div
              key={`${a.provider}-${a.external_account_id}`}
              className="flex items-center justify-between bg-zinc-950 rounded p-3"
              data-testid={`discovered-${a.provider}-${a.external_account_id}`}
            >
              <div>
                <div className="text-zinc-50 text-sm font-bold">
                  {a.display_name}
                </div>
                <div className="text-xs text-zinc-400 font-mono">
                  {a.external_account_id}
                  {a.organization_name && ` · ${a.organization_name}`}
                  {a._from_cache && (
                    <span className="ms-2 text-amber-300 font-semibold">
                      (من ذاكرة V1 — قد لا يدعم التوكن هذه المنظمة)
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Badge className="bg-zinc-800 text-zinc-100 border-zinc-700 font-semibold">
                  {a.currency_native}
                </Badge>
                {a._linked ? (
                  <Badge className={`${tone("active")} font-semibold`}>مربوط</Badge>
                ) : (
                  <Button
                    size="sm"
                    className="font-semibold"
                    onClick={() => onLink(a, block)}
                    data-testid={`link-btn-${a.external_account_id}`}
                  >
                    ربط بـ V2
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Tab 2: Currency & FX
// ──────────────────────────────────────────────────────────────────────
function CurrencyTab({ accounts, onPatch }) {
  if (accounts.length === 0) {
    return (
      <Card className="bg-zinc-900 border-zinc-800">
        <CardContent className="py-8">
          <p className="text-zinc-500 text-sm">
            اربط حساباً أولاً من تبويب «الحسابات والربط».
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      <p className="text-xs text-zinc-300 font-medium">
        لكل حساب سعر صرف خاص (إلى SAR). لا يوجد سعر افتراضي ثابت — إذا كانت
        عملة الحساب SAR فإن السعر = 1.0.
      </p>
      {accounts.map((a) => (
        <FxRow key={a.id} a={a} onPatch={onPatch} />
      ))}
    </div>
  );
}

function FxRow({ a, onPatch }) {
  const [rate, setRate] = useState(a.fx_to_sar?.rate ?? 1.0);
  const [from, setFrom] = useState(
    a.fx_to_sar?.effective_from || "2026-01-01");
  const [note, setNote] = useState(a.fx_to_sar?.source_note || "");

  const save = () => {
    onPatch(a.id, {
      fx_to_sar: {
        mode: "manual",
        rate: parseFloat(rate),
        effective_from: from,
        source_note: note,
      },
    });
  };

  return (
    <Card className="bg-zinc-900 border-zinc-800">
      <CardContent className="py-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-zinc-100 font-medium">{a.display_name}</div>
            <div className="text-xs text-zinc-500">
              {PROVIDER_LABEL[a.provider]} · العملة الأصلية: {a.currency_native}
            </div>
          </div>
          {a.currency_native === "SAR" && (
            <Badge className="bg-emerald-500/15 text-emerald-300 border-emerald-500/30">
              SAR (لا يحتاج تحويل)
            </Badge>
          )}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div>
            <FLabel className="text-zinc-400 text-xs">
              سعر الصرف ({a.currency_native} → SAR)
            </FLabel>
            <FInput
              type="number"
              step="0.0001"
              value={rate}
              onChange={(e) => setRate(e.target.value)}
              data-testid={`fx-rate-${a.id}`}
            />
          </div>
          <div>
            <FLabel className="text-zinc-400 text-xs">سارٍ من</FLabel>
            <FInput
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              data-testid={`fx-from-${a.id}`}
            />
          </div>
          <div className="md:col-span-2">
            <FLabel className="text-zinc-400 text-xs">المصدر/ملاحظة</FLabel>
            <FInput
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="مثال: SAMA reference June 2026"
              data-testid={`fx-note-${a.id}`}
            />
          </div>
        </div>
        <div className="mt-3 flex justify-end">
          <Button
            size="sm"
            onClick={save}
            data-testid={`fx-save-${a.id}`}
          >
            حفظ
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Tab 3: Bank Fees
// ──────────────────────────────────────────────────────────────────────
function BankFeesTab({ accounts, onPatch }) {
  if (accounts.length === 0) {
    return (
      <Card className="bg-zinc-900 border-zinc-800">
        <CardContent className="py-8">
          <p className="text-zinc-500 text-sm">
            اربط حساباً أولاً.
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      <p className="text-xs text-zinc-300 font-medium">
        العمولة البنكية تُضاف كقيد منفصل في دفتر الأستاذ (بعد اعتماد المراجعة). تدعم:
        نسبة مئوية، أو مبلغ ثابت، أو الاثنين معاً.
      </p>
      {accounts.map((a) => (
        <BankFeeRow key={a.id} a={a} onPatch={onPatch} />
      ))}
    </div>
  );
}

function BankFeeRow({ a, onPatch }) {
  const bf = a.bank_fee || {};
  const [enabled, setEnabled] = useState(!!bf.enabled);
  const [method, setMethod] = useState(bf.method || "none");
  const [pct, setPct] = useState((bf.rate_pct ?? 0) * 100);  // displayed as %
  const [flat, setFlat] = useState(bf.flat_amount_sar ?? 0);
  const [note, setNote] = useState(bf.note || "");

  const save = () => {
    onPatch(a.id, {
      bank_fee: {
        enabled,
        method,
        rate_pct: parseFloat(pct) / 100,
        flat_amount_sar: parseFloat(flat),
        note,
      },
    });
  };

  return (
    <Card className="bg-zinc-900 border-zinc-800">
      <CardContent className="py-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-zinc-100 font-medium">{a.display_name}</div>
            <div className="text-xs text-zinc-500">
              {PROVIDER_LABEL[a.provider]}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <FLabel className="text-zinc-400 text-xs">مفعَّلة</FLabel>
            <Switch
              checked={enabled}
              onCheckedChange={setEnabled}
              data-testid={`bank-fee-enabled-${a.id}`}
            />
          </div>
        </div>
        {enabled && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
              <div>
                <FLabel className="text-zinc-400 text-xs">الطريقة</FLabel>
                <Select value={method} onValueChange={setMethod}>
                  <FSelectTrigger data-testid={`bank-fee-method-${a.id}`}>
                    <SelectValue />
                  </FSelectTrigger>
                  <FSelectContent>
                    <FSelectItem value="none">لا شيء</FSelectItem>
                    <FSelectItem value="pct">نسبة %</FSelectItem>
                    <FSelectItem value="flat">مبلغ ثابت</FSelectItem>
                    <FSelectItem value="pct_plus_flat">
                      نسبة + مبلغ ثابت
                    </FSelectItem>
                  </FSelectContent>
                </Select>
              </div>
              {(method === "pct" || method === "pct_plus_flat") && (
                <div>
                  <FLabel className="text-zinc-400 text-xs">النسبة %</FLabel>
                  <FInput
                    type="number"
                    step="0.01"
                    value={pct}
                    onChange={(e) => setPct(e.target.value)}
                    placeholder="2.85"
                    data-testid={`bank-fee-pct-${a.id}`}
                  />
                </div>
              )}
              {(method === "flat" || method === "pct_plus_flat") && (
                <div>
                  <FLabel className="text-zinc-400 text-xs">
                    مبلغ ثابت (SAR)
                  </FLabel>
                  <FInput
                    type="number"
                    step="0.01"
                    value={flat}
                    onChange={(e) => setFlat(e.target.value)}
                    placeholder="5.00"
                    data-testid={`bank-fee-flat-${a.id}`}
                  />
                </div>
              )}
              <div>
                <FLabel className="text-zinc-400 text-xs">ملاحظة</FLabel>
                <FInput
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="مثال: Visa cross-border"
                  data-testid={`bank-fee-note-${a.id}`}
                />
              </div>
            </div>
          </>
        )}
        <div className="mt-3 flex justify-end">
          <Button
            size="sm"
            onClick={save}
            data-testid={`bank-fee-save-${a.id}`}
          >
            حفظ
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Tab 4: Review Settings
// ──────────────────────────────────────────────────────────────────────
function ReviewSettingsTab({ accounts, onPatch }) {
  if (accounts.length === 0) {
    return (
      <Card className="bg-zinc-900 border-zinc-800">
        <CardContent className="py-8">
          <p className="text-zinc-500 text-sm">
            اربط حساباً أولاً.
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      <p className="text-xs text-zinc-300 font-medium">
        إعدادات المراجعة تتحكم بمتى يحتاج القيد اعتماداً يدوياً، ومتى يُحجب بسبب نسبة الفرق.
      </p>
      {accounts.map((a) => (
        <ReviewSettingsRow key={a.id} a={a} onPatch={onPatch} />
      ))}
    </div>
  );
}

function ReviewSettingsRow({ a, onPatch }) {
  const r = a.review_settings || {};
  const [autoApproveUnder, setAutoApproveUnder] = useState(
    r.auto_approve_under_sar ?? 0);
  const [warnPct, setWarnPct] = useState(r.drift_warning_threshold_pct ?? 5);
  const [blockPct, setBlockPct] = useState(r.drift_block_threshold_pct ?? 15);

  const save = () => {
    onPatch(a.id, {
      review_settings: {
        auto_approve_under_sar: parseFloat(autoApproveUnder),
        drift_warning_threshold_pct: parseFloat(warnPct),
        drift_block_threshold_pct: parseFloat(blockPct),
      },
    });
  };

  return (
    <Card className="bg-zinc-900 border-zinc-800">
      <CardContent className="py-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-zinc-100 font-medium">{a.display_name}</div>
            <div className="text-xs text-zinc-500">
              {PROVIDER_LABEL[a.provider]}
            </div>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <FLabel className="text-zinc-300 text-xs font-semibold">
              اعتماد تلقائي تحت قيمة (SAR)
            </FLabel>
            <FInput
              type="number"
              step="0.01"
              value={autoApproveUnder}
              onChange={(e) => setAutoApproveUnder(e.target.value)}
              data-testid={`auto-approve-${a.id}`}
            />
            <p className="text-xs text-zinc-400 mt-1 font-medium">
              0 = يتطلب اعتماد يدوي دائماً
            </p>
          </div>
          <div>
            <FLabel className="text-zinc-300 text-xs font-semibold">
              تنبيه عند نسبة فرق (%)
            </FLabel>
            <FInput
              type="number"
              step="0.1"
              value={warnPct}
              onChange={(e) => setWarnPct(e.target.value)}
              data-testid={`warn-pct-${a.id}`}
            />
          </div>
          <div>
            <FLabel className="text-zinc-300 text-xs font-semibold">
              حجب عند نسبة فرق (%)
            </FLabel>
            <FInput
              type="number"
              step="0.1"
              value={blockPct}
              onChange={(e) => setBlockPct(e.target.value)}
              data-testid={`block-pct-${a.id}`}
            />
          </div>
        </div>
        <div className="mt-3 flex justify-end">
          <Button
            size="sm"
            onClick={save}
            data-testid={`review-save-${a.id}`}
          >
            حفظ
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
