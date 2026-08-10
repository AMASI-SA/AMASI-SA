import { createContext, useContext, useEffect, useState, useCallback } from "react";
import api, { formatApiErrorDetail } from "../lib/api";

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
    const [loading, setLoading] = useState(true);

    const refreshUser = useCallback(async () => {
        try {
            const { data } = await api.get("/auth/me");
            setUser(data);
            return data;
        } catch {
            setUser(false);
            return null;
        }
    }, []);

    useEffect(() => {
        (async () => {
            clearLegacyBrowserAccessToken();
            await refreshUser();
            setLoading(false);
        })();
    }, [refreshUser]);

    const login = async (email, password) => {
        const { data } = await api.post("/auth/login", { email, password });
        clearLegacyBrowserAccessToken();

        // Owner/Admin password success is deliberately not a browser session.
        // The backend returns a short-lived MFA challenge and withholds auth
        // cookies until the second factor succeeds.
        if (data?.mfa_required || data?.mfa_setup_required) {
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

        // First-time enrollment returns recovery codes that must remain visible
        // before PublicOnly sees an authenticated user and redirects away from
        // /login. The HttpOnly session cookie is already set; Login calls
        // refreshUser only after the merchant confirms the codes are saved.
        if (deferRefresh) {
            setUser(false);
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

export function useAuth() {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error("useAuth must be used within AuthProvider");
    return ctx;
}
