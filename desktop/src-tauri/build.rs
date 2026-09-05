const COMMANDS: &[&str] = &[
    "desktop_frontend_ready", "desktop_close",
    "desktop_apply_rpf_change_set", "select_rpf_plan_destination",
    "desktop_apply_rpf_transaction",
    "desktop_apply_rpf_utility", "select_rpf_utility_destination",
    "desktop_apply_workspace_action",
    "desktop_apply_gxt2_action", "select_gxt2_build_destination",
    "desktop_handshake",
    "desktop_catalog",
    "desktop_execute",
    "desktop_configure_assistant",
    "desktop_apply_weapon_authoring",
    "desktop_apply_ped_authoring",
    "desktop_prepare_vehicle_quick_import",
    "desktop_apply_vehicle_oiv_export",
    "desktop_apply_vehicle_package_publish",
    "desktop_apply_package_lifecycle",
    "desktop_render_vehicle_model",
    "desktop_vehicle_authoring_action",
    "desktop_model_material_authoring_action",
    "desktop_texture_authoring_action",
    "desktop_start_job",
    "desktop_cancel_job",
    "desktop_check_update",
    "select_path",
    "select_report_destination",
    "select_oiv_destination",
    "select_package_zip_destination",
    "select_model_build_destination",
    "select_texture_build_destination",
    "initial_launch_request",
    "restart_sidecar",
];

fn main() {
    if std::env::var("PROFILE").as_deref() == Ok("release") {
        assert!(!tauri_build::is_dev(), "Release builds must enable tauri/custom-protocol to embed the frontend");
        assert!(std::env::var_os("ALLIN1_BUILD_IDENTITY_FILE").is_some(), "Release builds require a candidate build identity");
    }
    println!("cargo:rerun-if-env-changed=ALLIN1_BUILD_IDENTITY_FILE");
    if let Ok(path) = std::env::var("ALLIN1_BUILD_IDENTITY_FILE") {
        println!("cargo:rerun-if-changed={path}");
        let identity = std::fs::read_to_string(path).expect("missing candidate build identity");
        assert!(!identity.contains(['\n', '\r']), "build identity must be single-line JSON");
        println!("cargo:rustc-env=ALLIN1_BUILD_IDENTITY_JSON={identity}");
    }
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("failed to build ALLIN1 desktop manifest");
}
