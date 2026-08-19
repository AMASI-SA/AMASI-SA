import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import AmasiDeliveryApp from "../pages/AmasiDeliveryApp";
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

    // Purpose-bound courier accounts never render Mezan administrative UI.
    // The standalone delivery workspace is returned directly regardless of the
    // URL the courier tries to open, while backend role checks remain the final
    // authorization boundary.
    if (user.role === "store_driver") {
        return <AmasiDeliveryApp />;
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
