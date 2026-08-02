import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import "@/reviewedPreparationSelection.css";
import "@/reviewMezanImageEnhancer";
import "@/reviewMezanImageGlobalDelete";
import "@/reviewCustomerHistoryFast";
import "@/reviewExportControlsEnhancer";
import "@/reviewSpecReplacementEnhancer";
import "@/reviewCompactActionLabels";
import "@/reviewInternalPreparationRouteEnhancer";
import "@/reviewProductEditMode";
import "@/reviewAutoAdvance";
import "@/reviewManualNavigation";
import "@/reviewCustomerWaiting";
import "@/reviewPreparationFileRegistryEnhancer";
import App from "@/App";

const root = ReactDOM.createRoot(document.getElementById("root"));

root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
