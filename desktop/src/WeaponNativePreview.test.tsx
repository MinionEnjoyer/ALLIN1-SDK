import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { WeaponNativePreview } from "./WeaponNativePreview";
import { weaponPreviewSnapshot } from "./weaponPreview";
import type { DesktopClient } from "./types";

const renderModel = vi.hoisted(() => vi.fn());
vi.mock("./VehicleViewport", () => ({ default: (props: Record<string, unknown>) => {
  renderModel(props);
  return <div aria-label="Interactive weapon viewport">{String(props.entry)}</div>;
} }));
const client = { selectPath: vi.fn(async () => "C:\\Games\\Enhanced") } as unknown as DesktopClient;

describe("WeaponNativePreview", () => {
  it("is lazy and requires an explicit edition when metadata is unresolved", async () => {
    renderModel.mockClear();
    render(<WeaponNativePreview client={client} snapshot={weaponPreviewSnapshot()} epoch={1} dirty={false} />);
    expect(renderModel).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Show model preview" }));
    expect(screen.getByText(/package does not identify a single game edition/)).toBeVisible();
    expect(renderModel).not.toHaveBeenCalled();
    await userEvent.selectOptions(screen.getByLabelText("Preview edition"), "enhanced");
    expect(renderModel).toHaveBeenLastCalledWith(expect.objectContaining({ source: "C:\\SDK\\weapons\\demo",
      entry: "stream/w_pi_demo.ydr", textureEntry: "stream/w_pi_demo.ytd", edition: "enhanced", meshLabel: "Mesh part" }));
    await userEvent.click(screen.getByRole("button", { name: "Hide model preview" }));
    expect(screen.queryByLabelText("Interactive weapon viewport")).not.toBeInTheDocument();
  });

  it("clears the body frame for missing attachments and preserves unsaved metadata", async () => {
    const snapshot = weaponPreviewSnapshot("C:\\copy"); snapshot.project.edition = "Enhanced";
    const before = structuredClone(snapshot);
    render(<WeaponNativePreview client={client} snapshot={snapshot} epoch={1} dirty />);
    await userEvent.click(screen.getByRole("button", { name: "Show model preview" }));
    expect(screen.getByRole("status")).toHaveTextContent("Unsaved metadata edits are not applied");
    await userEvent.selectOptions(screen.getByLabelText("Preview part"), "component:COMPONENT_DEMO_SCOPE");
    expect(screen.queryByLabelText("Interactive weapon viewport")).not.toBeInTheDocument();
    expect(screen.getByText(/Stock or external assets are not substituted/)).toBeVisible();
    expect(screen.getByLabelText("Texture dictionary")).toBeDisabled();
    expect(snapshot).toEqual(before);
  });

  it("requires exact model selection for multiple candidates and allows explicit texture overrides", async () => {
    const snapshot = weaponPreviewSnapshot(); snapshot.project.edition = "Legacy";
    const links = snapshot.native_preview!;
    links.parts[0].assets.push({ path: "other/w_pi_demo.ydr", texture_entries: [], texture_entry: null });
    links.texture_entries.push("stream/custom.ytd");
    render(<WeaponNativePreview client={client} snapshot={snapshot} epoch={1} dirty={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Show model preview" }));
    expect(screen.queryByLabelText("Interactive weapon viewport")).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Model asset"), "other/w_pi_demo.ydr");
    expect(screen.getByLabelText("Texture dictionary")).toHaveValue("");
    await userEvent.selectOptions(screen.getByLabelText("Texture dictionary"), "stream/custom.ytd");
    expect(screen.getByText(/Manually selected dictionary/)).toBeVisible();
    expect(renderModel).toHaveBeenLastCalledWith(expect.objectContaining({ entry: "other/w_pi_demo.ydr", textureEntry: "stream/custom.ytd" }));
  });

  it("refreshes saved source and selection on each adopted snapshot, retaining the open panel", async () => {
    const snapshot = weaponPreviewSnapshot(); snapshot.project.edition = "Enhanced";
    const view = render(<WeaponNativePreview client={client} snapshot={snapshot} epoch={1} dirty={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Show model preview" }));
    const updated = weaponPreviewSnapshot("C:\\copy", "WEAPON_DEMO", "component", "COMPONENT_DEMO_SCOPE");
    updated.source = "C:\\copy\\source";
    view.rerender(<WeaponNativePreview client={client} snapshot={updated} epoch={2} dirty={false} />);
    expect(screen.getByRole("button", { name: "Hide model preview" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Preview part")).toHaveValue("component:COMPONENT_DEMO_SCOPE");
    expect(screen.queryByLabelText("Interactive weapon viewport")).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Preview part"), "weapon:WEAPON_DEMO");
    await userEvent.selectOptions(screen.getByLabelText("Preview edition"), "enhanced");
    expect(renderModel).toHaveBeenLastCalledWith(expect.objectContaining({ source: "C:\\copy\\source" }));
  });

  it("allows a read-only optional decoder folder and handles dialog errors", async () => {
    const snapshot = weaponPreviewSnapshot(); snapshot.project.edition = "Enhanced";
    render(<WeaponNativePreview client={client} snapshot={snapshot} epoch={1} dirty={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Show model preview" }));
    await userEvent.click(screen.getByRole("button", { name: "Select decoder game folder (optional)" }));
    expect(renderModel).toHaveBeenLastCalledWith(expect.objectContaining({ gtaPath: "C:\\Games\\Enhanced" }));
    await userEvent.click(screen.getByRole("button", { name: "Clear game folder" }));
    expect(renderModel).toHaveBeenLastCalledWith(expect.objectContaining({ gtaPath: "" }));
    vi.mocked(client.selectPath).mockRejectedValueOnce(new Error("Dialog unavailable"));
    await userEvent.click(screen.getByRole("button", { name: "Select decoder game folder (optional)" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Dialog unavailable"));
  });
});
