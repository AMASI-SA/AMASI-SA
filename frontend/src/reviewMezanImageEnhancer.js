import api from "./lib/api";

const ROOT_ID = "mezan-review-image-enhancer-root";
let activeOrder = null;
let detail = null;
let refreshing = false;

function text(value) { return String(value || "").trim(); }
function orderNumberFromPage() {
    const heading = [...document.querySelectorAll("h2")].find((node) => node.textContent?.includes("مراجعة الطلب #"));
    return heading?.textContent?.match(/#(\d+)/)?.[1] || null;
}
function skuFromCard(card) {
    const candidate = [...card.querySelectorAll("span")].find((node) => node.textContent?.trim().startsWith("SKU:"));
    return candidate?.textContent?.replace(/^SKU:\s*/, "").trim() || "";
}
function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || "").split(",", 2)[1] || "");
        reader.onerror = () => reject(new Error("تعذر قراءة الصورة"));
        reader.readAsDataURL(file);
    });
}
function specsFor(item) {
    const rows = [];
    const seen = new Set();
    const add = (name, value) => {
        const key = text(name).toLocaleLowerCase("ar").replace(/[ـ:：\s_-]+/g, " ");
        const visible = typeof value === "object" && value ? text(value.name || value.value || value.text || value.label) : text(value);
        if (!key || !visible || seen.has(key)) return;
        seen.add(key);
        rows.push({ key, name: text(name), value: visible });
    };
    (item.options || []).forEach((row) => add(row?.name, row?.value));
    (item.custom_fields || []).forEach((row) => add(row?.name || row?.label || row?.title || row?.question || row?.key, row?.value ?? row?.answer ?? row?.selected ?? row?.choice ?? row?.text ?? row?.response));
    add("اللون", item.color); add("المقاس", item.size); add("الخامة", item.material);
    return rows;
}
function toast(message, error = false) {
    const node = document.createElement("div");
    node.textContent = message;
    node.style.cssText = `position:fixed;z-index:9999;bottom:24px;left:24px;max-width:420px;padding:12px 16px;border-radius:14px;color:white;font-weight:800;background:${error ? "#be123c" : "#047857"};box-shadow:0 12px 30px #0003`;
    document.body.appendChild(node);
    setTimeout(() => node.remove(), 3500);
}
async function loadDetail(orderNumber) {
    const { data } = await api.get(`/order-reviews-v1/${encodeURIComponent(orderNumber)}`);
    detail = data;
    activeOrder = orderNumber;
    return data;
}
async function refresh() {
    const number = orderNumberFromPage();
    if (!number || refreshing) return;
    refreshing = true;
    try { await loadDetail(number); decorateCards(); }
    catch { /* regular page owns load errors */ }
    finally { refreshing = false; }
}
function itemForCard(card) {
    const sku = skuFromCard(card);
    return (detail?.items || []).find((item) => text(item.sku) === sku) || null;
}
async function uploadImage(item, file, button) {
    if (!file) return;
    if (!/^image\/(jpeg|png|webp)$/i.test(file.type)) return toast("الصيغ المسموحة JPG أو PNG أو WEBP", true);
    if (file.size > 5 * 1024 * 1024) return toast("الحد الأقصى للصورة 5 MB", true);
    button.disabled = true; button.textContent = "جارٍ الرفع…";
    try {
        const data_base64 = await fileToBase64(file);
        const { data } = await api.post(`/order-reviews-v1/${encodeURIComponent(activeOrder)}/items/${encodeURIComponent(item.order_item_id)}/mezan-images`, { filename: file.name, content_type: file.type, data_base64 });
        detail = data; toast("تمت إضافة صورة ميزان لهذا المنتج"); decorateCards();
    } catch (error) { toast(error?.response?.data?.detail?.message || "تعذر رفع صورة ميزان", true); }
    finally { button.disabled = false; button.textContent = "إضافة صورة ميزان"; }
}
function showChoiceModal(item, imageUrl) {
    const specs = specsFor(item); const selected = new Set();
    const overlay = document.createElement("div");
    overlay.style.cssText = "position:fixed;inset:0;z-index:9998;background:#02061788;display:flex;align-items:center;justify-content:center;padding:16px;direction:rtl";
    const panel = document.createElement("div");
    panel.style.cssText = "background:white;width:min(620px,100%);max-height:92vh;overflow:auto;border-radius:28px;padding:20px";
    panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:start;gap:12px"><div><h2 style="margin:0;font-size:24px">حفظ صورة التجهيز</h2><p style="color:#64748b">صورة ميزان خاصة بإدارة التجهيز فقط.</p></div><button data-close style="border:1px solid #ddd;border-radius:12px;background:white;padding:8px 12px;font-size:20px">×</button></div><img src="${imageUrl}" style="display:block;width:170px;height:170px;object-fit:cover;border-radius:20px;margin:18px auto;border:1px solid #ddd"><h3>الخيارات التي تُحفظ معها الصورة</h3><div data-specs></div><div data-summary style="display:none;margin-top:12px;padding:12px;border-radius:12px;background:#f0f9ff"></div><div style="display:grid;gap:10px;margin-top:18px"><button data-mode="order_only" style="padding:14px;border:1px solid #ddd;border-radius:14px;background:white;font-weight:800">حفظ لهذا الطلب فقط</button><button data-mode="options" disabled style="padding:14px;border:0;border-radius:14px;background:#7c3aed;color:white;font-weight:800;opacity:.45">حفظ مع الخيارات المحددة</button><button data-mode="default" style="padding:14px;border:0;border-radius:14px;background:#047857;color:white;font-weight:800">حفظ كصورة رئيسية في ميزان</button></div>`;
    const specsHost = panel.querySelector("[data-specs]"); const summary = panel.querySelector("[data-summary]"); const optionsButton = panel.querySelector('[data-mode="options"]'); const defaultButton = panel.querySelector('[data-mode="default"]');
    specs.forEach((spec) => {
        const label = document.createElement("label"); label.style.cssText = "display:flex;gap:10px;align-items:start;padding:12px;margin:8px 0;border-radius:12px;background:#f5f3ff";
        const input = document.createElement("input"); input.type = "checkbox";
        const span = document.createElement("span"); span.innerHTML = `<b>${spec.name}:</b> ${spec.value}`;
        input.onchange = () => { input.checked ? selected.add(spec.key) : selected.delete(spec.key); optionsButton.disabled = selected.size === 0; optionsButton.style.opacity = selected.size ? "1" : ".45"; defaultButton.disabled = selected.size > 0; defaultButton.style.opacity = selected.size ? ".45" : "1"; summary.style.display = selected.size ? "block" : "none"; summary.textContent = selected.size ? `سيتم ربطها بـ: ${specs.filter((row) => selected.has(row.key)).map((row) => `${row.name} = ${row.value}`).join(" · ")}` : ""; };
        label.append(input, span); specsHost.appendChild(label);
    });
    panel.querySelector("[data-close]").onclick = () => overlay.remove();
    panel.querySelectorAll("[data-mode]").forEach((button) => button.onclick = async () => {
        const mode = button.dataset.mode; if (button.disabled) return;
        panel.querySelectorAll("button").forEach((node) => { node.disabled = true; }); button.textContent = "جارٍ الحفظ…";
        try {
            const { data } = await api.post(`/order-reviews-v1/${encodeURIComponent(activeOrder)}/items/${encodeURIComponent(item.order_item_id)}/image-choice`, { expected_revision: detail.revision, selected_image_url: imageUrl, mode, selected_spec_keys: mode === "options" ? [...selected] : [] });
            detail = data; overlay.remove(); toast(mode === "default" ? "تم حفظها كصورة رئيسية في ميزان" : mode === "options" ? "تم حفظها مع الخيارات المحددة" : "تم حفظها لهذا الطلب فقط"); decorateCards();
        } catch (error) { toast(error?.response?.data?.detail?.message || "تعذر حفظ الصورة", true); panel.querySelectorAll("button").forEach((node) => { node.disabled = false; }); }
    });
    overlay.appendChild(panel); document.body.appendChild(overlay);
}
async function deleteImage(item, imageUrl) {
    const id = imageUrl.split("/").pop();
    if (!window.confirm("حذف صورة ميزان؟ لن يؤثر ذلك على صور سلة.")) return;
    try { const { data } = await api.delete(`/order-reviews-v1/${encodeURIComponent(activeOrder)}/items/${encodeURIComponent(item.order_item_id)}/mezan-images/${encodeURIComponent(id)}`); detail = data; toast("تم حذف صورة ميزان"); decorateCards(); }
    catch (error) { toast(error?.response?.data?.detail?.message || "تعذر حذف صورة ميزان", true); }
}
function decorateCards() {
    if (!detail) return;
    document.querySelectorAll('[data-testid="order-review-product-card"]').forEach((card) => {
        const item = itemForCard(card); if (!item) return;
        let host = card.querySelector("[data-mezan-image-tools]");
        if (!host) { host = document.createElement("div"); host.dataset.mezanImageTools = "1"; host.style.cssText = "border-top:1px solid #e2e8f0;padding:12px;background:#f0fdfa"; card.appendChild(host); }
        host.innerHTML = "";
        const top = document.createElement("div"); top.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap";
        const title = document.createElement("b"); title.textContent = "صور ميزان — تظهر في انتظار المراجعة فقط";
        const label = document.createElement("label"); label.style.cssText = "cursor:pointer;padding:8px 12px;border-radius:10px;background:#0f766e;color:white;font-weight:800"; label.textContent = "إضافة صورة ميزان";
        const input = document.createElement("input"); input.type = "file"; input.accept = "image/jpeg,image/png,image/webp"; input.hidden = true; input.onchange = () => { uploadImage(item, input.files?.[0], label); input.value = ""; }; label.appendChild(input); top.append(title, label); host.appendChild(top);
        const strip = document.createElement("div"); strip.style.cssText = "display:flex;gap:10px;overflow:auto;margin-top:10px";
        (item.mezan_images || []).forEach((url) => {
            const box = document.createElement("div"); box.style.cssText = "position:relative;flex:0 0 86px";
            const img = document.createElement("img"); img.src = url; img.style.cssText = "width:86px;height:86px;object-fit:cover;border-radius:12px;border:2px solid #0d9488;cursor:pointer"; img.onclick = () => showChoiceModal(item, url);
            const badge = document.createElement("span"); badge.textContent = "صورة ميزان"; badge.style.cssText = "position:absolute;right:3px;top:3px;background:#0f766e;color:white;border-radius:8px;padding:2px 5px;font-size:9px;font-weight:800";
            const del = document.createElement("button"); del.textContent = "حذف"; del.style.cssText = "width:100%;margin-top:4px;border:1px solid #fecdd3;background:white;color:#be123c;border-radius:8px;padding:4px;font-weight:700"; del.onclick = () => deleteImage(item, url);
            box.append(img, badge, del); strip.appendChild(box);
        });
        host.appendChild(strip);
    });
}
function start() {
    if (document.getElementById(ROOT_ID)) return;
    const marker = document.createElement("div"); marker.id = ROOT_ID; marker.hidden = true; document.body.appendChild(marker);
    const observer = new MutationObserver(() => { const number = orderNumberFromPage(); if (!number) return; if (number !== activeOrder || !detail) refresh(); else decorateCards(); });
    observer.observe(document.body, { childList: true, subtree: true }); refresh();
}

if (typeof window !== "undefined") window.addEventListener("DOMContentLoaded", start, { once: true });
