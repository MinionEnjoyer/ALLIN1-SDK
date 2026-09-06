import {fireEvent,render,screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {expect,it,vi} from "vitest";
import OptimizationPixelPreview from "./OptimizationPixelPreview";

it("synchronizes channels, mip and crop and labels nonexistent original mips",async()=>{
  const user=userEvent.setup(),inspect=vi.fn();
  render(<OptimizationPixelPreview texture="diffuse" before={{width:128,height:64,mip_levels:1}} after={{mip_levels:8}} locked={false} inspect={inspect}
    evidence={{mip:2,x:0,y:0,channel:"alpha",width:32,height:16,mip_width:32,mip_height:16,before:null,after:"data:image/png;base64,AAAA",difference:null,scope:"Exact pixels"}}/>);
  await user.click(screen.getByText("Exact pixel comparison · diffuse"));
  expect(screen.getByText("before: mip not present; no synthesized comparison")).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Pixel mip diffuse"),"2");
  await user.selectOptions(screen.getByLabelText("Pixel channels diffuse"),"alpha");
  fireEvent.change(screen.getByLabelText("Pixel X diffuse"),{target:{value:"7"}});
  await user.click(screen.getByRole("button",{name:"Inspect exact pixels diffuse"}));
  expect(inspect).toHaveBeenLastCalledWith({mip:2,x:7,y:0,channel:"alpha"});
  await user.selectOptions(screen.getByLabelText("Pixel zoom diffuse"),"8");
  expect(screen.getByAltText("after exact pixels diffuse")).toHaveAttribute("width","256");
  fireEvent.change(screen.getByLabelText("Pixel X diffuse"),{target:{value:"32"}});
  expect(screen.getByRole("button",{name:"Inspect exact pixels diffuse"})).toBeDisabled();
});

it("does not permit another region request while evidence is being generated",async()=>{
  render(<OptimizationPixelPreview texture="locked" before={{width:16,height:8,mip_levels:1}} after={{mip_levels:1}} locked={true} inspect={vi.fn()}/>);
  await userEvent.click(screen.getByText("Exact pixel comparison · locked"));
  expect(screen.getByRole("button",{name:"Inspect exact pixels locked"})).toBeDisabled();
});
