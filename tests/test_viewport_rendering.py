from __future__ import annotations

import threading
import time

import pytest

from allin1_sdk.viewport_rendering import (
    LatestOnlyRenderWorker,
    ViewportRenderKey,
    WeightedLruCache,
    encoded_image_weight,
)


def _wait_for_result(worker, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = worker.poll()
        if result is not None:
            return result
        time.sleep(0.005)
    raise AssertionError("render worker did not complete")


def test_weighted_lru_promotes_hits_and_enforces_both_bounds():
    cache = WeightedLruCache[str, bytes](
        maximum_entries=2, maximum_weight=5, weigh=len,
    )
    assert cache.put("a", b"aa")
    assert cache.put("b", b"bb")
    assert cache.lookup("a") == (True, b"aa")
    assert cache.put("c", b"cc")
    assert cache.lookup("b") == (False, None)
    assert cache.lookup("a") == (True, b"aa")
    assert cache.lookup("c") == (True, b"cc")
    assert len(cache) == 2
    assert cache.weight == 4
    assert not cache.put("oversized", b"123456")


def test_latest_only_worker_skips_intermediate_pending_views():
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow_first():
        calls.append("first")
        started.set()
        assert release.wait(1.0)
        return b"first"

    with LatestOnlyRenderWorker[str, bytes]() as worker:
        first = worker.submit("first", slow_first)
        assert started.wait(1.0)
        middle = worker.submit("middle", lambda: calls.append("middle") or b"middle")
        latest = worker.submit("latest", lambda: calls.append("latest") or b"latest")
        release.set()
        outcome = _wait_for_result(worker)

    assert first < middle < latest
    assert outcome.generation == latest
    assert outcome.key == "latest"
    assert outcome.value == b"latest"
    assert calls == ["first", "latest"]


def test_repeated_inflight_view_is_reused_for_new_generation():
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def render():
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(1.0)
        return b"same frame"

    with LatestOnlyRenderWorker[str, bytes]() as worker:
        worker.submit("view", render)
        assert started.wait(1.0)
        latest = worker.submit("view", render)
        release.set()
        outcome = _wait_for_result(worker)

    assert outcome.generation == latest
    assert outcome.value == b"same frame"
    assert calls == 1


def test_reused_inflight_view_honors_latest_cache_policy():
    started = threading.Event()
    release = threading.Event()
    cache = WeightedLruCache[str, bytes](weigh=len)

    def render():
        started.set()
        assert release.wait(1.0)
        return b"interactive frame"

    with LatestOnlyRenderWorker[str, bytes](cache=cache) as worker:
        worker.submit("view", render, cache_result=True)
        assert started.wait(1.0)
        latest = worker.submit("view", render, cache_result=False)
        release.set()
        outcome = _wait_for_result(worker)

    assert outcome.generation == latest
    assert outcome.value == b"interactive frame"
    assert cache.lookup("view") == (False, None)


def test_cached_render_completes_immediately_without_running_callable():
    cache = WeightedLruCache[str, bytes](weigh=len)
    cache.put("front", b"rendered")
    with LatestOnlyRenderWorker[str, bytes](cache=cache) as worker:
        generation = worker.submit(
            "front", lambda: pytest.fail("cache hit must not render"),
        )
        outcome = worker.poll()

    assert outcome is not None
    assert outcome.generation == generation
    assert outcome.value == b"rendered"
    assert outcome.cache_hit
    assert outcome.elapsed_seconds == 0.0


def test_busy_remains_true_until_a_completion_is_consumed():
    with LatestOnlyRenderWorker[str, bytes]() as worker:
        worker.submit("ready", lambda: b"complete")
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not worker.busy:
            time.sleep(0.005)
        # Once the worker has finished, an unconsumed completion must still
        # keep a Tk polling loop alive through the final race window.
        time.sleep(0.02)
        assert worker.busy
        outcome = worker.poll()
        assert outcome is not None and outcome.value == b"complete"
        assert not worker.busy


def test_interactive_render_can_skip_cache_without_losing_latest_delivery():
    cache = WeightedLruCache[str, bytes](weigh=len)
    calls = 0

    def render():
        nonlocal calls
        calls += 1
        return f"frame {calls}".encode()

    with LatestOnlyRenderWorker[str, bytes](cache=cache) as worker:
        first = worker.submit("orbit", render, cache_result=False)
        first_outcome = _wait_for_result(worker)
        second = worker.submit("orbit", render, cache_result=False)
        second_outcome = _wait_for_result(worker)

    assert first < second
    assert first_outcome.value == b"frame 1"
    assert second_outcome.value == b"frame 2"
    assert not first_outcome.cache_hit
    assert not second_outcome.cache_hit
    assert cache.lookup("orbit") == (False, None)


def test_invalidation_discards_active_result_and_worker_surfaces_next_error():
    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        assert release.wait(1.0)
        return b"stale"

    with LatestOnlyRenderWorker[str, bytes]() as worker:
        worker.submit("stale", slow)
        assert started.wait(1.0)
        invalidated = worker.invalidate()
        release.set()
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            assert worker.poll() is None
            if not worker.busy:
                break
            time.sleep(0.005)
        latest = worker.submit(
            "broken", lambda: (_ for _ in ()).throw(ValueError("render failed")),
        )
        outcome = _wait_for_result(worker)

    assert invalidated < latest
    assert outcome.generation == latest
    assert outcome.value is None
    assert isinstance(outcome.error, ValueError)
    assert str(outcome.error) == "render failed"


def test_clear_cache_invalidation_cannot_be_undone_by_stale_active_render():
    started = threading.Event()
    release = threading.Event()
    cache = WeightedLruCache[str, bytes](weigh=len)

    def slow():
        started.set()
        assert release.wait(1.0)
        return b"stale"

    with LatestOnlyRenderWorker[str, bytes](cache=cache) as worker:
        worker.submit("stale", slow)
        assert started.wait(1.0)
        worker.invalidate(clear_cache=True)
        release.set()
        deadline = time.monotonic() + 1.0
        while worker.busy and time.monotonic() < deadline:
            time.sleep(0.005)

    assert cache.lookup("stale") == (False, None)


def test_viewport_key_normalizes_angles_filters_and_quality():
    key = ViewportRenderKey.create(
        "scene", yaw=394.00001, pitch=120,
        lod="HIGH", component="Body", render_mode=" MATERIALS ", quality="final",
    )
    assert key.yaw == 34.0
    assert key.pitch == 89.0
    assert key.lod == "high"
    assert key.component == "body"
    assert key.render_mode == "materials"
    assert key.quality == "final"
    full = ViewportRenderKey.create(
        "scene", yaw=0, pitch=0, quality="full",
    )
    assert full.quality == "full"
    with pytest.raises(ValueError, match="mode"):
        ViewportRenderKey.create("scene", yaw=0, pitch=0, render_mode="xray")
    with pytest.raises(ValueError, match="quality"):
        ViewportRenderKey.create("scene", yaw=0, pitch=0, quality="draft")
    with pytest.raises(ValueError, match="finite"):
        ViewportRenderKey.create("scene", yaw=float("nan"), pitch=0)


def test_encoded_image_weight_handles_renderer_result():
    assert encoded_image_weight(b"abc") == 3
    assert encoded_image_weight((b"image", {"triangles": 42})) == 5
