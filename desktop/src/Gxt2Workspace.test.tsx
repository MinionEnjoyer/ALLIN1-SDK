import { act, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import App from "./App";
import Gxt2Workspace, { type Gxt2ArchiveRequest, type Gxt2Session } from "./Gxt2Workspace";
import { createPreviewClient } from "./previewClient";
import { gxt2PreviewSession, gxt2PreviewReview } from "./gxt2Preview";
import type { Envelope, JobStart } from "./types";

const workspace = "C:\\SDK\\workspaces\\game-text";
const response = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "gxt2-test", job_id: "gxt2-job", operation: "result", sequence: 1, risk: "read_only", terminal: true, payload: { result } });
function setup() {
  const client = createPreviewClient("rpf");
  let current = gxt2PreviewSession({ workspace });
  const originalStart = client.startJob.bind(client);
  client.startJob = (op, payload, revision, event) => originalStart(op, payload, revision, message => {
    if (op === "review_gxt2_action" && payload.workspace && message.operation === "result" && message.terminal) {
      const value = message.payload.result as Record<string, unknown>;
      value.revision = current.revision;
      if (["edit", "remove"].includes(String(payload.action))) value.before = current.selected?.text ?? "";
    }
    event(message);
  });
  const applied = (payload: Record<string, unknown>) => {
    const session = gxt2PreviewSession({ workspace: payload.destination ?? workspace });
    if (payload.text !== undefined) {
      session.selected!.text = String(payload.text);
      session.entries[0].preview = String(payload.text);
    }
    session.revision = payload.action === "create" ? 0 : current.revision + 1;
    current = session;
    return response({ kind: "gxt2_applied", action: payload.action, session, review_sha256: payload.review_sha256,
      file_write_performed: true, game_write_performed: false });
  };
  const guard = vi.fn();
  const apply = vi.spyOn(client, "applyGxt2Action");
  render(<Gxt2Workspace client={client} onGuardChange={guard} />);
  return { client, apply, applied, guard, user: userEvent.setup() };
}
async function open(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Open text workspace" }));
  await screen.findByDisplayValue("KRISS Vector");
}

it("inspects a loose source read-only and requires reviewed confirmation to create a copy", async () => {
  const { user, client, apply, applied } = setup();
  const start = vi.spyOn(client, "startJob");
  apply.mockImplementation(async payload => applied(payload));
  await user.click(screen.getByRole("button", { name: "Open GXT2" }));
  expect(await screen.findByLabelText("Game text")).toHaveAttribute("readonly");
  await user.click(screen.getByRole("button", { name: "Create editable copy" }));
  await screen.findByRole("heading", { name: "Review: Create editable copy" });
  expect(start).toHaveBeenCalledWith("review_gxt2_action", expect.objectContaining({ action: "create" }), expect.any(String), expect.any(Function));
  expect(apply).not.toHaveBeenCalled();
  const buttons = screen.getAllByRole("button", { name: "Create editable copy" });
  expect(buttons.at(-1)).toBeDisabled();
  await user.click(screen.getByRole("checkbox"));
  await user.click(buttons.at(-1)!);
  await waitFor(() => expect(screen.getByLabelText("Game text")).not.toHaveAttribute("readonly"));
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ authoring_confirmed: true, review_sha256: "c".repeat(64) }));
});

it("reviews Unicode text, guards navigation and saves exactly the confirmed draft", async () => {
  const { user, guard, apply, applied } = setup();
  apply.mockImplementation(async payload => applied(payload));
  await open(user);
  await user.clear(screen.getByLabelText("Game text"));
  await user.type(screen.getByLabelText("Game text"), "Vector — 日本語");
  expect(guard).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole("button", { name: "Open GXT2" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Review text change" }));
  await screen.findByRole("heading", { name: "Review: Save text" });
  expect(screen.getByRole("button", { name: "Save text" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Save text" }));
  await waitFor(() => expect(guard).toHaveBeenLastCalledWith(false));
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ label_hash: 256, text: "Vector — 日本語", action: "edit" }));
});

it("adds and removes labels through separate explicit reviews", async () => {
  const { user, apply, applied } = setup();
  apply.mockImplementation(async payload => applied(payload));
  await open(user);
  await user.click(screen.getByRole("button", { name: "New label" }));
  await user.type(screen.getByLabelText("Label hash"), "0x500");
  await user.type(screen.getByLabelText("Game text"), "New label text");
  await user.click(screen.getByRole("button", { name: "Review text change" }));
  await screen.findByRole("heading", { name: "Review: Add label" });
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Add label" }));
  await user.click(await screen.findByRole("button", { name: "Review removal" }));
  await screen.findByRole("heading", { name: "Review: Remove label" });
  expect(screen.getByText(/Existing references may need updating/)).toBeInTheDocument();
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(apply).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Remove label" }));
  await waitFor(() => expect(apply).toHaveBeenCalledTimes(2));
});

it("reviews undo and exports a hash-verified new dictionary", async () => {
  const { user, apply, applied } = setup();
  apply.mockImplementation(async payload => payload.action === "build" ? response({ kind: "gxt2_built", archive: payload.destination,
    report: `${payload.destination}.gxt2-validation.json`, sha256: "d".repeat(64), review_sha256: payload.review_sha256,
    file_write_performed: true, game_write_performed: false }) : applied(payload));
  await open(user);
  await user.click(screen.getByRole("button", { name: "Review undo" }));
  await screen.findByRole("heading", { name: "Review: Undo last operation" });
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Undo last operation" }));
  await user.click(await screen.findByRole("button", { name: "Review GXT2 build" }));
  await screen.findByRole("heading", { name: "Review: Build dictionary" });
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Build dictionary" }));
  expect(await screen.findByRole("status")).toHaveTextContent(`SHA-256: ${"d".repeat(64)}`);
});

it("preserves unsaved text after a stale apply and invalidates confirmation", async () => {
  const { user, apply } = setup();
  apply.mockRejectedValueOnce(new Error("GXT2 state changed"));
  await open(user);
  await user.type(screen.getByLabelText("Game text"), " custom");
  await user.click(screen.getByRole("button", { name: "Review text change" }));
  await screen.findByRole("heading", { name: "Review: Save text" });
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Save text" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("state changed");
  expect(screen.getByLabelText("Game text")).toHaveValue("KRISS Vector custom");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Reset text draft" }));
  expect(screen.getByLabelText("Game text")).toHaveValue("KRISS Vector");
});

it("cancels late-starting read jobs and ignores stale results", async () => {
  const { client, user } = setup();
  let accepted!: (value: JobStart) => void;
  let event!: (message: Envelope) => void;
  vi.spyOn(client, "startJob").mockImplementation(async (_op, _data, _rev, callback) => {
    event = callback;
    return new Promise(resolve => { accepted = resolve; });
  });
  const cancel = vi.spyOn(client, "cancelJob");
  await user.click(screen.getByRole("button", { name: "Open text workspace" }));
  await user.click(screen.getByRole("button", { name: "Cancel text review" }));
  await act(async () => accepted({ job_id: "late", accepted: response({}) }));
  expect(cancel).toHaveBeenCalledWith("late");
  act(() => event(response(gxt2PreviewSession({ workspace }))));
  expect(screen.queryByLabelText("Game text")).not.toBeInTheDocument();
});

it("does no work when the native picker is cancelled", async () => {
  const { user, client, guard } = setup();
  vi.spyOn(client, "selectPath").mockResolvedValue(null);
  const start = vi.spyOn(client, "startJob");
  await user.click(screen.getByRole("button", { name: "Open GXT2" }));
  expect(start).not.toHaveBeenCalled();
  expect(guard).toHaveBeenLastCalledWith(false);
});

it("handles early terminal results and refuses malformed review hashes", async () => {
  const { user, client, apply } = setup();
  vi.spyOn(client, "startJob").mockImplementation(async (op, payload, _revision, event) => {
    event(response(op === "inspect_gxt2_workspace" ? gxt2PreviewSession(payload) : { ...gxt2PreviewReview(payload), review_sha256: "invalid" }));
    return { job_id: "early", accepted: response({}) };
  });
  await open(user);
  await user.type(screen.getByLabelText("Game text"), " changed");
  await user.click(screen.getByRole("button", { name: "Review text change" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Unexpected GXT2 review");
  expect(apply).not.toHaveBeenCalled();
});

it("prevents double submission and hides cancellation while writing", async () => {
  const { user, apply } = setup();
  let finish!: (message: Envelope) => void;
  apply.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await open(user);
  await user.type(screen.getByLabelText("Game text"), " changed");
  await user.click(screen.getByRole("button", { name: "Review text change" }));
  await screen.findByRole("heading", { name: "Review: Save text" });
  await user.click(screen.getByRole("checkbox"));
  await user.dblClick(screen.getByRole("button", { name: "Save text" }));
  expect(apply).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Back to text" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Cancel text review" })).not.toBeInTheDocument();
  await act(async () => finish({ ...response({}), operation: "error", payload: { message: "Fixture stopped" } }));
});

it("guards RPF tabs, sidebar, keyboard back and direct-open while text is dirty", async () => {
  const client = createPreviewClient("rpf");
  const user = userEvent.setup();
  let launch!: Parameters<typeof client.onLaunchRequest>[0];
  vi.spyOn(client, "onLaunchRequest").mockImplementation(async handler => { launch = handler; return () => {}; });
  render(<App client={client} />);
  await screen.findByText("Recursive index ready");
  const textTab = await screen.findByRole("tab", { name: "GXT2 game text" });
  await waitFor(() => expect(textTab).toBeEnabled());
  await user.click(textTab);
  await open(user);
  await user.type(screen.getByLabelText("Game text"), " draft");
  expect(screen.getByRole("tab", { name: "Archive inspection" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: /Package Linker/ }));
  expect(screen.getByRole("heading", { name: "GXT2 text editor" })).toBeInTheDocument();
  await user.keyboard("{Alt>}{ArrowLeft}{/Alt}");
  expect(screen.getByLabelText("Game text")).toHaveValue("KRISS Vector draft");
  act(() => launch({ workspace: "linker", source: "C:\\other", selection: null, category: null, warning: null }));
  expect(screen.getByRole("alert")).toHaveTextContent("before opening another source");
  await user.click(screen.getByRole("button", { name: "Reset text draft" }));
  await user.click(screen.getByRole("button", { name: /Package Linker/ }));
  expect(await screen.findByRole("heading", { name: "Package Linker" })).toBeInTheDocument();
});

const archiveRequest: Gxt2ArchiveRequest = {
  archive: "C:\\SDK\\texts.rpf", entry_id: "x64/american.rpf::text/global.gxt2", gta_path: "C:\\Games\\Enhanced", requestId: 1,
};

it("hands the exact indexed GXT2 member to the editor and retains both tab selections", async () => {
  const client = createPreviewClient("rpf"), user = userEvent.setup();
  const start = vi.spyOn(client, "startJob"), apply = vi.spyOn(client, "applyGxt2Action");
  render(<App client={client} />);
  await screen.findByText("Recursive index ready");
  await user.click(screen.getByRole("button", { name: /text\/global.gxt2/ }));
  const handoff = await screen.findByRole("button", { name: "Open in text editor" });
  await waitFor(() => expect(handoff).toBeEnabled());
  await user.click(handoff);
  const editor = await screen.findByRole("textbox", { name: "Game text" });
  expect(editor).toHaveAttribute("readonly");
  expect(start).toHaveBeenCalledWith("inspect_gxt2_workspace", {
    archive: "C:\\Games\\Grand Theft Auto V Enhanced\\mods\\update\\update.rpf",
    gta_path: "C:\\Games\\Grand Theft Auto V Enhanced", entry_id: "x64/data.rpf::text/global.gxt2",
  }, expect.any(String), expect.any(Function));
  expect(screen.getByRole("heading", { name: "Archive provenance" })).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Archive inspection" }));
  expect(screen.getByRole("button", { name: /text\/global.gxt2/ })).toHaveAttribute("aria-pressed", "true");
  await user.click(screen.getByRole("tab", { name: "GXT2 game text" }));
  expect(screen.getByRole("textbox", { name: "Game text" })).toHaveValue("KRISS Vector");
  expect(start.mock.calls.filter(call => call[0] === "inspect_gxt2_workspace")).toHaveLength(1);
  expect(apply).not.toHaveBeenCalled();
});

it("reviews archive-bound copying with a fresh confirmation and retains provenance after saving", async () => {
  const client = createPreviewClient("rpf"), user = userEvent.setup();
  const apply = vi.spyOn(client, "applyGxt2Action").mockImplementation(async payload => response({
    kind: "gxt2_applied", action: "create", review_sha256: payload.review_sha256, file_write_performed: true, game_write_performed: false,
    session: { ...gxt2PreviewSession({ ...archiveRequest }), workspace: String(payload.destination), source: String(payload.destination), revision: 0, can_undo: false },
  }));
  render(<StrictMode><Gxt2Workspace client={client} archiveRequest={archiveRequest} onGuardChange={vi.fn()} /></StrictMode>);
  await screen.findByRole("textbox", { name: "Game text" });
  await user.click(screen.getByRole("button", { name: "Create editable copy" }));
  const review = await screen.findByRole("region", { name: "GXT2 action review" });
  expect(within(review).getByText(archiveRequest.entry_id)).toBeInTheDocument();
  expect(apply).not.toHaveBeenCalled();
  await user.click(within(review).getByRole("checkbox"));
  await user.click(within(review).getByRole("button", { name: "Create editable copy" }));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Game text" })).not.toHaveAttribute("readonly"));
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ archive: archiveRequest.archive, entry_id: archiveRequest.entry_id, authoring_confirmed: true }));
  expect(screen.getByText(/This workspace is an independent copy/)).toBeInTheDocument();
});

it("rejects mismatched archive member evidence instead of opening an unrelated dictionary", async () => {
  const client = createPreviewClient("rpf");
  vi.spyOn(client, "startJob").mockImplementation(async (_op, payload, _revision, event) => {
    event(response(gxt2PreviewSession({ ...payload, entry_id: "x64/french.rpf::text/global.gxt2" })));
    return { job_id: "early", accepted: response({}) };
  });
  render(<Gxt2Workspace client={client} archiveRequest={archiveRequest} onGuardChange={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("does not match the selected member");
  expect(screen.queryByRole("textbox", { name: "Game text" })).not.toBeInTheDocument();
});

it("cancels an archive intake with late job acceptance and ignores its result", async () => {
  const client = createPreviewClient("rpf"), user = userEvent.setup();
  let accepted!: (job: JobStart) => void, terminal!: (message: Envelope) => void;
  vi.spyOn(client, "startJob").mockImplementation((_op, _payload, _revision, event) => {
    terminal = event;
    return new Promise(resolve => { accepted = resolve; });
  });
  const cancel = vi.spyOn(client, "cancelJob");
  render(<Gxt2Workspace client={client} archiveRequest={archiveRequest} onGuardChange={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: "Cancel text review" }));
  act(() => terminal(response(gxt2PreviewSession({ ...archiveRequest }))));
  await act(async () => accepted({ job_id: "late-archive", accepted: response({}) }));
  expect(cancel).toHaveBeenCalledWith("late-archive");
  expect(screen.queryByRole("textbox", { name: "Game text" })).not.toBeInTheDocument();
});

it("preserves the current draft when a new archive handoff is attempted", async () => {
  const client = createPreviewClient("rpf"), user = userEvent.setup(), guard = vi.fn();
  const view = render(<Gxt2Workspace client={client} onGuardChange={guard} />);
  await open(user);
  await user.type(screen.getByLabelText("Game text"), " unsaved");
  const start = vi.spyOn(client, "startJob");
  view.rerender(<Gxt2Workspace client={client} onGuardChange={guard} archiveRequest={archiveRequest} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Finish or reset");
  expect(screen.getByLabelText("Game text")).toHaveValue("KRISS Vector unsaved");
  expect(start).not.toHaveBeenCalled();
});

it("rejects review evidence for a different archive and never enables copying", async () => {
  const client = createPreviewClient("rpf"), user = userEvent.setup();
  const original = client.startJob.bind(client);
  vi.spyOn(client, "startJob").mockImplementation((op, payload, revision, event) => original(op, payload, revision, message => {
    if (op === "review_gxt2_action" && message.terminal && message.operation === "result") {
      const value = message.payload.result as Gxt2Session;
      value.source_binding!.outer_archive_sha256 = "f".repeat(64);
    }
    event(message);
  }));
  const apply = vi.spyOn(client, "applyGxt2Action");
  render(<Gxt2Workspace client={client} archiveRequest={archiveRequest} onGuardChange={vi.fn()} />);
  await screen.findByRole("textbox", { name: "Game text" });
  await user.click(screen.getByRole("button", { name: "Create editable copy" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Unexpected GXT2 review");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(apply).not.toHaveBeenCalled();
});
