import { useEffect, useMemo, useState } from "react";
import { Bell, ChevronDown, ShoppingCart, UserRound } from "lucide-react";

const PAGE_SIZE = 10;
const cartTime = (cart) => cart?.activity_at || cart?.cart_updated_at || cart?.updated_at || cart?.created_at || "";

export function cartAdditionEvents(carts = []) {
    return (Array.isArray(carts) ? carts : []).flatMap((cart) =>
        (Array.isArray(cart?.items) ? cart.items : []).map((item, index) => ({
            id: `${cart?.cart_id || "cart"}-${item?.product_id || item?.id || index}-${index}`,
            cartId: cart?.cart_id || "",
            customerName: cart?.customer_name || "زائر",
            productName: item?.name || item?.product_name || item?.title || "منتج",
            imageUrl: item?.image_url || item?.image || item?.thumbnail || "",
            quantity: Math.max(1, Number(item?.quantity || 1)),
            occurredAt: cartTime(cart),
        }))
    ).sort((a, b) => new Date(b.occurredAt || 0) - new Date(a.occurredAt || 0));
}

export function relativeCartTime(value, now = Date.now()) {
    const parsed = new Date(value).getTime();
    if (!Number.isFinite(parsed)) return "الآن";
    const seconds = Math.max(1, Math.floor((now - parsed) / 1000));
    if (seconds < 60) return `منذ ${seconds} ثانية`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `منذ ${minutes} دقيقة`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `منذ ${hours} ساعة`;
    return `منذ ${Math.floor(hours / 24)} يوم`;
}

export default function CartAdditionsNotifications() {
  return null;
}

export function FrozenCartAdditionsNotifications({ carts = [] }) {
    const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
    const [clock, setClock] = useState(() => Date.now());
    const events = useMemo(() => cartAdditionEvents(carts), [carts]);
    const visibleEvents = events.slice(0, visibleCount);

    useEffect(() => setVisibleCount(PAGE_SIZE), [carts]);
    useEffect(() => {
        const timer = window.setInterval(() => setClock(Date.now()), 30_000);
        return () => window.clearInterval(timer);
    }, []);

    return <section data-testid="cart-additions-notifications" className="overflow-hidden rounded-2xl border border-violet-200 bg-white shadow-sm">
        <div className="flex items-center justify-between bg-gradient-to-l from-violet-700 to-indigo-600 px-4 py-4 text-white">
            <div className="flex items-center gap-2"><span className="grid h-9 w-9 place-items-center rounded-xl bg-white/15"><Bell className="h-5 w-5" /></span><div><h2 className="text-base font-black">إضافات السلة</h2><p className="mt-0.5 text-[10px] font-bold text-violet-100">آخر نشاطات العملاء</p></div></div>
            <span className="rounded-full bg-white/15 px-3 py-1 text-[10px] font-extrabold">{events.length} إضافة</span>
        </div>
        {visibleEvents.length ? <div className="max-h-[360px] overflow-y-auto overscroll-contain" data-testid="cart-additions-scroll">
            {visibleEvents.map((event) => <article key={event.id} className="flex min-h-[88px] items-center gap-3 border-b border-slate-100 px-4 py-3 last:border-b-0">
                <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-violet-50 text-violet-700"><UserRound className="h-6 w-6" /></span>
                <div className="min-w-0 flex-1"><p className="text-xs font-bold leading-5 text-slate-700"><span className="font-black text-slate-900">{event.customerName}</span>{" أضاف "}<span className="font-black text-violet-700">{event.productName}</span>{event.quantity > 1 ? ` × ${event.quantity}` : ""}{" للسلة"}</p><div className="mt-1 flex items-center gap-2 text-[10px] font-bold text-slate-400"><span>{relativeCartTime(event.occurredAt, clock)}</span>{event.cartId ? <><span>•</span><span>سلة #{event.cartId}</span></> : null}</div></div>
                {event.imageUrl ? <img src={event.imageUrl} alt={event.productName} className="h-12 w-12 shrink-0 rounded-xl object-cover" loading="lazy" /> : <span className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-slate-50 text-slate-300"><ShoppingCart className="h-5 w-5" /></span>}
            </article>)}
        </div> : <div className="p-8 text-center text-xs font-bold text-slate-400">لا توجد إضافات للسلة في الفترة المحددة.</div>}
        {visibleCount < events.length && <div className="border-t border-violet-100 p-3"><button type="button" onClick={() => setVisibleCount((count) => Math.min(count + PAGE_SIZE, events.length))} className="flex w-full items-center justify-center gap-2 rounded-xl bg-violet-50 px-4 py-2 text-xs font-extrabold text-violet-800 hover:bg-violet-100"><ChevronDown className="h-4 w-4" />المزيد</button></div>}
    </section>;
}
