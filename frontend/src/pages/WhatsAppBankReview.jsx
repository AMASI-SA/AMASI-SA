/**
 * P06: read-only bank-transfer review for WhatsApp-assisted orders.
 * Only authenticated users with bank_receipts.view can query the server.
 * No payment, status, accounting or fulfillment POST actions here.
 */
import { useCallback, useEffect, useState } from "react";
import api from "../lib/api";

const BASE = "/orders-v2/assisted-salla/bank-review";
const PAGE_SIZE = 20;
const EVIDENCE = { image: "صورة", file: "ملف", text: "رسالة نصية" };
const STATE = {
    awaiting_staff_bank_verification: "بانتظار التحقق من البنك",
    needs_staff_reconciliation: "تحتاج مطابقة يدوية",
    waiting_for_mezan_sync: "بانتظار مزامنة ميزان",
    waiting_for_salla_reference: "بانتظار مرجع سلة",
};
const validIntent = (v) => typeof v === "string" && /^[A-Za-z0-9_-]{8,100}$/.test(v);
const shown = (v) => v == null ? "—" : String(v);
const money = (v) => {
    const n = Number(v);
    return Number.isFinite(n) && n >= 0
        ? n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ر.س"
        : "—";
};
const failure = (err) => {
    const status = err?.response?.status;
    if (status === 403) return "ليس لديك صلاحية مراجعة إثباتات التحويل البنكي (bank_receipts.view).";
    if (status === 503) return "واجهة مراجعة حوالات واتساب غير مفعّلة أو غير جاهزة بعد.";
    return "تعذر قراءة المراجعات. لم يتغير أي طلب أو قيد محاسبي.";
};

export default function WhatsAppBankReview() {
    const [offset, setOffset] = useState(0);
    const [version, setVersion] = useState(0);
    const [items, setItems] = useState([]);
    const [next, setNext] = useState(null);
    const [busy, setBusy] = useState(true);
    const [error, setError] = useState("");
    const [selected, setSelected] = useState(null);
    const [detail, setDetail] = useState(null);
    const [detailBusy, setDetailBusy] = useState(false);
    const [detailError, setDetailError] = useState("");

    const reload = useCallback(() => setVersion((n) => n + 1), []);
    useEffect(() => {
        let alive = true;
        setBusy(true);
        setError("");
        api.get(BASE + "/worklist", { params: { offset, limit: PAGE_SIZE } })
            .then(({ data }) => {
                if (!alive) return;
                if (data?.read_only !== true || !Array.isArray(data.items)) {
                    throw new Error("unexpected_bank_worklist_response");
                }
                setItems(data.items);
                setNext(Number.isInteger(data.next_offset) ? data.next_offset : null);
            }).catch((err) => {
                if (alive) {
                    setError(failure(err));
                    setItems([]);
                    setNext(null);
                }
            }).finally(() => { if (alive) setBusy(false); });
        return () => { alive = false; };
    }, [offset, version]);

    useEffect(() => {
        let alive = true;
        setDetail(null);
        setDetailError("");
        if (!selected) return () => { alive = false; };
        if (!validIntent(selected.intent_id)) {
            setDetailError("معرّف الطلب غير صالح.");
            return () => { alive = false; };
        }
        setDetailBusy(true);
        api.get(BASE + "/" + encodeURIComponent(selected.intent_id))
            .then(({ data }) => { if (alive) setDetail(data); })
            .catch((err) => { if (alive) setDetailError(failure(err)); })
            .finally(() => { if (alive) setDetailBusy(false); });
        return () => { alive = false; };
    }, [selected, version]);

    return (
        <main dir="rtl" className="mx-auto max-w-6xl space-y-4 p-4 md:p-6" data-testid="wa-bank-review-page">
            <header className="space-y-2">
                <h1 className="text-xl font-extrabold">مراجعة حوالات عملاء واتساب</h1>
                <p className="text-sm text-slate-600">
                    الإيصال المرسل صورة أو ملفاً أو رسالةً نصية هو دليل أولي فقط.
                    لا يثبت وصول الأموال، ولا يتيح بدء التنفيذ.
                </p>
                <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                    هذه الصفحة للقراءة فقط. لا توجد بها إجراءات لتأكيد الدفع أو
                    تغيير حالة الطلب أو ترحيل قيود. التأكيد البنكي يتم بواسطة
                    موظف مخول بعد مراجعة البنك الفعلية.
                </p>
            </header>
            <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="text-sm text-slate-600">
                    {busy ? "جارٍ التحميل..." : "السجلات المعروضة: " + items.length}
                </span>
                <button type="button" disabled={busy}
                    className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs disabled:opacity-50"
                    onClick={() => { setOffset(0); reload(); }}
                    data-testid="wa-bank-review-refresh">تحديث</button>
            </div>

            {error && <div role="alert" className="rounded-lg border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">{error}</div>}
            {!busy && !error && items.length === 0 &&
                <div className="rounded-lg border bg-white p-6 text-sm text-slate-600" data-testid="wa-bank-review-empty">
                    لا توجد سجلات حوالات بانتظار المراجعة في هذه الصفحة.
                </div>
            }
            {!error && items.length > 0 &&
                <div className="overflow-x-auto rounded-xl border bg-white">
                    <table className="w-full text-right text-sm">
                        <thead className="bg-slate-50">
                            <tr>
                                <th className="p-3">طلب سلة</th>
                                <th className="p-3">المبلغ المذكور</th>
                                <th className="p-3">الإثبات</th>
                                <th className="p-3">محادثة واتساب</th>
                                <th className="p-3">الحالة</th>
                                <th className="p-3">فحص المطابقة</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((row) =>
                                <tr key={String(row.intent_id) + ":" + String(row.salla_order_id)} className="border-t">
                                    <td className="p-3 font-mono">{shown(row.salla_reference_id || row.salla_order_id)}</td>
                                    <td className="p-3 whitespace-nowrap">{money(row.receipt_amount_sar)}</td>
                                    <td className="p-3">{EVIDENCE[row.source_kind] || "غير معروف"}</td>
                                    <td className="p-3 text-xs">
                                        <div>محادثة: {shown(row.conversation_id)}</div>
                                        <div>رسالة: {shown(row.source_message_id)}</div>
                                        {row.source_attachment_id > 0 &&
                                            <div>مرفق: {shown(row.source_attachment_id)}</div>}
                                    </td>
                                    <td className="p-3 text-amber-800">بانتظار تحقق الموظف</td>
                                    <td className="p-3">
                                        <button type="button" onClick={() => setSelected(row)}
                                            className="rounded-lg border px-3 py-2 text-xs"
                                            data-testid="wa-bank-review-detail">مطابقة</button>
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            }
            <div className="flex items-center justify-end gap-3">
                <button type="button" disabled={busy || offset === 0}
                    onClick={() => { setOffset(Math.max(0, offset - PAGE_SIZE)); setSelected(null); }}
                    className="rounded-lg border px-3 py-2 text-xs disabled:opacity-40">السابق</button>
                <span className="text-xs">صفحة {Math.floor(offset / PAGE_SIZE) + 1}</span>
                <button type="button" disabled={busy || next == null}
                    onClick={() => { setOffset(next); setSelected(null); }}
                    className="rounded-lg border px-3 py-2 text-xs disabled:opacity-40">التالي</button>
            </div>
            {selected && <section className="space-y-3 rounded-xl border bg-white p-4" data-testid="wa-bank-review-inspection">
                <div className="flex justify-between">
                    <h2 className="font-bold">مطابقة طلب {shown(selected.salla_reference_id || selected.salla_order_id)}</h2>
                    <button type="button" onClick={() => setSelected(null)} className="text-sm">إغلاق</button>
                </div>
                {detailBusy && <p className="text-sm">جارٍ التحقق من حالة الطلب في ميزان...</p>}
                {detailError && <p role="alert" className="text-sm text-rose-700">{detailError}</p>}
                {!detailBusy && detail && <>
                    <p className="text-sm">الحالة: {STATE[detail.state] || shown(detail.state)}</p>
                    {Array.isArray(detail.flags) && detail.flags.length > 0 &&
                        <p className="text-xs text-amber-800">ملاحظات المطابقة: {detail.flags.join("، ")}</p>}
                    <p className="text-sm">
                        راجع الإثبات الأصلي من محادثة Chatwoot رقم {shown(selected.conversation_id)}
                        ، رسالة رقم {shown(selected.source_message_id)}،
                        بصلاحيات الموظف المعتمد.
                    </p>
                    <p className="text-sm font-semibold text-amber-800">
                        لم يؤكد النظام وصول المبلغ. لا تبدأ تجهيز الطلب قبل تحقق البنك الفعلي.
                    </p>
                </>}
            </section>}
        </main>
    );
}
