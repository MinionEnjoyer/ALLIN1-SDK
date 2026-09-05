# allin1-sdk command reference — 0.6.4

Generated from the Click command tree by `documentation_audit.py`; do not edit individual rows.

Use `allin1-sdk COMMAND --help` for argument types, choices, defaults and complete safety requirements.
Listing a command here does not authorize execution, imply React UI parity, or qualify a game write.

| Command | Purpose | Parameters |
| --- | --- | --- |
| `allin1-sdk` | Author, audit, and inspect GTA V add-on content. | --version |
| `allin1-sdk add-gxt2-entry` | Add one unique hash/text record to a GXT2 workspace. | workspace, label_hash, text, --acknowledge-edit |
| `allin1-sdk add-rpf-graph-container` | Add a directory or nested archive below a graph parent node. | graph, parent_id, name, --archive, --x, --y, --acknowledge-edit |
| `allin1-sdk add-rpf-graph-file` | Add a source-hashed file below a graph parent node. | graph, parent_id, source, --name, --x, --y, --acknowledge-edit |
| `allin1-sdk add-rpf-program-node` | Add a typed operation node; the package and game remain unchanged. | program, node_type, --config-json, --x, --y, --acknowledge-edit |
| `allin1-sdk add-vehicle-tuning-entry` | Add or duplicate one validated tuning-kit entry. | workspace, model, kit_name, collection, --set, --duplicate-index, --acknowledge-edit |
| `allin1-sdk add-ytd-texture` | Add one named texture using DDS or a converted raster image. | workspace, texture_name, image, --acknowledge-edit |
| `allin1-sdk agent-api` | Serve the structured local AI/developer API over JSONL stdio. | --allow-game-writes |
| `allin1-sdk analyze-package-graph` | Resolve and persist typed vehicle relationships in a package graph. | graph, --output / -o |
| `allin1-sdk apply-rpf-plan` | Apply a ready RPF plan through backup, staging, verification, and receipt. | plan, --gta-path, --workspace-root, --receipt-dir, --acknowledge-write |
| `allin1-sdk assistant` | Prompt or inspect the optional local-first SDK assistant. |  |
| `allin1-sdk assistant context` | Show the exact evidence and typed operations supplied to the model. | question, --repository-root, --workspace-root, --manifest, --gta-path, --operation-mode, --source, --symbol, --prioritize, --telemetry, --telemetry-pattern |
| `allin1-sdk assistant prompt` | Ask the configured Qwen/compatible model a read-only question. | prompt, --root, --system-prompt, --repository-root, --workspace-root, --manifest, --gta-path, --operation-mode, --source, --symbol, --prioritize, --telemetry, --telemetry-pattern, --timeout, --startup-timeout, --max-tokens, --json-output, --no-progress |
| `allin1-sdk assistant review` | Run a chunked, repository-grounded multi-symbol code audit. | question, --root, --repository-root, --source, --symbols, --prioritize, --format, --preserve-findings-on-schema-failure, --chunk-size, --timeout, --startup-timeout, --max-tokens, --no-progress |
| `allin1-sdk assistant status` | Show the configured provider without starting a model. | --root |
| `allin1-sdk assistant stop` | Stop the local model server retained by this SDK process. |  |
| `allin1-sdk audit-folder` | Audit all supported packages in a staging folder. | folder, --output / -o, --draft-dir |
| `allin1-sdk build-axle-oiv` | Build a verified Legacy OIV or Enhanced OpenRPF fallback archive. | request_json, --identity-store, --output / -o, --acknowledge-edit |
| `allin1-sdk build-axle-runtime-bundle` | Build ready runtime targets into a new atomic staging directory. | config_json, --target, --skeleton-xml, --story-profile, --game-build, --output-dir / -o, --gta-path, --acknowledge-edit |
| `allin1-sdk build-binary-workspace` | Build a same-size binary asset and bounded changed-range report. | workspace, --output / -o |
| `allin1-sdk build-gxt2-workspace` | Rebuild and semantically reparse an edited GXT2 text table. | workspace, --output / -o |
| `allin1-sdk build-map-package` | Build a new validated map DLC and ALLIN1 runtime descriptor package. | source, descriptor, output, --project-root, --gta-path, --edition |
| `allin1-sdk build-material-workspace` | Compile edited material XML and reparse it before publication. | workspace, --gta-path, --output / -o |
| `allin1-sdk build-native-workspace` | Rebuild and reparse an edited native XML workspace. | workspace, --gta-path, --output / -o |
| `allin1-sdk build-rpf-graph` | Materialize, build, exactly verify, and bind a graph-authored RPF. | graph, --gta-path, --output / -o |
| `allin1-sdk build-rpf-tree` | Create and exactly verify a new RPF, including *.rpf.source subtrees. | source, --gta-path, --output / -o |
| `allin1-sdk build-story-axle-runtime` | Compile and validate generic Legacy/Enhanced Story controller candidates. | config_json, --target, --skeleton-xml, --output-dir / -o, --gta-path, --configuration-directory, --log-file, --discovery-interval-ms, --recovery-interval-ms, --runtime-enabled / --runtime-disabled, --restore-on-unload / --no-restore-on-unload, --archives / --no-archives, --build-id, --visual-studio-path, --ctest-path, --cmake-path, --toolchain-mode, --acknowledge-edit |
| `allin1-sdk build-vehicle-package` | Publish a vehicle DLC as a validated, installable ALLIN1 package. | source, --output-dir / -o, --pack-name, --mod-id, --name, --version, --edition, --gta-path |
| `allin1-sdk canary-rpf-transaction` | Prove real RPF apply/verify/rollback behavior on an isolated archive copy. | archive, --gta-path, --output-dir, --acknowledge-write |
| `allin1-sdk catalog-rpfs` | Build or incrementally refresh a global loose-RPF search catalog. | source, --gta-path, --output / -o, --refresh |
| `allin1-sdk clone-ped-bundle` | Apply one reviewed, revision-bound complete ped clone plan. | workspace, donor, --ped-name, --set, --expected-revision, --plan-sha256, --acknowledge-edit |
| `allin1-sdk clone-weapon-animation` | Clone complete native animation mappings without editing clip payloads. | workspace, weapon, --template, --source, --expected-revision, --acknowledge-edit |
| `allin1-sdk clone-weapon-bundle` | Apply one reviewed, revision-bound complete weapon clone plan. | workspace, donor, --weapon-name, --slot, --ammo-info, --model, --human-name-hash, --stat-name, --ammo-mode, --ammo-name, --expected-revision, --plan-sha256, --acknowledge-edit |
| `allin1-sdk compare-telemetry` | Compare numeric key/value telemetry from two text files without editing. | baseline, current |
| `allin1-sdk compile-oiv-recipe` | Compile guarded OIV XML, text, and PSO commands into an inert RPF plan. | source, archive, --output / -o, --gta-path, --workspace-root |
| `allin1-sdk compile-oiv-xml` | Compile official OIV XML commands into a verified inert RPF plan. | source, archive, --output / -o, --gta-path, --workspace-root |
| `allin1-sdk compile-vehicle-data` | Join vehicle metadata, assets, and registration data. | source, --output-dir / -o, --gta-path |
| `allin1-sdk configure-rpf-program-node` | Replace one operation node's validated JSON configuration. | program, node_id, config_json, --acknowledge-edit |
| `allin1-sdk connect-rpf-program-nodes` | Connect typed artifact/output pins and replace the target input link. | program, from_node, to_node, --acknowledge-edit |
| `allin1-sdk create-material-workspace` | Export one native model into a revisioned material workspace. | source, --output-dir / -o, --edition, --gta-path |
| `allin1-sdk create-ped-authoring` | Copy visible ped metadata into a safe editable workspace. | source, --output-dir / -o |
| `allin1-sdk create-rpf-change-set` | Create an inert source-bound workspace for staged atomic RPF changes. | archive, --gta-path, --output / -o |
| `allin1-sdk create-rpf-graph` | Create an empty or folder-imported visual RPF package graph. | source, --root-name, --output / -o |
| `allin1-sdk create-rpf-program` | Create a typed visual build program bound to one RPF package graph. | graph, --output / -o, --template |
| `allin1-sdk create-vehicle-authoring` | Copy visible vehicle DLC source into a safe editable workspace. | source, --output-dir / -o |
| `allin1-sdk create-weapon-authoring` | Copy visible weapon metadata into a safe editable workspace. | source, --output-dir / -o |
| `allin1-sdk defragment-rpf` | Create a smaller external RPF copy and exactly verify every leaf payload. | archive, --gta-path, --output / -o, --report |
| `allin1-sdk derive-rpf-plan` | Derive a guarded plan and changed payloads from before/after RPFs. | base, desired, --exact-content, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk detect-map-placements` | Detect installed DLC YMAP names with recursive RPF provenance. | pack_name, --descriptor, --expected-ipl, --gta-path |
| `allin1-sdk diff-meta` | Write a path-aware semantic diff for authored META/XML files. | before, after, --output / -o |
| `allin1-sdk diff-rpf` | Compare two recursive RPF trees and export JSON and Markdown reports. | left, right, --exact-content, --logical-content, --gta-path, --output / -o |
| `allin1-sdk disconnect-rpf-program-node` | Disconnect one node's typed input without removing the node. | program, node_id, --acknowledge-edit |
| `allin1-sdk dlc-inventory` | Inventory DLC folders and registrations. | gta_path, --output / -o |
| `allin1-sdk expand-rpf-graph-sealed` | Expand one immutable package RPF into retained editable graph nodes. | graph, node_id, --gta-path, --acknowledge-edit |
| `allin1-sdk export-legacy-vehicle-oiv` | Export validated Legacy vehicle files as a deterministic OIV. | package_root, destination, --author, --gta-path |
| `allin1-sdk export-managed-vehicle-package` | Create a schema-2 review package without installing it into GTA V. | source, destination, --edition, --gta-path, --package-id, --package-name, --version |
| `allin1-sdk export-native-workspace` | Export a native resource to an editable XML/dependency workspace. | source, --edition, --gta-path, --output / -o |
| `allin1-sdk export-rpf-binary-workspace` | Extract an exact RPF entry into an auditable same-size hex workspace. | archive, entry_path, --archive-path, --gta-path, --output / -o |
| `allin1-sdk export-rpf-gxt2-workspace` | Extract an exact GXT2 dictionary into a bound text workspace. | archive, entry_path, --archive-path, --gta-path, --output / -o |
| `allin1-sdk export-rpf-native-workspace` | Extract an RPF native asset into an editable CodeWalker XML workspace. | archive, entry_path, --archive-path, --gta-path, --output / -o |
| `allin1-sdk export-story-axle-runtime-config` | Translate a Workbench document into the native Story runtime schema. | config_json, --output / -o, --skeleton-xml, --gta-path, --update, --acknowledge-game-write, --acknowledge-edit |
| `allin1-sdk export-vehicle-axles` | Publish the model-specific FiveM axle runtime resource. | workspace, model, --output-dir / -o, --target, --update, --acknowledge-edit |
| `allin1-sdk export-vehicle-project` | Publish a portable vehicle asset project and relationship report. | source, --output-dir / -o, --gta-path |
| `allin1-sdk extract-rpf-entry` | Extract one exact root or nested-RPF entry. | archive, entry_path, --archive-path, --gta-path, --output / -o |
| `allin1-sdk extract-rpf-subtree` | Recursively export one root or nested-RPF directory with a hash manifest. | archive, --directory, --archive-path, --gta-path, --output / -o |
| `allin1-sdk import-package` | Scan a folder/archive and generate a review-only addon.json draft. | source, --output / -o |
| `allin1-sdk import-package-graph` | Create or reuse a persistent, provenance-checked package node graph. | source, --workspace-root |
| `allin1-sdk import-rpf-graph` | Expand an existing recursive RPF into an external visual graph workspace. | archive, --gta-path, --output / -o |
| `allin1-sdk index-rpf` | Export a structured recursive RPF index. | archive, --gta-path, --output / -o |
| `allin1-sdk inspect-binary-workspace` | Render a bounded hexdump from an auditable binary workspace. | workspace, --offset, --length |
| `allin1-sdk inspect-log` | Inspect bounded matching or trailing telemetry lines without editing. | log, --pattern, --max-lines |
| `allin1-sdk inspect-map-project` | Inventory map-native assets from a folder, archive, or direct RPF. | source, --gta-path |
| `allin1-sdk inspect-material-workspace` | Inspect the exact revision and material state of an editing workspace. | workspace |
| `allin1-sdk inspect-model-materials` | Inspect model hierarchy, shader usage, and typed texture bindings. | source, --edition, --gta-path, --asset |
| `allin1-sdk inspect-native-asset` | Inspect one native asset and optionally publish its bounded preview bundle. | source, --edition, --gta-path, --output-dir |
| `allin1-sdk inspect-package-graph-relations` | Inspect persisted vehicle links and relationship findings. | graph, --output / -o |
| `allin1-sdk inspect-package-receipt` | Inspect one validated managed-package receipt without changing GTA V. | mod_id, --gta-path |
| `allin1-sdk inspect-package-rpfs` | Index every loose RPF member of a package using temporary extraction. | source, --output-dir / -o, --gta-path |
| `allin1-sdk inspect-ped-authoring` | Inspect a ped workspace, validation state, and editable values. | workspace, --ped |
| `allin1-sdk inspect-product-workspace` | Audit a data-only product graph and each component's source coverage. | source, --include-files |
| `allin1-sdk inspect-rpf` | Write the helper's human-readable RPF inventory. | archive, --gta-path, --output / -o |
| `allin1-sdk inspect-rpf-change-set` | Inspect staged actions and optional source/payload verification. | change_set, --verify-files, --output / -o |
| `allin1-sdk inspect-rpf-graph` | Inspect nodes, edges, source hashes, and summary for one package graph. | graph, --output / -o |
| `allin1-sdk inspect-rpf-native-entry` | Inspect an exact root or nested-RPF asset without modifying its archive. | archive, entry_path, --archive-path, --gta-path, --output-dir, --safe-overwrite |
| `allin1-sdk inspect-rpf-program` | Inspect typed nodes, links, readiness issues, and execution order. | program, --verify-graph, --output / -o |
| `allin1-sdk inspect-source` | Inspect bounded source snippets around selected symbols without editing. | source, --symbol, --context-lines |
| `allin1-sdk inspect-story-axle-runtimes` | Verify explicit Story runtime profiles and target/build mappings. | --story-profile, --game-build |
| `allin1-sdk inspect-story-axle-toolchain` | Inspect the local native Story controller build prerequisites. | --source-root, --visual-studio-path, --ctest-path, --cmake-path, --toolchain-mode |
| `allin1-sdk inspect-vehicle-authoring` | Inspect a vehicle authoring workspace and its current validation state. | workspace, --model |
| `allin1-sdk inspect-vehicle-axles` | Inspect one saved axle configuration and its current evidence. | workspace, model, --skeleton-xml, --target |
| `allin1-sdk inspect-vehicle-distribution` | Inspect package-owned GBAY and ambient-traffic authoring metadata. | workspace, --model |
| `allin1-sdk inspect-vehicle-project` | Resolve a package's vehicle models, assets, and metadata links. | source, --model, --gta-path |
| `allin1-sdk inspect-vehicle-quick-import` | Inspect a vehicle archive for a no-write guided import. | source, --gta-path, --preferred-edition |
| `allin1-sdk inspect-vehicle-tuning` | Inspect tuning parts, performance entries, assets, and validation findings. | workspace, model, --kit |
| `allin1-sdk inspect-weapon-animation` | Inspect exact animation-set coverage retained for one weapon. | workspace, weapon, --source |
| `allin1-sdk inspect-weapon-authoring` | Inspect a weapon workspace, relationships, and editable values. | workspace, --weapon, --component |
| `allin1-sdk inspect-weapon-shop` | Inspect a weapon's exact existing storefront record and representations. | workspace, weapon, --source |
| `allin1-sdk inspect-workbench` | Return the Workbench's linked vehicle, weapon, and ped evidence as JSON. | source, --category, --gta-path |
| `allin1-sdk install-package` | Install a validated manifest, package folder, or bounded ZIP package. | manifest, --gta-path, --acknowledge-write |
| `allin1-sdk layout-rpf-graph` | Apply a deterministic readable tree layout to all graph nodes. | graph, --x-spacing, --y-spacing, --acknowledge-edit |
| `allin1-sdk layout-rpf-program` | Apply deterministic left-to-right layout to the operation graph. | program, --acknowledge-edit |
| `allin1-sdk link` | Write a linked integration and install-plan report. | manifest, --output / -o, --allow-failing-report |
| `allin1-sdk list` | List bundled SDK example manifests. |  |
| `allin1-sdk list-axle-prefabs` | List compatible behavior prefabs and independent tyre packages. | --axle-count, --layout, --category, --steering-type, --drive-type, --lift-axle / --no-lift-axle, --target, --experimental / --not-experimental |
| `allin1-sdk list-gxt2-entries` | List validated hash/text records from a GXT2 workspace. | workspace, --output / -o |
| `allin1-sdk list-installed-packages` | List receipt-backed mod packages installed in a GTA V edition. | --gta-path |
| `allin1-sdk list-rpf-program-templates` | List reusable visual RPF package program templates as JSON. |  |
| `allin1-sdk list-rpf-transactions` | List guarded RPF transaction history, including malformed receipts. | --gta-path, --receipt-dir, --output / -o |
| `allin1-sdk list-ytd-textures` | List validated texture records from a native YTD workspace. | workspace, --output / -o |
| `allin1-sdk materialize-rpf-graph` | Create a new provenance-safe loose tree with nested *.rpf.source folders. | graph, --output / -o |
| `allin1-sdk migrate-ped-identity` | Transactionally migrate ped metadata and owned streamed filenames. | workspace, ped, --new-name, --new-props, --expected-revision, --acknowledge-edit |
| `allin1-sdk migrate-vehicle-identity` | Transactionally migrate model/handling references and streamed filenames. | workspace, model, --new-model, --new-handling, --acknowledge-edit |
| `allin1-sdk move-rpf-change` | Move one staged action to a one-based review position. | change_set, action_id, position, --acknowledge-edit |
| `allin1-sdk move-vehicle-tuning-entry` | Reorder a tuning entry within its collection. | workspace, model, kit_name, collection, index, new_index, --acknowledge-edit |
| `allin1-sdk oiv-plan` | Preview an OIV recipe without executing it. | source, --output / -o, --managed-package, --rpf-batches, --created-rpf-package, --gta-path |
| `allin1-sdk open-axle-configurator` | Open the normal SDK desktop directly in a vehicle's Axle Configurator. | workspace, --model, --gta-path |
| `allin1-sdk open-launcher-package` | Reveal a prepared package in Launcher without installing it. | package_id, --traffic / --no-traffic |
| `allin1-sdk open-model-material-workbench` | Open a native model or package in the desktop Models & Materials workspace. | source, --gta-path |
| `allin1-sdk open-package-graph` | Open or reuse a complete persistent mod-package node graph. | source, --gta-path |
| `allin1-sdk open-product-workspace` | Open a validated product workspace in the existing Package Linker UI. | source |
| `allin1-sdk open-rpf-graph` | Open an RPF package graph in the desktop node editor. | graph, --gta-path, --focus-node |
| `allin1-sdk open-vehicle-workbench` | Open a vehicle add-on package in the desktop Workbench's Vehicles tab. | source, --gta-path |
| `allin1-sdk open-workbench` | Open vehicle, weapon, ped, and map projects in one desktop workspace. | source, --category, --gta-path |
| `allin1-sdk patch-binary-workspace` | Apply one same-size offset patch and append its hash-chained history. | workspace, --offset, --hex, --expected-hex, --acknowledge-edit |
| `allin1-sdk plan-axle-oiv` | Validate and preview a staged Story installer request. | request_json, --identity-store |
| `allin1-sdk plan-axle-runtime-bundle` | Plan cross-edition runtime outputs without creating files. | config_json, --target, --skeleton-xml, --story-profile, --game-build |
| `allin1-sdk plan-managed-vehicle-package` | Resolve one edition into a no-write managed-package conversion plan. | source, --edition, --gta-path, --package-id, --package-name, --version |
| `allin1-sdk plan-ped-clone` | Plan a complete donor-based ped record without changing files. | workspace, donor, --ped-name, --set |
| `allin1-sdk plan-rpf-add` | Create a checksummed plan to add a root or nested RPF entry. | archive, entry_path, payload, --archive-path, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk plan-rpf-batch` | Plan add/replace/delete/mkdir/rmdir/rename/upsert JSON changes atomically. | archive, change_manifest, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk plan-rpf-binary-workspace` | Build a bound same-size binary diff and create its reviewed RPF plan. | archive, entry_path, workspace, --archive-path, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk plan-rpf-change-set` | Compile a verified change set into the normal guarded atomic RPF plan. | change_set, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk plan-rpf-delete` | Create a checksummed plan to delete a root or nested RPF entry. | archive, entry_path, --archive-path, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk plan-rpf-graph-origin` | Build/diff an imported graph and emit an inert plan against its origin. | graph, --gta-path, --output / -o |
| `allin1-sdk plan-rpf-gxt2-workspace` | Rebuild/reparse a bound GXT2 workspace and create its reviewed RPF plan. | archive, entry_path, workspace, --archive-path, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk plan-rpf-native-workspace` | Rebuild/reparse a native workspace and create its RPF replacement plan. | archive, entry_path, workspace, --archive-path, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk plan-rpf-program` | Compile and bind a dry-run plan without executing operation nodes. | program, --output / -o |
| `allin1-sdk plan-rpf-replacement` | Create a checksummed replacement plan without writing the archive. | archive, entry_path, payload, --archive-path, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk plan-rpf-sync` | Plan all file and directory edits in a verified RPF subtree export. | archive, export_directory, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk plan-weapon-clone` | Plan a complete donor-based weapon bundle without changing files. | workspace, donor, --weapon-name, --slot, --ammo-info, --model, --human-name-hash, --stat-name, --ammo-mode, --ammo-name |
| `allin1-sdk position-rpf-graph-node` | Persist one node's visual canvas position. | graph, node_id, x, y, --acknowledge-edit |
| `allin1-sdk position-rpf-program-node` | Persist one operation node's canvas position. | program, node_id, x, y, --acknowledge-edit |
| `allin1-sdk prepare-vehicle-quick-import` | Prepare a reviewed vehicle package without writing to GTA V. | source, --edition, --gta-path, --package-id, --package-name, --version, --set, --destination, --publish-zip |
| `allin1-sdk preview-axle-prefab` | Preview canonical mapping and target compatibility without writing. | prefab_id, model, --skeleton-xml, --target, --export-mode, --base-config, --reported-wheel-count |
| `allin1-sdk preview-axle-steering` | Calculate signed per-axle steering gains without saving changes. | workspace, model, --skeleton-xml, --reference-lock, --pivot-y, --pivot-axle, --reference-axle, --steering-polarity, --target |
| `allin1-sdk preview-axle-tyres` | Preview visual tyres without adding runtime wheel indices. | package_id, config_json, --axle |
| `allin1-sdk propose-package-settings` | Ask Qwen for a typed advisory package-settings diff; never apply it. | request, --root, --timeout, --startup-timeout, --max-tokens, --no-progress |
| `allin1-sdk publish-managed-vehicle-package` | Publish a validated vehicle review folder as a deterministic ZIP. | package_root, destination, --gta-path |
| `allin1-sdk recover-rpf-transaction` | Reconcile an interrupted receipt without committing an archive write. | receipt, --gta-path, --workspace-root |
| `allin1-sdk redo-vehicle-edit` | Reapply the most recently undone guarded vehicle edit. | workspace, --acknowledge-edit |
| `allin1-sdk refresh-rpf-graph-sources` | Explicitly accept current size/hash values for changed graph sources. | graph, --acknowledge-edit |
| `allin1-sdk remove-gxt2-entry` | Remove one GXT2 record while retaining local undo history. | workspace, label_hash, --acknowledge-edit |
| `allin1-sdk remove-rpf-graph-node` | Remove a graph node and its descendants without deleting source files. | graph, node_id, --acknowledge-edit |
| `allin1-sdk remove-rpf-program-node` | Remove one operation node and its links without deleting artifacts. | program, node_id, --acknowledge-edit |
| `allin1-sdk remove-vehicle-tuning-entry` | Remove one tuning entry while retaining an undo snapshot. | workspace, model, kit_name, collection, index, --acknowledge-edit |
| `allin1-sdk remove-ytd-texture` | Remove one named texture while preserving local undo history. | workspace, texture_name, --acknowledge-edit |
| `allin1-sdk rename-rpf-graph-node` | Rename one graph node with sibling collision validation. | graph, node_id, name, --acknowledge-edit |
| `allin1-sdk render-native-model` | Compile one decoded YDR, YDD, or YFT model into an external PNG. | source, --output / -o, --edition, --gta-path, --texture-dictionary, --blender, --yaw, --pitch, --lens-mm, --lod, --component, --engine, --device, --quality, --width, --height, --samples, --light-rig, --light-rotation, --light-strength, --background, --background-color, --transparent / --opaque, --ground-plane / --no-ground-plane, --contact-shadows / --no-contact-shadows |
| `allin1-sdk render-rpf-graph-previews` | Render a hash-bound portable preview bundle for graph asset nodes. | graph, --gta-path, --limit, --output / -o |
| `allin1-sdk reparent-rpf-graph-node` | Reconnect a graph node to a validated archive or directory parent. | graph, node_id, parent_id, --acknowledge-edit |
| `allin1-sdk replace-ytd-texture` | Replace one texture using DDS or a converted raster image. | workspace, texture_name, image, --acknowledge-edit |
| `allin1-sdk rollback-rpf-transaction` | Roll back an applied receipt if the archive is still transaction-owned. | receipt, --gta-path, --workspace-root, --acknowledge-write |
| `allin1-sdk run-rpf-program` | Execute a ready external-authoring graph with exact failure cleanup. | program, --report, --acknowledge-execution |
| `allin1-sdk sdk` | Compatibility alias for commands previously hosted by the launcher. |  |
| `allin1-sdk sdk add-gxt2-entry` | Add one unique hash/text record to a GXT2 workspace. | workspace, label_hash, text, --acknowledge-edit |
| `allin1-sdk sdk add-rpf-graph-container` | Add a directory or nested archive below a graph parent node. | graph, parent_id, name, --archive, --x, --y, --acknowledge-edit |
| `allin1-sdk sdk add-rpf-graph-file` | Add a source-hashed file below a graph parent node. | graph, parent_id, source, --name, --x, --y, --acknowledge-edit |
| `allin1-sdk sdk add-rpf-program-node` | Add a typed operation node; the package and game remain unchanged. | program, node_type, --config-json, --x, --y, --acknowledge-edit |
| `allin1-sdk sdk add-ytd-texture` | Add one named texture using DDS or a converted raster image. | workspace, texture_name, image, --acknowledge-edit |
| `allin1-sdk sdk analyze-package-graph` | Resolve and persist typed vehicle relationships in a package graph. | graph, --output / -o |
| `allin1-sdk sdk apply-rpf-plan` | Apply a ready RPF plan through backup, staging, verification, and receipt. | plan, --gta-path, --workspace-root, --receipt-dir, --acknowledge-write |
| `allin1-sdk sdk audit-folder` | Audit all supported packages in a staging folder. | folder, --output / -o, --draft-dir |
| `allin1-sdk sdk build-binary-workspace` | Build a same-size binary asset and bounded changed-range report. | workspace, --output / -o |
| `allin1-sdk sdk build-gxt2-workspace` | Rebuild and semantically reparse an edited GXT2 text table. | workspace, --output / -o |
| `allin1-sdk sdk build-native-workspace` | Rebuild and reparse an edited native XML workspace. | workspace, --gta-path, --output / -o |
| `allin1-sdk sdk build-rpf-graph` | Materialize, build, exactly verify, and bind a graph-authored RPF. | graph, --gta-path, --output / -o |
| `allin1-sdk sdk build-rpf-tree` | Create and exactly verify a new RPF, including *.rpf.source subtrees. | source, --gta-path, --output / -o |
| `allin1-sdk sdk build-vehicle-package` | Publish a vehicle DLC as a validated, installable ALLIN1 package. | source, --output-dir / -o, --pack-name, --mod-id, --name, --version, --edition, --gta-path |
| `allin1-sdk sdk canary-rpf-transaction` | Prove real RPF apply/verify/rollback behavior on an isolated archive copy. | archive, --gta-path, --output-dir, --acknowledge-write |
| `allin1-sdk sdk catalog-rpfs` | Build or incrementally refresh a global loose-RPF search catalog. | source, --gta-path, --output / -o, --refresh |
| `allin1-sdk sdk clone-ped-bundle` | Apply one reviewed, revision-bound complete ped clone plan. | workspace, donor, --ped-name, --set, --expected-revision, --plan-sha256, --acknowledge-edit |
| `allin1-sdk sdk clone-weapon-animation` | Clone complete native animation mappings without editing clip payloads. | workspace, weapon, --template, --source, --expected-revision, --acknowledge-edit |
| `allin1-sdk sdk clone-weapon-bundle` | Apply one reviewed, revision-bound complete weapon clone plan. | workspace, donor, --weapon-name, --slot, --ammo-info, --model, --human-name-hash, --stat-name, --ammo-mode, --ammo-name, --expected-revision, --plan-sha256, --acknowledge-edit |
| `allin1-sdk sdk compile-oiv-xml` | Compile official OIV XML commands into a verified inert RPF plan. | source, archive, --output / -o, --gta-path, --workspace-root |
| `allin1-sdk sdk compile-vehicle-data` | Join vehicle metadata, assets, and registration data. | source, --output-dir / -o, --gta-path |
| `allin1-sdk sdk configure-rpf-program-node` | Replace one operation node's validated JSON configuration. | program, node_id, config_json, --acknowledge-edit |
| `allin1-sdk sdk connect-rpf-program-nodes` | Connect typed artifact/output pins and replace the target input link. | program, from_node, to_node, --acknowledge-edit |
| `allin1-sdk sdk create-ped-authoring` | Copy visible ped metadata into a safe editable workspace. | source, --output-dir / -o |
| `allin1-sdk sdk create-rpf-change-set` | Create an inert source-bound workspace for staged atomic RPF changes. | archive, --gta-path, --output / -o |
| `allin1-sdk sdk create-rpf-graph` | Create an empty or folder-imported visual RPF package graph. | source, --root-name, --output / -o |
| `allin1-sdk sdk create-rpf-program` | Create a typed visual build program bound to one RPF package graph. | graph, --output / -o, --template |
| `allin1-sdk sdk create-vehicle-authoring` | Copy visible vehicle DLC source into a safe editable workspace. | source, --output-dir / -o |
| `allin1-sdk sdk create-weapon-authoring` | Copy visible weapon metadata into a safe editable workspace. | source, --output-dir / -o |
| `allin1-sdk sdk defragment-rpf` | Create a smaller external RPF copy and exactly verify every leaf payload. | archive, --gta-path, --output / -o, --report |
| `allin1-sdk sdk derive-rpf-plan` | Derive a guarded plan and changed payloads from before/after RPFs. | base, desired, --exact-content, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk sdk diff-meta` | Write a path-aware semantic diff for authored META/XML files. | before, after, --output / -o |
| `allin1-sdk sdk diff-rpf` | Compare two recursive RPF trees and export JSON and Markdown reports. | left, right, --exact-content, --logical-content, --gta-path, --output / -o |
| `allin1-sdk sdk disconnect-rpf-program-node` | Disconnect one node's typed input without removing the node. | program, node_id, --acknowledge-edit |
| `allin1-sdk sdk dlc-inventory` | Inventory DLC folders and registrations. | gta_path, --output / -o |
| `allin1-sdk sdk expand-rpf-graph-sealed` | Expand one immutable package RPF into retained editable graph nodes. | graph, node_id, --gta-path, --acknowledge-edit |
| `allin1-sdk sdk export-native-workspace` | Export a native resource to an editable XML/dependency workspace. | source, --edition, --gta-path, --output / -o |
| `allin1-sdk sdk export-rpf-binary-workspace` | Extract an exact RPF entry into an auditable same-size hex workspace. | archive, entry_path, --archive-path, --gta-path, --output / -o |
| `allin1-sdk sdk export-rpf-gxt2-workspace` | Extract an exact GXT2 dictionary into a bound text workspace. | archive, entry_path, --archive-path, --gta-path, --output / -o |
| `allin1-sdk sdk export-rpf-native-workspace` | Extract an RPF native asset into an editable CodeWalker XML workspace. | archive, entry_path, --archive-path, --gta-path, --output / -o |
| `allin1-sdk sdk export-vehicle-project` | Publish a portable vehicle asset project and relationship report. | source, --output-dir / -o, --gta-path |
| `allin1-sdk sdk extract-rpf-entry` | Extract one exact root or nested-RPF entry. | archive, entry_path, --archive-path, --gta-path, --output / -o |
| `allin1-sdk sdk extract-rpf-subtree` | Recursively export one root or nested-RPF directory with a hash manifest. | archive, --directory, --archive-path, --gta-path, --output / -o |
| `allin1-sdk sdk import-package` | Scan a folder/archive and generate a review-only addon.json draft. | source, --output / -o |
| `allin1-sdk sdk import-package-graph` | Create or reuse a persistent, provenance-checked package node graph. | source, --workspace-root |
| `allin1-sdk sdk import-rpf-graph` | Expand an existing recursive RPF into an external visual graph workspace. | archive, --gta-path, --output / -o |
| `allin1-sdk sdk index-rpf` | Export a structured recursive RPF index. | archive, --gta-path, --output / -o |
| `allin1-sdk sdk inspect-binary-workspace` | Render a bounded hexdump from an auditable binary workspace. | workspace, --offset, --length |
| `allin1-sdk sdk inspect-native-asset` | Inspect one native asset and optionally publish its bounded preview bundle. | source, --edition, --gta-path, --output-dir |
| `allin1-sdk sdk inspect-package-graph-relations` | Inspect persisted vehicle links and relationship findings. | graph, --output / -o |
| `allin1-sdk sdk inspect-package-rpfs` | Index every loose RPF member of a package using temporary extraction. | source, --output-dir / -o, --gta-path |
| `allin1-sdk sdk inspect-ped-authoring` | Inspect a ped workspace, validation state, and editable values. | workspace, --ped |
| `allin1-sdk sdk inspect-product-workspace` | Audit a data-only product graph and each component's source coverage. | source, --include-files |
| `allin1-sdk sdk inspect-rpf` | Write the helper's human-readable RPF inventory. | archive, --gta-path, --output / -o |
| `allin1-sdk sdk inspect-rpf-change-set` | Inspect staged actions and optional source/payload verification. | change_set, --verify-files, --output / -o |
| `allin1-sdk sdk inspect-rpf-graph` | Inspect nodes, edges, source hashes, and summary for one package graph. | graph, --output / -o |
| `allin1-sdk sdk inspect-rpf-native-entry` | Inspect an exact root or nested-RPF asset without modifying its archive. | archive, entry_path, --archive-path, --gta-path, --output-dir, --safe-overwrite |
| `allin1-sdk sdk inspect-rpf-program` | Inspect typed nodes, links, readiness issues, and execution order. | program, --verify-graph, --output / -o |
| `allin1-sdk sdk inspect-vehicle-authoring` | Inspect a vehicle authoring workspace and its current validation state. | workspace, --model |
| `allin1-sdk sdk inspect-vehicle-distribution` | Inspect package-owned GBAY and ambient-traffic authoring metadata. | workspace, --model |
| `allin1-sdk sdk inspect-vehicle-project` | Resolve a package's vehicle models, assets, and metadata links. | source, --model, --gta-path |
| `allin1-sdk sdk inspect-weapon-animation` | Inspect exact animation-set coverage retained for one weapon. | workspace, weapon, --source |
| `allin1-sdk sdk inspect-weapon-authoring` | Inspect a weapon workspace, relationships, and editable values. | workspace, --weapon, --component |
| `allin1-sdk sdk inspect-weapon-shop` | Inspect a weapon's exact existing storefront record and representations. | workspace, weapon, --source |
| `allin1-sdk sdk layout-rpf-graph` | Apply a deterministic readable tree layout to all graph nodes. | graph, --x-spacing, --y-spacing, --acknowledge-edit |
| `allin1-sdk sdk layout-rpf-program` | Apply deterministic left-to-right layout to the operation graph. | program, --acknowledge-edit |
| `allin1-sdk sdk link` | Write a linked integration and install-plan report. | manifest, --output / -o, --allow-failing-report |
| `allin1-sdk sdk list` | List bundled SDK example manifests. |  |
| `allin1-sdk sdk list-gxt2-entries` | List validated hash/text records from a GXT2 workspace. | workspace, --output / -o |
| `allin1-sdk sdk list-rpf-program-templates` | List reusable visual RPF package program templates as JSON. |  |
| `allin1-sdk sdk list-rpf-transactions` | List guarded RPF transaction history, including malformed receipts. | --gta-path, --receipt-dir, --output / -o |
| `allin1-sdk sdk list-ytd-textures` | List validated texture records from a native YTD workspace. | workspace, --output / -o |
| `allin1-sdk sdk materialize-rpf-graph` | Create a new provenance-safe loose tree with nested *.rpf.source folders. | graph, --output / -o |
| `allin1-sdk sdk migrate-ped-identity` | Transactionally migrate ped metadata and owned streamed filenames. | workspace, ped, --new-name, --new-props, --expected-revision, --acknowledge-edit |
| `allin1-sdk sdk move-rpf-change` | Move one staged action to a one-based review position. | change_set, action_id, position, --acknowledge-edit |
| `allin1-sdk sdk oiv-plan` | Preview an OIV recipe without executing it. | source, --output / -o, --managed-package, --rpf-batches, --created-rpf-package, --gta-path |
| `allin1-sdk sdk open-product-workspace` | Open a validated product workspace in the existing Package Linker UI. | source |
| `allin1-sdk sdk patch-binary-workspace` | Apply one same-size offset patch and append its hash-chained history. | workspace, --offset, --hex, --expected-hex, --acknowledge-edit |
| `allin1-sdk sdk plan-ped-clone` | Plan a complete donor-based ped record without changing files. | workspace, donor, --ped-name, --set |
| `allin1-sdk sdk plan-rpf-add` | Create a checksummed plan to add a root or nested RPF entry. | archive, entry_path, payload, --archive-path, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk sdk plan-rpf-batch` | Plan add/replace/delete/mkdir/rmdir/rename/upsert JSON changes atomically. | archive, change_manifest, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk sdk plan-rpf-binary-workspace` | Build a bound same-size binary diff and create its reviewed RPF plan. | archive, entry_path, workspace, --archive-path, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk sdk plan-rpf-change-set` | Compile a verified change set into the normal guarded atomic RPF plan. | change_set, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk sdk plan-rpf-delete` | Create a checksummed plan to delete a root or nested RPF entry. | archive, entry_path, --archive-path, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk sdk plan-rpf-graph-origin` | Build/diff an imported graph and emit an inert plan against its origin. | graph, --gta-path, --output / -o |
| `allin1-sdk sdk plan-rpf-gxt2-workspace` | Rebuild/reparse a bound GXT2 workspace and create its reviewed RPF plan. | archive, entry_path, workspace, --archive-path, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk sdk plan-rpf-native-workspace` | Rebuild/reparse a native workspace and create its RPF replacement plan. | archive, entry_path, workspace, --archive-path, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk sdk plan-rpf-program` | Compile and bind a dry-run plan without executing operation nodes. | program, --output / -o |
| `allin1-sdk sdk plan-rpf-replacement` | Create a checksummed replacement plan without writing the archive. | archive, entry_path, payload, --archive-path, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk sdk plan-rpf-sync` | Plan all file and directory edits in a verified RPF subtree export. | archive, export_directory, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk sdk plan-weapon-clone` | Plan a complete donor-based weapon bundle without changing files. | workspace, donor, --weapon-name, --slot, --ammo-info, --model, --human-name-hash, --stat-name, --ammo-mode, --ammo-name |
| `allin1-sdk sdk position-rpf-graph-node` | Persist one node's visual canvas position. | graph, node_id, x, y, --acknowledge-edit |
| `allin1-sdk sdk position-rpf-program-node` | Persist one operation node's canvas position. | program, node_id, x, y, --acknowledge-edit |
| `allin1-sdk sdk recover-rpf-transaction` | Reconcile an interrupted receipt without committing an archive write. | receipt, --gta-path, --workspace-root |
| `allin1-sdk sdk refresh-rpf-graph-sources` | Explicitly accept current size/hash values for changed graph sources. | graph, --acknowledge-edit |
| `allin1-sdk sdk remove-gxt2-entry` | Remove one GXT2 record while retaining local undo history. | workspace, label_hash, --acknowledge-edit |
| `allin1-sdk sdk remove-rpf-graph-node` | Remove a graph node and its descendants without deleting source files. | graph, node_id, --acknowledge-edit |
| `allin1-sdk sdk remove-rpf-program-node` | Remove one operation node and its links without deleting artifacts. | program, node_id, --acknowledge-edit |
| `allin1-sdk sdk remove-ytd-texture` | Remove one named texture while preserving local undo history. | workspace, texture_name, --acknowledge-edit |
| `allin1-sdk sdk rename-rpf-graph-node` | Rename one graph node with sibling collision validation. | graph, node_id, name, --acknowledge-edit |
| `allin1-sdk sdk render-rpf-graph-previews` | Render a hash-bound portable preview bundle for graph asset nodes. | graph, --gta-path, --limit, --output / -o |
| `allin1-sdk sdk reparent-rpf-graph-node` | Reconnect a graph node to a validated archive or directory parent. | graph, node_id, parent_id, --acknowledge-edit |
| `allin1-sdk sdk replace-ytd-texture` | Replace one texture using DDS or a converted raster image. | workspace, texture_name, image, --acknowledge-edit |
| `allin1-sdk sdk rollback-rpf-transaction` | Roll back an applied receipt if the archive is still transaction-owned. | receipt, --gta-path, --workspace-root, --acknowledge-write |
| `allin1-sdk sdk run-rpf-program` | Execute a ready external-authoring graph with exact failure cleanup. | program, --report, --acknowledge-execution |
| `allin1-sdk sdk search-rpf-catalog` | Search a global RPF catalog by archive, nested path, or entry name. | catalog, query, --kind, --suffix, --limit, --output / -o |
| `allin1-sdk sdk set-gxt2-text` | Replace one GXT2 text value while retaining local undo history. | workspace, label_hash, text, --acknowledge-edit |
| `allin1-sdk sdk set-ped-fields` | Transactionally update copied ped metadata and revalidate the package. | workspace, ped, --set, --expected-revision, --acknowledge-edit |
| `allin1-sdk sdk set-vehicle-distribution` | Author one vehicle's GBAY listing and independent traffic eligibility. | workspace, model, --listed / --not-listed, --name, --manufacturer, --category, --price, --storage, --size-tier, --preview-dictionary, --preview-texture, --traffic-enabled / --traffic-disabled, --traffic-weight, --expected-revision, --acknowledge-edit |
| `allin1-sdk sdk set-vehicle-fields` | Transactionally update copied vehicle metadata and revalidate its links. | workspace, model, --set, --acknowledge-edit |
| `allin1-sdk sdk set-weapon-attachment` | Edit one existing weapon-to-component attachment link. | workspace, weapon, component, --set, --expected-revision, --acknowledge-edit |
| `allin1-sdk sdk set-weapon-component` | Transactionally edit one existing weapon-component definition. | workspace, component, --set, --expected-revision, --acknowledge-shared, --acknowledge-edit |
| `allin1-sdk sdk set-weapon-fields` | Transactionally edit an existing weapon and its linked ammo record. | workspace, weapon, --set, --expected-revision, --acknowledge-shared, --acknowledge-edit |
| `allin1-sdk sdk set-weapon-shop-fields` | Transactionally edit supported fields on an existing shop record. | workspace, weapon, --set, --source, --expected-revision, --acknowledge-edit |
| `allin1-sdk sdk stage-rpf-change` | Stage one inert action in a persistent RPF change-set workspace. | change_set, action, entry, --archive-path, --payload, --new-entry, --acknowledge-edit |
| `allin1-sdk sdk undo-binary-workspace` | Reverse the latest binary workspace operation and retain recovery history. | workspace, --acknowledge-edit |
| `allin1-sdk sdk undo-gxt2-edit` | Restore the GXT2 table before its latest recorded operation. | workspace, --acknowledge-edit |
| `allin1-sdk sdk undo-ped-edit` | Restore the latest ped metadata edit from retained local history. | workspace, --expected-revision, --acknowledge-edit |
| `allin1-sdk sdk undo-vehicle-edit` | Restore the latest vehicle metadata edit from retained local history. | workspace, --acknowledge-edit |
| `allin1-sdk sdk undo-weapon-edit` | Restore the latest weapon metadata edit from retained local history. | workspace, --expected-revision, --acknowledge-edit |
| `allin1-sdk sdk undo-ytd-texture-edit` | Restore the latest YTD texture edit while retaining recovery history. | workspace, --acknowledge-edit |
| `allin1-sdk sdk unstage-rpf-change` | Remove one inert action without changing its archive or payload. | change_set, action_id, --acknowledge-edit |
| `allin1-sdk sdk validate` | Validate an addon.json and its cross-file links. | manifest |
| `allin1-sdk sdk validate-meta-roundtrip` | Prove parse/serialize/reparse semantic equivalence for authored metadata. | source, --serialized-output, --output / -o |
| `allin1-sdk sdk validate-rpf-graph` | Validate the complete graph tree and every referenced source hash. | graph, --output / -o |
| `allin1-sdk sdk verify-rpf-archive` | Verify recursive structure and exact extraction of every RPF payload. | archive, --gta-path, --output / -o |
| `allin1-sdk sdk verify-rpf-transaction` | Verify a transaction's archive, entry, and rollback snapshot. | receipt, --gta-path, --workspace-root, --output / -o |
| `allin1-sdk search-rpf-catalog` | Search a global RPF catalog by archive, nested path, or entry name. | catalog, query, --kind, --suffix, --limit, --output / -o |
| `allin1-sdk set-geometry-material` | Assign one geometry to an existing shader in its local catalog. | workspace, geometry_index, material_index, --expected-revision, --acknowledge-edit |
| `allin1-sdk set-gxt2-text` | Replace one GXT2 text value while retaining local undo history. | workspace, label_hash, text, --acknowledge-edit |
| `allin1-sdk set-material-binding` | Edit existing shader and texture values without synthesizing XML nodes. | workspace, material_index, --shader-name, --texture, --expected-revision, --acknowledge-edit |
| `allin1-sdk set-ped-fields` | Transactionally update copied ped metadata and revalidate the package. | workspace, ped, --set, --expected-revision, --acknowledge-edit |
| `allin1-sdk set-vehicle-appearance` | Edit colors, liveries, tuning links, and light/siren selections. | workspace, model, --colors-json, --kits, --light-settings, --siren-settings, --acknowledge-edit |
| `allin1-sdk set-vehicle-axles` | Apply a versioned axle configuration in the guarded vehicle workspace. | workspace, config_json, --skeleton-xml, --expected-revision, --acknowledge-edit |
| `allin1-sdk set-vehicle-distribution` | Author one vehicle's GBAY listing and independent traffic eligibility. | workspace, model, --listed / --not-listed, --name, --manufacturer, --category, --price, --storage, --size-tier, --preview-dictionary, --preview-texture, --traffic-enabled / --traffic-disabled, --traffic-weight, --expected-revision, --acknowledge-edit |
| `allin1-sdk set-vehicle-fields` | Transactionally update copied vehicle metadata and revalidate its links. | workspace, model, --set, --acknowledge-edit |
| `allin1-sdk set-vehicle-light-profile` | Edit scalar values on one existing carcols light profile. | workspace, model, profile_id, --set, --acknowledge-edit |
| `allin1-sdk set-vehicle-tuning-entry` | Update scalar or array fields on one tuning entry. | workspace, model, kit_name, collection, index, --set, --acknowledge-edit |
| `allin1-sdk set-vehicle-tuning-kit` | Edit safe structured fields on an existing linked tuning kit. | workspace, model, kit_name, --kit-type, --livery-names, --acknowledge-edit |
| `allin1-sdk set-weapon-attachment` | Edit one existing weapon-to-component attachment link. | workspace, weapon, component, --set, --expected-revision, --acknowledge-edit |
| `allin1-sdk set-weapon-component` | Transactionally edit one existing weapon-component definition. | workspace, component, --set, --expected-revision, --acknowledge-shared, --acknowledge-edit |
| `allin1-sdk set-weapon-fields` | Transactionally edit an existing weapon and its linked ammo record. | workspace, weapon, --set, --expected-revision, --acknowledge-shared, --acknowledge-edit |
| `allin1-sdk set-weapon-shop-fields` | Transactionally edit supported fields on an existing shop record. | workspace, weapon, --set, --source, --expected-revision, --acknowledge-edit |
| `allin1-sdk stage-rpf-change` | Stage one inert action in a persistent RPF change-set workspace. | change_set, action, entry, --archive-path, --payload, --new-entry, --acknowledge-edit |
| `allin1-sdk undo-binary-workspace` | Reverse the latest binary workspace operation and retain recovery history. | workspace, --acknowledge-edit |
| `allin1-sdk undo-gxt2-edit` | Restore the GXT2 table before its latest recorded operation. | workspace, --acknowledge-edit |
| `allin1-sdk undo-material-edit` | Restore the last exact material XML snapshot after drift validation. | workspace, --expected-revision, --acknowledge-edit |
| `allin1-sdk undo-ped-edit` | Restore the latest ped metadata edit from retained local history. | workspace, --expected-revision, --acknowledge-edit |
| `allin1-sdk undo-vehicle-edit` | Restore the latest vehicle metadata edit from retained local history. | workspace, --acknowledge-edit |
| `allin1-sdk undo-weapon-edit` | Restore the latest weapon metadata edit from retained local history. | workspace, --expected-revision, --acknowledge-edit |
| `allin1-sdk undo-ytd-texture-edit` | Restore the latest YTD texture edit while retaining recovery history. | workspace, --acknowledge-edit |
| `allin1-sdk uninstall-package` | Uninstall one managed package using its verified receipt and backups. | mod_id, --gta-path, --acknowledge-write |
| `allin1-sdk unstage-rpf-change` | Remove one inert action without changing its archive or payload. | change_set, action_id, --acknowledge-edit |
| `allin1-sdk validate` | Validate an addon.json and its cross-file links. | manifest |
| `allin1-sdk validate-map-project` | Validate a declarative map, level, portal, and garage project. | descriptor |
| `allin1-sdk validate-meta-roundtrip` | Prove parse/serialize/reparse semantic equivalence for authored metadata. | source, --serialized-output, --output / -o |
| `allin1-sdk validate-package` | Validate a mod.toml, package folder, or bounded ZIP package. | manifest |
| `allin1-sdk validate-package-settings-proposal` | Validate a typed advisory diff against its immutable host request. | request, proposal |
| `allin1-sdk validate-rpf-graph` | Validate the complete graph tree and every referenced source hash. | graph, --output / -o |
| `allin1-sdk verify-package-ownership` | Verify receipt-owned files, backups, and RPF entries without mutation. | mod_id, --gta-path |
| `allin1-sdk verify-rpf-archive` | Verify recursive structure and exact extraction of every RPF payload. | archive, --gta-path, --output / -o |
| `allin1-sdk verify-rpf-transaction` | Verify a transaction's archive, entry, and rollback snapshot. | receipt, --gta-path, --workspace-root, --output / -o |
