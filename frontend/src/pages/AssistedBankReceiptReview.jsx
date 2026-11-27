import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const STATUS_NAMES = {
  awaiting_human_bank_confirmation: "بانتظار مراجعة خدمة العملاء",
  receipt_appearance_checked_awaiting_bank_credit:
    "الإيصال فُحص — بانتظار تأكيد وصول المبلغ",
  receipt_clarification_required: "يلزم طلب إيصال أوضح",
};

const ISSUE_LABELS = {
  receipt_not_legible: "صورة الإيصال غير واضحة",
  not_recognizable_bank_receipt: "الصورة لا تبدو إيصال تحويل",
  possible_reused_receipt: "اشتباه تكرار إيصال",
  image_integrity_warning: "احتمال تعديل الصورة",
  bank_recipient_not_verified: "المستفيد البنكي غير مؤكد",
  receipt_currency_not_sar: "عملة الحوالة لا تطابق الريال",
  receipt_amount_not_matching_order: "المبلغ الظاهر لا يطابق إجمالي سلة",
  salla_final_amount_not_yet_synced: "إجمالي سلة لم يتزامن بعد",
  transfer_date_unverified: "تاريخ التحويل غير واضح",
  transfer_date_outside_review_window: "تاريخ التحويل يحتاج تدقيقًا",
  transaction_reference_missing: "المرجع البنكي غير واضح",
};

function ReviewCard({ item, onReview, busy }) {
  const pending = item.status === "awaiting_human_bank_confirmation";
  const warning = item.appearance !== "looks_consistent" ||
    (item.issues || []).length > 0;
  const detail = (item.issues || []).map((issue) =>
    ISSUE_LABELS[issue] || "ملاحظة تحتاج مراجعة"
  );
  return (
    <article
      className="rounded-xl border bg-white p-4 shadow-sm space-y-3"
      data-testid={"bank-wa-review-" + item.review_id}
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-bold text-slate-900">
          طلب سلة رقم {item.order_reference || item.salla_order_id}
        </h3>
        <span className="rounded-full bg-amber-50 px-3 py-1 text-sm text-amber-900">
          {STATUS_NAMES[item.status] || "بحاجة للمراجعة"}
        </span>
      </header>
      <p className="text-sm text-slate-700">
        واتساب: محادثة {item.conversation_id}، رسالة الإيصال رقم{" "}
        {item.receipt_message_id}، مرفق {item.receipt_attachment_id}
      </p>
      <p className="text-sm text-slate-700">
        إجمالي سلة: {item.order_total_sar == null
          ? "لم يُتحقق منه بعد"
          : item.order_total_sar + " ر.س"}
      </p>
      <p className={warning ? "text-sm font-medium text-amber-800" :
        "text-sm text-emerald-800"}>
        {warning
          ? "الفحص الآلي رصد نقاطًا تحتاج تدقيق الموظف."
          : "الفحص المبدئي: بيانات الإيصال الظاهرة متسقة، ولم يُثبت وصول المبلغ."}
      </p>
      {detail.length > 0 && (
        <ul className="list-disc pr-5 text-sm text-slate-700 space-y-1">
          {detail.map((label, index) => <li key={index}>{label}</li>)}
        </ul>
      )}
      <p className="text-xs text-slate-500">
        صورة الإيصال الأصلية تبقى في المحادثة المحددة داخل خدمة العملاء.
        يجب على الموظف فحصها ثم التأكد من وصول الحوالة في البنك بشكل مستقل.
      </p>
      {pending && (
        <div className="flex flex-wrap gap-2 pt-2 border-t">
          <button
            type="button"
            disabled={busy}
            className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            onClick={() => onReview(item, "request_clearer_receipt")}
          >
            طلب إيصال أوضح
          </button>
          <button
            type="button"
            disabled={busy}
            className="rounded-lg bg-slate-800 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            onClick={() => onReview(item, "appearance_checked")}
          >
            تمت مراجعة شكل الإيصال
          </button>
        </div>
      )}
      <p className="text-xs text-red-700">
        لا يتم اعتبار الطلب مدفوعًا ولا يبدأ تنفيذه من هذه الصفحة.
      </p>
    </article>
  );
}

export default function AssistedBankReceiptReview() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const url = "/orders-v2/assisted-salla/bank-receipt-reviews";

  const fetchReviews = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const { data } = await api.get(url);
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (err) {
      const code = err?.response?.status;
      setError(code === 503
        ? "ميزة مراجعة إيصالات واتساب لم تُفعّل بعد."
        : code === 403
          ? "ليس لديك صلاحية الاطلاع على مراجعة إيصالات التحويل."
          : "تعذر تحميل بيانات المراجعة. حاول مجددًا.");
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchReviews(); }, [fetchReviews]);

  const counts = useMemo(() => ({
    waiting: items.filter((r) => r.status ===
      "awaiting_human_bank_confirmation").length,
    needsClarification: items.filter((r) => r.status ===
      "receipt_clarification_required").length,
  }), [items]);

  const onReview = async (item, decision) => {
    if (busy) return;
    const isClarification = decision === "request_clearer_receipt";
    const review_note_code = isClarification
      ? "other_staff_followup"
      : "appears_consistent";
    setBusy(item.review_id);
    try {
      const { data } = await api.post(
        `${url}/${encodeURIComponent(item.review_id)}/triage`,
        { decision, review_note_code }
      );
      if (data?.payment_confirmed !== false ||
          data?.ready_for_fulfillment !== false) {
        throw new Error("Unsafe bank review response");
      }
      toast.success(isClarification
        ? "تم تسجيل طلب توضيح الإيصال."
        : "تم تسجيل مراجعة الشكل. لا يزال تأكيد وصول المبلغ مطلوبًا.");
      await fetchReviews();
    } catch {
      toast.error("لم تُعتمد المراجعة؛ تأكد من الصلاحيات وحالة الطلب.");
    } finally {
      setBusy("");
    }
  };

  return (
    <main dir="rtl" className="p-5 space-y-5" data-testid="assisted-bank-receipt-review">
      <div className="space-y-2">
        <h1 className="text-xl font-bold">إيصالات تحويل عملاء واتساب أماسي</h1>
        <p className="text-sm text-slate-600">
          مراجعة الإيصال أولية؛ اعتماد السداد يتطلب تحقق وصول المبلغ للبنك.
        </p>
      </div>
      <div className="flex flex-wrap gap-3 text-sm">
        <span>بانتظار مراجعة الإيصال: {counts.waiting}</span>
        <span>تحتاج توضيحًا: {counts.needsClarification}</span>
        <button type="button" className="underline text-blue-700"
          onClick={fetchReviews} disabled={loading}>تحديث</button>
      </div>
      {loading ? <p>جاري تحميل طلبات التحويل…</p>
        : error ? <p className="text-amber-800">{error}</p>
        : items.length === 0 ? <p>لا توجد طلبات مراجعة ظاهرة.</p>
        : <section className="grid gap-4 lg:grid-cols-2">
            {items.map((item) => (
              <ReviewCard key={item.review_id} item={item}
                busy={busy === item.review_id} onReview={onReview} />
            ))}
          </section>}
    </main>
  );
}
