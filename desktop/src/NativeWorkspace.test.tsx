import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import NativeWorkspace from "./NativeWorkspace";
import type { DesktopClient, Envelope } from "./types";
import { createPreviewClient } from "./previewClient";
import animationFixture from "./nativeAnimationFixture.json";
import animationModel from "./nativeAnimationModelFixture.json";

vi.mock("./CodeEditor", () => ({ default: ({ value, onChange, locked }: { value: string; onChange: (value: string) => void; locked: boolean }) =>
  <textarea aria-label="Native XML" value={value} onChange={event => onChange(event.target.value)} disabled={locked} /> }));
const envelope = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "r", job_id: "j", operation: "result", sequence: 1, risk: "read_only", terminal: true, payload: { result } });
const session = { kind: "workspace_session", module: "native", schema_version: 1, read_only: true, game_write_performed: false, state_sha256: "a".repeat(64),
  source: "C:/work/native", workspace: "C:/work/native", name: "track.awc", edition: "Legacy", gta_path: "C:/game", xml_chunks: ["<root/>"], xml_editable: true,
  dependencies: [{ path: "track.wav", kind: "audio", size: 40, sha256: "c".repeat(64) }], warnings: [] };
function setup(onSearchReference?: (query: string, game: string) => void) {
  let emit: (value: Envelope) => void = () => {};
  const client = { selectPath: vi.fn(async () => "C:/work/native"), cancelJob: vi.fn(async () => envelope({})),
    startJob: vi.fn(async (_op, _payload, _revision, callback) => { emit = callback; return { job_id: "j", accepted: envelope({}) }; }),
    applyWorkspaceAction: vi.fn(async () => envelope({ kind: "workspace_applied", module: "native", schema_version: 1, action: "save_xml", game_write_performed: false,
      review_sha256: "b".repeat(64), session: { ...session, xml_chunks: ["<changed/>"] } })),
  } as unknown as DesktopClient;
  const guard = vi.fn();
  const mounted = render(<NativeWorkspace client={client} onGuardChange={guard} onSearchReference={onSearchReference} />);
  return { client, guard, ...mounted, emit: async (value: unknown) => act(async () => emit(envelope(value))) };
}

it("renders the backend collision fixture through native inspection and refuses browser-only authoring", async () => {
  const user = userEvent.setup(), client = createPreviewClient("rpf", "ybn");
  render(<NativeWorkspace client={client} onGuardChange={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "Open native workspace" }));
  expect(screen.getByText(/Browser demonstration: generated YBN collision fixture/)).toBeInTheDocument();
  const svg = screen.getByRole("img", { name: "Native collision geometry" });
  expect(svg.querySelectorAll("polygon")).toHaveLength(421);
  await user.selectOptions(screen.getByLabelText("Collision group"), "0");
  expect(svg.querySelectorAll("polygon")).toHaveLength(12);
  await user.click(screen.getByLabelText("Face normals"));
  expect(svg.querySelectorAll("line[data-normal]")).toHaveLength(12);
  await user.click(screen.getByRole("button", { name: "Review native workspace export" }));
  expect(screen.getByText(/Actual native inspection, export and build require the desktop SDK/)).toBeInTheDocument();
});

it("selects exact YCD clips through read-only inspection and guards unsaved animation XML", async () => {
  const user = userEvent.setup(), test = setup();
  await user.click(screen.getByRole("button", { name: "Open native workspace" }));
  await test.emit({ ...session, name: "fixture.ycd", dependencies: [], animation: animationFixture });
  expect(screen.getByRole("img", { name: "Animation channel curves" })).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Animation selection"), "animation:22222222");
  expect(test.client.startJob).toHaveBeenLastCalledWith("inspect_authoring_workspace", expect.objectContaining({ module: "native", workspace: "C:/work/native", document: { animation: "animation:22222222" } }), expect.any(String), expect.any(Function));
  await test.emit({ ...session, name: "fixture.ycd", animation: animationFixture });
  await user.type(screen.getByLabelText("Native XML"), " ");
  expect(screen.getByLabelText("Animation selection")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Play channels" })).toBeDisabled();
});

it("binds a user-selected model XML, preserves it across clip selection and blocks dirty rebinding", async () => {
  const user=userEvent.setup(), test=setup();
  await user.click(screen.getByRole("button", {name:"Open native workspace"}));
  const animated={...session,name:"fixture.ycd",animation:animationFixture};
  await test.emit(animated);
  vi.mocked(test.client.selectPath).mockResolvedValue("C:/models/ped.ydr.xml");
  await user.click(screen.getByRole("button",{name:"Choose animation model XML"}));
  expect(test.client.startJob).toHaveBeenLastCalledWith("inspect_authoring_workspace",expect.objectContaining({document:{animation:"clip:11111111",model_xml:"C:/models/ped.ydr.xml"}}),expect.any(String),expect.any(Function));
  // Inventory-only dictionary avoids requiring a graphics context in the handoff test.
  const bound={...animated,animation_model:{...animationModel,source:"C:/models/ped.ydr.xml",selected:null,bones:[],meshes:[]}};
  await test.emit(bound);
  await user.selectOptions(screen.getByLabelText("Animation selection"),"animation:22222222");
  expect(test.client.startJob).toHaveBeenLastCalledWith("inspect_authoring_workspace",expect.objectContaining({document:expect.objectContaining({animation:"animation:22222222",model_xml:"C:/models/ped.ydr.xml"})}),expect.any(String),expect.any(Function));
  await test.emit(bound);
  await user.type(screen.getByLabelText("Native XML")," ");
  expect(screen.getByRole("button",{name:"Choose animation model XML"})).toBeDisabled();
  expect(screen.getByRole("button",{name:"Clear animation model"})).toBeDisabled();
});

it("binds a separate shared skeleton and preserves both owners through LOD and clip selection",async()=>{
  const user=userEvent.setup(), test=setup();
  await user.click(screen.getByRole("button",{name:"Open native workspace"}));
  const mesh={...animationModel,source:"C:/models/clothes.ydd.xml",bones:[],meshes:[],binding_required:"Choose shared skeleton"};
  const current={...session,name:"fixture.ycd",animation:animationFixture,animation_model:mesh};
  await test.emit(current);
  vi.mocked(test.client.selectPath).mockResolvedValue("C:/models/male.yft.xml");
  await user.click(screen.getByRole("button",{name:"Choose shared skeleton XML"}));
  expect(test.client.startJob).toHaveBeenLastCalledWith("inspect_authoring_workspace",expect.objectContaining({document:expect.objectContaining({model_xml:mesh.source,skeleton_xml:"C:/models/male.yft.xml",drawable:"0",lod:"High"})}),expect.any(String),expect.any(Function));
  const skeleton_binding={mode:"external",source:"C:/models/male.yft.xml",source_sha256:"b".repeat(64),selected:null,drawables:[{key:"0",name:"First"},{key:"1",name:"Second"}],scope:"Explicit rig"};
  await test.emit({...current,animation_model:{...mesh,skeleton_binding}});
  await user.selectOptions(screen.getByLabelText("Shared skeleton drawable"),"1");
  expect(test.client.startJob).toHaveBeenLastCalledWith("inspect_authoring_workspace",expect.objectContaining({document:expect.objectContaining({skeleton_drawable:"1",drawable:"0",lod:"High"})}),expect.any(String),expect.any(Function));
  await test.emit({...current,animation_model:{...mesh,skeleton_binding:{...skeleton_binding,selected:"1"}}});
  await user.selectOptions(screen.getByLabelText("Animation selection"),"animation:22222222");
  expect(test.client.startJob).toHaveBeenLastCalledWith("inspect_authoring_workspace",expect.objectContaining({document:expect.objectContaining({skeleton_xml:"C:/models/male.yft.xml",skeleton_drawable:"1",animation:"animation:22222222"})}),expect.any(String),expect.any(Function));
  await test.emit({...current,animation_model:{...mesh,skeleton_binding}});
  await user.click(screen.getByRole("button",{name:"Use embedded skeleton"}));
  expect(test.client.startJob).toHaveBeenLastCalledWith("inspect_authoring_workspace",expect.objectContaining({document:expect.objectContaining({model_xml:mesh.source,skeleton_xml:undefined,skeleton_drawable:undefined})}),expect.any(String),expect.any(Function));
  await test.emit(current);
  await user.type(screen.getByLabelText("Native XML")," ");
  expect(screen.getByRole("button",{name:"Choose shared skeleton XML"})).toBeDisabled();
});

it("hands saved native references to the matching game browser and locks handoff for XML drafts", async () => {
  const user = userEvent.setup(), search = vi.fn(), test = setup(search);
  await user.click(screen.getByRole("button", { name: "Open native workspace" }));
  await test.emit({ ...session, name: "test.ytyp", relationships: { schema_version: 1, format: ".ytyp", read_only: true, scope: "Decoded document only.", node_count: 1, edge_count: 0, truncated: false, warnings: [], edges: [],
    nodes: [{ id: "ref", kind: "asset", label: "prop_chair", search: "prop_chair", fields: {} }] } });
  await user.click(screen.getByRole("option", { name: /prop_chair/ }));
  await user.click(screen.getByRole("button", { name: "Find reference in game browser" }));
  expect(search).toHaveBeenCalledExactlyOnceWith("prop_chair", "C:/game");
  await user.type(screen.getByLabelText("Native XML"), " ");
  expect(screen.getByRole("button", { name: "Find reference in game browser" })).toBeDisabled();
});

it("labels browser fixture inspection and refuses to simulate a verified native build", async () => {
  const user = userEvent.setup(), client = createPreviewClient("rpf");
  render(<NativeWorkspace client={client} onGuardChange={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "Open native workspace" }));
  expect(screen.getByText(/Browser demonstration: generated YMAP/)).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Native spatial relationships" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review verified native build" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Review native workspace export" }));
  expect(screen.getByText(/Actual native inspection, export and build require the desktop SDK/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Apply reviewed change" })).not.toBeInTheDocument();
});

it("requires explicit edition for loose resources but can open an edition-bound workspace", async () => {
  const user = userEvent.setup(), test = setup();
  expect(screen.getByRole("button", { name: "Open native file" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Open native workspace" }));
  expect(test.client.startJob).toHaveBeenCalledWith("inspect_authoring_workspace", expect.objectContaining({ module: "native", workspace: "C:/work/native" }), expect.any(String), expect.any(Function));
  await test.emit(session);
  expect(screen.getByLabelText("Native XML")).toHaveValue("<root/>");
});

it("guards drafts and requires the digest-bound review plus confirmation before save", async () => {
  const user = userEvent.setup(), test = setup();
  await user.click(screen.getByRole("button", { name: "Open native workspace" })); await test.emit(session);
  await user.clear(screen.getByLabelText("Native XML")); await user.type(screen.getByLabelText("Native XML"), "<changed/>");
  expect(test.guard).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole("button", { name: "Review verified native build" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Review native XML save" }));
  expect(test.client.startJob).toHaveBeenLastCalledWith("review_workspace_action", expect.objectContaining({ module: "native", action: "save_xml", expected_state_sha256: session.state_sha256, document: { language: "xml", chunks: ["<changed/>"] } }), expect.any(String), expect.any(Function));
  await test.emit({ kind: "workspace_review", module: "native", schema_version: 1, game_write_performed: false, review_only: true, action: "save_xml", state_sha256: session.state_sha256, request_sha256: "c".repeat(64), review_sha256: "b".repeat(64) });
  expect(screen.getByRole("button", { name: "Apply reviewed change" })).toBeDisabled();
  await user.click(screen.getByLabelText("I reviewed these authoring changes"));
  await user.click(screen.getByRole("button", { name: "Apply reviewed change" }));
  expect(test.client.applyWorkspaceAction).toHaveBeenCalledWith(expect.objectContaining({ authoring_confirmed: true, review_sha256: "b".repeat(64) }));
  expect(test.guard).toHaveBeenLastCalledWith(false);
});

it("loads only the selected dependency and sends channel selection without an authoring write", async () => {
  const user = userEvent.setup(), test = setup();
  await user.click(screen.getByRole("button", { name: "Open native workspace" })); await test.emit(session);
  await user.selectOptions(screen.getByLabelText("Native dependency"), "track.wav");
  await user.click(screen.getByRole("button", { name: "Load audio preview" }));
  expect(test.client.startJob).toHaveBeenLastCalledWith("inspect_authoring_workspace", expect.objectContaining({ module: "native", document: { dependency: "track.wav" } }), expect.any(String), expect.any(Function));
  expect(test.client.applyWorkspaceAction).not.toHaveBeenCalled();
});

it("hands an exact YTD copy to the texture editor and refreshes its digest before replacement planning", async () => {
  const user = userEvent.setup(), test = setup();
  const native = { ...session, name: "vehicle.ytd", archive_binding: { entry_id: "child.rpf::vehicle.ytd", outer_archive: "C:/work/dlc.rpf", outer_archive_sha256: "d".repeat(64) } };
  await user.click(screen.getByRole("button", { name: "Open native workspace" })); await test.emit(native);
  await user.click(screen.getByRole("button", { name: "Edit dictionary textures" }));
  expect(test.client.startJob).toHaveBeenLastCalledWith("inspect_texture_workspace", { workspace: "C:/work/native" }, expect.any(String), expect.any(Function));
  await test.emit({ kind: "texture_workspace_session", workspace: "C:/work/native", source: "C:/work/native/original/vehicle.ytd", source_name: "vehicle.ytd", source_size: 128,
    edition: "Legacy", revision: 0, state_sha256: "b".repeat(64), texture_count: 1, can_undo: false, warnings: [],
    textures: [{ name: "diffuse", file_name: "diffuse.dds", width: 16, height: 8, mip_levels: 1, format: "D3DFMT_A8R8G8B8", usage: "DEFAULT", size: 640, sha256: "c".repeat(64), warnings: [] }] });
  expect(screen.getByText(/Exact member: child.rpf::vehicle.ytd/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open YTD" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Open workspace" })).toBeDisabled();
  await user.type(screen.getByLabelText("Rename selected texture"), "updated");
  expect(test.guard).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole("button", { name: "Return to XML & replacement plan" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Discard texture rename" }));
  await user.click(screen.getByRole("button", { name: "Return to XML & replacement plan" }));
  expect(test.client.startJob).toHaveBeenLastCalledWith("inspect_authoring_workspace", expect.objectContaining({ module: "native", workspace: "C:/work/native", gta_path: "C:/game" }), expect.any(String), expect.any(Function));
  await test.emit({ ...native, state_sha256: "f".repeat(64), xml_chunks: ["<updated/>"] });
  expect(screen.getByLabelText("Native XML")).toHaveValue("<updated/>");
  vi.mocked(test.client.selectPath).mockResolvedValueOnce("C:/output");
  await user.click(screen.getByRole("button", { name: "Review native RPF replacement plan" }));
  expect(test.client.startJob).toHaveBeenLastCalledWith("review_workspace_action", expect.objectContaining({ action: "plan_replacement", expected_state_sha256: "f".repeat(64), workspace: "C:/work/native", gta_path: "C:/game" }), expect.any(String), expect.any(Function));
});
