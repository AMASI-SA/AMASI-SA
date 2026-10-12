import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import TikTokReplyDelivery, { TikTokReplyHistory } from "./TikTokReplyDelivery";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { post: jest.fn() } }));
let root, container;
const suggestion = { conversation_id: "thread", id: "draft", version: 2 };
const proposal = { proposal_id: "a".repeat(32), confirmation_digest: "b".repeat(64), status: "previewed", text: "كودك AM 😄", source_text: "كود" };
beforeEach(() => { api.post.mockReset(); container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
const button = (text) => [...container.querySelectorAll("button")].find((row) => row.textContent === text);
async function render(text = proposal.text, onConsumed = jest.fn()) { await act(async () => root.render(<TikTokReplyDelivery suggestion={suggestion} text={text} onConsumed={onConsumed} />)); }
async function preview() { api.post.mockResolvedValueOnce({ data: proposal }); await act(async () => button("معاينة الرد على تيك توك").click()); }

test("mount never generates AI or contacts TikTok; preview cannot send", async () => {
    await render(); expect(api.post).not.toHaveBeenCalled(); await preview();
    expect(api.post.mock.calls[0][0]).toMatch(/\/preview$/);
    expect(api.post.mock.calls[0][1]).toEqual({ conversation_id: "thread", suggestion_id: "draft", version: 2, text: proposal.text });
    expect(button("اعتماد وإرسال الرد إلى تيك توك").disabled).toBe(true);
    expect(container.querySelectorAll("input[type=file],video,img")).toHaveLength(0);
});

test("explicit approval sends exactly the frozen preview and locks repeated clicks", async () => {
    const consumed = jest.fn(); await render(proposal.text, consumed); await preview();
    await act(async () => container.querySelector('input[type=checkbox]').click());
    api.post.mockResolvedValueOnce({ data: { ...proposal, status: "accepted" } });
    await act(async () => { const send = button("اعتماد وإرسال الرد إلى تيك توك"); send.click(); send.click(); });
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][1]).toEqual({ confirmation_digest: proposal.confirmation_digest });
    expect(consumed).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("ظهوره للعامة لم يُثبت بعد");
    expect(button("معاينة الرد على تيك توك").disabled).toBe(true);
});

test("editing text invalidates approval until a new preview", async () => {
    await render(); await preview(); await act(async () => container.querySelector('input[type=checkbox]').click());
    await render("نص آخر");
    expect(button("اعتماد وإرسال الرد إلى تيك توك").disabled).toBe(true);
    expect(container.textContent).toContain("أعد المعاينة");
    expect(api.post).toHaveBeenCalledTimes(1);
});

test("ambiguous network result never enables a retry", async () => {
    await render(); await preview(); await act(async () => container.querySelector('input[type=checkbox]').click());
    api.post.mockRejectedValueOnce(new Error("network"));
    await act(async () => button("اعتماد وإرسال الرد إلى تيك توك").click());
    expect(container.textContent).toContain("افتح سجل الردود ولا تكرر الإرسال");
    expect(button("معاينة الرد على تيك توك").disabled).toBe(true);
    expect(api.post).toHaveBeenCalledTimes(2);
});

test("reply history recovers receipts only on demand and checks visibility without sending", async () => {
    await act(async () => root.render(<TikTokReplyHistory conversationId="thread" />));
    expect(api.post).not.toHaveBeenCalled();
    api.post.mockResolvedValueOnce({ data: { items: [{ ...proposal, status: "accepted" }] } });
    await act(async () => button("سجل ردود تيك توك").click());
    api.post.mockResolvedValueOnce({ data: { ...proposal, status: "published_public" } });
    await act(async () => button("التحقق من ظهور الرد").click());
    expect(container.textContent).toContain("تأكد ظهور الرد للعامة");
    expect(api.post.mock.calls.map(([path]) => path.endsWith("/execute"))).toEqual([false, false]);
});
