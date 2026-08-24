"""Searchable, task-oriented help for the ALLIN1 desktop application."""

from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk

from allin1_sdk.ui_foundation import place_window


@dataclass(frozen=True)
class HelpTopic:
    """One concise help-center article."""

    key: str
    category: str
    title: str
    summary: str
    body: str
    keywords: tuple[str, ...] = ()


HELP_TOPICS: tuple[HelpTopic, ...] = (
    HelpTopic(
        "getting-started", "Start here", "Getting started",
        "Import or audit a package, resolve its links, and export a reviewable report.",
        """1. Use Import or audit package to open an addon.json or scan a DLC folder/archive.
2. Select a package and inspect every node in the Package Linker graph.
3. Resolve missing fields, source files, references, edition tags, and rollback steps.
4. Export the link report and keep it with the package's release artifacts.

The SDK audits and authors content. Its RPF workspace applies reviewed file and directory-tree plans to an exact mods copy or an explicitly isolated external workspace, including recursively nested entries up to eight archive levels. Full package installation, repair, and game launch remain the responsibility of the separate ALLIN1 Launcher.""",
        ("first run", "import", "addon", "linker", "report"),
    ),
    HelpTopic(
        "editions", "Start here", "Legacy and Enhanced installations",
        "Select the matching game build whenever native formats or encryption keys matter.",
        """Legacy and Enhanced can coexist on one PC. RPF Archives asks for the matching installation so it can use the correct archive keys and resource decoder.

Package scans label declared and inferred compatibility but never silently convert assets between editions. Treat an unresolved edition as a release blocker until it is tested against a specific build.""",
        ("gen9", "path", "target", "compatibility"),
    ),
    HelpTopic(
        "install-repair", "Environment", "SDK toolchain setup",
        "Prepare Python, CodeWalker, and the RPF helper.",
        """Install the Python package in an isolated environment, initialize the pinned CodeWalker submodule, and run runtools.ps1 to publish RpfPatcher.

The native viewers remain useful without the helper for lightweight header inspection. Recursive RPF indexing and structured Rockstar-resource XML require the helper. The SDK does not install or repair the gameplay client.""",
        ("python", "codewalker", "rpfpatcher", "dependencies", "build"),
    ),
    HelpTopic(
        "gameplay", "Automation", "Command-line workflows",
        "Run validation and inspection in scripts or continuous integration.",
        """The allin1-sdk command exposes package import, manifest validation, link reports, OIV plans, DLC inventory, vehicle compilation, and RPF indexing, exact-entry extraction, verified directory-subtree export, and archive diffing.

Commands return non-zero exit codes for invalid or unsafe inputs. Creating a single or multi-entry change plan never authorizes a write. plan-rpf-batch accepts a JSON changes list with add, replace, delete, mkdir, rmdir, rename, or upsert actions and produces one guarded plan for up to 1,000 root or deeply nested operations. plan-rpf-sync converts edits in a verified subtree workspace—including folder changes—into that same atomic format. Applying or rolling back a ready plan is a separate command and requires --acknowledge-write, a closed game, an authorized target, and unchanged input hashes. Real-archive canary mode proves replace/add/delete and rollback on a disposable copy.""",
        ("cli", "automation", "continuous integration", "script", "exit code"),
    ),
    HelpTopic(
        "console", "Automation", "SDK Console",
        "Use the complete command surface without leaving the desktop SDK.",
        """Use the prompt docked beneath every workspace, or press Ctrl+` to focus and expand it. Start typing to progressively filter commands. Suggestions include command syntax, options, local paths, and persistent command history.

The prompt stays docked at the bottom of every workspace. Ctrl+backtick focuses and expands it; Expand reveals output, history, and the complete suggestion table without leaving the current tool. Tab accepts the selected suggestion. Up and Down move through visible matches; Ctrl+Up and Ctrl+Down move through history. Enter runs the command asynchronously, Ctrl+L clears output, and Escape clears the command or collapses the dock.

The console invokes the same Click commands and safety checks as allin1-sdk in a terminal. It does not bypass target authorization, acknowledgements, hashes, locks, game-process checks, or rollback requirements. Type help for the catalog or help <command> for detailed syntax.

If the optional assistant was configured in the ALLIN1 Launcher's SDK Manager, type assistant prompt followed by a question to ask the configured Qwen/GGUF model. Quotes are optional for ordinary multi-word questions. assistant context followed by the question shows the repository, manifest, game-path, policy, selected source or telemetry evidence, and exact command evidence that would be supplied without starting the model. Use --source with --symbol for complete brace-balanced function definitions plus automatically retrieved counter writers/reset sites. Use --telemetry with --telemetry-pattern for bounded log excerpts and whole-file session aggregates, so an idle final line cannot hide earlier activity. The host budgets context before model startup, preserves explicitly requested definitions, reports every omission instead of silently truncating a large repository, and prints startup, prefill, generation, and heartbeat progress. Responses use a structured advisory format with separate engineering/security severity. The model may propose a conceptual change to a grounded file and symbol, but the host always marks it advisory-only and unexecuted. Invented, irrelevant, or already-completed inspections, manual-copy instructions, and destructive guidance are withheld by deterministic SDK code. Privacy-bounded receipts store hashes, evidence ranges, aggregate telemetry, omissions, timing, token usage, and the structured result without full prompts or source excerpts. A long-lived SDK or Agent process caches unchanged grounding and retains a local model for a bounded idle period; assistant stop closes it immediately. Prompting remains read-only and cannot execute a recommendation. Official Windows installs include a standalone allin1-sdk.exe that works from a newly opened PowerShell without Python.""",
        ("console", "autocomplete", "completion", "history", "source", "terminal", "assistant", "qwen", "prompt"),
    ),
    HelpTopic(
        "agent-api", "Automation", "AI agent integration",
        "Let local AI and developer tools inspect and operate the SDK through structured JSON.",
        """Run allin1-sdk agent-api and exchange one JSON object per line over standard input and output. Use the ping action to negotiate the protocol, catalog to discover command schemas and risk levels, and execute with a command plus a string args array.

The transport never invokes a shell and cannot evaluate Python. Requests are written to the per-user agent-api-audit.jsonl log. Game/archive mutation commands are rejected by default. A user must explicitly start the process with --allow-game-writes, and the requested SDK command must still pass its normal acknowledgement, closed-game, authorized-target, checksum, lock, backup, and rollback checks. validate-package verifies a mod.toml and its payload hashes; inspect-package-receipt and verify-package-ownership provide read-only receipt evidence; list-installed-packages discovers receipt-backed installs; install-package validates and installs a package; uninstall-package removes one through its owned-file and backup receipt.

The read-only assistant group is also available through execute requests: pass command assistant and args beginning with context or prompt. The host supplies focused repository, workspace, manifest, game-path, dirty-state, and live command evidence under a permanent ALLIN1 policy. A prompt response cannot approve or invoke another command. This API remains the stable foundation for later reviewed AI-assisted package inspection and approved package lifecycle actions. The ALLIN1 Launcher remains the user-facing owner of assistant installation, interactive package installation, and game launch.""",
        ("ai", "agent", "api", "json", "jsonl", "stdio", "automation", "audit", "qwen", "assistant"),
    ),
    HelpTopic(
        "input", "Interface", "Navigating the SDK",
        "Use the integration graph, field inspector, menus, and search efficiently.",
        """Package imports and audits are grouped under Import or audit package. Inspect or export applies to the selected package, and Package tools contains cross-package workflows.

Use the workspace sidebar to move between Package Linker, Asset Viewer, Content Workbench, RPF Archives, Package Recipes, and Help Center. The slim arrow on its right edge points < while open to fold the sidebar in, then points > to expand it back out; Ctrl+B or View > Show workspace sidebar provides the same toggle from anywhere. Ctrl+1–6 selects those workspaces without opening another application window. A compact ‹ previous-workspace link appears in the header when there is somewhere useful to return; Alt+Left activates the same history route even while the sidebar is folded. Ctrl+Tab and Ctrl+Shift+Tab cycle between workspaces. The SDK Console remains available along the bottom in every context.

Select an integration node to see its source, contract, and linked fields. Select a field for a plain-language explanation. Ctrl+` focuses or expands the console dock and F1 routes to contextual help.""",
        ("navigation", "menus", "graph", "field", "keyboard"),
    ),
    HelpTopic(
        "packages", "Content", "Package auditing",
        "Inventory and classify content without executing compiled payloads.",
        """Import a loose DLC folder or supported OIV/ZIP/RAR/7z archive. The scanner inventories metadata, native assets, plug-in headers, dependencies, edition hints, and inferred destinations.

Generated addon.json files are drafts. Resolve their warnings and linker errors before packaging. DLL and ASI payloads are inspected as inert bytes and are never loaded by the SDK.""",
        ("mods", "package", "manifest", "archive", "audit", "plugin"),
    ),
    HelpTopic(
        "package-recipes", "Authoring", "Package Recipes",
        "Inspect OIV instructions once, then choose only the outputs proven safe.",
        """Open an OIV/ZIP package or an unpacked recipe folder. Package Recipes reads assembly.xml as inert data, lists every ordered operation, and separates blockers and warnings from the operation table. It never executes package instructions.

The Available outputs panel enables actions only when the inspection result supports them. Export inspection report writes matching Markdown and JSON evidence. Compile against existing RPF replays supported ordered file, XML, bounded text, and native PSO operations against an explicitly selected archive, then emits verified payloads, an audit, and an inert hash-bound plan without writing the archive. Export atomic RPF batches creates payload-backed manifests for exact existing-archive changes. Build declared new archives recursively verifies bounded createIfNotExist trees before producing a managed package. Export managed package is limited to operations representable by receipt ownership and rollback.

Select the matching Legacy or Enhanced installation only when archive keys or native-resource rebuilds are needed. Output folders must be new, and applying an inert RPF plan remains a separate reviewed transaction. The bottom SDK Console and Agent API expose the same inspection and compilation rules.""",
        (
            "oiv", "recipe", "assembly.xml", "managed package", "compile",
            "atomic batch", "createifnotexist",
        ),
    ),
    HelpTopic(
        "sdk", "Authoring", "Package Linker",
        "Trace game-facing fields and audit add-on integration before installation.",
        """The Add-on SDK links authored package fields to metadata, native UI text, animations, runtime behavior, packaging, and rollback expectations.

Import a DLC folder or archive, inspect its integration graph, then select nodes and fields for explanations. Workbench keeps vehicle, weapon, and ped projects in three tabs backed by the same package scan. Package Tools opens the persistent Package Recipes workspace alongside DLC inventory, vehicle-data compilation, and structured META/XML comparison and round-trip tools.

Package Recipes recognizes official XML add/replace/remove, bounded line-oriented text, and native PSO grammar as compile workflows while keeping wildcard text masks, PSO edits in newly created archives, unbounded archive creation, ambiguous selectors, and unknown operations blocked. Select the matching outer RPF to compile supported recipes into verified payloads and a hash-bound inert plan; the source archive is not written. Native PSO targets are decoded with the selected game's keys, edited through bounded XPath, rebuilt against their immutable source, reparsed, and required to match the edited XML semantically. Text insert/replace/delete selectors use exact or prefix matching and must resolve to one line. Bounded createIfNotExist recipes replay declared adds, structured edits, and cleanup deletes in order before becoming exactly verified managed packages, while exact adds/replacements/deletes inside existing nested RPF trees can be exported as payload-backed batch manifests.""",
        ("authoring", "addon", "dlc", "audit", "linker", "developer"),
    ),
    HelpTopic(
        "asset-viewer", "Inspectors", "Asset Viewer",
        "Browse package files and preview supported native resources without executing code.",
        """Open a package folder or supported archive, search its inventory, and select an asset. Images and text preview directly. Supported Rockstar resources receive header analysis, structured CodeWalker XML, and texture contact sheets when possible. YDR, YDD, and YFT model resources also receive a bounded indexed-geometry diagnostic view with drawable, LOD, bounds, vertex, triangle, shader, named texture-reference, skin/bone-binding, skeleton, and light statistics. YBN collision resources visualize world-positioned triangle meshes and diagnostic primitive hulls, with geometry, material, polygon-type, vertex, and bounds counts. YMAP placement resources plot entities by archetype, position, orientation, scale, and parent link in a top-down world-space overview, with generator/occluder/time-cycle counts. YNV navigation meshes plot polygon surfaces, portal spans, directed point nodes, edge references, content flags, and bounds. YND path dictionaries plot vehicle and pedestrian nodes, internal and external links, junctions, street labels, and declared node-count mismatches. YTYP archetype dictionaries become typed dependency graphs connecting definitions to asset names and shared texture, drawable, physics, and clip dictionaries. These are inspection views rather than shader-, physics-, pathfinding-, or game-engine-accurate renderers; unsupported/custom layouts are skipped instead of guessed.

AWC audio containers use the matching Legacy or Enhanced installation keys to expose stream names, codecs, sample rates, duration, loop/peak metadata, encryption flags, and individual WAV dependencies. AWC workspace builds create a separate new container, decrypt and parse it again, and reject publication unless the edited and reparsed stream definitions are semantically identical. The game installation path is used only for keys and is never stored in the workspace.

SDK Console command inspect-native-asset publishes the same analysis through JSON and can create a new portable folder containing structured text, a PNG preview, and report.json. Pass --gta-path for encrypted AWC content. The command and its GTA-path parameter are also discoverable through the local structured Agent API.

For a supported native resource, Native authoring > Export selected asset as editable workspace preserves an immutable source snapshot beside editable XML and dependencies. Workspace tools > Build verified asset from workspace reconstructs the binary only after CodeWalker successfully parses the result again and writes a validation sidecar.

Workspace tools > Open YTD texture workspace switches this same window into a searchable texture editor. It previews DDS images, imports DDS or common raster formats, updates width/height/mip/format metadata, supports add/replace/remove, and retains local undo history. Build + validate YTD reparses the completed dictionary before it is accepted. Raster imports become uncompressed DDS; provide a prepared DDS when compression and mip control must be retained. Compiled DLL, ASI, and script payloads are never executed.""",
        ("ytd", "ydr", "yft", "awc", "audio", "texture", "model", "preview", "codewalker"),
    ),
    HelpTopic(
        "workbench", "Inspectors", "Content Workbench",
        "Review vehicle, weapon, and ped projects without reopening the package.",
        """Open a loose add-on folder or supported archive once. The Workbench scans it once, shows counts on the Vehicles, Weapons, and Peds tabs, and keeps every specialist view tied to that same package evidence.

Vehicles provides the existing model viewport and guarded vehicle-authoring tools. Weapons links definitions to ammo, animations, shop registration, attachment points, component models, and streamed assets. Peds links peds.meta definitions to drawable dictionaries, texture dictionaries, props, movement clips, and expression data.

The Package Linker and all three content workbenches use the same slim green divider arrows around their center workspace. Use the left arrow to fold the catalog out and the right arrow to fold the inspector out; the arrows reverse when collapsed. Expanding a side restores its previous width, selection, and edit state, while the center workspace immediately uses the released room.

Use Open selected asset to route a project member into the SDK Asset Viewer without creating another application window. Missing links remain visible as review findings; the Workbench does not invent metadata or execute package code. Console and Agent API command inspect-workbench returns the same records, component links, and findings as structured JSON. open-workbench accepts --category auto, vehicles, weapons, or peds. The older open-vehicle-workbench command remains a compatibility alias.""",
        (
            "workbench", "vehicle", "weapon", "ped", "package", "project",
            "attachment", "component", "metadata",
        ),
    ),
    HelpTopic(
        "weapon-workbench", "Inspectors", "Weapons Workbench",
        "Trace a weapon from its definition through attachments and package assets.",
        """Select a weapon to review its slot, model, ammo pool, HUD and stat names, animation mapping, shop registration, and source metadata. Attachments lists every component linked at each attach bone, including the default choice and component model when the package declares it.

Readiness separates required integration from optional attachment content. Asset matching is evidence-based and includes related drawable, texture, collision, and metadata files. Double-click an asset or use Open selected asset to continue in Asset Viewer. This first workbench pass is read-only; it exposes the complete structure needed for a later guarded weapon-authoring workflow without guessing component hashes or game behavior.""",
        ("weapon", "ammo", "attachment", "component", "shop", "animation"),
    ),
    HelpTopic(
        "ped-workbench", "Inspectors", "Peds Workbench",
        "Review ped definitions, streamed models, textures, props, and motion links.",
        """Select a ped to inspect its peds.meta fields and the package files that share its model or props identity. Readiness distinguishes visible loose assets from content that may still be packed inside an opaque RPF.

The workbench checks core definition fields, drawable and texture presence, props, movement clip sets, and expression sets. Double-click a related file or use Open selected asset to inspect supported native content in Asset Viewer. It never previews a model by executing scripts or loading it into GTA V.""",
        ("ped", "peds.meta", "ydd", "ydr", "ytd", "props", "movement"),
    ),
    HelpTopic(
        "vehicle-workbench", "Inspectors", "Vehicle Workbench",
        "Inspect each vehicle as one linked model, texture, metadata, and registration project.",
        """Open a loose DLC folder or supported package archive. The workbench resolves every vehicles.meta record against its primary and high-detail YFT fragments, YTD texture dictionary, handling record, variation record, tuning kits, labels, and DLC registration evidence.

Select a vehicle on the left to see its resolved project members and unresolved links. The live viewport is the responsive inspection view, works without Blender, and uses the same bounded native conversion as Asset Viewer. Its compact dark strip keeps the model area clear: choose Shaded for a lit solid preview, Materials to inspect material assignments, or Wireframe to study topology. Render full-quality frame stays inside the SDK viewport renderer and produces a more detailed static inspection frame; Studio / Compiled Render is the separate option for an offline presentation still. Model opens the fragment, LOD, and component filters; View provides perspective, orthographic-style side/front/top presets, incremental orbit controls, and camera reset. Use the mouse wheel or +/− keys to zoom, left-drag to pan, right-drag to orbit, F or a left double-click to fit, 0 to return to 100%, and R or a right double-click to reset the camera. The on-canvas summary reports geometry counts, shader/material names, and named texture references without taking another permanent toolbar row. Interactive orbit uses a reduced preview budget and resolves to the normal detailed render when released. Render full-quality frame is an opt-in background job for dense models and is cached for the exact model, filters, mode, and camera view. The workbench retains at most two decoded fragment scenes to keep memory bounded.

Studio / Compiled Render opens one embedded drawer over the viewport instead of another application window. It uses Blender as a separately installed, optional offline renderer; Blender is not bundled, and every live viewport feature remains available without it. Choose the Eevee or Cycles render engine and a CPU/GPU device, then set the resolution, studio lighting rig, background, and output file. Advanced settings expose samples, custom dimensions, light rotation and strength, and an exact background color. Transparent output is available for PNG renders. The drawer reports the detected Blender backend, render stage, and progress. Cancel cooperatively stops an active job, and Open output becomes available only after a verified image is produced. If Blender is not detected, Locate Blender selects an existing executable and Get Blender opens the official download page; the SDK never downloads or installs it automatically.

Compiled rendering is output-only: it creates temporary interchange files in an isolated workspace, removes them when the job finishes, and writes only the verified PNG to the output file you chose. It never changes the source package, RPF contents, vehicle metadata, or GTA V installation. Console and Agent API command render-native-model exposes the same guarded renderer for one existing YDR, YDD, or YFT source and an external PNG destination. A linked loose YTD is decoded through the verified native workspace path, and the renderer preserves UV0 plus named diffuse, normal, emissive, and specular sampler roles. Vehicle YFTs discover a safe same-name sibling YTD automatically; --texture-dictionary selects another explicit file. Missing shared-game textures retain the semantic material fallback. The render reproduces linked texture pixels but approximates game shader programs, reflections, and skinning, so it remains a presentation and diagnostic render rather than an exact in-game frame.

Select a project member and choose Open selected in Asset Viewer, or double-click it, to continue detailed inspection and supported native workspace export inside the same SDK window. Open texture dictionary jumps directly to the linked YTD, where the existing editable texture workspace can be exported, changed, validated, and rebuilt.

Create authoring workspace makes a verified copy of every visible package member before enabling edits. The Author tab exposes player-facing labels, texture dictionary, class/type/layout/audio, and common mass/drive/brake/steering values. The Appearance tab manages structured spawn color and livery sets, linked tuning kits, selected light and siren profiles, tuning-kit type and livery labels, and every existing scalar field in local light-profile definitions. The Tuning Builder tab groups visible parts, linked companion parts, performance upgrades, and category labels. Pick an unlinked model asset to start a new part, add or duplicate entries, edit every known or already-present scalar/array field, reorder shop choices, and review missing assets, duplicate identities, companion links, and array mismatches beside the editor. Apply actions snapshot all affected XML files, commit them as one operation, reparse the result, and run the package relationship compiler again. A new missing texture, handling, variation, tuning kit, tuning model, or registration causes every touched file to roll back. Undo latest retains an additional recovery snapshot.

Identity migration safely renames modelName and handlingId across owned metadata while renaming matching primary/high-detail YFT and YTD files in the same transaction. It rejects collisions and shared handling records, then verifies that the renamed model still resolves before retaining the edit. Tuning-kit names and numeric IDs stay locked because changing those IDs can collide with content outside the package. Additional guarded handling fields are available through set-vehicle-fields in the console and Agent API. Binary GXT2 labels and YTD textures continue through their dedicated validated workspaces rather than unsafe raw text edits. After metadata changes, package publication requires a rebuildable dlc.rpf.source; the SDK blocks an unchanged prebuilt dlc.rpf from silently discarding those edits.

Export vehicle project writes a new portable vehicle-project.json plus a readable relationship report without copying or modifying the package. Build installable package accepts exactly one existing dlc.rpf, or compiles one reviewed dlc.rpf.source when a matching GTA path is available. It generates the standard mods/update/x64/dlcpacks destination, SHA-256 checksum, audit report, and validated mod.toml in a new atomic output folder; it never installs or changes the game. Console and Agent API commands create-vehicle-authoring, inspect-vehicle-authoring, set-vehicle-fields, set-vehicle-appearance, set-vehicle-tuning-kit, inspect-vehicle-tuning, add-vehicle-tuning-entry, set-vehicle-tuning-entry, remove-vehicle-tuning-entry, move-vehicle-tuning-entry, set-vehicle-light-profile, migrate-vehicle-identity, undo-vehicle-edit, inspect-vehicle-project, export-vehicle-project, and build-vehicle-package expose the same guarded implementation. Missing or opaque data stays visible as a finding rather than being guessed.""",
        (
            "vehicle", "workbench", "yft", "ytd", "handling", "carvariations",
            "carcols", "tuning", "viewport", "zoom", "pan", "component",
            "material", "package", "dlc.rpf", "author", "handling", "undo",
            "colors", "liveries", "lights", "sirens", "identity", "migration",
            "parts", "performance", "slot", "builder", "live viewport",
            "studio render", "compiled render", "blender", "eevee", "cycles",
        ),
    ),
    HelpTopic(
        "rpf-explorer", "Inspectors", "RPF Archives",
        "Search archives, inspect metadata, and transact guarded root or nested changes.",
        """Select the matching GTA V installation before opening an RPF so the correct encryption keys and resource decoder are used.

Search and filter the archive tree, then use the visible Preview, Extract, and Plan replacement buttons or the grouped Selected entry menu. A selected directory can be exported recursively, and Archive tools > Inspect & Verify > Extract current archive tree exports the root of the outer archive. Each subtree export scans the outer RPF once, writes into a new staged folder, refuses output collisions, verifies that its source did not change, and records source and per-file SHA-256 values in .allin1-rpf-export.json. Nested RPF files remain files in their parent export; select that virtual archive explicitly to export its internal tree. Compare with another archive supports metadata, exact-byte, and logical-content modes. Logical mode compares RSC7 resource headers plus decompressed payloads, avoiding false changes caused only by recompression while retaining exact mode for forensic byte comparison.

Archive tools > Catalog > Build/update global RPF catalog recursively discovers up to 512 loose archives and atomically writes a SQLite search index outside the game installation. Unchanged size/mtime records reuse their prior recursive index; CLI --refresh forces every archive through indexing and SHA-256 again. Unreadable archives are recorded without hiding valid results. Archive tools > Catalog > Search global RPF catalog searches outer names, nested archive paths, and entry paths; double-click a result to open its outer archive and select the exact nested entry.

Archive tools > Build & Author > Build new RPF from folder creates a brand-new OPEN archive outside the GTA V installation without touching an existing archive or game file. Name a loose directory example.rpf.source to author it as a nested example.rpf. The builder refuses precompiled nested RPFs, links, path collisions, output overwrite, source changes, excessive depth or size, and any result whose recursive index, directory tree, or extracted logical payload differs from the source. Ordinary files are byte-exact; RSC7 resources are verified by their exact header plus decompressed bytes so safe compressor differences do not create a false failure. The JSON report records raw and canonical hashes for every entry; install the verified archive later through a validated, receipt-owned package.

The Package Graph tab keeps the visual authoring workflow inside RPF Archives. Import a loose source tree, recursively expand the currently opened RPF into a new external graph workspace, or start from an empty archive card. Existing-RPF import leaves the archive untouched, requires every nested archive to be indexed, turns nested containers into `.rpf.source` branches, and binds the graph to the origin archive hash. Purple archive, green directory, and blue source-file cards expose containment ports; drag cards to position them and drag a parent output port onto a child card to validate and save a reparent operation. Complete mod-package imports are retained as reusable projects. Analyze links adds pink vehicle-system cards and typed links to models, textures, handling, variations, tuning, registrations, text labels, edition, and install target. The Links filter and legend isolate each relationship group. The inspector reports missing, mismatched, duplicate, and orphaned references and can route a selected asset or vehicle directly into its specialist workspace. These derived cards never become package files. The inspector also adds, renames, removes, searches, lays out, validates, materializes, and exactly builds the containment graph. Imported graphs can build and canonically compare against their unchanged origin, retain the desired archive and exact payload evidence, and emit a normal inert multi-entry change plan; nested edits collapse to a reviewed parent-RPF replacement. One versioned JSON document backs the canvas and the complete create/import/inspect/validate/analyze/add/rename/reparent/position/layout/remove/refresh/materialize/build/origin-plan CLI and agent-API surface. Graph removal never deletes source files, changed sources require an explicit hash refresh, and applying an origin plan remains a separate guarded receipt-owned action.

The graph editor's Build Flow tab is an executable typed operation canvas rather than another directory view. Its zoom, fit, and reset controls match Package Layout. Gold artifact pins connect only to compatible typed inputs: Package graph -> Validate package -> Materialize tree, Build + verify RPF, or Plan imported-origin changes; a built RPF can feed Defragment + verify and any final artifact can feed a named output node. Create flow includes reusable Validate, Loose authoring tree, Verified build, Compact release, and Imported-origin plan scaffolds. Cycles, incompatible types, multiple inputs, missing paths, output collisions, existing outputs, and outputs inside any configured GTA installation are refused. Dry-run plan binds the program, package graph, hashes, node order, and expected outputs without executing anything. Run flow requires confirmation, creates only new external artifacts, cleans up exact outputs from a failed run, rechecks both source documents, and writes one execution report. The same template catalog and create/inspect/add/configure/connect/disconnect/position/layout/remove/plan/run surface is discoverable through the bottom SDK Console and Agent API.

The Visual Change Set tab replaces hand-written batch manifests for multi-entry editing. Stage replacements, new files, deletions, directory creation/removal, and same-parent renames; reorder or remove staged actions, verify source and payload bindings, then compile one atomic plan. The workspace is inert and never writes the RPF. Its versioned JSON binds the opened archive and every payload by size and SHA-256, and compilation rechecks all files and the document. Apply entry-change plan remains the separate transaction step with closed-game enforcement, full-archive staging, verification, receipt, recovery, and rollback. The same create/inspect/stage/unstage/move/plan workflow is available in the SDK Console and Agent API.

Package Recipes recognizes bounded createIfNotExist recipes instead of executing them. The CLI's --created-rpf-package workflow extracts only declared content members into retained loose authoring sources, replays supported XML, bounded text edits, and cleanup deletes in recipe order, builds all nested archives through the same exact-verification engine, and emits a validated mod.toml package plus a retained operation audit. Edit and delete operations in a new archive are accepted only after the target file is available at that point; a newly created text file must begin with an add-line command. XML output is reparsed and canonically verified before archive construction. Line-oriented text output preserves supported Unicode encoding, BOM, newline style, and final-newline state, passes an encoding round trip, and must remain well-formed when the target is XML-shaped. Exact or prefix selectors must match one line; wildcard masks remain blocked. Official XML, text, and supported native PSO commands can also be compiled against one explicitly selected existing outer RPF with compile-oiv-recipe. Repeated target edits are coalesced into a retained payload bundle plus the normal inert multi-entry plan. PSO edits inside newly created archives, missing nested creation declarations, and creation roots deeper than one existing archive remain blocked.

Supported native files expose Selected entry > Export Workspace > Export editable native workspace. After editing its XML or dependencies, Selected entry > Plan Change > Plan replacement from native workspace rebuilds the binary, reparses it through CodeWalker, keeps the payload and validation report beside the inert plan, and binds their hashes to the selected root or nested entry. The archive is still unchanged until the normal reviewed apply step. Console/API command inspect-rpf-native-entry performs the same bounded preview for an exact root or nested member, binds the report to the outer archive hash and virtual entry, and rechecks that the archive did not change.

Every non-directory entry exposes Preview, Extract, Plan, and Edit bytes beside the archive tree. Edit bytes exports and immediately opens the embedded Binary Workspace tab with an immutable original.bin, same-size editable.bin, exact archive/entry binding, and hash-chained patch history. Orange bytes differ from the source. Page controls inspect bounded offsets; Read current bytes fills the required expected-byte guard; Apply patch retains the operation; History recalls prior offsets; Undo latest appends recovery history; and Build verified publishes a changed-range report. Create RPF plan validates the exact recorded archive entry without leaving RPF Archives. The SDK Console and Agent API expose the same inspect, patch, undo, build, and plan surface. Planning refuses source/archive drift, a broken history chain, size changes, more than 4 MiB of changed bytes, or a workspace exported from another entry; applying remains a separate reviewed transaction.

GXT2 entries expose Edit GXT2 and Export GXT2 text workspace. The searchable table stays in the GXT2 Text tab and supports UTF-8 text edits, unique 32-bit hash additions, removal, local undo, and verified builds; the tab's start page resumes an existing project. The same operations are available to the SDK Console and agent API. Both little-endian 2TXG markers, offsets, terminators, source snapshot, contiguous history hashes, outer archive hash, virtual entry, and edition are revalidated before Plan replacement from GXT2 workspace can publish an inert plan.

Archive tools > Inspect & Verify > Compare with another archive indexes both recursive trees. Metadata mode is fast and reports added, removed, modified, and unchanged archive/entry records. Exact-content mode batch-extracts each side into bounded temporary storage and compares SHA-256 values, detecting payload changes even when size and resource metadata are identical. JSON and Markdown reports are written together, and both source hashes are rechecked before the report is accepted.

Archive tools > Plan Changes > Derive plan from desired archive turns a known-good opened base and a finished desired RPF into one inert multi-entry plan plus a portable sidecar containing only changed payloads. Canonical logical comparison ignores harmless RSC7 recompression and nested-container repacking. Existing child archives are edited through deep virtual paths, while a completely new child RPF stays one reviewed container payload. Case-only paths and in-place type changes are refused as ambiguous. Both complete archives are rehashed before publishing, partial output is removed after failure, and applying the plan remains a separate guarded action.

Visual package graph file cards render bounded previews in a background worker so navigation remains responsive. Images and supported native visual resources receive real thumbnails; text/configuration files and other formats receive deterministic compact cards. Create output > Export preview bundle verifies every graph source hash, renders into a staged external folder, revalidates the graph after rendering, and publishes an atomic bundle with per-preview hashes. The same action is available through console/API command render-rpf-graph-previews, and it refuses destinations inside the selected GTA V installation.

Archive tools > Inspect & Verify > Verify full archive integrity checks every indexed parent relationship, proves that each nested archive has a matching parent entry and every archive entry was recursively indexed, then batch-extracts and SHA-256 hashes every payload. The portable JSON report includes exact hashes, size/compression totals, duplicate payload groups, helper warnings, structural findings, and the unchanged source archive hash.

Archive tools > Inspect & Verify > Build verified defragmented copy compacts a new archive outside GTA V and never writes the opened source. The compacted copy is recursively rescanned, its complete archive/entry tree and preserved metadata are compared, and every leaf is extracted from both versions. Ordinary files must match byte-for-byte; recompressible RSC7 resources must retain both raw and canonical logical hashes. The copy publishes only after the source SHA-256 is rechecked and a portable verification report is ready.

Single-file replace, add, and delete planning creates an inert schema-v3 JSON plan. Atomic tree plans additionally support mkdir, explicit empty-directory rmdir, same-parent rename, and nested-RPF deletion. Both hash the archive, original state, payload where applicable, edition, and authorized scope.

Archive tools > Plan Changes > Create multi-entry plan accepts a JSON changes list. Use new_entry for rename. Upsert becomes add or replace only after the exact target is indexed. Directory removal is non-recursive, so all descendants must be independently listed for deletion; this prevents an accidental folder selection from erasing a subtree. Plan Changes > Plan new directory and Selected entry > Plan Change > Plan rename expose the common structural operations directly. A ready atomic plan snapshots every payload, creates parents before children, removes children before parents, processes each nested RPF once from deepest to outermost, commits the outer archive once, verifies every changed entry, and owns one full rollback receipt. An archive entry cannot be replaced or deleted in the same plan that edits its internal tree.

A subtree export preserves empty directories in its manifest and on disk. After editing the loose workspace, Archive tools > Plan Changes > Plan subtree workspace sync validates the untouched source and manifest, then reconciles changed, added, and removed files and directories into one inert atomic plan.

A ready plan may be applied only inside the selected installation's mods directory or an external workspace explicitly authorized by the CLI. A nested change walks a bounded chain of up to eight RPF levels inside the staged outer copy, verifies the deepest change, and verifies every child while reassembling its parent before commit. Transaction History provides verification, interrupted-receipt reconciliation, guarded rollback, and stale-lock inspection. Run disposable archive canary proves the real writer without changing the selected source.""",
        ("archive", "nested", "extract", "replacement", "rpf", "metadata"),
    ),
    HelpTopic(
        "recovery", "Safety & recovery", "Backups and recovery",
        "Understand RPF transaction ownership, verification, and rollback.",
        """Package scans, native previews, RPF indexes, extracted copies, linker reports, and replacement-plan creation are read-only operations.

An applied RPF plan stores its complete pre-write archive, an exact payload snapshot, the reviewed plan, and a receipt under the ALLIN1 SDK user-data directory. Verify transaction receipt proves whether the archive is still applied, already original, or externally modified and checks the rollback snapshot and exact entry.

Rollback is refused if another tool changed the archive after ALLIN1 applied it. A rollback restores the complete snapshot through a staged copy and verifies the original entry before changing the receipt to rolled_back. Failed commits automatically attempt the same restoration and preserve their receipt for diagnosis.

Only one ALLIN1 transaction can own an archive at a time. Transaction History can reconcile an interrupted receipt without completing an uncommitted write. It clears an .allin1.lock only after proving that its owner process is gone, GTA V is closed, and the archive remains inside its authorized scope.""",
        ("backup", "rollback", "restore", "safety", "receipt"),
    ),
    HelpTopic(
        "troubleshooting", "Safety & recovery", "Troubleshooting and logs",
        "Resolve importer, helper, native-decoder, and package-validation failures.",
        """Confirm the package is complete, the correct GTA V edition is selected, and the helper was built from the pinned CodeWalker submodule.

An unresolved edition, missing source, unsafe archive path, checksum mismatch, or incomplete rollback step is intentionally surfaced instead of guessed. Export the audit/link report when asking for help so the exact finding codes and paths are preserved.

F1 routes the current workspace to the relevant article in this embedded Help Center.""",
        ("logs", "error", "helper", "diagnostics", "finding", "failure"),
    ),
)


def search_help_topics(query: str) -> tuple[HelpTopic, ...]:
    """Return help topics ranked by a simple, predictable text match."""
    words = tuple(part.casefold() for part in query.split() if part.strip())
    if not words:
        return HELP_TOPICS

    scored: list[tuple[int, HelpTopic]] = []
    for topic in HELP_TOPICS:
        title = topic.title.casefold()
        category = topic.category.casefold()
        summary = topic.summary.casefold()
        body = topic.body.casefold()
        keywords = " ".join(topic.keywords).casefold()
        haystack = " ".join((title, category, summary, body, keywords))
        if not all(word in haystack for word in words):
            continue
        score = sum(
            8 if word in title else 4 if word in keywords else 2 if word in summary else 1
            for word in words
        )
        scored.append((score, topic))
    return tuple(topic for _score, topic in sorted(
        scored, key=lambda item: (-item[0], item[1].category, item[1].title),
    ))


class HelpCenterDialog(ttk.Frame):
    """Searchable help center embedded in the primary SDK shell."""

    def __init__(
        self, parent: tk.Misc, initial_topic: str | None = None,
        *, embedded: bool = False,
    ) -> None:
        self._window: tk.Toplevel | None = None
        host = parent
        if not embedded:
            self._window = tk.Toplevel(parent)
            self._window.title("ALLIN1 SDK Help Center")
            place_window(
                self._window, preferred=(1040, 700), minimum=(780, 540),
            )
            self._window.transient(parent.winfo_toplevel())
            host = self._window
        super().__init__(host)
        self.pack(fill="both", expand=True)
        self.initial_topic = initial_topic
        self.visible_topics: tuple[HelpTopic, ...] = ()
        self.topic_items: dict[str, HelpTopic] = {}
        self._build()
        self._populate()
        if self._window is not None:
            self._window.bind("<Escape>", lambda _event: self._window.destroy())
            self._window.bind("<Control-f>", self._focus_search)
            self.after_idle(self.search_entry.focus_set)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 16))
        ttk.Label(
            header, text="Help Center", font=("Segoe UI Semibold", 20),
            foreground="#173d32",
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="Guidance for setup, package authoring, native assets, and recovery.",
            foreground="#52635c",
        ).pack(anchor="w", pady=(3, 0))

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)
        navigation = ttk.Frame(body, padding=(0, 0, 14, 0))
        article = ttk.Frame(body, padding=(18, 4, 4, 4))
        body.add(navigation, weight=2)
        body.add(article, weight=5)

        ttk.Label(navigation, text="Search help", style="FieldLabel.TLabel").pack(
            anchor="w",
        )
        self.query = tk.StringVar()
        self.search_entry = ttk.Entry(navigation, textvariable=self.query)
        self.search_entry.pack(fill="x", pady=(6, 12))
        self.search_entry.bind("<Escape>", lambda _event: self._clear_search())
        self.query.trace_add("write", lambda *_args: self._populate())
        self.results = tk.Listbox(
            navigation, exportselection=False, activestyle="none", borderwidth=0,
            highlightthickness=1, highlightbackground="#d7e0dc",
            selectbackground="#dcefe3", selectforeground="#173d32",
            font=("Segoe UI", 10),
        )
        result_scroll = ttk.Scrollbar(
            navigation, orient="vertical", command=self.results.yview,
        )
        self.results.configure(yscrollcommand=result_scroll.set)
        self.results.pack(side="left", fill="both", expand=True)
        result_scroll.pack(side="right", fill="y")
        self.results.bind("<<ListboxSelect>>", self._select_topic)
        self.results.bind("<Return>", self._select_topic)

        self.category = tk.StringVar(value="START HERE")
        self.heading = tk.StringVar(value="Select a help topic")
        self.summary = tk.StringVar(value="")
        ttk.Label(
            article, textvariable=self.category, foreground="#1f7f42",
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w")
        ttk.Label(
            article, textvariable=self.heading, font=("Segoe UI Semibold", 18),
            foreground="#173d32",
        ).pack(anchor="w", pady=(4, 2))
        ttk.Label(
            article, textvariable=self.summary, foreground="#52635c",
            wraplength=650, justify="left",
        ).pack(anchor="w", pady=(0, 12))
        ttk.Separator(article).pack(fill="x", pady=(0, 12))
        article_frame = ttk.Frame(article)
        article_frame.pack(fill="both", expand=True)
        self.body = tk.Text(
            article_frame, wrap="word", relief="flat", borderwidth=0,
            background="#ffffff", foreground="#24332d", font=("Segoe UI", 10),
            padx=4, pady=4, spacing1=3, spacing3=8, state="disabled",
        )
        article_scroll = ttk.Scrollbar(
            article_frame, orient="vertical", command=self.body.yview,
        )
        self.body.configure(yscrollcommand=article_scroll.set)
        self.body.pack(side="left", fill="both", expand=True)
        article_scroll.pack(side="right", fill="y")

    def _populate(self) -> None:
        self.visible_topics = search_help_topics(self.query.get())
        self.results.delete(0, "end")
        self.topic_items.clear()
        for index, topic in enumerate(self.visible_topics):
            label = f"{topic.category} · {topic.title}"
            self.results.insert("end", label)
            self.topic_items[str(index)] = topic
        if not self.visible_topics:
            self.category.set("NO RESULTS")
            self.heading.set("No matching help topics")
            self.summary.set("Try a shorter search such as ‘RPF’, ‘install’, or ‘logs’.")
            self._set_body("")
            return
        selected_index = 0
        if self.initial_topic:
            for index, topic in enumerate(self.visible_topics):
                if topic.key == self.initial_topic:
                    selected_index = index
                    break
            self.initial_topic = None
        self.results.selection_set(selected_index)
        self.results.see(selected_index)
        self._show_topic(self.visible_topics[selected_index])

    def _focus_search(self, _event: object | None = None) -> str:
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def _clear_search(self) -> str:
        self.query.set("")
        return "break"

    def show_topic(self, key: str) -> None:
        """Navigate an existing help workspace without opening a window."""
        self.initial_topic = key
        self.query.set("")
        self._populate()

    def _select_topic(self, _event: object | None = None) -> None:
        selection = self.results.curselection()
        if selection and selection[0] < len(self.visible_topics):
            self._show_topic(self.visible_topics[selection[0]])

    def _show_topic(self, topic: HelpTopic) -> None:
        self.category.set(topic.category.upper())
        self.heading.set(topic.title)
        self.summary.set(topic.summary)
        self._set_body(topic.body)

    def _set_body(self, value: str) -> None:
        self.body.configure(state="normal")
        self.body.delete("1.0", "end")
        self.body.insert("1.0", value)
        self.body.configure(state="disabled")
