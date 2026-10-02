import React from "react";
import {createRoot} from "react-dom/client";
import DriverPanel from "../../../frontend/src/pages/accounting/h2/DriverPanel";
import "../../../frontend/src/pages/accounting/h2/h2.css";
import "./styles.css";

createRoot(document.getElementById("root")).render(<main className="h2-desk">
  <p dir="ltr" style={{background:"#fff3cd",padding:16}}>SYNTHETIC LOCAL C2 — real HTTP + native writers + disposable Mongo. Read-only browser. Not Smoke B / full UAT.</p>
  <div className="h2-inline-panel"><DriverPanel/></div>
</main>);
