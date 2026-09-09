import { expect, it } from "vitest";
import { triangleNormal } from "./weaponSightRenderer";
it("keeps tiny valid sight triangles while skipping only zero-area faces",()=>{
  expect(triangleNormal([[0,0,0],[1e-6,0,0],[0,1e-6,0]])).toEqual([0,0,1]);
  expect(triangleNormal([[0,0,0],[1,0,0],[2,0,0]])).toBeNull();
  expect(()=>triangleNormal([[0,0,0],[Infinity,0,0],[0,1,0]])).toThrow("Nonfinite");
});
