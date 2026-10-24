import { useCallback, useEffect, useRef, useState } from "react";
import { getSourceAdditions, reviewSourceAddition, sourceAdditionError } from "../services/sourceAdditionReview";

export function useSourceAdditionReview({ orderNumber, actorKey, enabled, refreshKey = "" }) {
    const scope = enabled && orderNumber && actorKey ? JSON.stringify([orderNumber, actorKey]) : "";
    const currentScope = useRef(scope);
    currentScope.current = scope;
    const epoch = useRef(0);
    const busy = useRef(null);
    const [state, setState] = useState({ scope: "", snapshot: null, loading: false, pending: null, error: "" });

    const reload = useCallback(async () => {
        if (!scope || currentScope.current !== scope) return null;
        const request = ++epoch.current;
        setState((old) => ({ ...(old.scope === scope ? old : {}), scope, loading: true, error: "" }));
        try {
            const snapshot = await getSourceAdditions(orderNumber);
            if (currentScope.current !== scope || epoch.current !== request) return null;
            setState((old) => ({ ...old, scope, snapshot, loading: false, error: "" }));
            return snapshot;
        } catch (error) {
            if (currentScope.current === scope && epoch.current === request) {
                setState((old) => ({ ...old, scope, snapshot: null, loading: false, error: sourceAdditionError(error) }));
            }
            return null;
        }
    }, [scope, orderNumber]);

    useEffect(() => {
        currentScope.current = scope;
        busy.current = null;
        if (scope) void reload();
        else setState({ scope: "", snapshot: null, loading: false, pending: null, error: "" });
        return () => { epoch.current += 1; currentScope.current = ""; };
    }, [scope, reload]);

    // A changed source line set refreshes capabilities once for the whole page.
    const previousRefresh = useRef(refreshKey);
    useEffect(() => {
        if (previousRefresh.current !== refreshKey) {
            previousRefresh.current = refreshKey;
            if (!busy.current) void reload();
        }
    }, [refreshKey, reload]);

    const visible = state.scope === scope ? state : {};
    const approve = async (itemId) => {
        const row = visible.snapshot?.items.find((item) => item.order_item_id === itemId);
        if (!scope || currentScope.current !== scope || busy.current || visible.loading
            || visible.error || !row?.eligible || row.reviewed) return;
        const operation = { scope, itemId };
        busy.current = operation;
        setState((old) => ({ ...old, pending: itemId, error: "" }));
        let failure = null;
        try {
            await reviewSourceAddition(orderNumber, itemId, visible.snapshot);
        } catch (error) {
            failure = error;
        }
        if (currentScope.current !== scope || busy.current !== operation) return;
        // Also read back after uncertain transport: never automatically replay POST.
        const snapshot = await reload();
        if (currentScope.current !== scope || busy.current !== operation) return;
        busy.current = null;
        const confirmed = snapshot?.items.some((item) => item.order_item_id === itemId && item.reviewed);
        setState((old) => ({ ...old, pending: null,
            error: confirmed ? "" : old.error || (failure ? sourceAdditionError(failure)
                : "تم إرسال المراجعة، لكن تعذّر تأكيد حالتها. حدّث البيانات قبل المحاولة مجددًا."),
        }));
    };

    return {
        items: visible.snapshot?.items || [], loading: Boolean(visible.loading),
        pending: visible.pending || null, error: visible.error || "", reload, approve,
        enabled: Boolean(scope),
    };
}
