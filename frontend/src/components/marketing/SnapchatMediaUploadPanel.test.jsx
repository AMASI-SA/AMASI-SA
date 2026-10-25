import React, { act } from "react";
import { createRoot } from "react-dom/client";
import SnapchatMediaUploadPanel from "./SnapchatMediaUploadPanel";
import {
    createSnapchatMediaUploadPreview, getSnapchatMediaProfiles, prepareSnapchatVideoFile,
    reconcileSnapchatMediaUpload, uploadSnapchatVideoFile,
} from "../../services/snapchatMediaUpload";

jest.mock("../../services/snapchatMediaUpload", () => ({
    prepareSnapchatVideoFile: jest.fn(), getSnapchatMediaProfiles: jest.fn(),
    createSnapchatMediaUploadPreview: jest.fn(), uploadSnapchatVideoFile: jest.fn(),
    reconcileSnapchatMediaUpload: jest.fn(),
    snapchatMediaUploadError: (error, fallback) => fallback || "تعذّر الرفع",
}));

const metadata = {
    name: "video.mp4", file_name: "video.mp4", size_bytes: 16,
    content_type: "video/mp4", sha256: "a".repeat(64),
};
const preview = {
    provider: "snapchat_ads", account_id: "account-1", upload_id: "b".repeat(64), ...metadata,
    status: "previewed", can_upload: true, confirm_token: "token", provider_upload_attempted: false,
};
const ready = {
    ...preview, status: "ready", can_upload: false, provider_media_id: "media-1",
    provider_media_status: "READY", provider_upload_attempted: true, ready_for_creative: true,
};
let container;
let root;
let onReady;
let onClear;
let onProfileChange;

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    onReady = jest.fn(); onClear = jest.fn(); onProfileChange = jest.fn();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    prepareSnapchatVideoFile.mockResolvedValue(metadata);
    createSnapchatMediaUploadPreview.mockResolvedValue(preview);
    uploadSnapchatVideoFile.mockResolvedValue(ready);
    reconcileSnapchatMediaUpload.mockResolvedValue(ready);
    getSnapchatMediaProfiles.mockResolvedValue([{ profile_id: "profile-1", display_name: "AMASI" }]);
});

afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
});

async function render(accountId = "account-1", ownerId = "owner-1") {
    await act(async () => {
        root.render(<SnapchatMediaUploadPanel ownerId={ownerId} accountId={accountId} allowed
            onReady={onReady} onClear={onClear} onProfileChange={onProfileChange} />);
    });
}

async function selectFile() {
    const source = new File([new Uint8Array(16)], "video.mp4", { type: "video/mp4" });
    const input = container.querySelector('[data-testid="snapchat-media-file"]');
    Object.defineProperty(input, "files", { configurable: true, value: [source] });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
    return source;
}

async function click(testId) {
    await act(async () => container.querySelector('[data-testid="' + testId + '"]').click());
}

test("file selection hashes locally; explicit upload hands back verified READY media", async () => {
    await render();
    const source = await selectFile();
    expect(prepareSnapchatVideoFile).toHaveBeenCalledWith(source, expect.any(Object));
    expect(createSnapchatMediaUploadPreview).not.toHaveBeenCalled();
    expect(uploadSnapchatVideoFile).not.toHaveBeenCalled();
    await click("snapchat-media-upload");
    expect(uploadSnapchatVideoFile).toHaveBeenCalledTimes(1);
    expect(uploadSnapchatVideoFile.mock.calls[0][0].file).toBe(source);
    expect(onReady).toHaveBeenCalledWith(ready, { ownerId: "owner-1", accountId: "account-1" });
    expect(container.textContent).toContain("الفيديو جاهز للاستخدام في الإعلان");
    expect(container.querySelector('[data-testid="snapchat-media-upload"]').type).toBe("button");
    expect(onProfileChange).toHaveBeenCalledWith("profile-1", { ownerId: "owner-1", accountId: "account-1" });
});

test("transport uncertainty disables upload until explicit reconciliation", async () => {
    uploadSnapchatVideoFile.mockRejectedValueOnce(new Error("timeout"));
    await render();
    await selectFile();
    await click("snapchat-media-upload");
    expect(onReady).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="snapchat-media-upload"]').disabled).toBe(true);
    expect(uploadSnapchatVideoFile).toHaveBeenCalledTimes(1);
    await click("snapchat-media-reconcile");
    expect(reconcileSnapchatMediaUpload).toHaveBeenCalledTimes(1);
    expect(uploadSnapchatVideoFile).toHaveBeenCalledTimes(1);
    expect(onReady).toHaveBeenCalledTimes(1);
});

test("an unresolved create from preview never triggers an upload POST", async () => {
    createSnapchatMediaUploadPreview.mockResolvedValueOnce({
        ...preview, status: "create_unknown", can_upload: false, confirm_token: "",
    });
    await render();
    await selectFile();
    await click("snapchat-media-upload");
    expect(uploadSnapchatVideoFile).not.toHaveBeenCalled();
    expect(onReady).not.toHaveBeenCalled();
    expect(container.textContent).toContain("الرفع متوقف لمنع التكرار");
});

test("account change discards the old in-flight READY and selected file", async () => {
    let finish;
    uploadSnapchatVideoFile.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await render();
    await selectFile();
    await click("snapchat-media-upload");
    await render("account-2");
    await act(async () => finish(ready));
    expect(onReady).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("video.mp4");
    expect(container.querySelector('[data-testid="snapchat-media-upload"]').disabled).toBe(true);
    expect(onClear).toHaveBeenCalledWith({ ownerId: "owner-1", accountId: "account-2" });
});

test("a bare provider READY status never reaches the creative form", async () => {
    uploadSnapchatVideoFile.mockResolvedValueOnce({ ...ready, ready_for_creative: false });
    await render();
    await selectFile();
    await click("snapchat-media-upload");
    expect(onReady).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("الفيديو جاهز للاستخدام في الإعلان");
});

test("stale profile listing cannot populate another account", async () => {
    let finish;
    getSnapchatMediaProfiles.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await render();
    getSnapchatMediaProfiles.mockResolvedValueOnce([{ profile_id: "profile-2", display_name: "Second" }]);
    await render("account-2");
    await act(async () => finish([{ profile_id: "profile-1", display_name: "First" }]));
    expect(onProfileChange).not.toHaveBeenCalledWith("profile-1", expect.anything());
    expect(onProfileChange).toHaveBeenCalledWith("profile-2", { ownerId: "owner-1", accountId: "account-2" });
});

test("missing profile access is shown as blocked without a manual-id field", async () => {
    getSnapchatMediaProfiles.mockRejectedValueOnce(new Error("forbidden"));
    await render();
    expect(container.textContent).toContain("تعذّر جلب ملفات سناب المتاحة لهذا الحساب");
    expect(container.querySelector('[data-testid="snapchat-media-profile-select"]').value).toBe("");
    expect(container.querySelector('input[type="text"]')).toBeNull();
});
