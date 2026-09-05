import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import App from "./App";
import RpfTransactionWorkspace from "./RpfTransactionWorkspace";
import { rpfTransactionPreviewSession, rpfTransactionPreviewReview } from "./rpfTransactionPreview";
import { createPreviewClient } from "./previewClient";
import type { Envelope } from "./types";

const response = (result: unknown): Envelope => ({ protocol_version:"1.0.0",request_id:"test",job_id:"tx",operation:"result",sequence:1,risk:"read_only",terminal:true,payload:{result} });
function setup(app = false, live = false, interrupted = false, stale = false) {
  const fixture = (receipt = false) => {
    const value = rpfTransactionPreviewSession(receipt);
    if(live) {value.target_scope="mods_copy";value.authorized_root=null;value.archive=`${value.gta_path}\\mods\\update\\update.rpf`;}
    if(receipt && interrupted) value.status="verified_staging";
    if(receipt && stale) value.archive_lock = {path:value.archive.replace(/update\.rpf$/, ".update.rpf.allin1.lock"),pid:99999999,process_running:false,sha256:"9".repeat(64),plan_id:value.plan_id,created_at:"2026-09-04T00:00:00Z",identity:"1:12345",cleanup_supported:true};
    return value;
  };
  const client = createPreviewClient("transactions"); let current = fixture();
  const picker = client.selectPath.bind(client) as (kind: string) => Promise<string | null>;
  client.selectPath = async kind => kind === "gta_folder" ? String(current.gta_path) : picker(kind);
  const original = client.startJob.bind(client);
  client.startJob = vi.fn(async (operation, payload, revision, event) => {
    if (operation !== "inspect_rpf_transaction" && operation !== "review_rpf_transaction") return original(operation, payload, revision, event);
    if (operation === "inspect_rpf_transaction" && String(payload.source).endsWith("receipt.json") && current.source_kind === "plan") current = fixture(true);
    event(response(operation === "inspect_rpf_transaction" ? current : rpfTransactionPreviewReview(payload, current)));
    return {job_id:"tx",accepted:response({})};
  });
  const apply = vi.spyOn(client, "applyRpfTransaction"), guard = vi.fn(), changed = vi.fn(), user = userEvent.setup();
  const saved = (p: Record<string, unknown>) => {
    const lockEvidence = rpfTransactionPreviewReview(p, current).lock_evidence;
    current = fixture(true); current.status="applied";
    if(p.action === "clear_lock") current.archive_lock = null;
    if (p.action === "rollback") current = {...current,status:"rolled_back",state_sha256:"7".repeat(64),archive_sha256:"b".repeat(64),verification:{...current.verification!,archive_state:"original",archive_sha256:"b".repeat(64)}};
    return response({kind:"rpf_transaction_applied",action:p.action,review_sha256:p.review_sha256,session:current,receipt_write_performed:p.action !== "clear_lock",archive_write_performed:p.action !== "recover" && p.action !== "clear_lock",game_write_performed:live && p.action !== "recover",lock_write_performed:p.action === "clear_lock",lock_evidence:lockEvidence});
  };
  render(app ? <App client={client}/> : <RpfTransactionWorkspace client={client} onGuardChange={guard} onArchiveChanged={changed}/>);
  return {client,apply,saved,guard,changed,user};
}
async function open(user: ReturnType<typeof userEvent.setup>, receipt = false) {
  await user.click(screen.getByRole("button",{name:receipt ? "Open transaction receipt" : "Open compiled plan"}));
  await screen.findByText(receipt ? "Receipt: applied" : "Plan: ready");
  await user.click(screen.getByRole("button",{name:"Authorize archive folder"}));
}
async function review(user: ReturnType<typeof userEvent.setup>, rollback = false) {
  await user.click(screen.getByRole("button",{name:rollback ? "Review rollback" : "Review execution"}));
  return screen.findByRole("region",{name:"RPF transaction confirmation"});
}
async function confirm(user: ReturnType<typeof userEvent.setup>, rollback = false) {
  const region = screen.getByRole("region",{name:"RPF transaction confirmation"});
  await user.click(within(region).getByRole("checkbox"));
  await user.click(within(region).getByRole("button",{name:rollback ? "Restore original archive" : "Apply to authoring archive"}));
}

it("opens read-only, separately confirms execution and restores from the verified receipt", async () => {
  const {user,apply,saved,changed,guard} = setup(); apply.mockImplementation(async p => saved(p));
  await open(user); expect(apply).not.toHaveBeenCalled();
  await review(user); expect(screen.getByRole("heading",{name:"Confirm archive execution"})).toHaveFocus();
  expect(screen.getByRole("button",{name:"Apply to authoring archive"})).toBeDisabled();
  expect(guard).toHaveBeenLastCalledWith(true); await confirm(user);
  await screen.findByText("Receipt: applied"); expect(changed).toHaveBeenCalledWith("C:\\SDK\\archives\\update.rpf");
  expect(screen.getByRole("heading",{name:"Execute & restore"})).toHaveFocus();
  await review(user,true); expect(screen.getByRole("checkbox")).not.toBeChecked(); await confirm(user,true);
  await screen.findByText("Receipt: rolled_back"); expect(screen.getByRole("button",{name:"Review rollback"})).toBeDisabled();
  expect(apply).toHaveBeenCalledTimes(2); expect(apply).toHaveBeenLastCalledWith(expect.objectContaining({action:"rollback",archive_write_confirmed:true}));
});

it("can reopen a receipt and returns focus without authorizing rollback",async () => {
  const {user,apply} = setup(); await open(user,true); await review(user,true);
  await user.click(screen.getByRole("button",{name:"Back to transaction"}));
  expect(screen.getByRole("heading",{name:"Execute & restore"})).toHaveFocus(); expect(apply).not.toHaveBeenCalled();
});

it("retains the selected document but clears confirmation after a stale write failure",async () => {
  const {user,apply} = setup(); await open(user); await review(user);
  apply.mockResolvedValue({...response({}),operation:"error",payload:{message:"Archive changed after review"}});
  await confirm(user); expect(await screen.findByRole("alert")).toHaveTextContent("Archive changed");
  await review(user); expect(screen.getByRole("checkbox")).not.toBeChecked();
});

it.each(["archive","state","scope","risk","changes"])("rejects mismatched %s review evidence",async field => {
  const {user,client,apply} = setup(); await open(user);
  client.startJob = vi.fn(async (_op,payload,_revision,event) => {
    const value = rpfTransactionPreviewReview(payload);
    if(field === "archive") value.session.archive = "C:\\wrong.rpf";
    if(field === "state") value.session.state_sha256 = "0".repeat(64);
    if(field === "scope") value.authorized_root = "C:\\";
    if(field === "risk") value.game_write_performed = true;
    if(field === "changes") value.session.changes[0].entry = "wrong.gxt2";
    event(response(value)); return {job_id:"bad",accepted:response({})};
  });
  await user.click(screen.getByRole("button",{name:"Review execution"})); expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was authorized"); expect(apply).not.toHaveBeenCalled();
});

it("cancels a pending review and ignores its late terminal event",async () => {
  const {user,client} = setup(); await open(user); let deliver: ((e: Envelope) => void) | undefined; let request: Record<string,unknown> = {};
  client.startJob = vi.fn(async (_op,payload,_revision,event) => {deliver=event;request=payload;return {job_id:"late",accepted:response({})};});
  const cancel = vi.spyOn(client,"cancelJob"); await user.click(screen.getByRole("button",{name:"Review execution"}));
  await user.click(await screen.findByRole("button",{name:"Cancel transaction review"}));
  await act(async () => deliver?.(response(rpfTransactionPreviewReview(request))));
  expect(cancel).toHaveBeenCalledWith("late");expect(screen.queryByRole("region",{name:"RPF transaction confirmation"})).not.toBeInTheDocument();
});

it("does not submit twice and rejects unverified execution evidence",async () => {
  const {user,apply,saved} = setup();await open(user);await review(user);
  let resolve: ((e: Envelope) => void) | undefined;let payload:Record<string,unknown>={};
  apply.mockImplementation(p => {payload=p;return new Promise(r => {resolve=r;});});
  await confirm(user);expect(screen.getByRole("button",{name:"Writing and verifying…"})).toBeDisabled();expect(screen.getByRole("button",{name:"Back to transaction"})).toBeDisabled();
  expect(screen.queryByRole("button",{name:"Cancel transaction review"})).not.toBeInTheDocument();
  const result=saved(payload);(result.payload.result as {game_write_performed:boolean}).game_write_performed=true;
  await act(async()=>resolve?.(result));expect(await screen.findByRole("alert")).toHaveTextContent("could not be verified");expect(apply).toHaveBeenCalledTimes(1);
});

it("handles a cancelled picker without opening or authorizing a plan",async () => {
  const {user,client,apply} = setup();vi.spyOn(client,"selectPath").mockResolvedValue(null);
  await user.click(screen.getByRole("button",{name:"Open compiled plan"}));
  expect(screen.getByText("No transaction selected")).toBeVisible();expect(apply).not.toHaveBeenCalled();
});

it("guards all RPF tabs and workspace navigation during a transaction review",async () => {
  const {user} = setup(true);await user.click(await screen.findByRole("button",{name:/^RPF Archives/}));await user.click(screen.getByRole("tab",{name:"Execute & restore"}));
  await open(user);await review(user);
  for(const name of ["Archive inspection","GXT2 game text","Change sets"])expect(screen.getByRole("tab",{name})).toBeDisabled();
  await user.click(screen.getByRole("button",{name:/^Help Center/}));expect(screen.getByRole("heading",{name:"Confirm archive execution"})).toBeVisible();
  await user.click(screen.getByRole("button",{name:"Back to transaction"}));await user.click(screen.getByRole("button",{name:/^Help Center/}));await screen.findByRole("heading",{name:"Help Center"});
});

it("requires an explicitly selected game and two fresh confirmations for a live mods write",async () => {
  const {user,apply,saved} = setup(false,true);apply.mockImplementation(async p => saved(p));
  await user.click(screen.getByRole("button",{name:"Open compiled plan"}));await screen.findByText("Plan: ready");
  expect(screen.getByRole("button",{name:"Review execution"})).toBeDisabled();
  expect(screen.queryByRole("button",{name:"Authorize archive folder"})).not.toBeInTheDocument();
  await user.click(screen.getByRole("button",{name:"Choose GTA installation"}));await review(user);
  const checks = screen.getAllByRole("checkbox"), button = screen.getByRole("button",{name:"Apply to GTA mods archive"});
  await user.click(checks[0]);expect(button).toBeDisabled();expect(apply).not.toHaveBeenCalled();
  await user.click(checks[1]);await user.click(button);await screen.findByText("Receipt: applied");
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({archive_write_confirmed:true,game_write_confirmed:true}));
  expect(screen.getByRole("status")).toHaveTextContent("selected GTA mods archive was updated");
  await review(user,true);expect(screen.getAllByRole("checkbox").every(c => !(c as HTMLInputElement).checked)).toBe(true);
});

it("drops both live-write confirmations after a failed attempt",async () => {
  const {user,apply} = setup(false,true);
  await user.click(screen.getByRole("button",{name:"Open compiled plan"}));await screen.findByText("Plan: ready");
  await user.click(screen.getByRole("button",{name:"Choose GTA installation"}));await review(user);
  apply.mockResolvedValue({...response({}),operation:"error",payload:{message:"Close GTA V"}});
  for(const checkbox of screen.getAllByRole("checkbox"))await user.click(checkbox);
  await user.click(screen.getByRole("button",{name:"Apply to GTA mods archive"}));await screen.findByRole("alert");
  await review(user);expect(screen.getAllByRole("checkbox").every(c => !(c as HTMLInputElement).checked)).toBe(true);
});

it("rejects a live review that omits the game-write requirement",async () => {
  const {user,client,apply} = setup(false,true);
  await user.click(screen.getByRole("button",{name:"Open compiled plan"}));await screen.findByText("Plan: ready");
  await user.click(screen.getByRole("button",{name:"Choose GTA installation"}));
  const original=client.startJob.bind(client);
  client.startJob=async(op,payload,revision,event)=>original(op,payload,revision,message=>{
    if(op === "review_rpf_transaction") (message.payload.result as {game_write_required:boolean}).game_write_required=false;
    event(message);
  });
  await user.click(screen.getByRole("button",{name:"Review execution"}));expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was authorized");expect(apply).not.toHaveBeenCalled();
});

it("opens retained history read-only and verifies the chosen receipt",async () => {
  const {user,apply} = setup();await user.click(screen.getByRole("button",{name:"Refresh transaction history"}));
  await user.click(await screen.findByRole("button",{name:/applied · 2 changes/}));await screen.findByText("Receipt: applied");
  expect(apply).not.toHaveBeenCalled();expect(screen.getByRole("button",{name:"Review receipt recovery"})).toBeDisabled();
});

it("reconciles an interrupted receipt with a metadata-only confirmation",async () => {
  const {user,apply,saved,changed} = setup(false,false,true);apply.mockImplementation(async p => saved(p));
  await user.click(screen.getByRole("button",{name:"Open transaction receipt"}));await screen.findByText("Receipt: verified_staging");
  await user.click(screen.getByRole("button",{name:"Authorize archive folder"}));
  await user.click(screen.getByRole("button",{name:"Review receipt recovery"}));
  expect(await screen.findByRole("heading",{name:"Confirm receipt recovery"})).toHaveFocus();
  expect(screen.getByRole("button",{name:"Reconcile receipt only"})).toBeDisabled();
  await user.click(screen.getByRole("checkbox"));await user.click(screen.getByRole("button",{name:"Reconcile receipt only"}));
  await screen.findByText("Receipt: applied");expect(changed).not.toHaveBeenCalled();
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({action:"recover",receipt_write_confirmed:true}));
  expect(apply.mock.calls[0][0]).not.toHaveProperty("archive_write_confirmed");
  expect(apply.mock.calls[0][0]).not.toHaveProperty("game_write_confirmed");
  expect(screen.getByRole("status")).toHaveTextContent("archive and locks unchanged");
});

it("clears only a reviewed stale lock and shows retained evidence without announcing an archive write",async () => {
  const {user,apply,saved,changed,guard} = setup(false,false,false,true);
  apply.mockImplementation(async p => saved(p));
  await open(user,true);
  expect(screen.getByRole("button",{name:"Review rollback"})).toBeDisabled();
  await user.click(screen.getByRole("button",{name:"Review stale lock cleanup"}));
  expect(await screen.findByRole("heading",{name:"Confirm stale lock cleanup"})).toHaveFocus();
  expect(screen.getByRole("button",{name:"Clear stale lock only"})).toBeDisabled();
  expect(guard).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole("checkbox",{name:/Retain the lock evidence/}));
  await user.click(screen.getByRole("button",{name:"Clear stale lock only"}));
  expect(await screen.findByRole("status")).toHaveTextContent("Archive, receipt and backup unchanged");
  expect(screen.getByRole("status")).toHaveTextContent("cleared-lock-");
  expect(changed).not.toHaveBeenCalled();
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({action:"clear_lock",lock_clear_confirmed:true}));
  expect(apply.mock.calls[0][0]).not.toHaveProperty("archive_write_confirmed");
  expect(apply.mock.calls[0][0]).not.toHaveProperty("receipt_write_confirmed");
  expect(screen.getByRole("button",{name:"Review rollback"})).toBeEnabled();
});

it("requires a separate game confirmation for mods lock cleanup",async () => {
  const {user,apply,saved} = setup(false,true,false,true); apply.mockImplementation(async p => saved(p));
  await user.click(screen.getByRole("button",{name:"Open transaction receipt"}));
  expect(await screen.findByRole("button",{name:"Review stale lock cleanup"})).toBeDisabled();
  await user.click(screen.getByRole("button",{name:"Choose GTA installation"}));
  await user.click(screen.getByRole("button",{name:"Review stale lock cleanup"}));
  await user.click(screen.getByRole("checkbox",{name:/Retain the lock evidence/}));
  expect(screen.getByRole("button",{name:"Clear stale lock only"})).toBeDisabled();
  await user.click(screen.getByRole("checkbox",{name:/GTA is closed. I authorize removing only/}));
  await user.click(screen.getByRole("button",{name:"Clear stale lock only"}));
  expect(await screen.findByRole("status")).toHaveTextContent("Stale lock cleared");
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({game_write_confirmed:true,lock_clear_confirmed:true}));
});

it.each(["active", "unrelated", "unsettled", "unsupported"])("blocks %s locks without enabling a write",async mutation => {
  const {user,client,apply} = setup(false,false,false,true);
  const original = client.startJob.bind(client);
  client.startJob=async(op,payload,revision,event)=>original(op,payload,revision,message=>{
    if(op === "inspect_rpf_transaction") {
      const s = message.payload.result as ReturnType<typeof rpfTransactionPreviewSession>;
      if(mutation === "active") s.archive_lock!.process_running=true;
      else if(mutation === "unrelated") s.archive_lock!.plan_id="0".repeat(64);
      else if(mutation === "unsettled") s.status="verified_staging";
      else s.archive_lock!.cleanup_supported=false;
    }
    event(message);
  });
  await user.click(screen.getByRole("button",{name:"Open transaction receipt"}));
  await user.click(screen.getByRole("button",{name:"Authorize archive folder"}));
  expect(screen.getByRole("button",{name:"Review stale lock cleanup"})).toBeDisabled();
  expect(apply).not.toHaveBeenCalled();
});

it.each(["lock", "evidence", "archive_write", "game_write"])("rejects mismatched lock-cleanup review %s",async mutation => {
  const {user,client,apply} = setup(false,false,false,true); await open(user,true);
  const original = client.startJob.bind(client);
  client.startJob=async(op,payload,revision,event)=>original(op,payload,revision,message=>{
    if(op === "review_rpf_transaction") {
      const value = message.payload.result as ReturnType<typeof rpfTransactionPreviewReview>;
      if(mutation === "lock") value.session.archive_lock!.sha256="0".repeat(64);
      else if(mutation === "evidence") value.lock_evidence!.path="C:\\elsewhere.json";
      else if(mutation === "archive_write") value.archive_write_required=true;
      else value.game_write_required=true;
    }
    event(message);
  });
  await user.click(screen.getByRole("button",{name:"Review stale lock cleanup"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was authorized");
  expect(apply).not.toHaveBeenCalled();
});

it("drops lock-cleanup consent after failure and rejects changed receipt output",async () => {
  const {user,apply,saved} = setup(false,false,false,true); await open(user,true);
  apply.mockResolvedValue({...response({}),operation:"error",payload:{message:"Lock changed after review"}});
  await user.click(screen.getByRole("button",{name:"Review stale lock cleanup"}));
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button",{name:"Clear stale lock only"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("Lock changed");
  await user.click(screen.getByRole("button",{name:"Review stale lock cleanup"}));
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  apply.mockImplementation(async p => { const result=saved(p); (result.payload.result as {session:{state_sha256:string}}).session.state_sha256="0".repeat(64); return result; });
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button",{name:"Clear stale lock only"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be verified");
  expect(screen.queryByText(/Stale lock cleared/)).not.toBeInTheDocument();
});
