import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { WeaponFireRate, rpmKey, intervalKey } from "./WeaponFireRate";

const original = { [rpmKey]: "508.474576", [intervalKey]: "0.118000" };
it("shows the source interval without rounding or silently changing the draft", () => {
  const change = vi.fn();
  render(<WeaponFireRate values={original} original={original} editable={[rpmKey]} disabled={false} onChange={change} />);
  expect(screen.getByLabelText("Rounds per minute (RPM)")).toHaveValue("508.474576");
  expect(screen.getByText("0.118000")).toBeInTheDocument();
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Rounds per minute (RPM)"), { target: { value: "1200" } });
  expect(change).toHaveBeenCalledWith({ [rpmKey]: "1200" });
});
it("shows a proposed native interval and keeps the source distinct", () => {
  render(<WeaponFireRate values={{ ...original, [rpmKey]: "1200" }} original={original}
    editable={[rpmKey]} disabled={false} onChange={vi.fn()} />);
  expect(screen.getByText("Proposed shot interval")).toBeInTheDocument();
  expect(screen.getByText("0.05")).toBeInTheDocument();
});
it.each(["", "0", "-1", "NaN", "Infinity", "60001"])("flags invalid RPM %s without guessing an interval", value => {
  render(<WeaponFireRate values={{ ...original, [rpmKey]: value }} original={original}
    editable={[rpmKey]} disabled={false} onChange={vi.fn()} />);
  expect(screen.getByRole("alert")).toHaveTextContent("1 to 60,000 RPM");
  expect(screen.getByText("0.118000")).toBeInTheDocument();
});
it("honors read-only state and missing source nodes", () => {
  const view = render(<WeaponFireRate values={original} original={original}
    editable={[rpmKey]} disabled onChange={vi.fn()} />);
  expect(screen.getByLabelText("Rounds per minute (RPM)")).toBeDisabled();
  view.rerender(<WeaponFireRate values={{}} original={{}} editable={[]} disabled={false} onChange={vi.fn()} />);
  expect(screen.queryByLabelText("Rounds per minute (RPM)")).not.toBeInTheDocument();
  expect(screen.getByText(/No editable TimeBetweenShots/)).toBeInTheDocument();
});
