import React, { act, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import TikTokContentWorkspace from "./TikTokContentWorkspace";
import TikTokFilePublishing from "./TikTokFilePublishing";
import api from "../../lib/api";
import { describeFile, uploadVideoFile } from "./tiktokFileTransfer";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
jest.mock("./tiktokFileTransfer", () => ({ describeFile: jest.fn(), uploadVideoFile: jest.fn() }));
const creator = "a".repeat(64), fingerprint = "b".repeat(64);
const metadata = { file_name: "winter.mp4", file_size: 3, mime_type: "video/mp4", file_manifest_sha256: fingerprint, width: 720, height: 1280, duration_seconds: 32 };
const proof = { creator_ref: creator, settings: { privacy_level_options: ["SELF_ONLY", "PUBLIC_TO_EVERYONE"], max_video_post_duration_sec: 600, comment_disabled: false, duet_disabled: false, stitch_disabled: false } };
const proposal = { proposal_id: "d".repeat(32), confirmation_digest: "c".repeat(64), status: "previewed", creator_ref: creator,
    input: { ...metadata, delivery: "direct", privacy_level: "PUBLIC_TO_EVERYONE", caption: "هدية الشتاء", disclosure: "own_business", is_aigc: false }, source_info: { source: "FILE_UPLOAD", video_size: 3, chunk_size: 3, total_chunk_count: 1 } };
let host, root;
beforeEach(() => {
    api.get.mockReset(); api.post.mockReset(); describeFile.mockReset(); uploadVideoFile.mockReset();
    Object.defineProperty(window, "crypto", { configurable: true, value: { randomUUID: () => "12345678-1234-1234-1234-123456789012" } });
    URL.createObjectURL = jest.fn(() => "blob:local-video"); URL.revokeObjectURL = jest.fn();
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const button = (name) => [...host.querySelectorAll("button")].find((node) => node.textContent === name);
const input = (name) => host.querySelector(`[aria-label="${name}"]`);
async function fill(name, value) {
    const node = input(name), proto = node.tagName === "SELECT" ? HTMLSelectElement.prototype : node.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    await act(async () => { Object.getOwnPropertyDescriptor(proto, "value").set.call(node,value); node.dispatchEvent(new Event(node.tagName === "SELECT" ? "change" : "input", { bubbles: true })); });
}
async function ready() {
    await act(async () => root.render(<TikTokFilePublishing />));
    api.get.mockResolvedValueOnce({ data: { items: [{ creator_ref: creator, label: "أماسي", status: "connected" }] } });
    await act(async () => button("تحديث حسابات رفع الملفات").click()); await fill("حساب رفع ملف TikTok", creator);
    api.post.mockResolvedValueOnce({ data: proof }); await act(async () => button("التحقق من جاهزية رفع الملفات").click());
    describeFile.mockResolvedValueOnce(metadata);
    const node = input("اختيار فيديو من الجهاز"), file = new File(["abc"], "winter.mp4", { type: "video/mp4" });
    await act(async () => { Object.defineProperty(node,"files",{ configurable:true,value:[file] });node.dispatchEvent(new Event("change",{bubbles:true})); });
    await fill("خصوصية ملف TikTok", "PUBLIC_TO_EVERYONE"); await fill("نص منشور ملف TikTok", "هدية الشتاء");
    await act(async () => input("تأكيد حق استخدام الفيديو").click());
    api.post.mockResolvedValueOnce({ data: proposal }); await act(async () => host.querySelector("form").dispatchEvent(new Event("submit",{bubbles:true,cancelable:true})));
}

test("default source is a local file,StrictMode mount has no native or model request", async () => {
    await act(async () => root.render(<StrictMode><TikTokContentWorkspace /></StrictMode>));
    expect(input("اختيار فيديو من الجهاز")).toBeTruthy(); expect(input("اختيار فيديو من الجهاز").disabled).toBe(true);
    expect(host.textContent).not.toContain("رابط الفيديو الموثق"); expect(api.get).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
    await act(async () => button("رابط خارجي").click()); expect(host.textContent).toContain("رابط الفيديو الموثق");
});

test("preview posts metadata only;approval transfers bytes only through native helper", async () => {
    await ready(); expect(uploadVideoFile).not.toHaveBeenCalled();
    expect(api.post.mock.calls[1][1]).toMatchObject({ file_manifest_sha256:fingerprint,file_size:3,creator_ref:creator });
    expect(JSON.stringify(api.post.mock.calls)).not.toContain("blob:");
    expect(button("اعتماد رفع الملف ونشره").disabled).toBe(true);
    await act(async () => input("اعتماد رفع ملف TikTok").click());
    const transfer = { url: "https://open-upload.tiktokapis.com/video/?upload_token=private-capability", expires_at:new Date(Date.now()+60000).toISOString() };
    api.post.mockResolvedValueOnce({ data: { ...proposal,status:"awaiting_upload",transfer } }).mockResolvedValueOnce({ data: { ...proposal,status:"processing",uploaded_bytes:3 } });uploadVideoFile.mockResolvedValueOnce();
    await act(async () => button("اعتماد رفع الملف ونشره").click());
    expect(uploadVideoFile).toHaveBeenCalledTimes(1); expect(uploadVideoFile.mock.calls[0][0]).toBeInstanceOf(File);
    expect(host.textContent).not.toContain("private-capability"); expect(host.textContent).toContain("TikTok يعالج الفيديو");
    expect(api.post.mock.calls.filter(([path]) => path.endsWith("approve-and-initialize"))).toHaveLength(1);
});

test("ambiguous init is not retried by the approval button", async () => {
    await ready(); await act(async () => input("اعتماد رفع ملف TikTok").click());
    api.post.mockRejectedValueOnce({ response: { data: { detail: { message:"راجع حالة العملية" } } } });
    await act(async () => button("اعتماد رفع الملف ونشره").click());
    expect(host.textContent).toContain("راجع حالة العملية"); expect(button("اعتماد رفع الملف ونشره")).toBeUndefined(); expect(uploadVideoFile).not.toHaveBeenCalled();
    expect(api.post.mock.calls.filter(([path]) => path.endsWith("approve-and-initialize"))).toHaveLength(1);
});
