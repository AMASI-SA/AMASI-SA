import React, {useEffect, useState} from "react";
import {createRoot} from "react-dom/client";
import {Toaster} from "sonner";
import {DeliveryPaymentModal} from "../../../frontend/src/pages/AmasiDeliveryApp";
import DriverPhysicalCash from "../../../frontend/src/components/driver/DriverPhysicalCash";
import DriverCashReconciliation from "../../../frontend/src/pages/accounting/h2/DriverCashReconciliation";
import "../../../frontend/src/pages/accounting/h2/h2.css";
import "./styles.css";

function Fixture() {
  const [proof,setProof]=useState(null), [selected,setSelected]=useState(null), [busy,setBusy]=useState(false);
  const [revision,setRevision]=useState(0), [mode,setMode]=useState("driver"), [saved,setSaved]=useState("");
  useEffect(()=>{fetch("/__test/proof").then(r=>r.json()).then(setProof);},[]);
  return <main>
    <p dir="ltr" className="fixture-banner">SYNTHETIC LOCAL C3 — actual product components + HTTP + isolated Mongo. Synthetic auth and Salla transport. Financial writes paused. Not Smoke B / full UAT.</p>
    <nav className="fixture-controls" aria-label="Synthetic fixture navigation">
      <button onClick={()=>setMode("driver")}>إقرارات الموصل</button>
      <button onClick={()=>setMode("accountant")}>مطابقة المحاسب</button>
      {proof?.deliveries.map(row=><button key={row.id} onClick={()=>setSelected(row)}>تسليم {row.order_number}</button>)}
    </nav>
    {saved && <p role="status">تم حفظ تسليم {saved}</p>}
    {mode==="driver" ? <DriverPhysicalCash refreshKey={revision}/> : <div className="h2-desk h2-inline-panel"><DriverCashReconciliation driverId="driver-f"/></div>}
    {selected && <DeliveryPaymentModal assignment={selected} banks={[]} busy={busy} setBusy={setBusy}
      onClose={()=>setSelected(null)} onSaved={async result=>{setSaved(result.order_number);setSelected(null);setRevision(v=>v+1);}}/>}
    <Toaster/>
  </main>;
}
createRoot(document.getElementById("root")).render(<Fixture/>);
