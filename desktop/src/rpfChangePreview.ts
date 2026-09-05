import type { RpfChange, RpfChangeReview, RpfChangeSession } from "./RpfChangeSetWorkspace";

export function rpfChangePreviewSession(path="C:\\SDK\\workspaces\\archive-changes.json"): RpfChangeSession {
  return {kind:"rpf_change_set_session",change_set:path,state_sha256:"a".repeat(64),
    archive:{path:"C:\\Games\\Grand Theft Auto V Enhanced\\mods\\update\\update.rpf",size:1489288192,edition:"enhanced",sha256:"b".repeat(64)},
    actions:[{id:"change-text",action:"replace",archive_path:"x64/data.rpf",entry:"text/global.gxt2",payload:{path:"C:\\SDK\\exports\\global.gxt2",size:512,sha256:"c".repeat(64)}},
      {id:"change-folder",action:"mkdir",archive_path:"",entry:"common/allin1"}],
    action_limit:128,files_verified:false,read_only:true,game_write_performed:false};
}
export function rpfChangePreviewReview(request:Record<string,unknown>, session=rpfChangePreviewSession(String(request.change_set ?? "C:\\SDK\\workspaces\\archive-changes.json"))): RpfChangeReview {
  const action=String(request.action), before=action==="create"?[]:session.actions, after=structuredClone(before);
  if(action==="stage") {
    const change=request.change as Record<string,unknown>;
    after.push({id:"change-new",action:String(change.action),entry:String(change.entry),archive_path:String(change.archive_path ?? ""),
      ...(change.new_entry?{new_entry:String(change.new_entry)}:{}),...(change.payload?{payload:{path:String(change.payload),size:640,sha256:"d".repeat(64)}}:{})});
  }
  if(action==="remove" || action==="move") {
    const index=after.findIndex(row=>row.id===request.action_id), [row]=after.splice(index,1);
    if(action==="move") after.splice(Number(request.position)-1,0,row);
  }
  return {kind:"rpf_change_set_review",action,request:structuredClone(request),review_sha256:"e".repeat(64),
    change_set:action==="create"?null:session.change_set,state_sha256:action==="create"?null:session.state_sha256,
    archive:action==="create"?{...session.archive,path:String(request.archive)}:session.archive,
    gta_path:request.gta_path?String(request.gta_path):null,authorized_root:request.authorized_root?String(request.authorized_root):null,
    destination:request.destination?String(request.destination):null,before,after,
    plan:action==="compile"?{status:"ready",plan_id:"preview-plan",target_scope:request.authorized_root?"workspace_copy":"mods_copy",changes:after.map((row:RpfChange)=>({...row})),blocking_reasons:[],warnings:[]}:null,
    review_only:true,game_write_performed:false,archive_write_performed:false};
}
