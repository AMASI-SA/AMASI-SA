import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ArrowClockwise,
  CheckCircle,
  Gear,
  ListChecks,
  Receipt,
  ShieldCheck,
  WarningCircle,
} from "@phosphor-icons/react";

import api from "../lib/api";
import QoyodReconciliation from "./QoyodReconciliation";
import QoyodInvoiceReview from "./QoyodInvoiceReview";
import QoyodSettings from "./QoyodSettings";
import QoyodUnsentOrders from "./QoyodUnsentOrders";

export const QOYOD_V2_TABS = [
  { id: "overview", label: "الحالة", Icon: ShieldCheck },
  { id: "exceptions", label: "الاستثناءات", Icon: WarningCircle },
  { id: "invoices", label: "فواتير قيود", Icon: Receipt },
  { id: "reconciliation", label: "المطابقة", Icon: ListChecks },
  { id: "settings", label: "الإعدادات", Icon: Gear },
];

const REASON_LABELS = {
  connector_disabled: "التكامل متوقف",
  operator_disabled: "الإرسال التلقائي متوقف",
  dry_run_enabled: "وضع المحاكاة مفعّل",
  circuit_breaker: "أوقفه حارس الأمان بعد خطأ",
  credentials_removed: "مفتاح قيود غير موجود",
};

function Card({ label, value, hint, tone = "slate", testid }) {
  const tones = {
    emerald: "border-emerald-200 bg-emerald-50 text-emerald-900",
    amber: "border-amber-200 bg-amber-50 text-amber-900",
    rose: "border-rose-200 bg-rose-50 text-rose-900",
    sky: "border-sky-200 bg-sky-50 text-sky-900",
    slate: "border-slate-200 bg-white text-slate-900",
  };
  return (
    <div className={`rounded-2xl border p-4 ${tones[tone]}`} data-testid={testid}>
      <div className="text-xs font-extrabold opacity-70">{label}</div>
      <div className="mt-2 text-2xl font-black" dir="ltr">{value}</div>
      <div className="mt-2 text-xs font-semibold opacity-70">{hint}</div>
    </div>
  );
}

function errorMessage(error) {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return detail?.message || error?.message || "تعذر تحميل حالة قيود";
}

export function QoyodOverview({ settings, unsent, loading, error, onRefresh, onOpenTab }) {
  const automatic = settings?.plan_b_auto_send_status || {};
  const worker = automatic.worker || {};
  const live = automatic.armed === true && worker.running === true;
  const lastError = automatic.last_error;
  const queue = unsent?.queue_counts || {};
  const legacyPending = Number(unsent?.counts?.["لم يُرسل"] || 0);
  const legacyFailed = Number(unsent?.counts?.["فشل"] || 0);
  const duplicate = Number(unsent?.counts?.["مكرر"] || 0);
  const ready = Number(queue.ready_to_send ?? legacyPending);
  const quarantined = Number(queue.quarantined ?? legacyFailed);
  const paymentVerification = Number(queue.needs_payment_verification || 0);
  const inQoyod = Number(
    queue.in_qoyod ?? unsent?.counts?.["أُرسل"] ?? 0,
  );
  const retryableSync = Number(queue.retryable_sync || 0);
  const credentials = settings?.credentials?.configured === true;

  return (
    <div className="space-y-5" data-testid="qoyod-v2-overview">
      <div className={`rounded-2xl border p-5 ${live
        ? "border-emerald-300 bg-emerald-50 text-emerald-950"
        : "border-rose-300 bg-rose-50 text-rose-950"}`}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            {live
              ? <CheckCircle size={34} weight="fill" className="text-emerald-600" />
              : <WarningCircle size={34} weight="fill" className="text-rose-600" />}
            <div>
              <h2 className="text-xl font-black">
                {live ? "الإرسال التلقائي إلى قيود يعمل" : "الإرسال التلقائي يحتاج مراجعة"}
              </h2>
              <p className="mt-1 text-sm font-semibold opacity-75">
                {live
                  ? "يعالج الطلبات المؤهلة من الأقدم إلى الأحدث، ويحفظ الفاتورة والسداد في ميزان فورًا."
                  : REASON_LABELS[automatic.disabled_reason] || "راجع الإعدادات وحالة العامل."}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl border border-current/20 bg-white px-4 py-2 text-sm font-extrabold disabled:opacity-50"
          >
            <ArrowClockwise size={18} className={loading ? "animate-spin" : ""} />
            تحديث الحالة
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-800">
          {error}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <Card
          label="مفتاح قيود"
          value={credentials ? "محفوظ" : "غير محفوظ"}
          hint="لا يتم عرض المفتاح داخل ميزان"
          tone={credentials ? "emerald" : "rose"}
          testid="qoyod-v2-credentials"
        />
        <Card
          label="العامل التلقائي"
          value={worker.running ? "يعمل" : "متوقف"}
          hint={worker.last_run_at ? `آخر دورة: ${String(worker.last_run_at).slice(0, 16).replace("T", " ")}` : "لم تُسجّل دورة بعد"}
          tone={worker.running ? "emerald" : "rose"}
          testid="qoyod-v2-worker"
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="qoyod-v2-queue-counts">
        <Card
          label="جاهز للإرسال"
          value={ready}
          hint={retryableSync
            ? `إعادة مزامنة مجدولة لعدد ${retryableSync}`
            : "طلبات مؤهلة وستُعالج من الأقدم أولًا"}
          tone={ready ? "amber" : "emerald"}
          testid="qoyod-v2-ready"
        />
        <Card
          label="محجور للمراجعة"
          value={quarantined}
          hint={`أخطاء محاسبية حقيقية · المكرر: ${duplicate}`}
          tone={quarantined ? "rose" : "emerald"}
          testid="qoyod-v2-quarantined"
        />
        <Card
          label="يحتاج تحقق دفع"
          value={paymentVerification}
          hint="لا يُرسل حتى يثبت سداد الطلب من سلة"
          tone={paymentVerification ? "amber" : "emerald"}
          testid="qoyod-v2-payment-verification"
        />
        <Card
          label="موجود في قيود"
          value={inQoyod}
          hint="مطابقة دقيقة لرقم الطلب في مرجع الفاتورة"
          tone="sky"
          testid="qoyod-v2-in-qoyod"
        />
      </div>

      {lastError && (
        <div className="rounded-2xl border border-rose-200 bg-white p-5">
          <div className="font-black text-rose-800">آخر خطأ أوقف الإرسال</div>
          <div className="mt-2 text-sm text-slate-700">
            {lastError.message || lastError.code || "خطأ غير معروف"}
          </div>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <button
          type="button"
          onClick={() => onOpenTab("exceptions")}
          className="rounded-2xl border border-amber-200 bg-amber-50 p-5 text-right hover:bg-amber-100"
        >
          <div className="font-black text-amber-950">مراجعة الاستثناءات</div>
          <div className="mt-2 text-sm text-amber-800">الأخطاء الحقيقية فقط؛ أخطاء المزامنة يعاد جدولتها تلقائيًا.</div>
        </button>
        <button
          type="button"
          onClick={() => onOpenTab("reconciliation")}
          className="rounded-2xl border border-sky-200 bg-sky-50 p-5 text-right hover:bg-sky-100"
        >
          <div className="font-black text-sky-950">مطابقة ميزان مع قيود</div>
          <div className="mt-2 text-sm text-sky-800">فحص محلي آمن للفواتير والمبالغ وعلامات الإرسال.</div>
        </button>
        <button
          type="button"
          onClick={() => onOpenTab("settings")}
          className="rounded-2xl border border-slate-200 bg-white p-5 text-right hover:bg-slate-50"
        >
          <div className="font-black text-slate-950">الإعدادات والحماية</div>
          <div className="mt-2 text-sm text-slate-600">مفتاح API، الحسابات، الضريبة، ووسائل الدفع.</div>
        </button>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
        <span className="font-black">سياسة التشغيل النهائية:</span>{" "}
        الإرسال تلقائي فقط. الفاتورة الموجودة في قيود تُصالح ولا تُعاد، وأي طلب غير آمن يبقى محجورًا بسبب واضح.
      </div>
    </div>
  );
}

export default function QoyodControlCenter() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab") || "overview";
  const activeTab = QOYOD_V2_TABS.some((tab) => tab.id === requestedTab)
    ? requestedTab
    : "overview";
  const [settings, setSettings] = useState(null);
  const [unsent, setUnsent] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError("");
    const [settingsResult, unsentResult] = await Promise.allSettled([
      api.get("/integrations/qoyod/settings"),
      api.get("/integrations/qoyod/unsent-orders", { params: { days: 30, limit: 1000 } }),
    ]);
    if (settingsResult.status === "fulfilled") setSettings(settingsResult.value.data);
    if (unsentResult.status === "fulfilled") setUnsent(unsentResult.value.data);
    const failed = [settingsResult, unsentResult].find((result) => result.status === "rejected");
    if (failed) setError(errorMessage(failed.reason));
    setLoading(false);
  }, []);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  const openTab = useCallback((tab) => {
    setSearchParams(tab === "overview" ? {} : { tab });
  }, [setSearchParams]);

  const content = useMemo(() => {
    if (activeTab === "exceptions") return <QoyodUnsentOrders />;
    if (activeTab === "invoices") return <QoyodInvoiceReview />;
    if (activeTab === "reconciliation") return <QoyodReconciliation />;
    if (activeTab === "settings") return <QoyodSettings />;
    return (
      <QoyodOverview
        settings={settings}
        unsent={unsent}
        loading={loading}
        error={error}
        onRefresh={loadOverview}
        onOpenTab={openTab}
      />
    );
  }, [activeTab, settings, unsent, loading, error, loadOverview, openTab]);

  return (
    <div className="space-y-5" dir="rtl" data-testid="qoyod-v2-control-center">
      <header className="rounded-2xl border border-slate-900 bg-slate-950 p-5 text-white sm:p-7">
        <div className="text-xs font-black text-emerald-300">MEZAN OS V2</div>
        <h1 className="mt-2 text-2xl font-black sm:text-3xl">قيود — مركز التشغيل التلقائي</h1>
        <p className="mt-2 max-w-3xl text-sm font-semibold text-slate-300">
          صفحة واحدة لحالة الربط، فواتير قيود، الاستثناءات، المطابقة، والإعدادات. أُزيلت صفحات التجارب والانتقال والتشخيص القديمة.
        </p>
      </header>

      <nav className="flex gap-2 overflow-x-auto rounded-2xl border border-slate-200 bg-white p-2" aria-label="أقسام مركز قيود">
        {QOYOD_V2_TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => openTab(id)}
            className={`inline-flex shrink-0 items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-extrabold ${activeTab === id
              ? "bg-slate-950 text-white"
              : "text-slate-600 hover:bg-slate-100"}`}
            data-testid={`qoyod-v2-tab-${id}`}
          >
            <Icon size={18} weight={activeTab === id ? "fill" : "regular"} />
            {label}
          </button>
        ))}
      </nav>

      {content}
    </div>
  );
}
