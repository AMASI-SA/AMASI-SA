import {
    downloadReviewedPreparationBatchPdf,
    listPreparationFileEmployees,
    listPreparationFiles,
} from "./services/orderReviewEngine";
import {
    preparationFileMetadataPayload,
    preparationFileRecordLabel,
    readSelectionMetric,
    riyadhDateParts,
} from "./preparationFileRegistryUi";

const ROOT_ID = "mezan-preparation-file-registry-enhancer";
const HISTORY_ID = "mezan-preparation-file-history";
const bypassButtons = new WeakSet();
let modalOpen = false;
let employeeCache = null;
let historyLoading = false;
let scheduled = false;

const text = (value) => String(value || "").trim();

function createElement(tag, className = "", content = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content) node.textContent = content;
    return node;
}

async function loadEmployees() {
    if (employeeCache) return employeeCache;
    const result = await listPreparationFileEmployees();
    employeeCache = result.items || [];
    return employeeCache;
}

function metadataModal({ quantity, productCount }) {
    return new Promise(async (resolve) => {
        if (modalOpen) return resolve(null);
        modalOpen = true;
        const overlay = createElement("div");
        overlay.dataset.preparationFileMetadataModal = "1";
        overlay.style.cssText = "position:fixed;inset:0;z-index:14000;background:#02061799;display:flex;align-items:center;justify-content:center;padding:14px;direction:rtl";
        const panel = createElement("div");
        panel.style.cssText = "width:min(560px,100%);max-height:92vh;overflow:auto;border-radius:22px;background:white;padding:20px;box-shadow:0 28px 90px #0005";
        const title = createElement("h2", "", "بيانات ملف التجهيز");
        title.style.cssText = "margin:0;font-size:22px;font-weight:950;color:#0f172a";
        const subtitle = createElement("p", "", "احفظ اسم الملف وحدد الموظف المسؤول قبل إنشاء PDF.");
        subtitle.style.cssText = "margin:6px 0 18px;color:#64748b;font-size:13px";

        const labelName = createElement("label", "", "اسم الملف");
        labelName.style.cssText = "display:block;font-size:13px;font-weight:900;color:#334155";
        const nameInput = createElement("input");
        nameInput.type = "text";
        nameInput.maxLength = 120;
        nameInput.value = "تجهيز المنتجات";
        nameInput.placeholder = "مثال: دفعة سلاسل الأسماء";
        nameInput.style.cssText = "width:100%;margin-top:7px;border:1px solid #cbd5e1;border-radius:12px;padding:12px;font-size:15px;outline:none";

        const stats = createElement("div");
        stats.style.cssText = "display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:14px";
        const date = riyadhDateParts();
        [["التاريخ", date.display], ["عدد القطع", String(quantity)], ["المنتجات", String(productCount)]].forEach(([label, value]) => {
            const card = createElement("div");
            card.style.cssText = "border-radius:12px;background:#f8fafc;padding:10px;text-align:center";
            const small = createElement("div", "", label);
            small.style.cssText = "font-size:10px;font-weight:800;color:#94a3b8";
            const strong = createElement("div", "", value);
            strong.style.cssText = "margin-top:3px;font-size:16px;font-weight:950;color:#0f172a";
            card.append(small, strong);
            stats.appendChild(card);
        });

        const employeeLabel = createElement("label", "", "الموظف المسؤول");
        employeeLabel.style.cssText = "display:block;margin-top:15px;font-size:13px;font-weight:900;color:#334155";
        const select = createElement("select");
        select.style.cssText = "width:100%;margin-top:7px;border:1px solid #cbd5e1;border-radius:12px;padding:12px;background:white;font-size:15px;outline:none";
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "اختر الموظف المسؤول";
        select.appendChild(placeholder);

        const error = createElement("div");
        error.style.cssText = "display:none;margin-top:12px;border-radius:10px;background:#fff1f2;padding:10px;color:#be123c;font-size:13px;font-weight:800";
        const actions = createElement("div");
        actions.style.cssText = "display:flex;gap:9px;margin-top:18px";
        const cancel = createElement("button", "", "إلغاء");
        cancel.type = "button";
        cancel.style.cssText = "min-height:46px;border:1px solid #cbd5e1;border-radius:12px;background:white;padding:0 18px;font-weight:900;color:#475569";
        const save = createElement("button", "", "حفظ وإنشاء PDF");
        save.type = "button";
        save.style.cssText = "min-height:46px;flex:1;border:0;border-radius:12px;background:#6d28d9;padding:0 18px;font-weight:950;color:white";

        function close(value) {
            modalOpen = false;
            overlay.remove();
            resolve(value);
        }
        cancel.onclick = () => close(null);
        overlay.addEventListener("click", (event) => {
            if (event.target === overlay) close(null);
        });
        save.onclick = () => {
            const payload = preparationFileMetadataPayload({
                fileTitle: nameInput.value,
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

        panel.append(title, subtitle, labelName, nameInput, stats, employeeLabel, select, error, actions);
        actions.append(cancel, save);
        overlay.appendChild(panel);
        document.body.appendChild(overlay);
        nameInput.focus();

        try {
            const employees = await loadEmployees();
            employees.forEach((employee) => {
                const option = document.createElement("option");
                option.value = employee.id;
                option.textContent = employee.name || employee.email || employee.id;
                select.appendChild(option);
            });
            if (employees.length === 1) select.value = employees[0].id;
            if (!employees.length) {
                error.textContent = "لا يوجد موظف نشط يملك صلاحية إدارة التجهيز.";
                error.style.display = "block";
                save.disabled = true;
                save.style.opacity = ".5";
            }
        } catch (loadError) {
            error.textContent = loadError.message;
            error.style.display = "block";
            save.disabled = true;
            save.style.opacity = ".5";
        }
    });
}

function isCreateFileButton(button) {
    return Boolean(button)
        && text(button.textContent).includes("إنشاء وتحميل الملف")
        && Boolean(button.closest('[data-testid="reviewed-preparation-selection-bar"]'));
}

async function captureCreateFile(event) {
    const button = event.target?.closest?.("button");
    if (!isCreateFileButton(button)) return;
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
    const metadata = await metadataModal({ quantity, productCount });
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

function historySection(stage) {
    let section = document.getElementById(HISTORY_ID);
    if (section) return section;
    section = createElement("section");
    section.id = HISTORY_ID;
    section.style.cssText = "border:1px solid #e2e8f0;border-radius:16px;background:white;padding:14px;box-shadow:0 1px 3px #0f172a12";
    const firstCard = stage.firstElementChild;
    if (firstCard?.nextSibling) stage.insertBefore(section, firstCard.nextSibling);
    else stage.appendChild(section);
    return section;
}

async function renderHistory() {
    const stage = document.querySelector('[data-testid="reviewed-orders-stage"]');
    if (!stage || historyLoading) return;
    const section = historySection(stage);
    historyLoading = true;
    section.innerHTML = "";
    const header = createElement("div");
    header.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:10px";
    const heading = createElement("div");
    const h3 = createElement("h3", "", "سجل ملفات التجهيز");
    h3.style.cssText = "margin:0;font-size:17px;font-weight:950;color:#0f172a";
    const p = createElement("p", "", "الملفات المحفوظة مع رقم الملف والموظف والكمية والتاريخ.");
    p.style.cssText = "margin:3px 0 0;font-size:11px;color:#64748b";
    heading.append(h3, p);
    const loading = createElement("span", "", "جارٍ التحميل…");
    loading.style.cssText = "font-size:11px;color:#7c3aed";
    header.append(heading, loading);
    section.appendChild(header);

    try {
        const result = await listPreparationFiles({ limit: 30 });
        loading.textContent = String(result.items.length);
        loading.style.cssText = "display:inline-flex;min-width:28px;height:28px;align-items:center;justify-content:center;border-radius:999px;background:#ede9fe;color:#6d28d9;font-size:12px;font-weight:950";
        if (!result.items.length) {
            const empty = createElement("div", "", "لا توجد ملفات تجهيز محفوظة حتى الآن.");
            empty.style.cssText = "margin-top:12px;border:1px dashed #cbd5e1;border-radius:12px;padding:18px;text-align:center;color:#64748b;font-size:13px";
            section.appendChild(empty);
            return;
        }
        const list = createElement("div");
        list.style.cssText = "display:grid;gap:8px;margin-top:12px";
        result.items.forEach((file) => {
            const row = createElement("article");
            row.dataset.preparationFileNumber = file.file_number || "";
            row.style.cssText = "display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center;border:1px solid #e2e8f0;border-radius:13px;padding:11px;background:#f8fafc";
            const info = createElement("div");
            const name = createElement("div", "", preparationFileRecordLabel(file));
            name.style.cssText = "font-size:13px;font-weight:950;color:#0f172a;overflow-wrap:anywhere";
            const meta = createElement("div", "", `${file.allocated_quantity} قطعة • ${file.file_date_display || file.file_date} • المسؤول: ${file.responsible_employee_name || "—"}`);
            meta.style.cssText = "margin-top:4px;font-size:11px;font-weight:700;color:#64748b";
            info.append(name, meta);
            const download = createElement("button", "", "PDF");
            download.type = "button";
            download.style.cssText = "min-height:40px;border:0;border-radius:11px;background:#6d28d9;padding:0 14px;color:white;font-weight:950";
            download.onclick = async () => {
                if (!file.batch_id) return;
                download.disabled = true;
                const original = download.textContent;
                download.textContent = "…";
                try {
                    await downloadReviewedPreparationBatchPdf(file.batch_id, file.file_name);
                } finally {
                    download.disabled = false;
                    download.textContent = original;
                }
            };
            row.append(info, download);
            list.appendChild(row);
        });
        section.appendChild(list);
    } catch (error) {
        loading.textContent = "!";
        const warning = createElement("div", "", error.message);
        warning.style.cssText = "margin-top:12px;border-radius:12px;background:#fff1f2;padding:12px;color:#be123c;font-size:12px;font-weight:800";
        section.appendChild(warning);
    } finally {
        historyLoading = false;
    }
}

function scheduleHistory() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(() => {
        scheduled = false;
        if (document.querySelector('[data-testid="reviewed-orders-stage"]')) {
            renderHistory();
        }
    });
}

function start() {
    if (!document.body || document.getElementById(ROOT_ID)) return;
    const marker = createElement("div");
    marker.id = ROOT_ID;
    marker.hidden = true;
    document.body.appendChild(marker);
    document.addEventListener("click", captureCreateFile, true);
    window.addEventListener("mezan:preparation-file-created", () => {
        const existing = document.getElementById(HISTORY_ID);
        existing?.remove();
        renderHistory();
    });
    const observer = new MutationObserver(scheduleHistory);
    observer.observe(document.body, { childList: true, subtree: true });
    scheduleHistory();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
    if (document.readyState === "loading") {
        window.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
}
