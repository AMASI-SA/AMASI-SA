import api from "../lib/api";
import fixture from "./__fixtures__/googleNamespaceBootstrap.json";
import { validateBootstrapResult, bootstrapExpiry, newBootstrapNonce, readGoogleBootstrapReceipt, prepareGoogleNamespaceBootstrap, executeGoogleNamespaceBootstrap } from "./googleNamespaceBootstrap";
jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
const copy = (x) => JSON.parse(JSON.stringify(x));
const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
const NOW = Date.parse("2026-10-30T12:00:00Z");
beforeEach(() => { Object.defineProperty(globalThis, "crypto", { configurable: true, value: { getRandomValues: jest.fn() } }); jest.spyOn(Date, "now").mockReturnValue(NOW); api.get.mockReset(); api.post.mockReset(); });
afterEach(() => { jest.restoreAllMocks(); if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto); else delete globalThis.crypto; });

test.each(["no_preparation", "prepared", "foreign", "expired", "consumed", "applied", "historical"])("actual serializer %s receipt stays storage-only", (key) => {
    expect(validateBootstrapResult(fixture[key])).toEqual(fixture[key]);
    expect(fixture[key].execution_authorized).toBe(false); expect(fixture[key].namespace_contract_verified).toBe(false);
});
test.each([["noop", "prepare"], ["verified", "execute"]])("fresh %s is only current storage observation", (key, operation) => {
    expect(validateBootstrapResult(fixture[key], operation)).toEqual(fixture[key]);
    expect(() => validateBootstrapResult(fixture[key], "receipt")).toThrow();
});
test.each(["execution_authorized", "funding_ready", "policy_configuration_verified", "legacy_bindings_verified", "future_permissions_guaranteed", "atomic_catalog_snapshot_verified", "new_uuid_lineage_verified", "namespace_contract_verified", "execution_permitted"])("refuses inflated %s before a caller can act", (key) => {
    expect(() => validateBootstrapResult({ ...fixture.prepared, [key]: true })).toThrow();
});
test.each([
    { provider_writes: 1 }, { business_document_writes: false }, { unexpected: true }, { status: "connected" },
    { would_create: [] }, { would_create: ["account_guard", "account_guard"] }, { would_create: ["arbitrary"] },
    { execution_token: "x".repeat(64) }, { request_nonce: "A".repeat(32) }, { manifest_digest: null },
    { expires_at: "2026-02-30T12:00:00Z" }, { expires_at: "2026-10-30T15:10:00+03:00" },
    { preparation_token_available: 1 }, { observation_digest: undefined },
])("strict schema rejects malformed prepared %j", (patch) => expect(() => validateBootstrapResult({ ...fixture.prepared, ...patch })).toThrow());
test("receipt validation copies arrays and refuses endpoint-swapped states", () => {
    const value = copy(fixture.prepared), checked = validateBootstrapResult(value); checked.would_create.pop();
    expect(value.would_create).toHaveLength(4);
    expect(() => validateBootstrapResult(fixture.prepared, "execute")).toThrow();
    expect(() => validateBootstrapResult(fixture.no_preparation, "prepare")).toThrow();
});
test("GET and both POSTs carry abort/deadline and no-auth-replay marker with exact bodies", async () => {
    const signal = new AbortController().signal;
    api.get.mockResolvedValue({ data: fixture.no_preparation }); api.post.mockResolvedValueOnce({ data: fixture.prepared }).mockResolvedValueOnce({ data: fixture.verified });
    await readGoogleBootstrapReceipt({ signal }); await prepareGoogleNamespaceBootstrap("f".repeat(32), { signal });
    await executeGoogleNamespaceBootstrap(fixture.prepared, { signal });
    const options = { signal, timeout: 30000, _mezanAuthRetried: true };
    expect(api.get).toHaveBeenCalledWith("/integrations-v2/google_ads/automation/namespace-bootstrap/receipt", options);
    expect(api.post.mock.calls[0]).toEqual(["/integrations-v2/google_ads/automation/namespace-bootstrap/prepare", { request_nonce: "f".repeat(32), acknowledge_preparation_only: true }, options]);
    expect(api.post.mock.calls[1]).toEqual(["/integrations-v2/google_ads/automation/namespace-bootstrap/execute", {
        request_nonce: fixture.prepared.request_nonce, execution_token: fixture.prepared.execution_token,
        observation_digest: fixture.prepared.observation_digest, manifest_digest: fixture.prepared.manifest_digest, acknowledge_storage_only: true,
    }, options]);
});
test("expired or malformed actions never POST and secure nonce uses native entropy", async () => {
    jest.spyOn(Date, "now").mockReturnValue(bootstrapExpiry(fixture.prepared.expires_at));
    await expect(executeGoogleNamespaceBootstrap(fixture.prepared)).rejects.toThrow();
    await expect(prepareGoogleNamespaceBootstrap("wrong")).rejects.toThrow(); expect(api.post).not.toHaveBeenCalled();
    const random = jest.spyOn(globalThis.crypto, "getRandomValues").mockImplementation((bytes) => { bytes.fill(7); return bytes; });
    expect(newBootstrapNonce()).toBe("07".repeat(16)); expect(random).toHaveBeenCalledTimes(1);
});

test("missing secure randomness has no timestamp or Math.random fallback", () => {
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: undefined });
    const fallback = jest.spyOn(Math, "random"); expect(() => newBootstrapNonce()).toThrow("randomness_unavailable");
    expect(fallback).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});
