import { mkdtempSync, readFileSync, writeFileSync, existsSync, mkdirSync, rmSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join, basename } from "node:path";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import BinaryWorkspace from "./BinaryWorkspace";
import NativeWorkspace from "./NativeWorkspace";
import App from "./App";
import DataToolsWorkspace from "./DataToolsWorkspace";
import Gxt2Workspace from "./Gxt2Workspace";
import MapWorkbench, { newMapTemplate } from "./MapWorkbench";
import GraphWorkbench from "./GraphWorkbench";
import RuntimeWorkbench from "./RuntimeWorkbench";
import RenderWorkbench from "./RenderWorkbench";
import RecipeConversionPanel from "./RecipeConversionPanel";
import VehicleIdentityEditor from "./VehicleIdentityEditor";
import VehicleHitchEditor from "./VehicleHitchEditor";
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
  // Match the canonical paths returned by real native dialogs and Python.
  // Hosted Windows TEMP may use RUNNER~1 rather than the full profile name.
  const root = realpathSync.native(mkdtempSync(join(tmpdir(), "allin1-react-authoring-"))); roots.push(root);
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

for (const mode of ["whole_archive", "member"]) {
it.runIf(process.env.ALLIN1_NATIVE_RPF_TEST === "1")(`RPF publication React native ${mode} export retains exact build-to-install lineage`, async context => {
  const { files, paths, client, user, invoke, python, sdk } = fixture();
  if (mode === "member") {
    const guard = spawnSync(python, ["-c", `from allin1_sdk.rpf_tools import RpfExplorerService
RpfExplorerService._require_game_closed()`], { cwd: sdk, encoding: "utf8", windowsHide: true, timeout: 30000,
      env: { ...process.env, PYTHONPATH: join(sdk, "src") } });
    if (guard.status !== 0 && guard.stderr.includes("Close GTA V before changing an RPF archive")) context.skip();
    expect(guard.status, guard.stderr || guard.stdout).toBe(0);
  }
  const setup = spawnSync(python, ["-c", `import json,sys
from pathlib import Path
from allin1_sdk.paths import project_root
from allin1_sdk.rpf_builder import RpfArchiveBuilder
from allin1_sdk.rpf_tools import RpfExplorerService
from allin1_sdk.gxt2_workspace import Gxt2Workspace
from allin1_sdk import gxt2_desktop as desktop
root=Path(sys.argv[1]);source=root/'source';source.mkdir()
nested=source/'x64/american.rpf.source';nested.mkdir(parents=True)
for folder,text in ((source,'Root dictionary'),(nested,'Nested dictionary')):
 (folder/'global.gxt2').write_bytes(Gxt2Workspace.encode([{'hash':256,'text':text}]))
game=root/'decoder';game.mkdir();(game/'GTA5_Enhanced.exe').write_bytes(b'non-executable test marker')
# Only the global process check is isolated. Every archive is test-owned and
# native indexing/replacement/verification remains real; never launch this marker.
assert root.parent.name.startswith('allin1-react-authoring-')
assert (game/'GTA5_Enhanced.exe').read_bytes()==b'non-executable test marker'
RpfExplorerService._require_game_closed=staticmethod(lambda:None)
archive,_=RpfArchiveBuilder(project_root(),game).build(source,root/'text-fixture.rpf')
service=RpfExplorerService(project_root(),game);index=service.index(archive)
raw,binding=service.read_gxt2_entry(index,index.entry('x64/american.rpf::global.gxt2'))
binding['gta_path']=str(game)
workspace=Gxt2Workspace().export_bytes('global.gxt2',raw,root/'text-workspace',source_binding=binding)
Gxt2Workspace.set_text(workspace,256,'Edited dictionary')
context={'workspace':str(workspace)};session=desktop.inspect(context)
payload={**context,'action':'package_rpf','destination':str(root/'rpf-build'),'expected_state_sha256':session['state_sha256']}
review=desktop.review(payload);built=desktop.apply({**payload,'review_sha256':review['review_sha256'],'authoring_confirmed':True})
print(json.dumps({'workspace':str(workspace),'build':built['destination'],'archive':str(archive),'game':str(game)}))`, files],
    { cwd: sdk, encoding: "utf8", windowsHide: true, timeout: 60000, env: { ...process.env, PYTHONPATH: join(sdk, "src") } });
  expect(setup.status, setup.stderr || setup.stdout).toBe(0);
  const input = JSON.parse(setup.stdout), original = readFileSync(input.archive);
  paths.gxt2_workspace = input.workspace; paths.rpf_package_source = input.build;
  client.applyGxt2Action = vi.fn(async payload => invoke("apply_gxt2_action", payload));
  const destination = join(files, "published.zip");
  client.selectPackageZipDestination = vi.fn(async () => destination);
  render(<Gxt2Workspace client={client} onGuardChange={() => {}} />);
  await user.click(screen.getByRole("button", { name: "Open text workspace" }));
  await user.click(await screen.findByRole("button", { name: "Configure ALLIN1 export" }));
  await user.click(screen.getByRole("button", { name: "Choose RPF build folder" }));
  await user.type(screen.getByLabelText("Author", { exact: true }), "Native fixture");
  await user.type(screen.getByLabelText("GTA-relative archive destination"), "mods/update/text-fixture.rpf");
  await user.selectOptions(screen.getByLabelText("Export scope"), mode);
  await user.click(screen.getByRole("button", { name: "Review ALLIN1 ZIP" }));
  await screen.findByRole("heading", { name: "Review: Export ALLIN1 ZIP" });
  expect(screen.getByText("sdk-artifact.json")).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Export ALLIN1 ZIP" }));
  expect(await screen.findByRole("status")).toHaveTextContent("ALLIN1 ZIP exported and validated");
  expect(readFileSync(input.archive)).toEqual(original);
  const installed = spawnSync(python, ["-c", `import json,sys,shutil,zipfile
from pathlib import Path
from allin1_sdk.artifact_contract import validate_manifest
root=Path(sys.argv[1]);mode=sys.argv[2]
sys.path.insert(0,str(Path.cwd().parent/'ALLIN1/src'))
from allin1.mods import ModIntegrationService,open_mod_package
with zipfile.ZipFile(root/'published.zip') as archive:
 artifact=validate_manifest(json.loads(archive.read('sdk-artifact.json')))
 evidence=json.loads(archive.read('allin1.rpf-build.json'))
 assert evidence['input_build_status']=='recorded'
 assert evidence['input_build']['build_fingerprint']==artifact['build']['build_fingerprint']
 assert artifact['build']['mode'].startswith('development_')
 assert set(artifact['outputs'])==set(archive.namelist())-{'sdk-artifact.json'}
if mode=='member':
 from scripts.smoke_rpf_member_install import verify_export
 from allin1_sdk.gxt2_workspace import Gxt2Workspace
 verify_export(root/'published.zip',root/'text-fixture.rpf',Path.cwd()/'tools/RpfPatcher/RpfPatcher.exe',root/'decoder',Path.cwd().parent/'ALLIN1/src',Gxt2Workspace.encode([{'hash':256,'text':'Root dictionary'}]))
else:
 game=root/'install-test';game.mkdir();(game/'GTA5_Enhanced.exe').write_bytes(b'test marker')
 target=game/'mods/update/text-fixture.rpf';target.parent.mkdir(parents=True);shutil.copyfile(root/'text-fixture.rpf',target)
 service=ModIntegrationService(game);service._check_dependencies=lambda _:None
 with open_mod_package(root/'published.zip') as manifest:
  service.install(manifest)
  receipt=service._read_receipt(manifest.mod_id)['sdk_provenance']
  assert receipt['artifact_id']==artifact['artifact_id']
  assert receipt['build_fingerprint']==artifact['build']['build_fingerprint']
  service.uninstall(manifest.mod_id)
 assert target.read_bytes()==(root/'text-fixture.rpf').read_bytes()
print('Lineage verified')`, files, mode],
    { cwd: sdk, encoding: "utf8", windowsHide: true, timeout: 60000, env: { ...process.env, PYTHONPATH: `${join(sdk, "src")};${sdk}` } });
  expect(installed.status, installed.stderr || installed.stdout).toBe(0);
  expect(installed.stdout).toContain("Lineage verified");
  expect(readFileSync(input.archive)).toEqual(original);
}, 120000);
}

it("code draft survives keyboard Back, sidebar navigation and native close requests", async () => {
  const { client, user } = fixture();
  client.initialLaunchRequest = async () => null;
  let closeRequest = () => {};
  client.onCloseRequested = async callback => { closeRequest = callback; return () => {}; };
  client.closeWindow = vi.fn();
  render(<App client={client} />);
  await user.click(await screen.findByRole("button", { name: /Data Tools/ }));
  await user.click(await screen.findByRole("button", { name: "XML, JSON & Lua editor" }));
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
  await user.click(screen.getByRole("button", { name: "XML, JSON & Lua editor" }));
  await user.click(screen.getByRole("button", { name: "Open XML / JSON / Lua" }));
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
  await user.click(screen.getByRole("button", { name: "XML, JSON & Lua editor" }));
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
  await user.click(screen.getByRole("button", { name: "XML, JSON & Lua editor" }));
  await user.click(screen.getByRole("button", { name: "Open XML / JSON / Lua" }));
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

it("JSON editor repairs real source, blocks invalid saves, and preserves reviewed bytes and a backup", async () => {
  const { files, paths, client, user } = fixture();
  const source = join(files, "config.json"); paths.code_source = source;
  const original = '{\n  "broken":\n}';
  writeFileSync(source, original);
  render(<DataToolsWorkspace client={client} onGuardChange={() => {}} />);
  await user.click(screen.getByRole("button", { name: "XML, JSON & Lua editor" }));
  await user.click(screen.getByRole("button", { name: "Open XML / JSON / Lua" }));
  await screen.findByRole("textbox", { name: "JSON source editor" });
  expect(screen.getByText(/Line 3, column 1/)).toBeInTheDocument();
  editCode("JSON", '{"value": NaN}');
  await user.click(screen.getByRole("button", { name: "Review save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Syntax check failed");
  expect(readFileSync(source, "utf8")).toBe(original);
  const draft = '{ "enabled": true, "value": 123456789012345678901234567890 }\n';
  editCode("JSON", draft);
  expect(screen.getByRole("button", { name: "Metadata reports" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Check syntax" }));
  expect(await screen.findByText("Syntax check passed")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Review save" }));
  await screen.findByRole("region", { name: "Code save diff" });
  expect(readFileSync(source, "utf8")).toBe(original);
  await confirm(user);
  expect(readFileSync(source, "utf8")).toBe(draft);
  const result = await (client.applyWorkspaceAction as ReturnType<typeof vi.fn>).mock.results[0].value;
  expect(readFileSync(result.payload.result.backup, "utf8")).toBe(original);
}, 30000);

it("JSON editor creates a new source document through reviewed save-copy", async () => {
  const { files, client, user } = fixture();
  render(<DataToolsWorkspace client={client} onGuardChange={() => {}} />);
  await user.click(screen.getByRole("button", { name: "XML, JSON & Lua editor" }));
  await user.click(screen.getByRole("button", { name: "New JSON" }));
  await screen.findByRole("textbox", { name: "JSON source editor" });
  editCode("JSON", '{"new": true}\n');
  expect(screen.getByLabelText("New copy filename")).toHaveValue("untitled.json");
  await user.click(screen.getByRole("button", { name: "Review save a copy" }));
  expect(existsSync(join(files, "untitled.json"))).toBe(false);
  await confirm(user);
  expect(readFileSync(join(files, "untitled.json"), "utf8")).toBe('{"new": true}\n');
}, 30000);

it("native model workspace displays and exports the real evidence-scoped asset report", async () => {
  const {files,client,user,sdk}=fixture();
  const root=join(files,"native-model");mkdirSync(root);
  mkdirSync(join(root,"original"));mkdirSync(join(root,"edit"));mkdirSync(join(root,"edit","assets"));
  const original=Buffer.from("SDK-owned test snapshot");
  writeFileSync(join(root,"original","asset.ydr"),original);
  writeFileSync(join(root,"edit","asset.ydr.xml"),readFileSync(join(sdk,"tests","fixtures","animation_skin.ydr.xml")));
  writeFileSync(join(root,"native-workspace.json"),JSON.stringify({schema_version:1,operation:"native_asset_workspace",edition:"Legacy",
    source:{name:"asset.ydr",snapshot:"original/asset.ydr",size:original.length,sha256:createHash("sha256").update(original).digest("hex")},
    xml:{path:"edit/asset.ydr.xml"},dependencies:[]}));
  render(<NativeWorkspace client={client} request={{workspace:root,requestId:1}} onGuardChange={()=>{}}/>);
  expect(await screen.findByLabelText("Skeleton ambiguity & transforms")).toHaveTextContent("pass");
  expect(screen.getByLabelText("Attachment placement")).toHaveTextContent("not checked");
  await user.click(screen.getByRole("button",{name:"Review asset report export"}));
  const output=join(files,"asset.ydr.asset-validation.json");
  expect(existsSync(output)).toBe(false);
  await confirm(user);
  const report=JSON.parse(readFileSync(output,"utf8"));
  expect(report.runtime_status).toBe("not_tested");
  expect(report.static_status).toBe("incomplete");
  expect(report.source_identity.original_sha256).toBe(createHash("sha256").update(original).digest("hex"));
  expect(readFileSync(join(root,"original","asset.ydr"))).toEqual(original);
},30000);

it.runIf(process.env.ALLIN1_NATIVE_RPF_TEST === "1").each(["Legacy", "Enhanced"])("native rebuild React preserves actual %s SDK/helper provenance", async edition => {
  const {files,client,user,sdk,python}=fixture();
  const prepared=spawnSync(python,["-c",`import pathlib,sys,subprocess
from allin1_sdk.native_assets import NativeAssetInspector
sdk=pathlib.Path(sys.argv[1]);root=pathlib.Path(sys.argv[2]);edition=sys.argv[3]
source=root/'source.ydr'
subprocess.run([str(sdk/'tools/RpfPatcher/RpfPatcher.exe'),'asset-from-xml',str(sdk/'tests/fixtures/animation_skin.ydr.xml'),str(source),str(root),'legacy' if edition=='Legacy' else 'gen9'],check=True,capture_output=True)
NativeAssetInspector(sdk).export_workspace(source,root/'native',edition=edition)
`,sdk,files,edition],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:30000,env:{...process.env,PYTHONPATH:join(sdk,"src")}});
  if(prepared.status!==0)throw new Error(prepared.stderr||String(prepared.error));
  const original=readFileSync(join(files,"source.ydr"));
  render(<NativeWorkspace client={client} request={{workspace:join(files,"native"),requestId:1}} onGuardChange={()=>{}}/>);
  await screen.findByLabelText("Native output filename");
  await user.clear(screen.getByLabelText("Native output filename"));
  await user.type(screen.getByLabelText("Native output filename"),"rebuilt.ydr");
  await user.click(screen.getByRole("button",{name:"Review verified native build"}));
  expect(await screen.findByText("Native build identity")).toBeInTheDocument();
  expect(existsSync(join(files,"rebuilt.ydr"))).toBe(false);
  await confirm(user);
  const report=JSON.parse(readFileSync(join(files,"rebuilt.ydr.allin1.json"),"utf8"));
  expect(report.artifact.edition).toBe(edition);
  expect(report.artifact.outputs["rebuilt.ydr"]).toBe(createHash("sha256").update(readFileSync(join(files,"rebuilt.ydr"))).digest("hex"));
  expect(report.artifact.build.resource_files["tools/RpfPatcher/RpfPatcher.exe"]).toBe(createHash("sha256").update(readFileSync(join(sdk,"tools/RpfPatcher/RpfPatcher.exe"))).digest("hex"));
  expect(screen.getByText(new RegExp(report.artifact.artifact_id))).toBeInTheDocument();
  expect(readFileSync(join(files,"source.ydr"))).toEqual(original);
  expect(readFileSync(join(files,"native/original/source.ydr"))).toEqual(original);
},60000);

it("package validation connects model and metadata evidence to a reviewed React export", async () => {
  const {files,paths,client,user,sdk}=fixture();
  const source=join(files,"package"), comparison=join(files,"comparison");mkdirSync(source);mkdirSync(comparison);
  const metadata=(texture:string)=>`<CVehicleModelInfo__InitDataList><InitDatas><Item><modelName>car</modelName><txdName>${texture}</txdName></Item></InitDatas></CVehicleModelInfo__InitDataList>`;
  writeFileSync(join(source,"car.ydr.xml"),readFileSync(join(sdk,"tests/fixtures/animation_skin.ydr.xml")));
  writeFileSync(join(source,"vehicles.meta"),metadata("first"));
  writeFileSync(join(comparison,"vehicles.meta"),metadata("second"));
  paths.package_folder=source;
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Validate asset package"}));
  await user.click(screen.getByRole("button",{name:"Choose source"}));
  paths.package_folder=comparison;
  await user.click(screen.getByRole("button",{name:"Choose comparison context"}));
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  expect(await screen.findByLabelText("Metadata collisions")).toHaveTextContent("intentional replacement or a conflict");
  expect(screen.getByLabelText("Skin weights & palettes")).toHaveTextContent("pass");
  expect(screen.getByLabelText("Attachment placement")).toHaveTextContent("not checked");
  await user.click(screen.getByRole("button",{name:"Review asset report export"}));
  const output=join(files,"asset-validation-report","asset-validation.json");
  expect(existsSync(output)).toBe(false);
  await confirm(user);
  const report=JSON.parse(readFileSync(output,"utf8"));
  expect(report.files).toHaveLength(2);
  expect(report.runtime_status).toBe("not_tested");
  expect(report.comparison_definition_count).toBe(1);
  expect(readFileSync(join(source,"vehicles.meta"),"utf8")).toBe(metadata("first"));
},30000);

it.runIf(process.env.ALLIN1_NATIVE_RPF_TEST==="1")("native metadata React report decodes archetypes and exports exact source/XML hashes",async()=>{
  const {files,paths,client,user,python,sdk}=fixture();
  const setup=spawnSync(python,["-c",`from pathlib import Path
import sys
from test_native_relationships import YTYP
from allin1_sdk.paths import project_root
from allin1_sdk.processes import run_hidden
root=Path(sys.argv[1]);source=root/'fixture.ytyp.xml';source.write_bytes(YTYP)
package=root/'package';package.mkdir()
result=run_hidden([str(project_root()/'tools/RpfPatcher/RpfPatcher.exe'),'asset-from-xml',str(source),str(package/'fixture.ytyp'),str(root),'gen9'],capture_output=True,text=True,timeout=60)
assert result.returncode==0,result.stderr
print(package)`,files],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:60000,
    env:{...process.env,PYTHONPATH:[join(sdk,"src"),join(sdk,"tests")].join(process.platform==="win32"?";":":")}});
  expect(setup.status,setup.stderr||setup.stdout).toBe(0);
  paths.package_folder=setup.stdout.trim();
  const original=readFileSync(join(paths.package_folder,"fixture.ytyp"));
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Validate asset package"}));
  await user.click(screen.getByRole("button",{name:"Choose source"}));
  await user.selectOptions(screen.getByLabelText("Package validation edition"),"Enhanced");
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  await user.click(await screen.findByText("Metadata decoding evidence · 1 sources"));
  expect(screen.getByRole("table",{name:"Metadata source and decoding coverage"})).toHaveTextContent("1 mapped definitions");
  await user.click(screen.getByRole("button",{name:"Review asset report export"}));
  await confirm(user);
  const report=JSON.parse(readFileSync(join(files,"asset-validation-report/asset-validation.json"),"utf8"));
  expect(report.metadata_evidence[0].source_sha256).toBe(createHash("sha256").update(original).digest("hex"));
  expect(report.metadata_evidence[0].xml_sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(report.definition_count).toBe(1);
  expect(report.runtime_status).toBe("not_tested");
  expect(readFileSync(join(paths.package_folder,"fixture.ytyp"))).toEqual(original);
},60000);

it("package texture costs render from real validated DDS payloads",async()=>{
  const {files,paths,client,user,python,sdk}=fixture();
  const setup=spawnSync(python,["-c",`from pathlib import Path
import sys
from test_texture_validation import package_fixture
print(package_fixture(Path(sys.argv[1])))`,files],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:30000,
    env:{...process.env,PYTHONPATH:[join(sdk,"src"),join(sdk,"tests")].join(process.platform==="win32"?";":":")}});
  if(setup.status!==0) throw new Error(setup.stderr);
  paths.package_folder=setup.stdout.trim();
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Validate asset package"}));
  await user.click(screen.getByRole("button",{name:"Choose source"}));
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  expect(await screen.findByText("Validated texture storage subtotal · 4 bytes")).toBeInTheDocument();
  expect(screen.getByLabelText("Texture dependencies")).toHaveTextContent("pass");
  expect(screen.getByRole("table",{name:"Verified texture payload costs"})).toHaveTextContent("fixture_diffuse");
  await user.click(screen.getByRole("button",{name:"Review asset report export"}));
  await confirm(user);
  const report=JSON.parse(readFileSync(join(files,"asset-validation-report/asset-validation.json"),"utf8"));
  expect(report.texture_resolutions[0].payload_sha256).toBe(report.texture_costs[0].sha256);
},30000);

it("parent texture React report resolves only selected shared context and exports its metadata hash",async()=>{
  const {files,paths,client,user,python,sdk}=fixture();
  const setup=spawnSync(python,["-c",`from pathlib import Path
import sys,json
from test_texture_parents import parent_package
source,context,metadata=parent_package(Path(sys.argv[1]))
print(json.dumps({'source':str(source),'context':str(context),'metadata':str(metadata)}))`,files],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:30000,
    env:{...process.env,PYTHONPATH:[join(sdk,"src"),join(sdk,"tests")].join(process.platform==="win32"?";":":")}});
  if(setup.status!==0)throw new Error(setup.stderr);
  const input=JSON.parse(setup.stdout),original=readFileSync(input.metadata);
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Validate asset package"}));
  paths.package_folder=input.source;await user.click(screen.getByRole("button",{name:"Choose source"}));
  paths.package_folder=input.context;await user.click(screen.getByRole("button",{name:"Choose comparison context"}));
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  await user.click(await screen.findByText("Resolved texture dependencies · 1"));
  await user.click(screen.getByText("fixture_diffuse · comparison:shared.ytd.xml"));
  expect(screen.getByRole("list",{name:"Selected texture lookup chain"})).toHaveTextContent("shared · comparison:shared.ytd.xml");
  await user.click(screen.getByText("Texture parent declaration provenance · 1"));
  expect(screen.getByRole("table",{name:"Selected texture parent metadata"})).toHaveTextContent(createHash("sha256").update(original).digest("hex"));
  await user.click(screen.getByRole("button",{name:"Review asset report export"}));await confirm(user);
  const report=JSON.parse(readFileSync(join(files,"asset-validation-report/asset-validation.json"),"utf8"));
  expect(report.texture_resolutions[0].parent_chain).toEqual([{child:"paint",parent:"shared"}]);
  expect(report.runtime_status).toBe("not_tested");
  expect(readFileSync(input.metadata)).toEqual(original);
},30000);

it("package attachment anchors render and export real metadata-to-model evidence",async()=>{
  const {files,paths,client,user,python,sdk}=fixture();
  const setup=spawnSync(python,["-c",`from pathlib import Path
import sys
from test_attachment_validation import package_fixture
print(package_fixture(Path(sys.argv[1])/'package'))`,files],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:30000,
    env:{...process.env,PYTHONPATH:[join(sdk,"src"),join(sdk,"tests")].join(process.platform==="win32"?";":":")}});
  if(setup.status!==0) throw new Error(setup.stderr);
  paths.package_folder=setup.stdout.trim();
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Validate asset package"}));
  await user.click(screen.getByRole("button",{name:"Choose source"}));
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  await user.click(await screen.findByText("Resolved attachment anchors · 1"));
  await user.click(screen.getByText("WEAPON_TEST → COMPONENT_TEST · tip"));
  expect(screen.getByRole("table",{name:"Authored skeleton anchor matrix · tip"})).toHaveTextContent("1.00000");
  expect(screen.getByLabelText("Attachment placement")).toHaveTextContent("in-game assembly have not been proved");
  await user.click(screen.getByText("Declared attachment placement · 0 pairs"));
  const parent = screen.getByLabelText("Assembly parent"), child = screen.getByLabelText("Assembly child");
  await user.selectOptions(parent, within(parent).getByRole("option", {name:"package:body.ydr.xml · drawable 0"}));
  await user.selectOptions(child, within(child).getByRole("option", {name:"package:clip.ydr.xml · drawable 0"}));
  await user.type(screen.getByLabelText("Parent anchor bone"), "tip");
  fireEvent.change(screen.getByLabelText("Local offset X"), {target:{value:"2"}});
  await user.click(screen.getByRole("button", {name:"Add declared assembly pair"}));
  expect(screen.getByRole("button", {name:"Review report export"})).toBeDisabled();
  expect(screen.getByRole("button", {name:"XML, JSON & Lua editor"})).toBeDisabled();
  await user.click(screen.getByText("Declared attachment placement · 1 pairs"));
  await user.click(screen.getByRole("button", {name:"Inspect data"}));
  await user.click(await screen.findByText("Declared assembly evidence · 1 pairs"));
  await user.click(screen.getByText("Pair 1 · checked"));
  expect(screen.getByRole("table", {name:"Declared child-to-parent matrix · pair 1"})).toHaveTextContent("2.00000");
  await user.click(screen.getByRole("button",{name:"Review asset report export"}));
  await confirm(user);
  const report=JSON.parse(readFileSync(join(files,"asset-validation-report/asset-validation.json"),"utf8"));
  expect(report.attachment_bindings[0]).toMatchObject({parent_source:"body.ydr.xml",child_source:"clip.ydr.xml",bone_tag:42});
  expect(report.attachment_bindings[0].skeleton_matrix[2][3]).toBe(1);
  expect(report.assembly_evidence[0].child_to_parent_matrix[0][3]).toBe(2);
  expect(report.assembly_evidence[0].child_to_parent_matrix[2][3]).toBe(1);
  expect(report.assembly_evidence[0].relative_anchor_error).toBe(0);
  expect(report.runtime_status).toBe("not_tested");
},30000);

it("fragment React report exports real child relationships and separate authored matrices",async()=>{
  const {files,paths,client,user,python,sdk}=fixture();
  const setup=spawnSync(python,["-c",`from pathlib import Path
import sys
from lxml import etree
from test_fragment_validation import fragment
root=Path(sys.argv[1])/'package'
root.mkdir()
(root/'fixture.yft.xml').write_bytes(etree.tostring(fragment()))
print(root)`,files],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:30000,
    env:{...process.env,PYTHONPATH:[join(sdk,"src"),join(sdk,"tests")].join(process.platform==="win32"?";":":")}});
  if(setup.status!==0)throw new Error(setup.stderr);
  paths.package_folder=setup.stdout.trim();
  const original=readFileSync(join(paths.package_folder,"fixture.yft.xml"));
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Validate asset package"}));
  await user.click(screen.getByRole("button",{name:"Choose source"}));
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  await user.click(await screen.findByText("Fragment physics-child evidence · 1"));
  await user.click(screen.getByText("fixture.yft.xml · LOD1 child 0"));
  expect(screen.getByRole("table",{name:"Physics matrix · serialized 4×4 rows"})).toHaveTextContent("5.00000");
  await user.click(screen.getByRole("button",{name:"Review asset report export"}));await confirm(user);
  const report=JSON.parse(readFileSync(join(files,"asset-validation-report/asset-validation.json"),"utf8"));
  expect(report.fragment_children[0]).toMatchObject({source:"fixture.yft.xml",bone_tag:42,bone_indices:[1],position_offset:[2,3,4]});
  expect(report.fragment_children[0].drawables[0].matrix[3]).toEqual([8,9,10]);
  expect(report.runtime_status).toBe("not_tested");
  expect(readFileSync(join(paths.package_folder,"fixture.yft.xml"))).toEqual(original);
},30000);

it("shared-rig React report requires explicit hashed owners and revalidation before export",async()=>{
  const {files,paths,client,user,python,sdk}=fixture();
  const setup=spawnSync(python,["-c",`from pathlib import Path
import sys,json
from test_shared_rig_validation import fixture
root,context,binding=fixture(Path(sys.argv[1]))
print(json.dumps({'source':str(root),'comparison':str(context),'binding':binding}))`,files],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:30000,
    env:{...process.env,PYTHONPATH:[join(sdk,"src"),join(sdk,"tests")].join(process.platform==="win32"?";":":")}});
  if(setup.status!==0)throw new Error(setup.stderr);
  const input=JSON.parse(setup.stdout),original=readFileSync(join(input.source,"car.ydr.xml"));
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Validate asset package"}));
  paths.package_folder=input.source;await user.click(screen.getByRole("button",{name:"Choose source"}));
  paths.package_folder=input.comparison;await user.click(screen.getByRole("button",{name:"Choose comparison context"}));
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  await user.click(await screen.findByText("Explicit shared-rig context · 0 selected"));
  expect(screen.getByRole("button",{name:"Use selected shared rig"})).toBeDisabled();
  await user.selectOptions(screen.getByLabelText("Model drawable"),JSON.stringify([input.binding.model,0,input.binding.model_sha256]));
  await user.selectOptions(screen.getByLabelText("Shared skeleton drawable"),JSON.stringify([input.binding.rig,0,input.binding.rig_sha256]));
  await user.click(screen.getByRole("button",{name:"Use selected shared rig"}));
  expect(screen.getByRole("button",{name:"Review report export"})).toBeDisabled();
  expect(screen.getByRole("button",{name:"Optimize & recover"})).toBeDisabled();
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  expect(await screen.findByRole("heading",{name:"Skin weights & palettes · pass"})).toBeInTheDocument();
  await user.click(screen.getByText("Selected shared-rig evidence · 1"));
  await user.click(screen.getByRole("button",{name:"Review report export"}));await confirm(user);
  const report=JSON.parse(readFileSync(join(files,"asset-validation-report/asset-validation.json"),"utf8"));
  expect(report.shared_rigs[0]).toMatchObject(input.binding);
  expect(report.runtime_status).toBe("not_tested");
  expect(readFileSync(join(input.source,"car.ydr.xml"))).toEqual(original);
},30000);

it("validation-to-optimization handoff retains explicit rigs and shared texture context",async()=>{
  const {files,paths,client,user,python,sdk}=fixture();
  const setup=spawnSync(python,["-c",`from pathlib import Path
import sys,json
from test_optimization_context import shared_request
print(json.dumps(shared_request(Path(sys.argv[1]))))`,files],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:30000,
    env:{...process.env,PYTHONPATH:[join(sdk,"src"),join(sdk,"tests")].join(process.platform==="win32"?";":":")}});
  if(setup.status!==0)throw new Error(setup.stderr);
  const input=JSON.parse(setup.stdout),binding=input.settings.rig_bindings[0];
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Validate asset package"}));
  paths.package_folder=input.source;await user.click(screen.getByRole("button",{name:"Choose source"}));
  paths.package_folder=input.comparison;await user.click(screen.getByRole("button",{name:"Choose comparison context"}));
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  await user.click(await screen.findByText("Explicit shared-rig context · 0 selected"));
  await user.selectOptions(screen.getByLabelText("Model drawable"),JSON.stringify([binding.model,binding.drawable,binding.model_sha256]));
  await user.selectOptions(screen.getByLabelText("Shared skeleton drawable"),JSON.stringify([binding.rig,binding.rig_drawable,binding.rig_sha256]));
  await user.click(screen.getByRole("button",{name:"Use selected shared rig"}));
  await user.click(screen.getByText("Declared attachment placement · 0 pairs"));
  await user.selectOptions(screen.getByLabelText("Assembly parent"),JSON.stringify([binding.model,binding.drawable,binding.model_sha256]));
  await user.selectOptions(screen.getByLabelText("Assembly child"),JSON.stringify([binding.rig,binding.rig_drawable,binding.rig_sha256]));
  await user.type(screen.getByLabelText("Parent anchor bone"),"tip");
  await user.click(screen.getByRole("button",{name:"Add declared assembly pair"}));
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  await user.click(await screen.findByRole("button",{name:"Optimize with this validation context"}));
  expect(screen.getByText(/Validation context copied from the asset report/)).toBeInTheDocument();
  expect(screen.queryByRole("button",{name:"Queue texture candidate"})).not.toBeInTheDocument();
  await user.click(screen.getByText("Optimization validation context"));
  expect(screen.getByText(input.comparison)).toBeInTheDocument();
  await user.click(screen.getByText("Explicit shared-rig context · 1 selected"));
  expect(screen.getByRole("button",{name:"Remove shared rig 1"})).toBeInTheDocument();
  await user.click(screen.getByText("Declared attachment placement · 1 pairs"));
  expect(screen.getByRole("button",{name:"Remove assembly pair 1"})).toBeInTheDocument();
  await user.click(screen.getByRole("button",{name:"Inspect optimization inputs"}));
  await user.selectOptions(await screen.findByLabelText("Declared material role"),"color");
  fireEvent.change(screen.getByLabelText("Candidate mip count"),{target:{value:"5"}});
  await user.click(screen.getByRole("button",{name:"Queue texture candidate"}));
  await user.click(screen.getByRole("button",{name:"Preview optimization candidates"}));
  await user.click(await screen.findByRole("button",{name:"Review optimized package export"}));await confirm(user);
  const receipt=JSON.parse(readFileSync(join(files,"optimized-package/optimization.json"),"utf8"));
  expect(receipt.validation_context.shared_rig_count).toBe(1);
  for(const name of ["before_report","after_report"]){
    expect(receipt[name].shared_rigs[0]).toMatchObject(binding);
    expect(receipt[name].assembly_evidence[0]).toMatchObject({parent:binding.model,child:binding.rig,parent_bone:"tip",child_bone:"",status:"checked"});
    expect(receipt[name].texture_resolutions.some((row:{dictionary:string})=>row.dictionary==="comparison:shared.ytd.xml")).toBe(true);
  }
  expect(receipt.before_report.assembly_evidence).toEqual(receipt.after_report.assembly_evidence);
  expect(existsSync(join(files,"optimized-package/package/shared.ydr.xml"))).toBe(false);
},60000);

it("optimization React refuses a normal-map candidate even after a color declaration",async()=>{
  const {files,paths,client,user,python,sdk}=fixture();
  const setup=spawnSync(python,["-c",`from pathlib import Path
import json,sys
from test_optimization_package import request
from test_material_roles import change_slot
payload=request(Path(sys.argv[1]));change_slot(payload['source'],'BumpSampler',mixed=True)
print(json.dumps(payload))`,files],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:30000,
    env:{...process.env,PYTHONPATH:[join(sdk,"src"),join(sdk,"tests")].join(process.platform==="win32"?";":":")}});
  expect(setup.status,setup.stderr||setup.stdout).toBe(0);
  paths.package_folder=JSON.parse(setup.stdout).source;
  const original=readFileSync(join(paths.package_folder,"assets/diffuse.dds"));
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Optimize & recover"}));
  await user.click(screen.getByRole("button",{name:"Choose optimization package"}));
  await user.click(screen.getByRole("button",{name:"Inspect optimization inputs"}));
  await user.selectOptions(await screen.findByLabelText("Declared material role"),"color");
  expect(screen.getByText(/Color conversion blocked for this material usage/)).toHaveTextContent("normal");
  expect(screen.getByRole("button",{name:"Queue texture candidate"})).toBeDisabled();
  expect(screen.queryByRole("button",{name:"Review optimized package export"})).not.toBeInTheDocument();
  expect(readFileSync(join(paths.package_folder,"assets/diffuse.dds"))).toEqual(original);
},30000);

it("optimization React path previews measured candidates, exports exact originals, and recovers them",async()=>{
  const {files,paths,client,user,python,sdk}=fixture(),guard=vi.fn();
  const setup=spawnSync(python,["-c",`from pathlib import Path
import json,sys
from test_optimization_package import request
print(json.dumps(request(Path(sys.argv[1]))))`,files],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:30000,
    env:{...process.env,PYTHONPATH:[join(sdk,"src"),join(sdk,"tests")].join(process.platform==="win32"?";":":")}});
  if(setup.status!==0)throw new Error(setup.stderr);
  paths.package_folder=JSON.parse(setup.stdout).source;
  const original=readFileSync(join(paths.package_folder,"assets/diffuse.dds"));
  render(<DataToolsWorkspace client={client} onGuardChange={guard}/>);
  await user.click(screen.getByRole("button",{name:"Optimize & recover"}));
  await user.click(screen.getByRole("button",{name:"Choose optimization package"}));
  await user.click(screen.getByRole("button",{name:"Inspect optimization inputs"}));
  expect(await screen.findByRole("button",{name:"Queue texture candidate"})).toBeDisabled();
  await user.selectOptions(screen.getByLabelText("Declared material role"),"color");
  fireEvent.change(screen.getByLabelText("Candidate mip count"),{target:{value:"5"}});
  await user.click(screen.getByRole("button",{name:"Queue texture candidate"}));
  expect(guard).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole("button",{name:"Preview optimization candidates"}));
  expect(await screen.findByAltText("Before fixture_diffuse")).toHaveAttribute("src",expect.stringContaining("data:image/png;base64,"));
  expect(screen.getByAltText("After fixture_diffuse")).toBeInTheDocument();
  await user.click(screen.getByText("Exact pixel comparison · fixture_diffuse"));
  await user.click(screen.getByRole("button",{name:"Inspect exact pixels fixture_diffuse"}));
  expect(await screen.findByAltText("before exact pixels fixture_diffuse")).toHaveAttribute("width","64");
  expect(screen.getByAltText("after exact pixels fixture_diffuse")).toHaveAttribute("width","64");
  expect(screen.getByAltText("difference exact pixels fixture_diffuse")).toBeInTheDocument();
  expect(screen.getByText(/All files outside the explicitly selected/)).toBeInTheDocument();
  await user.click(screen.getByRole("button",{name:"Review optimized package export"}));
  await confirm(user);
  const output=join(files,"optimized-package");
  const receipt=JSON.parse(readFileSync(join(output,"optimization.json"),"utf8"));
  expect(screen.getByRole("table",{name:"Texture costs fixture_diffuse"})).toBeInTheDocument();
  expect(screen.getByText(/not measured GPU residency, streaming behavior or driver allocations/)).toBeInTheDocument();
  expect(receipt.storage_delta_bytes).toBeLessThan(0);
  expect(readFileSync(join(output,"originals/assets/diffuse.dds"))).toEqual(original);
  expect(readFileSync(join(paths.package_folder,"assets/diffuse.dds"))).toEqual(original);
  paths.package_folder=output;
  await user.click(screen.getByRole("button",{name:"Open recovery package"}));
  await user.click(await screen.findByRole("button",{name:"Review exact original recovery"}));
  await confirm(user);
  expect(readFileSync(join(files,"recovered-package/assets/diffuse.dds"))).toEqual(original);
  expect(readFileSync(join(files,"recovered-package/car.ydr.xml"))).toEqual(readFileSync(join(output,"originals/car.ydr.xml")));
},30000);

it("diagnostic React path distinguishes file drift from crash causality and exports redacted evidence",async()=>{
  const {files,paths,client,user,python,sdk}=fixture();
  const setup=spawnSync(python,["-c",`from pathlib import Path
import sys,json
from test_diagnostic_trail import fixture,session_file
from test_crash_evidence import event_file,collected_session
root=Path(sys.argv[1]);payload,artifact,_=fixture(root)
session,_=session_file(root,payload,artifact)
crash=event_file(root,payload)
session=collected_session(root,payload,artifact,[{'attempt':1,'status':'observed','events':[crash.read_text()],'query_truncated':False}])
(root/'runtime.log').write_text('session_start controller=1.4.0'+chr(10)+'PrivateMachine'+chr(10)+'password=do-not-export'+chr(10))
(Path(payload['gta_path'])/'scripts/owned.asi').write_bytes(b'stale installed bytes')
print(json.dumps({**payload,'session':str(session),'crash':str(crash)}))`,files],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:30000,
    env:{...process.env,PYTHONPATH:[join(sdk,"src"),join(sdk,"tests")].join(process.platform==="win32"?";":":")}});
  if(setup.status!==0)throw new Error(setup.stderr);
  const input=JSON.parse(setup.stdout);
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Trace build to game"}));
  paths.code_source=input.source;
  await user.click(screen.getByRole("button",{name:"Choose artifact manifest"}));
  paths.code_source=input.comparison;
  await user.click(screen.getByRole("button",{name:"Choose installation receipt"}));
  paths.gta_folder=input.gta_path;
  await user.click(screen.getByRole("button",{name:"Choose diagnostic installation"}));
  paths.code_source=input.session;
  await user.click(screen.getByRole("button",{name:"Choose runtime session"}));
  paths.metadata=input.crash;
  await user.click(screen.getByRole("button",{name:"Choose crash event XML"}));
  await user.click(screen.getByRole("button",{name:"Clear crash event"}));
  await user.click(screen.getByText("Selected diagnostic log excerpts · 0"));
  paths.binary_source=join(files,"runtime.log");
  await user.click(screen.getByRole("button",{name:"Choose diagnostic log"}));
  fireEvent.change(screen.getByLabelText("Additional private terms (one per line)"),{target:{value:"PrivateMachine"}});
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  expect(await screen.findByText(/Build-to-game evidence · cause not established/)).toBeInTheDocument();
  expect(screen.getByText(/Current bytes differ from the installation receipt/,{selector:"li"})).toBeInTheDocument();
  expect(screen.getByText(/Normal exit and crash cannot be distinguished/,{selector:"li"})).toBeInTheDocument();
  expect(screen.getByLabelText("Redacted diagnostic preview")).not.toHaveTextContent("do-not-export");
  expect(screen.getByRole("button",{name:"Review report export"})).toBeDisabled();
  await user.click(screen.getByRole("checkbox",{name:/I reviewed these exact redacted excerpts/}));
  await user.click(screen.getByRole("button",{name:"Review report export"}));
  await confirm(user);
  const exported=readFileSync(join(files,"diagnostic-trail-report/diagnostic-trail.json"),"utf8");
  const report=JSON.parse(exported);
  expect(report.crash_cause).toBe("not_established");
  expect(report.crash_evidence.status).toBe("application_crash_recorded");
  expect(report.crash_evidence.source).toBe("launcher_session_event_query");
  expect(report.crash_evidence.matches[0].faulting_module).toBe("ntdll.dll");
  expect(exported).not.toContain("PRIVATE-");
  expect(report.findings.map((f:{code:string})=>f.code)).toContain("session_receipt_link");
  expect(report.findings.find((f:{code:string})=>f.code==="process_identity_observed").evidence.executable).toBe("<game>/GTA5.exe");
  expect(exported).not.toContain(files);
  expect(exported).not.toContain("PrivateMachine");
  const bundle=JSON.parse(readFileSync(join(files,"diagnostic-trail-report/diagnostic-bundle.json"),"utf8"));
  expect(bundle.artifact_id).toBe(report.artifact_id);
  expect(bundle.session_id).toBe(report.session_id);
  expect(readFileSync(join(files,"diagnostic-trail-report/log-01.txt"),"utf8")).toContain("session_start controller=1.4.0");
  expect(readFileSync(join(files,"diagnostic-trail-report/log-01.txt"),"utf8")).not.toContain("do-not-export");
},30000);

it.skipIf(process.env.ALLIN1_NATIVE_RPF_TEST!=="1")("installed RPF React diagnostics recheck an exact nested member and export without changing game bytes",async()=>{
  const {files,paths,client,user,python,sdk}=fixture();
  const setup=spawnSync(python,["-c",`from pathlib import Path
import sys,json,shutil
from test_package_intake import native_archive
from test_diagnostic_rpf import receipt_for
from allin1_sdk.workspace_desktop import file_hash
root=Path(sys.argv[1]);archive,game=native_archive(root,'Enhanced')
(game/'mods').mkdir();shutil.copyfile(archive,game/'mods/dlc.rpf')
artifact,receipt=receipt_for(file_hash(root/'source/vehicles.rpf.source/vehicles.meta'),'Enhanced')
(root/'artifact.json').write_text(json.dumps(artifact));(root/'receipt.json').write_text(json.dumps(receipt))
print(str(game))`,files],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:30000,
    env:{...process.env,PYTHONPATH:[join(sdk,"src"),join(sdk,"tests")].join(process.platform==="win32"?";":":")}});
  if(setup.status!==0)throw new Error(setup.stderr);
  const game=setup.stdout.trim(),original=readFileSync(join(game,"mods/dlc.rpf"));
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Trace build to game"}));
  paths.code_source=join(files,"artifact.json");await user.click(screen.getByRole("button",{name:"Choose artifact manifest"}));
  paths.code_source=join(files,"receipt.json");await user.click(screen.getByRole("button",{name:"Choose installation receipt"}));
  paths.gta_folder=game;await user.click(screen.getByRole("button",{name:"Choose diagnostic installation"}));
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  await user.click(await screen.findByText("Installed RPF member identities · 1"));
  expect(screen.getByRole("table",{name:"Current installed RPF members"})).toHaveTextContent("vehicles.rpf!vehicles.meta");
  expect(screen.getByRole("table",{name:"Current installed RPF members"})).toHaveTextContent("match");
  await user.click(screen.getByRole("button",{name:"Review report export"}));await confirm(user);
  const exported=JSON.parse(readFileSync(join(files,"diagnostic-trail-report/diagnostic-trail.json"),"utf8"));
  expect(exported.rpf_members[0].status).toBe("match");
  expect(exported.crash_cause).toBe("not_established");
  expect(readFileSync(join(game,"mods/dlc.rpf"))).toEqual(original);
},30000);

it.skipIf(process.env.ALLIN1_NATIVE_RPF_TEST!=="1").each(["folder","single RPF handoff"])("packed RPF React path validates exact members and optimizes with recoverable original containers (%s)",async(mode)=>{
  const {files,paths,client,user,python,sdk}=fixture();
  const setup=spawnSync(python,["-c",`from pathlib import Path
import sys,json,shutil
from test_package_intake import native_archive
root=Path(sys.argv[1]);archive,game=native_archive(root,'Enhanced',size=16)
package=root/'packed';package.mkdir();shutil.copyfile(archive,package/'dlc.rpf')
print(json.dumps({'archive':str(archive),'game':str(game),'package':str(package)}))`,files],{cwd:sdk,encoding:"utf8",windowsHide:true,timeout:60000,
    env:{...process.env,PYTHONPATH:[join(sdk,"src"),join(sdk,"tests")].join(process.platform==="win32"?";":":")}});
  if(setup.status!==0)throw new Error(setup.stderr);
  const input=JSON.parse(setup.stdout);paths.rpf=input.archive;paths.gta_folder=input.game;paths.package_folder=input.package;
  const original=readFileSync(input.archive);
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Validate asset package"}));
  await user.click(screen.getByRole("button",{name:"Choose RPF archive"}));
  await user.selectOptions(screen.getByLabelText("Package validation edition"),"Enhanced");
  await user.click(screen.getByRole("button",{name:"Choose decoder installation"}));
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  await user.click(await screen.findByText(/Archive\/member provenance ·/));
  expect(screen.getByRole("table",{name:"Exact archive member identities"})).toHaveTextContent("vehicles.rpf::car.ydr");
  if(mode==="folder"){
    await user.click(screen.getByRole("button",{name:"Optimize & recover"}));
    await user.click(screen.getByRole("button",{name:"Choose optimization package"}));
    await user.selectOptions(screen.getByLabelText("Optimization edition"),"Enhanced");
    await user.click(screen.getByRole("button",{name:"Choose optimization decoder context"}));
  }else{
    await user.click(screen.getByRole("button",{name:"Optimize with this validation context"}));
    expect(screen.getByLabelText("Optimization edition")).toHaveValue("Enhanced");
  }
  await user.click(screen.getByRole("button",{name:"Inspect optimization inputs"}));
  await user.selectOptions(await screen.findByLabelText("Declared material role"),"color");
  fireEvent.change(screen.getByLabelText("Candidate mip count"),{target:{value:"5"}});
  await user.click(screen.getByRole("button",{name:"Queue texture candidate"}));
  await user.click(screen.getByRole("button",{name:"Preview optimization candidates"}));
  await user.click(await screen.findByText("Verified RPF rebuilds · 1"));
  await user.click(screen.getByRole("button",{name:"Review optimized package export"}));
  await confirm(user);
  expect(readFileSync(join(files,"optimized-package/originals/dlc.rpf"))).toEqual(original);
  expect(readFileSync(join(files,"optimized-package/package/dlc.rpf"))).not.toEqual(original);
  expect(readFileSync(join(input.package,"dlc.rpf"))).toEqual(original);
  const receipt=JSON.parse(readFileSync(join(files,"optimized-package/optimization.json"),"utf8"));
  expect(receipt.archive_rebuilds).toHaveLength(1);
  expect(receipt.source_kind).toBe(mode==="folder"?"package_folder":"rpf_archive");
  expect(readFileSync(input.archive)).toEqual(original);
  expect(receipt.storage_delta_bytes).toBeLessThan(0);
  expect(receipt.before_report.runtime_status).toBe("not_tested");
},90000);

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
  const buildReport = JSON.parse(readFileSync(data.manifest, "utf8"));
  expect(buildReport.build.build_fingerprint).toMatch(/^[a-f0-9]{64}$/);
  expect(screen.getByText("SDK execution identity")).toBeInTheDocument();
  for (const edition of ["Legacy", "Enhanced"]) {
    const artifact = JSON.parse(readFileSync(join(data.output, edition, "sdk-artifact.json"), "utf8"));
    expect(artifact.build).toEqual(buildReport.build);
    expect(artifact.edition).toBe(edition);
    expect(artifact.artifact_id).toBe(buildReport.edition_artifact_ids[`story-${edition.toLowerCase()}`]);
    for (const [name, digest] of Object.entries(artifact.outputs)) {
      expect(createHash("sha256").update(readFileSync(join(data.output, edition, name))).digest("hex")).toBe(digest);
    }
    const publication = JSON.parse(readFileSync(join(data.output, edition, "sdk-publication.json"), "utf8"));
    expect(publication.installable_allin1_package).toBe(true);
    expect(existsSync(join(data.output, edition, "mod.toml"))).toBe(true);
  }
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
  await user.click(screen.getByRole("checkbox", { name: "Arrange nodes" }));
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

it.runIf(process.env.ALLIN1_NATIVE_RPF_TEST === "1").each(["Legacy","Enhanced"])("package layout React binds actual %s RPF construction identity",async edition=>{
  const {files,client,paths,user,sdk,python}=fixture();
  const source=join(files,"source");mkdirSync(source);
  const nested=join(source,"nested.rpf.source");mkdirSync(nested);
  writeFileSync(join(nested,"owned.bin"),"SDK-owned fixture");
  const game=join(files,"Synthetic decoder");mkdirSync(game);
  writeFileSync(join(game,edition==="Legacy"?"GTA5.exe":"GTA5_Enhanced.exe"),"MZ-fixture-never-executed");
  paths.graph_source=source;paths.gta_folder=game;
  render(<GraphWorkbench client={client} module="graph" onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Graph from folder"}));
  await user.click(await screen.findByRole("button",{name:"Review graph save"}));await confirm(user);
  await user.click(screen.getByRole("button",{name:"Decoder game folder"}));
  await user.clear(screen.getByLabelText("Output / report name"));
  await user.type(screen.getByLabelText("Output / report name"),"built.rpf");
  await user.click(screen.getByRole("button",{name:"Review RPF build"}));
  expect(await screen.findByText("RPF construction identity")).toBeInTheDocument();
  expect(existsSync(join(files,"built.rpf"))).toBe(false);
  await confirm(user);
  const output=join(files,"built.rpf"),report=JSON.parse(readFileSync(output+".validation.json","utf8"));
  expect(report.edition).toBe(edition);
  expect(report.source_kind).toBe("rpf_package_graph");
  expect(report.archive.sha256).toBe(createHash("sha256").update(readFileSync(output)).digest("hex"));
  expect(report.build.resource_files["tools/RpfPatcher/RpfPatcher.exe"]).toBe(createHash("sha256").update(readFileSync(join(sdk,"tools/RpfPatcher/RpfPatcher.exe"))).digest("hex"));
  expect(screen.getByText(new RegExp(report.report_sha256))).toBeInTheDocument();
  const verify=spawnSync(python,["-c","import json,sys;from allin1_sdk.artifact_contract import verify_seal;verify_seal(json.load(open(sys.argv[1],encoding='utf-8')),'report_sha256')",output+".validation.json"],
    {cwd:sdk,encoding:"utf8",windowsHide:true,timeout:30000,env:{...process.env,PYTHONPATH:join(sdk,"src")}});
  expect(verify.status,verify.stderr).toBe(0);
  expect(readFileSync(join(nested,"owned.bin"),"utf8")).toBe("SDK-owned fixture");
},60000);

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

it("vehicle hitches React happy path validates a real draft, saves after confirmation and preserves undo", async () => {
  const { files, client, user, invoke, python, sdk } = fixture(), guard = vi.fn(), saved = vi.fn();
  const setup = spawnSync(python, ["-c", `import sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'tests'))
from test_vehicle_authoring import _source
from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace
root=Path(sys.argv[2])
print(VehicleAuthoringWorkspace.create(_source(root),root/'Hitch copy').root)`, sdk, files], { encoding: "utf8", cwd: sdk, windowsHide: true });
  expect(setup.status, setup.stderr).toBe(0);
  const workspace = setup.stdout.trim();
  const session = invoke("inspect_vehicle_authoring_workspace", { workspace, model: "authorcar" }).payload.result as import("./types").VehicleAuthoringSession;
  render(<StrictMode><VehicleHitchEditor client={client} session={session} disabled={false} onGuardChange={guard} onSaved={saved} /></StrictMode>);
  await user.click(await screen.findByRole("button", { name: "Add rear hitch" }));
  expect(guard).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole("button", { name: "Review hitches" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("compatible trailer");
  fireEvent.change(screen.getByLabelText("rear compatible trailers"), { target: { value: "trailers, trailers2" } });
  await user.click(screen.getByRole("button", { name: "Review hitches" }));
  expect(screen.getByRole("button", { name: "Apply reviewed change" })).toBeDisabled();
  expect(JSON.parse(readFileSync(join(workspace, "vehicle-authoring.json"), "utf8")).hitch_configurations).toBeUndefined();
  await confirm(user);
  await waitFor(() => expect(saved).toHaveBeenCalledWith(expect.objectContaining({ revision: 1, selected_model: "authorcar" })));
  expect(JSON.parse(readFileSync(join(workspace, "vehicle-authoring.json"), "utf8")).hitch_configurations.authorcar.points[0].compatible_models).toEqual(["trailers", "trailers2"]);
  expect(readFileSync(join(files, "vehicle-source", "stream", "authorcar.yft"), "utf8")).toBe("fragment");
  expect(invoke("apply_vehicle_authoring_history", { workspace, direction: "undo", expected_revision: 1, authoring_confirmed: true }).operation).toBe("result");
  expect(JSON.parse(readFileSync(join(workspace, "vehicle-authoring.json"), "utf8")).hitch_configurations).toBeUndefined();
}, 30000);

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
  await user.click(screen.getByRole("checkbox", { name: "Arrange nodes" }));
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
