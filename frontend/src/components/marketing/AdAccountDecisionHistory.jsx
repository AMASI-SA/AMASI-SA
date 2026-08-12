import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, ClockCounterClockwise, WarningCircle } from "@phosphor-icons/react";

import {
    adDecisionError,
    getAdDecisionAccountSummaries,
    getAdDecisionHistory,
} from "../../services/adDecisionLearning";
import AdDecisionChangeCard from "./AdDecisionChangeCard";
import AdDecisionIntelligencePanel from "./AdDecisionIntelligencePanel";

const PAGE_SIZE = 5;

function emptyReport(requestKey = "", page = 1) {
    return {
        requestKey,
        items: [],
        pagination: { page, pages: 0, total: 0, limit: PAGE_SIZE },
    };
}

export default function AdAccountDecisionHistory({
    accountId,
    accountName,
    page = 1,
    onPageChange,
    onSummariesLoaded,
}) {
    const requestKey = accountId ? `${accountId}:${page}` : "";
    const [report, setReport] = useState(() => emptyReport());
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const summaryRequestRef = useRef(0);
    const historyRequestRef = useRef(0);

    useEffect(() => {
        const controller = new AbortController();
        const requestId = ++summaryRequestRef.current;
        getAdDecisionAccountSummaries({ limitPerAccount: PAGE_SIZE, signal: controller.signal })
            .then((summaries) => {
                if (requestId !== summaryRequestRef.current) return;
                onSummariesLoaded?.(summaries);
            })
            .catch((loadError) => {
                if (loadError?.name !== "CanceledError" && loadError?.name !== "AbortError") {
                    // The selected account history remains usable if only the summaries fail.
                }
            });
        return () => {
            controller.abort();
            summaryRequestRef.current += 1;
        };
    }, [onSummariesLoaded]);

    useEffect(() => {
        const controller = new AbortController();
        const requestId = ++historyRequestRef.current;
        if (!accountId) {
            setReport(emptyReport());
            setError("");
            setLoading(false);
            return () => controller.abort();
        }

        setReport(emptyReport(requestKey, page));
        setLoading(true);
        setError("");
        getAdDecisionHistory({ accountId, page, limit: PAGE_SIZE, signal: controller.signal })
            .then((result) => {
                if (requestId !== historyRequestRef.current) return;
                setReport({ ...result, requestKey });
                if (result.pagination.pages > 0 && page > result.pagination.pages) {
                    onPageChange?.(result.pagination.pages);
                }
            })
            .catch((loadError) => {
                if (requestId !== historyRequestRef.current) return;
                if (loadError?.name === "CanceledError" || loadError?.name === "AbortError") return;
                setError(adDecisionError(loadError));
            })
            .finally(() => {
                if (requestId === historyRequestRef.current) setLoading(false);
            });

        return () => {
            controller.abort();
            historyRequestRef.current += 1;
        };
    }, [accountId, onPageChange, page, requestKey]);

    const visibleReport = report.requestKey === requestKey
        ? report
        : emptyReport(requestKey, page);
    const visibleLoading = loading || (!!accountId && report.requestKey !== requestKey);

    if (!accountId) {
        return (
            <section className="rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-center" data-testid="ad-decision-history-empty-selection">
                <ClockCounterClockwise size={32} weight="duotone" className="mx-auto text-slate-400" />
                <h2 className="mt-3 font-black text-slate-900">اختر حسابًا لعرض سجل تعديلاته</h2>
                <p className="mt-1 text-xs font-semibold text-slate-500">يعرض ميزان آخر 5 تعديلات في كل صفحة، ثم يقارن المتوقع بالنتيجة الفعلية.</p>
            </section>
        );
    }

    return (
        <section className="space-y-3" data-testid="ad-decision-history" aria-busy={visibleLoading}>
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                <div>
                    <h2 className="font-black text-slate-950">سجل التعديلات والنتائج · {accountName || accountId}</h2>
                    <p className="mt-1 text-xs font-semibold text-slate-500">الارتباط الزمني يساعد التشخيص، لكنه لا يُعرض كسبب مؤكد دون دليل كافٍ.</p>
                </div>
                <div className="rounded-full bg-slate-100 px-3 py-1 font-mono text-xs font-black text-slate-700">
                    {visibleReport.pagination.total.toLocaleString("en-US")} تعديل
                </div>
            </div>

            <AdDecisionIntelligencePanel accountId={accountId} />

            {error && (
                <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-800">
                    <WarningCircle size={19} weight="fill" className="ml-2 inline" />{error}
                </div>
            )}

            {visibleLoading && !visibleReport.items.length && (
                <div className="space-y-3" data-testid="ad-decision-history-loading">
                    {[1, 2].map((item) => <div key={item} className="h-56 animate-pulse rounded-2xl bg-slate-100" />)}
                </div>
            )}

            {!visibleLoading && !error && !visibleReport.items.length && (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-center text-sm font-bold text-slate-500">
                    لا توجد تعديلات موثقة لهذا الحساب بعد.
                </div>
            )}

            {visibleReport.items.map((decision) => (
                <AdDecisionChangeCard key={decision.decision_id} decision={decision} />
            ))}

            {visibleReport.pagination.pages > 1 && (
                <nav className="flex items-center justify-center gap-3 rounded-2xl border border-slate-200 bg-white p-3" aria-label="صفحات سجل التعديلات">
                    <button
                        type="button"
                        onClick={() => onPageChange?.(Math.max(1, visibleReport.pagination.page - 1))}
                        disabled={visibleLoading || visibleReport.pagination.page <= 1}
                        className="inline-flex min-h-10 items-center gap-1 rounded-xl border border-slate-200 px-3 text-xs font-black disabled:opacity-40"
                    >
                        <ArrowRight size={16} />السابق
                    </button>
                    <span className="font-mono text-xs font-black text-slate-600">
                        صفحة {visibleReport.pagination.page.toLocaleString("en-US")} من {visibleReport.pagination.pages.toLocaleString("en-US")}
                    </span>
                    <button
                        type="button"
                        onClick={() => onPageChange?.(Math.min(visibleReport.pagination.pages, visibleReport.pagination.page + 1))}
                        disabled={visibleLoading || visibleReport.pagination.page >= visibleReport.pagination.pages}
                        className="inline-flex min-h-10 items-center gap-1 rounded-xl border border-slate-200 px-3 text-xs font-black disabled:opacity-40"
                    >
                        التالي<ArrowLeft size={16} />
                    </button>
                </nav>
            )}
        </section>
    );
}
