import { useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect } from "vitest";
import { EditorView } from "@codemirror/view";
import CodeEditor from "./CodeEditor";

function Harness({ locked = false }: { locked?: boolean }) {
  const [value, setValue] = useState("local first = 1\r\nreturn first\r\n");
  return <><CodeEditor value={value} onChange={setValue} language="lua" lineEnding="CRLF" locked={locked} /><output data-testid="value">{JSON.stringify(value)}</output></>;
}

it("real editor preserves CRLF through edits and keyboard undo/redo", () => {
  render(<Harness />);
  const content = screen.getByRole("textbox", { name: "LUA source editor" });
  const view = EditorView.findFromDOM(content)!;
  act(() => view.dispatch({ changes: { from: 0, to: 5, insert: "local" } }));
  expect(view.state.sliceDoc()).toBe("local first = 1\r\nreturn first\r\n");
  act(() => view.dispatch({ changes: { from: 6, to: 11, insert: "second" }, userEvent: "input.type" }));
  expect(screen.getByTestId("value")).toHaveTextContent('local second = 1\\r\\nreturn first\\r\\n');
  fireEvent.keyDown(content, { key: "z", ctrlKey: true });
  expect(view.state.sliceDoc()).toBe("local first = 1\r\nreturn first\r\n");
  fireEvent.keyDown(content, { key: "y", ctrlKey: true });
  expect(view.state.sliceDoc()).toContain("second");
});

it("search replacement changes the draft only while editing is unlocked", async () => {
  const user = userEvent.setup();
  const { rerender } = render(<Harness />);
  await user.click(screen.getByRole("button", { name: "Find / replace" }));
  await user.type(screen.getByRole("textbox", { name: "Find" }), "first");
  await user.type(screen.getByRole("textbox", { name: "Replace" }), "second");
  await user.click(screen.getByRole("button", { name: "replace all" }));
  expect(screen.getByTestId("value")).toHaveTextContent("local second");
  rerender(<Harness locked />);
  const view = EditorView.findFromDOM(screen.getByRole("textbox", { name: "LUA source editor" }))!;
  expect(view.state.readOnly).toBe(true);
  // CodeMirror's replacement command must respect a pending save review.
  await user.clear(screen.getByRole("textbox", { name: "Find" }));
  await user.type(screen.getByRole("textbox", { name: "Find" }), "second");
  await user.clear(screen.getByRole("textbox", { name: "Replace" }));
  await user.type(screen.getByRole("textbox", { name: "Replace" }), "forbidden");
  await user.click(screen.getByRole("button", { name: "replace all" }));
  expect(view.state.sliceDoc()).toBe("local second = 1\r\nreturn second\r\n");
});
