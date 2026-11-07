import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const money = value => typeof value === "number" && Number.isFinite(value)
    ? `${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س` : "غير متاح";
const reasonLabel = code => code.includes("option") || code.includes("selection") ? "اختيار عميل غير متحقق"
    : code === "shipping_cost_missing" ? "تكلفة الشحن ناقصة"
    : code === "payment_fees_missing" ? "عمولة الدفع غير محددة"
    : code === "order_currency_unverified" ? "تحويل عملة الطلب غير متحقق"
    : code === "refund_cost_reconciliation_required" ? "الطلب ملغى أو مسترجع ويحتاج تسوية تكاليفه"
    : "تكلفة منتج أو خدمة أو مكوّن غير مكتملة";

export default function OrderProfitabilityCard({ orderNumber, revision }) {
    const container = useRef(null);
    const [visible, setVisible] = useState(false);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [attempt, setAttempt] = useState(0);
    useEffect(() => {
        if (typeof IntersectionObserver === "undefined") { setVisible(true); return; }
        const observer = new IntersectionObserver(entries => {
            if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
        }, { rootMargin: "250px" });
        observer.observe(container.current);
        return () => observer.disconnect();
    }, []);
    useEffect(() => {
        setData(null); setError("");
        if (!visible || !orderNumber) return;
        let current = true;
        const controller = new AbortController();
        setLoading(true);
        api.get(`/orders-v2/${encodeURIComponent(orderNumber)}/profitability`, { signal: controller.signal, timeout: 20000 })
            .then(response => { if (current) setData(response.data); })
            .catch(() => { if (current) setError("تعذّر تحميل التكاليف. يمكنك إعادة المحاولة."); })
            .finally(() => { if (current) setLoading(false); });
        return () => { current = false; controller.abort(); };
    }, [orderNumber, revision, visible, attempt]);
    const hasAds = typeof data?.advertising_cost_sar === "number" && Number.isFinite(data.advertising_cost_sar);
    const total = hasAds ? data?.total_cost_sar : data?.direct_cost_sar;
    const profit = hasAds ? data?.profit_sar : data?.profit_before_advertising_sar;
    return <section ref={container} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="order-v2-accounting-summary">
        <div className="mb-4 flex items-center justify-between gap-3">
            <h2 className="text-xl font-extrabold text-slate-950">المحاسبة والربحية</h2>
            <button type="button" className="text-sm font-bold text-violet-700" disabled={loading} onClick={() => { setVisible(true); setAttempt(value => value + 1); }}>تحديث التكاليف</button>
        </div>
        {loading ? <p role="status">جارٍ حساب تكلفة الطلب…</p> : error ? <p role="alert" className="text-amber-700">{error}</p> : data ? <>
            <div className="grid gap-4 sm:grid-cols-2">
                <div className="rounded-xl bg-slate-50 p-4"><div className="font-bold text-slate-500">{hasAds ? "تكلفة الطلب مع الترويج" : "تكلفة الطلب قبل الترويج"}</div>
                    <div className="num mt-2 text-lg font-extrabold">{total == null ? "غير مكتملة" : money(total)}</div>
                    {total == null && <div className="mt-1 text-sm text-amber-700">المعروف {money(hasAds ? data.known_total_cost_sar : data.known_direct_cost_sar)} — الإجمالي غير مكتمل</div>}
                </div>
                <div className="rounded-xl bg-slate-50 p-4"><div className="font-bold text-slate-500">{hasAds ? "الربح التقديري بعد الترويج" : "الربح التقديري قبل الترويج"}</div>
                    <div className={`num mt-2 text-lg font-extrabold ${profit == null ? "text-amber-700" : profit < 0 ? "text-rose-700" : "text-emerald-700"}`}>{profit == null ? "غير مكتمل" : money(profit)}</div>
                </div>
            </div>
            <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                <div><dt>المنتجات والخدمات والمكونات</dt><dd className="num font-bold">{data.product_cost_sar == null ? `غير مكتملة — المعروف ${money(data.known_product_cost_sar)}` : money(data.product_cost_sar)}</dd></div>
                <div><dt>تكلفة الشحن</dt><dd className="num font-bold">{money(data.shipping_cost_sar)}</dd></div>
                <div><dt>عمولات الدفع</dt><dd className="num font-bold">{money(data.payment_fees_sar)}</dd></div>
                <div><dt>تكلفة الترويج الموزعة على الطلب</dt><dd className="num font-bold">{money(data.advertising_cost_sar)}</dd></div>
            </dl>
            <p className="mt-4 text-xs text-slate-500">جميع المبالغ بالريال السعودي. هامش تقديري من قيمة الطلب شامل الضريبة، قبل الرواتب والإيجارات والمصروفات العامة. {hasAds ? "الترويج توزيع محفوظ على الطلب، وليس تكلفة شراء فردية مؤكدة من المنصة." : "لم تتوفر تكلفة ترويج موثقة لهذا الطلب؛ لم تُفترض صفرًا."}</p>
            {data.reasons?.length > 0 && <p role="status" className="mt-2 text-sm text-amber-700">{[...new Set(data.reasons.map(reasonLabel))].join(" · ")}</p>}
        </> : <p className="text-sm text-slate-500">تُحمّل التكاليف عند الوصول إلى البطاقة.</p>}
    </section>;
}
