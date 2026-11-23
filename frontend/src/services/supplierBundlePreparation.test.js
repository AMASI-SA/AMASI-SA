import api from "../lib/api";
import {
    createSupplierBundlePreparation,
    inspectSupplierBundlePreparation,
    listPendingSupplierBundlePreparations,
    retrySupplierBundlePreparation,
} from "./supplierBundlePreparation";

jest.mock("../lib/api", () => ({ __esModule: true, API_BASE: "https://synthetic.test/api",
    default: { get: jest.fn(), post: jest.fn() } }));

const scope = "a".repeat(64);
const options = { schema: 2, contract: "preparation_assignment_v2", session_scope: scope,
    actor: { id: "owner" }, capabilities: { atomic_publication: true, defer: true } };
const selection = { group_key: "pair-card", quantity: 1, revision: "b".repeat(64), order_number: "synthetic-order" };
const args = () => ({ clientRequestId: "request-bundle-original", selections: [{ ...selection }], physicalQuantity: 2,
    deferSupplierConfirmed: true, metadata: { fileTitle: "بكج قطعتين", responsibleEmployeeId: "employee",
        expectedQuantity: 2, selectedProductCount: 1, scheduleMode: "required", requiredDueAt: "2026-12-01T13:00:00+03:00" } });

function response(body, state = "COMMITTED", physicalQuantity = 2) {
    const { expected_session_scope, ...command } = body;
    return { ...options, session_scope: expected_session_scope, client_request_id: body.client_request_id, state,
        command: state === "ABSENT" ? null : { ...command, required_due_at: body.required_due_at?.replace(".000Z", "+00:00") ?? null },
        receipt: state !== "COMMITTED" ? null : {
            request_fingerprint: "c".repeat(64), responsible_employee_id: body.responsible_employee_id,
            supplier_assignment: { mode: "defer" }, destination: "waiting", dispatch: null,
            piece_count: physicalQuantity, physical_quantity: physicalQuantity, commercial_quantity: body.selections.length,
            piece_ids: Array.from({ length: physicalQuantity }, (_, index) => `leaf-${index}`),
            file: { file_number: "PF-SYN", batch_id: "batch-syn", client_request_id: body.client_request_id,
                status: "ready", file_title: body.file_title, file_name: "pair.pdf",
                allocated_quantity: body.selections.length, selected_product_count: body.selections.length,
                order_count: 1, responsible_employee_id: body.responsible_employee_id,
                schedule_mode: body.schedule_mode, required_due_at: body.required_due_at },
            batch: { batch_id: "batch-syn", status: "ready", allocated_quantity: body.selections.length,
                physical_quantity: physicalQuantity, selections: body.selections,
                piece_ids: Array.from({ length: physicalQuantity }, (_, index) => `leaf-${index}`) },
        },
    };
}

const saved = () => JSON.parse(localStorage.getItem(localStorage.key(0)));
const recoveryArgs = () => ({ sessionScope: scope, clientRequestId: args().clientRequestId });

beforeEach(() => {
    jest.restoreAllMocks(); api.get.mockReset(); api.post.mockReset(); localStorage.clear();
    api.get.mockResolvedValue({ data: options });
    api.post.mockImplementation(async (_path, body) => ({ data: response(body) }));
});

test("persists the exact scoped command before posting and verifies two leaves for one sale card", async () => {
    api.post.mockImplementation(async (path, body) => {
        expect(path).toBe("/preparation-assignment-v2/requests");
        expect(saved()).toMatchObject({ session_scope: scope, actor_id: "owner", physical_quantity: 2, body });
        expect(body).toMatchObject({ expected_quantity: 1, selected_product_count: 1, expected_session_scope: scope,
            supplier_assignment: { mode: "defer" }, required_due_at: "2026-12-01T10:00:00.000Z" });
        return { data: response(body) };
    });
    const batch = await createSupplierBundlePreparation(args());
    expect(batch).toMatchObject({ physical_quantity: 2, commercial_quantity: 1, schedule_mode: "required",
        assignment_contract: "preparation_assignment_v2", schedule_committed: true });
    expect(localStorage.length).toBe(0);
    expect(api.post).toHaveBeenCalledTimes(1);
});

test("one mixed file keeps commercial count separate from physical bundle pieces", async () => {
    const input = args();
    input.selections.push({ ...selection, group_key: "ordinary-card" }); input.physicalQuantity = 3;
    api.post.mockImplementation(async (_path, body) => ({ data: response(body, "COMMITTED", 3) }));
    const result = await createSupplierBundlePreparation(input);
    expect(api.post.mock.calls[0][1].expected_quantity).toBe(2);
    expect(result.physical_quantity).toBe(3);
});

test("a single selected hoodie produces one physical requirement with an automatic schedule", async () => {
    const input = args(); input.physicalQuantity = 1; input.metadata.scheduleMode = "automatic";
    api.post.mockImplementation(async (_path, body) => ({ data: response(body, "COMMITTED", 1) }));
    const batch = await createSupplierBundlePreparation(input);
    expect(api.post.mock.calls[0][1]).toMatchObject({ expected_quantity: 1, required_due_at: null, schedule_mode: "automatic" });
    expect(batch).toMatchObject({ physical_quantity: 1, commercial_quantity: 1 });
});

test("missing explicit defer consent makes no network or storage request", async () => {
    await expect(createSupplierBundlePreparation({ ...args(), deferSupplierConfirmed: false }))
        .rejects.toMatchObject({ code: "bundle_preparation_confirmation_required" });
    expect(api.get).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled(); expect(localStorage.length).toBe(0);
});

test.each([
    { selections: [] },
    { selections: [{ ...selection, quantity: 2 }] },
    { selections: [selection, selection] },
    { selections: [{ ...selection, revision: "" }] },
    { selections: [{ ...selection, order_number: "" }] },
    { physicalQuantity: 3 },
    { physicalQuantity: 0 },
])("invalid or stale selection is refused before options or publication: %j", async (overrides) => {
    await expect(createSupplierBundlePreparation({ ...args(), ...overrides })).rejects.toHaveProperty("code", "bundle_preparation_selection_invalid");
    expect(api.get).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});

test("more than 50 commercial cards cannot start a modern command", async () => {
    await expect(createSupplierBundlePreparation({ ...args(), physicalQuantity: 52,
        selections: Array.from({ length: 51 }, (_, index) => ({ ...selection, group_key: `card-${index}` })) }))
        .rejects.toHaveProperty("code", "bundle_preparation_limit");
    expect(api.get).not.toHaveBeenCalled();
});

test.each([null, "2026-12-01T10:00:00", "invalid"])("required deadline must carry a valid timezone: %s", async (due) => {
    const input = args(); input.metadata.requiredDueAt = due;
    await expect(createSupplierBundlePreparation(input)).rejects.toHaveProperty("code", "bundle_preparation_schedule_invalid");
    expect(api.post).not.toHaveBeenCalled();
});

test.each([
    { ...options, session_scope: "wrong" },
    { ...options, capabilities: { atomic_publication: false, defer: true } },
    { ...options, capabilities: { atomic_publication: true, defer: false } },
    { ...options, contract: "legacy" },
])("missing server assignment capability cannot fall back to publication: %j", async (data) => {
    api.get.mockResolvedValue({ data });
    await expect(createSupplierBundlePreparation(args())).rejects.toHaveProperty("code", "bundle_preparation_unavailable");
    expect(api.post).not.toHaveBeenCalled(); expect(localStorage.length).toBe(0);
});

test("storage failure is discovered before the only mutating request", async () => {
    jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
    await expect(createSupplierBundlePreparation(args())).rejects.toHaveProperty("code", "bundle_preparation_storage_unavailable");
    expect(api.post).not.toHaveBeenCalled();
});

async function loseReply() {
    api.post.mockRejectedValueOnce(new Error("connection lost"));
    await expect(createSupplierBundlePreparation(args())).rejects.toHaveProperty("code", "bundle_preparation_pending");
    return saved().body;
}

test("a lost reply is recovered read-only from a verified committed receipt", async () => {
    const body = await loseReply();
    expect(localStorage.length).toBe(1);
    api.get.mockImplementation(async (path) => ({ data: path.endsWith("/options") ? options : response(body) }));
    expect((await listPendingSupplierBundlePreparations()).requests).toMatchObject([{ clientRequestId: body.client_request_id, physicalQuantity: 2 }]);
    const result = await inspectSupplierBundlePreparation(recoveryArgs());
    expect(result.state).toBe("COMMITTED"); expect(localStorage.length).toBe(0); expect(api.post).toHaveBeenCalledTimes(1);
});

test.each(["ABSENT", "PENDING"])("explicit retry checks %s status then sends the exact frozen body once", async (state) => {
    const body = await loseReply();
    api.get.mockImplementation(async (path) => ({ data: path.endsWith("/options") ? options : response(body, state) }));
    expect((await inspectSupplierBundlePreparation(recoveryArgs())).state).toBe(state);
    expect(api.post).toHaveBeenCalledTimes(1);
    const statusReads = () => api.get.mock.calls.filter(([path]) => path.includes("/requests/"));
    expect(statusReads()).toHaveLength(1);
    api.post.mockImplementation(async (_path, frozen) => ({ data: response(frozen) }));
    expect((await retrySupplierBundlePreparation({ ...recoveryArgs(), metadata: { fileTitle: "changed" } })).state).toBe("COMMITTED");
    expect(statusReads()).toHaveLength(2);
    expect(api.post.mock.calls[1]).toEqual(["/preparation-assignment-v2/requests", body]);
    expect(localStorage.length).toBe(0);
});

test("retry observes an already committed command without a second POST", async () => {
    const body = await loseReply();
    api.get.mockImplementation(async (path) => ({ data: path.endsWith("/options") ? options : response(body) }));
    expect((await retrySupplierBundlePreparation(recoveryArgs())).state).toBe("COMMITTED");
    expect(api.post).toHaveBeenCalledTimes(1);
});

test("changed session scope never reads or replays a previous actor's request", async () => {
    await loseReply();
    api.get.mockResolvedValue({ data: { ...options, session_scope: "d".repeat(64), actor: { id: "other" } } });
    await expect(retrySupplierBundlePreparation(recoveryArgs())).rejects.toHaveProperty("code", "bundle_preparation_scope_changed");
    expect(api.get.mock.calls.every(([path]) => path.endsWith("/options"))).toBe(true);
    expect(api.post).toHaveBeenCalledTimes(1); expect(localStorage.length).toBe(1);
});

test("the current account lists only its own scope without exposing another account's pending filename", async () => {
    await loseReply();
    api.get.mockResolvedValue({ data: { ...options, session_scope: "d".repeat(64), actor: { id: "other" } } });
    expect((await listPendingSupplierBundlePreparations()).requests).toEqual([]);
    expect(localStorage.length).toBe(1); expect(api.post).toHaveBeenCalledTimes(1);
});

test("per-request recovery removes only its own record when two tabs left separate uncertain attempts", async () => {
    const body = await loseReply();
    const originalKey = localStorage.key(0);
    const other = JSON.parse(JSON.stringify(saved()));
    other.body.client_request_id = "other-tab-request"; other.body.file_title = "ملف آخر";
    localStorage.setItem(originalKey.replace(encodeURIComponent(body.client_request_id), "other-tab-request"), JSON.stringify(other));
    api.get.mockImplementation(async (path) => ({ data: path.endsWith("/options") ? options : response(body) }));
    expect((await listPendingSupplierBundlePreparations()).requests).toHaveLength(2);
    await inspectSupplierBundlePreparation(recoveryArgs());
    expect(localStorage.length).toBe(1); expect(saved().body.client_request_id).toBe("other-tab-request");
    expect(api.post).toHaveBeenCalledTimes(1);
});

test("another pending command blocks a new attempt without overwriting its body", async () => {
    const body = await loseReply();
    await expect(createSupplierBundlePreparation({ ...args(), clientRequestId: "different-request" }))
        .rejects.toHaveProperty("code", "bundle_preparation_pending");
    expect(saved().body).toEqual(body); expect(api.post).toHaveBeenCalledTimes(1);
});

test("corrupt current-scope storage fails closed without publishing or deleting evidence", async () => {
    await loseReply();
    localStorage.setItem(localStorage.key(0), "{broken");
    await expect(createSupplierBundlePreparation({ ...args(), clientRequestId: "different-request" }))
        .rejects.toHaveProperty("code", "bundle_preparation_storage_invalid");
    expect(api.post).toHaveBeenCalledTimes(1); expect(localStorage.length).toBe(1);
});

test("a server-proven not-applied rejection releases only that saved attempt", async () => {
    api.post.mockRejectedValue({ response: { data: { detail: { code: "reviewed_selection_stale", not_applied: true } } } });
    await expect(createSupplierBundlePreparation(args())).rejects.toMatchObject({ code: "bundle_preparation_not_applied", notApplied: true });
    expect(localStorage.length).toBe(0); expect(api.post).toHaveBeenCalledTimes(1);
});

test("a scope error from another current account cannot discard the original saved attempt", async () => {
    api.post.mockImplementation(async (_path, body) => {
        api.get.mockResolvedValue({ data: { ...response(body, "ABSENT"), session_scope: "d".repeat(64), actor: { id: "other" } } });
        throw { response: { data: { detail: { code: "preparation_assignment_session_scope_changed", not_applied: true } } } };
    });
    await expect(createSupplierBundlePreparation(args())).rejects.toHaveProperty("code", "bundle_preparation_pending");
    expect(localStorage.length).toBe(1); expect(api.post).toHaveBeenCalledTimes(1);
});

test.each(["ABSENT", "PENDING"])("early command rejection only clears an original-scope ABSENT status, received %s", async (state) => {
    api.post.mockImplementation(async (_path, body) => {
        api.get.mockResolvedValue({ data: response(body, state) });
        throw { response: { data: { detail: { code: "preparation_assignment_command_invalid", not_applied: true } } } };
    });
    await expect(createSupplierBundlePreparation(args())).rejects.toHaveProperty("code",
        state === "ABSENT" ? "bundle_preparation_not_applied" : "bundle_preparation_pending");
    expect(localStorage.length).toBe(state === "ABSENT" ? 0 : 1); expect(api.post).toHaveBeenCalledTimes(1);
});

test.each([
    (data) => { data.actor.id = "other"; },
    (data) => { data.command.file_title = "other"; },
    (data) => { data.receipt.physical_quantity = 1; },
    (data) => { data.receipt.piece_ids = ["leaf-0", "leaf-0"]; },
    (data) => { data.receipt.supplier_assignment = { mode: "assign", supplier_id: "other" }; },
    (data) => { data.receipt.file.required_due_at = null; },
    (data) => { data.receipt.batch.batch_id = "other"; },
    (data) => { data.receipt.file.client_request_id = "other"; },
    (data) => { data.session_scope = "d".repeat(64); },
    (data) => { data.command.required_due_at = "invalid"; },
])("unproven committed response keeps the command available for recovery (%#)", async (mutate) => {
    api.post.mockImplementation(async (_path, body) => {
        const data = JSON.parse(JSON.stringify(response(body))); mutate(data); return { data };
    });
    await expect(createSupplierBundlePreparation(args())).rejects.toHaveProperty("code", "bundle_preparation_response_invalid");
    expect(localStorage.length).toBe(1); expect(api.post).toHaveBeenCalledTimes(1);
});

test("pending publication remains recoverable and does not automatically retry", async () => {
    api.post.mockImplementation(async (_path, body) => ({ data: response(body, "PENDING") }));
    await expect(createSupplierBundlePreparation(args())).rejects.toHaveProperty("code", "bundle_preparation_pending");
    expect(localStorage.length).toBe(1); expect(api.post).toHaveBeenCalledTimes(1);
});

test.each(["ABSENT", "PENDING", "COMMITTED"])("an invalidated recovery action retains its record and cannot retry after held %s status", async (state) => {
    const body = await loseReply();
    let active = true; let resolveStatus;
    const held = new Promise((resolve) => { resolveStatus = resolve; });
    api.get.mockImplementation(async (path) => path.endsWith("/options") ? { data: options } : held);
    const retry = retrySupplierBundlePreparation({ ...recoveryArgs(), isCurrent: () => active });
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(api.get.mock.calls.some(([path]) => path.includes("/requests/"))).toBe(true);
    active = false; resolveStatus({ data: response(body, state) });
    await expect(retry).rejects.toHaveProperty("code", "bundle_preparation_context_changed");
    expect(api.post).toHaveBeenCalledTimes(1); expect(localStorage.length).toBe(1);
    expect(saved().body).toEqual(body);
});

test.each(["COMMITTED", "not_applied"])("invalidation during retry publication keeps the original command after %s", async (outcome) => {
    const body = await loseReply();
    let active = true; let resolvePost; let rejectPost;
    const held = new Promise((resolve, reject) => { resolvePost = resolve; rejectPost = reject; });
    api.get.mockImplementation(async (path) => ({ data: path.endsWith("/options") ? options : response(body, "PENDING") }));
    api.post.mockReturnValue(held);
    const retry = retrySupplierBundlePreparation({ ...recoveryArgs(), isCurrent: () => active });
    for (let index = 0; index < 6; index += 1) await Promise.resolve();
    expect(api.post).toHaveBeenCalledTimes(2);
    active = false;
    if (outcome === "COMMITTED") resolvePost({ data: response(body) });
    else rejectPost({ response: { data: { detail: { code: "reviewed_selection_stale", not_applied: true } } } });
    await expect(retry).rejects.toHaveProperty("code", "bundle_preparation_context_changed");
    expect(localStorage.length).toBe(1); expect(saved().body).toEqual(body);
});
