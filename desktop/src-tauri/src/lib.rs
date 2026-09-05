mod launch;
mod protocol;
mod package_probe;
mod sidecar;
mod runtime_location;

use launch::{parse_launch_args, LaunchRequest};
use protocol::Envelope;
use serde::Serialize;
use serde_json::{json, Value};
use sidecar::{validate_command, SidecarManager};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tauri::ipc::Channel;
use tauri::{Emitter, Manager, State, WindowEvent};

struct LaunchState(Mutex<Option<LaunchRequest>>);
struct CloseState(AtomicBool);

pub fn inspect_runtime_location() -> Result<Value, String> {
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let root = executable.parent().ok_or("SDK executable has no parent directory")?;
    let mut report = runtime_location::inspect(root);
    report["build_identity"] = build_identity().unwrap_or(Value::Null);
    Ok(report)
}

#[tauri::command]
fn desktop_frontend_ready(state: State<'_, CloseState>) {
    state.0.store(true, Ordering::Release);
}

#[tauri::command]
async fn desktop_close(window: tauri::WebviewWindow, manager: State<'_, Arc<SidecarManager>>) -> Result<(), String> {
    let manager = manager.inner().clone();
    tauri::async_runtime::spawn_blocking(move || manager.try_shutdown())
        .await.map_err(|error| format!("SDK shutdown failed: {error}"))??;
    window.destroy().map_err(|error| error.to_string())
}

pub fn build_identity() -> Option<Value> {
    option_env!("ALLIN1_BUILD_IDENTITY_JSON").map(|value| {
        serde_json::from_str(value).expect("invalid embedded SDK build identity")
    })
}

pub fn inspect_embedded_frontend() -> Result<Value, String> {
    let identity = build_identity().unwrap_or(Value::Null);
    package_probe::inspect(&tauri::generate_context!(), identity["build_id"].as_str().unwrap_or(""), env!("CARGO_PKG_VERSION"))
}

#[derive(Serialize)]
struct JobStart {
    job_id: String,
    accepted: Envelope,
}

async fn broker_request(
    manager: Arc<SidecarManager>,
    operation: &'static str,
    payload: Value,
    timeout: Duration,
) -> Result<Envelope, String> {
    tauri::async_runtime::spawn_blocking(move || manager.request(operation, payload, timeout))
        .await
        .map_err(|error| format!("sidecar request task failed: {error}"))?
}

#[tauri::command]
async fn desktop_handshake(manager: State<'_, Arc<SidecarManager>>) -> Result<Envelope, String> {
    let manager = manager.inner().clone();
    tauri::async_runtime::spawn_blocking(move || manager.handshake())
        .await
        .map_err(|error| format!("sidecar handshake task failed: {error}"))?
}

#[tauri::command]
async fn desktop_catalog(manager: State<'_, Arc<SidecarManager>>) -> Result<Envelope, String> {
    broker_request(
        manager.inner().clone(),
        "catalog",
        json!({}),
        Duration::from_secs(30),
    )
    .await
}

#[tauri::command]
async fn desktop_execute(
    manager: State<'_, Arc<SidecarManager>>,
    command: String,
    args: Vec<String>,
    authoring_confirmed: Option<bool>,
) -> Result<Envelope, String> {
    validate_command(&command, &args)?;
    broker_request(
        manager.inner().clone(),
        "execute",
        json!({
            "command": command,
            "args": args,
            "authoring_confirmed": authoring_confirmed.unwrap_or(false),
        }),
        Duration::from_secs(15 * 60),
    )
    .await
}

#[tauri::command]
async fn desktop_prepare_vehicle_quick_import(
    manager: State<'_, Arc<SidecarManager>>,
    payload: Value,
) -> Result<Envelope, String> {
    broker_request(
        manager.inner().clone(),
        "prepare_vehicle_quick_import",
        payload,
        Duration::from_secs(15 * 60),
    )
    .await
}

#[tauri::command]
async fn desktop_apply_vehicle_oiv_export(
    manager: State<'_, Arc<SidecarManager>>,
    payload: Value,
) -> Result<Envelope, String> {
    broker_request(manager.inner().clone(), "apply_vehicle_oiv_export", payload,
        Duration::from_secs(15 * 60)).await
}

#[tauri::command]
async fn desktop_apply_workspace_action(manager: State<'_, Arc<SidecarManager>>, payload: Value) -> Result<Envelope, String> {
    broker_request(manager.inner().clone(), "apply_workspace_action", payload, Duration::from_secs(15 * 60)).await
}

#[tauri::command]
async fn desktop_apply_gxt2_action(manager: State<'_, Arc<SidecarManager>>, payload: Value) -> Result<Envelope, String> {
    broker_request(manager.inner().clone(), "apply_gxt2_action", payload, Duration::from_secs(15 * 60)).await
}

#[tauri::command]
async fn desktop_apply_rpf_change_set(manager: State<'_, Arc<SidecarManager>>, payload: Value) -> Result<Envelope, String> {
    broker_request(manager.inner().clone(), "apply_rpf_change_set", payload, Duration::from_secs(15 * 60)).await
}

#[tauri::command]
async fn desktop_apply_rpf_transaction(manager: State<'_, Arc<SidecarManager>>, payload: Value) -> Result<Envelope, String> {
    broker_request(manager.inner().clone(), "apply_rpf_transaction", payload, Duration::from_secs(15 * 60)).await
}

#[tauri::command]
async fn desktop_apply_rpf_utility(manager: State<'_, Arc<SidecarManager>>, payload: Value) -> Result<Envelope, String> {
    broker_request(manager.inner().clone(), "apply_rpf_utility", payload, Duration::from_secs(30 * 60)).await
}

fn rpf_utility_file_name(suggested_name: &str, extension: &str, fallback: &str) -> String {
    let candidate = Path::new(suggested_name).file_name().and_then(|name| name.to_str())
        .map(|name| name.trim().trim_end_matches('.'))
        .filter(|name| !name.is_empty() && name.len() <= 150
            && name.chars().all(|character| character.is_alphanumeric() || " -_.()[]".contains(character)))
        .filter(|name| !matches!(name.split('.').next().unwrap_or("").to_ascii_lowercase().as_str(),
            "con" | "prn" | "aux" | "nul" | "com1" | "com2" | "com3" | "com4" | "com5" | "com6" | "com7" | "com8" | "com9" | "lpt1" | "lpt2" | "lpt3" | "lpt4" | "lpt5" | "lpt6" | "lpt7" | "lpt8" | "lpt9"))
        .unwrap_or(fallback);
    let mut path = PathBuf::from(candidate);
    if !extension.is_empty() { path.set_extension(extension); }
    path.file_name().and_then(|name| name.to_str()).unwrap_or(fallback).to_string()
}

#[tauri::command]
async fn select_rpf_utility_destination(action: String, suggested_name: String) -> Result<Option<String>, String> {
    if matches!(action.as_str(), "export_native_workspace" | "extract_subtree" | "extract_archive") {
        let safe_name = rpf_utility_file_name(&suggested_name, "", "rpf-export");
        return Ok(rfd::AsyncFileDialog::new()
            .set_title("Choose parent folder for the new RPF export")
            .pick_folder().await
            .map(|handle| handle.path().join(safe_name)
                .to_string_lossy().into_owned()));
    }
    let (title, extension, filter) = match action.as_str() {
        "extract_entry" => ("Extract an exact RPF member", "", "RPF member copy"),
        "compare" => ("Save RPF comparison report", "json", "JSON report"),
        "verify_integrity" => ("Save RPF integrity report", "json", "JSON report"),
        "defragment_copy" => ("Save verified defragmented RPF copy", "rpf", "Rockstar archive"),
        _ => return Err(format!("unsupported RPF utility destination: {action}")),
    };
    let file_name = rpf_utility_file_name(&suggested_name, extension, "rpf-output");
    let mut dialog = rfd::AsyncFileDialog::new().set_title(title).set_file_name(file_name);
    if !extension.is_empty() { dialog = dialog.add_filter(filter, &[extension]); }
    Ok(dialog.save_file().await.map(|handle| handle.path().to_string_lossy().into_owned()))
}

#[tauri::command]
async fn select_rpf_plan_destination(suggested_name: String) -> Result<Option<String>, String> {
    let name = Path::new(&oiv_file_name(&suggested_name)).with_extension("json");
    Ok(rfd::AsyncFileDialog::new().set_title("Save new RPF change set or plan")
        .set_file_name(name.to_string_lossy()).add_filter("RPF authoring JSON", &["json"])
        .save_file().await.map(|h| h.path().to_string_lossy().into_owned()))
}

#[tauri::command]
async fn select_gxt2_build_destination(suggested_name: String) -> Result<Option<String>, String> {
    let name = Path::new(&oiv_file_name(&suggested_name)).with_extension("gxt2");
    let selected = rfd::AsyncFileDialog::new().set_title("Build a new GXT2 dictionary")
        .set_file_name(name.to_string_lossy()).add_filter("GTA text dictionary", &["gxt2"])
        .save_file().await;
    selected.map(|handle| {
        let path = handle.path();
        let parent = path.parent().ok_or("Destination has no parent")?.canonicalize().map_err(|e| e.to_string())?;
        Ok(parent.join(path.file_name().ok_or("Destination has no filename")?).to_string_lossy().into_owned())
    }).transpose()
}

#[tauri::command]
async fn desktop_apply_vehicle_package_publish(
    manager: State<'_, Arc<SidecarManager>>,
    payload: Value,
) -> Result<Envelope, String> {
    broker_request(
        manager.inner().clone(), "apply_vehicle_package_publish", payload,
        Duration::from_secs(15 * 60),
    ).await
}

#[tauri::command]
async fn desktop_apply_package_lifecycle(
    manager: State<'_, Arc<SidecarManager>>,
    payload: Value,
) -> Result<Envelope, String> {
    broker_request(
        manager.inner().clone(),
        "apply_package_lifecycle",
        payload,
        Duration::from_secs(15 * 60),
    )
    .await
}

#[tauri::command]
async fn desktop_render_vehicle_model(
    manager: State<'_, Arc<SidecarManager>>,
    payload: Value,
) -> Result<Envelope, String> {
    broker_request(
        manager.inner().clone(),
        "render_vehicle_model",
        payload,
        Duration::from_secs(2 * 60),
    )
    .await
}

fn validated_vehicle_authoring_operation(operation: &str) -> Result<&'static str, String> {
    match operation {
        "create_vehicle_authoring_workspace" => Ok("create_vehicle_authoring_workspace"),
        "apply_vehicle_authoring_edit" => Ok("apply_vehicle_authoring_edit"),
        "apply_vehicle_authoring_appearance" => Ok("apply_vehicle_authoring_appearance"),
        "apply_vehicle_authoring_tuning" => Ok("apply_vehicle_authoring_tuning"),
        "apply_vehicle_authoring_light_profile" => Ok("apply_vehicle_authoring_light_profile"),
        "apply_vehicle_authoring_axles" => Ok("apply_vehicle_authoring_axles"),
        "apply_vehicle_authoring_transmission" => Ok("apply_vehicle_authoring_transmission"),
        "apply_vehicle_authoring_distribution" => Ok("apply_vehicle_authoring_distribution"),
        "apply_vehicle_package_build" => Ok("apply_vehicle_package_build"),
        "apply_vehicle_authoring_history" => Ok("apply_vehicle_authoring_history"),
        _ => Err(format!(
            "vehicle authoring operation is not allowlisted: {operation}"
        )),
    }
}

fn validated_model_material_authoring_operation(operation: &str) -> Result<&'static str, String> {
    match operation {
        "create_model_material_workspace" => Ok("create_model_material_workspace"),
        "apply_model_material_edit" => Ok("apply_model_material_edit"),
        "apply_model_material_history" => Ok("apply_model_material_history"),
        "apply_model_material_build" => Ok("apply_model_material_build"),
        _ => Err(format!(
            "model material authoring operation is not allowlisted: {operation}"
        )),
    }
}

fn validated_texture_authoring_operation(operation: &str) -> Result<&'static str, String> {
    match operation {
        "create_texture_workspace" => Ok("create_texture_workspace"),
        "apply_texture_edit" => Ok("apply_texture_edit"),
        "apply_texture_history" => Ok("apply_texture_history"),
        "apply_texture_build" => Ok("apply_texture_build"),
        _ => Err(format!(
            "texture authoring operation is not allowlisted: {operation}"
        )),
    }
}

#[tauri::command]
async fn desktop_vehicle_authoring_action(
    manager: State<'_, Arc<SidecarManager>>,
    operation: String,
    payload: Value,
) -> Result<Envelope, String> {
    let authoring_operation = validated_vehicle_authoring_operation(&operation)?;
    broker_request(
        manager.inner().clone(),
        authoring_operation,
        payload,
        Duration::from_secs(15 * 60),
    )
    .await
}

#[tauri::command]
async fn desktop_model_material_authoring_action(
    manager: State<'_, Arc<SidecarManager>>,
    operation: String,
    payload: Value,
) -> Result<Envelope, String> {
    let authoring_operation = validated_model_material_authoring_operation(&operation)?;
    broker_request(
        manager.inner().clone(),
        authoring_operation,
        payload,
        Duration::from_secs(15 * 60),
    )
    .await
}

#[tauri::command]
async fn desktop_configure_assistant(
    manager: State<'_, Arc<SidecarManager>>,
    payload: Value,
) -> Result<Envelope, String> {
    broker_request(
        manager.inner().clone(),
        "configure_assistant",
        payload,
        Duration::from_secs(15),
    )
    .await
}

#[tauri::command]
async fn desktop_apply_weapon_authoring(
    manager: State<'_, Arc<SidecarManager>>,
    payload: Value,
) -> Result<Envelope, String> {
    broker_request(
        manager.inner().clone(),
        "apply_weapon_authoring",
        payload,
        Duration::from_secs(15 * 60),
    )
    .await
}

#[tauri::command]
async fn desktop_apply_ped_authoring(
    manager: State<'_, Arc<SidecarManager>>,
    payload: Value,
) -> Result<Envelope, String> {
    broker_request(
        manager.inner().clone(),
        "apply_ped_authoring",
        payload,
        Duration::from_secs(15 * 60),
    )
    .await
}

#[tauri::command]
async fn desktop_texture_authoring_action(
    manager: State<'_, Arc<SidecarManager>>,
    operation: String,
    payload: Value,
) -> Result<Envelope, String> {
    let authoring_operation = validated_texture_authoring_operation(&operation)?;
    broker_request(
        manager.inner().clone(),
        authoring_operation,
        payload,
        Duration::from_secs(15 * 60),
    )
    .await
}

#[tauri::command]
async fn desktop_start_job(
    manager: State<'_, Arc<SidecarManager>>,
    operation: String,
    payload: Value,
    revision: String,
    on_event: Channel<Envelope>,
) -> Result<JobStart, String> {
    if !matches!(
        operation.as_str(),
        "execute"
            | "inspect_package"
            | "preview_asset"
            | "inspect_model_materials"
            | "inspect_model_material_workspace"
            | "review_model_material_workspace"
            | "review_model_material_edit"
            | "review_model_material_build"
            | "inspect_texture_workspace"
            | "inspect_authoring_workspace" | "review_workspace_action"
            | "inspect_gxt2_workspace" | "review_gxt2_action"
            | "inspect_rpf_change_set" | "review_rpf_change_set"
            | "list_rpf_transactions" | "inspect_rpf_transaction" | "review_rpf_transaction"
            | "review_texture_workspace"
            | "preview_texture_workspace"
            | "review_texture_edit"
            | "review_texture_build"
            | "assistant_status"
            | "assistant_prompt"
            | "inspect_weapon_workbench"
            | "review_weapon_authoring"
            | "inspect_ped_workbench"
            | "review_ped_authoring"
            | "inspect_rpf_archive"
            | "review_rpf_utility"
            | "inspect_vehicle_project"
            | "inspect_vehicle_authoring_workspace"
            | "review_vehicle_authoring_workspace"
            | "review_vehicle_authoring_edit"
            | "review_vehicle_authoring_appearance"
            | "inspect_vehicle_authoring_tuning"
            | "review_vehicle_authoring_tuning"
            | "review_vehicle_authoring_light_profile"
            | "review_vehicle_authoring_axles"
            | "inspect_vehicle_authoring_axle_skeleton"
            | "review_vehicle_authoring_transmission"
            | "review_vehicle_authoring_distribution"
            | "review_vehicle_package_build"
            | "inspect_recipe"
            | "inspect_package_receipts"
            | "review_package_lifecycle"
            | "inspect_vehicle_quick_import"
            | "review_vehicle_quick_import"
            | "review_vehicle_oiv_export"
            | "review_vehicle_package_publish"
            | "check_update"
    ) {
        return Err(format!("job operation is not allowlisted: {operation}"));
    }
    if revision.len() > 256 || revision.contains('\0') {
        return Err("job revision must be a bounded string".to_string());
    }
    if operation == "execute" {
        let command = payload
            .get("command")
            .and_then(Value::as_str)
            .ok_or("execute job requires a command")?;
        let args: Vec<String> =
            serde_json::from_value(payload.get("args").cloned().unwrap_or_else(|| json!([])))
                .map_err(|_| "execute job args must be strings")?;
        validate_command(command, &args)?;
    }
    let manager = manager.inner().clone();
    let job_id = manager.next_id("job");
    let broker = manager.register_job(job_id.clone(), on_event)?;
    let request_payload = json!({
        "job_id": job_id.clone(),
        "operation": operation,
        "payload": payload,
        "revision": revision,
    });
    let response = broker_request(
        manager,
        "start_job",
        request_payload,
        Duration::from_secs(15),
    )
    .await;
    match response {
        Ok(accepted) if accepted.operation != "error" => Ok(JobStart { job_id, accepted }),
        Ok(error) => {
            broker.unregister_job(&job_id);
            Err(error.payload.to_string())
        }
        Err(error) => {
            broker.unregister_job(&job_id);
            Err(error)
        }
    }
}

#[tauri::command]
async fn desktop_cancel_job(
    manager: State<'_, Arc<SidecarManager>>,
    job_id: String,
) -> Result<Envelope, String> {
    if job_id.is_empty() || job_id.len() > 128 || !job_id.is_ascii() {
        return Err("invalid job id".to_string());
    }
    broker_request(
        manager.inner().clone(),
        "cancel_job",
        json!({"job_id": job_id}),
        Duration::from_secs(8),
    )
    .await
}

#[tauri::command]
async fn desktop_check_update(manager: State<'_, Arc<SidecarManager>>) -> Result<Value, String> {
    let response = broker_request(
        manager.inner().clone(),
        "check_update",
        json!({}),
        Duration::from_secs(30),
    )
    .await?;
    if response.operation == "error" {
        return Err(response.payload.to_string());
    }
    response
        .payload
        .get("result")
        .cloned()
        .ok_or_else(|| "update response did not include a result".to_string())
}

#[tauri::command]
async fn select_path(kind: String) -> Result<Option<String>, String> {
    let selected = match kind.as_str() {
        "ped_workspace" | "ped_parent" => rfd::AsyncFileDialog::new()
            .set_title(if kind == "ped_workspace" { "Open editable ped workspace" } else { "Choose where to create the editable ped copy" })
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "weapon_package" | "weapon_workspace" | "weapon_parent" => rfd::AsyncFileDialog::new()
            .set_title(match kind.as_str() {
                "weapon_package" => "Open unpacked weapon package",
                "weapon_workspace" => "Open editable weapon workspace",
                _ => "Choose where to create the editable weapon copy",
            })
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "package" => rfd::AsyncFileDialog::new()
            .set_title("Open ALLIN1 package or manifest")
            .add_filter(
                "ALLIN1 packages",
                &["json", "zip", "oiv", "rar", "7z", "rpf"],
            )
            .pick_file()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "code_source" => rfd::AsyncFileDialog::new().set_title("Open XML or Lua source")
            .add_filter("XML and Lua source", &["xml", "meta", "lua"]).pick_file().await.map(|h| h.path().to_path_buf()),
        "metadata" => rfd::AsyncFileDialog::new().set_title("Open META/XML")
            .add_filter("GTA metadata", &["meta", "xml", "ymt"]).pick_file().await.map(|handle| handle.path().to_path_buf()),
        "package_folder" => rfd::AsyncFileDialog::new()
            .set_title("Open package folder")
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "mod_package" => rfd::AsyncFileDialog::new()
            .set_title("Review managed package install")
            .add_filter("Managed package", &["toml", "zip"])
            .pick_file()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "mod_package_folder" => rfd::AsyncFileDialog::new()
            .set_title("Review managed package folder")
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "recipe" => rfd::AsyncFileDialog::new()
            .set_title("Open OIV package recipe")
            .add_filter("OIV package recipe", &["oiv", "zip"])
            .pick_file()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "recipe_folder" => rfd::AsyncFileDialog::new()
            .set_title("Open unpacked OIV package recipe")
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "vehicle_import_source" => rfd::AsyncFileDialog::new()
            .set_title("Open vehicle package for Quick Import")
            .add_filter("Vehicle package", &["zip", "rar", "7z", "rpf"])
            .pick_file()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "vehicle_import_folder" => rfd::AsyncFileDialog::new()
            .set_title("Open unpacked vehicle package")
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "model_asset" => rfd::AsyncFileDialog::new()
            .set_title("Open native model")
            .add_filter("GTA V native model", &["ydr", "ydd", "yft"])
            .pick_file()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "model_material_parent" => rfd::AsyncFileDialog::new()
            .set_title("Choose where to create the editable material workspace")
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "model_material_workspace" => rfd::AsyncFileDialog::new()
            .set_title("Open editable model material workspace")
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "texture_asset" => rfd::AsyncFileDialog::new()
            .set_title("Open native texture dictionary")
            .add_filter("GTA V texture dictionary", &["ytd"])
            .pick_file()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "binary_source" => rfd::AsyncFileDialog::new().set_title("Open binary source").pick_file().await.map(|h| h.path().to_path_buf()),
        "binary_workspace" | "authoring_parent" | "map_source" | "graph_source" => rfd::AsyncFileDialog::new().set_title("Choose authoring folder").pick_folder().await.map(|h| h.path().to_path_buf()),
        "graph_document" | "program_document" | "map_descriptor" => rfd::AsyncFileDialog::new().set_title("Open authoring document").add_filter("Authoring JSON", &["json"]).pick_file().await.map(|h| h.path().to_path_buf()),
        "render_model" => rfd::AsyncFileDialog::new().set_title("Select a native model to render").add_filter("GTA native model", &["ydr", "ydd", "yft"]).pick_file().await.map(|h| h.path().to_path_buf()),
        "render_textures" => rfd::AsyncFileDialog::new().set_title("Link a texture dictionary").add_filter("Texture dictionary", &["ytd"]).pick_file().await.map(|h| h.path().to_path_buf()),
        "blender_executable" => rfd::AsyncFileDialog::new().set_title("Locate Blender").add_filter("Blender executable", &["exe"]).pick_file().await.map(|h| h.path().to_path_buf()),
        "gxt2_source" => rfd::AsyncFileDialog::new().set_title("Open a loose GXT2 dictionary")
            .add_filter("GTA text dictionary", &["gxt2"]).pick_file().await.map(|h| h.path().to_path_buf()),
        "rpf_change_set" => rfd::AsyncFileDialog::new().set_title("Open RPF change set")
            .add_filter("RPF change set", &["json"]).pick_file().await.map(|h| h.path().to_path_buf()),
        "rpf_plan" => rfd::AsyncFileDialog::new().set_title("Open compiled RPF plan")
            .add_filter("Compiled RPF plan", &["json"]).pick_file().await.map(|h| h.path().to_path_buf()),
        "rpf_receipt" => rfd::AsyncFileDialog::new().set_title("Open RPF transaction receipt")
            .add_filter("RPF receipt", &["json"]).pick_file().await.map(|h| h.path().to_path_buf()),
        "rpf_payload" => rfd::AsyncFileDialog::new().set_title("Choose replacement or added file")
            .pick_file().await.map(|h| h.path().to_path_buf()),
        "rpf_authorized_root" => rfd::AsyncFileDialog::new().set_title("Choose the folder directly containing the workspace archive")
            .pick_folder().await.map(|h| h.path().to_path_buf()),
        "gxt2_workspace" => rfd::AsyncFileDialog::new().set_title("Open a GXT2 workspace")
            .pick_folder().await.map(|h| h.path().to_path_buf()),
        "gxt2_parent" => rfd::AsyncFileDialog::new().set_title("Choose the parent folder for the editable copy")
            .pick_folder().await.map(|h| h.path().to_path_buf()),
        "rpf_package_parent" => rfd::AsyncFileDialog::new().set_title("Choose where to create the new RPF package folder")
            .pick_folder().await.map(|h| h.path().to_path_buf()),
        "rpf_package_source" => rfd::AsyncFileDialog::new().set_title("Choose a verified RPF build folder containing rpf-package.json")
            .pick_folder().await.map(|h| h.path().to_path_buf()),
        "texture_workspace_parent" => rfd::AsyncFileDialog::new()
            .set_title("Choose where to create the editable texture workspace")
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "texture_workspace" => rfd::AsyncFileDialog::new()
            .set_title("Open editable YTD texture workspace")
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "texture_source" => rfd::AsyncFileDialog::new()
            .set_title("Choose DDS or raster texture")
            .add_filter(
                "Texture image",
                &["dds", "png", "jpg", "jpeg", "bmp", "tga", "webp"],
            )
            .pick_file()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "vehicle_authoring_parent" => rfd::AsyncFileDialog::new()
            .set_title("Choose where to create the editable vehicle workspace")
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "vehicle_authoring_workspace" => rfd::AsyncFileDialog::new()
            .set_title("Open editable vehicle workspace")
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "vehicle_package_parent" => rfd::AsyncFileDialog::new()
            .set_title("Choose where to build the validated vehicle package")
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "vehicle_skeleton" => rfd::AsyncFileDialog::new()
            .set_title("Open CodeWalker vehicle skeleton XML")
            .add_filter("CodeWalker model XML", &["xml"])
            .pick_file()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "rpf" => rfd::AsyncFileDialog::new()
            .set_title("Open loose RPF archive")
            .add_filter("Rockstar archive", &["rpf"])
            .pick_file()
            .await
            .map(|handle| handle.path().to_path_buf()),
        "gta_folder" => rfd::AsyncFileDialog::new()
            .set_title("Select GTA V installation")
            .pick_folder()
            .await
            .map(|handle| handle.path().to_path_buf()),
        _ => return Err(format!("path dialog kind is not allowlisted: {kind}")),
    };
    selected
        .map(|path| {
            path.canonicalize()
                .map(|resolved| resolved.to_string_lossy().into_owned())
                .map_err(|error| format!("selected path could not be canonicalized: {error}"))
        })
        .transpose()
}

fn report_file_name(suggested_name: &str) -> String {
    let candidate = Path::new(suggested_name)
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty() && name.len() <= 160 && !name.contains('\0'))
        .unwrap_or("allin1-link-report.md");
    let mut path = PathBuf::from(candidate);
    if path
        .extension()
        .and_then(|extension| extension.to_str())
        .is_none_or(|extension| !extension.eq_ignore_ascii_case("md"))
    {
        path.set_extension("md");
    }
    path.file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("allin1-link-report.md")
        .to_string()
}

fn model_build_file_name(suggested_name: &str, extension: &str) -> Result<String, String> {
    let extension = extension.trim_start_matches('.').to_ascii_lowercase();
    if !matches!(extension.as_str(), "ydr" | "ydd" | "yft") {
        return Err("model build extension must be ydr, ydd, or yft".to_string());
    }
    let candidate = Path::new(suggested_name)
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty() && name.len() <= 160 && !name.contains('\0'))
        .unwrap_or("allin1-model");
    let mut path = PathBuf::from(candidate);
    path.set_extension(&extension);
    Ok(path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("allin1-model.ydr")
        .to_string())
}

fn texture_build_file_name(suggested_name: &str) -> String {
    let stem = Path::new(suggested_name)
        .file_stem()
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty() && name.len() <= 156 && !name.contains('\0'))
        .unwrap_or("allin1-textures");
    format!("{stem}.ytd")
}

fn oiv_file_name(suggested_name: &str) -> String {
    let stem = Path::new(suggested_name).file_stem().and_then(|name| name.to_str())
        .map(|name| name.trim().trim_end_matches('.'))
        .filter(|name| !name.is_empty() && name.len() <= 140 &&
            name.chars().all(|c| c.is_alphanumeric() || " -_.".contains(c)))
        .unwrap_or("legacy-vehicle");
    format!("{stem}.oiv")
}

#[tauri::command]
async fn select_oiv_destination(suggested_name: String) -> Result<Option<String>, String> {
    let selected = rfd::AsyncFileDialog::new()
        .set_title("Export Legacy vehicle OIV — choose a new filename")
        .set_file_name(oiv_file_name(&suggested_name))
        .add_filter("Legacy vehicle OIV", &["oiv"])
        .save_file().await;
    selected.map(|handle| {
        let path = handle.path();
        let parent = path.parent().ok_or("OIV destination requires a parent directory")?
            .canonicalize().map_err(|error| format!("Could not resolve export directory: {error}"))?;
        if !parent.is_dir() { return Err("Export parent must be a directory".to_string()); }
        Ok(parent.join(oiv_file_name(path.file_name().and_then(|v| v.to_str()).unwrap_or_default()))
            .to_string_lossy().into_owned())
    }).transpose()
}

#[tauri::command]
async fn select_package_zip_destination(suggested_name: String) -> Result<Option<String>, String> {
    let selected = rfd::AsyncFileDialog::new()
        .set_title("Publish vehicle package ZIP — choose a new filename")
        .set_file_name(package_zip_file_name(&suggested_name))
        .add_filter("ALLIN1 vehicle package", &["zip"])
        .save_file().await;
    selected.map(|handle| {
        let path = handle.path();
        let parent = path.parent().ok_or("ZIP destination requires a parent directory")?
            .canonicalize().map_err(|error| format!("Could not resolve export directory: {error}"))?;
        if !parent.is_dir() { return Err("Export parent must be a directory".to_string()); }
        Ok(parent.join(package_zip_file_name(path.file_name().and_then(|v| v.to_str()).unwrap_or_default()))
            .to_string_lossy().into_owned())
    }).transpose()
}

fn package_zip_file_name(suggested_name: &str) -> String {
    Path::new(&oiv_file_name(suggested_name)).with_extension("zip").to_string_lossy().into_owned()
}

#[tauri::command]
async fn select_report_destination(suggested_name: String) -> Result<Option<String>, String> {
    let selected = rfd::AsyncFileDialog::new()
        .set_title("Export ALLIN1 inspection report")
        .set_file_name(report_file_name(&suggested_name))
        .add_filter("Markdown report", &["md"])
        .save_file()
        .await
        .map(|handle| handle.path().to_path_buf());
    selected
        .map(|path| {
            let file_name = report_file_name(
                path.file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or_default(),
            );
            let parent = path
                .parent()
                .ok_or("report destination must have a parent directory")?
                .canonicalize()
                .map_err(|error| format!("report directory could not be resolved: {error}"))?;
            if !parent.is_dir() {
                return Err("report destination parent must be a directory".to_string());
            }
            Ok(parent.join(file_name).to_string_lossy().into_owned())
        })
        .transpose()
}

#[tauri::command]
async fn select_model_build_destination(
    suggested_name: String,
    extension: String,
) -> Result<Option<String>, String> {
    let file_name = model_build_file_name(&suggested_name, &extension)?;
    let selected = rfd::AsyncFileDialog::new()
        .set_title("Build verified native model")
        .set_file_name(file_name)
        .add_filter("GTA V native model", &[extension.trim_start_matches('.')])
        .save_file()
        .await
        .map(|handle| handle.path().to_path_buf());
    selected
        .map(|path| {
            let file_name = model_build_file_name(
                path.file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or_default(),
                &extension,
            )?;
            let parent = path
                .parent()
                .ok_or("model build destination must have a parent directory")?
                .canonicalize()
                .map_err(|error| format!("model build directory could not be resolved: {error}"))?;
            if !parent.is_dir() {
                return Err("model build destination parent must be a directory".to_string());
            }
            Ok(parent.join(file_name).to_string_lossy().into_owned())
        })
        .transpose()
}

#[tauri::command]
async fn select_texture_build_destination(
    suggested_name: String,
) -> Result<Option<String>, String> {
    let file_name = texture_build_file_name(&suggested_name);
    let selected = rfd::AsyncFileDialog::new()
        .set_title("Build verified YTD texture dictionary")
        .set_file_name(file_name)
        .add_filter("GTA V texture dictionary", &["ytd"])
        .save_file()
        .await
        .map(|handle| handle.path().to_path_buf());
    selected
        .map(|path| {
            let file_name = texture_build_file_name(
                path.file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or_default(),
            );
            let parent = path
                .parent()
                .ok_or("texture build destination must have a parent directory")?
                .canonicalize()
                .map_err(|error| {
                    format!("texture build directory could not be resolved: {error}")
                })?;
            if !parent.is_dir() {
                return Err("texture build destination parent must be a directory".to_string());
            }
            Ok(parent.join(file_name).to_string_lossy().into_owned())
        })
        .transpose()
}

#[tauri::command]
fn initial_launch_request(state: State<'_, LaunchState>) -> Result<Option<LaunchRequest>, String> {
    state
        .0
        .lock()
        .map_err(|_| "launch request lock was poisoned".to_string())
        .map(|mut request| request.take())
}

#[tauri::command]
async fn restart_sidecar(manager: State<'_, Arc<SidecarManager>>) -> Result<Envelope, String> {
    let manager = manager.inner().clone();
    tauri::async_runtime::spawn_blocking(move || manager.restart())
        .await
        .map_err(|error| format!("sidecar restart task failed: {error}"))?
}

pub fn run() {
    let initial_cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let initial_args: Vec<String> = std::env::args().collect();
    let initial_launch = parse_launch_args(&initial_args, &initial_cwd);

    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, args, cwd| {
            let cwd = Path::new(&cwd);
            if let Some(request) = parse_launch_args(&args, cwd) {
                let _ = app.emit("launch-request", request);
            }
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .setup(move |app| {
            let manager = Arc::new(SidecarManager::new(app.handle().clone()));
            if let Err(error) = manager.ensure_started() {
                eprintln!("[ALLIN1 desktop] sidecar startup deferred: {error}");
            }
            app.manage(manager);
            app.manage(LaunchState(Mutex::new(initial_launch.clone())));
            app.manage(CloseState(AtomicBool::new(false)));
            if let Some(window) = app.get_webview_window("main") {
                window.show()?;
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let app = window.app_handle();
                if app.state::<CloseState>().0.load(Ordering::Acquire) {
                    let _ = window.emit("desktop-close-requested", ());
                } else {
                    // A renderer which has not started must still be closable.
                    let manager = app.state::<Arc<SidecarManager>>().inner().clone();
                    let window = window.clone();
                    tauri::async_runtime::spawn_blocking(move || {
                        if manager.try_shutdown().is_ok() { let _ = window.destroy(); }
                    });
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            desktop_handshake,
            desktop_catalog,
            desktop_execute,
            desktop_configure_assistant,
            desktop_apply_weapon_authoring,
            desktop_apply_ped_authoring,
            desktop_prepare_vehicle_quick_import,
            desktop_apply_vehicle_oiv_export,
            desktop_apply_vehicle_package_publish,
            desktop_apply_package_lifecycle,
            desktop_render_vehicle_model,
            desktop_vehicle_authoring_action,
            desktop_model_material_authoring_action,
            desktop_texture_authoring_action,
            desktop_start_job,
            desktop_cancel_job,
            desktop_check_update,
            select_path,
            select_report_destination,
            select_oiv_destination,
            select_package_zip_destination,
            select_model_build_destination,
            select_texture_build_destination,
            desktop_apply_workspace_action,
            desktop_apply_gxt2_action, select_gxt2_build_destination,
            desktop_apply_rpf_change_set, select_rpf_plan_destination,
            desktop_apply_rpf_transaction,
            desktop_apply_rpf_utility, select_rpf_utility_destination,
            initial_launch_request,
            restart_sidecar,
            desktop_frontend_ready, desktop_close,
        ]);

    builder
        .run(tauri::generate_context!())
        .expect("error while running the ALLIN1 SDK Tauri application");
}

#[cfg(test)]
mod tests {
    use super::{
        model_build_file_name, report_file_name, rpf_utility_file_name,
        texture_build_file_name, oiv_file_name,
        package_zip_file_name,
        validated_model_material_authoring_operation, validated_texture_authoring_operation,
        validated_vehicle_authoring_operation,
    };

    #[test]
    fn package_zip_names_and_native_export_permissions_are_declared() {
        assert_eq!(package_zip_file_name("vehicle.enhanced.1.0.zip"), "vehicle.enhanced.1.0.zip");
        assert_eq!(package_zip_file_name("../package.oiv"), "package.zip");
        let build = include_str!("../build.rs");
        let capability: serde_json::Value = serde_json::from_str(include_str!("../capabilities/desktop-main.json")).unwrap();
        let permissions = capability["permissions"].as_array().unwrap();
        for command in ["desktop_apply_vehicle_oiv_export", "select_oiv_destination",
                        "desktop_apply_vehicle_package_publish", "select_package_zip_destination",
                        "desktop_apply_workspace_action",
                        "desktop_apply_gxt2_action", "select_gxt2_build_destination",
                        "desktop_apply_rpf_change_set", "select_rpf_plan_destination", "desktop_apply_rpf_transaction",
                        "desktop_apply_rpf_utility", "select_rpf_utility_destination"] {
            assert!(build.contains(&format!("\"{command}\"")));
            assert!(permissions.contains(&serde_json::Value::String(format!("allow-{}", command.replace('_', "-")))));
        }
    }

    #[test]
    fn every_frontend_command_is_generated_and_permitted() {
        let build = include_str!("../build.rs");
        let capability: serde_json::Value =
            serde_json::from_str(include_str!("../capabilities/desktop-main.json")).unwrap();
        let permissions = capability["permissions"].as_array().unwrap();
        let commands = [
            "desktop_handshake", "desktop_catalog", "desktop_execute",
            "desktop_configure_assistant", "desktop_apply_weapon_authoring",
            "desktop_apply_ped_authoring", "desktop_prepare_vehicle_quick_import",
            "desktop_apply_vehicle_oiv_export", "desktop_apply_vehicle_package_publish",
            "desktop_apply_package_lifecycle", "desktop_render_vehicle_model",
            "desktop_vehicle_authoring_action", "desktop_model_material_authoring_action",
            "desktop_texture_authoring_action", "desktop_start_job", "desktop_cancel_job",
            "desktop_check_update", "select_path", "select_report_destination",
            "select_oiv_destination", "select_package_zip_destination",
            "select_model_build_destination", "select_texture_build_destination",
            "desktop_apply_workspace_action", "desktop_apply_gxt2_action",
            "select_gxt2_build_destination", "desktop_apply_rpf_change_set",
            "select_rpf_plan_destination", "desktop_apply_rpf_transaction",
            "desktop_apply_rpf_utility", "select_rpf_utility_destination",
            "initial_launch_request", "restart_sidecar",
        ];
        for command in commands {
            assert!(build.contains(&format!("\"{command}\"")),
                "{command} is missing from the generated Tauri command manifest");
            assert!(permissions.contains(&serde_json::Value::String(
                format!("allow-{}", command.replace('_', "-")))),
                "{command} is missing from the main-window capability");
        }
    }

    #[test]
    fn oiv_names_are_bounded_to_oiv_files() {
        assert_eq!(oiv_file_name("lunga.legacy.oiv"), "lunga.legacy.oiv");
        assert_eq!(oiv_file_name("../nested/lunga.zip"), "lunga.oiv");
        assert_eq!(oiv_file_name("..."), "legacy-vehicle.oiv");
        assert_eq!(oiv_file_name(""), "legacy-vehicle.oiv");
        assert_eq!(oiv_file_name("evil?.oiv"), "legacy-vehicle.oiv");
    }

    #[test]
    fn report_names_are_bounded_to_markdown_files() {
        assert_eq!(
            report_file_name("sample-link-report.md"),
            "sample-link-report.md"
        );
        assert_eq!(report_file_name("sample.txt"), "sample.md");
        assert_eq!(report_file_name("../nested/report.md"), "report.md");
        assert_eq!(report_file_name(""), "allin1-link-report.md");
    }

    #[test]
    fn rpf_utility_names_are_bounded_and_preserve_member_extensions() {
        assert_eq!(rpf_utility_file_name("x64/data/global.gxt2", "", "fallback"), "global.gxt2");
        assert_eq!(rpf_utility_file_name("source-diff.rpf", "json", "fallback"), "source-diff.json");
        assert_eq!(rpf_utility_file_name("../CON", "", "fallback"), "fallback");
        assert_eq!(rpf_utility_file_name("evil?.bin", "", "fallback"), "fallback");
    }

    #[test]
    fn model_build_names_are_bounded_to_native_extensions() {
        assert_eq!(
            model_build_file_name("comet6.ydr", "ydr").unwrap(),
            "comet6.ydr"
        );
        assert_eq!(
            model_build_file_name("../nested/comet6.txt", ".yft").unwrap(),
            "comet6.yft"
        );
        assert!(model_build_file_name("comet6.ydr", "rpf").is_err());
    }

    #[test]
    fn texture_build_names_are_bounded_to_ytd() {
        assert_eq!(texture_build_file_name("comet6.ytd"), "comet6.ytd");
        assert_eq!(
            texture_build_file_name("../nested/comet6.txt"),
            "comet6.ytd"
        );
        assert_eq!(texture_build_file_name(""), "allin1-textures.ytd");
    }

    #[test]
    fn vehicle_authoring_command_allows_only_guarded_mutations() {
        assert_eq!(
            validated_vehicle_authoring_operation("apply_vehicle_authoring_edit").unwrap(),
            "apply_vehicle_authoring_edit"
        );
        assert_eq!(
            validated_vehicle_authoring_operation("apply_vehicle_authoring_appearance").unwrap(),
            "apply_vehicle_authoring_appearance"
        );
        assert_eq!(
            validated_vehicle_authoring_operation("apply_vehicle_authoring_tuning").unwrap(),
            "apply_vehicle_authoring_tuning"
        );
        assert_eq!(
            validated_vehicle_authoring_operation("apply_vehicle_authoring_light_profile").unwrap(),
            "apply_vehicle_authoring_light_profile"
        );
        assert_eq!(
            validated_vehicle_authoring_operation("apply_vehicle_authoring_axles").unwrap(),
            "apply_vehicle_authoring_axles"
        );
        assert_eq!(
            validated_vehicle_authoring_operation("apply_vehicle_authoring_transmission").unwrap(),
            "apply_vehicle_authoring_transmission"
        );
        assert_eq!(
            validated_vehicle_authoring_operation("apply_vehicle_authoring_distribution").unwrap(),
            "apply_vehicle_authoring_distribution"
        );
        assert_eq!(
            validated_vehicle_authoring_operation("apply_vehicle_package_build").unwrap(),
            "apply_vehicle_package_build"
        );
        assert!(validated_vehicle_authoring_operation("inspect_package").is_err());
        assert!(validated_vehicle_authoring_operation("execute").is_err());
    }

    #[test]
    fn model_material_authoring_command_allows_only_guarded_mutations() {
        for operation in [
            "create_model_material_workspace",
            "apply_model_material_edit",
            "apply_model_material_history",
            "apply_model_material_build",
        ] {
            assert_eq!(
                validated_model_material_authoring_operation(operation).unwrap(),
                operation
            );
        }
        assert!(validated_model_material_authoring_operation("inspect_package").is_err());
        assert!(validated_model_material_authoring_operation("execute").is_err());
    }

    #[test]
    fn texture_authoring_command_allows_only_guarded_mutations() {
        for operation in [
            "create_texture_workspace",
            "apply_texture_edit",
            "apply_texture_history",
            "apply_texture_build",
        ] {
            assert_eq!(
                validated_texture_authoring_operation(operation).unwrap(),
                operation
            );
        }
        assert!(validated_texture_authoring_operation("preview_texture_workspace").is_err());
        assert!(validated_texture_authoring_operation("execute").is_err());
    }
}
