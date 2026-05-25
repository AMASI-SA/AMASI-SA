import { createContext, useContext, useEffect, useState, useCallback } from "react";
import api, { formatApiErrorDetail } from "../lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
    const [user, setUser] = useState(null); // null while checking, false if anon
    const [loading, setLoading] = useState(true);

    const checkSession = useCallback(async () => {
        try {
            const { data } = await api.get("/auth/me");
            setUser(data);
        } catch {
            setUser(false);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        checkSession();
    }, [checkSession]);

    const login = async (email, password) => {
        const { data } = await api.post("/auth/login", { email, password });
        if (data?.access_token) localStorage.setItem("access_token", data.access_token);
        setUser(data);
        return data;
    };

    const register = async (name, email, password) => {
        const { data } = await api.post("/auth/register", { name, email, password });
        if (data?.access_token) localStorage.setItem("access_token", data.access_token);
        setUser(data);
        return data;
    };

    const logout = async () => {
        try { await api.post("/auth/logout"); } catch {}
        localStorage.removeItem("access_token");
        setUser(false);
    };

    return (
        <AuthContext.Provider value={{ user, loading, login, register, logout, formatApiErrorDetail }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error("useAuth must be used within AuthProvider");
    return ctx;
}
