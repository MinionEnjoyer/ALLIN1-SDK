// Optional real-browser interaction check. Run Vite first; provide a saved graph
// and a screenshot output folder. This substitutes transport only, not graph UI.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const { chromium } = await import(process.env.ALLIN1_BROWSER_TEST_PLAYWRIGHT
  ? pathToFileURL(process.env.ALLIN1_BROWSER_TEST_PLAYWRIGHT).href : "playwright");
const [graphPath, outputPath] = process.argv.slice(2);
if (!graphPath || !outputPath) throw new Error("Usage: graph-browser.mjs graph.json output-folder (Vite on 127.0.0.1:1420)");
const document = JSON.parse(await readFile(graphPath, "utf8"));
assert.equal(document.operation, "rpf_package_graph");
const firstFile = document.nodes.find(n => n.type === "file" && /\.yft$/i.test(n.name)) || document.nodes.find(n => n.type === "file");
assert(firstFile, "Fixture needs a file node");
const session = { kind: "workspace_session", module: "graph", schema_version: 1, game_write_performed: false, read_only: true,
  state_sha256: "a".repeat(64), workspace: resolve(graphPath), issues: [], document };
const browser = await chromium.launch({ headless: true, channel: "msedge" });
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 }, deviceScaleFactor: 1 });
const errors = []; page.on("pageerror", error => { errors.push(String(error)); console.error(error); });
const data = JSON.stringify(session).replaceAll("<", "\\u003c");
await page.route("http://127.0.0.1:1420/graph-review", route => route.fulfill({ contentType: "text/html", body: `<!doctype html><html data-theme="dark"><head><title>Node viewer interaction test</title></head><body><div id="root" style="padding:18px"></div><script type="module">
import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type; window.__vite_plugin_react_preamble_installed__=true;
const ReactModule=await import('/node_modules/.vite/deps/react.js'); const React=ReactModule.default||ReactModule; const clientModule=await import('/node_modules/.vite/deps/react-dom_client.js'); const {createRoot}=clientModule.default||clientModule;
const {default:GraphWorkbench}=await import('/src/GraphWorkbench.tsx'); await import('/src/styles.css');
const session=${data}; window.graphGuard=false; window.writeAttempts=0;
const client={startJob:async(op,payload,revision,callback)=>{if(op!=='inspect_authoring_workspace')throw Error('Browser check is read-only');callback({operation:'result',terminal:true,payload:{result:session}});return{job_id:'browser-read'};},cancelJob:async()=>{},selectPath:async()=>null,applyWorkspaceAction:async()=>{window.writeAttempts++;throw Error('Writes forbidden in browser check');}};
createRoot(document.getElementById('root')).render(React.createElement(GraphWorkbench,{client,module:'graph',initialSource:session.workspace,initialFocus:${JSON.stringify(firstFile.id)},initialFocusSerial:1,onGuardChange:value=>{window.graphGuard=value;}}));
</script></body></html>` }));
try {
  await page.goto("http://127.0.0.1:1420/graph-review");
  const viewport = page.getByRole("region", { name: "Graph viewport" });
  await viewport.waitFor();
  await page.waitForFunction(id => document.querySelector(`[data-node="${id}"]`)?.getAttribute('aria-pressed') === 'true', firstFile.id);
  assert.equal(await page.evaluate(() => window.graphGuard), false, "Deep-link focus must not create an edit");
  const search = page.getByRole("textbox", { name: "Find node" });
  await search.fill(firstFile.name);
  const selected = page.getByRole("button", { name: `Select node ${firstFile.name}`, exact: true });
  await page.waitForFunction(id => document.querySelector(`[data-node="${id}"]`)?.getAttribute('aria-pressed') === 'true', firstFile.id);
  await viewport.scrollIntoViewIfNeeded();
  const box = await selected.boundingBox(); assert(box);
  const camera = () => page.getByTestId("graph-camera").getAttribute("transform");
  const beforePan = await camera();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down({ button: "middle" });
  await page.mouse.move(box.x + box.width / 2 + 110, box.y + box.height / 2 + 55, { steps: 12 });
  await page.mouse.up({ button: "middle" });
  assert.notEqual(await camera(), beforePan, "Middle drag must move camera");
  assert.equal(await page.evaluate(() => window.graphGuard), false, "Middle drag must not edit the graph");
  assert.equal(await selected.getAttribute("aria-pressed"), "true");
  const beforeWheel = await camera(), scrollBefore = await page.evaluate(() => scrollY);
  await page.mouse.wheel(0, -220); await page.waitForTimeout(120);
  assert.notEqual(await camera(), beforeWheel, "Wheel must zoom");
  assert.equal(await page.evaluate(() => scrollY), scrollBefore, "Wheel must not scroll the page");
  await page.getByRole("button", { name: "Focus selected", exact: true }).click();
  const centerBox = await selected.boundingBox(), viewBox = await viewport.boundingBox(); assert(centerBox && viewBox);
  assert(Math.abs(centerBox.x + centerBox.width / 2 - viewBox.x - viewBox.width / 2) < 3, "Focus must center node horizontally");
  assert(Math.abs(centerBox.y + centerBox.height / 2 - viewBox.y - viewBox.height / 2) < 3, "Focus must center node vertically");
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await page.getByRole("combobox", { name: "View layout" }).selectOption("groups");
  await page.getByRole("button", { name: "Expand canvas", exact: true }).click();
  await page.getByRole("button", { name: "Fit graph", exact: true }).click();
  await page.waitForTimeout(100);
  const fitted = await viewport.boundingBox(); assert(fitted);
  for (const node of await page.locator('[data-node]').all()) {
    const bounds = await node.boundingBox(); assert(bounds);
    assert(bounds.x >= fitted.x && bounds.y >= fitted.y && bounds.x + bounds.width <= fitted.x + fitted.width && bounds.y + bounds.height <= fitted.y + fitted.height, `Fit must contain ${await node.getAttribute('data-node')}`);
  }
  await mkdir(outputPath, { recursive: true });
  await page.screenshot({ path: resolve(outputPath, "rs5-color-groups.png"), fullPage: true });
  await page.getByRole("button", { name: "Show panels", exact: true }).click();
  await page.getByRole("combobox", { name: "View layout" }).selectOption("hierarchy");
  await search.fill(firstFile.name); await page.waitForTimeout(250);
  await page.screenshot({ path: resolve(outputPath, "rs5-search-focus.png"), fullPage: true });
  await page.setViewportSize({ width: 760, height: 1000 });
  await page.waitForTimeout(150);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2);
  assert.equal(overflow, false, "Small window must not overflow horizontally");
  await page.screenshot({ path: resolve(outputPath, "rs5-small-window.png"), fullPage: true });
  assert.equal(await page.evaluate(() => window.writeAttempts), 0);
  assert.equal(await page.evaluate(() => window.graphGuard), false);
  assert.deepEqual(errors, []);
  const report = { nodes: document.nodes.length, checks: ["middle-drag on node", "no accidental draft", "wheel zoom / no page scroll", "search / centered focus", "color groups", "expanded canvas", "760px responsive layout", "no write calls", "no browser errors"], source: resolve(graphPath) };
  await writeFile(resolve(outputPath, "browser-check.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} catch (error) {
  await mkdir(outputPath, { recursive: true });
  await page.screenshot({ path: resolve(outputPath, "failure.png"), fullPage: true });
  console.error(errors); throw error;
} finally { await browser.close(); }
