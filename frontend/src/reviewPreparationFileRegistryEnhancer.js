import {
    downloadReviewedPreparationBatchPdf,
    listPreparationFileEmployees,
    listPreparationFiles,
} from "./services/orderReviewEngine";
import {
    preparationFileMetadataPayload,
    preparationFileRecordLabel,
    readSelectedProductCount,
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
        overlay.style.cssText = "position:fixed;inset:0;z-index:14000;background:#02061799;display:flex;align-items:center;justify-content:center;padding:12px;direction:rtl";
        const panel = node("div");
        panel.style.cssText = "width:min(430px,100%);max-height:94vh;overflow:auto;border-radius:22px;background:white;padding:16px;box-shadow:0 28px 90px #0005";

        const heading = node("h2", "بيانات ملف المنتجات");
        heading.style.cssText = "margin:0;font-size:20px;font-weight:950;color:#74102f;text-align:right";
        const description = node("p", "حدد المورد والموظف المسؤول. سيظهر الاسمان معًا في رأس ملف A4.");
        description.style.cssText = "margin:6px 0 16px;color:#64748b;font-size:12px;line-height:20px";

        const titleLabel = node("label", "اسم المورد");
        titleLabel.style.cssText = "display:block;font-size:12px;font-weight:900;color:#334155";
        const titleInput = node("input");
        titleInput.type = "text";
        titleInput.maxLength = 120;
        titleInput.value = "";
        titleInput.placeholder = "اكتب اسم المورد كما سيظهر في الملف";
        titleInput.autocomplete = "off";
        titleInput.style.cssText = "box-sizing:border-box;width:100%;margin-top:7px;border:1px solid #d8bcc7;border-radius:12px;padding:12px;font-size:14px;outline:none;text-align:right";

        const stats = node("div");
        stats.style.cssText = "display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:12px";
        const date = riyadhDateParts();
        [["التاريخ", date.display], ["عدد القطع", quantity], ["المنتجات", productCount]].forEach(([label, value]) => {
            const card = node("div");
            card.style.cssText = "border:1px solid #f0e2e8;border-radius:12px;background:#fffafb;padding:9px;text-align:center";
            const small = node("div", label);
            small.style.cssText = "font-size:9px;font-weight:800;color:#9b7280";
            const strong = node("div", String(value));
            strong.style.cssText = "margin-top:3px;font-size:15px;font-weight:950;color:#74102f";
            card.append(small, strong);
            stats.appendChild(card);
        });

        const employeeLabel = node("label", "الموظف المسؤول");
        employeeLabel.style.cssText = "display:block;margin-top:14px;font-size:12px;font-weight:900;color:#334155";
        const select = node("select");
        select.style.cssText = "box-sizing:border-box;width:100%;margin-top:7px;border:1px solid #d8bcc7;border-radius:12px;padding:12px;background:white;font-size:14px;outline:none";
        const placeholder = node("option", "اختر الموظف المسؤول");
        placeholder.value = "";
        select.appendChild(placeholder);

        const preview = node("div");
        preview.style.cssText = "margin-top:12px;border-top:1px solid #ead7df;padding-top:10px;text-align:center;color:#74102f;font-size:11px;font-weight:900";
        const refreshPreview = () => {
            const employee = select.options[select.selectedIndex]?.text || "—";
            preview.textContent = `المورد: ${titleInput.value.trim() || "—"}  |  الموظف المسؤول: ${employee}`;
        };
        titleInput.addEventListener("input", refreshPreview);
        select.addEventListener("change", refreshPreview);
        refreshPreview();

        const error = node("div");
        error.style.cssText = "display:none;margin-top:12px;border-radius:10px;background:#fff1f2;padding:10px;color:#be123c;font-size:12px;font-weight:800";
        const actions = node("div");
        actions.style.cssText = "display:flex;gap:8px;margin-top:16px";
        const cancel = node("button", "إلغاء");
        cancel.type = "button";
        cancel.style.cssText = "min-height:46px;border:1px solid #d8bcc7;border-radius:12px;background:white;padding:0 16px;font-weight:900;color:#74102f";
        const save = node("button", "حفظ وإنشاء PDF");
        save.type = "button";
        save.style.cssText = "min-height:46px;flex:1;border:0;border-radius:12px;background:#74102f;padding:0 16px;font-weight:950;color:white";
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
                // The registry's permanent file title is also the supplier
                // name for this operational file. The PDF renderer reads it
                // back as supplier_name when no later supplier assignment has
                // populated an explicit supplier field yet.
                fileTitle: titleInput.value,
                responsibleEmployeeId: select.value,
                expectedQuantity: quantity,
                selectedProductCount: productCount,
            });
            if (!payload.fileTitle || !payload.responsibleEmployeeId) {
                error.textContent = "اكتب اسم المورد واختر الموظف المسؤول.";
                error.style.display = "block";
                return;
            }
            close(payload);
        };

        panel.append(heading, description, titleLabel, titleInput, stats, employeeLabel, select, preview, error, actions);
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
            refreshPreview();
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
    section.style.cssText = "border:1px solid #ead7df;border-radius:16px;background:white;padding:12px;box-shadow:0 1px 3px #0f172a12";
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
    const heading = node("h3", "سجل ملفات المنتجات");
    heading.style.cssText = "margin:0;font-size:16px;font-weight:950;color:#74102f";
    const description = node("p", "رقم الملف والمورد والموظف والكمية والتاريخ وإعادة تحميل PDF.");
    description.style.cssText = "margin:3px 0 0;font-size:10px;color:#64748b";
    headerText.append(heading, description);
    const count = node("span", "…");
    count.style.cssText = "font-size:11px;color:#74102f";
    header.append(headerText, count);
    section.appendChild(header);

    try {
        const files = (await listPreparationFiles({ limit: 30 })).items || [];
        count.textContent = String(files.length);
        count.style.cssText = "display:inline-flex;min-width:28px;height:28px;align-items:center;justify-content:center;border-radius:999px;background:#f9eaf0;color:#74102f;font-size:12px;font-weight:950";
        if (!files.length) {
            const empty = node("div", "لا توجد ملفات منتجات محفوظة حتى الآن.");
            empty.style.cssText = "margin-top:12px;border:1px dashed #d8bcc7;border-radius:12px;padding:16px;text-align:center;color:#64748b;font-size:12px";
            section.appendChild(empty);
            return;
        }
        const list = node("div");
        list.style.cssText = "display:grid;gap:8px;margin-top:12px";
        files.forEach((file) => {
            const row = node("article");
            row.dataset.preparationFileNumber = file.file_number || "";
            row.style.cssText = "display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;border:1px solid #ead7df;border-radius:13px;padding:10px;background:#fffafb";
            const info = node("div");
            const name = node("div", preparationFileRecordLabel(file));
            name.style.cssText = "font-size:12px;font-weight:950;color:#74102f;overflow-wrap:anywhere";
            const meta = node("div", `المورد: ${file.file_title || "—"} • المسؤول: ${file.responsible_employee_name || "—"} • ${file.allocated_quantity} قطعة • ${file.file_date_display || file.file_date}`);
            meta.style.cssText = "margin-top:4px;font-size:10px;font-weight:700;color:#64748b;line-height:17px";
            info.append(name, meta);
            const download = node("button", "PDF");
            download.type = "button";
            download.style.cssText = "min-height:40px;border:0;border-radius:11px;background:#74102f;padding:0 13px;color:white;font-weight:950";
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

function applyMobileReviewedLayout() {
    const stage = document.querySelector('[data-testid="reviewed-orders-stage"]');
    const grid = document.querySelector('[data-testid="reviewed-products-grid"]');
    if (!stage || !grid) return;
    const mobile = window.matchMedia("(max-width: 640px)").matches;
    stage.style.direction = "rtl";
    stage.style.maxWidth = mobile ? "430px" : "";
    stage.style.marginInline = mobile ? "auto" : "";
    if (!mobile) {
        grid.style.display = "";
        grid.style.gridTemplateColumns = "";
        grid.style.gap = "";
        return;
    }
    grid.style.display = "grid";
    grid.style.gridTemplateColumns = "repeat(2,minmax(0,1fr))";
    grid.style.gap = "8px";
    grid.querySelectorAll('[data-testid="reviewed-product-card"]').forEach((card) => {
        card.style.borderRadius = "16px";
        card.style.minWidth = "0";
        const body = card.firstElementChild;
        if (body) {
            body.style.display = "flex";
            body.style.flexDirection = "column";
            body.style.padding = "8px";
            body.style.gap = "7px";
            const image = body.firstElementChild;
            if (image) {
                image.style.width = "100%";
                image.style.aspectRatio = "1";
                image.style.borderRadius = "14px";
            }
        }
        card.querySelectorAll("h3").forEach((heading) => {
            heading.style.fontSize = "11px";
            heading.style.lineHeight = "16px";
            heading.style.textAlign = "center";
        });
        card.querySelectorAll("button").forEach((button) => {
            button.style.fontSize = "10px";
            button.style.minHeight = "38px";
        });
    });
}

function scheduleInitialHistory() {
    if (scheduled || document.getElementById(HISTORY_ID)) return;
    scheduled = true;
    window.requestAnimationFrame(() => {
        scheduled = false;
        if (!document.getElementById(HISTORY_ID)) renderHistory();
        applyMobileReviewedLayout();
    });
}

function start() {
    if (!document.body || document.getElementById(ROOT_ID)) return;
    const marker = node("div");
    marker.id = ROOT_ID;
    marker.hidden = true;
    document.body.appendChild(marker);
    document.addEventListener("click", captureCreate, true);
    window.addEventListener("resize", applyMobileReviewedLayout);
    window.addEventListener("mezan:preparation-file-created", () => renderHistory({ force: true }));
    const observer = new MutationObserver(() => {
        if (!document.getElementById(HISTORY_ID)) scheduleInitialHistory();
        window.requestAnimationFrame(applyMobileReviewedLayout);
    });
    observer.observe(document.body, { childList: true, subtree: true });
    scheduleInitialHistory();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
    if (document.readyState === "loading") window.addEventListener("DOMContentLoaded", start, { once: true });
    else start();
}
