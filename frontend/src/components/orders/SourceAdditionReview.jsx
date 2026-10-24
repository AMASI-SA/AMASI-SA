export function SourceAdditionReviewNotice({ review }) {
    if (!review.enabled || !review.error) return null;
    return <div role="alert" className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
        <p>{review.error}</p>
        <button type="button" onClick={review.reload} disabled={review.loading || Boolean(review.pending)} className="mt-2 font-bold underline">تحديث حالة المنتجات المضافة</button>
    </div>;
}

export function SourceAdditionReviewButton({ itemId, review }) {
    const row = review.items.find((entry) => entry.order_item_id === itemId);
    if (!review.enabled || !row) return null;
    const notice = row.addition_notice && <p className="mt-2 text-xs text-slate-500">{row.addition_notice}</p>;
    if (row.reviewed) return <>{notice}<div role="status" className="mt-3 text-sm font-bold text-emerald-700">تمت مراجعة المنتج المضاف</div></>;
    if (row.blocked_reason === "source_addition_order_stage_ineligible") return <>{notice}<p role="status" className="mt-2 text-xs text-amber-800">
        {row.blocked_message || "حالة الطلب الحالية لا تسمح باعتماد المنتج المضاف. الاعتماد متاح في تمت المراجعة وقيد التنفيذ فقط."}
    </p></>;
    return <div className="mt-3">
        {notice}
        <button type="button" onClick={() => review.approve(itemId)}
            disabled={!row.eligible || review.loading || Boolean(review.pending) || Boolean(review.error)}
            className="rounded-lg bg-violet-700 px-4 py-2 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-50">
            {review.pending === itemId ? "جاري الاعتماد…" : "تمت المراجعة"}
        </button>
        {!row.eligible && <p className="mt-2 text-xs text-amber-800">المنتج المضاف غير جاهز للاعتماد. حدّث الطلب وتحقق من بياناته.</p>}
    </div>;
}
