import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const OK = "ok";
const WARN = "warn";
const BLOCK = "block";

const AI_ORDER_FIELDS = [
    {
        id: "order_number",
        label: "رقم الطلب",
        paths: ["order_number", "reference_id", "order_id", "number"],
        required: true,
        why: "ضروري للمطابقة بين سلة وميزان وقيود ومنع التكرار.",
    },
    {
        id: "order_date",
        label: "تاريخ الطلب",
        paths: ["order_date", "created_at", "date", "salla_order_created_at"],
        required: true,
        why: "ضروري لمنع فروقات اليوم والمنطقة الزمنية والتقارير الشهرية.",
    },
    {
        id: "order_status",
        label: "حالة الطلب",
        paths: ["order_status", "status", "status.name", "order_status_native"],
        required: true,
        why: "ضروري لتحديد هل الطلب يدخل المحاسبة أو يبقى غير مرحل.",
    },
    {
        id: "payment_method",
        label: "طريقة الدفع",
        paths: ["payment_method", "payment.method", "payment_method_name", "payment_method_native"],
        required: true,
        why: "ضروري لفصل تمارا وتابي وسلة وCOD والتحويل البنكي.",
    },
    {
        id: "total_amount",
        label: "إجمالي الطلب",
        paths: ["total_amount", "amount", "total", "summary.total", "totals.order_total"],
        required: true,
        why: "أي فرق حتى 0.01 ريال يعتبر مانع محاسبي قبل قيود/ZATCA.",
    },
    {
        id: "products",
        label: "سطور المنتجات",
        paths: ["products", "items", "line_items", "order_items"],
        required: true,
        why: "ضرورية للتكلفة والربح الحقيقي وربط SKU مع قيود.",
    },
    {
        id: "shipping",
        label: "الشحن",
        paths: ["shipping_company", "shipping_cost", "shipping.company", "totals.shipping_ex_tax"],
        required: true,
        why: "ضروري لحساب تكلفة الشحن وذمم الدفع عند الاستلام.",
    },
    {
        id: "tax",
        label: "الضريبة",
        paths: ["vat_amount", "tax_amount", "total_vat", "amounts.tax.amount", "totals.tax_amount"],
        required: true,
        why: "ميزان يجب أن يثبت ضريبة 15% كمصدر داخلي لا يعتمد على سلة أو قيود.",
    },
    {
        id: "ad_link",
        label: "ربط الإعلان بالطلب",
        fragments: ["utm", "campaign", "ad_id", "adset", "ad_squad", "click"],
        required: false,
        why: "ضروري لاتخاذ قرار توسيع أو إيقاف الحملات الإعلانية.",
    },
];

const AI_DATA_CONTRACT = [
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
            "ad set / ad squad id/name/targeting",
            "ad id/name/creative/link/status",
            "daily spend, impressions, clicks, reach, frequency",
            "purchases, purchase value, add to cart, checkout, landing page views",
            "UTM or click id stored on each order when available",
        ],
    },
    {
        title: "قيود",
        items: [
            "invoice id, invoice number, invoice status, invoice total",
            "invoice_payment id, paid status, request body, response body",
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

function todayIso() {
    return new Date().toISOString().slice(0, 10);
}

function addDaysIso(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
}

function buildQuery(params = {}) {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && String(value).trim() !== "") {
            q.set(key, value);
        }
    });
    const s = q.toString();
    return s ? `?${s}` : "";
}

function fmtInt(n) {
    return (Number(n) || 0).toLocaleString("en-US");
}

function fmtMoney(n) {
    return `${(Number(n) || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ر.س`;
}

function fmtPct(n) {
    return `${(Number(n) || 0).toLocaleString("en-US", {
        maximumFractionDigits: 1,
    })}%`;
}

function hasValue(value) {
    if (value === null || value === undefined) return false;
    if (typeof value === "string") return value.trim() !== "" && value.trim() !== "\\N";
    if (typeof value === "number") return Number.isFinite(value);
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === "object") return Object.keys(value).length > 0;
    return Boolean(value);
}

function getPath(obj, path) {
    const parts = String(path || "").split(".").filter(Boolean);
    let current = obj;

    for (const part of parts) {
        if (current === null || current === undefined) return undefined;
        current = current[part];
    }

    return current;
}

function getAny(obj, paths = []) {
    for (const path of paths) {
        const value = getPath(obj, path);
        if (hasValue(value)) return value;
    }
    return undefined;
}

function deepHasKeyFragment(obj, fragments = [], depth = 0) {
    if (!obj || typeof obj !== "object" || depth > 5) return false;

    for (const [key, value] of Object.entries(obj)) {
        const lowered = String(key).toLowerCase();

        if (fragments.some((fragment) => lowered.includes(fragment)) && hasValue(value)) {
            return true;
        }

        if (value && typeof value === "object" && deepHasKeyFragment(value, fragments, depth + 1)) {
            return true;
        }
    }

    return false;
}

function toneClass(status) {
    if (status === BLOCK) return "bg-red-50 border-red-300 text-red-800";
    if (status === WARN) return "bg-amber-50 border-amber-300 text-amber-800";
    return "bg-emerald-50 border-emerald-300 text-emerald-800";
}

function statusLabel(status) {
    if (status === BLOCK) return "⛔ مانع";
    if (status === WARN) return "⚠️ ناقص";
    return "✅ جاهز";
}

function extractOrders(payload) {
    if (!payload) return [];
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload.items)) return payload.items;
    if (Array.isArray(payload.orders)) return payload.orders;
    if (Array.isArray(payload.data)) return payload.data;
    if (payload.data && Array.isArray(payload.data.items)) return payload.data.items;
    if (payload.data && Array.isArray(payload.data.orders)) return payload.data.orders;
    return [];
}

function extractTotalOrders(payload, fallback) {
    if (!payload) return fallback;
    return (
        payload.total ||
        payload.total_count ||
        payload.count ||
        payload.summary?.total_orders ||
        payload.totals?.orders_count ||
        fallback
    );
}

function fieldCoverage(items, field) {
    const total = items.length;

    if (!total) {
        return {
            ...field,
            present: 0,
            total: 0,
            pct: 0,
            status: field.required ? BLOCK : WARN,
        };
    }

    const present = items.reduce((count, item) => {
        const ok = field.fragments
            ? deepHasKeyFragment(item, field.fragments)
            : hasValue(getAny(item, field.paths || []));

        return count + (ok ? 1 : 0);
    }, 0);

    const pct = (present / total) * 100;
    const status = field.required && pct < 95 ? BLOCK : pct < 80 ? WARN : OK;

    return { ...field, present, total, pct, status };
}

function Kpi({ title, value, hint }) {
    return (
        <div className="rounded-xl border bg-white p-4 shadow-sm">
            <p className="text-xs text-slate-600 font-bold">{title}</p>
            <div className="text-2xl font-extrabold mt-1 num">{value}</div>
            {hint && <p className="text-xs text-slate-500 mt-2 leading-5">{hint}</p>}
        </div>
    );
}

function Tab({ active, onClick, children }) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={`px-4 py-2 rounded-lg text-sm font-bold border ${
                active
                    ? "bg-slate-900 text-white border-slate-900"
                    : "bg-white text-slate-700 border-slate-200 hover:bg-slate-50"
            }`}
        >
            {children}
        </button>
    );
}

function Gate({ gate }) {
    return (
        <div className={`rounded-xl border p-4 ${toneClass(gate.status)}`}>
            <div className="flex justify-between gap-2">
                <h3 className="font-extrabold">{gate.title}</h3>
                <span className="text-xs font-bold whitespace-nowrap">{statusLabel(gate.status)}</span>
            </div>
            <p className="text-sm mt-2 leading-6">{gate.text}</p>
        </div>
    );
}

function CoverageTable({ rows }) {
    return (
        <div className="overflow-x-auto rounded-xl border bg-white shadow-sm">
            <table className="min-w-full text-sm">
                <thead className="bg-slate-50 text-slate-600">
                    <tr>
                        <th className="p-3 text-right">البيان</th>
                        <th className="p-3 text-right">التغطية</th>
                        <th className="p-3 text-right">الحالة</th>
                        <th className="p-3 text-right">لماذا يحتاجه AI؟</th>
                    </tr>
                </thead>

                <tbody>
                    {rows.map((row) => (
                        <tr key={row.id} className="border-t">
                            <td className="p-3 font-bold">{row.label}</td>
                            <td className="p-3 num">
                                {fmtInt(row.present)} / {fmtInt(row.total)} — {fmtPct(row.pct)}
                            </td>
                            <td className="p-3">
                                <span className={`inline-flex rounded-full border px-2 py-1 text-xs font-bold ${toneClass(row.status)}`}>
                                    {statusLabel(row.status)}
                                </span>
                            </td>
                            <td className="p-3 text-slate-600 leading-6">{row.why}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function SimpleList({ title, rows }) {
    if (!rows.length) {
        return (
            <div className="rounded-xl border bg-emerald-50 border-emerald-200 p-4 text-emerald-800 font-bold">
                ✅ {title}: لا توجد عينة ظاهرة
            </div>
        );
    }

    const keys = Object.keys(rows[0]);

    return (
        <div className="rounded-xl border bg-white p-5 overflow-x-auto shadow-sm">
            <h2 className="text-xl font-extrabold mb-3">{title}</h2>

            <table className="min-w-full text-sm">
                <thead className="bg-slate-50">
                    <tr>
                        {keys.map((key) => (
                            <th key={key} className="p-3 text-right">
                                {key}
                            </th>
                        ))}
                    </tr>
                </thead>

                <tbody>
                    {rows.map((row, idx) => (
                        <tr key={idx} className="border-t">
                            {keys.map((key) => (
                                <td key={key} className="p-3 num">
                                    {String(row[key] ?? "—")}
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export default function AIControlCenter() {
    const [fromDate, setFromDate] = useState(addDaysIso(-30));
    const [toDate, setToDate] = useState(todayIso());
    const [tab, setTab] = useState("readiness");
    const [loading, setLoading] = useState(false);
    const [data, setData] = useState({});

    const load = async () => {
        setLoading(true);

        const range = { from_date: fromDate, to_date: toDate };

        const endpoints = [
            ["dashboard", `/dashboard${buildQuery(range)}`],
            ["orders", `/orders${buildQuery({ ...range, page: 1, limit: 500 })}`],
            ["statusSummary", `/orders/status-summary${buildQuery(range)}`],
            ["adsBreakdown", `/dashboard/ads-cost-breakdown${buildQuery(range)}`],
            ["qoyodStats", "/integrations/qoyod/first-sync-monitor/stats/summary"],
        ];

        const next = {};

        await Promise.all(
            endpoints.map(async ([key, url]) => {
                try {
                    const res = await api.get(url);
                    next[key] = { ok: true, data: res.data };
                } catch (e) {
                    next[key] = {
                        ok: false,
                        error: e?.response?.data?.detail || e?.message || "فشل التحميل",
                    };
                }
            })
        );

        setData(next);
        setLoading(false);

        const failed = Object.values(next).filter((x) => !x.ok).length;
        if (failed) toast.warning(`تم الفحص مع ${failed} مصدر لم يستجب`);
    };

    useEffect(() => {
        load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const model = useMemo(() => {
        const ordersPayload = data.orders?.data || {};
        const orders = extractOrders(ordersPayload);

        const dashboard = data.dashboard?.data || {};
        const dashboardSummary = dashboard.summary || dashboard || {};

        const statusSummary = data.statusSummary?.data || {};
        const adsBreakdown = data.adsBreakdown?.data || {};
        const qoyodStats = data.qoyodStats?.data || {};

        const coverage = AI_ORDER_FIELDS.map((field) => fieldCoverage(orders, field));

        const requiredBlockers = coverage.filter((row) => row.required && row.status === BLOCK);
        const productReady = coverage.find((row) => row.id === "products")?.status === OK;
        const adCoverage = coverage.find((row) => row.id === "ad_link");

        const adSpend = Number(
            dashboardSummary.daily_ads_total ||
                dashboardSummary.total_ads_cost ||
                dashboardSummary.ads_total ||
                adsBreakdown.total_amount ||
                adsBreakdown.total ||
                0
        );

        const qoyodFailed = Number(
            qoyodStats?.stats?.failed ||
                qoyodStats?.failed ||
                qoyodStats?.summary?.failed ||
                0
        );

        const seen = new Map();

        orders.forEach((order) => {
            const number = getAny(order, ["order_number", "reference_id", "order_id", "number"]);
            if (!hasValue(number)) return;

            const key = String(number).trim();
            seen.set(key, (seen.get(key) || 0) + 1);
        });

        const duplicates = [...seen.entries()]
            .filter(([, count]) => count > 1)
            .slice(0, 20);

        const missingOrders = orders
            .filter((order) =>
                AI_ORDER_FIELDS.some((field) => field.required && !hasValue(getAny(order, field.paths || [])))
            )
            .slice(0, 25);

        const totalOrders = extractTotalOrders(ordersPayload, orders.length);

        const dashboardOrders =
            dashboardSummary.total_orders ||
            dashboardSummary.orders_count ||
            dashboardSummary.delivered_orders ||
            0;

        const statusOrders =
            statusSummary?.totals?.orders_count ||
            statusSummary?.orders_count ||
            statusSummary?.total_orders ||
            0;

        const sales =
            dashboardSummary.total_sales ||
            dashboardSummary.sales ||
            dashboardSummary.revenue ||
            dashboardSummary.total_revenue ||
            0;

        const gates = [
            {
                title: "المحاسبة والتقارير",
                status: requiredBlockers.length ? BLOCK : OK,
                text: "لا نعتمد أي تحليل محاسبي إذا كانت حقول الطلب الأساسية ناقصة.",
            },
            {
                title: "الربح الحقيقي",
                status: productReady ? OK : BLOCK,
                text: "تحليل الربح يحتاج سطور منتجات وتكلفة وSKU على مستوى الطلب.",
            },
            {
                title: "الإعلانات",
                status: adSpend <= 0 ? WARN : adCoverage?.status === OK ? OK : BLOCK,
                text: "لا قرار توسع أو إيقاف بدون ربط الطلب بالحملة أو الإعلان.",
            },
            {
                title: "قيود / ZATCA",
                status: qoyodFailed > 0 ? BLOCK : data.qoyodStats?.ok ? OK : WARN,
                text: "أي فشل ترحيل أو سداد في قيود يعتبر مانعاً قبل الاعتماد.",
            },
        ];

        const recommendations = [];

        coverage.forEach((row) => {
            if (row.required && row.status === BLOCK) {
                recommendations.push(
                    `P0: أضف أو أصلح ${row.label} في بيانات الطلبات — التغطية الحالية ${fmtPct(row.pct)}.`
                );
            }
        });

        if (adCoverage?.status !== OK) {
            recommendations.push(
                "P0: أضف campaign_id / ad_id / ad_squad_id / UTM داخل الطلب حتى يصبح تحليل الحملات موثوقاً."
            );
        }

        if (!data.adsBreakdown?.ok) {
            recommendations.push(
                "P1: أصلح تفصيل مصروفات الإعلانات لأن إجمالي الصرف وحده لا يكفي لحساب CPA وROAS."
            );
        }

        if (duplicates.length) {
            recommendations.push("P0: امنع تكرار الطلبات عبر upsert صارم على user_id + order_number.");
        }

        return {
            orders,
            totalOrders,
            dashboardSummary,
            dashboardOrders,
            statusOrders,
            sales,
            statusSummary,
            adsBreakdown,
            qoyodStats,
            coverage,
            gates,
            recommendations,
            duplicates,
            missingOrders,
            adSpend,
            qoyodFailed,
        };
    }, [data]);

    const overall = model.gates.some((gate) => gate.status === BLOCK)
        ? BLOCK
        : model.gates.some((gate) => gate.status === WARN)
            ? WARN
            : OK;

    return (
        <div className="space-y-6" dir="rtl" data-testid="ai-control-center">
            <div className="flex items-start justify-between flex-wrap gap-4">
                <div>
                    <div className="inline-flex bg-slate-900 text-white rounded-full px-3 py-1 text-xs font-bold mb-3">
                        🧠 AI Preflight · قراءة فقط
                    </div>

                    <h1 className="text-3xl font-extrabold">مركز الذكاء والتحقق</h1>

                    <p className="text-sm text-slate-600 mt-2 leading-7 max-w-3xl">
                        هذه الصفحة تجهز ميزان للذكاء الاصطناعي: تفحص اكتمال البيانات،
                        تكشف الفروقات الأولية، وتحوّل النواقص إلى أوامر واضحة قبل ربط OpenAI فعلياً.
                    </p>
                </div>

                <div className="rounded-xl border bg-white p-3 flex flex-wrap items-end gap-3 shadow-sm">
                    <label className="text-xs font-bold text-slate-600">
                        من
                        <input
                            type="date"
                            value={fromDate}
                            onChange={(e) => setFromDate(e.target.value)}
                            className="block mt-1 border rounded-lg px-3 py-2 text-sm"
                        />
                    </label>

                    <label className="text-xs font-bold text-slate-600">
                        إلى
                        <input
                            type="date"
                            value={toDate}
                            onChange={(e) => setToDate(e.target.value)}
                            className="block mt-1 border rounded-lg px-3 py-2 text-sm"
                        />
                    </label>

                    <button
                        onClick={load}
                        disabled={loading}
                        className="bg-slate-900 text-white rounded-lg px-4 py-2 font-bold text-sm disabled:opacity-50"
                    >
                        {loading ? "جارٍ الفحص…" : "🔄 فحص الآن"}
                    </button>
                </div>
            </div>

            <div className={`rounded-xl border-2 p-5 ${toneClass(overall)}`}>
                <h2 className="text-xl font-extrabold">حكم الجاهزية العام: {statusLabel(overall)}</h2>
                <p className="text-sm mt-1 leading-6">
                    إذا ظهرت بوابة ⛔ فالقرار المرتبط بها ممنوع حتى يكتمل مصدر البيانات أو يتم إصلاح الفرق.
                </p>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                <Kpi title="طلبات API" value={fmtInt(model.totalOrders)} />
                <Kpi title="طلبات Dashboard" value={fmtInt(model.dashboardOrders)} />
                <Kpi title="مبيعات" value={fmtMoney(model.sales)} />
                <Kpi title="صرف إعلاني" value={fmtMoney(model.adSpend)} />
                <Kpi title="فشل قيود" value={fmtInt(model.qoyodFailed)} />
            </div>

            <div className="flex flex-wrap gap-2">
                <Tab active={tab === "readiness"} onClick={() => setTab("readiness")}>
                    جاهزية البيانات
                </Tab>

                <Tab active={tab === "reconciliation"} onClick={() => setTab("reconciliation")}>
                    كشف الفروقات
                </Tab>

                <Tab active={tab === "errors"} onClick={() => setTab("errors")}>
                    أخطاء الربط
                </Tab>

                <Tab active={tab === "contract"} onClick={() => setTab("contract")}>
                    عقد البيانات
                </Tab>
            </div>

            {tab === "readiness" && (
                <div className="space-y-5">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {model.gates.map((gate) => (
                            <Gate key={gate.title} gate={gate} />
                        ))}
                    </div>

                    <CoverageTable rows={model.coverage} />
                    <Recommendations items={model.recommendations} />
                </div>
            )}

            {tab === "reconciliation" && <Reconciliation model={model} />}
            {tab === "errors" && <Errors data={data} model={model} />}
            {tab === "contract" && <Contract />}
        </div>
    );
}

function Recommendations({ items }) {
    return (
        <div className="rounded-xl border bg-white p-5 shadow-sm">
            <h2 className="text-xl font-extrabold mb-3">توصيات للمطور</h2>

            {items.length ? (
                <ul className="space-y-2 text-sm leading-7 list-disc list-inside">
                    {items.map((item) => (
                        <li key={item}>{item}</li>
                    ))}
                </ul>
            ) : (
                <p className="text-sm text-slate-500">لا توجد توصيات حرجة في العينة الحالية.</p>
            )}
        </div>
    );
}

function Reconciliation({ model }) {
    const gap = Number(model.statusOrders || 0) - Number(model.dashboardOrders || 0);

    return (
        <div className="space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <Kpi title="تكرار رقم الطلب" value={fmtInt(model.duplicates.length)} />
                <Kpi title="طلبات ناقصة حقول حرجة" value={fmtInt(model.missingOrders.length)} />
                <Kpi
                    title="فرق Dashboard vs Status"
                    value={fmtInt(gap)}
                    hint="قد يكون بسبب فلاتر الحالات؛ لا يُتجاهل قبل التفسير."
                />
            </div>

            <SimpleList
                title="طلبات مكررة"
                rows={model.duplicates.map(([order, count]) => ({ order, count }))}
            />

            <SimpleList
                title="عينة طلبات ناقصة"
                rows={model.missingOrders.map((order) => ({
                    order: getAny(order, ["order_number", "reference_id", "order_id", "number"]) || "—",
                    date: getAny(order, ["order_date", "created_at", "date", "salla_order_created_at"]) || "—",
                    status: getAny(order, ["order_status", "status", "status.name", "order_status_native"]) || "—",
                    payment: getAny(order, ["payment_method", "payment.method", "payment_method_name", "payment_method_native"]) || "—",
                    amount: getAny(order, ["total_amount", "amount", "total", "summary.total", "totals.order_total"]) || 0,
                }))}
            />
        </div>
    );
}

function Errors({ data, model }) {
    const endpoints = [
        ["Dashboard", data.dashboard],
        ["Orders", data.orders],
        ["Status", data.statusSummary],
        ["Ads", data.adsBreakdown],
        ["Qoyod", data.qoyodStats],
    ];

    return (
        <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
                {endpoints.map(([name, result]) => (
                    <div
                        key={name}
                        className={`rounded-lg border p-3 ${
                            result?.ok ? "bg-white" : "bg-red-50 border-red-200 text-red-800"
                        }`}
                    >
                        <div className="font-bold text-sm">{name}</div>
                        <div className="text-xs mt-1 break-words">
                            {result?.ok ? "✅ متصل" : `⛔ ${result?.error || "فشل"}`}
                        </div>
                    </div>
                ))}
            </div>

            <Gate
                gate={{
                    title: "قيود",
                    status: model.qoyodFailed > 0 ? BLOCK : data.qoyodStats?.ok ? OK : WARN,
                    text: `failed = ${fmtInt(model.qoyodFailed)}`,
                }}
            />

            <Gate
                gate={{
                    title: "الإعلانات",
                    status: data.adsBreakdown?.ok ? OK : WARN,
                    text: `total = ${fmtMoney(model.adSpend)} · حالة المصدر: ${
                        data.adsBreakdown?.ok ? "متصل" : "غير متصل أو غير متاح"
                    }`,
                }}
            />
        </div>
    );
}

function Contract() {
    return (
        <div className="space-y-4">
            {AI_DATA_CONTRACT.map((section) => (
                <div key={section.title} className="rounded-xl border bg-white p-5 shadow-sm">
                    <h3 className="text-lg font-extrabold mb-3">{section.title}</h3>

                    <ul dir="ltr" className="list-disc list-inside text-sm leading-7 text-slate-700">
                        {section.items.map((item) => (
                            <li key={item}>{item}</li>
                        ))}
                    </ul>
                </div>
            ))}
        </div>
    );
}