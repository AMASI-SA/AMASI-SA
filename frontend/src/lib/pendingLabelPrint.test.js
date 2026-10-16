import { createPendingLabelPrint } from "./pendingLabelPrint";

const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
const ready = { ready: true, label_url: "https://labels.example/current.pdf" };
function setup(saved = new Map()) {
    const popup = { closed: false, close: jest.fn(), location: { replace: jest.fn() } };
    const load = jest.fn();
    const storage = { getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value) };
    const openWindow = jest.fn(() => popup);
    const renderCourier = jest.fn(() => true);
    const queue = createPendingLabelPrint({ storage, load, openWindow, renderCourier });
    queue.setScope("api:actor-a");
    return { queue, load, popup, openWindow, saved, renderCourier };
}
beforeEach(() => jest.useFakeTimers("modern"));
afterEach(() => jest.useRealTimers());
async function advance(ms) { for (let elapsed = 0; elapsed < ms; elapsed += 1000) { jest.advanceTimersByTime(Math.min(1000, ms - elapsed)); await flush(); } }

test("reserves synchronously, releases the screen, retries delayed label, opens exactly once", async () => {
    const { queue, load, openWindow, popup } = setup();
    load.mockResolvedValueOnce({ ready: false }).mockResolvedValueOnce(ready);
    const action = queue.request("100");
    expect(openWindow).toHaveBeenCalledTimes(1);
    await expect(action).resolves.toEqual({ pending: true });
    await flush();
    await queue.request("100");
    expect(openWindow).toHaveBeenCalledTimes(1);
    await advance(2000);
    expect(popup.location.replace).toHaveBeenCalledTimes(1);
    expect(queue.snapshot()).toEqual([]);
    await advance(60000);
    expect(load).toHaveBeenCalledTimes(2);
});

test("transport failure keeps durable intent and eventually prints", async () => {
    const { queue, load, saved, popup } = setup();
    load.mockRejectedValueOnce(new Error("timeout")).mockResolvedValueOnce(ready);
    await queue.request("100"); await flush();
    expect([...saved.values()].join("")).toContain("100");
    await advance(2000);
    expect(popup.location.replace).toHaveBeenCalledWith(ready.label_url);
});

test("reload recovers intent, never opens an unsolicited popup, explicit open revalidates", async () => {
    const first = setup();
    first.load.mockResolvedValue({ ready: false });
    await first.queue.request("100"); await flush();
    first.queue.setScope(null);
    const second = setup(first.saved);
    second.load.mockResolvedValue(ready);
    await advance(1000);
    expect(second.openWindow).not.toHaveBeenCalled();
    expect(second.queue.snapshot()[0].state).toBe("ready");
    await second.queue.request("100"); await flush();
    expect(second.load).toHaveBeenCalledTimes(2);
    expect(second.popup.location.replace).toHaveBeenCalledWith(ready.label_url);
    expect([...first.saved.values()].join("")).not.toContain("https:");
});

test("logout or actor switch suppresses in-flight completion and isolates recovery", async () => {
    const { queue, load, popup } = setup();
    let resolve;
    load.mockReturnValue(new Promise(done => { resolve = done; }));
    await queue.request("100");
    queue.setScope(null);
    queue.setScope("api:actor-b");
    resolve(ready); await flush();
    expect(popup.close).toHaveBeenCalledTimes(1);
    expect(popup.location.replace).not.toHaveBeenCalled();
    expect(queue.snapshot()).toEqual([]);
});

test("server permission revocation stops retries and stays stopped after restart", async () => {
    const first = setup();
    first.load.mockRejectedValue(Object.assign(new Error("denied"), { status: 403 }));
    await first.queue.request("100"); await flush();
    expect(first.queue.snapshot()[0].state).toBe("stopped");
    await advance(60000);
    expect(first.load).toHaveBeenCalledTimes(1);
    first.queue.setScope(null);
    const second = setup(first.saved);
    await advance(60000);
    expect(second.load).not.toHaveBeenCalled();
});

test("cancel suppresses late response and deletes saved intent", async () => {
    const { queue, load, popup, saved } = setup();
    let resolve;
    load.mockReturnValue(new Promise(done => { resolve = done; }));
    await queue.request("100");
    queue.cancel("100");
    resolve(ready); await flush();
    expect(popup.location.replace).not.toHaveBeenCalled();
    expect([...saved.values()].join("")).toBe("[]");
});

test("store courier still uses its renderer and a closed popup retains ready intent", async () => {
    const { queue, load, popup, renderCourier } = setup();
    load.mockResolvedValue({ ready: true, label_type: "store_courier", print_data: { qr_code: "100" } });
    await queue.request("100"); await flush();
    expect(renderCourier).toHaveBeenCalledWith(popup, { qr_code: "100" });
    popup.closed = true;
    load.mockResolvedValue(ready);
    await queue.request("200"); await flush();
    expect(queue.snapshot()[0].state).toBe("ready");
    expect(popup.location.replace).not.toHaveBeenCalled();
});

test("cannot silently promise durability if local storage is unavailable", async () => {
    const load = jest.fn();
    const popup = { close: jest.fn() };
    const queue = createPendingLabelPrint({
        storage: { getItem: () => null, setItem: () => { throw new Error("quota"); } },
        load, openWindow: () => popup, renderCourier: jest.fn(),
    });
    queue.setScope("api:actor");
    await expect(queue.request("100")).rejects.toThrow("تعذر حفظ طلب الطباعة");
    expect(load).not.toHaveBeenCalled();
    expect(popup.close).toHaveBeenCalled();
});
