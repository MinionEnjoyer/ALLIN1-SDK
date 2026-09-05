import type { WeaponSnapshot } from "./WeaponWorkbench";
import type { WeaponClonePlan, WeaponCloneSpec } from "./WeaponClone";

export function weaponPreviewSnapshot(
  workspace: string | null = null, weapon = "WEAPON_DEMO",
  editorKind: WeaponSnapshot["editor_kind"] = "weapon", component = "COMPONENT_DEMO_CLIP",
  metadataSource = "weapon_shop.meta",
): WeaponSnapshot {
  const values = { "weapon.slot": "SLOT_PISTOL", "weapon.ammoInfo": "AMMO_DEMO", "weapon.model": "w_pi_demo",
    "weapon.humanNameHash": "WT_DEMO", "weapon.statName": "WT_DEMO", "ammo.ammoMax": "240", "ammo.ammoMax50": "120",
    "weapon.roundsPerMinute": "600", "weapon.timeBetweenShots": "0.100000",
    "weapon.firstPersonScopeOffset.x": "0.00000", "weapon.firstPersonScopeOffset.y": "0.00000", "weapon.firstPersonScopeOffset.z": "-0.014",
    "weapon.firstPersonScopeFov": "30.00000", "weapon.weaponFlags": "CarriedInHand Gun UseFPSAimIK" };
  const cameraFields = ["x", "y", "z"].map(axis => ({ key: `weapon.firstPersonScopeOffset.${axis}`, tag: "FirstPersonScopeOffset",
    attribute: axis, label: `Scope position ${axis.toUpperCase()}`, group: "Scope position", unit: "metres", minimum: -10, maximum: 10, step: "0.00001" }));
  cameraFields.push({ key: "weapon.firstPersonScopeFov", tag: "FirstPersonScopeFov", attribute: "value", label: "Scope FOV",
    group: "Field of view", unit: "degrees", minimum: 1, maximum: 179, step: "0.01" });
  const components = ["CLIP", "SCOPE", "SUPP"].map(suffix => ({ name: `COMPONENT_DEMO_${suffix}`,
    model: `w_pi_demo_${suffix.toLowerCase()}`, component_type: "CWeaponComponentInfo", source: "weaponcomponents.meta" }));
  const attachments = [
    { weapon_name: "WEAPON_DEMO", component_name: "COMPONENT_DEMO_CLIP", attach_bone: "WAPClip", default: true },
    { weapon_name: "WEAPON_DEMO_ALT", component_name: "COMPONENT_DEMO_CLIP", attach_bone: "WAPClip", default: true },
    { weapon_name: "WEAPON_DEMO", component_name: "COMPONENT_DEMO_SCOPE", attach_bone: "WAPClip", default: false },
    { weapon_name: "WEAPON_DEMO", component_name: "COMPONENT_DEMO_SUPP", attach_bone: "WAPSupp", default: false },
  ];
  const componentFields = { "component.model": components.find(c => c.name === component)?.model ?? "",
    "component.locName": "WCT_CLIP1", "component.locDesc": "WCD_CLIP1", "component.attachBone": "WAPClip",
    "component.type": "CWeaponComponentInfo" };
  const link = attachments.find(a => a.weapon_name === weapon && a.component_name === component)!;
  const shopFields = { "shop.cost": "7500", "shop.ammoCost": "2", "shop.textLabel": "WT_DEMO",
    "shop.weaponDesc": "WTD_DEMO", "shop.weaponTT": "WTT_DEMO", "shop.weaponUppercase": "WTU_DEMO", "shop.availableInSP": "true" };
  return {
    kind: "weapon_workbench", source: "C:\\SDK\\weapons\\demo", workspace, revision: workspace ? 0 : null,
    selected_weapon: weapon, editable_fields: workspace ? Object.keys(values).filter(key => key !== "weapon.timeBetweenShots") : [], can_undo: false,
    camera_fields: cameraFields,
    native_preview: {
      selected_part: ["component", "attachment"].includes(editorKind) ? `component:${component}` : `weapon:${weapon}`,
      parts: [{ id: `weapon:${weapon}`, kind: "weapon", name: weapon, model: "w_pi_demo", reason: "",
        assets: [{ path: "stream/w_pi_demo.ydr", texture_entry: "stream/w_pi_demo.ytd", texture_entries: ["stream/w_pi_demo.ytd"] }] },
        ...components.filter(item => attachments.some(link => link.weapon_name === weapon && link.component_name === item.name) || item.name === component).map(item => ({
          id: `component:${item.name}`, kind: "component", name: item.name, model: item.model,
          reason: "Referenced model is not bundled in this package.", assets: [],
          attach_bones: attachments.filter(link => link.weapon_name === weapon && link.component_name === item.name).map(link => link.attach_bone),
          default: item.name === "COMPONENT_DEMO_CLIP",
        }))],
      texture_entries: ["stream/w_pi_demo.ytd"], warnings: [],
    },
    editor_kind: editorKind,
    shop_sources: ["weapon_shop.meta"],
    shop_values: editorKind === "shop" ? { weapon, source: metadataSource, values: shopFields,
      affected_weapons: [weapon], identity_field: "nameHash", identity_representation: "text",
      representations: Object.fromEntries(Object.keys(shopFields).map(key => [key, "value"])) } : null,
    relationship_editable_fields: !workspace ? [] : editorKind === "component"
      ? Object.keys(componentFields).filter(key => key !== "component.type") : editorKind === "shop" ? Object.keys(shopFields) : ["attachment.default"],
    component_values: editorKind === "component" ? { component, values: componentFields, source: "weaponcomponents.meta",
      affected_weapons: attachments.filter(a => a.component_name === component).map(a => a.weapon_name) } : null,
    attachment_values: editorKind === "attachment" ? { weapon, component, source: "weapons.meta",
      values: { "attachment.attachBone": link.attach_bone, "attachment.default": String(link.default) },
      affected_weapons: [weapon], other_defaults: attachments.filter(a => a.weapon_name === weapon && a.attach_bone === link.attach_bone
        && a.component_name !== component && a.default).map(a => a.component_name) } : null,
    project: { edition: "unknown", summary: { weapons: 2, ammo: 1, components: 3, attachments: 4, errors: 0, warnings: 0 },
      weapons: ["WEAPON_DEMO", "WEAPON_DEMO_ALT"].map(name => ({ name, model: "w_pi_demo", ammo_info: "AMMO_DEMO", source: "weapons.meta" })),
      components, attachments, findings: [],
      animation_records: ["DEFAULT", "FIRST_PERSON"].map((set_name, set_ordinal) => ({ weapon_name: "WEAPON_DEMO",
        source: "weaponanimations.meta", set_name, set_ordinal, ordinal: 0 })) },
    values: { weapon, values, sources: { weapon: "weapons.meta", ammo: "ammo.meta" }, affected_weapons: ["WEAPON_DEMO", "WEAPON_DEMO_ALT"] },
    assets: [{ path: "stream/w_pi_demo.ydr", size: 4096 }, { path: "stream/w_pi_demo.ytd", size: 8192 }],
  };
}

export function weaponPreviewReview(payload: Record<string, unknown>) {
  if (payload.action === "clone_animation") return {
    kind: "weapon_authoring_review", action: "clone_animation", review_sha256: "c".repeat(64),
    weapon: payload.weapon, template_weapon: payload.template_weapon, source: payload.metadata_source,
    affected_weapons: [payload.weapon],
    changes: ["DEFAULT", "FIRST_PERSON"].map(set => ({ field: "animation.mapping", before: payload.template_weapon,
      after: payload.weapon, source: payload.metadata_source, set })),
  };
  if (payload.action === "clone") return {
    kind: "weapon_authoring_review", action: "clone", review_sha256: "c".repeat(64),
    clone_plan: weaponPreviewClonePlan(payload.spec as WeaponCloneSpec),
  };
  const snapshot = weaponPreviewSnapshot(null, String(payload.weapon ?? "WEAPON_DEMO"),
    payload.action === "edit_component" ? "component" : payload.action === "edit_attachment" ? "attachment" : payload.action === "edit_shop" ? "shop" : "weapon",
    String(payload.component ?? "COMPONENT_DEMO_CLIP"));
  const data = snapshot.component_values ?? snapshot.attachment_values ?? snapshot.shop_values ?? snapshot.values!;
  const values = data.values;
  return { kind: "weapon_authoring_review", action: payload.action, review_sha256: "c".repeat(64),
    component: payload.component, weapon: payload.weapon, source: payload.metadata_source,
    destination: payload.action === "create" ? `${payload.parent}\\${payload.name}` : undefined,
    weapon_count: 2, affected_weapons: data.affected_weapons,
    changes: Object.entries((payload.updates || {}) as Record<string, string>).map(([field, after]) => ({ field, before: values[field], after })),
  };
}

// Read-only browser fixture, never a substitute for the Python clone validator.
export function weaponPreviewClonePlan(spec: WeaponCloneSpec): WeaponClonePlan {
  return { ready: true, donor_complete: true, revision: 0, plan_sha256: "d".repeat(64), spec,
    donor_completeness: { weapon_record: true, ammo_record: true, animation_mappings: 2,
      animation_sets: ["DEFAULT", "FIRST_PERSON"], shop_record: true, attachment_links: 1,
      component_definitions: 1, authorable_source: true },
    selected_sources: { weapon: "weapons.meta", ammo: "ammo.meta", animation: "weaponanimations.meta",
      shop: "weapon_shop.meta", model_asset: `stream/${spec.model}.ydr` },
    reused_components: ["COMPONENT_DEMO_CLIP"], collisions: [], findings: [],
    additions: [
      { kind: "weapon", name: spec.weapon_name, source: "weapons.meta", detail: `clone of ${spec.donor_weapon}` },
      ...(spec.clone_ammo ? [{ kind: "ammo", name: spec.ammo_info, source: "ammo.meta", detail: "clone of donor ammo" }] : []),
      { kind: "attachment_link", name: `${spec.weapon_name}/COMPONENT_DEMO_CLIP/WAPClip`, source: "weapons.meta", detail: "reuses component definition" },
      ...["DEFAULT", "FIRST_PERSON"].map(detail => ({ kind: "animation_mapping", name: spec.weapon_name, source: "weaponanimations.meta", detail })),
      { kind: "shop", name: spec.weapon_name, source: "weapon_shop.meta", detail: `clone of ${spec.donor_weapon}` },
    ],
  };
}
