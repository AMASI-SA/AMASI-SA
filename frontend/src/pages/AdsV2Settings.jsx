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
          <CardTitle className="text-zinc-100 text-base">
            آخر الأحداث
          </CardTitle>
        </CardHeader>
        <CardContent>
          {activity.length === 0 ? (
            <p className="text-zinc-500 text-sm">لا توجد أحداث بعد.</p>
          ) : (
            <div className="space-y-2" data-testid="recent-activity">
              {activity.map((e) => (
                <div
                  key={e.id}
                  className="flex items-center justify-between text-sm border-b border-zinc-800 pb-2"
                >
                  <div className="flex items-center gap-2">
                    <Badge className="bg-zinc-800 text-zinc-300 border-zinc-700">
                      {e.event}
                    </Badge>
                    <span className="text-zinc-400">
                      {e.details && JSON.stringify(e.details).slice(0, 80)}
                    </span>
                  </div>
                  <span className="text-zinc-500 text-xs">
                    {new Date(e.at).toLocaleString("ar-SA")}
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
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
                  <TableHead className="text-zinc-200 font-semibold">المنظمة</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">العملة</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">الحالة</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">حالة التوكن</TableHead>
                  <TableHead className="text-zinc-200 font-semibold">المزامنة</TableHead>
                  <TableHead className="text-zinc-200 font-semibold text-end">
                    إجراء
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accounts.map((a) => (
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
                    </TableCell>
                    <TableCell className="text-zinc-300">
                      {a.organization_name || "—"}
                    </TableCell>
                    <TableCell className="text-zinc-100 font-semibold">
                      {a.currency_native}
                    </TableCell>
                    <TableCell>
                      <Badge className={`${tone(a.sync_status)} font-semibold`}>
                        {statusAr(a.sync_status)}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {a._v1_token_health?.ok ? (
                        <Badge className={`${tone("active")} font-semibold`}>سليم</Badge>
                      ) : (
                        <Badge className={`${tone("error")} font-semibold`}>
                          {statusAr(a._v1_token_health?.reason) || "غير متاح"}
                        </Badge>
                      )}
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
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-red-300 hover:text-red-200"
                        onClick={() => onDelete(a.id)}
                        data-testid={`delete-btn-${a.id}`}
                      >
                        إيقاف
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
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
