import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import NativeAnimationView from "./NativeAnimationView";
import AnimationModelViewport from "./AnimationModelViewport";
import model from "./nativeAnimationModelFixture.json";
import animation from "./nativeAnimationFixture.json";
import type { AnimationModel } from "./animationPose";

afterEach(()=>vi.restoreAllMocks());
function canvasMock() {
  const draw={fillRect:vi.fn(),beginPath:vi.fn(),closePath:vi.fn(),moveTo:vi.fn(),lineTo:vi.fn(),arc:vi.fn(),fill:vi.fn(),stroke:vi.fn()};
  vi.spyOn(HTMLCanvasElement.prototype,"getContext").mockReturnValue(draw as unknown as CanvasRenderingContext2D);
  return draw;
}
it("renders the bound mesh at timeline time and redraws camera/skeleton controls",async()=>{
  const draw=canvasMock(), user=userEvent.setup();
  render(<NativeAnimationView packet={animation} model={model} active locked={false} draftDirty={false} onSelect={vi.fn()}/>);
  expect(screen.getByRole("img",{name:"Animated model viewport"})).toBeVisible();
  expect(screen.getByLabelText("Model binding coverage")).toHaveTextContent("2 matched channels");
  const start=draw.moveTo.mock.calls.at(0);
  draw.moveTo.mockClear();
  fireEvent.change(screen.getByLabelText("Animation time"),{target:{value:".5"}});
  expect(draw.moveTo.mock.calls[0]).not.toEqual(start);
  fireEvent.change(screen.getByLabelText("Animation camera yaw"),{target:{value:"90"}});
  await user.click(screen.getByLabelText("Wireframe model"));
  await user.click(screen.getByLabelText("Skeleton overlay"));
  expect(draw.stroke).toHaveBeenCalled();
  expect(screen.getByLabelText("Animation timestamp")).toHaveTextContent("0.500");
});
it("requires exact dictionary selection and guards rebinding",async()=>{
  const user=userEvent.setup(), bind=vi.fn();
  const unbound={...model,selected:null,lod:null,lods:[],bones:[],meshes:[],drawables:[{key:"0",name:"First"},{key:"1",name:"Second"}]};
  const view=render(<AnimationModelViewport model={unbound} animation={animation} time={0} locked={false} onBind={bind}/>);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Animation model drawable"),"1");
  expect(bind).toHaveBeenCalledExactlyOnceWith({drawable:"1"});
  view.rerender(<AnimationModelViewport model={unbound} animation={animation} time={0} locked onBind={bind}/>);
  expect(screen.getByLabelText("Animation model drawable")).toBeDisabled();
});
it("reports partial coverage and refuses a wholly unrelated skeleton",()=>{
  canvasMock();
  const partial={...animation,tracks:[...animation.tracks,{...animation.tracks[0],id:"other",bone_tag:1234}]};
  const view=render(<AnimationModelViewport model={model} animation={partial} time={0} locked={false} onBind={vi.fn()}/>);
  expect(screen.getByRole("status")).toHaveTextContent("Partial pose");
  view.rerender(<AnimationModelViewport model={model} animation={{...partial,tracks:partial.tracks.slice(-1)}} time={0} locked={false} onBind={vi.fn()}/>);
  expect(screen.getByRole("alert")).toHaveTextContent("No supported channels match");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
it("reports unavailable canvas rendering without claiming a visible model",()=>{
  vi.spyOn(HTMLCanvasElement.prototype,"getContext").mockReturnValue(null);
  render(<AnimationModelViewport model={model} animation={animation} time={0} locked={false} onBind={vi.fn()}/>);
  expect(screen.getByRole("alert")).toHaveTextContent("Canvas rendering is unavailable");
});

it("collapses the model panel independently and stops hidden pose redraws",async()=>{
  const draw=canvasMock(), user=userEvent.setup();
  const view=render(<AnimationModelViewport model={model} animation={animation} time={0} locked={false} onBind={vi.fn()}/>);
  const toggle=screen.getByRole("button",{name:"Model playback · reconstructed XML bind pose"});
  await user.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded","false");
  draw.fillRect.mockClear();
  view.rerender(<AnimationModelViewport model={model} animation={animation} time={.5} locked={false} onBind={vi.fn()}/>);
  expect(draw.fillRect).not.toHaveBeenCalled();
  await user.click(toggle);
  expect(draw.fillRect).toHaveBeenCalledOnce();
});

it("shows pending shared-skeleton selection without claiming a model pose",async()=>{
  const user=userEvent.setup(), bind=vi.fn();
  const pending:AnimationModel={...model,bones:[],meshes:[],binding_required:"Choose an exact shared skeleton drawable",skeleton_binding:{mode:"external",source:"C:/models/ped.yft.xml",source_sha256:"b".repeat(64),selected:null,drawables:[{key:"0",name:"male"},{key:"1",name:"female"}],scope:"User-selected rig; compatibility not inferred."}};
  const view=render(<AnimationModelViewport model={pending} animation={animation} time={0} locked={false} onBind={bind}/>);
  expect(screen.getByRole("status")).toHaveTextContent("Choose an exact shared skeleton");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Shared skeleton drawable"),"1");
  expect(bind).toHaveBeenCalledExactlyOnceWith({skeleton_drawable:"1"});
  view.rerender(<AnimationModelViewport model={pending} animation={animation} time={0} locked onBind={bind}/>);
  expect(screen.getByLabelText("Shared skeleton drawable")).toBeDisabled();
});

it("root-motion preview is explicit and changes coverage only when enabled",async()=>{
  canvasMock();const user=userEvent.setup();
  const rooted={...animation,tracks:[...animation.tracks,{...animation.tracks[0],id:"root",track:5}]};
  render(<AnimationModelViewport model={model} animation={rooted} time={0} locked={false} onBind={vi.fn()}/>);
  expect(screen.getByLabelText("Apply root motion")).not.toBeChecked();
  expect(screen.getByLabelText("Model binding coverage")).toHaveTextContent("2 matched channels");
  await user.click(screen.getByLabelText("Apply root motion"));
  expect(screen.getByLabelText("Model binding coverage")).toHaveTextContent("3 matched channels");
});

it("keeps a lower LOD selectable when the first LOD exceeds playback bounds",async()=>{
  const user=userEvent.setup(), bind=vi.fn();
  render(<AnimationModelViewport model={{...model,bones:[],meshes:[],vertex_count:0,triangle_count:0,lods:["High","Medium"],view_unavailable:"Choose a lower LOD"}} animation={animation} time={0} locked={false} onBind={bind}/>);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("lower LOD");
  await user.selectOptions(screen.getByLabelText("Animation model LOD"),"Medium");
  expect(bind).toHaveBeenCalledExactlyOnceWith({drawable:"0",lod:"Medium"});
});
