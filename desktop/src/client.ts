import { Channel, invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type {
  DesktopCatalog,
  DesktopClient,
  Envelope,
  JobStart,
  LaunchRequest,
  UpdateResult,
} from "./types";

export const tauriClient: DesktopClient = {
  onCloseRequested: async handler => {
    const unlisten = await listen("desktop-close-requested", handler);
    try { await invoke("desktop_frontend_ready"); }
    catch (error) { unlisten(); throw error; }
    return unlisten;
  },
  closeWindow: () => invoke<void>("desktop_close"),
  applyWorkspaceAction: payload => invoke<Envelope>("desktop_apply_workspace_action", { payload }),
  applyRpfChangeSet: payload => invoke<Envelope>("desktop_apply_rpf_change_set", { payload }),
  applyRpfTransaction: payload => invoke<Envelope>("desktop_apply_rpf_transaction", { payload }),
  applyRpfUtility: payload => invoke<Envelope>("desktop_apply_rpf_utility", { payload }),
  selectRpfPlanDestination: suggestedName => invoke<string | null>("select_rpf_plan_destination", { suggestedName }),
  selectRpfUtilityDestination: (action, suggestedName) => invoke<string | null>("select_rpf_utility_destination", { action, suggestedName }),
  applyGxt2Action: payload => invoke<Envelope>("desktop_apply_gxt2_action", { payload }),
  selectGxt2BuildDestination: suggestedName => invoke<string | null>("select_gxt2_build_destination", { suggestedName }),
  handshake: () => invoke<Envelope>("desktop_handshake"),
  catalog: async () => {
    const response = await invoke<Envelope<DesktopCatalog>>("desktop_catalog");
    return response.payload;
  },
  execute: (command, args, authoringConfirmed = false) =>
    invoke<Envelope>("desktop_execute", { command, args, authoringConfirmed }),
  configureAssistant: (payload) => invoke<Envelope>("desktop_configure_assistant", { payload }),
  applyWeaponAuthoring: (payload) => invoke<Envelope>("desktop_apply_weapon_authoring", { payload }),
  applyPedAuthoring: (payload) => invoke<Envelope>("desktop_apply_ped_authoring", { payload }),
  prepareVehicleQuickImport: (payload) =>
    invoke<Envelope>("desktop_prepare_vehicle_quick_import", { payload }),
  applyVehicleOivExport: (payload) => invoke<Envelope>("desktop_apply_vehicle_oiv_export", { payload }),
  applyVehiclePackagePublish: (payload) => invoke<Envelope>("desktop_apply_vehicle_package_publish", { payload }),
  applyPackageLifecycle: (payload) =>
    invoke<Envelope>("desktop_apply_package_lifecycle", { payload }),
  renderVehicleModel: (payload) =>
    invoke<Envelope>("desktop_render_vehicle_model", { payload }),
  vehicleAuthoringAction: (operation, payload) =>
    invoke<Envelope>("desktop_vehicle_authoring_action", { operation, payload }),
  modelMaterialAuthoringAction: (operation, payload) =>
    invoke<Envelope>("desktop_model_material_authoring_action", { operation, payload }),
  textureAuthoringAction: (operation, payload) =>
    invoke<Envelope>("desktop_texture_authoring_action", { operation, payload }),
  startJob: (operation, payload, revision, onEvent) => {
    const channel = new Channel<Envelope>();
    channel.onmessage = onEvent;
    return invoke<JobStart>("desktop_start_job", {
      operation,
      payload,
      revision,
      onEvent: channel,
    });
  },
  cancelJob: (jobId) => invoke<Envelope>("desktop_cancel_job", { jobId }),
  selectPath: (kind) => invoke<string | null>("select_path", { kind }),
  selectReportDestination: (suggestedName) =>
    invoke<string | null>("select_report_destination", { suggestedName }),
  selectOivDestination: (suggestedName) => invoke<string | null>("select_oiv_destination", { suggestedName }),
  selectPackageZipDestination: (suggestedName) => invoke<string | null>("select_package_zip_destination", { suggestedName }),
  selectModelBuildDestination: (suggestedName, extension) =>
    invoke<string | null>("select_model_build_destination", { suggestedName, extension }),
  selectTextureBuildDestination: (suggestedName) =>
    invoke<string | null>("select_texture_build_destination", { suggestedName }),
  exportLinkReport: (source, destination) =>
    invoke<Envelope>("desktop_execute", {
      command: "link",
      args: [source, "--output", destination, "--allow-failing-report"],
      authoringConfirmed: true,
    }),
  exportRecipeReport: (source, destination) =>
    invoke<Envelope>("desktop_execute", {
      command: "oiv-plan",
      args: [source, "--output", destination],
      authoringConfirmed: true,
    }),
  initialLaunchRequest: () => invoke<LaunchRequest | null>("initial_launch_request"),
  onLaunchRequest: async (handler) => {
    const unlisten = await listen<LaunchRequest>("launch-request", (event) => {
      handler(event.payload);
    });
    return unlisten;
  },
  checkUpdate: () => invoke<UpdateResult>("desktop_check_update"),
  restartSidecar: () => invoke<Envelope>("restart_sidecar"),
  onSidecarStatus: async (handler) => {
    const unlisten = await listen<string>("sidecar-status", (event) => {
      handler(event.payload);
    });
    return unlisten;
  },
};
