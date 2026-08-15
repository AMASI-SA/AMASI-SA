import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function ProtectedRoute({ children }) {
    const { user, loading } = useAuth();
    const location = useLocation();

    if (loading) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-background" data-testid="auth-loading">
                <div className="text-brand text-lg font-medium">جاري التحقق…</div>
            </div>
        );
    }
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
