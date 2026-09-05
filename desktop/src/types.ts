export type Risk =
  | "none"
  | "read_only"
  | "authoring_write"
  | "game_write"
  | "unclassified";

export type Operation =
  | "handshake"
  | "catalog"
  | "execute"
  | "inspect_package"
  | "preview_asset"
  | "render_vehicle_model"
  | "inspect_model_materials"
  | "inspect_model_material_workspace"
  | "review_model_material_workspace"
  | "create_model_material_workspace"
  | "review_model_material_edit"
  | "apply_model_material_edit"
  | "apply_model_material_history"
  | "review_model_material_build"
  | "apply_model_material_build"
  | "inspect_texture_workspace"
  | "list_rpf_transactions" | "inspect_rpf_transaction" | "review_rpf_transaction" | "apply_rpf_transaction" | "inspect_rpf_change_set" | "review_rpf_change_set" | "apply_rpf_change_set" | "inspect_authoring_workspace" | "review_workspace_action" | "inspect_gxt2_workspace" | "review_gxt2_action" | "apply_gxt2_action" | "apply_workspace_action"
  | "review_texture_workspace"
  | "create_texture_workspace"
  | "preview_texture_workspace"
  | "review_texture_edit"
  | "apply_texture_edit"
  | "apply_texture_history"
  | "review_texture_build"
  | "apply_texture_build"
  | "assistant_status"
  | "assistant_prompt"
  | "configure_assistant"
  | "inspect_weapon_workbench"
  | "inspect_ped_workbench"
  | "review_ped_authoring"
  | "apply_ped_authoring"
  | "review_weapon_authoring"
  | "apply_weapon_authoring"
  | "inspect_rpf_archive"
  | "review_rpf_utility"
  | "apply_rpf_utility"
  | "inspect_vehicle_project"
  | "inspect_vehicle_authoring_workspace"
  | "review_vehicle_authoring_workspace"
  | "create_vehicle_authoring_workspace"
  | "review_vehicle_authoring_edit"
  | "apply_vehicle_authoring_edit"
  | "review_vehicle_authoring_appearance"
  | "apply_vehicle_authoring_appearance"
  | "inspect_vehicle_authoring_tuning"
  | "review_vehicle_authoring_tuning"
  | "apply_vehicle_authoring_tuning"
  | "review_vehicle_authoring_light_profile"
  | "apply_vehicle_authoring_light_profile"
  | "review_vehicle_authoring_axles"
  | "apply_vehicle_authoring_axles"
  | "inspect_vehicle_authoring_axle_skeleton"
  | "review_vehicle_authoring_transmission"
  | "apply_vehicle_authoring_transmission"
  | "review_vehicle_authoring_distribution"
  | "apply_vehicle_authoring_distribution"
  | "review_vehicle_package_build"
  | "apply_vehicle_package_build"
  | "apply_vehicle_authoring_history"
  | "inspect_recipe"
  | "inspect_package_receipts"
  | "review_package_lifecycle"
  | "apply_package_lifecycle"
  | "inspect_vehicle_quick_import"
  | "review_vehicle_quick_import"
  | "prepare_vehicle_quick_import"
  | "review_vehicle_oiv_export"
  | "apply_vehicle_oiv_export"
  | "review_vehicle_package_publish"
  | "apply_vehicle_package_publish"
  | "check_update"
  | "start_job"
  | "cancel_job"
  | "job_event"
  | "result"
  | "error"
  | "shutdown";

export interface Envelope<T = Record<string, unknown>> {
  protocol_version: "1.0.0";
  request_id: string | null;
  job_id: string | null;
  operation: Operation;
  payload: T;
  sequence: number;
  risk: Risk;
  terminal: boolean;
}

export interface CommandParameter {
  name: string;
  kind: "option" | "argument";
  required: boolean;
  type: string;
  flags?: string[];
  help?: string;
  choices?: string[];
}

export interface CommandCatalogItem {
  name: string;
  description: string;
  risk: Exclude<Risk, "none">;
  parameters: CommandParameter[];
}

export interface NavigationItem {
  id: WorkspaceId;
  label: string;
  shortcut: string;
  phase: number;
}

export type WorkspaceId =
  | "data_tools"
  | "linker"
  | "assets"
  | "workbench"
  | "receipts"
  | "quick_import"
  | "models"
  | "rpf"
  | "recipes"
  | "help";

export interface HelpTopic {
  key: string;
  category: string;
  title: string;
  summary: string;
  body: string;
  keywords: string[];
}

export interface DesktopCatalog {
  commands: CommandCatalogItem[];
  navigation: NavigationItem[];
  help_topics: HelpTopic[];
  operations: string[];
  job_operations: string[];
}

export interface JobStart {
  job_id: string;
  accepted: Envelope;
}

export interface LaunchRequest {
  workspace: WorkspaceId;
  source: string | null;
  selection: string | null;
  category: string | null;
  warning: string | null;
}

export interface UpdateResult {
  current_version: string;
  latest_version: string;
  update_available: boolean;
  name: string;
  page_url: string;
  archive_name: string;
  archive_size: number;
}

export interface DesktopClient {
  selectPath(kind: "code_source" | "authoring_parent" | "binary_source" | "binary_workspace" | "blender_executable" | "graph_document" | "graph_source" | "gta_folder" | "gxt2_parent" | "gxt2_source" | "gxt2_workspace" | "map_descriptor" | "map_source" | "metadata" | "mod_package" | "mod_package_folder" | "model_asset" | "model_material_parent" | "model_material_workspace" | "package" | "package_folder" | "ped_parent" | "ped_workspace" | "program_document" | "recipe" | "recipe_folder" | "render_model" | "render_textures" | "rpf" | "rpf_authorized_root" | "rpf_change_set" | "rpf_package_parent" | "rpf_package_source" | "rpf_payload" | "rpf_plan" | "rpf_receipt" | "texture_asset" | "texture_source" | "texture_workspace" | "texture_workspace_parent" | "vehicle_authoring_parent" | "vehicle_authoring_workspace" | "vehicle_import_folder" | "vehicle_import_source" | "vehicle_package_parent" | "vehicle_skeleton" | "weapon_package" | "weapon_parent" | "weapon_workspace"): Promise<string | null>;
  onCloseRequested(handler: () => void): Promise<() => void>;
  closeWindow(): Promise<void>;
  applyWorkspaceAction(payload: Record<string, unknown>): Promise<Envelope>;
  applyRpfChangeSet(payload: Record<string, unknown>): Promise<Envelope>;
  applyRpfTransaction(payload: Record<string, unknown>): Promise<Envelope>;
  applyRpfUtility(payload: Record<string, unknown>): Promise<Envelope>;
  selectRpfPlanDestination(suggestedName: string): Promise<string | null>;
  selectRpfUtilityDestination(action: string, suggestedName: string): Promise<string | null>;
  handshake(): Promise<Envelope>;
  catalog(): Promise<DesktopCatalog>;
  execute(command: string, args: string[], authoringConfirmed?: boolean): Promise<Envelope>;
  configureAssistant(payload: Record<string, unknown>): Promise<Envelope>;
  applyWeaponAuthoring(payload: Record<string, unknown>): Promise<Envelope>;
  applyPedAuthoring(payload: Record<string, unknown>): Promise<Envelope>;
  prepareVehicleQuickImport(payload: Record<string, unknown>): Promise<Envelope>;
  applyVehicleOivExport(payload: Record<string, unknown>): Promise<Envelope>;
  applyVehiclePackagePublish(payload: Record<string, unknown>): Promise<Envelope>;
  applyPackageLifecycle(payload: Record<string, unknown>): Promise<Envelope>;
  renderVehicleModel(payload: Record<string, unknown>): Promise<Envelope>;
  vehicleAuthoringAction(
    operation: "create_vehicle_authoring_workspace" | "apply_vehicle_authoring_edit" | "apply_vehicle_authoring_appearance" | "apply_vehicle_authoring_tuning" | "apply_vehicle_authoring_light_profile" | "apply_vehicle_authoring_axles" | "apply_vehicle_authoring_transmission" | "apply_vehicle_authoring_distribution" | "apply_vehicle_package_build" | "apply_vehicle_authoring_history",
    payload: Record<string, unknown>,
  ): Promise<Envelope>;
  modelMaterialAuthoringAction(
    operation: "create_model_material_workspace" | "apply_model_material_edit" | "apply_model_material_history" | "apply_model_material_build",
    payload: Record<string, unknown>,
  ): Promise<Envelope>;
  textureAuthoringAction(
    operation: "create_texture_workspace" | "apply_texture_edit" | "apply_texture_history" | "apply_texture_build",
    payload: Record<string, unknown>,
  ): Promise<Envelope>;
  applyGxt2Action(payload: Record<string, unknown>): Promise<Envelope>;
  selectGxt2BuildDestination(suggestedName: string): Promise<string | null>;
  startJob(
    operation: "inspect_ped_workbench" | "review_ped_authoring" | "list_rpf_transactions" | "inspect_rpf_transaction" | "review_rpf_transaction" | "inspect_rpf_change_set" | "review_rpf_change_set" | "inspect_authoring_workspace" | "review_workspace_action" | "inspect_gxt2_workspace" | "review_gxt2_action" | "inspect_weapon_workbench" | "review_weapon_authoring" | "execute" | "inspect_package" | "preview_asset" | "inspect_model_materials" | "inspect_model_material_workspace" | "review_model_material_workspace" | "review_model_material_edit" | "review_model_material_build" | "inspect_texture_workspace" | "review_texture_workspace" | "preview_texture_workspace" | "review_texture_edit" | "review_texture_build" | "assistant_status" | "assistant_prompt" | "inspect_rpf_archive" | "review_rpf_utility" | "inspect_vehicle_project" | "inspect_vehicle_authoring_workspace" | "review_vehicle_authoring_workspace" | "review_vehicle_authoring_edit" | "review_vehicle_authoring_appearance" | "inspect_vehicle_authoring_tuning" | "review_vehicle_authoring_tuning" | "review_vehicle_authoring_light_profile" | "review_vehicle_authoring_axles" | "inspect_vehicle_authoring_axle_skeleton" | "review_vehicle_authoring_transmission" | "review_vehicle_authoring_distribution" | "review_vehicle_package_build" | "inspect_recipe" | "inspect_package_receipts" | "review_package_lifecycle" | "inspect_vehicle_quick_import" | "review_vehicle_quick_import" | "review_vehicle_oiv_export" | "review_vehicle_package_publish" | "check_update",
    payload: Record<string, unknown>,
    revision: string,
    onEvent: (message: Envelope) => void,
  ): Promise<JobStart>;
  cancelJob(jobId: string): Promise<Envelope>;
  selectReportDestination(suggestedName: string): Promise<string | null>;
  selectOivDestination(suggestedName: string): Promise<string | null>;
  selectPackageZipDestination(suggestedName: string): Promise<string | null>;
  selectModelBuildDestination(suggestedName: string, extension: "ydr" | "ydd" | "yft"): Promise<string | null>;
  selectTextureBuildDestination(suggestedName: string): Promise<string | null>;
  exportLinkReport(source: string, destination: string): Promise<Envelope>;
  exportRecipeReport(source: string, destination: string): Promise<Envelope>;
  initialLaunchRequest(): Promise<LaunchRequest | null>;
  onLaunchRequest(handler: (request: LaunchRequest) => void): Promise<() => void>;
  checkUpdate(): Promise<UpdateResult>;
  restartSidecar(): Promise<Envelope>;
  onSidecarStatus(handler: (status: string) => void): Promise<() => void>;
}

export type PackageResult = Record<string, unknown> & {
  kind: "manifest" | "package_scan";
  source: string;
  valid: boolean;
  error_count: number;
  warning_count: number;
};

export interface PreviewArtifact {
  path: string;
  sha256: string;
  size: number;
  media_type: string;
  preview_url?: string;
  width?: number;
  height?: number;
}

export interface AssetPreviewResult extends Record<string, unknown> {
  source: string;
  path: string;
  name: string;
  category: string;
  preview_kind: "text" | "image" | "binary";
  display_kind: "text" | "image" | "metadata";
  size: number;
  bytes_read: number;
  truncated: boolean;
  sha256: string | null;
  text: string | null;
  text_truncated: boolean;
  artifact: PreviewArtifact | null;
  metadata: Record<string, unknown>;
  warnings: string[];
}

export interface VehicleViewportComponent extends Record<string, unknown> {
  name: string;
  lod: string;
  geometry_count: number;
  vertex_count: number;
  triangle_count: number;
  material_names: string[];
  texture_names: string[];
}

export interface VehicleViewportTextureBinding extends Record<string, unknown> {
  slot: string;
  name: string;
  resolved: boolean | null;
}

export interface VehicleViewportMaterialParameter extends Record<string, unknown> {
  name: string;
  source_type: "Vector" | "Array";
  values: [number, number, number, number][];
  record_count: number;
}

export interface VehicleViewportMaterial extends Record<string, unknown> {
  index: number;
  name: string;
  record_count: number;
  geometry_count: number;
  triangle_count: number;
  lods: string[];
  components: string[];
  texture_bindings: VehicleViewportTextureBinding[];
  parameters: VehicleViewportMaterialParameter[];
  parameter_count: number;
}

export interface VehicleViewportTexture extends Record<string, unknown> {
  name: string;
  file_name: string;
  width: number;
  height: number;
  mip_levels: number;
  format: string;
  usage: string;
  size: number;
  sha256: string;
  contact_sheet_index: number;
  warnings: string[];
}

export interface VehicleViewportTextureDictionary extends Record<string, unknown> {
  path: string;
  name: string;
  size: number;
  bytes_read: number;
  sha256: string;
  texture_count: number;
  previewed_count: number;
  truncated: boolean;
  artifact: PreviewArtifact | null;
  textures: VehicleViewportTexture[];
  warnings: string[];
  cache_hit: boolean;
  read_only: true;
}

export interface VehicleViewportCollisionPrimitive extends Record<string, unknown> {
  kind: string;
  count: number;
  overlay: boolean;
  fidelity: "exact mesh" | "diagnostic hull" | "count only";
}

export interface VehicleViewportCollisionDictionary extends Record<string, unknown> {
  path: string;
  name: string;
  size: number;
  bytes_read: number;
  sha256: string;
  geometry_count: number;
  vertex_count: number;
  polygon_count: number;
  material_count: number;
  render_triangle_count: number;
  overlay_polygon_count: number;
  unrendered_polygon_count: number;
  primitive_counts: VehicleViewportCollisionPrimitive[];
  bounds: { min: number[]; max: number[]; size: number[] } | null;
  warnings: string[];
  cache_hit: boolean;
  read_only: true;
}

export interface VehicleViewportUvTextureGroup extends Record<string, unknown> {
  name: string;
  resolved: boolean;
  material_names: string[];
  geometry_count: number;
  sampled_triangle_count: number;
  valid_triangle_count: number;
  rendered_triangle_count: number;
  island_count: number;
  seam_triangle_count: number;
  degenerate_triangle_count: number;
  missing_triangle_count: number;
}

export interface VehicleViewportUvAtlas extends Record<string, unknown> {
  artifact: PreviewArtifact;
  width: number;
  height: number;
  triangle_budget: number;
  source_triangle_count: number;
  sampled_triangle_count: number;
  rendered_triangle_count: number;
  valid_triangle_count: number;
  degenerate_triangle_count: number;
  missing_triangle_count: number;
  seam_triangle_count: number;
  island_count: number;
  texture_group_count: number;
  returned_texture_group_count: number;
  sampled: boolean;
  texture_groups: VehicleViewportUvTextureGroup[];
  selection: { lod: string; component: string; material: string };
  fidelity: string;
  cache_hit: boolean;
  read_only: true;
}

export interface VehicleViewportResult extends Record<string, unknown> {
  kind: "vehicle_model_viewport";
  source: string;
  path: string;
  name: string;
  size: number;
  bytes_read: number;
  sha256: string;
  edition: "legacy" | "enhanced";
  artifact: PreviewArtifact;
  camera: {
    yaw: number;
    pitch: number;
    lod: string;
    component: string;
    material: string;
    render_mode: "materials" | "shaded" | "textured" | "uvs" | "wireframe";
    quality: "interactive" | "final";
    collision_visible: boolean;
  };
  scene: {
    lods: string[];
    components: VehicleViewportComponent[];
    materials: VehicleViewportMaterial[];
    component_count: number;
    material_count: number;
    surface_count: number;
    bone_count: number;
  };
  metadata: Record<string, unknown>;
  texture_dictionary: VehicleViewportTextureDictionary | null;
  collision_dictionary: VehicleViewportCollisionDictionary | null;
  uv_atlas: VehicleViewportUvAtlas | null;
  warnings: string[];
  cache_hit: boolean;
  read_only: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export interface ModelMaterialTextureBinding extends Record<string, unknown> {
  slot: string;
  texture: string;
  role: string;
}

export interface ModelMaterialParameter extends Record<string, unknown> {
  name: string;
  source_type: "Vector" | "Array";
  values: [number, number, number, number][];
}

export interface ModelMaterialRecord extends Record<string, unknown> {
  index: number;
  shader: string;
  textures: ModelMaterialTextureBinding[];
  parameters: ModelMaterialParameter[];
  geometry_indices: number[];
}

export interface ModelGeometryRecord extends Record<string, unknown> {
  index: number;
  component: string;
  lod: string;
  material_index: number | null;
  material_document_index: number | null;
  material_name: string;
  available_materials: string[];
}

export interface ModelMaterialFinding extends Record<string, unknown> {
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
  subject: string;
}

export interface ModelMaterialProjectResult extends Record<string, unknown> {
  kind: "model_material_project" | "model_material_authoring_session";
  operation: "inspect_model_materials" | "inspect_model_material_workspace" | "create_model_material_workspace" | "apply_model_material_edit" | "apply_model_material_history";
  workspace?: string;
  source: string;
  name: string;
  suffix: string;
  edition: string;
  size: number;
  sha256: string;
  revision: number | null;
  can_undo?: boolean;
  summary: {
    materials: number;
    texture_bindings: number;
    numeric_parameters: number;
    geometries: number;
    components: number;
    errors: number;
    warnings: number;
  };
  materials: ModelMaterialRecord[];
  geometries: ModelGeometryRecord[];
  components: Record<string, unknown>[];
  lods: string[];
  metadata: Record<string, unknown>;
  findings: ModelMaterialFinding[];
  viewport: {
    source: string;
    entry: string;
    texture_entry: string | null;
    collision_entry: string | null;
  };
  read_only: boolean;
  workspace_write_performed: boolean;
  package_write_performed: boolean;
  game_write_performed: boolean;
}

export interface ModelMaterialChange extends Record<string, unknown> {
  field: string;
  before: string;
  after: string;
}

export interface ModelMaterialWorkspaceReview extends Record<string, unknown> {
  kind: "model_material_workspace_review";
  operation: "review_model_material_workspace";
  source: string;
  source_name: string;
  source_size: number;
  source_sha256: string;
  edition: string;
  destination: string;
  ready: boolean;
  review_sha256: string;
  review_only: true;
}

export interface ModelMaterialEditReview extends Record<string, unknown> {
  kind: "model_material_edit_review";
  operation: "review_model_material_edit";
  workspace: string;
  revision: number;
  action: "material" | "parameter" | "geometry";
  subject: string;
  changes: ModelMaterialChange[];
  ready: boolean;
  review_sha256: string;
  review_only: true;
}

export interface ModelMaterialBuildCheck extends Record<string, unknown> {
  key: string;
  label: string;
  status: "ready";
  detail: string;
}

export interface ModelMaterialBuildReview extends Record<string, unknown> {
  kind: "model_material_build_review";
  operation: "review_model_material_build";
  workspace: string;
  revision: number;
  project_sha256: string;
  edition: string;
  source_name: string;
  destination: string;
  validation_report: string;
  checks: ModelMaterialBuildCheck[];
  ready: boolean;
  review_sha256: string;
  review_only: true;
  output_write_performed: false;
}

export interface ModelMaterialBuildResult extends Record<string, unknown> {
  kind: "model_material_build_result";
  operation: "apply_model_material_build";
  workspace: string;
  revision: number;
  review_sha256: string;
  output: { path: string; size: number; sha256: string };
  validation_report: string;
  validation_report_sha256: string;
  validation: {
    reparsed: boolean;
    xml_sha256: string;
    edited_semantic_xml_sha256: string;
    reparsed_semantic_xml_sha256: string;
    semantic_xml_match: boolean;
    dependency_count: number;
  };
  comparison: {
    source_xml_sha256: string;
    output_sha256: string;
    source_materials: number;
    output_materials: number;
    source_geometries: number;
    output_geometries: number;
  };
  built_project: ModelMaterialProjectResult;
  output_write_performed: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export interface TextureRecord extends Record<string, unknown> {
  name: string;
  file_name: string;
  width: number;
  height: number;
  mip_levels: number;
  format: string;
  usage: string;
  size: number | null;
  sha256: string | null;
  warnings: string[];
}

export interface TextureWorkspaceSession extends Record<string, unknown> {
  kind: "texture_workspace_session";
  operation: "inspect_texture_workspace" | "create_texture_workspace" | "apply_texture_edit" | "apply_texture_history";
  workspace: string;
  source: string;
  source_name: string;
  source_size: number;
  source_sha256: string;
  edition: string;
  revision: number;
  state_sha256: string;
  can_undo: boolean;
  texture_count: number;
  warnings: string[];
  textures: TextureRecord[];
  read_only: boolean;
  workspace_write_performed: boolean;
  package_write_performed: false;
  game_write_performed: false;
}

export interface TextureWorkspaceReview extends Record<string, unknown> {
  kind: "texture_workspace_review";
  operation: "review_texture_workspace";
  source: string;
  source_name: string;
  source_size: number;
  source_sha256: string;
  edition: string;
  destination: string;
  ready: boolean;
  review_sha256: string;
  review_only: true;
}

export interface TextureSourceInspection extends Record<string, unknown> {
  source: string;
  size: number;
  sha256: string;
  width: number;
  height: number;
  mip_levels: number;
  format: string;
  converted_to_dds: boolean;
}

export interface TextureEditReview extends Record<string, unknown> {
  kind: "texture_edit_review";
  operation: "review_texture_edit";
  workspace: string;
  revision: number;
  state_sha256: string;
  action: "replace" | "add" | "remove";
  texture_name: string;
  source: TextureSourceInspection | null;
  changes: ModelMaterialChange[];
  warning: string | null;
  ready: boolean;
  review_sha256: string;
  review_only: true;
}

export interface TextureWorkspacePreview extends Record<string, unknown> {
  kind: "texture_workspace_preview";
  workspace: string;
  state_sha256: string;
  texture: TextureRecord;
  artifact: (PreviewArtifact & { width: number; height: number }) | null;
  warning: string | null;
  read_only: true;
}

export interface TextureBuildReview extends Record<string, unknown> {
  kind: "texture_build_review";
  operation: "review_texture_build";
  workspace: string;
  revision: number;
  state_sha256: string;
  destination: string;
  validation_report: string;
  checks: ModelMaterialBuildCheck[];
  ready: boolean;
  review_sha256: string;
  review_only: true;
}

export interface TextureBuildResult extends Record<string, unknown> {
  kind: "texture_build_result";
  operation: "apply_texture_build";
  workspace: string;
  revision: number;
  state_sha256: string;
  review_sha256: string;
  output: { path: string; size: number; sha256: string };
  validation: {
    reparsed: boolean;
    xml_sha256?: string;
    edited_semantic_xml_sha256?: string;
    reparsed_semantic_xml_sha256?: string;
    semantic_xml_match: boolean;
    dependency_count: number;
  };
  validation_report: string;
  validation_report_sha256: string;
  output_write_performed: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export interface AssistantStatusResult extends Record<string, unknown> {
  kind: "assistant_status";
  configured: boolean;
  enabled: boolean;
  mode: string;
  model?: string;
  workflow?: string;
  profile?: string;
  local_runtime_running: boolean;
  structured_output_ready: boolean;
  provider_capabilities: string[];
  thinking?: string;
  message: string;
  read_only: true;
}

export interface AssistantFinding extends Record<string, unknown> {
  severity_domain: "engineering" | "security";
  severity: "info" | "low" | "medium" | "high" | "blocker" | "critical";
  evidence: string;
  file: string;
  line: number | null;
  confidence: number;
  status: "confirmed" | "inferred" | "speculative";
}

export interface AssistantAdvisory extends Record<string, unknown> {
  summary: string;
  findings: AssistantFinding[];
  recommended_operations: Record<string, unknown>[];
  proposed_changes: Record<string, unknown>[];
  missing_context: string[];
  abstentions: string[];
}

export interface AssistantPromptResult extends Record<string, unknown> {
  kind: "assistant_prompt_result";
  text: string;
  model: string;
  mode: string;
  elapsed_seconds: number;
  advisory: AssistantAdvisory | null;
  safety_flags: string[];
  estimated_input_tokens: number;
  actual_input_tokens: number | null;
  actual_output_tokens: number | null;
  receipt_path: string;
  thinking: string;
  read_only: true;
  advisory_only: true;
  command_execution_performed: false;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export interface RpfArchiveRecord extends Record<string, unknown> {
  path: string;
  name: string;
  version: number;
  encryption: string;
  size: number;
  entry_count: number;
}

export interface RpfEntryRecord extends Record<string, unknown> {
  id: string;
  archive_path: string;
  path: string;
  name: string;
  kind: "directory" | "resource" | "binary" | "archive";
  size: number;
  stored_size: number;
  encrypted: boolean | null;
  compressed: boolean | null;
  resource_version: number | null;
}

export interface RpfArchiveResult extends Record<string, unknown> {
  kind: "rpf_archive_index";
  operation: "inspect_rpf_archive";
  source: string;
  gta_path: string;
  edition: string;
  archive_size: number;
  archives: RpfArchiveRecord[];
  entries: RpfEntryRecord[];
  warnings: string[];
  suffix_counts: Record<string, number>;
  archive_count: number;
  entry_count: number;
  returned_entry_count: number;
  directory_count: number;
  file_count: number;
  logical_bytes: number;
  stored_bytes: number;
  truncated: boolean;
  read_only: true;
  game_write_performed: false;
}

export interface VehicleProjectFinding extends Record<string, unknown> {
  severity: "error" | "warning" | "info";
  code: string;
  model: string;
  message: string;
}

export interface VehicleProjectAsset extends Record<string, unknown> {
  role: string;
  path: string;
  size: number;
  required: boolean;
  previewable: boolean;
}

export interface VehicleProjectModel extends Record<string, unknown> {
  model: string;
  display_name: string;
  make_name: string;
  vehicle_class: string;
  vehicle_type: string;
  handling_id: string;
  layout: string;
  audio_name_hash: string;
  texture_dictionary: string;
  tuning_kits: string[];
  assets: VehicleProjectAsset[];
  findings: VehicleProjectFinding[];
  primary_model: string | null;
  high_detail_model: string | null;
  texture_asset: string | null;
  collision_asset: string | null;
  ready_for_preview: boolean;
  complete: boolean;
  asset_count: number;
  finding_count: number;
  assets_truncated: boolean;
  findings_truncated: boolean;
}

export interface VehicleProjectResult extends Record<string, unknown> {
  kind: "vehicle_project_inspection";
  operation: "inspect_vehicle_project";
  source: string;
  source_kind: string;
  gta_path: string | null;
  edition: string;
  inventory_fingerprint: string;
  models: VehicleProjectModel[];
  findings: VehicleProjectFinding[];
  axle_configurations: VehicleAxleConfiguration[];
  model_count: number;
  returned_model_count: number;
  asset_count: number;
  returned_asset_count: number;
  previewable_count: number;
  complete_count: number;
  error_count: number;
  warning_count: number;
  model_finding_count: number;
  truncated: boolean;
  read_only: true;
  package_write_performed: false;
  game_write_performed: false;
}

export interface VehicleAuthoringSession extends Record<string, unknown> {
  kind: "vehicle_authoring_session";
  operation: "inspect_vehicle_authoring_workspace" | "create_vehicle_authoring_workspace" | "apply_vehicle_authoring_edit" | "apply_vehicle_authoring_appearance" | "apply_vehicle_authoring_tuning" | "apply_vehicle_authoring_light_profile" | "apply_vehicle_authoring_axles" | "apply_vehicle_authoring_transmission" | "apply_vehicle_authoring_distribution" | "apply_vehicle_authoring_history";
  workspace: string;
  source: string;
  original_source: string;
  revision: number;
  selected_model: string | null;
  editable_fields: string[];
  values: Record<string, string>;
  sources: Record<string, string>;
  appearance: VehicleAppearance | null;
  transmission: VehicleTransmissionConfiguration | null;
  distribution: VehicleDistributionValues | null;
  tuning_builder?: VehicleTuningBuilder;
  can_undo: boolean;
  can_redo: boolean;
  project: VehicleProjectResult;
  read_only: boolean;
  workspace_write_performed: boolean;
  package_write_performed: boolean;
  game_write_performed: false;
  changes?: VehicleAuthoringChange[];
  history?: string;
  direction?: "undo" | "redo";
}

export interface VehicleAuthoringChange extends Record<string, unknown> {
  field: string;
  before: string;
  after: string;
}

export interface VehicleAuthoringWorkspaceReview extends Record<string, unknown> {
  kind: "vehicle_authoring_workspace_review";
  operation: "review_vehicle_authoring_workspace";
  source: string;
  destination: string;
  source_kind: string;
  inventory_fingerprint: string;
  model_count: number;
  models: string[];
  copy_bytes: number;
  ready: true;
  review_sha256: string;
  review_only: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export interface VehicleAuthoringEditReview extends Record<string, unknown> {
  kind: "vehicle_authoring_edit_review";
  operation: "review_vehicle_authoring_edit";
  workspace: string;
  revision: number;
  model: string;
  changes: VehicleAuthoringChange[];
  review_sha256: string;
  review_only: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export interface VehicleColorSet extends Record<string, unknown> {
  indices: number[];
  liveries: boolean[];
}

export interface VehicleTuningKitSummary extends Record<string, unknown> {
  source: string;
  name: string;
  kit_id: string;
  kit_type: string;
  visible_mods: number;
  link_mods: number;
  stat_mods: number;
  livery_names: string[];
}

export interface VehicleLightProfile extends Record<string, unknown> {
  source: string;
  profile_id: string;
  name: string;
  values: Record<string, string>;
}

export interface VehicleAppearance extends Record<string, unknown> {
  model: string;
  source: string;
  colors: VehicleColorSet[];
  kits: string[];
  light_settings: string;
  siren_settings: string;
  available_kits: VehicleTuningKitSummary[];
  light_profiles: VehicleLightProfile[];
}

export interface VehicleAuthoringAppearanceReview extends Record<string, unknown> {
  kind: "vehicle_authoring_appearance_review";
  operation: "review_vehicle_authoring_appearance";
  workspace: string;
  revision: number;
  model: string;
  appearance: Pick<VehicleAppearance, "colors" | "kits" | "light_settings" | "siren_settings">;
  changes: VehicleAuthoringChange[];
  review_sha256: string;
  review_only: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export type VehicleTuningCollection = "visibleMods" | "linkMods" | "statMods" | "slotNames";

export interface VehicleTuningEntry extends Record<string, unknown> {
  collection: VehicleTuningCollection;
  index: number;
  summary: string;
  mod_type: string;
  fields: Record<string, string>;
  key: string;
}

export interface VehicleTuningAsset extends Record<string, unknown> {
  path: string;
  name: string;
  kind: string;
  referenced: boolean;
}

export interface VehicleTuningFinding extends Record<string, unknown> {
  severity: "error" | "warning";
  code: string;
  message: string;
  entry: string;
}

export interface VehicleTuningFieldSchema extends Record<string, unknown> {
  kind: string;
  required: boolean;
  default: string;
}

export interface VehicleTuningBuilder extends Record<string, unknown> {
  kind: "vehicle_authoring_tuning";
  operation: "inspect_vehicle_authoring_tuning";
  workspace: string;
  revision: number;
  model: string;
  kit_name: string;
  kit_id: string;
  kit_type: string;
  source: string;
  entries: VehicleTuningEntry[];
  assets: VehicleTuningAsset[];
  findings: VehicleTuningFinding[];
  error_count: number;
  warning_count: number;
  collections: VehicleTuningCollection[];
  vmt_types: string[];
  field_schemas: Record<VehicleTuningCollection, Record<string, VehicleTuningFieldSchema>>;
  read_only: boolean;
  workspace_write_performed: boolean;
  package_write_performed: boolean;
  game_write_performed: false;
}

export interface VehicleAuthoringTuningReview extends Record<string, unknown> {
  kind: "vehicle_authoring_tuning_review";
  operation: "review_vehicle_authoring_tuning";
  workspace: string;
  revision: number;
  model: string;
  action: string;
  mutation: Record<string, unknown>;
  changes: VehicleAuthoringChange[];
  review_sha256: string;
  review_only: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export interface VehicleAuthoringLightProfileReview extends Record<string, unknown> {
  kind: "vehicle_authoring_light_profile_review";
  operation: "review_vehicle_authoring_light_profile";
  workspace: string;
  revision: number;
  model: string;
  profile_id: string;
  updates: Record<string, string>;
  changes: VehicleAuthoringChange[];
  review_sha256: string;
  review_only: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export interface VehicleAxleSuspension extends Record<string, unknown> {
  support_weight: number;
}

export interface VehicleAxle extends Record<string, unknown> {
  physical_order: number;
  logical_role: "front" | "middle" | "rear" | "tag";
  left_bone: string;
  right_bone: string;
  left_runtime_index: number;
  right_runtime_index: number;
  steered: boolean;
  powered: boolean;
  service_brake: boolean;
  handbrake: boolean;
  visual_family: "front" | "shared_middle_rear";
  addon_geometry: Record<string, unknown>[];
  steering_gain?: number;
  suspension?: VehicleAxleSuspension;
}

export interface VehicleAxleConfiguration extends Record<string, unknown> {
  schema_version: number;
  vehicle_model: string;
  configuration_id: string;
  model_hash: string;
  minimum_runtime_version: string;
  preset: string;
  export_mode: "stock_metadata" | "selective_runtime";
  expected_wheel_count: number;
  axles: VehicleAxle[];
  runtime_reapplication: Record<string, boolean | number>;
  compatibility: Record<string, boolean>;
  handbrake_rear_steering: boolean;
  steering_command_polarity: "normal" | "inverted";
  steering_calculation?: Record<string, unknown>;
  intentional_layout_override?: Record<string, unknown>;
}

export interface VehicleAxleFinding extends Record<string, unknown> {
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
  axle: number | null;
}

export interface VehicleAuthoringAxleReview extends Record<string, unknown> {
  kind: "vehicle_authoring_axle_review";
  operation: "review_vehicle_authoring_axles";
  workspace: string;
  revision: number;
  model: string;
  configuration: VehicleAxleConfiguration;
  changes: VehicleAuthoringChange[];
  findings: VehicleAxleFinding[];
  warnings: string[];
  review_sha256: string;
  review_only: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export type VehicleTransmissionType = "automatic" | "manual" | "sequential" | "dual_clutch";

export interface VehicleTransmissionConfiguration extends Record<string, unknown> {
  schema_version: 1;
  vehicle_model: string;
  transmission_type: VehicleTransmissionType;
  gear_ratios: number[];
  reverse_gear_ratio: number;
  final_drive_ratio: number;
}

export interface VehicleAuthoringTransmissionReview extends Record<string, unknown> {
  kind: "vehicle_authoring_transmission_review";
  operation: "review_vehicle_authoring_transmission";
  workspace: string;
  revision: number;
  model: string;
  configuration: VehicleTransmissionConfiguration;
  changes: VehicleAuthoringChange[];
  warnings: string[];
  review_sha256: string;
  review_only: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export interface VehicleDistributionValues extends Record<string, unknown> {
  model: string;
  listed: boolean;
  name: string;
  manufacturer: string;
  category: string;
  price: number;
  storage: string;
  size_tier: number;
  preview_dictionary: string | null;
  preview_texture: string | null;
  traffic_enabled: boolean;
  traffic_weight: number;
}

export interface VehicleAuthoringDistributionReview extends Record<string, unknown> {
  kind: "vehicle_authoring_distribution_review";
  operation: "review_vehicle_authoring_distribution";
  workspace: string;
  revision: number;
  model: string;
  distribution: VehicleDistributionValues;
  changes: VehicleAuthoringChange[];
  review_sha256: string;
  review_only: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export interface VehiclePackageBuildCheck extends Record<string, unknown> {
  key: string;
  label: string;
  status: "ready";
  detail: string;
}

export interface VehiclePackageBuildReview extends Record<string, unknown> {
  kind: "vehicle_package_build_review";
  operation: "review_vehicle_package_build";
  workspace: string;
  revision: number;
  source: string;
  destination: string;
  pack_name: string;
  mod_id: string;
  name: string;
  version: string;
  editions: string[];
  source_mode: string;
  models: string[];
  ready: true;
  checks: VehiclePackageBuildCheck[];
  warnings: string[];
  review_sha256: string;
  review_only: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export interface VehiclePackageBuildArtifact extends Record<string, unknown> {
  root: string;
  manifest: string;
  payload: string;
  report: string;
  catalog: string;
  content_manifest: string;
  profiles: string | null;
  pack_name: string;
  mod_id: string;
  payload_sha256: string;
  source_mode: string;
}

export interface VehiclePackageBuildResult extends Record<string, unknown> {
  kind: "vehicle_package_build_result";
  operation: "apply_vehicle_package_build";
  review_sha256: string;
  package: VehiclePackageBuildArtifact;
  warnings: string[];
  read_only: false;
  workspace_write_performed: false;
  package_write_performed: true;
  game_write_performed: false;
}

export interface VehicleAxleSkeletonBone extends Record<string, unknown> {
  name: string;
  position: [number, number, number];
}

export interface VehicleAuthoringAxleSkeleton extends Record<string, unknown> {
  kind: "vehicle_authoring_axle_skeleton";
  operation: "inspect_vehicle_authoring_axle_skeleton";
  workspace: string;
  revision: number;
  model: string;
  action: "detect" | "validate" | "steering" | "physical_order" | "canonical_order";
  skeleton_xml: string;
  bone_count: number;
  wheel_bones: VehicleAxleSkeletonBone[];
  bone_position_sha256: string;
  configuration: VehicleAxleConfiguration;
  solution: Record<string, unknown> | null;
  findings: VehicleAxleFinding[];
  warnings: string[];
  review_only: true;
  workspace_write_performed: false;
  package_write_performed: false;
  game_write_performed: false;
}

export interface RecipePlanResult extends Record<string, unknown> {
  kind: "recipe_plan";
  source: string;
  name: string;
  version: string;
  author: string;
  format_version: string;
  editions: string[];
  assembly_sha256: string;
  readiness: string;
  readiness_label: string;
  operation_count: number;
  error_count: number;
  warning_count: number;
  recipe_supported: boolean;
  translatable: boolean;
  managed_exportable: boolean;
  rpf_recipe_compilable: boolean;
  operations: Record<string, unknown>[];
  findings: Record<string, unknown>[];
}

export interface PackageReceiptStatus extends Record<string, unknown> {
  mod_id: string;
  name: string;
  version: string;
  mod_type: string;
  enabled: boolean;
}

export interface PackageOwnershipCheck extends Record<string, unknown> {
  kind: "file" | "rpf_entry";
  destination?: string;
  archive?: string;
  entry?: string;
  exists?: boolean;
  hash_recorded?: boolean;
  hash_matches?: boolean | null;
  backup_present?: boolean | null;
  matches_receipt?: boolean;
}

export interface PackageReceiptResult extends Record<string, unknown> {
  kind: "package_receipt_inventory";
  operation: "inspect_package_receipts";
  gta_path: string;
  edition: string;
  receipt_root: string;
  packages: PackageReceiptStatus[];
  selected_id: string | null;
  receipt: Record<string, unknown> | null;
  verification: (Record<string, unknown> & {
    package_id: string;
    version: string;
    enabled: boolean;
    healthy: boolean;
    ownership_verified: boolean;
    checks: PackageOwnershipCheck[];
    issues: string[];
  }) | null;
  package_count: number;
  enabled_count: number;
  check_count: number;
  issue_count: number;
  read_only: true;
  game_write_performed: false;
}

export interface PackageLifecycleReviewResult extends Record<string, unknown> {
  kind: "package_lifecycle_review";
  operation: "review_package_lifecycle";
  action: "install" | "uninstall" | "enable" | "disable";
  source: string | null;
  gta_path: string;
  ready: boolean;
  package: Record<string, unknown> & {
    id: string;
    name: string;
    version: string;
    type: string;
  };
  target_edition: string;
  replacing?: boolean;
  installed_version?: string | null;
  enabled?: boolean;
  current_enabled?: boolean;
  target_enabled?: boolean;
  operations: Record<string, unknown>[];
  findings: { severity: string; code: string; message: string }[];
  ownership?: Record<string, unknown>;
  rollback: Record<string, unknown>;
  review_sha256: string;
  review_only: true;
  game_write_required: true;
  game_write_performed: false;
}

export interface PackageLifecycleExecutionResult extends Record<string, unknown> {
  kind: "package_lifecycle_execution";
  operation: "apply_package_lifecycle";
  action: "install" | "uninstall" | "enable" | "disable";
  status: "installed" | "uninstalled" | "enabled" | "disabled";
  source: string | null;
  gta_path: string;
  package: Record<string, unknown> & {
    id: string;
    name: string;
    version: string;
    type: string;
  };
  review_sha256: string;
  process_check: {
    gta_closed: true;
    running_processes: string[];
  };
  postcondition: Record<string, unknown>;
  rollback: Record<string, unknown>;
  game_write_confirmed: true;
  game_write_performed: true;
}

export interface VehicleQuickImportResult extends Record<string, unknown> {
  kind: "vehicle_quick_import_inspection";
  operation: "inspect_vehicle_quick_import";
  source: string;
  source_kind: string;
  available_editions: string[];
  suggested_edition: string;
  edition_basis: string;
  vehicles: Record<string, unknown>[];
  errors: number;
  warnings: number;
  branch_count: number;
  vehicle_count: number;
  game_write_performed: false;
  package_write_performed: false;
}

export interface VehicleQuickImportCatalogEntry extends Record<string, unknown> {
  model: string;
  name: string;
  manufacturer: string;
  category: string;
  price: number;
  storage: string;
  source_pack: string;
  size_tier: number;
  preview_dictionary?: string;
  preview_texture?: string;
  traffic: { enabled: boolean; weight: number };
}

export interface VehicleQuickImportPlan extends Record<string, unknown> {
  source: string;
  source_kind: string;
  source_package_sha256: string | null;
  edition: string;
  source_member: string;
  source_member_size: number;
  source_member_sha256: string;
  package_id: string;
  name: string;
  version: string;
  dlc_pack: string;
  destination: string;
  catalog: {
    schema_version: number;
    id: string;
    name: string;
    vehicles: VehicleQuickImportCatalogEntry[];
  };
}

export interface VehicleQuickImportReviewResult extends Record<string, unknown> {
  kind: "vehicle_quick_import_review";
  operation: "review_vehicle_quick_import";
  plan: VehicleQuickImportPlan;
  warnings: string[];
  acknowledged_free_models: string[];
  destination_preview: string;
  destination_review: {
    state: "new" | "managed_replacement" | "blocked";
    exists: boolean;
    replaceable: boolean;
    message: string;
  };
  review_sha256: string;
  vehicle_count: number;
  warning_count: number;
  review_only: true;
  game_write_performed: false;
  package_write_performed: false;
}

export interface VehicleQuickImportPreparedResult extends Record<string, unknown> {
  kind: "vehicle_quick_import_prepared";
  operation: "prepare_vehicle_quick_import";
  review_sha256: string;
  game_write_performed: false;
  package_write_performed: true;
  launcher_install_required: true;
  launcher_library: boolean;
  replaced_existing: boolean;
  package: Record<string, unknown> & { package_root: string };
  published: Record<string, unknown> | null;
  warnings: string[];
}
