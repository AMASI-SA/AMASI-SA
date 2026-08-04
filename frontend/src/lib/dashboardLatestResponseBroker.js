function queryParams(config = {}) {
    const params = new URLSearchParams();
    const raw = String(config?.url || "");
    const query = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
    new URLSearchParams(query).forEach((value, key) => params.set(key, value));
    if (config?.params && typeof config.params === "object") {
        Object.entries(config.params).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== "") {
                params.set(key, String(value));
            }
        });
    }
    return params;
}

export function dashboardRequestRangeKey(config = {}) {
    const params = queryParams(config);
    return [
        "from_date",
        "to_date",
        "payment_methods",
        "shipping_companies",
    ].map((key) => `${key}=${params.get(key) || ""}`).join("&");
}

export function createLatestResponseBroker() {
    let sequence = 0;
    let latestId = 0;
    let latestCompleted = null;
    let waiters = [];

    const waitForLatest = () => new Promise((resolve, reject) => {
        waiters.push({ resolve, reject });
    });

    const settleWaiters = (kind, value) => {
        const current = waiters;
        waiters = [];
        current.forEach((waiter) => waiter[kind](value));
    };

    return {
        begin(meta = {}) {
            const token = {
                id: sequence + 1,
                rangeKey: String(meta.rangeKey || ""),
            };
            sequence = token.id;
            latestId = token.id;
            latestCompleted = null;
            return token;
        },

        isLatest(token) {
            return Boolean(token && Number(token.id) === latestId);
        },

        resolve(token, response) {
            if (this.isLatest(token)) {
                latestCompleted = { id: latestId, response };
                settleWaiters("resolve", response);
                return Promise.resolve(response);
            }
            if (latestCompleted?.id === latestId) {
                return Promise.resolve(latestCompleted.response);
            }
            return waitForLatest();
        },

        reject(token, error) {
            if (this.isLatest(token)) {
                settleWaiters("reject", error);
                return Promise.reject(error);
            }
            if (latestCompleted?.id === latestId) {
                return Promise.resolve(latestCompleted.response);
            }
            return waitForLatest();
        },

        snapshot() {
            return {
                latestId,
                latestRangeKey: latestCompleted?.response?.config
                    ? dashboardRequestRangeKey(latestCompleted.response.config)
                    : null,
                waiting: waiters.length,
            };
        },
    };
}
