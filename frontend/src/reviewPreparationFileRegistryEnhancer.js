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

function createHistorySection(stage) {
    const section = node("section");
    section.id = HISTORY_ID;
    section.style.cssText = "border:1px solid #e2e8f0;border-radius:16px;background:white;padding:14px;box-shadow:0 1px 3px #0f172a12";
    const first = stage.firstElementChild;
    if (first?.nextSibling) stage.insertBefore(section, first.nextSibling);
    else stage.appendChild(section);
    return section;
}

async function renderHistory({ force = false } = {}) {
    const stage = document.querySelector('[data-testid="reviewed-orders-stage"]');
    if (!stage || historyLoading) return;
    let section = document.getElementById(HISTORY_ID);
    if (section && !force) return;
    if (section && force) section.remove();
    section = createHistorySection(stage);
    historyLoading = true;

    const header = node("div");
    header.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:10px";
    const headerText = node("div");
    const heading = node("h3", "سجل ملفات التجهيز");
    heading.style.cssText = "margin:0;font-size:17px;font-weight:950;color:#0f172a";
    const description = node("p", "رقم الملف والموظف والكمية والتاريخ وإعادة تحميل PDF.");
    description.style.cssText = "margin:3px 0 0;font-size:11px;color:#64748b";
    headerText.append(heading, description);
    const count = node("span", "…");
    count.style.cssText = "font-size:11px;color:#7c3aed";
    header.append(headerText, count);
    section.appendChild(header);

    try {
        const files = (await listPreparationFiles({ limit: 30 })).items || [];
        count.textContent = String(files.length);
        count.style.cssText = "display:inline-flex;min-width:28px;height:28px;align-items:center;justify-content:center;border-radius:999px;background:#ede9fe;color:#6d28d9;font-size:12px;font-weight:950";
        if (!files.length) {
            const empty = node("div", "لا توجد ملفات تجهيز محفوظة حتى الآن.");
            empty.style.cssText = "margin-top:12px;border:1px dashed #cbd5e1;border-radius:12px;padding:18px;text-align:center;color:#64748b;font-size:13px";
            section.appendChild(empty);
            return;
        }
        const list = node("div");
        list.style.cssText = "display:grid;gap:8px;margin-top:12px";
        files.forEach((file) => {
            const row = node("article");
            row.dataset.preparationFileNumber = file.file_number || "";
            row.style.cssText = "display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center;border:1px solid #e2e8f0;border-radius:13px;padding:11px;background:#f8fafc";
            const info = node("div");
            const name = node("div", preparationFileRecordLabel(file));
            name.style.cssText = "font-size:13px;font-weight:950;color:#0f172a;overflow-wrap:anywhere";
            const meta = node("div", `${file.allocated_quantity} قطعة • ${file.file_date_display || file.file_date} • المسؤول: ${file.responsible_employee_name || "—"}`);
            meta.style.cssText = "margin-top:4px;font-size:11px;font-weight:700;color:#64748b";
            info.append(name, meta);
            const download = node("button", "PDF");
            download.type = "button";
            download.style.cssText = "min-height:40px;border:0;border-radius:11px;background:#6d28d9;padding:0 14px;color:white;font-weight:950";
            download.onclick = async () => {
                if (!file.batch_id || download.disabled) return;
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
    } catch (loadError) {
        count.textContent = "!";
        const warning = node("div", loadError.message);
        warning.style.cssText = "margin-top:12px;border-radius:12px;background:#fff1f2;padding:12px;color:#be123c;font-size:12px;font-weight:800";
        section.appendChild(warning);
    } finally {
        historyLoading = false;
    }
}

function scheduleInitialHistory() {
    if (scheduled || document.getElementById(HISTORY_ID)) return;
    scheduled = true;
    window.requestAnimationFrame(() => {
        scheduled = false;
        if (!document.getElementById(HISTORY_ID)) renderHistory();
    });
}

function start() {
    if (!document.body || document.getElementById(ROOT_ID)) return;
    const marker = node("div");
    marker.id = ROOT_ID;
    marker.hidden = true;
    document.body.appendChild(marker);
    document.addEventListener("click", captureCreate, true);
    window.addEventListener("mezan:preparation-file-created", () => renderHistory({ force: true }));
    const observer = new MutationObserver(() => {
        if (!document.getElementById(HISTORY_ID)) scheduleInitialHistory();
    });
    observer.observe(document.body, { childList: true, subtree: true });
    scheduleInitialHistory();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
    if (document.readyState === "loading") window.addEventListener("DOMContentLoaded", start, { once: true });
    else start();
}
