import * as service from "./accountingOnboarding";

const copy = value => JSON.parse(JSON.stringify(value));
const key = () => globalThis.crypto.randomUUID();
function decode(response, expectedId) {
    const session = response?.session || response;
    const id = session?.id || session?.session_id;
    if (!id || (expectedId && id !== expectedId) || !Number.isInteger(session.version)
        || session.version < 1 || !["draft", "previewed", "reviewed"].includes(session.status)) throw new Error("onboarding_response_invalid");
    return { ...session, id };
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
            if (current && next.version !== request.expectedVersion + 1) throw new Error("onboarding_response_version_invalid");
            current = copy(next); pending = null; conflict = false;
            return copy(current);
        } catch (error) {
            const status = error?.response?.status;
            if (status === 409 || status === 403 || status === 404 || status === 422) conflict = true;
            throw error;
        }
    }
    function mutate(method, args, payload) {
        return enqueue(async () => {
            if (pending) throw new Error("onboarding_pending_request_requires_retry_or_reload");
            if (!current) throw new Error("onboarding_session_required");
            if (current.status === "reviewed" || current.opening_draft) throw new Error("onboarding_session_locked");
            const request = { method, expectedVersion: current.version, args: [current.id, ...args, { ...copy(payload), version: current.version, idempotency_key: makeKey() }] };
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
