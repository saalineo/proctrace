from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from proctrace._proctrace_core import (
    BackgroundSampler,
    ResourceSnapshot,
    list_open_fds,
    snapshot_resources,
)
from proctrace.core.types import ResourceDelta

if TYPE_CHECKING:
    from types import TracebackType
    from typing import Self

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
    ) -> None:
        self.memory = memory
        self.fds = fds
        self.threads = threads
        self.children = children
        resolved_interval = (
            sample_interval
            if sample_interval is not None
            else get_default_sample_interval()
        )
        self.sample_interval = resolved_interval
        self.sample_interval_ms = max(1, int(resolved_interval * 1000))

        # State captured at block entry
        self._snapshot_before: ResourceSnapshot | None = None
        self._fds_before: list[str] = []
        self._threads_before: list[str] = []
        self._children_before: int = 0
        self._enter_time_ns: int = 0
        self._sampler: BackgroundSampler | None = None

        self.result: ResourceDelta | None = None
        self.on_exit: Callable[[ResourceDelta], None] | None = None
        self._running: bool = False

    def __enter__(self) -> Self:
        """Start resource tracking and spawn the background sampler if enabled."""
        if self.memory or self.fds:
            self._snapshot_before = snapshot_resources()

        if self.fds:
            self._fds_before = list_open_fds()

        if self.threads:
            self._threads_before = [t.name for t in threading.enumerate()]

        if self.children:
            self._children_before = _count_children()

        self._enter_time_ns = time.monotonic_ns()

        if self.memory:
            self._sampler = BackgroundSampler()
            self._sampler.start(self.sample_interval_ms)

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

        peak_rss = 0
        try:
            if self._sampler is not None:
                peak_rss = self._sampler.stop()
        finally:
            self._sampler = None

        snapshot_after = snapshot_resources() if (self.memory or self.fds) else None
        if snapshot_after is not None and snapshot_after.rss_bytes > peak_rss:
            peak_rss = snapshot_after.rss_bytes
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
) -> ResourceWatcher:
    """Create and return a ResourceWatcher context manager."""
    return ResourceWatcher(
        memory=memory,
        fds=fds,
        threads=threads,
        children=children,
        sample_interval=sample_interval,
    )


__all__ = [
    "DEFAULT_SAMPLE_INTERVAL",
    "ResourceWatcher",
    "get_default_sample_interval",
    "watch",
]
