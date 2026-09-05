// Synthetic browser/test data only. No native or runtime acceptance is asserted.
import type { PedReview, PedSnapshot } from "./PedWorkbench";

export function pedPreviewSnapshot(workspace: string | null = null, selection?: string): PedSnapshot {
  const peds = ["ig_demo", "ig_neighbor"].map(name => ({ name, ped_type: "PERSON", model_type: "human", props_name: `${name}_p`,
    clip_dictionary: "move_m@generic", expression_set: "expr_set_ambient_male", movement_clip_set: "move_m@casual@d",
    creature_metadata: "METADATA_HUMAN_MALE", source: "common/data/peds.meta" }));
  const ped = peds.find(p => p.name === selection) ?? peds[0];
  const values = { "ped.pedType": ped.ped_type, "ped.modelType": ped.model_type, "ped.propsName": ped.props_name,
    "ped.clipDictionary": ped.clip_dictionary, "ped.expressionSet": ped.expression_set,
    "ped.movementClipSet": ped.movement_clip_set, "ped.creatureMetadata": ped.creature_metadata };
  return { kind: "ped_workbench", source: workspace ? `${workspace}/content` : "C:/SDK/peds/synthetic-demo", workspace,
    revision: workspace ? 0 : null, state_sha256: workspace ? "a".repeat(64) : null, selected_ped: ped, selected_index: peds.indexOf(ped), selection_unique: true,
    project: { peds, edition: "Unresolved", source_kind: "folder", findings: [{ code: "synthetic_preview", severity: "info", path: "", message: "Synthetic UI fixture. No game or native asset validation has run." }] },
    values, editable_fields: workspace ? Object.keys(values) : [], can_create: !workspace, can_undo: false, decoder_edition: "Unresolved",
    assets: [".ydd", ".ytd"].map(suffix => ({ path: `stream/${ped.name}${suffix}`, size: 4096, role: suffix === ".ydd" ? "Drawable" : "Texture dictionary", link: "exact identity", suffix, stem: ped.name })),
    readiness: [{ system: "Drawable", status: "Present (synthetic fixture)", evidence: [`stream/${ped.name}.ydd`] },
      { system: "Textures", status: "Present (synthetic fixture)", evidence: [`stream/${ped.name}.ytd`] },
      { system: "Movement", status: "Declared", evidence: [ped.movement_clip_set] }, { system: "Expressions", status: "Declared", evidence: [ped.expression_set] }] };
}
export function pedPreviewReview(payload: Record<string, unknown>): PedReview {
  const result: PedReview = { kind: "ped_authoring_review", action: String(payload.action), review_sha256: "b".repeat(64) };
  if (payload.action === "create") return { ...result, source: String(payload.source), destination: `${payload.parent}/${payload.name}`, ped_count: 2, copy_bytes: 8192 };
  const snapshot = pedPreviewSnapshot(String(payload.workspace), String(payload.ped));
  if (payload.action === "edit") result.changes = Object.entries(payload.updates as Record<string, string>).map(([field, after]) => ({ field, before: snapshot.values?.[field] ?? "", after }));
  if (payload.action === "migrate") {
    result.changes = [{ field: "ped.Name", before: snapshot.selected_ped!.name, after: String(payload.new_name) }];
    result.renames = snapshot.assets.map(a => ({ before: a.path, after: `stream/${payload.new_name}${a.suffix}` }));
  }
  if (payload.action === "clone") result.clone_plan = {
    ready: false, plan_sha256: "c".repeat(64), revision: 0,
    spec: { donor_ped: snapshot.selected_ped!.name, ped_name: String(payload.new_name), updates: { "ped.propsName": String(payload.new_props || `${payload.new_name}_p`) } },
    selected_sources: { ped_metadata: "common/data/peds.meta" }, source_sha256: { "common/data/peds.meta": "d".repeat(64) },
    additions: [{ kind: "ped", name: String(payload.new_name), source: "common/data/peds.meta", detail: "Complete metadata clone" }],
    findings: [{ severity: "error", code: "synthetic_preview_only", message: "Browser preview cannot validate target native assets. Open the SDK to review a real package." }],
  };
  return result;
}
