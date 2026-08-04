import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import "@/marketingAdsManagerWorkspace.css";
import "@/marketingCampaignResultSource.css";
import "@/marketingSnapchatAccountTimezone.css";
import "@/marketingAdsManagerWorkspaceEnhancer";
import "@/marketingSnapchatAccountTimezoneEnhancer";
import "@/marketingSnapchatDeliveryStateEnhancer";
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
import { installSpaRuntimeRecovery } from "@/spaRuntimeRecovery";

applyPublicLegalNoIndex(window.location.pathname);

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
