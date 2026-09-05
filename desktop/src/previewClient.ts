import type { AssistantPromptResult, AssistantStatusResult, AssetPreviewResult, DesktopCatalog, DesktopClient, Envelope, LaunchRequest, ModelMaterialProjectResult, PackageLifecycleExecutionResult, PackageLifecycleReviewResult, PackageReceiptResult, PackageResult, RecipePlanResult, RpfArchiveResult, TextureRecord, TextureWorkspaceSession, VehicleAppearance, VehicleAuthoringSession, VehicleAxleConfiguration, VehicleDistributionValues, VehiclePackageBuildResult, VehicleProjectResult, VehicleQuickImportResult, VehicleQuickImportReviewResult, VehicleTransmissionConfiguration, VehicleTuningBuilder, VehicleViewportResult } from "./types";

import { weaponPreviewSnapshot, weaponPreviewReview } from "./weaponPreview";
import { pedPreviewSnapshot, pedPreviewReview } from "./pedPreview";
import { gxt2PreviewSession, gxt2PreviewReview } from "./gxt2Preview";
import { rpfChangePreviewSession, rpfChangePreviewReview } from "./rpfChangePreview";
import { rpfTransactionPreviewSession, rpfTransactionPreviewReview } from "./rpfTransactionPreview";
import { helpPreviewTopics } from "./helpPreview";
import packageInfo from "../package.json";

const previewGraphDocument = {
  schema_version: 1, operation: "rpf_package_graph", root_id: "root",
  origin: { type: "mod_package_import", path: "C:\\SDK\\packages\\comet6.zip", entries: 4 },
  nodes: [
    { id: "root", type: "archive", name: "package-preview.rpf", x: 35, y: 40 },
    { id: "models", type: "directory", name: "stream", x: 310, y: 40 },
    { id: "fragment", type: "file", name: "comet6.yft", x: 585, y: 40, source: "C:\\SDK\\graphs\\package-source\\stream\\comet6.yft", size: 246810, sha256: "a".repeat(64) },
  ],
  edges: [{ parent: "root", child: "models" }, { parent: "models", child: "fragment" }],
  semantic: { generated_utc: "2026-09-04T12:00:00Z", analyzer: "vehicle_relationships", schema_version: 1,
    entities: [{ id: "vehicle-comet6", type: "vehicle", name: "comet6", x: 585, y: 180, source_root: "C:\\SDK\\graphs\\package-source", edition: "Enhanced", metadata: { display_name: "Comet S2", handling_id: "COMET6", vehicle_class: "VC_SPORT" } }],
    relations: [{ source: "vehicle-comet6", target: "fragment", role: "primary_model" }], findings: [],
    summary: { entities: 1, relations: 1, errors: 0, warnings: 0, analysis_roots: 1, relation_groups: { assets: 1 } } },
};
function graphWorkspacePreview(operation: string, payload: Record<string, unknown>) {
  const common = { module: "graph", schema_version: 1, game_write_performed: false };
  if (operation === "review_workspace_action") return { ...common, kind: "workspace_review", review_only: true,
    action: String(payload.action), state_sha256: "0".repeat(64), request_sha256: "1".repeat(64), review_sha256: "2".repeat(64),
    source: String(payload.source ?? "C:\\SDK\\packages\\comet6.zip"), destination: String(payload.destination),
    outputs: [String(payload.destination)], document: previewGraphDocument };
  return { ...common, kind: "workspace_session", read_only: true, workspace: "C:\\SDK\\graphs\\comet6\\package-graph.json",
    state_sha256: "0".repeat(64), document: previewGraphDocument, issues: [] };
}

const PREVIEW_CATALOG: DesktopCatalog = {
  navigation: [
    { id: "data_tools", label: "Data Tools", shortcut: "Ctrl+9", phase: 5 },
    { id: "linker", label: "Package Linker", shortcut: "Ctrl+1", phase: 3 },
    { id: "assets", label: "Asset Viewer", shortcut: "Ctrl+2", phase: 4 },
    { id: "workbench", label: "Content Workbench", shortcut: "Ctrl+3", phase: 3 },
    { id: "receipts", label: "Package Receipts", shortcut: "Ctrl+8", phase: 4 },
    { id: "quick_import", label: "Quick Import", shortcut: "Ctrl+I", phase: 4 },
    { id: "models", label: "Models & Materials", shortcut: "Ctrl+4", phase: 5 },
    { id: "rpf", label: "RPF Archives", shortcut: "Ctrl+5", phase: 3 },
    { id: "recipes", label: "Package Recipes", shortcut: "Ctrl+6", phase: 4 },
    { id: "help", label: "Help Center", shortcut: "Ctrl+7", phase: 3 },
  ],
  commands: [
    { name: "validate", description: "Validate a package manifest", risk: "read_only", parameters: [] },
    { name: "inspect-rpf", description: "Inspect a loose RPF archive", risk: "read_only", parameters: [] },
  ],
  help_topics: [{
    key: "getting-started",
    category: "Start here",
    title: "Getting started",
    summary: "Open a package and review its bounded evidence.",
    body: "Choose a package manifest, folder, or archive.\nReview inventory and diagnostics before authoring any output.",
    keywords: ["package", "inspect"],
  }],
operations: ["handshake", "catalog", "execute", "inspect_package", "preview_asset", "render_vehicle_model", "inspect_model_materials", "inspect_model_material_workspace", "review_model_material_workspace", "create_model_material_workspace", "review_model_material_edit", "apply_model_material_edit", "apply_model_material_history", "review_model_material_build", "apply_model_material_build", "inspect_texture_workspace", "review_texture_workspace", "create_texture_workspace", "preview_texture_workspace", "review_texture_edit", "apply_texture_edit", "apply_texture_history", "review_texture_build", "apply_texture_build", "assistant_status", "assistant_prompt", "inspect_rpf_archive", "inspect_vehicle_project", "inspect_vehicle_authoring_workspace", "review_vehicle_authoring_workspace", "create_vehicle_authoring_workspace", "review_vehicle_authoring_edit", "apply_vehicle_authoring_edit", "review_vehicle_authoring_appearance", "apply_vehicle_authoring_appearance", "inspect_vehicle_authoring_tuning", "review_vehicle_authoring_tuning", "apply_vehicle_authoring_tuning", "review_vehicle_authoring_light_profile", "apply_vehicle_authoring_light_profile", "review_vehicle_authoring_axles", "apply_vehicle_authoring_axles", "inspect_vehicle_authoring_axle_skeleton", "review_vehicle_authoring_transmission", "apply_vehicle_authoring_transmission", "review_vehicle_authoring_distribution", "apply_vehicle_authoring_distribution", "review_vehicle_package_build", "apply_vehicle_package_build", "apply_vehicle_authoring_history", "inspect_recipe", "inspect_package_receipts", "review_package_lifecycle", "apply_package_lifecycle", "inspect_vehicle_quick_import", "review_vehicle_quick_import", "prepare_vehicle_quick_import"],
  job_operations: ["inspect_ped_workbench", "review_ped_authoring", "inspect_weapon_workbench", "review_weapon_authoring", "execute", "inspect_package", "preview_asset", "inspect_model_materials", "inspect_model_material_workspace", "review_model_material_workspace", "review_model_material_edit", "review_model_material_build", "inspect_texture_workspace", "review_texture_workspace", "preview_texture_workspace", "review_texture_edit", "review_texture_build", "assistant_status", "assistant_prompt", "inspect_rpf_archive", "inspect_vehicle_project", "inspect_vehicle_authoring_workspace", "review_vehicle_authoring_workspace", "review_vehicle_authoring_edit", "review_vehicle_authoring_appearance", "inspect_vehicle_authoring_tuning", "review_vehicle_authoring_tuning", "review_vehicle_authoring_light_profile", "review_vehicle_authoring_axles", "inspect_vehicle_authoring_axle_skeleton", "review_vehicle_authoring_transmission", "review_vehicle_authoring_distribution", "review_vehicle_package_build", "inspect_recipe", "inspect_package_receipts", "review_package_lifecycle", "inspect_vehicle_quick_import", "review_vehicle_quick_import"],
};

const SAMPLE_MODEL_MATERIAL_RESULT: ModelMaterialProjectResult = {
  kind: "model_material_project",
  operation: "inspect_model_materials",
  source: "C:\\SDK\\models\\comet6.yft",
  name: "comet6.yft",
  suffix: ".yft",
  edition: "Enhanced",
  size: 4_826_112,
  sha256: "0fc4711ea23719a55f2243c1b5ed7a6b8e4970644a2395971881818eb847d03d",
  revision: null,
  summary: { materials: 4, texture_bindings: 6, numeric_parameters: 2, geometries: 5, components: 3, errors: 0, warnings: 1 },
  materials: [
    { index: 0, shader: "vehicle_paint1", textures: [{ slot: "DiffuseSampler", texture: "comet6_sign_1", role: "color" }, { slot: "BumpSampler", texture: "vehicle_generic_smallspecmap", role: "normal" }], parameters: [{ name: "specularIntensityMult", source_type: "Vector", values: [[0.5, 0, 0, 0]] }, { name: "detailSettings", source_type: "Array", values: [[1, 0.72, 0.18, 0], [4, 2, 1, 0]] }], geometry_indices: [0, 1] },
    { index: 1, shader: "vehicle_vehglass", textures: [{ slot: "DiffuseSampler", texture: "comet6_glass", role: "color" }], parameters: [], geometry_indices: [2] },
    { index: 2, shader: "vehicle_tire", textures: [{ slot: "DiffuseSampler", texture: "vehicle_generic_tyrewallblack", role: "color" }], parameters: [], geometry_indices: [3] },
    { index: 3, shader: "vehicle_interior2", textures: [{ slot: "DiffuseSampler", texture: "comet6_interior", role: "color" }, { slot: "BumpSampler", texture: "comet6_interior_n", role: "normal" }], parameters: [], geometry_indices: [4] },
  ],
  geometries: [
    { index: 0, component: "Chassis", lod: "High", material_index: 0, material_document_index: 0, material_name: "vehicle_paint1", available_materials: ["vehicle_paint1", "vehicle_vehglass"] },
    { index: 1, component: "Door LF", lod: "High", material_index: 0, material_document_index: 0, material_name: "vehicle_paint1", available_materials: ["vehicle_paint1", "vehicle_vehglass"] },
    { index: 2, component: "Windows", lod: "High", material_index: 1, material_document_index: 1, material_name: "vehicle_vehglass", available_materials: ["vehicle_paint1", "vehicle_vehglass"] },
    { index: 3, component: "Wheel LF", lod: "High", material_index: 2, material_document_index: 2, material_name: "vehicle_tire", available_materials: ["vehicle_tire"] },
    { index: 4, component: "Interior", lod: "Medium", material_index: 3, material_document_index: 3, material_name: "vehicle_interior2", available_materials: ["vehicle_interior2"] },
  ],
  components: [{ name: "Chassis" }, { name: "Wheel LF" }, { name: "Interior" }],
  lods: ["High", "Medium", "Low"],
  metadata: { model_total_triangles: 154_810 },
  findings: [{ severity: "warning", code: "unresolved_texture", message: "One shared texture resolves through the game texture dictionary.", subject: "vehicle_generic_smallspecmap" }],
  viewport: { source: "C:\\SDK\\models", entry: "comet6.yft", texture_entry: "comet6.ytd", collision_entry: "comet6.ybn" },
  read_only: true,
  workspace_write_performed: false,
  package_write_performed: false,
  game_write_performed: false,
};

const SAMPLE_ASSISTANT_STATUS: AssistantStatusResult = {
  kind: "assistant_status", configured: true, enabled: true, mode: "managed_local",
  model: "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf", workflow: "sdk_assistant",
  profile: "balanced", local_runtime_running: false, structured_output_ready: true,
  provider_capabilities: ["json_schema", "qwen_thinking"], thinking: "provider_default",
  message: "Assistant configuration is ready.", read_only: true,
};

const SAMPLE_ASSISTANT_RESULT: AssistantPromptResult = {
  kind: "assistant_prompt_result", text: "", model: "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
  mode: "managed_local", elapsed_seconds: 4.8,
  advisory: { summary: "The selected model is structurally sound; verify the unresolved shared normal map against the target edition before packaging.", findings: [{ severity_domain: "engineering", severity: "medium", evidence: "The material inventory contains one texture binding that resolves through the game texture dictionary rather than the sibling YTD.", file: "comet6.yft", line: null, confidence: 0.91, status: "confirmed" }], recommended_operations: [], proposed_changes: [], missing_context: ["Target-edition game texture dictionary"], abstentions: ["No package or game operation was executed."] },
  safety_flags: ["advisory_only"], estimated_input_tokens: 1218, actual_input_tokens: 1194,
  actual_output_tokens: 186, receipt_path: "C:\\SDK\\receipts\\assistant-preview.json",
  thinking: "provider_default", read_only: true, advisory_only: true,
  command_execution_performed: false, workspace_write_performed: false,
  package_write_performed: false, game_write_performed: false,
};

const SAMPLE_ENTRIES = [
  { path: "content.xml", category: "Metadata", preview_kind: "text", size: 4_218 },
  { path: "dlc.rpf", category: "Archives", preview_kind: "binary", size: 17_924_096 },
  { path: "preview/comet6.png", category: "Images", preview_kind: "image", size: 482_106 },
  { path: "README.txt", category: "Text", preview_kind: "text", size: 79_692 },
];

const SAMPLE_RESULT: PackageResult = {
  kind: "package_scan",
  source: "C:\\SDK\\projects\\street-pack",
  valid: false,
  error_count: 0,
  warning_count: 2,
  file_count: 4,
  inventory_count: 4,
  total_bytes: 18_490_112,
  edition: "Enhanced",
  entries: SAMPLE_ENTRIES,
  findings: [
    { severity: "warning", code: "target-edition", message: "Confirm the target GTA V edition before compiling the install plan." },
    { severity: "info", code: "preview-bounded", message: "Preview assets were read without executing package content." },
  ],
};

const SAMPLE_MANIFEST_RESULT: PackageResult = {
  kind: "manifest",
  source: "C:\\SDK\\projects\\street-pack\\addon.json",
  source_root: "C:\\SDK\\projects\\street-pack",
  id: "allin1.street_pack",
  name: "Street Pack",
  version: "1.2.0",
  summary: "A bounded package integration fixture.",
  editions: ["enhanced"],
  valid: false,
  error_count: 0,
  warning_count: 1,
  nodes: [
    { id: "package.main", kind: "package", label: "Street Pack", source: "dlc.rpf", fields: { Registration: "dlclist.xml", Edition: "enhanced", Safety: "reviewed" } },
    { id: "vehicle.comet6", kind: "vehicle", label: "Comet S2", source: "vehicles.meta", fields: { ModelName: "comet6", HandlingId: "COMET6" } },
  ],
  references: [
    { id: "vehicle-registration", source: "vehicle.comet6", source_field: "ModelName", target: "package.main", target_field: "Registration", relationship: "registers_vehicle", required: true, valid: true, message: "Reference resolved." },
  ],
  issues: [{ severity: "warning", code: "edition-review", message: "Confirm the Enhanced target before export.", subject: "package.main" }],
  install_steps: [{ step_id: "register-dlc", order: 10, label: "Register DLC", target: "dlclist.xml", strategy: "merge", source: "content.xml" }],
};

const SAMPLE_RECIPE_RESULT: RecipePlanResult = {
  kind: "recipe_plan",
  source: "C:\\SDK\\recipes\\street-lighting.oiv",
  name: "Street Lighting",
  version: "1.4",
  author: "ALLIN1 Studio",
  format_version: "2.2",
  editions: ["enhanced"],
  assembly_sha256: "7d4ac72c8ff1f0fc5de2d7d7a022282046c9752c7d41e2f33505655baf12539a",
  readiness: "existing_rpf_compile_ready",
  readiness_label: "EXISTING RPF COMPILE READY",
  operation_count: 4,
  error_count: 0,
  warning_count: 1,
  recipe_supported: true,
  translatable: false,
  managed_exportable: false,
  rpf_recipe_compilable: true,
  operations: [
    { number: 1, kind: "add", source: "content/vehicles.meta", target: "common/data/levels/gta5/vehicles.meta", archives: ["mods/update/update.rpf"], supported: true, detail: "Add the declared vehicle metadata payload.", creates_archive: false, creates_file: false, edits: [] },
    { number: 2, kind: "xml", source: "", target: "common/data/dlclist.xml", archives: ["mods/update/update.rpf"], supported: true, detail: "Append the street-lighting DLC registration when absent.", creates_archive: false, creates_file: false, edits: [{ action: "add", xpath: "/SMandatoryPacksData/Paths" }] },
    { number: 3, kind: "text", source: "", target: "common/data/extracontentmounts.meta", archives: ["mods/update/update.rpf"], supported: true, detail: "Insert one bounded mount declaration.", creates_archive: false, creates_file: false, edits: [{ action: "add-line" }] },
    { number: 4, kind: "add", source: "content/street-lighting.rpf", target: "dlcpacks/street-lighting/dlc.rpf", archives: ["mods/update/x64/dlcpacks.rpf"], supported: true, detail: "Stage the authored DLC archive as one exact entry.", creates_archive: false, creates_file: false, edits: [] },
  ],
  findings: [
    { severity: "warning", code: "target_archive_required", operation: 1, message: "Select and hash-bind the matching Enhanced update.rpf before compiling this recipe." },
  ],
};

const SAMPLE_QUICK_IMPORT_RESULT: VehicleQuickImportResult = {
  kind: "vehicle_quick_import_inspection",
  operation: "inspect_vehicle_quick_import",
  source: "C:\\SDK\\imports\\comet-s2-package.zip",
  source_kind: "archive",
  available_editions: ["legacy", "enhanced"],
  suggested_edition: "enhanced",
  edition_basis: "package_branches",
  vehicles: [
    { model: "blista", edition: "legacy", display_name: "Blista", manufacturer: "Dinka", vehicle_class: "Compacts" },
    { model: "comet6", edition: "enhanced", display_name: "Comet S2", manufacturer: "Pfister", vehicle_class: "Sports" },
    { model: "comet6c", edition: "enhanced", display_name: "Comet S2 Cabrio", manufacturer: "Pfister", vehicle_class: "Sports" },
  ],
  errors: 0,
  warnings: 1,
  branch_count: 2,
  vehicle_count: 3,
  game_write_performed: false,
  package_write_performed: false,
};

const SAMPLE_AXLE_CONFIGURATION: VehicleAxleConfiguration = {
  schema_version: 1,
  vehicle_model: "comet6",
  configuration_id: "comet6-axles",
  model_hash: "0x991EFC04",
  minimum_runtime_version: "1.0.0",
  preset: "Steer → Drive → Rear Steer",
  export_mode: "selective_runtime",
  expected_wheel_count: 6,
  axles: [
    { physical_order: 1, logical_role: "front", left_bone: "wheel_lf", right_bone: "wheel_rf", left_runtime_index: 0, right_runtime_index: 1, steered: true, powered: false, service_brake: true, handbrake: false, visual_family: "front", addon_geometry: [] },
    { physical_order: 2, logical_role: "middle", left_bone: "wheel_lm1", right_bone: "wheel_rm1", left_runtime_index: 4, right_runtime_index: 5, steered: false, powered: true, service_brake: true, handbrake: false, visual_family: "shared_middle_rear", addon_geometry: [] },
    { physical_order: 3, logical_role: "rear", left_bone: "wheel_lr", right_bone: "wheel_rr", left_runtime_index: 2, right_runtime_index: 3, steered: true, powered: false, service_brake: true, handbrake: true, visual_family: "shared_middle_rear", addon_geometry: [] },
  ],
  runtime_reapplication: { on_entity_created: true, on_network_ownership: true, after_repair: true, on_resource_restart: true, recovery_check_ms: 1500 },
  compatibility: { "fivem-legacy": true, "fivem-enhanced": false, "story-legacy": false, "story-enhanced": false },
  handbrake_rear_steering: false,
  steering_command_polarity: "normal",
};

const SAMPLE_VEHICLE_PROJECT_RESULT: VehicleProjectResult = {
  kind: "vehicle_project_inspection",
  operation: "inspect_vehicle_project",
  source: "C:\\SDK\\projects\\street-pack",
  source_kind: "folder",
  gta_path: "C:\\Games\\Grand Theft Auto V Enhanced",
  edition: "enhanced",
  inventory_fingerprint: "74fd1d4e05ba879f083b5851a1af07177f42f3a9fa3aca6e699fe8c981a05e4e",
  models: [
    {
      model: "comet6",
      display_name: "Comet S2",
      make_name: "Pfister",
      vehicle_class: "Sports",
      vehicle_type: "Automobile",
      handling_id: "COMET6",
      layout: "LAYOUT_LOW",
      audio_name_hash: "comet2",
      texture_dictionary: "comet6",
      tuning_kits: ["comet6_modkit"],
      assets: [
        { role: "primary_model", path: "x64/levels/gta5/vehicles/comet6.yft", size: 4_826_112, required: true, previewable: true },
        { role: "texture_dictionary", path: "x64/levels/gta5/vehicles/comet6.ytd", size: 8_204_288, required: true, previewable: true },
        { role: "collision_dictionary", path: "x64/levels/gta5/vehicles/comet6.ybn", size: 384_640, required: false, previewable: true },
        { role: "vehicles_meta", path: "common/data/levels/gta5/vehicles.meta", size: 12_642, required: true, previewable: false },
      ],
      findings: [],
      primary_model: "x64/levels/gta5/vehicles/comet6.yft",
      high_detail_model: null,
      texture_asset: "x64/levels/gta5/vehicles/comet6.ytd",
      collision_asset: "x64/levels/gta5/vehicles/comet6.ybn",
      ready_for_preview: true,
      complete: true,
      asset_count: 4,
      finding_count: 0,
      assets_truncated: false,
      findings_truncated: false,
    },
    {
      model: "sultanrs",
      display_name: "Sultan RS",
      make_name: "Karin",
      vehicle_class: "Super",
      vehicle_type: "Automobile",
      handling_id: "SULTANRS",
      layout: "LAYOUT_STD",
      audio_name_hash: "sultan2",
      texture_dictionary: "sultanrs",
      tuning_kits: [],
      assets: [
        { role: "primary_model", path: "x64/levels/gta5/vehicles/sultanrs.yft", size: 3_962_880, required: true, previewable: true },
      ],
      findings: [{ severity: "warning", code: "missing_texture_dictionary", model: "sultanrs", message: "No owned texture dictionary was resolved for this model." }],
      primary_model: "x64/levels/gta5/vehicles/sultanrs.yft",
      high_detail_model: null,
      texture_asset: null,
      collision_asset: null,
      ready_for_preview: true,
      complete: false,
      asset_count: 1,
      finding_count: 1,
      assets_truncated: false,
      findings_truncated: false,
    },
  ],
  findings: [],
  axle_configurations: [SAMPLE_AXLE_CONFIGURATION],
  model_count: 2,
  returned_model_count: 2,
  asset_count: 5,
  returned_asset_count: 5,
  previewable_count: 2,
  complete_count: 1,
  error_count: 0,
  warning_count: 0,
  model_finding_count: 1,
  truncated: false,
  read_only: true,
  package_write_performed: false,
  game_write_performed: false,
};

const SAMPLE_RPF_RESULT: RpfArchiveResult = {
  kind: "rpf_archive_index",
  operation: "inspect_rpf_archive",
  source: "C:\\Games\\Grand Theft Auto V Enhanced\\mods\\update\\update.rpf",
  gta_path: "C:\\Games\\Grand Theft Auto V Enhanced",
  edition: "enhanced",
  archive_size: 1_489_288_192,
  archives: [
    { path: "", name: "update.rpf", version: 7, encryption: "AES", size: 1_489_288_192, entry_count: 7 },
    { path: "x64/data.rpf", name: "data.rpf", version: 7, encryption: "none", size: 412_844_032, entry_count: 3 },
  ],
  entries: [
    { id: "::common", archive_path: "", path: "common", name: "common", kind: "directory", size: 0, stored_size: 0, encrypted: null, compressed: null, resource_version: null },
    { id: "::common/data", archive_path: "", path: "common/data", name: "data", kind: "directory", size: 0, stored_size: 0, encrypted: null, compressed: null, resource_version: null },
    { id: "::common/data/dlclist.xml", archive_path: "", path: "common/data/dlclist.xml", name: "dlclist.xml", kind: "binary", size: 18_422, stored_size: 4_908, encrypted: false, compressed: true, resource_version: null },
    { id: "::common/data/handling.meta", archive_path: "", path: "common/data/handling.meta", name: "handling.meta", kind: "binary", size: 128_640, stored_size: 23_401, encrypted: false, compressed: true, resource_version: null },
    { id: "::x64/data.rpf", archive_path: "", path: "x64/data.rpf", name: "data.rpf", kind: "archive", size: 412_844_032, stored_size: 412_844_032, encrypted: false, compressed: false, resource_version: null },
    { id: "x64/data.rpf::textures", archive_path: "x64/data.rpf", path: "textures", name: "textures", kind: "directory", size: 0, stored_size: 0, encrypted: null, compressed: null, resource_version: null },
    { id: "x64/data.rpf::textures/vehshare.ytd", archive_path: "x64/data.rpf", path: "textures/vehshare.ytd", name: "vehshare.ytd", kind: "resource", size: 4_828_160, stored_size: 3_109_504, encrypted: false, compressed: true, resource_version: 13 },
    { id: "x64/data.rpf::text/global.gxt2", archive_path: "x64/data.rpf", path: "text/global.gxt2", name: "global.gxt2", kind: "binary", size: 512, stored_size: 320, encrypted: false, compressed: true, resource_version: null },
    { id: "x64/data.rpf::levels/gta5/vehicles.rpf", archive_path: "x64/data.rpf", path: "levels/gta5/vehicles.rpf", name: "vehicles.rpf", kind: "archive", size: 94_322_688, stored_size: 94_322_688, encrypted: false, compressed: false, resource_version: null },
  ],
  warnings: ["One nested archive layer is displayed from the recursive helper index."],
  suffix_counts: { ".xml": 1, ".meta": 1, ".rpf": 2, ".ytd": 1, ".gxt2": 1 },
  archive_count: 2,
  entry_count: 9,
  returned_entry_count: 9,
  directory_count: 3,
  file_count: 6,
  logical_bytes: 512_142_454,
  stored_bytes: 510_304_853,
  truncated: false,
  read_only: true,
  game_write_performed: false,
};

const SAMPLE_RECEIPT_RESULT: PackageReceiptResult = {
  kind: "package_receipt_inventory",
  operation: "inspect_package_receipts",
  gta_path: "C:\\Games\\Grand Theft Auto V Enhanced",
  edition: "enhanced",
  receipt_root: "C:\\Games\\Grand Theft Auto V Enhanced\\scripts\\.allin1\\mods",
  packages: [
    { mod_id: "allin1.street-pack", name: "Street Pack", version: "1.2.0", mod_type: "mixed", enabled: true },
    { mod_id: "allin1.photo-tools", name: "Photo Tools", version: "2.0.1", mod_type: "script", enabled: false },
  ],
  selected_id: null,
  receipt: null,
  verification: null,
  package_count: 2,
  enabled_count: 1,
  check_count: 0,
  issue_count: 0,
  read_only: true,
  game_write_performed: false,
};

function receiptFixture(selectedId: string | null): PackageReceiptResult {
  if (!selectedId) return SAMPLE_RECEIPT_RESULT;
  const selected = SAMPLE_RECEIPT_RESULT.packages.find((item) => item.mod_id === selectedId) ?? SAMPLE_RECEIPT_RESULT.packages[0];
  const checks = selected.mod_id === "allin1.street-pack" ? [
    { kind: "file" as const, destination: "scripts/StreetPack.asi", exists: true, hash_recorded: true, hash_matches: true, backup_present: null },
    { kind: "file" as const, destination: "mods/update/x64/dlcpacks/streetpack/dlc.rpf", exists: true, hash_recorded: true, hash_matches: true, backup_present: true },
    { kind: "rpf_entry" as const, archive: "mods/update/update.rpf", entry: "common/data/dlclist.xml", matches_receipt: true },
  ] : [
    { kind: "file" as const, destination: "scripts/PhotoTools.dll", exists: true, hash_recorded: true, hash_matches: true, backup_present: null },
  ];
  return {
    ...SAMPLE_RECEIPT_RESULT,
    selected_id: selected.mod_id,
    receipt: {
      id: selected.mod_id,
      name: selected.name,
      version: selected.version,
      type: selected.mod_type,
      enabled: selected.enabled,
      installed_at: "2026-08-29T18:42:11+00:00",
      editions: ["enhanced"],
      dependencies: [],
      files: checks.filter((item) => item.kind === "file"),
      rpf_entries: checks.filter((item) => item.kind === "rpf_entry"),
    },
    verification: {
      package_id: selected.mod_id,
      version: selected.version,
      enabled: selected.enabled,
      healthy: true,
      ownership_verified: true,
      checks,
      issues: [],
    },
    check_count: checks.length,
  };
}

function lifecycleReviewFixture(action: string, payload: Record<string, unknown>, blocked = false): PackageLifecycleReviewResult {
  const selectedAction = (["install", "uninstall", "enable", "disable"].includes(action) ? action : "install") as PackageLifecycleReviewResult["action"];
  const install = selectedAction === "install";
  const uninstall = selectedAction === "uninstall";
  const stateChange = selectedAction === "enable" || selectedAction === "disable";
  const currentlyEnabled = selectedAction !== "enable";
  return {
    kind: "package_lifecycle_review",
    operation: "review_package_lifecycle",
    action: selectedAction,
    source: install ? String(payload.source ?? "C:\\SDK\\packages\\camera-tools\\mod.toml") : null,
    gta_path: SAMPLE_RECEIPT_RESULT.gta_path,
    ready: !blocked,
    package: install
      ? { id: "camera-tools", name: "Camera Tools", version: "2.1.0", type: "script", editions: ["enhanced"], dependencies: ["shvdn"], requires: [], conflicts: [] }
      : { id: "allin1.street-pack", name: "Street Pack", version: "1.2.0", type: "mixed" },
    target_edition: "enhanced",
    replacing: false,
    installed_version: null,
    enabled: install ? undefined : currentlyEnabled,
    current_enabled: stateChange ? currentlyEnabled : undefined,
    target_enabled: stateChange ? !currentlyEnabled : undefined,
    operations: uninstall ? [
      { kind: "file", destination: "scripts/StreetPack.asi", active_path: "C:\\Games\\Grand Theft Auto V Enhanced\\scripts\\StreetPack.asi", backup: null, disposition: "remove" },
      { kind: "file", destination: "mods/update/x64/dlcpacks/streetpack/dlc.rpf", active_path: "C:\\Games\\Grand Theft Auto V Enhanced\\mods\\update\\x64\\dlcpacks\\streetpack\\dlc.rpf", backup: "ALLIN1_Backups/Mods/allin1.street-pack/dlc.rpf", disposition: "restore_backup" },
    ] : stateChange ? [
      { kind: "file", destination: "scripts/StreetPack.asi", current_path: currentlyEnabled ? "C:\\Games\\Grand Theft Auto V Enhanced\\scripts\\StreetPack.asi" : "C:\\Games\\Grand Theft Auto V Enhanced\\scripts\\StreetPack.asi.disabled", target_path: currentlyEnabled ? "C:\\Games\\Grand Theft Auto V Enhanced\\scripts\\StreetPack.asi.disabled" : "C:\\Games\\Grand Theft Auto V Enhanced\\scripts\\StreetPack.asi", disposition: selectedAction === "enable" ? "enable_file" : "disable_file" },
      { kind: "dlc_registration", destination: "streetpack", disposition: selectedAction === "enable" ? "register_dlc" : "unregister_dlc" },
    ] : [
      { kind: "file", source: "payload/CameraTools.dll", destination: "scripts/CameraTools.dll", payload_sha256: "d5ae7b0ba71d37e4f5fd42fc4db4a76d9f88bb68a876b29d429c6d65925c8a71", destination_exists: false, disposition: "create" },
      { kind: "file", source: "payload/CameraTools.toml", destination: "scripts/CameraTools.toml", payload_sha256: "6db50f215529935d6b7d31dbb14cf8f664224e25a3714c523058bddad4b9af35", destination_exists: true, disposition: "backup_and_replace" },
    ],
    findings: blocked ? [
      { severity: "error", code: "dependency_blocked", message: "Missing required loader(s): shvdn" },
      { severity: "error", code: "conflict_blocked", message: "File destination is owned by: legacy-camera-tools" },
    ] : [],
    rollback: uninstall
      ? { restore_count: 1, receipt_removed: true, extension_registry_rebuilt: true }
      : stateChange
        ? { receipt_state_restored: true, loose_move_count: 1, rpf_entry_count: 0, dlc_registration_count: 1, extension_registry_rebuilt: true }
        : { backup_count: 1, previous_receipt_preserved: false, receipt_created: true },
    review_sha256: "f241d8f31ce5486ff72bf8f70064ec51f76cbf6420e61b9cc4838d63f254a7a8",
    review_only: true,
    game_write_required: true,
    game_write_performed: false,
  };
}

function quickImportReviewFixture(edition: string): VehicleQuickImportReviewResult {
  const legacy = edition === "legacy";
  const model = legacy ? "blista" : "comet6";
  return {
    kind: "vehicle_quick_import_review",
    operation: "review_vehicle_quick_import",
    plan: {
      source: SAMPLE_QUICK_IMPORT_RESULT.source,
      source_kind: "archive",
      source_package_sha256: "86bf3a17e67ac32b20d51b498b810665751f4779c062b75daee5a094626dd238",
      edition,
      source_member: `install/${edition}/${model}/dlc.rpf`,
      source_member_size: 17_924_096,
      source_member_sha256: "c80d355d0e2b931e68046f7655ddf580aba92d0b9ac938706e71b2dc72aa1ec4",
      package_id: `allin1.import.${model}`,
      name: legacy ? "Blista Package" : "Comet S2 Package",
      version: "1.0.0",
      dlc_pack: model,
      destination: `mods/update/x64/dlcpacks/${model}/dlc.rpf`,
      catalog: {
        schema_version: 1,
        id: `allin1.import.${model}`,
        name: legacy ? "Blista Package" : "Comet S2 Package",
        vehicles: [{
          model,
          name: legacy ? "Blista" : "Comet S2",
          manufacturer: legacy ? "Dinka" : "Pfister",
          category: legacy ? "compacts" : "sports",
          price: legacy ? 32000 : 185000,
          storage: "garage",
          source_pack: model,
          size_tier: 0,
          traffic: { enabled: false, weight: 1.0 },
        }],
      },
    },
    warnings: ["Review the inferred storefront price before package preparation."],
    acknowledged_free_models: [],
    destination_preview: `C:\\Users\\Developer\\AppData\\Local\\ALLIN1\\Packages\\allin1.import.${model}`,
    destination_review: {
      state: "new",
      exists: false,
      replaceable: true,
      message: "A new Launcher package will be created.",
    },
    review_sha256: "0b950bd7c61b98a17d09dd54d08fab849789aa55049d1225820b63ca4e21e4a8",
    vehicle_count: 1,
    warning_count: 1,
    review_only: true,
    game_write_performed: false,
    package_write_performed: false,
  };
}

function envelope(payload: Record<string, unknown> = {}, operation: Envelope["operation"] = "result"): Envelope {
  return { protocol_version: "1.0.0", request_id: "preview", job_id: null, operation, payload, sequence: 0, risk: "none", terminal: true };
}

function previewFixture(source: string, requestedPath: string): AssetPreviewResult {
  const entry = SAMPLE_ENTRIES.find((candidate) => candidate.path === requestedPath) ?? SAMPLE_ENTRIES[0];
  const common = {
    source,
    path: entry.path,
    name: entry.path.split(/[\\/]/).at(-1) ?? entry.path,
    category: entry.category,
    preview_kind: entry.preview_kind as AssetPreviewResult["preview_kind"],
    size: entry.size,
  };

  if (entry.preview_kind === "image") {
    return {
      ...common,
      display_kind: "image",
      bytes_read: entry.size,
      truncated: false,
      sha256: "d4909fd909d32e9180a8560b552cfea642e058eedd28750f44e48b4d9db42f24",
      text: null,
      text_truncated: false,
      artifact: {
        path: "preview-fixtures/comet6-normalized.svg",
        preview_url: "/asset-preview-fixture.svg",
        sha256: "4f1b8353f63b5099ff2767f9b4e2568999612912425150b9daed6e89bf76a746",
        size: 3_142,
        media_type: "image/svg+xml",
        width: 960,
        height: 540,
      },
      metadata: { format: "PNG", width: 1920, height: 1080, color_mode: "RGBA" },
      warnings: [],
    };
  }

  if (entry.preview_kind === "binary") {
    return {
      ...common,
      display_kind: "text",
      bytes_read: 256,
      truncated: true,
      sha256: null,
      text: "Rockstar archive header\n\n52 50 46 37  20 00 00 00  04 00 00 00  10 00 00 00\n00 08 00 00  00 00 00 00  43 4f 4d 45 54 36 00 00\n\nBinary content is not executed or decoded in the desktop preview.",
      text_truncated: false,
      artifact: null,
      metadata: { format: "Rockstar archive", signature: "RPF7", header_bytes: 256 },
      warnings: ["Only the bounded archive header is displayed."],
    };
  }

  if (entry.path === "README.txt") {
    return {
      ...common,
      display_kind: "text",
      bytes_read: entry.size,
      truncated: false,
      sha256: "2a2e6e3fb2d654efc8d1ef0b0dd417139aa6afb666046bf63ba4157683133ef8",
      text: "ALLIN1 Street Pack\n==================\n\nTarget: GTA V Enhanced\nPackage: street-pack 1.2.0\n\nThis package contains the Comet S2 vehicle definition and its authored preview.\nReview content.xml and validate the target edition before compiling an install plan.\n\nThe desktop viewer reads this member as bounded text; it does not execute package content.",
      text_truncated: true,
      artifact: null,
      metadata: { encoding: "UTF-8", line_count: 10 },
      warnings: [],
    };
  }

  return {
    ...common,
    display_kind: "text",
    bytes_read: entry.size,
    truncated: false,
    sha256: "b1f0e9c2748c36f2a19cb327ef6a88f8cd46d7dd3cb2aac4fd38bd46d598b541",
    text: "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<package id=\"street-pack\">\n  <target edition=\"enhanced\" />\n  <archive path=\"dlc.rpf\" />\n</package>\n",
    text_truncated: false,
    artifact: null,
    metadata: { encoding: "UTF-8", line_count: 5 },
    warnings: [],
  };
}

function rpfPreviewFixture(entryId: string): AssetPreviewResult {
  const entry = SAMPLE_RPF_RESULT.entries.find((item) => item.id === entryId)
    ?? SAMPLE_RPF_RESULT.entries.find((item) => item.kind !== "directory")!;
  const suffix = entry.name.split(".").at(-1)?.toLocaleLowerCase() ?? "bin";
  const structured = suffix === "xml" || suffix === "meta";
  return {
    source: SAMPLE_RPF_RESULT.source,
    path: entry.id,
    name: entry.name,
    category: structured ? "Metadata" : suffix === "ytd" ? "Textures" : "Archives",
    preview_kind: structured ? "text" : "binary",
    display_kind: "text",
    size: entry.size,
    bytes_read: Math.min(entry.size, 65_536),
    truncated: entry.size > 65_536,
    sha256: entry.size > 65_536 ? null : "9915a1c2af321b018b7f4c5723246c82992aa1c5c58c9316370a47ad90fa2a47",
    text: structured
      ? "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<ArchivePreview>\n  <Entry source=\"RpfPatcher\" mode=\"read-only\" />\n  <Path>" + entry.path + "</Path>\n</ArchivePreview>\n"
      : suffix === "gxt2" ? "GXT2 game-text dictionary\n\n" + entry.path + "\n\nOpen in text editor to inspect labels or create an editable copy."
      : "Native RPF member preview\n\nEntry: " + entry.path + "\nArchive layer: " + (entry.archive_path || "Root archive") + "\nResource version: " + (entry.resource_version ?? "not reported") + "\n\nFirst bytes\n\n52 50 46 37  00 00 00 00  0d 00 00 00  00 00 00 00",
    text_truncated: false,
    artifact: null,
    metadata: structured
      ? { encoding: "UTF-8", parser: "bounded text" }
      : { format: suffix.toLocaleUpperCase(), resource_version: entry.resource_version ?? "n/a" },
    warnings: entry.size > 65_536 ? ["Preview was clipped to the guarded member-read limit."] : [],
  };
}

function vehiclePreviewFixture(entryPath: string): AssetPreviewResult {
  const suffix = entryPath.split(".").at(-1)?.toLocaleLowerCase() ?? "bin";
  const isMetadata = suffix === "meta" || suffix === "xml";
  const size = SAMPLE_VEHICLE_PROJECT_RESULT.models
    .flatMap((model) => model.assets)
    .find((asset) => asset.path === entryPath)?.size ?? 0;
  return {
    source: SAMPLE_VEHICLE_PROJECT_RESULT.source,
    path: entryPath,
    name: entryPath.split(/[\\/]/).at(-1) ?? entryPath,
    category: isMetadata ? "Metadata" : "Vehicle assets",
    preview_kind: isMetadata ? "text" : "binary",
    display_kind: "text",
    size,
    bytes_read: Math.min(size, 65_536),
    truncated: size > 65_536,
    sha256: size > 65_536 ? null : "49b78142f1f14ac5b18c4a28d216f28ef42b9a4ec5038814f4c47688ab74fe1f",
    text: isMetadata
      ? "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<VehiclePreview modelName=\"comet6\" source=\"" + entryPath + "\" />\n"
      : "Linked vehicle asset\n\nPath: " + entryPath + "\nFormat: " + suffix.toLocaleUpperCase() + "\nRead mode: bounded and read-only\n\n52 53 43 37  00 00 00 00  0d 00 00 00  00 00 00 00",
    text_truncated: false,
    artifact: null,
    metadata: { format: suffix.toLocaleUpperCase(), ownership: "package-resolved" },
    warnings: size > 65_536 ? ["Preview was clipped to the guarded member-read limit."] : [],
  };
}

export function createPreviewClient(mode: string): DesktopClient {
  const transactionSession = (receipt = false) => {
    const session = rpfTransactionPreviewSession(receipt);
    if (mode === "transactions-live") {
      session.target_scope = "mods_copy"; session.authorized_root = null;
      session.gta_path = SAMPLE_RPF_RESULT.gta_path;
      session.archive = `${session.gta_path}\\mods\\update\\update.rpf`;
    }
    if (mode === "transactions-recovery" && receipt) session.status = "verified_staging";
    if (mode === "transactions-lock" && receipt) session.archive_lock = {path:"C:\\SDK\\archives\\.update.rpf.allin1.lock",pid:99999999,process_running:false,sha256:"9".repeat(64),plan_id:session.plan_id,created_at:"2026-09-04T00:00:00Z",identity:"1:12345",cleanup_supported:true};
    return session;
  };
  const loaded = mode === "loaded" || mode === "manifest" || mode === "assets" || mode === "workbench" || mode === "models" || mode === "rpf" || mode === "recipes" || mode === "quick_import" || mode === "receipts" || mode === "receipts_blocked";
  const sample = mode === "manifest" ? SAMPLE_MANIFEST_RESULT : SAMPLE_RESULT;
  const authoringWorkspace = "C:\\SDK\\workspaces\\street-pack-authoring";
  const materialWorkspace = "C:\\SDK\\workspaces\\comet6-materials";
  let preparedPublication = { package_id: "vehicle.comet6", name: "Comet S2 package", version: "1.0.0", edition: "enhanced",
    vehicles: [{ model: "comet6", name: "Comet S2", price: 1878000 }] };
  let materialRevision = 0;
  let materialCanUndo = false;
  let materialProject = structuredClone(SAMPLE_MODEL_MATERIAL_RESULT);
  let previousMaterialProject = structuredClone(materialProject);
  const materialAuthoringSession = (operation: ModelMaterialProjectResult["operation"] = "inspect_model_material_workspace"): ModelMaterialProjectResult => ({
    ...structuredClone(materialProject),
    kind: "model_material_authoring_session",
    operation,
    workspace: materialWorkspace,
    source: "C:\\SDK\\workspaces\\comet6-materials\\original\\comet6.yft",
    revision: materialRevision,
    can_undo: materialCanUndo,
    viewport: {
      source: "C:\\SDK\\workspaces\\comet6-materials\\original",
      entry: "comet6.yft",
      texture_entry: null,
      collision_entry: null,
    },
    read_only: operation === "inspect_model_material_workspace",
    workspace_write_performed: operation !== "inspect_model_material_workspace",
    package_write_performed: false,
    game_write_performed: false,
  });
  const textureWorkspace = "C:\\SDK\\workspaces\\comet6-textures";
  let textureRevision = 0;
  let textureCanUndo = false;
  let textures: TextureRecord[] = [
    { name: "comet6_sign_1", file_name: "comet6_sign_1.dds", width: 1024, height: 1024, mip_levels: 10, format: "D3DFMT_DXT5", usage: "Diffuse", size: 1_398_256, sha256: "1".repeat(64), warnings: [] },
    { name: "comet6_interior", file_name: "comet6_interior.dds", width: 1024, height: 1024, mip_levels: 10, format: "D3DFMT_DXT1", usage: "Diffuse", size: 699_192, sha256: "2".repeat(64), warnings: [] },
    { name: "comet6_interior_n", file_name: "comet6_interior_n.dds", width: 512, height: 512, mip_levels: 9, format: "D3DFMT_ATI2", usage: "Normal", size: 349_696, sha256: "3".repeat(64), warnings: [] },
    { name: "comet6_glass", file_name: "comet6_glass.dds", width: 256, height: 256, mip_levels: 8, format: "D3DFMT_DXT5", usage: "Diffuse", size: 87_552, sha256: "4".repeat(64), warnings: [] },
  ];
  let previousTextures = structuredClone(textures);
  const textureStateSha = () => `${textureRevision.toString(16).padStart(2, "0")}${"a".repeat(62)}`;
  const textureSession = (
    operation: TextureWorkspaceSession["operation"] = "inspect_texture_workspace",
  ): TextureWorkspaceSession => ({
    kind: "texture_workspace_session",
    operation,
    workspace: textureWorkspace,
    source: `${textureWorkspace}\\original\\comet6.ytd`,
    source_name: "comet6.ytd",
    source_size: 8_204_288,
    source_sha256: "5".repeat(64),
    edition: "Enhanced",
    revision: textureRevision,
    state_sha256: textureStateSha(),
    can_undo: textureCanUndo,
    texture_count: textures.length,
    warnings: textures.flatMap((item) => item.warnings.map((warning) => `${item.name}: ${warning}`)),
    textures: structuredClone(textures),
    read_only: operation === "inspect_texture_workspace",
    workspace_write_performed: operation !== "inspect_texture_workspace",
    package_write_performed: false,
    game_write_performed: false,
  });
  let authoringRevision = 0;
  let authoringCanUndo = false;
  let authoringCanRedo = false;
  let authoringValues: Record<string, string> = {
    "vehicle.gameName": "COMET6",
    "vehicle.vehicleMakeName": "PFISTER",
    "vehicle.txdName": "comet6",
    "vehicle.vehicleClass": "VC_SPORT",
    "vehicle.type": "VEHICLE_TYPE_CAR",
    "vehicle.layout": "LAYOUT_LOW",
    "vehicle.audioNameHash": "comet2",
    "handling.fMass": "1685.0",
    "handling.fInitialDragCoeff": "2.05",
    "handling.fDriveBiasFront": "0.0",
    "handling.nInitialDriveGears": "8",
    "handling.fInitialDriveForce": "0.36",
    "handling.fDriveInertia": "1.0",
    "handling.fInitialDriveMaxFlatVel": "183.0",
    "handling.fBrakeForce": "1.0",
    "handling.fBrakeBiasFront": "0.52",
    "handling.fHandBrakeForce": "0.8",
    "handling.fSteeringLock": "38.0",
    "handling.fTractionCurveMax": "2.65",
    "handling.fTractionCurveMin": "2.45",
    "handling.fTractionCurveLateral": "22.5",
    "handling.fLowSpeedTractionLossMult": "1.0",
    "handling.fTractionBiasFront": "0.48",
    "handling.fTractionLossMult": "1.0",
    "handling.fSuspensionForce": "2.6",
    "handling.fSuspensionCompDamp": "1.35",
    "handling.fSuspensionReboundDamp": "2.2",
    "handling.fSuspensionUpperLimit": "0.08",
    "handling.fSuspensionLowerLimit": "-0.11",
    "handling.fSuspensionRaise": "0.0",
    "handling.fSuspensionBiasFront": "0.5",
    "handling.fAntiRollBarForce": "0.8",
    "handling.fAntiRollBarBiasFront": "0.55",
    "handling.fCollisionDamageMult": "1.0",
    "handling.fWeaponDamageMult": "1.0",
    "handling.fDeformationDamageMult": "0.8",
    "handling.fEngineDamageMult": "1.5",
    "variation.lightSettings": "1",
    "variation.sirenSettings": "0",
    "variation.kits": "123_comet6_modkit",
  };
  let previousAuthoringValues = { ...authoringValues };
  let authoringAppearance: VehicleAppearance = {
    model: "comet6",
    source: `${authoringWorkspace}\\source\\carvariations.meta`,
    colors: [
      { indices: [0, 28, 0, 0, 0, 0], liveries: [true, false] },
      { indices: [111, 111, 111, 0, 0, 0], liveries: [false, true] },
    ],
    kits: ["123_comet6_modkit"],
    light_settings: "1",
    siren_settings: "0",
    available_kits: [
      { source: `${authoringWorkspace}\\source\\carcols.meta`, name: "123_comet6_modkit", kit_id: "123", kit_type: "MKT_STANDARD", visible_mods: 8, link_mods: 1, stat_mods: 3, livery_names: ["COMET6_LIVERY_1", "COMET6_LIVERY_2"] },
      { source: `${authoringWorkspace}\\source\\carcols.meta`, name: "124_comet6_trackkit", kit_id: "124", kit_type: "MKT_SPORT", visible_mods: 5, link_mods: 0, stat_mods: 2, livery_names: [] },
    ],
    light_profiles: [
      { source: `${authoringWorkspace}\\source\\carcols.meta`, profile_id: "1", name: "comet6_lights", values: { "headLight.intensity": "2.000000", "tailLight.intensity": "1.000000" } },
      { source: `${authoringWorkspace}\\source\\carcols.meta`, profile_id: "2", name: "comet6_track_lights", values: { "headLight.intensity": "2.750000", "tailLight.intensity": "1.250000" } },
    ],
  };
  let previousAuthoringAppearance: VehicleAppearance = structuredClone(authoringAppearance);
  let authoringTuning: VehicleTuningBuilder = {
    kind: "vehicle_authoring_tuning",
    operation: "inspect_vehicle_authoring_tuning",
    workspace: authoringWorkspace,
    revision: authoringRevision,
    model: "comet6",
    kit_name: "123_comet6_modkit",
    kit_id: "123",
    kit_type: "MKT_STANDARD",
    source: `${authoringWorkspace}\\source\\carcols.meta`,
    entries: [
      { collection: "visibleMods", index: 0, summary: "comet6_spoiler_1", mod_type: "VMT_SPOILER", key: "visibleMods:0", fields: { modelName: "comet6_spoiler_1", modShopLabel: "CM6_SPOILER_1", type: "VMT_SPOILER", bone: "chassis", cameraPos: "VMCP_DEFAULT", audioApply: "1.000000", weight: "0", turnOffExtra: "false", allowBonnetSlide: "true" } },
      { collection: "visibleMods", index: 1, summary: "comet6_bumper_1", mod_type: "VMT_BUMPER_F", key: "visibleMods:1", fields: { modelName: "comet6_bumper_1", modShopLabel: "CM6_BUMPER_1", type: "VMT_BUMPER_F", bone: "chassis", cameraPos: "VMCP_DEFAULT", audioApply: "1.000000", weight: "0", turnOffExtra: "false", allowBonnetSlide: "true" } },
      { collection: "statMods", index: 0, summary: "CM6_ENGINE_1", mod_type: "VMT_ENGINE", key: "statMods:0", fields: { identifier: "CM6_ENGINE_1", modifier: "25", audioApply: "1.000000", weight: "0", type: "VMT_ENGINE" } },
    ],
    assets: [
      { path: "stream/comet6_spoiler_1.yft", name: "comet6_spoiler_1", kind: "Model", referenced: true },
      { path: "stream/comet6_bumper_1.yft", name: "comet6_bumper_1", kind: "Model", referenced: true },
      { path: "stream/comet6_diffuser_1.yft", name: "comet6_diffuser_1", kind: "Model", referenced: false },
    ],
    findings: [],
    error_count: 0,
    warning_count: 0,
    collections: ["visibleMods", "linkMods", "statMods", "slotNames"],
    vmt_types: ["VMT_SPOILER", "VMT_BUMPER_F", "VMT_BUMPER_R", "VMT_SKIRT", "VMT_EXHAUST", "VMT_ENGINE", "VMT_BRAKES", "VMT_GEARBOX", "VMT_SUSPENSION", "VMT_ARMOUR"],
    field_schemas: {
      visibleMods: {
        modelName: { kind: "identifier", required: true, default: "" },
        modShopLabel: { kind: "identifier", required: true, default: "" },
        linkedModels: { kind: "identifier_array", required: false, default: "" },
        turnOffBones: { kind: "identifier_array", required: false, default: "" },
        type: { kind: "vmt", required: true, default: "VMT_SPOILER" },
        bone: { kind: "identifier", required: false, default: "chassis" },
        cameraPos: { kind: "identifier", required: false, default: "VMCP_DEFAULT" },
        audioApply: { kind: "float", required: false, default: "1.000000" },
        weight: { kind: "integer", required: false, default: "0" },
        turnOffExtra: { kind: "boolean", required: false, default: "false" },
        allowBonnetSlide: { kind: "boolean", required: false, default: "true" },
      },
      linkMods: {
        modelName: { kind: "identifier", required: true, default: "" },
        bone: { kind: "identifier", required: false, default: "chassis" },
        turnOffExtra: { kind: "boolean", required: false, default: "false" },
      },
      statMods: {
        identifier: { kind: "identifier", required: false, default: "" },
        modifier: { kind: "float", required: true, default: "25" },
        audioApply: { kind: "float", required: false, default: "1.000000" },
        weight: { kind: "integer", required: false, default: "0" },
        type: { kind: "vmt", required: true, default: "VMT_ENGINE" },
      },
      slotNames: {
        slot: { kind: "vmt", required: true, default: "VMT_SPOILER" },
        name: { kind: "identifier", required: true, default: "" },
      },
    },
    read_only: true,
    workspace_write_performed: false,
    package_write_performed: false,
    game_write_performed: false,
  };
  let previousAuthoringTuning: VehicleTuningBuilder = structuredClone(authoringTuning);
  let authoringAxles: VehicleAxleConfiguration = structuredClone(SAMPLE_AXLE_CONFIGURATION);
  let previousAuthoringAxles: VehicleAxleConfiguration = structuredClone(authoringAxles);
  let authoringTransmission: VehicleTransmissionConfiguration = {
    schema_version: 1,
    vehicle_model: "comet6",
    transmission_type: "dual_clutch",
    gear_ratios: [3.5, 2.31, 1.72, 1.35, 1.09, 0.91, 0.79, 0.7],
    reverse_gear_ratio: 3.2,
    final_drive_ratio: 3.44,
  };
  let previousAuthoringTransmission = structuredClone(authoringTransmission);
  let authoringDistribution: VehicleDistributionValues = {
    model: "comet6",
    listed: true,
    name: "Comet S2",
    manufacturer: "Pfister",
    category: "sports",
    price: 145000,
    storage: "garage",
    size_tier: 2,
    preview_dictionary: "comet6",
    preview_texture: "comet6",
    traffic_enabled: false,
    traffic_weight: 1,
  };
  let previousAuthoringDistribution = structuredClone(authoringDistribution);
  const authoringSession = (
    operation: VehicleAuthoringSession["operation"] = "inspect_vehicle_authoring_workspace",
  ): VehicleAuthoringSession => ({
    kind: "vehicle_authoring_session",
    operation,
    workspace: authoringWorkspace,
    source: `${authoringWorkspace}\\source`,
    original_source: SAMPLE_VEHICLE_PROJECT_RESULT.source,
    revision: authoringRevision,
    selected_model: "comet6",
    editable_fields: Object.keys(authoringValues),
    values: { ...authoringValues },
    sources: Object.fromEntries(Object.keys(authoringValues).map((field) => [field, field.split(".")[0] + ".meta"])),
    appearance: structuredClone(authoringAppearance),
    transmission: structuredClone(authoringTransmission),
    distribution: structuredClone(authoringDistribution),
    can_undo: authoringCanUndo,
    can_redo: authoringCanRedo,
    project: { ...SAMPLE_VEHICLE_PROJECT_RESULT, axle_configurations: [structuredClone(authoringAxles)] },
    read_only: operation === "inspect_vehicle_authoring_workspace",
    workspace_write_performed: operation !== "inspect_vehicle_authoring_workspace",
    package_write_performed: operation !== "inspect_vehicle_authoring_workspace",
    game_write_performed: false,
  });
  return {
    handshake: async () => envelope({ sdk_version: packageInfo.version }),
    catalog: async () => mode === "help" ? { ...PREVIEW_CATALOG, help_topics: helpPreviewTopics } : PREVIEW_CATALOG,
    execute: async (command) => envelope({ result: { output: `Preview result for ${command}` } }),
    configureAssistant: async () => ({ ...envelope({ message: "Settings cannot be saved in browser preview. Open the installed SDK." }), operation: "error" }),
    applyWeaponAuthoring: async () => ({ ...envelope({ message: "Weapon writes require the installed SDK." }), operation: "error" }),
    applyPedAuthoring: async () => ({ ...envelope({ message: "Ped writes require the SDK. Synthetic browser data cannot authorize writes." }), operation: "error" }),
    applyVehicleOivExport: async () => ({ ...envelope({ message: "OIV export writes require the installed SDK. This preview did not create a file." }), operation: "error" }),
    applyVehiclePackagePublish: async () => ({ ...envelope({ message: "ZIP publication requires the installed SDK. This preview did not create or upload a file." }), operation: "error" }),
    applyWorkspaceAction: async () => ({ ...envelope({ message: "Workspace writes require the SDK. Browser preview cannot authorize writes." }), operation: "error" }),
    applyGxt2Action: async () => ({ ...envelope({ message: "GXT2 writes require the installed SDK. Preview data did not change any files." }), operation: "error" }),
    applyRpfChangeSet: async () => ({...envelope({message:"Saving change sets requires the installed SDK. Preview did not change files."}),operation:"error"}),
    applyRpfTransaction: async () => ({...envelope({message:"Archive transactions require the installed SDK. Browser preview never writes archives."}),operation:"error"}),
    applyRpfUtility: async () => ({...envelope({message:"RPF utility outputs require the installed SDK. Browser preview never writes archives or reports."}),operation:"error"}),
    selectRpfPlanDestination: async name => `C:\\SDK\\exports\\${name}`,
    selectRpfUtilityDestination: async (_action, name) => `C:\\SDK\\exports\\${name}`,
    selectGxt2BuildDestination: async name => `C:\\SDK\\exports\\${name}`,
    prepareVehicleQuickImport: async (payload) => {
      const plan = quickImportReviewFixture(String(payload.edition ?? "enhanced")).plan;
      preparedPublication = { package_id: plan.package_id, name: plan.name, version: plan.version,
        edition: plan.edition, vehicles: plan.catalog.vehicles };
      return envelope({ result: {
      kind: "vehicle_quick_import_prepared",
      operation: "prepare_vehicle_quick_import",
      review_sha256: String(payload.review_sha256 ?? ""),
      game_write_performed: false,
      package_write_performed: true,
      launcher_install_required: true,
      launcher_library: true,
      replaced_existing: false,
      package: { package_root: `C:\\Users\\Developer\\AppData\\Local\\ALLIN1\\Packages\\${String(payload.package_id ?? "allin1.import.comet6")}` },
      published: null,
      warnings: [],
    } }); },
    applyPackageLifecycle: async (payload) => {
      const action = String(payload.action) as PackageLifecycleExecutionResult["action"];
      const stateChange = action === "enable" || action === "disable";
      return envelope({ result: ({
        kind: "package_lifecycle_execution",
        operation: "apply_package_lifecycle",
        action,
        status: ({ install: "installed", uninstall: "uninstalled", enable: "enabled", disable: "disabled" } as const)[action],
        source: action === "install" ? String(payload.source) : null,
        gta_path: String(payload.gta_path),
        package: action === "install"
          ? { id: "camera-tools", name: "Camera Tools", version: "2.1.0", type: "script" }
          : { id: "allin1.street-pack", name: "Street Pack", version: "1.2.0", type: "mixed" },
        review_sha256: String(payload.review_sha256),
        process_check: { gta_closed: true, running_processes: [] },
        postcondition: action === "install"
          ? { installed: true, enabled: true, ownership: { ownership_verified: true } }
          : stateChange
            ? { installed: true, enabled: action === "enable", ownership: { ownership_verified: true } }
            : { installed: false, receipt_present: false },
        rollback: action === "install"
          ? { receipt_written: true, ownership_verified: true, backup_count: 1, rpf_entry_count: 0 }
          : stateChange
            ? { receipt_state_updated: true, ownership_verified: true, loose_move_count: 1, rpf_entry_count: 0, dlc_registration_count: 1, extension_registry_rebuilt: true }
            : { receipt_removed: true, restored_backup_count: 1, removed_payload_count: 1, extension_registry_rebuilt: true },
        game_write_confirmed: true,
        game_write_performed: true,
      } satisfies PackageLifecycleExecutionResult) });
    },
    renderVehicleModel: async (payload) => {
      if (String(payload.entry).includes("w_pi_demo")) {
        return { ...envelope({ message: "The browser demo has no native weapon bytes. Open a package in the desktop SDK to render its actual geometry." }), operation: "error" };
      }
      const yaw = Number(payload.yaw ?? 34);
      const pitch = Number(payload.pitch ?? 24);
      const lod = String(payload.lod ?? "All");
      const component = String(payload.component ?? "All");
      const material = String(payload.material ?? "All");
      const renderMode = String(payload.render_mode ?? "shaded") as VehicleViewportResult["camera"]["render_mode"];
      const quality = String(payload.quality ?? "final") as VehicleViewportResult["camera"]["quality"];
      return envelope({ result: ({
        kind: "vehicle_model_viewport",
        source: String(payload.source),
        path: String(payload.entry),
        name: String(payload.entry).split(/[\\/]/).at(-1) ?? "vehicle.yft",
        size: 4_826_112,
        bytes_read: 4_826_112,
        sha256: "0fc4711ea23719a55f2243c1b5ed7a6b8e4970644a2395971881818eb847d03d",
        edition: String(payload.edition).toLocaleLowerCase() === "legacy" ? "legacy" : "enhanced",
        artifact: { path: "C:\\SDK\\cache\\vehicle-frame.png", preview_url: "/asset-preview-fixture.svg", sha256: "5e90ef15393073d127f69f58c4c5900dc4b39dbf69f78d59ff3b12111e79937f", size: 48_210, media_type: "image/png", width: 960, height: 680 },
        camera: { yaw, pitch, lod, component, material, render_mode: renderMode, quality, collision_visible: Boolean(payload.collision_visible) },
        scene: {
          lods: ["High", "Medium", "Low"],
          components: [
            { name: "Chassis", lod: "High", geometry_count: 18, vertex_count: 84_112, triangle_count: 96_420, material_names: ["vehicle_paint1", "vehicle_vehglass"], texture_names: ["comet6_sign_1"] },
            { name: "Wheel LF", lod: "High", geometry_count: 4, vertex_count: 12_804, triangle_count: 16_202, material_names: ["vehicle_tire", "vehicle_rim"], texture_names: ["vehicle_generic_tyrewallblack"] },
            { name: "Interior", lod: "Medium", geometry_count: 9, vertex_count: 35_901, triangle_count: 42_188, material_names: ["vehicle_interior2"], texture_names: ["comet6_interior"] },
          ],
          materials: [
            { index: 0, name: "vehicle_paint1", record_count: 1, geometry_count: 8, triangle_count: 68_210, lods: ["High"], components: ["Chassis"], texture_bindings: [{ slot: "DiffuseSampler", name: "comet6_sign_1", resolved: true }, { slot: "BumpSampler", name: "vehicle_generic_smallspecmap", resolved: false }], parameter_count: 4, parameters: [{ name: "specularIntensityMult", source_type: "Vector", values: [[0.5, 0, 0, 0]], record_count: 1 }, { name: "specularFalloffMult", source_type: "Vector", values: [[20, 0, 0, 0]], record_count: 1 }, { name: "globalAnimUV0", source_type: "Vector", values: [[1, 0, 0, 0]], record_count: 1 }, { name: "detailSettings", source_type: "Array", values: [[1, 0.72, 0.18, 0], [4, 2, 1, 0]], record_count: 1 }] },
            { index: 1, name: "vehicle_vehglass", record_count: 1, geometry_count: 4, triangle_count: 28_210, lods: ["High"], components: ["Chassis"], texture_bindings: [{ slot: "DiffuseSampler", name: "comet6_glass", resolved: true }], parameter_count: 2, parameters: [{ name: "specularIntensityMult", source_type: "Vector", values: [[0.85, 0, 0, 0]], record_count: 1 }, { name: "HardAlphaBlend", source_type: "Vector", values: [[1, 0, 0, 0]], record_count: 1 }] },
            { index: 2, name: "vehicle_tire", record_count: 1, geometry_count: 4, triangle_count: 16_202, lods: ["High"], components: ["Wheel LF"], texture_bindings: [{ slot: "DiffuseSampler", name: "vehicle_generic_tyrewallblack", resolved: false }], parameter_count: 1, parameters: [{ name: "bumpiness", source_type: "Vector", values: [[1, 0, 0, 0]], record_count: 1 }] },
            { index: 3, name: "vehicle_interior2", record_count: 1, geometry_count: 9, triangle_count: 42_188, lods: ["Medium"], components: ["Interior"], texture_bindings: [{ slot: "DiffuseSampler", name: "comet6_interior", resolved: true }], parameter_count: 0, parameters: [] },
          ],
          component_count: 3,
          material_count: 17,
          surface_count: 4,
          bone_count: 126,
        },
        metadata: {
          model_total_triangles: 154_810,
          model_rendered_triangles: quality === "interactive" ? 6_000 : 45_000,
          ...(renderMode === "uvs" ? {
            model_render_uv_valid_triangle_count: 43_702,
            model_render_uv_resolved_triangle_count: 38_410,
            model_render_uv_unresolved_triangle_count: 5_292,
            model_render_uv_degenerate_triangle_count: 814,
            model_render_uv_missing_triangle_count: 484,
            model_render_uv_coverage_percent: 97.12,
          } : {}),
        },
        texture_dictionary: payload.texture_entry ? {
          path: String(payload.texture_entry), name: "comet6.ytd", size: 8_204_288,
          bytes_read: 8_204_288, sha256: "7".repeat(64), texture_count: 3,
          previewed_count: 3, truncated: false,
          artifact: { path: "C:\\SDK\\cache\\vehicle-textures.png", preview_url: "/asset-preview-fixture.svg", sha256: "8".repeat(64), size: 36_210, media_type: "image/png", width: 840, height: 185 },
          textures: [
            { name: "comet6_sign_1", file_name: "comet6_sign_1.dds", width: 2048, height: 2048, mip_levels: 12, format: "DXT5", usage: "DEFAULT", size: 2_796_336, sha256: "9".repeat(64), contact_sheet_index: 0, warnings: [] },
            { name: "comet6_glass", file_name: "comet6_glass.dds", width: 1024, height: 1024, mip_levels: 11, format: "DXT1", usage: "DEFAULT", size: 699_192, sha256: "a".repeat(64), contact_sheet_index: 1, warnings: [] },
            { name: "comet6_interior", file_name: "comet6_interior.dds", width: 1024, height: 1024, mip_levels: 11, format: "DXT5", usage: "DEFAULT", size: 1_398_232, sha256: "b".repeat(64), contact_sheet_index: 2, warnings: [] },
          ], warnings: [], cache_hit: true, read_only: true,
        } : null,
        collision_dictionary: payload.collision_entry ? {
          path: String(payload.collision_entry), name: "comet6.ybn", size: 384_640,
          bytes_read: 384_640, sha256: "c".repeat(64), geometry_count: 7,
          vertex_count: 2_416, polygon_count: 1_892, material_count: 11,
          render_triangle_count: 2_148, overlay_polygon_count: 1_836,
          unrendered_polygon_count: 56,
          primitive_counts: [
            { kind: "Triangle", count: 1_804, overlay: true, fidelity: "exact mesh" },
            { kind: "Box", count: 32, overlay: true, fidelity: "diagnostic hull" },
            { kind: "Capsule", count: 56, overlay: false, fidelity: "count only" },
          ],
          bounds: { min: [-1.08, -2.46, -0.41], max: [1.08, 2.37, 1.42], size: [2.16, 4.83, 1.83] },
          warnings: [], cache_hit: true, read_only: true,
        } : null,
        uv_atlas: renderMode === "uvs" ? {
          artifact: { path: "C:\\SDK\\cache\\vehicle-uv-atlas.png", preview_url: "/asset-preview-fixture.svg", sha256: "d".repeat(64), size: 71_204, media_type: "image/png", width: 960, height: 1016 },
          width: 960, height: 1016, triangle_budget: 45_000,
          source_triangle_count: 154_810, sampled_triangle_count: 45_000,
          rendered_triangle_count: 42_480, valid_triangle_count: 43_702,
          degenerate_triangle_count: 814, missing_triangle_count: 484,
          seam_triangle_count: 1_222, island_count: 37,
          texture_group_count: 4, returned_texture_group_count: 4, sampled: true,
          texture_groups: [
            { name: "comet6_sign_1", resolved: true, material_names: ["vehicle_paint1"], geometry_count: 8, sampled_triangle_count: 19_400, valid_triangle_count: 19_200, rendered_triangle_count: 18_910, island_count: 8, seam_triangle_count: 290, degenerate_triangle_count: 120, missing_triangle_count: 80 },
            { name: "comet6_glass", resolved: true, material_names: ["vehicle_vehglass"], geometry_count: 4, sampled_triangle_count: 10_500, valid_triangle_count: 10_240, rendered_triangle_count: 10_030, island_count: 11, seam_triangle_count: 210, degenerate_triangle_count: 160, missing_triangle_count: 100 },
            { name: "vehicle_generic_tyrewallblack", resolved: false, material_names: ["vehicle_tire"], geometry_count: 4, sampled_triangle_count: 8_100, valid_triangle_count: 7_860, rendered_triangle_count: 7_512, island_count: 10, seam_triangle_count: 348, degenerate_triangle_count: 150, missing_triangle_count: 90 },
            { name: "comet6_interior", resolved: true, material_names: ["vehicle_interior2"], geometry_count: 9, sampled_triangle_count: 7_000, valid_triangle_count: 6_402, rendered_triangle_count: 6_028, island_count: 8, seam_triangle_count: 374, degenerate_triangle_count: 384, missing_triangle_count: 214 },
          ],
          selection: { lod, component, material },
          fidelity: "UV0 coordinates decoded from native geometry; islands connect sampled same-tile triangles by shared mesh edges; linked YTD backgrounds are bounded previews; cross-tile seams are count-only",
          cache_hit: true, read_only: true,
        } : null,
        warnings: [],
        cache_hit: true,
        read_only: true,
        workspace_write_performed: false,
        package_write_performed: false,
        game_write_performed: false,
      } satisfies VehicleViewportResult) });
    },
    vehicleAuthoringAction: async (operation, payload) => {
      if (operation === "create_vehicle_authoring_workspace") {
        authoringRevision = 0;
        authoringCanUndo = false;
        authoringCanRedo = false;
        return envelope({ result: authoringSession(operation) });
      }
      if (operation === "apply_vehicle_authoring_edit") {
        previousAuthoringValues = { ...authoringValues };
        previousAuthoringAppearance = structuredClone(authoringAppearance);
        previousAuthoringTuning = structuredClone(authoringTuning);
        previousAuthoringAxles = structuredClone(authoringAxles);
        previousAuthoringTransmission = structuredClone(authoringTransmission);
        previousAuthoringDistribution = structuredClone(authoringDistribution);
        authoringValues = { ...authoringValues, ...(payload.updates as Record<string, string>) };
        authoringRevision += 1;
        authoringCanUndo = true;
        authoringCanRedo = false;
        return envelope({ result: authoringSession(operation) });
      }
      if (operation === "apply_vehicle_authoring_appearance") {
        previousAuthoringValues = { ...authoringValues };
        previousAuthoringAppearance = structuredClone(authoringAppearance);
        previousAuthoringTuning = structuredClone(authoringTuning);
        previousAuthoringAxles = structuredClone(authoringAxles);
        previousAuthoringTransmission = structuredClone(authoringTransmission);
        previousAuthoringDistribution = structuredClone(authoringDistribution);
        const appearance = payload.appearance as Pick<VehicleAppearance, "colors" | "kits" | "light_settings" | "siren_settings">;
        authoringAppearance = { ...authoringAppearance, ...structuredClone(appearance) };
        authoringValues = {
          ...authoringValues,
          "variation.lightSettings": authoringAppearance.light_settings,
          "variation.sirenSettings": authoringAppearance.siren_settings,
          "variation.kits": authoringAppearance.kits.join(", "),
        };
        authoringRevision += 1;
        authoringCanUndo = true;
        authoringCanRedo = false;
        return envelope({ result: authoringSession(operation) });
      }
      if (operation === "apply_vehicle_authoring_tuning") {
        previousAuthoringValues = { ...authoringValues };
        previousAuthoringAppearance = structuredClone(authoringAppearance);
        previousAuthoringTuning = structuredClone(authoringTuning);
        previousAuthoringAxles = structuredClone(authoringAxles);
        previousAuthoringTransmission = structuredClone(authoringTransmission);
        previousAuthoringDistribution = structuredClone(authoringDistribution);
        const mutation = payload.mutation as Record<string, unknown>;
        const action = String(mutation.action);
        const collection = String(mutation.collection ?? "visibleMods");
        const index = Number(mutation.index ?? 0);
        const values = (mutation.values ?? {}) as Record<string, string>;
        if (action === "update_kit") {
          authoringTuning.kit_type = String(mutation.kit_type);
          const kit = authoringAppearance.available_kits.find((item) => item.name === mutation.kit_name);
          if (kit) {
            kit.kit_type = String(mutation.kit_type);
            kit.livery_names = [...(mutation.livery_names as string[])];
          }
        } else if (action === "update_entry") {
          const entry = authoringTuning.entries.find((item) => item.collection === collection && item.index === index);
          if (entry) {
            entry.fields = { ...entry.fields, ...values };
            entry.summary = entry.fields.modelName || entry.fields.identifier || entry.fields.name || entry.fields.slot || entry.summary;
            entry.mod_type = entry.fields.type || entry.fields.slot || entry.mod_type;
          }
        } else if (action === "add_entry" || action === "duplicate_entry") {
          const existing = authoringTuning.entries.filter((item) => item.collection === collection);
          const source = action === "duplicate_entry" ? existing[index] : null;
          const fields = { ...(source?.fields ?? {}), ...values };
          const newIndex = existing.length;
          authoringTuning.entries.push({ collection: collection as VehicleTuningBuilder["collections"][number], index: newIndex, key: `${collection}:${newIndex}`, fields, summary: fields.modelName || fields.identifier || fields.name || fields.slot || `Item ${newIndex + 1}`, mod_type: fields.type || fields.slot || "" });
        } else if (action === "remove_entry") {
          authoringTuning.entries = authoringTuning.entries.filter((item) => !(item.collection === collection && item.index === index));
          authoringTuning.entries.filter((item) => item.collection === collection).forEach((item, entryIndex) => { item.index = entryIndex; item.key = `${collection}:${entryIndex}`; });
        } else if (action === "move_entry") {
          const entries = authoringTuning.entries.filter((item) => item.collection === collection);
          const [moved] = entries.splice(index, 1);
          if (moved) entries.splice(Number(mutation.new_index), 0, moved);
          const other = authoringTuning.entries.filter((item) => item.collection !== collection);
          entries.forEach((item, entryIndex) => { item.index = entryIndex; item.key = `${collection}:${entryIndex}`; });
          authoringTuning.entries = [...entries, ...other];
        }
        authoringRevision += 1;
        authoringTuning.revision = authoringRevision;
        authoringCanUndo = true;
        authoringCanRedo = false;
        return envelope({ result: { ...authoringSession(operation), tuning_builder: structuredClone(authoringTuning) } });
      }
      if (operation === "apply_vehicle_authoring_light_profile") {
        previousAuthoringValues = { ...authoringValues };
        previousAuthoringAppearance = structuredClone(authoringAppearance);
        previousAuthoringTuning = structuredClone(authoringTuning);
        previousAuthoringAxles = structuredClone(authoringAxles);
        previousAuthoringTransmission = structuredClone(authoringTransmission);
        previousAuthoringDistribution = structuredClone(authoringDistribution);
        const profile = authoringAppearance.light_profiles.find((item) => item.profile_id === String(payload.profile_id));
        if (profile) profile.values = { ...profile.values, ...(payload.updates as Record<string, string>) };
        authoringRevision += 1;
        authoringTuning.revision = authoringRevision;
        authoringCanUndo = true;
        authoringCanRedo = false;
        return envelope({ result: authoringSession(operation) });
      }
      if (operation === "apply_vehicle_authoring_axles") {
        previousAuthoringValues = { ...authoringValues };
        previousAuthoringAppearance = structuredClone(authoringAppearance);
        previousAuthoringTuning = structuredClone(authoringTuning);
        previousAuthoringAxles = structuredClone(authoringAxles);
        previousAuthoringTransmission = structuredClone(authoringTransmission);
        previousAuthoringDistribution = structuredClone(authoringDistribution);
        authoringAxles = structuredClone(payload.configuration as VehicleAxleConfiguration);
        authoringRevision += 1;
        authoringTuning.revision = authoringRevision;
        authoringCanUndo = true;
        authoringCanRedo = false;
        return envelope({ result: authoringSession(operation) });
      }
      if (operation === "apply_vehicle_authoring_transmission") {
        previousAuthoringValues = { ...authoringValues };
        previousAuthoringAppearance = structuredClone(authoringAppearance);
        previousAuthoringTuning = structuredClone(authoringTuning);
        previousAuthoringAxles = structuredClone(authoringAxles);
        previousAuthoringTransmission = structuredClone(authoringTransmission);
        previousAuthoringDistribution = structuredClone(authoringDistribution);
        authoringTransmission = structuredClone(
          payload.configuration as VehicleTransmissionConfiguration,
        );
        authoringValues = {
          ...authoringValues,
          "handling.nInitialDriveGears": String(authoringTransmission.gear_ratios.length),
        };
        authoringRevision += 1;
        authoringCanUndo = true;
        authoringCanRedo = false;
        return envelope({ result: authoringSession(operation) });
      }
      if (operation === "apply_vehicle_authoring_distribution") {
        previousAuthoringValues = { ...authoringValues };
        previousAuthoringAppearance = structuredClone(authoringAppearance);
        previousAuthoringTuning = structuredClone(authoringTuning);
        previousAuthoringAxles = structuredClone(authoringAxles);
        previousAuthoringTransmission = structuredClone(authoringTransmission);
        previousAuthoringDistribution = structuredClone(authoringDistribution);
        authoringDistribution = {
          ...authoringDistribution,
          ...(payload.updates as Partial<VehicleDistributionValues>),
        };
        authoringRevision += 1;
        authoringCanUndo = true;
        authoringCanRedo = false;
        return envelope({ result: authoringSession(operation) });
      }
      if (operation === "apply_vehicle_package_build") {
        const root = String(payload.destination);
        return envelope({ result: ({
          kind: "vehicle_package_build_result",
          operation: "apply_vehicle_package_build",
          review_sha256: String(payload.review_sha256),
          package: {
            root,
            manifest: `${root}\\mod.toml`,
            payload: `${root}\\payload\\dlc.rpf`,
            report: `${root}\\vehicle-package-report.json`,
            catalog: `${root}\\payload\\vehicles.json`,
            content_manifest: `${root}\\allin1.content.json`,
            profiles: `${root}\\payload\\vehicle-profiles.json`,
            pack_name: String(payload.pack_name ?? "comet6"),
            mod_id: String(payload.mod_id ?? "vehicle.comet6"),
            payload_sha256: "a".repeat(64),
            source_mode: "prebuilt_dlc_rpf",
          },
          warnings: ["Transmission ratios are preserved in vehicle-profiles.json; runtime activation remains a separate integration step."],
          read_only: false,
          workspace_write_performed: false,
          package_write_performed: true,
          game_write_performed: false,
        } satisfies VehiclePackageBuildResult) });
      }
      const current = { ...authoringValues };
      const currentAppearance = structuredClone(authoringAppearance);
      const currentTuning = structuredClone(authoringTuning);
      const currentAxles = structuredClone(authoringAxles);
      const currentTransmission = structuredClone(authoringTransmission);
      const currentDistribution = structuredClone(authoringDistribution);
      authoringValues = { ...previousAuthoringValues };
      authoringAppearance = structuredClone(previousAuthoringAppearance);
      authoringTuning = structuredClone(previousAuthoringTuning);
      authoringAxles = structuredClone(previousAuthoringAxles);
      authoringTransmission = structuredClone(previousAuthoringTransmission);
      authoringDistribution = structuredClone(previousAuthoringDistribution);
      previousAuthoringValues = current;
      previousAuthoringAppearance = currentAppearance;
      previousAuthoringTuning = currentTuning;
      previousAuthoringAxles = currentAxles;
      previousAuthoringTransmission = currentTransmission;
      previousAuthoringDistribution = currentDistribution;
      authoringRevision += 1;
      authoringTuning.revision = authoringRevision;
      authoringCanUndo = String(payload.direction) === "redo";
      authoringCanRedo = String(payload.direction) === "undo";
      return envelope({ result: { ...authoringSession(operation), direction: payload.direction } });
    },
    modelMaterialAuthoringAction: async (operation, payload) => {
      if (operation === "create_model_material_workspace") {
        materialRevision = 0;
        materialCanUndo = false;
        materialProject = structuredClone(SAMPLE_MODEL_MATERIAL_RESULT);
        previousMaterialProject = structuredClone(materialProject);
        return envelope({ result: materialAuthoringSession(operation) });
      }
      if (operation === "apply_model_material_edit") {
        previousMaterialProject = structuredClone(materialProject);
        if (payload.action === "material") {
          const index = Number(payload.material_index);
          const material = materialProject.materials.find((item) => item.index === index);
          if (material) {
            if (typeof payload.shader_name === "string") material.shader = payload.shader_name;
            const textures = (payload.textures ?? {}) as Record<string, string>;
            material.textures = material.textures.map((binding) => ({
              ...binding,
              texture: Object.entries(textures).find(([slot]) => slot.toLocaleLowerCase() === binding.slot.toLocaleLowerCase())?.[1] ?? binding.texture,
            }));
          }
        } else if (payload.action === "parameter") {
          const material = materialProject.materials.find((item) => item.index === Number(payload.material_index));
          const parameter = material?.parameters.find((item) => item.name.toLocaleLowerCase() === String(payload.parameter_name).toLocaleLowerCase());
          if (parameter && Array.isArray(payload.values)) {
            parameter.values = payload.values.map((row) => (
              Array.isArray(row) ? row.map(Number) : []
            )) as [number, number, number, number][];
          }
        } else {
          const geometry = materialProject.geometries.find((item) => item.index === Number(payload.geometry_index));
          const materialIndex = Number(payload.material_index);
          if (geometry) {
            geometry.material_index = materialIndex;
            geometry.material_name = geometry.available_materials[materialIndex] ?? "";
          }
        }
        materialRevision += 1;
        materialCanUndo = true;
        return envelope({ result: materialAuthoringSession(operation) });
      }
      if (operation === "apply_model_material_build") {
        const destination = String(payload.destination ?? "C:\\SDK\\exports\\comet6.yft");
        const outputName = destination.split(/[\\/]/).at(-1) ?? "comet6.yft";
        const outputParent = destination.slice(0, Math.max(destination.lastIndexOf("\\"), destination.lastIndexOf("/")));
        const builtProject: ModelMaterialProjectResult = {
          ...structuredClone(materialProject),
          kind: "model_material_project",
          operation: "inspect_model_materials",
          source: destination,
          name: outputName,
          sha256: "8".repeat(64),
          revision: null,
          viewport: { source: outputParent, entry: outputName, texture_entry: null, collision_entry: null },
          read_only: true,
          workspace_write_performed: false,
          package_write_performed: false,
          game_write_performed: false,
        };
        return envelope({ result: {
          kind: "model_material_build_result",
          operation,
          workspace: materialWorkspace,
          revision: materialRevision,
          review_sha256: String(payload.review_sha256),
          output: { path: destination, size: builtProject.size, sha256: builtProject.sha256 },
          validation_report: `${destination}.allin1.json`,
          validation_report_sha256: "9".repeat(64),
          validation: {
            reparsed: true,
            xml_sha256: "7".repeat(64),
            edited_semantic_xml_sha256: "6".repeat(64),
            reparsed_semantic_xml_sha256: "6".repeat(64),
            semantic_xml_match: true,
            dependency_count: 0,
          },
          comparison: {
            source_xml_sha256: "6".repeat(64), output_sha256: builtProject.sha256,
            source_materials: materialProject.summary.materials, output_materials: builtProject.summary.materials,
            source_geometries: materialProject.summary.geometries, output_geometries: builtProject.summary.geometries,
          },
          built_project: builtProject,
          read_only: false,
          output_write_performed: true,
          workspace_write_performed: false,
          package_write_performed: false,
          game_write_performed: false,
        } });
      }
      const current = structuredClone(materialProject);
      materialProject = structuredClone(previousMaterialProject);
      previousMaterialProject = current;
      materialRevision += 1;
      materialCanUndo = false;
      return envelope({ result: materialAuthoringSession(operation) });
    },
    textureAuthoringAction: async (operation, payload) => {
      if (operation === "create_texture_workspace") {
        textureRevision = 0;
        textureCanUndo = false;
        return envelope({ result: textureSession(operation) });
      }
      if (operation === "apply_texture_edit") {
        previousTextures = structuredClone(textures);
        const action = String(payload.action);
        const name = String(payload.texture_name);
        if (action === "remove") {
          textures = textures.filter((item) => item.name.toLocaleLowerCase() !== name.toLocaleLowerCase());
        } else if (action === "add") {
          textures.push({ name, file_name: `${name}.dds`, width: 2048, height: 1024, mip_levels: 1, format: "D3DFMT_A8B8G8R8", usage: "Diffuse", size: 8_388_736, sha256: "b".repeat(64), warnings: [] });
        } else {
          textures = textures.map((item) => item.name.toLocaleLowerCase() === name.toLocaleLowerCase()
            ? { ...item, width: 2048, height: 1024, mip_levels: 1, format: "D3DFMT_A8B8G8R8", size: 8_388_736, sha256: "b".repeat(64) }
            : item);
        }
        textureRevision += 1;
        textureCanUndo = true;
        return envelope({ result: textureSession(operation) });
      }
      if (operation === "apply_texture_history") {
        const current = structuredClone(textures);
        textures = structuredClone(previousTextures);
        previousTextures = current;
        textureRevision += 1;
        textureCanUndo = false;
        return envelope({ result: textureSession(operation) });
      }
      const destination = String(payload.destination ?? "C:\\SDK\\exports\\comet6.ytd");
      return envelope({ result: {
        kind: "texture_build_result",
        operation: "apply_texture_build",
        workspace: textureWorkspace,
        revision: textureRevision,
        state_sha256: textureStateSha(),
        review_sha256: String(payload.review_sha256),
        output: { path: destination, size: 8_314_880, sha256: "c".repeat(64) },
        validation: { reparsed: true, xml_sha256: "d".repeat(64), edited_semantic_xml_sha256: "e".repeat(64), reparsed_semantic_xml_sha256: "e".repeat(64), semantic_xml_match: true, dependency_count: textures.length },
        validation_report: `${destination}.allin1.json`,
        validation_report_sha256: "f".repeat(64),
        output_write_performed: true,
        workspace_write_performed: false,
        package_write_performed: false,
        game_write_performed: false,
      } });
    },
    startJob: async (operation, payload, revision, onEvent) => {
      const jobId = "preview-job";
      if (payload.module === "code") {
        onEvent({ ...envelope({ revision, message: "XML/Lua parsing and file editing require the desktop SDK service; this browser-only preview cannot validate or save source." }), operation: "error", job_id: jobId });
        return { job_id: jobId, accepted: { ...envelope({ revision }), job_id: jobId, terminal: false } };
      }
      const result = ((operation === "inspect_authoring_workspace" || operation === "review_workspace_action") && payload.module === "graph") ? graphWorkspacePreview(operation, payload)
        : operation === "inspect_ped_workbench" ? pedPreviewSnapshot(typeof payload.workspace === "string" ? payload.workspace : null, typeof payload.ped === "string" ? payload.ped : undefined)
        : operation === "review_ped_authoring" ? pedPreviewReview(payload)
        : operation === "inspect_weapon_workbench"
        ? weaponPreviewSnapshot(typeof payload.workspace === "string" ? payload.workspace : null, typeof payload.weapon === "string" ? payload.weapon : undefined,
            payload.editor_kind === "component" || payload.editor_kind === "attachment" || payload.editor_kind === "shop" || payload.editor_kind === "animation" ? payload.editor_kind : "weapon",
            typeof payload.component === "string" ? payload.component : undefined,
            typeof payload.metadata_source === "string" ? payload.metadata_source : undefined)
        : operation === "review_weapon_authoring" ? weaponPreviewReview(payload)
        : operation === "preview_asset"
        ? mode === "workbench"
          ? vehiclePreviewFixture(String(payload.entry ?? ""))
          : mode === "rpf" || String(payload.source ?? "").toLocaleLowerCase().endsWith(".rpf")
          ? rpfPreviewFixture(String(payload.entry ?? ""))
          : previewFixture(sample.source, String(payload.entry ?? "content.xml"))
        : operation === "inspect_model_materials"
          ? SAMPLE_MODEL_MATERIAL_RESULT
        : operation === "inspect_model_material_workspace"
          ? materialAuthoringSession()
        : operation === "review_model_material_workspace"
          ? {
              kind: "model_material_workspace_review",
              operation: "review_model_material_workspace",
              source: String(payload.source),
              source_name: "comet6.yft",
              source_size: SAMPLE_MODEL_MATERIAL_RESULT.size,
              source_sha256: SAMPLE_MODEL_MATERIAL_RESULT.sha256,
              edition: String(payload.edition),
              destination: `${String(payload.parent)}\\${String(payload.name)}`,
              ready: true,
              review_sha256: "6".repeat(64),
              review_only: true,
              workspace_write_performed: false,
              package_write_performed: false,
              game_write_performed: false,
            }
        : operation === "review_model_material_edit"
          ? (() => {
              const action = payload.action === "geometry" ? "geometry" : payload.action === "parameter" ? "parameter" : "material";
              if (action === "geometry") {
                const geometry = materialProject.geometries.find((item) => item.index === Number(payload.geometry_index));
                const index = Number(payload.material_index);
                return {
                  kind: "model_material_edit_review", operation: "review_model_material_edit",
                  workspace: materialWorkspace, revision: materialRevision, action,
                  subject: `geometry:${String(payload.geometry_index)}`,
                  changes: [{ field: "geometry.shaderIndex", before: String(geometry?.material_index ?? ""), after: String(index) }],
                  ready: true, review_sha256: "5".repeat(64), review_only: true,
                  workspace_write_performed: false, package_write_performed: false,
                  game_write_performed: false,
                };
              }
              if (action === "parameter") {
                const material = materialProject.materials.find((item) => item.index === Number(payload.material_index));
                const parameter = material?.parameters.find((item) => item.name.toLocaleLowerCase() === String(payload.parameter_name).toLocaleLowerCase());
                const values = payload.values as string[][];
                const changes = (parameter?.values ?? []).flatMap((row, rowIndex) => row.flatMap((value, axisIndex) => {
                  const after = values?.[rowIndex]?.[axisIndex];
                  return after !== undefined && Number(after) !== value ? [{ field: `parameter.${parameter?.name}[${rowIndex}].${["x", "y", "z", "w"][axisIndex]}`, before: String(value), after: String(after) }] : [];
                }));
                return {
                  kind: "model_material_edit_review", operation: "review_model_material_edit",
                  workspace: materialWorkspace, revision: materialRevision, action,
                  subject: `parameter:${String(payload.material_index)}:${String(payload.parameter_name)}`,
                  changes, ready: true, review_sha256: "5".repeat(64), review_only: true,
                  workspace_write_performed: false, package_write_performed: false,
                  game_write_performed: false,
                };
              }
              const material = materialProject.materials.find((item) => item.index === Number(payload.material_index));
              const textures = (payload.textures ?? {}) as Record<string, string>;
              const changes = [
                ...(typeof payload.shader_name === "string" && payload.shader_name !== material?.shader ? [{ field: "shader.name", before: material?.shader ?? "", after: payload.shader_name }] : []),
                ...Object.entries(textures).flatMap(([slot, value]) => {
                  const binding = material?.textures.find((item) => item.slot.toLocaleLowerCase() === slot.toLocaleLowerCase());
                  return binding && binding.texture !== value ? [{ field: `texture.${slot}`, before: binding.texture, after: value }] : [];
                }),
              ];
              return {
                kind: "model_material_edit_review", operation: "review_model_material_edit",
                workspace: materialWorkspace, revision: materialRevision, action,
                subject: `material:${String(payload.material_index)}`, changes,
                ready: true, review_sha256: "5".repeat(64), review_only: true,
                workspace_write_performed: false, package_write_performed: false,
                game_write_performed: false,
              };
            })()
        : operation === "review_model_material_build"
          ? {
              kind: "model_material_build_review",
              operation: "review_model_material_build",
              workspace: materialWorkspace,
              revision: materialRevision,
              project_sha256: "6".repeat(64),
              edition: SAMPLE_MODEL_MATERIAL_RESULT.edition,
              source_name: SAMPLE_MODEL_MATERIAL_RESULT.name,
              destination: String(payload.destination),
              validation_report: `${String(payload.destination)}.allin1.json`,
              checks: [
                { key: "revision", label: "Workspace revision", status: "ready", detail: `Revision ${materialRevision} is current` },
                { key: "toolchain", label: "Native compiler", status: "ready", detail: "RpfPatcher asset-from-xml is available" },
                { key: "reparse", label: "Post-build validation", status: "ready", detail: "Compiled output must decode back to XML" },
                { key: "destination", label: "Output boundary", status: "ready", detail: "New asset outside the workspace and GTA V" },
              ],
              ready: true,
              review_sha256: "4".repeat(64),
              review_only: true,
              output_write_performed: false,
              workspace_write_performed: false,
              package_write_performed: false,
              game_write_performed: false,
            }
        : operation === "inspect_texture_workspace"
          ? textureSession()
        : operation === "review_texture_workspace"
          ? {
              kind: "texture_workspace_review",
              operation,
              source: String(payload.source),
              source_name: "comet6.ytd",
              source_size: 8_204_288,
              source_sha256: "5".repeat(64),
              edition: String(payload.edition),
              destination: `${String(payload.parent)}\\${String(payload.name)}`,
              ready: true,
              review_sha256: "6".repeat(64),
              review_only: true,
              workspace_write_performed: false,
              output_write_performed: false,
              package_write_performed: false,
              game_write_performed: false,
            }
        : operation === "preview_texture_workspace"
          ? {
              kind: "texture_workspace_preview",
              workspace: textureWorkspace,
              state_sha256: textureStateSha(),
              texture: structuredClone(textures.find((item) => item.name === String(payload.texture_name)) ?? textures[0]),
              artifact: { path: "C:\\SDK\\cache\\texture-preview.png", preview_url: "/asset-preview-fixture.svg", sha256: "7".repeat(64), size: 41_210, media_type: "image/png", width: 960, height: 680 },
              warning: null,
              read_only: true,
              workspace_write_performed: false,
              package_write_performed: false,
              game_write_performed: false,
            }
        : operation === "review_texture_edit"
          ? (() => {
              const action = String(payload.action) as "replace" | "add" | "remove";
              const name = String(payload.texture_name);
              const existing = textures.find((item) => item.name.toLocaleLowerCase() === name.toLocaleLowerCase());
              const source = action === "remove" ? null : {
                source: String(payload.source_image), size: 2_480_112, sha256: "8".repeat(64),
                width: 2048, height: 1024, mip_levels: 1, format: "D3DFMT_A8B8G8R8", converted_to_dds: true,
              };
              return {
                kind: "texture_edit_review", operation, workspace: textureWorkspace,
                revision: textureRevision, state_sha256: textureStateSha(), action, texture_name: name,
                source,
                changes: [
                  { field: "texture", before: existing?.name ?? "(absent)", after: action === "remove" ? "(removed)" : name },
                  ...(source ? [
                    { field: "dimensions", before: existing ? `${existing.width}×${existing.height}` : "(absent)", after: `${source.width}×${source.height}` },
                    { field: "format", before: existing?.format ?? "(absent)", after: source.format },
                  ] : []),
                ],
                warning: action === "remove" ? "Removing a texture may leave external model bindings unresolved." : "Raster inputs are converted to uncompressed RGBA DDS with one mip level.",
                ready: true, review_sha256: "9".repeat(64), review_only: true,
                workspace_write_performed: false, package_write_performed: false, game_write_performed: false,
              };
            })()
        : operation === "review_texture_build"
          ? {
              kind: "texture_build_review", operation, workspace: textureWorkspace,
              revision: textureRevision, state_sha256: textureStateSha(), destination: String(payload.destination),
              validation_report: `${String(payload.destination)}.allin1.json`,
              checks: [
                { key: "revision", label: "Workspace state", status: "ready", detail: `Revision ${textureRevision} and its texture digest are current` },
                { key: "toolchain", label: "Native compiler", status: "ready", detail: "RpfPatcher asset-from-xml is available" },
                { key: "reparse", label: "Post-build validation", status: "ready", detail: "Compiled YTD must decode and preserve semantic XML" },
                { key: "destination", label: "Output boundary", status: "ready", detail: "New YTD outside the workspace and GTA V" },
              ],
              ready: true, review_sha256: "a".repeat(64), review_only: true,
              output_write_performed: false, workspace_write_performed: false, package_write_performed: false, game_write_performed: false,
            }
        : operation === "assistant_status"
          ? SAMPLE_ASSISTANT_STATUS
        : operation === "assistant_prompt"
          ? SAMPLE_ASSISTANT_RESULT
        : operation === "inspect_rpf_archive"
          ? SAMPLE_RPF_RESULT
        : operation === "inspect_vehicle_project"
          ? SAMPLE_VEHICLE_PROJECT_RESULT
        : operation === "inspect_vehicle_authoring_workspace"
          ? authoringSession()
        : operation === "inspect_vehicle_authoring_tuning"
          ? { ...structuredClone(authoringTuning), revision: authoringRevision }
        : operation === "review_vehicle_authoring_workspace"
          ? {
              kind: "vehicle_authoring_workspace_review",
              operation: "review_vehicle_authoring_workspace",
              source: String(payload.source),
              destination: `${String(payload.parent)}\\${String(payload.name)}`,
              source_kind: "folder",
              inventory_fingerprint: SAMPLE_VEHICLE_PROJECT_RESULT.inventory_fingerprint,
              model_count: SAMPLE_VEHICLE_PROJECT_RESULT.model_count,
              models: SAMPLE_VEHICLE_PROJECT_RESULT.models.map((item) => item.model),
              copy_bytes: 17_281_024,
              ready: true,
              review_sha256: "a".repeat(64),
              review_only: true,
              workspace_write_performed: false,
              package_write_performed: false,
              game_write_performed: false,
            }
        : operation === "review_vehicle_authoring_edit"
          ? {
              kind: "vehicle_authoring_edit_review",
              operation: "review_vehicle_authoring_edit",
              workspace: authoringWorkspace,
              revision: authoringRevision,
              model: "comet6",
              changes: Object.entries(payload.updates as Record<string, string>).map(([field, value]) => ({ field, before: authoringValues[field] ?? "", after: value })),
              review_sha256: "b".repeat(64),
              review_only: true,
              workspace_write_performed: false,
              package_write_performed: false,
              game_write_performed: false,
            }
        : operation === "review_vehicle_authoring_appearance"
          ? (() => {
              const appearance = payload.appearance as Pick<VehicleAppearance, "colors" | "kits" | "light_settings" | "siren_settings">;
              const changes = [
                ...(JSON.stringify(appearance.colors) === JSON.stringify(authoringAppearance.colors) ? [] : [{ field: "variation.colors", before: JSON.stringify(authoringAppearance.colors), after: JSON.stringify(appearance.colors) }]),
                ...(appearance.kits.join(", ") === authoringAppearance.kits.join(", ") ? [] : [{ field: "variation.kits", before: authoringAppearance.kits.join(", "), after: appearance.kits.join(", ") }]),
                ...(appearance.light_settings === authoringAppearance.light_settings ? [] : [{ field: "variation.lightSettings", before: authoringAppearance.light_settings, after: appearance.light_settings }]),
                ...(appearance.siren_settings === authoringAppearance.siren_settings ? [] : [{ field: "variation.sirenSettings", before: authoringAppearance.siren_settings, after: appearance.siren_settings }]),
              ];
              return {
                kind: "vehicle_authoring_appearance_review",
                operation: "review_vehicle_authoring_appearance",
                workspace: authoringWorkspace,
                revision: authoringRevision,
                model: "comet6",
                appearance,
                changes,
                review_sha256: "c".repeat(64),
                review_only: true,
                workspace_write_performed: false,
                package_write_performed: false,
                game_write_performed: false,
              };
            })()
        : operation === "review_vehicle_authoring_tuning"
          ? (() => {
              const mutation = payload.mutation as Record<string, unknown>;
              const action = String(mutation.action);
              const collection = String(mutation.collection ?? "visibleMods");
              const index = Number(mutation.index ?? 0);
              const entry = authoringTuning.entries.find((item) => item.collection === collection && item.index === index);
              const values = (mutation.values ?? {}) as Record<string, string>;
              const before = action === "update_kit"
                ? JSON.stringify({ kit_type: authoringTuning.kit_type, livery_names: authoringAppearance.available_kits.find((item) => item.name === mutation.kit_name)?.livery_names ?? [] })
                : entry ? JSON.stringify(entry.fields) : action === "move_entry" ? String(index) : "";
              const after = action === "update_kit"
                ? JSON.stringify({ kit_type: mutation.kit_type, livery_names: mutation.livery_names })
                : action === "remove_entry" ? ""
                : action === "move_entry" ? String(mutation.new_index)
                : JSON.stringify({ ...(entry?.fields ?? {}), ...values });
              return {
                kind: "vehicle_authoring_tuning_review",
                operation: "review_vehicle_authoring_tuning",
                workspace: authoringWorkspace,
                revision: authoringRevision,
                model: "comet6",
                action,
                mutation,
                changes: [{ field: action === "update_kit" ? `tuning.${String(mutation.kit_name)}.metadata` : `tuning.${String(mutation.kit_name)}.${collection}${action === "update_entry" || action === "remove_entry" ? `[${index}]` : ""}`, before, after }],
                review_sha256: "d".repeat(64),
                review_only: true,
                workspace_write_performed: false,
                package_write_performed: false,
                game_write_performed: false,
              };
            })()
        : operation === "review_vehicle_authoring_light_profile"
          ? (() => {
              const profileId = String(payload.profile_id);
              const profile = authoringAppearance.light_profiles.find((item) => item.profile_id === profileId);
              const updates = payload.updates as Record<string, string>;
              return {
                kind: "vehicle_authoring_light_profile_review",
                operation: "review_vehicle_authoring_light_profile",
                workspace: authoringWorkspace,
                revision: authoringRevision,
                model: "comet6",
                profile_id: profileId,
                updates,
                changes: Object.entries(updates).filter(([field, value]) => profile?.values[field] !== value).map(([field, value]) => ({ field: `light.${profileId}.${field}`, before: profile?.values[field] ?? "", after: value })),
                review_sha256: "e".repeat(64),
                review_only: true,
                workspace_write_performed: false,
                package_write_performed: false,
                game_write_performed: false,
              };
            })()
        : operation === "review_vehicle_authoring_axles"
          ? (() => {
              const configuration = structuredClone(payload.configuration as VehicleAxleConfiguration);
              return {
                kind: "vehicle_authoring_axle_review",
                operation: "review_vehicle_authoring_axles",
                workspace: authoringWorkspace,
                revision: authoringRevision,
                model: "comet6",
                configuration,
                changes: [{ field: "axles.configuration", before: JSON.stringify(authoringAxles), after: JSON.stringify(configuration) }],
                findings: [],
                warnings: [],
                review_sha256: "f".repeat(64),
                review_only: true,
                workspace_write_performed: false,
                package_write_performed: false,
                game_write_performed: false,
              };
            })()
        : operation === "inspect_vehicle_authoring_axle_skeleton"
          ? (() => {
              const action = String(payload.action ?? "validate");
              const configuration = structuredClone(
                (payload.configuration as VehicleAxleConfiguration | undefined)
                ?? authoringAxles,
              );
              if (action === "steering") {
                const gains = [1, 0, -0.62];
                configuration.axles = configuration.axles.map((axle, index) => ({
                  ...axle,
                  steering_gain: axle.steered ? gains[index] ?? 0 : 0,
                }));
                configuration.schema_version = 2;
                configuration.export_mode = "selective_runtime";
                configuration.steering_calculation = {
                  mode: "automatic_geometry",
                  bone_position_sha256: "9".repeat(64),
                };
              }
              return {
                kind: "vehicle_authoring_axle_skeleton",
                operation: "inspect_vehicle_authoring_axle_skeleton",
                workspace: authoringWorkspace,
                revision: authoringRevision,
                model: "comet6",
                action,
                skeleton_xml: String(payload.skeleton_xml),
                bone_count: 64,
                wheel_bones: [
                  { name: "wheel_lf", position: [-1, 3, 0] },
                  { name: "wheel_rf", position: [1, 3, 0] },
                  { name: "wheel_lm1", position: [-1, 0, 0] },
                  { name: "wheel_rm1", position: [1, 0, 0] },
                  { name: "wheel_lr", position: [-1, -3, 0] },
                  { name: "wheel_rr", position: [1, -3, 0] },
                ],
                bone_position_sha256: "9".repeat(64),
                configuration,
                solution: action === "steering" ? {
                  reference_lock_degrees: 35,
                  reference_axle_order: 1,
                  pivot_axle_orders: [2],
                } : null,
                findings: [],
                warnings: [],
                review_only: true,
                workspace_write_performed: false,
                package_write_performed: false,
                game_write_performed: false,
              };
            })()
        : operation === "review_vehicle_authoring_transmission"
          ? (() => {
              const configuration = structuredClone(
                payload.configuration as VehicleTransmissionConfiguration,
              );
              return {
                kind: "vehicle_authoring_transmission_review",
                operation: "review_vehicle_authoring_transmission",
                workspace: authoringWorkspace,
                revision: authoringRevision,
                model: "comet6",
                configuration,
                changes: [{
                  field: "transmission.configuration",
                  before: JSON.stringify(authoringTransmission),
                  after: JSON.stringify(configuration),
                }],
                warnings: [],
                review_sha256: "8".repeat(64),
                review_only: true,
                workspace_write_performed: false,
                package_write_performed: false,
                game_write_performed: false,
              };
            })()
        : operation === "review_vehicle_authoring_distribution"
          ? (() => {
              const distribution = {
                ...authoringDistribution,
                ...(payload.updates as Partial<VehicleDistributionValues>),
              };
              return {
                kind: "vehicle_authoring_distribution_review",
                operation: "review_vehicle_authoring_distribution",
                workspace: authoringWorkspace,
                revision: authoringRevision,
                model: "comet6",
                distribution,
                changes: Object.entries(payload.updates as Record<string, unknown>).map(([field, value]) => ({
                  field: `distribution.${field}`,
                  before: String(authoringDistribution[field]),
                  after: String(value ?? ""),
                })),
                review_sha256: "7".repeat(64),
                review_only: true,
                workspace_write_performed: false,
                package_write_performed: false,
                game_write_performed: false,
              };
            })()
        : operation === "review_vehicle_package_build"
          ? {
              kind: "vehicle_package_build_review",
              operation: "review_vehicle_package_build",
              workspace: authoringWorkspace,
              revision: authoringRevision,
              source: `${authoringWorkspace}\\source`,
              destination: String(payload.destination),
              pack_name: String(payload.pack_name ?? "comet6"),
              mod_id: String(payload.mod_id ?? "vehicle.comet6"),
              name: String(payload.name ?? "Comet S2 vehicle add-on"),
              version: String(payload.version ?? "1.0.0"),
              editions: payload.editions as string[] ?? ["legacy", "enhanced"],
              source_mode: "prebuilt_dlc_rpf",
              models: ["comet6"],
              ready: true,
              checks: [
                { key: "workspace", label: "Workspace revision", status: "ready", detail: `Revision ${authoringRevision} is current` },
                { key: "source", label: "DLC source", status: "ready", detail: "prebuilt dlc rpf" },
                { key: "distribution", label: "Distribution catalog", status: "ready", detail: "1 listed vehicle" },
                { key: "profiles", label: "Authoring profiles", status: "ready", detail: "1 axle · 1 transmission" },
                { key: "destination", label: "Output boundary", status: "ready", detail: "New folder outside the workspace and GTA V" },
              ],
              warnings: ["Transmission ratios are preserved in vehicle-profiles.json; runtime activation remains a separate integration step."],
              review_sha256: "6".repeat(64),
              review_only: true,
              workspace_write_performed: false,
              package_write_performed: false,
              game_write_performed: false,
            }
        : operation === "inspect_recipe"
          ? SAMPLE_RECIPE_RESULT
        : operation === "inspect_package_receipts"
          ? receiptFixture(typeof payload.selected_id === "string" ? payload.selected_id : null)
        : operation === "review_package_lifecycle"
          ? lifecycleReviewFixture(String(payload.action ?? "install"), payload, mode === "receipts_blocked")
        : operation === "inspect_vehicle_quick_import"
          ? SAMPLE_QUICK_IMPORT_RESULT
        : operation === "review_vehicle_package_publish"
          ? { kind: "vehicle_package_publish_review", source_package: String(payload.source_package), destination: String(payload.destination),
              ...preparedPublication, total_bytes: 24576700, traffic_opt_in: false,
              members: ["allin1.content.json", "allin1.review.json", "mod.toml", "payload/dlc.rpf", "payload/vehicles.json"]
                .map((path, index) => ({ path, size: index === 3 ? 24576000 : 175, sha256: "a".repeat(64) })),
              review_sha256: "c".repeat(64), review_only: true, file_write_performed: false, game_write_performed: false, upload_performed: false }
        : operation === "inspect_gxt2_workspace" ? gxt2PreviewSession({ ...payload, archive_workspace: (mode === "rpf_package" || mode === "rpf_member") && !!payload.workspace, root_member: mode === "rpf_member" })
        : operation === "review_gxt2_action" ? gxt2PreviewReview({ ...payload, archive_workspace: (mode === "rpf_package" || mode === "rpf_member") && !!payload.workspace, root_member: mode === "rpf_member" })
        : operation === "inspect_rpf_change_set" ? rpfChangePreviewSession(String(payload.change_set))
        : operation === "list_rpf_transactions" ? {kind:"rpf_transaction_history",root:"C:\\SDK\\transactions",receipts:[{source:transactionSession(true).source,transaction_id:"preview-transaction",valid:true,status:"applied",archive:transactionSession(true).archive,created_at:"2026-09-04T12:00:00Z",change_count:2}],truncated:false,read_only:true,archive_write_performed:false,game_write_performed:false}
        : operation === "inspect_rpf_transaction" ? transactionSession(String(payload.source).endsWith("receipt.json"))
        : operation === "review_rpf_transaction" ? rpfTransactionPreviewReview(payload, transactionSession(payload.action !== "execute"))
        : operation === "review_rpf_change_set" ? rpfChangePreviewReview(payload)
        : operation === "review_vehicle_oiv_export"
          ? { kind: "vehicle_oiv_export_review", edition: "legacy", destination: String(payload.destination),
              source: String(payload.source), author: String(payload.author), name: String(payload.name ?? "Blista Legacy"),
              package_id: String(payload.package_id ?? "vehicle.blista.legacy"), version: String(payload.version ?? "1.0.0"),
              payload_member: "Legacy/blista/dlc.rpf", payload_size: 24576000, payload_sha256: "a".repeat(64),
              review_sha256: "b".repeat(64), members: ["assembly.xml", "content/dlcpacks/blista/dlc.rpf"],
              review_only: true, game_write_performed: false, file_write_performed: false }
        : operation === "review_vehicle_quick_import"
          ? quickImportReviewFixture(String(payload.edition ?? "enhanced"))
        : operation === "execute"
          ? {
              output: [
                "RPF archive inspection complete",
                `Archive: ${String((payload.args as unknown[] | undefined)?.[0] ?? sample.source)}`,
                "Signature: RPF7",
                "Entries: 428",
                "Encrypted entries: 0",
                "No archive content was executed.",
              ].join("\n"),
            }
          : sample;
      window.setTimeout(() => onEvent({
        ...envelope({ revision, result }),
        request_id: "preview-job-request",
        job_id: jobId,
        sequence: 1,
        risk: "read_only",
      }), 120);
      return { job_id: jobId, accepted: { ...envelope({ revision }), job_id: jobId, terminal: false } };
    },
    cancelJob: async () => envelope(),
    selectPath: async (kind) => kind === "ped_workspace" ? "C:/SDK/workspaces/ped-copy" : kind === "ped_parent" ? "C:/SDK/workspaces" : kind === "rpf_plan" ? transactionSession().source : kind === "rpf_receipt" ? transactionSession(true).source : kind === "rpf_change_set" ? "C:\\SDK\\workspaces\\archive-changes.json" : kind === "rpf_payload" ? "C:\\SDK\\imports\\replacement.gxt2" : kind === "rpf_authorized_root" ? "C:\\SDK\\archives" : kind === "rpf_package_source" ? "C:\\SDK\\exports\\text-rpf-package" : kind === "rpf_package_parent" ? "C:\\SDK\\exports" : kind === "gxt2_source" ? "C:\\SDK\\text\\global.gxt2" : kind === "gxt2_workspace" ? "C:\\SDK\\workspaces\\game-text" : kind === "gxt2_parent" ? "C:\\SDK\\workspaces" : kind === "weapon_package" ? "C:\\SDK\\weapons\\demo" : kind === "weapon_workspace" ? "C:\\SDK\\workspaces\\weapon-copy" : kind === "weapon_parent" ? "C:\\SDK\\workspaces" : kind === "model_asset" ? SAMPLE_MODEL_MATERIAL_RESULT.source : kind === "model_material_parent" ? "C:\\SDK\\workspaces" : kind === "model_material_workspace" ? materialWorkspace : kind === "texture_asset" ? "C:\\SDK\\models\\comet6.ytd" : kind === "texture_workspace_parent" ? "C:\\SDK\\workspaces" : kind === "texture_workspace" ? textureWorkspace : kind === "texture_source" ? "C:\\SDK\\imports\\comet6_sign_1.png" : kind === "rpf" ? SAMPLE_RPF_RESULT.source : kind === "gta_folder" ? SAMPLE_RPF_RESULT.gta_path : kind === "vehicle_authoring_parent" ? "C:\\SDK\\workspaces" : kind === "vehicle_authoring_workspace" ? authoringWorkspace : kind === "vehicle_package_parent" ? "C:\\SDK\\exports" : kind === "vehicle_skeleton" ? "C:\\SDK\\models\\comet6.yft.xml" : kind === "recipe" || kind === "recipe_folder" ? SAMPLE_RECIPE_RESULT.source : mode === "workbench" && (kind === "vehicle_import_source" || kind === "vehicle_import_folder") ? SAMPLE_VEHICLE_PROJECT_RESULT.source : kind === "vehicle_import_source" || kind === "vehicle_import_folder" ? SAMPLE_QUICK_IMPORT_RESULT.source : kind === "mod_package" || kind === "mod_package_folder" ? "C:\\SDK\\packages\\camera-tools\\mod.toml" : sample.source,
    selectReportDestination: async (suggestedName) => `C:\\SDK\\reports\\${suggestedName}`,
    selectOivDestination: async (suggestedName) => `C:\\SDK\\exports\\${suggestedName}`,
    selectPackageZipDestination: async (suggestedName) => `C:\\SDK\\exports\\${suggestedName}`,
    selectModelBuildDestination: async (suggestedName, extension) => `C:\\SDK\\exports\\${suggestedName.replace(/\.[^.]+$/, "")}.${extension}`,
    selectTextureBuildDestination: async (suggestedName) => `C:\\SDK\\exports\\${suggestedName.replace(/\.[^.]+$/, "")}.ytd`,
    exportLinkReport: async (_source, destination) => envelope({ result: { output: `Report written to ${destination}` } }),
    exportRecipeReport: async (_source, destination) => envelope({ result: { output: `Recipe report written to ${destination}` } }),
    initialLaunchRequest: async (): Promise<LaunchRequest | null> => loaded ? {
      workspace: mode === "assets" ? "assets" : mode === "workbench" ? "workbench" : mode === "models" ? "models" : mode === "rpf" ? "rpf" : mode === "recipes" ? "recipes" : mode === "quick_import" ? "quick_import" : mode === "receipts" || mode === "receipts_blocked" ? "receipts" : "linker",
      source: mode === "workbench" ? SAMPLE_VEHICLE_PROJECT_RESULT.source : mode === "models" ? SAMPLE_MODEL_MATERIAL_RESULT.source : mode === "rpf" ? SAMPLE_RPF_RESULT.source : mode === "recipes" ? SAMPLE_RECIPE_RESULT.source : mode === "quick_import" ? SAMPLE_QUICK_IMPORT_RESULT.source : mode === "receipts" || mode === "receipts_blocked" ? SAMPLE_RECEIPT_RESULT.gta_path : sample.source,
      selection: null,
      category: null,
      warning: null,
    } : null,
    onLaunchRequest: async () => () => undefined,
    checkUpdate: async () => ({ current_version: packageInfo.version, latest_version: packageInfo.version, update_available: false, name: "ALLIN1", page_url: "", archive_name: "", archive_size: 0 }),
    restartSidecar: async () => envelope(),
    onCloseRequested: async () => () => undefined,
    closeWindow: async () => undefined,
    onSidecarStatus: async () => () => undefined,
  };
}
