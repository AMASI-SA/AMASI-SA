import {
    applyTemporaryAiScope,
    isAiAnalysisAsyncResponse,
    isAiAnalysisRequest,
    pollAiAnalysisJob,
    QOYOD_ANALYSIS_PAUSED,
    rewriteAiAnalysisRequest,
} from "./aiAnalysisAsync";


describe("Mezan AI asynchronous transport", () => {
    test("rewrites the analyzer request and excludes Qoyod plus unverified orders", () => {
        const config = {
            method: "post",
            url: "/ai/analyze?source=control-center",
            data: {
                question: "ما أهم مشكلة تشغيلية؟",
                context: {
                    metrics: {
                        dashboard_orders: 8,
                        total_sales: 1200,
                        qoyod_failed: 4,
                        ad_spend: 75,
                    },
                    errors: [
                        { source: "qoyod", message: "failed" },
                        { source: "ads", message: "late sync" },
                    ],
                    operational_context_v2: {
                        sample_count: 2,
                        sample_selection: "latest_canonical_orders_v2",
                        orders: [
                            {
                                sample_id: "order_sample_01",
                                status: "under_review",
                                totals: { total: 100 },
                            },
                            {
                                sample_id: "order_sample_02",
                                status: "completed",
                                totals: { total: 200 },
                                qoyod_invoice_id: "12345",
                                qoyod_posting_verified: true,
                            },
                        ],
                        observed_paths: [
                            "status<string>",
                            "qoyod_invoice_id<string>",
                        ],
                        analysis_directives: [
                            "Infer commercial concepts.",
                            "Inspect Qoyod failures.",
                        ],
                    },
                },
            },
        };

        expect(QOYOD_ANALYSIS_PAUSED).toBe(true);
        expect(isAiAnalysisRequest(config)).toBe(true);
        const rewritten = rewriteAiAnalysisRequest(config);
        expect(rewritten.url).toBe(
            "/ai/analyze-async?source=control-center",
        );
        expect(rewritten._mezanAiAnalysisAsync).toBe(true);
        expect(rewritten.data.context.metrics).toEqual({ ad_spend: 75 });
        expect(rewritten.data.context.errors).toEqual([
            { source: "ads", message: "late sync" },
        ]);

        const operational = rewritten.data.context.operational_context_v2;
        expect(operational.sample_selection).toBe("qoyod_sent_verified_only");
        expect(operational.sample_count).toBe(1);
        expect(operational.excluded_unverified_or_unsent_orders).toBe(1);
        expect(operational.orders).toEqual([
            {
                sample_id: "order_sample_02",
                status: "completed",
                totals: { total: 200 },
            },
        ]);
        expect(operational.observed_paths).toEqual(["status<string>"]);
        expect(operational.analysis_directives.join(" ")).not.toMatch(/Qoyod failures/i);
        expect(operational.analysis_directives.join(" ")).toMatch(/temporarily disabled/i);

        const unrelated = { method: "post", url: "/orders/analyze" };
        expect(rewriteAiAnalysisRequest(unrelated)).toBe(unrelated);
    });

    test("blocks direct Qoyod questions before any analysis request is sent", () => {
        let captured;
        try {
            rewriteAiAnalysisRequest({
                method: "post",
                url: "/ai/analyze",
                data: { question: "لماذا توقفت قيود؟", context: {} },
            });
        } catch (error) {
            captured = error;
        }

        expect(captured).toBeTruthy();
        expect(captured.response).toMatchObject({
            status: 409,
            data: {
                code: "qoyod_ai_analysis_paused",
                retryable: false,
            },
        });
        expect(captured.response.data.detail).toContain("تحليل قيود موقوف مؤقتًا");
    });

    test("sanitizes JSON string payloads without changing their transport type", () => {
        const sanitized = applyTemporaryAiScope(JSON.stringify({
            question: "حلل الإعلانات",
            context: {
                metrics: { total_sales: 10, ad_spend: 2 },
                operational_context_v2: {
                    sample_count: 1,
                    orders: [{ sample_id: "unsent" }],
                },
            },
        }));

        expect(typeof sanitized).toBe("string");
        const parsed = JSON.parse(sanitized);
        expect(parsed.context.metrics).toEqual({ ad_spend: 2 });
        expect(parsed.context.operational_context_v2.orders).toEqual([]);
        expect(parsed.context.operational_context_v2.sample_count).toBe(0);
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
