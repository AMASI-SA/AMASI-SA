import React, { act } from "react";
import { createRoot } from "react-dom/client";

import InfiniteScrollSentinel from "./InfiniteScrollSentinel";

class IntersectionObserverMock {
    static instances = [];

    constructor(callback) {
        this.callback = callback;
        IntersectionObserverMock.instances.push(this);
    }

    observe() {}

    disconnect() {}

    intersect() {
        this.callback([{ isIntersecting: true }]);
    }
}

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    IntersectionObserverMock.instances = [];
    global.IntersectionObserver = IntersectionObserverMock;
});

afterEach(() => {
    delete global.IntersectionObserver;
});

test("requests the next page once while the bottom sentinel remains visible", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const onLoadMore = jest.fn();

    await act(async () => {
        root.render(
            <InfiniteScrollSentinel
                hasMore
                loaded={25}
                total={50}
                entityLabel="حملة"
                onLoadMore={onLoadMore}
            />,
        );
    });

    const observer = IntersectionObserverMock.instances[0];
    await act(async () => {
        observer.intersect();
        observer.intersect();
    });

    expect(onLoadMore).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("25 من 50");

    await act(async () => root.unmount());
    container.remove();
});
