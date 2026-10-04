import { useCallback, useEffect, useRef, useState } from "react";
import api from "../lib/api";

const EMPTY_ITEMS = [];

export function dashboardProductQuery(filters = {}) {
    const query = new URLSearchParams();
    if (filters.from) query.set("from_date", filters.from);
    if (filters.to) query.set("to_date", filters.to);
    for (const key of ["payment_methods", "shipping_companies"]) {
        const value = Array.isArray(filters[key]) ? filters[key].join(",") : filters[key];
        if (value) query.set(key, value);
    }
    return query.toString();
}

// Retain one detail page, never all products visited in the current period.
export function useDashboardProductPage({ filters, initialPage, kind = "products", enabled = true, client = api }) {
    const queryString = dashboardProductQuery(filters);
    const scope = `${kind}?${queryString}`;
    const latest = useRef(null);
    latest.current = { scope, initialPage, enabled };
    const sessionRef = useRef(null);
    const [state, setState] = useState(null);
    const initial = {
        items: initialPage?.product_rows || EMPTY_ITEMS,
        pagination: initialPage?.product_pagination || {},
        product_profit_summary: initialPage?.product_profit_summary || {},
        pageNumber: 1, canPrevious: false, loading: false, error: "",
    };
    useEffect(() => {
        const session = { active: true, request: null, cursor: null, history: [], pagination: initialPage?.product_pagination || {} };
        sessionRef.current = session;
        const current = () => session.active && latest.current.scope === scope
            && latest.current.initialPage === initialPage && latest.current.enabled;
        const initialState = {
            scope, initialPage, items: initialPage?.product_rows || EMPTY_ITEMS,
            pagination: session.pagination, product_profit_summary: initialPage?.product_profit_summary || {},
            pageNumber: 1, canPrevious: false, loading: false, error: "",
        };
        setState(initialState);
        const load = async (cursor, history) => {
            if (!current() || session.request) return false;
            const request = {};
            session.request = request;
            setState(previous => ({ ...previous, loading: true, error: "" }));
            try {
                const query = new URLSearchParams(queryString);
                query.set("kind", kind);
                query.set("limit", "50");
                if (cursor) query.set("cursor", cursor);
                const response = await client.get(`/dashboard-v2/product-details?${query}`);
                if (!current()) return false;
                session.cursor = cursor;
                session.history = history;
                session.failed = null;
                session.pagination = response.data?.pagination || {};
                setState(previous => ({
                    scope, initialPage, items: response.data?.items || EMPTY_ITEMS,
                    pagination: session.pagination,
                    product_profit_summary: response.data?.product_profit_summary || previous.product_profit_summary,
                    pageNumber: history.length + 1, canPrevious: history.length > 0, loading: false, error: "",
                }));
                return true;
            } catch {
                if (current()) {
                    session.failed = { cursor, history };
                    setState(previous => ({ ...previous, loading: false, error: "تعذّر تحميل المنتجات؛ حاول مرة أخرى." }));
                }
                return false;
            } finally {
                if (session.request === request) session.request = null;
            }
        };
        session.next = () => session.pagination.has_more && session.pagination.next_cursor
            ? load(session.pagination.next_cursor, [...session.history, session.cursor]) : Promise.resolve(false);
        session.previous = () => session.history.length
            ? load(session.history[session.history.length - 1], session.history.slice(0, -1)) : Promise.resolve(false);
        session.retry = () => session.failed ? load(session.failed.cursor, session.failed.history) : load(session.cursor, session.history);
        if (enabled && !initialPage) load(null, []);
        return () => { session.active = false; };
    }, [scope, queryString, kind, initialPage, enabled, client]);
    const next = useCallback(() => sessionRef.current?.next() || Promise.resolve(false), []);
    const previous = useCallback(() => sessionRef.current?.previous() || Promise.resolve(false), []);
    const retry = useCallback(() => sessionRef.current?.retry() || Promise.resolve(false), []);
    const displayed = state?.scope === scope && state?.initialPage === initialPage ? state : initial;
    return { ...displayed, next, previous, retry };
}
