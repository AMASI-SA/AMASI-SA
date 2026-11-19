import { useEffect, useRef, useState } from "react";
import { Bank, CheckCircle, Image, XCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../lib/api";

function money(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "SAR", maximumFractionDigits: 2 }).format(Number(value || 0));
}

function cents(value) {
  const text = String(value ?? "");
  if (!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  const amount = BigInt(whole) * BigInt(100) + BigInt(fraction.padEnd(2, "0"));
  return amount > BigInt(0) ? amount : null;
}

function isPosDestination(fact) {
  return fact?.status === "active" && fact.currency === "SAR" &&
    fact.source === "documented_opening_fact" && fact.category === "other_receivable" &&
    fact.entity_type === "asset" && fact.sub_account === "other_receivable" &&
    typeof fact.id === "string" && fact.id.length > 0 && fact.id === fact.entity_id &&
    typeof fact.display_name === "string" && fact.display_name.trim().length > 0;
}

const STATEMENT_SOURCE = "operational_driver_bank_statement";
const CONTEXT_CHANGED = new Set([
  "bank_statement_context_mismatch", "operational_driver_review_phase_changed",
  "operational_driver_bank_context_changed", "driver_payment_bank_context_changed",
  "operational_driver_bank_pending_review_required", "accounting_permission_required",
]);
const BANK_ERRORS = {
  bank_statement_context_mismatch: "تغيّرت بيانات المراجعة أو البنك؛ أعد تحميل خيارات الاعتماد.",
  operational_driver_review_phase_changed: "تغيّر مسار المراجعة؛ أعد تحميل خيارات الاعتماد.",
  operational_driver_bank_context_changed: "تغيّرت جلسة البنك؛ أعد تحميل خيارات الاعتماد.",
  driver_payment_bank_context_changed: "تغيّر البنك المرتبط بالتحويل؛ أعد تحميل خيارات الاعتماد.",
  operational_driver_bank_pending_review_required: "تغيّرت حالة التحويل؛ أعد تحميل خيارات الاعتماد.",
  operational_driver_bank_file_context_conflict: "هذا الكشف محفوظ لبنك أو فترة مختلفة. تحقق من البنك والكشف المختارين.",
  operational_driver_bank_import_idempotency_conflict: "تفاصيل الرفع تختلف عن المحاولة المحفوظة. أعد تحميل خيارات الاعتماد.",
  operational_driver_bank_existing_native_statement: "الكشف محفوظ مسبقًا. حدّث خيارات الاعتماد واختر حركته من القائمة.",
  operational_driver_bank_reference_conflict: "يوجد مرجع بنكي محفوظ ببيانات مختلفة؛ راجع الصف المتعارض في الكشف.",
  operational_driver_bank_rows_invalid: "توجد صفوف غير صالحة في الكشف؛ راجع التواريخ والمبالغ والمراجع.",
  operational_driver_bank_currency_invalid: "يلزم كشف بنك بالريال السعودي.",
  accounting_permission_required: "ليس لديك صلاحية رفع كشوف البنك؛ أعد تحميل خيارات الاعتماد للتحقق من الصلاحيات.",
};

function bankErrorMessage(error, fallback) {
  const detail = error?.response?.data?.detail;
  const message = typeof detail?.message === "string" ? detail.message.trim() : "";
  const mapped = BANK_ERRORS[detail?.code || error?.code];
  if (message && message !== "تعذر تنفيذ العملية") return message;
  if (detail?.code === "operational_driver_bank_rows_invalid" && Array.isArray(detail.errors)) {
    const rowErrors = {
      movement_date_required: "التاريخ مفقود", movement_date_invalid: "التاريخ غير صالح",
      movement_amount_invalid: "المبلغ غير صالح", movement_amount_precision: "المبلغ يتجاوز منزلتين عشريتين",
      movement_direction_invalid: "اتجاه الحركة غير صالح", movement_credit_debit_conflict: "مبالغ الوارد والصادر متعارضة",
      movement_description_or_reference_required: "يلزم وصف أو مرجع للحركة",
    };
    return [mapped, ...detail.errors.slice(0, 3).map(row =>
      `الصف ${row.row_no}: ${rowErrors[row.code] || "بيانات الحركة غير صالحة"}`)].join(" · ");
  }
  return mapped || message || fallback;
}

function sameReviewScope(first, second) {
  return first?.financial_source === second?.financial_source && first?.session_scope === second?.session_scope &&
    first?.started_at === second?.started_at;
}

function statementContextMatches(context, review) {
  return context?.source === STATEMENT_SOURCE && context.bank_source === "operational_bank_catalog" &&
    context.financial_source === "operational" && review?.financial_source === "operational" &&
    typeof context.started_at === "string" && context.started_at.length > 0 &&
    /^[a-f0-9]{64}$/.test(context.session_scope || "") && sameReviewScope(context, review) &&
    Array.isArray(context.items) && context.accepted_file_type === ".xlsx" &&
    Number.isSafeInteger(context.max_file_bytes) && context.max_file_bytes > 0;
}

function statementFileError(file, context) {
  if (!file) return "اختر ملف كشف البنك أولًا.";
  if (!context) return BANK_ERRORS.bank_statement_context_mismatch;
  if (!file.name.toLowerCase().endsWith(".xlsx")) return "اختر ملف Excel بصيغة .xlsx فقط.";
  if (!file.size) return "ملف كشف البنك فارغ.";
  if (file.size > context.max_file_bytes) return "حجم كشف البنك يتجاوز الحد المسموح.";
  return "";
}

function requestId() {
  return `bank-statement-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
}

export default function StoreDeliveryPaymentReview() {
  const [items, setItems] = useState([]);
  const [busyId, setBusyId] = useState("");
  const [filter, setFilter] = useState("");
  const [banks, setBanks] = useState([]);
  const [bankSource, setBankSource] = useState("");
  const [destinations, setDestinations] = useState({});
  const [bankError, setBankError] = useState("");
  const [posFacts, setPosFacts] = useState([]);
  const [posError, setPosError] = useState("");
  const [reviewOptions, setReviewOptions] = useState({});
  const [optionErrors, setOptionErrors] = useState({});
  const [optionLoading, setOptionLoading] = useState({});
  const [statementContexts, setStatementContexts] = useState({});
  const [uploads, setUploads] = useState({});
  const [uncertain, setUncertain] = useState({});
  const inFlight = useRef(false);
  const loadVersion = useRef(0);
  const optionVersions = useRef({});
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; loadVersion.current += 1; }; }, []);
  const optionsFor = item => reviewOptions[item.assignment_id];
  const posFor = item => optionsFor(item)?.financial_source === "operational" ? optionsFor(item).pos_receivables || [] : posFacts;
  const banksFor = item => optionsFor(item)?.financial_source === "operational"
    ? (statementContexts[item.assignment_id]?.items || []).filter(bank => bank.account_type === "bank" && bank.currency === "SAR" && bank.status === "active")
    : bankSource === "native" ? banks : [];
  const movementsFor = item => (optionsFor(item)?.bank_movements || []).filter(row =>
    typeof row.id === "string" && row.id.length > 0 && row.bank_account_id === optionsFor(item).bank_account_id &&
    row.direction === "in" && row.currency === "SAR" && row.status === "unclassified" &&
    ["operational_driver_bank_movements_v1", "mz2_daily_movements"].includes(row.source_namespace) &&
    cents(row.amount) !== null && cents(row.amount) === cents(item.amount));
  const validContext = item => {
    const value = optionsFor(item);
    return !optionLoading[item.assignment_id] && ["native", "operational"].includes(value?.financial_source) &&
      typeof value.session_scope === "string" && value.session_scope.length > 0 &&
      Number.isSafeInteger(value.expected_review_revision) && value.expected_review_revision > 0;
  };
  useEffect(() => {
    let active = true;
    api.get("/store-delivery/payment-review/bank-accounts").then(({ data }) => {
      if (!active) return;
      if ((data?.source === "mz2_financial_accounts" && [undefined, "native", "unavailable"].includes(data.financial_source)) ||
          (data?.source === "operational_bank_catalog" && data.financial_source === "operational")) {
        setBanks(data.items || []); setBankSource(data.financial_source || "native");
      } else setBankError("تعذر التحقق من مصدر قائمة البنوك؛ أعد تحميل خيارات الاعتماد");
    }, () => { if (active) setBankError("تعذر تحميل البنوك الأصلية؛ الاعتماد غير متاح"); });
    api.get("/accounting-module/onboarding/typed-facts").then(({ data }) => {
      if (active) setPosFacts(Array.isArray(data?.items) ? data.items.filter(isPosDestination) : []);
    }, () => { if (active) setPosError("تعذر تحميل الذمم الأصلية؛ اعتماد الشبكة غير متاح"); });
    return () => { active = false; };
  }, []);
  const destination = item => destinations[item.assignment_id] || {};
  const canUpload = item => validContext(item) && optionsFor(item)?.can_approve !== false &&
    statementContextMatches(statementContexts[item.assignment_id], optionsFor(item)) &&
    statementContexts[item.assignment_id].can_upload === true &&
    destination(item).bank === optionsFor(item).bank_account_id &&
    banksFor(item).some(bank => bank.id === destination(item).bank);
  const canApprove = item => validContext(item) && !uploads[item.assignment_id]?.attempt && optionsFor(item).can_approve !== false && (
    item.payment_method === "bank_transfer"
      ? banksFor(item).some(bank => bank.id === destination(item).bank) && (optionsFor(item).financial_source === "operational"
        ? statementContextMatches(statementContexts[item.assignment_id], optionsFor(item)) &&
          destination(item).bank === optionsFor(item).bank_account_id && movementsFor(item).some(row => row.id === destination(item).movement)
        : destination(item).movement?.trim())
      : item.payment_method === "card_terminal" && Boolean(item.receipt_reference?.trim() && item.receipt_url?.trim()) &&
        posFor(item).some(fact => fact.id === destination(item).pos) && cents(item.amount) !== null &&
        cents(destination(item).amount) === cents(item.amount));
  const updateDestination = (item, key, value) => {
    if (uploads[item.assignment_id]?.attempt) return;
    if (key === "bank" && value !== destination(item).bank) {
      setUploads(previous => ({...previous, [item.assignment_id]: {}}));
    }
    setDestinations(previous => ({ ...previous,
      [item.assignment_id]: { ...previous[item.assignment_id], ...(key === "bank" ? {movement: ""} : {}), [key]: value } }));
  };

  async function load() {
    const version = ++loadVersion.current;
    setReviewOptions({}); setOptionErrors({}); setStatementContexts({}); setOptionLoading({});
    setDestinations(Object.fromEntries(Object.entries(uploads).filter(([, upload]) => upload.attempt)
      .map(([id, upload]) => [id, {bank: upload.attempt.bank_account_id}])));
    try {
      const response = await api.get("/store-delivery/payment-review/pending", { params: filter ? { method: filter } : {} });
      if (!alive.current || version !== loadVersion.current) return;
      setItems(response.data?.items || []);
      setUncertain(previous => Object.fromEntries(Object.entries(previous).filter(([id]) => (response.data?.items || []).some(item => item.assignment_id === id))));
      setUploads(previous => Object.fromEntries(Object.entries(previous).filter(([id]) => (response.data?.items || []).some(item => item.assignment_id === id))));
    } catch (error) {
      if (!alive.current || version !== loadVersion.current) return;
      setItems([]); setUncertain({});
      toast.error(error?.response?.data?.detail?.code || "تعذر تحميل مراجعات الدفع");
    }
  }

  useEffect(() => { load(); }, [filter]);

  async function loadOptions(item) {
    const version = loadVersion.current;
    const id = item.assignment_id;
    const optionVersion = (optionVersions.current[id] || 0) + 1;
    optionVersions.current[id] = optionVersion;
    const current = () => alive.current && version === loadVersion.current && optionVersion === optionVersions.current[id];
    setOptionErrors(previous => ({...previous, [item.assignment_id]: ""}));
    setOptionLoading(previous => ({...previous, [id]: true}));
    try {
      const {data} = await api.get(`/store-delivery/payment-review/${item.assignment_id}/approval-options`);
      if (!current()) return;
      if (data.assignment_id !== item.assignment_id || data.payment_method !== item.payment_method ||
          cents(data.amount) === null || cents(data.amount) !== cents(item.amount)) throw Error("review_context_mismatch");
      let statementContext = null;
      if (item.payment_method === "bank_transfer" && data.financial_source === "operational") {
        statementContext = (await api.get("/store-delivery/payment-review/bank-statements/context")).data;
        if (!current()) return;
        if (!statementContextMatches(statementContext, data)) {
          const error = new Error("bank_statement_context_mismatch"); error.code = error.message; throw error;
        }
      } else if (item.payment_method === "bank_transfer" && data.financial_source === "native" && bankSource !== "native") {
        const catalog = (await api.get("/store-delivery/payment-review/bank-accounts")).data;
        if (!current()) return;
        if (catalog?.source !== "mz2_financial_accounts" || ![undefined, "native"].includes(catalog.financial_source)) {
          const error = new Error("bank_statement_context_mismatch"); error.code = error.message; throw error;
        }
        setBanks(catalog.items || []); setBankSource("native"); setBankError("");
      }
      const previousContext = optionsFor(item) || uploads[id]?.reviewContext;
      const attempt = uploads[id]?.attempt;
      if ((previousContext && (!sameReviewScope(previousContext, data) || previousContext.bank_account_id !== data.bank_account_id ||
          previousContext.expected_review_revision !== data.expected_review_revision)) || (attempt &&
          (data.financial_source !== "operational" || attempt.expected_started_at !== data.started_at ||
           attempt.expected_session_scope !== data.session_scope || attempt.bank_account_id !== data.bank_account_id))) {
        setDestinations(previous => ({...previous, [id]: {}}));
        setUploads(previous => ({...previous, [id]: {}}));
      }
      setStatementContexts(previous => ({...previous, [id]: statementContext}));
      setReviewOptions(previous => ({...previous, [item.assignment_id]: data}));
    } catch (error) {
      if (!current()) return;
      setReviewOptions(previous => ({...previous, [item.assignment_id]: null}));
      setStatementContexts(previous => ({...previous, [id]: null}));
      if (CONTEXT_CHANGED.has(error?.code || error?.response?.data?.detail?.code)) {
        setDestinations(previous => ({...previous, [id]: {}}));
        setUploads(previous => ({...previous, [id]: {}}));
      }
      setOptionErrors(previous => ({...previous, [item.assignment_id]: bankErrorMessage(error, error?.response?.data?.detail?.code || "تعذر تحميل خيارات الاعتماد")}));
    } finally {
      if (current()) setOptionLoading(previous => ({...previous, [id]: false}));
    }
  }

  function chooseStatement(item, file, input) {
    if (inFlight.current || uploads[item.assignment_id]?.attempt) return;
    const context = statementContexts[item.assignment_id];
    const error = file ? statementFileError(file, context) : "";
    if (error) input.value = "";
    setUploads(previous => ({...previous, [item.assignment_id]: {
      file: error ? null : file, error, inputVersion: previous[item.assignment_id]?.inputVersion || 0,
      reviewContext: optionsFor(item),
    }}));
  }

  async function uploadStatement(item, retry = false) {
    const id = item.assignment_id;
    if (inFlight.current || !canUpload(item) || uncertain[id]) return;
    const upload = uploads[id] || {};
    if (!retry && (upload.attempt || statementFileError(upload.file, statementContexts[id]))) return;
    const attempt = retry ? upload.attempt : {
      file: upload.file, assignment_id: id, bank_account_id: destination(item).bank,
      request_id: requestId(), expected_started_at: statementContexts[id].started_at,
      expected_session_scope: statementContexts[id].session_scope,
    };
    if (!attempt || attempt.expected_started_at !== statementContexts[id].started_at ||
        attempt.expected_session_scope !== statementContexts[id].session_scope || attempt.bank_account_id !== optionsFor(item).bank_account_id) return;
    const form = new FormData();
    Object.entries(attempt).forEach(([key, value]) => form.append(key, value));
    inFlight.current = true; setBusyId(id);
    setUploads(previous => ({...previous, [id]: {...previous[id], attempt, error: "", result: null}}));
    const version = loadVersion.current;
    try {
      const {data} = await api.post("/store-delivery/payment-review/bank-statements/upload", form);
      if (!alive.current || version !== loadVersion.current) return;
      if (!["imported", "duplicate"].includes(data?.status) || data.source !== STATEMENT_SOURCE ||
          data.financial_source !== "operational" || data.file?.bank_account_id !== attempt.bank_account_id ||
          data.file?.started_at !== attempt.expected_started_at || typeof data.file?.id !== "string" || !data.file.id ||
          !Number.isSafeInteger(data.file.row_count) || data.file.row_count < 0) throw Error("unconfirmed_statement_upload");
      setUploads(previous => ({...previous, [id]: {file: null, attempt: null, result: data,
        inputVersion: (previous[id]?.inputVersion || 0) + 1}}));
      setDestinations(previous => ({...previous, [id]: {...previous[id], movement: ""}}));
      // The upload includes every original row. Only fresh approval options
      // identify movements eligible for this assignment, bank and amount.
      await loadOptions(item);
    } catch (error) {
      if (!alive.current || version !== loadVersion.current) return;
      const status = error?.response?.status;
      const ambiguous = !status || status >= 500 || status === 408;
      const changed = CONTEXT_CHANGED.has(error?.response?.data?.detail?.code);
      const message = bankErrorMessage(error, ambiguous
        ? "لم تتأكد نتيجة رفع الكشف. أعد رفع نفس الملف للتحقق من المحاولة المحفوظة."
        : "تعذر رفع كشف البنك. تحقق من الملف ثم أعد المحاولة.");
      setUploads(previous => ({...previous, [id]: {...previous[id], file: changed ? null : attempt.file,
        attempt: ambiguous ? attempt : null, error: message, result: null}}));
      if (changed) {
        setDestinations(previous => ({...previous, [id]: {}}));
        setReviewOptions(previous => ({...previous, [id]: null}));
        setStatementContexts(previous => ({...previous, [id]: null}));
        setOptionErrors(previous => ({...previous, [id]: message}));
      }
    } finally {
      inFlight.current = false;
      if (alive.current) setBusyId("");
    }
  }

  async function decide(item, decision, retry = false) {
    if (inFlight.current || uploads[item.assignment_id]?.attempt || !validContext(item) || (!retry && decision === "approved" && !canApprove(item))) return;
    let payload = retry ? uncertain[item.assignment_id] : null;
    if (retry && (!payload || payload.expected_session_scope !== optionsFor(item).session_scope ||
        payload.expected_financial_source !== optionsFor(item).financial_source)) return;
    if (!payload) {
      const note = decision === "rejected" ? window.prompt("سبب الرفض للموصل:", "الإيصال غير واضح") : "تمت المطابقة";
      if (decision === "rejected" && note === null) return;
      const context = optionsFor(item);
      payload = {decision, note: note || "", expected_financial_source: context.financial_source,
        expected_review_revision: context.expected_review_revision, expected_session_scope: context.session_scope};
      if (decision === "approved") {
        if (item.payment_method === "bank_transfer") Object.assign(payload, {
          destination_financial_id: destination(item).bank, settlement_reference: context.financial_source === "operational"
            ? destination(item).movement : destination(item).movement.trim(),
        });
        else {
          payload.destination_financial_id = destination(item).pos;
          payload.pos_reviewed_amount = destination(item).amount;
          if (destination(item).reference?.trim()) payload.settlement_reference = destination(item).reference.trim();
        }
      }
    }
    inFlight.current = true; setBusyId(item.assignment_id);
    const version = loadVersion.current;
    try {
      await api.post(`/store-delivery/payment-review/${item.assignment_id}`, payload);
      if (!alive.current || version !== loadVersion.current) return;
      setUncertain(previous => {const next = {...previous}; delete next[item.assignment_id]; return next;});
      toast.success(payload.decision === "approved" ? "تم اعتماد الدفعة" : "تم رفض الإيصال وإعادته للموصل");
      await load();
    } catch (error) {
      if (!alive.current || version !== loadVersion.current) return;
      if (!error?.response?.status || error.response.status >= 500) {
        setUncertain(previous => ({...previous, [item.assignment_id]: payload}));
      } else {
        setUncertain(previous => {const next = {...previous}; delete next[item.assignment_id]; return next;});
        setReviewOptions(previous => ({...previous, [item.assignment_id]: null}));
      }
      toast.error(error?.response?.data?.detail?.code || "لم تتأكد نتيجة الاعتماد؛ تحقق من القائمة أو أعد نفس الطلب");
    } finally {
      inFlight.current = false;
      if (alive.current) setBusyId("");
    }
  }

  return (
    <main className="space-y-5 p-4 sm:p-6" dir="rtl">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div><h1 className="text-2xl font-black text-slate-950">مراجعة تحصيلات الموصلين</h1><p className="mt-1 text-sm font-bold text-slate-500">الشبكة والتحويل البنكي لا يصبحان مدفوعين إلا بعد مطابقة المحاسب.</p></div>
        <button type="button" disabled={Boolean(busyId)} onClick={load}>تحديث القائمة للتحقق</button>
        <select aria-label="طريقة التحصيل" disabled={Boolean(busyId)} value={filter} onChange={(e) => setFilter(e.target.value)} className="h-11 rounded-xl border bg-white px-3 text-sm font-bold"><option value="">الكل</option><option value="card_terminal">شبكة</option><option value="bank_transfer">تحويل بنكي</option></select>
      </header>

      <section className="grid gap-4 xl:grid-cols-2">
        {items.map((item) => (
          <article key={item.assignment_id} className="rounded-3xl border bg-white p-5 shadow-sm">
            <div className="flex items-start justify-between gap-3"><div><div className="text-xs font-black text-slate-500">الطلب</div><div className="text-xl font-black">#{item.order_number || item.order_id}</div><div className="mt-1 text-sm font-bold text-slate-600">الموصل: {item.driver_name || "—"} {item.driver_phone ? `· ${item.driver_phone}` : ""}</div></div><div className="rounded-2xl bg-emerald-50 px-3 py-2 text-lg font-black text-emerald-800" dir="ltr">{money(item.amount)}</div></div>
            <div className="mt-4 grid gap-2 text-sm font-bold sm:grid-cols-2"><div className="rounded-xl bg-slate-50 p-3">طريقة التحصيل<br/><b>{item.payment_method === "bank_transfer" ? "تحويل بنكي" : "شبكة"}</b></div><div className="rounded-xl bg-slate-50 p-3">البنك<br/><b>{item.bank_name_snapshot || "—"}</b></div></div>
            {item.receipt_url ? <a href={item.receipt_url} target="_blank" rel="noreferrer" className="mt-4 flex items-center justify-center gap-2 rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 font-black text-sky-800"><Image size={20} />عرض صورة الإيصال</a> : <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm font-black text-rose-900">لا يوجد إيصال صالح</div>}
            <div className="mt-3 rounded-xl border p-3">
              <button type="button" disabled={Boolean(busyId) || optionLoading[item.assignment_id]} onClick={() => loadOptions(item)}>تحميل خيارات الاعتماد</button>
              {optionsFor(item) && <p>مسار الاعتماد: {optionsFor(item).financial_source === "operational" ? "الأرصدة التشغيلية" : "المحاسبة الأصلية"}</p>}
              {optionsFor(item)?.note && <p>{optionsFor(item).note}</p>}
              {(optionErrors[item.assignment_id] || optionsFor(item)?.blocker) && <p role="alert">{optionErrors[item.assignment_id] || optionsFor(item).blocker}</p>}
              {uncertain[item.assignment_id] && <div role="alert"><p>نتيجة الطلب غير مؤكدة؛ لا تنشئ اعتمادًا جديدًا.</p>
                <button type="button" disabled={Boolean(busyId) || !validContext(item)} onClick={() => decide(item, uncertain[item.assignment_id].decision, true)}>إعادة إرسال نفس الطلب</button></div>}
            </div>
            {item.payment_method === "bank_transfer" ? <div className="mt-4 space-y-2">
              <label className="block space-y-1 font-bold">البنك المستلم المؤكد<select
                disabled={Boolean(busyId) || optionLoading[item.assignment_id] || Boolean(uploads[item.assignment_id]?.attempt)}
                className="block w-full rounded-xl border bg-white p-3" value={destination(item).bank || ""}
                onChange={e => updateDestination(item, "bank", e.target.value)}><option value="">اختر البنك</option>
                {banksFor(item).map(bank => <option key={bank.id} value={bank.id}>{bank.name || bank.id}</option>)}</select></label>
              {optionsFor(item)?.financial_source === "operational" ? <>
                {destination(item).bank && destination(item).bank !== optionsFor(item).bank_account_id &&
                  <p role="alert" className="text-sm font-bold text-rose-700">اختر البنك المرتبط بتحويل الموصل؛ لا يمكن تغيير البنك عند المطابقة.</p>}
                <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <label className="block space-y-2 font-bold">كشف البنك الأصلي (XLSX)<input type="file" accept=".xlsx"
                    key={`${statementContexts[item.assignment_id]?.started_at}-${statementContexts[item.assignment_id]?.session_scope}-${uploads[item.assignment_id]?.inputVersion || 0}`}
                    disabled={Boolean(busyId) || Boolean(uploads[item.assignment_id]?.attempt) || Boolean(uncertain[item.assignment_id]) || !canUpload(item)}
                    className="block w-full text-sm" onChange={e => chooseStatement(item, e.target.files?.[0] || null, e.target)} /></label>
                  <p className="text-sm text-slate-600">ارفع كشف البنك، ثم اختر الحركة الواردة المطابقة. يبقى التحويل بانتظار المراجعة حتى تضغط «اعتماد مدفوع».</p>
                  {statementContexts[item.assignment_id]?.max_file_bytes && <p className="text-xs text-slate-500">الحد الأقصى للملف: {Math.ceil(statementContexts[item.assignment_id].max_file_bytes / 1024)} كيلوبايت.</p>}
                  {statementContexts[item.assignment_id]?.can_upload === false && <p role="status" className="text-sm font-bold text-amber-800">ليست لديك صلاحية رفع كشوف البنك. يمكنك مطابقة التحويل مع حركة محفوظة في القائمة.</p>}
                  {uploads[item.assignment_id]?.file && <p className="break-words text-sm">الملف المختار: <bdi>{uploads[item.assignment_id].file.name}</bdi></p>}
                  {uploads[item.assignment_id]?.attempt ? <button type="button"
                    disabled={Boolean(busyId) || !canUpload(item)} onClick={() => uploadStatement(item, true)}
                    className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-2 font-bold text-amber-900 disabled:opacity-40">إعادة رفع نفس الملف</button>
                    : <button type="button" disabled={Boolean(busyId) || Boolean(uncertain[item.assignment_id]) || !canUpload(item) || !uploads[item.assignment_id]?.file}
                      onClick={() => uploadStatement(item)} className="rounded-xl bg-sky-700 px-4 py-2 font-bold text-white disabled:opacity-40">رفع كشف البنك</button>}
                  {uploads[item.assignment_id]?.error && <p role="alert" className="text-sm font-bold text-rose-700">{uploads[item.assignment_id].error}</p>}
                  {uploads[item.assignment_id]?.result && <p role="status" className="text-sm font-bold text-emerald-800">
                    {uploads[item.assignment_id].result.status === "duplicate" ? "الكشف محفوظ مسبقًا" : "تم حفظ كشف البنك"} · {uploads[item.assignment_id].result.file.row_count} صف.
                    اختر الحركة المطابقة من القائمة، ثم راجع التحويل لاعتماده.
                  </p>}
                </div>
                <label className="block space-y-1 font-bold">حركة البنك الواردة المطابقة<select
                  disabled={Boolean(busyId) || optionLoading[item.assignment_id] || Boolean(uploads[item.assignment_id]?.attempt) || destination(item).bank !== optionsFor(item).bank_account_id}
                  className="block w-full rounded-xl border bg-white p-3" value={destination(item).movement || ""}
                  onChange={e => updateDestination(item, "movement", e.target.value)}><option value="">اختر الحركة بعد مراجعة الكشف</option>
                  {movementsFor(item).map(row => <option key={row.id} value={row.id}>{row.movement_date} · {money(row.amount)} · {row.reference || row.description || `الصف ${row.row_no}`}</option>)}</select></label>
                {!movementsFor(item).length && <p role="status" className="text-sm text-slate-600">لا توجد حركات واردة مؤهلة لهذا البنك والمبلغ. ارفع الكشف أو حدّث خيارات الاعتماد بعد إضافته.</p>}
                <p className="text-sm text-slate-600">راجع التاريخ والمرجع قبل اختيار الحركة. تطابق المبلغ وحده لا يعتمد التحويل تلقائيًا.</p>
              </> : <>
                <label>معرف حركة البنك الواردة المستوردة<input value={destination(item).movement || ""} onChange={e => updateDestination(item, "movement", e.target.value)} /></label>
                <p>استخدم معرف الحركة من كشف البنك المستورد؛ رقم الإيصال أو وصف التحويل لا يثبت وصول المبلغ. يتحقق النظام من البنك والمبلغ وأصل الكشف.</p>
                {bankError && <p role="alert">{bankError}</p>}
              </>}
            </div> : item.payment_method === "card_terminal" ? <div className="mt-4 space-y-2">
              <label>ذمة الشبكة المختارة<select value={destination(item).pos || ""} onChange={e => updateDestination(item, "pos", e.target.value)}><option value="">اختر ذمة موثقة من الذمم المدينة الأخرى</option>{posFor(item).map(fact => <option key={fact.id} value={fact.id}>{fact.display_name}{isPosDestination(fact) ? ` · ${fact.entity_type}/${fact.entity_id}/${fact.sub_account}` : ""}</option>)}</select></label>
              <label>مبلغ إيصال الشبكة المطابق<input inputMode="decimal" value={destination(item).amount || ""} onChange={e => updateDestination(item, "amount", e.target.value)} /></label>
              <label>مرجع عملية الشبكة (اختياري)<input value={destination(item).reference || ""} onChange={e => updateDestination(item, "reference", e.target.value)} /></label>
              <p>راجع الإيصال المرتبط وطابق مبلغه مع مبلغ الطلب. اعتماد المحاسب ينقل مسؤولية COD إلى ذمة الشبكة المختارة ضمن الذمم المدينة الأخرى؛ لا يثبت وصول المبلغ إلى البنك.</p>
              <p>مرجع الإيصال المرتبط: <bdi>{item.receipt_reference || "غير متاح"}</bdi></p>
              {(!item.receipt_reference || !item.receipt_url) && <p role="alert">يلزم إيصال مرتبط قابل للعرض قبل الاعتماد.</p>}
              {destination(item).amount && cents(destination(item).amount) !== cents(item.amount) && <p role="alert">أدخل مبلغًا صحيحًا مطابقًا لمبلغ الطلب، بمنزلتين عشريتين كحد أقصى.</p>}
              {posError && optionsFor(item)?.financial_source !== "operational" && <p role="alert">{posError}</p>}
            </div> : <p role="status">طريقة التحصيل غير متاحة للاعتماد.</p>}
            <div className="mt-4 grid grid-cols-2 gap-2"><button disabled={Boolean(busyId) || Boolean(uncertain[item.assignment_id]) || Boolean(uploads[item.assignment_id]?.attempt) || !validContext(item) || optionsFor(item)?.can_reject === false} onClick={() => decide(item, "rejected")} className="flex items-center justify-center gap-2 rounded-2xl border border-rose-300 bg-rose-50 px-4 py-3 font-black text-rose-800 disabled:opacity-40"><XCircle size={20} />رفض</button><button disabled={Boolean(busyId) || Boolean(uncertain[item.assignment_id]) || !canApprove(item)} onClick={() => decide(item, "approved")} className="flex items-center justify-center gap-2 rounded-2xl bg-emerald-700 px-4 py-3 font-black text-white disabled:opacity-40"><CheckCircle size={20} />اعتماد مدفوع</button></div>
          </article>
        ))}
        {!items.length && <div className="rounded-3xl border border-dashed bg-white p-10 text-center text-sm font-bold text-slate-500 xl:col-span-2"><Bank className="mx-auto mb-2" size={32} />لا توجد دفعات بانتظار المراجعة.</div>}
      </section>
    </main>
  );
}
