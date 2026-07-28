"""One-shot patch for ten-order cursor pagination in the review queue."""
from pathlib import Path


ORDER_REVIEW_PATH = Path("frontend/src/pages/OrderReview.jsx")
CONTRACT_PATH = Path("backend/tests/test_fulfillment_v2_contract.py")


text = ORDER_REVIEW_PATH.read_text(encoding="utf-8")
old_import = """    ArrowLeft, CheckCircle, Clipboard, Eye, EyeSlash, FloppyDisk,
    MagnifyingGlass, SpinnerGap, WarningCircle, WhatsappLogo, X,"""
new_import = """    ArrowLeft, CaretLeft, CaretRight, CheckCircle, Clipboard, Eye, EyeSlash,
    FloppyDisk, MagnifyingGlass, SpinnerGap, WarningCircle, WhatsappLogo, X,"""
if text.count(old_import) != 1:
    raise SystemExit("OrderReview icon import marker was not found exactly once")
text = text.replace(old_import, new_import, 1)

component_marker = "export default function OrderReview() {"
if text.count(component_marker) != 1:
    raise SystemExit("OrderReview component marker was not found exactly once")
prefix = text.split(component_marker, 1)[0]
component = r'''export const REVIEW_PAGE_SIZE = 10;

export default function OrderReview() {
    const [orders, setOrders] = useState([]);
    const [currentCursor, setCurrentCursor] = useState(null);
    const [previousCursors, setPreviousCursors] = useState([]);
    const [nextCursor, setNextCursor] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [selectedOrder, setSelectedOrder] = useState(null);
    const [search, setSearch] = useState("");

    const pageNumber = previousCursors.length + 1;
    const hasPreviousPage = previousCursors.length > 0;

    const load = useCallback(async ({ cursor = null, background = false } = {}) => {
        if (!background) {
            setLoading(true);
            setError("");
        }
        try {
            const result = await listPendingOrderReviews({ limit: REVIEW_PAGE_SIZE, cursor });
            setOrders(result.items);
            setNextCursor(result.nextCursor);
        } catch (loadError) {
            if (!background) setError(loadError.message);
        } finally {
            if (!background) setLoading(false);
        }
    }, []);

    useEffect(() => {
        load({ cursor: currentCursor });
    }, [currentCursor, load]);

    useEffect(() => {
        const refresh = () => {
            if (document.hidden || !navigator.onLine) return;
            load({ cursor: currentCursor, background: true });
        };
        const intervalId = window.setInterval(refresh, 10_000);
        window.addEventListener("focus", refresh);
        window.addEventListener("online", refresh);
        document.addEventListener("visibilitychange", refresh);
        return () => {
            window.clearInterval(intervalId);
            window.removeEventListener("focus", refresh);
            window.removeEventListener("online", refresh);
            document.removeEventListener("visibilitychange", refresh);
        };
    }, [currentCursor, load]);

    const goToPreviousPage = () => {
        if (!hasPreviousPage || loading) return;
        const previousCursor = previousCursors[previousCursors.length - 1] ?? null;
        setPreviousCursors((history) => history.slice(0, -1));
        setCurrentCursor(previousCursor);
        setSearch("");
    };

    const goToNextPage = () => {
        if (!nextCursor || loading) return;
        setPreviousCursors((history) => [...history, currentCursor]);
        setCurrentCursor(nextCursor);
        setSearch("");
    };

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        if (!q) return orders;
        return orders.filter((order) => [order.order_number, order.customer?.name, order.customer?.mobile, paymentText(order)].some((value) => String(value || "").toLowerCase().includes(q)));
    }, [orders, search]);

    return (
        <div className="mx-auto max-w-7xl space-y-5 p-4" dir="rtl">
            <header className="rounded-2xl border bg-white p-5 shadow-sm">
                <h1 className="text-2xl font-extrabold text-slate-900">طلبات بانتظار المراجعة</h1>
                <p className="mt-1 text-sm text-slate-500">المرحلة الأولى من محرك تجهيز الطلب — مراجعة بيانات العميل والدفع والشحن والمنتجات.</p>
                <p className="mt-1 text-xs font-semibold text-violet-700">يعرض الجدول آخر 10 طلبات في كل صفحة، واستخدم الأسهم للانتقال بين الصفحات.</p>
                <div className="relative mt-4 max-w-xl">
                    <MagnifyingGlass className="absolute right-3 top-3 text-slate-400" />
                    <input value={search} onChange={(event) => setSearch(event.target.value)} className="w-full rounded-xl border py-2.5 pr-10 pl-3 outline-none focus:border-violet-500" placeholder="ابحث في الصفحة الحالية برقم الطلب أو العميل أو طريقة الدفع" />
                </div>
            </header>

            <section className="overflow-hidden rounded-2xl border bg-white shadow-sm">
                <div className="hidden grid-cols-[70px_1fr_1fr_1fr_1fr] gap-3 border-b bg-slate-100 px-4 py-3 text-sm font-extrabold text-slate-600 md:grid">
                    <div>تفاصيل</div><div>رقم الطلب</div><div>تاريخ الطلب</div><div>طريقة الدفع</div><div>العميل</div>
                </div>
                {loading ? (
                    <div className="flex min-h-64 items-center justify-center"><SpinnerGap size={32} className="animate-spin text-violet-600" /></div>
                ) : error ? (
                    <div className="m-5 rounded-xl border border-rose-200 bg-rose-50 p-4 text-rose-800"><WarningCircle className="ml-2 inline" />{error}</div>
                ) : filtered.length === 0 ? (
                    <div className="flex min-h-64 items-center justify-center text-slate-500">{search.trim() ? "لا توجد نتائج في هذه الصفحة" : "لا توجد طلبات بانتظار المراجعة"}</div>
                ) : filtered.map((order) => (
                    <button key={order.order_number} type="button" onClick={() => setSelectedOrder(order.order_number)} className={`grid w-full gap-2 border-b px-4 py-4 text-right last:border-b-0 md:grid-cols-[70px_1fr_1fr_1fr_1fr] md:items-center ${rowTone(order)}`}>
                        <span className="inline-flex items-center gap-1 font-bold text-violet-700"><ArrowLeft /> <span className="md:hidden">التفاصيل</span></span>
                        <span className="font-extrabold" dir="ltr">#{order.order_number}</span>
                        <span className="text-sm text-slate-600">{new Date(order.created_at).toLocaleString("ar-SA")}</span>
                        <span className="font-bold">{paymentText(order)}</span>
                        <span>{order.customer?.name || "عميل بدون اسم"}</span>
                    </button>
                ))}

                {!loading && !error && (orders.length > 0 || hasPreviousPage) && (
                    <div className="flex flex-col gap-3 border-t border-slate-200 bg-slate-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="text-xs font-bold text-slate-500">10 طلبات كحد أقصى في الصفحة</div>
                        <div className="flex items-center justify-center gap-2" aria-label="التنقل بين صفحات الطلبات">
                            <button
                                type="button"
                                onClick={goToPreviousPage}
                                disabled={!hasPreviousPage || loading}
                                aria-label="الصفحة السابقة"
                                className="inline-flex h-10 items-center gap-1 rounded-xl border border-slate-200 bg-white px-3 text-sm font-extrabold text-slate-700 transition hover:border-violet-300 hover:text-violet-700 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                                <CaretRight size={18} weight="bold" />
                                السابق
                            </button>
                            <span className="min-w-24 rounded-xl border border-violet-200 bg-violet-50 px-4 py-2 text-center text-sm font-extrabold text-violet-800">
                                الصفحة {pageNumber}
                            </span>
                            <button
                                type="button"
                                onClick={goToNextPage}
                                disabled={!nextCursor || loading}
                                aria-label="الصفحة التالية"
                                className="inline-flex h-10 items-center gap-1 rounded-xl border border-slate-200 bg-white px-3 text-sm font-extrabold text-slate-700 transition hover:border-violet-300 hover:text-violet-700 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                                التالي
                                <CaretLeft size={18} weight="bold" />
                            </button>
                        </div>
                    </div>
                )}
            </section>
            <div className="rounded-xl border border-slate-200 bg-white p-3 text-sm text-slate-600">
                <b>دليل الألوان:</b> أحمر للدفع عند الاستلام، أصفر للتحويل البنكي، وأبيض لبقية طرق الدفع.
            </div>
            {selectedOrder && (
                <ReviewDrawer
                    orderNumber={selectedOrder}
                    onClose={() => setSelectedOrder(null)}
                    onCompleted={(orderNumber) => {
                        setOrders((current) => current.filter((order) => order.order_number !== orderNumber));
                        setSelectedOrder(null);
                        load({ cursor: currentCursor, background: true });
                    }}
                />
            )}
        </div>
    );
}
'''
ORDER_REVIEW_PATH.write_text(prefix + component, encoding="utf-8")

contract = CONTRACT_PATH.read_text(encoding="utf-8")
test_name = "test_pending_review_table_uses_ten_row_cursor_pages_with_arrow_navigation"
if test_name not in contract:
    contract += r'''


def test_pending_review_table_uses_ten_row_cursor_pages_with_arrow_navigation():
    source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")

    assert "export const REVIEW_PAGE_SIZE = 10;" in source
    assert "limit: REVIEW_PAGE_SIZE" in source
    assert "const [currentCursor, setCurrentCursor]" in source
    assert "const [previousCursors, setPreviousCursors]" in source
    assert 'aria-label="الصفحة السابقة"' in source
    assert 'aria-label="الصفحة التالية"' in source
    assert "الصفحة {pageNumber}" in source
    assert "تحميل طلبات إضافية" not in source
    assert "append: true" not in source
'''
    CONTRACT_PATH.write_text(contract, encoding="utf-8")

print("Order review pagination patch applied.")
