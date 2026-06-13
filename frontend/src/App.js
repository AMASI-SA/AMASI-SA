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
import ShippingLedgerStub from "./pages/ShippingLedgerStub";
import ShippingTransfers from "./pages/ShippingTransfers";
import ShippingCompanySettings from "./pages/ShippingCompanySettings";
import MakeWebhook from "./pages/MakeWebhook";
import OperatingExpenses from "./pages/OperatingExpenses";
import OperationalReports from "./pages/OperationalReports";
import AdsReport from "./pages/AdsReport";
import SnapchatAccounts from "./pages/SnapchatAccounts";
import ProductCosts from "./pages/ProductCosts";
import ProductPreparation from "./pages/ProductPreparation";
import ImageCatalog from "./pages/ImageCatalog";
import SallaIntegration from "./pages/SallaIntegration";
import SallaSourceComparison from "./pages/SallaSourceComparison";
import PaymentSettlements from "./pages/PaymentSettlements";
import Profile from "./pages/Profile";
import TeamManagement from "./pages/TeamManagement";
import Settlements from "./pages/Settlements";
import Accounts from "./pages/Accounts";
import AccountDetails from "./pages/AccountDetails";
import OrdersDiagnostics from "./pages/OrdersDiagnostics";
import Orders from "./pages/Orders";
import ImportJobs from "./pages/ImportJobs";
import Transfers from "./pages/Transfers";
import Reconciliation from "./pages/Reconciliation";
import ReconciliationDetail from "./pages/ReconciliationDetail";
import FinancialPosition from "./pages/FinancialPosition";
import FinancialInputHub from "./pages/FinancialInputHub";
import Counterparties from "./pages/Counterparties";
import PurchaseInvoices from "./pages/PurchaseInvoices";
import Advances from "./pages/Advances";
import Receivables from "./pages/Receivables";
import OperationsDashboard from "./pages/OperationsDashboard";
import CustomAppIntegration from "./pages/CustomAppIntegration";
import AdAccounts from "./pages/AdAccounts";
import BnplIntegrations from "./pages/BnplIntegrations";
import BnplDiagnostics from "./pages/BnplDiagnostics";
import RefundAudit from "./pages/RefundAudit";
import BnplSettlements from "./pages/BnplSettlements";
import SallaSettlements from "./pages/SallaSettlements";
import SettlementsOverview from "./pages/SettlementsOverview";
import AlertsPage from "./pages/AlertsPage";
import UnifiedEntryScreen from "./pages/UnifiedEntryScreen";
import MigrationWizard from "./pages/MigrationWizard";
import EmployeesLedger from "./pages/EmployeesLedger";
import SuppliersLedger from "./pages/SuppliersLedger";
import ExternalsLedger from "./pages/ExternalsLedger";
import CouriersLedger from "./pages/CouriersLedger";
import FinancialPositionLedger from "./pages/FinancialPositionLedger";
import ApiPermissionsDiagnostic from "./pages/ApiPermissionsDiagnostic";
import AccountingCutoffs from "./pages/AccountingCutoffs";
import BnplBalancesDiagnostic from "./pages/BnplBalancesDiagnostic";
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
            <Route path="/operational-reports" element={<ProtectedRoute><Layout><OperationalReports /></Layout></ProtectedRoute>} />
            <Route path="/reports" element={<ProtectedRoute><Layout><Reports /></Layout></ProtectedRoute>} />
            <Route path="/reports/ads" element={<ProtectedRoute><Layout><AdsReport /></Layout></ProtectedRoute>} />
            <Route path="/snapchat-accounts" element={<ProtectedRoute><Layout><SnapchatAccounts /></Layout></ProtectedRoute>} />
            <Route path="/product-costs" element={<ProtectedRoute><Layout><ProductCosts /></Layout></ProtectedRoute>} />
            <Route path="/product-preparation" element={<ProtectedRoute><Layout><ProductPreparation /></Layout></ProtectedRoute>} />
            <Route path="/image-catalog" element={<ProtectedRoute><Layout><ImageCatalog /></Layout></ProtectedRoute>} />
            <Route path="/settings/salla" element={<ProtectedRoute><Layout><SallaIntegration /></Layout></ProtectedRoute>} />
            <Route path="/salla-sources" element={<ProtectedRoute><Layout><SallaSourceComparison /></Layout></ProtectedRoute>} />
            <Route path="/payment-settlements" element={<ProtectedRoute><Layout><PaymentSettlements /></Layout></ProtectedRoute>} />
            <Route path="/shipping-accounts" element={<ProtectedRoute><Layout><ShippingAccounts /></Layout></ProtectedRoute>} />
            {/* Iter-144 stubs — sidebar shows these in the new 'شركات الشحن' section. */}
            <Route path="/shipping/ledger" element={<ProtectedRoute><Layout><ShippingLedgerStub /></Layout></ProtectedRoute>} />
            <Route path="/shipping/transfers" element={<ProtectedRoute><Layout><ShippingTransfers /></Layout></ProtectedRoute>} />
            <Route path="/shipping/cod-settlements" element={<ProtectedRoute><Layout><ShippingLedgerStub /></Layout></ProtectedRoute>} />
            <Route path="/shipping/settings" element={<ProtectedRoute><Layout><ShippingCompanySettings /></Layout></ProtectedRoute>} />
            <Route path="/make-webhook" element={<ProtectedRoute><Layout><MakeWebhook /></Layout></ProtectedRoute>} />
            <Route path="/profile" element={<ProtectedRoute><Layout><Profile /></Layout></ProtectedRoute>} />
            <Route path="/team" element={<ProtectedRoute><Layout><TeamManagement /></Layout></ProtectedRoute>} />
            <Route path="/settlements" element={<ProtectedRoute><Layout><Settlements /></Layout></ProtectedRoute>} />
            <Route path="/accounts" element={<ProtectedRoute><Layout><Accounts /></Layout></ProtectedRoute>} />
            <Route path="/accounts/:id" element={<ProtectedRoute><Layout><AccountDetails /></Layout></ProtectedRoute>} />
            <Route path="/transfers" element={<ProtectedRoute><Layout><Transfers /></Layout></ProtectedRoute>} />
            <Route path="/reconciliation" element={<ProtectedRoute><Layout><Reconciliation /></Layout></ProtectedRoute>} />
            <Route path="/financial-position" element={<ProtectedRoute><Layout><FinancialPosition /></Layout></ProtectedRoute>} />
            <Route path="/financial-input-hub" element={<ProtectedRoute><Layout><FinancialInputHub /></Layout></ProtectedRoute>} />
            <Route path="/counterparties" element={<ProtectedRoute><Layout><Counterparties /></Layout></ProtectedRoute>} />
            <Route path="/purchase-invoices" element={<ProtectedRoute><Layout><PurchaseInvoices /></Layout></ProtectedRoute>} />
            <Route path="/advances" element={<ProtectedRoute><Layout><Advances /></Layout></ProtectedRoute>} />
            <Route path="/receivables" element={<ProtectedRoute><Layout><Receivables /></Layout></ProtectedRoute>} />
            <Route path="/operations-dashboard" element={<ProtectedRoute><Layout><OperationsDashboard /></Layout></ProtectedRoute>} />
            <Route path="/integrations/custom-app" element={<ProtectedRoute><Layout><CustomAppIntegration /></Layout></ProtectedRoute>} />
            <Route path="/ad-accounts" element={<ProtectedRoute><Layout><AdAccounts /></Layout></ProtectedRoute>} />
            <Route path="/integrations/bnpl" element={<ProtectedRoute><Layout><BnplIntegrations /></Layout></ProtectedRoute>} />
            <Route path="/integrations/bnpl/diagnostics" element={<ProtectedRoute><Layout><BnplDiagnostics /></Layout></ProtectedRoute>} />
            <Route path="/refund-audit" element={<ProtectedRoute><Layout><RefundAudit /></Layout></ProtectedRoute>} />
            <Route path="/bnpl-settlements" element={<ProtectedRoute><Layout><BnplSettlements /></Layout></ProtectedRoute>} />
            <Route path="/salla-settlements" element={<ProtectedRoute><Layout><SallaSettlements /></Layout></ProtectedRoute>} />
            <Route path="/settlements-overview" element={<ProtectedRoute><Layout><SettlementsOverview /></Layout></ProtectedRoute>} />
            <Route path="/alerts" element={<ProtectedRoute><Layout><AlertsPage /></Layout></ProtectedRoute>} />
            <Route path="/new-transaction" element={<ProtectedRoute><Layout><UnifiedEntryScreen /></Layout></ProtectedRoute>} />
            <Route path="/accounting/migration" element={<ProtectedRoute><Layout><MigrationWizard /></Layout></ProtectedRoute>} />
            <Route path="/employees-ledger" element={<ProtectedRoute><Layout><EmployeesLedger /></Layout></ProtectedRoute>} />
            <Route path="/suppliers-ledger" element={<ProtectedRoute><Layout><SuppliersLedger /></Layout></ProtectedRoute>} />
            <Route path="/externals-ledger" element={<ProtectedRoute><Layout><ExternalsLedger /></Layout></ProtectedRoute>} />
            <Route path="/couriers-ledger" element={<ProtectedRoute><Layout><CouriersLedger /></Layout></ProtectedRoute>} />
            <Route path="/financial-position-ledger" element={<ProtectedRoute><Layout><FinancialPositionLedger /></Layout></ProtectedRoute>} />
            <Route path="/diagnostics/api-permissions" element={<ProtectedRoute><Layout><ApiPermissionsDiagnostic /></Layout></ProtectedRoute>} />
            <Route path="/settings/accounting-cutoffs" element={<ProtectedRoute><Layout><AccountingCutoffs /></Layout></ProtectedRoute>} />
            <Route path="/bnpl-balances" element={<ProtectedRoute><Layout><BnplBalancesDiagnostic /></Layout></ProtectedRoute>} />
            <Route path="/reconciliation/:accountId" element={<ProtectedRoute><Layout><ReconciliationDetail /></Layout></ProtectedRoute>} />
            <Route path="/diagnostics" element={<ProtectedRoute><Layout><OrdersDiagnostics /></Layout></ProtectedRoute>} />
            <Route path="/orders" element={<ProtectedRoute><Layout><Orders /></Layout></ProtectedRoute>} />
            <Route path="/import-jobs" element={<ProtectedRoute><Layout><ImportJobs /></Layout></ProtectedRoute>} />
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
