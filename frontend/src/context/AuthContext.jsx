import { createContext, useContext, useEffect, useState, useCallback } from "react";
import api, { formatApiErrorDetail } from "../lib/api";

const AuthContext = createContext(null);

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
            await refreshUser();
            setLoading(false);
        })();
    }, [refreshUser]);

    const login = async (email, password) => {
        const { data } = await api.post("/auth/login", { email, password });
        if (data?.access_token) localStorage.setItem("access_token", data.access_token);
        // Fetch the FULL /auth/me payload (includes permissions + is_owner + has_security_question)
        // so RBAC-aware navigation works immediately after login.
        await refreshUser();
        return data;
    };

    const register = async (name, email, password) => {
        const { data } = await api.post("/auth/register", { name, email, password });
        if (data?.access_token) localStorage.setItem("access_token", data.access_token);
        await refreshUser();
        return data;
    };

    const logout = async () => {
        try {
            await api.post("/auth/logout");
        } catch {
            // Best-effort: server may already consider us logged out. Still clear client state.
        }
        localStorage.removeItem("access_token");
        // Hard reload to /login guarantees protected-route logic re-evaluates immediately
        // (and clears any in-flight state on Dashboard/Reports pages).
        window.location.replace("/login");
    };

    return (
        <AuthContext.Provider value={{ user, loading, login, register, logout, refreshUser, formatApiErrorDetail }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error("useAuth must be used within AuthProvider");
    return ctx;
}
