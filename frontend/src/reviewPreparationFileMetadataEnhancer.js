import { listPreparationFileEmployees } from "./services/orderReviewEngine";
import {
    preparationFileMetadataPayload,
    readSelectedProductCount,
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

function riyadhLocalDateTimeToIso(value) {
    const normalized = text(value);
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(normalized)) return null;
    const parsed = new Date(`${normalized}:00+03:00`);
    return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
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
        panel.style.cssText = "width:min(600px,100%);max-height:92vh;overflow:auto;border-radius:22px;background:white;padding:20px;box-shadow:0 28px 90px #0005";

        const heading = node("h2", "بيانات ملف التجهيز");
        heading.style.cssText = "margin:0;font-size:22px;font-weight:950;color:#0f172a";
        const description = node("p", "اكتب اسم الملف وحدد موظف التجهيز والوقت المطلوب قبل إنشاء PDF.");
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

        const employeeLabel = node("label", "موظف التجهيز المسؤول عن الملف");
        employeeLabel.style.cssText = "display:block;margin-top:15px;font-size:13px;font-weight:900;color:#334155";
        const select = node("select");
        select.style.cssText = "width:100%;margin-top:7px;border:1px solid #cbd5e1;border-radius:12px;padding:12px;background:white;font-size:15px;outline:none";
        const placeholder = node("option", "اختر الموظف المسؤول");
        placeholder.value = "";
        select.appendChild(placeholder);

        const scheduleTitle = node("div", "وقت التجهيز");
        scheduleTitle.style.cssText = "margin-top:16px;font-size:13px;font-weight:900;color:#334155";
        const scheduleChoices = node("div");
        scheduleChoices.style.cssText = "display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:8px";

        const automaticLabel = node("label");
        automaticLabel.style.cssText = "display:flex;gap:9px;align-items:flex-start;border:2px solid #7c3aed;border-radius:13px;padding:11px;background:#f5f3ff;cursor:pointer";
        const automaticRadio = node("input");
        automaticRadio.type = "radio";
        automaticRadio.name = "preparation-file-schedule";
        automaticRadio.value = "automatic";
        automaticRadio.checked = true;
        const automaticCopy = node("span");
        automaticCopy.innerHTML = "<strong style='display:block;color:#4c1d95'>وقت تلقائي</strong><small style='display:block;margin-top:3px;color:#6d28d9;line-height:1.5'>حسب متوسط التجهيز السابق، ويمكن تمييز التأخير المتوقع.</small>";
        automaticLabel.append(automaticRadio, automaticCopy);

        const requiredLabel = node("label");
        requiredLabel.style.cssText = "display:flex;gap:9px;align-items:flex-start;border:1px solid #cbd5e1;border-radius:13px;padding:11px;background:white;cursor:pointer";
        const requiredRadio = node("input");
        requiredRadio.type = "radio";
        requiredRadio.name = "preparation-file-schedule";
        requiredRadio.value = "required";
        const requiredCopy = node("span");
        requiredCopy.innerHTML = "<strong style='display:block;color:#881337'>موعد إجباري</strong><small style='display:block;margin-top:3px;color:#9f1239;line-height:1.5'>يظهر للموظف كملف مميز يجب إنجازه قبل الوقت المحدد.</small>";
        requiredLabel.append(requiredRadio, requiredCopy);
        scheduleChoices.append(automaticLabel, requiredLabel);

        const requiredTimeWrap = node("label", "التاريخ والساعة الإلزامية — بتوقيت الرياض");
        requiredTimeWrap.style.cssText = "display:none;margin-top:10px;font-size:12px;font-weight:900;color:#881337";
        const requiredTime = node("input");
        requiredTime.type = "datetime-local";
        requiredTime.style.cssText = "display:block;width:100%;box-sizing:border-box;margin-top:7px;border:1px solid #fda4af;border-radius:12px;padding:12px;background:#fff1f2;font-size:15px;outline:none";
        requiredTimeWrap.appendChild(requiredTime);

        function refreshSchedulePresentation() {
            const required = requiredRadio.checked;
            requiredTimeWrap.style.display = required ? "block" : "none";
            automaticLabel.style.border = required ? "1px solid #cbd5e1" : "2px solid #7c3aed";
            automaticLabel.style.background = required ? "white" : "#f5f3ff";
            requiredLabel.style.border = required ? "2px solid #e11d48" : "1px solid #cbd5e1";
            requiredLabel.style.background = required ? "#fff1f2" : "white";
        }
        automaticRadio.onchange = refreshSchedulePresentation;
        requiredRadio.onchange = refreshSchedulePresentation;

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
            const scheduleMode = requiredRadio.checked ? "required" : "automatic";
            const requiredDueAt = scheduleMode === "required"
                ? riyadhLocalDateTimeToIso(requiredTime.value)
                : null;
            const payload = {
                ...preparationFileMetadataPayload({
                    fileTitle: titleInput.value,
                    responsibleEmployeeId: select.value,
                    expectedQuantity: quantity,
                    selectedProductCount: productCount,
                }),
                scheduleMode,
                requiredDueAt,
            };
            if (!payload.fileTitle || !payload.responsibleEmployeeId) {
                error.textContent = "اكتب اسم الملف واختر الموظف المسؤول.";
                error.style.display = "block";
                return;
            }
            if (scheduleMode === "required" && !requiredDueAt) {
                error.textContent = "حدد التاريخ والساعة للموعد الإجباري.";
                error.style.display = "block";
                requiredTime.focus();
                return;
            }
            close(payload);
        };

        panel.append(
            heading,
            description,
            titleLabel,
            titleInput,
            stats,
            employeeLabel,
            select,
            scheduleTitle,
            scheduleChoices,
            requiredTimeWrap,
            error,
            actions,
        );
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
    const productCount = readSelectedProductCount(bar);
    if (!quantity || !productCount) return;

    const metadata = await showMetadataModal({ quantity, productCount });
    if (!metadata) return;
    window.__mezanPreparationFileMetadata = metadata;
    window.__mezanPreparationFileSchedulePending = {
        mode: metadata.scheduleMode,
        requiredDueAt: metadata.requiredDueAt,
    };

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

export { riyadhLocalDateTimeToIso };
