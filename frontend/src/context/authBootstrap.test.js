import {
    AuthBootstrapTimeoutError,
    runBoundedAuthBootstrap,
} from "./authBootstrap";

async function flushPromises() {
    await Promise.resolve();
    await Promise.resolve();
}

describe("bounded auth bootstrap", () => {
    afterEach(() => {
        jest.useRealTimers();
    });

    test("recovers from one transient failure without an open retry loop", async () => {
        const temporary = Object.assign(new Error("temporary"), {
            response: { status: 502, headers: {} },
        });
        const probe = jest.fn()
            .mockRejectedValueOnce(temporary)
            .mockResolvedValueOnce({ user: { id: "owner-1" } });

        const resultPromise = runBoundedAuthBootstrap({
            probe,
            retryDelayMs: 1,
            deadlineMs: 1_000,
            requestTimeoutMs: 500,
        });

        await expect(resultPromise).resolves.toEqual({ user: { id: "owner-1" } });
        expect(probe).toHaveBeenCalledTimes(2);
    });

    test("honours Retry-After without keeping the loading screen open", async () => {
        const unavailable = Object.assign(new Error("origin unavailable"), {
            response: {
                status: 520,
                headers: { "retry-after": "60" },
            },
        });
        const probe = jest.fn().mockRejectedValue(unavailable);

        await expect(runBoundedAuthBootstrap({
            probe,
            retryDelayMs: 100,
            deadlineMs: 1_000,
        })).rejects.toBe(unavailable);
        expect(probe).toHaveBeenCalledTimes(1);
    });

    test("forces a hung probe to fail at the wall-clock deadline", async () => {
        jest.useFakeTimers();
        jest.setSystemTime(new Date("2026-08-17T16:00:00Z"));
        const probe = jest.fn(() => new Promise(() => {}));
        const resultPromise = runBoundedAuthBootstrap({
            probe,
            deadlineMs: 200,
            requestTimeoutMs: 150,
        });
        await flushPromises();

        jest.advanceTimersByTime(200);
        await flushPromises();

        await expect(resultPromise).rejects.toBeInstanceOf(AuthBootstrapTimeoutError);
        expect(probe).toHaveBeenCalledTimes(1);
    });

    test("cancels a stale generation when its provider unmounts", async () => {
        jest.useFakeTimers();
        const controller = new AbortController();
        const probe = jest.fn(() => new Promise(() => {}));
        const resultPromise = runBoundedAuthBootstrap({
            probe,
            signal: controller.signal,
            deadlineMs: 1_000,
        });

        controller.abort();
        await expect(resultPromise).rejects.toMatchObject({ name: "AbortError" });
    });
});
