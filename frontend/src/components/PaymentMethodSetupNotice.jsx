import React from "react";

const setupPath = "/operational-balances/provider-fees";

export default function PaymentMethodSetupNotice({ setup }) {
    if (!setup) return null;
    const pending = (setup.items || []).filter(method => method.status === "needs_commission");
    if (setup.complete === true && !pending.length && !setup.pending_count) return null;

    return <section role="alert" data-testid="payment-method-setup-notice"
        className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
        {pending.length > 0 ? <>
            <h2 className="font-bold">طرق دفع تحتاج إضافة عمولة</h2>
            <p className="mt-1">أُضيفت تلقائيًا إلى طرق الدفع في الأرصدة التشغيلية. أكمل عمولاتها لاحتساب التكاليف والأرباح.</p>
            <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-2">{pending.map(method => <li key={method.key} className="flex min-w-0 flex-wrap items-center gap-2">
                <bdi className="break-all font-bold">{method.name}</bdi>
                <a className="font-bold underline underline-offset-2" href={`${setupPath}?method=${encodeURIComponent(method.name)}`}
                    aria-label={`إضافة عمولة ${method.name}`}>إضافة العمولة</a>
            </li>)}</ul>
        </> : <a className="font-bold underline underline-offset-2" href={setupPath}>مراجعة عمولات طرق الدفع</a>}
        {setup.complete !== true && <p className={pending.length ? "mt-2" : "mt-1"}>تعذر التحقق من جميع طرق الدفع. أعد قراءة الصفحة للمحاولة مجددًا.</p>}
    </section>;
}
