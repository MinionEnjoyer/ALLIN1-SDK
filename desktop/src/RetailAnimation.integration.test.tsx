import { readFileSync } from "node:fs";
import { expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import NativeAnimationView, { type AnimationPacket } from "./NativeAnimationView";
import { poseModel, prepareModel, type AnimationModel } from "./animationPose";

// Supplied only by smoke_retail_animation.py from temporary local extraction.
// No retail bytes are committed or fetched by the normal test suite.
const packetPath = process.env.ALLIN1_RETAIL_ANIMATION_PACKET;
it.skipIf(!packetPath)("poses and scrubs a locally decoded retail clip on its shared MP skeleton", () => {
  const { model, animation } = JSON.parse(readFileSync(packetPath!, "utf8")) as {model: AnimationModel; animation: AnimationPacket};
  const prepared = prepareModel(model);
  const start = poseModel(prepared, animation, 0, 0);
  const middle = poseModel(prepared, animation, animation.duration / 2, 0);
  const finish = poseModel(prepared, animation, animation.duration, 0, false, true);
  const layers = [...new Set(animation.tracks.map(track => track.layer))];
  for (const layer of layers) {
    for (const fraction of [0,.25,.5,.75,1]) {
      const sampled = poseModel(prepared,animation,animation.duration*fraction,layer,false,true);
      expect(sampled.matched).toBeGreaterThan(0);
      expect(sampled.meshes.flatMap(mesh => mesh.positions).every(Number.isFinite)).toBe(true);
    }
  }
  expect(start.matched).toBeGreaterThan(20);
  expect(animation.tracks.some(track => track.flags !== 0 && [0,1,2].includes(track.track))).toBe(true);
  expect(middle.meshes[0].positions).not.toEqual(start.meshes[0].positions);
  for (const pose of [start,middle,finish]) {
    expect(pose.meshes.flatMap(mesh => mesh.positions).every(Number.isFinite)).toBe(true);
    expect(pose.bones).toHaveLength(model.bones.length);
  }
  expect(poseModel(prepared,animation,0,0).meshes).toEqual(start.meshes);
  const draw = {fillRect:vi.fn(),beginPath:vi.fn(),closePath:vi.fn(),moveTo:vi.fn(),lineTo:vi.fn(),arc:vi.fn(),fill:vi.fn(),stroke:vi.fn()};
  const context = vi.spyOn(HTMLCanvasElement.prototype,"getContext").mockReturnValue(draw as unknown as CanvasRenderingContext2D);
  try {
    render(<NativeAnimationView packet={animation} model={model} active locked={false} draftDirty={false} onSelect={vi.fn()} />);
    expect(screen.getByRole("img",{name:"Animated model viewport"})).toBeVisible();
    expect(screen.getByLabelText("Model binding coverage")).toHaveTextContent(`${start.matched} matched channels`);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const initial = draw.moveTo.mock.calls.slice();
    draw.moveTo.mockClear();
    fireEvent.change(screen.getByLabelText("Animation time"),{target:{value:String(animation.duration/2)}});
    expect(draw.moveTo.mock.calls.length).toBeGreaterThan(0);
    expect(draw.moveTo.mock.calls).not.toEqual(initial);
    for (const layer of layers) {
      fireEvent.change(screen.getByLabelText("Model animation layer"),{target:{value:String(layer)}});
      const expected = poseModel(prepared,animation,animation.duration/2,layer);
      expect(screen.getByLabelText("Model binding coverage")).toHaveTextContent(`${expected.matched} matched channels`);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    }
    console.log(JSON.stringify({phase:"retail_react_pose", selection:animation.selected, layers, matched:start.matched, missing:start.missing,
      unsupported:start.unsupported, root_enabled_matched:finish.matched, bones:model.bones.length,
      vertices:model.vertex_count, changed_mesh:true, canvas_commands:true, pixel_rendering:"not_tested", game_acceptance:"not_tested"}));
  } finally { context.mockRestore(); }
}, 30000);
