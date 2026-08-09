import { useEffect, useRef } from "react";

export default function InfiniteScrollSentinel({
    hasMore = false,
    loading = false,
    loaded = 0,
    total = 0,
    entityLabel = "نتيجة",
    onLoadMore,
    testId = "infinite-scroll-sentinel",
}) {
    const sentinelRef = useRef(null);
    const loadMoreRef = useRef(onLoadMore);
    const loadingRef = useRef(loading);
    const requestedRef = useRef(false);

    useEffect(() => {
        loadMoreRef.current = onLoadMore;
    }, [onLoadMore]);

    useEffect(() => {
        loadingRef.current = loading;
        if (!loading) requestedRef.current = false;
    }, [loading]);

    useEffect(() => {
        const node = sentinelRef.current;
        if (!node || !hasMore || typeof IntersectionObserver === "undefined") {
            return undefined;
        }

        const observer = new IntersectionObserver((entries) => {
            if (
                entries.some((entry) => entry.isIntersecting)
                && !loadingRef.current
                && !requestedRef.current
            ) {
                requestedRef.current = true;
                loadMoreRef.current?.();
            }
        }, {
            root: null,
            rootMargin: "180px 0px",
            threshold: 0.01,
        });

        observer.observe(node);
        return () => observer.disconnect();
    }, [hasMore]);

    const visibleTotal = total || loaded;
    let message = `تم عرض جميع ${visibleTotal} ${entityLabel}`;
    if (loading) message = `جاري تحميل المزيد من ${entityLabel}…`;
    else if (hasMore) message = `تم عرض ${loaded} من ${visibleTotal} ${entityLabel} · يتم تحميل المزيد تلقائيًا عند النزول`;

    return (
        <div
            ref={sentinelRef}
            className="flex min-h-12 items-center justify-center gap-2 border-t border-slate-100 bg-slate-50/70 px-4 py-3 text-center text-xs font-black text-slate-500"
            data-testid={testId}
            data-has-more={hasMore ? "true" : "false"}
            aria-live="polite"
        >
            {loading && <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-emerald-600" aria-hidden="true" />}
            <span>{message}</span>
        </div>
    );
}
