import React from "react";

const setupPath = "/operational-balances/provider-fees";

function distinctMethods(items, status) {
    const rows = new Map();
    for (const method of items || []) {
        if (method.status !== status) continue;
        const scope = method.fee_scope;
        const key = scope ? `${scope.provider}/${scope.payment_method || ""}` : method.key;
        if (!rows.has(key)) rows.set(key, method);
    }
    return [...rows.values()];
}

export default function PaymentMethodSetupNotice({ setup }) {
    if (!setup) return null;
    const pending = distinctMethods(setup.items, "needs_commission");
    const review = distinctMethods(setup.items, "needs_review");
    if (setup.complete === true && !pending.length && !setup.pending_count && !review.length && !setup.review_count) return null;

    return <section role="alert" data-testid="payment-method-setup-notice"
        className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
        {pending.length > 0 ? <>
            <h2 className="font-bold">طرق دفع تحتاج إضافة عمولة</h2>
            <p className="mt-1">أُضيفت تلقائيًا إلى طرق الدفع في الأرصدة التشغيلية. أكمل عمولاتها لاحتساب التكاليف والأرباح.</p>
            <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-2">{pending.map(method => <li key={method.key} className="flex min-w-0 flex-wrap items-center gap-2">
                <bdi className="break-all font-bold">{method.label || method.name}</bdi>
                <a className="font-bold underline underline-offset-2" href={`${setupPath}?method=${encodeURIComponent(method.name)}`}
                    aria-label={`إضافة عمولة ${method.name}`}>إضافة العمولة</a>
            </li>)}</ul>
        </> : !review.length && <a className="font-bold underline underline-offset-2" href={setupPath}>مراجعة عمولات طرق الدفع</a>}
        {review.length > 0 && <div className={pending.length ? "mt-3" : ""}>
            <h2 className="font-bold">تعرفات دفع تحتاج مراجعة</h2>
            <p className="mt-1">توجد تعرفة تحتاج تحديد الخطة أو مراجعة شروطها. اكتمال التعرفة لا يعني اكتمال رسوم كل طلب.</p>
            <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-2">{review.map(method => <li key={method.key} className="flex items-center gap-2">
                <bdi className="font-bold">{method.label || method.name}</bdi>
                <a className="font-bold underline underline-offset-2" href={`${setupPath}?method=${encodeURIComponent(method.name)}`}>مراجعة التعرفة</a>
            </li>)}</ul>
        </div>}
        {setup.complete !== true && <p className={pending.length ? "mt-2" : "mt-1"}>تعذر التحقق من جميع طرق الدفع. أعد قراءة الصفحة للمحاولة مجددًا.</p>}
    </section>;
}
