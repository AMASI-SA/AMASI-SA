import React, { act, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import TikTokCommentsWorkspace from "./TikTokCommentsWorkspace";
import api from "../../lib/api";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
const ref = "a".repeat(64);
const video = "6990565363377392901";
const readonly = { receive_only: true, provider_write_allowed: false, ai_auto_reply_allowed: false, ai_analysis_requested: false };
const connection = { ...readonly, creator_ref: ref, status: "connected", source: "owner_initiated_api" };
const page = { ...readonly, items: [{ video_id: video, caption: "منتج الاختبار", media_type: "VIDEO", thumbnail_url: "https://never-fetch.example.test/image.jpg" }], has_more: false, next_cursor: null };
let root, container;
beforeEach(() => {
    api.get.mockReset(); api.post.mockReset();
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
const el = (id) => container.querySelector(`[data-testid="tiktok-comments-${id}"]`);
async function mount() { await act(async () => root.render(<TikTokCommentsWorkspace creatorRef={ref} accountConnected />)); }
async function connect() {
    await mount(); api.post.mockResolvedValueOnce({ data: connection });
    await act(async () => el("confirmation").click()); await act(async () => el("connect").click());
}
async function ready() {
    await connect(); api.get.mockResolvedValueOnce({ data: page });
    await act(async () => el("posts").click());
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(el("video"), video); el("video").dispatchEvent(new Event("change", { bubbles: true })); });
}

test("StrictMode mount never reads TikTok, sends AI context, or loads media", async () => {
    await act(async () => root.render(<StrictMode><TikTokCommentsWorkspace creatorRef={ref} accountConnected /></StrictMode>));
    expect(api.get).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
    expect(container.querySelectorAll("img,video,iframe,audio,input[type=file]")).toHaveLength(0);
    expect(el("connect").disabled).toBe(true);
});

test("connection requires explicit receive-only approval and refuses double click", async () => {
    await mount(); await act(async () => el("connect").click()); expect(api.post).not.toHaveBeenCalled();
    let finish; api.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await act(async () => el("confirmation").click());
    await act(async () => { el("connect").click(); el("connect").click(); });
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][1]).toEqual({ confirm_receive_only: true });
    await act(async () => finish({ data: connection }));
    expect(el("connected")).not.toBeNull(); expect(api.get).not.toHaveBeenCalled();
});

test("only a manual bounded post page and selected-video pull are requested", async () => {
    await ready();
    api.post.mockResolvedValueOnce({ data: { ...readonly, imported: 1, duplicates: 1, skipped: 0, inspected: 2, has_more: true, next_cursor: 2 } });
    await act(async () => el("pull").click());
    expect(api.get).toHaveBeenCalledTimes(1); expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][1]).toEqual({ video_id: video, cursor: 0 });
    expect(el("result").textContent).toContain("تم استيراد 1 تعليق");
    expect(container.textContent).not.toContain("never-fetch");
    expect(container.querySelectorAll("img,video,iframe")).toHaveLength(0);
});

test("a payload claiming automatic reply or provider write cannot unlock comment reads", async () => {
    await mount(); api.post.mockResolvedValueOnce({ data: { ...connection, provider_write_allowed: true } });
    await act(async () => el("confirmation").click()); await act(async () => el("connect").click());
    expect(el("connected")).toBeNull(); expect(el("posts")).toBeNull(); expect(container.querySelector('[role="alert"]')).not.toBeNull();
});

test("switching account aborts a pending page and hides all prior account data", async () => {
    await connect(); let finish;
    api.get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await act(async () => el("posts").click()); const signal = api.get.mock.calls[0][1].signal;
    await act(async () => root.render(<TikTokCommentsWorkspace key="new-account" creatorRef={"b".repeat(64)} accountConnected />));
    expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: page }));
    expect(el("video")).toBeNull(); expect(el("connected")).toBeNull(); expect(api.get).toHaveBeenCalledTimes(1);
});

test("non-advancing pages fail closed and never start a background retry", async () => {
    await connect(); api.get.mockResolvedValueOnce({ data: { ...page, has_more: true, next_cursor: 0 } });
    await act(async () => el("posts").click());
    expect(el("video")).toBeNull(); expect(api.get).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
});
