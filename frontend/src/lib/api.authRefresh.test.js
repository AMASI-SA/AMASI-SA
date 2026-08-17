import axios from "axios";
import api from "./api";

jest.mock("axios", () => {
    const responseHandlers = [];
    const instance = {
        interceptors: {
            request: { use: jest.fn() },
            response: {
                use: jest.fn((fulfilled, rejected) => {
                    responseHandlers.push({ fulfilled, rejected });
                }),
            },
        },
        request: jest.fn(),
    };
    const client = {
        create: jest.fn(() => instance),
        get: jest.fn(),
        post: jest.fn(),
        __instance: instance,
        __responseHandlers: responseHandlers,
    };
    return { __esModule: true, default: client };
});

function authResponseError(path = "/auth/me") {
    return Object.assign(new Error("access expired"), {
        response: { status: 401 },
        config: { method: "get", url: path },
    });
}

describe("browser session refresh interceptor", () => {
    const rejectResponse = axios.__responseHandlers[0].rejected;

    beforeEach(() => {
        axios.post.mockReset();
        api.request.mockReset();
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    test("retries the original request after one successful cookie refresh", async () => {
        const response = { data: { id: "owner-1" } };
        axios.post.mockResolvedValueOnce({ data: { ok: true } });
        api.request.mockResolvedValueOnce(response);

        await expect(rejectResponse(authResponseError())).resolves.toBe(response);

        expect(axios.post).toHaveBeenCalledWith(
            expect.stringContaining("/api/auth/refresh"),
            {},
            expect.objectContaining({ withCredentials: true, timeout: 6_000 }),
        );
        expect(api.request).toHaveBeenCalledWith(expect.objectContaining({
            method: "get",
            url: "/auth/me",
            _mezanAuthRetried: true,
        }));
    });

    test("preserves a transient refresh failure instead of converting it to 401", async () => {
        const originFailure = Object.assign(new Error("origin unavailable"), {
            response: { status: 520, headers: { "retry-after": "60" } },
        });
        axios.post.mockRejectedValueOnce(originFailure);

        await expect(rejectResponse(authResponseError())).rejects.toBe(originFailure);
        expect(api.request).not.toHaveBeenCalled();
    });

    test("caps refresh transport time to the caller's remaining deadline", async () => {
        jest.spyOn(Date, "now").mockReturnValue(1_000);
        const accessFailure = authResponseError();
        accessFailure.config.timeout = 6_000;
        accessFailure.config._mezanAuthDeadlineAt = 1_250;
        axios.post.mockRejectedValueOnce(new Error("timeout"));

        await expect(rejectResponse(accessFailure)).rejects.toThrow("timeout");
        expect(axios.post).toHaveBeenCalledWith(
            expect.stringContaining("/api/auth/refresh"),
            {},
            expect.objectContaining({ timeout: 250 }),
        );
    });

    test("keeps a definitive refresh 401 as an anonymous-session result", async () => {
        const accessFailure = authResponseError();
        axios.post.mockRejectedValueOnce(Object.assign(new Error("expired"), {
            response: { status: 401 },
        }));

        await expect(rejectResponse(accessFailure)).rejects.toBe(accessFailure);
        expect(api.request).not.toHaveBeenCalled();
    });

    test("shares one refresh request across concurrent 401 responses", async () => {
        let finishRefresh;
        axios.post.mockReturnValueOnce(new Promise((resolve) => {
            finishRefresh = resolve;
        }));
        api.request
            .mockResolvedValueOnce({ data: { request: 1 } })
            .mockResolvedValueOnce({ data: { request: 2 } });

        const first = rejectResponse(authResponseError("/auth/me"));
        const second = rejectResponse(authResponseError("/orders"));
        expect(axios.post).toHaveBeenCalledTimes(1);

        finishRefresh({ data: { ok: true } });
        await expect(Promise.all([first, second])).resolves.toHaveLength(2);
        expect(api.request).toHaveBeenCalledTimes(2);
    });
});
