import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import ArchiveBrowser from "./ArchiveBrowser";
import type { DesktopClient, Envelope } from "./types";

const location = { path: "", layer: "", directory: "" };
const response = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "r", job_id: "j", operation: "result", sequence: 1, risk: "read_only", terminal: true, payload: { result } });
const listing = (extra = {}) => ({ kind: "archive_browser", root: "C:/game", gta_path: "C:/game", location, parent: null,
  entries: [], offset: 0, page_size: 100, matched_count: 0, has_more: false, scan_complete: true, warnings: [], ...extra });
const entry = { id: "dlc.rpf::child.rpf::x.ymt", name: "x.ymt", path: "dlc.rpf::child.rpf::x.ymt", kind: "resource", size: 123,
  archive: "C:/game/dlc.rpf", source: "C:/game/dlc.rpf", entry_id: "child.rpf::x.ymt", origin: "source", location: null, edition: "Legacy" };
function setup() {
  let emit: (event: Envelope) => void = () => {};
  const client = { selectPath: vi.fn(async () => "C:/game"), cancelJob: vi.fn(async () => response({})),
    startJob: vi.fn(async (_operation, _payload, _revision, onEvent) => { emit = onEvent; return { job_id: "j", accepted: response({}) }; }) } as unknown as DesktopClient;
  const onOpen = vi.fn(), onStage = vi.fn(), onJob = vi.fn();
  const mounted = render(<ArchiveBrowser client={client} onOpen={onOpen} onStage={onStage} onJob={onJob} />);
  return { client, onOpen, onStage, onJob, ...mounted, emit: async (value: unknown) => act(async () => emit(response(value))) };
}
beforeEach(() => localStorage.clear());

it("prepares a native reference in its decoder root without silently starting a global scan", async () => {
  const user = userEvent.setup(), test = setup();
  test.rerender(<ArchiveBrowser client={test.client} onOpen={test.onOpen} onJob={test.onJob}
    referenceRequest={{ query: "prop_chair", gta_path: "C:/matching-game", requestId: 1 }} />);
  expect(screen.getByLabelText("Search files and RPF members")).toHaveValue("prop_chair");
  expect(screen.getByText(/Reference search prepared/)).toHaveAttribute("role", "status");
  expect(test.client.startJob).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Search root" }));
  expect(test.client.startJob).toHaveBeenLastCalledWith("search_game_files", expect.objectContaining({ root: "C:/matching-game", gta_path: "C:/matching-game", query: "prop_chair", scope: "all" }), expect.any(String), expect.any(Function));
});

it("captures the original layer and member path for a change-set handoff", async () => {
  const user = userEvent.setup(), test = setup();
  const target = { ...entry, archive_path: "child.rpf", member_path: "textures/x.ymt" };
  await user.click(screen.getByRole("button", { name: "Open GTA V" }));
  await test.emit(listing({ entries: [target], matched_count: 1 }));
  await user.click(screen.getByRole("button", { name: "x.ymt" })); await test.emit({ text: "preview" });
  await user.click(screen.getByRole("button", { name: "Stage selected archive member" }));
  expect(test.onStage).toHaveBeenCalledWith(target, "C:/game");
  expect(test.onOpen).not.toHaveBeenCalled();
});

it("preserves exact nested identity and edition for preview and editor handoff", async () => {
  const user = userEvent.setup(), test = setup();
  await user.click(screen.getByRole("button", { name: "Open GTA V" }));
  await test.emit(listing({ entries: [entry], matched_count: 1 }));
  await user.click(screen.getByRole("button", { name: "x.ymt" }));
  expect(test.client.startJob).toHaveBeenLastCalledWith("preview_asset", expect.objectContaining({ source: entry.archive, entry: entry.entry_id, edition: "Legacy" }), expect.any(String), expect.any(Function));
  await test.emit({ text: "native preview" });
  await user.click(screen.getByRole("button", { name: "Open native workspace" }));
  expect(test.onOpen).toHaveBeenCalledWith(entry, "C:/game");
});

it("pages the executed search, not an unsubmitted edited query", async () => {
  const user = userEvent.setup(), test = setup();
  await user.click(screen.getByRole("button", { name: "Open GTA V" })); await test.emit(listing());
  await user.type(screen.getByLabelText("Search files and RPF members"), "wheel");
  await user.click(screen.getByRole("button", { name: "Search root" }));
  await test.emit(listing({ query: "wheel", scope: "all", entries: [entry], matched_count: 101, has_more: true }));
  await user.clear(screen.getByLabelText("Search files and RPF members"));
  await user.type(screen.getByLabelText("Search files and RPF members"), "different");
  await user.selectOptions(screen.getByLabelText("Search scope"), "mods");
  await user.click(screen.getByRole("button", { name: "Next page" }));
  expect(test.client.startJob).toHaveBeenLastCalledWith("search_game_files", expect.objectContaining({ query: "wheel", scope: "all", offset: 100 }), expect.any(String), expect.any(Function));
});

it("persists favorites, reindexes decoder changes and ignores cancelled results", async () => {
  const user = userEvent.setup(), test = setup();
  await user.click(screen.getByRole("button", { name: "Open GTA V" })); await test.emit(listing());
  await user.click(screen.getByRole("button", { name: "Favorite" }));
  expect(JSON.parse(localStorage.getItem("allin1.sdk.archive-browser.v1")!).favorites).toHaveLength(1);
  vi.mocked(test.client.selectPath).mockResolvedValueOnce("C:/other-game");
  await user.click(screen.getByRole("button", { name: "Decoder installation" }));
  expect(test.client.startJob).toHaveBeenLastCalledWith("browse_game_files", expect.objectContaining({ gta_path: "C:/other-game" }), expect.any(String), expect.any(Function));
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await test.emit(listing({ entries: [entry] }));
  expect(screen.queryByRole("button", { name: "x.ymt" })).not.toBeInTheDocument();
  expect(test.client.cancelJob).toHaveBeenCalledWith("j");
});
