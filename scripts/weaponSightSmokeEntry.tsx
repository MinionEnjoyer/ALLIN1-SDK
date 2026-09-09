// Local read-only visual harness. Input artifacts are supplied by the smoke CLI,
// never bundled retail/example assets. This is not the installed SDK process.
import React, {useState} from "react";
import {createRoot} from "react-dom/client";
import {SightEditor} from "../desktop/src/WeaponSightBench";
import "../desktop/src/SliderField.css";
const records=await (await fetch("/cases.json")).json();
function Harness(){
  const [index,setIndex]=useState(0);
  return <><h1>Offline sight bench · local model smoke test</h1><p>No game files are changed. Untextured geometry and model-space camera only.</p>
    <label>Test weapon<select aria-label="Test weapon" value={index} onChange={e=>setIndex(Number(e.target.value))}>{records.map((r:any,i:number)=><option key={i} value={i}>{r.case ?? r.snapshot.selected_weapon} · {r.model.attachment?.component ?? "Body only"}</option>)}</select></label>
    <div className="weapon-sight"><Case key={index} record={records[index]}/></div></>;
}
function Case({record}:{record:any}){
  const [draft,setDraft]=useState(record.snapshot.values.values);
  return <SightEditor model={record.model} animation={record.animation??null} snapshot={record.snapshot} draft={draft} locked={false}
    onChange={changes=>setDraft({...draft,...changes})} onReview={()=>alert("Smoke harness: no metadata save. Use the SDK workbench for review/apply.")}/>;
}
createRoot(document.getElementById("root")!).render(<Harness/>);
