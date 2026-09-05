# Tauri v2 migration validation record

> Historical checkpoint: results, commands and artifact identities below apply
> only to the described source/session. They do not qualify current 0.6.4.
> See the [current release guide](release-0.6.4.md) before using this as guidance.

Last updated: 2026-09-04

## Ped Workbench pivot — 2026-09-04

The React Ped Workbench now implements the Tkinter catalog, metadata inspection,
seven existing authoring fields, copied workspace creation/opening, exact
identity/asset migration, reviewed complete metadata clone, verified undo,
asset-family inspection, integration findings and diagnostic native previews.
The existing Python domain remains authoritative. New content-state guards
bind consent to file hashes and recheck inside the operation lock. Duplicate ped
definitions are retained and ambiguous writes are refused.

Validation:

- 194 React tests passed, including 12 Ped Workbench cases. Coverage includes
  read-only/copy boundaries, missing nodes, review confirmation, clone blockers,
  renames, stale/cancelled results, no double submission, exact preview selection,
  search shortcuts and shell/category dirty-navigation guards.
- Focused Python ped, scanner, protocol, content-workbench, weapon and toolchain
  regressions passed. Native RPF identity checks: 66 passed. Rust: 10 passed.
- The rebuilt frozen sidecar passed the ordinary packaged smoke and the dedicated
  real-JSONL ped copy/edit/migrate/clone/undo smoke without GTA write authority.
  Synthetic invalid drawable bytes produced an honest unavailable preview.
- The frozen sidecar also read exact Enhanced stock nested members from
  `x64v.rpf` / `models/cdimages/componentpeds_a_f_o.rpf`:
  `a_f_o_genstreet_01.ydd` SHA-256
  `56ee8d9f76abde81b828ff97695f34355c0da28df2713428f17afb2a617e440b`, and
  `a_f_o_genstreet_01.ytd` SHA-256
  `d91f9c5c86f2bf535e2805f65fdec4b1e23815da47c813e26ae79bc4e5c0e679`.
  Both produced native images without warnings/truncation; the same service's
  diagnostic geometry and texture contact sheet were visually inspected.
- That real check found a Windows temporary-filename bug for `::` member IDs.
  The decoder now receives a safe leaf filename after the bounded reader has
  resolved the exact member. Result identity/hashes stay fully qualified.
  Regression coverage distinguishes same-named members in different archives.
- Browser UI checks at 1280×720 and 720×800 showed no horizontal overflow. The
  three main panel headers measured 76px at matching Y positions side-by-side;
  narrow layouts stack. The review table and consent controls were checked in
  light and system-dark colors. Preview theme/viewport were restored afterward.

No Launcher, installed SDK or GTA files were modified. Installer generation is a
local candidate build, not installation or clean-machine certification. Native
dialog E2E and broader edition/real-ped coverage remain. No runtime expansion or
in-game acceptance was tested. The YMT inspector/schemas/preflight generalization
remain separate follow-on work; see `ped-workbench-migration-and-ymt-handoff.md`.

## Phase 1 — architecture and contract freeze

Status: **complete for the initial vertical-slice contract**

Evidence:

- ADR 0003 records process ownership, trust boundaries, cancellation policy,
  packaged discovery, and rejected alternatives.
- `tauri-feature-parity.md` maps current screens, workflows, shortcuts, deep
  links, tests, and extraction targets to migration phases.
- `desktop-protocol-v1.schema.json` fixes the complete envelope.
- Existing Agent API tests remain unchanged and pass beside the desktop tests.

## Scope/flags authoring and private KRISS test — 2026-09-03

- Added 42 existing-node first-person position/rotation/FOV fields and the
  complete `WeaponFlags` token list to React/Python authoring. Missing axes are
  not synthesized. Untouched attributes, unknown flags and other records are
  retained; finite/range/token validation runs before transactional save.
- Scope calibration uses measured reference/custom anchors in one explicitly
  acknowledged oriented weapon-local frame. Optional FOV uses perspective
  magnification math. Proposals are staged into a draft, never auto-saved; using
  a proposal clears measurements to avoid applying a delta twice. This is not
  geometry inference or a claim of in-game alignment.
- 109 targeted Python weapon tests passed. All 46 React tests passed, including
  calibration, read-only protection, scope/flag dirty guards, review/save/undo,
  blank/non-finite input handling, and preservation of unknown flags.
- TypeScript/Vite production build, Rust broker check, standalone sidecar smoke
  and x64 NSIS build passed. Packaged smoke now saves/undoes scope and flag edits.
  A separate read-only call to the packaged sidecar inspected the real KRISS
  workspace: one weapon, revision 1, 42 camera fields, scope Z `0.0180`, no game
  write. This turn did not repeat the full Python suite or native visual QA.
- Private Legacy KRISS package built under ignored `output/kriss-vector-test`:
  one independent weapon/ammo, two components, eight animation mappings, six
  upstream model/texture assets, and one authored test shop entry. Recursive RPF
  verification passed for all 17 payloads across three archives. Donor archives
  and upstream assets were hash-checked unchanged; nothing was installed.
- The generic loose inspector reports zero errors and one unresolved edition
  declaration warning; build/provenance explicitly target Legacy. Game behavior,
  shop visibility, HUD/pickups and sight/reload fit are not validated. Model XML
  decoding worked, but this drawable's position buffers are not currently
  supported by the preview renderer. See `weapon-scope-authoring.md` and the
  private test README for limits and the two donor/extraction gaps encountered.
- Unsigned installer: 68,436,769 bytes, SHA-256
  `8e17d3bda02060d1bd7f0a6d9bb8eedbe68ad0d65361ed19cf3416cb993d3710`.
  Private test ZIP: 16,814,261 bytes, SHA-256
  `f4487b1336ec12b03eb170c2145305df6efd28293a2cddfe4494763370fbc9c6`.
  Both checksum companions verified. No publishing or clean-VM installation.

## Weapon bundle cloning — 2026-09-03

- Added New from template inside the existing weapon metadata pane. The form
  requires explicit weapon/slot/model/text identities and clone-or-reuse ammo;
  it is available only in a copied workspace with no unsaved field edits.
- Reused the domain's complete bundle plan and transactional clone without
  introducing a TypeScript writer or expanding native command authority. Review
  displays donor completeness, source evidence, reused components, planned
  additions, collisions, and blocking findings. Blocked plans cannot be applied.
- Creation requires its own review confirmation and regenerates the plan under
  the domain lock. Undo displays records to remove, restores exact source bytes,
  and selects the donor. Clone drafts guard navigation; cancelled/failed reviews
  preserve inputs, while stale saves require fresh review and confirmation.
- Added 20 desktop Python cases around both ammo modes, read-only planning,
  strict spec validation, missing dependencies/collisions, action-time drift,
  source protection, exact undo, the protocol risk/confirmation boundary, and
  refusal of oversized evidence rather than silent review truncation.
- Three new React workflows cover create/undo confirmation, record inventories,
  blocked evidence, ammo reuse, draft/navigation protection, malformed reviews,
  and stale-save recovery. All 42 React tests and TypeScript/Vite build passed.
- Expanded packaged-sidecar smoke with a complete donor, bundle clone, and undo
  checks for exact bytes across every source and asset.
- Initial full regression passed: 1,364 Python tests / four skips (Windows symlink
  privileges and the private local-only fixture), eight Rust tests, Clippy with
  warnings denied, and Rust formatting. Review headings receive keyboard focus
  so long clone forms bring the returned plan into view.
- After adding the oversized-review guard, all 20 clone boundary tests passed.
  The final full suite had 1,364 passes, four skips, and one intermittent existing
  failure: `test_destroy_closes_scene_loader_without_waiting_for_active_decode`
  took 333 ms against its 250 ms Tk teardown threshold. Its module rerun also
  exceeded the threshold (267 ms, 12 other tests passed); the isolated test then
  passed. The threshold and legacy teardown implementation were not changed.
  The final full-suite result is therefore not recorded as an all-green pass.
- Native release opened the real donor fixture with aligned pane headers. The
  new-form visual check was not completed: the Windows helper reported an
  unsupported folder-field property, then its screenshot and accessibility
  target disagreed. Native input was stopped; no native authoring was performed.
  New-form and narrow-width visual QA remain release checks, not claimed passes.
- Rebuilt the sidecar and x64 NSIS installer. The complete clone/undo smoke
  passed in staging and the final release layout under an isolated no-Launcher
  profile. Installer: 68,432,098 bytes, SHA-256
  `b1327f29685c40eabea00076cec52da1a5ad0bd508ae84bd812d4965bb3b389b`;
  checksum companion verified. Unsigned/unpublished; clean-Windows
  install/uninstall verification remains a release gate.

## Weapon component and attachment authoring — 2026-09-03

- Added a component inventory and explicit component-definition / attachment-link
  editors within the existing three-pane workbench. Existing model, localization,
  description, and component-bone fields can be edited; identity and component
  type remain locked. The component bone is distinguished from a weapon's
  immutable attachment point.
- Shared component changes require explicit affected-weapon acknowledgement.
  A link can change only its existing default flag. The review rejects ambiguous
  links, missing nodes, invalid assets/identifiers/booleans, and a second default
  at the same attachment point. No other link is silently modified.
- Read-only reviews reuse the domain save path and bind exact targets, revision,
  input changes, and source contents. Synchronous saves recheck the digest under
  the workspace lock. Undo restores exact bytes and keeps the component/link
  selected. Original sources and game files remain untouched.
- Added 15 Python regression cases and three React workflow tests covering
  shared acknowledgement, source read-only state, locked fields, reviewed save
  and undo, preserved unknown XML, exact attachment ownership, input validation,
  duplicate/default conflicts, target tampering, same-size external edits,
  action-time digest checks, and failed-review draft preservation.
- Expanded the packaged sidecar smoke to edit and undo a component and an
  attachment link in the isolated no-Launcher user environment.
- Native release UI inspection opened a real two-weapon, three-component test
  package. Switching to Components, inspecting its four fields and locked type,
  then following a shared usage into the exact weapon/component attachment link
  worked. The locked attachment point and read-only default flag were visible;
  all three pane headers remained aligned at the maximized desktop size.
  This native pass was read-only; save/undo is covered by React, domain, and
  packaged-sidecar tests. Narrow-width visual QA remains a release check.
- Following an attachment from a filtered component inventory clears the old
  filter when returning to Weapons, with a regression assertion for navigation.
- Full regression: 1,345 Python tests passed / four skipped (Windows symlink
  privileges and the private local-only fixture), all 39 React tests passed,
  TypeScript/Vite production build passed, and eight Rust tests, Clippy with
  warnings denied, and Rust formatting passed.
- Rebuilt the Python sidecar and final x64 NSIS installer. The expanded smoke
  passed against staging and the final release layout, including byte-identical
  component/attachment undo. Installer: 68,413,748 bytes, SHA-256
  `0d2e65645ec4ad496b23715380ebe863b7afe7731859a69676184d1852daec6d`;
  checksum companion updated. This local build is unsigned and unpublished;
  clean-Windows install/uninstall verification remains a release gate.

## Weapon Workbench — 2026-09-03

- First React authoring slice: unpacked source inspection, copied workspace
  creation/opening, existing weapon/ammo fields, shared-ammo impact lists,
  reviewed confirmation, and exact latest-revision undo. Three adjacent pane
  headers use one 76 px row; narrower layouts reflow the evidence pane and then
  stack all panes.
- The Python adapter delegates to the existing weapon domain. Reviews write
  nothing, source and game files stay untouched, same-size external file edits
  invalidate reviews, and commits recheck content under the workspace lock.
- Component tests cover copy/save confirmation, shared-ammo acknowledgement,
  dirty category/deep-link navigation, cancellation and late terminal rejection,
  malformed responses, and StrictMode's initial double-effect probe.
- The packaged sidecar smoke exercises real inspect/copy/review/edit/undo in
  an isolated user profile without Launcher, Python, or .NET on PATH, and verifies
  byte-identical undo plus an untouched original package.
- Advanced components/attachments, clone plans, animation/shop editing, native
  weapon preview, and publication remain explicit Tkinter fallback areas.
- Validation: 1,329 Python tests passed / 5 skipped, 36 React tests passed, eight
  Rust tests passed, Clippy with warnings denied, and Rust formatting passed.
  Skips cover Windows symlink privileges, one unavailable Tk display, and a
  private local-only fixture. The first full Python run hit the existing
  Tk teardown timing threshold (252 ms versus 250 ms); its full module and the
  second full suite passed without changing that test.
- The native Tauri release opened a real two-weapon test package and displayed
  weapon/ammo values, shared references, attachments, and Python findings.
  Its three pane headers were visually aligned at the default desktop size.
  The visual pass prompted fixed weapon-before-ammo field ordering and stronger
  read-only text contrast. Narrow-width visual QA remains unverified because
  the browser was unavailable and the native resize attempt scrolled instead.
- The final TypeScript/Vite build and x64 NSIS packaging succeeded after closing
  the test instance that locked the first bundle attempt. The sidecar smoke
  passed against both staging and the final release layout. Native sidecar
  processes had zero window handles. Installer: 68,408,811 bytes, SHA-256
  `9eb532b0abe3331badd5fbf886cf8be7db161f98221de88ac1b25aace350855e`.
  This local build is unsigned and unpublished; clean-Windows install/uninstall
  verification remains a release gate.

## Standalone packaging pass — 2026-09-03

- Added a per-user NSIS distribution with staged schemas, examples, authoring
  sources, assets, docs, README/license, and a self-contained RpfPatcher/.NET
  runtime. Build outputs include an installer SHA-256 companion; CI now uploads
  the installer rather than isolated executables missing their dependencies.
- The packaged sidecar smoke passed both against the staging resource home and
  Tauri's final release layout, with fresh user data, unrelated working directory,
  no Launcher configuration, and system-only PATH. Resource hashes were verified
  and RpfPatcher started without a separate .NET installation on PATH.
- The native release opened under that isolated user profile and created no
  Launcher data. Its main window was present; both PyInstaller sidecar processes
  had zero window handles. The host still disables generic game-write authority.
- Qwen standalone configuration is an explicitly confirmed direct operation,
  not a cancellable job. Tests cover precedence, legacy compatibility, invalid
  settings preserving the previous file, local file validation, no runtime start,
  and browser-preview refusal. The browser form was inspected and its mode
  switch/refusal exercised; native control interaction was not verified because
  the Windows automation input did not change the observed controls.
- Validation: 1,321 Python tests passed / 3 skipped, 31 React tests passed,
  TypeScript/Vite production build, 8 Rust tests, and clippy with warnings denied.
  The NSIS build succeeded locally. This is not a published or signed release,
  and install/uninstall on a pristine Windows VM remains a release gate.

Known limitations:

- The matrix will continue to gain per-workflow E2E identifiers as later
  workspaces enter implementation.
- Help topic data is still declared in `help_center.py`, so the sidecar imports
  the Tk module to read that catalog. Extracting content to a display-free
  module is tracked before final PyInstaller optimization.

## Phase 2 — Tauri foundation

Status: **partial**

Implemented:

- Persistent versioned Python sidecar and backward-compatible Agent delegation.
- One-at-a-time read-only jobs, cancellation, sequence/revision echo, bounded
  diagnostics, shutdown, and fail-closed risk classification.
- Rust-owned child process, handshake, request routing, ordered Tauri channels,
  timeout/error mapping, crash notification, explicit restart, native dialogs,
  canonical paths, launch-argument routing, single-instance focus/forwarding,
  window state, CSP, and a custom command-only capability.
- React/TypeScript app shell, responsive navigation, light/dark/system tokens,
  sidebar and console persistence, focus movement, keyboard routes, and Vitest.
- PyInstaller/Tauri build and packaged-sidecar smoke scripts.

Executed validation on this host:

```text
pytest tests/test_desktop_protocol.py tests/test_addon_sdk.py tests/test_agent_api.py -q
62 passed

pnpm test
2 test files passed; 19 tests passed

pnpm build
TypeScript check passed; Vite production bundle generated

build_tauri_desktop.ps1
PyInstaller sidecar built; packaged handshake/catalog/read-only/lifecycle-review/lifecycle-process-gate/text-preview/image-artifact/failing-report-export/shutdown smoke passed; Tauri NSIS installer generated

rustc --version / cargo --version
rustc 1.98.0; cargo 1.98.0

cargo test
2 tests passed

cargo fmt --check
passed

cargo clippy --all-targets -- -D warnings
passed

cargo check
passed

pnpm tauri build
release executable and x64 NSIS installer generated

pytest -q
1,243 passed; 3 skipped; 1 unrelated pre-existing parity failure
```

The full-suite failure is
`test_extension_contract_implementation_matches_launcher_copy`: this dirty SDK
checkout already contains map-extension contract work that has not been copied
to the sibling `ALLIN1` launcher checkout. The Tauri changes do not modify that
contract or its parity test.

The shell was inspected with deterministic development fixtures in both empty
and populated states at the default viewport and the 720 px compact breakpoint.
The audit caught and fixed the compact overlay sidebar retaining a desktop grid
column. The development fixture is guarded by `import.meta.env.DEV` and is
removed from production builds.

The release executable was then launched natively against the real packaged
Python sidecar. The app reported `SDK 0.6.3 · protocol 1.0.0`, rendered the
stable Package Linker inventory/diagnostics/inspector layout, and navigated to
the Python-supplied Help Center. The smoke-test window closed cleanly.

Packaged artifacts:

- `desktop/src-tauri/target/release/allin1-sdk-desktop.exe`
- `desktop/src-tauri/target/release/bundle/nsis/ALLIN1 SDK_0.1.0_x64-setup.exe`
- The independently packaged PyInstaller sidecar is 88,045,972 bytes and its
  executable smoke test passed (SHA-256
  `4BECA196975EAD247E135636CDDD47BDA3ADB1DF69C1F5B6C7156DB5D6751B89`).
  The smoke now includes real text/image previews, recipe inspection,
  receipt ownership plus enable/disable review, confirmed recipe/link report
  exports, the non-cancellable Quick Import authoring contract, the typed RPF
  index and vehicle-authoring field/appearance/tuning/light/axle/skeleton/
  transmission/distribution review contracts and guarded vehicle-package
  build catalog contract,
  and clean shutdown.
- The Tauri v2 release executable is 9,869,312 bytes (SHA-256
  `1929B7C0C66E1FC86641D90DA7A9987480BF42F23FB7EDF923546B0256B2C2BC`).
  A native process-tree launch found the SDK window and packaged sidecar; the
  sidecar had no window handle, confirming the Windows `CREATE_NO_WINDOW`
  launch path in the release build.
- The x64 NSIS installer is 86,534,470 bytes (SHA-256
  `FE91EA1F619680227A766E8E70C1BEEAED34A72FD58C242024531447DE92D4DB`).
- The packaged lifecycle smoke uses a disposable fake installation. When a
  real GTA process is active, the expected packaged result is a `game_write`
  refusal with no fixture write; when GTA is closed, the same smoke completes
  the temporary install/uninstall and verifies receipt rollback.

Remaining packaging validation is a clean-VM install/launch/uninstall run and
release signing; neither is represented as complete by this local smoke test.

## Phase 3 — production vertical slice

Status: **partial / experimental**

Implemented paths use the real sidecar, not a mock:

- Application shell and all nine navigation destinations.
- Package Linker manifest/product-workspace link summary and bounded package
  scan summary through existing Python services.
- Package Linker node/reference/install-step switching, selected evidence,
  stale-result rejection, and native Markdown report destinations. Report
  content is still generated by `AddonLinkReport.to_markdown` through the
  existing Agent API `link` command; the WebView does not write files.
- Loose read-only RPF inspection through `inspect-rpf` and RpfPatcher.
- Docked structured SDK Console using the existing command catalog and Agent API.
- Searchable Help Center supplied by the Python catalog.
- Existing checksum-oriented release check.
- Existing CLI launch flags parsed and forwarded through single-instance events.

Known limitations:

- Installed catalog memory, full runtime-contract drill-down for product
  workspaces, and the read-only RPF tree are not yet at Tkinter parity.
- The updater check is real, but Tauri install/swap is intentionally disabled
  until Rust owns verified staging and the full sidecar process-tree pre-exit.
- Deep links for complex workspaces route correctly and display an experimental
  notice; their authoring editors remain in the Tkinter fallback.
- Console authoring commands execute asynchronously from the WebView, but only
  read-only jobs currently receive streaming cancellation. Game-write authority
  is not granted by the Tauri launch path.

## Phase 4 — Asset Viewer inventory and previews

Status: **partial**

Implemented:

- Package folders and bounded archives reuse the Package Linker inspection and
  expose Python's `workbench_entries` inventory with category, path, preview
  class, size, filtering, and truncation evidence.
- The typed read-only `preview_asset` job reopens one exact member through
  `PackageAssetReader`; containment, archive ambiguity, read caps, digesting,
  text decoding, native inspection, and image decoding remain in Python.
- Authored text is clipped explicitly at the desktop string contract. Generic
  binaries expose only a small hexadecimal header. Package-controlled content
  is rendered as text, never HTML.
- Images are decoded with pixel/output limits, orientation-normalized, resized
  when necessary, re-encoded as PNG, atomically written under a SHA-256 name,
  and pruned in a 64-file/256 MiB broker-owned cache. Protocol responses carry
  the artifact path, digest, size, and media type rather than base64 data.
- Rust creates and canonicalizes the cache root before sidecar startup. Tauri's
  asset protocol scope is limited to that one application-cache subtree; the
  WebView still has no filesystem plugin or general file-read command.
- React provides flat inventory, preview, and evidence panes; category/path
  filters; cancellation; warnings for clipped/truncated output; responsive
  stacking; accessible region names; and stale-preview rejection.

Local validation:

```text
pytest tests/test_desktop_protocol.py -q
15 passed

pytest tests/test_desktop_protocol.py tests/test_addon_sdk.py tests/test_agent_api.py tests/test_addon_importer.py -q
132 passed

pnpm test
2 test files passed; 12 tests passed

pnpm build
TypeScript check passed; Vite production bundle generated

cargo check
passed with Tauri protocol-asset feature and scoped configuration
```

The Asset Viewer development fixture was inspected at 1440×900, 1280×800, and
the 720 px compact breakpoint in light and dark themes. Inventory controls,
text preview, evidence wrapping, sidebar states, and compact stacking remained
readable without horizontal page overflow.

Known limitations:

- Direct loose-RPF asset reads still require the matching GTA installation and
  are not yet wired into this workspace's intake controls.
- Native CodeWalker previews depend on the packaged RpfPatcher resources and
  selected edition/game context. The bounded binary/text fallback remains
  available when deep conversion cannot run.
- Native workspace export/build, texture editing, and open-location/export
  actions remain in the Tkinter fallback; this milestone is intentionally
  read-only.

## Phase 4 — Package receipt ownership

Status: **partial**

Implemented:

- The versioned desktop contract exposes `inspect_package_receipts` as a
  read-only, isolated, cancellable operation. Rust and Python both allowlist
  the operation, and invalid GTA folders or package ids fail with read-only
  risk instead of falling through to an unclassified command.
- `ModIntegrationService` remains authoritative for installation validation,
  receipt parsing, managed-file containment, SHA-256 comparison, rollback
  backup presence, and RPF-entry verification. React never reads receipt files
  or game paths directly.
- The versioned contract also exposes `review_package_lifecycle` as a read-only
  isolated job. Install/update review validates payload hashes, edition,
  loaders, content requirements, conflicts, destination types, existing
  ownership, backup scope, and replacement restrictions. Uninstall review
  validates dependents, owned hashes, backup hashes, disabled-file layering,
  RPF state, and rollback scope. Results carry a deterministic review digest
  that grants no write authority. Enable/disable reviews also expose current
  and target state, loose-file moves, RPF transitions, DLC registrations, and
  requirement/dependent gates.
- `apply_package_lifecycle` is a synchronous, non-cancellable `game_write`
  operation. The native Tauri shell grants only package-lifecycle authority;
  the broader Agent API game-write capability remains disabled. The operation
  regenerates the review from the same opened package, rejects digest drift,
  requires a matching package id and explicit action-time confirmation, and
  fails closed when Windows process inspection cannot prove GTA V is stopped.
- The existing transactional `ModIntegrationService` remains the only writer.
  Successful results report the process check, receipt and ownership
  postconditions, backup/RPF counts, restored or removed payload counts, and
  receipt cleanup or state updates. Allowed and refused attempts are written
  to the existing audit stream.
- React provides canonical GTA folder selection, managed-package filtering,
  enabled/disabled state, selected receipt inspection, explicit mismatch
  diagnostics, per-check evidence, native install-candidate selection, and a
  bounded lifecycle review dialog. The aligned lifecycle controls expose the
  selected package's available enable/disable transition beside uninstall.
  Ready reviews advance to a distinct focused game-write confirmation; blocked
  reviews expose no action. Completion shows the closed-game, rollback, and
  ownership result before refreshing the selected receipt.

Local validation:

```text
pytest tests/test_desktop_protocol.py -q
24 passed

pytest tests/test_desktop_protocol.py tests/test_vehicle_quick_import.py tests/test_vehicle_quick_import_cli.py tests/test_agent_api.py tests/test_addon_sdk.py tests/test_addon_importer.py tests/test_assistant_grounding.py tests/test_schema_v2_lifecycle_parity.py tests/test_mod_package_contract.py -q
195 passed, 2 skipped

pnpm test
2 test files passed; 19 tests passed

pnpm build
TypeScript check passed; Vite production bundle generated

cargo fmt --check / cargo check
passed
```

The populated receipt fixture was inspected at 1366×900, 1280×720, and the
720×700 compact breakpoint. All three desktop pane headers share the same top
and height, and the compact layout stacks to bounded internally scrollable
panes. The aligned lifecycle controls remain within the inspector; both review
buttons share a 30 px baseline. The checked viewports had no horizontal page
overflow. The browser console reported no errors or warnings. Ready and
blocked lifecycle reviews, the separate confirmation, and install, uninstall,
and disable completion receipts were exercised. At 720×700 each state remains
bounded, opens at scroll position zero, and keeps both 33 px actions visible.
The blocked fixture contains only `Close review` and cannot advance.

Known limitations:

- RPF-entry verification still depends on the existing packaged RpfPatcher
  helper; any helper error is preserved as ownership evidence rather than
  hidden by the desktop.

## Phase 5 — RPF archive index and exact-member preview

Status: **partial**

Implemented:

- The versioned desktop contract exposes `inspect_rpf_archive` as a read-only,
  isolated, cancellable operation. Python, Rust, TypeScript, the JSON schema,
  and packaged-sidecar catalog smoke coverage share the same allowlist.
- The Python service validates a loose `.rpf` and optional GTA installation,
  then delegates recursive decoding to the existing `RpfExplorerService`.
  Results contain a bounded archive map, exact member ids, sizes, storage
  flags, suffix totals, warnings, and explicit no-game-write evidence.
- React replaces the raw command console with aligned archive-layer,
  recursive-entry, and evidence panes. It supports archive scoping, path and
  type filters, bounded result rendering, stale-result rejection, refresh and
  cancellation, and directory-safe selection.
- Selecting a file reuses the typed `preview_asset` operation with its exact
  recursive member id. Containment, extraction, decoding, preview limits, and
  artifacts remain Python-owned; the WebView never reads or rewrites an RPF.

Local validation:

```text
pytest tests/test_desktop_protocol.py -q
25 passed

pytest tests/test_desktop_protocol.py tests/test_vehicle_quick_import.py tests/test_vehicle_quick_import_cli.py tests/test_agent_api.py tests/test_addon_sdk.py tests/test_addon_importer.py tests/test_assistant_grounding.py tests/test_schema_v2_lifecycle_parity.py tests/test_mod_package_contract.py -q
196 passed, 2 skipped

pnpm test
2 test files passed; 20 tests passed

pnpm build
TypeScript check passed; Vite production bundle generated

cargo test / cargo check / cargo clippy --all-targets -- -D warnings / cargo fmt --check
passed

scripts/build_tauri_desktop.ps1
Packaged sidecar smoke passed; Tauri v2 NSIS bundle generated
```

Known limitations:

- This slice is deliberately inspection-only. Node graphs, programs, staged
  change sets, archive writes, rollback, and transaction receipts remain in
  later migration phases and are not implied by the preview contract.
- Live indexing and exact-member preview require the same pinned RpfPatcher
  helper and supported GTA archive formats as the existing Python explorer.

## Phase 5 — Vehicle project inspection and copied authoring

Status: **partial**

Implemented:

- The versioned desktop contract exposes `inspect_vehicle_project` as a
  read-only, isolated, cancellable operation across Python, Rust, TypeScript,
  the JSON schema, and packaged-sidecar catalog smoke coverage.
- Source, optional GTA installation, and edition values fail closed before the
  operation delegates to the existing `VehicleProjectResolver`. Direct RPF
  inspection requires matching decoder context; package archives and folders
  retain their existing resolver behavior.
- Results bound models, linked assets, per-model and project findings, and axle
  configurations while preserving complete counts and truncation evidence.
  The response explicitly reports that neither package files nor GTA V changed.
- React replaces the generic migration board with Vehicles/Weapons/Peds/Maps
  sub-navigation and a real Vehicles surface. Resolved models, exact package
  links, and immutable evidence use one aligned three-pane grid with independent
  filtering, responsive stacking, refresh, cancellation, and stale-preview
  rejection.
- Selecting ordinary linked assets reuses `preview_asset` with the exact
  resolver-owned path. Selecting YFT/YDR/YDD geometry now opens a dedicated
  React viewport backed by the synchronous read-only `render_vehicle_model`
  operation. Python revalidates package containment on every frame, caches only
  two digest/edition/decoder-bound native scenes, and owns the hash-named PNG
  artifacts. React owns coalesced orbit input, instant pan/zoom, keyboard camera
  controls, LOD/component/exact-surface filtering, and
  shaded/bounded-textured/material-ID/UV-coverage/wireframe mode. Textured mode samples
  real linked YTD preview pixels through decoded UV0 using a bounded
  three-vertex diagnostic, retains semantic fallbacks per unresolved surface,
  and explicitly reports that it is not the game shader. The same request revalidates the model's
  linked YTD, caches its digest-bound decode separately, and returns sampler
  slots resolved against a bounded texture catalog and contact sheet. The
  collapsible React evidence panel keeps raw shader records grouped into stable
  material surfaces rather than duplicating repeated shader names. It also
  preserves finite CodeWalker `Vector` and `Array` rows as exact Vector4
  constants, including repeated-record counts, without guessing which legacy
  components a game shader consumes.
  UV-coverage mode colors the same bounded 3D triangle sample by resolved UV0,
  UV-only/unresolved texture, degenerate coordinates, or missing UV0. React
  exposes the four exact counts in a compact synchronized legend.
  The resolver also attaches only a same-directory, same-stem package YBN to
  the vehicle model. Its digest-bound decode is cached independently, revalidated
  for every frame, and composited through the same model-local camera. Source
  triangles are exact overlay geometry; four-point boxes are explicitly labelled
  diagnostic hulls; capsules, cylinders, spheres, and unsupported primitives
  remain count-only evidence. A separate toggle, synchronized legend, bounds,
  primitive-fidelity list, source path, and digest keep collision evidence visible
  without replacing the chosen surface render mode.
  Other content categories remain clearly labelled Tkinter fallbacks rather
  than exposing non-functional editors.
- The `--vehicle-package` Tauri launch route now opens this inspection surface;
  opaque sources still fail closed until their metadata is visibly extractable.
- Copied authoring now uses the existing `VehicleAuthoringWorkspace` rather
  than a TypeScript reimplementation. Workspace creation and field changes each
  have separate read-only review and synchronous, action-confirmed apply calls;
  deterministic digests and exact revision checks reject stale state.
- Existing authoring workspaces can be reopened. React exposes every core
  identity and handling field in aligned groups, plus a non-duplicated structured
  Appearance tab for color/livery presets, resolved tuning-kit selection, and
  light/siren references. Appearance now also exposes one kit's metadata and
  visible/link/stat/slot entry collections, schema-driven entry fields, linked
  stream-asset evidence, builder findings, add/duplicate/update/remove/reorder
  review flows, and resolved light-profile scalar fields. Every mutation is
  rebuilt and validated in Python before its digest-bound confirmation. Project
  and exact-asset evidence remain beside the editor, with shared revision/dirty
  state and transactional undo/redo. The source package and GTA V are never
  written.
- Saved axle configurations now appear in a dedicated inspector mode as a
  top-down physical schematic. Axle selection is keyboard-accessible and keeps
  the diagram, wheel-bone/runtime-index evidence, steering, drive, service
  brake, handbrake, optional support weight, and export mode synchronized.
  Python performs the no-write axle review, handling-flag/drive-bias planning,
  deterministic digest, revision check, and transactional apply. Signed
  steering and intentional physical-order configurations stay read-only until
  their skeleton evidence can be verified. Native-model XML can now be selected
  directly: Python extracts the canonical wheel bones, detects axle groups,
  signs same-phase/counter-steer geometry, and proposes physical ordering.
- A fifth inspector mode edits transmission strategy, forward ratios, reverse
  ratio, and final drive in two aligned panels. Python validates and commits the
  digest-bound profile with the same revision history. The complete profile is
  an ALLIN1 authoring extension; stock `handling.meta` receives only the
  synchronized `nInitialDriveGears` count because it has no per-gear array.
- A sixth Output mode places Vehicle distribution and Managed package panels on
  one equal-height grid. GBAY listing, name/make, category, price, storage,
  preview, and explicit traffic settings use the same digest-bound revision and
  undo/redo path as other authoring changes. Package review binds the current
  revision, DLC source, distribution catalog, axle/transmission profiles,
  editions, and new output boundary before a separate confirmation atomically
  creates the managed package. The output receipt exposes the manifest, DLC,
  catalog, validation report, and preserved `vehicle-profiles.json`; no install
  or GTA V write occurs.
- Unsaved fields and in-flight authoring actions block model/category/workspace
  navigation until the user reviews, saves, or resets the draft.

Local validation:

```text
pytest tests/test_vehicle_authoring.py tests/test_vehicle_package.py tests/test_desktop_protocol.py tests/test_vehicle_viewport.py -q
67 passed

pytest tests/test_vehicle_viewport.py tests/test_native_model_renderer.py tests/test_rpf_tools.py tests/test_desktop_protocol.py -q
166 passed; 1 skipped

pytest -q
1,291 passed; 4 skipped

pnpm test
3 test files passed; 23 tests passed

pnpm build
TypeScript check passed; Vite production bundle generated

cargo test
4 tests passed

cargo check / cargo clippy --all-targets -- -D warnings / cargo fmt --check
passed

packaged sidecar smoke / Tauri v2 release executable / NSIS bundle
passed
```

The populated Vehicle Workbench and copied authoring flow were inspected at
1366×900, 1280×720, 720×900, and 480×800. Desktop pane headers, filters, row
baselines, and vertical boundaries align on one grid; the field editor remains
inside the evidence column with a sticky review bar. Confirmation copy exposes
source, destination, copy scope, revision, and exact before/after values.
Compact category navigation forms a balanced 2×2 grid, the panes stack at
bounded heights, and the page has no horizontal overflow. The structured
Appearance tab was exercised through review and revision save; its kit cards,
two-column lighting references, and color rows retain aligned labels at desktop
width and collapse to a single readable column at the compact breakpoint. The
tuning entry inventory/editor and asset/finding evidence use paired, equal-edge
grids without page-level horizontal overflow. Tuning and light-profile review
and revision saves were exercised in the component suite; the browser console
reported no warnings or errors. The axle schematic and detail editor remain
side by side at the tested widths, preserve selected-axle state after a save,
and report no horizontal overflow (`scrollWidth == clientWidth`). The
transmission panels share exact top/bottom edges; at 480 pixels only the ratio
list changes from two columns to one so decimal values remain readable.
The Output panels were inspected with the sidebar expanded at 1280×720 and at
the 720×560 minimum window. Both panels had identical measured heights, their
content stayed within the editor width, the six tabs remained legible, and no
page-level horizontal overflow or browser warnings appeared. The complete
distribution-review/save and package-review/build-receipt flow was exercised in
both the browser preview and component suite.
The native vehicle viewport was inspected with both collapsed and expanded
navigation at 1280×720 and at the 720×560 minimum window. Its LOD/component
controls switch to a single aligned column inside a narrow evidence pane, while
the wider stacked layout keeps LOD/component/surface controls on one equal
three-column grid. Orbit/keyboard camera updates, bounded textured mode,
material-ID mode, UV-coverage mode and its four-class legend, exact
surface isolation, YTD contact-sheet evidence, resolved/missing sampler states,
numeric Vector4 and Vector4-array shader rows, and the collapsed/expanded
material panel were exercised. The package-owned YBN toggle, exact-triangle and
diagnostic-box legend, expanded ownership/bounds/digest panel, and the disabled
state for a model without an owned YBN were also exercised. In the two-column inspector the short material
list stays pinned beside the longer evidence stream; the narrow inspector returns
to one natural column. Asset selection stays synchronized and both checked
layouts retained zero horizontal overflow. The UV
legend uses a 2×2 grid in the narrow desktop inspector and one four-column row at
the minimum stacked width without truncating its counts.
The flattened UV0 atlas was also exercised at 1280×720 and 720×560. Its Python
renderer groups topology by diffuse texture, connects bounded same-tile triangles
through shared mesh edges, overlays resolved YTD preview pixels, and reports sampled,
degenerate, missing, and cross-tile seam evidence without folding seams into false
base-tile polygons. React switches the atlas and per-texture evidence between an
aligned side-by-side grid and natural stacking from the inspector's actual width,
reverses the disclosure arrow, and introduces no page-level horizontal overflow
or alerts.
The three collision classes remain on one aligned row at both checked widths;
the narrow viewport places Collision and Reset view on a balanced two-button row.
The document, evidence pane, toolbar, and expanded collision evidence all reported
zero horizontal overflow, and no browser alerts appeared.

Packaged artifacts from this phase:

- `desktop/src-tauri/sidecar/ALLIN1-SDK-Desktop-Sidecar.exe` — 87,790,978
  bytes; SHA-256
  `71DD211745917E945D8D5645E25E20FC21BFAEF0C0255A747611874AC77437C7`.
- `desktop/src-tauri/target/release/allin1-sdk-desktop.exe` — 10,499,584
  bytes; SHA-256
  `0A273955A4820E9FF14B1AA4519065ED55A861099348B990AD2E17083C0C6276`.
- `desktop/src-tauri/target/release/bundle/nsis/ALLIN1 SDK_0.1.0_x64-setup.exe`
  — 86,319,664 bytes; SHA-256
  `2EB81222A8CE68141FD511A34A7E588144F7D1369E31FBD61EDEBF86753B0456`.

Known limitations:

- Identity cloning, runtime profile activation/preflight, and package
  installation still use their existing Tkinter or dedicated package tools.
- The native vehicle viewport is migrated for bounded diagnostic frames,
  texture-binding and exact numeric shader-parameter evidence, approximate
  UV0 sampling of resolved linked YTD previews, and a bounded flattened UV0 island
  atlas. It spatially classifies UV0 health on the rendered triangle sample and
  overlays package-owned YBN triangle/box evidence in the same camera.
  Non-triangle/box collision primitives and cross-tile atlas seams remain count-only
  rather than approximated. Full game-shader
  reproduction, compiled Blender studio renders, and WebGL-resident geometry
  remain separate follow-up work; the React client never parses untrusted RAGE
  resources itself.
- Weapons, Peds, Maps, and the Axle Configurator visual-package/build/export tools
  remain in the Tkinter fallback.

## Phase 4 — Package Recipes

Status: **partial**

Implemented:

- The versioned desktop contract now exposes `inspect_recipe` as a read-only,
  isolated, cancellable job. Rust and Python both allowlist the operation;
  unsupported file types and invalid paths fail closed before planning.
- `OivWorkbench.inspect` remains the sole parser and safety decision maker.
  The desktop response contains bounded recipe identity, ordered operations,
  structured findings, readiness gates, and assembly digest evidence. Package
  instructions are never executed during inspection.
- React provides ordered-operation filtering, finding selection, exact
  operation/finding detail, stale-result rejection, refresh/cancel behavior,
  responsive three-pane review, and an explicit execution-boundary notice.
- Report destinations remain Rust-owned native dialogs. Markdown/JSON report
  generation delegates to the existing `oiv-plan` Agent command and retains
  its `authoring_write` risk classification; React does not write report files.

Local validation:

```text
pytest tests/test_desktop_protocol.py tests/test_addon_sdk.py tests/test_agent_api.py tests/test_addon_importer.py -q
134 passed

pnpm test
2 test files passed; 15 tests passed

pnpm build
TypeScript check passed; Vite production bundle generated

cargo test / cargo check / cargo clippy --all-targets -- -D warnings / cargo fmt --check
passed
```

The Package Recipes development fixture was exercised at 1440×900 and the
720×700 compact breakpoint. Filtering, operation/finding selection, detail
evidence, report export, and responsive pane order passed without horizontal
page overflow. Information-bearing row and detail text remains at least
11.52 px in the compact layout.

Known limitations:

- Existing-RPF compilation, atomic batch generation, created-archive builds,
  and managed-package export remain in the Tkinter fallback. They require
  separately designed authoring contracts and are not exposed as cancellable
  read-only jobs.
- The React workspace does not yet expose GTA/archive selection because this
  milestone stops at inspection and report export.

## Phase 4 — Vehicle Quick Import review and guarded preparation

Status: **partial**

Implemented:

- The versioned desktop contract exposes `inspect_vehicle_quick_import` as a
  read-only, isolated, cancellable operation. Rust and Python both allowlist
  it; source paths, optional GTA installations, and preferred editions are
  validated before the existing `VehicleQuickImportService` is invoked.
- Native dialogs accept bounded package archives, direct RPF sources, and
  unpacked folders. GTA selection reuses the canonical folder picker and the
  existing detector remains the fallback when no installation is selected.
- React provides explicit inspection settings and aligned branch, vehicle,
  and evidence panes. Branch selection filters vehicles by edition, selected
  metadata remains read-only, and the view clearly states that no Launcher
  package or GTA file was written.
- The `review_vehicle_quick_import` job re-inspects the source, regenerates the
  selected edition's plan, and applies bounded in-memory edits through
  `VehicleQuickImportService.customize`. The returned package identity,
  catalog, source digests, destination state, warnings, and deterministic review
  digest are canonical Python output; the WebView never writes a draft file.
- The selected listing exposes display name, manufacturer, price/free
  acknowledgement, category/storage, size tier, traffic eligibility, and
  existing preview references. Legacy and Enhanced drafts remain isolated when
  switching branches. Changed drafts block source replacement and workspace
  navigation until they are validated or reset.
- `prepare_vehicle_quick_import` is a synchronous, non-cancellable
  `authoring_write` operation. It regenerates the source, draft, and destination
  evidence, rejects a stale review digest, and requires explicit action-time
  confirmation. Existing destinations also require a separate replacement
  acknowledgement and must prove intact SDK ownership.
- The preparation service stages and validates output before an atomic swap,
  reports whether it created or replaced the package, writes only the per-user
  Launcher library, and leaves Launcher trust/install review as a separate
  action. It never writes GTA V.
- The reusable React confirmation surface also gates SDK Console authoring
  commands. The protocol independently rejects unconfirmed authoring commands;
  broad Agent API game-write commands remain disabled by the Tauri launch path,
  while managed package writes use a separate least-privilege capability.

Local validation:

```text
pytest tests/test_desktop_protocol.py -q
20 passed

pytest tests/test_desktop_protocol.py tests/test_vehicle_quick_import.py tests/test_vehicle_quick_import_cli.py tests/test_agent_api.py tests/test_addon_sdk.py tests/test_addon_importer.py -q
152 passed

pnpm test
2 test files passed; 19 tests passed

npm run build
TypeScript check passed; Vite production bundle generated

cargo test / cargo check / cargo clippy --all-targets -- -D warnings / cargo fmt --check
passed
```

The populated Quick Import fixture was inspected at the desktop viewport and
the 720×700 compact breakpoint. The three desktop panes share aligned headers;
the compact view stacks without horizontal overflow (`scrollWidth == 720`),
editor controls remain 31 px high, and information-bearing editor text remains
at least 11.52 px. The authoring confirmation was also verified at 1366×900 and
720×700: it remains fully visible with 10 px compact margins, both actions are
33 px high, the details collapse to one column, and the completion receipt stays
within the viewport without horizontal overflow.

Known limitations:

- Deterministic publish ZIP, standalone Legacy OIV export, and direct Launcher
  handoff remain in the Tkinter fallback. Those are separate destination and
  process-launch contracts; they are not implied by Launcher-library preparation.
- The live source scan still depends on the same RPF decoder/toolchain
  availability as the existing Python Quick Import workflow.

## Phase 5 — Models & Materials and Qwen assistant

Status: **partial**

Implemented:

- `inspect_model_materials` is a typed, isolated, cancellable read-only job for
  loose YDR/YDD/YFT assets. Python retains native decoding, size limits,
  symlink rejection, shader/texture/geometry relationships, and findings.
- Same-stem sibling YTD and YBN files are discovered case-insensitively and
  supplied to the existing native viewport without copying or extracting them.
  React presents aligned material, geometry, and rendered-evidence panes plus
  shaded, textured, material-ID, UV, wireframe, collision, sampler, parameter,
  texture-sheet, and UV-atlas views.
- Loose models can be copied into the existing guarded material-authoring
  workspace, reopened later, and edited through deterministic shader-name,
  texture-binding, and geometry-assignment reviews. Commits are bound to the
  exact revision, preserve the immutable source snapshot, and expose verified
  undo history.
- Native Vector and Array shader parameters are retained as exact Vector4 rows
  in material inspection. React exposes them behind a compact Bindings / Parameters
  switch in the existing material pane. A parameter review targets one existing
  unique name, preserves its original type and row count, bounds every component
  to a finite float32 value, and records component-level diffs without synthesizing
  schema fields.
- Native YDR/YDD/YFT output now has a separate read-only build review and
  synchronous confirmed apply. The destination must retain the source
  extension, must not exist, and must resolve outside the authoring workspace
  and every detected GTA V installation. `RpfPatcher` compiles the staged XML,
  decodes the output again, checks the semantic XML digest, and publishes the
  asset plus its SHA-256 validation receipt as one guarded result.
- React presents that result as a compact evidence receipt and can compare the
  immutable source snapshot and reparsed compiled asset in equal, synchronized
  viewport panels. The panels stack below 1000 px without horizontal overflow.
- Models & Materials now has a restrained two-area switch instead of mixing YTD
  controls into the model surface editor. The Texture dictionaries area creates
  or reopens a separate native YTD workspace and keeps its inventory and selected
  preview/evidence panes on one exact shared header and content grid.
- Texture catalog opening verifies the immutable source snapshot, native XML,
  dependency paths, DDS headers, dimensions, mip counts, formats, and hashes.
  Selected textures render through the bounded preview-artifact cache; missing
  or undecodable dependencies remain visible as metadata warnings.
- Add/replace accepts validated DDS or bounded PNG/JPEG/BMP/TGA/WebP sources.
  Raster conversion to one-mip uncompressed RGBA DDS is stated in the review;
  remove reviews warn about external bindings. Apply regenerates the review
  against the exact workspace/source digest, requires confirmation, and records
  undo history.
- YTD builds require a new destination outside the workspace and GTA V. The
  output and receipt are accepted only when their path/size/SHA evidence matches,
  reparsing succeeds, and semantic XML matches. A negative semantic-receipt test
  confirms both newly created files are removed on rejection.
- `assistant_status` passively reports Launcher-owned Qwen/provider readiness
  without starting inference. Missing configuration is a typed UI state rather
  than a sidecar failure.
- `assistant_prompt` invokes the existing grounded structured-response client
  in an isolated worker. The desktop receives advisory evidence and bounded
  telemetry but not the potentially large private grounding context. It cannot
  execute recommended operations or write workspace, package, or GTA state.
- Windows cancellation terminates the complete worker process tree so a local
  llama/Qwen runtime does not survive a stopped request. Non-Windows workers
  use their own process group and receive the equivalent group termination.
- The docked console keeps ordinary Agent API command execution intact and adds
  a distinct Qwen surface with provider status, prompt cancellation, structured
  findings, confidence/status evidence, missing context, and safety notes.

Local validation:

```text
pytest tests/test_texture_workspace.py tests/test_desktop_protocol.py -q -k "texture_workspace or texture_source"
10 passed

pytest tests/test_model_materials.py tests/test_desktop_protocol.py -q
50 passed

pytest -q
1306 passed; 3 skipped

pnpm test
3 test files passed; 29 tests passed

pnpm build
TypeScript check passed; Vite production bundle generated

cargo test / cargo clippy --all-targets -- -D warnings / cargo fmt --check
8 tests passed; Clippy and formatting passed

build_tauri_desktop.ps1
PyInstaller sidecar smoke passed; Tauri v2 x64 NSIS installer generated
```

The model-surface fixture was exercised at 1440×900, 900×900, and
600×820. Desktop pane headers share one exact 61 px row; the 900 px layout keeps
material and geometry headers aligned and moves evidence to a full-width second
row. At 600 px all panes stack, the document has no horizontal overflow
(`scrollWidth == innerWidth == 600`). The new authoring controls form two clean
rows at compact width, the receipt remains within its 540 px workspace, and the
comparison uses equal 689 px panels at 1440 px before stacking to equal 835 px
and 563 px panels at the two smaller breakpoints. No browser console errors were
reported. The Qwen status, prompt, and structured finding result were also
exercised at desktop and compact widths.

The populated Texture dictionaries fixture was exercised at 1440×900,
900×900, and 600×820. At both desktop widths its inventory and preview headers
share the exact same top coordinate and 61 px height; the measured desktop
columns were 436/886 px and 298/496 px respectively. At 600 px the panes stack
to the same 538 px width and `scrollWidth == innerWidth == 600`. The compact
build-review dialog remains fully within the viewport at 580×630 px. The YTD
selection, copied-workspace creation, preview, and build review were exercised
without browser warnings or errors.

The numeric material-parameter editor was exercised in both read-only and
editable workspace states at 1440×900, 900×800, and 680×800. At 900 px the
material and geometry panes measured the same 397×543 px before evidence moved
to the full-width row. At 680 px all panes measured the same 618 px width and
stacked without horizontal overflow (`scrollWidth == innerWidth == 680`). The
four Vector4 component cells remained labelled and editable, and no browser
warnings or errors were reported.

Packaged artifacts from this phase:

- `desktop/src-tauri/sidecar/ALLIN1-SDK-Desktop-Sidecar.exe` — 87,830,494
  bytes; SHA-256
  `FD3018C74946A872CD8970BE69E716F282C908BEC07481A66419E56DAFDC1BDF`.
- `desktop/src-tauri/target/release/allin1-sdk-desktop.exe` — 10,660,864
  bytes; SHA-256
  `AFF732E439336159396D160D9595BBE2F3E0F6A1951938D4308A53E6C3DD43B0`.
- `desktop/src-tauri/target/release/bundle/nsis/ALLIN1 SDK_0.1.0_x64-setup.exe`
  — 86,389,842 bytes; SHA-256
  `BE1D151EB106418E369EFE39B857356412BB70C5515321F654A1E117EBF95B09`.
- A native launch found a live Tauri window and the packaged sidecar in its
  process tree. The desktop had a nonzero window handle while the sidecar's
  handle was exactly zero, revalidating the hidden-console launch path.

Known limitations:

- Shader-schema synthesis, bulk parameter transforms, and advanced texture
  processing/bulk export remain in the Tkinter workbench. React model authoring
  intentionally limits itself to existing shader names, existing texture slots,
  existing numeric Vector/Array rows, and valid local geometry assignments;
  React YTD authoring uses validated dependency-level add, replace, remove, and
  undo operations.
- The first Qwen surface accepts a focused question and the assistant's normal
  SDK grounding. Explicit per-workspace source/symbol selectors and warm-runtime
  reuse across isolated prompt workers remain future refinements.

## 2026-09-04 — Weapon shop and animation parity

Migrated the Tkinter domain's independent shop edits and animation-mapping
cloning into the Tauri Weapon Workbench. The Weapon section selector separates
weapon/ammo, GTA shop metadata, and animation mappings. Shop controls explicitly
distinguish GTA shop metadata from ALLIN1 GBAY catalogs. Animation copying is
limited to previously unmapped targets; it never replaces existing mappings or
generates animation assets.

Verification completed:

- 160 focused Python tests passed across weapon authoring core/CLI, desktop
  orchestration, shop/animation, bundle cloning, camera/flags, and RPM.
- 55 desktop-protocol and standalone-resource tests passed.
- All 63 React tests passed across eight files, including seven new
  shop/animation tests for read-only sources, exact source choice, confirmation,
  save/undo selection, stale-save recovery, and numeric boolean display.
- Rust `cargo check`, production React compilation, and Tauri NSIS packaging
  passed. The normal standalone browser build emits a non-blocking 500 kB chunk
  advisory; the Tauri-targeted build completed without that advisory.
- The expanded packaged-sidecar smoke passed both against the build artifact
  and against the updated standalone installation. It exercises real shop edit
  and exact undo, independent animation mapping clone and exact undo, as well
  as the existing RPM/camera/component/attachment/bundle and other smoke flows.

The browser fixture was checked at its default 1280×720 viewport in light and
dark themes. The three weapon panes shared the same top coordinate, equal
704.34375 px height, and 76 px headers in the animation-review state. No pane
had horizontal overflow. The review displayed every source/set and the
template/target distinction. A source-identity spacing issue was corrected and
rechecked. Browser console warnings/errors were empty. Native window automation
was unavailable; this is browser presentation evidence, not a native-window
interaction or in-game animation test.

While packaging with the preview open, Vite attempted to watch a locked Rust
executable and exited with EBUSY. `server.watch.ignored` now excludes
`**/src-tauri/**`, which also prevents generated Tauri HTML from reloading React.
The development server restarted successfully and stayed available through
subsequent Rust checking and frontend compilation.

The current-user standalone SDK was updated using the NSIS installer (exit 0).
The installed sidecar matches the build hash. The installed desktop binary
differs from the unbundled release binary only in Tauri's three-byte bundle-type
marker (`UNK` → `NSS`), verified by byte comparison and the local tauri-utils
implementation. Installed artifacts:

- Installer: 68,451,411 bytes; SHA-256
  `5201f8281c499ea994ae42b4520b0fa1226ddb91aa91d9fdf1f73222be4aa35b`.
- Sidecar: 39,900,666 bytes; SHA-256
  `953274c180280f48a61010eaa71b09791f9829066ea1a5f56c28c54f33f51fdc`.
- Installed desktop executable: 10,723,840 bytes; SHA-256
  `a6ec6eb685ccc46f881330aa3b737cab903d15cb855df78ad493d76658c5f481`.

The exact prior standalone directory and previous build artifacts were backed
up under `.work/pre-weapon-distribution-build-20260904-005754/`, with the exact
installed files in `installed-sdk/`. Original authoring inputs, the older
Launcher-managed SDK, the installed KRISS Vector package, and game files were
not changed by this migration slice.

Remaining weapon gaps include native preview, publication/GBAY catalog authoring,
and ordinary bullet-ammo donor completeness. See
`weapon-shop-animation-authoring.md` and `tauri-feature-parity.md`.

## 2026-09-04 — Native weapon preview

Added an on-demand, full-width Model preview panel to the Weapon Workbench.
It resolves exact body/component YDR/YDD/YFT candidates, follows bounded
weaponarchetypes model/texture declarations (including shared YTDs), exposes
explicit duplicate/base/high-detail model choices and texture overrides, and
requires an explicit decoder edition when package evidence is unresolved.
Stock/external attachments are not silently substituted. This is separate-part
geometry inspection, not an assembled preview or proof of scope alignment.

Saved snapshot adoption resets preview selection and stale frames while
retaining the open panel; unsaved metadata is explicitly not applied. Existing
orbit, pan, zoom, LOD, surface, material and UV diagnostics are reused. Decoder
failures now stop the loading indicator and offer Retry. The triangle footer
uses the actual Python `model_rendered_triangle_count` field.

Validation: 157 focused Python tests; 80 React tests; Rust check; frozen-sidecar
standalone smoke; packaged Vector inspection (five parts and shared irons
texture resolution); Tauri v2/NSIS production build. Browser fixture verified the
narrow layout and missing-edition state. Native Vector body, magazine and
separate irons rendered successfully through the real decoder with no warnings;
package hashes remained identical. Stock optic and suppressor correctly stayed
unavailable in this package-only view. Private native preview artifacts live in
`.work/weapon-native-preview-cache/` and are not distributable mod assets.

The build exposed a stale generated `desktop/vite.config.js` that shadowed the
maintained TypeScript config and bypassed the Rust-output watcher exclusion.
It and its generated declaration were moved, not deleted, to
`.work/pre-vite-config-shadow-20260904/`. Dev, build and test scripts now choose
`vite.config.ts` explicitly. The real watcher verifier also checks default
configuration discovery and passed: frontend sources watched, the entire
`src-tauri` tree excluded. All 80 React tests and the production installer build
passed again with the explicit configuration.

Installer SHA-256:
`3a458990d5b3b8598e98a9173f96cf044205c1ba96c8bb730fc101e72a927dfe`.
Sidecar SHA-256:
`3509b2c3bc1ae7788955c9bd8325be3d6cb4239eeb5072c21c581cb7d5f5b2cf`.
Built outputs were **not installed**; the user's installed SDK, GTA files,
Vector DLC and save data were left untouched in this pass.

### Follow-up installation — 2026-09-04

On explicit approval, the NSIS installer above updated the existing current-user
SDK (exit 0). All 266 previous installation files were backed up and checked
under `D:/ALLIN1-SDK-Backups/vector-runtime-refinement/20260904_092137_418324/installed-sdk/`.
The installed sidecar matches the build hash and passed the standalone
resource-checksum, bundled-helper and protocol/authoring smoke suite.
Installed desktop SHA-256:
`e2eef719cf74b0b614246a7cabb6c067b64cb410ce47cbd0b633c385eeddee9d`.
Its bytes match the release binary after only the expected Tauri bundle marker
change from `__TAURI_BUNDLE_TYPE_VAR_UNK` to `__TAURI_BUNDLE_TYPE_VAR_NSS`.
The installation resolves to the current user's Codex-virtualized LocalAppData
SDK directory; no SDK or GTA process was launched for gameplay validation.

## Quick Import Legacy OIV export — 2026-09-04

Migrated the existing Legacy vehicle OIV workflow into Tauri v2. Quick Import
now exposes author credit and a constrained native save destination, a separate
cancellable read-only review, and explicit confirmation before synchronous
authoring. Export does not prepare or access the Launcher library. The existing
Python exporter still writes and verifies the archive; no TypeScript writer or
second export implementation was introduced. Source, plan, package identity,
author and destination are bound to the reviewed digest and inspected again
before writing. Existing outputs, Enhanced branches, unsafe filenames, redirected
paths, and destinations inside the source or game are rejected.

The UI distinguishes standalone OIV contents from GBAY listings, traffic settings,
receipts and rollback. Export settings/review/write guard navigation, source and
edition changes. Failed writes invalidate confirmation without discarding author
settings; cancelled or early-terminal reviews cannot produce stale authorizations.
Browser preview export writes are explicitly disabled.

Validation:

- **101 Python tests passed:** desktop protocol, new OIV facade, existing exporter,
  Quick Import and standalone SDK regressions. Fixture-scanned plans use the real
  archive writer and ZIP readback; stale payloads/identity/author/output and safety
  boundaries are exercised.
- **89 React tests passed**, including 9 new OIV flow/race/navigation tests.
- **9 Rust tests passed**, plus production TypeScript/Vite and Cargo checks.
- Frozen sidecar smoke passed, including new OIV risk/confirmation/Legacy gates
  and standalone bundled-helper/resource checks.
- Browser visual check at 1280×720 and 900×800: form alignment, compact review,
  text/hash wrapping and confirmation controls verified. All three Quick Import
  headers measured 56px at the same top coordinate. Fixed the author input's
  native unstyled background and removed duplicate disabled fields during review.
- Actual Vite watcher check passed: frontend watched, Rust/build subtree excluded.
- Tauri v2 NSIS installer built successfully. Vite's existing >500 kB bundle
  warning remains; it did not fail the build.

Installer SHA-256:
`c4a043efeba96861d9fea616f61da24bdd7eef7042a17b30836be85970ac1c86`.
Sidecar SHA-256:
`5c3ca9052416a942610c0cdd620b8b1cbd5a14e31e89368280e3030b64264dd2`.
Release desktop SHA-256:
`d1652cbb8f8cd0e90188e21b837e191db746bbddb651e9908221ac22dcceec9f`.

This build was **not installed**. Installed SDK, GTA, Vector, Suppressors Enhanced
and save data were left unchanged. Clean-machine native-dialog/end-to-end OIV
installation testing remains separate. The next Quick Import parity gap is
managed package ZIP publication.

## September 4 follow-up: installed OIV build and migrated ZIP publication

The previously recorded OIV build was subsequently installed at the user's
request, with 267 existing SDK files backed up and verified under
`D:/ALLIN1-SDK-Backups/sdk-oiv-migration/20260904_025855_1041377`.
Installed-sidecar smoke passed. The installed desktop differs from the recorded
release binary only by the three-byte Tauri bundle marker (`UNK` to `NSS`).

Quick Import now offers publication of its prepared package to a new ZIP via
the native save picker. A cancellable read-only review displays package identity,
edition, destination, GBAY vehicle listings/prices, traffic preference and five
exact member hashes. Separate action-time confirmation starts a non-cancellable
write through the existing Python publisher. No upload or game write occurs.
Unsaved draft settings and extra folder files are excluded. Review evidence is
revalidated and each streamed member checked; changed inputs, unsafe paths,
redirected parents and existing/concurrently claimed outputs are rejected.

An integration test caught navigation locks incorrectly invalidating the prepared
package. Locks and actual draft changes are now distinguished. Native export ACL
coverage also found the preceding OIV slice lacked explicit command permissions;
both OIV and ZIP commands now have build/capability entries and Rust regressions.

Validation: 160 focused Python tests; 98 React tests; 10 Rust tests; production
TypeScript/Vite and Cargo checks; frozen sidecar smoke including both export
confirmation/risk gates. Browser checks at 1280x720 and 900x800 verified review,
GBAY table, expanded file hashes, readable wrapping and reachable confirmation
controls without horizontal overflow. Browser preview writes remain disabled.
The computer-use skill guided the visual check; its fixture consistency issue
was corrected so prepared identity/edition/listing prices agree with ZIP review.

Clean-machine/native save-dialog E2E and gameplay validation remain pending;
browser and fixture tests do not establish those outcomes.

The refreshed ZIP/permission-fixed SDK was built and installed successfully.
The prior installation's 267 files and reviewed installer are backed up under
`D:/ALLIN1-SDK-Backups/sdk-zip-migration/20260904_031710_8471153`.
Installed resource/checksum and frozen-service smoke passed. An additional
installed-service test reviewed, wrote and reopened both Legacy and Enhanced ZIP
fixtures, verified every member hash and GBAY entry, and left mock game folders
empty. Its initial test harness omitted the protocol handshake; after correcting
the harness, both editions passed without repeating the installation.

Installer SHA-256:
`b61755ee7e2278c06b3252fde8b501192f1614977d5d63a81fea3f6db53cda03`.
Installed desktop SHA-256:
`2216ac27b15e66edba19d67e3b8ab7e6d57accbc6d69ffb08b23ae69891e4e91`.
Installed sidecar SHA-256:
`86f8c78b739f1a800f1ce92c9a93108bc784bb5024145b0360e61bb589c3844c`.
The desktop matches its release binary except for the expected NSIS marker.

## September 4 follow-up: GXT2 text authoring

RPF Archives now includes a GXT2 game-text tab. A loose source stays read-only;
authors can create an editable copy or reopen a text workspace, search hashes
and multilingual text, review add/edit/remove operations, undo, and build a new
dictionary with a verification report. Three aligned panels separate inventory,
editing and validation/history. Source switching, RPF tabs, sidebar/keyboard
navigation and direct-open requests cannot discard an active review or text
draft. Browser previews have no file-write implementation.

All writes delegate to the existing Python GXT2 workspace. Added operation
locking, exact pre-edit snapshot bytes and rollback on history-write failure.
The latter fixes history validation for originally unsorted dictionaries.
Exclusive output/report creation prevents files appearing after review from
being overwritten. Desktop review hashes bind the original, current entries,
manifest and history; stale inputs and redirected paths are rejected.

Validation: 81 targeted Python tests passed with no skips; all 108 React tests
and 10 Rust tests passed. Native permission declarations are regression-tested.
Frozen sidecar smoke passed with development Python/.NET removed from its PATH,
including real GXT2 create/edit/add/remove/undo/build operations, independent
expected-byte comparison, report existence and unchanged original bytes.
The full React run exposed a test startup race; waiting for the initial archive
index before selecting the text tab resolved it. TypeScript test signatures
were also corrected before the production build.

The computer-use skill guided browser preview QA at 1280x720, 900x800 and
680x800. Full-width panels share header baselines; narrower layouts use two
columns then one, without horizontal content overflow. Verified Unicode search,
filter reset, readable before/after review, disabled unconfirmed writes and draft
reset. Missing outer workspace spacing and search/editor field spacing were
corrected during this pass. No browser console warnings/errors were observed.

This slice does not extract a selected GXT2 member from an RPF, insert a built
dictionary into an archive, or install it into GTA. Native dialog/clean-machine
E2E, physical window-close guards and gameplay remain unverified. Tkinter is
retained. The new candidate is not installed; the installed ZIP-publication SDK,
GTA Enhanced, Vector and Suppressors Enhanced are unchanged by this turn.

Production TypeScript/Vite and x64 NSIS build passed. Vite still reports the
existing large-bundle advisory; this is not a failed build. Final browser
measurement at 1280x720: all three headers are 74px high on the same baseline;
the search and label-hash inputs are both 37px high with identical top positions.

Candidate installer SHA-256:
`9f22b9cb10ac026c993a91102fdcd40ec7189d3325e323224426418269168be5`.
Release desktop SHA-256:
`6b95d66f68829a277b5567535acf49c0f159588fd8b5d561aeacda70121d0b9b`.
Frozen sidecar SHA-256:
`bbc3ad7afb7d82ee38c7e19a4e8581f962440c35ccee3c8ba98d5406929a20f5`.

## September 4 follow-up: selected RPF dictionary handoff

RPF entry evidence now offers **Open in text editor** for bounded GXT2 members.
The handoff uses the exact recursive entry ID, outer archive and selected GTA
context, never a filename-only lookup. Intake remains read-only; editing starts
only after a separately reviewed and confirmed new workspace copy. Switching
between archive and text tabs retains the selected archive row and text session.
Dirty drafts, pending reviews and writes continue to guard navigation.

The shared `RpfExplorerService.read_gxt2_entry` now backs both desktop intake and
the existing Tkinter/CLI export path. It checks index membership/type/size,
extracts into an owned temporary file, bounds reads, compares extracted size,
parses the complete dictionary, and compares the outer archive hash before and
after extraction. Copy review binds that archive hash, exact entry, edition,
GTA context and dictionary bytes. Apply rechecks them; changed archives,
changed member bytes and same-named members in another layer cannot reuse an
old review. Copied workspaces/build reports retain the archive provenance and
remain editable if the original archive is subsequently unavailable. They do
not claim that their historical binding matches a live archive indefinitely.

Validation:

- 194 focused Python tests passed across GXT2 desktop/core, RPF tools and
  desktop protocol; one pre-existing Windows symlink-privilege test skipped.
- All 114 React tests and 10 Rust tests passed. New React coverage includes the
  actual archive-button handoff, tab retention, StrictMode intake, separately
  confirmed copying, wrong-member/hash evidence, late job cancellation and
  preservation of an existing dirty draft.
- Production TypeScript/Vite, frozen standalone sidecar smoke and x64 NSIS
  build passed. Vite retains its existing >500-kB bundle advisory.
- Extended packaged smoke with `--rpf-game-path` using the Enhanced installation
  for read-only decoding context. Built a temporary OPEN RPF with distinct root
  and nested dictionaries sharing `global.gxt2`; inspected, copied, edited and
  built both via the frozen protocol. Independent expected-byte comparisons and
  preserved build provenance passed. The original RPF SHA-256 stayed unchanged.
  Development Python/.NET paths were removed from the sidecar environment.
- Computer-use-guided browser QA at 1280x720, 900x800 and 720x800 confirmed the
  three aligned 74px headers at full width, two-/one-column responsive layouts,
  readable archive/member review and disabled unconfirmed writes. No horizontal
  document overflow or browser console warnings/errors. Temporary preview tab
  and development server were closed after testing.

Candidate only: the installed SDK and GTA/Vector/Suppressors files were not
updated. Native file-dialog and clean-machine E2E remain unverified. This slice
does not insert a built dictionary back into an RPF, stage a replacement plan,
or install anything into GTA. Staged archive replacement is the next migration
slice; Tkinter remains the fallback.

Candidate installer SHA-256:
`ed338210d57c9a339c42f5066e81702a557230bcedb688e75d66fbce93efc093`.
Release desktop SHA-256:
`b7fd86697ee83551d6c70749ea9132aa16de32108e71880b93f9e043e93cfcc8`.
Frozen sidecar SHA-256:
`93bdd3bb7e8f61749bd0c7835aec148a427cd9df2a3a6a0e42066876ef7b5075`.

## September 4 follow-up: reviewed GXT2-to-RPF packaging

Archive-bound text workspaces now expose **Review RPF package** after saved
edits. A native parent-folder picker and new folder name identify the output.
The review shows the original archive, exact recursive member, dictionary
byte sizes, payload verification count, required free space and output files.
Confirmation is separate from review; dirty edits, stale/cancelled responses,
source changes and existing destinations cannot authorize a build.

The existing Python RPF transaction writer receives only a generated temporary
archive copy. Its original basename is preserved under `archive/`, including
for filename-dependent decoding. Before publication, the backend checks the
same archive structure, reparses the replaced dictionary, verifies all other
payloads (canonical resource fingerprints where recompression is permitted),
and rechecks source/workspace hashes. Ancestor archive containers necessarily
change for nested replacements and are not compared as unrelated byte blobs.
GTA must remain closed. Exclusive Windows directory rename publishes only a
new output folder; failed checks discard temporary files, not existing outputs.

The folder contains the new RPF, replacement GXT2, dictionary validation report
and `rpf-package.json` with source provenance, hashes and per-payload evidence.
Temporary transaction backups are not shipped. This is a single-dictionary
archive-copy workflow, not a general change-set editor or installable ALLIN1
package: it generates no installer manifest and performs no game writes.

Validation:

- 224 focused Python tests passed; one pre-existing Windows symlink-privilege
  test skipped. Thirty new parametrized packaging cases cover root/nested
  members, exact artifact inventory, confirmation/state guards, unrelated
  payload corruption, structural changes, transaction failures, receipt
  mismatch, source changes during build, destination races, unchanged/unbound
  inputs, edition mismatch, game destinations and disk/game preflight.
- All 121 React tests and 10 Rust tests passed. Seven new component tests cover
  reviewed packaging, unbound sources, dirty-state/picker cancellation, stale
  apply, fresh confirmation, malformed evidence, late read cancellation,
  double submission and mismatched write outcomes.
- Production TypeScript/Vite, full Windows build script, frozen sidecar base
  smoke and x64 NSIS build passed. The existing >500-kB Vite advisory remains.
- Extended frozen sidecar smoke with `--rpf-game-path` passed using Enhanced
  only as read-only decoding context. A generated OPEN RPF contains distinct
  root/nested `global.gxt2` dictionaries. Each was independently copied,
  edited, built and packaged, then both dictionaries in each output RPF were
  reread to prove the correct target changed and the other stayed unchanged.
  Archive/report hashes matched and the original RPF SHA-256 was preserved.
  The first attempt exposed an invalid slash-containing request ID in the
  new smoke harness; indexed IDs fixed it and the full rerun passed. The
  sidecar used no development Python/.NET paths.
- Computer-use-guided browser QA at 1280x720, 900x800 and 720x800 confirmed
  readable package contents, safety copy and confirmation controls, two-/one-
  column review layouts, no horizontal overflow and aligned 74px editor
  headers at full width. No browser warnings/errors. Preview tab and Vite
  server were closed afterward.
- `git diff --check` passed for tracked changes (existing CRLF normalization
  warnings only). Existing unrelated worktree changes were preserved.

Candidate only: not installed. The installed SDK and GTA/Vector/Suppressors
files were not updated. Native dialog/clean-machine E2E, large/encrypted
production-archive coverage and installable ALLIN1 manifest export remain.
General multi-entry RPF change sets and explicit rollback UI remain unmigrated;
Tkinter is still the fallback.

Candidate installer SHA-256:
`f030d15d0c5790c124a9574d7c734c0bc9fca3e51c8d634501eba328af76f924`.
Release desktop SHA-256:
`cc012eca9830881a4ba7b891eeb94140be04a4c0564006beaca7e0fcbf3131a4`.
Frozen sidecar SHA-256:
`99808cc2a7428c3db98a5a6a4cff4bced2a0b59bdb25303418efe364824bb1a3`.

## September 4 follow-up: installable ALLIN1 RPF ZIP export

The GXT2 packaging section now offers **Configure ALLIN1 export**. Authors can
use the just-built RPF folder or reopen an existing verified build, enter an ID,
name, version and author, and choose an explicit GTA-relative archive target.
The target must stay below `mods/` and preserve the original archive basename;
no destination is inferred from an external filename. Edition is locked to the
build's source edition. This is intentionally a whole-archive replacement, not
a member-level delta or new DLC registration. That overwrite risk is displayed
in the settings, review, confirmation and generated README.

`publish_rpf` uses the existing reviewed GXT2 operation family and native ZIP
picker. Python checks the build report, dictionary validation, current workspace
state, archive bytes, per-payload comparison evidence and metadata before
generating a schema-1 `mod.toml`. It contains one checksum-bound RPF `[[files]]`
entry, the `openrpf` dependency, and no `dlc_packs`. Export does not need a live
original archive or game context, since native RPF writing already completed.
The package loader's 4-GiB member limit applies even though RPF build supports
larger archives.

The new ZIP contains exactly:

- `mod.toml`
- `payload/<original-rpf-name>.rpf`
- `allin1.rpf-build.json`
- `README.txt`

Portable evidence excludes automatic local workspace/GTA paths. ZIP entries
have stable ordering/timestamps, explicit size/hash evidence and no compression
ratio surprises. The staged ZIP is independently rehashed and opened through
the existing ALLIN1 package loader before source/workspace rechecks and exclusive
Windows publication. Existing outputs are never replaced. Authoring remains
non-cancellable once writing starts; export does not install or upload anything.

Validation:

- 303 focused Python tests passed in the combined protocol/GXT2/RPF/publication/
  package-contract run, with three existing Windows symlink-privilege skips.
  The publication suite subsequently passed all 58 cases, including two added
  Launcher install/restore cases (305 distinct passing focused tests overall).
  These cover stale source/metadata/output/confirmation, malformed evidence,
  unsafe/renamed targets, size/disk limits, output races, staging cleanup and
  publication without the original archive. Both SDK and Launcher loaders
  accepted the generated ZIPs.
- Launcher installation, receipt creation, backup and uninstall restoration
  succeeded against temporary fake-game directories for both fixture variants.
  Only dependency/loader availability was simulated; no real GTA files were used
  as install targets. This is not a real-game or clean-machine install test.
- All 127 React tests and 10 Rust tests passed. Six new React cases cover metadata
  review, explicit overwrite acknowledgement, cancelled native selection/review,
  stale failures, preserved settings, fresh confirmation, double submission,
  malformed reviews and mismatched write outcomes. Opening/returning to export
  settings also asserts keyboard focus on the settings heading.
- Full standalone build and base frozen-sidecar smoke passed. Final frontend
  tests, TypeScript/Vite and NSIS rebuild passed after the focus refinement.
  The existing >500-kB bundle advisory remains.
- Extended frozen smoke passed for both generated OPEN RPF variants using the
  Enhanced installation only for read-only decoding. Root/nested dictionary
  edits were built, verified, wrapped into ALLIN1 ZIPs, and independently checked
  for exact manifest target/edition/dependency and every ZIP member hash. Original
  RPF SHA-256 remained unchanged; no development Python/.NET paths were needed.
- Computer-use browser validation at 1280x720, 900x800 and 720x800 confirmed
  readable settings, whole-archive warnings, manifest preview and confirmation;
  no horizontal overflow, and three aligned 74px editor headers at full width.
  The check led to explicit focus/scroll handoff when opening and returning to
  settings, plus the clearer **Back to export settings** label. A transient
  hot-reload warning from changing effect dependencies did not recur on a fresh
  page load. Temporary tabs and Vite were closed after validation.

Candidate only: the SDK installer was rebuilt, not installed. Actual GTA,
KRISS Vector and Suppressors Enhanced installations were not modified. Package
installation still uses the existing Launcher/OpenRPF contract; no installer
authority or runtime behavior was changed here. Native-dialog/clean-machine E2E,
large/encrypted production RPF coverage, member-level patch distribution,
multi-entry change sets and new DLC registration remain separate work.

Candidate installer SHA-256:
`fb7f5aad0d1616ea41df070dd4e7641603b9dbc60d3bc61cb515d77dc5e722d4`.
Release desktop SHA-256:
`dc150d0fdebd2998f8992ee1c3b113542d381862163a69bccc1354f9cc7a6a9e`.
Frozen sidecar SHA-256:
`cb55c05f4c6b0122a9a5a76b7f423dd0c5a22c6cf09b9a0a6c0135cef0a24748`.

## 2026-09-04 — exact RPF member identity prerequisite

Member-level distribution review uncovered unsafe suffix matching in the
SDK/Launcher native helper. A request for `text/global.gxt2` could select
`shadow/text/global.gxt2` if the exact target was missing, or become ambiguous
when both existed. Managed extraction also used the legacy basename-search
command for root entries. This pass fixes those prerequisites before exposing
member-only ZIP export.

Both native helpers now resolve each path segment from the selected archive's
directory tree. File/directory identity is independent of display paths; case
and slash normalization are supported, ambiguous siblings fail, and malformed
paths or implicit nested-RPF traversal are refused. SDK batch edits use the same
resolver. `extract-entry` keeps its legacy basename behavior for existing
inventory callers. Managed SDK/Launcher backups and verification instead call
the new `extract-exact-entry`; unavailable/failed helpers never trigger fallback.

Validation:

- 324 focused SDK Python tests passed, with three existing Windows
  symlink-privilege skips. These include 15 new SDK/Launcher exact-command,
  missing/error, stale-probe, missing-output and native-source parity cases.
- Launcher package tests: 80 passed, three existing symlink-privilege skips.
- React: 127 passed. Rust: 10 passed. No React layout or interaction changes
  were made in this pass, so no new visual QA is claimed.
- 51 native identity checks pass without GTA or keys. The desktop build now
  runs these before resource staging, covering suffix decoys, missing root and
  directory targets, case/separators, wrong types, duplicate siblings, malformed
  paths and no implicit nested traversal.
- Real generated OPEN RPFs passed extraction, replacement, addition, deletion,
  absent-target refusal and SDK batch checks with same-named root/folder/nested
  dictionaries. Both rebuilt SDK and Launcher helpers passed native Launcher
  install/disable/enable/uninstall restoration in owned temporary game trees.
  Only loader availability and the read-only game-key context were redirected;
  actual member writing, receipt handling and restoration used the real code.
- The expanded smoke also passed missing-root install/disable/enable/uninstall:
  `new.gxt2` was added/removed without backing up or touching the existing
  `shadow/new.gxt2` decoy. Final runs made 112 native calls through the SDK's
  bundled self-contained helper (including batch checks) and 104 through the
  rebuilt Launcher helper. All writes remained in owned temporary trees.
- Full standalone SDK build, base frozen-sidecar smoke and extended frozen
  root/nested dictionary intake/edit/build/RPF verification/ALLIN1 ZIP export
  passed. Original fixture RPF SHA-256 stayed unchanged. The existing frontend
  >500-kB bundle advisory remains.

Boundary: `publish_rpf` still emits whole-archive packages. Member-only export
needs an enforceable recipient compatibility check and explicit nested-member
policy; the helper fix must not be assumed present in old Launcher releases.
The Launcher source and matching helper were built/tested locally, not deployed
as a Launcher release. The rebuilt SDK installer is a candidate only, not
installed. Actual GTA, KRISS Vector and Suppressors Enhanced files were untouched.

Reproduction commands and native test scope are documented in
`tools/RpfPatcher.Tests/README.md`.

Candidate installer SHA-256:
`29a8e5f66274141b64a9cdd1818dfd5c574ca65b5b23a95f2c30e97453bac8ea`.
Release desktop SHA-256:
`a30bdf0efa4eaf2de1d5c1a0681c5daaebbd6f0bed9eea5fc8af997d16d4f127`.
Frozen sidecar SHA-256:
`278dcd6b395e27a5a1e169a5fc34467299f59e3de47998f56e94098fcce84326`.
Bundled native helper SHA-256:
`33183865209a9d438dcf198632364b03133fe5bf439d7833ce81dcf5f2dd5528`.

## Nested RPF dictionary distribution — 2026-09-04

Tauri now exports a saved nested dictionary as a schema-4 member-only ZIP. Its
manifest and review preserve the explicit archive chain, required original SHA-256,
replacement hash and recipient compatibility acknowledgment. Whole-archive schema 1
and outer-member schema 3 remain separate choices. No containing archive is shipped.

The shared SDK/Launcher contract and native implementation are mirrored. Nested
install/enable/disable/uninstall use exact chain identity and leaf caches. The helper
holds the original read-only during staging, rebuilds detached children bottom-up,
checks all bounded file payloads, then commits the verified outer copy. Restore
preserves later unrelated edits. Parent-RPF and whole-archive ownership conflicts
are rejected in both directions. See [schema-4 limits and semantics](rpf-member-package-v4.md);
large/encrypted production certification, crash-journal/resume support and a compatible
signed Launcher release are not implied.

Validation completed:

- 627 SDK-side Python tests passed, 3 skips, across package contracts/publication,
  GXT2, transaction, change-set, native-identity and desktop-protocol suites.
- 103 Launcher tests passed, 3 platform-related skips. Legacy schema-1 member
  install/restore tests remain green; the unsupported-version test now uses schema 5.
- 182 React tests passed; all 13 publication tests were rerun after review-focus
  refinement. TypeScript, production Vite, 10 Rust tests and 66 native identity
  checks passed. The existing large frontend chunk advisory remains.
- The final bundled native helper passed an isolated two-layer fixture test:
  same-name decoys at multiple depths, replacement/restore, later neighbour edits,
  wrong current/payload hashes, missing parent, existing lock, idempotence and a
  staging-name collision case. Detached archives retain their basenames in unique
  per-depth folders.
- Full frozen-sidecar smoke passed with the final staged resources: root/nested
  GXT2 intake/edit/copy build, schema-1/3/4 publication, native Launcher
  install/disable/enable/uninstall, original-checksum refusal, multi-entry plan/
  execute/verify/rollback, history, receipt reconciliation, matching stale-lock
  cleanup on external and temporary mods copies, dual confirmation and stock refusal.
  Real GTA supplied read-only decoding context; all archive writes were isolated.

Computer-use QA used the read-only development fixture at 1280×720 and 720×800,
light and dark. Paths/hashes wrap without horizontal overflow; the three editor
headers share a 74-pixel height and top position. The UI pass prompted a refinement:
settings/reviews scroll their focused heading to the top. Back restores focus;
the export button remains disabled until explicit schema-4 consent. No export was
submitted through browser preview, and console warning/error logs were empty.

The final native publish used `build/tauri-rpf-schema4` because an unrelated
weapon-pack build was using `build/tauri-rpf`. That build was left running.
Resources were fully staged before the final installer build and native/frozen
checks; no resources were restaged while those tests ran. All 266 staged resource
hashes matched. Final validation notes and parity-table wording were updated in
source after packaging; the packaged native contract document is included.

Candidate built, **not installed**. No installed SDK, Launcher, real GTA, KRISS or
Suppressors runtime files were changed by this migration turn.

- NSIS installer: 68,626,023 bytes; SHA-256
  `de94af43158abfd14d21fdeadb65777026ba3779e70d4c610d1fa5d043626088`.
- Unbundled release shell SHA-256:
  `79bfe1c56d6c948504a362d9a6f422ca24eb464209e987a147fe3e7ff227f78a`.
- Frozen sidecar SHA-256:
  `aa169f340706b5ddf97d5f75947a633518193e1416c31f158affdc18068255a3`.
- Bundled native helper DLL SHA-256:
  `39acd1c787231987c12746cc06d4e300739881d921e5d299c04a4d3af13c2afc`.

Next bounded migration target: expose existing `RpfPackageGraph.describe`/
validation through a desktop facade and build a React node/inventory/evidence
view, followed by reviewed graph authoring and the program-plan editor.


## 2026-09-04 — exact-member ALLIN1 ZIP export

The GXT2 export flow now distinguishes whole-archive schema-1 packages from
schema-3 exact-member replacements. Member mode ships only the selected outer
archive dictionary, with an explicit archive destination, edition/OpenRPF lock,
replacement hash and required original-member hash. The scope selector,
compatibility warning, target/member/checksum review and confirmation are bound
to the same saved workspace and verified RPF build. Nested-member publication is
explicitly unavailable rather than silently exporting an entire archive.

The dependency-free contract remains byte-identical in SDK and Launcher.
Schema 3 is strict, replacement-only and rejects nested targets, unknown fields,
loose files, DLC registration, missing hashes and mixed editions. Both installers
preflight all originals before preparing game writes and check captured backups
again before replacing a member. Receipts retain original/payload hashes; cache
validation prevents corrupted backups or applied payloads from being reused by
disable, re-enable or restore. Old schema readers reject these packages outright.
No compatible Launcher release is implied by these local source changes.

Validation completed:

- Focused SDK Python suites: 412 passed, three existing Windows symlink-privilege
  skips, covering protocol, standalone behavior, GXT2 authoring/build/publication,
  package contracts, exact native commands and both sibling package readers.
- Launcher package/contract suites: 103 passed, three existing symlink skips.
- 52 new contract tests include a saved pre-schema-3 reader, unchanged-original
  requirements, all-member preflight, helper refusal, backup race recheck,
  install/disable/enable/uninstall with controlled I/O, and corrupted-cache refusal.
  Publication tests independently open real ZIPs and verify the exact dictionary,
  manifest, portable evidence, stale review/scope rejection and output guards.
- All 134 React tests and 10 Rust tests passed. Seven new React cases cover member
  export, nested refusal, scope-change confirmation and malformed review evidence.
- Full standalone build, base frozen-sidecar smoke and final frontend/NSIS rebuild
  passed. The existing >500-kB Vite bundle advisory remains. Native helper build
  checks passed (51 game-independent exact-identity cases).
- Computer-use browser QA passed at 1280x720, 900x800 and 720x800: scope/target
  controls, original checksum wrapping, generated manifest, confirmation and
  return-to-settings focus were readable with no page horizontal overflow or
  browser console warnings/errors. This pass changed tiny-package size display
  from `0.0 MiB` to an exact byte count under **Unpacked ZIP size**. The temporary
  viewport was reset, preview closed and Vite stopped.
- The first extended frozen/native smoke correctly refused archive authoring
  because GTA Enhanced was running. The game was not terminated or the guard
  bypassed. After a read-only process check found it closed, the full extended
  smoke passed: frozen-sidecar root/nested intake, edits, verified RPF copy builds,
  whole-archive ZIPs, root-member schema-3 ZIP and explicit nested-member refusal.
  The actual exported member ZIP then passed native Launcher service install,
  disable, enable and uninstall in a generated fake-game tree, restoring the
  original dictionary and preserving the unrelated nested dictionary at each
  step. A changed original was rejected before replacement. Only loader
  availability and read-only key context were redirected; package loading,
  receipt lifecycle and native member operations used real implementations.
  Original fixture RPF SHA-256 stayed unchanged and the full sidecar smoke exited
  successfully. Real GTA files were not install targets.

The candidate SDK installer was rebuilt, not installed. No actual GTA, KRISS
Vector or Suppressors Enhanced installation was changed. The test harness limits
native writes to owned generated archives and a temporary fake-game tree, using
real GTA only as read-only decoding context. This is not clean-machine or
native-dialog install certification, nor full RPF-category parity. General
change sets/rollback UI, nested distribution and graph/program editors remain.

Candidate installer SHA-256:
`f22476e7108d723ef6eea557863d9150d806920c33b9a2fbd0d6576b57137baa`.
Release desktop SHA-256:
`528ef0c9b308d77548c83d1d82a5cd473801f01daac1bd32a68b30702e45c653`.
Frozen sidecar SHA-256:
`cb1b7e859145126a6f45ba226ab574deed414b9d5dd7a9d49625883f59f4f436`.
Bundled native helper SHA-256:
`33183865209a9d438dcf198632364b03133fe5bf439d7833ce81dcf5f2dd5528`.

## 2026-09-04 — RPF change-set editor and compiled-plan export

The third RPF tab now creates/opens existing Python change-set documents, stages
add/replace/delete/rename/mkdir/rmdir, reviews reordering/removal, and exports a
native-verified atomic JSON plan. Archive inspection can hand off an exact root
or nested member. The aligned action/draft/evidence panels retain source hashes,
payload hashes, order and draft guards. Every document write requires a separate
review and confirmation. Compile shows ready/blocked status and explicit scope;
it does not execute a transaction or grant implicit write authority.

Validation completed:

- Python regression suites: **452 passed, one skipped**, covering the change-set
  facade/shared domain, RPF tools/graph/program domains, GXT2, archive/package
  publication, schema-3 contracts, desktop protocol and standalone behavior.
  New checks cover all action types, digest/target/payload drift, same-size archive
  drift immediately before creation, final commit/compile races, output refusal,
  bounded documents/actions/files, explicit scope, missing-payload removal and
  authoring operations excluded from cancellable jobs.
- React: **153 passed**, including 19 change-set cases. Exact-member handoff,
  create/stage/reorder/remove/compile confirmation, affected-member review,
  keyboard focus, early/cancelled terminal delivery, duplicate saves, malformed
  evidence, case-normalized native targets and guarded tabs/navigation passed.
- Rust: **10 passed**, including dedicated apply/save-picker permission coverage.
  Full standalone build, 51 game-independent native helper checks, base frozen
  smoke, final TypeScript/Vite compilation and optimized Tauri/NSIS rebuild passed.
  A test-only unsupported `exact` query option was removed after the TypeScript
  build caught it; the 19 focused tests and final build were rerun successfully.
  The existing Vite >500-kB main-chunk advisory remains.
- Computer-use browser QA at 1280x720, 900x800 and 720x800 checked layout and
  review readability. The three desktop panel headers shared the same top and
  74-pixel height; narrower windows reflowed to two/one columns without page
  horizontal overflow. Checksums, selected-member details, plan JSON and explicit
  confirmation remained readable. Closing a review restored workspace-heading
  focus. Browser console warnings/errors were empty. This pass added affected-row
  summaries and return focus, and clarified mods-copy scope text. The temporary
  viewport was reset, preview closed, and Vite stopped.
- Extended packaged/native smoke passed after a read-only check found GTA closed.
  The frozen sidecar inspected/staged/reordered/removed actions on owned fixtures,
  then created a change set against a generated OPEN RPF, staged root/nested
  replacements plus a directory, and exported one ready native plan containing
  all three changes and original-member hashes. Original RPF SHA-256 stayed
  unchanged. The existing root/nested GXT2 copy-build/publication and schema-3
  native install/disable/enable/uninstall smoke also passed, including mismatched
  original refusal and unrelated nested-dictionary preservation. Native writes
  were confined to generated archives and the temporary fake-game tree; real
  GTA supplied only read-only decoding context.
- CI now includes the shared change-set and desktop-facade Python suites.

This completes staging and plan export, not the full RPF category. General
reviewed archive execution, transaction receipts/rollback UI, graph/program
editors, nested-member distribution and production/native-dialog/clean-machine
validation remain. Existing Tkinter/CLI transaction behavior is retained.

The unsigned candidate installer was rebuilt, not installed. No actual GTA,
Launcher, KRISS Vector or Suppressors Enhanced installation was changed.

Candidate installer SHA-256:
`280cdc6b4993b6c1133f03b329a7c4cabd832beee6e24bff9254ec4a1b99124d`.
Release desktop SHA-256:
`2a72571e5c735d2fe57cd0347a6996c53459b7829b6fd7ca3341303f4967b386`.
Frozen sidecar SHA-256:
`80488663799355e903410107c77852b144918b3e35002a49ef5286e44d925fc9`.
Bundled native helper SHA-256:
`33183865209a9d438dcf198632364b03133fe5bf439d7833ce81dcf5f2dd5528`.

## 2026-09-04 — external RPF execution, receipts and rollback

The new **Execute & restore** tab completes the external-authoring transaction
loop: open a compiled multi-entry plan, explicitly select its workspace folder,
review and confirm execution, inspect native receipt/member/backup verification,
and separately review/confirm exact rollback. Existing Python transactions own
all archive operations. Stock GTA and mods-directory targets remain blocked by
the desktop facade; no game-write process authority was enabled.

Execution re-indexes and recomputes the plan from current archive/payload evidence.
Source documents are SHA-bound at the shared domain entry point and rechecked at
commit. The shared multi-change apply and rollback paths now recheck the archive
immediately before replacement. A rollback failure before commit no longer
overwrites an external edit with its recovery copy. Retained receipts/backups use
the fixed per-user SDK transaction root. The UI never retries writes or removes
stale locks automatically, and invalidates the archive index after a write attempt.

Validation:

- **480 Python tests passed, one existing skip**, across transaction/change-set,
  RPF tools/graph/program, GXT2, archive/package publication, schema-3 contracts,
  desktop protocol and standalone behavior. The 28 transaction tests exercise
  real shared Python transactions with controlled native archive I/O, root/nested
  changes, exact rollback, separate confirmation, scope/type/size limits, changed
  inputs, staging-time archive/document drift, occupied locks, insufficient space,
  open-game refusal, missing/corrupt backups and redirected backup declarations.
- **165 React tests passed**, including 12 transaction tests covering execution,
  receipt reopening, rollback, independent confirmation, stale failure, malformed
  review/outcome evidence, picker/read cancellation, duplicate-submission prevention,
  review/return focus and RPF-tab/workspace guards.
- **10 Rust tests passed**, including dedicated transaction-command capability
  coverage. TypeScript/Vite, the full standalone build, 51 game-independent native
  helper checks and base frozen-sidecar smoke passed. The final frontend/NSIS
  rebuild includes the readability refinements. The existing >500-kB Vite main
  chunk advisory remains.
- Computer-use browser QA checked 1280x720, 900x800 and 720x800. Desktop headers
  aligned at the same top and 74-pixel height; narrow layouts reflowed without
  page overflow. QA found long JSON hashes bleeding into the neighboring pane;
  evidence now wraps within its own column, verified by equal client/scroll widths.
  Review actions have readable labels and rollback explicitly identifies the
  original transaction changes being undone. Current/original hashes, full-backup
  location and confirmation remain readable. A clean final preview had no console
  warnings/errors; an earlier transient HMR error occurred between two live-edit
  patches and was absent after loading the finished code. Return focus passed.
  Temporary previews were closed, viewport reset and Vite stopped.
- Extended frozen/native smoke passed with real GTA used only as read-only decoding
  context. The generated OPEN RPF received root/nested dictionary replacements and
  directory creation through the new desktop transaction facade. The resulting
  receipt, full backup and current archive hashes verified. Missing confirmation
  was refused. A simulated external edit was refused by rollback without modifying
  it. After restoring the test fixture's applied bytes, a fresh confirmed rollback
  restored the original RPF SHA-256 exactly. All transaction backups stayed inside
  the harness's temporary per-user directory. Existing root/nested GXT2 builds,
  whole/member ZIP publication and native schema-3 install/disable/enable/uninstall
  tests also passed, preserving the unrelated nested dictionary.
- CI now includes the desktop transaction suite alongside change-set tests.

The candidate installer was rebuilt, not installed. No actual GTA, Launcher,
KRISS Vector or Suppressors Enhanced installation was changed. Full RPF parity
still needs live-game archive authority, transaction-history browsing and advanced
interrupted-transaction recovery, graph/program editors, nested-member distribution,
large/encrypted production-archive validation and native-dialog/clean-machine E2E.

Candidate installer SHA-256:
`596f27f80ca612d6e9777bc715e79b2e622dcaffc15e9a53103ff334275efd90`.
Release desktop SHA-256:
`af44f9bdcbc133aaaf571cd62b81b4d15ed3a16a184069c0bedf9e8f1b8a5cbd`.
Frozen sidecar SHA-256:
`27faa6542a2dcdc45080d86964513329c14542132deb28c995000369e406e162`.
Bundled native helper SHA-256:
`33183865209a9d438dcf198632364b03133fe5bf439d7833ce81dcf5f2dd5528`.

## 2026-09-04 — guarded mods writes, retained history and receipt reconciliation

The transaction tab now accepts compiled multi-entry plans for existing archives
inside the explicitly selected GTA installation's `mods/` directory, as well as
external authoring copies. Stock archives, another installation's targets and
redirected paths remain blocked. The native owner enables only the dedicated
`--allow-rpf-writes` capability; general console game writes remain disabled.
Live execution and rollback require both archive and game-write confirmations,
fresh review evidence, a closed game and the existing full-backup/staged-commit
transaction. Scope and document checks run again immediately before replacement.

Retained history has bounded scans and reports malformed receipts and truncation.
Interrupted receipts can be reviewed and reconciled to verified applied/original
state with a metadata-only confirmation. Archive, backup, receipt and lock drift
invalidate the review. Active owners block recovery; stale locks are reported and
retained, not silently removed. There is no automatic replay or archive repair.

Validation completed:

- 503 Python checks passed, 1 skipped across desktop transaction/change-set,
  shared RPF/graph/program, GXT2, package publication, protocol and standalone suites.
  Transaction tests include mods execute/rollback, separate native authority,
  missing/string confirmations, cross-game and changed-marker refusals, final
  commit scope changes, receipt recovery in both archive states, stale inputs,
  active/stale locks and bounded/malformed history.
- 170 React tests passed; live execution requires an explicitly chosen game and
  two fresh checkboxes. Failure clears both. Metadata recovery does not submit
  archive/game confirmation or invalidate the archive index. History opens a
  separately verified receipt. Existing cancellation/navigation guards still pass.
- 10 Rust broker tests and 51 game-independent native helper checks passed.
  TypeScript/Vite and optimized native builds passed. The first installer attempt
  caught a test-fixture overload typing issue; it was corrected before the final build.
- Frozen-sidecar base and extended smoke passed. The extended run builds generated
  OPEN RPFs, edits exact root/nested dictionaries, publishes whole/member packages,
  and checks native install/disable/enable/uninstall on fixtures. It then exercises
  mods-scope execute, dual confirmation, history, interrupted-receipt recovery,
  stock refusal and exact rollback through the frozen protocol in a temporary game.
  Only a local GTA executable copy is used for decoding keys; it is never launched.
  The actual GTA installation is not a write target.
- Visual checks in the computer-use workflow covered light/dark themes at 1280×720,
  900×800 and 720×800. Full-width panel headers have identical top coordinates and
  74-pixel heights; code evidence and the page have no horizontal overflow.
  History heading alignment and metadata-only recovery wording were refined.
  Both preview pages reported no browser warnings/errors. Preview tabs were closed
  and viewport/theme settings restored after the check.

Remaining RPF work includes reviewed stale-lock cleanup, advanced crash recovery,
node graph/program editors, nested-member distribution, large/encrypted production
archive coverage and native-dialog/clean-machine certification. This is not a claim
of full RPF parity or a production game installation test.

### Local installation verification

The user-authorized current-user NSIS upgrade completed with exit code 0.
The installed sidecar passed the packaged smoke suite from its installed resource
folder. Its hash and the native helper hash match the verified build. The installed
shell matches the release executable byte-for-byte except for Tauri's expected
three-byte `__TAURI_BUNDLE_TYPE_VAR_UNK` → `..._NSS` installer marker; that exact
normalization was checked, not assumed.

The installed shell was started and its native sidecar processes were observed with
`--allow-rpf-writes` and without `--allow-game-writes`. The nominal installation is
`%LOCALAPPDATA%\ALLIN1 SDK`; this Codex-hosted run resolves it through
the host's MSIX `LocalCache\Local\ALLIN1 SDK` directory. GTA Enhanced reopened during
the final native-window check. No game writes were attempted, and native visual
interaction was not treated as certified. The earlier browser visual checks and
frozen native transaction tests remain the functional evidence. No Launcher,
KRISS Vector or Suppressors Enhanced files were changed in the real game.

Installed installer SHA-256:
`c75e20d78ec7f15a6f58d766b0c85f32855959a0ccdaac31922d78aa2a5aac2e`.
Installed NSIS-marked desktop SHA-256:
`b342f168cbd126441253a69268db57c031b617d5d8eb2f9e6e3afcbb47dcb4f4`.
Unbundled release desktop SHA-256:
`32fe44e3f9f6a7bfbcb0d3a871d6614c69e687da88efedc7c88879daae455cee`.
Installed sidecar SHA-256:
`3f44ef58d529265fc7775c1d33a4469e6e0e228ab02c121a9737d95bd3244f97`.
Installed native helper SHA-256:
`33183865209a9d438dcf198632364b03133fe5bf439d7833ce81dcf5f2dd5528`.

## Help Center contained scrolling — 2026-09-04

The Help Center now fills the available workspace height instead of expanding the
page with its topic list. Search remains outside the topic scrollbox; long articles
scroll independently. Filtering resets the list to its top, while selecting another
topic resets only the article. The article is keyboard-focusable. Below 720 px the
two panes stack within the same bounded workspace, including when the console opens.

Validation: all 171 React tests passed, followed by a focused rerun of the help test
with Tab-navigation assertions. TypeScript and the production Vite build passed
(the existing large-chunk advisory remains). Browser UI checks used the explicit
`?preview=help` development fixture with 20 topics and an 18-section article:
1280×720, 900×800 with the sidebar expanded, and 720×800 with console closed/open.
Both panes scroll without moving the heading/search or each other; measured main
workspace vertical and horizontal overflow was zero. Light and dark layouts were
visually checked. No native installer was rebuilt or installed for this UI-only
pass, and no backend, game, package, or RPF lock files were changed.

## Reviewed stale-lock cleanup — 2026-09-04

Execute & restore now exposes a separately reviewed `clear_lock` action. It requires
a matching, exited owner and a settled receipt whose archive and backup verify.
The Windows implementation retains the exact bounded lock bytes beside the receipt,
then deletes the same exclusively held file. A different/replaced file, active or
unverifiable owner, changed transaction, hard link, unsupported volume or missing
confirmation fails closed. Retained evidence is never overwritten; an exact copy
left after a failed removal can be reused after a fresh review. No archive, receipt
or backup is written by cleanup. Mods-folder lock removal still needs native RPF
authority and its own game-write confirmation, and all cleanup requires GTA closed.

Source validation passed: 483 Python tests (one existing platform-specific skip)
across transaction/domain/protocol/change-set/member-publication/GXT2 suites;
182 React tests; 10 Rust tests; 51 native exact-member checks. The 28 transaction
React tests were rerun after the final control-placement and focus refinement.
Python regression cases include replacement/mutation attempts while the lock handle
is held, identical-byte file replacement, post-retention archive/receipt/backup/game/
owner drift, deletion failure with both copies retained, and native-authority refusal.

Browser UI checks used the read-only `transactions-lock` preview at 1280×720 and
720×800, light and dark. Cleanup and receipt recovery sit in the Review operation
column; all three visible panel headers measured 74 px at the same top position.
Reviews scroll to and focus their heading, hashes/paths wrap without horizontal
overflow, consent gates the button, and Back returns focus. Browser logs were clear.
No cleanup was submitted through the preview. General orphan-lock/missing-archive
recovery, nested-member distribution and clean-machine/native-dialog certification
remain separate; this is not full RPF parity.

### Packaged verification and candidate

The final NSIS build passed. All 265 staged resource hashes matched their manifest.
The extended frozen-sidecar smoke then passed sequentially against that unchanged
resource home: root/nested text edit and RPF build, schema-1/3 publication, temporary
Launcher install/disable/enable/uninstall, original-checksum refusal, multi-entry
execution, history, receipt reconciliation, stale-lock cleanup on both external and
temporary mods copies, exact rollback and stock refusal. Cleanup rejected a live
owner first, then retained exact bytes for an exited test process. Archive, receipt
and backup byte comparisons were unchanged after each cleanup.

An earlier smoke run was discarded after resource restaging collided with a loaded
native helper. That run was stopped; the generated resource tree was fully restaged,
the installer rebuilt, all hashes rechecked, and the complete smoke rerun passed.
Do not restage resources while a frozen smoke test is running against them.

The candidate includes the Help Center scrolling refinement. It was not installed;
the installed SDK and real GTA/Launcher/KRISS/Suppressors files were not changed.

Candidate installer SHA-256:
`abbaa9260cd2aeae41d65d19b592b8acc2cd57d580251f5509e611c2ac427632`.
Unbundled release shell SHA-256:
`d6e07eda01ddfbd2e168be5364f4693cf5b586aaf9e01316cb2fae1be23fb2b3`.
Frozen sidecar SHA-256:
`872c8b162eef358d2a4880a12a42e7b1c89424d24e9b87a821245f1f9613758c`.
Native helper SHA-256 (unchanged):
`33183865209a9d438dcf198632364b03133fe5bf439d7833ce81dcf5f2dd5528`.

## 0.6.4 release-candidate qualification

Candidate sealing now owns the validation run instead of accepting copied smoke
summaries. Each required Python, React, Rust, native-RPF and frontend-build gate
is executed after the candidate identity is created. Its exact command,
executable identity, complete log hash, result, timestamps, build ID and source-
tree digest are retained. A complete PASS set is mandatory, and the React gate
rejects reported skips even when the test runner exits zero. Package integrity,
automated tests and live acceptance remain separate results in the final receipt.

The parity inventory is machine checked through
`desktop/module-happy-paths.json`: every one of the 26 React modules names a real
happy-path test. Native runtime compilation, Blender rendering, real RPF layout
and native recipe generation are enabled during candidate qualification rather
than skipped. The local no-skip baseline is 22 files and 217 tests.

RPF Explorer now exposes reviewed exact-member extraction, editable native-
workspace export, subtree/archive extraction, metadata/logical/exact comparison,
integrity reporting and copy-only defragmentation. Every action validates source,
destination and game-path containment before writes, binds review evidence to
the source/index state, requires a fresh action-time confirmation, and cleans up
only its exact newly-created output if an operation fails. Frozen-sidecar smoke
uses a disposable generated game context and never discovers or writes a real GTA
installation.

The NSIS installer and deterministic portable ZIP are sealed from the same exact
payload inventory. A production signing identity/public key has not been supplied,
so React update installation remains deliberately disabled. Checksums or a valid
executable header alone are not treated as authorization to install an update.
Clean-machine install/upgrade/repair/uninstall/rollback, Authenticode/updater
signatures and live-game acceptance remain separate release gates.
