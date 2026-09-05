import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import VehicleViewport from "./VehicleViewport";
import type { DesktopClient, Envelope } from "./types";

function response(payload: Record<string, unknown>): Envelope {
  return { protocol_version: "1.0.0", request_id: "viewport", job_id: null, operation: "result", payload, sequence: 0, risk: "read_only", terminal: true };
}

function viewportClient() {
  const renderVehicleModel = vi.fn(async (payload: Record<string, unknown>) => response({ result: {
    kind: "vehicle_model_viewport",
    source: String(payload.source),
    path: String(payload.entry),
    name: "comet6.yft",
    size: 4096,
    bytes_read: 4096,
    sha256: "a".repeat(64),
    edition: "enhanced",
    artifact: { path: "C:\\cache\\frame.png", preview_url: "/asset-preview-fixture.svg", sha256: "b".repeat(64), size: 1024, media_type: "image/png", width: 960, height: 680 },
    camera: { yaw: payload.yaw, pitch: payload.pitch, lod: payload.lod, component: payload.component, material: payload.material, render_mode: payload.render_mode, quality: payload.quality, collision_visible: Boolean(payload.collision_visible) },
    scene: {
      lods: ["High", "Medium"],
      components: [
        { name: "Chassis", lod: "High", geometry_count: 2, vertex_count: 12000, triangle_count: 18000, material_names: ["vehicle_paint1"], texture_names: ["comet6"] },
        { name: "Interior", lod: "Medium", geometry_count: 1, vertex_count: 6000, triangle_count: 9000, material_names: ["vehicle_interior2"], texture_names: ["comet6_interior"] },
      ],
      materials: [
        { index: 0, name: "vehicle_paint1", record_count: 1, geometry_count: 2, triangle_count: 18000, lods: ["High"], components: ["Chassis"], texture_bindings: [{ slot: "DiffuseSampler", name: "comet6_sign_1", resolved: true }], parameter_count: 2, parameters: [{ name: "specularIntensityMult", source_type: "Vector", values: [[0.5, 0, 0, 0]], record_count: 1 }, { name: "detailSettings", source_type: "Array", values: [[1, 0.75, 0.25, 0], [4, 3, 2, 1]], record_count: 1 }] },
        { index: 1, name: "vehicle_interior2", record_count: 1, geometry_count: 1, triangle_count: 9000, lods: ["Medium"], components: ["Interior"], texture_bindings: [{ slot: "DiffuseSampler", name: "comet6_interior", resolved: true }], parameter_count: 0, parameters: [] },
      ],
      component_count: 2,
      material_count: 6,
      surface_count: 2,
      bone_count: 126,
    },
    metadata: {
      model_rendered_triangles: payload.quality === "interactive" ? 6000 : 27000,
      ...(payload.render_mode === "uvs" ? {
        model_render_uv_resolved_triangle_count: 25000,
        model_render_uv_unresolved_triangle_count: 1200,
        model_render_uv_degenerate_triangle_count: 500,
        model_render_uv_missing_triangle_count: 300,
        model_render_uv_coverage_percent: 97.04,
      } : {}),
    },
    texture_dictionary: {
      path: String(payload.texture_entry), name: "comet6.ytd", size: 8192,
      bytes_read: 8192, sha256: "c".repeat(64), texture_count: 2,
      previewed_count: 2, truncated: false,
      artifact: { path: "C:\\cache\\textures.png", preview_url: "/asset-preview-fixture.svg", sha256: "d".repeat(64), size: 2048, media_type: "image/png" },
      textures: [
        { name: "comet6_sign_1", file_name: "comet6_sign_1.dds", width: 2048, height: 2048, mip_levels: 12, format: "DXT5", usage: "DEFAULT", size: 1024, sha256: "e".repeat(64), contact_sheet_index: 0, warnings: [] },
        { name: "comet6_interior", file_name: "comet6_interior.dds", width: 1024, height: 1024, mip_levels: 11, format: "DXT1", usage: "DEFAULT", size: 1024, sha256: "f".repeat(64), contact_sheet_index: 1, warnings: [] },
      ], warnings: [], cache_hit: true, read_only: true,
    },
    collision_dictionary: payload.collision_entry ? {
      path: String(payload.collision_entry), name: "comet6.ybn", size: 4096,
      bytes_read: 4096, sha256: "1".repeat(64), geometry_count: 2,
      vertex_count: 32, polygon_count: 14, material_count: 3,
      render_triangle_count: 17, overlay_polygon_count: 13,
      unrendered_polygon_count: 1,
      primitive_counts: [
        { kind: "Triangle", count: 12, overlay: true, fidelity: "exact mesh" },
        { kind: "Box", count: 1, overlay: true, fidelity: "diagnostic hull" },
        { kind: "Capsule", count: 1, overlay: false, fidelity: "count only" },
      ],
      bounds: { min: [-1, -2, -0.5], max: [1, 2, 1.5], size: [2, 4, 2] },
      warnings: [], cache_hit: true, read_only: true,
    } : null,
    uv_atlas: payload.render_mode === "uvs" ? {
      artifact: { path: "C:\\cache\\uv-atlas.png", preview_url: "/asset-preview-fixture.svg", sha256: "2".repeat(64), size: 4096, media_type: "image/png", width: 960, height: 1016 },
      width: 960, height: 1016, triangle_budget: 45000,
      source_triangle_count: 27000, sampled_triangle_count: 27000,
      rendered_triangle_count: 25500, valid_triangle_count: 26200,
      degenerate_triangle_count: 500, missing_triangle_count: 300,
      seam_triangle_count: 700, island_count: 14,
      texture_group_count: 2, returned_texture_group_count: 2, sampled: false,
      texture_groups: [
        { name: "comet6_sign_1", resolved: true, material_names: ["vehicle_paint1"], geometry_count: 2, sampled_triangle_count: 18000, valid_triangle_count: 17600, rendered_triangle_count: 17200, island_count: 8, seam_triangle_count: 400, degenerate_triangle_count: 250, missing_triangle_count: 150 },
        { name: "comet6_interior", resolved: true, material_names: ["vehicle_interior2"], geometry_count: 1, sampled_triangle_count: 9000, valid_triangle_count: 8600, rendered_triangle_count: 8300, island_count: 6, seam_triangle_count: 300, degenerate_triangle_count: 250, missing_triangle_count: 150 },
      ],
      selection: { lod: String(payload.lod), component: String(payload.component), material: String(payload.material) },
      fidelity: "UV0 coordinates decoded from native geometry; cross-tile seams are count-only",
      cache_hit: true, read_only: true,
    } : null,
    warnings: [],
    cache_hit: true,
    read_only: true,
    workspace_write_performed: false,
    package_write_performed: false,
    game_write_performed: false,
  } }));
  return { client: { renderVehicleModel } as unknown as DesktopClient, renderVehicleModel };
}

describe("VehicleViewport", () => {
  it("clears failed loading state, retries, and uses native triangle counts for weapon assets", async () => {
    const { client, renderVehicleModel } = viewportClient();
    const good = await client.renderVehicleModel({ source: "C:\\weapon", entry: "weapon.ydr" });
    const result = (good.payload as { result: { metadata: Record<string, number> } }).result;
    result.metadata.model_rendered_triangle_count = 17829;
    renderVehicleModel.mockResolvedValueOnce({ ...response({ message: "Decoder unavailable" }), operation: "error" });
    render(<VehicleViewport client={client} source="C:\\weapon" entry="weapon.ydr" edition="Enhanced" model="weapon" ariaLabel="Interactive weapon viewport" meshLabel="Mesh part" />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Decoder unavailable"));
    expect(screen.queryByText("Decoding native geometry")).not.toBeInTheDocument();
    renderVehicleModel.mockResolvedValueOnce(good);
    await userEvent.click(screen.getByRole("button", { name: "Retry native preview" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByLabelText("Interactive weapon viewport")).toBeInTheDocument();
    expect(screen.getByLabelText("Mesh part")).toBeInTheDocument();
    expect(screen.getByText(/17,829 rendered triangles/)).toBeInTheDocument();
  });
  it("renders validated frames and keeps React camera and selection controls synchronized", async () => {
    const { client, renderVehicleModel } = viewportClient();
    const user = userEvent.setup();
    render(<VehicleViewport client={client} source="C:\\Mods\\Demo" entry="stream/comet6.yft" edition="enhanced" gtaPath="C:\\Games\\GTA V" model="comet6" textureEntry="stream/comet6.ytd" collisionEntry="stream/comet6.ybn" />);

    expect(await screen.findByRole("img", { name: "Rendered native geometry for comet6" })).toBeInTheDocument();
    expect(renderVehicleModel).toHaveBeenCalledWith(expect.objectContaining({
      yaw: 34, pitch: 24, lod: "All", component: "All", material: "All",
      render_mode: "shaded", quality: "final",
      texture_entry: "stream/comet6.ytd",
      collision_entry: "stream/comet6.ybn", collision_visible: false,
    }));

    await user.click(screen.getByRole("button", { name: "Collision overlay" }));
    await waitFor(() => expect(renderVehicleModel).toHaveBeenCalledWith(expect.objectContaining({
      collision_entry: "stream/comet6.ybn", collision_visible: true,
    })));
    expect(screen.getByLabelText("Collision overlay legend")).toHaveTextContent("Triangle mesh12");
    expect(screen.getByLabelText("Collision ownership evidence")).toHaveTextContent("comet6.ybn");
    await user.click(screen.getByRole("button", { name: "Collision overlay" }));
    await waitFor(() => expect(renderVehicleModel).toHaveBeenCalledWith(expect.objectContaining({
      collision_visible: false,
    })));

    await user.click(screen.getByRole("button", { name: "Textured" }));
    await waitFor(() => expect(renderVehicleModel).toHaveBeenCalledWith(expect.objectContaining({
      render_mode: "textured", quality: "final", texture_entry: "stream/comet6.ytd",
    })));
    expect(screen.getByText("UV0 texture diagnostic · linked YTD pixels")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Material IDs" }));
    await waitFor(() => expect(renderVehicleModel).toHaveBeenCalledWith(expect.objectContaining({
      render_mode: "materials", quality: "final",
    })));

    await user.click(screen.getByRole("button", { name: "UVs" }));
    await waitFor(() => expect(renderVehicleModel).toHaveBeenCalledWith(expect.objectContaining({
      render_mode: "uvs", quality: "final", texture_entry: "stream/comet6.ytd",
    })));
    expect(screen.getByLabelText("UV coverage legend")).toHaveTextContent("25,000");
    expect(screen.getByText("97.0% valid UV0 · rendered sample")).toBeInTheDocument();
    expect(screen.getByLabelText("UV0 island atlas evidence")).toHaveTextContent("14 islands");
    expect(screen.getByRole("img", { name: "Flattened UV0 island atlas for comet6" })).toBeInTheDocument();
    expect(screen.getByLabelText("UV texture groups")).toHaveTextContent("comet6_sign_1");
    expect(screen.getByText(/700 cross-tile seams/)).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("LOD"), "High");
    await waitFor(() => expect(renderVehicleModel).toHaveBeenCalledWith(expect.objectContaining({
      lod: "High", component: "All",
    })));
    expect(screen.getByRole("option", { name: "Chassis" })).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Surface"), "vehicle_paint1");
    await waitFor(() => expect(renderVehicleModel).toHaveBeenCalledWith(expect.objectContaining({
      material: "vehicle_paint1", texture_entry: "stream/comet6.ytd",
    })));
    expect(screen.getAllByText("comet6_sign_1").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("2048×2048 · DXT5")).toBeInTheDocument();
    expect(screen.getByText("specularIntensityMult")).toBeInTheDocument();
    expect(screen.getByText("Vector4 array · 2 rows")).toBeInTheDocument();
    expect(screen.getByLabelText("x 0.5 y 0 z 0 w 0")).toBeInTheDocument();

    const stage = screen.getByLabelText(/comet6 model view/);
    fireEvent.keyDown(stage, { key: "ArrowRight" });
    await waitFor(() => expect(renderVehicleModel).toHaveBeenCalledWith(expect.objectContaining({
      yaw: 39, pitch: 24, quality: "final",
    })));
    expect(screen.getByText(/39° yaw/)).toBeInTheDocument();
  });
});
