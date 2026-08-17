import { createContext, useContext, useEffect, useState, useCallback } from "react";
import api, { formatApiErrorDetail } from "../lib/api";
import {
    AUTH_SESSION_REQUEST_TIMEOUT_MS,
    isAuthBootstrapAbort,
    runBoundedAuthBootstrap,
} from "./authBootstrap";

const AuthContext = createContext(null);

function clearLegacyBrowserAccessToken() {
    // Browser authentication is cookie-only. Older Mezan builds persisted the
    // JWT in localStorage, which made a long-lived bearer token readable by any
    // script running in the page. Remove that legacy copy before the first
    // authenticated request; the server-issued HttpOnly/Secure cookie remains
    // the browser session source of truth.
    try {
        localStorage.removeItem("access_token");
    } catch {
        // Storage can be unavailable in hardened/private browser modes. Cookie
        // authentication still works, so this cleanup is best-effort.
    }
}

// Run the migration at module bootstrap, before AuthProvider's children can
// mount and issue API calls. `api.js` reads localStorage only at request time,
// so this prevents even one legacy bearer-token request during the first render.
clearLegacyBrowserAccessToken();

export function AuthProvider({ children }) {
    const [user, setUser] = useState(null); // null while checking, false if anon
    const [authStatus, setAuthStatus] = useState("checking");
    const [authError, setAuthError] = useState(null);
    const [probeGeneration, setProbeGeneration] = useState(0);
    const loading = authStatus === "checking";

    const loadCurrentUser = useCallback(async ({
        signal,
        timeoutMs = AUTH_SESSION_REQUEST_TIMEOUT_MS,
    } = {}) => {
        const { data } = await api.get("/auth/me", {
            signal,
            timeout: timeoutMs,
            _mezanAuthDeadlineAt: Date.now() + timeoutMs,
        });
        return data;
    }, []);

    const refreshUser = useCallback(async (options = {}) => {
        try {
            const data = await loadCurrentUser(options);
            setUser(data);
            setAuthError(null);
            setAuthStatus("authenticated");
            return data;
        } catch (error) {
            const status = error?.response?.status;
            if (status === 401) {
                setUser(false);
                setAuthError(null);
                setAuthStatus("anonymous");
                return null;
            }

            // A temporary network/origin failure is not proof that the cookie
            // session ended. Keep the last authenticated user internally, but
            // fail closed until a fresh probe succeeds instead of redirecting.
            setAuthError(error);
            setAuthStatus("unavailable");
            throw error;
        }
    }, [loadCurrentUser]);

    useEffect(() => {
        let cancelled = false;
        const controller = new AbortController();

        setAuthError(null);
        setAuthStatus("checking");
        clearLegacyBrowserAccessToken();

        runBoundedAuthBootstrap({
            signal: controller.signal,
            probe: async (requestOptions) => {
                try {
                    const data = await loadCurrentUser(requestOptions);
                    return { user: data, status: "authenticated" };
                } catch (error) {
                    if (error?.response?.status === 401) {
                        return { user: false, status: "anonymous" };
                    }
                    throw error;
                }
            },
        }).then((result) => {
            if (cancelled) return;
            setUser(result.user);
            setAuthError(null);
            setAuthStatus(result.status);
        }).catch((error) => {
            if (cancelled || isAuthBootstrapAbort(error)) return;
            // Fail closed without deleting the cookie or pretending the user
            // is anonymous. Protected/Public routes render a retry surface.
            setAuthError(error);
            setAuthStatus("unavailable");
        });

        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [loadCurrentUser, probeGeneration]);

    const retryAuth = useCallback(() => {
        setAuthError(null);
        setAuthStatus("checking");
        setProbeGeneration((generation) => generation + 1);
    }, []);

    const login = async (email, password, mfaBootstrapCode = "", forceTotp = false) => {
        const payload = { email, password };
        if (mfaBootstrapCode) payload.mfa_bootstrap_code = mfaBootstrapCode;
        if (forceTotp) payload.force_totp = true;
        const { data } = await api.post("/auth/login", payload);
        clearLegacyBrowserAccessToken();

        // Owner/Admin password success is deliberately not a browser session.
        // First enrollment may require an out-of-band bootstrap proof; returning
        // Owner devices may receive a platform-passkey challenge before TOTP.
        if (
            data?.mfa_bootstrap_required
            || data?.mfa_required
            || data?.mfa_setup_required
            || data?.passkey_required
        ) {
            setUser(false);
            return data;
        }

        await refreshUser();
        return data;
    };

    const verifyMfa = async (challengeToken, code, { deferRefresh = false } = {}) => {
        const { data } = await api.post("/auth/mfa/verify", {
            challenge_token: challengeToken,
            code,
        });
        clearLegacyBrowserAccessToken();

        // Login may deliberately defer hydration while recovery codes or the
        // optional trusted-device enrollment screen must remain visible.
        if (deferRefresh) {
            setUser(false);
            setAuthStatus("anonymous");
            return data;
        }

        await refreshUser();
        return data;
    };

    const register = async (name, email, password) => {
        const { data } = await api.post("/auth/register", { name, email, password });
        clearLegacyBrowserAccessToken();
        await refreshUser();
        return data;
    };

    const logout = async () => {
        try {
            await api.post("/auth/logout");
        } catch {
            // Best-effort: server may already consider us logged out. Still clear client state.
        }
        clearLegacyBrowserAccessToken();
        // Hard reload to /login guarantees protected-route logic re-evaluates immediately
        // (and clears any in-flight state on Dashboard/Reports pages).
        window.location.replace("/login");
    };

    return (
        <AuthContext.Provider value={{
            user,
            loading,
            authStatus,
            authError,
            retryAuth,
            login,
            verifyMfa,
            register,
            logout,
            refreshUser,
            formatApiErrorDetail,
        }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useOptionalAuth() {
    return useContext(AuthContext);
}

export function useAuth() {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error("useAuth must be used within AuthProvider");
    return ctx;
}
