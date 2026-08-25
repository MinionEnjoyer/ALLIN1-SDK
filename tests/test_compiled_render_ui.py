from __future__ import annotations

import tkinter as tk
import threading
import time
from pathlib import Path

import pytest

from allin1_sdk.app import _configure_style
from allin1_sdk.compiled_render import (
    BlenderInstallation,
    CompiledRenderError,
    CompiledRenderProgress,
    CompiledRenderResult,
)
from allin1_sdk.compiled_render_ui import CompiledRenderPanel
from allin1_sdk.native_assets import NativeModelScene
from allin1_sdk.vehicle_workbench import VehicleWorkbenchFrame
import allin1_sdk.vehicle_workbench as vehicle_workbench


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    root.withdraw()
    _configure_style(root)
    try:
        yield root
    finally:
        if root.winfo_exists():
            root.destroy()


def test_compiled_render_panel_collects_explicit_production_settings(tmp_path, tk_root):
    requested: list[dict[str, object]] = []
    panel = CompiledRenderPanel(
        tk_root,
        backend_status=lambda: {
            "available": True,
            "name": "Blender 4.5",
            "detail": "Ready",
            "device": "NVIDIA GPU",
        },
        on_render=lambda settings: requested.append(settings) or True,
        on_cancel=lambda: None,
    )
    try:
        panel.set_scene_available(True)
        panel.show(suggested_output=tmp_path / "vehicle.png")
        panel.engine.set("Cycles · path-traced")
        panel.resolution.set("Custom")
        panel.width.set("3200")
        panel.height.set("1800")
        panel.samples.set("256")
        panel.device.set("GPU")
        panel.light_rig.set("Dramatic")
        panel.light_rotation.set(75.0)
        panel.light_strength.set(1.8)
        panel.background.set("Custom color")
        panel.background_color.set("#183020")
        panel.transparent.set(True)
        panel.render_button.invoke()

        assert len(requested) == 1
        settings = requested[0]
        assert settings == {
            "width": 3200,
            "height": 1800,
            "engine": "cycles",
            "quality": "production",
            "samples": 256,
            "device": "gpu",
            "light_rig": "dramatic",
            "light_rotation_deg": 75.0,
            "light_strength": 1.8,
            "background": "custom_color",
            "background_color": "#183020",
            "transparent": True,
            "ground_plane": True,
            "contact_shadows": True,
            "output_path": tmp_path / "vehicle.png",
        }
        assert panel._running is True
        assert str(panel.cancel_button.cget("state")) == "normal"
        assert str(panel.render_button.cget("state")) == "disabled"
    finally:
        panel.destroy()


def test_compiled_render_panel_progress_completion_and_output_action(tmp_path, tk_root):
    opened: list[Path] = []
    cancelled: list[bool] = []
    panel = CompiledRenderPanel(
        tk_root,
        backend_status=lambda: {"available": True, "name": "Renderer ready"},
        on_render=lambda _settings: True,
        on_cancel=lambda: cancelled.append(True),
        on_open_output=opened.append,
    )
    output = tmp_path / "finished.png"
    output.write_bytes(b"image")
    try:
        panel.set_scene_available(True)
        panel.refresh_backend_status()
        panel.set_running(True)
        panel.set_progress(0.42, "Tracing lighting · 42%")
        assert float(panel.progress.cget("value")) == 42.0
        assert panel.progress_message.get() == "Tracing lighting · 42%"
        panel.cancel_button.invoke()
        assert cancelled == [True]

        panel.set_output(output, message="Render complete")
        assert str(panel.open_output_button.cget("state")) == "normal"
        panel.open_output_button.invoke()
        assert opened == [output]
        assert panel.progress_message.get() == "Render complete"
    finally:
        panel.destroy()


def test_compiled_render_panel_discloses_advanced_controls_and_backend_setup(
    tmp_path, tk_root,
):
    located: list[Path] = []
    panel = CompiledRenderPanel(
        tk_root,
        backend_status=lambda: {
            "available": False,
            "name": "Blender not detected",
            "detail": "Locate an existing installation.",
        },
        on_render=lambda _settings: True,
        on_cancel=lambda: None,
        on_locate_backend=located.append,
    )
    try:
        panel.set_scene_available(True)
        panel.show(suggested_output=tmp_path / "vehicle.png")
        assert str(panel.render_button.cget("state")) == "disabled"
        assert panel.backend_actions.winfo_manager() == "pack"
        assert not panel._advanced_visible
        panel.advanced_button.invoke()
        assert panel._advanced_visible
        assert panel.advanced_label.get() == "‹  Basic settings"
        panel.resolution.set("4K UHD · 3840 × 2160")
        panel._resolution_changed()
        assert (panel.width.get(), panel.height.get()) == ("3840", "2160")
        panel.resolution.set("16K UHD · 15360 × 8640")
        panel._resolution_changed()
        assert (panel.width.get(), panel.height.get()) == ("15360", "8640")
    finally:
        panel.destroy()


def test_compiled_render_panel_rejects_bad_output_inline(tmp_path, tk_root):
    requested: list[dict[str, object]] = []
    panel = CompiledRenderPanel(
        tk_root,
        backend_status=lambda: {"available": True, "name": "Renderer ready"},
        on_render=lambda settings: requested.append(settings) or True,
        on_cancel=lambda: None,
    )
    try:
        panel.set_scene_available(True)
        panel.refresh_backend_status()
        panel.output_path.set(str(tmp_path / "vehicle.txt"))
        panel.render_button.invoke()
        assert not requested
        assert panel.progress_message.get() == (
            "Compiled renders use PNG output."
        )
    finally:
        panel.destroy()


def test_vehicle_render_menu_opens_embedded_drawer_with_existing_output_parent(
    tmp_path, tk_root,
):
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    frame._model_scene = NativeModelScene("renderable", ())
    try:
        frame.render_mode_menu.invoke("Compiled render…")
        assert frame.compiled_render_panel.winfo_manager() == "place"
        output = Path(frame.compiled_render_panel.output_path.get())
        assert output.suffix.casefold() == ".png"
        assert output.parent.is_dir()
        assert frame.compiled_render_panel._scene_available
        assert not any(
            isinstance(widget, tk.Toplevel)
            for widget in frame.compiled_render_panel.winfo_children()
        )
    finally:
        frame.destroy()


def test_workbench_persists_only_a_validated_blender_executable(
    tmp_path, tk_root, monkeypatch,
):
    state_root = tmp_path / "state"
    executable = tmp_path / "blender.exe"
    executable.write_bytes(b"test")
    monkeypatch.setattr(vehicle_workbench, "user_data_root", lambda: state_root)
    monkeypatch.setattr(
        vehicle_workbench, "detect_blender",
        lambda selected=None: BlenderInstallation(
            Path(selected or executable), "4.5.1", "explicit",
        ),
    )
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    try:
        frame._locate_compiled_render_backend(executable)
        config = state_root / "compiled-render.json"
        assert config.is_file()
        assert frame._load_compiled_render_executable() == executable
        assert not list(state_root.glob("*.tmp"))
    finally:
        frame.destroy()


def test_workbench_compiles_snapshot_off_tk_thread_with_protected_roots(
    tmp_path, tk_root, monkeypatch,
):
    source = tmp_path / "vehicle.rar"
    source.write_bytes(b"archive")
    game_root = tmp_path / "game"
    game_root.mkdir()
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"test")
    output_root = tmp_path.parent / f"{tmp_path.name}-compiled-output"
    output_root.mkdir(exist_ok=True)
    output = output_root / "vehicle.png"
    captured: dict[str, object] = {}

    monkeypatch.setattr(vehicle_workbench, "user_data_root", lambda: tmp_path / "state")
    monkeypatch.setattr(
        vehicle_workbench, "detect_blender",
        lambda _selected=None: BlenderInstallation(blender, "4.5.1", "test"),
    )

    def compile_render(scene, output_path, **kwargs):
        captured.update(scene=scene, output=output_path, kwargs=kwargs)
        captured["thread"] = threading.current_thread()
        kwargs["progress"](
            CompiledRenderProgress("render", 0.6, "Tracing studio lighting"),
        )
        Path(output_path).write_bytes(b"png")
        return CompiledRenderResult(Path(output_path), 1920, 1080, 1.25)

    monkeypatch.setattr(vehicle_workbench, "compile_vehicle_render", compile_render)
    frame = VehicleWorkbenchFrame(
        tk_root, tmp_path, installation_roots=(game_root,),
    )
    frame.source = source
    frame._model_scene = NativeModelScene("renderable", ())
    try:
        frame._show_compiled_render()
        panel = frame.compiled_render_panel
        assert panel is not None
        panel.output_path.set(str(output))
        panel.render_button.invoke()
        deadline = time.monotonic() + 2.0
        while frame._compiled_render_thread is not None and time.monotonic() < deadline:
            tk_root.update()
            time.sleep(0.01)
        tk_root.update()

        assert captured["scene"] is frame._model_scene
        assert captured["thread"] is not threading.current_thread()
        assert captured["output"] == output
        kwargs = captured["kwargs"]
        assert kwargs["protected_roots"] == (game_root, source.parent)
        assert kwargs["yaw"] == 34.0
        assert kwargs["pitch"] == 24.0
        assert kwargs["lod"] is None
        assert kwargs["component"] is None
        assert kwargs["settings"].quality == "production"
        assert kwargs["settings"].ground_plane is True
        assert panel.progress_message.get() == "Render complete in 1.2 seconds."
        assert panel._last_output == output
    finally:
        frame.destroy()
        output.unlink(missing_ok=True)
        output_root.rmdir()


def test_workbench_compiled_render_cancel_is_cooperative(
    tmp_path, tk_root, monkeypatch,
):
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"test")
    started = threading.Event()
    observed_cancel = threading.Event()
    monkeypatch.setattr(vehicle_workbench, "user_data_root", lambda: tmp_path / "state")
    monkeypatch.setattr(
        vehicle_workbench, "detect_blender",
        lambda _selected=None: BlenderInstallation(blender, "4.5.1", "test"),
    )

    def compile_render(_scene, _output, **kwargs):
        started.set()
        assert kwargs["cancel_event"].wait(1.0)
        observed_cancel.set()
        raise CompiledRenderError("render_cancelled", "The render was cancelled")

    monkeypatch.setattr(vehicle_workbench, "compile_vehicle_render", compile_render)
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    frame._model_scene = NativeModelScene("renderable", ())
    try:
        frame._show_compiled_render()
        panel = frame.compiled_render_panel
        assert panel is not None
        panel.output_path.set(str(tmp_path / "cancelled.png"))
        panel.render_button.invoke()
        assert started.wait(1.0)
        panel.cancel_button.invoke()
        deadline = time.monotonic() + 2.0
        while frame._compiled_render_thread is not None and time.monotonic() < deadline:
            tk_root.update()
            time.sleep(0.01)
        assert observed_cancel.is_set()
        assert panel.progress_message.get() == "The render was cancelled"
        assert str(panel.render_button.cget("state")) == "normal"
    finally:
        frame.destroy()


def test_compiled_render_footer_remains_visible_in_compact_workbench(
    tmp_path, tk_root,
):
    tk_root.geometry("900x600+0+0")
    tk_root.deiconify()
    frame = VehicleWorkbenchFrame(tk_root, tmp_path, show_context_header=False)
    frame.pack(fill="both", expand=True)
    frame._model_scene = NativeModelScene("renderable", ())
    try:
        frame._show_compiled_render()
        tk_root.update()
        panel = frame.compiled_render_panel
        assert panel is not None
        for advanced in (False, True):
            if panel._advanced_visible != advanced:
                panel.toggle_advanced()
                tk_root.update()
            panel_bounds = (
                panel.winfo_rootx(), panel.winfo_rooty(),
                panel.winfo_rootx() + panel.winfo_width(),
                panel.winfo_rooty() + panel.winfo_height(),
            )
            root_bounds = (
                tk_root.winfo_rootx(), tk_root.winfo_rooty(),
                tk_root.winfo_rootx() + tk_root.winfo_width(),
                tk_root.winfo_rooty() + tk_root.winfo_height(),
            )
            for control in (
                panel.output_row, panel.backend_card, panel.progress, panel.actions,
            ):
                assert control.winfo_ismapped()
                control_bounds = (
                    control.winfo_rootx(), control.winfo_rooty(),
                    control.winfo_rootx() + control.winfo_width(),
                    control.winfo_rooty() + control.winfo_height(),
                )
                assert panel_bounds[0] <= control_bounds[0] < control_bounds[2]
                assert control_bounds[2] <= panel_bounds[2]
                assert panel_bounds[1] <= control_bounds[1] < control_bounds[3]
                assert control_bounds[3] <= panel_bounds[3]
                assert root_bounds[0] <= control_bounds[0] < control_bounds[2]
                assert control_bounds[2] <= root_bounds[2]
                assert root_bounds[1] <= control_bounds[1] < control_bounds[3]
                assert control_bounds[3] <= root_bounds[3]
            # The card shows setup actions only when Blender is unavailable;
            # an available backend replaces them with the compact refresh action.
            backend_action = (
                panel.backend_refresh_button
                if panel._backend_available else panel.backend_actions
            )
            assert backend_action.winfo_ismapped()
            assert not (
                panel.backend_actions.winfo_ismapped()
                and panel.backend_refresh_button.winfo_ismapped()
            )
            if advanced:
                for control in (
                    panel.width_entry, panel.height_entry, panel.samples_combo,
                    panel.device_combo, panel.background_entry,
                    panel.rotation_entry, panel.strength_entry,
                    panel.ground_check, panel.contact_shadow_check,
                ):
                    assert control.winfo_ismapped()
                    assert (
                        control.winfo_rooty() + control.winfo_height()
                        <= panel.job_footer.winfo_rooty()
                    )
    finally:
        frame.destroy()
        tk_root.withdraw()
