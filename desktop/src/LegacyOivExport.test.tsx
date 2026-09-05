import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import LegacyOivExport from "./LegacyOivExport";
import App from "./App";
import { createPreviewClient } from "./previewClient";
import type { Envelope, JobStart } from "./types";

const response = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "test", job_id: "test-job",
  operation: "result", sequence: 1, risk: "read_only", terminal: true, payload: { result } });
const fixture = (payload: Record<string, unknown>) => ({ ...payload, kind: "vehicle_oiv_export_review", edition: "legacy",
  name: "Lunga", package_id: "vehicle.lunga.legacy", version: "1.0.0", payload_member: "Legacy/lunga/dlc.rpf",
  payload_size: 1048576, payload_sha256: "a".repeat(64), review_sha256: "b".repeat(64),
  members: ["assembly.xml", "content/dlcpacks/lunga/dlc.rpf"], review_only: true, game_write_performed: false, file_write_performed: false });
function setup(edition = "legacy") {
  const client = createPreviewClient("quick_import");
  const guard = vi.fn();
  const apply = vi.spyOn(client, "applyVehicleOivExport");
  render(<LegacyOivExport client={client} source={"C:\\Mods\\Lunga"} gtaPath="" edition={edition} disabled={false} onGuardChange={guard} />);
  return { client, guard, apply, user: userEvent.setup() };
}
async function fill(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Set up OIV export" }));
  await user.type(screen.getByLabelText("Package author"), "Fixture author");
  await user.click(screen.getByRole("button", { name: "Choose file" }));
}
async function review(user: ReturnType<typeof userEvent.setup>) {
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Review OIV export" }));
  await screen.findByRole("heading", { name: "Review OIV export" });
}

it("exports only the reviewed payload after explicit confirmation and displays the archive hash", async () => {
  const { client, user, apply, guard } = setup();
  const start = vi.spyOn(client, "startJob");
  apply.mockImplementation(async payload => response({ kind: "vehicle_oiv_exported", archive: payload.destination,
    archive_size: 12345, archive_sha256: "c".repeat(64), review_sha256: payload.review_sha256, game_write_performed: false }));
  await review(user);
  expect(guard).toHaveBeenLastCalledWith(true);
  expect(start).toHaveBeenCalledWith("review_vehicle_oiv_export", { source: "C:\\Mods\\Lunga", edition: "legacy",
    author: "Fixture author", destination: "C:\\SDK\\exports\\legacy-vehicle.oiv" }, expect.any(String), expect.any(Function));
  expect(screen.getByText(/GBAY listings, traffic preferences/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Export Legacy OIV" })).toBeDisabled();
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Export Legacy OIV" }));
  expect(await screen.findByText("Legacy OIV exported")).toBeInTheDocument();
  expect(screen.getByText(`Archive SHA-256: ${"c".repeat(64)}`)).toBeInTheDocument();
  expect(apply).toHaveBeenCalledTimes(1);
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ review_sha256: "b".repeat(64), authoring_confirmed: true }));
  await waitFor(() => expect(guard).toHaveBeenLastCalledWith(false));
});

it("keeps Enhanced read-only instead of silently converting it", () => {
  setup("enhanced");
  expect(screen.getByRole("button", { name: "Set up OIV export" })).toBeDisabled();
  expect(screen.getByText(/Enhanced assets are not converted/)).toBeInTheDocument();
});

it("cancels a late-starting review and ignores its terminal result", async () => {
  const { client, user, apply } = setup();
  let deliver!: (message: Envelope) => void;
  let started!: (value: JobStart) => void;
  let payload!: Record<string, unknown>;
  vi.spyOn(client, "startJob").mockImplementation(async (_op, request, _revision, onEvent) => {
    deliver = onEvent; payload = request;
    return await new Promise<JobStart>(resolve => { started = resolve; });
  });
  const cancel = vi.spyOn(client, "cancelJob");
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Review OIV export" }));
  await user.click(screen.getByRole("button", { name: "Cancel review" }));
  await act(async () => { started({ job_id: "late-job", accepted: response({}) }); });
  expect(cancel).toHaveBeenCalledWith("late-job");
  act(() => deliver(response(fixture(payload))));
  expect(screen.queryByRole("button", { name: "Export Legacy OIV" })).not.toBeInTheDocument();
  expect(apply).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Package author")).toHaveValue("Fixture author");
});

it("accepts a terminal review before job start resolves without showing a stale cancel action", async () => {
  const { client, user } = setup();
  vi.spyOn(client, "startJob").mockImplementation(async (_op, payload, _revision, onEvent) => {
    onEvent(response(fixture(payload)));
    return { job_id: "already-done", accepted: response({}) };
  });
  await review(user);
  expect(screen.queryByRole("button", { name: "Cancel review" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Export Legacy OIV" })).toBeDisabled();
});

it("drops stale authorization after export failure and preserves author settings", async () => {
  const { user, apply } = setup();
  apply.mockRejectedValue(new Error("Source changed after review"));
  await review(user);
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Export Legacy OIV" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Source changed after review");
  expect(screen.queryByRole("button", { name: "Export Legacy OIV" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Package author")).toHaveValue("Fixture author");
  await user.click(screen.getByRole("button", { name: "Review OIV export" }));
  expect(await screen.findByRole("button", { name: "Export Legacy OIV" })).toBeDisabled();
});

it("requires a fresh confirmation after returning to export settings", async () => {
  const { user, apply } = setup();
  await review(user);
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Back to export settings" }));
  await user.type(screen.getByLabelText("Package author"), " changed");
  await user.click(screen.getByRole("button", { name: "Review OIV export" }));
  expect(await screen.findByRole("button", { name: "Export Legacy OIV" })).toBeDisabled();
  expect(apply).not.toHaveBeenCalled();
});

it("blocks closing or double submission while an export is writing", async () => {
  const { user, apply, guard } = setup();
  let finish!: (value: Envelope) => void;
  apply.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await review(user);
  await user.click(screen.getByRole("checkbox"));
  await user.dblClick(screen.getByRole("button", { name: "Export Legacy OIV" }));
  expect(apply).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Close export" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Writing OIV…" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Cancel review" })).not.toBeInTheDocument();
  expect(guard).toHaveBeenLastCalledWith(true);
  await act(async () => finish({ ...response({}), operation: "error", payload: { message: "fixture failure" } }));
});

it("rejects malformed review evidence before allowing export", async () => {
  const { client, user, apply } = setup();
  vi.spyOn(client, "startJob").mockImplementation(async (_op, payload, _revision, onEvent) => {
    onEvent(response({ ...fixture(payload), payload_sha256: "missing" }));
    return { job_id: "bad-evidence", accepted: response({}) };
  });
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Review OIV export" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Unexpected OIV review response");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(apply).not.toHaveBeenCalled();
});

it("guards Quick Import navigation, edition and source while OIV settings are open", async () => {
  const client = createPreviewClient("quick_import");
  const user = userEvent.setup();
  render(<App client={client} />);
  await screen.findByRole("heading", { name: "Quick Import" });
  await user.click(screen.getByRole("button", { name: "Inspect source" }));
  await screen.findByRole("button", { name: /Legacy.*discovered vehicle/ });
  await user.click(screen.getByRole("button", { name: /Legacy.*discovered vehicle/ }));
  await user.click(screen.getByRole("button", { name: "Set up OIV export" }));
  expect(screen.getByRole("button", { name: "Open archive" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Enhanced.*discovered vehicle/ })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: /Package Linker/ }));
  expect(screen.getByRole("heading", { name: "Quick Import" })).toBeInTheDocument();
  expect(screen.getByText(/close the OIV export/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Close export" }));
  await user.click(screen.getByRole("button", { name: /Package Linker/ }));
  expect(await screen.findByRole("heading", { name: "Package Linker" })).toBeInTheDocument();
});
