import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import Gxt2Workspace, { type Gxt2Review } from "./Gxt2Workspace";
import { createPreviewClient } from "./previewClient";
import { gxt2PreviewSession } from "./gxt2Preview";
import type { Envelope, JobStart } from "./types";

const response = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "package-test", job_id: null,
  operation: "result", sequence: 1, risk: "authoring_write", terminal: true, payload: { result } });
async function setup(mode = "rpf_package") {
  const client = createPreviewClient(mode), user = userEvent.setup(), guard = vi.fn();
  const apply = vi.spyOn(client, "applyGxt2Action");
  render(<Gxt2Workspace client={client} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Open text workspace" }));
  await screen.findByRole("textbox", { name: "Game text" });
  const result = (payload: Record<string, unknown>) => ({ kind: "gxt2_rpf_packaged", destination: payload.destination,
    archive: `${payload.destination}/archive/text-fixture.rpf`, sha256: "9".repeat(64), report: `${payload.destination}/rpf-package.json`,
    report_sha256: "8".repeat(64), payload_sha256: "d".repeat(64), verified_payloads: 4,
    source_binding: gxt2PreviewSession({ archive_workspace: true }).source_binding,
    review_sha256: payload.review_sha256, file_write_performed: true, game_write_performed: false });
  return { client, user, guard, apply, result };
}
async function review(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Review RPF package" }));
  await screen.findByRole("heading", { name: "Review: Build RPF package" });
}

it("reviews archive output and requires separate confirmation before packaging", async () => {
  const { client, user, apply, result, guard } = await setup();
  const select = vi.spyOn(client, "selectPath");
  apply.mockImplementation(async payload => response(result(payload)));
  await review(user);
  expect(select).toHaveBeenCalledWith("rpf_package_parent");
  expect(screen.getByText("archive/text-fixture.rpf")).toBeInTheDocument();
  expect(screen.getByText(/GTA V must remain closed/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Build RPF package" })).toBeDisabled();
  expect(guard).toHaveBeenLastCalledWith(true);
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Build RPF package" }));
  expect(await screen.findByRole("status")).toHaveTextContent("RPF package built and verified");
  expect(screen.getByRole("status")).toHaveTextContent("not an installable ALLIN1 package");
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ action: "package_rpf", authoring_confirmed: true }));
  expect(guard).toHaveBeenLastCalledWith(false);
});

it("explains the archive binding requirement for loose dictionaries", async () => {
  await setup("rpf");
  expect(screen.getByText(/archive binding required for packaging/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review RPF package" })).not.toBeInTheDocument();
});

it("blocks packaging unsaved text and recovers after a cancelled destination picker", async () => {
  const { user, client, guard } = await setup();
  await user.type(screen.getByRole("textbox", { name: "Game text" }), " unsaved");
  expect(screen.getByRole("button", { name: "Review RPF package" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Reset text draft" }));
  vi.spyOn(client, "selectPath").mockResolvedValueOnce(null);
  await user.click(screen.getByRole("button", { name: "Review RPF package" }));
  await waitFor(() => expect(guard).toHaveBeenLastCalledWith(false));
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
});

it("invalidates a failed package review without discarding saved text or output name", async () => {
  const { user, apply } = await setup();
  apply.mockRejectedValueOnce(new Error("Original RPF changed"));
  await user.clear(screen.getByLabelText("RPF package folder name"));
  await user.type(screen.getByLabelText("RPF package folder name"), "custom-rpf");
  await review(user);
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Build RPF package" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Original RPF changed");
  expect(screen.getByRole("textbox", { name: "Game text" })).toHaveValue("KRISS Vector");
  expect(screen.getByLabelText("RPF package folder name")).toHaveValue("custom-rpf");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  await review(user);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
});

it("rejects malformed package evidence before enabling confirmation", async () => {
  const { client, user, apply } = await setup();
  const original = client.startJob.bind(client);
  vi.spyOn(client, "startJob").mockImplementation((op, payload, revision, event) => original(op, payload, revision, message => {
    if (op === "review_gxt2_action" && message.terminal && message.operation === "result") {
      (message.payload.result as Gxt2Review).rpf_package!.entry_id = "other.rpf::global.gxt2";
    }
    event(message);
  }));
  await user.click(screen.getByRole("button", { name: "Review RPF package" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Unexpected GXT2 review");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(apply).not.toHaveBeenCalled();
});

it("cancels late package reviews and ignores stale completion", async () => {
  const { client, user } = await setup();
  let accept!: (start: JobStart) => void, terminal!: (message: Envelope) => void;
  vi.spyOn(client, "startJob").mockImplementation((_op, _payload, _revision, event) => {
    terminal = event;
    return new Promise(resolve => { accept = resolve; });
  });
  const cancel = vi.spyOn(client, "cancelJob");
  await user.click(screen.getByRole("button", { name: "Review RPF package" }));
  await user.click(await screen.findByRole("button", { name: "Cancel text review" }));
  act(() => terminal(response({})));
  await act(async () => accept({ job_id: "late-package", accepted: response({}) }));
  expect(cancel).toHaveBeenCalledWith("late-package");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
});

it("prevents double package submission and rejects mismatched output evidence", async () => {
  const { user, apply, result } = await setup();
  let finish!: (message: Envelope) => void;
  apply.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await review(user);
  await user.click(screen.getByRole("checkbox"));
  await user.dblClick(screen.getByRole("button", { name: "Build RPF package" }));
  expect(apply).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Back to text" })).toBeDisabled();
  expect(within(screen.getByRole("region", { name: "GXT2 action review" })).queryByRole("button", { name: /Cancel/ })).not.toBeInTheDocument();
  await act(async () => finish(response({ ...result(apply.mock.calls[0][0]), payload_sha256: "0".repeat(64) })));
  expect(await screen.findByRole("alert")).toHaveTextContent("RPF package outcome could not be verified");
});
