import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi } from "vitest";
import PedWorkbench from "./PedWorkbench";
import { createPreviewClient } from "./previewClient";
import { pedPreviewReview, pedPreviewSnapshot } from "./pedPreview";
import type { Envelope } from "./types";
import App from "./App";

const response = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "ped-test", job_id: null,
  operation: "result", sequence: 0, risk: "read_only", terminal: true, payload: { result } });
async function editable() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  await screen.findByText("Editable copy · revision 0");
  return user;
}

it("inspects definitions, filters the catalog and reverses both pane toggles", async () => {
  const client = createPreviewClient("peds");
  render(<PedWorkbench client={client} onDirtyChange={vi.fn()} initialSource="C:/SDK/peds/demo" />);
  await screen.findByRole("heading", { name: "ig_demo" });
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Filter peds"), "neighbor");
  expect(within(screen.getByRole("region", { name: "Ped catalog" })).queryByRole("button", { name: /ig_demo PERSON/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Collapse ped catalog" }));
  expect(screen.getByRole("button", { name: "Expand ped catalog" })).toHaveTextContent("›");
  await user.click(screen.getByRole("button", { name: "Expand ped catalog" }));
  expect(screen.getByLabelText("Filter peds")).toHaveValue("neighbor");
  await user.click(screen.getByRole("button", { name: "Collapse ped integration" }));
  expect(screen.getByRole("button", { name: "Expand ped integration" })).toHaveTextContent("‹");
});

it("reviews a copied edit, guards navigation and requires action-time confirmation", async () => {
  const client = createPreviewClient("peds");
  const dirty = vi.fn();
  const start = vi.spyOn(client, "startJob");
  const saved = pedPreviewSnapshot("C:/SDK/workspaces/ped-copy");
  saved.revision = 1; saved.values!["ped.modelType"] = "animal";
  const apply = vi.spyOn(client, "applyPedAuthoring").mockResolvedValue(response(saved));
  render(<PedWorkbench client={client} onDirtyChange={dirty} />);
  const user = await editable();
  await user.selectOptions(screen.getByLabelText("Ped section"), "author");
  await user.clear(screen.getByLabelText("Model type")); await user.type(screen.getByLabelText("Model type"), "animal");
  expect(screen.getByRole("button", { name: "Open ped folder" })).toBeDisabled();
  expect(screen.getByLabelText("Ped section")).toBeDisabled();
  expect(dirty).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole("button", { name: "Review field changes" }));
  expect(await screen.findByRole("button", { name: "Apply reviewed action" })).toBeDisabled();
  expect(start).toHaveBeenLastCalledWith("review_ped_authoring", expect.objectContaining({
    expected_state_sha256: "a".repeat(64), updates: { "ped.modelType": "animal" } }), expect.any(String), expect.any(Function));
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByLabelText("I confirm this exact copied-workspace action."));
  await user.click(screen.getByRole("button", { name: "Apply reviewed action" }));
  await screen.findByText("Editable copy · revision 1");
  expect(screen.getByLabelText("Model type")).toHaveValue("animal");
  expect(apply).toHaveBeenCalledTimes(1);
});

it("does not enable absent XML nodes or ambiguous authoring targets", async () => {
  const client = createPreviewClient("peds");
  const snapshot = pedPreviewSnapshot("C:/SDK/copy");
  snapshot.editable_fields = snapshot.editable_fields.filter(field => field !== "ped.expressionSet");
  vi.spyOn(client, "startJob").mockImplementation(async (_op, _payload, _revision, event) => { event(response(snapshot)); return { job_id: "early", accepted: response({}) }; });
  render(<PedWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = await editable();
  await user.selectOptions(screen.getByLabelText("Ped section"), "author");
  expect(screen.getByLabelText(/Expression set/)).toBeDisabled();
  expect(screen.getByLabelText("Model type")).toBeEnabled();
});

it("shows the exact rename paths before migration", async () => {
  const client = createPreviewClient("peds");
  const start = vi.spyOn(client, "startJob");
  render(<PedWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = await editable();
  await user.selectOptions(screen.getByLabelText("Ped section"), "identity");
  await user.type(screen.getByLabelText("New model identity"), "ig_new");
  await user.click(screen.getByRole("button", { name: "Review identity migration" }));
  await screen.findByRole("heading", { name: "Review identity migration" });
  expect(screen.getByText("stream/ig_demo.ydd → stream/ig_new.ydd")).toBeInTheDocument();
  expect(start).toHaveBeenLastCalledWith("review_ped_authoring", expect.objectContaining({ action: "migrate", new_name: "ig_new", new_props: null }), expect.any(String), expect.any(Function));
});

it("keeps incomplete clone plans blocked and invalidates review when returning to edit", async () => {
  const client = createPreviewClient("peds");
  const apply = vi.spyOn(client, "applyPedAuthoring");
  render(<PedWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = await editable();
  await user.selectOptions(screen.getByLabelText("Ped section"), "clone");
  await user.type(screen.getByLabelText("New model identity"), "ig_new");
  await user.click(screen.getByRole("button", { name: "Review ped clone" }));
  await screen.findByText("Clone blocked");
  expect(screen.getByRole("button", { name: "Apply reviewed action" })).toBeDisabled();
  expect(screen.getByLabelText("I confirm this exact copied-workspace action.")).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Back to editing" }));
  expect(screen.queryByRole("region", { name: "Ped action review" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("New model identity")).toHaveValue("ig_new");
  expect(apply).not.toHaveBeenCalled();
});

it("adopts a validated clone and prevents double submission", async () => {
  const client = createPreviewClient("peds");
  const snapshot = pedPreviewSnapshot("C:/SDK/copy");
  vi.spyOn(client, "startJob").mockImplementation(async (op, payload, _revision, event) => {
    if (op === "inspect_ped_workbench") event(response(snapshot));
    else { const review = pedPreviewReview(payload); review.clone_plan!.ready = true; review.clone_plan!.findings = []; event(response(review)); }
    return { job_id: "early", accepted: response({}) };
  });
  let finish!: (value: Envelope) => void;
  const apply = vi.spyOn(client, "applyPedAuthoring").mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<PedWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = await editable();
  await user.selectOptions(screen.getByLabelText("Ped section"), "clone");
  await user.type(screen.getByLabelText("New model identity"), "ig_new");
  await user.click(screen.getByRole("button", { name: "Review ped clone" }));
  await user.click(screen.getByLabelText("I confirm this exact copied-workspace action."));
  fireEvent.click(screen.getByRole("button", { name: "Apply reviewed action" }));
  fireEvent.click(screen.getByRole("button", { name: "Apply reviewed action" }));
  expect(apply).toHaveBeenCalledTimes(1);
  snapshot.revision = 1;
  finish(response(snapshot));
  await screen.findByText("Editable copy · revision 1");
});

it("ignores cancelled jobs, including a terminal result that arrives late", async () => {
  const client = createPreviewClient("peds");
  let event!: (message: Envelope) => void;
  vi.spyOn(client, "startJob").mockImplementation(async (_op, _payload, _revision, cb) => { event = cb; return { job_id: "waiting", accepted: response({}) }; });
  const cancel = vi.spyOn(client, "cancelJob");
  render(<PedWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open ped folder" }));
  await user.click(await screen.findByRole("button", { name: "Cancel inspection" }));
  event(response(pedPreviewSnapshot()));
  expect(screen.queryByRole("heading", { name: "ig_demo" })).not.toBeInTheDocument();
  expect(cancel).toHaveBeenCalledWith("waiting");
});

it("drops failed reviews but preserves the edit for recovery", async () => {
  const client = createPreviewClient("peds");
  vi.spyOn(client, "applyPedAuthoring").mockResolvedValue({ ...response(null), operation: "error", payload: { message: "Ped snapshot changed; refresh" } });
  render(<PedWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = await editable();
  await user.selectOptions(screen.getByLabelText("Ped section"), "author");
  await user.clear(screen.getByLabelText("Model type")); await user.type(screen.getByLabelText("Model type"), "animal");
  await user.click(screen.getByRole("button", { name: "Review field changes" }));
  await user.click(await screen.findByLabelText("I confirm this exact copied-workspace action."));
  await user.click(screen.getByRole("button", { name: "Apply reviewed action" }));
  await screen.findByRole("alert");
  expect(screen.queryByRole("region", { name: "Ped action review" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Model type")).toHaveValue("animal");
});

it("requires an explicit preview edition, reads exact model and texture sequentially, and reports missing images honestly", async () => {
  const client = createPreviewClient("peds");
  const start = vi.spyOn(client, "startJob").mockImplementation(async (op, payload, _revision, event) => {
    event(response(op === "inspect_ped_workbench" ? pedPreviewSnapshot() : {
      path: payload.entry, source: payload.source, display_kind: "metadata", warnings: ["Synthetic asset is not renderable"],
      bytes_read: 12, size: 12, sha256: "a".repeat(64), metadata: { decoded: false }, artifact: null,
    }));
    return { job_id: "early", accepted: response({}) };
  });
  render(<PedWorkbench client={client} onDirtyChange={vi.fn()} initialSource="C:/SDK/peds" />);
  await screen.findByRole("heading", { name: "ig_demo" });
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("Ped section"), "preview");
  expect(start.mock.calls.filter(call => call[0] === "preview_asset")).toHaveLength(0);
  await user.selectOptions(screen.getByLabelText("Decoder edition"), "enhanced");
  await waitFor(() => expect(start.mock.calls.filter(call => call[0] === "preview_asset")).toHaveLength(2));
  expect(start.mock.calls.filter(call => call[0] === "preview_asset").map(call => call[1].entry)).toEqual(["stream/ig_demo.ydd", "stream/ig_demo.ytd"]);
  expect(screen.getAllByText("No renderable image was produced. See the decoder evidence below.")).toHaveLength(2);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("scopes search shortcuts and retains drafts when the shell navigation is blocked", async () => {
  const client = createPreviewClient("workbench");
  vi.spyOn(client, "initialLaunchRequest").mockResolvedValue({ workspace: "workbench", source: "C:/SDK/peds/demo", category: "peds", selection: null, warning: null });
  render(<App client={client} />);
  const user = userEvent.setup();
  await screen.findByRole("heading", { name: "Ped Workbench" });
  await screen.findByRole("heading", { name: "ig_demo" });
  await user.click(screen.getByLabelText("Filter peds"));
  await user.type(screen.getByLabelText("Filter peds"), "neighbor");
  await user.keyboard("{Escape}");
  expect(screen.getByLabelText("Filter peds")).toHaveValue("");
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  await screen.findByText("Editable copy · revision 0");
  await user.selectOptions(screen.getByLabelText("Ped section"), "author");
  await user.clear(screen.getByLabelText("Model type")); await user.type(screen.getByLabelText("Model type"), "animal");
  await user.click(screen.getByRole("tab", { name: /Weapons/ }));
  expect(screen.getByLabelText("Model type")).toHaveValue("animal");
  expect(screen.getByRole("alert")).toHaveTextContent("Finish the current authoring action");
  await user.click(screen.getByRole("button", { name: "Discard unapplied changes" }));
  await user.click(screen.getByRole("tab", { name: /Weapons/ }));
  expect(await screen.findByRole("heading", { name: "Weapon Workbench" })).toBeInTheDocument();
});

it("reviews workspace copying before permitting creation", async () => {
  const client = createPreviewClient("peds");
  const apply = vi.spyOn(client, "applyPedAuthoring");
  render(<PedWorkbench client={client} onDirtyChange={vi.fn()} initialSource="C:/SDK/peds/demo" />);
  await screen.findByRole("heading", { name: "ig_demo" });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Create editable copy" }));
  await screen.findByRole("heading", { name: "Review create" });
  expect(screen.getByText("→ C:/SDK/workspaces/ped-workspace")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Apply reviewed action" })).toBeDisabled();
  expect(apply).not.toHaveBeenCalled();
});

it("does not silently choose one of multiple same-name model assets", async () => {
  const client = createPreviewClient("peds");
  const snapshot = pedPreviewSnapshot();
  snapshot.assets.push({ ...snapshot.assets[0], path: "second/ig_demo.ydd" });
  const start = vi.spyOn(client, "startJob").mockImplementation(async (_op, _payload, _revision, event) => { event(response(snapshot)); return { job_id: "early", accepted: response({}) }; });
  render(<PedWorkbench client={client} onDirtyChange={vi.fn()} initialSource="C:/SDK/peds" />);
  await screen.findByRole("heading", { name: "ig_demo" });
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("Ped section"), "preview");
  expect(screen.getByLabelText("Exact model asset")).toHaveValue("");
  expect(screen.getByText("Multiple candidates remain separate. Choose an exact package path above.")).toBeInTheDocument();
  expect(start.mock.calls.filter(call => call[0] === "preview_asset")).toHaveLength(0);
});
