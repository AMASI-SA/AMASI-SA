import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleRecommendationAction from "./GoogleRecommendationAction";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { post: jest.fn() } }));
const item = { recommendation_id: "google_ads:campaign:123:456:PAUSE_CAMPAIGN", action: "PAUSE_CAMPAIGN" };
let container, root;
beforeEach(() => {
    api.post.mockReset(); container = document.createElement("div"); document.body.appendChild(container);
    root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) {
    await act(async () => root.render(<GoogleRecommendationAction analysisId="saved" recommendation={item} onPreview={() => {}} {...props} />));
}

test("one review request opens a frozen proposal and never approves it", async () => {
    let finish; const onPreview = jest.fn();
    api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render({ onPreview });
    await act(async () => { const button = container.querySelector("button"); button.click(); button.click(); });
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(container.querySelector("button").disabled).toBe(true);
    const proposal = { proposal_id: "p1", action: "pause", account_id: "123", status: "previewed" };
    await act(async () => finish({ data: proposal }));
    expect(onPreview).toHaveBeenCalledWith(proposal);
    expect(api.post.mock.calls[0][0]).toBe("/integrations-v2/google_ads/ai/recommendations/preview");
    expect(api.post).toHaveBeenCalledTimes(1);
});

test("a late response cannot open the prior analysis after switching", async () => {
    let finish; const onPreview = jest.fn();
    api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render({ onPreview }); await act(async () => container.querySelector("button").click());
    const signal = api.post.mock.calls[0][2].signal;
    await render({ analysisId: "new-analysis", onPreview });
    expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: { proposal_id: "foreign-old" } }));
    expect(onPreview).not.toHaveBeenCalled();
    expect(container.querySelector("button").disabled).toBe(false);
});

test("expired advice explains the failure and is never retried automatically", async () => {
    const onPreview = jest.fn();
    api.post.mockRejectedValue({ response: { status: 409, data: { detail: { message: "أعد تحليل الحملة؛ أدلة التوصية قديمة." } } } });
    await render({ onPreview }); await act(async () => container.querySelector("button").click());
    expect(container.querySelector('[role="alert"]').textContent).toContain("أعد تحليل الحملة");
    expect(api.post).toHaveBeenCalledTimes(1); expect(onPreview).not.toHaveBeenCalled();
});

test("diagnostic and unsaved advice do not offer provider execution review", async () => {
    await render({ recommendation: { ...item, action: "REVIEW_TRACKING" } });
    expect(container.querySelector("button")).toBeNull();
    await render({ analysisId: undefined }); expect(container.querySelector("button")).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
});
