import {render,screen} from "@testing-library/react";
import {expect,it} from "vitest";
import OptimizationCostEvidence,{validateOptimizationCosts} from "./OptimizationCostEvidence";

const cost={before:{format:"RGBA8",width:16,height:8,mip_levels:1,storage_bytes:512,file_bytes:640},
  after:{format:"DXT5",width:16,height:8,mip_levels:5,storage_bytes:208,file_bytes:336},storage_delta_bytes:-304,file_delta_bytes:-304,
  memory_scope:"Tightly packed mip storage, not measured GPU residency."};
it("compares exact formats, mips and costs separately from residency or LOD claims",()=>{
  validateOptimizationCosts([cost]);
  render(<OptimizationCostEvidence texture="paint" cost={cost}/>);
  expect(screen.getByRole("table",{name:"Texture costs paint"})).toHaveTextContent("RGBA8");
  expect(screen.getByRole("table",{name:"Texture costs paint"})).toHaveTextContent("DXT5");
  expect(screen.getByText(/not measured GPU residency/)).toBeInTheDocument();
  expect(screen.getByText(/Geometry LODs and their activation distances are unchanged/)).toBeInTheDocument();
  expect(screen.getByText(/Negative values are reductions/)).toBeInTheDocument();
});
it.each([
  {...cost,storage_delta_bytes:-1},{...cost,file_delta_bytes:NaN},{...cost,memory_scope:""},
  {...cost,after:{...cost.after,width:8}},{...cost,before:{...cost.before,storage_bytes:Infinity}},
  {...cost,after:{...cost.after,mip_levels:0}},{...cost,after:{...cost.after,file_bytes:1}},
])("refuses inconsistent or missing cost evidence %#",invalid=>{
  expect(()=>validateOptimizationCosts([invalid])).toThrow("Invalid optimization cost evidence");
});
