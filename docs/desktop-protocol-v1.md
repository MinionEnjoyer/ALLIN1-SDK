# ALLIN1 desktop protocol v1

The desktop sidecar uses newline-delimited UTF-8 JSON over inherited stdio.
Stdout is reserved for protocol envelopes. Diagnostic logs go to stderr.

The normative envelope schema is
[`desktop-protocol-v1.schema.json`](desktop-protocol-v1.schema.json).

## Envelope

Every request and response includes:

- `protocol_version`: negotiated semantic protocol version
- `request_id`: caller-generated identifier, or `null` for process events
- `job_id`: job identifier, or `null` for non-job messages
- `operation`: typed operation name
- `payload`: JSON object
- `sequence`: `0` for ordinary messages; monotonic per job
- `risk`: `none`, `read_only`, `authoring_write`, `game_write`, or `unclassified`
- `terminal`: whether no later message belongs to this request/job

Requests are limited to 256 KiB. IDs are limited to 128 conservative ASCII
characters. Arguments are limited to 128 NUL-free strings. Unknown operations,
versions, fields, commands, and risks fail closed.

## Negotiation

The first message must be `handshake` and list the client's supported versions.
The sidecar selects exactly one supported version and returns its SDK version,
process capabilities, maximum request/output sizes, and whether game-write
or managed-package-write authority was granted at process startup. The Tauri
shell grants these separately and the WebView cannot change either flag.

## Requests

- `inspect_gxt2_workspace`: read-only, cancellable inspection of one absolute
  `source` (loose `.gxt2`), `workspace` folder, or `archive` (`.rpf`) with exact
  recursive `entry_id` and optional `gta_path`. These source forms are mutually
  exclusive. Archive reads re-index through RpfPatcher, reject missing, non-GXT2,
  oversized or incomplete members, and hash the outer archive before/after
  bounded extraction. Optional `query` (256 characters),
  nonnegative `offset` and `selected_hash` select a 100-row page and one exact
  label. Returns original/state SHA-256, revision, validation, recent history,
  row previews and full selected text. Labels above 16,384 characters are
  explicitly read-only with `text: null`, never editable truncated previews.
  `source_binding` is null for loose intake, or records `outer_archive`, its
  SHA-256, exact `entry_id`, edition and GTA context. For copied workspaces it
  is historical provenance, not a claim that the live archive still matches.
- `review_gxt2_action`: read-only, cancellable review for `create`, `edit`, `add`,
  `remove`, `undo`, `build`, `package_rpf` or `publish_rpf`. Requires `expected_state_sha256` from inspection,
  plus the exact label hash/text or new destination for the action. Returns
  before/after text, source/revision, output hash when building, and a digest of
  the review. Source dictionaries are bounded to 128 MiB; desktop workspaces to
  256 MiB and 1,000 history records. Unsupported/unsafe content is rejected.
- `apply_gxt2_action`: synchronous `authoring_write`, never a cancellable job.
  Requires the reviewed payload, `review_sha256` and literal
  `authoring_confirmed: true`. Python verifies state before and after acquiring
  a workspace operation lock, then delegates to `Gxt2Workspace`. Workspace and
  output paths must be outside GTA and free of symlink/reparse redirects.
  Build exclusively creates a new `.gxt2` and `.gxt2-validation.json`, reparses
  labels and verifies the reviewed hash. No existing output is overwritten.
  The dedicated native `desktop_apply_gxt2_action` command and
  `select_gxt2_build_destination` picker have explicit capability permissions.
  Original dictionaries, RPF archives, Launcher and game files are not written.
  Archive `create` reviews also bind the complete archive identity/hash and
  extracted dictionary hash. Apply rereads them and refuses changed sources or
  same-named members in a different layer. The copy and subsequent build report
  retain that binding; editing/building the copy no longer requires the original
  archive. `package_rpf` additionally requires the unchanged original archive
  and its GTA decoding context, saved text changes and a new destination folder.
  Its digest-bound `rpf_package` review lists the exact member, before/after
  dictionary sizes, source/payload/index hashes, verification count, required
  disk space and four relative output paths. The native `select_path` kind
  `rpf_package_parent` selects only the parent; existing output folders are refused.
  Python stages a private archive copy under that parent, retaining the original
  RPF basename for filename-dependent keys, and delegates replacement to the
  existing transactional RPF writer with authority limited to the temporary
  workspace. It verifies structure, reparses the changed dictionary and compares
  every unrelated payload before exclusive Windows directory publication.
  Ancestor archive containers are excluded from byte comparisons because their
  child changed; resource comparisons normalize RSC7 recompression. The original
  archive and workspace state must still match, and GTA must remain closed.
  Limits are 16 GiB per archive/total verification bytes, 512 MiB per verification
  member, 25,000 indexed entries and the existing eight-layer nesting guard.
  Output contains `archive/<original-name>.rpf`, `payload/replacement.gxt2`, its
  validation report and `rpf-package.json` with hashes and per-payload evidence.
  This is not an installable ALLIN1 package: no installer manifest, upload,
  live archive replacement or general multi-entry change-set editor is exposed.
  A separate `publish_rpf` action wraps an already verified build into an
  installable ALLIN1 ZIP. It takes `source_package`, a new `.zip` `destination`,
  optional `publication_mode` (`whole_archive`, the default, or `member`), and
  `package_metadata` containing exactly `id`, `name`, `version`, `author`
  and `target`. Other actions reject `publication_mode`. The target is an explicit GTA-relative path under `mods/`,
  preserving the original RPF basename. It is never guessed from a loose
  archive filename. The `rpf_publication` review binds metadata, saved workspace,
  source build/report hashes, single source edition, generated `mod.toml`,
  exact ZIP members and disk requirements. It also binds `publication_mode`,
  `manifest_schema_version`, `entry`, `original_sha256` and `payload_sha256`;
  changing scope requires a new review and confirmation. In whole-archive mode,
  the UI separately acknowledges the
  whole-archive overwrite risk; installation may replace unrelated target edits.
  The schema-1 manifest has one checksum-bound `[[files]]` RPF, `openrpf`
  dependency and no DLC registrations. ZIP contents are exactly `mod.toml`,
  `payload/<original-name>.rpf`, portable `allin1.rpf-build.json` and `README.txt`.
  Member mode emits schema 3 for an outer-archive dictionary or schema 4 for a
  nested dictionary, with one `[[rpf_entries]]` replacement. It ships
  `payload/replacement.gxt2`, never a containing RPF. Schema 4 uses an explicit
  `!`-separated chain (1–8 archive layers, then the exact file); container targets,
  implicit traversal and missing members are refused. The recorded original dictionary SHA-256 is an
  installation precondition, distinct from the replacement payload SHA-256.
  Review explicitly warns that older Launchers reject the selected schema. No downgrade or
  whole-archive fallback is automatic. The export still verifies the complete
  source build and retains the existing publication limits in both modes.
  Local workspace/GTA paths are not copied into generated export evidence.
  Publication streams a new ZIP, rehashes each member, validates it through
  `open_mod_package`, rechecks workspace/build state and exclusively renames
  the staged file on Windows. It follows the package loader's 4-GiB member
  limit. The original archive/GTA need not be available for ZIP publication;
  no native RPF mutation occurs at this stage. Read-only review is cancellable;
  apply remains non-cancellable `authoring_write`. Export neither installs nor
  uploads, modifies the verified build, adds GBAY data, or registers a new DLC.
  Native exact-member lookup now traverses the selected archive's directory
  tree; it does not accept suffix matches or implicitly cross a nested RPF.
  The shared SDK/Launcher managed-member services use `extract-exact-entry`
  for root backup/verification and `extract-exact-nested-entry` for explicit chains.
  They never fall back to legacy basename extraction.
  Schema-3/4 installation checks every original before preparing game writes,
  rechecks captured backups before replacement, and records hashes for restore
  and enable/disable cache validation. The matching Launcher source supports
  this contract; a compatible Launcher release is still required for recipients.
  See the [root contract](rpf-member-package-v3.md) and
  [nested contract and native bounds](rpf-member-package-v4.md). General orphan-lock
  recovery, graph/program editors and large/encrypted archive certification remain.
- `list_rpf_transactions`: read-only, cancellable, empty payload. Scans only the
  fixed per-user receipt root, at most 256 folders, with 2-MiB receipt limits.
  Returns recorded summaries (not integrity verification), malformed-row errors
  and explicit truncation. The receipt picker can open unlisted transactions.
- `inspect_rpf_transaction`: read-only, cancellable inspection of an absolute
  multi-entry plan or `receipt.json`, with optional `gta_path` decoding context.
  Returns document/current-archive hashes, exact ordered changes, recorded scope,
  receipt status and backup metadata. Receipt inspection uses the existing native
  verifier to report applied/original/external state, backup validity and member
  validity and bounded lock owner/digest evidence. A missing backup is visible but
  cannot authorize rollback.
- `review_rpf_transaction`: read-only, cancellable review for `execute`, `rollback`,
  `recover` or `clear_lock`. Requires `source`, `expected_sha256` and explicit `authorized_root`
  exactly matching the external archive's direct parent and recorded workspace
  scope for external copies. Mods-copy transactions instead require an explicit
  matching `gta_path` and no external root. Stock, cross-game and redirected paths
  are blocked, including at final commit. Execution re-indexes
  and recomputes the existing multi-entry plan, checking current original/payload
  evidence and disk space. Rollback requires a recoverable receipt status, unchanged
  applied archive, verified members and verified backup. The review digest binds
  the request, full session, selected scope, backup location and original restore hash.
- `apply_rpf_transaction`: synchronous, never a cancellable job. External writes
  and receipt recovery are `authoring_write`; live apply/rollback and mods-folder
  lock cleanup are `game_write` (even though cleanup does not rewrite an archive).
  Requires the reviewed request plus `review_sha256` and literal boolean
  `archive_write_confirmed: true`. Repeats review and closed-game checks, then calls
  the existing transactional apply/rollback domain with a raw-document SHA guard
  and final path/document checks. The domain rechecks the archive hash before commit;
  staging-time external edits are not overwritten. New receipts/backups use the
  fixed per-user SDK transaction root. Receipt-backed restore retains that folder.
  Live writes additionally require literal `game_write_confirmed: true` and the
  native owner's dedicated `--allow-rpf-writes` flag; handshake reports
  `rpf_writes_enabled`. General console `--allow-game-writes` stays disabled and
  does not substitute for this authority. Outcomes truthfully report archive/game
  write effects and verified receipt state; successes and failures are audited.
  `recover` instead requires `receipt_write_confirmed: true`, no archive or game
  confirmation. It reconciles an unsettled receipt to verified applied/original
  state, guarded by document/archive/backup/lock evidence and a closed-game check.
  Active lock owners block it. Stale locks are retained. Recovery returns both
  archive/game write flags false and `receipt_write_performed: true`.
  `clear_lock` instead requires `lock_clear_confirmed: true`, a settled receipt
  whose verified archive state matches its status, a healthy backup and a matching
  exited lock owner. Mods-folder cleanup additionally requires the native RPF flag
  and `game_write_confirmed: true`. Lock evidence adds `plan_id`, `created_at`,
  file `identity` and `cleanup_supported`. Reviews bind `lock_write_required` and
  `lock_evidence` (`path`, `sha256`, nullable `existing_sha256`). Cleanup is restricted
  to local Windows volumes; exclusive file handles prevent replacement during
  validation, retention and deletion. The original bytes are retained beside the
  receipt before deleting the held file. The outcome reports
  `archive_write_performed: false`, `receipt_write_performed: false`,
  `lock_write_performed: true` and the reviewed `lock_evidence`. All other actions
  report `lock_write_performed: false` and null `lock_evidence`. Audit records retain
  these effects. Errors never authorize automatic retries or backup cleanup.
  Desktop bounds: 128 changes, 2-MiB document, 16-GiB archive/backup, 512-MiB payload,
  1-GiB total payload, 25,000 entries for execution review and 1-MiB evidence.
- `inspect_rpf_change_set`: read-only, cancellable inspection of an absolute
  `change_set` JSON path. Returns the source archive identity, ordered typed
  actions and raw-document SHA-256. Opening does not hash-verify every payload;
  `files_verified: false` makes that distinction explicit. Missing payloads can
  remain visible so their staged action can be removed.
- `review_rpf_change_set`: read-only, cancellable review for `create`, `stage`,
  `remove`, `move`, or `compile`. Existing documents require `change_set` and
  `expected_sha256`. Stage accepts only `action`, `archive_path`, `entry`, and
  action-specific `payload`/`new_entry`; file add/replace/delete/rename and
  directory mkdir/rmdir are supported. Remove/move identify an exact `action_id`;
  move uses an existing one-based `position`. Create requires an archive and new
  JSON destination; compile requires a new JSON destination. Both accept a GTA
  decoding context. Compile optionally accepts `authorized_root`, restricted to
  the external archive's direct parent folder, outside GTA. No scope is silently
  granted. Unsafe-scope plans remain blocked but can be exported for inspection.
  The existing Python planner re-indexes the root/nested targets and verifies
  source/payload hashes, original members, tree conflicts and action compatibility.
  Review binds the full request, before/after order, source/document/payload
  evidence, destination, scope and compiled plan (excluding its timestamp).
- `apply_rpf_change_set`: synchronous `authoring_write`, never a job. Requires
  the reviewed request, `review_sha256` and literal `authoring_confirmed: true`.
  It repeats the review, then delegates to guarded shared Python create/commit/
  compile methods with final document/source/plan checks. Only an inert change-set
  document or new plan JSON is saved; archives and GTA are never modified.
  Outputs and change-set documents must stay outside GTA. Redirected source,
  payload and destination paths are refused. New outputs cannot replace files.
  Desktop limits: 128 actions, 2-MiB document, 16-GiB archive, 512-MiB individual
  payload, 1-GiB total payload and 1-MiB review evidence; creation additionally
  bounds its index to 25,000 entries. Dedicated native apply/save-picker commands
  have explicit capabilities. React guards drafts, source changes and navigation,
  rejects stale/mismatched evidence, restores review focus and requires a fresh
  confirmation after failure. There is no automatic archive execution or rollback.
- `inspect_ped_workbench`: inspects a folder/supported package archive/direct RPF
  or opens an existing ped workspace. `source` and `workspace` are exclusive;
  optional `gta_path` supplies read-only decoder context. `ped`,
  `metadata_source` and `record_index` retain a specific catalog definition,
  including conflicting definitions. Returns metadata values, existing editable
  fields, exact/name-candidate asset evidence, independent presence/declaration
  findings, revision and a copied-content/manifest digest. Duplicate identities
  remain inspectable but are not editable. Direct RPF sources must be extracted
  before authoring. No mounting, YMT capacity or runtime-acceptance conclusion.
- `inspect_ped_ymt`: cancellable read-only inventory for a loose binary YMT,
  exported `*.ymt.xml`, folder, supported package archive, or direct RPF.
  `edition` is required as decoder context; `gta_path` is required when RPF
  indexing cannot use an already supplied installation. The result follows
  `sdk/ped-ymt-report.schema.json`, keeps archive/decoding/dependency/runtime/
  acceptance evidence independent, and never mounts metadata or writes GTA.
- `review_ped_authoring`: cancellable read-only review for `create`, `edit`,
  `migrate`, `clone` or `undo`. Create binds source hashes and a new outside-GTA
  destination. Other actions require the inspected revision and state SHA-256.
  Reviews include exact changes/renames or complete clone additions, sources,
  source hashes and blockers. Oversized consent evidence is refused, not silently
  truncated. The clone plan requires target drawable/texture/props assets and
  never relabels donor native bytes.
- `apply_ped_authoring`: synchronous authoring-only native command, excluded
  from worker jobs. Requires `review_sha256` and fresh
  `authoring_confirmed: true`; reuses the existing PedAuthoringWorkspace
  transactions, validation and verified history. State is rechecked inside the
  workspace lock. Undo restores the original identity/donor selection. `preview_asset`
  renders ped model and texture evidence separately with exact nested identity
  preserved; only the decoder temporary filename is a leaf. No GTA writes.
- `inspect_weapon_workbench`: reads an unpacked weapon folder or opens a copied
  weapon workspace. Returns weapon/ammo values, existing editable-node names,
  shared-ammo users, attachment evidence, findings, revision, and undo state.
  Optional `editor_kind` selects `weapon` (default), `component`, `attachment`,
  `shop`, or `animation`;
  components require a bounded `component` identity and attachment links require
  both `weapon` and `component`. Relationship responses include existing editable
  nodes, component type/attachment-point locks, affected users, and other defaults.
  Shop inspection returns `shop_sources`, `shop_values`, and existing editable
  nodes; `metadata_source` selects an exact discovered relative path. Multiple
  shop sources are left unselected until one is chosen. Animation set coverage
  is returned in the project's source-aware `animation_records`.
  Source inspection and `review_weapon_authoring` are read-only cancellable jobs.
- `review_weapon_authoring`: reviews `create`, `edit`, `edit_component`,
  `edit_attachment`, `edit_shop`, `clone_animation`, `clone`, or `undo`. Creation binds
  the source contents and new outside-source/outside-game destination. Edits bind
  normalized fields, shared-ammo acknowledgement, revision, and full content
  hashes. Component edits also require acknowledgement when used by multiple
  weapons. Attachment edits only change an existing `attachment.default` string
  boolean and reject conflicting defaults; they cannot synthesize nodes or
  reparent links. Reviews bind the exact component or weapon/component pair.
  `edit_shop` uses `weapon`, `metadata_source`, `updates`, and `expected_revision`
  to review existing GTA shop prices, text keys, and availability; it does not
  edit GBAY catalogs. `clone_animation` uses `weapon`, `template_weapon`,
  `metadata_source`, and `expected_revision`; every complete per-set mapping in
  the chosen source is reviewed. Existing target mappings and duplicate donor
  mappings are refused. Both actions retain exact source selection through undo
  and reject oversized review evidence before confirmation.
  Clone requires a copied workspace, `expected_revision`, and an exact `spec`:
  `donor_weapon`, `weapon_name`, `slot`, `model`, `human_name_hash`, `stat_name`,
  `ammo_info`, boolean `clone_ammo`, and `ammo_name` (the same ammo identity when
  cloning, otherwise null). Identifiers are bounded strings. The existing domain
  returns a content-bound `clone_plan` with donor completeness, exact source
  evidence, reused components, additions, collisions, findings, and readiness.
  Blocked plans are inspectable but cannot be applied. No assets or localization
  entries are generated. Undo binds the latest verified history and current
  source state; clone undo lists `removed_records` and reselects its donor.
  Clone/undo evidence that would exceed protocol display bounds is refused
  before confirmation instead of silently truncating the reviewed bundle.
- `apply_weapon_authoring`: a dedicated, synchronous native command requiring
  the review SHA-256 plus `authoring_confirmed: true`. It regenerates the review,
  then uses the existing Python copied-workspace/transaction/undo implementation;
  edits, clone plans, and undo recheck state under the workspace lock. Clones use
  `clone_weapon_bundle` with the regenerated plan digest and revision, then select
  the new weapon. It never writes game
  files, cannot run as a cancellable job, and rejects stale same-size file edits.
  Opaque archives, native weapon previews, and weapon publication
  remain outside this desktop slice.
- `catalog`: returns the existing Agent API command schemas/risk classes,
  workspace navigation, help topics, and supported desktop operations.
- `execute`: runs one command through `agent_api.execute_request`. It never uses
  a shell and retains all Agent/CLI safety gates. `authoring_write` commands
  additionally require the desktop to send an action-time confirmation flag.
- `inspect_package`: reads a manifest/product workspace or scans a package and
  returns a bounded summary with no package-controlled HTML.
- `preview_asset`: revalidates and reads one exact inventory member through
  `PackageAssetReader`. It returns bounded text/metadata or a normalized,
  hash-named PNG beneath the broker-owned preview cache.
- `render_vehicle_model`: synchronously revalidates one exact YFT/YDR/YDD
  package member, reuses a two-scene digest/edition/decoder-context cache, and
  optionally revalidates its linked YTD, and renders a hash-named PNG for a
  bounded yaw, pitch, LOD, component, exact material surface, render mode, and
  interactive/final quality request. The operation is read-only and returns
  scene/component/material/bone evidence, sampler-slot resolution, exact
  CodeWalker Vector4/Vector4-array shader constants, a bounded
  texture catalog/contact sheet, optional bounded UV0 texture sampling for the
  diagnostic `textured` mode, and explicit no-write flags. Textured frames use
  decoded preview pixels and never claim full game-shader fidelity. The bounded
  `uvs` mode classifies the rendered triangle sample as texture-resolved,
  UV-only, degenerate, or missing UV0 and returns the exact class counts.
  It stays in the persistent sidecar so React orbit gestures do not repeat the
  CodeWalker decode for every camera frame.
- `inspect_model_materials`: validates one loose YDR/YDD/YFT, delegates native
  decoding and shader/geometry assignment inspection to the existing Python
  model-material service, and returns bounded material, texture-slot, LOD,
  component, geometry, and finding evidence. Same-stem sibling YTD/YBN files
  are linked as read-only viewport inputs; no workspace, package, or GTA file
  is written.
- `review_model_material_workspace`: validates an exact loose model, target
  edition, optional GTA toolchain context, and a new destination directory,
  then binds those inputs to a deterministic SHA-256 review without writing.
  `create_model_material_workspace` regenerates the review, requires action-time
  confirmation, and delegates creation to the existing guarded native/material
  workspace service. The original model and GTA V remain immutable.
- `inspect_model_material_workspace`: opens an existing verified material
  workspace and returns its exact revision, shader/texture/geometry evidence,
  undo availability, immutable source snapshot, and explicit no-write flags.
  `review_model_material_edit` validates either existing shader-name/texture-slot
  changes or one geometry assignment within its local shader group, returns an
  exact before/after diff and digest, and performs no write.
- `apply_model_material_edit`: synchronously regenerates the exact-revision
  review, rejects digest drift, requires action-time confirmation, and commits
  only existing XML nodes through `MaterialAuthoringWorkspace`. The separate
  `apply_model_material_history` operation restores the latest verified history
  snapshot as a new revision; neither operation writes a package or GTA V.
- `review_model_material_build`: binds the exact workspace revision, native
  compiler availability, original model extension, a new outside-workspace and
  outside-GTA destination, and the companion validation receipt to one
  deterministic digest without writing either file. `apply_model_material_build`
  repeats that review synchronously, requires action-time confirmation, compiles
  through `RpfPatcher`, decodes the result again, and publishes the output and
  SHA-256 evidence receipt as a pair. React receives the reparsed project so it
  can render source and compiled output side by side.
- `review_texture_workspace`: validates one loose YTD, target edition, optional
  GTA toolchain context, and a new outside-game workspace destination. The
  deterministic review writes nothing. `create_texture_workspace` regenerates
  the review, requires action-time confirmation, and decodes the immutable YTD
  snapshot into the existing native workspace layout.
- `inspect_texture_workspace`: opens one manifest-backed YTD workspace, verifies
  the immutable source identity, XML, DDS dependency paths and headers, and
  returns its texture catalog, exact state digest, revision, warnings, and undo
  availability. `preview_texture_workspace` renders one exact dependency into a
  bounded hash-named PNG artifact and never changes the workspace.
- `review_texture_edit`: binds an add, replace, or remove action to the current
  workspace digest. DDS inputs are header-validated; PNG/JPEG/BMP/TGA/WebP
  inputs are decoded under dimension/pixel limits and explicitly reviewed as
  one-mip uncompressed RGBA conversion. `apply_texture_edit` repeats that
  inspection, rejects source or state drift, requires confirmation, and commits
  one revision. `apply_texture_history` restores the latest verified snapshot
  only when the exact state digest and confirmation are supplied.
- `review_texture_build`: binds the workspace digest, native compiler, new YTD
  destination outside the workspace/GTA V, and companion receipt without
  writing. `apply_texture_build` repeats the review, compiles through
  `RpfPatcher`, reparses the output, and publishes only when the receipt proves
  an exact output hash and semantic XML match. Failed semantic verification
  removes the newly created output/receipt pair.
- `assistant_status`: reads the Launcher-owned assistant configuration without
  starting a model runtime. Missing or disabled configuration is returned as a
  normal typed status so the console can explain the next action.
- `assistant_prompt`: runs one grounded, structured, advisory-only Qwen or
  compatible-provider request in the isolated read-only worker. Large private
  grounding context is removed from the WebView result; recommendations remain
  unexecuted, and cancellation terminates the worker process tree including a
  spawned local runtime.
- `inspect_rpf_archive`: validates one loose `.rpf`, resolves or validates its
  GTA installation, and invokes the existing `RpfExplorerService` to return a
  bounded recursive archive/entry index, storage totals, suffix counts, and
  decoder warnings. It extracts nothing and never writes the archive or GTA V.
- `inspect_vehicle_project`: validates a vehicle package, archive, folder, or
  direct RPF plus optional GTA context, then delegates to the existing
  `VehicleProjectResolver`. It returns bounded model identity, linked package
  assets, model/project findings, axle-configuration evidence, and explicit
  no-write flags. Inspection does not create an editable revision or package.
- `review_vehicle_authoring_workspace`: re-inspects a visible vehicle source,
  validates a new destination, and returns a deterministic copy review digest.
  It performs no write. `create_vehicle_authoring_workspace` regenerates that
  review, rejects digest drift, requires action-time confirmation, and delegates
  copied-workspace creation to `VehicleAuthoringWorkspace` without touching the
  original source or GTA V.
- `inspect_vehicle_authoring_workspace`: opens an existing copied workspace and
  returns its revision, selected model, complete flat editable-field contract,
  current values/sources, undo/redo availability, and nested project evidence.
- `review_vehicle_authoring_edit`: validates and normalizes 1–64 bounded field
  changes against an exact workspace revision without writing. The returned
  before/after diff and digest are required by `apply_vehicle_authoring_edit`,
  which re-reviews synchronously before committing one transactional revision.
- `review_vehicle_authoring_appearance`: validates bounded color presets, linked
  tuning-kit identifiers, and light/siren references against an exact workspace
  revision without writing. `apply_vehicle_authoring_appearance` requires the
  deterministic review digest plus action-time confirmation, re-reviews for
  drift, and commits the structured variation data through the same transaction
  and history service as other vehicle edits.
- `inspect_vehicle_authoring_tuning`: resolves one kit into typed entry
  collections, field schemas, linked stream assets, and validation findings.
  `review_vehicle_authoring_tuning` rebuilds kit metadata or one add, duplicate,
  update, remove, or move action in memory without writing. The corresponding
  apply call requires the exact revision, deterministic digest, and action-time
  confirmation before committing one transactional revision.
- `review_vehicle_authoring_light_profile`: resolves one carcols light profile
  and returns normalized scalar changes without writing. Its guarded apply call
  regenerates the review, rejects digest drift, and commits only the copied
  authoring workspace.
- `review_vehicle_authoring_axles`: parses and validates one complete physical
  axle configuration against an exact workspace revision, calculates the
  corresponding handling-flag and drive-bias changes, and returns a
  deterministic no-write review. `apply_vehicle_authoring_axles` regenerates
  that review, requires action-time confirmation, and commits the configuration
  and package-owned handling metadata as one transactional revision. Signed
  steering and custom physical-order evidence remain skeleton-gated.
- `inspect_vehicle_authoring_axle_skeleton`: opens one bounded native-model XML
  export, extracts canonical wheel-bone evidence through the existing native
  model reader, and provides no-write detection, validation, signed-steering,
  and intentional physical-order proposals. The client cannot mint either
  evidence signature locally.
- `review_vehicle_authoring_transmission`: validates a bounded transmission
  type, reverse/final-drive values, and 1–16 positive forward ratios against an
  exact workspace revision without writing. Its guarded apply call regenerates
  the review, rejects digest drift, saves the profile in revisioned ALLIN1
  authoring metadata, and synchronizes the stock `nInitialDriveGears` field.
  Stock `handling.meta` has no per-gear ratio array, so individual ratios are
  deliberately not represented as native handling fields.
- `review_vehicle_authoring_distribution`: validates bounded GBAY catalog and
  ambient-traffic settings against an exact workspace revision without writing.
  Its guarded apply call regenerates the deterministic review, rejects drift,
  requires action-time confirmation, and commits the distribution record as a
  normal transactional revision with undo/redo support.
- `review_vehicle_package_build`: re-inspects the exact authoring revision,
  resolved DLC source, listed distribution catalog, ALLIN1 axle/transmission
  profiles, selected editions, and a new output folder outside the workspace
  and GTA V. `apply_vehicle_package_build` repeats that review synchronously,
  requires the matching digest and action-time confirmation, and atomically
  creates only the managed package. It does not install the output or write GTA
  V. Runtime-only axle/transmission profiles are preserved in
  `payload/vehicle-profiles.json` with explicit integration warnings.
- `apply_vehicle_authoring_history`: requires an exact revision, explicit undo
  or redo direction, and action-time confirmation before delegating to the
  existing transactional history service. Vehicle authoring mutations are
  synchronous and are never accepted as cancellable jobs.
- `inspect_recipe`: parses one OIV/ZIP package or unpacked recipe with the
  existing `OivWorkbench`, returning bounded ordered operations, findings,
  readiness gates, and recipe identity without executing package instructions.
- `inspect_package_receipts`: validates one GTA V installation through the
  existing `ModIntegrationService`, lists its managed receipts, and optionally
  returns one receipt with live file, backup, hash, and RPF-entry ownership
  checks. It is read-only and never changes package state or GTA V.
- `review_package_lifecycle`: performs a read-only install/update, uninstall,
  enable, or disable
  preflight through `ModIntegrationService`. Install reviews validate the
  package payload, edition, loaders, content requirements, conflicts,
  destination types, replacement ownership, and backup scope. Uninstall
  reviews validate receipt ownership, dependents, backup hashes, disabled-file
  layering, RPF evidence, and rollback scope. Enable/disable reviews add the
  current and target state, dependent/requirement gates, destination vacancy,
  loose-file moves, RPF state changes, and DLC registration scope. The
  deterministic review digest grants no write authority and every action must
  revalidate current state.
- `apply_package_lifecycle`: synchronously regenerates the install/update,
  uninstall, enable, or disable review from current package and game state,
  rejects review-digest
  drift, requires native process-owner authority plus a matching package id and
  action-time confirmation, and fails closed unless GTA V process inspection
  proves the game is stopped. It then delegates to the transactional Python
  package service and returns post-write ownership and rollback evidence. It is
  intentionally not a cancellable job.
- `inspect_vehicle_quick_import`: performs the existing bounded vehicle-package
  scan and returns detected editions and vehicle identity evidence. It never
  prepares a Launcher package and never writes GTA V.
- `review_vehicle_quick_import`: regenerates one selected edition's conversion
  plan, applies bounded in-memory listing edits through the existing Python
  catalog validator, and returns the canonical draft, destination state, and
  deterministic review digest. It does not create, replace, publish, or export
  package files and never writes GTA V.
- `prepare_vehicle_quick_import`: synchronously regenerates the reviewed draft
  and destination state, rejects digest drift, requires explicit authoring and
  replacement confirmation, and then delegates atomic preparation to the Python
  service. It writes only the Launcher package library and never GTA V.
- `check_update`: uses the existing checksum-oriented release discovery service
  and returns release metadata only; it does not download or install.
- `start_job`: starts one read-only `execute`, `inspect_package`,
  `preview_asset`, `inspect_model_materials`,
  `inspect_model_material_workspace`, `review_model_material_workspace`,
  `review_model_material_edit`, `review_model_material_build`,
  `inspect_texture_workspace`, `review_texture_workspace`,
  `preview_texture_workspace`, `review_texture_edit`, `review_texture_build`,
  `assistant_status`, `assistant_prompt`,
  `inspect_rpf_archive`, `inspect_rpf_change_set`, `review_rpf_change_set`,
  `list_rpf_transactions`, `inspect_rpf_transaction`, `review_rpf_transaction`, `inspect_vehicle_project`,
  `inspect_vehicle_authoring_workspace`, `review_vehicle_authoring_workspace`,
  `review_vehicle_authoring_edit`, `review_vehicle_authoring_appearance`,
  `inspect_vehicle_authoring_tuning`, `review_vehicle_authoring_tuning`,
  `review_vehicle_authoring_light_profile`,
  `review_vehicle_authoring_axles`,
  `inspect_vehicle_authoring_axle_skeleton`,
  `review_vehicle_authoring_transmission`,
  `review_vehicle_authoring_distribution`,
  `review_vehicle_package_build`,
  `inspect_recipe`,
  `inspect_package_receipts`,
  `review_package_lifecycle`,
  `inspect_vehicle_quick_import`,
  `review_vehicle_quick_import`, `review_vehicle_oiv_export`, or `check_update` request in an isolated worker.
  A second heavy job is rejected.
- `cancel_job`: terminates the matching read-only worker and emits one terminal
  `job_event`. Mutation jobs are not accepted by v1.
- `shutdown`: rejects new work, stops the active read-only job, flushes one
  terminal response, and exits.

`job_event`, `result`, and `error` are sidecar-to-broker operations and are
rejected when received from a client.

## Jobs and stale results

### Legacy vehicle OIV export

`review_vehicle_oiv_export` is a read-only, cancellable operation. Its exact
payload is `source`, optional `gta_path`, `edition: "legacy"`, optional
`package_id`/`name`/`version`, `author`, and an absolute new `.oiv` destination.
It reinspects the Legacy branch without resolving or preparing a Launcher
library package. Game detection or a selected GTA directory is still needed by
the existing Quick Import inspection service. Enhanced branches are rejected,
not converted. GBAY/traffic `updates` and unknown payload keys are rejected.

The review binds the canonical source, complete plan digest, author, output
path, identity and exact payload size/SHA-256 to `review_sha256`. It lists the
two archive members and explicitly excludes GBAY catalogs, traffic preferences,
ALLIN1 receipts, backups and rollback. Review performs no export writes.

`apply_vehicle_oiv_export` is a synchronous `authoring_write`, never a job. It
requires the same payload plus `review_sha256` and literal boolean
`authoring_confirmed: true`. It repeats inspection, rejects any changed review,
then uses `LegacyVehicleOivExporter.export_plan`. Original source/destination
paths cannot use symlinks/reparse points; output must be outside both source
and game directories with an existing parent and a safe filename. No existing
file is replaced. The existing exporter checks the payload again, verifies the
archive, exclusively claims the new destination, and returns archive SHA-256
and `game_write_performed: false`. The React owner guards routes, source and
edition while settings/review/write are active, ignores late cancelled results,
and requires a fresh review/confirmation after any write failure. Browser
fixtures cannot perform writes.

### Shared job lifecycle

`configure_assistant` is a direct `authoring_write` operation, never a job. It
requires `authoring_confirmed: true` and a bounded `settings` object. Only the
fixed per-user SDK assistant configuration is written, atomically. The caller
cannot select the destination, store a raw API key, download a model, start a
runtime, or change Launcher/game files through this operation. The result records
`settings_write_performed: true`, `launcher_write_performed: false`,
`game_write_performed: false`, and `runtime_started: false`.

A job begins with sequence `0` and `state: accepted`, followed by ordered
status/progress messages. `result`, `error`, or a cancelled `job_event` is
terminal. Payloads echo the caller's opaque `revision`. The React owner must
discard messages whose `(job_id, revision)` does not match its current view.

Interrupted work is never replayed automatically. After a sidecar crash, all
pending requests fail. The Rust broker may start a fresh sidecar only for a new
user request.

## Example

```json
{"protocol_version":"1.0.0","request_id":"boot-1","job_id":null,"operation":"handshake","payload":{"client":{"name":"ALLIN1 Tauri","version":"0.1.0"},"supported_versions":["1.0.0"]},"sequence":0,"risk":"none","terminal":false}
{"protocol_version":"1.0.0","request_id":"boot-1","job_id":null,"operation":"result","payload":{"negotiated_version":"1.0.0","service":"ALLIN1 SDK Desktop Sidecar"},"sequence":0,"risk":"none","terminal":true}
```


## Shared offline authoring workspaces — schema 1

The three operations `inspect_authoring_workspace`, `review_workspace_action`,
and `apply_workspace_action` provide one lifecycle for binary, maps, graph,
program, runtime, render, recipe, and vehicle-identity modules. Inspection and
review are cancellable read jobs. Apply is synchronous, re-runs the complete
review before and after taking a per-target process/thread lock, and requires the
exact `review_sha256` plus literal `authoring_confirmed: true`.

All modules return `game_write_performed: false`. Inputs and manifests must use
absolute, normalized, link-free paths; destinations must be new, outside GTA,
and separated from sources. Reviews bind the complete input state, dependencies,
tool identities, outputs, and operation-specific document. Responses remain
within the protocol's depth/string/list bounds and convert tuples to JSON arrays.

- binary: copied immutable original, exact-byte edits, undo, and verified output;
- maps: create/save plus validated native package output;
- graph/program: folder/RPF/package import, source-bound documents, sealed
  expansion, semantic relationships, materialize/build/preview/origin plans and
  typed flow plans/execution;
- runtime: CMake/MSVC/CTest preflight and candidate compilation, never activation;
- render: native decode into an SDK-owned cache and separate reviewed PNG export;
- recipe: managed exports, complete nested batches, new-RPF packages and inert
  XML/text/PSO bundles; OIV instructions are parsed, never executed;
- vehicle_identity: a copied vehicle workspace only, with every metadata update
  and streamed asset rename displayed before transactional migration.

This contract does not turn offline evidence into installed-WebView or live-game
acceptance. Runtime and render receipts state that boundary explicitly.
