import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import Gxt2Workspace, { type Gxt2Review } from "./Gxt2Workspace";
import { createPreviewClient } from "./previewClient";
import { rpfPublicationPreview } from "./gxt2Preview";
import type { Envelope, JobStart } from "./types";

const response = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "publish-test", job_id: null,
  operation: "result", sequence: 1, risk: "authoring_write", terminal: true, payload: { result } });
async function setup(member = false) {
  const client = createPreviewClient(member ? "rpf_member" : "rpf_package"), user = userEvent.setup(), guard = vi.fn();
  render(<Gxt2Workspace client={client} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Open text workspace" }));
  await user.click(await screen.findByRole("button", { name: "Configure ALLIN1 export" }));
  expect(screen.getByRole("heading", { name: "Export a whole-archive replacement" })).toHaveFocus();
  await user.click(screen.getByRole("button", { name: "Choose RPF build folder" }));
  await user.type(screen.getByLabelText("Author", { exact: true }), "Test author");
  await user.type(screen.getByLabelText("GTA-relative archive destination"), "mods/update/text-fixture.rpf");
  const result = (payload: Record<string, unknown>) => {
    const publication = rpfPublicationPreview({ ...payload, root_member: member });
    return { kind: "gxt2_rpf_published", archive: payload.destination, sha256: "7".repeat(64), archive_size: 528000,
      package_id: publication.metadata.id, edition: "enhanced", target: publication.metadata.target,
      payload_sha256: publication.payload_sha256, publication_mode: publication.publication_mode, manifest_schema_version: publication.manifest_schema_version,
      entry: publication.entry, original_sha256: publication.original_sha256, members: publication.members, review_sha256: payload.review_sha256,
      file_write_performed: true, game_write_performed: false, install_performed: false, upload_performed: false };
  };
  const apply = vi.spyOn(client, "applyGxt2Action").mockImplementation(async payload => response(result(payload)));
  return { user, client, guard, apply, result };
}
async function review(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Review ALLIN1 ZIP" }));
  await screen.findByRole("heading", { name: "Review: Export ALLIN1 ZIP" });
}

it("reviews the exact whole-archive target and confirms a validated ZIP export", async () => {
  const { user, client, apply, guard } = await setup();
  const picker = vi.spyOn(client, "selectPackageZipDestination");
  expect(guard).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole("button", { name: "Open GXT2" })).toBeDisabled();
  await review(user);
  expect(picker).toHaveBeenCalledWith("game-text-patch-1.0.0.zip");
  expect(screen.getByRole("button", { name: "Export ALLIN1 ZIP" })).toBeDisabled();
  expect(screen.getByText(/Installing this ZIP can replace unrelated edits/)).toBeInTheDocument();
  expect(screen.getByText("payload/text-fixture.rpf")).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: /whole-archive replacement/ }));
  await user.click(screen.getByRole("button", { name: "Export ALLIN1 ZIP" }));
  expect(await screen.findByRole("status")).toHaveTextContent("ALLIN1 ZIP exported and validated");
  expect(screen.getByRole("status")).toHaveTextContent("Nothing was installed or uploaded");
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ action: "publish_rpf", authoring_confirmed: true,
    package_metadata: expect.objectContaining({ author: "Test author", target: "mods/update/text-fixture.rpf" }) }));
  expect(guard).toHaveBeenLastCalledWith(false);
});

it("keeps settings after picker cancellation and requires metadata before review", async () => {
  const { user, client } = await setup();
  await user.clear(screen.getByLabelText("Author", { exact: true }));
  expect(screen.getByRole("button", { name: "Review ALLIN1 ZIP" })).toBeDisabled();
  await user.type(screen.getByLabelText("Author", { exact: true }), "Retained author");
  vi.spyOn(client, "selectPackageZipDestination").mockResolvedValueOnce(null);
  await user.click(screen.getByRole("button", { name: "Review ALLIN1 ZIP" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Review ALLIN1 ZIP" })).toBeEnabled());
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Author", { exact: true })).toHaveValue("Retained author");
});

it("invalidates failed export confirmation without discarding settings", async () => {
  const { user, apply } = await setup();
  apply.mockRejectedValueOnce(new Error("RPF build changed"));
  await review(user);
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Export ALLIN1 ZIP" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("RPF build changed");
  expect(screen.getByLabelText("Author", { exact: true })).toHaveValue("Test author");
  await review(user);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  await user.click(screen.getByRole("button", { name: "Back to export settings" }));
  expect(screen.getByRole("heading", { name: "Export a whole-archive replacement" })).toHaveFocus();
  await review(user);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
});

it("rejects mismatched manifest review evidence", async () => {
  const { user, client, apply } = await setup();
  const original = client.startJob.bind(client);
  vi.spyOn(client, "startJob").mockImplementation((op, payload, revision, event) => original(op, payload, revision, message => {
    if (message.operation === "result" && message.terminal) (message.payload.result as Gxt2Review).rpf_publication!.metadata.target = "mods/other.rpf";
    event(message);
  }));
  await user.click(screen.getByRole("button", { name: "Review ALLIN1 ZIP" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Unexpected GXT2 review");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(apply).not.toHaveBeenCalled();
});

it("cancels late ZIP reviews without losing the selected build", async () => {
  const { user, client } = await setup();
  let accept!: (start: JobStart) => void, terminal!: (message: Envelope) => void;
  vi.spyOn(client, "startJob").mockImplementation((_op, _payload, _revision, event) => {
    terminal = event; return new Promise(resolve => { accept = resolve; });
  });
  const cancel = vi.spyOn(client, "cancelJob");
  await user.click(screen.getByRole("button", { name: "Review ALLIN1 ZIP" }));
  await user.click(await screen.findByRole("button", { name: "Cancel text review" }));
  act(() => terminal(response({})));
  await act(async () => accept({ job_id: "late-zip", accepted: response({}) }));
  expect(cancel).toHaveBeenCalledWith("late-zip");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Review ALLIN1 ZIP" })).toBeEnabled();
});

it("prevents double ZIP writes and rejects mismatched published payloads", async () => {
  const { user, apply, result } = await setup();
  let finish!: (message: Envelope) => void;
  apply.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await review(user);
  await user.click(screen.getByRole("checkbox"));
  await user.dblClick(screen.getByRole("button", { name: "Export ALLIN1 ZIP" }));
  expect(apply).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Back to export settings" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Close export settings" })).toBeDisabled();
  await act(async () => finish(response({ ...result(apply.mock.calls[0][0]), payload_sha256: "0".repeat(64) })));
  expect(await screen.findByRole("alert")).toHaveTextContent("ALLIN1 ZIP outcome could not be verified");
});

it("exports only the selected outer-archive dictionary with compatibility and original-hash review", async () => {
  const { user, apply } = await setup(true);
  await user.selectOptions(screen.getByLabelText("Export scope"), "member");
  expect(screen.getByRole("heading", { name: "Export an exact dictionary patch" })).toBeInTheDocument();
  expect(screen.getByText(/Older Launchers reject this ZIP/)).toBeInTheDocument();
  await review(user);
  expect(screen.getByLabelText("Export scope")).toBeDisabled();
  expect(screen.getByText("payload/replacement.gxt2")).toBeInTheDocument();
  expect(screen.queryByText("payload/text-fixture.rpf")).not.toBeInTheDocument();
  expect(screen.getByText("Required original SHA-256")).toBeInTheDocument();
  expect(screen.getByText("2,190 bytes")).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: /exact-member patch/ }));
  await user.click(screen.getByRole("button", { name: "Export ALLIN1 ZIP" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Exact member: global.gxt2");
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ publication_mode: "member", authoring_confirmed: true }));
});

it("exports a nested dictionary with schema-4 compatibility and its complete target", async () => {
  const { user, apply } = await setup();
  expect(screen.getByRole("option", { name: /Selected dictionary only · schema 4/ })).toBeEnabled();
  await user.selectOptions(screen.getByLabelText("Export scope"), "member");
  expect(screen.getByText(/No containing RPF is shipped/)).toBeInTheDocument();
  await review(user);
  expect(screen.getByRole("button", { name: "Export ALLIN1 ZIP" })).toBeDisabled();
  expect(screen.getAllByText("x64/american.rpf!global.gxt2").length).toBeGreaterThan(0);
  expect(screen.getByText(/Schema 4 · exact nested replacement only/)).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: /schema-4 compatibility/ }));
  await user.click(screen.getByRole("button", { name: "Export ALLIN1 ZIP" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Exact member: x64/american.rpf!global.gxt2");
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ publication_mode: "member", authoring_confirmed: true }));
});

it("requires a fresh confirmation when switching export scope and preserves it after stale failure", async () => {
  const { user, apply } = await setup(true);
  await review(user);
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Back to export settings" }));
  await user.selectOptions(screen.getByLabelText("Export scope"), "member");
  await review(user);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  apply.mockRejectedValueOnce(new Error("Member build changed"));
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Export ALLIN1 ZIP" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Member build changed");
  expect(screen.getByLabelText("Export scope")).toHaveValue("member");
  await review(user);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
});

it.each(["entry", "original_sha256", "manifest_schema_version", "publication_mode"] as const)("rejects mismatched member review %s", async field => {
  const { user, client, apply } = await setup(true);
  await user.selectOptions(screen.getByLabelText("Export scope"), "member");
  const original = client.startJob.bind(client);
  vi.spyOn(client, "startJob").mockImplementation((op, payload, revision, event) => original(op, payload, revision, message => {
    if (message.operation === "result" && message.terminal) {
      Object.assign((message.payload.result as Gxt2Review).rpf_publication!, { [field]: field === "manifest_schema_version" ? 1 : "wrong" });
    }
    event(message);
  }));
  await user.click(screen.getByRole("button", { name: "Review ALLIN1 ZIP" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Unexpected GXT2 review");
  expect(apply).not.toHaveBeenCalled();
});
