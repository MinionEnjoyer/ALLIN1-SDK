# SDK priorities: validation, reversible optimization, diagnostic traceability

User-directed priorities after pausing the broad React parity expansion.
These goals are not complete. Existing work is retained; new editor features
are justified by their contribution to these three outcomes.

## Offline hardening follow-through (September 7)

The user approved recovery integrity, consistent React report validation and an
offline end-to-end regression. All three bounded tasks are now implemented.

Recovery now validates the existing receipt state identity, sealed SDK artifact,
input/output inventories, edition, change-report identity, both sealed static
reports, source-inventory relationships, validator identity and common comparison
context before accepting the saved originals. Malformed or contradictory evidence
cannot become trusted just by reopening the recovery package. Hashes still provide
internal content consistency, not authenticity against someone who rewrites and
reseals all evidence. No new signature or receipt schema is introduced.

Recovery deliberately does not require the current source tree or optimized
candidate: an intact receipt and exact saved originals suffice after the candidate
is damaged, moved or deleted. If a receipt is rejected, preserve the originals and
receipt for investigation; do not discard the backup or regenerate a receipt merely
to bypass the check. No automatic repair of contradictory evidence is attempted.

Immediately before publication, optimization rehashes the complete staged export,
including originals, candidate, artifact and receipt, rejecting changed or extra
files. Recovery rechecks its receipt/originals after copying and verifies the staged
recovered inventory. Outputs remain new destinations, not replacements of user
folders. Injected low-space and interrupted-copy failures leave no published partial
destination and clean up owned staging. These tests model handled errors, not sudden
power loss, disk firmware guarantees or a forcibly terminated process.

React now enforces the same category-severity/count rules as diagnostic report
ingestion: complete categories, bounded counts/codes, correct truncation, no
understated shown findings, exact aggregate status and literal read-only/runtime
claims. Truncated findings may omit the worst finding, so their reported maximum
is preserved rather than incorrectly downgraded. The Data Tools and optimization
screens reject inconsistent reports before adopting an exportable session. Backend
content-seal checks remain authoritative; React performs structural consistency.

The new source-level offline pipeline uses the real SDK review/apply boundary and
Launcher install/uninstall service against temporary test-owned installations for
Legacy and Enhanced (non-executable marker files only). It exercises declared
rotation, before/after optimization, artifact linkage, selected optimization-report
ingestion, stale diagnostic export, corrupt report rejection, installed-byte drift,
redacted export, restoration of pre-install bytes and exact original recovery. Its
deterministic build identity is a fixture, not release-build evidence. No runtime
session or causal crash diagnosis is inferred from these checks.

Final focused verification: **154 Python passed, 8 gated skipped; 58 React passed;
TypeScript project checks and Git whitespace checks passed.** This supersedes the
preceding continuation's focused counts, not the historical full-suite/frozen
results. No game launch, retail/archive scan, preview interruption, native/frozen
rebuild, real installation, commit or push occurred. Unrelated concurrent edits were
preserved. The broader three-goal objective and live acceptance remain unfinished.

## Offline-only continuation during preview generation (September 7)

The user directed further work that does not require a game launch while preview
generation is running. This resumes offline implementation despite the historical
runtime-choice blocker below; it does not authorize an installation or live test.
Concurrent vehicle-hitch and ClipSet edits were preserved. No game/archive scan,
native helper rebuild, frozen-shell rebuild, preview-process interruption, commit,
push, installation or GTA launch was performed during this continuation.

Declared pairwise attachment placement now accepts optional unit XYZW rotation
offsets. Legacy declarations retain identity rotation without changing their
authored shape. Nonfinite, malformed or nonunit quaternions fail rather than being
silently normalized. The column-vector composition is parent anchor × local rigid
offset (translation × rotation) × inverse(child anchor); translation remains in
parent-anchor axes. React uses a nested collapsible rotation section. Before/after
optimization and exported evidence retain the same declaration; changing rotation
invalidates the reviewed export. This is static bind-frame evidence, not proof of
engine attachment, animation or fragment physics semantics.

The diagnostic workflow now accepts a selected `asset-validation.json` or an
exported `optimization.json` (its candidate/after report). Sealed report hashes
must be recorded by the selected artifact before the report is linked. Matching
source inventories distinguish baseline inputs, candidate outputs, identical
inventories or an unestablished relationship. An unrelated report stays explicitly
unlinked. Importing the nested report does not certify the surrounding optimization
receipt or recovery files. Hashes are content links, not publisher signatures.

The collapsed diagnostic evidence panel carries bounded category statuses/counts
and finding codes; private paths and free-text findings are omitted. Contradictory
counts, category severities, report seals or runtime claims fail validation. Static
failures never establish crash cause. Exact selected-file bytes participate in
review identity; report changes invalidate export. Existing selected-log redaction
and privacy review work alongside this evidence without mutating the request.

Verification: **124 Python tests passed, 8 gated tests skipped; 35 React tests
passed; TypeScript project checks passed.** Tests include analytic rotation
composition, invalid rotations, before/after preservation, stale rotation export,
actual fixture optimization export → candidate report selection → artifact link,
malformed reports, truncation, redacted diagnostic export, stale selected-file
review and React selection/clear invalidation. These are fixture/component checks,
not new frozen/native or live-game acceptance. Earlier full-suite and frozen
candidate results predate these changes. The complete three-goal objective remains
unfinished; no engine-specific unknown has been promoted to a pass.

## Current acceptance snapshot (resumed September 7)

The user has resumed these three goals. The historical pause and scope-frozen
handoff below no longer prevent remaining implementation. Earlier progression
checkboxes describe their original checkpoints, not a current feature inventory.

- Goal 1: bounded package/dependency, shared-rig, skinning, fragment-reference,
  material, metadata and authored-LOD checks exist. Explicit pairwise bind-frame
  placement now checks exact declared parent/child anchors; engine conventions,
  fragment physics placement and independent runtime acceptance remain open.
- Goal 2: the supported reversible color-texture workflow is statically complete.
  This does not certify rendered appearance, residency or animation in game.
- Goal 3: native/RPF/optimization producers and Launcher diagnostics already
  connect. Managed Quick Import conversion and native Story controller candidates
  now carry the shared artifact envelope, as do map/vehicle project packages.
  Refreshed frozen offline acceptance now passes. Live-game acceptance remains
  open; nonmanaged Legacy OIV exports explicitly omit ALLIN1 receipts and cannot
  claim a traced managed installation. Not all goals are complete.

### Resumed managed/runtime provenance

Managed conversion captures the actual SDK/helper build and hashes the selected
RPF, conversion plan, generated catalog, manifests and review evidence. Its ZIP
publisher preserves that origin rather than relabeling a repack with the current
SDK. Present invalid envelopes block publication. Older absent envelopes remain
explicitly `not_recorded`; extra private folder notes are not silently published.
React shows recorded/missing provenance in a collapsed, scrolling identity panel.

Story candidates now bind the SDK/helper, controller-source inventory, configuration
request, compiler selection, validation receipt and exact edition outputs. Each
edition retains its own artifact; the combined ZIP is not one installable package.
Final readback checks staged bytes and all edition/combined ZIP members, then
rechecks source, SDK/helper and compiler identities before exclusive publication.
Supported layouts include a Launcher manifest. The standalone settings editor is
retained in the export but is not installed by that manifest. Custom layouts
outside the existing destination policy remain manual candidates, with the reason
recorded in `sdk-publication.json`; no Launcher allowlist was broadened.

Verification: 141 focused backend regressions passed; 12 React ZIP publication
tests and production React compilation passed. Two real React/Python runtime
tests passed, including both actual MSVC-built edition binaries, native CTest,
artifact/file hash comparisons and an intentionally missing dependency. The real
compiler run preceded the final ZIP readback hardening, which has separate staged
payload/ZIP tamper and source/SDK/toolchain drift regression coverage. Launcher
contract tests accept the generated envelopes and reject changed install bytes.
These are source/test-candidate results, not a frozen release or live-game proof.
No game files were changed; unrelated ClipSet PSO work was preserved.

### Resumed project packages and declared assembly

Map and vehicle project builders now ship `sdk-inputs.json` with the full bounded
source inventory and portable request. `sdk-artifact.json` binds the executing
SDK/helper, complete output inventory and review reports. Changed source/helper/
SDK inputs abort publication. Twenty-one focused project/provenance tests passed,
including acceptance by the Launcher's existing envelope contract.

Declared attachment pairs identify exact parent/child drawable hashes and named
anchors (blank explicitly means model origin), plus translation in parent-anchor
axes. The validator composes parent anchor × translation × inverse(child anchor),
checks finite nonsingular well-conditioned frames and anchor agreement, and warns
on orientation reversal. Shared rigs must be explicitly selected and compatible.
Fragments/drawable-matrix precedence remain `not_checked`; no physics, animation,
clipping, hierarchy-chain or engine attachment rule is guessed.

React presents declaration/evidence in collapsible scrolling panels. Changed
declarations invalidate exports and navigation until inspected or discarded.
Validation-to-optimization handoff retains identical declarations, source hashes
and shared context in both before/after reports and the exported receipt. The
existing preservation guards still prohibit model/animation edits by this bounded
color-texture workflow. Six evidence-shape tests and the real React/Python
inspect/export and validation→optimization→export integrations passed. Related
backend coverage passed (53 tests, two environment-gated skips); final broad and
frozen verification is pending after the last reflected-frame/container checks.

The prior full backend run found only a stale generated vehicle-catalog source
fingerprint after the preceding catalog-name work. The existing generator updated
the source version/checksum only; no model/hash pairs or catalog scope changed.
That targeted regression now passes. This does not establish live-game acceptance.

### September 7 verified handoff and next acceptance boundary

Final unchanged implementation checks passed:

- Full Python: **3,012 passed, 81 skipped**, 460.99 seconds. Skips are not passes.
- Full default React: **443 passed, 15 skipped**. Two separately enabled real
  runtime tests passed afterward, including both MSVC-built editions and CTest.
- Native-enabled validation/material/fragment/texture/assembly/optimization and
  native-build provenance regression: **89 passed**.
- Native helper: **113 checks passed**; Rust desktop library: **19 passed**.
- Fresh frozen service, existing archive/ped smoke checks, production React,
  native shell build, embedded frontend probe and portable lifecycle: passed.
- Fresh frozen validation → declared assembly → before/after optimization →
  temporary Launcher install → changed-byte diagnostics/export → uninstall →
  exact original recovery: passed. This uses test-owned files, not a GTA session.

Diagnostic candidate: `build/tauri-candidates/6a90fbab49ed48dda34e347634de6b50`.
Its source identity precedes this documentation-only entry. The diagnostic is
deliberately **not release-qualified**, not installed/published, and includes the
existing dirty source snapshot (including preserved unrelated ClipSet drafts).
Do not represent its successful probes as installer/native-GUI/live-game proof.

The next acceptance step requires the user's choice of a real reference asset
and GTA edition. Use a reviewed baseline with known rig/attachment behavior,
then a supported color-texture candidate and exact restoration. Compare the same
poses/attachments/LOD views and correlate the resulting installation/session
identities. A natural failure may supply crash evidence; no deliberate crash or
inferred crash cause is required. No game installation or launch was authorized
or performed for this resumed offline work. Engine-specific fragment/attachment
precedence remains outside declared pairwise placement and must not be certified
from the affine alignment result alone.

### Read-only retail report qualification (September 7 continuation)

Added the opt-in `--asset-report` path to `scripts/smoke_retail_animation_model.py`
and a real React report-consumption test. The harness uses temporary extracted
retail XML, selects exact hash-bound shared-rig owners through the same package
validator, checks that the desktop transport preserves the report, and requires
an actually executed/passed React assertion (a skipped/empty run is not a pass).
No retail asset bytes become repository fixtures or distributable outputs.

Executed against the locally installed stock MP-male `uppr_000_r.ydd` and
`mp_m_freemode_01.yft` in **both Legacy and Enhanced**. Each has 128 selected rig
bones; all three LODs passed supported bind/skinning checks. Measured geometry
was 3,355/212/64 vertices and 5,720/284/60 triangles for High/Medium/Low. React
displayed both actual reports without rejecting or truncating their evidence.

The report correctly remains incomplete: absent attachment/texture declarations,
partial mip chains, unbound slots and engine LOD activation are not certified.
Selecting a known shared skeleton does not prove engine-intended assembly rules.
Full `x64v.rpf` before/after hashes matched:

- Legacy: `6df3454d01f632895d69998355c084bd3176e211df50954b770a5d22de89cb53`.
- Enhanced: `edb14830c0f869b2b0d811748e4d9e7261fb7652d8d8959f0f56ac60aad9fa42`.

Qualification helper/package/assembly regressions: **29 passed**. Production
React compilation passed. These additions change qualification scripts/tests,
not production validation behavior; the preceding frozen candidate and full
suite results predate this added qualification coverage. No GTA launch, install,
runtime acceptance, commit or push was performed. The real in-game reference
package/edition choice is still pending; the overall goal is not complete.

### Acceptance blocker audit

The same reference-package/edition decision remained unanswered across the
resumed implementation turn and two continuations. Offline source, frozen,
native and real-retail report checks are finished for the recorded scope; none
can establish the remaining engine-specific assembly behavior or live candidate
acceptance. Work is blocked pending that user choice and direction to perform the
corresponding game test. This preserves the full objective rather than declaring
the existing static subset complete. No installation, launch or speculative
engine-rule change is performed to bypass the missing decision.

## 1. One trustworthy asset-validation report

- [x] Initial versioned, read-only model-XML report with stable report, source,
  validator implementation and workspace fingerprints; React display/export.
- [x] Primary drawable skeleton indices/tags/names, parent cycles, finite and
  nonsingular transforms; supported skinning palettes/weights and LOD counts.
- [x] Missing context, unsupported checks and static/runtime proof are distinct.
- [ ] Package intake: all relevant native assets and metadata, not just one
  exported model. Exact archive member identities and dependency coverage.
- [ ] Explicit shared-rig candidates, attachment definitions and assembled
  transform validation, including fragment physics children.
- [ ] Resolve embedded/package/shared-game textures with precise provenance;
  validate payload existence/format, not only names.
- [ ] Detect metadata identity/hash collisions across package and selected
  installed/load-order context. Distinguish intended overrides from conflicts.
- [ ] LOD distance/activation, measured memory estimates with assumptions,
  visual-quality comparison and independent in-game acceptance evidence.

Current entry point: Native resources & audio → exported YDR/YDD/YFT workspace
→ Asset validation report → Review asset report export. No “RPF decodes” result
is promoted to asset safety. The first report uses decoded primary drawable
XML and explicitly leaves attachment/package metadata/runtime checks open.
Validator fingerprints identify the actual validation sources, not a signed
or complete SDK distribution build identity.

Initial verification (September 6): 77 targeted Python/native tests passed;
13 React report/native-workspace tests and one real React-to-Python report-export
integration passed. Production React build and whitespace checks passed.
Checks include ambiguous tags, cyclic parents, invalid transforms/skin palettes,
missing rigs, ineffective geometry LODs, duplicate texture names, report digest
integrity, source preservation, stale reviews and competing-file preservation.
These are source/test-fixture results, not retail package or in-game acceptance.

### Package validation progression

Data Tools → Validate asset package now combines folder-package model reports
with typed metadata definition checks. Each source file has its own hash and
coverage label. Native model decoding requires an explicit edition; missing
decoding context and embedded archives remain visibly unverified. An optional
comparison folder is recorded by content fingerprint, never inferred from this
machine's game installation or treated as an established load order.

Supported definition namespaces currently include weapon/ammo/component names,
vehicle/ped/weapon model identities, handling names and tuning-kit names/IDs.
Reference records are not mistaken for definitions. Identical duplicates,
conflicting definitions, distinct-name hash collisions and external override
candidates are reported separately. Unknown metadata schemas remain unchecked.
This does not yet cover every package/archive, resolve shared textures, assemble
attachments or determine intended overrides; the completion boxes remain open.

The preceding goal setup turn changed the active objective but implemented no
code; this continuation made concrete progress with package intake, metadata
collision classification, a React workflow and an actual reviewed export test.

Package slice verification: 44 targeted backend tests passed; 13 report/native
React tests plus three real React-to-Python data/report integrations passed.
Production React build and whitespace checks passed. Runtime and retail-package
acceptance are not inferred from these checks.

### Texture payload and cost evidence

The package report now resolves explicitly declared model→dictionary bindings
and embedded texture entries. Each resolved sampler identifies its dictionary
and verified payload hash. YTD binaries decode in temporary workspaces with an
explicit edition; exported YTD XML uses its exact adjacent `assets` DDS paths.
Duplicate dictionary candidates have no inferred winner. Parent/shared-game
dictionary inheritance and undeclared bindings remain unchecked, not missing.

DDS checks verify dimensions/format against declarations, mip-chain lengths,
payload byte bounds and per-mip pixel decoding for supported tight 2D layouts.
Missing/truncated/mismatched payloads fail; arrays/cubes/volumes, padded rows and
out-of-bound decoding remain explicitly unsupported. Format-specific block
rounding produces per-mip packed-storage costs. React shows a **validated
subtotal**, excludes unknown payloads rather than treating them as zero, and
states that residency, alignment, driver copies and runtime allocations are not
measured. Costs are counted per dictionary entry, not deduplicated.

Verification: 88 targeted Python/native tests passed, including actual helper
build/decode of generated native YTDs in both Legacy and Enhanced modes; four
React report tests and one real React→Python DDS-validation/export integration
passed. This adds a reusable optimization measurement input, not a completed
optimization workflow or retail/in-game texture certification.

### Attachment evidence progression

Package reports now resolve supported weapon `AttachPoints` through exact
component definitions and model basenames to one drawable owner and one named
parent bone. Missing assets, duplicate definitions, multi-owner ambiguity,
missing shared rigs and invalid anchors remain distinct findings. Full parent
hierarchies are checked for indices, unique tags, cycles, finite/unit TRS,
singular scales and numerically collapsed composed matrices before emitting
local and parent-composed authored anchor frames. Divergent weapon/component
bone declarations are reported without guessing engine precedence.

React exposes the resolved parent/child sources, index/tag and matrix in nested
collapsible report panels; the reviewed JSON export carries the same evidence.
These are **bind-frame diagnostics**, not an inferred child assembly transform,
animation/clipping test or in-game placement certification. Comparison metadata
can supply component definitions; comparison models/shared rigs and non-weapon
attachment schemas are not silently treated as resolved. No game files changed.

Verification includes synthetic translation/rotation/scale composition,
malformed hierarchy/anchor cases, exact package source preservation, ambiguous
binding cases, React malformed-matrix rejection and a real React-to-Python
inspect/review/export integration. This closes a static binding gap, not all of
goal 1 or its runtime acceptance criteria. Latest check: 90 targeted
Python/native tests, five React report tests and one real attachment report
integration passed; React production build and whitespace checks passed.

## 2. A reversible optimization workflow

- [x] Generic asset/package optimization session, not weapon-specific scripts.
- [x] Immutable originals and an explicit candidate with step-by-step changes.
- [x] Before/after validation reports and synchronized quality previews.
- [x] Memory/cost deltas with format, mip, LOD and residency assumptions stated.
- [x] Guard animation bindings, bone/attachment transforms and material roles;
  abort or report unsupported preservation checks rather than guessing.
- [x] Reviewed optimized package export with exact source/output hashes,
  precise changes, recovery originals and a tested reverse path.

Reuse texture conversion, native workspace snapshots and reviewed archive
transactions. A smaller RPF alone is not evidence of lower streaming cost or
preserved behavior. Optimization must consume the same report schema as goal 1.

### Candidate/export progression

Data Tools → Optimize & recover now supports generic folder packages with
loose XML dictionaries or native Legacy/Enhanced YTDs. A bounded explicit queue
selects dictionary, texture, format, mip count and a user-identified color role.
Normal/mask/data roles are not guessed; alpha-losing DXT1 is rejected. Candidates
decode every mip and measure top-level RGBA error plus exact mip storage/file
deltas. Before/after thumbnails are synchronized but limited to 64 pixels;
full-resolution inspection and material-aware visual acceptance remain open.

The same package validator runs before and after. New reported static failures,
truncated findings, ambiguous shared payload ownership and changes to any file
outside the selected dictionary/payload set block export. Models, rigs,
animations and attachment metadata stay byte-identical. Native dictionaries
are rebuilt/reparsed with the existing native workspace verifier. This does
not certify their visual appearance or streaming behavior in game.

Reviewed export produces `package/`, exact `originals/`, and `optimization.json`
with input/output inventories, settings, metrics and both validation reports.
Recovery rechecks original hashes and writes them to a new folder; no overwrite
or install is implied. React's real Python-backed integration has exercised
selection, preview, review, export and byte-exact recovery. Synthetic native
conversion/reparse checks passed for both editions. Archive intake/repacking,
broader quality/behavior comparison, build identity integration and final
cross-workflow verification are still required before goal 2 is complete.

## 3. A connected build-to-game diagnostic trail

- [ ] Complete SDK build identity: version plus source/helper/package hashes.
- [ ] Per-artifact build manifest linking inputs, report IDs, candidate changes,
  output hashes, edition and toolchain evidence.
- [ ] Launcher installation receipts binding exact installed paths and bytes
  to that manifest; verify actual installed hashes, not timestamps/version text.
- [ ] Session correlation for runtime startup and crash evidence, with explicit
  process/session/build identities and missing/stale evidence indicators.
- [ ] Diagnostic classification: proven stale installation or mismatch versus
  evidence suggesting asset/runtime/unrelated-mod causes. No inferred certainty
  from a crash timestamp or ASI loader line alone.
- [x] Exportable, bounded diagnostic bundle with user-visible file selection,
  private-path redaction and no automatic upload of assets or logs.

Existing build, archive and Launcher receipts should be adapted to a shared
identity contract rather than replaced. SDK-only evidence cannot establish what
the Launcher installed or what a running game loaded without those consumers.

### Build/installation lineage progression

`artifact_identity.current()` binds the executing SDK source snapshot (including
dirty/untracked development work), actual native helper/runtime file hashes,
Python/sidecar executable hash and environment version evidence. Frozen builds
must pass their embedded resource-checksum verifier. Development fingerprints
are explicitly not releases or proof that a compiled helper matches current
source. Local checks observed 630 source inputs and 189 helper/runtime files;
counts are observations of this checkout, not hardcoded identity requirements.

Optimization exports now include a sealed `sdk-artifact.json` in `package/`,
binding this build fingerprint, exact input/output inventories, edition,
validation report identities and change digest. The envelope is generated
metadata outside its own payload inventory; prior envelopes are preserved with
the originals when re-optimizing. React displays build and artifact identities.

The Launcher repository now shares the dependency-free envelope contract and
adapts its existing optional-mod installation transaction. A present invalid or
wrong-edition SDK envelope is rejected before installation. Post-copy checks
bind actual installed loose files and verified RPF-member records to the SDK
artifact; the lineage is stored in the existing install receipt and retained
across enable/disable. Failure uses the established rollback path. Packages
without provenance remain explicitly untraced; version text is not promoted to
a build identity. These hashes are content identities, not publisher signatures.

Verification: twelve SDK contract/identity tests, seven Launcher lineage tests,
one real SDK-export → Launcher-install synthetic integration, and the real
React/Python optimization export/recovery integration passed. The wider
Launcher optional-mod/toggle run passed 109 tests with three existing skips.
No real game installation was changed. Runtime session/process/module binding,
crash correlation/classification, user-selected diagnostic bundles and broader
SDK build-path integration remain open; goal 3 is not complete.

### Runtime observation and React triage progression

Launcher launch now creates a local `runtime-traces/<session-id>.json` under its
state directory, snapshots receipted SDK loose-file bytes, and records launch
observations. Once a stable process is observed, a read-only Windows probe
captures its creation time, exact executable path and native OS module paths;
only game-local module files are hashed. A bounded background observer remains
attached to that PID/creation identity after the Story-ready monitor finishes.
Wrong-installation processes, PID reuse, access failures, observation limits and
process disappearance stay distinct. Disappearance is **not** called a crash;
on-disk module hashes are **not** described as mapped-memory hashes. Managed
assembly loading and asset-use telemetry still need independent evidence.

Data Tools → Trace build to game now accepts a chosen SDK artifact, installation
receipt, game folder and optional session. It verifies envelope/event-chain
identities and current installed loose-file bytes; flags different builds,
missing/changed bytes, disabled packages and unmatched session revisions; and
separates verified observations from indications and unresolved causes. The
reviewed derived JSON export redacts the private process-installation path and
does not upload files. Raw-log bundle selection, correlated crash-event parsing,
RPF-member reinspection and causal reproduction evidence remain open.

Verification includes eight SDK triage tests, seventeen Launcher session/launch
tests, and a real React/Python inspect-review-export diagnostic integration.
A read-only probe of the test Python process on this PC returned PID, creation
time and 19 module paths; no game was launched for that check. React production
build passed. This is process-observer evidence, not live GTA acceptance.

### Nested RPF intake and reversible packed optimization progression

Package validation now accepts a standalone RPF or a folder containing RPFs.
Bounded recursive extraction occurs only in a private temporary copy, with an
explicit edition and decoder installation. Missing decoder context, ambiguous
indexes, expansion limits and unsupported archives remain visible coverage
gaps. Reports bind the original container hash, exact nested member identity,
extracted bytes and canonical RSC content hash. Source archives are rehashed
and never modified. React exposes an independently scrolling provenance panel.

Optimization can select native YTD dictionaries inside nested RPFs using the
same generic queue. Only affected outer archives are rebuilt; the existing
builder verifies their structure and member contents. Nonselected extracted
members and loose files remain unchanged; canonical RSC comparisons account
for compression-envelope differences. Exact original containers are retained
for recovery. React displays rebuilt container hashes and changed members.

Both-edition native tests exercised real YDR/YTD members inside nested RPFs.
The actual React/Python packed Enhanced workflow validated, previewed and
exported the package with byte-exact recoverable originals. This does not prove
in-game streaming, visual quality or attachment behavior. Enhanced shader XML
can contain unbound sampler slots; those are now `not_checked`, not falsely
reported as missing required dependencies without material-role evidence.

### Explicit crash-event correlation progression

Trace build to game now accepts selected Windows Application Error event XML
(up to 32 events / 1 MiB). Provider, event ID, error level and channel are
validated. Correlation requires the same executable path, PID, exact process
creation FILETIME (including the seventh fractional digit) and compatible
observation chronology. Reused PIDs, old runs, missing identities and unrelated
events cannot establish a session crash. Session module records are bounded
and validated before linking observed on-disk hashes to artifact outputs.

The derived report separates `application_crash_recorded` from root cause.
Faulting-module path/hash correlation can identify a selected artifact's fault
site, but does not prove that an asset or unrelated mod caused it. Only
whitelisted derived fields leave the parser; private host, account and absolute
module/installation paths are omitted. Export review re-reads/hash-binds the
selected event XML. No dump capture, registry change or automatic upload is
performed. Event XML is locally supplied evidence, not authenticated telemetry.

Verification: 33 focused crash/session/asset tests passed, including one-tick
creation-time precision, wrong PID/path, duplicate fields, DTD rejection and
module identity. The real React/Python diagnostic inspect/review/export test
passed with correlated crash evidence, installed-file drift and redaction.
Automatic event collection, selected-log bundles, RPF installed-member
reinspection and controlled runtime acceptance remain open.

### Full-resolution pixel/mip comparison progression

Optimization previews now offer synchronized exact-pixel inspection for any
available mip and coordinate, in bounded 64 × 64 regions without downsampling.
Before, candidate and absolute-difference images use the same crop, channel
selection (RGBA, RGB or alpha) and integer display zoom. Missing original or
candidate mips are explicitly absent, not resampled into fictitious evidence.
Changing the viewed region leaves the artifact/candidate identity unchanged;
stale source, settings or executing-build identities reject the inspection.

This closes thumbnail-only inspection for supported color DDS candidates, but
does not provide material-aware rendered or in-game quality approval. Every
region is decoded from the actual original/candidate DDS mip bytes. The real
React/Python workflow passed exact-pixel inspection followed by reviewed export
and byte-exact recovery. Focused texture/package tests passed 21 with four
native-gated skips; production React build passed. Separately, the surrounding
native-enabled package/validation/crash regression run passed 103 tests.

### Current installed RPF-member verification progression

Build-to-game diagnostics now re-extract exact receipted installed RPF members,
including explicit nested `!` paths. Receipt source/hash mappings are validated
before access; only named `mods/` containers are inspected. Decoding uses
private container copies with source-stability hashes, explicit receipted
edition, unique member matching, and bounded containers, extraction bytes and
staging space. Original game archives are not modified. Disabled packages are
not attributed to the active members restored in their place.

React exposes a collapsible installed-member identity table. Matching extracted
bytes, missing archives/members, mismatches and unavailable decoder/size/stability
checks remain distinct. Raw extracted-byte drift is not automatically a
canonical RSC-content defect or a crash cause. Seventeen native-enabled
diagnostic tests and the actual React/Python nested-member inspect/review/export
test passed; synthetic Legacy/Enhanced game folders remained byte-identical.

Latest focused optimizer/crash regression: 45 tests passed with native gates
enabled. Pixel/report React unit tests: seven passed. Genuine in-game acceptance,
broader shared-rig/material context, selected diagnostic bundles, automatic
crash collection and remaining SDK artifact-producing build paths are still
open; the overarching goal remains active.

End-of-slice verification: the combined native-enabled validation, attachment,
texture, archive intake, optimization, session/crash and installed-RPF suite
passed 128 tests. Latest React production build and whitespace checks passed.

### Explicit shared-rig and authored LOD context progression

The unified package report now catalogs exact drawable owners in both the
package and selected comparison context, including native assets and expanded
archive members. React offers explicit per-drawable shared-rig selection with
both source hashes. No available candidate is automatically selected. Changed
bytes, unknown owners and duplicate selections reject stale bindings. Selected
skeleton indices/tags/parents/transforms are checked by the same compatibility
logic used in animation preview; conflicts with embedded binds fail rather
than silently replacing the authored model. Source model bytes stay untouched.

Selected compatible shared rigs also supply static attachment-anchor frames;
comparison models can supply exact parent/child assets. Duplicate model names
remain ambiguous. Reports retain selected rig identities and distinguish
user-selected static compatibility from proof of game-intended binding or
animated assembly. Selection drafts survive panel collapse, guard navigation,
and require reinspection before export. The real React/Python shared-rig
selection/revalidation/report-export test passed.

Authored `LodDistHigh/Med/Low/Vlow` values now accompany populated LOD geometry.
Nonfinite, duplicate or out-of-float32 values fail; missing populated
thresholds remain unknown; negative, zero/nonincreasing populated thresholds warn. Negative values are retained as authored, without inventing engine sentinel semantics. React
shows file values separately from unmeasured engine-scaled transitions. Field
names are grounded in the bundled CodeWalker Drawable reader/XML writer, not
inferred from UI labels. These checks do not claim in-game activation behavior.

Verification: 103 targeted native-enabled asset, shared-rig, animation,
attachment, package/archive and optimization tests passed; React production
build passed. Fragment physics-child assembly, material/shared-texture context,
runtime acceptance evidence and the remaining diagnostic/build integration
work are still open. All three overarching goals remain active.

### Selected diagnostic bundles and automatic crash-query progression

Trace build to game now accepts up to eight explicitly chosen .log/.txt
snapshots and one-based excerpt ranges, with bounded input bytes, selected
lines and redacted output. React displays every included line, redaction and
truncation counts, plus source hashes. Path, common secret, email, URL and IP
patterns are redacted; users can add private terms. Free-form anonymity is not
guaranteed. Export requires acknowledgment of the exact redacted preview hash,
followed by the normal reviewed export. Changed logs invalidate that review.

The local bundle contains the derived diagnostic JSON, numbered redacted text
excerpts and a manifest binding all file hashes to the artifact, SDK build and
session IDs. Original paths/names, raw logs and private-term lists are omitted;
no upload is performed. Excerpts are labeled user-selected context, not
independently correlated runtime telemetry. Backend and real React/Python
inspect/acknowledge/review/export tests passed.

The Launcher now anchors the first observable startup process, before stable
launch acceptance. Its background observer queries bounded Windows Application
Error events after process disappearance or an anchored launch failure, with
two delayed retries for event-log latency. Query input travels as JSON stdin;
there is no shell interpolation, elevation, remote-machine query or registry
change. Captured XML must match PID, executable path and exact creation time.
SDK triage revalidates that evidence from the selected session without another
file picker. No event, access failures and query truncation remain incomplete
evidence, not proof of a normal exit or unrelated-mod causality.

A real read-only query against this PC's healthy test Python process initially
exposed missing diagnostic-module autoload in hidden Windows PowerShell.
Explicitly importing the system module fixed it; the live query then completed
with zero matching events. No GTA process was launched for that check.

Frozen-build handling was also corrected: Launcher observer identity binds its
sidecar executable and verified packaged-build identity, while SDK validation
and redaction fingerprints bind their executing sidecar instead of assuming
loose Python sources exist inside a PyInstaller build. Source mode still hashes
the actual implementation files. Simulated frozen-layout regression tests pass;
this is not yet a complete packaged-release smoke test.

Verification: 31 Launcher startup/session/crash/lineage tests passed; 57 focused
SDK implementation/validation/log-bundle/crash tests passed; the real React
diagnostic test passed using automatically collected session-event evidence and
a reviewed redacted bundle. The surrounding SDK build/asset/runtime acceptance
work remains open. Existing unrelated Launcher Reactor-resource whitespace
findings were left untouched; targeted changed launch-code checks passed.

### Fragment physics-child evidence progression

The common report now reads bounded `Physics/LOD1..3` children, preserves null
array slots and checks exact group indices, group-parent cycles and primary
skeleton bone-tag matches. Decoder-assigned primary/shared rigs supply child
skinning validation without guessing a rig by filename. Conflicting embedded
rigs, broken palettes, ambiguous tags, duplicate arrays and malformed/nonfinite
matrices are not silently repaired. Missing group names are reported because
the native decoder cannot rebuild them. Unsupported or sentinel meanings stay
unverified rather than being classified as guaranteed game defects.

Physics matrices, drawable matrices and authored offsets remain separate in a
collapsible React panel and the reviewed JSON report. This intentionally does
not invent world-transform composition, wheel corrections, damage behavior or
physics simulation. Additional drawable matrix IDs, fragment default-pose
semantics and actual assembled/runtime behavior still need separate coverage.

Verification: 129 targeted native-enabled cross-workflow tests passed, including
generated YFT build/decode checks for both editions. The actual React/Python
fragment inspect/review/export test passed with unchanged source bytes. The
production React build and whitespace checks passed. A full baseline regression
then passed 2,898 Python tests and 412 React tests, with one failure in each:
the automation catalog omitted optimization, and a UI test had a stale matrix
revision. Both were corrected; the catalog now advertises optimization/recovery,
native report export and diagnostic inputs, with schema-alignment assertions.
Targeted post-correction tests passed. Those baseline totals are not a claim
that the entire corrected suite has been rerun. All three goals remain active.

### Selected parent/shared-texture context progression

Package and selected comparison metadata now supply explicit texture-parent
relationships using the bundled decoder's vehicle, ped and map-parent schemas.
Ped multi-child declarations and decoded `.ymt.xml` relationship files are also
accepted. Lookup follows at most 32 uniquely selected dictionaries and records
the exact chain, payload hash and supporting metadata file hashes. Conflicting
parents, graph cycles, duplicate dictionaries, invalid payloads, missing child
dictionaries and dictionary/texture-name engine-hash collisions cannot become
resolved passes. Invalid relationship documents contribute no partial graph.

React displays the lookup and declaration provenance in collapsed, scrollable
report content. Comparison context is selected explicitly; no game folder is
searched for guessed dependency winners. Missing context, native metadata not
decoded by this adapter, shader-role requirements and actual runtime load order
remain unverified. Native YMT decoding and full inherited material semantics
remain follow-up work, not completed coverage.

Verification: 175 combined native-enabled backend tests passed across parent
textures, fragment validation, package intake, optimization, diagnostics and
automation. Four real React/Python workflows passed, covering parent context,
fragment export, shared-rig revalidation and optimization/export/recovery.
Eight report unit tests and the React production build passed. No game assets
were installed or modified. The overarching three-goal objective remains active.

Post-correction full React regression: 415 passed, seven gated skips, no failures.
The final native-metadata coverage adjustment passed 22 focused backend tests.
Next integration priority is carrying selected validation context into the
optimizer's before/after reports, followed by wider artifact-producing build
paths and packaged/runtime acceptance checks. These are remaining requirements,
not a completion report.

### Validation-to-optimization context integration

React now hands a selected package, edition, decoder, comparison context and
hash-bound shared-rig selections directly into optimization. The handoff does
not reuse an old report as proof: reinspection is required before candidates or
export become available. The optimizer also exposes its own collapsed context
panel, explicit rig controls, and folder/RPF selectors. Context changes preserve
the candidate draft but invalidate its report and export authorization.

Both before/after reports consume the same selected comparison bytes and rig
bindings. Comparison changes between reports or after export staging reject
publication; comparison dependencies are never modified or included as payload.
The exported receipt binds context identity and both report hashes. Direct
domain calls also reject publication/recovery inside protected source/context
folders. Export refuses a receipt larger than its recovery reader can accept.

Standalone RPF inputs now use the same immutable-copy, candidate, export and
recovery transaction as folders. Only the selected archive is included—not its
parent directory. Generated Legacy and Enhanced native tests kept exact model
rig selections through repacking and recovered the original archive bytes.

Verification: 108 combined native-enabled optimization, asset, intake, artifact,
Launcher-integration and automation tests passed before the final receipt-bound
guard; 12 focused tests passed after that guard (six native-gated skips). Real
React/Python handoff, folder-RPF and standalone-RPF workflows plus the shared-rig
control test passed. Production React compilation passed. Material-aware visual
acceptance, remaining native metadata/build-path provenance and packaged/runtime
acceptance remain required; the full three-goal objective is still active.

Final context/recovery regression: 64 tests passed with native gates enabled,
including both single-RPF editions, exact reverse recovery, source/context
publication guards, receipt-size parity, artifact-to-Launcher integration and
automation discovery. The next provenance integration point is the existing
RPF ZIP publisher, which currently exports payload hashes but no shared SDK
artifact envelope. Native build reports and managed vehicle/runtime package
producers also still need the shared build identity contract.

### RPF construction and publication provenance

GXT2-to-RPF builds now record the actual executing SDK/helper identity in both
review and build report. Identity drift after review or during construction
rejects publication. RPF ZIP exports carry the shared `sdk-artifact.json`, bind
every shipped byte and the exact input reports, and preserve the construction
identity in portable evidence separately from the publishing SDK. Older input
reports remain explicitly `not_recorded`, never assigned a guessed build.

React exposes these identities in collapsed review panels and verifies them in
the write result. Launcher install receipts retain the artifact/build linkage
for whole archives and exact nested members. Native member smoke additionally
checks installed hashes through install, disable, enable and uninstall, while
preserving the unrelated dictionary and original archive.

Verification so far: 187 backend tests passed across construction/publication,
artifact identity and actual Launcher integration; 26 React unit tests passed.
The first real native React/Python publication tests passed both whole-archive
and nested-member modes, including temporary Launcher install/restore. Final
native rerun after adding construction identity passed whole-archive export and
Launcher install/restore. The nested-member rerun hit the native patcher's
game-closed guard after GTA Enhanced started on this PC; it remains deferred,
not passed. Its test now explicitly skips when that guard reports an open game.
The fixture-only process probe isolation for whole-archive construction never
changes production guards or touches game archives. This does not certify game
startup, runtime acceptance, other producers or the full goals.

Final publication regression: 28 React unit tests passed, production React build
and whitespace checks passed. Native React rerun passed the whole-archive path;
the nested install case was explicitly skipped because GTA Enhanced was running.

### Actual frozen three-goal pipeline smoke

Built an isolated, non-release-qualified frozen sidecar and self-contained
helper/resources under `build/tauri-candidates/f2cd43bc05c4418b8bd5548c5c468b61`.
The broad candidate smoke stopped at the unchanged game-closed guard while GTA
Enhanced was open. Its `diagnostic-validation.json` remains FAIL; this candidate
is not release-qualified and no installer or live-game acceptance is claimed.

The separate opt-in `tests/test_frozen_asset_pipeline.py` passed against that
actual executable in 17.76 seconds. With Python overrides removed, PATH limited
to System32 and disposable user/output state, it exercised package validation,
texture optimization, reviewed export, actual temporary Launcher installation,
matching build identity, deliberate installed-file drift, reviewed diagnostic
export, uninstall and exact original recovery. Every protocol process verified
the embedded identity against staged resources; the artifact identified the
actual frozen executable hash. This is not the earlier simulated-frozen test.

Frozen sidecar SHA-256:
`68bb8db7b7b3945949afdedcab23a1787581e2b4e2aad8bf65416ba367dcc604`.
Resource checksum inventory SHA-256:
`738101fb6de5b00b87fe30a3fc5f1867028bc52fa98c54259b342b2dd614a9bf`.

The frozen executable/resources were left unchanged. The candidate predates
only these subsequent test/ledger additions; rebuilding is still required for
whole-source release qualification. Native RPF mutation smoke remains deferred
while GTA is running. Next substantive gaps include native metadata coverage,
material-aware optimization/assembled attachments and remaining producer
provenance; the three-goal objective remains active.

### Native metadata intake in the unified report

Package validation now decodes selected YMT/YTYP/YMAP/YMF/PSO inputs through the
existing helper in temporary workspaces when an explicit edition is supplied.
Original-file and decoded-XML hashes remain distinct and appear in a collapsed
React evidence table. Input/output size, file-count and total-XML limits are
explicit; unavailable decoding and unknown definition schemas remain unchecked.
Native decode failure is never converted into a successful definition scan.

YTYP archetype and YMAP map identities now participate in namespace checks.
The decoder's `hash_XXXXXXXX` representation is interpreted as its existing
32-bit key, not re-hashed as text or mistaken for a second known name. A mapped
identity's name spelling is normalized for duplicate comparison. Other schema
fields retain their authored semantics; no load-order winner is invented.

Initial verification: 78 combined native-enabled metadata, texture, package and
optimization tests passed; actual META ped YMT, YTYP and YMAP decoding passed in
Legacy and Enhanced. Three selected React tests passed, including real native
archetype decoding and reviewed JSON export with unchanged original bytes.
Production React build and whitespace checks passed. Ped variation collision
semantics, unsupported structured metadata variants, material-role inference
and assembled/runtime acceptance remain open. The previous frozen candidate
predates this implementation and does not verify these new metadata changes.

Post-integration full regressions passed: **2,942 backend tests, 79 gated skips**
(203.88 seconds) and **425 React tests, 11 gated skips** (67.43 seconds). The
metadata/context/source-drift additions passed 27 focused native-enabled tests.
These full suites ran with the default native gates, so the separate native
and frozen results above remain the evidence for those specific paths. No GTA
files or running game processes were changed. Next optimization work is keeping
shader-slot usage in texture provenance and rejecting known non-color/mixed-role
payloads before conversion; manual color selection alone is not enough.

### Material-role preservation guards

Texture provenance now retains every bounded shader-slot use, including explicit
comparison consumers and fragment physics-child drawables. Known bump, data and
palette slots (including hashed slot names) block color conversion before the
encoder runs; a manual color declaration cannot override mixed/non-color usage.
Unknown slots remain explicitly unknown, and color hints still require an author
declaration. This is not shader-bytecode analysis or visual/runtime certification.

The static preservation guard is now checked: nonselected models, animation,
rig and attachment files remain hash-identical; unsupported material context,
incomplete model decoding and truncated evidence block conversion. Reports retain
the limits of supported checks. Final assembled appearance remains a separate gap.
React resets material declarations when selecting another texture or package.

Verification: 81 combined backend/native tests, three actual React/Python
workflows and production React compilation passed. A final 15-test native-enabled
material suite passed, including generated packed Legacy and Enhanced normal-map
assets, mixed-role and fragment-child usage, and rejection before encoding.
These results do not establish in-game preservation or close all three goals.

### Native rebuild producer provenance

Native workspace rebuild receipts now embed the shared artifact envelope with
the actual executing SDK, explicitly selected helper resource hashes, original
snapshot, manifest, edited XML, dependencies, output and reparse-evidence hashes.
The bounded input inventory is checked before conversion. Workspace/helper/SDK
drift rejects publication; the output/report pair retains transactional cleanup.
Native React build and replacement-plan reviews bind this identity, and display
a collapsed identity panel. Replacement plans keep the precise receipt hash.
The envelope is embedded in the per-asset receipt, not silently installed as a
root package manifest. Reparse success remains separate from asset/game safety.

Initial regression: 98 native-enabled backend tests passed, including actual
Legacy/Enhanced export, rebuild and reviewed archive replacement-plan handoff,
exact helper/input/output linkage, and six during-build drift cases. The React
optimization workflows also passed after role-selection reset (three tests).
Native React build integration and broader regressions are being verified next.

Native React verification passed for actual Legacy and Enhanced builds, with
exact receipt/output/helper checks and untouched originals. Full regressions
then passed: 2,963 Python tests (81 gated skips) and 426 React tests (13 gated
skips). These suites include the material-role and native-provenance changes;
the subsequent cost-presentation addition is verified separately below.

### Optimization cost and preview acceptance scope

The supported generic color-texture workflow now satisfies the static goal-2
checklist: synchronized actual DDS pixel/mip previews, same-context before/after
validation, explicit preservation guards, and exact export/recovery. A compact
collapsible cost table now places each original/candidate format, dimensions,
mip count, packed mip storage and DDS file bytes beside the delta. Inconsistent
cost evidence is rejected before the React session is adopted. All stored mips
are counted; geometry LODs/distances remain unchanged. Residency, alignment,
driver allocations, streaming behavior and rendered material quality are not
claimed as measured. This does not broaden support to geometry optimization or
normal/data/palette conversion, and does not establish in-game acceptance.

Cost-presentation verification: eight component/invalid-evidence tests and two
actual React/Python cost/export/recovery tests passed (10 total). Production
React compilation and whitespace checks passed. The native identity panel is
also height-bounded and scrollable when expanded. No game files were changed.

Next required work remains goal-1 assembled attachment/coverage acceptance,
remaining producer provenance (including direct RPF builder and managed/runtime
publication paths), and refreshed packaged/runtime verification. The prior
frozen candidate predates the material, metadata, native-provenance and cost UI
changes and must not be cited as verification of them. All three goals remain
active; this ledger records supported static completion without declaring
unobserved game behavior or every possible asset format safe.

### Direct RPF and graph construction provenance

Direct RPF builds now seal the actual SDK/helper identity together with the
complete existing source inventory, recursive readback hashes, edition and
output hash. The 25,000-file authoring bound is retained; the report is not
truncated to the smaller install-envelope inventory limit. This is a sealed
construction receipt for downstream publication, not a root install manifest.
Graph builds verify that receipt before adding exact graph identity and
resealing it. React's RPF-build review binds the executing SDK identity and
displays a collapsed, scrolling construction-evidence panel.

Readback-time source/helper/SDK drift now aborts publication. Exclusive hard-link
publication also refuses output/report files created after preflight, preserving
the competing file and cleaning up only the build's own staged/published output.
No game archive is modified. Hash seals remain content evidence, not signatures.

Verification: 161 surrounding builder/graph/program/text-publication tests passed
before the final exclusive-publication/race checks; 40 focused provenance/builder/graph tests
passed afterward, including tampering, three readback drift cases, two competing
output races and stale SDK review. Two actual React/Python/native nested-RPF
construction tests passed (Legacy and Enhanced), checked output/helper hashes
and graph-receipt seals, and preserved their original inputs. Production React
compilation and whitespace checks passed. Downstream runtime/managed package
publication still needs the shared installable artifact envelope.

Final RPF cross-workflow regression passed: 186 native-enabled backend tests in
70.41 seconds, covering provenance, recursive builder, graphs, programs,
optimization/context and text-package publication. The two actual React/native
RPF build cases and production compilation also passed. No test process remains
running from this checkpoint.

## User-requested discussion checkpoint

The user requested a pause at the next completed goal to discuss whether the
goals have been conservatively met. Implementation is paused after the completed
static, supported goal-2 workflow and the in-flight RPF verification above.
No runtime-packaging implementation was started. Resume only after the user's
discussion/direction; the overall three-goal objective has not been declared
complete. Goal 1's deeper assembled/coverage checks and goal 3's remaining
producer/packaged/runtime acceptance remain open, not silently waived.

## Scope-frozen stabilization and Git handoff (2026-09-06)

The user subsequently authorized stabilizing and pushing the existing work,
explicitly without adding features or areas of coverage. This supersedes the
pause for that bounded handoff only; it does not resume the remaining roadmap.

Verification of the handoff source:
- SDK Python: 2,970 passed, 81 skipped.
- React full suite and production frontend build: passed.
- Rust desktop library: 19 passed.
- Fresh native helper: 105 checks passed.
- Matching diagnostic-only Launcher tree: 2,571 passed, 8 skipped. Unrelated
  Launcher drafts were excluded from the commit and from this clean-tree test.
- Fresh frozen service: existing desktop/archive and ped smoke scripts passed.
- Fresh frozen validation, optimization, temporary installation, diagnostic
  comparison/export and exact recovery integration: passed.

The frozen diagnostic candidate was built from this implementation before this
documentation-only entry and the final Git commit; its embedded source identity
therefore identifies that precommit snapshot, not the eventual commit. It is
test evidence, not a published release or installer. No game files were changed.

These checks establish the supported offline workflows, not full OpenIV parity,
in-game appearance or crash-cause proof. Goal 1's deeper coverage and goal 3's
remaining producer/runtime acceptance stay open. No additional implementation
was started to close those areas during this handoff.
