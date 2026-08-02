import api from "./lib/api";
import {
    CREATE_PREVIEW_SEED_CONFIRMATION,
    RESET_PREVIEW_SEED_CONFIRMATION,
    isPreviewRuntimeHost,
    previewSeedScenario,
    previewSeedStatusLabel,
} from "./previewFulfillmentSeedUi";


const ROOT_ID = "mezan-preview-fulfillment-seed-root";
let status = null;
let loadingStatus = false;
let mutating = false;
let scheduled = false;

function text(value) {
    return String(value || "").trim();
}

function reviewedStage() {
    return document.querySelector('[data-testid="reviewed-orders-stage"]');
}

function toast(message, error = false) {
    const node = document.createElement("div");
    node.textContent = message;
    node.style.cssText = [
        "position:fixed",
        "z-index:14000",
        "left:18px",
        "bottom:18px",
        "max-width:430px",
        "padding:12px 16px",
        "border-radius:14px",
        "font-weight:900",
        "color:white",
        `background:${error ? "#be123c" : "#047857"}`,
        "box-shadow:0 14px 34px rgba(15,23,42,.28)",
        "direction:rtl",
    ].join(";");
    document.body.appendChild(node);
    window.setTimeout(() => node.remove(), 4200);
}

function errorMessage(error, fallback) {
    return error?.response?.data?.detail?.message
        || error?.response?.data?.detail
        || error?.message
        || fallback;
}

function button(label, tone = "violet") {
    const node = document.createElement("button");
    node.type = "button";
    node.textContent = label;
    const palettes = {
        violet: ["#6d28d9", "white", "#6d28d9"],
        rose: ["white", "#be123c", "#fecdd3"],
        slate: ["white", "#334155", "#cbd5e1"],
    };
    const [background, color, border] = palettes[tone] || palettes.violet;
    node.style.cssText = [
        "min-height:44px",
        "border-radius:12px",
        `border:1px solid ${border}`,
        `background:${background}`,
        `color:${color}`,
        "padding:9px 14px",
        "font-size:13px",
        "font-weight:900",
        "cursor:pointer",
    ].join(";");
    return node;
}

function renderHost(host) {
    if (!status?.available) {
        host.remove();
        return;
    }
    host.innerHTML = "";
    host.style.cssText = [
        "border:1px solid #c4b5fd",
        "border-radius:18px",
        "background:linear-gradient(135deg,#faf5ff,#f5f3ff)",
        "padding:16px",
        "box-shadow:0 6px 20px rgba(109,40,217,.08)",
        "direction:rtl",
    ].join(";");

    const top = document.createElement("div");
    top.style.cssText = "display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap";

    const copy = document.createElement("div");
    copy.style.cssText = "min-width:0;flex:1";
    const title = document.createElement("div");
    title.textContent = "بيانات اختبار Preview";
    title.style.cssText = "font-size:17px;font-weight:950;color:#3b0764";
    const description = document.createElement("div");
    description.textContent = "منتجات وطلبات تجريبية معزولة لاختبار المراجعة والتجميع وملفات PDF والانتقال إلى قيد التنفيذ.";
    description.style.cssText = "margin-top:5px;font-size:12px;line-height:1.8;color:#6b21a8";
    copy.append(title, description);

    const badge = document.createElement("span");
    badge.textContent = previewSeedStatusLabel(status);
    badge.style.cssText = [
        "display:inline-flex",
        "align-items:center",
        "min-height:30px",
        "border-radius:999px",
        `background:${status.created ? "#dcfce7" : "#ede9fe"}`,
        `color:${status.created ? "#047857" : "#6d28d9"}`,
        "padding:5px 10px",
        "font-size:11px",
        "font-weight:950",
        "white-space:nowrap",
    ].join(";");
    top.append(copy, badge);
    host.appendChild(top);

    const scenario = previewSeedScenario(status);
    const grid = document.createElement("div");
    grid.style.cssText = "display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-top:14px";
    [
        ["المنتجات", scenario.products],
        ["الطلبات", scenario.orders],
        ["تمت المراجعة", scenario.reviewedOrders],
        ["بانتظار المراجعة", scenario.pendingOrders],
        ["إجمالي القطع", scenario.reviewedQuantity],
    ].forEach(([label, value]) => {
        const card = document.createElement("div");
        card.style.cssText = "border:1px solid #e9d5ff;border-radius:12px;background:white;padding:9px;text-align:center";
        card.innerHTML = `<div style="font-size:10px;font-weight:800;color:#8b5cf6">${label}</div><div style="margin-top:2px;font-size:18px;font-weight:950;color:#3b0764">${value}</div>`;
        grid.appendChild(card);
    });
    host.appendChild(grid);

    const detail = document.createElement("div");
    detail.textContent = `السيناريو: سلسال ${scenario.necklaceQuantity} قطعة • ساعة ${scenario.watchQuantity} قطع • شنطة ${scenario.bagQuantity} قطع. اختيار السلسال كاملًا مع الساعة ينشئ 16 بطاقة لاختبار الصفحة الثانية في PDF.`;
    detail.style.cssText = "margin-top:12px;border-radius:12px;background:#fff;padding:10px;font-size:11px;font-weight:750;line-height:1.8;color:#5b21b6";
    host.appendChild(detail);

    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;margin-top:13px";
    const create = button(status.created ? "إعادة إنشاء بيانات الاختبار" : "إنشاء بيانات الاختبار", "violet");
    create.disabled = mutating;
    create.onclick = async () => {
        if (mutating) return;
        const confirmed = window.confirm(
            status.created
                ? "سيتم حذف بيانات الاختبار الحالية وملفاتها ثم إنشاؤها من جديد. متابعة؟"
                : "إنشاء منتجات وطلبات اختبار داخل Preview فقط؟",
        );
        if (!confirmed) return;
        mutating = true;
        create.disabled = true;
        create.textContent = "جارٍ الإنشاء…";
        try {
            await api.post("/preview-fulfillment-seed-v1/create", {
                confirmation: CREATE_PREVIEW_SEED_CONFIRMATION,
            });
            toast("تم إنشاء بيانات Preview. تُحدّث الصفحة الآن.");
            window.setTimeout(() => window.location.reload(), 450);
        } catch (error) {
            mutating = false;
            toast(errorMessage(error, "تعذر إنشاء بيانات Preview."), true);
            renderHost(host);
        }
    };
    actions.appendChild(create);

    if (status.created) {
        const reset = button("حذف بيانات الاختبار", "rose");
        reset.disabled = mutating;
        reset.onclick = async () => {
            if (mutating) return;
            if (!window.confirm("حذف منتجات وطلبات وملفات اختبار Preview فقط؟")) return;
            mutating = true;
            reset.disabled = true;
            reset.textContent = "جارٍ الحذف…";
            try {
                await api.delete("/preview-fulfillment-seed-v1/reset", {
                    data: { confirmation: RESET_PREVIEW_SEED_CONFIRMATION },
                });
                toast("تم حذف بيانات الاختبار.");
                window.setTimeout(() => window.location.reload(), 450);
            } catch (error) {
                mutating = false;
                toast(errorMessage(error, "تعذر حذف بيانات Preview."), true);
                renderHost(host);
            }
        };
        actions.appendChild(reset);
    }
    host.appendChild(actions);
}

async function loadStatus() {
    if (loadingStatus || status) return;
    loadingStatus = true;
    try {
        const { data } = await api.get("/preview-fulfillment-seed-v1/status");
        status = data || { available: false };
    } catch (error) {
        status = { available: false, reason: text(error?.message) };
    } finally {
        loadingStatus = false;
        scheduleDecorate();
    }
}

function decorate() {
    if (!status?.available) return;
    const stage = reviewedStage();
    if (!stage) return;
    let host = stage.querySelector("[data-preview-fulfillment-seed]");
    if (!host) {
        host = document.createElement("div");
        host.dataset.previewFulfillmentSeed = "1";
        stage.insertBefore(host, stage.firstChild);
    }
    renderHost(host);
}

function scheduleDecorate() {
    if (scheduled || typeof window === "undefined") return;
    scheduled = true;
    window.requestAnimationFrame(() => {
        scheduled = false;
        decorate();
    });
}

function start() {
    if (typeof document === "undefined" || !document.body) return;
    if (!isPreviewRuntimeHost(window.location.hostname)) return;
    if (document.getElementById(ROOT_ID)) return;
    const marker = document.createElement("div");
    marker.id = ROOT_ID;
    marker.hidden = true;
    document.body.appendChild(marker);
    const observer = new MutationObserver(scheduleDecorate);
    observer.observe(document.body, { childList: true, subtree: true });
    loadStatus();
    scheduleDecorate();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
    if (document.readyState === "loading") {
        window.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
}
