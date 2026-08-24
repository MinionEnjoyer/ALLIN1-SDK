"""Small, UI-agnostic primitives for responsive model viewport rendering.

Tk must only be touched by its owning thread.  The classes in this module keep
expensive, pure-Python/native model rendering on one daemon worker and expose a
polling API for the UI thread.  Pending work is deliberately latest-only: an
orbit gesture should not leave a queue of stale camera frames to render after
the pointer has already moved elsewhere.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
import threading
import time
from typing import Callable, Generic, Hashable, Literal, TypeVar


K = TypeVar("K", bound=Hashable)
V = TypeVar("V")
RenderQuality = Literal["interactive", "final", "full"]
RenderMode = Literal["materials", "shaded", "wireframe"]


@dataclass(frozen=True)
class ViewportRenderKey:
    """Canonical cache key for one model camera view.

    ``scene`` must change whenever decoded geometry changes.  A package member
    path plus its content digest is ideal; ``(path, id(scene))`` is sufficient
    for a cache whose lifetime is limited to one open workbench.
    """

    scene: Hashable
    yaw: float
    pitch: float
    lod: str
    component: str
    render_mode: RenderMode
    quality: RenderQuality

    @classmethod
    def create(
        cls, scene: Hashable, *, yaw: float, pitch: float,
        lod: str | None = None, component: str | None = None,
        render_mode: RenderMode | str = "shaded",
        quality: RenderQuality = "interactive",
    ) -> "ViewportRenderKey":
        normalized_mode = (
            render_mode.strip().casefold() if isinstance(render_mode, str) else ""
        )
        if normalized_mode not in {"materials", "shaded", "wireframe"}:
            raise ValueError(f"Unsupported viewport render mode: {render_mode}")
        if quality not in {"interactive", "final", "full"}:
            raise ValueError(f"Unsupported viewport render quality: {quality}")
        if not math.isfinite(yaw) or not math.isfinite(pitch):
            raise ValueError("Viewport camera angles must be finite")
        return cls(
            scene=scene,
            # Hundredth-degree precision avoids cache misses caused only by
            # floating-point drift without making visible camera steps.
            yaw=round(yaw % 360.0, 2),
            pitch=round(min(89.0, max(-89.0, pitch)), 2),
            lod=(lod or "All").casefold(),
            component=(component or "All").casefold(),
            render_mode=normalized_mode,  # type: ignore[arg-type]
            quality=quality,
        )


class WeightedLruCache(Generic[K, V]):
    """Thread-safe LRU bounded by both entry count and caller-defined weight."""

    def __init__(
        self, *, maximum_entries: int = 12, maximum_weight: int = 32 * 1024 * 1024,
        weigh: Callable[[V], int] | None = None,
    ) -> None:
        if maximum_entries <= 0:
            raise ValueError("maximum_entries must be positive")
        if maximum_weight <= 0:
            raise ValueError("maximum_weight must be positive")
        self.maximum_entries = maximum_entries
        self.maximum_weight = maximum_weight
        self._weigh = weigh or (lambda _value: 1)
        self._items: OrderedDict[K, tuple[V, int]] = OrderedDict()
        self._weight = 0
        self._lock = threading.RLock()

    @property
    def weight(self) -> int:
        with self._lock:
            return self._weight

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def lookup(self, key: K) -> tuple[bool, V | None]:
        """Return ``(found, value)`` while promoting a hit to most-recent."""
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return False, None
            self._items.move_to_end(key)
            return True, item[0]

    def put(self, key: K, value: V) -> bool:
        """Insert a value; return false when one value exceeds the full budget."""
        weight = int(self._weigh(value))
        if weight < 0:
            raise ValueError("Cache weights cannot be negative")
        if weight > self.maximum_weight:
            return False
        with self._lock:
            previous = self._items.pop(key, None)
            if previous is not None:
                self._weight -= previous[1]
            self._items[key] = (value, weight)
            self._weight += weight
            while (
                len(self._items) > self.maximum_entries
                or self._weight > self.maximum_weight
            ):
                _discarded_key, (_discarded_value, discarded_weight) = (
                    self._items.popitem(last=False)
                )
                self._weight -= discarded_weight
        return True

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._weight = 0


@dataclass(frozen=True)
class RenderOutcome(Generic[K, V]):
    """One render completion to consume on the UI thread."""

    generation: int
    key: K
    value: V | None
    error: BaseException | None
    elapsed_seconds: float
    cache_hit: bool = False


@dataclass(frozen=True)
class _RenderRequest(Generic[K, V]):
    generation: int
    key: K
    render: Callable[[], V]
    cache_result: bool


class LatestOnlyRenderWorker(Generic[K, V]):
    """Run expensive renders off-thread while coalescing obsolete requests.

    ``submit`` and ``invalidate`` are safe from the UI thread.  ``poll`` should
    be called by a short Tk ``after`` loop; it never blocks and never calls Tk
    from the worker.  Only one render executes at a time, avoiding CPU storms
    on triangle-heavy models.
    """

    def __init__(
        self, *, cache: WeightedLruCache[K, V] | None = None,
        thread_name: str = "allin1-viewport-render",
    ) -> None:
        self.cache = cache if cache is not None else WeightedLruCache()
        self._condition = threading.Condition(threading.RLock())
        self._pending: _RenderRequest[K, V] | None = None
        self._completed: RenderOutcome[K, V] | None = None
        self._generation = 0
        self._closed = False
        self._active = False
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=thread_name,
        )
        self._thread.start()

    @property
    def latest_generation(self) -> int:
        with self._condition:
            return self._generation

    @property
    def busy(self) -> bool:
        """Whether work or an unconsumed completion still needs UI polling."""
        with self._condition:
            return (
                self._active or self._pending is not None
                or self._completed is not None
            )

    def submit(
        self, key: K, render: Callable[[], V], *, cache_result: bool = True,
    ) -> int:
        """Replace any pending request and return its monotonic generation."""
        with self._condition:
            if self._closed:
                raise RuntimeError("Viewport render worker is closed")
            self._generation += 1
            generation = self._generation
            found, value = self.cache.lookup(key)
            if found:
                self._pending = None
                self._completed = RenderOutcome(
                    generation, key, value, None, 0.0, cache_hit=True,
                )
            else:
                self._completed = None
                self._pending = _RenderRequest(
                    generation, key, render, bool(cache_result),
                )
                self._condition.notify()
            return generation

    def poll(self) -> RenderOutcome[K, V] | None:
        """Consume the latest available completion without blocking."""
        with self._condition:
            outcome = self._completed
            self._completed = None
            return outcome

    def invalidate(self, *, clear_cache: bool = False) -> int:
        """Make active/pending work stale, optionally dropping cached frames."""
        with self._condition:
            self._generation += 1
            self._pending = None
            self._completed = None
            generation = self._generation
        if clear_cache:
            self.cache.clear()
        return generation

    def close(self, *, wait: bool = True, timeout: float = 2.0) -> None:
        """Stop accepting work; the daemon exits after any active render."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._pending = None
            self._completed = None
            self._condition.notify_all()
        if wait and threading.current_thread() is not self._thread:
            self._thread.join(timeout=max(0.0, timeout))

    def __enter__(self) -> "LatestOnlyRenderWorker[K, V]":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._closed or self._pending is not None,
                )
                if self._closed:
                    return
                request = self._pending
                self._pending = None
                self._active = True
            assert request is not None
            started = time.perf_counter()
            value: V | None = None
            error: BaseException | None = None
            try:
                value = request.render()
            except BaseException as exc:  # surfaced to UI; never kill the worker
                error = exc
            elapsed = time.perf_counter() - started
            with self._condition:
                self._active = False
                if self._closed:
                    return
                # A repeated request for the same view can reuse the render
                # already in flight rather than executing it twice.
                if (
                    error is None and self._pending is not None
                    and self._pending.key == request.key
                ):
                    request = self._pending
                    self._pending = None
                if error is None and request.cache_result:
                    self.cache.put(request.key, value)
                if request.generation == self._generation:
                    self._completed = RenderOutcome(
                        request.generation, request.key, value, error, elapsed,
                    )
                if self._pending is not None:
                    self._condition.notify()


def encoded_image_weight(value: object) -> int:
    """Estimate cache weight for bytes or a renderer's ``(bytes, metadata)``."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    if isinstance(value, tuple) and value:
        return encoded_image_weight(value[0])
    return 1
