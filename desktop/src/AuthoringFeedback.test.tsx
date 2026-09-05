import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { AuthoringFeedback, type useAuthoringWorkspace } from "./useAuthoringWorkspace";

it("reveals a newly received review without acknowledging or executing it", () => {
  const work: ReturnType<typeof useAuthoringWorkspace> = {
    busy: false, reading: false, error: "", notice: "", lastResult: null,
    review: null, confirmed: false, locked: false,
    setConfirmed: vi.fn(), clearReview: vi.fn(), run: vi.fn(), choose: vi.fn(),
    cancel: vi.fn(), apply: vi.fn(), setError: vi.fn(),
  };
  const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
  const view = render(<AuthoringFeedback work={work} />);
  expect(scroll).not.toHaveBeenCalled();
  const review = { request: { action: "save_copy" }, value: {
    kind: "workspace_review", module: "code" as const, schema_version: 1, action: "save_copy",
  } };
  view.rerender(<AuthoringFeedback work={{ ...work, review, locked: true }} />);
  const panel = screen.getByRole("region", { name: "Authoring review" });
  expect(panel).toHaveFocus();
  expect(scroll).toHaveBeenCalledWith({ block: "start", behavior: "auto" });
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Apply reviewed change" })).toBeDisabled();
  expect(work.apply).not.toHaveBeenCalled();
  const calls = scroll.mock.calls.length;
  view.rerender(<AuthoringFeedback work={{ ...work, review, locked: true, confirmed: true }} />);
  expect(scroll).toHaveBeenCalledTimes(calls);
  expect(work.apply).not.toHaveBeenCalled();
  scroll.mockRestore();
});
