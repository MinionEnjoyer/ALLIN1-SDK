import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import RecipeConversionPanel from "./RecipeConversionPanel";
import { createPreviewClient } from "./previewClient";
import type { Envelope, JobStart } from "./types";

const requested = "C:\\Users\\RUNNER~1\\Recipe source";
const canonical = "C:\\Users\\runneradmin\\Recipe source";
function session(overrides: Record<string, unknown> = {}): Envelope {
  return { protocol_version: "1.0.0", request_id: "recipe", job_id: "inspection", sequence: 1,
    operation: "result", risk: "read_only", terminal: true, payload: { result: {
      kind: "workspace_session", module: "recipe", schema_version: 1, read_only: true, game_write_performed: false,
      source: canonical, requested_source: requested, state_sha256: "a".repeat(64),
      capabilities: { managed: true, batches: false, created: false, compile: false }, ...overrides,
    } } };
}
function clientFixture() {
  const client = createPreviewClient("rpf");
  const events: ((message: Envelope) => void)[] = [];
  client.startJob = vi.fn(async (_operation, _payload, _revision, callback) => {
    events.push(callback); return { job_id: "inspection" } as JobStart;
  });
  client.cancelJob = vi.fn(async () => session());
  client.selectPath = vi.fn(async () => "C:/Authoring outputs");
  client.applyWorkspaceAction = vi.fn();
  return { client, events, user: userEvent.setup(), guard: vi.fn() };
}

it("accepts a host-resolved Windows alias but reviews the canonical source with its original digest", async () => {
  const { client, events, user, guard } = clientFixture();
  render(<RecipeConversionPanel client={client} source={requested} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Inspect conversion options" }));
  act(() => events[0](session()));
  expect(screen.getByLabelText("Conversion type")).toHaveValue("managed");
  expect(screen.getByText(canonical)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Review recipe conversion" }));
  expect(client.startJob).toHaveBeenLastCalledWith("review_workspace_action", expect.objectContaining({
    source: canonical, expected_state_sha256: "a".repeat(64), action: "managed",
  }), expect.any(String), expect.any(Function));
  expect(client.applyWorkspaceAction).not.toHaveBeenCalled();
});

it.each<[string, Record<string, unknown>]>([
  ["different request", { requested_source: "C:/different recipe" }],
  ["missing request", { requested_source: undefined }],
  ["empty source", { source: "" }], ["missing source", { source: undefined }],
  ["missing capabilities", { capabilities: { managed: true } }],
  ["invalid digest", { state_sha256: "invalid" }], ["not read-only", { read_only: false }],
])("rejects mismatched or incomplete recipe identity: %s", async (_reason, overrides) => {
  const { client, events, user, guard } = clientFixture();
  render(<RecipeConversionPanel client={client} source={requested} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Inspect conversion options" }));
  act(() => events[0](session(overrides)));
  expect(screen.getByRole("alert")).toBeInTheDocument();
  expect(screen.queryByLabelText("Conversion type")).not.toBeInTheDocument();
  expect(client.applyWorkspaceAction).not.toHaveBeenCalled();
});

it("drops an old session when the recipe changes and ignores the cancelled selection's late result", async () => {
  const { client, events, user, guard } = clientFixture();
  const { rerender } = render(<RecipeConversionPanel client={client} source={requested} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Inspect conversion options" }));
  act(() => events[0](session()));
  expect(screen.getByLabelText("Conversion type")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Inspect conversion options" }));
  rerender(<RecipeConversionPanel client={client} source="C:/next recipe" onGuardChange={guard} />);
  expect(client.cancelJob).toHaveBeenCalledWith("inspection");
  act(() => events[1](session()));
  expect(screen.queryByLabelText("Conversion type")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(guard).toHaveBeenLastCalledWith(false);
});
