# React archive and authoring completion

Active implementation goal, requested September 5, 2026. Baseline: `0265767`.
The retained Tk 0.6.3 source is the historical workflow reference. A backend
command alone does not qualify as a completed React workflow.

Current user-directed priorities are tracked in `sdk-three-goals.md`: trustworthy
asset validation, reversible optimization and build-to-game diagnostics. The
broader parity ledger below is retained, not claimed complete; resume feature
expansion only where it contributes to those priorities.

## Completion ledger

- [ ] Restore a green complete CI run and current release evidence.
- [ ] Remove retired Tk fallback instructions and reconcile the parity ledger.
- [ ] Unified game/folder/RPF browser with exact member identity, explicit
  Legacy/Enhanced context, stock/mods locations, address navigation, history,
  recent locations and favorites.
- [ ] Global filename and audio search with paging, cancellation, visible
  coverage limits and direct result handoffs.
- [ ] Contextual multi-selection extraction and reviewed add/replace/delete/
  rename/create-directory/create-archive actions using existing transactions.
- [ ] Generic native resource workspace export, edit, verified build and RPF
  replacement handoff, including matching-installation decryption context.
- [ ] AWC stream inventory, playback, WAV export and verified rebuild workflow.
- [x] Texture rename, properties/mipmap controls, selected/all export and
  bulk operations with direct archive handoff.
- [ ] YMAP/YTYP placement and dependency views, YNV/YND navigation views,
  supported animation playback, fuller collision primitives/normals and REL views.
- [x] Versioned format capability matrix: inspection, preview, export, editing,
  rebuild and evidence by edition; unsupported variants remain visible.
- [ ] Supported OIV 2.2 operation coverage, content/destination selection,
  logs, recovery and Launcher loader-install/repair handoff.
- [ ] Large/encrypted archive corpus, interrupted-write/missing-backup recovery,
  native dialogs, clean-Windows lifecycle, independent Legacy/Enhanced acceptance.

## Evidence policy

### JSON source editing — immediate workflow addition (September 6)

Data Tools → XML, JSON & Lua editor now opens UTF-8 `.json` files and
creates new JSON documents. CodeMirror provides JSON highlighting, line
numbers, undo/redo and find/replace. Native file dialogs include JSON.
The existing review-bound save/copy workflow retains exact source bytes,
UTF-8 BOM and line endings, creates recovery backups for replacements,
rejects stale source revisions and prevents direct game-directory writes.
Malformed JSON opens for repair; invalid drafts cannot be saved. Validation
rejects comments, trailing commas, NaN/Infinity, duplicate object keys and
nesting over 128 levels. Syntax checks are not JSON Schema or game/mod-schema
certification. The existing 64 KiB / 2,000-line source limits still apply.
This is loose-source authoring: archive members must be extracted first;
it does not claim an integrated JSON-to-RPF replacement handoff.

Verification: 71 code-workspace Python tests passed; 46 protocol/console/
module tests passed; six real React-to-Python source-editor integration tests
passed (including JSON repair, invalid-save rejection, exact large-number
preservation, recovery backup and new-document save-copy). Production React
build and diff whitespace checks passed. Native file-dialog runtime acceptance
and a packaged release remain separate qualification work.

### Dense workspace UI convention

When a workspace accumulates feature navigation/options, use a secondary
collapsible panel with its own bounded scrollbar instead of adding more rows
to the page. Reuse `WorkspaceToolPanel` for tool navigation. Keep the toggle
outside the scrolling region, remember collapsed state, label controls for
keyboard/screen-reader access, and stack the panel above content at narrow
widths. Keep active editors mounted and preserve all dirty/review guards.
The RPF tools use this pattern; future dense workspaces should follow it.

### Verification

Record implementation and passing tests for each slice. Distinguish generated
fixtures, native real-resource tests, packaged desktop observations and actual
game acceptance. Keep unfinished checks unchecked. Release publication and
runtime support claims require their own verified evidence.

## Progress

- Updated the SDK working baseline from `31d1201` to `0265767` by fast-forward.
- Corrected the console autocomplete regression to include the new headless
  authoring command while retaining the existing full-prefix expectation.
- Added the React game/folder/nested-RPF browser and typed cancellable jobs:
  exact entry IDs, explicit decoder/edition, parent/history/address navigation,
  persisted favorites/recent locations, paged filename search, source/mods scope,
  and visible coverage bounds. Read-only backend tests and three React behavior
  tests pass. Full-game indexing performance and audio-content search remain open.
- Added the generic native React workspace on the shared digest-bound review
  protocol. Source export, XML editing (currently the shared 64 KiB editor limit),
  recovery backups, exact dependency export and reparse-validated builds are
  connected. Larger XML remains externally editable without silent truncation.
  Archive provenance is retained. Exact-member replacement planning now rebuilds
  and reparses the workspace, binds the original archive hash, and hands the inert
  plan to Execute & restore. External archive scope requires explicit selection.
- Added bounded PCM WAV playback from exported native dependencies, individual
  channel selection, normalization into the broker-owned preview cache and shared
  cache pruning. Unsupported codecs/oversize files remain exportable, not claimed
  playable. No new arbitrary filesystem or network media scope is granted.
- Targeted verification: 93 Python tests passed / 2 environment skips before the
  additional audio tests; native/audio adapter now has 13 passing tests.
  Three browser React tests and three native React tests pass. App plus retained
  offline workflow integration: 61 passed / 4 explicit native skips. Production
  React build passes. These are source/test results, not packaged or game acceptance.
- Full React suite: 298 passed / 4 gated native skips. Full Python run: 2548
  passed / 8 skips, with one discovery-ledger failure introduced by the new native
  module. Added its catalog/route registration and matching evidence inventory;
  the updated discovery tests are being rerun.
- Real RpfPatcher smoke passed with the owned tetrahedron fixture for both Legacy
  and Enhanced: loose export/build/reparse, exact archive intake, reviewed XML
  save and replacement planning. Source resource and archive hashes were preserved.
  Separate retained native archive/binary/graph smoke also passed. Synthetic decoder
  directories select editions; these checks do not prove encrypted retail resources
  or live game acceptance.
- Browser multi-selection now exports up to 128 files / 512 MiB from one RPF per
  reviewed output. Layer-separated paths and an exact-entry/hash manifest prevent
  duplicate basenames from overwriting each other. Staging refuses raced user
  destinations. Added invalid-selection and collision/race tests.
- Removed dead Tk fallback UI and replaced the weapon oversized-review fallback
  with a supported instruction to narrow the operation.
- RPF secondary tool navigation is a reusable collapsed-state-persisting panel
  with independent bounded scrolling, keyboard navigation and intact editor
  guards. Browser preview verified expanded/collapsed; 45 targeted React tests pass.
- Added reviewed selected/all texture export to fresh outside-workspace/game
  folders: exact DDS or top-mip PNG, bounded batches, staged publication, full
  hash receipts and no cleanup of raced user destinations. The React controls
  use a collapsible section and independently scrolling selection list. Browser
  preview verified the loaded panel, confirmation and cancellation (fixtures only).
- Added reviewed texture rename and RGBA8/DXT1/DXT3/DXT5 conversion with explicit
  mip counts/full-chain regeneration and undo. Lossy compression, DXT1 alpha loss
  and box-filter/normal-vector limitations are shown before apply. Required Pillow
  minimum is now 11.2.1, the first release supporting compressed DDS encoding
  ([upstream release note](https://pillow.readthedocs.io/en/stable/releasenotes/11.2.1.html)).
- Real native qualification exposed missing/truncated DDS data despite matching
  XML. Fixed zero-sized non-square mip dimensions and Legacy quarter-size mip
  reads in an SDK-owned MSBuild overlay, leaving the pinned CodeWalker checkout
  clean. The overlay fails closed if its expected upstream source changes.
  Native YTD build now verifies every texture's metadata and encoded pixel/mip
  payload before publication; dictionary order and DDS filenames are treated as
  storage details, not game-visible texture identity. Receipts expose exact
  texture-payload validation. `runtools.ps1` now fails if publish/clean fails.
- Evidence: full Python run before the final conversion/native changes passed
  2574 tests / 9 environment skips; full React at that point passed 304 / 4 skips;
  Rust 19 passed. Latest targeted Python 166 passed / 3 native skips, latest
  texture React 5 passed, production build passed. Native tests with the actual
  rebuilt converter passed 22 tests: both editions, all four encodings, five-mip
  non-square fixtures, plus retained archive/native workspaces. The native C# gate
  passed 81 checks, including eight new payload-preserving YTD/DDS round trips.
  These remain generated-fixture/source-build results, not encrypted-retail or
  in-game acceptance. Bulk texture mutation, direct YTD archive handoff and the
  remaining format/relationship/package/lifecycle roadmap are still incomplete.
- Final texture pass: 47 React shell/texture tests passed; 20 texture conversion
  tests passed with native gates enabled, including renamed dictionary entries
  whose dependency filenames/order change on export in both editions. No source
  YTD was altered. Production build passes after payload receipt enforcement.
- Closed direct archive/editor handoffs: indexed native members open with the
  exact outer archive, nested member identity and decoder context. YTD workspaces
  hand the same exported copy to the texture editor, retain archive provenance,
  guard unfinished edits, and refresh native inventory/hashes before replacement
  planning. Two actual-converter tests pass for Legacy and Enhanced after texture
  rename plus five-mip DXT5 conversion; the original archive remains unchanged.
- Browser members can stage a change-set draft without first indexing that archive
  in the inspector. Captured archive/layer/member/decoder context takes precedence
  over unrelated inspector state. Compiled plans hand directly to Execute & restore,
  which still requires its own fresh review and confirmation.
- Archive utility pickers now guard navigation immediately, report picker failures,
  ignore cancelled/duplicate terminal responses and cancel late job acknowledgements.
  Review and completion evidence must match the requested archive/output/action and
  no-source/no-game-write contract. Seven utility React tests pass.
- Full Python suite after these handoffs: 2592 passed / 13 environment skips.
  Targeted App/change-set tests: 64 passed; native/change-set/transaction tests:
  53 passed. Production React build passes. These do not establish packaged
  installation, encrypted-retail corpus or in-game acceptance.
- Full React suite after utility guard/receipt hardening: 316 passed / 4 native
  skips across 36 files. Remaining utility qualification includes owned-output
  staging/race cleanup for the older single-output actions; selection exports
  already stage before publication. The overall parity goal remains active.
- Closed the older desktop utility output-cleanup gap: all eight actions now
  generate in private same-parent staging, verify source stability, then publish
  new-only outputs. Public paths are never deleted during rollback. Partial
  companion publication reports retained paths explicitly. Windows file publication
  uses non-replacing rename and works without hard-link-capable disks. Defrag
  receipts name the public output; multi-dot comparison names remain intact.
  Tests cover raced user destinations on success/failure for every older action,
  late companion collisions, and actual native output usability for all eight
  actions in both editions. Actual converter suite: 38 passed; generated resources
  and synthetic decoder contexts only, not encrypted retail/game acceptance.
- Added the versioned, searchable 23-format React capability matrix, accessible
  from the RPF tool panel. It lists inspection, preview, export, edit/rebuild,
  variant limits, implementation references and separate edition evidence.
  Generic native routing derives from the same catalog; a Python drift test
  requires exact coverage of the backend extension sets. Explicit unsupported
  animation/Scaleform/REL views and pending retail evidence stay visible.
  Browser preview verified full table and both panel widths; filtering/edition
  selection are covered by React tests. Skill: computer-use browser inspection.
  Targeted Python: 133 passed / 3 native skips. Targeted React: 53 passed.
  Production build passes. This closes the matrix deliverable, not the unfinished
  capabilities or acceptance requirements it documents.
- Completed React bulk texture edits: selected conversion/removal and flat-folder
  DDS/raster import with explicit replace-only, add-only or combined policy.
  Filename-stem mapping, collision rejection, exact source hashes, dimensions,
  format and mip changes are reviewed before confirmation. Batches have one
  undo point and stay within 128 textures / 2 GiB guarded staging bounds with
  free-space checks. Unsupported codecs and normal/color-space limitations remain
  visible; this is not a claim of unrestricted DDS variant support.
- Batches prepare in a private workspace, then commit managed XML/DDS changes
  against hashes with grouped recovery snapshots. Failure rollback, interrupted
  commit recovery after reopening, corrupt-backup refusal, unchanged immutable
  originals and preservation of unrelated files are tested. UI draft/review guards,
  cancellation, late job acknowledgements, import policy, stale results and double
  submission are covered. Existing Undo restores the whole batch.
- Native fixture tests rebuild batch-imported, converted and removed dictionaries
  in both editions and verify every remaining DDS payload. Exact archive-to-texture
  handoff tests now include batch conversion before replacement planning and prove
  original archive hashes unchanged. Latest native texture group: 38 passed.
- Full React: 324 passed / 4 native skips; latest targeted UI pass: 8 passed;
  production build passes. Full Python before the final extra source-tamper test:
  2621 passed / 17 environment skips. Computer-use browser verification covered
  panel expansion, selection guards, confirmation layout and cancellation; no real
  files were written through the browser fixture. Matrix revision is now
  `2026-09-05.2`. Texture implementation/handoff deliverable is closed; broad retail,
  game, large-workload and packaged acceptance remain separate open requirements.
- Added read-only YMAP/YTYP/YND/YNV relationship views to loose/exact-archive
  inspection and exported native workspaces. YMAP preserves original entity/parent
  indices even for missing positions. YTYP resolves local MLO room/portal/attached
  entity relationships and exposes entity-set inventory; MLO coordinates are
  explicitly archetype-local, and activation/location interpretation remains open.
  YND distinguishes local links, external nodes and duplicate-ID ambiguity. YNV
  preserves polygon indices, area-qualified adjacency, portal endpoints and points.
- React supports record search, linked-record navigation, XY/XZ/YZ projection,
  zoom/focus/fit and an explicit reference-search handoff into the matching decoder
  root. It does not automatically scan an installation or claim filename matches
  resolve hashed assets. XML drafts/reviews block handoffs while saved evidence
  stays inspectable. Displays are bounded to 1000 nodes / 1800 links with totals
  and truncation notices; XML is capped at 16 MiB and malformed coordinates fail
  visibly. Panels remain collapsible with independently scrolling records/details.
- Generated binary fixtures now pass actual converter decode/export and graph
  position/link checks for maps, archetypes, MLO interiors, path nodes and navigation
  meshes in both edition contexts. These checks preserve the source hash and are
  not encrypted-retail or in-game acceptance. Latest backend integration group:
  81 passed. Full React: 334 passed / 4 native skips; 61 targeted App/native/browser
  tests passed before the final additional preview-only regression test. The final
  native-workspace/matrix group passes all 9 tests, including explicit browser
  fixture labeling and refusal to simulate native authoring. Production build passes.
- Matrix revision `2026-09-05.3` records these exact capabilities and remaining
  limits. Computer-use browser verification was attempted but the in-app browser
  became unavailable after a tab-creation timeout; rendered visual qualification
  is still pending. The local Vite listener was confirmed alive. A labeled,
  backend-contract-checked generated fixture supports a later visual pass without
  opening or modifying game files. Animation, collision, REL and broad acceptance
  requirements remain open; the overall goal is not complete.
- The full Python coverage run at the map/navigation checkpoint completed:
  2658 passed / 6 skips, 81.16% coverage (80% gate met). This predates the REL
  implementation below and does not establish remote CI or packaged acceptance.
- Added native `rel-relationships` analysis via CodeWalker's typed speech, synth,
  mixer, curve, category, sound and game hash APIs. Some reference arrays populate
  only on binary load, so saved XML is normalized/reloaded in memory first. A
  canonical typed-field comparison rejects lossy normalization (including byte-
  count overflow); neither intermediate bytes nor game files are written.
- The shared React relationship viewer now handles REL records, local hash links,
  duplicate-hash ambiguity, external references and the document-level container
  catalog. Hash matches across unrelated REL families are never treated as local
  links. Container filenames can be handed to browser search; this does not claim
  resolved sound-to-AWC bindings. Unknown fields are not inferred from XML names.
- Tests cover Dat54 source/workspace export, reviewed XML edits, graph refresh,
  build/reparse and unchanged originals in both edition contexts; Dat22 category/
  curve and Dat151 game links; duplicates, bounded displays, missing/outdated
  helpers and lossy normalization. Latest native/matrix group: 54 passed, including
  both node/edge display limits. Native C# gate: 86 checks passed. React native/
  relationship/browser/matrix group: 23 passed with a backend-bound REL fixture;
  production build passes. Matrix revision `2026-09-06.1` records the scope and unqualified
  families. These are generated fixtures, not retail, packaged or game acceptance.
- Added a read-only React collision view for source and saved-workspace YBNs:
  triangle meshes, reconstructed eight-corner box surfaces, sphere/capsule/cylinder
  tessellation and standalone sphere/box/capsule/cylinder/disc bounds. Geometry
  centers and nested row-vector composite transforms are applied. Users can orbit,
  zoom, isolate material groups, show wireframe and inspect derived face normals.
  Saved-state/draft warnings, invalid-packet rejection, owner-local material-index
  labels and explicit 96-group / 1500-triangle sampling limits prevent overclaims.
  The new panel is collapsible and its group details scroll independently.
- Native round-trip testing exposed CodeWalker vertex clamping: half of the AABB
  extent need not contain vertex coordinates relative to GeometryCenter. The
  SDK-owned source overlay now includes both ordinary and shrunk vertices in the
  quantization range, with a nonzero floor for degenerate axes. The pinned upstream
  checkout is unchanged. Published CodeWalker.Core SHA-256:
  `3354ACD161E8DB16B33E3EEE8D0B5767980069A0DB4D25797322B131DC1820C1`.
- Collision tests: 29 passed, including actual native decode/export/rebuild of
  composite geometry and all five standalone primitive kinds in both edition
  contexts, per-coordinate quantization tolerance, original hash preservation,
  transformed bounds, outward winding, malformed input and bounded transport.
  Combined collision/native-workspace/viewport/archive group before the ten added
  standalone native cases: 137 passed / 1 skip. Native C# gate: 90 checks passed.
- Full React checkpoint: 351 passed / 4 native skips. Final collision/native/
  relationship/matrix group after visual refinements: 33 passed. Production build
  passes. Browser computer-use checks covered the collision fixture, box/capsule
  surfaces and normals, and the REL graph. Visual findings fixed horizontal group
  overflow and added space-bounded labels/centred single-record columns for small
  dependency diagrams. Browser fixtures remain explicitly labeled and cannot
  simulate native authoring; no game files were opened or modified by UI checks.
- Matrix revision `2026-09-06.2` records these collision capabilities. Display
  tessellation/face normals are not physics contact meshes. Native fixture tests
  do not qualify every material, flag, margin, retail variant or in-game behavior;
  general YBN build receipts still distinguish reparse from semantic equality.
  Animation playback and the broader acceptance/CI/OIV requirements remain open.
- Full Python regression checkpoint including REL and collision: 2703 passed /
  6 skips in 235.82 seconds. This run used native fixture gates but did not collect
  coverage. The separate coverage run subsequently completed: 2703 passed /
  6 skips in 277.90 seconds, 81.22% coverage (80% gate met). This predates the
  animation work below and does not establish remote CI or packaged acceptance.
- Animation foundation: native `animation-samples` exposes exact clip/animation
  keys, clip intervals and rates, sequence-aware local channel samples, quaternion
  interpolation and separate multi-animation layers. Guards reject bad frame
  layouts, overflowing typed fields, missing bindings, duplicate identities,
  truncated value arrays and unknown channel encodings. Cached quaternion values
  from XML follow the pinned decoder's scalar reconstruction because its binary
  reader cache does not exist after `ReadXml`.
- React channel inspection supports play/pause, seek, speed, loop, track selection,
  exact component values and curves in a collapsible panel. It pauses on hiding,
  collapse, review/draft locks and unmount. The UI and matrix explicitly distinguish
  this from model animation playback. Inventory selection is a read-only native
  inspection; saved XML remains guarded and originals are unchanged.
- Generated YCD tests cover source decoding, exported-workspace selection, reviewed
  rate edits, native rebuild and matching channel evidence in both edition contexts.
  Latest native relationships/collision/animation group: 102 passed. Native C# gate:
  105 checks passed. React animation/native/relationships group: 29 passed.
  Matrix revision `2026-09-06.3` records the foundation without closing the animation
  requirement. Next: explicit matching drawable/skeleton selection, bone-tag binding,
  hierarchy transforms and supported skinned-model playback. Do not substitute a
  channel plot or inferred track-order skeleton for that remaining requirement.
- Animation model playback now accepts an explicitly selected exported YDR/YDD/YFT
  XML. Dictionary owners and LODs are explicit; no skeleton is borrowed from a
  different drawable. Bounded parsing validates unique tags, array indices,
  hierarchy, transforms, byte weights and geometry-local bone palettes. Vertex
  buffers are chunked to preserve the desktop report contract without truncation.
- React reconstructs the XML bind hierarchy, evaluates one selected layer's local
  translation/rotation/scale channels by bone tag, applies inverse-bind linear
  blend skinning or rigid model bone transforms, and draws an untextured animated
  mesh. Bind-pose, skeleton, wireframe, orbit and zoom controls are connected to
  the shared scrub/play/pause/rate/loop timeline. Missing and unsupported channels
  are counted visibly; wholly unrelated skeletons refuse animated playback.
  Flag bytes are now retained in channel evidence instead of silently discarded.
- The model view follows the dense UI convention: independently collapsible with
  a bounded inner scrollbar, and no hidden pose redraws. Computer-use browser
  checks confirmed the generated weighted triangle, timeline play/pause, camera
  zoom and independent model collapse. These checks used labeled owned fixtures,
  not retail or live game assets, and did not authorize game-file mutation.
- This slice passed 52 Python/native tests and 41 React tests before additional
  chunk/rigid/collapse tests. Both edition contexts retained the generated YDR's
  skeleton, nonidentity palette and weighted vertex data through native encoding
  and decoding. Pose tests independently check bind invariance, weighted child
  rotation, hierarchy, shortest-arc quaternions and separate layers. Native C#:
  105 checks. Complete React checkpoint: 385 passed / 4 gated skips; production
  build passed. Matrix `2026-09-06.4` describes actual supported playback.
- Animation acceptance remains open: retail codecs/inverse-bind matrices,
  external shared skeleton selection, textured rendering, root motion and
  expression tracks, real-model performance and packaged/retail verification
  are not established by generated XML-bind fixtures. The broader CI, OIV,
  encrypted/large archive and Windows/game acceptance ledger remains active.
- Final targeted animation/model/workspace/matrix verification after disclosure,
  large-buffer, rigid-transform and quaternion-overflow checks: 56 Python/native
  tests and 45 React tests passed. Production TypeScript/Vite build passed again.
- Complete Python coverage checkpoint for this slice: 2742 passed / 6 gated skips
  in 309.06 seconds, 81.23% coverage (80% gate met). Collection preceded the final
  three model guard/transport tests; those and the quaternion-overflow guard are
  covered by the subsequent 56-test targeted run above. This is local source
  evidence, not remote CI, a published package or live-game acceptance.
- Shared skeleton gap: React now accepts a separately chosen exported skeleton
  XML and, for dictionaries, an independent explicit owner. Model and skeleton
  hashes and source paths remain separate, and both selections survive clip/LOD
  changes. Skeletonless model intake returns selectable metadata rather than
  losing the model context. Embedded/shared bind conflicts, invalid palettes and
  wrong owners fail closed; a valid index/tag map is not claimed to prove the
  selected rig is artistically correct for that mesh. Oversized LODs likewise
  retain navigation choices but return no partial mesh.
- Optional root-motion preview applies selected-layer tracks 5/6 to a parentless
  tag-0 root after local TRS, following the pinned renderer's composition order.
  It defaults off, recomputes on every seek, and resets at loop boundaries rather
  than accumulating guessed displacement. Tests cover rotation composition,
  weighted descendants, repeat seeks, bind-pose bypass and rejected non-root
  motion tracks. Ordinary nonzero flags are supported after the retail
  correction below. Expression/cloth/physics behavior remains unsupported.
- Opt-in retail read-only qualification is now reproducible with
  `scripts/smoke_retail_animation_model.py --game <installation> --edition <edition>`.
  It indexed the real NG-encrypted `x64v.rpf`, extracted only
  `models/cdimages/streamedpeds_mp.rpf::mp_m_freemode_01/uppr_000_r.ydd` and
  `models/cdimages/streamedpeds_mp.rpf::mp_m_freemode_01.yft` into temporary storage,
  exported native XML and validated the shared rig plus report transport in both
  local installations. High: 128 bones / 3,355 vertices / 5,720 triangles;
  Medium: 212 vertices / 284 triangles; Low: 64 vertices / 60 triangles.
  All three LODs passed for each edition. Temporary retail assets were not retained
  in the repo or distributed. This did not launch GTA or rebuild a game resource.
- Retail source invariance: Legacy `x64v.rpf` was 1,942,046,720 bytes / 10,907
  indexed entries, SHA-256
  `6df3454d01f632895d69998355c084bd3176e211df50954b770a5d22de89cb53`;
  Enhanced was 1,940,961,280 bytes / 10,905 entries, SHA-256
  `edb14830c0f869b2b0d811748e4d9e7261fb7652d8d8959f0f56ac60aad9fa42`.
  Full archive hashes matched before and after both checks. This is one bounded
  encrypted-ped corpus case per edition, not completion of the broader large/
  encrypted archive qualification requirement or retail animation rendering.
- Shared-skeleton/root-motion slice: 61 Python/native/matrix tests and 51 React
  tests passed, plus the production build. Matrix `2026-09-06.5` now separates
  implemented explicit shared-skeleton and root-motion preview from remaining
  retail YCD rendering/codecs, inverse-bind, textures, performance and game tests.

### Retail animation correction and qualification (September 6)

- Previous turn was progress: JSON source editing, protocol/UI coverage and
  tested save safeguards were added. Continued animation work found an actual
  retail playback defect rather than treating decoding as complete playback.
- `Renderable.UpdateAnim` in pinned CodeWalker uses `AnimationBoneId.Unk0` in
  expression remapping, not to disable ordinary translation/rotation/scale or
  root-motion channels. React previously skipped every nonzero flag. Corrected
  that filter while retaining unsupported-expression disclosure, byte-range
  validation and rejection of duplicate bone/track targets, even across flags.
- `scripts/smoke_retail_animation.py --game <installation> --edition <edition>
  --all-clips` now qualifies the retail `move_m@casual@a.ycd` against the MP male
  torso and explicitly selected shared skeleton. All extracted files/XML/JSON
  stay in an automatically removed temporary directory. No game resources are
  rebuilt or changed. The script invokes the real React/TypeScript pose and
  canvas-command test, not a duplicated Python skinning implementation.
- Legacy and Enhanced both passed run (`clip:1109B569`), idle
  (`clip:71C21326`) and walk (`clip:83504C9C`), each with two separately selected
  layers at five timeline points. Layer 0 matches 30/36/30 local channels and
  32/38/32 with root motion enabled, with no missing bone tags. Each clip changes
  mesh coordinates on scrubbing; seeks are deterministic and coordinates finite.
  React layer selection and actual canvas draw-command changes passed too.
  Unsupported tracks remain disclosed: these are partial poses, not complete
  multilayer/expression playback, pixel verification or live-game acceptance.
- Full `x64c.rpf` and `x64v.rpf` hashes matched before/after every run.
  Additional x64c SHA-256 evidence: Legacy
  `097949cc8268cce0166c81dad942c1f7f89661593580236c020a66d03b746b8b`;
  Enhanced `ca030a2bdd730b1aa8098a5fc00eba4ce0197200f52a21ebed72e07dc52dcc97`.
  x64v hashes are recorded above. Matrix revision `2026-09-06.6` distinguishes
  this retail pose/canvas-command evidence from remaining acceptance work.
- Verification checkpoint: 395 React tests passed, five gated tests skipped
  in the ordinary suite (including the separately executed retail test);
  61 targeted Python/native/matrix tests passed. All six retail clip/edition
  runs passed separately. Production React build and whitespace checks passed.
  The full objective remains open; no remote CI or release acceptance is inferred.
