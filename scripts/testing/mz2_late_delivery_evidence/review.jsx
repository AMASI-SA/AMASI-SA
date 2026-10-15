import React, {useState} from "react";
import {createRoot} from "react-dom/client";
import api from "../../../frontend/src/lib/api";
import LateDeliveryEvidence from "../../../frontend/src/components/driver/LateDeliveryEvidence";
import LateDeliveryEvidenceReview from "../../../frontend/src/pages/accounting/LateDeliveryEvidenceReview";
import "./styles.css";
api.defaults.headers.common["x-synthetic-actor"]="driver";
function Fixture(){
 const [mode,setMode]=useState("driver"),[number,setNumber]=useState("1");
 function choose(value){api.defaults.headers.common["x-synthetic-actor"]=value;setMode(value);}
 return <main><p className="fixture-banner">SYNTHETIC LOCAL — real components / HTTP / disposable Mongo. Evidence only; no financial posting. Not production acceptance.</p>
 <nav className="fixture-controls" aria-label="Synthetic fixture navigation"><button onClick={()=>choose("driver")}>الموصل</button><button onClick={()=>choose("reviewer")}>المراجع</button><button onClick={()=>choose("viewer")}>عرض فقط</button>{["1","2"].map(id=><button key={id} onClick={()=>{choose("driver");setNumber(id);}}>طلب {id}</button>)}</nav>
 {mode==="driver"?<LateDeliveryEvidence key={mode+number} assignment={{id:"assignment-"+number,status:"delivered"}}/>:<LateDeliveryEvidenceReview key={mode} accountingPermissions={mode==="reviewer"?["accounting.shipping.view","accounting.shipping.contracts.review"]:["accounting.shipping.view"]}/>}
 </main>;
}
createRoot(document.getElementById("root")).render(<Fixture/>);
