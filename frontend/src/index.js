import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import "@/reviewMezanImageEnhancer";
import "@/reviewMezanImageGlobalDelete";
import "@/reviewCustomerHistoryFast";
import "@/reviewExportControlsEnhancer";
import "@/reviewCompactActionLabels";
import "@/reviewInternalPreparationRouteEnhancer";
import App from "@/App";

const root = ReactDOM.createRoot(document.getElementById("root"));

root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
