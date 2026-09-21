
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle } from "@phosphor-icons/react";

import {
    getAccountingDailyMovements,
    getAccountingOrderEvidence,
    getAccountingOrderRecognitionQueue,
    processAccountingStoreDriverPending,
} from "../../services/accountingModule";
import { formatMoney } from "./AccountingShared";
import { ACCOUNTING_PAGES } from "./accountingPages";

const PROVIDERS = {
    salla: "سلة",
    tamara: "تمارا",
    tabby: "تابي",
    emkan: "إمكان",
    cod: "الدفع عند الاستلام",
    bank_transfer: "تحويل بنكي",
};
const SETTLEMENT_REVIEW = new Set(["needs_review", "ready_for_review", "reviewed", "rejected"]);

function friendlyReference(value, fallback) {
    const text = String(value || "").trim();
    if (!text) return fallback;
    const technical =
        text.length > 28
        || text.includes(":")
        || /^SYN[-_:]/i.test(text)
        || /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(text);
    return technical ? fallback : text;
}

export default function AccountingDailyAutomationStatus({
    status,
    drafts = [],
    receipts = [],
    refreshToken = 0,
}) {
    const [orders, setOrders] = useState([]);
    const [recognition, setRecognition] = useState(null);
    const [movements, setMovements] = useState([]);
    const [shipping, setShipping] = useState(null);

    useEffect(() => {
        let active = true;
        Promise.allSettled([
            getAccountingOrderEvidence({ limit: 300 }),
            getAccountingOrderRecognitionQueue(300),
            getAccountingDailyMovements({ limit: 200 }),
            processAccountingStoreDriverPending({ limit: 200, dryRun: true }),
        ]).then((results) => {
            if (!active) return;
            if (results[0].status === "fulfilled") setOrders(results[0].value?.items || []);
            if (results[1].status === "fulfilled") setRecognition(results[1].value || null);
            if (results[2].status === "fulfilled") setMovements(results[2].value?.items || []);
            if (results[3].status === "fulfilled") setShipping(results[3].value || null);
        });
        return () => { active = false; };
    }, [refreshToken]);

    const exceptions = useMemo(() => {
        const items = [];

        for (const task of status?.tasks || []) {
            const page = ACCOUNTING_PAGES.find((row) => row.id === task.page) || ACCOUNTING_PAGES[0];
            items.push({
                id: "task:" + task.id,
                title: task.title,
                detail: task.detail,
                to: page.to,
                informational: false,
            });
        }

        for (const row of orders) {
            if (row.status === "needs_review" || row.status === "needs_bank_transfer_evidence") {
                items.push({
                    id: "order:" + row.id,
                    title: "طلب " + row.order_number + " يحتاج مراجعة",
                    detail: row.status === "needs_bank_transfer_evidence"
                        ? "ينتظر مطابقة التحويل البنكي."
                        : "يوجد تعارض أو دليل غير مكتمل في الطلب.",
                    to: "/integrations-v2?workspace=financial&page=financial-movements",
                    informational: false,
                });
            } else if (
                row.status === "ready_sale_refund_pending_evidence"
                || row.status === "recognized_refund_pending_evidence"
            ) {
                items.push({
                    id: "refund:" + row.id,
                    title: "استرداد مرتبط بالطلب " + row.order_number,
                    detail: "البيع محفوظ، والاسترداد ينتظر دليل التنفيذ المستقل.",
                    to: "/integrations-v2?workspace=financial&page=financial-movements",
                    informational: true,
                });
            }
        }

        for (const row of recognition?.items || []) {
            if (row.state === "waiting") {
                items.push({
                    id: "provider-wait:" + row.evidence_id,
                    title: "طلب " + row.order_number + " ينتظر دليل " + (PROVIDERS[row.provider] || row.provider || "المزود"),
                    detail: "ميزان سيعيد المحاولة عند وصول دليل المزود؛ لا يحتاج قرارًا الآن.",
                    to: "/integrations-v2?workspace=financial&page=home",
                    informational: true,
                });
            }
        }

        for (const draft of drafts) {
            if (!SETTLEMENT_REVIEW.has(draft.status)) continue;
            items.push({
                id: "settlement:" + draft.id,
                title: (PROVIDERS[draft.provider] || draft.provider || "تسوية") + " — تحتاج مراجعة",
                detail: "راجع ملف التسوية أو المطابقة قبل الترحيل.",
                to: "/integrations-v2?workspace=financial&page=settlements",
                informational: false,
            });
        }

        for (const row of movements) {
            if (!["unclassified", "needs_review"].includes(row.status)) continue;
            items.push({
                id: "movement:" + row.id,
                title: row.direction === "in" ? "حركة بنك واردة تحتاج تصنيفًا" : "حركة بنك صادرة تحتاج تصنيفًا",
                detail: formatMoney(row.amount),
                to: "/integrations-v2?workspace=financial&page=financial-movements",
                informational: false,
            });
        }

        for (const row of shipping?.items || []) {
            if (!["waiting", "blocked"].includes(row.state)) continue;
            items.push({
                id: "shipping:" + String(row.assignment_id || row.order_number || Math.random()),
                title: "شحن/COD للطلب " + (row.order_number || "—"),
                detail: row.state === "waiting"
                    ? "ينتظر دليلًا وسيعاد تلقائيًا عند اكتماله."
                    : "المسار مقفل أو يحتاج إعدادًا قبل الترحيل.",
                to: "/integrations-v2?workspace=financial&page=shipping-cod",
                informational: row.state === "waiting",
            });
        }

        const unique = [];
        const seen = new Set();
        for (const item of items) {
            if (seen.has(item.id)) continue;
            seen.add(item.id);
            unique.push(item);
        }
        return unique;
    }, [status, orders, recognition, drafts, movements, shipping]);

    const recent = useMemo(() => {
        const events = [];
        for (const row of orders.slice(0, 100)) {
            events.push({
                id: "order:" + row.id,
                at: row.updated_source_text || "",
                title: "طلب " + row.order_number + " — " + (PROVIDERS[row.accounting_provider] || "غير مصنف"),
                detail: row.status === "recognized"
                    ? "تم إثبات البيع"
                    : row.status === "recognized_cod"
                    ? "تم إثبات COD"
                    : row.status === "recognized_refund_pending_evidence"
                    ? "البيع مثبت والاسترداد ينتظر دليله"
                    : "تم تحديث دليل الطلب",
                tone: String(row.status || "").startsWith("recognized") ? "ok" : row.status === "needs_review" ? "warn" : "neutral",
            });
        }
        for (const draft of drafts) {
            events.push({
                id: "draft:" + draft.id,
                at: draft.updated_at || draft.created_at || "",
                title: (PROVIDERS[draft.provider] || draft.provider || "تسوية") + " — ملف تسوية",
                detail: friendlyReference(draft.statement_reference, friendlyReference(draft.source_snapshot?.filename, "ملف تسوية")),
                tone: draft.status === "posted" ? "ok" : SETTLEMENT_REVIEW.has(draft.status) ? "warn" : "neutral",
            });
        }
        for (const row of receipts) {
            events.push({
                id: "receipt:" + row.id,
                at: row.created_at || row.received_on || "",
                title: "مبلغ واصل — " + (PROVIDERS[row.provider] || row.provider),
                detail: formatMoney(row.amount) + " · " + (row.status === "posted" ? "تمت تسويته" : row.status === "linked" ? "تمت مطابقته" : "ينتظر كشف المنصة"),
                tone: row.status === "posted" ? "ok" : "neutral",
            });
        }
        for (const row of movements.slice(0, 50)) {
            events.push({
                id: "bank:" + row.id,
                at: row.movement_date || row.created_at || "",
                title: row.direction === "in" ? "حركة بنك واردة" : "حركة بنك صادرة",
                detail: formatMoney(row.amount) + " · " + (row.status === "accounting_posted" ? "تم تصنيفها" : "بانتظار التصنيف"),
                tone: row.status === "accounting_posted" ? "ok" : row.status === "needs_review" ? "warn" : "neutral",
            });
        }
        return events
            .sort((a, b) => String(b.at).localeCompare(String(a.at)))
            .slice(0, 10);
    }, [orders, drafts, receipts, movements]);

    const visible = exceptions.slice(0, 10);
    const actionable = exceptions.filter((item) => !item.informational).length;

    return (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,.9fr)]" data-testid="daily-automation-status">
            <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="daily-accounting-exceptions">
                <div className="flex items-start justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-black text-slate-950">يحتاج منك</h2>
                        <p className="mt-1 text-xs font-semibold text-slate-500">
                            القرارات والاستثناءات فقط؛ الانتظار الآلي يظهر «للعلم».
                            {exceptions.length > visible.length && (
                                <span className="mt-1 block font-extrabold text-amber-800">
                                    القائمة مختصرة إلى {visible.length.toLocaleString("en-US")} من {exceptions.length.toLocaleString("en-US")} عنصر.
                                </span>
                            )}
                        </p>
                    </div>
                    <span className="rounded-full bg-amber-100 px-3 py-1 font-mono text-xs font-black text-amber-900" dir="ltr">
                        {actionable.toLocaleString("en-US")}
                    </span>
                </div>
                <div className="mt-4 space-y-2">
                    {visible.length === 0 ? (
                        <div className="flex items-center gap-2 rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-sm font-extrabold text-emerald-800">
                            <CheckCircle size={21} weight="fill" /> لا يوجد شيء يحتاج منك الآن.
                        </div>
                    ) : visible.map((item) => (
                        <Link key={item.id} to={item.to} className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 p-3 transition hover:border-amber-300 hover:bg-amber-50">
                            <div>
                                <div className="text-sm font-black text-slate-900">{item.title}</div>
                                <div className="mt-1 text-xs font-semibold leading-5 text-slate-500">{item.detail}</div>
                            </div>
                            <span className={"rounded-full px-2 py-1 text-[10px] font-extrabold " + (item.informational ? "bg-sky-50 text-sky-700" : "bg-amber-100 text-amber-900")}>
                                {item.informational ? "للعلم" : "راجع"}
                            </span>
                        </Link>
                    ))}
                </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="daily-accounting-recent">
                <h2 className="text-lg font-black text-slate-950">آخر العمليات</h2>
                <p className="mt-1 text-xs font-semibold text-slate-500">مختصر تشغيلي بدون المراجع التقنية أو تفاصيل دفتر الأستاذ.</p>
                <div className="mt-4 divide-y divide-slate-100">
                    {recent.length === 0 ? (
                        <div className="py-6 text-center text-xs font-semibold text-slate-400">لا توجد عمليات حديثة.</div>
                    ) : recent.map((event) => (
                        <div key={event.id} className="flex items-start gap-3 py-3">
                            <span className={"mt-1 h-2.5 w-2.5 shrink-0 rounded-full " + (event.tone === "ok" ? "bg-emerald-500" : event.tone === "warn" ? "bg-amber-500" : "bg-slate-300")} />
                            <div className="min-w-0 flex-1">
                                <div className="truncate text-sm font-extrabold text-slate-900">{event.title}</div>
                                <div className="mt-1 text-xs font-semibold text-slate-500">{event.detail}</div>
                            </div>
                        </div>
                    ))}
                </div>
            </section>
        </div>
    );
}
