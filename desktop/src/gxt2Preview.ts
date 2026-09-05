import type { Gxt2Session, Gxt2Review, Gxt2RpfPublication, RpfPackageMetadata } from "./Gxt2Workspace";
export function rpfPublicationPreview(payload: Record<string, unknown>): Gxt2RpfPublication {
  const metadata = { ...(payload.package_metadata as RpfPackageMetadata), target: String((payload.package_metadata as RpfPackageMetadata).target).replaceAll("\\", "/") };
  const member = payload.publication_mode === "member", schema = member ? (payload.root_member ? 3 : 4) : 1;
  const entry = payload.root_member ? "global.gxt2" : "x64/american.rpf!global.gxt2";
  const payloadPath = member ? "payload/replacement.gxt2" : "payload/text-fixture.rpf", payloadHash = (member ? "d" : "9").repeat(64);
  return { source_package: String(payload.source_package), metadata, edition: "enhanced", archive_sha256: "9".repeat(64),
    publication_mode: member ? "member" : "whole_archive", manifest_schema_version: schema,
    entry: member ? entry : null, original_sha256: member ? "b".repeat(64) : null, payload_sha256: payloadHash,
    members: [ { path: "README.txt", size: 600, sha256: "1".repeat(64) }, { path: "allin1.rpf-build.json", size: 900, sha256: "2".repeat(64) },
      { path: "mod.toml", size: 500, sha256: "3".repeat(64) }, { path: payloadPath, size: member ? 190 : 524288, sha256: payloadHash } ],
    total_bytes: member ? 2190 : 526288, required_free_bytes: 70 * 1024**2, whole_archive_replacement: !member,
    install_performed: false, dlc_registration_performed: false, upload_performed: false,
    manifest_text: `schema_version = ${schema}\nid = "${metadata.id}"\nname = "${metadata.name}"\nversion = "${metadata.version}"\nauthor = "${metadata.author}"\ntype = "rpf"\neditions = ["enhanced"]\ndependencies = ["openrpf"]\ndlc_packs = []\n\n${member ? '[[rpf_entries]]' : '[[files]]'}\nsource = "${payloadPath}"\n${member ? "archive" : "destination"} = "${metadata.target}"\nsha256 = "${payloadHash}"\n${member ? `entry = "${entry}"\noriginal_sha256 = "${"b".repeat(64)}"\n` : ""}` };
}
export function gxt2PreviewSession(payload: Record<string, unknown>): Gxt2Session {
  const rows = [
    { hash: 256, hash_hex: "0x00000100", text: "KRISS Vector" },
    { hash: 512, hash_hex: "0x00000200", text: "A compact .45-caliber submachine gun." },
    { hash: 768, hash_hex: "0x00000300", text: "Suppressor — tuned for controlled fire" },
    { hash: 1024, hash_hex: "0x00000400", text: "Français · 日本語 · Español" },
  ];
  const query = String(payload.query ?? "");
  const matches = rows.filter(e => `${e.hash_hex} ${e.text} ${e.hash}`.toLowerCase().includes(query.toLowerCase()));
  const selected = payload.selected_hash === undefined ? matches[0] : rows.find(e => e.hash === Number(payload.selected_hash));
  return { kind: "gxt2_session", workspace: payload.workspace ? String(payload.workspace) : null,
    source: String(payload.workspace ?? payload.archive ?? payload.source), name: "global.gxt2", state_sha256: "a".repeat(64), original_sha256: "b".repeat(64),
    source_binding: payload.archive ? { outer_archive: String(payload.archive), entry_id: String(payload.entry_id),
      outer_archive_sha256: "e".repeat(64), edition: "Enhanced", gta_path: String(payload.gta_path) } : payload.archive_workspace ? {
        outer_archive: "C:\\SDK\\archives\\text-fixture.rpf", outer_archive_sha256: "e".repeat(64),
        edition: "Enhanced", gta_path: "C:\\Games\\Enhanced", entry_id: payload.root_member ? "::global.gxt2" : "x64/american.rpf::global.gxt2",
      } : null,
    revision: payload.workspace ? 1 : 0, can_undo: !!payload.workspace, entry_count: rows.length, match_count: matches.length,
    offset: Number(payload.offset ?? 0), page_size: 100, query, read_only: true, game_write_performed: false,
    entries: matches.map(e => ({ hash: e.hash, hash_hex: e.hash_hex, preview: e.text })),
    selected: selected ? { ...selected, editable: true, text_length: selected.text.length } : null,
    history: payload.workspace ? [{ sequence: 1, action: "set_text", created_utc: "2026-09-04T12:00:00Z" }] : [] };
}
export function gxt2PreviewReview(payload: Record<string, unknown>): Gxt2Review {
  const session = gxt2PreviewSession({ ...payload, selected_hash: payload.label_hash });
  return { kind: "gxt2_review", action: String(payload.action), source: session.source, destination: payload.destination ? String(payload.destination) : null,
    source_binding: session.source_binding,
    revision: session.revision, state_sha256: session.state_sha256, review_sha256: "c".repeat(64), label_hash: payload.label_hash === undefined ? null : Number(payload.label_hash),
    before: ["edit", "remove"].includes(String(payload.action)) ? session.selected?.text ?? "" : null,
    after: ["edit", "add"].includes(String(payload.action)) ? String(payload.text ?? "") : null,
    entry_count: session.entry_count, output_sha256: payload.action === "build" ? "d".repeat(64) : null,
    ...(payload.action === "publish_rpf" ? { rpf_publication: rpfPublicationPreview(payload) } : {}),
    ...(payload.action === "package_rpf" && session.source_binding ? { rpf_package: {
      archive_name: "text-fixture.rpf", archive_size: 524288, entry_id: session.source_binding.entry_id,
      entry_size_before: 160, entry_size_after: 190, payload_sha256: "d".repeat(64), original_sha256: session.original_sha256,
      archive_sha256: session.source_binding.outer_archive_sha256, edition: "Enhanced", index_sha256: "f".repeat(64),
      indexed_entries: 8, verified_payloads: 4, required_free_bytes: 70 * 1024**2,
      game_must_be_closed: true, source_unchanged_required: true, new_output_only: true,
      outputs: ["archive/text-fixture.rpf", "payload/replacement.gxt2", "payload/replacement.gxt2.gxt2-validation.json", "rpf-package.json"],
    } } : {}),
    review_only: true, game_write_performed: false };
}
