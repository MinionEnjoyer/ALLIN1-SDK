import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import { createPreviewClient } from "./previewClient";
import { weaponPreviewSnapshot } from "./weaponPreview";
import type { DesktopCatalog, DesktopClient, Envelope } from "./types";

const catalog: DesktopCatalog = {
  navigation: [
    { id: "linker", label: "Package Linker", shortcut: "Ctrl+1", phase: 3 },
    { id: "assets", label: "Asset Viewer", shortcut: "Ctrl+2", phase: 4 },
    { id: "workbench", label: "Content Workbench", shortcut: "Ctrl+3", phase: 3 },
    { id: "receipts", label: "Package Receipts", shortcut: "Ctrl+8", phase: 4 },
    { id: "quick_import", label: "Quick Import", shortcut: "Ctrl+I", phase: 4 },
    { id: "models", label: "Models & Materials", shortcut: "Ctrl+4", phase: 5 },
    { id: "rpf", label: "RPF Archives", shortcut: "Ctrl+5", phase: 3 },
    { id: "recipes", label: "Package Recipes", shortcut: "Ctrl+6", phase: 4 },
    { id: "help", label: "Help Center", shortcut: "Ctrl+7", phase: 3 },
  ],
  commands: [{ name: "validate", description: "Validate a manifest", risk: "read_only", parameters: [] }],
  help_topics: [{ key: "getting-started", category: "Start here", title: "Getting started", summary: "Open a package.", body: "Choose a source and inspect it.", keywords: ["package"] }],
  operations: [],
  job_operations: [],
};

function response(payload: Record<string, unknown> = {}): Envelope {
  return { protocol_version: "1.0.0", request_id: "test", job_id: null, operation: "result", payload, sequence: 0, risk: "none", terminal: true };
}

function mockClient(): DesktopClient {
  return {
    onCloseRequested: vi.fn(async () => () => undefined),
    closeWindow: vi.fn(async () => undefined),
    applyRpfChangeSet: vi.fn(async () => response({ result: {} })),
    applyRpfTransaction: vi.fn(async () => response({ result: {} })),
    applyRpfUtility: vi.fn(async () => response({ result: {} })),
    selectRpfPlanDestination: vi.fn(async () => "C:\\SDK\\exports\\rpf-plan.json"),
    selectRpfUtilityDestination: vi.fn(async (_action, name) => `C:\\SDK\\exports\\${name}`),
    handshake: vi.fn(async () => response({ sdk_version: "0.6.3" })),
    catalog: vi.fn(async () => catalog),
    execute: vi.fn(async () => response({ result: { output: "PASS" } })),
    configureAssistant: vi.fn(async () => response({ result: { kind: "assistant_configuration" } })),
    applyWeaponAuthoring: vi.fn(async () => response({ result: {} })),
    applyPedAuthoring: vi.fn(async () => response({ result: {} })),
    startJob: vi.fn(async (operation, payload, revision, onEvent) => {
      const result = operation === "inspect_weapon_workbench" ? weaponPreviewSnapshot() : operation === "review_vehicle_quick_import" ? {
        kind: "vehicle_quick_import_review",
        operation: "review_vehicle_quick_import",
        plan: {
          source: "C:\\Mods\\Demo",
          source_kind: "archive",
          source_package_sha256: "package123",
          edition: String(payload.edition ?? "enhanced"),
          source_member: `install/${String(payload.edition ?? "enhanced")}/${String(payload.edition) === "legacy" ? "blista" : "comet"}/dlc.rpf`,
          source_member_size: 2048,
          source_member_sha256: "member123",
          package_id: String(payload.package_id ?? (String(payload.edition) === "legacy" ? "vehicle.blista" : "vehicle.comet6")),
          name: String(payload.name ?? (String(payload.edition) === "legacy" ? "Blista Package" : "Comet S2 Package")),
          version: String(payload.version ?? "1.0.0"),
          dlc_pack: String(payload.edition) === "legacy" ? "blista" : "comet6",
          destination: `mods/update/x64/dlcpacks/${String(payload.edition) === "legacy" ? "blista" : "comet6"}/dlc.rpf`,
          catalog: {
            schema_version: 1,
            id: String(payload.edition) === "legacy" ? "vehicle.blista" : "vehicle.comet6",
            name: String(payload.edition) === "legacy" ? "Blista Package" : "Comet S2 Package",
            vehicles: [{
              model: String(payload.edition) === "legacy" ? "blista" : "comet6",
              name: String((payload.updates as Record<string, Record<string, unknown>> | undefined)?.[String(payload.edition) === "legacy" ? "blista" : "comet6"]?.name ?? (String(payload.edition) === "legacy" ? "Blista" : "Comet S2")),
              manufacturer: String(payload.edition) === "legacy" ? "Dinka" : "Pfister",
              category: String(payload.edition) === "legacy" ? "compacts" : "sports",
              price: 185000,
              storage: "garage",
              source_pack: String(payload.edition) === "legacy" ? "blista" : "comet6",
              size_tier: 0,
              traffic: { enabled: false, weight: 1.0 },
            }],
          },
        },
        warnings: [],
        acknowledged_free_models: [],
        destination_preview: `C:\\Users\\Test\\ALLIN1\\Packages\\${String(payload.edition) === "legacy" ? "vehicle.blista" : "vehicle.comet6"}`,
        destination_review: {
          state: "new",
          exists: false,
          replaceable: true,
          message: "A new Launcher package will be created.",
        },
        review_sha256: "0b950bd7c61b98a17d09dd54d08fab849789aa55049d1225820b63ca4e21e4a8",
        vehicle_count: 1,
        warning_count: 0,
        review_only: true,
        game_write_performed: false,
        package_write_performed: false,
      } : operation === "review_package_lifecycle" ? {
        kind: "package_lifecycle_review",
        operation: "review_package_lifecycle",
        action: String(payload.action),
        source: payload.action === "install" ? String(payload.source) : null,
        gta_path: String(payload.gta_path),
        ready: true,
        package: payload.action === "install"
          ? { id: "camera-tools", name: "Camera Tools", version: "2.1.0", type: "script" }
          : { id: "allin1.demo", name: "Demo Package", version: "1.0.0", type: "mixed" },
        target_edition: "enhanced",
        replacing: false,
        installed_version: null,
        current_enabled: payload.action === "disable" ? true : payload.action === "enable" ? false : undefined,
        target_enabled: payload.action === "disable" ? false : payload.action === "enable" ? true : undefined,
        operations: [{ kind: "file", destination: payload.action === "install" ? "scripts/CameraTools.dll" : "scripts/Demo.asi", disposition: payload.action === "install" ? "create" : payload.action === "uninstall" ? "remove" : payload.action === "enable" ? "enable_file" : "disable_file" }],
        findings: [],
        rollback: payload.action === "install" ? { backup_count: 0, receipt_created: true } : payload.action === "uninstall" ? { restore_count: 0, receipt_removed: true } : { receipt_state_restored: true, loose_move_count: 1, extension_registry_rebuilt: true },
        review_sha256: "f241d8f31ce5486ff72bf8f70064ec51f76cbf6420e61b9cc4838d63f254a7a8",
        review_only: true,
        game_write_required: true,
        game_write_performed: false,
      } : operation === "inspect_vehicle_project" ? {
        kind: "vehicle_project_inspection",
        operation: "inspect_vehicle_project",
        source: "C:\\Mods\\Demo",
        source_kind: "folder",
        gta_path: "C:\\Mods\\Demo",
        edition: "enhanced",
        inventory_fingerprint: "vehicle-project-fixture",
        models: [{
          model: "comet6",
          display_name: "Comet S2",
          make_name: "Pfister",
          vehicle_class: "Sports",
          vehicle_type: "Automobile",
          handling_id: "COMET6",
          layout: "LAYOUT_LOW",
          audio_name_hash: "comet2",
          texture_dictionary: "comet6",
          tuning_kits: ["comet6_modkit"],
          assets: [
            { role: "primary_model", path: "x64/vehicles/comet6.yft", size: 2048, required: true, previewable: true },
            { role: "vehicles_meta", path: "common/data/vehicles.meta", size: 512, required: true, previewable: false },
          ],
          findings: [],
          primary_model: "x64/vehicles/comet6.yft",
          high_detail_model: null,
          texture_asset: null,
          collision_asset: null,
          ready_for_preview: true,
          complete: true,
          asset_count: 2,
          finding_count: 0,
          assets_truncated: false,
          findings_truncated: false,
        }],
        findings: [],
        axle_configurations: [],
        model_count: 1,
        returned_model_count: 1,
        asset_count: 2,
        returned_asset_count: 2,
        previewable_count: 1,
        complete_count: 1,
        error_count: 0,
        warning_count: 0,
        model_finding_count: 0,
        truncated: false,
        read_only: true,
        package_write_performed: false,
        game_write_performed: false,
      } : operation === "inspect_rpf_archive" ? {
        kind: "rpf_archive_index",
        operation: "inspect_rpf_archive",
        source: "C:\\Mods\\Demo\\update.rpf",
        gta_path: "C:\\Mods\\Demo",
        edition: "enhanced",
        archive_size: 4096,
        archives: [
          { path: "", name: "update.rpf", version: 7, encryption: "none", size: 4096, entry_count: 3 },
          { path: "x64/data.rpf", name: "data.rpf", version: 7, encryption: "none", size: 2048, entry_count: 1 },
        ],
        entries: [
          { id: "::common", archive_path: "", path: "common", name: "common", kind: "directory", size: 0, stored_size: 0, encrypted: null, compressed: null, resource_version: null },
          { id: "::common/data/handling.meta", archive_path: "", path: "common/data/handling.meta", name: "handling.meta", kind: "binary", size: 512, stored_size: 320, encrypted: false, compressed: true, resource_version: null },
          { id: "::x64/data.rpf", archive_path: "", path: "x64/data.rpf", name: "data.rpf", kind: "archive", size: 2048, stored_size: 2048, encrypted: false, compressed: false, resource_version: null },
          { id: "x64/data.rpf::textures/vehicle.ytd", archive_path: "x64/data.rpf", path: "textures/vehicle.ytd", name: "vehicle.ytd", kind: "resource", size: 1024, stored_size: 800, encrypted: false, compressed: true, resource_version: 13 },
        ],
        warnings: [],
        suffix_counts: { ".meta": 1, ".rpf": 1, ".ytd": 1 },
        archive_count: 2,
        entry_count: 4,
        returned_entry_count: 4,
        directory_count: 1,
        file_count: 3,
        logical_bytes: 3584,
        stored_bytes: 3168,
        truncated: false,
        read_only: true,
        game_write_performed: false,
      } : operation === "inspect_package_receipts" ? {
        kind: "package_receipt_inventory",
        operation: "inspect_package_receipts",
        gta_path: String(payload.gta_path),
        edition: "enhanced",
        receipt_root: `${String(payload.gta_path)}\\scripts\\.allin1\\mods`,
        packages: [{ mod_id: "allin1.demo", name: "Demo Package", version: "1.0.0", mod_type: "mixed", enabled: true }],
        selected_id: payload.selected_id ? "allin1.demo" : null,
        receipt: payload.selected_id ? { id: "allin1.demo", name: "Demo Package", version: "1.0.0", type: "mixed", enabled: true, installed_at: "2026-08-29T18:42:11+00:00", files: [{ destination: "scripts/Demo.asi" }], rpf_entries: [] } : null,
        verification: payload.selected_id ? { package_id: "allin1.demo", version: "1.0.0", enabled: true, healthy: true, ownership_verified: true, checks: [{ kind: "file", destination: "scripts/Demo.asi", exists: true, hash_recorded: true, hash_matches: true, backup_present: null }], issues: [] } : null,
        package_count: 1,
        enabled_count: 1,
        check_count: payload.selected_id ? 1 : 0,
        issue_count: 0,
        read_only: true,
        game_write_performed: false,
      } : operation === "inspect_vehicle_quick_import" ? {
        kind: "vehicle_quick_import_inspection",
        operation: "inspect_vehicle_quick_import",
        source: "C:\\Mods\\Demo",
        source_kind: "archive",
        available_editions: ["legacy", "enhanced"],
        suggested_edition: "enhanced",
        edition_basis: "package_branches",
        vehicles: [
          { model: "comet6", edition: "enhanced", display_name: "Comet S2", manufacturer: "Pfister", vehicle_class: "Sports" },
          { model: "blista", edition: "legacy", display_name: "Blista", manufacturer: "Dinka", vehicle_class: "Compacts" },
        ],
        errors: 0,
        warnings: 1,
        branch_count: 2,
        vehicle_count: 2,
        game_write_performed: false,
        package_write_performed: false,
      } : operation === "preview_asset" ? {
        source: "C:\\Mods\\Demo",
        path: String(payload.entry),
        name: String(payload.entry),
        category: "Archives",
        preview_kind: "binary",
        display_kind: "text",
        size: 2048,
        bytes_read: 256,
        truncated: true,
        sha256: null,
        text: "Binary asset. The viewer displays a bounded header.",
        text_truncated: false,
        artifact: null,
        metadata: { format: "Rockstar archive" },
        warnings: [],
      } : operation === "inspect_recipe" ? {
        kind: "recipe_plan",
        source: "C:\\Mods\\Demo",
        name: "Desktop Recipe",
        version: "1.4",
        author: "ALLIN1 Test",
        format_version: "2.2",
        editions: ["enhanced"],
        assembly_sha256: "abc123",
        readiness: "existing_rpf_compile_ready",
        readiness_label: "EXISTING RPF COMPILE READY",
        operation_count: 2,
        error_count: 0,
        warning_count: 1,
        recipe_supported: true,
        translatable: false,
        managed_exportable: false,
        rpf_recipe_compilable: true,
        operations: [
          { number: 1, kind: "add", source: "content/test.meta", target: "common/data/test.meta", archives: ["mods/update/update.rpf"], supported: true, detail: "Add exact metadata.", creates_archive: false, edits: [] },
          { number: 2, kind: "xml", source: "", target: "common/data/dlclist.xml", archives: ["mods/update/update.rpf"], supported: true, detail: "Append registration.", creates_archive: false, edits: [{ action: "add" }] },
        ],
        findings: [{ severity: "warning", code: "target_archive_required", operation: 1, message: "Select the matching Enhanced archive before compile." }],
      } : operation === "execute" ? {
        output: "RPF archive inspection complete\nSignature: RPF7\nNo archive content was executed.",
      } : {
        kind: "package_scan",
        source: "C:\\Mods\\Demo",
        valid: true,
        error_count: 0,
        warning_count: 1,
        file_count: 2,
        inventory_count: 2,
        total_bytes: 2048,
        entries: [{ path: "dlc.rpf", size: 2048, category: "Archives", preview_kind: "binary" }],
        findings: [{ severity: "warning", code: "edition", message: "Confirm edition" }],
      };
      queueMicrotask(() => onEvent({
        protocol_version: "1.0.0",
        request_id: "job-request",
        job_id: "job-ui",
        operation: "result",
        payload: {
          revision,
          result,
        },
        sequence: 1,
        risk: "read_only",
        terminal: true,
      }));
      return { job_id: "job-ui", accepted: response() };
    }),
    cancelJob: vi.fn(async () => response()),
    applyVehicleOivExport: vi.fn(async () => response()),
    applyVehiclePackagePublish: vi.fn(async () => response()),
    selectPackageZipDestination: vi.fn(async () => "C:\\Exports\\vehicle.zip"),
    selectOivDestination: vi.fn(async () => "C:\\Exports\\vehicle.oiv"),
    prepareVehicleQuickImport: vi.fn(async (payload) => response({ result: {
      kind: "vehicle_quick_import_prepared",
      operation: "prepare_vehicle_quick_import",
      review_sha256: String(payload.review_sha256),
      game_write_performed: false,
      package_write_performed: true,
      launcher_install_required: true,
      launcher_library: true,
      replaced_existing: false,
      package: { package_root: "C:\\Users\\Test\\ALLIN1\\Packages\\vehicle.comet6" },
      published: null,
      warnings: [],
    } })),
    applyPackageLifecycle: vi.fn(async (payload) => response({ result: {
      kind: "package_lifecycle_execution",
      operation: "apply_package_lifecycle",
      action: String(payload.action),
      status: ({ install: "installed", uninstall: "uninstalled", enable: "enabled", disable: "disabled" } as Record<string, string>)[String(payload.action)],
      source: payload.action === "install" ? String(payload.source) : null,
      gta_path: String(payload.gta_path),
      package: payload.action === "install"
        ? { id: "camera-tools", name: "Camera Tools", version: "2.1.0", type: "script" }
        : { id: "allin1.demo", name: "Demo Package", version: "1.0.0", type: "mixed" },
      review_sha256: String(payload.review_sha256),
      process_check: { gta_closed: true, running_processes: [] },
      postcondition: payload.action === "install" ? { installed: true, enabled: true } : payload.action === "uninstall" ? { installed: false, receipt_present: false } : { installed: true, enabled: payload.action === "enable" },
      rollback: payload.action === "install"
        ? { receipt_written: true, ownership_verified: true, backup_count: 0, rpf_entry_count: 0 }
        : payload.action === "uninstall"
          ? { receipt_removed: true, restored_backup_count: 0, removed_payload_count: 1, extension_registry_rebuilt: true }
          : { receipt_state_updated: true, ownership_verified: true, loose_move_count: 1, extension_registry_rebuilt: true },
      game_write_confirmed: true,
      game_write_performed: true,
    } })),
    renderVehicleModel: vi.fn(async (payload) => response({ result: {
      kind: "vehicle_model_viewport",
      source: String(payload.source),
      path: String(payload.entry),
      name: "comet6.yft",
      size: 2048,
      bytes_read: 2048,
      sha256: "0fc4711ea23719a55f2243c1b5ed7a6b8e4970644a2395971881818eb847d03d",
      edition: "enhanced",
      artifact: { path: "C:\\Cache\\frame.png", preview_url: "/asset-preview-fixture.svg", sha256: "5e90ef15393073d127f69f58c4c5900dc4b39dbf69f78d59ff3b12111e79937f", size: 1024, media_type: "image/png", width: 960, height: 680 },
      camera: { yaw: Number(payload.yaw), pitch: Number(payload.pitch), lod: String(payload.lod), component: String(payload.component), material: String(payload.material), render_mode: String(payload.render_mode), quality: String(payload.quality) },
      scene: { lods: ["High", "Medium"], components: [{ name: "Chassis", lod: "High", geometry_count: 8, vertex_count: 24000, triangle_count: 32000, material_names: ["vehicle_paint1"], texture_names: ["comet6"] }], materials: [{ index: 0, name: "vehicle_paint1", record_count: 1, geometry_count: 8, triangle_count: 32000, lods: ["High"], components: ["Chassis"], texture_bindings: [{ slot: "DiffuseSampler", name: "comet6", resolved: null }] }], component_count: 1, material_count: 4, surface_count: 1, bone_count: 126 },
      metadata: { model_rendered_triangles: payload.quality === "interactive" ? 6000 : 32000 },
      texture_dictionary: null,
      warnings: [],
      cache_hit: true,
      read_only: true,
      workspace_write_performed: false,
      package_write_performed: false,
      game_write_performed: false,
    } })),
    vehicleAuthoringAction: vi.fn(async () => response({ result: {} })),
    modelMaterialAuthoringAction: vi.fn(async () => response({ result: {} })),
    textureAuthoringAction: vi.fn(async () => response({ result: {} })),
    applyWorkspaceAction: vi.fn(async () => response({ result: {} })),
    applyGxt2Action: vi.fn(async () => response({ result: {} })),
    selectGxt2BuildDestination: vi.fn(async () => "C:\\Exports\\global.gxt2"),
    selectPath: vi.fn(async (kind) => kind === "rpf" ? "C:\\Mods\\Demo\\update.rpf" : "C:\\Mods\\Demo"),
    selectReportDestination: vi.fn(async () => "C:\\Reports\\demo-link-report.md"),
    selectModelBuildDestination: vi.fn(async () => "C:\\Exports\\comet6.yft"),
    selectTextureBuildDestination: vi.fn(async () => "C:\\Exports\\comet6.ytd"),
    exportLinkReport: vi.fn(async () => response({ result: { output: "Report written" } })),
    exportRecipeReport: vi.fn(async () => response({ result: { output: "Recipe report written" } })),
    initialLaunchRequest: vi.fn(async () => null),
    onLaunchRequest: vi.fn(async () => () => undefined),
    checkUpdate: vi.fn(async () => ({ current_version: "0.6.3", latest_version: "0.6.3", update_available: false, name: "ALLIN1", page_url: "https://example.invalid", archive_name: "sdk.zip", archive_size: 1 })),
    restartSidecar: vi.fn(async () => response()),
    onSidecarStatus: vi.fn(async () => () => undefined),
  };
}

describe("ALLIN1 desktop shell", () => {
  it("loads the real catalog shape and navigates with accessible controls", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    render(<App client={client} />);
    expect(await screen.findByRole("heading", { name: "Package Linker" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByText("Tools")).toBeInTheDocument();
    expect(screen.getByText("Reference")).toBeInTheDocument();
    expect(screen.queryByText("Current view")).not.toBeInTheDocument();
    const collapseSidebar = screen.getByRole("button", { name: "Hide workspace sidebar" });
    expect(collapseSidebar).toHaveTextContent("‹");
    await user.click(collapseSidebar);
    expect(screen.getByRole("button", { name: "Show workspace sidebar" })).toHaveTextContent("›");
    await user.click(screen.getByRole("button", { name: /Help Center/ }));
    expect(await screen.findByRole("heading", { name: "Help Center" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Getting started" })).toBeInTheDocument();
  });

  it("checks SDK updates through the standalone desktop service", async () => {
    const user = userEvent.setup();
    const client = mockClient();
    client.checkUpdate = vi.fn(async () => ({ current_version: "0.6.4", latest_version: "0.6.5", update_available: true, name: "ALLIN1", page_url: "https://example.invalid", archive_name: "sdk.zip", archive_size: 1 }));
    render(<App client={client} />);
    await user.click(await screen.findByRole("button", { name: "Updates" }));
    expect(client.checkUpdate).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("ALLIN1 SDK 0.6.5 is available")).toBeInTheDocument();
    expect(screen.getByText("Installation is disabled until signed Tauri update metadata is configured.")).toBeInTheDocument();
    expect(screen.queryByText(/legacy updater/i)).not.toBeInTheDocument();
  });

  it("keeps help search outside the topic scrollbox and resets only the changed pane", async () => {
    const client = mockClient();
    client.catalog = vi.fn(async () => ({ ...catalog, help_topics: [
      ...catalog.help_topics,
      { key: "rpf", category: "Archives", title: "RPF transactions", summary: "Review archive changes.", body: "Keep the receipt and backup.", keywords: ["rollback"] },
    ] }));
    const user = userEvent.setup();
    render(<App client={client} />);
    await user.click(await screen.findByRole("button", { name: /Help Center/ }));
    const list = screen.getByRole("listbox", { name: "Help topics" });
    const search = screen.getByRole("textbox", { name: "Search help" });
    const article = screen.getByRole("article", { name: "Getting started" });
    expect(screen.getByRole("main")).toHaveClass("help-host");
    expect(list).toHaveClass("help-topic-list");
    expect(list).not.toContainElement(search);
    expect(article).toHaveAttribute("tabindex", "0");

    list.scrollTop = 140;
    article.scrollTop = 250;
    await user.click(within(list).getByRole("option", { name: /RPF transactions/ }));
    expect(article.scrollTop).toBe(0);
    expect(list.scrollTop).toBe(140);
    expect(screen.getByRole("heading", { name: "RPF transactions" })).toBeVisible();
    article.scrollTop = 180;
    await user.type(search, "rollback");
    expect(list.scrollTop).toBe(0);
    expect(article.scrollTop).toBe(180);
    expect(within(list).getAllByRole("option")).toHaveLength(1);
    expect(search).toHaveFocus();
    await user.type(search, " no-match");
    expect(within(list).queryByRole("option")).not.toBeInTheDocument();
    expect(screen.getByText("No help topic matches this search.")).toBeVisible();
    await user.clear(search);
    expect(within(list).getAllByRole("option")).toHaveLength(2);
    expect(article.scrollTop).toBe(0);
    await user.tab();
    expect(within(list).getByRole("option", { name: /Getting started/ })).toHaveFocus();
    await user.tab();
    expect(within(list).getByRole("option", { name: /RPF transactions/ })).toHaveFocus();
    await user.tab();
    expect(article).toHaveFocus();
    await user.click(screen.getByRole("button", { name: /Package Linker/ }));
    expect(screen.getByRole("main")).not.toHaveClass("help-host");
  });

  it("keeps the operational package panes visible before a source is selected", async () => {
    render(<App client={mockClient()} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    const packagePane = screen.getByRole("region", { name: "Package entries" });
    expect(packagePane).toBeInTheDocument();
    expect(packagePane.closest(".panel-grid")).toHaveClass("linker-grid", "is-empty");
    expect(screen.getByRole("region", { name: "Diagnostics" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Field inspector" })).toBeInTheDocument();
    expect(screen.getByText("No package loaded")).toBeInTheDocument();
    expect(screen.getByText("Waiting for inspection")).toBeInTheDocument();
  });

  it("uses the shared alignment grid for adjacent asset and recipe panes", async () => {
    const user = userEvent.setup();
    render(<App client={mockClient()} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.click(screen.getByRole("button", { name: /Asset Viewer/ }));
    await screen.findByRole("heading", { name: "Asset Viewer" });
    expect(screen.getByRole("region", { name: "Asset inventory" }).closest(".panel-grid")).toHaveClass("asset-grid", "is-empty");
    await user.click(screen.getByRole("button", { name: /Package Recipes/ }));
    await screen.findByRole("heading", { name: "Package Recipes" });
    expect(screen.getByRole("region", { name: "Ordered recipe operations" }).closest(".panel-grid")).toHaveClass("recipe-grid", "is-empty");
  });

  it("resolves a vehicle project into aligned model, asset, and evidence panes", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.click(screen.getByRole("button", { name: /Content Workbench/ }));
    expect(await screen.findByRole("heading", { name: "Content Workbench" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Resolved vehicles" }).closest(".panel-grid")).toHaveClass("vehicle-project-grid", "is-empty");
    expect(screen.getByRole("region", { name: "Linked vehicle assets" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Vehicle evidence" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Open package" }));
    await waitFor(() => expect(client.startJob).toHaveBeenCalledWith(
      "inspect_vehicle_project",
      { source: "C:\\Mods\\Demo" },
      expect.stringMatching(/^vehicle-project-/),
      expect.any(Function),
    ));
    expect(await screen.findByText("Vehicle project ready")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Resolved vehicles" }).closest(".panel-grid")).toHaveClass("vehicle-project-grid", "has-result");

    await user.click(screen.getByRole("button", { name: /Comet S2/ }));
    await user.click(screen.getByRole("button", { name: /x64\/vehicles\/comet6\.yft/ }));
    await waitFor(() => expect(client.renderVehicleModel).toHaveBeenCalledWith(
      expect.objectContaining({
        source: "C:\\Mods\\Demo",
        entry: "x64/vehicles/comet6.yft",
        edition: "enhanced",
        gta_path: "C:\\Mods\\Demo",
        yaw: 34,
        pitch: 24,
        render_mode: "shaded",
        quality: "final",
      }),
    ));
    const evidence = within(screen.getByRole("complementary", { name: "Vehicle evidence" }));
    expect(await evidence.findByRole("img", { name: "Rendered native geometry for comet6" })).toBeInTheDocument();
    expect(evidence.getByText(/32,000 rendered triangles/)).toBeInTheDocument();
    expect(evidence.getByText("COMET6")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Weapons/ }));
    expect(await screen.findByRole("heading", { name: "Weapon Workbench" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open weapon folder" })).toBeInTheDocument();
  });

  it("creates a copied vehicle workspace and guards reviewed field revisions", async () => {
    const user = userEvent.setup();
    render(<App client={createPreviewClient("workbench")} />);
    expect(await screen.findByRole("heading", { name: "Content Workbench" })).toBeInTheDocument();
    expect(await screen.findByText("Vehicle project ready")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create editable copy" }));
    const createDialog = await screen.findByRole("dialog", { name: "Create editable vehicle copy?" });
    expect(createDialog).toBeInTheDocument();
    expect(screen.getByText("C:\\SDK\\workspaces\\street-pack-authoring")).toBeInTheDocument();
    await user.click(within(createDialog).getByRole("button", { name: "Create editable copy" }));

    expect(await screen.findByRole("tab", { name: "Core fields" })).toHaveAttribute("aria-selected", "true");
    expect(within(screen.getByRole("complementary", { name: "Vehicle evidence" })).getAllByRole("textbox")).toHaveLength(37);
    const mass = screen.getByRole("textbox", { name: "Mass" });
    expect(mass).toHaveValue("1685.0");
    await user.clear(mass);
    await user.type(mass, "1725.5");
    expect(screen.getByText(/unsaved changes/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Help Center/ }));
    expect(screen.getByRole("heading", { name: "Content Workbench" })).toBeInTheDocument();
    expect(screen.getByText(/before leaving Content Workbench/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Review changes" }));
    expect(await screen.findByRole("dialog", { name: "Save reviewed vehicle fields?" })).toBeInTheDocument();
    expect(screen.getByText("1685.0 → 1725.5")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save new revision" }));
    expect(await screen.findByText(/workspace clean/)).toBeInTheDocument();
    expect(screen.getByText(/Revision 1/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(await screen.findByRole("dialog", { name: "Undo the last vehicle edit?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Undo edit" }));
    expect(await screen.findByText("Undo completed as revision 2.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Mass" })).toHaveValue("1685.0");

    await user.click(screen.getByRole("tab", { name: "Appearance" }));
    expect(screen.getByText("Structured appearance")).toBeInTheDocument();
    expect(screen.getAllByText(/123_comet6_modkit/).length).toBeGreaterThan(0);
    const siren = screen.getByLabelText(/Siren settings/);
    await user.clear(siren);
    await user.type(siren, "7");
    await user.click(screen.getByRole("button", { name: "Review appearance" }));
    const appearanceDialog = await screen.findByRole("dialog", { name: "Save reviewed appearance?" });
    expect(within(appearanceDialog).getByText("variation.sirenSettings")).toBeInTheDocument();
    await user.click(within(appearanceDialog).getByRole("button", { name: "Save appearance revision" }));
    expect(await screen.findByText(/appearance change saved as revision 3/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Siren settings/)).toHaveValue("7");

    await user.click(screen.getByRole("tab", { name: "Tuning kit" }));
    expect(await screen.findByText("Tuning-kit internals")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Visible entries" })).toBeInTheDocument();
    const modShopLabel = screen.getByLabelText(/modShopLabel/);
    expect(modShopLabel).toHaveValue("CM6_SPOILER_1");
    await user.clear(modShopLabel);
    await user.type(modShopLabel, "CM6_SPOILER_TRACK");
    await user.click(screen.getByRole("button", { name: "Review entry" }));
    const tuningDialog = await screen.findByRole("dialog", { name: "Save reviewed tuning change?" });
    expect(within(tuningDialog).getByText("update entry")).toBeInTheDocument();
    await user.click(within(tuningDialog).getByRole("button", { name: "Save tuning revision" }));
    expect(await screen.findByText(/tuning change saved as revision 4/)).toBeInTheDocument();
    expect(screen.getByLabelText(/modShopLabel/)).toHaveValue("CM6_SPOILER_TRACK");

    await user.click(screen.getByRole("tab", { name: "Light profile" }));
    expect(await screen.findByText("Light-profile scalars")).toBeInTheDocument();
    const headlightIntensity = screen.getByLabelText(/headLight\.intensity/);
    expect(headlightIntensity).toHaveValue("2.000000");
    await user.clear(headlightIntensity);
    await user.type(headlightIntensity, "3.250000");
    await user.click(screen.getByRole("button", { name: "Review light profile" }));
    const lightDialog = await screen.findByRole("dialog", { name: "Save reviewed light profile?" });
    expect(within(lightDialog).getByText("light.1.headLight.intensity")).toBeInTheDocument();
    await user.click(within(lightDialog).getByRole("button", { name: "Save light revision" }));
    expect(await screen.findByText(/light value saved as revision 5/)).toBeInTheDocument();
    expect(screen.getByLabelText(/headLight\.intensity/)).toHaveValue("3.250000");

    await user.click(screen.getByRole("tab", { name: "Axles" }));
    const schematic = screen.getByRole("region", { name: "comet6 axle schematic" });
    expect(within(schematic).getByRole("button", { name: /Axle 1.*same.*free/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load skeleton XML" }));
    expect(await screen.findByText("Skeleton linked")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Calculate gains" }));
    expect(await within(schematic).findByRole("button", { name: /Axle 3.*counter/ })).toBeInTheDocument();
    await user.click(within(schematic).getByRole("button", { name: /Axle 2.*fixed.*drive/ }));
    const handbrake = screen.getByLabelText(/Handbrake/);
    expect(handbrake).not.toBeChecked();
    await user.click(handbrake);
    await user.click(screen.getByRole("button", { name: "Review axle changes" }));
    const axleDialog = await screen.findByRole("dialog", { name: "Save reviewed axle configuration?" });
    expect(within(axleDialog).getByText("3")).toBeInTheDocument();
    await user.click(within(axleDialog).getByRole("button", { name: "Save axle revision" }));
    expect(await screen.findByText(/axle change saved as revision 6/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Handbrake/)).toBeChecked();

    await user.click(screen.getByRole("tab", { name: "Transmission" }));
    expect(screen.getByText("Forward gears")).toBeInTheDocument();
    const transmissionType = screen.getByLabelText(/^Type/);
    expect(transmissionType).toHaveValue("dual_clutch");
    await user.selectOptions(transmissionType, "sequential");
    const finalDrive = screen.getByRole("textbox", { name: "Final drive" });
    await user.clear(finalDrive);
    await user.type(finalDrive, "3.7");
    await user.click(screen.getByRole("button", { name: "Review transmission" }));
    const transmissionDialog = await screen.findByRole("dialog", { name: "Save reviewed transmission profile?" });
    expect(within(transmissionDialog).getByText("sequential")).toBeInTheDocument();
    await user.click(within(transmissionDialog).getByRole("button", { name: "Save transmission revision" }));
    expect(await screen.findByText(/transmission change saved as revision 7/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Final drive" })).toHaveValue("3.7");

    await user.click(screen.getByRole("tab", { name: "Output" }));
    expect(screen.getByRole("region", { name: "Vehicle distribution" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Managed package" })).toBeInTheDocument();
    const distributionName = screen.getByRole("textbox", { name: /Display name/ });
    await user.clear(distributionName);
    await user.type(distributionName, "Comet S2 Track");
    expect(screen.getByRole("button", { name: "Review package" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Review distribution" }));
    const distributionDialog = await screen.findByRole("dialog", { name: "Save reviewed distribution settings?" });
    expect(within(distributionDialog).getByText("Comet S2 → Comet S2 Track")).toBeInTheDocument();
    await user.click(within(distributionDialog).getByRole("button", { name: "Save distribution revision" }));
    expect(await screen.findByText(/distribution change saved as revision 8/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Choose…" }));
    expect(screen.getByText("C:\\SDK\\exports\\comet6-package")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review package" }));
    const packageDialog = await screen.findByRole("dialog", { name: "Build this validated vehicle package?" });
    expect(within(packageDialog).getByText("vehicle.comet6")).toBeInTheDocument();
    await user.click(within(packageDialog).getByRole("button", { name: "Build validated package" }));
    expect(await screen.findByText("Package built")).toBeInTheDocument();
    expect(screen.getByText("Output verified")).toBeInTheDocument();
    expect(screen.getByText(/vehicle-package-report\.json/)).toBeInTheDocument();
    expect(screen.getByText(/GTA V was not modified/)).toBeInTheDocument();
  }, 12_000);

  it("indexes recursive RPF entries and previews one exact member", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.click(screen.getByRole("button", { name: /RPF Archives/ }));
    expect(await screen.findByRole("heading", { name: "RPF Archives" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Archive layers" }).closest(".panel-grid")).toHaveClass("rpf-grid", "is-empty");
    expect(screen.getByRole("region", { name: "Archive entries" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "RPF entry evidence" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Open archive" }));
    await waitFor(() => expect(client.startJob).toHaveBeenCalledWith(
      "inspect_rpf_archive",
      { archive: "C:\\Mods\\Demo\\update.rpf" },
      expect.stringMatching(/^rpf-index-/),
      expect.any(Function),
    ));
    expect(await screen.findByText("Recursive index ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /All archive layers/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /common\/data\/handling\.meta/ }));
    await waitFor(() => expect(client.startJob).toHaveBeenLastCalledWith(
      "preview_asset",
      {
        source: "C:\\Mods\\Demo\\update.rpf",
        entry: "::common/data/handling.meta",
        edition: "enhanced",
        gta_path: "C:\\Mods\\Demo",
      },
      expect.stringMatching(/^rpf-preview-/),
      expect.any(Function),
    ));
    expect(await within(screen.getByRole("complementary", { name: "RPF entry evidence" })).findByText("Binary asset. The viewer displays a bounded header.")).toBeInTheDocument();
    const evidence = within(screen.getByRole("complementary", { name: "RPF entry evidence" }));
    expect(evidence.getByText("Compressed")).toBeInTheDocument();
    expect(evidence.getByText("512 B")).toBeInTheDocument();
  });

  it("lists receipts and verifies selected ownership through the read-only typed job", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.click(screen.getByRole("button", { name: /Package Receipts/ }));
    expect(await screen.findByRole("heading", { name: "Package Receipts" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Managed package receipts" }).closest(".panel-grid")).toHaveClass("receipts-grid", "is-empty");
    expect(screen.getByRole("region", { name: "Ownership checks" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Receipt evidence" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Choose GTA V" }));
    await waitFor(() => expect(client.startJob).toHaveBeenCalledWith(
      "inspect_package_receipts",
      { gta_path: "C:\\Mods\\Demo" },
      expect.stringMatching(/^receipts-/),
      expect.any(Function),
    ));
    await user.click(await screen.findByRole("button", { name: /Demo Package/ }));
    await waitFor(() => expect(client.startJob).toHaveBeenLastCalledWith(
      "inspect_package_receipts",
      { gta_path: "C:\\Mods\\Demo", selected_id: "allin1.demo" },
      expect.stringMatching(/^receipts-/),
      expect.any(Function),
    ));
    expect(await screen.findByText("Ownership verified")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /scripts\/Demo\.asi/ })).toBeInTheDocument();
    expect(within(screen.getByRole("complementary", { name: "Receipt evidence" })).getByText("2026-08-29T18:42:11+00:00")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Review install" }));
    await waitFor(() => expect(client.startJob).toHaveBeenLastCalledWith(
      "review_package_lifecycle",
      { action: "install", gta_path: "C:\\Mods\\Demo", source: "C:\\Mods\\Demo" },
      expect.stringMatching(/^lifecycle-/),
      expect.any(Function),
    ));
    expect(await screen.findByRole("dialog", { name: "Install review" })).toHaveTextContent("Execution remains gated");
    expect(client.applyPackageLifecycle).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Continue to install" }));
    expect(screen.getByRole("dialog", { name: "Install Camera Tools?" })).toHaveTextContent("Close GTA V before continuing");
    expect(client.applyPackageLifecycle).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Install package" }));
    await waitFor(() => expect(client.applyPackageLifecycle).toHaveBeenCalledWith({
      action: "install",
      gta_path: "C:\\Mods\\Demo",
      source: "C:\\Mods\\Demo",
      review_sha256: "f241d8f31ce5486ff72bf8f70064ec51f76cbf6420e61b9cc4838d63f254a7a8",
      confirmation_id: "camera-tools",
      game_write_confirmed: true,
      replace_confirmed: false,
    }));
    expect(await screen.findByRole("dialog", { name: "Package installed" })).toHaveTextContent("Closed before write");
    await user.click(screen.getByRole("button", { name: "Done" }));

    await user.click(screen.getByRole("button", { name: "Review disable" }));
    await waitFor(() => expect(client.startJob).toHaveBeenLastCalledWith(
      "review_package_lifecycle",
      { action: "disable", gta_path: "C:\\Mods\\Demo", mod_id: "allin1.demo" },
      expect.stringMatching(/^lifecycle-/),
      expect.any(Function),
    ));
    expect(await screen.findByRole("dialog", { name: "Disable review" })).toHaveTextContent("Target state");
    await user.click(screen.getByRole("button", { name: "Continue to disable" }));
    expect(screen.getByRole("dialog", { name: "Disable Demo Package?" })).toHaveTextContent("verify GTA V is closed");
    await user.click(screen.getByRole("button", { name: "Disable package" }));
    await waitFor(() => expect(client.applyPackageLifecycle).toHaveBeenLastCalledWith(expect.objectContaining({
      action: "disable",
      gta_path: "C:\\Mods\\Demo",
      mod_id: "allin1.demo",
      confirmation_id: "allin1.demo",
      game_write_confirmed: true,
    })));
    expect(await screen.findByRole("dialog", { name: "Package disabled" })).toHaveTextContent("ownership state was checked");
    await user.click(screen.getByRole("button", { name: "Done" }));

    await user.click(screen.getByRole("button", { name: "Review uninstall" }));
    await waitFor(() => expect(client.startJob).toHaveBeenLastCalledWith(
      "review_package_lifecycle",
      { action: "uninstall", gta_path: "C:\\Mods\\Demo", mod_id: "allin1.demo" },
      expect.stringMatching(/^lifecycle-/),
      expect.any(Function),
    ));
    expect(await screen.findByRole("dialog", { name: "Uninstall review" })).toHaveTextContent("No package or GTA V file has changed");
    await user.click(screen.getByRole("button", { name: "Continue to uninstall" }));
    expect(screen.getByRole("dialog", { name: "Uninstall Demo Package?" })).toHaveTextContent("re-run the preflight");
    await user.click(screen.getByRole("button", { name: "Uninstall package" }));
    await waitFor(() => expect(client.applyPackageLifecycle).toHaveBeenLastCalledWith(expect.objectContaining({
      action: "uninstall",
      gta_path: "C:\\Mods\\Demo",
      mod_id: "allin1.demo",
      confirmation_id: "allin1.demo",
      game_write_confirmed: true,
    })));
    expect(await screen.findByRole("dialog", { name: "Package uninstalled" })).toHaveTextContent("receipt removed");
  });

  it("inspects Quick Import sources through the read-only typed workflow", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.click(screen.getByRole("button", { name: /Quick Import/ }));
    expect(await screen.findByRole("heading", { name: "Quick Import" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Detected vehicle branches" }).closest(".panel-grid")).toHaveClass("quick-import-grid", "is-empty");
    expect(screen.getByRole("region", { name: "Discovered vehicles" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Selected vehicle evidence" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Open archive" }));
    expect(screen.getByText("Source ready for inspection")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    await waitFor(() => expect(client.startJob).toHaveBeenCalledWith(
      "inspect_vehicle_quick_import",
      { source: "C:\\Mods\\Demo" },
      expect.stringMatching(/^quick-import-/),
      expect.any(Function),
    ));
    expect(await screen.findByText("Read-only inspection complete")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Comet S2/ })).toBeInTheDocument();
    expect(screen.getByText(/Preparation writes only to the per-user Launcher package library/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Build draft" }));
    await waitFor(() => expect(client.startJob).toHaveBeenCalledWith(
      "review_vehicle_quick_import",
      { source: "C:\\Mods\\Demo", edition: "enhanced" },
      expect.stringMatching(/^quick-import-review-/),
      expect.any(Function),
    ));
    expect(await screen.findByRole("textbox", { name: "Package ID" })).toHaveValue("vehicle.comet6");
    expect(screen.getByRole("textbox", { name: "Display name" })).toHaveValue("Comet S2");
    expect(screen.getByText("No storefront warnings")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Prepare for Launcher" }));
    expect(screen.getByRole("dialog", { name: "Create Launcher package?" })).toHaveTextContent("C:\\Users\\Test\\ALLIN1\\Packages\\vehicle.comet6");
    expect(client.prepareVehicleQuickImport).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Create package" }));
    await waitFor(() => expect(client.prepareVehicleQuickImport).toHaveBeenCalledWith(expect.objectContaining({
      source: "C:\\Mods\\Demo",
      edition: "enhanced",
      package_id: "vehicle.comet6",
      review_sha256: "0b950bd7c61b98a17d09dd54d08fab849789aa55049d1225820b63ca4e21e4a8",
      authoring_confirmed: true,
      replace_confirmed: false,
    })));
    expect(await screen.findByText("Launcher package created")).toBeInTheDocument();
    expect(screen.getByText(/GTA was not modified/)).toBeInTheDocument();

    await user.clear(screen.getByRole("textbox", { name: "Display name" }));
    await user.type(screen.getByRole("textbox", { name: "Display name" }), "Comet S2 Reviewed");
    expect(screen.getByText("Draft changed")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Legacy.*discovered vehicle/ }));
    await user.click(screen.getByRole("button", { name: "Build draft" }));
    expect(await screen.findByDisplayValue("Blista")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Enhanced.*discovered vehicle/ }));
    expect(screen.getByRole("textbox", { name: "Display name" })).toHaveValue("Comet S2 Reviewed");
    await user.click(screen.getByRole("button", { name: /Package Linker/ }));
    expect(screen.getByRole("heading", { name: "Quick Import" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Validate or reset every changed Quick Import draft");

    await user.click(screen.getByRole("button", { name: "Validate changes" }));
    await waitFor(() => expect(client.startJob).toHaveBeenLastCalledWith(
      "review_vehicle_quick_import",
      expect.objectContaining({
        source: "C:\\Mods\\Demo",
        edition: "enhanced",
        package_id: "vehicle.comet6",
        updates: expect.objectContaining({ comet6: expect.objectContaining({ name: "Comet S2 Reviewed" }) }),
      }),
      expect.stringMatching(/^quick-import-review-/),
      expect.any(Function),
    ));
    expect(await screen.findByDisplayValue("Comet S2 Reviewed")).toBeInTheDocument();
  });

  it("inspects a selected package through a cancellable typed job", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.click(screen.getByRole("button", { name: "Open package" }));
    await waitFor(() => expect(client.startJob).toHaveBeenCalledWith(
      "inspect_package",
      { source: "C:\\Mods\\Demo" },
      expect.stringMatching(/^linker-/),
      expect.any(Function),
    ));
    expect(await screen.findByText("Inspection complete")).toBeInTheDocument();
    expect(screen.getByText("dlc.rpf")).toBeInTheDocument();
    expect(screen.getByText("Confirm edition")).toBeInTheDocument();
  });

  it("reviews manifest links and exports the Python-owned Markdown report", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    client.selectPath = vi.fn(async () => "C:\\Mods\\Manifest\\addon.json");
    client.startJob = vi.fn(async (_operation, _payload, revision, onEvent) => {
      queueMicrotask(() => onEvent({
        protocol_version: "1.0.0",
        request_id: "manifest-request",
        job_id: "manifest-job",
        operation: "result",
        payload: {
          revision,
          result: {
            kind: "manifest",
            source: "C:\\Mods\\Manifest\\addon.json",
            id: "test.manifest",
            name: "Manifest Test",
            valid: true,
            error_count: 0,
            warning_count: 0,
            nodes: [{ id: "package.main", kind: "package", label: "Package", fields: { Edition: "enhanced" } }],
            references: [{ id: "register", source: "vehicle.test", source_field: "ModelName", target: "package.main", target_field: "Registration", relationship: "registers_vehicle", required: true, valid: true, message: "Reference resolved." }],
            issues: [],
            install_steps: [{ step_id: "register-dlc", order: 10, label: "Register DLC", target: "dlclist.xml", strategy: "merge" }],
          },
        },
        sequence: 1,
        risk: "read_only",
        terminal: true,
      }));
      return { job_id: "manifest-job", accepted: response() };
    });
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.click(screen.getByRole("button", { name: "Open package" }));
    expect(await screen.findByRole("region", { name: "Integration graph" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Links/ }));
    await user.click(screen.getByRole("button", { name: /vehicle\.test\.ModelName/ }));
    expect(within(screen.getByRole("complementary", { name: "Field inspector" })).getByText("registers_vehicle")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Export report" }));
    await waitFor(() => expect(client.selectReportDestination).toHaveBeenCalledWith("test.manifest-link-report.md"));
    expect(client.exportLinkReport).toHaveBeenCalledWith(
      "C:\\Mods\\Manifest\\addon.json",
      "C:\\Reports\\demo-link-report.md",
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Report exported to");
  });

  it("loads bounded asset previews through the typed Python job", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.click(screen.getByRole("button", { name: /Asset Viewer/ }));
    expect(await screen.findByRole("heading", { name: "Asset Viewer" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Asset inventory" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Asset preview" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Preview evidence" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open package" }));
    expect(await screen.findByRole("button", { name: /dlc\.rpf/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /dlc\.rpf/ }));
    await waitFor(() => expect(client.startJob).toHaveBeenCalledWith(
      "preview_asset",
      { source: "C:\\Mods\\Demo", entry: "dlc.rpf", edition: "Enhanced" },
      expect.stringMatching(/^assets-/),
      expect.any(Function),
    ));
    expect(await screen.findByText(/viewer displays a bounded header/)).toBeInTheDocument();
    expect(within(screen.getByRole("complementary", { name: "Preview evidence" })).getByText("Rockstar archive")).toBeInTheDocument();
  });

  it("reviews ordered package recipes and exports through the guarded command", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.click(screen.getByRole("button", { name: /Package Recipes/ }));
    expect(await screen.findByRole("heading", { name: "Package Recipes" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Ordered recipe operations" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Recipe findings" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Recipe detail" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Open recipe" }));
    await waitFor(() => expect(client.startJob).toHaveBeenCalledWith(
      "inspect_recipe",
      { source: "C:\\Mods\\Demo" },
      expect.stringMatching(/^recipes-/),
      expect.any(Function),
    ));
    expect(await screen.findByText("Desktop Recipe")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /XML · common\/data\/dlclist\.xml/ }));
    expect(within(screen.getByRole("complementary", { name: "Recipe detail" })).getByText("Append registration.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /target_archive_required/ }));
    expect(within(screen.getByRole("complementary", { name: "Recipe detail" })).getByText(/matching Enhanced archive/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(client.startJob).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("button", { name: /XML · common\/data\/dlclist\.xml/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Export report" }));
    await waitFor(() => expect(client.selectReportDestination).toHaveBeenCalledWith("Demo-recipe-plan.md"));
    expect(client.exportRecipeReport).toHaveBeenCalledWith(
      "C:\\Mods\\Demo",
      "C:\\Reports\\demo-link-report.md",
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Recipe report exported to");
  });

  it("rejects a late asset preview after a newer selection", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    const previewCallbacks: { revision: string; emit: (message: Envelope) => void; path: string }[] = [];
    client.startJob = vi.fn(async (operation, payload, revision, onEvent) => {
      if (operation === "inspect_package") {
        queueMicrotask(() => onEvent({
          ...response({
            revision,
            result: {
              kind: "package_scan", source: "C:\\Mods\\Demo", valid: true,
              error_count: 0, warning_count: 0, file_count: 2,
              inventory_count: 2, total_bytes: 2,
              entries: [
                { path: "first.txt", size: 1, category: "Text", preview_kind: "text" },
                { path: "second.txt", size: 1, category: "Text", preview_kind: "text" },
              ], findings: [],
            },
          }),
          job_id: "inspect-job",
          risk: "read_only",
        }));
      } else if (operation === "preview_asset") {
        previewCallbacks.push({ revision, emit: onEvent, path: String(payload.entry) });
      }
      return { job_id: `${operation}-${revision}`, accepted: response() };
    });
    const previewMessage = (item: typeof previewCallbacks[number], text: string): Envelope => ({
      ...response({
        revision: item.revision,
        result: {
          source: "C:\\Mods\\Demo", path: item.path, name: item.path,
          category: "Text", preview_kind: "text", display_kind: "text",
          size: 1, bytes_read: 1, truncated: false, sha256: "abc",
          text, text_truncated: false, artifact: null, metadata: {}, warnings: [],
        },
      }),
      job_id: `preview-${item.path}`,
      risk: "read_only",
    });
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.click(screen.getByRole("button", { name: /Asset Viewer/ }));
    await user.click(screen.getByRole("button", { name: "Open package" }));
    await user.click(await screen.findByRole("button", { name: /first\.txt/ }));
    await waitFor(() => expect(previewCallbacks).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: /second\.txt/ }));
    await waitFor(() => expect(previewCallbacks).toHaveLength(2));
    await act(async () => previewCallbacks[1].emit(previewMessage(previewCallbacks[1], "second preview")));
    await act(async () => previewCallbacks[0].emit(previewMessage(previewCallbacks[0], "stale first preview")));
    expect(await screen.findByText("second preview")).toBeInTheDocument();
    expect(screen.queryByText("stale first preview")).not.toBeInTheDocument();
  });

  it("discards a late package result after a newer inspection starts", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    const callbacks: { revision: string; emit: (message: Envelope) => void }[] = [];
    client.selectPath = vi.fn()
      .mockResolvedValueOnce("C:\\Mods\\First")
      .mockResolvedValueOnce("C:\\Mods\\Second");
    client.startJob = vi.fn(async (_operation, _payload, revision, onEvent) => {
      callbacks.push({ revision, emit: onEvent });
      return { job_id: `job-${callbacks.length}`, accepted: response() };
    });
    const completed = (revision: string, path: string): Envelope => ({
      protocol_version: "1.0.0",
      request_id: "stale-request",
      job_id: "stale-job",
      operation: "result",
      payload: {
        revision,
        result: {
          kind: "package_scan",
          source: path,
          valid: true,
          error_count: 0,
          warning_count: 0,
          file_count: 1,
          total_bytes: 1,
          entries: [{ path, size: 1, category: "fixture" }],
          findings: [],
        },
      },
      sequence: 1,
      risk: "read_only",
      terminal: true,
    });
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.click(screen.getByRole("button", { name: "Open package" }));
    await waitFor(() => expect(callbacks).toHaveLength(1));
    await act(async () => callbacks[0].emit(completed(callbacks[0].revision, "first.rpf")));
    expect(await screen.findByText("first.rpf")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open package" }));
    await waitFor(() => expect(callbacks).toHaveLength(2));
    await act(async () => callbacks[0].emit(completed(callbacks[0].revision, "stale.rpf")));
    await act(async () => callbacks[1].emit(completed(callbacks[1].revision, "second.rpf")));
    expect(await screen.findByText("second.rpf")).toBeInTheDocument();
    expect(screen.queryByText("stale.rpf")).not.toBeInTheDocument();
  });

  it("toggles the docked console with the existing shortcut", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.keyboard("{Control>}`{/Control}");
    expect(screen.getByLabelText("Command suggestions")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /clear-history/ })).toBeInTheDocument();
  });

  it("submits an SDK command from the keyboard", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.keyboard("{Control>}`{/Control}");
    await user.type(screen.getByRole("textbox", { name: "SDK command" }), "validate{Enter}");
    await waitFor(() => expect(client.execute).toHaveBeenCalledWith("validate", []));
    expect(screen.getByText(/PASS/)).toBeInTheDocument();
  });

  it("requires action-time confirmation for console authoring commands", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    client.catalog = vi.fn(async () => ({
      ...catalog,
      commands: [
        ...catalog.commands,
        { name: "oiv-plan", description: "Export a reviewed recipe plan", risk: "authoring_write" as const, parameters: [] },
      ],
    }));
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.keyboard("{Control>}`{/Control}");
    await user.type(screen.getByRole("textbox", { name: "SDK command" }), "oiv-plan source.oiv --output plan.md{Enter}");
    expect(screen.getByRole("dialog", { name: "Run authoring command?" })).toHaveTextContent("source.oiv --output plan.md");
    expect(client.execute).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Run command" }));
    await waitFor(() => expect(client.execute).toHaveBeenCalledWith(
      "oiv-plan",
      ["source.oiv", "--output", "plan.md"],
      true,
    ));
  });

  it("keeps preview fixtures aligned with the selected text and image assets", async () => {
    const user = userEvent.setup();
    render(<App client={createPreviewClient("assets")} />);
    expect(await screen.findByRole("heading", { name: "Asset Viewer" })).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: /README\.txt/ }));
    expect(await screen.findByText(/ALLIN1 Street Pack/)).toBeInTheDocument();
    expect(screen.queryByText(/<package id=/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /preview\/comet6\.png/ }));
    const image = await screen.findByRole("img", { name: "Read-only preview of preview/comet6.png" });
    expect(image).toHaveAttribute("src", "/asset-preview-fixture.svg");
    expect(screen.getByText("Preview artifact")).toBeInTheDocument();
  });

  it("does not retain a stale active RPF job when completion wins the start race", async () => {
    const client = mockClient();
    const user = userEvent.setup();
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.click(screen.getByRole("button", { name: /RPF Archives/ }));
    expect(screen.getByRole("button", { name: "Choose GTA V" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open archive" }));
    await waitFor(() => expect(client.startJob).toHaveBeenCalledWith(
      "inspect_rpf_archive",
      { archive: "C:\\Mods\\Demo\\update.rpf" },
      expect.stringMatching(/^rpf-index-/),
      expect.any(Function),
    ));
    expect(await screen.findByText("Recursive index ready")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument());
  });

  it("inspects native models in three aligned material evidence panes", async () => {
    const user = userEvent.setup();
    render(<App client={createPreviewClient("models")} />);
    expect(await screen.findByRole("heading", { name: "Models & Materials" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open model" }));
    await user.click(screen.getByRole("button", { name: "Inspect model" }));
    expect((await screen.findAllByText("vehicle_paint1")).length).toBeGreaterThan(0);
    expect(screen.getByRole("listbox", { name: "Materials" })).toBeInTheDocument();
    expect(screen.getByRole("listbox", { name: "Geometry" })).toBeInTheDocument();
    expect(screen.getByText("Rendered evidence")).toBeInTheDocument();
    expect(screen.getByText("Texture linked")).toBeInTheDocument();
    expect(screen.queryByText("Decoded cleanly")).not.toBeInTheDocument();
  });

  it("reviews and commits material edits only inside an editable workspace copy", async () => {
    const user = userEvent.setup();
    render(<App client={createPreviewClient("models")} />);
    await screen.findByRole("heading", { name: "Models & Materials" });
    await user.click(screen.getByRole("button", { name: "Open model" }));
    await user.click(screen.getByRole("button", { name: "Inspect model" }));
    await screen.findByRole("button", { name: "Create editable copy" });

    await user.click(screen.getByRole("button", { name: "Create editable copy" }));
    expect(await screen.findByRole("dialog", { name: "Create editable material copy" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create copy" }));
    expect(await screen.findByText("Revision 0")).toBeInTheDocument();

    const shader = screen.getByRole("textbox", { name: "Shader name" });
    await user.clear(shader);
    await user.type(shader, "vehicle_paint_custom");
    await user.click(screen.getByRole("button", { name: "Review material changes" }));
    expect(await screen.findByRole("dialog", { name: "Commit reviewed material changes" })).toBeInTheDocument();
    expect(screen.getByText("shader.name")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Commit changes" }));
    expect(await screen.findByText("Revision 1")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Shader name" })).toHaveValue("vehicle_paint_custom");

    await user.click(screen.getByRole("button", { name: "Undo edit" }));
    expect(await screen.findByRole("dialog", { name: "Undo the latest material edit" })).toBeInTheDocument();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Undo edit" }));
    expect(await screen.findByText("Revision 2")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Shader name" })).toHaveValue("vehicle_paint1");
  });

  it("reviews, commits, and restores existing numeric shader parameters", async () => {
    const user = userEvent.setup();
    render(<App client={createPreviewClient("models")} />);
    await screen.findByRole("heading", { name: "Models & Materials" });
    await user.click(screen.getByRole("button", { name: "Open model" }));
    await user.click(screen.getByRole("button", { name: "Inspect model" }));
    await user.click(await screen.findByRole("button", { name: "Create editable copy" }));
    await user.click(await screen.findByRole("button", { name: "Create copy" }));

    await user.click(screen.getByRole("tab", { name: /Parameters/ }));
    expect(screen.getByRole("combobox", { name: "Parameter" })).toHaveValue("specularIntensityMult");
    const component = screen.getByRole("textbox", { name: "specularIntensityMult value x" });
    expect(component).toHaveValue("0.5");
    await user.clear(component);
    await user.type(component, "0.85");
    await user.click(screen.getByRole("button", { name: "Review parameter changes" }));

    const review = await screen.findByRole("dialog", { name: "Commit reviewed material changes" });
    expect(within(review).getByText("parameter.specularIntensityMult[0].x")).toBeInTheDocument();
    await user.click(within(review).getByRole("button", { name: "Commit changes" }));
    expect(await screen.findByText("Revision 1")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "specularIntensityMult value x" })).toHaveValue("0.85");

    await user.click(screen.getByRole("button", { name: "Undo edit" }));
    const undo = await screen.findByRole("dialog", { name: "Undo the latest material edit" });
    await user.click(within(undo).getByRole("button", { name: "Undo edit" }));
    expect(await screen.findByText("Revision 2")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "specularIntensityMult value x" })).toHaveValue("0.5");
  });

  it("reviews, builds, receipts, and compares a verified native material output", async () => {
    const user = userEvent.setup();
    const client = createPreviewClient("models");
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Models & Materials" });
    await user.click(screen.getByRole("button", { name: "Open model" }));
    await user.click(screen.getByRole("button", { name: "Inspect model" }));
    await user.click(await screen.findByRole("button", { name: "Create editable copy" }));
    await user.click(await screen.findByRole("button", { name: "Create copy" }));

    await user.click(await screen.findByRole("button", { name: "Build verified asset" }));
    const buildDialog = await screen.findByRole("dialog", { name: "Build verified native asset" });
    expect(within(buildDialog).getByText("Native compiler")).toBeInTheDocument();
    expect(within(buildDialog).getByText("Post-build validation")).toBeInTheDocument();
    expect(within(buildDialog).getByText("C:\\SDK\\exports\\comet6.yft")).toBeInTheDocument();
    await user.click(within(buildDialog).getByRole("button", { name: "Build asset" }));

    const receipt = await screen.findByRole("region", { name: "Verified build receipt" });
    expect(within(receipt).getByText("Matched")).toBeInTheDocument();
    expect(within(receipt).getByText("comet6.yft")).toBeInTheDocument();
    await user.click(within(receipt).getByRole("button", { name: "Compare renders" }));
    const comparison = await screen.findByRole("dialog", { name: "Source and rebuilt output" });
    expect(within(comparison).getByText("Editable source snapshot")).toBeInTheDocument();
    expect(within(comparison).getByText("Verified rebuilt output")).toBeInTheDocument();
  });

  it("authors, previews, restores, and builds a guarded texture dictionary", async () => {
    const user = userEvent.setup();
    render(<App client={createPreviewClient("models")} />);
    await screen.findByRole("heading", { name: "Models & Materials" });
    await user.click(screen.getByRole("button", { name: /Texture dictionaries/ }));
    expect(await screen.findByRole("heading", { name: "Texture Dictionary" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Open YTD" }));
    await user.click(screen.getByRole("button", { name: "Create editable copy" }));
    const createDialog = await screen.findByRole("dialog", { name: "Create editable texture copy" });
    expect(within(createDialog).getByText(/\\models\\comet6\.ytd$/)).toBeInTheDocument();
    await user.click(within(createDialog).getByRole("button", { name: "Create copy" }));

    expect(await screen.findByRole("listbox", { name: "Textures" })).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "Preview of comet6_sign_1" })).toHaveAttribute("src", "/asset-preview-fixture.svg");
    await user.click(screen.getByRole("button", { name: "Replace image" }));
    const replaceDialog = await screen.findByRole("dialog", { name: "Replace reviewed texture" });
    expect(within(replaceDialog).getByText("Raster inputs are converted to uncompressed RGBA DDS with one mip level.")).toBeInTheDocument();
    await user.click(within(replaceDialog).getByRole("button", { name: "Commit texture edit" }));
    expect(await screen.findByText("Texture replace committed at revision 1.")).toBeInTheDocument();
    expect(screen.getAllByText("2048 × 1024").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Undo edit" }));
    const undoDialog = await screen.findByRole("dialog", { name: "Undo the latest texture edit" });
    await user.click(within(undoDialog).getByRole("button", { name: "Undo edit" }));
    expect(await screen.findByText("Previous texture state restored at revision 2.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Build YTD" }));
    const buildDialog = await screen.findByRole("dialog", { name: "Build verified texture dictionary" });
    expect(within(buildDialog).getByText("Post-build validation")).toBeInTheDocument();
    await user.click(within(buildDialog).getByRole("button", { name: "Build YTD" }));
    const receipt = await screen.findByRole("region", { name: "Verified texture build receipt" });
    expect(within(receipt).getByText("Matched")).toBeInTheDocument();
    expect(within(receipt).getByText("comet6.ytd")).toBeInTheDocument();
  });

  it("wires Qwen through a structured cancellable assistant job", async () => {
    const user = userEvent.setup();
    render(<App client={createPreviewClient("loaded")} />);
    await screen.findByRole("heading", { name: "Package Linker" });
    await user.keyboard("{Control>}`{/Control}");
    const assistant = screen.getByLabelText("Qwen assistant");
    expect(await within(assistant).findByText("Ready")).toBeInTheDocument();
    await user.type(within(assistant).getByRole("textbox", { name: /Ask about SDK development/ }), "Review the selected model boundary");
    await user.click(within(assistant).getByRole("button", { name: "Ask Qwen" }));
    expect(await within(assistant).findByText(/selected model is structurally sound/i)).toBeInTheDocument();
    expect(within(assistant).getByText(/No commands or writes/)).toBeInTheDocument();
    expect(within(assistant).getByText("confirmed · 91%")).toBeInTheDocument();
  });
});
