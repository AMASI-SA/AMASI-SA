import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import ProtectedRoute from "./components/ProtectedRoute";
import AuthLoadingScreen from "./components/AuthLoadingScreen";
import AuthRecoveryScreen from "./components/AuthRecoveryScreen";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Register from "./pages/Register";
const Dashboard = lazy(() => import("./pages/Dashboard"));
const AdvancedDashboard = lazy(() => import("./pages/AdvancedDashboard"));
const SoldProducts = lazy(() => import("./pages/SoldProducts"));
const ChartDashboard = lazy(() => import("./pages/ChartDashboard"));
const CampaignRecommendations = lazy(() => import("./pages/CampaignRecommendations"));
const UploadExcel = lazy(() => import("./pages/UploadExcel"));
const AnalysisResult = lazy(() => import("./pages/AnalysisResult"));
const Settings = lazy(() => import("./pages/Settings"));
const DailyCosts = lazy(() => import("./pages/DailyCosts"));
const History = lazy(() => import("./pages/History"));
const Reports = lazy(() => import("./pages/Reports"));
const ShippingAccounts = lazy(() => import("./pages/ShippingAccounts"));
const ShippingLedgerStub = lazy(() => import("./pages/ShippingLedgerStub"));
const ShippingTransfers = lazy(() => import("./pages/ShippingTransfers"));
const ShippingCompanySettings = lazy(() => import("./pages/ShippingCompanySettings"));
const MakeWebhook = lazy(() => import("./pages/MakeWebhook"));
const OperatingExpenses = lazy(() => import("./pages/OperatingExpenses"));
const RecurringObligations = lazy(() => import("./pages/RecurringObligations"));
const OperationalReports = lazy(() => import("./pages/OperationalReports"));
const AdsReport = lazy(() => import("./pages/AdsReport"));
const AdvertisingExpensesReport = lazy(() => import("./pages/AdvertisingExpensesReport"));
const AdsV2Settings = lazy(() => import("./pages/AdsV2Settings"));
const AdsV2Report = lazy(() => import("./pages/AdsV2Report"));
const ProductCosts = lazy(() => import("./pages/ProductCosts"));
const ProductsPage = lazy(() => import("./pages/ProductsPage"));
const BankTransferReview = lazy(() => import("./pages/BankTransferReview"));
const SettlementDashboard = lazy(() => import("./pages/SettlementDashboard"));
const ProductPreparation = lazy(() => import("./pages/ProductPreparation"));
const ImageCatalog = lazy(() => import("./pages/ImageCatalog"));
const SallaIntegration = lazy(() => import("./pages/SallaIntegration"));
const QoyodControlCenter = lazy(() => import("./pages/QoyodControlCenter"));
const IntegrationPlaceholder = lazy(() => import("./pages/IntegrationPlaceholder"));
const SallaSourceComparison = lazy(() => import("./pages/SallaSourceComparison"));
const PaymentSettlements = lazy(() => import("./pages/PaymentSettlements"));
const Profile = lazy(() => import("./pages/Profile"));
const TeamManagement = lazy(() => import("./pages/TeamManagement"));
const Settlements = lazy(() => import("./pages/Settlements"));
const Accounts = lazy(() => import("./pages/Accounts"));
const AccountDetails = lazy(() => import("./pages/AccountDetails"));
const OrdersDiagnostics = lazy(() => import("./pages/OrdersDiagnostics"));
const SnapchatPeriodDiagnosticsPage = lazy(() => import("./pages/SnapchatPeriodDiagnosticsPage"));
const Orders = lazy(() => import("./pages/Orders"));
const OrdersV2 = lazy(() => import("./pages/OrdersV2"));
const OrderDetailsV2 = lazy(() => import("./pages/OrderDetailsV2"));
const MezanProducts = lazy(() => import("./pages/MezanProducts"));
const MezanComponents = lazy(() => import("./pages/MezanComponents"));
const AppsIntegrationsControlCenter = lazy(() => import("./pages/AppsIntegrationsControlCenter"));
const SnapchatV2Page = lazy(() => import("./pages/SnapchatV2Page"));
const InstagramCustomerIntelligenceIntegration = lazy(() => import("./pages/InstagramCustomerIntelligenceIntegration"));
const CustomerIntelligenceCenter = lazy(() => import("./pages/CustomerIntelligenceCenter"));
const OrderTrackingNotes = lazy(() => import("./pages/OrderTrackingNotes"));
const AdsManager = lazy(() => import("./pages/AdsManager"));
const MarketingReports = lazy(() => import("./pages/MarketingReports"));
const FulfillmentV2 = lazy(() => import("./pages/FulfillmentV2"));
const InventoryReceivingWorkspace = lazy(() => import("./pages/InventoryReceivingWorkspace"));
const MezanSuppliersV2 = lazy(() => import("./pages/MezanSuppliersV2"));
const EmployeesV2 = lazy(() => import("./pages/EmployeesV2"));
import OwnerOnlyRoute from "./components/OwnerOnlyRoute";
import PermissionRoute from "./components/PermissionRoute";
const ImportJobs = lazy(() => import("./pages/ImportJobs"));
const Transfers = lazy(() => import("./pages/Transfers"));
const Reconciliation = lazy(() => import("./pages/Reconciliation"));
const ReconciliationDetail = lazy(() => import("./pages/ReconciliationDetail"));
const FinancialPosition = lazy(() => import("./pages/FinancialPosition"));
const FinancialInputHub = lazy(() => import("./pages/FinancialInputHub"));
const Counterparties = lazy(() => import("./pages/Counterparties"));
const PurchaseInvoices = lazy(() => import("./pages/PurchaseInvoices"));
const Advances = lazy(() => import("./pages/Advances"));
const Receivables = lazy(() => import("./pages/Receivables"));
const OperationsDashboard = lazy(() => import("./pages/OperationsDashboard"));
const CustomAppIntegration = lazy(() => import("./pages/CustomAppIntegration"));
const AdAccounts = lazy(() => import("./pages/AdAccounts"));
const BnplIntegrations = lazy(() => import("./pages/BnplIntegrations"));
const BnplDiagnostics = lazy(() => import("./pages/BnplDiagnostics"));
const RefundAudit = lazy(() => import("./pages/RefundAudit"));
const BnplSettlements = lazy(() => import("./pages/BnplSettlements"));
const BnplSettlementsRegister = lazy(() => import("./pages/BnplSettlementsRegister"));
const EmployeeOrphanDiagnostic = lazy(() => import("./pages/EmployeeOrphanDiagnostic"));
const AdDebtDiagnostic = lazy(() => import("./pages/AdDebtDiagnostic"));
const LedgerHealthDiagnostic = lazy(() => import("./pages/LedgerHealthDiagnostic"));
const ExpenseCategoryTreePage = lazy(() => import("./pages/ExpenseCategoryTreePage"));
const SuppliersPage = lazy(() => import("./pages/SuppliersPage"));
const FinancialMovementNewPage = lazy(() => import("./pages/FinancialMovementNewPage"));
const FinancialMovementsListPage = lazy(() => import("./pages/FinancialMovementsListPage"));
const LegacyUsageReportPage = lazy(() => import("./pages/LegacyUsageReportPage"));
const SuppliersReportPage = lazy(() => import("./pages/SuppliersReportPage"));
const AdsCostSettingsV2 = lazy(() => import("./pages/AdsCostSettingsV2"));
const CustomerCohortReport = lazy(() => import("./pages/CustomerCohortReport"));
const SallaSettlements = lazy(() => import("./pages/SallaSettlements"));
const SettlementsOverview = lazy(() => import("./pages/SettlementsOverview"));
const AlertsPage = lazy(() => import("./pages/AlertsPage"));
const ProductPriceChangeRequest = lazy(() => import("./pages/ProductPriceChangeRequest"));
const UnifiedEntryScreen = lazy(() => import("./pages/UnifiedEntryScreen"));
const LedgerTransactionsPage = lazy(() => import("./pages/LedgerTransactionsPage"));
const MigrationWizard = lazy(() => import("./pages/MigrationWizard"));
import LegacyRedirect from "./components/LegacyRedirect";
const EmployeesLedger = lazy(() => import("./pages/EmployeesLedger"));
const EmployeeCorrections = lazy(() => import("./pages/EmployeeCorrections"));
const SalaryReversals = lazy(() => import("./pages/SalaryReversals"));
const ExpenseReversals = lazy(() => import("./pages/ExpenseReversals"));
const SuppliersLedger = lazy(() => import("./pages/SuppliersLedger"));
const ExternalsLedger = lazy(() => import("./pages/ExternalsLedger"));
const CouriersLedger = lazy(() => import("./pages/CouriersLedger"));
// Iter-250b · P1.5.r — Deep-link wrapper for /entity-ledger/:type/:id
const EntityLedgerByIdPage = lazy(() => import("./pages/EntityLedgerByIdPage"));
// Iter-250b · P1.5.s — Supplier Ledger Detail (read-only, printable)
const SupplierLedgerDetailPage = lazy(() => import("./pages/SupplierLedgerDetailPage"));
const FinancialPositionLedger = lazy(() => import("./pages/FinancialPositionLedger"));
const ReconciliationReport = lazy(() => import("./pages/ReconciliationReport"));
const ApiPermissionsDiagnostic = lazy(() => import("./pages/ApiPermissionsDiagnostic"));
const AccountingCutoffs = lazy(() => import("./pages/AccountingCutoffs"));
const BnplBalancesDiagnostic = lazy(() => import("./pages/BnplBalancesDiagnostic"));
const CODDiagnostic = lazy(() => import("./pages/CODDiagnostic"));
const PostMigrationAudit = lazy(() => import("./pages/PostMigrationAudit"));
const CustodyOpenBalances = lazy(() => import("./pages/CustodyOpenBalances"));
const OperationAccountBindings = lazy(() => import("./pages/OperationAccountBindings"));
const ShippingLedger = lazy(() => import("./pages/ShippingLedger"));
const AdAccountForensic = lazy(() => import("./pages/AdAccountForensic"));
const BalanceDriftDiagnostic = lazy(() => import("./pages/BalanceDriftDiagnostic"));
const AIControlCenter = lazy(() => import("./pages/AIControlCenter"));
import { Toaster } from "./components/ui/sonner";

const OperationalBalances = lazy(() => import("./pages/operational/OperationalBalances"));
const InventoryWorkspace = lazy(() => import("./pages/InventoryWorkspace"));
const ProviderFeeSetup = lazy(() => import("./pages/operational/ProviderFeeSetup"));
const OperationalFinancialPosition = lazy(() => import("./pages/operational/OperationalFinancialPosition"));
const OperationalCustomerReturns = lazy(() => import("./pages/operational/CustomerReturns"));
const OperationalCustomerExchanges = lazy(() => import("./pages/operational/CustomerExchanges"));

function PublicOnly({ children }) {
    const { user, loading, authStatus, retryAuth } = useAuth();
    if (authStatus === "unavailable") {
        return <AuthRecoveryScreen onRetry={retryAuth} />;
    }
    if (loading) return <AuthLoadingScreen />;
    if (user) return <Navigate to="/dashboard-advanced" replace />;
    return children;
}

function AppRoutes() {
    return (
        <Routes>
            <Route path="/operational-balances" element={<ProtectedRoute><Layout><OperationalBalances /></Layout></ProtectedRoute>} />
            <Route path="/inventory-v2" element={<ProtectedRoute><Layout><InventoryWorkspace /></Layout></ProtectedRoute>} />
            <Route path="/operational-balances/provider-fees" element={<ProtectedRoute><Layout><ProviderFeeSetup /></Layout></ProtectedRoute>} />
            <Route path="/operational-balances/financial-position" element={<ProtectedRoute><Layout><OperationalFinancialPosition /></Layout></ProtectedRoute>} />
            <Route path="/operational-balances/customer-returns" element={<ProtectedRoute><Layout><OperationalCustomerReturns /></Layout></ProtectedRoute>} />
            <Route path="/operational-balances/customer-exchanges" element={<ProtectedRoute><Layout><OperationalCustomerExchanges /></Layout></ProtectedRoute>} />
            <Route path="/login" element={<PublicOnly><Login /></PublicOnly>} />
            <Route path="/register" element={<PublicOnly><Register /></PublicOnly>} />

            <Route path="/" element={<ProtectedRoute><Navigate to="/dashboard-advanced" replace /></ProtectedRoute>} />
            <Route path="/legacy-dashboard" element={<ProtectedRoute><Layout><Dashboard /></Layout></ProtectedRoute>} />
            <Route path="/upload" element={<ProtectedRoute><Layout><UploadExcel /></Layout></ProtectedRoute>} />
            <Route path="/analyses/:id" element={<ProtectedRoute><Layout><AnalysisResult /></Layout></ProtectedRoute>} />
            <Route path="/history" element={<ProtectedRoute><Layout><History /></Layout></ProtectedRoute>} />
            <Route path="/daily-costs" element={<ProtectedRoute><Layout><DailyCosts /></Layout></ProtectedRoute>} />
            <Route path="/operating-expenses" element={<ProtectedRoute><Layout><OperatingExpenses /></Layout></ProtectedRoute>} />
            <Route path="/operational-reports" element={<ProtectedRoute><Layout><OperationalReports /></Layout></ProtectedRoute>} />
            <Route path="/reports" element={<ProtectedRoute><Layout><Reports /></Layout></ProtectedRoute>} />
            <Route path="/reports/ads" element={<ProtectedRoute><Layout><AdsReport /></Layout></ProtectedRoute>} />
            <Route path="/reports/advertising-expenses" element={<ProtectedRoute><Layout><AdvertisingExpensesReport /></Layout></ProtectedRoute>} />
            <Route path="/ads-manager/reports" element={<ProtectedRoute><Layout><MarketingReports /></Layout></ProtectedRoute>} />
            <Route path="/snapchat-accounts" element={<ProtectedRoute><Layout><SnapchatV2Page /></Layout></ProtectedRoute>} />
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
            <Route path="/shipping-settings-v2" element={<ProtectedRoute><Layout><ShippingCompanySettings mezan2 /></Layout></ProtectedRoute>} />
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
            <Route path="/integrations-v2/qoyod" element={<ProtectedRoute><OwnerOnlyRoute><Layout><QoyodControlCenter /></Layout></OwnerOnlyRoute></ProtectedRoute>} />
            {/* Qoyod is automatic-only now. Old bookmarks converge on the V2 control center. */}
            <Route path="/integrations/qoyod/settings" element={<ProtectedRoute><Navigate to="/integrations-v2/qoyod?tab=settings" replace /></ProtectedRoute>} />
            <Route path="/integrations/qoyod/reconciliation" element={<ProtectedRoute><Navigate to="/integrations-v2/qoyod?tab=reconciliation" replace /></ProtectedRoute>} />
            <Route path="/integrations/qoyod/unsent-orders" element={<ProtectedRoute><Navigate to="/integrations-v2/qoyod?tab=exceptions" replace /></ProtectedRoute>} />
            <Route path="/admin/qoyod-manual-send" element={<ProtectedRoute><Navigate to="/integrations-v2/qoyod?tab=exceptions" replace /></ProtectedRoute>} />
            <Route path="/integrations/qoyod/*" element={<ProtectedRoute><Navigate to="/integrations-v2/qoyod" replace /></ProtectedRoute>} />
            <Route path="/integrations/salla/orders" element={<ProtectedRoute><Layout><IntegrationPlaceholder title="مراقبة الطلبات — سلة" subtitle="عرض الطلبات الواردة من سلة وحالتها الحالية في خط الأنابيب." icon="🛒" phase="مرحلة لاحقة" testid="salla-orders-placeholder" related={[{to:"/orders",label:"الطلبات"},{to:"/salla-sources",label:"مقارنة مصادر البيانات"}]} /></Layout></ProtectedRoute>} />
            <Route path="/integrations/salla/events" element={<ProtectedRoute><Layout><IntegrationPlaceholder title="سجل الأحداث — سلة" subtitle="جميع أحداث Webhooks من سلة (طلب جديد، شحن، إلغاء، استرجاع…)." icon="📋" phase="مرحلة لاحقة" testid="salla-events-placeholder" related={[{to:"/make-webhook",label:"ربط Make.com"}]} /></Layout></ProtectedRoute>} />
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
            <Route
                path="/assistant"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><AIControlCenter /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/ai/control-center"
                element={<ProtectedRoute><Navigate to="/assistant" replace /></ProtectedRoute>}
            />
            <Route path="/expense-categories-tree" element={<ProtectedRoute><Layout><ExpenseCategoryTreePage /></Layout></ProtectedRoute>} />
            <Route path="/suppliers-new" element={<ProtectedRoute><Layout><SuppliersPage /></Layout></ProtectedRoute>} />
            <Route path="/financial-movement/new" element={<ProtectedRoute><Layout><FinancialMovementNewPage /></Layout></ProtectedRoute>} />
            <Route path="/financial-movements" element={<ProtectedRoute><Layout><FinancialMovementsListPage /></Layout></ProtectedRoute>} />
            <Route path="/legacy-usage-report" element={<ProtectedRoute><Layout><LegacyUsageReportPage /></Layout></ProtectedRoute>} />
            <Route path="/reports/suppliers" element={<ProtectedRoute><Layout><SuppliersReportPage /></Layout></ProtectedRoute>} />
            <Route path="/settings/ads-currencies" element={<ProtectedRoute><Navigate to="/ads-manager/cost-settings" replace /></ProtectedRoute>} />
            <Route path="/salla-settlements" element={<ProtectedRoute><Layout><SallaSettlements /></Layout></ProtectedRoute>} />
            <Route path="/settlements-overview" element={<ProtectedRoute><Layout><SettlementsOverview /></Layout></ProtectedRoute>} />
            <Route path="/alerts" element={<ProtectedRoute><Layout><AlertsPage /></Layout></ProtectedRoute>} />
            <Route path="/price-change-requests/:requestId" element={<ProtectedRoute><OwnerOnlyRoute><Layout><ProductPriceChangeRequest /></Layout></OwnerOnlyRoute></ProtectedRoute>} />
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
            <Route path="/diagnostics/snapchat-period" element={<ProtectedRoute><Layout><SnapchatPeriodDiagnosticsPage /></Layout></ProtectedRoute>} />
            <Route path="/orders" element={<ProtectedRoute><Layout><Orders /></Layout></ProtectedRoute>} />
            <Route
                path="/dashboard-v2"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><Dashboard sourceMode="mezan_v2" /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/dashboard-advanced"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><AdvancedDashboard /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
      <Route
        path="/dashboard-charts"
        element={
          <ProtectedRoute>
            <OwnerOnlyRoute>
              <Layout>
                <Suspense fallback={null}>
                  <ChartDashboard />
                </Suspense>
              </Layout>
            </OwnerOnlyRoute>
          </ProtectedRoute>
        }
      />
            <Route
                path="/ads-manager/recommendations"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><CampaignRecommendations /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/orders-v2"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><OrdersV2 /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/orders-v2/:orderNumber"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><OrderDetailsV2 /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/products-v2"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><MezanProducts /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route path="/products-v2/sold" element={<ProtectedRoute><OwnerOnlyRoute><Layout><SoldProducts /></Layout></OwnerOnlyRoute></ProtectedRoute>} />
            <Route
                path="/employees-v2"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><EmployeesV2 /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/recurring-obligations"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><RecurringObligations /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/components-v2"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><MezanComponents /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/suppliers-v2"
                element={
                    <ProtectedRoute>
                        <Layout><MezanSuppliersV2 /></Layout>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/integrations-v2"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><AppsIntegrationsControlCenter /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/integrations-v2/instagram"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><InstagramCustomerIntelligenceIntegration /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/customer-intelligence"
                element={
                    <ProtectedRoute>
                        <PermissionRoute permission="customer_intelligence.inbox.read">
                            <Layout><CustomerIntelligenceCenter /></Layout>
                        </PermissionRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/order-tracking-notes"
                element={
                    <ProtectedRoute>
                        <PermissionRoute permission="customer_intelligence.inbox.read">
                            <Layout><OrderTrackingNotes /></Layout>
                        </PermissionRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/ads-manager/cost-settings"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><AdsCostSettingsV2 /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/ads-manager/customer-cohorts"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><CustomerCohortReport /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/ads-manager"
                element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <Layout><AdsManager /></Layout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                }
            />
            <Route
                path="/fulfillment-v2"
                element={<ProtectedRoute><Layout><FulfillmentV2 /></Layout></ProtectedRoute>}
            />
            <Route
                path="/inventory-receiving-v2"
                element={<ProtectedRoute><Layout><InventoryReceivingWorkspace /></Layout></ProtectedRoute>}
            />
            <Route
                path="/order-review"
                element={<ProtectedRoute><Navigate to="/fulfillment-v2?stage=pending_review" replace /></ProtectedRoute>}
            />
            <Route path="/import-jobs" element={<ProtectedRoute><Layout><ImportJobs /></Layout></ProtectedRoute>} />
            <Route path="/settings" element={<ProtectedRoute><Layout><Settings /></Layout></ProtectedRoute>} />

            <Route path="*" element={<Navigate to="/dashboard-advanced" replace />} />
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
