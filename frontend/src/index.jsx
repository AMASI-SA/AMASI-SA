import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import "@/marketingAdsManagerWorkspace.css";
import "@/marketingAdsManagerReadability.css";
import "@/marketingAdsTableUXEnhancer.css";
import "@/marketingCampaignResultSource.css";
import "@/marketingSnapchatAccountTimezone.css";
import "@/marketingAdsManagerWorkspaceEnhancer";
import "@/marketingCampaignStaleResponseGuard";
import "@/marketingSnapchatAccountTimezoneEnhancer";
import "@/marketingSnapchatManualRangeGuard";
import "@/marketingSnapchatDeliveryStateEnhancer";
import "@/marketingAdsLiveWorkspaceEnhancer";
import "@/marketingAdsTableUXEnhancer";
import "@/marketingCampaignSelectedSourceGuard";
import "@/dashboardVisibleRangeRequestGuard";
import "@/dashboardExecutivePlatformSpendInterceptor";
import "@/reviewedPreparationSelection.css";
import "@/reviewMezanImageEnhancer";
import "@/reviewMezanImageGlobalDelete";
import "@/reviewCustomerHistoryFast";
import "@/reviewExportControlsEnhancer";
import "@/reviewSpecReplacementEnhancer";
import "@/reviewCompactActionLabels";
import "@/reviewInternalPreparationRouteEnhancer";
import "@/reviewProductEditMode";
import "@/reviewImageDialogAndEditControlEnhancer";
import "@/reviewSpecRowLayoutFix";
import "@/reviewSpecEditVisibilitySafety";
import "@/reviewAutoAdvance";
import "@/reviewManualNavigation";
import "@/reviewCustomerWaiting";
import "@/reviewPreparationFileMetadataEnhancer";
import "@/reviewPreparationFileScheduleSync";
import "@/preparationFileFailureSafetyClient";
import "@/reviewedProductSortEnhancer";
import "@/reviewedProductSortThumbnailEnhancer";
import "@/reviewHideReviewedSecondaryTab";
import App from "@/App";
import AppCrashBoundary from "@/components/AppCrashBoundary";
import PublicLegalApp, { isPublicLegalPath } from "@/PublicLegalApp";
import { applyPublicLegalNoIndex } from "@/publicLegalNoIndex";
import { installDomMutationCompatibilityGuard } from "@/domMutationCompatibilityGuard";
import { installSpaRuntimeRecovery } from "@/spaRuntimeRecovery";

applyPublicLegalNoIndex(window.location.pathname);
installDomMutationCompatibilityGuard();

const rootElement = document.getElementById("root");
const root = ReactDOM.createRoot(rootElement);
const RootComponent = isPublicLegalPath(window.location.pathname)
  ? PublicLegalApp
  : App;

root.render(
  <React.StrictMode>
    <AppCrashBoundary>
      <RootComponent />
    </AppCrashBoundary>
  </React.StrictMode>,
);

installSpaRuntimeRecovery();
