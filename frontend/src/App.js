import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import ProtectedRoute from "./components/ProtectedRoute";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Dashboard from "./pages/Dashboard";
import UploadExcel from "./pages/UploadExcel";
import AnalysisResult from "./pages/AnalysisResult";
import Settings from "./pages/Settings";
import DailyCosts from "./pages/DailyCosts";
import History from "./pages/History";
import Reports from "./pages/Reports";
import ShippingAccounts from "./pages/ShippingAccounts";
import MakeWebhook from "./pages/MakeWebhook";
import OperatingExpenses from "./pages/OperatingExpenses";
import AdsReport from "./pages/AdsReport";
import SnapchatAccounts from "./pages/SnapchatAccounts";
import ProductCosts from "./pages/ProductCosts";
import ProductPreparation from "./pages/ProductPreparation";
import ImageCatalog from "./pages/ImageCatalog";
import SallaIntegration from "./pages/SallaIntegration";
import Profile from "./pages/Profile";
import TeamManagement from "./pages/TeamManagement";
import Settlements from "./pages/Settlements";
import Accounts from "./pages/Accounts";
import AccountDetails from "./pages/AccountDetails";
import OrdersDiagnostics from "./pages/OrdersDiagnostics";
import { Toaster } from "./components/ui/sonner";

function PublicOnly({ children }) {
    const { user, loading } = useAuth();
    if (loading) return null;
    if (user) return <Navigate to="/" replace />;
    return children;
}

function AppRoutes() {
    return (
        <Routes>
            <Route path="/login" element={<PublicOnly><Login /></PublicOnly>} />
            <Route path="/register" element={<PublicOnly><Register /></PublicOnly>} />

            <Route path="/" element={<ProtectedRoute><Layout><Dashboard /></Layout></ProtectedRoute>} />
            <Route path="/upload" element={<ProtectedRoute><Layout><UploadExcel /></Layout></ProtectedRoute>} />
            <Route path="/analyses/:id" element={<ProtectedRoute><Layout><AnalysisResult /></Layout></ProtectedRoute>} />
            <Route path="/history" element={<ProtectedRoute><Layout><History /></Layout></ProtectedRoute>} />
            <Route path="/daily-costs" element={<ProtectedRoute><Layout><DailyCosts /></Layout></ProtectedRoute>} />
            <Route path="/operating-expenses" element={<ProtectedRoute><Layout><OperatingExpenses /></Layout></ProtectedRoute>} />
            <Route path="/reports" element={<ProtectedRoute><Layout><Reports /></Layout></ProtectedRoute>} />
            <Route path="/reports/ads" element={<ProtectedRoute><Layout><AdsReport /></Layout></ProtectedRoute>} />
            <Route path="/snapchat-accounts" element={<ProtectedRoute><Layout><SnapchatAccounts /></Layout></ProtectedRoute>} />
            <Route path="/product-costs" element={<ProtectedRoute><Layout><ProductCosts /></Layout></ProtectedRoute>} />
            <Route path="/product-preparation" element={<ProtectedRoute><Layout><ProductPreparation /></Layout></ProtectedRoute>} />
            <Route path="/image-catalog" element={<ProtectedRoute><Layout><ImageCatalog /></Layout></ProtectedRoute>} />
            <Route path="/settings/salla" element={<ProtectedRoute><Layout><SallaIntegration /></Layout></ProtectedRoute>} />
            <Route path="/shipping-accounts" element={<ProtectedRoute><Layout><ShippingAccounts /></Layout></ProtectedRoute>} />
            <Route path="/make-webhook" element={<ProtectedRoute><Layout><MakeWebhook /></Layout></ProtectedRoute>} />
            <Route path="/profile" element={<ProtectedRoute><Layout><Profile /></Layout></ProtectedRoute>} />
            <Route path="/team" element={<ProtectedRoute><Layout><TeamManagement /></Layout></ProtectedRoute>} />
            <Route path="/settlements" element={<ProtectedRoute><Layout><Settlements /></Layout></ProtectedRoute>} />
            <Route path="/accounts" element={<ProtectedRoute><Layout><Accounts /></Layout></ProtectedRoute>} />
            <Route path="/accounts/:id" element={<ProtectedRoute><Layout><AccountDetails /></Layout></ProtectedRoute>} />
            <Route path="/diagnostics" element={<ProtectedRoute><Layout><OrdersDiagnostics /></Layout></ProtectedRoute>} />
            <Route path="/settings" element={<ProtectedRoute><Layout><Settings /></Layout></ProtectedRoute>} />

            <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
    );
}

export default function App() {
    return (
        <BrowserRouter>
            <AuthProvider>
                <AppRoutes />
                <Toaster richColors position="top-center" />
            </AuthProvider>
        </BrowserRouter>
    );
}
