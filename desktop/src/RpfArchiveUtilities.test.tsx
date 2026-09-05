import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import RpfArchiveUtilities from "./RpfArchiveUtilities";
import type { DesktopClient, Envelope, RpfArchiveResult } from "./types";

const envelope = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "r", job_id: "j", operation: "result", sequence: 1, risk: "read_only", terminal: true, payload: { result } });
const archive: RpfArchiveResult = { kind: "rpf_archive_index", operation: "inspect_rpf_archive", source: "C:\\work\\source.rpf", gta_path: "C:\\Games\\GTAV", edition: "Enhanced", archive_size: 4, archives: [], entries: [], warnings: [], suffix_counts: {}, archive_count: 1, entry_count: 1, returned_entry_count: 1, directory_count: 0, file_count: 1, logical_bytes: 4, stored_bytes: 4, truncated: false, read_only: true, game_write_performed: false };

it("reviews and confirms a full archive integrity report without authorizing a game write", async () => {
  const user = userEvent.setup(); let emit: (value: Envelope) => void = () => {};
  const client = {
    selectRpfUtilityDestination: vi.fn(async () => "C:\\work\\source-integrity.json"),
    startJob: vi.fn(async (_operation, _payload, _revision, onEvent) => { emit = onEvent; return { job_id: "job", accepted: envelope({}) }; }),
    applyRpfUtility: vi.fn(async () => envelope({ label: "Verify every recursive payload", game_write_performed: false })),
    cancelJob: vi.fn(async () => envelope({})), selectPath: vi.fn(),
  } as unknown as DesktopClient;
  const guard = vi.fn();
  render(<RpfArchiveUtilities client={client} result={archive} entry={null} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Verify integrity" }));
  expect(client.startJob).toHaveBeenCalledWith("review_rpf_utility", expect.objectContaining({ action: "verify_integrity", gta_path: archive.gta_path }), expect.any(String), expect.any(Function));
  await act(async () => emit(envelope({ action: "verify_integrity", label: "Verify every recursive payload", destination: "C:\\work\\source-integrity.json", archive: archive.source, archive_sha256: "a".repeat(64), review_sha256: "b".repeat(64), ready: true })));
  expect(screen.getByRole("dialog", { name: "Review RPF utility output" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Create reviewed output" }));
  expect(client.applyRpfUtility).toHaveBeenCalledWith(expect.objectContaining({ authoring_confirmed: true, review_sha256: "b".repeat(64) }));
  expect(await screen.findByText(/completed\. Source archive was not changed/)).toBeInTheDocument();
  expect(guard).toHaveBeenCalledWith(true);
});

it("offers exact member and subtree actions only for the matching selected entry kind", () => {
  const client = { cancelJob: vi.fn() } as unknown as DesktopClient;
  const { rerender } = render(<RpfArchiveUtilities client={client} result={archive} entry={{ id: "f", archive_path: "", path: "x.bin", name: "x.bin", kind: "binary", size: 4, stored_size: 4, encrypted: false, compressed: false, resource_version: null }} />);
  expect(screen.getByRole("button", { name: "Extract member" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Export subtree" })).toBeDisabled();
  rerender(<RpfArchiveUtilities client={client} result={archive} entry={{ id: "d", archive_path: "", path: "x", name: "x", kind: "directory", size: 0, stored_size: 0, encrypted: false, compressed: false, resource_version: null }} />);
  expect(screen.getByRole("button", { name: "Extract member" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Export subtree" })).toBeEnabled();
});
