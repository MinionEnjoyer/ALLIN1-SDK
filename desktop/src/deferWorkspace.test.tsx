import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, it, vi } from "vitest";
import { deferWorkspace } from "./deferWorkspace";

it("does not load until rendered and keeps a mounted draft through rerenders", async () => {
  const loader = vi.fn(async () => ({ default: function Editor({ name }: { name: string }) {
    const [draft, setDraft] = useState("");
    return <input aria-label={name} value={draft} onChange={event => setDraft(event.target.value)} />;
  } }));
  const Workspace = deferWorkspace(loader);
  expect(loader).not.toHaveBeenCalled();
  const view = render(<Workspace name="Draft" />);
  const input = await screen.findByRole("textbox", { name: "Draft" });
  await userEvent.setup().type(input, "keep this change");
  view.rerender(<Workspace name="Updated label" />);
  expect(screen.getByRole("textbox", { name: "Updated label" })).toHaveValue("keep this change");
  expect(loader).toHaveBeenCalledTimes(1);
});

it("keeps the shell usable while loading and after an import failure without retrying", async () => {
  let reject!: (error: Error) => void;
  const loader = vi.fn(() => new Promise<{ default: () => React.ReactNode }>((_resolve, fail) => { reject = fail; }));
  const Workspace = deferWorkspace(loader);
  const navigate = vi.fn();
  const errorLog = vi.spyOn(console, "error").mockImplementation(() => undefined);
  try {
    render(<><button onClick={navigate}>Help</button><Workspace /></>);
    expect(screen.getByRole("status")).toHaveTextContent("Loading workspace");
    await userEvent.setup().click(screen.getByRole("button", { name: "Help" }));
    await act(async () => { reject(new Error("Missing chunk")); });
    expect(await screen.findByRole("alert")).toHaveTextContent("has not restarted");
    await userEvent.setup().click(screen.getByRole("button", { name: "Help" }));
    expect(navigate).toHaveBeenCalledTimes(2);
    expect(loader).toHaveBeenCalledTimes(1);
  } finally { errorLog.mockRestore(); }
});
