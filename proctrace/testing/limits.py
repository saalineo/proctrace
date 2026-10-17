"""
Resource limits specification and violation checking.

Provides the `Limits` dataclass for evaluating test execution deltas
against predefined budgets and ignore patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from proctrace.core.types import ResourceDelta


@dataclass(frozen=True, slots=True)
class Limits:
    """Resource limits and allowable exceptions for a test execution.

    Attributes:
        max_rss_mb: Maximum allowed RSS increase in megabytes.
        max_peak_mb: Maximum allowed peak RSS in megabytes.
        max_fd_delta: Maximum allowed open file descriptor delta.
        max_threads: Maximum allowed thread count increase.
        max_children: Maximum allowed child process increase.
        max_time_ms: Maximum allowed wall-clock execution time in milliseconds.
        allow_leaked: Sequence of regex patterns matching file descriptors to ignore.
        allow_threads: Sequence of regex patterns matching thread names to ignore.
    """

    max_rss_mb: float | None = None
    max_peak_mb: float | None = None
    max_fd_delta: int | None = None
    max_threads: int | None = None
    max_children: int | None = None
    max_time_ms: float | None = None
    allow_leaked: tuple[str, ...] = ()
    allow_threads: tuple[str, ...] = ()

    @classmethod
    def from_options(
        cls,
        *,
        max_rss_mb: float | None = None,
        max_peak_mb: float | None = None,
        max_fd_delta: int | None = None,
        max_threads: int | None = None,
        max_children: int | None = None,
        max_time_ms: float | None = None,
        allow_leaked: Sequence[str] = (),
        allow_threads: Sequence[str] = (),
    ) -> Limits:
        """Construct a Limits instance with sequence normalization."""
        return cls(
            max_rss_mb=max_rss_mb,
            max_peak_mb=max_peak_mb,
            max_fd_delta=max_fd_delta,
            max_threads=max_threads,
            max_children=max_children,
            max_time_ms=max_time_ms,
            allow_leaked=tuple(allow_leaked),
            allow_threads=tuple(allow_threads),
        )

    def check(self, delta: ResourceDelta) -> list[str]:
        """Evaluate a ResourceDelta against limits and return human-readable violations."""
        violations: list[str] = []

        # 1. Filter leaked file descriptors
        effective_leaked_fds: list[str] = []
        ignored_fds_count = 0
        fd_patterns = [re.compile(p) for p in self.allow_leaked]

        for fd_path in delta.leaked_fds:
            if any(pat.search(fd_path) for pat in fd_patterns):
                ignored_fds_count += 1
            else:
                effective_leaked_fds.append(fd_path)

        effective_fd_delta = max(0, delta.fd_delta - ignored_fds_count)

        if self.max_fd_delta is not None and effective_fd_delta > self.max_fd_delta:
            leaked_detail = f": {', '.join(effective_leaked_fds)}" if effective_leaked_fds else ""
            violations.append(
                f"leaked {effective_fd_delta} fd(s) (limit: {self.max_fd_delta}){leaked_detail}"
            )

        # 2. Filter threads
        effective_threads: list[str] = []
        ignored_threads_count = 0
        thread_patterns = [re.compile(p) for p in self.allow_threads]

        for thread_name in delta.new_thread_names:
            if any(pat.search(thread_name) for pat in thread_patterns):
                ignored_threads_count += 1
            else:
                effective_threads.append(thread_name)

        effective_thread_delta = max(0, delta.thread_delta - ignored_threads_count)

        if self.max_threads is not None and effective_thread_delta > self.max_threads:
            thread_detail = f": {', '.join(effective_threads)}" if effective_threads else ""
            violations.append(
                f"spawned {effective_thread_delta} thread(s) (limit: {self.max_threads}){thread_detail}"
            )

        # 3. RSS Delta
        if self.max_rss_mb is not None and delta.rss_delta_mb > self.max_rss_mb:
            violations.append(
                f"RSS delta {delta.rss_delta_mb:.1f} MB exceeded limit {self.max_rss_mb:.1f} MB"
            )

        # 4. Peak RSS
        if self.max_peak_mb is not None and delta.peak_rss_mb > self.max_peak_mb:
            violations.append(
                f"peak RSS {delta.peak_rss_mb:.1f} MB exceeded limit {self.max_peak_mb:.1f} MB"
            )

        # 5. Child processes
        if self.max_children is not None and delta.child_delta > self.max_children:
            violations.append(
                f"spawned {delta.child_delta} child process(es) (limit: {self.max_children})"
            )

        # 6. Elapsed time
        if self.max_time_ms is not None and delta.elapsed_ms > self.max_time_ms:
            violations.append(
                f"elapsed time {delta.elapsed_ms:.1f} ms exceeded limit {self.max_time_ms:.1f} ms"
            )

        return violations


__all__ = ["Limits"]
