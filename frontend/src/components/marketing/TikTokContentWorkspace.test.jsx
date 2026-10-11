import React, { act, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import TikTokContentWorkspace from "./TikTokContentWorkspace";
import api from "../../lib/api";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
const ref = "a".repeat(64);
const creator = { creator_ref: ref, label: "Product account", status: "connected" };
const proof = { creator_ref: ref, capabilities: { publish: true, draft_upload: true, comments_read: true, messaging_read: false }, settings: { privacy_level_options: ["PUBLIC_TO_EVERYONE", "SELF_ONLY"], max_video_post_duration_sec: 60, comment_disabled: true, duet_disabled: false, stitch_disabled: false }, verified_properties: [{ type: 1, host: "media.example.test", path: "/" }] };
const proposal = { proposal_id: "b".repeat(32), creator_ref: ref, account_label: "Product account", kind: "video", delivery: "draft", status: "previewed", confirmation_digest: "c".repeat(64), input: { kind: "video", video_url: "https://media.example.test/product.mp4", caption: "Product description", privacy_level: "PUBLIC_TO_EVERYONE", is_brand_organic: true, is_branded_content: false }, effective_post_info: { upload_to_draft: true }, draft_notice: "تصل المسودة إلى صندوق TikTok لاستكمالها" };
let container, root;
beforeEach(() => {
    api.get.mockReset(); api.post.mockReset();
    Object.defineProperty(window, "crypto", { configurable: true, value: { randomUUID: jest.fn(() => "12345678-1234-1234-1234-123456789012") } });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
const testId = (id) => container.querySelector(`[data-testid="tiktok-content-${id}"]`);
const labelElement = (label) => [...container.querySelectorAll("label")].find((node) => node.querySelector("span")?.textContent === label)?.querySelector("input,textarea,select");
async function fill(element, value) {
    const proto = element.tagName === "SELECT" ? HTMLSelectElement.prototype : element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    await act(async () => { Object.getOwnPropertyDescriptor(proto, "value").set.call(element, value); element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true })); });
}
async function ready() {
    api.get.mockResolvedValueOnce({ data: { items: [creator] } });
    api.post.mockResolvedValueOnce({ data: proof });
    await act(async () => root.render(<TikTokContentWorkspace />));
    await act(async () => testId("load-accounts").click());
    await fill(testId("creator"), ref);
    await act(async () => testId("verify").click());
}
async function prepare() {
    await fill(labelElement("رابط الفيديو الموثق"), "https://media.example.test/product.mp4");
    await fill(labelElement("مدة الفيديو بالثواني"), "10");
    await fill(labelElement("نص المنشور"), "Product description");
    await fill(labelElement("خصوصية المنشور"), "PUBLIC_TO_EVERYONE");
    await fill(labelElement("الإفصاح عن المحتوى التجاري"), "own");
    await act(async () => testId("media-confirmation").click());
    await act(async () => testId("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}

test("StrictMode mount performs no content request and renders no media uploader or player", async () => {
    await act(async () => root.render(<StrictMode><TikTokContentWorkspace /></StrictMode>));
    expect(api.get).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
    expect(container.querySelectorAll("video,img,input[type=file],iframe,audio")).toHaveLength(0);
    expect(testId("preview").disabled).toBe(true);
    expect(container.textContent).toContain("15 عملية إرسال");
});

test("owner explicitly verifies a creator, previews only metadata and separately approves", async () => {
    await ready(); api.post.mockResolvedValueOnce({ data: proposal }); await prepare();
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[0][0]).toBe(`/integrations-v2/tiktok/content/creators/${ref}/verify`);
    expect(api.post.mock.calls[1][1]).toMatchObject({ creator_ref: ref, kind: "video", delivery: "draft", video_duration_seconds: 10, is_brand_organic: true, media_requirements_confirmed: true });
    expect(testId("publish").disabled).toBe(true);
    expect(container.textContent).toContain("تصل المسودة إلى صندوق TikTok");
    expect(container.textContent).toContain("لم يمنح الحساب صلاحية الرسائل");
    expect(container.querySelectorAll("video,img,input[type=file]")).toHaveLength(0);
});

test("double click submits once, treats share ID as a task and never polls", async () => {
    await ready(); api.post.mockResolvedValueOnce({ data: proposal }); await prepare();
    let finish; api.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await act(async () => testId("approval").click());
    const publish = testId("publish"); await act(async () => { publish.click(); publish.click(); });
    expect(api.post).toHaveBeenCalledTimes(3);
    expect(api.post.mock.calls[2][1]).toEqual({ confirmation_digest: "c".repeat(64) });
    expect(api.post.mock.calls[2][2].timeout).toBe(60000);
    await act(async () => finish({ data: { ...proposal, status: "accepted", publish_task_id: "p_pub_url~v1.1234", post_ids: [] } }));
    expect(testId("publish")).toBeNull();
    expect(container.textContent).toContain("معرّف مهمة النشر");
    expect(container.textContent).not.toContain("معرّفات المنشور المؤكدة");
    expect(api.get).toHaveBeenCalledTimes(1);
});

test("lost publish response locks another write until the owner reads status", async () => {
    await ready(); api.post.mockResolvedValueOnce({ data: proposal }); await prepare();
    api.post.mockRejectedValueOnce(new Error("lost response"));
    await act(async () => testId("approval").click()); await act(async () => testId("publish").click());
    expect(testId("publish")).toBeNull();
    expect(container.textContent).toContain("انقطع الاتصال");
    api.get.mockResolvedValueOnce({ data: { ...proposal, status: "uncertain", post_ids: [] } });
    await act(async () => testId("check-status").click());
    expect(api.post).toHaveBeenCalledTimes(3); expect(api.get).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("يُمنع تكراره");
});

test("input change invalidates the previous preview and unmount aborts a pending request", async () => {
    await ready(); api.post.mockResolvedValueOnce({ data: proposal }); await prepare();
    await fill(labelElement("نص المنشور"), "Different caption");
    expect(testId("proposal")).toBeNull();
    let finish; api.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await act(async () => testId("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    const signal = api.post.mock.calls[2][2].signal;
    await act(async () => root.render(<div>Other workspace</div>));
    expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: proposal }));
    expect(container.textContent).toBe("Other workspace");
});

test("history is loaded on demand with twelve records and never submits content", async () => {
    await act(async () => root.render(<TikTokContentWorkspace />));
    api.get.mockResolvedValueOnce({ data: { items: [{ ...proposal, status: "draft_delivered" }] } });
    await act(async () => testId("history").click());
    expect(api.get.mock.calls[0][1].params).toEqual({ limit: 12 });
    expect(api.post).not.toHaveBeenCalled();
    expect(container.textContent).toContain("وصلت المسودة إلى صندوق TikTok");
});

test("connection exposes an exact TikTok consent link without navigating or creating a grant", async () => {
    await act(async () => root.render(<TikTokContentWorkspace />));
    await fill(labelElement("اسم الحساب في ميزان"), "Product account");
    api.post.mockResolvedValueOnce({ data: { authorization_url: "https://www.tiktok.com/v2/auth/authorize?state=fixture", requested_scopes: ["video.publish"] } });
    await act(async () => testId("connect").click());
    expect(container.querySelector("a").href).toBe("https://www.tiktok.com/v2/auth/authorize?state=fixture");
    expect(api.post).toHaveBeenCalledTimes(1);
});
