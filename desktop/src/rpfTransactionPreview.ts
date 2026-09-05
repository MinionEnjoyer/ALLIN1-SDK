import type { RpfTransactionSession, RpfTransactionReview } from "./RpfTransactionWorkspace";

export function rpfTransactionPreviewSession(receipt = false): RpfTransactionSession {
  return { kind: "rpf_transaction_session", source_kind: receipt ? "receipt" : "plan",
    source: receipt ? "C:\\SDK\\transactions\\preview-transaction\\receipt.json" : "C:\\SDK\\exports\\rpf-plan.json",
    state_sha256: (receipt ? "d" : "a").repeat(64), archive: "C:\\SDK\\archives\\update.rpf", archive_sha256: (receipt ? "c" : "b").repeat(64),
    edition: "enhanced", plan_id: "e".repeat(64), status: receipt ? "applied" : "ready", target_scope: "workspace_copy", authorized_root: "C:\\SDK\\archives",
    gta_path: "C:\\Games\\Grand Theft Auto V Enhanced", transaction_id: receipt ? "preview-transaction" : null, archive_lock:null,
    changes: [{ action: "replace", archive_path: "x64/data.rpf", entry: "text/global.gxt2", original: {exists:true, size:512, sha256:"f".repeat(64)}, payload: {path:"C:\\SDK\\exports\\global.gxt2",size:640,sha256:"1".repeat(64)} },
      { action: "mkdir", archive_path: "", entry: "common/allin1", original: {exists:false,size:0,sha256:null}, payload: null }],
    backup: receipt ? {path:"C:\\SDK\\transactions\\preview-transaction\\archive.rpf.backup",size:1489288192,sha256:"b".repeat(64)} : null,
    verification: receipt ? {healthy:true,archive_state:"applied",archive_sha256:"c".repeat(64),backup_valid:true,entry_valid:true} : null,
    read_only:true,archive_write_performed:false,game_write_performed:false };
}
export function rpfTransactionPreviewReview(request: Record<string, unknown>, session = rpfTransactionPreviewSession(request.action !== "execute")): RpfTransactionReview {
  const clearing = request.action === "clear_lock";
  return { kind:"rpf_transaction_review",action:request.action as RpfTransactionReview["action"],request:structuredClone(request),session:structuredClone(session),
    receipt_root:session.source_kind === "receipt" ? session.source.replace(/\\receipt\.json$/, "") : "C:\\SDK\\transactions",authorized_root:session.target_scope === "mods_copy" ? null : String(request.authorized_root),restore_sha256:session.backup?.sha256 ?? null,
    review_sha256:"2".repeat(64),review_only:true,archive_write_required:request.action !== "recover" && !clearing,game_write_performed:false,
    lock_write_required:clearing,lock_evidence:clearing ? {path:session.source.replace(/receipt\.json$/, `cleared-lock-${session.archive_lock?.sha256}.json`),sha256:session.archive_lock!.sha256,existing_sha256:null} : null,
    game_write_required:session.target_scope === "mods_copy" && request.action !== "recover",recovery_status:request.action === "recover" ? session.verification?.archive_state === "applied" ? "applied" : "interrupted_before_commit" : null,
    warning:clearing ? "Retain the reviewed lock evidence, then remove only this stale transaction lock. The archive, receipt and backup stay unchanged. GTA must be closed."
      : request.action === "recover" ? "Update only this receipt to match the verified archive. No archive is rewritten. Stale locks are retained."
      : `This replaces the selected ${session.target_scope === "mods_copy" ? "GTA mods" : "authoring"} RPF in place. Keep the receipt and full-archive backup for rollback. GTA must be closed.` };
}
