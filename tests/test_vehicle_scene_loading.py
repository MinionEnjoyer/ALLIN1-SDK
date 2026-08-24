from __future__ import annotations

from types import SimpleNamespace
import io
import threading
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image
import pytest

import allin1_sdk.vehicle_workbench as vehicle_workbench
from allin1_sdk.app import _configure_style
from allin1_sdk.native_assets import NativeModelScene
from allin1_sdk.vehicle_workbench import VehicleWorkbenchFrame


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


def _wait_for_ui(root: tk.Tk, predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("vehicle scene load did not complete")


def _prepare_preview(
    frame: VehicleWorkbenchFrame, path: str, *, size: int = 1024,
) -> SimpleNamespace:
    frame.source = frame.project_root
    frame.reader = SimpleNamespace()
    frame.scan = SimpleNamespace(
        entries=(SimpleNamespace(path=path, size=size),),
    )
    frame._fragment_paths = {"Primary": path}
    frame.fragment.set("Primary")
    # These tests exercise loading rather than rasterization. Let activation
    # update the actual menus/status while keeping the renderer out of scope.
    frame._render_model_scene = lambda **_kwargs: None
    return SimpleNamespace(
        primary_model=path, high_detail_model=None,
    )


def test_package_reader_and_native_inspector_are_both_created_off_tk(
    tmp_path, monkeypatch, tk_root,
):
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    path = "stream/ThreadedModel.yft"
    model = _prepare_preview(frame, path)
    frame.reader = None
    ui_thread = threading.get_ident()
    calls: list[tuple[str, int]] = []

    class FakeReader:
        def __init__(self, _source):
            calls.append(("reader-init", threading.get_ident()))

        def read(self, _path, *, limit):
            assert limit > 0
            calls.append(("reader-read", threading.get_ident()))
            return SimpleNamespace(data=b"native", truncated=False)

    class FakeInspector:
        def __init__(self, _root, _game_path):
            calls.append(("inspector-init", threading.get_ident()))

        def inspect_bytes(self, *_args, **_kwargs):
            calls.append(("inspector-read", threading.get_ident()))
            return SimpleNamespace(
                model_scene=NativeModelScene("Threaded model", ()), warnings=(),
            )

    monkeypatch.setattr(vehicle_workbench, "PackageAssetReader", FakeReader)
    monkeypatch.setattr(vehicle_workbench, "NativeAssetInspector", FakeInspector)
    try:
        frame._load_model_preview(model)
        _wait_for_ui(
            tk_root,
            lambda: frame._model_scene is not None
            and frame._model_scene.name == "Threaded model",
        )
        assert [name for name, _thread in calls] == [
            "reader-init", "reader-read", "inspector-init", "inspector-read",
        ]
        assert all(thread != ui_thread for _name, thread in calls)
        assert isinstance(frame.reader, FakeReader)
    finally:
        frame.destroy()


def test_native_scene_loading_is_nonblocking_and_latest_only(
    tmp_path, monkeypatch, tk_root,
):
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    first_path = "stream/FirstModel.yft"
    second_path = "stream/SecondModel.yft"
    first_started = threading.Event()
    release_first = threading.Event()
    worker_threads: list[int] = []

    def decode(reader, _source, path, _size, **_kwargs):
        worker_threads.append(threading.get_ident())
        if path == first_path:
            first_started.set()
            assert release_first.wait(1.0)
            # A stale failure must not replace the newer model or status.
            raise ValueError("stale decoder failure")
        return vehicle_workbench._DecodedNativeModel(
            reader, NativeModelScene("Second model", ()),
        )

    monkeypatch.setattr(vehicle_workbench, "_decode_native_model_scene", decode)
    try:
        first_model = _prepare_preview(frame, first_path)
        started = time.perf_counter()
        frame._load_model_preview(first_model)
        assert time.perf_counter() - started < 0.15
        assert frame.viewport_message.get() == f"Loading {first_path}…"
        assert first_started.wait(1.0)

        second_model = _prepare_preview(frame, second_path)
        frame._load_model_preview(second_model)
        assert frame.viewport_message.get() == f"Loading {second_path}…"
        release_first.set()

        _wait_for_ui(
            tk_root,
            lambda: frame._model_scene is not None
            and frame._model_scene.name == "Second model",
        )
        assert frame.viewport_message.get() == second_path
        assert tuple(frame._scene_cache) == (second_path.casefold(),)
        assert all(thread_id != threading.get_ident() for thread_id in worker_threads)
        assert "stale decoder failure" not in frame.viewport_message.get()
    finally:
        release_first.set()
        frame.destroy()


def test_native_scene_loading_surfaces_current_decoder_error(
    tmp_path, monkeypatch, tk_root,
):
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    path = "stream/BrokenModel.yft"
    model = _prepare_preview(frame, path)

    def fail_decode(*_args, **_kwargs):
        raise ValueError("deterministic decode failure")

    monkeypatch.setattr(
        vehicle_workbench, "_decode_native_model_scene", fail_decode,
    )
    try:
        frame._load_model_preview(model)
        _wait_for_ui(
            tk_root,
            lambda: "deterministic decode failure" in frame.viewport_message.get(),
        )
        assert frame._model_scene is None
        assert frame._scene_cache == {}
    finally:
        frame.destroy()


def test_destroy_closes_scene_loader_without_waiting_for_active_decode(
    tmp_path, monkeypatch, tk_root,
):
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    path = "stream/SlowModel.yft"
    model = _prepare_preview(frame, path)
    started = threading.Event()
    release = threading.Event()

    def slow_decode(*_args, **_kwargs):
        started.set()
        assert release.wait(1.0)
        return vehicle_workbench._DecodedNativeModel(
            frame.reader, NativeModelScene("Slow model", ()),
        )

    monkeypatch.setattr(vehicle_workbench, "_decode_native_model_scene", slow_decode)
    frame._load_model_preview(model)
    assert started.wait(1.0)
    began = time.perf_counter()
    frame.destroy()
    assert time.perf_counter() - began < 0.15
    with pytest.raises(RuntimeError, match="closed"):
        frame._viewport_scene_worker.submit("late", lambda: None)
    release.set()


def test_model_render_crop_uses_only_validated_integer_view_box():
    image = Image.new("RGBA", (10, 8), "green")
    cropped = vehicle_workbench._crop_model_render(
        image, {"model_render_view_box": [2, 1, 9, 7]},
    )
    assert cropped.size == (7, 6)

    for box in (
        None, (2, 1, 11, 7), (2, 1, 2, 7), (2.0, 1, 9, 7), (True, 1, 9, 7),
    ):
        unchanged = vehicle_workbench._crop_model_render(
            image, {"model_render_view_box": box},
        )
        assert unchanged is image


def test_interactive_frame_preparation_crops_and_resizes_off_tk():
    source = Image.new("RGB", (100, 60), "green")
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")
    metadata = {"model_render_view_box": (10, 5, 90, 55)}

    frame = vehicle_workbench._prepare_viewport_frame(
        encoded.getvalue(), metadata, quality="interactive", zoom=0.5,
    )

    assert frame.source_image.mode == "RGB"
    assert frame.source_image.size == (80, 50)
    assert frame.display_image is not None
    assert frame.display_image.size == (40, 25)
    assert frame.display_zoom == 0.5


def test_interactive_render_bypasses_png_while_final_keeps_encoded_cache(
    tmp_path, tk_root,
):
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    original_worker = frame._viewport_render_worker
    original_worker.close(wait=False)
    calls: list[str] = []
    submitted: list[tuple[object, object, bool]] = []

    class FakeScene:
        def render_image(self, **_kwargs):
            calls.append("image")
            return Image.new("RGB", (20, 10), "green"), {
                "model_render_view_box": (2, 1, 18, 9),
            }

        def render(self, **_kwargs):
            calls.append("png")
            output = io.BytesIO()
            Image.new("RGB", (20, 10), "green").save(output, format="PNG")
            return output.getvalue(), {"model_render_view_box": (2, 1, 18, 9)}

    class FakeWorker:
        busy = False

        def submit(self, key, render, *, cache_result=True):
            value = render()
            submitted.append((key, value, cache_result))
            return len(submitted)

        def invalidate(self):
            return len(submitted) + 1

        def close(self, **_kwargs):
            return None

    frame._viewport_render_worker = FakeWorker()
    frame._model_scene = FakeScene()
    frame._active_scene_key = (1, "model")
    frame._ensure_render_poll = lambda: None
    try:
        frame._render_model_scene(quality="interactive")
        frame._render_model_scene(quality="final")
    finally:
        frame.destroy()

    assert calls == ["image", "png"]
    assert isinstance(submitted[0][1], vehicle_workbench._PreparedViewportFrame)
    assert submitted[0][1].source_image.size == (16, 8)
    assert submitted[0][2] is False
    assert isinstance(submitted[1][1], tuple)
    assert isinstance(submitted[1][1][0], bytes)
    assert submitted[1][2] is True


def test_viewport_reuses_canvas_items_between_orbit_frames(tmp_path, tk_root):
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    try:
        frame._source_image = Image.new("RGB", (40, 20), "green")
        frame._render_viewport()
        item_ids = dict(frame._viewport_canvas_items)
        canvas_items = tuple(frame.viewport.find_all())

        frame._source_image = Image.new("RGB", (40, 20), "blue")
        frame._viewport_photo = None
        frame._render_viewport()

        assert frame._viewport_canvas_items == item_ids
        assert tuple(frame.viewport.find_all()) == canvas_items
    finally:
        frame.destroy()


def test_view_menu_requests_opt_in_full_quality_without_an_extra_button(
    tmp_path, tk_root,
):
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    requested: list[dict[str, object]] = []
    frame._model_scene = NativeModelScene("Full quality", ())
    frame._render_model_scene = lambda **kwargs: requested.append(kwargs)
    try:
        frame.render_mode_menu.invoke("Render full-quality frame")
        assert requested == [{"quality": "full"}]
        assert frame.viewport_message.get() == (
            "Rendering full-quality frame in background…"
        )
        pending = list(frame.winfo_children())
        visible_button_labels: list[str] = []
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(
                widget, (tk.Button, tk.Menubutton, ttk.Button, ttk.Menubutton),
            ):
                visible_button_labels.append(str(widget.cget("text")))
        assert "Render full-quality frame" not in visible_button_labels
    finally:
        frame.destroy()


def test_orbit_coalesces_motion_until_the_active_frame_is_consumed(
    tmp_path, tk_root,
):
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    original_worker = frame._viewport_render_worker
    original_worker.close(wait=False)

    class FakeWorker:
        busy = True

        def invalidate(self):
            return 1

        def close(self, **_kwargs):
            return None

    worker = FakeWorker()
    frame._viewport_render_worker = worker
    frame._model_scene = NativeModelScene("Orbit model", ())
    frame._orbit_origin = (10, 10)
    frame._orbit_camera = (34.0, 24.0)
    frame._orbit_render_dirty = True
    poll_requests: list[bool] = []
    render_requests: list[dict[str, object]] = []
    frame._ensure_render_poll = lambda: poll_requests.append(True)
    frame._render_model_scene = lambda **kwargs: render_requests.append(kwargs)
    try:
        frame._run_scheduled_scene_render()
        assert poll_requests == [True]
        assert render_requests == []
        assert frame._orbit_render_dirty

        worker.busy = False
        frame._run_scheduled_scene_render()
        assert render_requests == [{"quality": "interactive"}]
        assert not frame._orbit_render_dirty
    finally:
        frame.destroy()


def test_orbit_release_invalidates_interactive_work_before_final_render(
    tmp_path, tk_root,
):
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    original_worker = frame._viewport_render_worker
    original_worker.close(wait=False)
    events: list[object] = []

    class FakeWorker:
        busy = True

        def invalidate(self):
            events.append("invalidate")
            return 17

        def close(self, **_kwargs):
            return None

    frame._viewport_render_worker = FakeWorker()
    frame._model_scene = NativeModelScene("Orbit model", ())
    frame._orbit_origin = (10, 10)
    frame._orbit_camera = (34.0, 24.0)
    frame._orbit_render_dirty = True
    frame._render_model_scene = lambda **kwargs: events.append(("render", kwargs))
    try:
        frame._end_orbit(SimpleNamespace())
        assert events == ["invalidate"]
        assert frame._final_render_job is not None
        frame.after_cancel(frame._final_render_job)
        frame._final_render_job = None
        frame._render_final_after_orbit()
        assert events == ["invalidate", ("render", {"quality": "final"})]
        assert frame._render_generation == 17
        assert frame._orbit_origin is None
        assert frame._orbit_camera is None
        assert not frame._orbit_render_dirty
    finally:
        frame.destroy()


def test_orbit_regrab_cancels_deferred_final_frame(tmp_path, tk_root):
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    frame._model_scene = NativeModelScene("Orbit model", ())
    requested: list[dict[str, object]] = []
    frame._render_model_scene = lambda **kwargs: requested.append(kwargs)
    try:
        frame._orbit_origin = (0, 0)
        frame._orbit_camera = (34.0, 24.0)
        frame._end_orbit(SimpleNamespace())
        assert frame._final_render_job is not None

        frame._begin_orbit(SimpleNamespace(x=4, y=5))
        assert frame._final_render_job is None
        tk_root.update()
        assert requested == []
    finally:
        frame.destroy()


def test_stale_interactive_completion_cannot_replace_release_final(
    tmp_path, tk_root,
):
    frame = VehicleWorkbenchFrame(tk_root, tmp_path)
    started = threading.Event()
    release = threading.Event()
    delivered: list[str] = []

    class BlockingScene:
        def render_image(self, **_kwargs):
            started.set()
            assert release.wait(1.0)
            return Image.new("RGB", (20, 10), "red"), {"tag": "interactive"}

        def render(self, **_kwargs):
            output = io.BytesIO()
            Image.new("RGB", (20, 10), "blue").save(output, format="PNG")
            return output.getvalue(), {"tag": "final"}

    frame._model_scene = BlockingScene()
    frame._active_scene_key = (1, "model")
    frame._apply_rendered_scene = (
        lambda metadata, **_kwargs: delivered.append(str(metadata["tag"]))
    )
    try:
        frame._render_model_scene(quality="interactive")
        assert started.wait(1.0)
        frame._cancel_scene_render()
        frame._render_model_scene(quality="final")
        release.set()
        _wait_for_ui(tk_root, lambda: delivered == ["final"])
        assert delivered == ["final"]
        assert frame._source_image is not None
        assert frame._source_image.getpixel((0, 0)) == (0, 0, 255)
    finally:
        release.set()
        frame.destroy()
