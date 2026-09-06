import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import FormatCapabilities from "./FormatCapabilities";
import { isNativeResource } from "./nativeFormats";
import capabilities from "./formatCapabilities.json";

it("filters the matrix and keeps unsupported variants visible", async () => {
  const user = userEvent.setup();
  render(<FormatCapabilities />);
  expect(screen.getByRole("status")).toHaveTextContent("23 of 23 formats");
  await user.type(screen.getByLabelText("Find a format or capability"), ".ycd");
  expect(screen.getByRole("status")).toHaveTextContent("1 of 23 formats");
  expect(screen.getByText(/One layer at a time; local TRS and optional root motion/)).toBeInTheDocument();
  await user.clear(screen.getByLabelText("Find a format or capability"));
  await user.type(screen.getByLabelText("Find a format or capability"), "not-a-format");
  expect(screen.getByText(/Unknown extensions have no implied semantic editing support/)).toBeInTheDocument();
});

it("shows versioned edition evidence without treating fixtures as game acceptance", async () => {
  const user = userEvent.setup();
  render(<FormatCapabilities />);
  await user.type(screen.getByLabelText("Find a format or capability"), ".ytd");
  await user.selectOptions(screen.getByLabelText("Evidence edition"), "Legacy");
  expect(screen.getByRole("columnheader", { name: "Limits & Legacy evidence" })).toBeInTheDocument();
  expect(screen.getByText(/Generated fixture export\/edit\/build\/reparse tested; not retail/)).toBeInTheDocument();
  expect(screen.getByText(/Matrix revision/)).toHaveTextContent(`Matrix revision ${capabilities.revision}`);
});

it("routes only the matrix's generic XML-editable native extensions", () => {
  expect(isNativeResource("X64/textures/PAINT.YTD")).toBe(true);
  expect(isNativeResource("vehicle.yft")).toBe(true);
  expect(isNativeResource("game.gxt2")).toBe(false);
  expect(isNativeResource("movie.gfx")).toBe(false);
  expect(isNativeResource("archive.rpf")).toBe(false);
  expect(isNativeResource("no-extension")).toBe(false);
});
