import {readFileSync} from "node:fs";
import {render,screen} from "@testing-library/react";
import {expect,it} from "vitest";
import AssetValidationReport from "./AssetValidationReport";

it.runIf(Boolean(process.env.ALLIN1_RETAIL_ASSET_REPORT))("renders actual temporary retail report without claiming runtime proof",()=>{
  const report=JSON.parse(readFileSync(process.env.ALLIN1_RETAIL_ASSET_REPORT!,"utf8"));
  render(<AssetValidationReport report={report}/>);
  expect(screen.queryByText("Asset validation evidence is invalid.")).not.toBeInTheDocument();
  expect(screen.getByText(`Asset validation report · static ${report.static_status}`)).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("In-game behavior has not been tested");
  expect(report.runtime_status).toBe("not_tested");
  expect(report.shared_rigs.length).toBeGreaterThan(0);
  expect(screen.getByText(`Selected shared-rig evidence · ${report.shared_rigs.length}`)).toBeInTheDocument();
});
