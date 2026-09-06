export type CostSnapshot = {format:string;width:number;height:number;mip_levels:number;storage_bytes:number;file_bytes:number};
export type OptimizationCost = {before:CostSnapshot;after:CostSnapshot;storage_delta_bytes:number;file_delta_bytes:number;memory_scope:string};

export function validateOptimizationCosts(value:unknown): asserts value is OptimizationCost[] {
  const snapshot=(item:CostSnapshot)=>item&&typeof item.format==="string"&&item.format.length>0&&item.format.length<=80
    &&[item.width,item.height,item.mip_levels,item.storage_bytes,item.file_bytes].every(n=>Number.isSafeInteger(n)&&n>0)
    &&item.mip_levels<=32&&item.file_bytes>=item.storage_bytes;
  if(!Array.isArray(value)||value.length>8||value.some(item=>!item||!snapshot(item.before)||!snapshot(item.after)
    ||item.before.width!==item.after.width||item.before.height!==item.after.height
    ||!Number.isSafeInteger(item.storage_delta_bytes)||!Number.isSafeInteger(item.file_delta_bytes)
    ||item.storage_delta_bytes!==item.after.storage_bytes-item.before.storage_bytes
    ||item.file_delta_bytes!==item.after.file_bytes-item.before.file_bytes
    ||typeof item.memory_scope!=="string"||!item.memory_scope||item.memory_scope.length>2000))
    throw new Error("Invalid optimization cost evidence; inspect again before export.");
}

export default function OptimizationCostEvidence({texture,cost}:{texture:string;cost:OptimizationCost}) {
  return <details className="asset-validation"><summary>Format, mip and cost comparison · {texture}</summary><div className="asset-validation-body">
    <table aria-label={`Texture costs ${texture}`}><thead><tr><th>Version</th><th>Format</th><th>Dimensions</th><th>Mips</th><th>Packed mip bytes</th><th>DDS file bytes</th></tr></thead>
      <tbody>{(["before","after"] as const).map(key=>{const row=cost[key];return <tr key={key}><th>{key==="before"?"Original":"Candidate"}</th><td>{row.format}</td><td>{row.width} × {row.height}</td><td>{row.mip_levels}</td><td>{row.storage_bytes.toLocaleString()}</td><td>{row.file_bytes.toLocaleString()}</td></tr>;})}</tbody></table>
    <p>Packed mip delta: {cost.storage_delta_bytes.toLocaleString()} bytes. DDS file delta: {cost.file_delta_bytes.toLocaleString()} bytes. Negative values are reductions.</p>
    <p>{cost.memory_scope}</p>
    <p>Geometry LODs and their activation distances are unchanged. No model-memory or streaming savings are inferred from archive compression. All stored texture mips are counted, not a predicted resident subset.</p>
  </div></details>;
}
