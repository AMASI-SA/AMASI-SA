import api from "./lib/api";

const ROOT_ID = "mezan-review-customer-history-fast-root";
let activeOrder = null;
let loading = false;

const text = (value) => String(value || "").trim();
const normalizeEmail = (value) => text(value).toLowerCase();
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
function orderNumberFromPage() {
  const heading = [...document.querySelectorAll("h2")].find((node) => node.textContent?.includes("مراجعة الطلب #"));
  return heading?.textContent?.match(/#(\d+)/)?.[1] || null;
}
function statusText(order) { return text(order?.status_native || order?.status || "غير محدد"); }
function statusKind(order) {
  const status = statusText(order).toLowerCase().replace(/[_\s]+/g, " ");
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
const money = (value, currency = "SAR") => `${Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency || "SAR"}`;
const dateText = (value) => {
  const date = new Date(value || 0);
  return Number.isNaN(date.getTime()) ? text(value).slice(0, 10) || "—" : date.toLocaleDateString("en-GB");
};
function recommendation(history, currentOrder) {
  const completed = history.filter((row) => statusKind(row) === "completed");
  const cancelled = history.filter((row) => statusKind(row) === "cancelled");
  const returned = history.filter((row) => statusKind(row) === "returned");
  const spend = completed.reduce((sum, row) => sum + Number(row?.totals?.total || 0), 0);
  const risk = history.length ? (cancelled.length + returned.length) / history.length : 0;
  const currentTotal = Number(currentOrder?.totals?.total || 0);
  if (completed.length >= 5 && spend >= 1500 && risk <= 0.2) return { title: "عميل مميز — هدية ولاء مقترحة", reason: `${completed.length} طلبات مكتملة بإجمالي ${money(spend)}.`, reward: "هدية صغيرة ملائمة للطلب أو ترقية التغليف", max: Math.min(35, Math.max(12, currentTotal * 0.06)) };
  if ((completed.length >= 3 || spend >= 750) && risk <= 0.3) return { title: "عميل متكرر — لفتة تقدير مقترحة", reason: `${completed.length} طلبات مكتملة.`, reward: "تغليف مميز أو هدية رمزية", max: Math.min(20, Math.max(8, currentTotal * 0.04)) };
  if (completed.length >= 1 && risk <= 0.35) return { title: "عميل عائد — عزّز العلاقة", reason: "لديه تجربة شراء مكتملة سابقة.", reward: "بطاقة شكر باسم العميل أو تحسين التغليف", max: Math.min(10, currentTotal * 0.025) };
  if (risk > 0.35) return { title: "لا تقترح هدية قبل التحقق", reason: "نسبة الإلغاء أو الاسترجاع مرتفعة.", reward: "تحقق من الطلب وخدمة العميل أولًا", max: 0 };
  return { title: "لا توجد هدية مقترحة الآن", reason: "البيانات السابقة غير كافية.", reward: "رسالة شكر شخصية بعد اكتمال الطلب", max: 0 };
}
function render(currentOrder, history, partial = false) {
  const heading = [...document.querySelectorAll("h2,h3")].find((node) => node.textContent?.trim() === "منتجات الطلب");
  if (!heading) return;
  let host = document.querySelector("[data-customer-history-card]");
  if (!host) {
    host = document.createElement("section");
    host.dataset.customerHistoryCard = "1";
    heading.parentElement?.insertBefore(host, heading);
  }
  const counts = { completed: 0, cancelled: 0, returned: 0, active: 0, other: 0 };
  history.forEach((row) => { counts[statusKind(row)] += 1; });
  const completedSpend = history.filter((row) => statusKind(row) === "completed").reduce((sum, row) => sum + Number(row?.totals?.total || 0), 0);
  const cod = history.filter(isCod);
  const codCompleted = cod.filter((row) => statusKind(row) === "completed").length;
  const codFailed = cod.filter((row) => ["cancelled", "returned"].includes(statusKind(row))).length;
  const rec = recommendation(history, currentOrder);
  const currentCod = isCod(currentOrder);
  const codMessage = currentCod ? (codFailed ? `تنبيه: لدى العميل ${codFailed} طلب دفع عند الاستلام سابق لم يكتمل.` : codCompleted ? `العميل استلم ${codCompleted} طلب دفع عند الاستلام سابقًا.` : "لا توجد تجربة دفع عند الاستلام مكتملة ضمن السجل المحمّل.") : "";
  const rows = history.map((order) => `<tr style="border-top:1px solid #e2e8f0"><td style="padding:9px"><a href="/orders/${encodeURIComponent(order.order_number)}" style="font-weight:900;color:#0f766e">#${order.order_number}</a></td><td style="padding:9px">${dateText(order.created_at)}</td><td style="padding:9px">${money(order?.totals?.total, order?.totals?.currency)}</td><td style="padding:9px">${text(order?.payment?.method_native || order?.payment?.method) || "—"}</td><td style="padding:9px">${statusText(order)}</td></tr>`).join("");
  host.style.cssText = "margin:14px 0;border:1px solid #cbd5e1;border-radius:18px;background:white;overflow:hidden";
  host.innerHTML = `<div style="padding:14px 16px;background:#f8fafc"><div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap"><div><b style="font-size:18px">سجل العميل السابق</b><div style="color:#64748b;margin-top:3px">مطابقة بالجوال ثم البريد الإلكتروني${partial ? " — جارٍ استكمال السجل…" : ""}</div></div><button data-toggle style="border:1px solid #94a3b8;background:white;border-radius:12px;padding:9px 13px;font-weight:800">${history.length} طلبات سابقة</button></div><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px"><span>${counts.completed} مكتملة</span><span>${counts.cancelled} ملغاة</span><span>${counts.returned} مرتجعة</span><span>مشتريات مكتملة: ${money(completedSpend)}</span></div></div>${codMessage ? `<div style="padding:11px 16px;background:#fffbeb;color:#92400e;font-weight:900">${codMessage}</div>` : ""}<div style="padding:14px 16px;background:#f5f3ff"><b>${rec.title}</b><div style="margin-top:5px">${rec.reason}</div><div style="margin-top:7px"><b>الاقتراح:</b> ${rec.reward}${rec.max ? ` — تكلفة قصوى ${money(rec.max)}` : ""}</div><div style="font-size:12px;color:#6b7280;margin-top:5px">توصية فقط وتحتاج اعتمادًا بشريًا.</div></div><div data-table hidden style="overflow:auto;padding:12px"><table style="width:100%;min-width:680px;border-collapse:collapse"><thead><tr><th>رقم الطلب</th><th>التاريخ</th><th>الإجمالي</th><th>الدفع</th><th>الحالة</th></tr></thead><tbody>${rows || `<tr><td colspan="5" style="padding:18px;text-align:center">لا توجد طلبات سابقة مطابقة ضمن السجل المحمّل.</td></tr>`}</tbody></table></div>`;
  const table = host.querySelector("[data-table]");
  host.querySelector("[data-toggle]").onclick = () => { table.hidden = !table.hidden; };
}
async function load() {
  const orderNumber = orderNumberFromPage();
  if (!orderNumber || loading || orderNumber === activeOrder) return;
  loading = true;
  try {
    const { data: currentOrder } = await api.get(`/orders-v2/${encodeURIComponent(orderNumber)}`);
    let cursor = null;
    const all = [];
    for (let page = 0; page < 3; page += 1) {
      const params = { limit: 100 };
      if (cursor) params.cursor = cursor;
      const { data } = await api.get("/orders-v2", { params });
      all.push(...(Array.isArray(data?.items) ? data.items : []));
      const history = all.filter((row) => row.order_number !== currentOrder.order_number && customerMatches(currentOrder.customer, row.customer));
      history.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
      render(currentOrder, history, Boolean(data?.next_cursor && page < 2));
      cursor = data?.next_cursor || null;
      if (!cursor) break;
    }
    activeOrder = orderNumber;
  } catch (error) {
    console.warn("Customer history unavailable", error);
  } finally {
    loading = false;
  }
}
function start() {
  if (document.getElementById(ROOT_ID)) return;
  const marker = document.createElement("div"); marker.id = ROOT_ID; marker.hidden = true; document.body.appendChild(marker);
  const observer = new MutationObserver(() => load());
  observer.observe(document.body, { childList: true, subtree: true });
  load();
}
if (typeof window !== "undefined") window.addEventListener("DOMContentLoaded", start, { once: true });
