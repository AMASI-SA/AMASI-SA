import { Blob } from "buffer";
import { createHash, webcrypto } from "crypto";
import { TextEncoder } from "util";
import { chunkPlan, fingerprintFile, uploadVideoFile, validateDestination, FINGERPRINT_BLOCK } from "./tiktokFileTransfer";

const destination = "https://open-upload.tiktokapis.com/video/?upload_id=fixture&upload_token=private-capability";
function file(bytes) { const value = new Blob([bytes], { type: "video/mp4" }); Object.defineProperty(value, "name", { value: "winter.mp4" }); return value; }
const transfer = (offset = 0) => ({ url: destination, expires_at: new Date(Date.now() + 60000).toISOString(), offset });
beforeEach(() => { global.TextEncoder = TextEncoder; Object.defineProperty(window, "crypto", { configurable: true, value: webcrypto }); global.fetch = jest.fn(); });
afterEach(() => { delete global.fetch; });

test("streaming fingerprint commits every block and stays bounded", async () => {
    const bytes = Buffer.alloc(FINGERPRINT_BLOCK + 1, 65), selected = file(bytes);
    const first = createHash("sha256").update(bytes.subarray(0, FINGERPRINT_BLOCK)).digest();
    const second = createHash("sha256").update(bytes.subarray(FINGERPRINT_BLOCK)).digest();
    const expected = createHash("sha256").update(`tiktok-file-manifest-v1\0${bytes.length}\0${FINGERPRINT_BLOCK}\0`).update(first).update(second).digest("hex");
    const progress = [];
    expect(await fingerprintFile(selected, { onProgress: (value) => progress.push(value) })).toBe(expected);
    expect(progress[progress.length - 1]).toBe(100);
    expect(fetch).not.toHaveBeenCalled();
});

test("PUT chunks run sequentially to TikTok without Mezan auth or a media proxy", async () => {
    const selected = file(Buffer.alloc(25 * 1024 * 1024, 1)), progress = [];
    let finishFirst;
    fetch.mockImplementationOnce(() => new Promise((resolve) => { finishFirst = () => resolve({ status: 206 }); })).mockResolvedValueOnce({ status: 201 });
    const request = uploadVideoFile(selected, chunkPlan(selected.size), transfer(), { onProgress: (value) => progress.push(value) });
    expect(fetch).toHaveBeenCalledTimes(1);
    finishFirst(); await request;
    expect(fetch).toHaveBeenCalledTimes(2);
    const first = fetch.mock.calls[0], last = fetch.mock.calls[1];
    expect(first[0]).toBe(destination);
    expect(first[1]).toMatchObject({ method: "PUT", credentials: "omit", redirect: "error", referrerPolicy: "no-referrer", mode: "cors" });
    expect(first[1].headers).toEqual({ "Content-Type": "video/mp4", "Content-Range": "bytes 0-10485759/26214400" });
    expect(first[1].body.size).toBe(10485760);
    expect(last[1].headers["Content-Range"]).toBe("bytes 10485760-26214399/26214400");
    expect(last[1].body.size).toBe(15728640);
    expect(progress).toEqual([40, 100]);
});

test("ambiguous chunk failure stops without replaying a chunk", async () => {
    const selected = file(Buffer.alloc(25 * 1024 * 1024, 1));
    fetch.mockRejectedValueOnce(new Error(destination));
    await expect(uploadVideoFile(selected, chunkPlan(selected.size), transfer())).rejects.toThrow("لم يُكرر الإرسال");
    expect(fetch).toHaveBeenCalledTimes(1);
});

test("explicit resume uses only the proven original boundary", async () => {
    const selected = file(Buffer.alloc(25 * 1024 * 1024, 1)); fetch.mockResolvedValue({ status: 201 });
    await uploadVideoFile(selected, chunkPlan(selected.size), transfer(10485760));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][1].headers["Content-Range"]).toBe("bytes 10485760-26214399/26214400");
    await expect(uploadVideoFile(selected, chunkPlan(selected.size), transfer(123))).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(1);
});

test.each(["https://open-upload.tiktokapis.com.evil.test/video/?x=1", "http://open-upload.tiktokapis.com/video/?x=1", "https://open-upload.tiktokapis.com:443/video/?x=1", "https://user@open-upload.tiktokapis.com/video/?x=1", "https://open-upload.tiktokapis.com/other/?x=1"])('untrusted destination rejected: %s', (url) => {
    expect(() => validateDestination(url)).toThrow(); expect(fetch).not.toHaveBeenCalled();
});

test("abort before transfer sends nothing", async () => {
    const selected = file(Buffer.from("abc")), controller = new AbortController(); controller.abort();
    await expect(uploadVideoFile(selected, chunkPlan(selected.size), transfer(), { signal: controller.signal })).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
});
