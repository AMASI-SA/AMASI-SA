import {
    CheckCircle,
    ClockCountdown,
    MinusCircle,
    SnapchatLogo,
    WarningCircle,
} from "@phosphor-icons/react";

const DATE_LOCALE = "ar-SA-u-ca-gregory-nu-latn";
const EVIDENCE_WINDOWS = [14, 7, 3, 2, 1];

const ACTION_LABELS = {
    "campaign.create": "إنشاء حملة",
    "campaign.update": "تعديل حملة",
    "ad_squad.create": "إنشاء مجموعة إعلانية",
    "ad_squad.update": "تعديل مجموعة إعلانية",
    "ad.create": "إنشاء إعلان",
    "ad.update": "تعديل إعلان",
    "provider.observed_update": "تعديل رُصد في Snapchat",
    create: "إنشاء",
    update: "تعديل",
    pause: "إيقاف",
    activate: "تشغيل",
};

const FIELD_LABELS = {
    status: "الحالة",
    daily_budget: "الميزانية اليومية",
    daily_budget_micro: "الميزانية اليومية",
    lifetime_budget: "ميزانية المدة",
    spend: "الصرف",
    spend_sar: "الصرف",
    sales: "المبيعات",
    sales_sar: "المبيعات",
    orders: "الطلبات",
    profit: "المكسب المساهم",
    contribution_profit_sar: "المكسب المساهم",
    gross_profit_before_marketing_sar: "مكسب المتجر قبل التسويق",
    product_cost_sar: "تكلفة المنتجات",
    margin: "الهامش",
    margin_pct: "الهامش",
    profit_margin_pct: "هامش المكسب",
    gross_margin_before_marketing_pct: "هامش المتجر قبل التسويق",
    roas: "العائد ROAS",
    cpa: "تكلفة الطلب",
    cpa_sar: "تكلفة الطلب",
    name: "الاسم",
};

const EXECUTION_LABELS = {
    completed: "نُفّذ وتحقق",
    verified: "نُفّذ وتحقق",
    executing: "قيد التنفيذ",
    approved: "معتمد بانتظار التنفيذ",
    previewed: "معاينة فقط",
    failed: "فشل التنفيذ",
    rolled_back: "تم التراجع",
    observed: "رُصد وتحقق من Snapchat",
    unknown: "حالة التنفيذ غير معروفة",
};

const OUTCOME_LABELS = {
    successful: "حقق الهدف",
    succeeded: "حقق الهدف",
    success: "حقق الهدف",
    failed: "لم يحقق الهدف",
    failure: "لم يحقق الهدف",
    mixed: "نتيجة مختلطة",
    pending: "بانتظار اكتمال القياس",
    not_evaluated: "بانتظار اكتمال القياس",
    inconclusive: "الدليل غير حاسم",
    unknown: "لم يُقيّم بعد",
};

const VERIFIED_EXECUTION_STATUSES = new Set(["completed", "verified", "observed"]);
const SCOPE_LABELS = {
    campaign: "الحملة",
    account: "الحساب",
    store: "المتجر",
};
const DIRECTION_LABELS = {
    increase: "ارتفاع",
    decrease: "انخفاض",
    stable: "ثبات",
};
const METRIC_PRIORITY = [
    "sales_sar",
    "contribution_profit_sar",
    "gross_profit_before_marketing_sar",
    "orders",
    "spend_sar",
    "roas",
    "cpa_sar",
    "profit_margin_pct",
    "gross_margin_before_marketing_pct",
    "product_cost_sar",
];

function dateTime(value) {
    if (!value) return "وقت غير متوفر";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleString(DATE_LOCALE, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function valueText(value, key = "") {
    if (value === null || value === undefined || value === "") return "غير متوفر";
    if (typeof value === "boolean") return value ? "نعم" : "لا";
    if (typeof value === "number") {
        const normalized = key.endsWith("_micro") ? value / 1_000_000 : value;
        const formatted = normalized.toLocaleString("en-US", { maximumFractionDigits: 2 });
        if (key.includes("margin") || key.includes("rate") || key.includes("pct")) return `${formatted}%`;
        if (key === "roas") return `${formatted}×`;
        return formatted;
    }
    if (typeof value === "object") return null;
    return String(value);
}

function safeDisplayText(value, depth = 0) {
    if (value === null || value === undefined || value === "") return "";
    if (["string", "number", "boolean"].includes(typeof value)) return String(value);
    if (depth >= 2) return "";
    if (Array.isArray(value)) {
        return value
            .map((item) => safeDisplayText(item, depth + 1))
            .filter(Boolean)
            .slice(0, 3)
            .join("، ");
    }
    if (typeof value !== "object") return "";
    return Object.entries(value)
        .map(([key, nested]) => {
            const formatted = safeDisplayText(nested, depth + 1);
            return formatted ? `${FIELD_LABELS[key] || key}: ${formatted}` : "";
        })
        .filter(Boolean)
        .slice(0, 3)
        .join(" · ");
}

function supportingContextText(item) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
        return safeDisplayText(item) || "معلومة سياقية غير موثقة";
    }
    for (const key of ["label", "detail", "description", "note", "text", "value"]) {
        const candidate = safeDisplayText(item[key]);
        if (candidate) return candidate;
    }
    const withoutMetadata = Object.fromEntries(
        Object.entries(item).filter(([key]) => !["code", "verification_status"].includes(key)),
    );
    return safeDisplayText(withoutMetadata) || "معلومة سياقية غير موثقة";
}

function primitiveEntries(value = {}, max = 8) {
    return Object.entries(value || {}).flatMap(([key, raw]) => {
        if (raw && typeof raw === "object" && !Array.isArray(raw)) {
            if ("before" in raw || "after" in raw) {
                return [[key, `${valueText(raw.before, key)} ← ${valueText(raw.after, key)}`]];
            }
            return [];
        }
        const formatted = valueText(raw, key);
        return formatted === null ? [] : [[key, formatted]];
    }).slice(0, max);
}

function DataBox({ title, value, empty }) {
    const entries = primitiveEntries(value);
    return (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <div className="text-[11px] font-black text-slate-500">{title}</div>
            {entries.length ? (
                <dl className="mt-2 space-y-1.5">
                    {entries.map(([key, formatted]) => (
                        <div key={key} className="flex items-start justify-between gap-3 text-xs">
                            <dt className="font-bold text-slate-500">{FIELD_LABELS[key] || key}</dt>
                            <dd className="max-w-[65%] break-words text-left font-mono font-black text-slate-900" dir="auto">{formatted}</dd>
                        </div>
                    ))}
                </dl>
            ) : <p className="mt-2 text-xs font-semibold text-slate-400">{empty}</p>}
        </div>
    );
}

function StatusBox({ kind, status, detail }) {
    const execution = kind === "execution";
    const label = execution
        ? EXECUTION_LABELS[status] || status || EXECUTION_LABELS.unknown
        : OUTCOME_LABELS[status] || status || OUTCOME_LABELS.unknown;
    const bad = ["failed", "failure"].includes(status);
    const good = ["completed", "verified", "observed", "successful", "succeeded", "success"].includes(status);
    const Icon = good ? CheckCircle : bad ? WarningCircle : ClockCountdown;
    return (
        <div className={`rounded-xl border p-3 ${good ? "border-emerald-200 bg-emerald-50" : bad ? "border-rose-200 bg-rose-50" : "border-amber-200 bg-amber-50"}`}>
            <div className="flex items-center gap-2 text-[11px] font-black text-slate-500">
                <Icon size={17} weight="fill" />
                {execution ? "حالة التنفيذ" : "نتيجة الأعمال"}
            </div>
            <div className="mt-1 text-sm font-black text-slate-900">{label}</div>
            {detail && <div className="mt-1 text-xs font-semibold text-slate-600">{detail}</div>}
        </div>
    );
}

function EvidenceWindows({ windows = {} }) {
    const prioritizedEntries = (metrics = {}) => {
        const priority = [
            "orders",
            "sales_sar",
            "contribution_profit_sar",
            "roas",
            "spend_sar",
            "cpa_sar",
        ];
        const entries = primitiveEntries(metrics, 20);
        return entries
            .sort(([left], [right]) => {
                const leftIndex = priority.indexOf(left);
                const rightIndex = priority.indexOf(right);
                return (leftIndex < 0 ? 99 : leftIndex) - (rightIndex < 0 ? 99 : rightIndex);
            })
            .slice(0, 4);
    };
    return (
        <div className="mt-4">
            <div className="text-xs font-black text-slate-700">نوافذ الدليل وقت القرار</div>
            <div className="mt-2 grid gap-2 sm:grid-cols-5">
                {EVIDENCE_WINDOWS.map((days) => {
                    const metrics = windows[String(days)] || {};
                    const entries = prioritizedEntries(metrics);
                    return (
                        <div key={days} className="rounded-xl border border-slate-200 bg-white p-2.5">
                            <div className="font-mono text-xs font-black text-slate-900">{days} يوم</div>
                            {entries.length ? entries.map(([key, formatted]) => (
                                <div key={key} className="mt-1 flex justify-between gap-1 text-[10px]">
                                    <span className="font-bold text-slate-400">{FIELD_LABELS[key] || key}</span>
                                    <span className="font-mono font-black text-slate-700" dir="auto">{formatted}</span>
                                </div>
                            )) : <div className="mt-2 text-[10px] font-semibold text-slate-400">غير متوفر</div>}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

function ProductAttributionWindows({ windows = {} }) {
    const available = EVIDENCE_WINDOWS.filter(
        (days) => Array.isArray(windows[String(days)]) && windows[String(days)].length,
    );
    if (!available.length) return null;
    return (
        <div className="mt-4 rounded-xl border border-cyan-200 bg-cyan-50/60 p-3">
            <div className="text-xs font-black text-slate-800">
                مبيعات المنتج: المنسوب للحملة مقابل إجمالي المتجر
            </div>
            <p className="mt-1 text-[11px] font-semibold leading-5 text-slate-600">
                الطلب المضاف يدويًا يظهر كما سجّلته سلة؛ لا يعني واتساب. والمنصات الأخرى المثبتة تُستبعد من أثر Snapchat، أما الباقي فيظل غير محسوم.
            </p>
            <div className="mt-3 grid gap-2 lg:grid-cols-2">
                {available.map((days) => (
                    <div key={days} className="rounded-xl border border-cyan-100 bg-white p-2.5">
                        <div className="font-mono text-xs font-black text-slate-900">{days} يوم</div>
                        <div className="mt-2 space-y-2">
                            {windows[String(days)].slice(0, 3).map((row, index) => (
                                <div key={`${row.identity || row.salla_product_id || index}`} className="border-t border-slate-100 pt-2 first:border-0 first:pt-0">
                                    <div className="truncate text-[11px] font-black text-slate-700">{row.name || row.sku || "منتج مرتبط"}</div>
                                    <div className="mt-1 grid grid-cols-3 gap-1 text-center">
                                        <div className="rounded-lg bg-violet-50 p-1.5">
                                            <div className="text-[9px] font-bold text-slate-500">منسوب للحملة</div>
                                            <div className="font-mono text-[11px] font-black">{Number(row.campaign_attributed_units || 0).toLocaleString("en-US")}</div>
                                        </div>
                                        <div className="rounded-lg bg-slate-50 p-1.5">
                                            <div className="text-[9px] font-bold text-slate-500">إجمالي المنتج</div>
                                            <div className="font-mono text-[11px] font-black">{Number(row.whole_store_product_units || 0).toLocaleString("en-US")}</div>
                                        </div>
                                        <div className="rounded-lg bg-amber-50 p-1.5">
                                            <div className="text-[9px] font-bold text-slate-500">غير محسوم لسناب</div>
                                            <div className="font-mono text-[11px] font-black">{Number(row.units_unresolved_for_snapchat_decision || 0).toLocaleString("en-US")}</div>
                                        </div>
                                    </div>
                                    {(Number(row.salla_manual_entry_units || 0) > 0 || Number(row.verified_other_ad_platform_units || 0) > 0) && (
                                        <div className="mt-1 text-[9px] font-semibold text-slate-500">
                                            مضاف يدويًا في سلة: <span className="font-mono font-black">{Number(row.salla_manual_entry_units || 0).toLocaleString("en-US")}</span>
                                            <span className="mx-1">·</span>
                                            منصات أخرى مثبتة: <span className="font-mono font-black">{Number(row.verified_other_ad_platform_units || 0).toLocaleString("en-US")}</span>
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

function ProductScope({ products = [], inventory = [], linkState = "" }) {
    if (!products.length && !inventory.length) return null;
    const productRows = products.length ? products : inventory;
    const inventoryKey = (productId, variantId) => `${String(productId || "")}::${String(variantId || "")}`;
    const inventoryById = new Map(inventory.flatMap((item) => {
        const variantId = item.product_variant_id || "";
        return [
            [inventoryKey(item.salla_product_id, variantId), item],
            [inventoryKey(item.mezan_product_id, variantId), item],
        ];
    }));
    return (
        <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50/60 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-xs font-black text-slate-800">المنتج المقصود والمخزون وقت القرار</div>
                {linkState && <span className="rounded-full bg-white px-2 py-1 text-[9px] font-black text-emerald-800">{linkState}</span>}
            </div>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {productRows.map((product, index) => {
                    const identity = String(product.product_id || product.salla_product_id || product.mezan_product_id || "");
                    const variantId = String(product.product_variant_id || "");
                    const stock = inventoryById.get(inventoryKey(identity, variantId))
                        || (!identity ? inventory[index] : null)
                        || {};
                    const stockVerified = stock.freshness_status === "fresh"
                        && stock.observed_after_capture !== true
                        && stock.variant_found !== false
                        && stock.delivery_blocked !== true;
                    const quantity = stockVerified
                        ? (stock.unlimited_quantity ? "غير محدود" : valueText(stock.quantity, "quantity"))
                        : "غير متحقق وقت القرار";
                    const verificationNote = stock.observed_after_capture === true
                        ? "لقطة المخزون أحدث من وقت القرار، لذلك لم تُستخدم تاريخيًا."
                        : stock.variant_found === false
                            ? "خيار المنتج لم يوجد في لقطة المخزون المتاحة."
                            : stock.delivery_blocked === true
                                ? "المخزون أو حالة المنتج لا تسمح باعتباره متاحًا."
                                : "لقطة المخزون قديمة أو غير مكتملة.";
                    return (
                        <div key={`${identity}-${variantId}-${index}`} className="rounded-lg bg-white px-3 py-2 text-xs">
                            <div className="font-black text-slate-800">{product.product_name || product.name || identity || "منتج مرتبط"}</div>
                            {variantId && <div className="mt-0.5 font-mono text-[9px] font-bold text-slate-400">الخيار: {variantId}</div>}
                            <div className="mt-1 font-semibold text-slate-500">المخزون: <span className="font-mono font-black text-slate-800">{quantity}</span></div>
                            {!stockVerified && <div className="mt-1 text-[9px] font-semibold leading-4 text-amber-800">{verificationNote}</div>}
                        </div>
                    );
                })}
            </div>
            <p className="mt-2 text-[9px] font-semibold text-slate-500">ربط المنتج يثبت المقصود بالإعلان، ولا يثبت وحده أن كل مبيعات المنتج سببها الحملة.</p>
        </div>
    );
}

function signedNumber(value, maximumFractionDigits = 1) {
    if (value === null || value === undefined || value === "") return "";
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "";
    const formatted = Math.abs(parsed).toLocaleString("en-US", { maximumFractionDigits });
    if (parsed > 0) return `+${formatted}`;
    if (parsed < 0) return `−${formatted}`;
    return "0";
}

function deltaText(metric, delta) {
    if (!delta || typeof delta !== "object") return "";
    const actual = valueText(delta.actual, metric);
    const direction = delta.direction === "increase" ? "↑" : delta.direction === "decrease" ? "↓" : "↔";
    const percent = signedNumber(delta.delta_pct);
    return `${actual || "غير متوفر"} ${direction}${percent ? ` ${percent}%` : ""}`;
}

function ExpectedChecks({ checks = [] }) {
    const available = checks.filter((item) => item && typeof item === "object").slice(0, 4);
    if (!available.length) return null;
    return (
        <div>
            <div className="text-[11px] font-black text-slate-600">فحوص المتوقع مقابل الفعلي</div>
            <div className="mt-2 flex flex-wrap gap-2">
                {available.map((check, index) => {
                    const metric = String(check.metric || "قياس").split(".").pop();
                    const scope = check.scope || (String(check.metric || "").includes(".") ? String(check.metric).split(".")[0] : "campaign");
                    const result = check.met === true ? "تحقق" : check.met === false ? "لم يتحقق" : "غير متاح";
                    const color = check.met === true
                        ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                        : check.met === false
                            ? "border-rose-200 bg-rose-50 text-rose-900"
                            : "border-amber-200 bg-amber-50 text-amber-900";
                    return (
                        <span key={`${scope}-${metric}-${index}`} className={`rounded-lg border px-2 py-1 text-[10px] font-black ${color}`}>
                            {SCOPE_LABELS[scope] || scope} · {FIELD_LABELS[metric] || metric}
                            {check.direction && <span> · {DIRECTION_LABELS[check.direction] || check.direction}</span>}
                            <span> · {result}</span>
                        </span>
                    );
                })}
            </div>
        </div>
    );
}

function OutcomeDeltas({ deltas = {} }) {
    const scopes = ["campaign", "account", "store"].flatMap((scope) => {
        const values = deltas?.[scope];
        if (!values || typeof values !== "object") return [];
        const metrics = Object.entries(values)
            .filter(([, delta]) => delta && typeof delta === "object")
            .sort(([left], [right]) => {
                const leftRank = METRIC_PRIORITY.indexOf(left);
                const rightRank = METRIC_PRIORITY.indexOf(right);
                return (leftRank < 0 ? 99 : leftRank) - (rightRank < 0 ? 99 : rightRank);
            })
            .slice(0, 4);
        return metrics.length ? [{ scope, metrics }] : [];
    });
    if (!scopes.length) return null;
    return (
        <div>
            <div className="text-[11px] font-black text-slate-600">التغير المقاس بعد القرار</div>
            <div className="mt-2 grid gap-2 md:grid-cols-3">
                {scopes.map(({ scope, metrics }) => (
                    <div key={scope} className="rounded-xl border border-slate-200 bg-white p-2.5">
                        <div className="text-[10px] font-black text-slate-500">{SCOPE_LABELS[scope] || scope}</div>
                        {metrics.map(([metric, delta]) => (
                            <div key={metric} className="mt-1 flex items-center justify-between gap-2 text-[10px]">
                                <span className="font-bold text-slate-500">{FIELD_LABELS[metric] || metric}</span>
                                <span className="font-mono font-black text-slate-800" dir="auto">{deltaText(metric, delta)}</span>
                            </div>
                        ))}
                    </div>
                ))}
            </div>
        </div>
    );
}

function PostAttribution({ attribution = {} }) {
    const metrics = [
        ["منسوب للحملة", attribution.campaign_attributed_units],
        ["إجمالي المنتج", attribution.whole_store_product_units],
        ["مستبعد لمنصات مثبتة", attribution.verified_cross_platform_units_excluded],
        ["غير محسوم لسناب", attribution.units_unresolved_for_snapchat_decision],
    ].filter(([, value]) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)));
    if (!metrics.length) return null;
    return (
        <div>
            <div className="text-[11px] font-black text-slate-600">إسناد المنتج بعد القرار</div>
            <div className="mt-2 flex flex-wrap gap-2">
                {metrics.map(([label, value]) => (
                    <span key={label} className="rounded-lg border border-cyan-100 bg-cyan-50 px-2 py-1 text-[10px] font-bold text-slate-600">
                        {label}: <span className="font-mono font-black text-slate-900">{Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 })}</span>
                    </span>
                ))}
            </div>
        </div>
    );
}

function MeasuredOutcome({ outcome = {} }) {
    const checks = Array.isArray(outcome.expected_vs_actual?.checks)
        ? outcome.expected_vs_actual.checks
        : [];
    const hasDeltas = ["campaign", "account", "store"].some(
        (scope) => Object.keys(outcome.deltas?.[scope] || {}).length,
    );
    const hasAttribution = Object.keys(outcome.post_attribution || {}).length > 0;
    if (!checks.length && !hasDeltas && !hasAttribution) return null;
    return (
        <div className="mt-3 space-y-3 rounded-xl border border-indigo-100 bg-indigo-50/50 p-3">
            <ExpectedChecks checks={checks} />
            <OutcomeDeltas deltas={outcome.deltas} />
            <PostAttribution attribution={outcome.post_attribution} />
        </div>
    );
}

export default function AdDecisionChangeCard({ decision }) {
    const executionStatus = decision.execution?.status || "unknown";
    const outcomeStatus = decision.outcome?.status || "pending";
    const sourceLabel = decision.direct_snapchat ? "تعديل مباشر من Snapchat" : "تعديل عبر ميزان";
    const reason = decision.reason || (decision.direct_snapchat
        ? "لم يُسجَّل سبب التعديل في Snapchat؛ لن يفترض ميزان سببًا من عنده."
        : "لم يُسجَّل سبب لهذا التعديل.");
    const verifiedAfter = VERIFIED_EXECUTION_STATUSES.has(executionStatus)
        ? decision.after
        : {};
    const afterEmpty = executionStatus === "failed"
        ? "فشل التنفيذ؛ لا توجد حالة بعدية متحققة."
        : "لم تتوفر لقطة بعدية متحققة.";

    return (
        <article className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm" data-testid={`ad-decision-${decision.decision_id}`}>
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="flex flex-wrap items-center gap-2">
                        <h3 className="font-black text-slate-950">
                            {ACTION_LABELS[decision.action] || decision.action || "تعديل إعلاني"}
                        </h3>
                        <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[10px] font-black ${decision.direct_snapchat ? "bg-yellow-100 text-yellow-900" : "bg-violet-100 text-violet-800"}`}>
                            {decision.direct_snapchat ? <SnapchatLogo size={14} weight="fill" /> : <MinusCircle size={14} weight="fill" />}
                            {sourceLabel}
                        </span>
                    </div>
                    <div className="mt-1 text-xs font-semibold text-slate-500">
                        {decision.entity_name || decision.entity_id || "كيان إعلاني غير مسمى"}
                        {decision.entity_type && <span> · {decision.entity_type}</span>}
                    </div>
                </div>
                <time className="font-mono text-xs font-bold text-slate-400" dateTime={decision.occurred_at || undefined}>
                    {dateTime(decision.occurred_at)}
                </time>
            </div>

            <div className={`mt-4 rounded-xl border p-3 text-sm font-bold leading-6 ${decision.reason ? "border-blue-100 bg-blue-50 text-slate-700" : "border-amber-200 bg-amber-50 text-amber-950"}`}>
                <span className="ml-1 font-black">السبب:</span>{reason}
            </div>

            <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <StatusBox kind="execution" status={executionStatus} detail={decision.execution?.detail || decision.execution?.message} />
                <StatusBox kind="outcome" status={outcomeStatus} detail={decision.outcome?.verdict || decision.outcome?.summary} />
            </div>

            <div className="mt-3 grid gap-2 md:grid-cols-3">
                <DataBox title="قبل التعديل" value={decision.before} empty="لم تتوفر لقطة قبلية." />
                <DataBox title={decision.direct_snapchat ? "الفروق المرصودة" : "التغيير المخطط"} value={decision.changes} empty="لم يُسجَّل تغيير مخطط." />
                <DataBox title="بعد التنفيذ المتحقق" value={verifiedAfter} empty={afterEmpty} />
                <DataBox title="المتوقع" value={decision.expected} empty="لم يُسجَّل توقع قابل للقياس." />
                <DataBox title="النتيجة الفعلية" value={decision.actual} empty="بانتظار اكتمال نافذة القياس." />
            </div>

            <MeasuredOutcome outcome={decision.outcome} />

            <EvidenceWindows windows={decision.evidence?.windows} />
            <ProductScope
                products={decision.evidence?.products || []}
                inventory={decision.evidence?.inventory || []}
                linkState={decision.evidence?.product_link_state}
            />
            <ProductAttributionWindows windows={decision.evidence?.product_comparison_windows} />

            {decision.trend_override_reason && (
                <div className="mt-3 rounded-xl border border-orange-200 bg-orange-50 p-3 text-xs font-bold leading-5 text-orange-950">
                    <span className="ml-1 font-black">سبب تجاهل التحسن الحديث:</span>
                    {decision.trend_override_reason}
                </div>
            )}

            {!!decision.supporting_context?.length && (
                <div className="mt-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-3">
                    <div className="text-xs font-black text-slate-700">سياق مساند — ليس أساس القرار وحده</div>
                    <ul className="mt-2 space-y-1 text-xs font-semibold leading-5 text-slate-600">
                        {decision.supporting_context.map((item, index) => (
                            <li key={`${index}-${typeof item === "string" ? item : item?.code || "context"}`}>
                                • {supportingContextText(item)}
                                {typeof item === "object" && item?.verification_status && (
                                    <span className={`mr-2 rounded-full px-2 py-0.5 text-[9px] font-black ${item.verification_status === "verified" ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-900"}`}>
                                        {item.verification_status === "verified" ? "متحقق" : item.verification_status === "inferred" ? "استنتاج" : "اقتراح غير متحقق"}
                                    </span>
                                )}
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {!!decision.annotations?.length && (
                <div className="mt-3 rounded-xl border border-violet-200 bg-violet-50/60 p-3">
                    <div className="text-xs font-black text-slate-800">ملاحظات التعديل المسجلة لاحقًا</div>
                    <ul className="mt-2 space-y-2">
                        {decision.annotations.map((item, index) => (
                            <li key={item.id || `${index}-${item.text}`} className="rounded-lg bg-white px-3 py-2 text-xs font-semibold leading-5 text-slate-700">
                                <div>{item.text}</div>
                                {item.annotated_at && (
                                    <time className="mt-1 block font-mono text-[9px] text-slate-400" dateTime={item.annotated_at}>
                                        {dateTime(item.annotated_at)}
                                    </time>
                                )}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </article>
    );
}
