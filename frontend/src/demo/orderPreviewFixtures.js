const STANDARD_STATUSES = [
  "بإنتظار الدفع",
  "بإنتظار المراجعة",
  "تم المراجعة",
  "بإنتظار تأكيد العميل",
  "بإنتظار مراجعة العميل",
  "مراجعة الملاحظات",
  "قيد التنفيذ",
  "مدمج",
  "جاري التنفيذ",
  "قيد التنفيذ لطلبات الرياض",
  "تم التجهيز",
  "مسند إلى مندوب التوصيل",
  "جاري التوصيل",
  "تم التوصيل",
  "تم الشحن",
  "تم التنفيذ",
  "ملغي",
  "محذوف",
  "مسترجع",
  "قيد الاسترجاع",
  "طلب عرض سعر",
];

const CUSTOMERS = [
  ["سارة محمد", "female", "الرياض", "mada"],
  ["ماجد القحطاني", "male", "جدة", "credit_card"],
  ["نورة العتيبي", "female", "الدمام", "tabby_installment"],
  ["أحمد الشهري", "male", "مكة المكرمة", "cod"],
  ["Rehab Ali", "female", "المدينة المنورة", "tamara_installment"],
  ["Riyad Zahrani", "male", "الرياض", "bank"],
];

const PRODUCTS = [
  { id: "demo-product-1", sku: "AM-DEMO-001", name: "طقم إكسسوارات تجريبي", price: 99, image_url: "", options: [{ name: "اللون", value: "ذهبي" }] },
  { id: "demo-product-2", sku: "AM-DEMO-002", name: "ساعة نسائية تجريبية", price: 149, image_url: "", options: [{ name: "المقاس", value: "موحد" }] },
  { id: "demo-product-3", sku: "AM-DEMO-003", name: "هدية تغليف تجريبية", price: 25, image_url: "", options: [{ name: "نوع التغليف", value: "هدية" }] },
];

function statusKey(label) {
  return String(label || "").replaceAll("_", " ").trim().toLowerCase();
}

function buildItems(index) {
  const count = (index % 3) + 1;
  return Array.from({ length: count }, (_, itemIndex) => {
    const product = PRODUCTS[(index + itemIndex) % PRODUCTS.length];
    const quantity = itemIndex === 0 && index % 4 === 0 ? 2 : 1;
    return {
      id: `${product.id}-${index}-${itemIndex}`,
      product_id: product.id,
      sku: product.sku,
      name: product.name,
      quantity,
      unit_price: product.price,
      total: product.price * quantity,
      image_url: product.image_url,
      options: product.options,
      is_demo: true,
    };
  });
}

export function isPreviewDemoEnvironment() {
  if (typeof window === "undefined") return false;
  const host = String(window.location.hostname || "").toLowerCase();
  return host.includes("preview.emergent") || host.includes(".preview.") || host.startsWith("preview-");
}

export const PREVIEW_DEMO_ORDERS = STANDARD_STATUSES.flatMap((status, statusIndex) =>
  Array.from({ length: statusIndex < 6 ? 3 : 1 }, (_, duplicateIndex) => {
    const index = statusIndex * 3 + duplicateIndex;
    const [name, gender, city, paymentMethod] = CUSTOMERS[index % CUSTOMERS.length];
    const items = buildItems(index);
    const subtotal = items.reduce((sum, item) => sum + Number(item.total || 0), 0);
    const shipping = index % 5 === 0 ? 0 : 25;
    const total = subtotal + shipping;
    const createdAt = new Date(Date.now() - index * 37 * 60 * 1000).toISOString();
    return {
      order_number: `DEMO-${String(index + 1).padStart(4, "0")}`,
      order_id: `preview-${index + 1}`,
      created_at: createdAt,
      updated_at: createdAt,
      status_native: status,
      status: status,
      status_exact: statusKey(status),
      is_new: index < 5,
      is_gift: index % 7 === 0,
      is_demo: true,
      demo_label: "بيانات تجريبية — Preview فقط",
      customer: {
        name,
        gender,
        mobile: `050000${String(index + 1).padStart(4, "0")}`,
        email: `demo${index + 1}@example.test`,
        avatar_url: "",
        shipping_address: { city, country: "السعودية", address_line: "عنوان تجريبي للمعاينة" },
      },
      shipping: {
        company: index % 2 ? "سمسا — تجريبي" : "iMile — تجريبي",
        cost: shipping,
        tracking_number: `DEMO-TRACK-${index + 1}`,
        address: { city, country: "السعودية", address_line: "عنوان تجريبي للمعاينة" },
      },
      payment: { method: paymentMethod, method_native: paymentMethod, status: "demo" },
      items,
      totals: { subtotal, shipping, discount: 0, tax: 0, total },
      notes: "هذا طلب تجريبي مخصص لمراجعة تصميم Preview فقط.",
      source: { channel: index % 3 === 0 ? "snapchat" : index % 3 === 1 ? "tiktok" : "google", campaign: `demo-campaign-${(index % 4) + 1}` },
    };
  })
);

export function listPreviewOrders({ limit = 15, cursor = null, statusExact = null } = {}) {
  const normalizedStatus = statusKey(statusExact);
  const filtered = normalizedStatus
    ? PREVIEW_DEMO_ORDERS.filter((order) => statusKey(order.status_native) === normalizedStatus)
    : PREVIEW_DEMO_ORDERS;
  const offset = Math.max(0, Number(cursor || 0));
  const items = filtered.slice(offset, offset + Number(limit || 15));
  const nextOffset = offset + items.length;
  return {
    items,
    nextCursor: nextOffset < filtered.length ? String(nextOffset) : null,
    skippedInvalid: 0,
  };
}

export function getPreviewOrder(orderNumber) {
  const normalized = String(orderNumber || "").trim().toLowerCase();
  return PREVIEW_DEMO_ORDERS.find((order) => String(order.order_number).toLowerCase() === normalized) || null;
}

export function getPreviewOrderSummary() {
  const statusCards = STANDARD_STATUSES.map((label) => {
    const key = statusKey(label);
    const count = PREVIEW_DEMO_ORDERS.filter((order) => statusKey(order.status_native) === key).length;
    return { key, label, count };
  });
  return {
    total: PREVIEW_DEMO_ORDERS.length,
    statusCards,
    statusCounts: Object.fromEntries([["all", PREVIEW_DEMO_ORDERS.length], ...statusCards.map((card) => [card.key, card.count])]),
  };
}
