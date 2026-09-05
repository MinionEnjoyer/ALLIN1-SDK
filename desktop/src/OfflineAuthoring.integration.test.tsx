import { mkdtempSync, readFileSync, writeFileSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join, basename } from "node:path";
import { spawnSync } from "node:child_process";
import { afterEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import BinaryWorkspace from "./BinaryWorkspace";
import App from "./App";
import DataToolsWorkspace from "./DataToolsWorkspace";
import MapWorkbench, { newMapTemplate } from "./MapWorkbench";
import GraphWorkbench from "./GraphWorkbench";
import RuntimeWorkbench from "./RuntimeWorkbench";
import RenderWorkbench from "./RenderWorkbench";
import RecipeConversionPanel from "./RecipeConversionPanel";
import VehicleIdentityEditor from "./VehicleIdentityEditor";
import { EditorView } from "@codemirror/view";
import { createPreviewClient } from "./previewClient";
import type { Envelope, JobStart } from "./types";

// jsdom has no WebView asset protocol. Only URL conversion is substituted;
// native model decoding, Blender, PNG bytes and export receipts remain real.
vi.mock("@tauri-apps/api/core", () => ({ convertFileSrc: (path: string) => `http://fixture.invalid/${encodeURIComponent(path)}` }));

// These UI tests execute the real protocol/domain in Python, not canned success
// objects. All authoring and app-data paths are disposable and outside GTA.
const roots: string[] = [];
afterEach(() => { for (const root of roots.splice(0)) {
  if (!basename(root).startsWith("allin1-react-authoring-")) throw new Error("Unexpected fixture cleanup path");
  rmSync(root, { recursive: true, force: true });
} });
function fixture() {
  const root = mkdtempSync(join(tmpdir(), "allin1-react-authoring-")); roots.push(root);
  const files = join(root, "Paths with spaces"); mkdirSync(files);
  const sdk = resolve(".."), localPython = resolve(sdk, "../ALLIN1/.venv/Scripts/python.exe");
  const python = process.env.ALLIN1_SDK_TEST_PYTHON || (existsSync(localPython) ? localPython : "python");
  const client = createPreviewClient("rpf");
  const invoke = (operation: string, payload: Record<string, unknown>): Envelope => {
    const result = spawnSync(python, ["-c", `import json,sys,importlib.abc
class NoTk(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in {'tkinter','_tkinter'}:
   raise AssertionError('React fixture imported Tkinter: '+fullname)
sys.meta_path.insert(0,NoTk())
from allin1_sdk import detector
detector.detect_gta_path=lambda *a,**k: None
from allin1_sdk.desktop_protocol import dispatch_operation
request=json.load(sys.stdin)
try:
 risk,result=dispatch_operation(request['operation'],request['payload'])
 print(json.dumps({'operation':'result','risk':risk,'payload':{'result':result}}))
except (ValueError,OSError,RuntimeError,TypeError) as error:
 print(json.dumps({'operation':'error','risk':'none','payload':{'message':str(error)}}))`], {
      cwd: sdk, input: JSON.stringify({ operation, payload }), encoding: "utf8", windowsHide: true, timeout: ["runtime", "render"].includes(String(payload.module)) ? 180000 : 30000,
      env: { ...process.env, PYTHONPATH: join(sdk, "src"), LOCALAPPDATA: root, APPDATA: root, XDG_CACHE_HOME: root, XDG_DATA_HOME: root, ALLIN1_PREVIEW_DIR: join(root, "preview cache") },
    });
    if (result.error || result.status !== 0) throw new Error(`Python integration failed: ${result.error || result.stderr}`);
    return { protocol_version: "1.0.0", request_id: "offline-test", job_id: "offline-job", sequence: 1, terminal: true, ...JSON.parse(result.stdout) };
  };
  client.startJob = vi.fn(async (operation, payload, _revision, event) => {
    event(invoke(operation, payload)); // Deliberately exercises terminal-before-start ordering.
    return { job_id: "offline-job" } as Awaited<ReturnType<typeof client.startJob>>;
  });
  client.applyWorkspaceAction = vi.fn(async payload => invoke("apply_workspace_action", payload));
  const paths: Record<string, string> = { authoring_parent: files, binary_source: join(files, "original.bin"), binary_workspace: join(files, "binary-copy"), map_descriptor: join(files, "maps.json") };
  client.selectPath = vi.fn(async (kind: string) => paths[kind] || null) as typeof client.selectPath;
  return { root, files, client, paths, user: userEvent.setup(), invoke, python, sdk };
}
async function confirm(user: ReturnType<typeof userEvent.setup>) {
  const button = await screen.findByRole("button", { name: "Apply reviewed change" });
  expect(button).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: "I reviewed these authoring changes" }));
  await user.click(button);
  await waitFor(() => expect(screen.queryByRole("region", { name: "Authoring review" })).not.toBeInTheDocument());
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
}

function editCode(language: string, text: string) {
  const view = EditorView.findFromDOM(screen.getByRole("textbox", { name: `${language} source editor` }));
  if (!view) throw new Error("The real CodeMirror editor did not mount");
  act(() => view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } }));
}

it("code draft survives keyboard Back, sidebar navigation and native close requests", async () => {
  const { client, user } = fixture();
  client.initialLaunchRequest = async () => null;
  let closeRequest = () => {};
  client.onCloseRequested = async callback => { closeRequest = callback; return () => {}; };
  client.closeWindow = vi.fn();
  render(<App client={client} />);
  await user.click(await screen.findByRole("button", { name: /Data Tools/ }));
  await user.click(await screen.findByRole("button", { name: "XML & Lua editor" }));
  await user.click(screen.getByRole("button", { name: "New LUA" }));
  await screen.findByRole("textbox", { name: "LUA source editor" });
  editCode("LUA", "return { retained = true }");
  fireEvent.keyDown(window, { altKey: true, key: "ArrowLeft" });
  expect(screen.getByRole("textbox", { name: "LUA source editor" })).toHaveTextContent("retained");
  expect(await screen.findByText(/Save or discard the code draft and finish/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Help Center/ }));
  expect(screen.getByRole("textbox", { name: "LUA source editor" })).toHaveTextContent("retained");
  act(() => closeRequest());
  expect(client.closeWindow).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Close / discard draft" }));
  fireEvent.keyDown(window, { altKey: true, key: "ArrowLeft" });
  await screen.findByRole("heading", { name: "Package Linker" });
});

it("XML editor React happy path validates real source, reviews a diff and saves with a recovery backup", async () => {
  const { files, paths, client, user } = fixture();
  const source = join(files, "vehicle.meta"); paths.code_source = source;
  writeFileSync(source, '<Vehicle><Name>Original</Name></Vehicle>');
  render(<DataToolsWorkspace client={client} onGuardChange={() => {}} />);
  await user.click(screen.getByRole("button", { name: "XML & Lua editor" }));
  await user.click(screen.getByRole("button", { name: "Open XML / Lua" }));
  expect(await screen.findByText("Syntax check passed")).toBeInTheDocument();
  editCode("XML", '<Vehicle><Name>Edited</Name></Vehicle>');
  expect(screen.getByRole("button", { name: "Metadata reports" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Check syntax" }));
  expect(await screen.findByText("Syntax check passed")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Review save" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "Review save" }));
  expect(await screen.findByRole("region", { name: "Code save diff" })).toHaveTextContent("-<Vehicle><Name>Original");
  expect(readFileSync(source, "utf8")).toContain("Original");
  await confirm(user);
  expect(readFileSync(source, "utf8")).toContain("Edited");
  const applied = client.applyWorkspaceAction as ReturnType<typeof vi.fn>;
  const result = await applied.mock.results[0].value;
  expect(readFileSync(result.payload.result.backup, "utf8")).toContain("Original");
  expect(screen.getByRole("button", { name: "Metadata reports" })).toBeEnabled();
}, 30000);

it("Lua editor React happy path creates syntax-checked source and exports a copy without execution", async () => {
  const { files, client, user } = fixture();
  render(<DataToolsWorkspace client={client} onGuardChange={() => {}} />);
  await user.click(screen.getByRole("button", { name: "XML & Lua editor" }));
  await user.click(screen.getByRole("button", { name: "New LUA" }));
  await screen.findByRole("textbox", { name: "LUA source editor" });
  editCode("LUA", 'local config <const> = { rpm = 900 }\nreturn config\n');
  await user.click(screen.getByRole("button", { name: "Check syntax" }));
  expect(await screen.findByText("Syntax check passed")).toBeInTheDocument();
  await user.clear(screen.getByLabelText("New copy filename"));
  await user.type(screen.getByLabelText("New copy filename"), "weapon.lua");
  await user.click(screen.getByRole("button", { name: "Review save a copy" }));
  expect(existsSync(join(files, "weapon.lua"))).toBe(false);
  await confirm(user);
  expect(readFileSync(join(files, "weapon.lua"), "utf8")).toContain("rpm = 900");
  expect(screen.getByRole("button", { name: "New XML" })).toBeEnabled();
}, 30000);

it("code editor preserves malformed and stale drafts and releases its guard only on explicit discard", async () => {
  const { files, paths, client, user } = fixture();
  const source = join(files, "source.xml"); paths.code_source = source;
  writeFileSync(source, '<original/>');
  render(<DataToolsWorkspace client={client} onGuardChange={() => {}} />);
  await user.click(screen.getByRole("button", { name: "XML & Lua editor" }));
  await user.click(screen.getByRole("button", { name: "Open XML / Lua" }));
  await screen.findByRole("textbox", { name: "XML source editor" });
  editCode("XML", '<broken>');
  await user.click(screen.getByRole("button", { name: "Check syntax" }));
  expect(await screen.findByText("Syntax errors")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "XML source editor" })).toHaveTextContent("<broken>");
  await user.click(screen.getByRole("button", { name: "Review save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Syntax check failed");
  editCode("XML", '<valid/>');
  await user.click(screen.getByRole("button", { name: "Review save" }));
  await screen.findByRole("region", { name: "Code save diff" });
  writeFileSync(source, '<external/>');
  await user.click(screen.getByRole("checkbox", { name: "I reviewed these authoring changes" }));
  await user.click(screen.getByRole("button", { name: "Apply reviewed change" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Source changed");
  expect(readFileSync(source, "utf8")).toBe('<external/>');
  expect(screen.getByRole("textbox", { name: "XML source editor" })).toHaveTextContent("<valid/>");
  expect(screen.getByRole("button", { name: "Metadata reports" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Close / discard draft" }));
  expect(screen.getByRole("button", { name: "Metadata reports" })).toBeEnabled();
}, 30000);

it("data tools React happy path compares real metadata and exports reviewed reports", async () => {
  const { files, paths, client, user } = fixture();
  const first = join(files, "before.meta"), second = join(files, "after.meta");
  writeFileSync(first, '<root><value value="1" /></root>');
  writeFileSync(second, '<root><value value="2" /></root>');
  paths.metadata = first;
  render(<DataToolsWorkspace client={client} onGuardChange={() => {}} />);
  await user.click(screen.getByRole("button", { name: "Choose source" }));
  paths.metadata = second;
  await user.click(screen.getByRole("button", { name: "Choose comparison" }));
  await user.click(screen.getByRole("button", { name: "Inspect data" }));
  expect(await screen.findByRole("cell", { name: "1 → 2" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Review report export" }));
  expect(existsSync(join(files, "meta-diff-report"))).toBe(false);
  await confirm(user);
  const report = JSON.parse(readFileSync(join(files, "meta-diff-report", "meta-diff.json"), "utf8"));
  expect(report.change_count).toBe(1);
  expect(readFileSync(first, "utf8")).toContain('value="1"');
  await user.click(screen.getByRole("button", { name: "Validate metadata round trip" }));
  await user.click(screen.getByRole("button", { name: "Choose source" }));
  await user.click(screen.getByRole("button", { name: "Inspect data" }));
  expect(await screen.findByText("Semantic equivalence: PASS")).toBeInTheDocument();
}, 30000);

it("binary React happy path performs real create, patch, undo, reopen and verified export", async () => {
  const { files, paths, client, user } = fixture(), guard = vi.fn();
  const original = Buffer.from(Array.from({ length: 300 }, (_, n) => n % 256));
  writeFileSync(paths.binary_source, original);
  render(<BinaryWorkspace client={client} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Open binary" }));
  expect(await screen.findByRole("button", { name: "Byte 00000000: 00" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Create binary copy" })); await confirm(user);
  expect(existsSync(join(paths.binary_workspace, "original.bin"))).toBe(true);
  await user.click(screen.getByRole("button", { name: "Byte 00000000: 00" }));
  await user.type(screen.getByLabelText("Replacement bytes"), "FF");
  expect(guard).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole("button", { name: "Open binary" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Review binary patch" })); await confirm(user);
  expect(screen.getByRole("button", { name: "Byte 00000000: FF" })).toHaveClass("changed-byte");
  expect(readFileSync(join(paths.binary_workspace, "editable.bin"))[0]).toBe(255);
  await user.click(screen.getByRole("button", { name: "Review undo latest patch" })); await confirm(user);
  expect(readFileSync(join(paths.binary_workspace, "editable.bin"))).toEqual(original);
  await user.click(screen.getByRole("button", { name: "Byte 00000000: 00" }));
  await user.type(screen.getByLabelText("Replacement bytes"), "EE");
  await user.click(screen.getByRole("button", { name: "Review binary patch" })); await confirm(user);
  await user.click(screen.getByRole("button", { name: "Open binary workspace" }));
  await user.click(screen.getByRole("button", { name: "Next bytes" }));
  expect(await screen.findByRole("button", { name: "Byte 00000100: 00" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Review binary build" })); await confirm(user);
  expect(readFileSync(join(files, "patched.bin"))[0]).toBe(238);
  const report = JSON.parse(readFileSync(join(files, "patched.bin.binary-diff.json"), "utf8"));
  expect(report.status).toBe("verified"); expect(report.changed_bytes).toBe(1);
  expect(readFileSync(paths.binary_source)).toEqual(original);
  expect(readFileSync(join(paths.binary_workspace, "original.bin"))).toEqual(original);
  expect(guard).toHaveBeenLastCalledWith(false);
}, 30000);

it("map React happy path creates, edits topology and slots, saves and reopens real descriptors", async () => {
  const { files, client, user } = fixture(), guard = vi.fn();
  render(<MapWorkbench client={client} onDirtyChange={guard} />);
  await user.click(screen.getByRole("button", { name: "New map project" }));
  expect(guard).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole("button", { name: "Review map save" })); await confirm(user);
  const saved = join(files, "maps.json");
  expect(JSON.parse(readFileSync(saved, "utf8")).name).toBe("Custom Map");
  await user.click(screen.getByRole("button", { name: "Custom Interior" }));
  const section = JSON.parse((screen.getByLabelText("Section JSON") as HTMLTextAreaElement).value);
  section.name = "Ground floor"; section.center.z = -40;
  fireEvent.change(screen.getByLabelText("Section JSON"), { target: { value: JSON.stringify(section, null, 2) } });
  await user.click(screen.getByRole("button", { name: "Apply section to draft" }));
  await user.click(screen.getByRole("button", { name: "Add level" }));
  await user.click(screen.getByRole("button", { name: "Main Garage" }));
  await user.click(screen.getByRole("button", { name: "Add slot" }));
  await user.click(screen.getByRole("button", { name: "Review map save" })); await confirm(user);
  let descriptor = JSON.parse(readFileSync(saved, "utf8"));
  expect(descriptor.levels[0].center.z).toBe(-40);
  expect(descriptor.levels).toHaveLength(2); expect(descriptor.garages[0].slots).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "Open map descriptor" }));
  await user.click(await screen.findByRole("button", { name: "New Level" }));
  await user.click(screen.getByRole("button", { name: "Remove selected record" }));
  await user.click(screen.getByRole("button", { name: "Review map save" })); await confirm(user);
  descriptor = JSON.parse(readFileSync(saved, "utf8")); expect(descriptor.levels).toHaveLength(1);
  expect(guard).toHaveBeenLastCalledWith(false);
}, 30000);

it("a concurrent disk edit invalidates a map confirmation without losing the React draft", async () => {
  const { files, client, user } = fixture();
  const saved = join(files, "maps.json"); writeFileSync(saved, JSON.stringify(newMapTemplate()));
  render(<MapWorkbench client={client} onDirtyChange={() => {}} />);
  await user.click(screen.getByRole("button", { name: "Open map descriptor" }));
  await user.click(await screen.findByRole("button", { name: "Add level" }));
  await user.click(screen.getByRole("button", { name: "Review map save" }));
  const concurrent = JSON.parse(readFileSync(saved, "utf8")); concurrent.name = "External author edit";
  writeFileSync(saved, JSON.stringify(concurrent));
  await user.click(screen.getByRole("checkbox")); await user.click(screen.getByRole("button", { name: "Apply reviewed change" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("changed");
  expect(screen.getByRole("button", { name: "New Level" })).toBeInTheDocument();
  expect(JSON.parse(readFileSync(saved, "utf8")).name).toBe("External author edit");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
}, 15000);

it("cancelled reads cannot adopt late completion or keep an early-start job alive", async () => {
  const { files, client, user } = fixture(); writeFileSync(join(files, "original.bin"), Buffer.from([1, 2]));
  let event: (message: Envelope) => void = () => {};
  let finish!: (value: Awaited<ReturnType<typeof client.startJob>>) => void;
  client.startJob = vi.fn((_op, _payload, _revision, handler) => { event = handler; return new Promise<JobStart>(resolve => { finish = resolve; }); });
  const cancel = vi.spyOn(client, "cancelJob");
  render(<BinaryWorkspace client={client} onGuardChange={() => {}} />);
  await user.click(screen.getByRole("button", { name: "Open binary" }));
  await user.click(screen.getByRole("button", { name: "Cancel inspection" }));
  await act(async () => { event({ protocol_version: "1.0.0", request_id: "late", job_id: "late-job", sequence: 1, risk: "read_only", operation: "result", terminal: true, payload: { result: {} } }); finish({ job_id: "late-job" } as JobStart); });
  await waitFor(() => expect(cancel).toHaveBeenCalledWith("late-job"));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open binary" })).toBeEnabled();
});

it("Story runtime React preflight handles a missing dependency without enabling a build", async () => {
  const { files, client, user } = fixture();
  render(<RuntimeWorkbench client={client} onDirtyChange={() => {}} />);
  await user.selectOptions(screen.getByLabelText("Toolchain selection"), "manual");
  fireEvent.change(screen.getByLabelText("CMake executable"), { target: { value: join(files, "missing", "cmake.exe") } });
  await user.click(screen.getByRole("button", { name: "Run native preflight" }));
  expect(await screen.findByText("Toolchain requires attention")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Review controller build" })).toBeDisabled();
  expect(existsSync(join(files, "story-runtime-candidate"))).toBe(false);
}, 30000);

// Opt in to the real compiler gate; absence is a skip, never a fabricated pass.
it.runIf(process.env.ALLIN1_NATIVE_RUNTIME_TEST === "1")("Story runtime React happy path builds both actual native candidates and retains CTest evidence", async () => {
  const { files, client, user } = fixture(), guard = vi.fn();
  render(<RuntimeWorkbench client={client} onDirtyChange={guard} />);
  await user.click(screen.getByRole("checkbox", { name: "Story Legacy" }));
  fireEvent.change(screen.getByLabelText("Build identity"), { target: { value: "sdk-0.6.4-react-happy-path" } });
  await user.click(screen.getByRole("button", { name: "Run native preflight" }));
  expect(await screen.findByText("Toolchain verified")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Review controller build" }));
  expect(screen.getByText("Candidate only. Live game acceptance: NOT TESTED.")).toBeInTheDocument();
  await confirm(user);
  const receipt = screen.getByText("Candidate build receipt").parentElement!;
  const data = JSON.parse(receipt.querySelector("pre")!.textContent!);
  expect(data.built_targets).toEqual(["story-legacy", "story-enhanced"]);
  expect(data.candidate_status).toEqual({ supported: false, game_acceptance: "not-tested" });
  expect(data.commands.find((item: { name: string }) => item.name === "Native CTest").returncode).toBe(0);
  expect(data.archives.map((file: string) => basename(file))).toEqual([
    `VehicleWorkbenchAxles-Legacy-${data.runtime_version}.zip`,
    `VehicleWorkbenchAxles-Enhanced-${data.runtime_version}.zip`,
    `VehicleWorkbenchAxles-${data.runtime_version}-Legacy-and-Enhanced.zip`,
  ]);
  expect(existsSync(data.manifest)).toBe(true);
  expect(data.output).toBe(join(files, "story-runtime-candidate"));
  await waitFor(() => expect(guard).toHaveBeenLastCalledWith(false));
}, 240000);

it("package layout React happy path imports a real folder, renames, arranges, saves and materializes", async () => {
  const { files, client, paths, user } = fixture(), guard = vi.fn();
  const source = join(files, "dlc.rpf.source"); mkdirSync(source); writeFileSync(join(source, "example.bin"), "owned fixture");
  paths.graph_source = source; paths.graph_document = join(files, "rpf-graph.json");
  render(<GraphWorkbench client={client} module="graph" onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Graph from folder" }));
  expect(await screen.findByRole("button", { name: "Select node example.bin" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Review graph save" })); await confirm(user);
  await user.click(screen.getByRole("button", { name: "Select node example.bin" }));
  fireEvent.change(screen.getByLabelText("Node name"), { target: { value: "renamed.bin" } });
  expect(screen.getByRole("button", { name: "Open graph" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Apply node to draft" }));
  fireEvent.keyDown(screen.getByRole("button", { name: "Select node renamed.bin" }), { key: "ArrowDown" });
  await user.click(screen.getByRole("button", { name: "Add directory" }));
  await user.click(screen.getByRole("button", { name: "Auto layout nodes" }));
  await user.click(screen.getByRole("button", { name: "Review graph save" })); await confirm(user);
  expect(JSON.parse(readFileSync(paths.graph_document, "utf8")).nodes).toHaveLength(3);
  await user.click(screen.getByRole("button", { name: "Open graph" }));
  await user.click(screen.getByRole("button", { name: "Review materialize tree" })); await confirm(user);
  expect(readFileSync(join(files, "materialized-tree", "renamed.bin"), "utf8")).toBe("owned fixture");
  expect(existsSync(join(files, "materialized-tree", "New directory"))).toBe(true);
  expect(readFileSync(join(source, "example.bin"), "utf8")).toBe("owned fixture");
  expect(guard).toHaveBeenLastCalledWith(false);
}, 30000);

it.runIf(!!process.env.ALLIN1_BLENDER_EXECUTABLE)("render studio React happy path decodes a real model, renders in Blender and exports verified pixels", async () => {
  const { files, paths, client, user } = fixture(), guard = vi.fn();
  const sdk = resolve(".."), modelSource = join(files, "Model source"); mkdirSync(modelSource);
  paths.render_model = join(modelSource, "fixture.ydr"); paths.blender_executable = process.env.ALLIN1_BLENDER_EXECUTABLE!;
  const created = spawnSync(join(sdk, "tools/RpfPatcher/RpfPatcher.exe"), ["asset-from-xml", join(sdk, "tests/fixtures/render_tetrahedron.ydr.xml"), paths.render_model, modelSource, "legacy"], { encoding: "utf8", windowsHide: true, timeout: 30000 });
  expect(created.error || created.stderr).toBeFalsy(); expect(created.status).toBe(0);
  const sourceBytes = readFileSync(paths.render_model);
  render(<RenderWorkbench client={client} onDirtyChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Choose render model" }));
  await user.click(screen.getByRole("button", { name: "Locate Blender" }));
  await user.click(screen.getByRole("button", { name: "Check Blender" }));
  expect(await screen.findByText(/Blender 4\.5\.13 verified/)).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Render edition"), "Legacy");
  await user.selectOptions(screen.getByLabelText("Render engine"), "cycles");
  await user.selectOptions(screen.getByLabelText("Render device"), "cpu");
  fireEvent.change(screen.getByLabelText("Width (px)"), { target: { value: "512" } });
  fireEvent.change(screen.getByLabelText("Height (px)"), { target: { value: "512" } });
  fireEvent.change(screen.getByLabelText("Samples (blank: quality default)"), { target: { value: "8" } });
  await user.click(screen.getByRole("button", { name: "Render frame" }));
  expect(await screen.findByAltText("Compiled Blender frame")).toBeInTheDocument();
  expect(guard).toHaveBeenLastCalledWith(true);
  const details = JSON.parse(screen.getByText("Render identities & evidence").parentElement!.querySelector("pre")!.textContent!);
  expect(details.metadata.backend).toBe("Blender headless"); expect(details.metadata.triangle_count).toBe(4);
  await user.click(screen.getByRole("button", { name: "Review PNG export" })); await confirm(user);
  const exported = join(files, "compiled-render.png"), png = readFileSync(exported);
  expect(png.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  expect(png.readUInt32BE(16)).toBe(512); expect(png.readUInt32BE(20)).toBe(512);
  expect(JSON.parse(readFileSync(exported + ".render.json", "utf8")).output_sha256).toBe(details.output_sha256);
  expect(readFileSync(paths.render_model)).toEqual(sourceBytes);
  expect(screen.getByAltText("Compiled Blender frame")).toBeInTheDocument();
  await waitFor(() => expect(guard).toHaveBeenLastCalledWith(false));
}, 240000);

it.runIf(process.env.ALLIN1_NATIVE_RPF_TEST === "1")("package layout React happy path imports and expands real RPFs without touching their originals", async () => {
  const { files, paths, client, user } = fixture(), sdk = resolve("..");
  const source = join(files, "Archive source"), game = join(files, "Synthetic decoder context"); mkdirSync(source); mkdirSync(game);
  writeFileSync(join(source, "owned.txt"), "owned archive fixture");
  writeFileSync(join(game, "GTA5.exe"), "not executable; edition-routing fixture only");
  paths.rpf = join(files, "owned.rpf"); paths.gta_folder = game;
  const built = spawnSync(join(sdk, "tools/RpfPatcher/RpfPatcher.exe"), ["build-dlc", source, paths.rpf], { encoding: "utf8", windowsHide: true, timeout: 30000 });
  expect(built.error).toBeUndefined(); expect(built.status, built.stderr).toBe(0);
  const original = readFileSync(paths.rpf);
  render(<GraphWorkbench client={client} module="graph" onGuardChange={() => {}} />);
  await user.click(screen.getByRole("button", { name: "Import RPF graph" })); await confirm(user);
  const imported = JSON.parse(readFileSync(join(files, "owned-graph/rpf-graph.json"), "utf8"));
  expect(imported.origin.type).toBe("rpf_archive_import");
  expect(readFileSync(imported.nodes.find((node: { name: string }) => node.name === "owned.txt").source, "utf8")).toBe("owned archive fixture");
  fireEvent.change(screen.getByLabelText("Output / report name"), { target: { value: "preview-bundle" } });
  await user.click(screen.getByRole("button", { name: "Review preview bundle" })); await confirm(user);
  expect(JSON.parse(readFileSync(join(files, "preview-bundle/preview-report.json"), "utf8")).summary.failed).toBe(0);
  const sealed = join(files, "Sealed sources"); mkdirSync(sealed); writeFileSync(join(sealed, "child.rpf"), original); paths.graph_source = sealed;
  await user.click(screen.getByRole("button", { name: "Graph from folder" }));
  await user.click(screen.getByRole("button", { name: "Review graph save" })); await confirm(user);
  await user.click(screen.getByRole("button", { name: "Select node child.rpf" }));
  await user.click(screen.getByRole("button", { name: "Review sealed archive expansion" })); await confirm(user);
  const expanded = JSON.parse(readFileSync(join(files, "rpf-graph.json"), "utf8"));
  expect(expanded.nodes.find((node: { name: string }) => node.name === "child.rpf").type).toBe("archive");
  expect(readFileSync(expanded.nodes.find((node: { name: string }) => node.name === "owned.txt").source, "utf8")).toBe("owned archive fixture");
  expect(readFileSync(paths.rpf)).toEqual(original); expect(readFileSync(join(sealed, "child.rpf"))).toEqual(original);
}, 60000);

it("build flow React happy path configures typed nodes, saves, plans and executes real offline artifacts", async () => {
  const { files, client, paths, user, invoke } = fixture(), guard = vi.fn();
  const source = join(files, "source"); mkdirSync(source); writeFileSync(join(source, "example.bin"), "flow fixture");
  paths.graph_document = join(files, "graph.json"); paths.program_document = join(files, "rpf-program.json");
  const inspection = invoke("inspect_authoring_workspace", { module: "graph", source }).payload.result as { document: unknown };
  const create = { module: "graph", action: "create", destination: paths.graph_document, document: inspection.document };
  const review = invoke("review_workspace_action", create).payload.result as { review_sha256: string };
  expect(invoke("apply_workspace_action", { ...create, review_sha256: review.review_sha256, authoring_confirmed: true }).operation).toBe("result");
  render(<GraphWorkbench client={client} module="program" onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "New program" }));
  await user.click(await screen.findByRole("button", { name: "Select node materialize" }));
  fireEvent.change(screen.getByLabelText("Output path"), { target: { value: join(files, "flow output") } });
  await user.click(screen.getByRole("button", { name: "Apply node to draft" }));
  await user.click(screen.getByRole("button", { name: "Review program save" })); await confirm(user);
  await user.click(screen.getByRole("button", { name: "Review flow plan" })); await confirm(user);
  expect(JSON.parse(readFileSync(join(files, "program-report.json"), "utf8")).status).toBe("ready");
  expect(existsSync(join(files, "flow output"))).toBe(false);
  fireEvent.change(screen.getByLabelText("Output / report name"), { target: { value: "execution.json" } });
  await user.click(screen.getByRole("button", { name: "Review flow execution" }));
  expect(screen.getByText(join(files, "flow output"))).toBeInTheDocument();
  await confirm(user);
  expect(readFileSync(join(files, "flow output", "example.bin"), "utf8")).toBe("flow fixture");
  expect(JSON.parse(readFileSync(join(files, "execution.json"), "utf8")).status).toBe("verified");
  expect(guard).toHaveBeenLastCalledWith(false);
}, 30000);

function recipeFixture(files: string, operations: string) {
  const source = join(files, "Recipe source"); mkdirSync(join(source, "content"), { recursive: true });
  writeFileSync(join(source, "content", "data.xml"), "<Root><Value>owned source</Value></Root>");
  writeFileSync(join(source, "assembly.xml"), `<package version="2.2"><metadata><name>Owned Recipe</name><gameversion>enhanced</gameversion></metadata><content>${operations}</content></package>`);
  return source;
}

it.each(["managed", "batches"])("recipe React happy path exports a real %s conversion without executing it", async action => {
  const { files, client, user } = fixture(), guard = vi.fn();
  const source = recipeFixture(files, action === "managed" ? '<add source="data.xml">scripts/Owned/config.xml</add>'
    : '<archive path="update/update.rpf"><archive path="child.rpf"><add source="data.xml">new.xml</add><delete>old.xml</delete></archive></archive>');
  render(<RecipeConversionPanel client={client} source={source} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Inspect conversion options" }));
  await user.selectOptions(screen.getByLabelText("Conversion type"), action);
  await user.click(screen.getByRole("button", { name: "Review recipe conversion" }));
  expect(guard).toHaveBeenLastCalledWith(true);
  expect(existsSync(join(files, "converted-recipe"))).toBe(false);
  expect(screen.getByRole("button", { name: "Inspect conversion options" })).toBeDisabled();
  await confirm(user);
  const receipt = await vi.mocked(client.applyWorkspaceAction).mock.results.at(-1)?.value;
  const result = receipt.payload.result;
  expect(result.file_count).toBeGreaterThan(1);
  expect(result.archive_write_performed).toBe(false);
  const report = readFileSync(result.reports[0], "utf8");
  expect(report).toContain(action === "managed" ? 'id = "owned-recipe"' : '"rpf_multi_entry_change_manifest"');
  if (action === "batches") expect(screen.getByText(/Inert plan only/)).toBeInTheDocument();
  await waitFor(() => expect(guard).toHaveBeenLastCalledWith(false));
}, 30000);

it.skipIf(process.env.ALLIN1_NATIVE_RPF_TEST !== "1")("recipe React happy path builds a native RPF package and compiles XML changes without touching that archive", async () => {
  const { files, client, paths, user } = fixture(), guard = vi.fn();
  paths.gta_folder = join(files, "Synthetic decoder"); mkdirSync(paths.gta_folder);
  writeFileSync(join(paths.gta_folder, "GTA5_Enhanced.exe"), "MZ-owned-marker-not-executable");
  const source = recipeFixture(files, '<archive path="update/x64/dlcpacks/owned/dlc.rpf" createIfNotExist="true"><add source="data.xml">config.xml</add></archive>');
  render(<RecipeConversionPanel client={client} source={source} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Inspect conversion options" }));
  await user.selectOptions(screen.getByLabelText("Conversion type"), "created");
  await user.click(screen.getByRole("button", { name: "Choose recipe decoder context" }));
  await user.click(screen.getByRole("button", { name: "Review recipe conversion" })); await confirm(user);
  const built = (await vi.mocked(client.applyWorkspaceAction).mock.results.at(-1)?.value).payload.result;
  const manifest = readFileSync(built.reports[0], "utf8");
  const payload = manifest.match(/^source = "([^"]+)"/m)![1];
  const original = readFileSync(join(files, "converted-recipe", payload));
  // The generated package prefixes payload filenames; the manifest installs it
  // as dlc.rpf. Use an owned external copy with that exact basename for compile.
  paths.rpf = join(files, "dlc.rpf"); writeFileSync(paths.rpf, original);
  expect(original.readUInt32LE(0)).toBe(0x52504637);
  writeFileSync(join(source, "assembly.xml"), '<package version="2.2"><metadata><name>Owned Recipe</name><gameversion>enhanced</gameversion></metadata><content><archive path="update/x64/dlcpacks/owned/dlc.rpf"><xml path="config.xml"><replace xpath="/Root/Value"><Value>compiled change</Value></replace></xml></archive></content></package>');
  await user.click(screen.getByRole("button", { name: "Inspect conversion options" }));
  await user.selectOptions(screen.getByLabelText("Conversion type"), "compile");
  await user.click(screen.getByRole("button", { name: "Choose recipe outer archive" }));
  fireEvent.change(screen.getByLabelText("Recipe output folder"), { target: { value: "compiled-recipe" } });
  await user.click(screen.getByRole("button", { name: "Review recipe conversion" })); await confirm(user);
  const compiled = (await vi.mocked(client.applyWorkspaceAction).mock.results.at(-1)?.value).payload.result;
  expect(compiled.inert_plan_only).toBe(true);
  expect(compiled.reports).toHaveLength(2);
  const plan = JSON.parse(readFileSync(compiled.reports[0], "utf8"));
  expect(JSON.stringify(plan)).toContain("config.xml");
  expect(readFileSync(paths.rpf)).toEqual(original);
  expect(readFileSync(join(source, "content", "data.xml"), "utf8")).toContain("owned source");
}, 90000);

it("vehicle identity React happy path reviews real references and asset renames, applies, and supports existing undo", async () => {
  const { files, client, user, invoke, python, sdk } = fixture(), guard = vi.fn(), saved = vi.fn();
  const setup = spawnSync(python, ["-c", `import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'tests'))
from test_vehicle_authoring import _source
from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace
root=Path(sys.argv[2])
workspace=VehicleAuthoringWorkspace.create(_source(root),root/'Vehicle editable copy')
print(str(workspace.root))`, sdk, files], { encoding: "utf8", cwd: sdk, windowsHide: true });
  expect(setup.status, setup.stderr).toBe(0);
  const workspace = setup.stdout.trim();
  const session = invoke("inspect_vehicle_authoring_workspace", { workspace, model: "authorcar" }).payload.result as import("./types").VehicleAuthoringSession;
  render(<VehicleIdentityEditor client={client} session={session} disabled={false} onGuardChange={guard} onSaved={saved} />);
  await user.click(screen.getByRole("button", { name: "Inspect identity migration" }));
  fireEvent.change(screen.getByLabelText("New vehicle model identifier"), { target: { value: "reactcar" } });
  fireEvent.change(screen.getByLabelText("New vehicle handling identifier"), { target: { value: "REACTHAND" } });
  expect(guard).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole("button", { name: "Review identity migration" }));
  expect(screen.getAllByText(/stream.*reactcar.yft/).length).toBeGreaterThan(0);
  expect(existsSync(join(workspace, "source", "stream", "reactcar.yft"))).toBe(false);
  await confirm(user);
  await waitFor(() => expect(saved).toHaveBeenCalledWith(expect.objectContaining({ revision: 1, selected_model: "reactcar" })));
  expect(readFileSync(join(workspace, "source", "stream", "reactcar.yft"), "utf8")).toBe("fragment");
  expect(readFileSync(join(files, "vehicle-source", "stream", "authorcar.yft"), "utf8")).toBe("fragment");
  const undo = invoke("apply_vehicle_authoring_history", { workspace, direction: "undo", expected_revision: 1, authoring_confirmed: true });
  expect(undo.operation, JSON.stringify(undo.payload)).toBe("result");
  expect(existsSync(join(workspace, "source", "stream", "authorcar.yft"))).toBe(true);
}, 30000);

it("package relationships React happy path imports a retained folder, arranges semantic nodes, analyzes and hands off exact sources", async () => {
  const { files, client, paths, user, python, sdk } = fixture(), guard = vi.fn(), openVehicle = vi.fn(), openAsset = vi.fn();
  const setup = spawnSync(python, ["-c", `import sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'tests'))
from test_vehicle_authoring import _source
print(_source(Path(sys.argv[2])))`, sdk, files], { cwd: sdk, windowsHide: true, encoding: "utf8" });
  expect(setup.status, setup.stderr).toBe(0); paths.graph_source = setup.stdout.trim();
  render(<GraphWorkbench client={client} module="graph" onGuardChange={guard} onOpenVehicle={openVehicle} onOpenAsset={openAsset} />);
  await user.click(screen.getByRole("button", { name: "Import package folder" })); await confirm(user);
  await user.click(screen.getByRole("button", { name: "Select node authorcar" }));
  expect(screen.getByText("Resolved vehicle relationships")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Open graph vehicle" }));
  expect(openVehicle).toHaveBeenCalledWith(expect.stringContaining("package-source"), "authorcar");
  const retainedSource = openVehicle.mock.calls[0][0];
  fireEvent.keyDown(screen.getByRole("button", { name: "Select node authorcar" }), { key: "ArrowRight" });
  expect(screen.getByRole("button", { name: "Open graph vehicle" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Review graph save" })); await confirm(user);
  await user.click(screen.getByRole("button", { name: "Review package relationships" })); await confirm(user);
  await user.click(screen.getByRole("button", { name: "Select node authorcar.yft" }));
  await user.click(screen.getByRole("button", { name: "Open graph asset" }));
  expect(openAsset).toHaveBeenCalledWith(join(retainedSource, "stream", "authorcar.yft"));
  await user.click(screen.getByRole("button", { name: "Select node package-preview.rpf" }));
  await user.click(screen.getByRole("button", { name: "Collapse selected branch" }));
  expect(screen.queryByRole("button", { name: "Select node authorcar.yft" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Expand all branches" }));
  expect(screen.getByRole("button", { name: "Select node authorcar.yft" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Fit graph" }));
  expect(guard).toHaveBeenLastCalledWith(false);
  expect(readFileSync(join(paths.graph_source, "stream", "authorcar.yft"), "utf8")).toBe("fragment");
}, 60000);
