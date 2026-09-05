import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import QuickImportPublish from "./QuickImportPublish";
import App from "./App";
import { createPreviewClient } from "./previewClient";
import type { Envelope, JobStart } from "./types";

const source = "C:\\Packages\\vehicle.comet6";
const response = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "test", job_id: "zip-job",
  operation: "result", sequence: 1, risk: "read_only", terminal: true, payload: { result } });
const fixture = (payload: Record<string, unknown>) => ({ ...payload, kind: "vehicle_package_publish_review",
  name: "Comet package", package_id: "vehicle.comet6", version: "1.0.0", edition: "legacy", total_bytes: 500,
  traffic_opt_in: true, vehicles: [{ model: "comet6", name: "Comet S2", price: 1878000 }],
  members: ["mod.toml", "allin1.content.json", "allin1.review.json", "payload/dlc.rpf", "payload/vehicles.json"]
    .map(path => ({ path, size: 100, sha256: "a".repeat(64) })), review_sha256: "c".repeat(64),
  review_only: true, file_write_performed: false, game_write_performed: false });
function setup(sourcePackage = source) {
  const client = createPreviewClient("quick_import");
  const guard = vi.fn();
  const apply = vi.spyOn(client, "applyVehiclePackagePublish");
  render(<QuickImportPublish client={client} sourcePackage={sourcePackage} gtaPath="" disabled={false} onGuardChange={guard} />);
  return { client, guard, apply, user: userEvent.setup() };
}
async function review(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Review ZIP publication" }));
  await screen.findByRole("heading", { name: "Review package ZIP" });
}

it("reviews prepared files and GBAY metadata and publishes only after confirmation", async () => {
  const { client, guard, apply, user } = setup();
  const start = vi.spyOn(client, "startJob");
  apply.mockImplementation(async payload => response({ kind: "vehicle_package_published", archive: payload.destination,
    archive_size: 1000, archive_sha256: "d".repeat(64), review_sha256: payload.review_sha256,
    file_write_performed: true, game_write_performed: false, upload_performed: false }));
  await review(user);
  expect(start).toHaveBeenCalledWith("review_vehicle_package_publish", { source_package: source,
    destination: "C:\\SDK\\exports\\vehicle.comet6.zip" }, expect.any(String), expect.any(Function));
  expect(screen.getByRole("table")).toHaveTextContent("Comet S2");
  expect(screen.getByText(/1,878,000/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Publish package ZIP" })).toBeDisabled();
  expect(guard).toHaveBeenLastCalledWith(true);
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Publish package ZIP" }));
  expect(await screen.findByRole("heading", { name: "Package ZIP published" })).toBeInTheDocument();
  expect(screen.getByText(`Archive SHA-256: ${"d".repeat(64)}`)).toBeInTheDocument();
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ source_package: source, review_sha256: "c".repeat(64), authoring_confirmed: true }));
  await waitFor(() => expect(guard).toHaveBeenLastCalledWith(false));
});

it("requires a prepared package and performs no job if the native picker is cancelled", async () => {
  const { client, user, guard } = setup();
  const start = vi.spyOn(client, "startJob");
  vi.spyOn(client, "selectPackageZipDestination").mockResolvedValue(null);
  await user.click(screen.getByRole("button", { name: "Review ZIP publication" }));
  expect(start).not.toHaveBeenCalled();
  expect(guard).toHaveBeenLastCalledWith(false);
});

it("does not offer publication before package preparation", () => {
  setup("");
  expect(screen.getByRole("button", { name: "Review ZIP publication" })).toBeDisabled();
  expect(screen.getByText(/Prepare a package above/)).toBeInTheDocument();
});

it("ignores a late terminal result and cancels a job whose start returns after cancellation", async () => {
  const { client, user, apply } = setup();
  let deliver!: (value: Envelope) => void;
  let accepted!: (value: JobStart) => void;
  let payload!: Record<string, unknown>;
  vi.spyOn(client, "startJob").mockImplementation(async (_op, data, _rev, event) => {
    payload = data; deliver = event;
    return await new Promise(resolve => { accepted = resolve; });
  });
  const cancel = vi.spyOn(client, "cancelJob");
  await user.click(screen.getByRole("button", { name: "Review ZIP publication" }));
  await user.click(screen.getByRole("button", { name: "Cancel ZIP review" }));
  await act(async () => accepted({ job_id: "late-zip", accepted: response({}) }));
  expect(cancel).toHaveBeenCalledWith("late-zip");
  act(() => deliver(response(fixture(payload))));
  expect(screen.queryByRole("heading", { name: "Review package ZIP" })).not.toBeInTheDocument();
  expect(apply).not.toHaveBeenCalled();
});

it("handles a completed review arriving before its job acceptance", async () => {
  const { client, user } = setup();
  vi.spyOn(client, "startJob").mockImplementation(async (_op, payload, _rev, event) => {
    event(response(fixture(payload)));
    return { job_id: "finished", accepted: response({}) };
  });
  await review(user);
  expect(screen.getByText("Legacy only")).toBeInTheDocument();
  expect(screen.getByText(/Opt-in included/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel ZIP review" })).not.toBeInTheDocument();
});

it("requires a fresh review and confirmation after stale write failure", async () => {
  const { apply, user } = setup();
  apply.mockRejectedValueOnce(new Error("Prepared package changed after review"));
  await review(user);
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Publish package ZIP" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("changed after review");
  expect(screen.queryByRole("button", { name: "Publish package ZIP" })).not.toBeInTheDocument();
  await review(user);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Publish package ZIP" })).toBeDisabled();
});

it("prevents double submission and cancellation during the non-cancellable write", async () => {
  const { apply, user, guard } = setup();
  let finish!: (value: Envelope) => void;
  apply.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await review(user);
  await user.click(screen.getByRole("checkbox"));
  await user.dblClick(screen.getByRole("button", { name: "Publish package ZIP" }));
  expect(apply).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Publishing ZIP…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Back to package" })).toBeDisabled();
  expect(guard).toHaveBeenLastCalledWith(true);
  await act(async () => finish({ ...response({}), operation: "error", payload: { message: "fixture stopped" } }));
});

it("rejects missing file hashes instead of authorizing publication", async () => {
  const { client, user, apply } = setup();
  vi.spyOn(client, "startJob").mockImplementation(async (_op, data, _rev, event) => {
    const invalid = fixture(data); invalid.members[0].sha256 = "";
    event(response(invalid));
    return { job_id: "invalid", accepted: response({}) };
  });
  await user.click(screen.getByRole("button", { name: "Review ZIP publication" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Unexpected ZIP review evidence");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(apply).not.toHaveBeenCalled();
});

it("guards source, edition, OIV export and workspace navigation during ZIP review", async () => {
  const client = createPreviewClient("quick_import");
  const user = userEvent.setup();
  render(<App client={client} />);
  await screen.findByRole("heading", { name: "Quick Import" });
  await user.click(screen.getByRole("button", { name: "Inspect source" }));
  await screen.findByRole("button", { name: "Build draft" });
  await user.click(screen.getByRole("button", { name: /Legacy.*discovered vehicle/ }));
  await user.click(screen.getByRole("button", { name: "Build draft" }));
  await user.click(await screen.findByRole("button", { name: "Prepare for Launcher" }));
  await user.click(screen.getByRole("button", { name: "Create package" }));
  await screen.findByText("Launcher package created");
  await waitFor(() => expect(screen.getByRole("button", { name: "Review ZIP publication" })).toBeEnabled());
  await review(user);
  expect(screen.getByText("Legacy only")).toBeInTheDocument();
  expect(screen.getByRole("table")).toHaveTextContent("Blista");
  expect(screen.getByRole("table")).toHaveTextContent("32,000");
  expect(screen.getByRole("button", { name: "Open archive" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Enhanced.*discovered vehicle/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Set up OIV export" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: /Package Linker/ }));
  expect(screen.getByRole("heading", { name: "Quick Import" })).toBeInTheDocument();
  expect(screen.getByText(/finish or cancel ZIP publication/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Back to package" }));
  await user.click(screen.getByRole("button", { name: /Package Linker/ }));
  expect(await screen.findByRole("heading", { name: "Package Linker" })).toBeInTheDocument();
});
