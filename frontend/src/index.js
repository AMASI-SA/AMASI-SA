import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import "@/marketingAdsManagerWorkspace.css";
import "@/marketingCampaignResultSource.css";
import "@/marketingAdsManagerWorkspaceEnhancer";
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
import "@/reviewAutoAdvance";
import "@/reviewManualNavigation";
import "@/reviewCustomerWaiting";
import "@/reviewPreparationFileMetadataEnhancer";
import "@/reviewPreparationFileScheduleSync";
import "@/reviewedProductSortEnhancer";
import "@/reviewedProductSortThumbnailEnhancer";
import "@/reviewHideReviewedSecondaryTab";
import App from "@/App";
import PublicLegalApp, { isPublicLegalPath } from "@/PublicLegalApp";
import { applyPublicLegalNoIndex } from "@/publicLegalNoIndex";

applyPublicLegalNoIndex(window.location.pathname);

const root = ReactDOM.createRoot(document.getElementById("root"));
const RootComponent = isPublicLegalPath(window.location.pathname)
  ? PublicLegalApp
  : App;

root.render(
  <React.StrictMode>
    <RootComponent />
  </React.StrictMode>,
);
