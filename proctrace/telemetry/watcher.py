from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from proctrace._proctrace_core import (
    BackgroundSampler,
    ResourceSnapshot,
    list_open_fds,
    snapshot_resources,
)
from proctrace.core.types import ResourceDelta
from proctrace.timeline import Timeline

if TYPE_CHECKING:
    from types import TracebackType
    from typing import Self
    from proctrace.alloc import AllocationReport, AllocationTracker, ObjectGrowth

DEFAULT_SAMPLE_INTERVAL: Final[float] = 0.1  # seconds
_DEFAULT_SUBPROCESS_TIMEOUT: Final[float] = 2.0  # seconds


def _count_children() -> int:
    """Count child processes of the current process."""
    proc_children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children")
    try:
        children_str = proc_children.read_text(encoding="utf-8")
        return len(children_str.split()) if children_str.strip() else 0
    except (OSError, FileNotFoundError):
        try:
            result = subprocess.run(
                ["pgrep", "-P", str(os.getpid())],
                capture_output=True,
                text=True,
                check=False,
                timeout=_DEFAULT_SUBPROCESS_TIMEOUT,
            )
            return sum(1 for line in result.stdout.splitlines() if line)
        except (OSError, subprocess.SubprocessError):
            return 0


def get_default_sample_interval() -> float:
    """Get the default sampling interval in seconds, honoring PROCTRACE_SAMPLE_INTERVAL if set."""
    env_val = os.environ.get("PROCTRACE_SAMPLE_INTERVAL")
    if env_val:
        try:
            val = float(env_val)
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_SAMPLE_INTERVAL


class ResourceWatcher:
    """Context manager that measures resource deltas across an execution block.

    Monitors RSS/VMS memory changes, peak memory via a background sampler,
    open file descriptors, newly created threads, and spawned child processes.
    """

    def __init__(
        self,
        memory: bool = True,
        fds: bool = True,
        threads: bool = True,
        children: bool = True,
        sample_interval: float | None = None,
        timeline: bool = False,
        attribution: Literal["tracemalloc", "objects", "all"] | str | bool | None = None,
        top: int = 10,
        attribution_top: int | None = None,
        frames: int = 1,
        attribution_frames: int | None = None,
        cpu: bool = False,
        io: bool = False,
        sockets: bool = False,
        threads_cpu: bool = False,
    ) -> None:
        self.memory = memory
        self.fds = fds
        self.threads = threads
        self.children = children
        self.timeline = timeline
        self.cpu = cpu
        self.io = io
        self.sockets = sockets
        self.threads_cpu = threads_cpu
        resolved_interval = (
            sample_interval
            if sample_interval is not None
            else get_default_sample_interval()
        )
        self.sample_interval = resolved_interval
        self.sample_interval_ms = max(1, int(resolved_interval * 1000))

        if attribution is True:
            self.attribution: str | None = "tracemalloc"
        elif attribution is False or attribution is None:
            self.attribution = None
        elif isinstance(attribution, str):
            attr_norm = attribution.strip().lower()
            if attr_norm not in ("tracemalloc", "objects", "all"):
                raise ValueError(
                    f"Invalid attribution mode: {attribution!r}. "
                    "Expected None, 'tracemalloc', 'objects', or 'all'."
                )
            self.attribution = attr_norm
        else:
            raise TypeError(
                f"attribution must be str, bool, or None, got {type(attribution).__name__}"
            )

        self.top = max(1, attribution_top if attribution_top is not None else top)
        self.frames = max(1, attribution_frames if attribution_frames is not None else frames)

        # State captured at block entry
        self._snapshot_before: ResourceSnapshot | None = None
        self._extended_before: Any = None
        self._fds_before: list[str] = []
        self._threads_before: list[str] = []
        self._children_before: int = 0
        self._enter_time_ns: int = 0
        self._sampler: BackgroundSampler | None = None
        self._alloc_tracker: AllocationTracker | None = None
        self._objects_before: Counter[str] | None = None

        self.result: ResourceDelta | None = None
        self.on_exit: Callable[[ResourceDelta], None] | None = None
        self._running: bool = False

    def __enter__(self) -> Self:
        """Start resource tracking and spawn the background sampler if enabled."""
        if self.memory or self.fds or self.timeline:
            self._snapshot_before = snapshot_resources()

        if self.cpu or self.io or self.sockets or self.threads_cpu:
            from proctrace._proctrace_core import snapshot_extended
            self._extended_before = snapshot_extended(
                cpu=self.cpu,
                io=self.io,
                sockets=self.sockets,
                threads_cpu=self.threads_cpu,
            )

        if self.fds:
            self._fds_before = list_open_fds()

        if self.threads:
            self._threads_before = [t.name for t in threading.enumerate()]

        if self.children:
            self._children_before = _count_children()

        self._enter_time_ns = time.monotonic_ns()

        if self.memory or self.timeline:
            self._sampler = BackgroundSampler()
            self._sampler.start(self.sample_interval_ms)

        # Start allocation and object tracking AFTER baseline snapshot
        # so tracker startup itself does not count as resource growth
        if self.attribution in ("objects", "all"):
            from proctrace.alloc import get_object_census
            self._objects_before = get_object_census()

        if self.attribution in ("tracemalloc", "all"):
            from proctrace.alloc import AllocationTracker
            self._alloc_tracker = AllocationTracker(top=self.top, frames=self.frames)
            self._alloc_tracker.start()

        self._running = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        """Stop tracking, compute deltas, and clean up background resources."""
        exit_time_ns = time.monotonic_ns()
        self._running = False

        snapshot_after = (
            snapshot_resources()
            if (self.memory or self.fds or self.timeline)
            else None
        )

        extended_after = None
        if self.cpu or self.io or self.sockets or self.threads_cpu:
            from proctrace._proctrace_core import snapshot_extended

            extended_after = snapshot_extended(
                cpu=self.cpu,
                io=self.io,
                sockets=self.sockets,
                threads_cpu=self.threads_cpu,
            )

        peak_rss = 0
        peak_at_ms: float | None = None
        timeline_obj: Timeline | None = None
        if self._sampler is not None:
            try:
                peak_rss = self._sampler.stop()
                peak_at_ns = self._sampler.peak_at_ns()
                if peak_at_ns > 0:
                    peak_at_ms = peak_at_ns / 1_000_000.0
                elif peak_rss > 0:
                    peak_at_ms = 0.0

                if self.timeline:
                    raw_samples = self._sampler.samples()
                    if snapshot_after is not None and len(raw_samples) < 2:
                        exit_elapsed_ns = exit_time_ns - self._enter_time_ns
                        last_t_ns = raw_samples[-1][0] if raw_samples else 0
                        if exit_elapsed_ns > last_t_ns:
                            raw_samples.append((
                                exit_elapsed_ns,
                                snapshot_after.rss_bytes,
                                snapshot_after.vms_bytes,
                                snapshot_after.open_fds,
                            ))
                    timeline_obj = Timeline.from_samples(raw_samples)
            finally:
                self._sampler = None

        if snapshot_after is not None and snapshot_after.rss_bytes > peak_rss:
            peak_rss = snapshot_after.rss_bytes
            peak_at_ms = (exit_time_ns - self._enter_time_ns) / 1_000_000.0
        fds_after = list_open_fds() if self.fds else []
        threads_after_names = (
            [t.name for t in threading.enumerate()] if self.threads else []
        )
        children_after = _count_children() if self.children else self._children_before

        before = self._snapshot_before

        rss_delta = (
            (snapshot_after.rss_bytes - before.rss_bytes)
            if (snapshot_after and before)
            else 0
        )
        vms_delta = (
            (snapshot_after.vms_bytes - before.vms_bytes)
            if (snapshot_after and before)
            else 0
        )
        leaked_counter = Counter(fds_after) - Counter(self._fds_before)
        leaked_fds = sorted(leaked_counter.elements())

        raw_fd_delta = len(fds_after) - len(self._fds_before) if self.fds else 0
        if not self.fds:
            fd_delta = 0
        elif leaked_fds:
            fd_delta = max(raw_fd_delta, len(leaked_fds))
        else:
            fd_delta = max(0, raw_fd_delta)

        threads_before_set = set(self._threads_before)
        new_thread_names = [
            n for n in threads_after_names if n not in threads_before_set
        ]

        thread_delta = len(threads_after_names) - len(self._threads_before)
        child_delta = children_after - self._children_before
        elapsed_ns = exit_time_ns - self._enter_time_ns

        # Compute extended resource metrics
        cpu_user_ns = 0
        cpu_sys_ns = 0
        ctx_switches_vol = 0
        ctx_switches_invol = 0
        minor_faults = 0
        major_faults = 0
        io_read_bytes = 0
        io_write_bytes = 0
        io_rchar_bytes = 0
        io_wchar_bytes = 0
        sockets_by_state: dict[str, int] = {}
        socket_deltas: dict[str, int] = {}
        busiest_threads: list[tuple[str, float]] = []

        if extended_after is not None and self._extended_before is not None:
            eb = self._extended_before
            ea = extended_after
            if self.cpu:
                cpu_user_ns = max(0, ea.cpu_user_ns - eb.cpu_user_ns)
                cpu_sys_ns = max(0, ea.cpu_sys_ns - eb.cpu_sys_ns)
                ctx_switches_vol = max(0, ea.vol_ctx - eb.vol_ctx)
                ctx_switches_invol = max(0, ea.invol_ctx - eb.invol_ctx)
                minor_faults = max(0, ea.minor_faults - eb.minor_faults)
                major_faults = max(0, ea.major_faults - eb.major_faults)
            if self.io:
                io_read_bytes = max(0, ea.io_read_bytes - eb.io_read_bytes)
                io_write_bytes = max(0, ea.io_write_bytes - eb.io_write_bytes)
                io_rchar_bytes = max(0, ea.io_rchar_bytes - eb.io_rchar_bytes)
                io_wchar_bytes = max(0, ea.io_wchar_bytes - eb.io_wchar_bytes)
            if self.sockets:
                sockets_by_state = dict(ea.sockets_by_state)
                all_states = set(ea.sockets_by_state.keys()) | set(eb.sockets_by_state.keys())
                for st in all_states:
                    d = ea.sockets_by_state.get(st, 0) - eb.sockets_by_state.get(st, 0)
                    if d != 0:
                        socket_deltas[st] = d
            if self.threads_cpu:
                elapsed_s = (exit_time_ns - self._enter_time_ns) / 1_000_000_000.0
                before_map = {tid: (u, s) for tid, _comm, u, s in eb.thread_cpu}
                tid_to_pyname = {
                    t.native_id: t.name
                    for t in threading.enumerate()
                    if getattr(t, "native_id", None) is not None
                }
                thread_rankings = []
                for tid, comm, u, s in ea.thread_cpu:
                    b_u, b_s = before_map.get(tid, (0, 0))
                    delta_t = max(0, (u - b_u) + (s - b_s))
                    if elapsed_s > 0 and delta_t > 0:
                        pct = (delta_t / 1_000_000_000.0) / elapsed_s * 100.0
                        display_name = tid_to_pyname.get(tid) or comm or f"tid-{tid}"
                        thread_rankings.append((display_name, round(pct, 2)))
                thread_rankings.sort(key=lambda x: x[1], reverse=True)
                busiest_threads = thread_rankings

        # Stop attribution tracker and object census AFTER snapshot_after
        # so releasing snapshot memory does not skew process RSS measurements
        allocations_report = None
        if self._alloc_tracker is not None:
            try:
                allocations_report = self._alloc_tracker.stop(rss_delta_bytes=rss_delta)
            finally:
                self._alloc_tracker = None

        object_growth = None
        if self._objects_before is not None:
            from proctrace.alloc import ObjectGrowth, get_object_census

            objects_after = get_object_census()
            growth_counter = objects_after - self._objects_before
            self._objects_before = None
            object_growth = ObjectGrowth(growth_counter)

        self.result = ResourceDelta(
            rss_delta_bytes=rss_delta,
            vms_delta_bytes=vms_delta,
            fd_delta=fd_delta,
            peak_rss_bytes=peak_rss,
            thread_delta=thread_delta,
            child_delta=child_delta,
            elapsed_ns=elapsed_ns,
            leaked_fds=leaked_fds,
            new_thread_names=new_thread_names,
            sample_interval_ms=self.sample_interval_ms,
            timeline=timeline_obj,
            peak_at_ms=peak_at_ms,
            allocations=allocations_report,
            object_growth=object_growth,
            cpu_user_ns=cpu_user_ns,
            cpu_sys_ns=cpu_sys_ns,
            ctx_switches_vol=ctx_switches_vol,
            ctx_switches_invol=ctx_switches_invol,
            minor_faults=minor_faults,
            major_faults=major_faults,
            io_read_bytes=io_read_bytes,
            io_write_bytes=io_write_bytes,
            io_rchar_bytes=io_rchar_bytes,
            io_wchar_bytes=io_wchar_bytes,
            sockets_by_state=sockets_by_state,
            socket_deltas=socket_deltas,
            busiest_threads=busiest_threads,
        )

        if self.on_exit is not None:
            self.on_exit(self.result)

        return False

    def __repr__(self) -> str:
        state = "running" if self._running else "done"
        if self.result is not None:
            return (
                f"ResourceWatcher({state}, rss_delta={self.result.rss_delta_mb:.1f}MB)"
            )
        return f"ResourceWatcher({state})"


def watch(
    memory: bool = True,
    fds: bool = True,
    threads: bool = True,
    children: bool = True,
    sample_interval: float | None = None,
    timeline: bool = False,
    attribution: Literal["tracemalloc", "objects", "all"] | str | bool | None = None,
    top: int = 10,
    attribution_top: int | None = None,
    frames: int = 1,
    attribution_frames: int | None = None,
    cpu: bool = False,
    io: bool = False,
    sockets: bool = False,
    threads_cpu: bool = False,
) -> ResourceWatcher:
    """Create and return a ResourceWatcher context manager."""
    return ResourceWatcher(
        memory=memory,
        fds=fds,
        threads=threads,
        children=children,
        sample_interval=sample_interval,
        timeline=timeline,
        attribution=attribution,
        top=top,
        attribution_top=attribution_top,
        frames=frames,
        attribution_frames=attribution_frames,
        cpu=cpu,
        io=io,
        sockets=sockets,
        threads_cpu=threads_cpu,
    )


__all__ = [
    "DEFAULT_SAMPLE_INTERVAL",
    "ResourceWatcher",
    "get_default_sample_interval",
    "watch",
]
