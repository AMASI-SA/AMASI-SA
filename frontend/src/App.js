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
import AdvertisingExpensesReport from "./pages/AdvertisingExpensesReport";
import SnapchatAccounts from "./pages/SnapchatAccounts";
import AdsV2Settings from "./pages/AdsV2Settings";
import AdsV2Report from "./pages/AdsV2Report";
import ProductCosts from "./pages/ProductCosts";
import ProductsPage from "./pages/ProductsPage";
import BankTransferReview from "./pages/BankTransferReview";
import SettlementDashboard from "./pages/SettlementDashboard";
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
import BnplSettlementsRegister from "./pages/BnplSettlementsRegister";
import EmployeeOrphanDiagnostic from "./pages/EmployeeOrphanDiagnostic";
import AdDebtDiagnostic from "./pages/AdDebtDiagnostic";
import LedgerHealthDiagnostic from "./pages/LedgerHealthDiagnostic";
import ExpenseCategoryTreePage from "./pages/ExpenseCategoryTreePage";
import SuppliersPage from "./pages/SuppliersPage";
import FinancialMovementNewPage from "./pages/FinancialMovementNewPage";
import FinancialMovementsListPage from "./pages/FinancialMovementsListPage";
import LegacyUsageReportPage from "./pages/LegacyUsageReportPage";
import SuppliersReportPage from "./pages/SuppliersReportPage";
import AdsCurrencySettings from "./pages/AdsCurrencySettings";
import SallaSettlements from "./pages/SallaSettlements";
import SettlementsOverview from "./pages/SettlementsOverview";
import AlertsPage from "./pages/AlertsPage";
import UnifiedEntryScreen from "./pages/UnifiedEntryScreen";
import LedgerTransactionsPage from "./pages/LedgerTransactionsPage";
import MigrationWizard from "./pages/MigrationWizard";
import LegacyRedirect from "./components/LegacyRedirect";
import EmployeesLedger from "./pages/EmployeesLedger";
import EmployeeCorrections from "./pages/EmployeeCorrections";
import SalaryReversals from "./pages/SalaryReversals";
import ExpenseReversals from "./pages/ExpenseReversals";
import SuppliersLedger from "./pages/SuppliersLedger";
import ExternalsLedger from "./pages/ExternalsLedger";
import CouriersLedger from "./pages/CouriersLedger";
// Iter-250b · P1.5.r — Deep-link wrapper for /entity-ledger/:type/:id
import EntityLedgerByIdPage from "./pages/EntityLedgerByIdPage";
// Iter-250b · P1.5.s — Supplier Ledger Detail (read-only, printable)
import SupplierLedgerDetailPage from "./pages/SupplierLedgerDetailPage";
import FinancialPositionLedger from "./pages/FinancialPositionLedger";
import ReconciliationReport from "./pages/ReconciliationReport";
import ApiPermissionsDiagnostic from "./pages/ApiPermissionsDiagnostic";
import AccountingCutoffs from "./pages/AccountingCutoffs";
import BnplBalancesDiagnostic from "./pages/BnplBalancesDiagnostic";
import CODDiagnostic from "./pages/CODDiagnostic";
import PostMigrationAudit from "./pages/PostMigrationAudit";
import CustodyOpenBalances from "./pages/CustodyOpenBalances";
import OperationAccountBindings from "./pages/OperationAccountBindings";
import ShippingLedger from "./pages/ShippingLedger";
import AdAccountForensic from "./pages/AdAccountForensic";
import BalanceDriftDiagnostic from "./pages/BalanceDriftDiagnostic";
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
            <Route path="/reports/advertising-expenses" element={<ProtectedRoute><Layout><AdvertisingExpensesReport /></Layout></ProtectedRoute>} />
            <Route path="/snapchat-accounts" element={<ProtectedRoute><Layout><SnapchatAccounts /></Layout></ProtectedRoute>} />
            <Route path="/ads-v2/settings" element={<ProtectedRoute><Layout><AdsV2Settings /></Layout></ProtectedRoute>} />
            <Route path="/ads-v2/report" element={<ProtectedRoute><Layout><AdsV2Report /></Layout></ProtectedRoute>} />
            <Route path="/product-costs" element={<ProtectedRoute><Layout><ProductCosts /></Layout></ProtectedRoute>} />
            <Route path="/products" element={<ProtectedRoute><Layout><ProductsPage /></Layout></ProtectedRoute>} />
            <Route path="/bank-transfer-review" element={<ProtectedRoute><Layout><BankTransferReview /></Layout></ProtectedRoute>} />
            <Route path="/settlement-engine" element={<ProtectedRoute><Layout><SettlementDashboard /></Layout></ProtectedRoute>} />
            <Route path="/product-preparation" element={<ProtectedRoute><Layout><ProductPreparation /></Layout></ProtectedRoute>} />
            <Route path="/image-catalog" element={<ProtectedRoute><Layout><ImageCatalog /></Layout></ProtectedRoute>} />
            <Route path="/settings/salla" element={<ProtectedRoute><Layout><SallaIntegration /></Layout></ProtectedRoute>} />
            <Route path="/salla-sources" element={<ProtectedRoute><Layout><SallaSourceComparison /></Layout></ProtectedRoute>} />
            <Route path="/payment-settlements" element={<ProtectedRoute><Layout><PaymentSettlements /></Layout></ProtectedRoute>} />
            <Route path="/shipping-accounts" element={<ProtectedRoute><Layout><LegacyRedirect oldLabel="حسابات الشحن الآجلة" replacement="/shipping/orders-ledger" replacementLabel="دفتر الشحن التفصيلي" reason="تم استبدالها بالنسخة المبنية على Ledger مباشرة." /></Layout></ProtectedRoute>} />
            {/* Iter-144 stubs — sidebar shows these in the new 'شركات الشحن' section. */}
            <Route path="/shipping/ledger" element={<ProtectedRoute><Layout><LegacyRedirect oldLabel="أرصدة شركات الشحن (موحَّد)" replacement="/shipping/orders-ledger" replacementLabel="دفتر الشحن التفصيلي" reason="كانت Stub فارغة." /></Layout></ProtectedRoute>} />
            <Route path="/shipping/transfers" element={<ProtectedRoute><Layout><ShippingTransfers /></Layout></ProtectedRoute>} />
            <Route path="/shipping/cod-settlements" element={<ProtectedRoute><Layout><LegacyRedirect oldLabel="تسويات COD" replacement="/shipping/orders-ledger" replacementLabel="دفتر الشحن التفصيلي" reason="نفس الـ Stub المكرّر." /></Layout></ProtectedRoute>} />
            <Route path="/shipping/settings" element={<ProtectedRoute><Layout><ShippingCompanySettings /></Layout></ProtectedRoute>} />
            <Route path="/make-webhook" element={<ProtectedRoute><Layout><MakeWebhook /></Layout></ProtectedRoute>} />
            <Route path="/profile" element={<ProtectedRoute><Layout><Profile /></Layout></ProtectedRoute>} />
            <Route path="/team" element={<ProtectedRoute><Layout><TeamManagement /></Layout></ProtectedRoute>} />
            <Route path="/settlements" element={<ProtectedRoute><Layout><LegacyRedirect oldLabel="تسويات المدفوعات" replacement="/settlements-overview" replacementLabel="جميع التسويات" reason="تم توحيد التسويات في صفحة واحدة مبنية على Ledger." /></Layout></ProtectedRoute>} />
            <Route path="/accounts" element={<ProtectedRoute><Layout><Accounts /></Layout></ProtectedRoute>} />
            <Route path="/accounts/:id" element={<ProtectedRoute><Layout><AccountDetails /></Layout></ProtectedRoute>} />
            <Route path="/transfers" element={<ProtectedRoute><Layout><LegacyRedirect oldLabel="التحويلات بين الحسابات" replacement="/new-transaction" replacementLabel="حركة مالية جديدة (موحّدة)" reason="تم توحيد التحويلات داخل شاشة الإدخال المالي." /></Layout></ProtectedRoute>} />
            <Route path="/reconciliation" element={<ProtectedRoute><Layout><LegacyRedirect oldLabel="المطابقة والتسويات" replacement="/accounting/reconciliation" replacementLabel="تقرير المطابقة (Ledger)" reason="النسخة الجديدة تعتمد Ledger SSOT." /></Layout></ProtectedRoute>} />
            <Route path="/financial-position" element={<ProtectedRoute><Layout><LegacyRedirect oldLabel="المركز المالي" replacement="/financial-position-ledger" replacementLabel="المركز المالي (Ledger)" reason="النسخة الجديدة مبنية على Ledger مباشرة." /></Layout></ProtectedRoute>} />
            <Route path="/financial-input-hub" element={<ProtectedRoute><Layout><LegacyRedirect oldLabel="مركز الإدخال المالي" replacement="/new-transaction" replacementLabel="حركة مالية جديدة (موحّدة)" reason="تم استبداله بشاشة الإدخال الموحّدة." /></Layout></ProtectedRoute>} />
            <Route path="/counterparties" element={<ProtectedRoute><Layout><LegacyRedirect oldLabel="قائمة الأطراف الموحَّدة" replacement="/suppliers-new" replacementLabel="الموردون" reason="تم استبدال شاشة الأطراف القديمة بصفحة الموردين الجديدة المبنية على financial_movements." /></Layout></ProtectedRoute>} />
            <Route path="/purchase-invoices" element={<ProtectedRoute><Layout><PurchaseInvoices /></Layout></ProtectedRoute>} />
            <Route path="/advances" element={<ProtectedRoute><Layout><LegacyRedirect oldLabel="عُهد الموظفين والمندوبين" replacement="/new-transaction" replacementLabel="حركة مالية جديدة (موحّدة)" reason="استخدم نوع الحركة salary_advance من شاشة الإدخال الموحّدة." /></Layout></ProtectedRoute>} />
            <Route path="/receivables" element={<ProtectedRoute><Layout><Receivables /></Layout></ProtectedRoute>} />
            <Route path="/operations-dashboard" element={<ProtectedRoute><Layout><OperationsDashboard /></Layout></ProtectedRoute>} />
            <Route path="/integrations/custom-app" element={<ProtectedRoute><Layout><CustomAppIntegration /></Layout></ProtectedRoute>} />
            <Route path="/ad-accounts" element={<ProtectedRoute><Layout><AdAccounts /></Layout></ProtectedRoute>} />
            <Route path="/integrations/bnpl" element={<ProtectedRoute><Layout><BnplIntegrations /></Layout></ProtectedRoute>} />
            <Route path="/integrations/bnpl/diagnostics" element={<ProtectedRoute><Layout><BnplDiagnostics /></Layout></ProtectedRoute>} />
            <Route path="/refund-audit" element={<ProtectedRoute><Layout><RefundAudit /></Layout></ProtectedRoute>} />
            <Route path="/bnpl-settlements" element={<ProtectedRoute><Layout><BnplSettlements /></Layout></ProtectedRoute>} />
            <Route path="/bnpl-settlements/register" element={<ProtectedRoute><Layout><BnplSettlementsRegister /></Layout></ProtectedRoute>} />
            <Route path="/audit/employee-orphans" element={<ProtectedRoute><Layout><EmployeeOrphanDiagnostic /></Layout></ProtectedRoute>} />
            <Route path="/audit/ad-debt" element={<ProtectedRoute><Layout><AdDebtDiagnostic /></Layout></ProtectedRoute>} />
            <Route path="/audit/ad-account-forensic" element={<ProtectedRoute><Layout><AdAccountForensic /></Layout></ProtectedRoute>} />
            <Route path="/audit/balance-drift" element={<ProtectedRoute><Layout><BalanceDriftDiagnostic /></Layout></ProtectedRoute>} />
            <Route path="/audit/ledger-health" element={<ProtectedRoute><Layout><LedgerHealthDiagnostic /></Layout></ProtectedRoute>} />
            <Route path="/expense-categories-tree" element={<ProtectedRoute><Layout><ExpenseCategoryTreePage /></Layout></ProtectedRoute>} />
            <Route path="/suppliers-new" element={<ProtectedRoute><Layout><SuppliersPage /></Layout></ProtectedRoute>} />
            <Route path="/financial-movement/new" element={<ProtectedRoute><Layout><FinancialMovementNewPage /></Layout></ProtectedRoute>} />
            <Route path="/financial-movements" element={<ProtectedRoute><Layout><FinancialMovementsListPage /></Layout></ProtectedRoute>} />
            <Route path="/legacy-usage-report" element={<ProtectedRoute><Layout><LegacyUsageReportPage /></Layout></ProtectedRoute>} />
            <Route path="/reports/suppliers" element={<ProtectedRoute><Layout><SuppliersReportPage /></Layout></ProtectedRoute>} />
            <Route path="/settings/ads-currencies" element={<ProtectedRoute><Layout><AdsCurrencySettings /></Layout></ProtectedRoute>} />
            <Route path="/salla-settlements" element={<ProtectedRoute><Layout><SallaSettlements /></Layout></ProtectedRoute>} />
            <Route path="/settlements-overview" element={<ProtectedRoute><Layout><SettlementsOverview /></Layout></ProtectedRoute>} />
            <Route path="/alerts" element={<ProtectedRoute><Layout><AlertsPage /></Layout></ProtectedRoute>} />
            <Route path="/new-transaction" element={<ProtectedRoute><Layout><UnifiedEntryScreen /></Layout></ProtectedRoute>} />
            <Route path="/transactions" element={<ProtectedRoute><Layout><LedgerTransactionsPage /></Layout></ProtectedRoute>} />
            <Route path="/accounting/migration" element={<ProtectedRoute><Layout><MigrationWizard /></Layout></ProtectedRoute>} />
            <Route path="/employees-ledger" element={<ProtectedRoute><Layout><EmployeesLedger /></Layout></ProtectedRoute>} />
            <Route path="/employee-corrections" element={<ProtectedRoute><Layout><EmployeeCorrections /></Layout></ProtectedRoute>} />
            <Route path="/salary-reversals" element={<ProtectedRoute><Layout><SalaryReversals /></Layout></ProtectedRoute>} />
            <Route path="/expense-reversals" element={<ProtectedRoute><Layout><ExpenseReversals /></Layout></ProtectedRoute>} />
            <Route path="/suppliers-ledger" element={<ProtectedRoute><Navigate to="/suppliers-new?tab=balances" replace /></ProtectedRoute>} />
            {/* Iter-250b · P1.5.s — Supplier Ledger Detail (the primary
                target for "دفتر المورد" deep-links from reports). */}
            <Route path="/suppliers/:id/ledger-detail" element={<ProtectedRoute><Layout><SupplierLedgerDetailPage /></Layout></ProtectedRoute>} />
            {/* Iter-250b · P1.5.r — Deep-link: /entity-ledger/:type/:id
                (supplier|external|external_person|courier).
                Routes backend-generated `ledger_url` correctly. */}
            <Route path="/entity-ledger/:type/:id" element={<ProtectedRoute><Layout><EntityLedgerByIdPage /></Layout></ProtectedRoute>} />
            <Route path="/externals-ledger" element={<ProtectedRoute><Layout><ExternalsLedger /></Layout></ProtectedRoute>} />
            <Route path="/couriers-ledger" element={<ProtectedRoute><Layout><CouriersLedger /></Layout></ProtectedRoute>} />
            <Route path="/financial-position-ledger" element={<ProtectedRoute><Layout><FinancialPositionLedger /></Layout></ProtectedRoute>} />
            <Route path="/accounting/reconciliation" element={<ProtectedRoute><Layout><ReconciliationReport /></Layout></ProtectedRoute>} />
            <Route path="/diagnostics/api-permissions" element={<ProtectedRoute><Layout><ApiPermissionsDiagnostic /></Layout></ProtectedRoute>} />
            <Route path="/settings/accounting-cutoffs" element={<ProtectedRoute><Layout><AccountingCutoffs /></Layout></ProtectedRoute>} />
            <Route path="/bnpl-balances" element={<ProtectedRoute><Layout><BnplBalancesDiagnostic /></Layout></ProtectedRoute>} />
            <Route path="/diagnostics/cod-source" element={<ProtectedRoute><Layout><CODDiagnostic /></Layout></ProtectedRoute>} />
            <Route path="/audit/post-migration" element={<ProtectedRoute><Layout><PostMigrationAudit /></Layout></ProtectedRoute>} />
            <Route path="/employees/custody-balances" element={<ProtectedRoute><Layout><CustodyOpenBalances /></Layout></ProtectedRoute>} />
            <Route path="/settings/operation-account-bindings" element={<ProtectedRoute><Layout><OperationAccountBindings /></Layout></ProtectedRoute>} />
            <Route path="/shipping/orders-ledger" element={<ProtectedRoute><Layout><ShippingLedger /></Layout></ProtectedRoute>} />
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
