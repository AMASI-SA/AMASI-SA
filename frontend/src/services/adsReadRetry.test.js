import {
    ADS_READ_RETRY_POLICY,
    isTransientAdsReadFailure,
    retryAdsRead,
} from "./adsReadRetry";

function responseError(status, detail = "") {
    return {
        response: {
            status,
            data: { detail },
        },
    };
}

test("retries transient Cloudflare campaign reads and returns the successful page", async () => {
    const operation = jest.fn()
        .mockRejectedValueOnce(responseError(520, "The origin web server sent an invalid response."))
        .mockResolvedValueOnce({ campaigns: ["page-two"] });
    const sleep = jest.fn().mockResolvedValue(undefined);

    await expect(retryAdsRead(operation, { sleep })).resolves.toEqual({
        campaigns: ["page-two"],
    });
    expect(operation).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledWith(450);
});

test("recognizes Cloudflare text even when the proxy omitted a status", () => {
    expect(isTransientAdsReadFailure({
        message: "The origin web server sent a response that Cloudflare could not parse.",
    })).toBe(true);
});

test("does not retry validation or authentication failures", async () => {
    const error = responseError(400, "invalid date range");
    const operation = jest.fn().mockRejectedValue(error);
    const sleep = jest.fn();

    await expect(retryAdsRead(operation, { sleep })).rejects.toBe(error);
    expect(operation).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
});

test("stops after the bounded read-only retry budget", async () => {
    const error = responseError(503, "temporarily unavailable");
    const operation = jest.fn().mockRejectedValue(error);
    const sleep = jest.fn().mockResolvedValue(undefined);

    await expect(retryAdsRead(operation, { sleep })).rejects.toBe(error);
    expect(operation).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenCalledTimes(2);
    expect(ADS_READ_RETRY_POLICY).toMatchObject({
        read_only: true,
        maximum_attempts: 3,
        provider_writes_allowed: false,
    });
});
