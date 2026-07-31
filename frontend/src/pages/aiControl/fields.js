export const AI_ORDER_FIELDS = [
    {
        id: "order_number",
        label: "رقم الطلب",
        paths: ["order_number", "order_id"],
        required: true,
        why: "المطابقة بين سلة وميزان وقيود والتقارير.",
    },
    {
        id: "order_date",
        label: "تاريخ الطلب",
        paths: ["created_at"],
        required: true,
        why: "منع فروقات اليوم والمنطقة الزمنية.",
    },
    {
        id: "order_status",
        label: "حالة الطلب",
        paths: ["status", "status_native"],
        required: true,
        why: "تحديد هل الطلب يدخل المحاسبة أو يبقى غير مرحل.",
    },
    {
        id: "payment_method",
        label: "طريقة الدفع",
        paths: ["payment.method", "payment.method_native"],
        required: true,
        why: "فصل تمارا وتابي وسلة وCOD والتحويل البنكي.",
    },
    {
        id: "total_amount",
        label: "إجمالي الطلب",
        paths: ["totals.total"],
        required: true,
        why: "كشف أي فرق مالي حتى 0.01 ريال.",
    },
    {
        id: "products",
        label: "سطور المنتجات",
        paths: ["items"],
        required: true,
        why: "التكلفة والربح الحقيقي وربط SKU مع قيود.",
    },
    {
        id: "shipping",
        label: "الشحن",
        paths: ["shipping.company", "shipping.method", "totals.shipping"],
        required: true,
        why: "حساب تكلفة الشحن وذمم الدفع عند الاستلام.",
    },
    {
        id: "ad_link",
        label: "ربط الإعلان بالطلب",
        fragments: ["utm", "campaign", "ad_id", "adset", "ad_squad", "click"],
        required: false,
        why: "قرار توسيع أو إيقاف الحملات الإعلانية.",
    },
];

export const AI_DATA_CONTRACT = [
    {
        title: "طلبات ميزان 2",
        items: [
            "Orders V2 canonical order + operational order items",
            "OpenAI discovers commercial concepts from observed paths and values",
            "No hard-coded tax, discount, shipping, or total field names are used as AI evidence",
            "Customer names, phones, emails, addresses, references, and personalized option values are removed",
        ],
    },
    {
        title: "سلة / الطلبات",
        items: [
            "order_number, created_at, updated_at, status history",
            "payment method and collection state without private transaction references",
            "line items: product_id, sku, qty, unit_price, discount, source tax, total",
            "shipping company, shipping cost, COD amount, COD fee",
            "refunds: full or partial, refunded amount, refunded at",
        ],
    },
    {
        title: "الإعلانات",
        items: [
            "campaign id/name/status/budget",
            "ad squad or adset id/name/targeting",
            "ad id/name/creative/link/status",
            "daily or hourly spend, impressions, clicks or swipes, reach, frequency",
            "purchases, purchase value, add to cart, checkout, landing page views",
            "UTM or click id stored on each order when available",
        ],
    },
    {
        title: "قيود",
        items: [
            "invoice id, invoice number, invoice status, invoice total",
            "receipt id, receipt number, paid status",
            "request body and response body for each stage",
            "blocking reason when customer, product, invoice, or payment fails",
            "tax policy and any 0.01 SAR difference are verified by deterministic Mezan engines",
        ],
    },
    {
        title: "دفتر الأستاذ GL",
        items: [
            "txn_group_id linking order, invoice, payment, and ledger rows",
            "debit and credit balanced check for every entry",
            "source type and source id for every ledger row",
            "reversal linkage for refunds and corrections",
        ],
    },
];
