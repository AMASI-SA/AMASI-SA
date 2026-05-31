/**
 * Single source of truth for the dashboard KPI cards.
 *
 * Each entry's `id` is what gets persisted in
 * `settings.dashboard_hidden_cards` to hide that card. Adding a new card?
 * append it here; both the dashboard render loop and the settings UI pick
 * it up automatically.
 */
import {
    Coins, ShoppingBag, Receipt, Truck, Megaphone, Package,
    TrendUp, Percent, Wallet, Bank,
} from "@phosphor-icons/react";

export const KPI_GROUPS = [
    {
        id: "sales",
        title: "المبيعات والطلبات",
        cards: [
            { id: "total_sales", label: "إجمالي المبيعات", icon: Coins, accent: true, money: true, value: (t) => t.total_sales },
            { id: "net_sales", label: "صافي المبيعات", icon: TrendUp, accent: true, money: true, hint: "حسب إعدادات الخصم", value: (t) => t.net_sales },
            { id: "total_orders", label: "إجمالي الطلبات", icon: ShoppingBag, isInt: true, value: (t) => t.total_orders },
            { id: "expected_salla_transfer", label: "المتوقع من سلة", icon: Bank, accent: true, money: true, hint: "حوالة سلة المتوقعة", value: (t) => t.expected_salla_transfer },
        ],
    },
    {
        id: "payments",
        title: "رسوم بوابات الدفع",
        cards: [
            { id: "other_payment_fees", label: "رسوم بوابات الدفع", icon: Receipt, money: true, hint: "عدا تمارا وتابي وإمكان", value: (t) => t.other_payment_fees },
            { id: "electronic_net", label: "صافي المدفوعات الإلكترونية", icon: Wallet, money: true, hint: "المبيعات − العمولات", value: (t) => t.electronic_net },
            { id: "tamara_fees", label: "رسوم تمارا", icon: Receipt, money: true, hint: "BNPL", value: (t) => t.tamara_fees },
            { id: "tabby_fees", label: "رسوم تابي", icon: Receipt, money: true, hint: "BNPL", value: (t) => t.tabby_fees },
            { id: "emkan_fees", label: "رسوم إمكان", icon: Receipt, money: true, hint: "BNPL", value: (t) => t.emkan_fees },
            { id: "bnpl_net", label: "صافي تمارا وتابي وإمكان", icon: Wallet, money: true, hint: "بعد خصم العمولات", value: (t) => t.bnpl_net },
        ],
    },
    {
        id: "shipping",
        title: "الشحن",
        cards: [
            { id: "total_shipping_cost", label: "تكاليف الشحن", icon: Truck, money: true, value: (t) => t.total_shipping_cost },
            { id: "deferred_shipping_cost", label: "مستحقات الشحن الآجل", icon: Truck, money: true, hint: "ذمم للشركات الآجلة", value: (t) => t.deferred_shipping_cost },
            { id: "shipping_approved", label: "رصيد شحن معتمد", icon: Truck, money: true, hint: "طلبات (تم التوصيل)", value: (t) => t.shipping_approved },
            { id: "shipping_unapproved", label: "رصيد شحن غير معتمد", icon: Truck, money: true, hint: "طلبات قيد التنفيذ/الشحن", value: (t) => t.shipping_unapproved },
        ],
    },
    {
        id: "cod",
        title: "الدفع عند الاستلام (COD)",
        cards: [
            { id: "cod_approved", label: "COD معتمد", icon: Wallet, accent: true, money: true, hint: "مستحق على شركة الشحن", value: (t) => t.cod_approved },
            { id: "cod_unapproved", label: "COD غير معتمد", icon: Wallet, money: true, hint: "لم يصل بعد", value: (t) => t.cod_unapproved },
        ],
    },
    {
        id: "costs",
        title: "المصاريف والضريبة",
        cards: [
            { id: "total_vat", label: "إجمالي الضريبة المخصومة", icon: Percent, money: true, hint: "ضريبة الدفع + الشحن", value: (t) => t.total_vat },
            { id: "total_ads_cost", label: "تكاليف الإعلانات", icon: Megaphone, money: true, value: (t) => t.total_ads_cost },
            { id: "total_product_cost", label: "تكاليف المنتجات", icon: Package, money: true, hint: "من ملفات Excel", value: (t) => t.total_product_cost },
            { id: "daily_expenses_total", label: "مصاريف يومية", icon: Receipt, money: true, hint: "من سجل التكاليف", value: (t) => t.daily_expenses_total },
            { id: "operating_expenses_total", label: "المصروفات التشغيلية", icon: Receipt, money: true, hint: "رواتب + إيجارات + مدفوعة مقدماً + يومية أخرى", value: (t) => t.operating_expenses_total },
            { id: "operating_salaries_total", label: "إجمالي الرواتب", icon: Coins, money: true, hint: "موظفين + بيت + صدقات", value: (t) => t.operating_salaries_total },
            { id: "operating_rentals_total", label: "إيجارات الفترة", icon: Bank, money: true, hint: "بالتوزيع اليومي", value: (t) => t.operating_rentals_total },
            { id: "operating_prepaid_total", label: "المدفوعة مقدماً (تأمين/إقامات)", icon: Receipt, money: true, hint: "بالتوزيع اليومي على فترة الانتفاع", value: (t) => t.operating_prepaid_total },
            { id: "net_profit", label: "صافي الربح النهائي", icon: TrendUp, accent: true, money: true, hint: "بعد التكاليف اليومية والمصروفات التشغيلية", value: (t) => t.net_profit },
        ],
    },
];

/** Flat list of all KPI cards (preserves group order). */
export const ALL_KPI_CARDS = KPI_GROUPS.flatMap((g) => g.cards.map((c) => ({ ...c, groupId: g.id, groupTitle: g.title })));

/** Look up a card by id (returns undefined if removed). */
export function findKpi(id) {
    return ALL_KPI_CARDS.find((c) => c.id === id);
}
