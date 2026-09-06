import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import NativeAnimationView, { type AnimationPacket } from "./NativeAnimationView";
import fixture from "./nativeAnimationFixture.json";

afterEach(() => vi.restoreAllMocks());
const props = { packet: fixture, active: true, locked: false, draftDirty: false, onSelect: vi.fn() };

it("shows the native-backed curves, exact time samples and per-track identity", async () => {
  const user = userEvent.setup(), select = vi.fn();
  render(<NativeAnimationView {...props} onSelect={select} />);
  expect(screen.getByRole("img", { name: "Animation channel curves" }).querySelectorAll("polyline")).toHaveLength(4);
  expect(screen.getByText(/Channel inspection is not model playback/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Animation time"), { target: { value: ".5" } });
  expect(screen.getByLabelText("Animation timestamp")).toHaveTextContent("0.500 / 0.500 seconds");
  expect(screen.getByText("1.500000")).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Animation track"), "0:1");
  expect(screen.getByText(/Normalized quaternion XYZW/)).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Animation selection"), "animation:22222222");
  expect(select).toHaveBeenCalledExactlyOnceWith("animation:22222222");
});

it("plays locally and cancels playback when hidden, locked, collapsed or unmounted", async () => {
  const user = userEvent.setup();
  let pending: FrameRequestCallback | null = null;
  const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => { pending = null; });
  vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => { pending = callback; return 1; });
  const view = render(<NativeAnimationView {...props} />);
  await user.click(screen.getByRole("button", { name: "Play channels" }));
  act(() => { pending?.(0); });
  act(() => { pending?.(100); });
  expect(screen.getByLabelText("Animation timestamp")).toHaveTextContent("0.100 / 0.500");
  view.rerender(<NativeAnimationView {...props} active={false} />);
  expect(screen.getByRole("button", { name: "Play channels" })).toBeDisabled();
  expect(cancel).toHaveBeenCalled();
  view.rerender(<NativeAnimationView {...props} />);
  await user.click(screen.getByRole("button", { name: "Play channels" }));
  view.rerender(<NativeAnimationView {...props} draftDirty />);
  expect(screen.getByRole("button", { name: "Play channels" })).toBeDisabled();
  expect(screen.getByLabelText("Animation selection")).toBeDisabled();
  expect(screen.getByText(/Showing saved XML/)).toBeInTheDocument();
  view.rerender(<NativeAnimationView {...props} />);
  await user.click(screen.getByRole("button", { name: "Play channels" }));
  const details = screen.getByText("Animation timeline & decoded channels").closest("details")!;
  details.open = false;
  fireEvent(details, new Event("toggle"));
  expect(cancel).toHaveBeenCalledTimes(3);
  view.unmount();
  expect(pending).toBeNull();
});

it("respects speed and non-looping endpoint, and resets without a backend write", async () => {
  const user = userEvent.setup();
  let pending: FrameRequestCallback | null = null;
  vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => { pending = callback; return 1; });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => { pending = null; });
  render(<NativeAnimationView {...props} />);
  await user.click(screen.getByLabelText("Loop channels"));
  await user.selectOptions(screen.getByLabelText("Animation speed"), "2");
  await user.click(screen.getByRole("button", { name: "Play channels" }));
  act(() => pending?.(0));
  act(() => pending?.(250));
  expect(screen.getByLabelText("Animation timestamp")).toHaveTextContent("0.500 / 0.500");
  expect(screen.getByRole("button", { name: "Play channels" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "Reset timeline" }));
  expect(screen.getByLabelText("Animation time")).toHaveValue("0");
});

it.each([{ times: [0, 0] }, { times: [0, NaN] }, { tracks: [null] }, { choices: [null] }, { selected: "animation:NOTREAL" },
  { duration: Infinity }, { read_only: false }, { times: Array(241).fill(0) }, { tracks: [fixture.tracks[0], fixture.tracks[0]] }])("rejects malformed animation packets %#", change => {
  render(<NativeAnimationView {...props} packet={{ ...fixture, ...change } as AnimationPacket} />);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid or exceeds display limits");
});
