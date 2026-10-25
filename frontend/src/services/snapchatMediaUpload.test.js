import api from "../lib/api";
import {
    createSnapchatMediaUploadPreview, getSnapchatMediaProfiles, normalizeSnapchatMediaUpload,
    prepareSnapchatVideoFile, reconcileSnapchatMediaUpload, uploadSnapchatVideoFile, validateSnapchatVideoFile,
} from "./snapchatMediaUpload";

jest.mock("../lib/api", () => ({ post: jest.fn(), get: jest.fn() }));

const metadata = {
    name: "video.mp4", file_name: "video.mp4", size_bytes: 16,
    content_type: "video/mp4", sha256: "a".repeat(64),
};
const preview = {
    provider: "snapchat_ads", account_id: "account-1", upload_id: "b".repeat(64), ...metadata,
    status: "previewed", can_upload: true, confirm_token: "confirm-token",
    provider_upload_attempted: false, ai_generated: false,
};
const ready = {
    ...preview, status: "ready", can_upload: false, provider_media_id: "media-1",
    provider_media_status: "READY", provider_upload_attempted: true, ready_for_creative: true,
};
const file = () => new File([new Uint8Array(16)], "video.mp4", { type: "video/mp4" });

beforeEach(() => jest.clearAllMocks());

test("hashes the actual browser File through WebCrypto", async () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, "crypto");
    const digest = jest.fn(async () => new Uint8Array(32).fill(1).buffer);
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: { subtle: { digest } } });
    try {
        const source = file();
        const bytes = new Uint8Array(16).fill(7).buffer;
        Object.defineProperty(source, "arrayBuffer", { value: async () => bytes });
        const result = await prepareSnapchatVideoFile(source);
        expect(digest).toHaveBeenCalledWith("SHA-256", bytes);
        expect(result.sha256).toBe("01".repeat(32));
        expect(result.size_bytes).toBe(source.size);
    } finally {
        if (original) Object.defineProperty(globalThis, "crypto", original);
        else delete globalThis.crypto;
    }
});

test("rejects non-files and oversized files before any API call", () => {
    expect(() => validateSnapchatVideoFile({ name: "video.mp4", size: 16 })).toThrow();
    const large = file();
    Object.defineProperty(large, "size", { value: 32_000_001 });
    expect(() => validateSnapchatVideoFile(large)).toThrow();
    expect(api.post).not.toHaveBeenCalled();
});

test("a bare READY status never becomes verified creative media", () => {
    expect(normalizeSnapchatMediaUpload({ ...ready, ready_for_creative: false }, "account-1").ready_for_creative).toBe(false);
    expect(() => normalizeSnapchatMediaUpload(ready, "account-2")).toThrow();
});

test("binds preview to the selected account and exact source", async () => {
    api.post.mockResolvedValueOnce({ data: { ...preview, account_id: "account-2" } });
    await expect(createSnapchatMediaUploadPreview({ accountId: "account-1", metadata })).rejects.toThrow();
    expect(api.post).toHaveBeenCalledTimes(1);
});

test("sends a raw File, progress and no auth replay or automatic retry", async () => {
    const source = file();
    const progress = jest.fn();
    api.post.mockRejectedValueOnce(new Error("timeout"));
    await expect(uploadSnapchatVideoFile({
        accountId: "account-1", file: source, metadata, preview, onProgress: progress,
    })).rejects.toThrow("timeout");
    expect(api.post).toHaveBeenCalledTimes(1);
    const [url, body, config] = api.post.mock.calls[0];
    expect(url.endsWith("/" + preview.upload_id + "/upload")).toBe(true);
    expect(body).toBe(source);
    expect(config.transformRequest[0](source)).toBe(source);
    expect(config._mezanAuthRetried).toBe(true);
    expect(config.headers["Content-Type"]).toBe("video/mp4");
    config.onUploadProgress({ loaded: 8 });
    expect(progress).toHaveBeenLastCalledWith(50);
});

test("unknown outcomes only use the existing reconciliation endpoint", async () => {
    api.post.mockResolvedValueOnce({ data: {
        ...preview, status: "create_unknown", can_upload: false,
        confirm_token: "", error_code: "snapchat_media_create_unresolved",
    } });
    const result = await reconcileSnapchatMediaUpload({ accountId: "account-1", uploadId: preview.upload_id });
    expect(result.can_upload).toBe(false);
    expect(result.ready_for_creative).toBe(false);
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0].endsWith("/" + preview.upload_id + "/reconcile")).toBe(true);
    expect(api.post.mock.calls[0][2]._mezanAuthRetried).toBe(true);
});

test("accepts only complete account-scoped profile catalogues", async () => {
    api.get.mockResolvedValueOnce({ data: {
        provider: "snapchat_ads", account_id: "account-1", complete: true,
        profiles: [{ profile_id: "profile-1", display_name: "AMASI" }],
    } });
    await expect(getSnapchatMediaProfiles({ accountId: "account-1" })).resolves.toEqual([
        { profile_id: "profile-1", display_name: "AMASI" },
    ]);
    api.get.mockResolvedValueOnce({ data: {
        provider: "snapchat_ads", account_id: "account-2", complete: true, profiles: [],
    } });
    await expect(getSnapchatMediaProfiles({ accountId: "account-1" })).rejects.toThrow();
    api.get.mockResolvedValueOnce({ data: {
        provider: "snapchat_ads", account_id: "account-1", complete: false, profiles: [],
    } });
    await expect(getSnapchatMediaProfiles({ accountId: "account-1" })).rejects.toThrow();
});
