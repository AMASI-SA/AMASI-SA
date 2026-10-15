import {
    isAiAnalysisAsyncResponse,
    isAiAnalysisRequest,
    pollAiAnalysisJob,
    rewriteAiAnalysisRequest,
} from "./aiAnalysisAsync";


describe("Mezan AI asynchronous transport", () => {
    test("rewrites only the synchronous analyzer request", () => {
        const config = {
            method: "post",
            url: "/ai/analyze?source=control-center",
            data: { question: "ما أهم مشكلة؟" },
        };

        expect(isAiAnalysisRequest(config)).toBe(true);
        const rewritten = rewriteAiAnalysisRequest(config);
        expect(rewritten.url).toBe(
            "/ai/analyze-async?source=control-center",
        );
        expect(rewritten.data).toEqual(config.data);
        expect(rewritten._mezanAiAnalysisAsync).toBe(true);

        const unrelated = { method: "post", url: "/orders/analyze" };
        expect(rewriteAiAnalysisRequest(unrelated)).toBe(unrelated);
    });

    test("recognizes only accepted asynchronous analyzer responses", () => {
        expect(isAiAnalysisAsyncResponse({
            status: 202,
            config: { _mezanAiAnalysisAsync: true },
            data: { run_id: "job-1", status: "queued" },
        })).toBe(true);
        expect(isAiAnalysisAsyncResponse({
            status: 200,
            config: { _mezanAiAnalysisAsync: true },
            data: { run_id: "job-1", status: "complete" },
        })).toBe(false);
    });

    test("polls queued and running jobs until analysis completes", async () => {
        const loadJob = jest
            .fn()
            .mockResolvedValueOnce({ run_id: "job-1", status: "running" })
            .mockResolvedValueOnce({
                run_id: "job-1",
                status: "complete",
                mode: "read_only_analysis",
                writes_performed: false,
                analysis: { summary: "اكتمل التحليل" },
            });
        const wait = jest.fn().mockResolvedValue(undefined);

        const result = await pollAiAnalysisJob({
            accepted: { run_id: "job-1", status: "queued" },
            loadJob,
            wait,
            attempts: 3,
            intervalMs: 1,
        });

        expect(loadJob).toHaveBeenCalledTimes(2);
        expect(loadJob).toHaveBeenNthCalledWith(1, "job-1");
        expect(wait).toHaveBeenCalledTimes(1);
        expect(result.analysis.summary).toBe("اكتمل التحليل");
        expect(result.writes_performed).toBe(false);
    });

    test("surfaces a controlled terminal failure to the page", async () => {
        await expect(pollAiAnalysisJob({
            accepted: { run_id: "job-2", status: "queued" },
            loadJob: jest.fn().mockResolvedValue({
                run_id: "job-2",
                status: "failed",
                error: {
                    code: "ai_analysis_timeout",
                    message: "انتهت مهلة تحليل الذكاء.",
                    http_status: 504,
                    retryable: true,
                },
            }),
            wait: jest.fn().mockResolvedValue(undefined),
            attempts: 2,
            intervalMs: 1,
        })).rejects.toMatchObject({
            response: {
                status: 504,
                data: {
                    detail: "انتهت مهلة تحليل الذكاء.",
                    code: "ai_analysis_timeout",
                    retryable: true,
                },
            },
        });
    });

    test("fails closed when polling exceeds the safety window", async () => {
        await expect(pollAiAnalysisJob({
            accepted: { run_id: "job-3", status: "queued" },
            loadJob: jest.fn().mockResolvedValue({
                run_id: "job-3",
                status: "running",
            }),
            wait: jest.fn().mockResolvedValue(undefined),
            attempts: 2,
            intervalMs: 1,
        })).rejects.toMatchObject({
            response: {
                status: 504,
                data: {
                    code: "ai_analysis_poll_timeout",
                    retryable: true,
                },
            },
        });
    });
});
