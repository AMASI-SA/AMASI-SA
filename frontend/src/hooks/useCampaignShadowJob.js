import { useCallback, useEffect, useRef, useState } from "react";

const POLL_MS = 5000;
const MAX_POLL_MS = 5 * 60 * 1000;

// Reading the page only reads the cache. A user action is the only POST.
export default function useCampaignShadowJob(api, endpoint, days = 1) {
    const [snapshot, setSnapshot] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [pollingPaused, setPollingPaused] = useState(false);
    const session = useRef(null);
    const refreshRef = useRef(null);

    useEffect(() => {
        const state = { active: true, timer: null, controller: null, inFlight: false,
            deadline: Date.now() + MAX_POLL_MS, snapshot: null };
        session.current = state;
        setSnapshot(null); setBusy(false); setError(""); setPollingPaused(false);

        const accept = (data) => {
            if (!state.active) return;
            state.snapshot = data;
            setSnapshot(data);
            setError("");
            if (data?.job?.active) {
                if (Date.now() >= state.deadline) {
                    setPollingPaused(true);
                } else {
                    state.timer = window.setTimeout(() => read(), POLL_MS);
                }
            } else {
                setPollingPaused(false);
            }
        };
        const read = async () => {
            if (!state.active || state.inFlight) return;
            state.inFlight = true;
            state.controller = new AbortController();
            try {
                const { data } = await api.get(`${endpoint}?days=${days}`, {
                    signal: state.controller.signal, timeout: 10_000,
                });
                accept(data);
            } catch {
                if (state.active) {
                    setError("تعذر قراءة حالة المهمة. يمكنك إعادة المحاولة.");
                    setPollingPaused(Boolean(state.snapshot?.job?.active));
                }
            } finally {
                state.inFlight = false;
                if (state.active) setBusy(false);
            }
        };
        refreshRef.current = async () => {
            if (!state.active || state.inFlight) return;
            window.clearTimeout(state.timer);
            state.deadline = Date.now() + MAX_POLL_MS;
            setPollingPaused(false); setBusy(true); setError("");
            if (state.snapshot?.job?.active) {
                await read(); // Resume observation of existing work; never enqueue twice.
                return;
            }
            state.inFlight = true;
            state.controller = new AbortController();
            try {
                const { data } = await api.post(`${endpoint}/refresh?days=${days}`, null,
                    { signal: state.controller.signal, timeout: 15_000 });
                accept(data);
                if (data?.request_blocked_by === "active_job") {
                    setError("هناك مهمة أخرى جارية. انتظر اكتمالها ثم اطلب التحديث.");
                } else if (data?.request_blocked_by === "cooldown") {
                    setError("انتظر دقيقة بعد آخر طلب قبل بدء تحديث آخر.");
                }
            } catch {
                if (state.active) setError("تعذر تأكيد طلب التحديث. أعد قراءة الحالة قبل المحاولة.");
                // The POST may have reached the server; inspect its durable state
                // rather than resubmitting automatically after a lost response.
                if (state.active) state.timer = window.setTimeout(() => read(), POLL_MS);
            } finally {
                state.inFlight = false;
                if (state.active) setBusy(false);
            }
        };
        read();
        return () => {
            state.active = false;
            window.clearTimeout(state.timer);
            state.controller?.abort();
            if (session.current === state) refreshRef.current = null;
        };
    }, [api, endpoint, days]);

    const refresh = useCallback(() => refreshRef.current?.(), []);
    return { snapshot, busy, error, pollingPaused, refresh };
}
