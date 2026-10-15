export const AI_ORDER_FIELDS = [
    {
        id: "order_number",
        label: "رقم الطلب",
        paths: ["order_number", "reference_id", "order_id"],
        required: true,
        why: "المطابقة بين سلة وميزان وقيود والتقارير.",
    },
    {
        id: "order_date",
        label: "تاريخ الطلب",
        paths: ["order_date", "created_at", "date"],
        required: true,
        why: "منع فروقات اليوم والمنطقة الزمنية.",
    },
    {
        id: "order_status",
        label: "حالة الطلب",
        paths: ["order_status", "status", "status.name"],
        required: true,
        why: "تحديد هل الطلب يدخل المحاسبة أو يبقى غير مرحل.",
    },
    {
        id: "payment_method",
        label: "طريقة الدفع",
        paths: ["payment_method", "payment.method", "payment_method_name"],
        required: true,
        why: "فصل تمارا وتابي وسلة وCOD والتحويل البنكي.",
    },
    {
        id: "total_amount",
        label: "إجمالي الطلب",
        paths: ["total_amount", "amount", "total", "summary.total"],
        required: true,
        why: "كشف أي فرق مالي حتى 0.01 ريال.",
    },
    {
        id: "products",
        label: "سطور المنتجات",
        paths: ["products", "items", "line_items", "order_items"],
        required: true,
        why: "التكلفة والربح الحقيقي وربط SKU مع قيود.",
    },
    {
        id: "shipping",
        label: "الشحن",
        paths: ["shipping_company", "shipping_cost", "shipping.company"],
        required: true,
        why: "حساب تكلفة الشحن وذمم الدفع عند الاستلام.",
    },
    {
        id: "tax",
        label: "الضريبة",
        paths: ["vat_amount", "tax_amount", "total_vat", "amounts.tax.amount"],
        required: true,
        why: "إثبات سياسة ضريبة ميزان الثابتة 15%.",
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
        title: "سلة / الطلبات",
        items: [
            "order_number, order_date, updated_at, status history",
            "payment_method + transaction references",
            "line items: product_id, sku, qty, unit_price, discount, tax, total",
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
            "tax mode: Mezan fixed 15%; any 0.01 SAR difference is a blocker",
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
