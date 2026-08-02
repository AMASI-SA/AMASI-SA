import { listPreparationFileEmployees } from "./services/orderReviewEngine";
import {
    preparationFileMetadataPayload,
    readSelectionMetric,
    riyadhDateParts,
} from "./preparationFileRegistryUi";

const ROOT_ID = "mezan-preparation-file-metadata-enhancer";
const bypassButtons = new WeakSet();
let modalOpen = false;
let employeeCache = null;

const text = (value) => String(value || "").trim();

function node(tag, content = "") {
    const element = document.createElement(tag);
    if (content) element.textContent = content;
    return element;
}

async function employees() {
    if (employeeCache) return employeeCache;
    employeeCache = (await listPreparationFileEmployees()).items || [];
    return employeeCache;
}

function showMetadataModal({ quantity, productCount }) {
    return new Promise(async (resolve) => {
        if (modalOpen) return resolve(null);
        modalOpen = true;

        const overlay = node("div");
        overlay.dataset.preparationFileMetadataModal = "1";
        overlay.style.cssText = "position:fixed;inset:0;z-index:14000;background:#02061799;display:flex;align-items:center;justify-content:center;padding:14px;direction:rtl";
        const panel = node("div");
        panel.style.cssText = "width:min(560px,100%);max-height:92vh;overflow:auto;border-radius:22px;background:white;padding:20px;box-shadow:0 28px 90px #0005";

        const heading = node("h2", "بيانات ملف التجهيز");
        heading.style.cssText = "margin:0;font-size:22px;font-weight:950;color:#0f172a";
        const description = node("p", "اكتب اسم الملف وحدد الموظف المسؤول قبل إنشاء PDF.");
        description.style.cssText = "margin:6px 0 18px;color:#64748b;font-size:13px";

        const titleLabel = node("label", "اسم الملف");
        titleLabel.style.cssText = "display:block;font-size:13px;font-weight:900;color:#334155";
        const titleInput = node("input");
        titleInput.type = "text";
        titleInput.maxLength = 120;
        titleInput.value = "تجهيز المنتجات";
        titleInput.placeholder = "مثال: دفعة سلاسل الأسماء";
        titleInput.style.cssText = "width:100%;margin-top:7px;border:1px solid #cbd5e1;border-radius:12px;padding:12px;font-size:15px;outline:none";

        const stats = node("div");
        stats.style.cssText = "display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:14px";
        const date = riyadhDateParts();
        [["التاريخ", date.display], ["عدد القطع", quantity], ["المنتجات", productCount]].forEach(([label, value]) => {
            const card = node("div");
            card.style.cssText = "border-radius:12px;background:#f8fafc;padding:10px;text-align:center";
            const small = node("div", label);
            small.style.cssText = "font-size:10px;font-weight:800;color:#94a3b8";
            const strong = node("div", String(value));
            strong.style.cssText = "margin-top:3px;font-size:16px;font-weight:950;color:#0f172a";
            card.append(small, strong);
            stats.appendChild(card);
        });

        const employeeLabel = node("label", "الموظف المسؤول");
        employeeLabel.style.cssText = "display:block;margin-top:15px;font-size:13px;font-weight:900;color:#334155";
        const select = node("select");
        select.style.cssText = "width:100%;margin-top:7px;border:1px solid #cbd5e1;border-radius:12px;padding:12px;background:white;font-size:15px;outline:none";
        const placeholder = node("option", "اختر الموظف المسؤول");
        placeholder.value = "";
        select.appendChild(placeholder);

        const error = node("div");
        error.style.cssText = "display:none;margin-top:12px;border-radius:10px;background:#fff1f2;padding:10px;color:#be123c;font-size:13px;font-weight:800";
        const actions = node("div");
        actions.style.cssText = "display:flex;gap:9px;margin-top:18px";
        const cancel = node("button", "إلغاء");
        cancel.type = "button";
        cancel.style.cssText = "min-height:46px;border:1px solid #cbd5e1;border-radius:12px;background:white;padding:0 18px;font-weight:900;color:#475569";
        const save = node("button", "حفظ وإنشاء PDF");
        save.type = "button";
        save.style.cssText = "min-height:46px;flex:1;border:0;border-radius:12px;background:#6d28d9;padding:0 18px;font-weight:950;color:white";
        actions.append(cancel, save);

        function close(value) {
            modalOpen = false;
            overlay.remove();
            resolve(value);
        }

        cancel.onclick = () => close(null);
        overlay.onclick = (event) => {
            if (event.target === overlay) close(null);
        };
        save.onclick = () => {
            const payload = preparationFileMetadataPayload({
                fileTitle: titleInput.value,
                responsibleEmployeeId: select.value,
                expectedQuantity: quantity,
                selectedProductCount: productCount,
            });
            if (!payload.fileTitle || !payload.responsibleEmployeeId) {
                error.textContent = "اكتب اسم الملف واختر الموظف المسؤول.";
                error.style.display = "block";
                return;
            }
            close(payload);
        };

        panel.append(heading, description, titleLabel, titleInput, stats, employeeLabel, select, error, actions);
        overlay.appendChild(panel);
        document.body.appendChild(overlay);
        titleInput.focus();

        try {
            const rows = await employees();
            rows.forEach((employee) => {
                const option = node("option", employee.name || employee.email || employee.id);
                option.value = employee.id;
                select.appendChild(option);
            });
            if (rows.length === 1) select.value = rows[0].id;
            if (!rows.length) throw new Error("لا يوجد موظف نشط يملك صلاحية إدارة التجهيز.");
        } catch (loadError) {
            error.textContent = loadError.message;
            error.style.display = "block";
            save.disabled = true;
            save.style.opacity = ".5";
        }
    });
}

function isCreateButton(button) {
    return Boolean(button)
        && text(button.textContent).includes("إنشاء وتحميل الملف")
        && Boolean(button.closest('[data-testid="reviewed-preparation-selection-bar"]'));
}

async function captureCreate(event) {
    const button = event.target?.closest?.("button");
    if (!isCreateButton(button)) return;
    if (bypassButtons.has(button)) {
        bypassButtons.delete(button);
        return;
    }

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const bar = button.closest('[data-testid="reviewed-preparation-selection-bar"]');
    const quantity = readSelectionMetric(bar, "قطع هذا الملف");
    const productCount = readSelectionMetric(bar, "المنتجات المحددة");
    if (!quantity || !productCount) return;

    const metadata = await showMetadataModal({ quantity, productCount });
    if (!metadata) return;
    window.__mezanPreparationFileMetadata = metadata;

    const originalConfirm = window.confirm;
    try {
        window.confirm = () => true;
        bypassButtons.add(button);
        button.click();
    } finally {
        window.confirm = originalConfirm;
    }
}

function start() {
    if (!document.body || document.getElementById(ROOT_ID)) return;
    const marker = node("div");
    marker.id = ROOT_ID;
    marker.hidden = true;
    document.body.appendChild(marker);
    document.addEventListener("click", captureCreate, true);
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
    if (document.readyState === "loading") window.addEventListener("DOMContentLoaded", start, { once: true });
    else start();
}
