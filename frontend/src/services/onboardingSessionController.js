import * as service from "./accountingOnboarding";

const copy = value => JSON.parse(JSON.stringify(value));
const key = () => globalThis.crypto.randomUUID();
function decode(response, expectedId) {
    const session = response;
    const id = session?.id;
    if (!session || typeof session !== "object" || Array.isArray(session) || "session" in session
        || typeof id !== "string" || !id.trim() || (expectedId && id !== expectedId)
        || session.schema_version !== 1 || !Number.isInteger(session.version) || session.version < 1
        || typeof session.existing !== "boolean" || !session.sections || typeof session.sections !== "object"
        || Array.isArray(session.sections) || !["draft", "previewed", "reviewed", "handed_off"].includes(session.status)) {
        throw new Error("onboarding_response_invalid");
    }
    return session;
}

// Serialize setup metadata saves against a global CAS version. A failed request
// is retained verbatim for explicit retry or reload, never silently re-keyed.
export function createOnboardingSessionController(transport = service, makeKey = key) {
    let current = null, pending = null, conflict = false, queue = Promise.resolve();
    const enqueue = task => { const result = queue.then(task); queue = result.catch(() => {}); return result; };
    async function execute(request) {
        try {
            const result = await transport[request.method](...copy(request.args));
            const next = decode(result, request.method === "createOnboardingSession" ? undefined : current?.id);
            const minimumVersion = request.method === "createOnboardingSession" ? 1 : request.expectedVersion + 1;
            if (next.version < minimumVersion || (!next.existing && next.version !== minimumVersion)) {
                throw new Error("onboarding_response_version_invalid");
            }
            // A replay may include unseen saves from another writer. The wrapper's
            // local projections must be explicitly restored before replacing sections.
            current = copy(next); pending = null;
            conflict = next.existing && next.version > minimumVersion;
            return copy(current);
        } catch (error) {
            const status = error?.response?.status;
            if ([409, 403, 404, 422].includes(status) || error?.message?.startsWith("onboarding_response_")) conflict = true;
            throw error;
        }
    }
    function mutate(method, args, payload) {
        // Capture user intent now, before earlier queued requests can settle.
        const captured = copy(payload);
        return enqueue(async () => {
            if (pending) throw new Error("onboarding_pending_request_requires_retry_or_reload");
            if (conflict) throw new Error("onboarding_reload_required");
            if (!current) throw new Error("onboarding_session_required");
            if (["reviewed", "handed_off"].includes(current.status) || current.opening_draft) throw new Error("onboarding_session_locked");
            const request = { method, expectedVersion: current.version, args: [current.id, ...args, { ...captured, version: current.version, idempotency_key: makeKey() }] };
            pending = request;
            return execute(request);
        });
    }
    return {
        snapshot: () => current ? copy(current) : null,
        hasPendingRequest: () => pending !== null,
        needsReload: () => conflict,
        load: id => enqueue(async () => {
            const next = decode(await transport.getOnboardingSession(id), id);
            current = copy(next); pending = null; conflict = false; return copy(current);
        }),
        create: ({ cutover_at, cutover_timezone = "Asia/Riyadh" }) => enqueue(async () => {
            if (pending) throw new Error("onboarding_pending_request_requires_retry_or_reload");
            if (current) throw new Error("onboarding_session_already_selected");
            pending = { method: "createOnboardingSession", args: [{ cutover_at, cutover_timezone, idempotency_key: makeKey() }] };
            return execute(pending);
        }),
        saveCutover: payload => mutate("saveOnboardingCutover", [], payload),
        saveSection: (sectionId, payload) => mutate("saveOnboardingSection", [sectionId], payload),
        preview: note => mutate("previewOnboardingSession", [], { note }),
        review: note => mutate("reviewOnboardingSession", [], { note }),
        retry: () => enqueue(() => {
            if (!pending || conflict) throw new Error("onboarding_reload_required");
            return execute(pending);
        }),
    };
}
