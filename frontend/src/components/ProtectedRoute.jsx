import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import AuthLoadingScreen from "./AuthLoadingScreen";
import AuthRecoveryScreen from "./AuthRecoveryScreen";

export default function ProtectedRoute({ children }) {
    const { user, loading, authStatus, retryAuth } = useAuth();
    const location = useLocation();

    if (authStatus === "unavailable") {
        return <AuthRecoveryScreen onRetry={retryAuth} />;
    }
    if (loading) return <AuthLoadingScreen />;
    if (!user) {
        return <Navigate to="/login" state={{ from: location }} replace />;
    }
    if (user.role === "meta_reviewer") {
        const allowed = [
            "/integrations-v2",
            "/integrations-v2/instagram",
            "/customer-intelligence",
            "/ads-manager",
        ];
        if (!allowed.includes(location.pathname)) {
            return <Navigate to="/integrations-v2?provider=meta_ads" replace />;
        }
    }
    return children;
}
