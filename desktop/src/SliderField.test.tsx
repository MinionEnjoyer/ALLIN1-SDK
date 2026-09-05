import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import SliderField from "./SliderField";
import { sliderNumber } from "./sliderValue";
import { handlingSlider } from "./handlingSliders";

it.each(["", " ", "-", "1e", "NaN", "Infinity", "0x10", "1,2"])("does not coerce invalid draft %s into a number", value => {
  expect(Number.isNaN(sliderNumber(value))).toBe(true);
});
it("preserves precise source text and soft-range outliers without writing", () => {
  const change = vi.fn();
  const view = render(<SliderField label="Gain" min={0} max={1} step={.01} value="0.123456789" onChange={change} />);
  expect(screen.getByRole("textbox", { name: "Gain" })).toHaveValue("0.123456789");
  expect(change).not.toHaveBeenCalled();
  view.rerender(<SliderField label="Gain" min={0} max={1} step={.01} value="4.123456789" onChange={change} />);
  expect(screen.getByRole("textbox")).toHaveValue("4.123456789");
  expect(screen.getByRole("textbox")).not.toBeInvalid();
  expect(screen.getByText(/Outside slider range/)).toBeInTheDocument();
  expect(change).not.toHaveBeenCalled();
});
it("synchronizes drag, exact input, fine keyboard adjustment, endpoints and reset", () => {
  function Harness() {
    const [value, setValue] = useState(".5");
    return <SliderField label="Bias" value={value} onChange={setValue} min={0} max={1} step={.01} fineStep={.001} resetValue=".5" />;
  }
  render(<Harness />);
  const slider = screen.getByRole("slider"), input = screen.getByRole("textbox");
  fireEvent.change(slider, { target: { value: ".724" } });
  expect(input).toHaveValue("0.72");
  fireEvent.keyDown(slider, { key: "ArrowRight", shiftKey: true });
  expect(input).toHaveValue("0.721");
  fireEvent.keyDown(slider, { key: "PageUp" });
  expect(input).toHaveValue("0.821");
  fireEvent.keyDown(slider, { key: "Home" }); expect(input).toHaveValue("0");
  fireEvent.keyDown(slider, { key: "End" }); expect(input).toHaveValue("1");
  fireEvent.change(input, { target: { value: "0.333333333" } });
  expect(slider).toHaveValue("0.333333333");
  fireEvent.click(screen.getByRole("button", { name: "Reset Bias" }));
  expect(input).toHaveValue(".5");
});
it("keeps numeric partial edits, reports invalid instead of zero, and accepts external reset", () => {
  const change = vi.fn();
  function Harness() {
    const [value, setValue] = useState(1);
    return <><SliderField numeric label="Ratio" min={.1} max={10} hardMin={.1} hardMax={10} step={.01}
      value={value} onChange={next => { change(next); setValue(next); }} /><button onClick={() => setValue(1)}>External reset</button></>;
  }
  render(<Harness />);
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "" } });
  expect(change).toHaveBeenLastCalledWith(NaN);
  expect(input).toHaveValue(""); expect(input).toBeInvalid();
  fireEvent.change(input, { target: { value: "-" } }); expect(input).toHaveValue("-");
  fireEvent.change(input, { target: { value: "1." } }); expect(input).toHaveValue("1.");
  fireEvent.change(input, { target: { value: "1.23456789" } }); expect(input).toHaveValue("1.23456789");
  fireEvent.click(screen.getByText("External reset")); expect(input).toHaveValue("1");
});
it("never changes disabled controls and distinguishes hard errors from soft ranges", () => {
  const change = vi.fn();
  render(<SliderField label="FOV" value="200" min={1} max={179} hardMin={1} hardMax={179} step={1} resetValue="60" disabled onChange={change} />);
  expect(screen.getByRole("textbox")).toBeDisabled(); expect(screen.getByRole("textbox")).toBeInvalid();
  expect(screen.getByRole("slider")).toBeDisabled(); expect(screen.getByRole("button")).toBeDisabled();
  fireEvent.change(screen.getByRole("slider"), { target: { value: "90" } });
  fireEvent.keyDown(screen.getByRole("slider"), { key: "End" });
  expect(change).not.toHaveBeenCalled();
});
it("lets a view-only zoom be cleared and retyped without sending NaN to the camera", () => {
  const change = vi.fn();
  function Harness() {
    const [zoom, setZoom] = useState(100);
    return <SliderField numeric commitValidOnly label="Zoom" min={10} max={180} hardMin={10} hardMax={180} step={5}
      value={zoom} onChange={value => { change(value); setZoom(value); }} />;
  }
  render(<Harness />);
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "" } }); expect(input).toHaveValue("");
  fireEvent.change(input, { target: { value: "2" } }); expect(input).toHaveValue("2");
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: "25" } }); expect(input).toHaveValue("25");
  expect(change).toHaveBeenLastCalledWith(25);
});
it("leaves counts, references and raw shader values outside the handling slider allowlist", () => {
  expect(handlingSlider("handling.fDriveBiasFront")).toMatchObject({ min: 0, max: 1, endpoints: ["Rear · 0", "Front · 1"] });
  expect(handlingSlider("handling.fSuspensionLowerLimit")?.min).toBeLessThan(0);
  for (const key of ["handling.nInitialDriveGears", "variation.lightSettings", "vehicle.audioNameHash", "emissiveMultiplier"]) expect(handlingSlider(key)).toBeUndefined();
});
