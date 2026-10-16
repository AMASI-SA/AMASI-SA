// Only intent is durable: URLs, customer details and authorization never are.
export function createPendingLabelPrint({ storage, load, openWindow, renderCourier, changed = () => {}, onReady = () => {}, schedule = setTimeout, unschedule = clearTimeout }) {
    let scope = null;
    let jobs = new Map();
    let timer;
    let generation = 0;
    const key = () => `mezan.pending-label.v1:${scope}`;
    const snapshot = () => [...jobs.values()].map(({ orderNumber, source, state, message }) => ({ orderNumber, source, state, message }));
    const publish = () => {
        if (scope) storage.setItem(key(), JSON.stringify([...jobs.values()].map(({ orderNumber, source, state }) => ({ orderNumber, source, state: state === "stopped" ? "stopped" : "waiting" }))));
        changed(snapshot());
    };
    const reserve = () => {
        const popup = openWindow();
        if (popup) {
            popup.opener = null;
            if (popup.document?.body) {
                popup.document.title = "انتظار بوليصة الشحن";
                popup.document.body.dir = "rtl";
                popup.document.body.textContent = "طلب الطباعة محفوظ. ننتظر وصول رابط البوليصة إلى ميزان، وسيفتح هنا تلقائيًا. يمكنك متابعة العمل في التطبيق.";
            }
        }
        return popup;
    };
    const close = (job) => { if (job.popup && !job.popup.closed) job.popup.close?.(); job.popup = null; };
    const arm = () => {
        unschedule(timer);
        if (scope && [...jobs.values()].some(job => job.state === "waiting")) timer = schedule(tick, 1000);
    };
    const check = async (job) => {
        if (!scope || job.running || job.state !== "waiting") return;
        const epoch = generation;
        job.running = true;
        try {
            const result = await load(job.source, job.orderNumber);
            if (epoch !== generation || jobs.get(job.orderNumber) !== job) return;
            if (result?.ready && (result.label_url || (result.label_type === "store_courier" && result.print_data?.qr_code))) {
                if (job.popup && !job.popup.closed) {
                    if (result.label_type === "store_courier") {
                        if (!renderCourier(job.popup, result.print_data)) throw new Error("تعذر تجهيز الطباعة");
                    } else {
                        const url = new URL(result.label_url);
                        if (!["https:", "http:"].includes(url.protocol)) throw new Error("رابط البوليصة غير صالح");
                        job.popup.location.replace(url.href);
                    }
                    jobs.delete(job.orderNumber);
                } else {
                    job.state = "ready";
                    job.message = "البوليصة جاهزة — اضغط فتح ملف الطباعة";
                }
                onReady(job.orderNumber, result);
                return result;
            }
            job.message = result?.message || "بانتظار وصول رابط البوليصة؛ ستفتح تلقائيًا";
        } catch (error) {
            if (epoch !== generation || jobs.get(job.orderNumber) !== job) return;
            const status = error?.status || error?.response?.status;
            if (status >= 400 && status < 500 && ![408, 429].includes(status)) {
                job.state = "stopped";
                job.message = error.message || "توقفت المتابعة؛ تحقق من الصلاحية وحالة الطلب";
                close(job);
            } else {
                job.message = "تعذر الاتصال؛ طلب الطباعة محفوظ وسنعيد المحاولة تلقائيًا";
            }
        } finally {
            job.running = false;
            if (epoch === generation) {
                job.attempts += 1;
                job.next = Date.now() + Math.min(30000, 2000 * 2 ** Math.min(job.attempts - 1, 4));
                try { publish(); } catch { changed(snapshot()); }
                arm();
            }
        }
    };
    async function tick() {
        await Promise.all([...jobs.values()].filter(job => job.state === "waiting" && job.next <= Date.now()).map(check));
        arm();
    }
    return {
        snapshot,
        setScope(next) {
            if (scope === next) return;
            generation += 1;
            unschedule(timer);
            jobs.forEach(close);
            jobs = new Map();
            scope = next;
            if (scope) {
                let saved = [];
                try { saved = JSON.parse(storage.getItem(key()) || "[]"); } catch { /* Corrupt local intent is ignored. */ }
                if (Array.isArray(saved)) saved.forEach(item => {
                    if (typeof item?.orderNumber === "string" && ["owner", "fulfillment"].includes(item.source)) jobs.set(item.orderNumber, { ...item, state: item.state === "stopped" ? "stopped" : "waiting", attempts: 0, next: 0 });
                });
            }
            changed(snapshot());
            arm();
        },
        request(orderNumber, source = "fulfillment") {
            if (!scope) return Promise.reject(new Error("يلزم تسجيل الدخول وصلاحية طباعة البوليصة"));
            const normalized = String(orderNumber || "").trim();
            if (!normalized) return Promise.reject(new Error("رقم الطلب مطلوب"));
            let job = jobs.get(normalized);
            if (job?.running || job?.state === "waiting") return Promise.resolve({ pending: true });
            job = { orderNumber: normalized, source, state: "waiting", popup: reserve(), attempts: 0, next: 0 };
            jobs.set(normalized, job);
            try { publish(); } catch (error) { jobs.delete(normalized); close(job); return Promise.reject(new Error("تعذر حفظ طلب الطباعة على هذا الجهاز")); }
            // Background work must not hold the screen's button or loading indicator.
            void check(job);
            return Promise.resolve({ pending: true });
        },
        cancel(orderNumber) {
            const job = jobs.get(orderNumber);
            if (job) close(job);
            jobs.delete(orderNumber);
            publish();
            arm();
        },
    };
}
