import { useEffect, useRef, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import type { DesktopClient, Envelope, AssetPreviewResult } from "./types";
import type { WeaponSnapshot } from "./WeaponWorkbench";
import "./weapon-calibration.css";

type Point = [number, number];
type Profile = "iron" | "scope";
type Check = "pass" | "fail" | "not_tested";
const checks = ["aim", "fire", "reload", "camera_switch", "attachment_stability"] as const;
interface SessionSummary { id: string; profile: Profile; edition: string; accepted: boolean; captured_at: string }
interface MarkedSession extends SessionSummary {
  measurements: { aim: Point; sight: Point; impacts: Point[]; visual_error_px: Point; impact_centroid_error_px: Point | null };
  screenshot: { width: number; height: number; sha256: string };
  diagnostic: { artifact_id: string; build_fingerprint: string; files: { status: string }[]; findings: { code: string; message: string }[] } | null;
}
interface Comparison { kind: string; baseline: MarkedSession; trial: MarkedSession; comparison: Record<string, unknown> }
interface Review { payload: Record<string, unknown>; digest: string; session: MarkedSession }

function MarkedImage({ url, width, height, aim, sight, impacts, onMark }: {
  url: string; width: number; height: number; aim: Point | null; sight: Point | null; impacts: Point[]; onMark?: (point: Point) => void;
}) {
  return <svg className="calibration-image" viewBox={`0 0 ${width} ${height}`} aria-label="Marked calibration screenshot"
    onClick={event => {
      if (!onMark) return;
      const rect = event.currentTarget.getBoundingClientRect();
      onMark([Math.min(width - 1, Math.max(0, Math.round((event.clientX - rect.left) / rect.width * width))),
        Math.min(height - 1, Math.max(0, Math.round((event.clientY - rect.top) / rect.height * height)))]);
    }}>
    <image href={url} width={width} height={height} />
    {[...(aim ? [{ p: aim, color: "#3cff79", label: "Aim" }] : []), ...(sight ? [{ p: sight, color: "#00cfff", label: "Sight" }] : []),
      ...impacts.map((p, i) => ({ p, color: "#ffae44", label: `Impact ${i + 1}` }))].map(({ p, color, label }) => <g key={label}>
      <circle cx={p[0]} cy={p[1]} r={width / 100} fill="none" stroke={color} strokeWidth={width / 400} />
      <text x={p[0] + width / 80} y={p[1]} fill={color} fontSize={width / 50} stroke="#111" strokeWidth={width / 1600} paintOrder="stroke">{label}</text>
    </g>)}
  </svg>;
}

export default function WeaponCalibration({ client, snapshot, disabled, onPending, onReview }: {
  client: DesktopClient; snapshot: WeaponSnapshot; disabled: boolean;
  onPending: (pending: boolean) => void; onReview: (payload: Record<string, unknown>) => void;
}) {
  const [profile, setProfile] = useState<Profile>("iron");
  const [edition, setEdition] = useState(["Legacy", "Enhanced"].includes(snapshot.project.edition) ? snapshot.project.edition : "Enhanced");
  const [screenshot, setScreenshot] = useState("");
  const [capturedAt, setCapturedAt] = useState("");
  const [image, setImage] = useState<{ url: string; width: number; height: number; sha256: string } | null>(null);
  const [aim, setAim] = useState<Point | null>(null), [sight, setSight] = useState<Point | null>(null), [impacts, setImpacts] = useState<Point[]>([]);
  const [mark, setMark] = useState("aim"), [manualX, setManualX] = useState(""), [manualY, setManualY] = useState("");
  const [camera, setCamera] = useState("first_person_aim"), [fov, setFov] = useState(""), [fovAxis, setFovAxis] = useState("unknown");
  const [distance, setDistance] = useState(""), [pose, setPose] = useState("standing stationary");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [outcomes, setOutcomes] = useState<Record<string, Check>>(() => Object.fromEntries(checks.map(key => [key, "not_tested"])));
  const [notes, setNotes] = useState(""), [frames, setFrames] = useState("");
  const [accepted, setAccepted] = useState(false), [conditionsConfirmed, setConditionsConfirmed] = useState(false);
  const [artifact, setArtifact] = useState(""), [receipt, setReceipt] = useState(""), [game, setGame] = useState(""), [runtime, setRuntime] = useState("");
  const [sessions, setSessions] = useState<SessionSummary[]>([]), [baseline, setBaseline] = useState(""), [trial, setTrial] = useState("");
  const [comparison, setComparison] = useState<Comparison | null>(null), [pairImages, setPairImages] = useState<string[]>([]);
  const [field, setField] = useState(""), [axis, setAxis] = useState("x"), [controlled, setControlled] = useState(false);
  const [review, setReview] = useState<Review | null>(null), [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [edited, setEdited] = useState(false);
  const jobs = useRef(new Set<string>()), epoch = useRef(0);
  const locked = busy || disabled || Boolean(review);
  const context = { workspace: snapshot.workspace, weapon: snapshot.selected_weapon };
  useEffect(() => { onPending(edited || busy || Boolean(review)); }, [edited, busy, review, onPending]);
  useEffect(() => { const active = jobs.current; return () => { epoch.current++; for (const id of active) void client.cancelJob(id); }; }, [client]);
  const request = (operation: "inspect_weapon_workbench" | "review_weapon_authoring" | "preview_asset", payload: Record<string, unknown>): Promise<Envelope> => {
    const generation = epoch.current;
    return new Promise((resolve, reject) => {
      void client.startJob(operation, payload, `calibration-${generation}`, message => {
        if (generation !== epoch.current) return;
        if (!message.terminal) return;
        if (message.operation === "error") reject(new Error(String(message.payload.message || "Calibration request failed")));
        else if (message.payload.result) resolve({ ...message, payload: message.payload.result as Record<string, unknown> });
        else reject(new Error("Calibration request returned no result"));
      }).then(job => { if (generation === epoch.current) jobs.current.add(job.job_id); else void client.cancelJob(job.job_id); }, reject);
    });
  };
  const work = async (action: () => Promise<void>) => {
    const generation = epoch.current;
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (reason) { if (generation === epoch.current) setError(String(reason)); }
    finally { if (generation === epoch.current) setBusy(false); }
  };
  const list = async () => {
    const result = await request("inspect_weapon_workbench", { ...context, calibration_action: "list" });
    const data = result.payload as { kind?: string; sessions?: SessionSummary[] };
    if (data.kind !== "weapon_calibration_sessions" || !Array.isArray(data.sessions)) throw new Error("Calibration session support requires the updated sidecar.");
    setSessions(data.sessions);
  };
  const preview = async (path: string) => {
    const normalized = path.replaceAll("\\", "/"), split = normalized.lastIndexOf("/");
    const result = await request("preview_asset", { source: normalized.slice(0, split), entry: normalized.slice(split + 1) });
    const data = result.payload as AssetPreviewResult;
    if (!data.artifact || !data.artifact.width || !data.artifact.height || !data.artifact.media_type.startsWith("image/")) throw new Error("Choose a PNG or JPEG screenshot.");
    const dimensions = String(data.metadata.dimensions || "").match(/^(\d+)\s*×\s*(\d+)$/);
    if (!dimensions || !data.sha256 || data.truncated) throw new Error("Screenshot source dimensions/hash are missing or truncated; no marks were assumed.");
    return { url: data.artifact.preview_url || convertFileSrc(data.artifact.path), width: Number(dimensions[1]), height: Number(dimensions[2]), sha256: data.sha256 };
  };
  const selectImage = () => work(async () => {
    const selected = await client.selectPath("calibration_screenshot");
    if (!selected) return;
    const loaded = await preview(selected);
    setScreenshot(selected); setImage(loaded); setAim(null); setSight(null); setImpacts([]); setEdited(true);
  });
  const addMark = (point: Point) => {
    if (locked || !image || point.some(v => !Number.isFinite(v)) || point[0] < 0 || point[0] >= image.width || point[1] < 0 || point[1] >= image.height) return;
    if (mark === "aim") setAim(point); else if (mark === "sight") setSight(point); else setImpacts(old => old.length < 64 ? [...old, point] : old);
    setEdited(true);
  };
  const reset = () => { setScreenshot(""); setImage(null); setAim(null); setSight(null); setImpacts([]); setEdited(false); setAccepted(false); setConditionsConfirmed(false); setReview(null); setConfirmed(false); setCapturedAt(""); setOutcomes(Object.fromEntries(checks.map(key => [key, "not_tested"]))); setFrames(""); setNotes(""); };
  const reviewSession = () => work(async () => {
    const payload = { ...context, action: "calibration_session", expected_revision: snapshot.revision,
      ...(artifact || receipt || game || runtime ? { diagnostic: { source: artifact, comparison: receipt, gta_path: game, ...(runtime ? { document: runtime } : {}) } } : {}),
      session: { id: crypto.randomUUID(), captured_at: capturedAt, profile, edition, screenshot, screenshot_sha256: image?.sha256,
        conditions: { camera, fov_degrees: Number(fov), fov_axis: fovAxis, distance_m: Number(distance), pose, attachments },
        aim, sight, impacts, checks: outcomes, accepted, notes,
        frame_times_ms: frames.trim() ? frames.split(/[\s,]+/).filter(Boolean).map(Number) : [] } };
    const response = await request("review_weapon_authoring", payload);
    const data = response.payload as { review_sha256: string; session: MarkedSession };
    if (!data.session || !data.review_sha256) throw new Error("Missing session review.");
    setReview({ payload, digest: data.review_sha256, session: data.session }); setConfirmed(false);
  });
  const saveSession = () => work(async () => {
    if (!review || !confirmed) return;
    const result = await client.applyWeaponAuthoring({ ...review.payload, review_sha256: review.digest, authoring_confirmed: true });
    if (result.operation === "error" || (result.payload.result as { kind?: string })?.kind !== "weapon_calibration_saved") throw new Error(String(result.payload.message || "Session was not saved"));
    reset(); await list(); setNotice("Session and screenshot recorded. Saved references remain unchanged; no weapon or game files were edited.");
  });
  const compare = () => work(async () => {
    setComparison(null); setPairImages([]);
    const result = await request("inspect_weapon_workbench", { ...context, calibration_action: "compare", baseline, trial });
    const data = result.payload as unknown as Comparison;
    if (data.kind !== "weapon_calibration_comparison") throw new Error("Missing comparison.");
    setComparison(data);
    const urls = [];
    for (const id of [baseline, trial]) urls.push((await preview(`${snapshot.workspace}/.weapon-calibration/${id}/screenshot.png`)).url);
    setPairImages(urls);
  });
  const propose = () => work(async () => {
    const evidence = { ...context, baseline, trial, field, axis, controlled_trial_confirmed: controlled };
    const result = await request("inspect_weapon_workbench", { ...evidence, calibration_action: "propose" });
    const data = result.payload as { kind: string; updates: Record<string, string> };
    if (data.kind !== "weapon_calibration_proposal") throw new Error("Missing bounded proposal.");
    onReview({ ...context, action: "edit", expected_revision: snapshot.revision, updates: data.updates, calibration_evidence: evidence });
  });
  const profileFields = (snapshot.camera_fields ?? []).filter(spec => profile === "iron"
    ? /^weapon\.firstPersonLT(?:Rotation)?Offset\./.test(spec.key)
    : /^weapon\.firstPersonScope(?:Attachment)?(?:Rotation)?Offset\./.test(spec.key));
  const filtered = sessions.filter(s => s.profile === profile && s.edition === edition);
  const attachmentOptions = snapshot.project.attachments.filter(a => a.weapon_name === snapshot.selected_weapon);
  return <section className="weapon-calibration" aria-label="Calibration & Testing">
    <h4>Calibration &amp; Testing · {snapshot.selected_weapon}</h4>
    <p>Imported observations, not live capture. Pixel alignment and measured impacts stay separate. Camera/FOV, equipped attachments and test outcomes are operator-reported, not engine telemetry.</p>
    {!snapshot.workspace && <p>Create or open an editable copy to record sessions. Game files are never edited here.</p>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <fieldset disabled={locked || !snapshot.workspace}>
      <div className="calibration-grid">
        <label>Sight profile<select value={profile} disabled={edited} onChange={e => { setProfile(e.target.value as Profile); setBaseline(""); setTrial(""); setComparison(null); setField(""); setControlled(false); }}><option value="iron">Iron sights</option><option value="scope">Scope</option></select></label>
        <label>Edition<select value={edition} disabled={edited || ["Legacy", "Enhanced"].includes(snapshot.project.edition)} onChange={e => { setEdition(e.target.value); setComparison(null); }}><option>Legacy</option><option>Enhanced</option></select></label>
      </div>
      <details open><summary>Record a sight test</summary><div className="calibration-scroll" onChange={() => setEdited(true)}>
        <button className="quiet-button" onClick={() => void selectImage()}>Choose screenshot</button><code>{screenshot}</code>
        <div className="calibration-grid">
          <label>Mark<select value={mark} onChange={e => setMark(e.target.value)}><option value="aim">Intended aim (green)</option><option value="sight">Sight position (blue)</option><option value="impact">Observed impact (orange)</option></select></label>
          <label>Pixel X<input type="number" value={manualX} onChange={e => setManualX(e.target.value)} /></label><label>Pixel Y<input type="number" value={manualY} onChange={e => setManualY(e.target.value)} /></label>
          <button onClick={() => addMark([Number(manualX), Number(manualY)])} disabled={!image || !manualX || !manualY}>Add coordinate mark</button>
        </div>
        {image && <MarkedImage {...image} aim={aim} sight={sight} impacts={impacts} onMark={addMark} />}
        <p>Aim: {aim?.join(", ") || "unmarked"} · Sight: {sight?.join(", ") || "unmarked"} · Impacts: {impacts.length}. Mark impacts only in the same fixed-camera frame.</p>
        <button onClick={() => { setAim(null); setSight(null); setImpacts([]); }}>Clear marks</button>
        <div className="calibration-grid">
          <label>Camera state<input value={camera} onChange={e => setCamera(e.target.value)} maxLength={160} /></label>
          <label>Reported capture time (ISO with timezone)<input placeholder="2026-09-07T10:00:00Z" value={capturedAt} onChange={e => setCapturedAt(e.target.value)} maxLength={40} /></label>
          <label>Recorded FOV (degrees)<input type="number" min="1" max="179" value={fov} onChange={e => setFov(e.target.value)} /></label>
          <label>FOV axis<select value={fovAxis} onChange={e => setFovAxis(e.target.value)}><option value="unknown">Unknown</option><option value="vertical">Vertical</option><option value="horizontal">Horizontal</option></select></label>
          <label>Target distance (metres)<input type="number" min="0.1" max="2000" value={distance} onChange={e => setDistance(e.target.value)} /></label>
          <label>Pose / test setup<input value={pose} onChange={e => setPose(e.target.value)} maxLength={160} /></label>
        </div>
        <p>Equipped attachments (not assumed from metadata defaults):</p>
        {[...new Set(attachmentOptions.map(a => a.component_name))].map(name => <label className="weapon-checkbox" key={name}><input type="checkbox" checked={attachments.includes(name)} onChange={e => setAttachments(old => e.target.checked ? [...old, name] : old.filter(v => v !== name))} />{name}</label>)}
        <div className="calibration-grid">{checks.map(key => <label key={key}>{key.replaceAll("_", " ")}<select value={outcomes[key]} onChange={e => { setOutcomes(old => ({ ...old, [key]: e.target.value as Check })); setAccepted(false); }}><option value="not_tested">Not tested</option><option value="pass">Pass</option><option value="fail">Fail</option></select></label>)}</div>
        <p>Use the same target and pose: aim and fire a group; reload; switch camera out and back; observe attachment movement throughout. “Pass” is your observation, not an SDK certification.</p>
        <label>Notes / attachment movement<textarea value={notes} maxLength={2000} onChange={e => setNotes(e.target.value)} /></label>
        <label>Selected frame times (ms, optional; up to 1000)<textarea value={frames} maxLength={16000} onChange={e => setFrames(e.target.value)} /></label>
        <details><summary>Installed build evidence (optional)</summary>
          <p>Use the artifact, Launcher receipt, game folder and optional runtime session from the same installation. They are rechecked at save. Without these, build identity is unresolved. Even matching installed bytes do not prove the imported screenshot or workspace came from that running build.</p>
          {[{ label: "SDK artifact JSON", value: artifact, set: setArtifact }, { label: "Launcher receipt JSON", value: receipt, set: setReceipt }, { label: "GTA folder", value: game, set: setGame }, { label: "Runtime session JSON (optional)", value: runtime, set: setRuntime }].map(item => <label key={item.label}>{item.label}<input value={item.value} onChange={e => item.set(e.target.value)} /></label>)}
        </details>
        <label className="weapon-checkbox"><input type="checkbox" checked={conditionsConfirmed} onChange={e => setConditionsConfirmed(e.target.checked)} />I recorded the camera conditions and equipped attachments for this screenshot.</label>
        <label className="weapon-checkbox"><input type="checkbox" checked={accepted} disabled={checks.some(key => outcomes[key] !== "pass")} onChange={e => setAccepted(e.target.checked)} />Save as an accepted reference for this sight profile (all checks must pass).</label>
        <button className="primary-button" disabled={!aim || !sight || !screenshot || !capturedAt || !fov || !distance || !conditionsConfirmed} onClick={() => void reviewSession()}>Review session recording</button>
        <button className="quiet-button" onClick={reset}>Discard session draft</button>
      </div></details>
      <details open><summary>Compare &amp; propose</summary><div className="calibration-scroll">
        <button onClick={() => void work(list)}>Load recorded sessions</button>
        <div className="calibration-grid">{[{ label: "Reference / controlled baseline", value: baseline, set: setBaseline }, { label: "Current trial", value: trial, set: setTrial }].map(item => <label key={item.label}>{item.label}<select value={item.value} onChange={e => { item.set(e.target.value); setComparison(null); setControlled(false); }}><option value="">Choose session</option>{filtered.map(s => <option key={s.id} value={s.id}>{s.accepted ? "Accepted · " : "Trial · "}{s.captured_at} · {s.id}</option>)}</select></label>)}</div>
        <button disabled={!baseline || !trial || baseline === trial} onClick={() => void compare()}>Compare sessions</button>
        {comparison && <><div className="calibration-grid">{[comparison.baseline, comparison.trial].map((s, i) => <div key={s.id}><h5>{i ? "Trial" : "Reference"} · {s.accepted ? "Accepted" : "Unaccepted"}</h5>{pairImages[i] && <MarkedImage url={pairImages[i]} {...s.screenshot} {...s.measurements} />}<p>Visual: {s.measurements.visual_error_px.join(", ")} px<br />Impact: {s.measurements.impact_centroid_error_px?.join(", ") ?? "not measured"} px</p><p>Artifact: {s.diagnostic?.artifact_id || "unresolved"}</p></div>)}</div><pre>{JSON.stringify(comparison.comparison, null, 2)}</pre></>}
        <p>For a visual correction, record a controlled baseline, change exactly one field with the existing editor, and record a trial. All other package content and reported test conditions must match. Accepted current trials are protected. Impact-centroid differences never become camera edits.</p>
        <label>Existing profile field<select value={field} onChange={e => setField(e.target.value)}><option value="">Choose one field</option>{profileFields.map(spec => <option key={spec.key} value={spec.key}>{spec.label}</option>)}</select></label>
        <label>Measured screen axis<select value={axis} onChange={e => setAxis(e.target.value)}><option value="x">X (right)</option><option value="y">Y (down)</option></select></label>
        <label className="weapon-checkbox"><input type="checkbox" checked={controlled} onChange={e => setControlled(e.target.checked)} />These were controlled tests with only the selected field changed; the recorded workspace was the one I tested.</label>
        <button className="primary-button" disabled={edited || !baseline || !trial || !field || !controlled} onClick={() => void propose()}>Propose &amp; review visual edit</button>
      </div></details>
    </fieldset>
    {review && <section className="weapon-review" aria-label="Calibration session review">
      <h4>Review {review.session.accepted ? "accepted reference" : "test session"} · {review.session.profile}</h4>
      <p>Visual error: {review.session.measurements.visual_error_px.join(", ")} px. Impact error: {review.session.measurements.impact_centroid_error_px?.join(", ") ?? "not measured"} px.</p>
      <p>Screenshot SHA-256: <code>{review.session.screenshot.sha256}</code></p>
      <p>Artifact: {review.session.diagnostic?.artifact_id || "unresolved — no installed build evidence supplied"}</p>
      {review.session.diagnostic && <><p>Build: {review.session.diagnostic.build_fingerprint}</p>{review.session.diagnostic.findings.map((f, i) => <p key={i}>{f.code}: {f.message}</p>)}</>}
      <p>This creates an immutable local record and copies the screenshot; no weapon metadata or installed files change. The checksum is content identity, not authentication.</p>
      <label className="weapon-checkbox"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />I confirm these observations and this local recording.</label>
      <button disabled={busy} onClick={() => { setReview(null); setConfirmed(false); }}>Cancel session review</button>
      <button disabled={busy || !confirmed} onClick={() => void saveSession()}>Confirm session recording</button>
    </section>}
  </section>;
}
