import api, { API_BASE } from "../lib/api";

const CONTRACT = "preparation_assignment_v2";
const PATH = "/preparation-assignment-v2";
const HEX = /^[a-f0-9]{64}$/;
const STORAGE_PREFIX = `mezan:supplier-bundle-preparation:v1:${encodeURIComponent(API_BASE)}:`;

function failure(code, message) {
    const error = new Error(message);
    error.code = `bundle_preparation_${code}`;
    return error;
}

const validText = (value, max = 160) => typeof value === "string" && value.length > 0
    && value.length <= max && value === value.trim();
const same = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));

function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (!value || typeof value !== "object") return value;
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}

function instant(value) {
    if (typeof value !== "string" || !/(Z|[+-]\d\d:\d\d)$/.test(value)) return null;
    const parsed = new Date(value);
    return Number.isFinite(parsed.getTime()) ? parsed.toISOString() : null;
}

function comparableCommand(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const command = { ...value };
    delete command.expected_session_scope;
    if (command.required_due_at !== null && !instant(command.required_due_at)) return null;
    return { ...command, required_due_at: command.required_due_at === null
        ? null : instant(command.required_due_at) };
}

function commandFor({ clientRequestId, selections, metadata, physicalQuantity, deferSupplierConfirmed }, scope) {
    if (deferSupplierConfirmed !== true) throw failure("confirmation_required", "أكد تأجيل إرسال الملف للمورد قبل المتابعة.");
    if (!Array.isArray(selections) || selections.length === 0) throw failure("selection_invalid", "حدّد بطاقات البكج من قائمة تمت المراجعة.");
    if (selections.length > 50) throw failure("limit", "يمكن إنشاء ملف البكجات من 50 بطاقة كحد أقصى في المحاولة الواحدة.");
    if (new Set(selections.map((row) => row?.group_key)).size !== selections.length
        || selections.some((row) => !validText(row?.group_key, 500) || row.quantity !== 1
            || !HEX.test(row.revision || "") || !validText(row.order_number, 80))
        || !Number.isInteger(physicalQuantity) || physicalQuantity < selections.length
        || physicalQuantity > selections.length * 2) {
        throw failure("selection_invalid", "تغيّرت بطاقات البكج أو عدد قطعها. حدّث القائمة ثم أعد الاختيار.");
    }
    const title = typeof metadata?.fileTitle === "string" ? metadata.fileTitle.trim().replace(/\s+/g, " ") : "";
    if (!validText(clientRequestId) || clientRequestId.length < 8 || !validText(title, 120)
        || !validText(metadata?.responsibleEmployeeId, 120)) {
        throw failure("metadata_invalid", "أكمل اسم الملف والموظف المسؤول قبل إنشاء الملف.");
    }
    const scheduleMode = metadata?.scheduleMode;
    const due = scheduleMode === "required" ? instant(metadata?.requiredDueAt) : null;
    if (!["automatic", "required"].includes(scheduleMode) || (scheduleMode === "required" && !due)) {
        throw failure("schedule_invalid", "حدّد وقت التجهيز التلقائي أو موعدًا إجباريًا صحيحًا.");
    }
    return {
        client_request_id: clientRequestId, file_title: title,
        responsible_employee_id: metadata.responsibleEmployeeId,
        expected_quantity: selections.length, selected_product_count: selections.length,
        schedule_mode: scheduleMode, required_due_at: due,
        selections: selections.map(({ group_key, quantity, revision, order_number }) => ({ group_key, quantity, revision, order_number })),
        supplier_assignment: { mode: "defer" }, expected_session_scope: scope,
    };
}

async function context() {
    const { data } = await api.get(`${PATH}/options`);
    if (data?.schema !== 2 || data.contract !== CONTRACT || !HEX.test(data.session_scope || "")
        || !validText(data.actor?.id) || data.capabilities?.atomic_publication !== true || data.capabilities?.defer !== true) {
        throw failure("unavailable", "إسناد ملفات البكجات غير متاح لهذا الحساب الآن. لم يبدأ إنشاء ملف.");
    }
    return { scope: data.session_scope, actorId: data.actor.id };
}

function storage() {
    try {
        if (typeof window === "undefined" || !window.localStorage) throw new Error("missing");
        return window.localStorage;
    } catch (_) {
        throw failure("storage_unavailable", "تعذّر حفظ محاولة إنشاء الملف في هذا المتصفح. أعد تفعيل التخزين قبل المتابعة.");
    }
}

const recordKey = (scope, requestId) => `${STORAGE_PREFIX}${scope}:${encodeURIComponent(requestId)}`;

function validateRecord(record, ctx, key) {
    try {
        const body = record?.body;
        if (record?.schema !== 1 || record.contract !== CONTRACT || record.session_scope !== ctx.scope
            || record.actor_id !== ctx.actorId || body?.expected_session_scope !== ctx.scope
            || key !== recordKey(ctx.scope, body?.client_request_id)) throw new Error("scope");
        const expected = commandFor({ clientRequestId: body.client_request_id, selections: body.selections,
            metadata: { fileTitle: body.file_title, responsibleEmployeeId: body.responsible_employee_id,
                scheduleMode: body.schedule_mode, requiredDueAt: body.required_due_at },
            physicalQuantity: record.physical_quantity, deferSupplierConfirmed: true }, ctx.scope);
        if (!same(body, expected)) throw new Error("command");
        return record;
    } catch (_) {
        throw failure("storage_invalid", "تعذّرت قراءة محاولة البكج المحفوظة بأمان. لا تنشئ محاولة بديلة قبل مراجعة الملف الأصلي.");
    }
}

function records(ctx) {
    const store = storage();
    const prefix = `${STORAGE_PREFIX}${ctx.scope}:`;
    try {
        const result = [];
        for (let index = 0; index < store.length; index += 1) {
            const key = store.key(index);
            if (key?.startsWith(prefix)) result.push(validateRecord(JSON.parse(store.getItem(key)), ctx, key));
        }
        return result;
    } catch (error) {
        if (error?.code) throw error;
        throw failure("storage_invalid", "تعذّرت قراءة محاولة البكج المحفوظة. تحقّق من الملف الأصلي قبل إنشاء محاولة جديدة.");
    }
}

function save(record, ctx) {
    const store = storage();
    const key = recordKey(ctx.scope, record.body.client_request_id);
    try {
        if (store.getItem(key) !== null) throw failure("pending", "توجد محاولة سابقة لهذا الملف. افتح التحقق من المحاولات السابقة أولًا.");
        const serialized = JSON.stringify(record);
        store.setItem(key, serialized);
        if (store.getItem(key) !== serialized) throw new Error("readback");
    } catch (error) {
        if (error?.code) throw error;
        throw failure("storage_unavailable", "تعذّر حفظ المحاولة قبل الإرسال. لم يبدأ إنشاء ملف البكج.");
    }
}

function remove(record) {
    try { storage().removeItem(recordKey(record.session_scope, record.body.client_request_id)); }
    catch (_) { throw failure("storage_unavailable", "تم التحقق من المحاولة، لكن تعذّر تحديث سجلها في المتصفح. استخدم التحقق من المحاولات السابقة."); }
}

function pendingSummary(record) {
    return { clientRequestId: record.body.client_request_id, fileTitle: record.body.file_title,
        physicalQuantity: record.physical_quantity, commercialQuantity: record.body.selections.length,
        createdAt: record.created_at };
}

function verifiedResponse(data, record, ctx) {
    const body = record.body;
    const invalid = () => failure("response_invalid", "لم يمكن تأكيد نتيجة الملف بأمان. احتُفظ بالمحاولة؛ افتح التحقق من المحاولات السابقة.");
    if (data?.schema !== 2 || data.contract !== CONTRACT || data.session_scope !== ctx.scope
        || data.actor?.id !== ctx.actorId || data.client_request_id !== body.client_request_id) throw invalid();
    if (data.state === "ABSENT") {
        if (data.command !== null || data.receipt !== null) throw invalid();
        return { state: "ABSENT", clientRequestId: body.client_request_id };
    }
    if (!same(comparableCommand(data.command), comparableCommand(body))) throw invalid();
    if (data.state === "PENDING") {
        if (data.receipt !== null) throw invalid();
        return { state: "PENDING", clientRequestId: body.client_request_id };
    }
    const receipt = data.receipt;
    const file = receipt?.file;
    const batch = receipt?.batch;
    const ids = receipt?.piece_ids;
    const commercialCount = body.selections.length;
    const physicalCount = record.physical_quantity;
    if (data.state !== "COMMITTED" || !HEX.test(receipt?.request_fingerprint || "")
        || !same(receipt?.supplier_assignment, { mode: "defer" }) || receipt?.destination !== "waiting"
        || receipt?.dispatch !== null || receipt?.responsible_employee_id !== body.responsible_employee_id
        || receipt.piece_count !== physicalCount || receipt.physical_quantity !== physicalCount
        || receipt.commercial_quantity !== commercialCount || !Array.isArray(ids)
        || ids.length !== physicalCount || new Set(ids).size !== ids.length || ids.some((id) => !validText(id))
        || !validText(file?.batch_id) || !validText(file?.file_number) || file.status !== "ready"
        || file.client_request_id !== body.client_request_id || file.responsible_employee_id !== body.responsible_employee_id
        || file.allocated_quantity !== commercialCount || file.file_title !== body.file_title
        || file.selected_product_count !== commercialCount || file.schedule_mode !== body.schedule_mode
        || (body.required_due_at === null ? file.required_due_at !== null : instant(file.required_due_at) !== body.required_due_at)
        || batch?.batch_id !== file.batch_id || batch.status !== "ready"
        || batch.allocated_quantity !== commercialCount || batch.physical_quantity !== physicalCount
        || !same(batch.selections, body.selections) || !same(batch.piece_ids, ids)) throw invalid();
    return { state: "COMMITTED", clientRequestId: body.client_request_id,
        batch: { ...batch, ...file, physical_quantity: physicalCount, commercial_quantity: commercialCount,
            file_registered: true, registry_status: "ready", piece_registry_status: "ready",
            assignment_contract: CONTRACT, schedule_committed: true } };
}

async function post(record, ctx) {
    try {
        const { data } = await api.post(`${PATH}/requests`, record.body);
        const result = verifiedResponse(data, record, ctx);
        if (result.state === "COMMITTED") remove(record);
        return result;
    } catch (error) {
        const detail = error?.response?.data?.detail;
        if (detail?.not_applied === true) {
            // These two errors can precede the server's original-scope check.
            // A different signed-in account cannot discard the saved request.
            if (["preparation_assignment_session_scope_changed", "preparation_assignment_command_invalid"].includes(detail.code)) {
                let original;
                try { original = await status(record, ctx); }
                catch (_) { throw failure("pending", "تعذّر التحقق من المحاولة في حسابها الأصلي. احتُفظ بالمحاولة؛ أعد فتح التحقق من المحاولات السابقة."); }
                if (original.state === "COMMITTED") return original;
                if (original.state !== "ABSENT") throw failure("pending", "المحاولة الأصلية ما زالت محفوظة لدى الخادم. تحقّق من نتيجتها قبل إنشاء ملف جديد.");
            }
            remove(record);
            const rejected = failure("not_applied", detail.message || "لم يكتمل إنشاء الملف. حدّث القائمة وراجع الاختيار ثم أعد المحاولة.");
            rejected.notApplied = true;
            rejected.detail = detail;
            throw rejected;
        }
        if (error?.code?.startsWith("bundle_preparation_")) throw error;
        throw failure("pending", "تعذّر تأكيد إنشاء الملف. احتُفظ بالمحاولة؛ استخدم التحقق من المحاولات السابقة قبل أي إنشاء جديد.");
    }
}

export async function createSupplierBundlePreparation(args) {
    // Reject missing consent and invalid input before any request or stored command.
    commandFor(args, "");
    const ctx = await context();
    if (records(ctx).length) throw failure("pending", "توجد محاولة بكج سابقة تحتاج التحقق. افتح التحقق من المحاولات السابقة أولًا.");
    const body = commandFor(args, ctx.scope);
    const record = { schema: 1, contract: CONTRACT, session_scope: ctx.scope, actor_id: ctx.actorId,
        physical_quantity: args.physicalQuantity, created_at: new Date().toISOString(), body };
    save(record, ctx);
    const result = await post(record, ctx);
    if (result.state !== "COMMITTED") throw failure("pending", "لم تتأكد نتيجة الملف بعد. افتح التحقق من المحاولات السابقة؛ لا تنشئ ملفًا جديدًا.");
    return result.batch;
}

export async function listPendingSupplierBundlePreparations() {
    const ctx = await context();
    return { sessionScope: ctx.scope, requests: records(ctx).map(pendingSummary) };
}

async function loadRequest({ sessionScope, clientRequestId }) {
    const ctx = await context();
    if (ctx.scope !== sessionScope) throw failure("scope_changed", "تغيّر الحساب المفتوح. أعد فتح قائمة المحاولات لهذا الحساب.");
    const record = records(ctx).find((row) => row.body.client_request_id === clientRequestId);
    if (!record) throw failure("not_found", "لا توجد محاولة محفوظة لهذا الملف في الحساب الحالي. حدّث قائمة المحاولات.");
    return { ctx, record };
}

async function status(record, ctx) {
    const { data } = await api.get(`${PATH}/requests/${encodeURIComponent(record.body.client_request_id)}`);
    const result = verifiedResponse(data, record, ctx);
    if (result.state === "COMMITTED") remove(record);
    return result;
}

export async function inspectSupplierBundlePreparation(args) {
    const { ctx, record } = await loadRequest(args);
    return status(record, ctx);
}

export async function retrySupplierBundlePreparation(args) {
    const { ctx, record } = await loadRequest(args);
    const current = await status(record, ctx);
    if (current.state === "COMMITTED") return current;
    // This explicit retry uses only the frozen original command and request ID.
    return post(record, ctx);
}

export function notifySupplierBundlePreparationCommitted(batch) {
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("mezan:preparation-file-created", { detail: batch }));
}
