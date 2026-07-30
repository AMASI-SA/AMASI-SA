import api from "./lib/api";

const ROOT_ID = "mezan-review-customer-history-root";
const MAX_HISTORY_ORDERS = 1000;
let activeOrder = null;
let loading = false;
let scheduled = false;
let payload = null;

function text(value) { return String(value || "").trim(); }
function orderNumberFromPage() {
    const heading = [...document.querySelectorAll("h2")].find((node) => node.textContent?.includes("مراجعة الطلب #"));
    return heading?.textContent?.match(/#(\d+)/)?.[1] || null;
}
function normalizeEmail(value) { return text(value).toLowerCase(); }
function normalizeMobile(value) {
    let digits = text(value).replace(/\D/g, "");
    if (digits.startsWith("00966")) digits = digits.slice(2);
    if (digits.startsWith("966")) digits = `0${digits.slice(3)}`;
    if (digits.startsWith("5") && digits.length === 9) digits = `0${digits}`;
    return digits;
}
function customerMatches(current, candidate) {
    const mobile = normalizeMobile(current?.mobile);
    const otherMobile = normalizeMobile(candidate?.mobile);
    if (mobile && otherMobile) return mobile === otherMobile;
    const email = normalizeEmail(current?.email);
    const otherEmail = normalizeEmail(candidate?.email);
    return Boolean(email && otherEmail && email === otherEmail);
}
function statusText(order) { return text(order?.status_native || order?.status || "غير محدد"); }
function normalizedStatus(order) { return statusText(order).toLowerCase().replace(/[_\s]+/g, " "); }
function statusKind(order) {
    const status = normalizedStatus(order);
    if (/ملغ|cancel|deleted|رفض|فشل|لم يستلم/.test(status)) return "cancelled";
    if (/مرتجع|مسترجع|refunded|returned|restored/.test(status)) return "returned";
    if (/مكتمل|تم التنفيذ|تم التوصيل|delivered|completed/.test(status)) return "completed";
    if (/شحن|توصيل|تنفيذ|processing|shipping|delivering|in progress/.test(status)) return "active";
    return "other";
}
function isCod(order) {
    const method = text(order?.payment?.method_native || order?.payment?.method).toLowerCase();
    return method === "cod" || method.includes("cash on delivery") || method.includes("الدفع عند الاستلام");
}
function money(value, currency = "SAR") {
    return `${Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency || "SAR"}`;
}
function dateText(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? text(value).slice(0, 10) : date.toLocaleDateString("en-GB");
}
async function listHistoryOrders() {
    const rows = [];
    let cursor = null;
    do {
        const params = { limit: 100 };
        if (cursor) params.cursor = cursor;
        const { data } = await api.get("/orders-v2", { params });
        const items = Array.isArray(data?.items) ? data.items : [];
        rows.push(...items);
        cursor = data?.next_cursor || null;
    } while (cursor && rows.length < MAX_HISTORY_ORDERS);
    return rows.slice(0, MAX_HISTORY_ORDERS);
}
function buildRecommendation(history, currentOrder) {
    const completed = history.filter((order) => statusKind(order) === "completed");
    const cancelled = history.filter((order) => statusKind(order) === "cancelled");
    const returned = history.filter((order) => statusKind(order) === "returned");
    const completedSpend = completed.reduce((sum, order) => sum + Number(order?.totals?.total || 0), 0);
    const average = completed.length ? completedSpend / completed.length : 0;
    const currentTotal = Number(currentOrder?.totals?.total || 0);
    const riskRatio = history.length ? (cancelled.length + returned.length) / history.length : 0;
    let tier = "new";
    let title = "لا توجد هدية مقترحة الآن";
    let reason = "البيانات السابقة غير كافية؛ الأفضل جمع تجربة شراء ناجحة أولًا.";
    let reward = "رسالة شكر شخصية بعد اكتمال الطلب";
    let maxCost = 0;
    if (completed.length >= 5 && completedSpend >= 1500 && riskRatio <= 0.2) {
        tier = "vip";
        title = "عميل مميز — هدية ولاء مقترحة";
        reason = `${completed.length} طلبات مكتملة بإجمالي ${money(completedSpend)} وسجل إلغاء منخفض.`;
        reward = "هدية صغيرة ملائمة للطلب أو ترقية التغليف مع بطاقة شكر";
        maxCost = Math.min(35, Math.max(12, currentTotal * 0.06));
    } else if ((completed.length >= 3 || completedSpend >= 750) && riskRatio <= 0.3) {
        tier = "loyal";
        title = "عميل متكرر — لفتة تقدير مقترحة";
        reason = `${completed.length} طلبات مكتملة ومتوسط طلب ${money(average)}.`;
        reward = "تغليف مميز أو هدية رمزية منخفضة التكلفة";
        maxCost = Math.min(20, Math.max(8, currentTotal * 0.04));
    } else if (completed.length >= 1 && riskRatio <= 0.35) {
        tier = "returning";
        title = "عميل عائد — عزّز العلاقة";
        reason = "لديه تجربة شراء مكتملة سابقة ويمكن تعزيز العودة دون تكلفة مرتفعة.";
        reward = "بطاقة شكر باسم العميل أو تحسين التغليف";
        maxCost = Math.min(10, currentTotal * 0.025);
    } else if (riskRatio > 0.35) {
        tier = "caution";
        title = "لا تقترح هدية قبل التحقق";
        reason = "نسبة الإلغاء أو الاسترجاع مرتفعة مقارنة بعدد الطلبات السابقة.";
        reward = "تحقق من الطلب وخدمة العميل أولًا";
        maxCost = 0;
    }
    return { tier, title, reason, reward, max_cost: Number(maxCost.toFixed(2)), decision_mode: "ai_policy_v1", requires_approval: true };
}
function summarize(history, currentOrder) {
    const counts = { completed: 0, cancelled: 0, returned: 0, active: 0, other: 0 };
    history.forEach((order) => { counts[statusKind(order)] += 1; });
    const completedSpend = history.filter((order) => statusKind(order) === "completed").reduce((sum, order) => sum + Number(order?.totals?.total || 0), 0);
    const cod = history.filter(isCod);
    const codCompleted = cod.filter((order) => statusKind(order) === "completed").length;
    const codFailed = cod.filter((order) => ["cancelled", "returned"].includes(statusKind(order))).length;
    return { counts, completedSpend, cod, codCompleted, codFailed, recommendation: buildRecommendation(history, currentOrder) };
}
function tone(kind) {
    return {
        completed: ["#dcfce7", "#166534"], cancelled: ["#fee2e2", "#b91c1c"], returned: ["#ffedd5", "#c2410c"], active: ["#dbeafe", "#1d4ed8"], other: ["#f1f5f9", "#475569"],
    }[kind] || ["#f1f5f9", "#475569"];
}
function render() {
    if (!payload) return;
    const productsHeading = [...document.querySelectorAll("h2,h3")].find((node) => node.textContent?.trim() === "منتجات الطلب");
    if (!productsHeading) return;
    let host = document.querySelector("[data-customer-history-card]");
    if (!host) {
        host = document.createElement("section");
        host.dataset.customerHistoryCard = "1";
        productsHeading.parentElement?.insertBefore(host, productsHeading);
    }
    const { currentOrder, history, summary } = payload;
    const signature = JSON.stringify({ order: currentOrder.order_number, rows: history.map((row) => [row.order_number, statusText(row), row?.totals?.total]) });
    if (host.dataset.signature === signature) return;
    host.dataset.signature = signature;
    const currentCod = isCod(currentOrder);
    let codMessage = "";
    let codBackground = "#fffbeb";
    let codColor = "#92400e";
    if (currentCod && summary.codFailed > 0) {
        codMessage = `تنبيه: لدى العميل ${summary.codFailed} طلب دفع عند الاستلام سابق لم يكتمل.`;
        codBackground = "#fee2e2"; codColor = "#b91c1c";
    } else if (currentCod && summary.codCompleted > 0) {
        codMessage = `العميل استلم ${summary.codCompleted} طلب دفع عند الاستلام سابقًا.`;
        codBackground = "#dcfce7"; codColor = "#166534";
    } else if (currentCod) {
        codMessage = "أول تجربة دفع عند الاستلام مسجلة لهذا العميل.";
    }
    host.style.cssText = "margin:14px 0;border:1px solid #cbd5e1;border-radius:18px;background:white;overflow:hidden";
    host.innerHTML = `<div style="padding:14px 16px;background:#f8fafc"><div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap"><div><b style="font-size:18px">سجل العميل السابق</b><div style="color:#64748b;margin-top:3px">مطابقة آمنة بالجوال ثم البريد الإلكتروني</div></div><button data-toggle style="border:1px solid #94a3b8;background:white;border-radius:12px;padding:9px 13px;font-weight:800;cursor:pointer">${history.length} طلبات سابقة</button></div><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px"><span style="background:#dcfce7;color:#166534;padding:6px 10px;border-radius:999px;font-weight:800">${summary.counts.completed} مكتملة</span><span style="background:#fee2e2;color:#b91c1c;padding:6px 10px;border-radius:999px;font-weight:800">${summary.counts.cancelled} ملغاة</span><span style="background:#ffedd5;color:#c2410c;padding:6px 10px;border-radius:999px;font-weight:800">${summary.counts.returned} مرتجعة</span><span style="background:#eef2ff;color:#4338ca;padding:6px 10px;border-radius:999px;font-weight:800">مشتريات مكتملة: ${money(summary.completedSpend)}</span></div></div>${codMessage ? `<div style="padding:11px 16px;background:${codBackground};color:${codColor};font-weight:900">${codMessage}</div>` : ""}<div style="padding:14px 16px;background:#f5f3ff"><b>${summary.recommendation.title}</b><div style="margin-top:5px;color:#4c1d95">${summary.recommendation.reason}</div><div style="margin-top:7px"><b>الاقتراح:</b> ${summary.recommendation.reward}${summary.recommendation.max_cost ? ` — تكلفة قصوى مقترحة ${money(summary.recommendation.max_cost)}` : ""}</div><div style="font-size:12px;color:#6b7280;margin-top:5px">توصية فقط؛ لا تُضاف هدية أو خصم دون اعتماد بشري.</div></div><div data-table hidden style="overflow:auto;padding:12px"><table style="width:100%;border-collapse:collapse;min-width:680px"><thead><tr style="background:#f8fafc"><th style="padding:9px;text-align:right">رقم الطلب</th><th style="padding:9px;text-align:right">التاريخ</th><th style="padding:9px;text-align:right">الإجمالي</th><th style="padding:9px;text-align:right">الدفع</th><th style="padding:9px;text-align:right">الحالة</th></tr></thead><tbody>${history.map((order) => { const kind = statusKind(order); const [bg, color] = tone(kind); return `<tr style="border-top:1px solid #e2e8f0"><td style="padding:9px"><a href="/orders/${encodeURIComponent(order.order_number)}" style="font-weight:900;color:#0f766e">#${order.order_number}</a></td><td style="padding:9px">${dateText(order.created_at)}</td><td style="padding:9px">${money(order?.totals?.total, order?.totals?.currency)}</td><td style="padding:9px">${text(order?.payment?.method_native || order?.payment?.method) || "—"}</td><td style="padding:9px"><span style="background:${bg};color:${color};padding:5px 9px;border-radius:999px;font-weight:800">${statusText(order)}</span></td></tr>`; }).join("") || `<tr><td colspan="5" style="padding:18px;text-align:center;color:#64748b">لا توجد طلبات سابقة مطابقة.</td></tr>`}</tbody></table></div>`;
    const table = host.querySelector("[data-table]");
    host.querySelector("[data-toggle]").onclick = () => { table.hidden = !table.hidden; };
}
function scheduleRender() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(() => { scheduled = false; render(); });
}
async function load() {
    const orderNumber = orderNumberFromPage();
    if (!orderNumber || loading || orderNumber === activeOrder) return;
    loading = true;
    try {
        const [{ data: currentOrder }, allOrders] = await Promise.all([
            api.get(`/order-reviews-v1/${encodeURIComponent(orderNumber)}`),
            listHistoryOrders(),
        ]);
        const history = allOrders.filter((order) => order.order_number !== currentOrder.order_number && customerMatches(currentOrder.customer, order.customer));
        history.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
        payload = { currentOrder, history, summary: summarize(history, currentOrder) };
        activeOrder = orderNumber;
        scheduleRender();
    } catch { /* review page remains usable when history is unavailable */ }
    finally { loading = false; }
}
function start() {
    if (document.getElementById(ROOT_ID)) return;
    const marker = document.createElement("div"); marker.id = ROOT_ID; marker.hidden = true; document.body.appendChild(marker);
    const observer = new MutationObserver(() => { const number = orderNumberFromPage(); if (number && number !== activeOrder) load(); else scheduleRender(); });
    observer.observe(document.body, { childList: true, subtree: true });
    load();
}

if (typeof window !== "undefined") window.addEventListener("DOMContentLoaded", start, { once: true });
