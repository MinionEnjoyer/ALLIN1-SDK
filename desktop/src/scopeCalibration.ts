// Read-only CodeWalker skeleton measurements. Never infer an optical centre
// from mesh bounds, or substitute a similarly named bone when one is missing.
export type Matrix = number[][];
export interface ScopeRig { name: string; bones: Record<string, Matrix[]> }
export const maxScopeXmlBytes = 16 * 1024 * 1024;
const identity = (): Matrix => [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
const multiply = (a: Matrix, b: Matrix): Matrix => a.map(row => b[0].map((_, column) =>
  row.reduce((sum, value, index) => sum + value * b[index][column], 0)));
const close = (a: Matrix, b: Matrix, tolerance = .0001): boolean =>
  a.every((row, i) => row.every((value, j) => Math.abs(value - b[i][j]) <= tolerance));

function child(node: Element, name: string): Element {
  const matches = [...node.children].filter(value => value.tagName === name);
  if (matches.length !== 1) throw new Error(`Expected one ${name} node.`);
  return matches[0];
}
function scalar(node: Element, attribute: string): number {
  const raw = node.getAttribute(attribute);
  if (!raw?.trim() || !Number.isFinite(Number(raw))) throw new Error(`Missing or invalid ${attribute}.`);
  return Number(raw);
}
export function readScopeRig(xml: string): ScopeRig {
  if (xml.length > maxScopeXmlBytes || /<!DOCTYPE|<!ENTITY/i.test(xml)) throw new Error("Use bounded CodeWalker XML without DTDs or entities.");
  const document = new DOMParser().parseFromString(xml, "application/xml");
  if (document.querySelector("parsererror")) throw new Error("Invalid model XML.");
  const drawable = document.documentElement;
  if (drawable.tagName !== "Drawable") throw new Error("Choose an exported weapon YDR Drawable XML, not metadata or an archive.");
  const entries = [...child(child(drawable, "Skeleton"), "Bones").children];
  if (!entries.length || entries.length > 512) throw new Error("Skeleton must contain 1–512 bones.");
  const nodes = new Map<number, { name: string; parent: number; local: Matrix }>();
  for (const bone of entries) {
    const index = scalar(child(bone, "Index"), "value"), parent = scalar(child(bone, "ParentIndex"), "value");
    if (!Number.isInteger(index) || index < 0 || !Number.isInteger(parent) || parent < -1 || nodes.has(index)) throw new Error("Invalid or duplicate bone index.");
    const t = child(bone, "Translation"), s = child(bone, "Scale"), r = child(bone, "Rotation");
    const [tx, ty, tz] = ["x", "y", "z"].map(axis => scalar(t, axis));
    const [sx, sy, sz] = ["x", "y", "z"].map(axis => scalar(s, axis));
    if ([tx, ty, tz].some(v => Math.abs(v) > 10) || [sx, sy, sz].some(v => Math.abs(v - 1) > .0001)) throw new Error("Use metre-scale weapon bones without scaling or mirroring.");
    const q = ["x", "y", "z", "w"].map(axis => scalar(r, axis));
    const length = Math.hypot(...q);
    if (Math.abs(length - 1) > .001) throw new Error("Bone rotation is not a unit quaternion.");
    const [x, y, z, w] = q.map(value => value / length);
    const local = [
      [(1 - 2 * (y*y + z*z)) * sx, 2 * (x*y - z*w) * sy, 2 * (x*z + y*w) * sz, tx],
      [2 * (x*y + z*w) * sx, (1 - 2 * (x*x + z*z)) * sy, 2 * (y*z - x*w) * sz, ty],
      [2 * (x*z - y*w) * sx, 2 * (y*z + x*w) * sy, (1 - 2 * (x*x + y*y)) * sz, tz],
      [0, 0, 0, 1],
    ];
    const name = child(bone, "Name").textContent?.trim();
    if (!name || name.length > 128) throw new Error("Invalid bone name.");
    nodes.set(index, { name, parent, local });
  }
  const cache = new Map<number, Matrix>(), visiting = new Set<number>();
  function world(index: number): Matrix {
    if (index === -1) return identity();
    if (cache.has(index)) return cache.get(index)!;
    if (visiting.has(index) || visiting.size >= 128) throw new Error("Cyclic or excessively deep skeleton.");
    const node = nodes.get(index);
    if (!node) throw new Error("Missing parent bone.");
    visiting.add(index);
    const result = multiply(world(node.parent), node.local);
    visiting.delete(index); cache.set(index, result);
    return result;
  }
  const bones: Record<string, Matrix[]> = Object.create(null);
  for (const [index, node] of nodes) (bones[node.name] ??= []).push(world(index));
  return { name: child(drawable, "Name").textContent?.trim() || "Unnamed model", bones };
}
function uniqueBone(rig: ScopeRig, name: string): Matrix {
  const matches = rig.bones[name];
  if (!matches?.length) throw new Error(`${rig.name}: missing ${name}. No substitute was assumed.`);
  if (!matches.every(value => close(value, matches[0], .000001))) throw new Error(`${rig.name}: ${name} has ambiguous non-coincident placements.`);
  return matches[0]; // Coincident zero-transform parent/child aliases are safe.
}
export function scopeHeightProposal(reference: ScopeRig, custom: ScopeRig, referenceBone: string, customBone: string, referenceOffsetZ: number) {
  if (!Number.isFinite(referenceOffsetZ) || Math.abs(referenceOffsetZ) > 10) throw new Error("Enter the aligned reference's attached-scope Z offset in metres.");
  if (!close(uniqueBone(reference, "Gun_GripR"), uniqueBone(custom, "Gun_GripR"))) throw new Error("Grip frames differ. Register the models to the same aiming pose before calibration.");
  const from = uniqueBone(reference, referenceBone), to = uniqueBone(custom, customBone);
  if (!from.slice(0, 3).every((row, i) => row.slice(0, 3).every((value, j) => Math.abs(value - to[i][j]) < .0001))) throw new Error("Scope mount rotations differ; height-only calibration is not sufficient.");
  const referenceZ = from[2][3], customZ = to[2][3];
  // Lowering the optical anchor requires raising the weapon, not lowering it
  // a second time. Always use the fixed reference, never the current draft.
  const proposedZ = referenceOffsetZ + referenceZ - customZ;
  if (!Number.isFinite(proposedZ) || Math.abs(proposedZ) > 10) throw new Error("Calculated height exceeds the authoring limit.");
  return { referenceZ, customZ, correction: referenceZ - customZ, proposedZ };
}
