import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export function userHasPermission(user, permission) {
    if (!user || !permission) return false;
    return user.is_owner === true
        || (Array.isArray(user.permissions) && user.permissions.includes(permission));
}

export default function PermissionRoute({ permission, children }) {
    const { user, loading } = useAuth();
    const location = useLocation();

    if (loading) {
        return (
            <div
                className="flex min-h-[50vh] items-center justify-center"
                data-testid="permission-route-loading"
            >
                <div className="font-bold text-brand">جاري التحقق من الصلاحية…</div>
            </div>
        );
    }

    if (!user) {
        return <Navigate to="/login" state={{ from: location }} replace />;
    }

    if (!userHasPermission(user, permission)) {
        return (
            <div
                className="mx-auto mt-12 max-w-2xl rounded-2xl border border-amber-200 bg-amber-50 p-8 text-center"
                data-testid="permission-route-denied"
            >
                <div className="mb-3 text-4xl">🔒</div>
                <h1 className="text-xl font-extrabold text-amber-950">لا تملك صلاحية فتح هذه الصفحة</h1>
                <p className="mt-2 text-sm text-amber-800">
                    اطلب من مدير ميزان منحك صلاحية خدمة العملاء المطلوبة.
                </p>
            </div>
        );
    }

    return children;
}
