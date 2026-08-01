import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import "@/reviewMezanImageEnhancer";
import "@/reviewCustomerHistoryFast";
import "@/reviewExportControlsEnhancer";
import "@/reviewCompactActionLabels";
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
