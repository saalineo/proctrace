"""
proctrace.alloc - Memory allocation attribution and snapshot diffing.

Provides fine-grained attribution of process memory growth to Python allocation
sites (using tracemalloc) and object types (using GC census), alongside an
empirical split between Python-managed memory and native C/allocator overhead.
"""

from __future__ import annotations

import gc
import sys
import tracemalloc
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

_BYTES_PER_MB: Final[int] = 1024 * 1024
_BYTES_PER_KB: Final[int] = 1024


def _fmt_size(num_bytes: int, sign: bool = True) -> str:
    """Format byte count into human-readable representation (+/- MB/KB/B)."""
    s = "+" if sign and num_bytes >= 0 else ("-" if sign and num_bytes < 0 else "")
    abs_bytes = abs(num_bytes)
    if abs_bytes >= _BYTES_PER_MB:
        return f"{s}{abs_bytes / _BYTES_PER_MB:.1f} MB"
    elif abs_bytes >= _BYTES_PER_KB:
        return f"{s}{abs_bytes / _BYTES_PER_KB:.1f} KB"
    else:
        return f"{s}{abs_bytes} B"


def _fmt_summary_size(num_bytes: int) -> str:
    """Format total summary byte count (e.g., '112 MB' or '8 MB')."""
    abs_bytes = abs(num_bytes)
    if abs_bytes >= _BYTES_PER_MB:
        val = abs_bytes / _BYTES_PER_MB
        return f"{val:.0f} MB" if val.is_integer() else f"{val:.1f} MB"
    elif abs_bytes >= _BYTES_PER_KB:
        val = abs_bytes / _BYTES_PER_KB
        return f"{val:.0f} KB" if val.is_integer() else f"{val:.1f} KB"
    else:
        return f"{abs_bytes} B"


@dataclass(slots=True, frozen=True)
class AllocEntry:
    """Represents memory growth attributed to a single code location.

    Attributes:
        filename: Source file where allocation occurred.
        lineno: Line number of the allocation site.
        size_diff: Net bytes allocated during the measured window.
        count_diff: Net number of memory blocks allocated.
        traceback_lines: Optional formatted traceback frames when frames > 1.
    """

    filename: str
    lineno: int
    size_diff: int
    count_diff: int
    traceback_lines: tuple[str, ...] = ()

    @property
    def size_diff_mb(self) -> float:
        """Net size difference in megabytes (MiB)."""
        return self.size_diff / _BYTES_PER_MB

    @property
    def size_diff_kb(self) -> float:
        """Net size difference in kilobytes (KiB)."""
        return self.size_diff / _BYTES_PER_KB

    def to_dict(self) -> dict[str, Any]:
        """Convert entry to a serializable dictionary."""
        d: dict[str, Any] = {
            "filename": self.filename,
            "lineno": self.lineno,
            "size_diff": self.size_diff,
            "size_diff_bytes": self.size_diff,
            "count_diff": self.count_diff,
        }
        if self.traceback_lines:
            d["traceback"] = list(self.traceback_lines)
        return d


@dataclass(slots=True)
class AllocationReport:
    """Detailed memory allocation attribution report.

    Attributes:
        entries: Top allocation sites sorted by net growth.
        python_tracked_bytes: Total net memory tracked by Python tracemalloc.
        native_estimate_bytes: Estimated memory outside Python objects (e.g. C extensions, malloc fragmentation).
        rss_delta_bytes: Total process RSS delta across the monitored block.
        total_allocated_bytes: Sum of positive allocation diffs.
        total_freed_bytes: Sum of negative allocation diffs (memory freed).
        frames: Number of stack frames captured per allocation.
    """

    entries: list[AllocEntry]
    python_tracked_bytes: int
    native_estimate_bytes: int
    rss_delta_bytes: int
    total_allocated_bytes: int = 0
    total_freed_bytes: int = 0
    frames: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize report to a dictionary."""
        return {
            "entries": [e.to_dict() for e in self.entries],
            "python_tracked_bytes": self.python_tracked_bytes,
            "native_estimate_bytes": self.native_estimate_bytes,
            "rss_delta_bytes": self.rss_delta_bytes,
            "total_allocated_bytes": self.total_allocated_bytes,
            "total_freed_bytes": self.total_freed_bytes,
            "frames": self.frames,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AllocationReport:
        """Construct an AllocationReport from a dictionary representation."""
        entries = [
            AllocEntry(
                filename=e["filename"],
                lineno=e["lineno"],
                size_diff=e.get("size_diff_bytes", e.get("size_diff", 0)),
                count_diff=e.get("count_diff", 0),
                traceback_lines=tuple(e.get("traceback", ())),
            )
            for e in data.get("entries", [])
        ]
        return cls(
            entries=entries,
            python_tracked_bytes=data.get("python_tracked_bytes", 0),
            native_estimate_bytes=data.get("native_estimate_bytes", 0),
            rss_delta_bytes=data.get("rss_delta_bytes", 0),
            total_allocated_bytes=data.get("total_allocated_bytes", 0),
            total_freed_bytes=data.get("total_freed_bytes", 0),
            frames=data.get("frames", 1),
        )

    def report(self, *, color: bool | None = None) -> str:
        """Format a human-readable attribution summary report."""
        lines: list[str] = []

        if not self.entries:
            lines.append("No positive memory allocations tracked.")
        else:
            for entry in self.entries:
                size_str = _fmt_size(entry.size_diff)
                location = f"{entry.filename}:{entry.lineno}"
                count_str = f"({entry.count_diff:,} blocks)"
                lines.append(f"{size_str:<10} {location:<30} {count_str:>16}")
                if entry.traceback_lines:
                    for tb_line in entry.traceback_lines:
                        lines.append(f"    {tb_line}")

        py_str = _fmt_summary_size(self.python_tracked_bytes)
        native_str = _fmt_summary_size(self.native_estimate_bytes)
        rss_str = _fmt_summary_size(self.rss_delta_bytes)

        lines.append(
            f"python-tracked: {py_str}   native/untracked: {native_str}   (rss delta {rss_str})"
        )

        if self.rss_delta_bytes > 0 and self.native_estimate_bytes > (0.5 * self.rss_delta_bytes):
            lines.append(
                "hint: growth is mostly outside Python objects: check C extensions, "
                "numpy/pandas buffers, or malloc fragmentation (try MALLOC_ARENA_MAX=2 or jemalloc)"
            )

        return "\n".join(lines)


class ObjectGrowth(dict[str, int]):
    """Dictionary mapping object types to net count increases across an execution block.

    Supports direct key lookup as well as unqualified name lookups (e.g. 'MyRecord'
    will match 'mymodule.MyRecord' or 'test_func.<locals>.MyRecord').
    """

    def __getitem__(self, key: str) -> int:
        if super().__contains__(key):
            return super().__getitem__(key)

        suffix = f".{key}"
        matches = [
            v for k, v in self.items() if k.endswith(suffix) or k.split(".")[-1] == key
        ]
        if matches:
            return sum(matches)
        raise KeyError(key)

    def get(self, key: str, default: int = 0) -> int:  # type: ignore[override]
        try:
            return self[key]
        except KeyError:
            return default


def get_object_census() -> Counter[str]:
    """Take a full census of all GC-tracked Python object types."""
    gc.collect()
    counts: Counter[str] = Counter()
    for obj in gc.get_objects():
        obj_type = type(obj)
        mod = getattr(obj_type, "__module__", None)
        qual = getattr(
            obj_type, "__qualname__", getattr(obj_type, "__name__", str(obj_type))
        )
        if mod in ("builtins", None):
            name = qual
        else:
            name = f"{mod}.{qual}"
        counts[name] += 1
    return counts


class AllocationTracker:
    """Manages tracemalloc lifecycle, snapshot capture, filtering, and diff calculation."""

    def __init__(self, top: int = 10, frames: int = 1) -> None:
        self.top = max(1, top)
        self.frames = max(1, frames)
        self._was_tracing: bool = False
        self._before: tracemalloc.Snapshot | None = None

    def start(self) -> None:
        """Start allocation tracing if not already active and take the baseline snapshot."""
        self._was_tracing = tracemalloc.is_tracing()
        if not self._was_tracing:
            tracemalloc.start(self.frames)
        self._before = tracemalloc.take_snapshot()

    def stop(self, rss_delta_bytes: int = 0) -> AllocationReport:
        """Take terminal snapshot, stop tracing if started by us, and compute diffs."""
        if self._before is None:
            return AllocationReport(
                entries=[],
                python_tracked_bytes=0,
                native_estimate_bytes=max(0, rss_delta_bytes),
                rss_delta_bytes=rss_delta_bytes,
                frames=self.frames,
            )

        try:
            after = tracemalloc.take_snapshot()
        finally:
            if not self._was_tracing:
                tracemalloc.stop()

        before = self._before
        self._before = None  # Free snapshot reference immediately to release large dictionary

        # Setup filters to exclude proctrace internals and interpreter bootstrap
        proctrace_pkg_dir = Path(__file__).resolve().parent
        filters: list[tracemalloc.Filter] = [
            tracemalloc.Filter(False, "<frozen *"),
            tracemalloc.Filter(False, f"{proctrace_pkg_dir}/*"),
            tracemalloc.Filter(False, f"{proctrace_pkg_dir}/**/*"),
            tracemalloc.Filter(False, __file__),
        ]
        tracemalloc_file = getattr(tracemalloc, "__file__", None)
        if tracemalloc_file:
            filters.append(tracemalloc.Filter(False, tracemalloc_file))

        filtered_before = before.filter_traces(filters)
        filtered_after = after.filter_traces(filters)

        key_type = "traceback" if self.frames > 1 else "lineno"
        stats = filtered_after.compare_to(filtered_before, key_type)

        entries: list[AllocEntry] = []
        python_tracked_bytes = 0
        total_allocated_bytes = 0
        total_freed_bytes = 0

        for stat in stats:
            python_tracked_bytes += stat.size_diff
            if stat.size_diff > 0:
                total_allocated_bytes += stat.size_diff
            else:
                total_freed_bytes += abs(stat.size_diff)

        positive_stats = [s for s in stats if s.size_diff > 0]
        for stat in positive_stats[: self.top]:
            # stat.traceback[-1] is the innermost allocation site
            frame = stat.traceback[-1] if stat.traceback else None
            filename = frame.filename if frame else "<unknown>"
            lineno = frame.lineno if frame else 0

            tb_lines: tuple[str, ...] = ()
            if self.frames > 1 and stat.traceback:
                tb_lines = tuple(stat.traceback.format())

            entries.append(
                AllocEntry(
                    filename=filename,
                    lineno=lineno,
                    size_diff=stat.size_diff,
                    count_diff=stat.count_diff,
                    traceback_lines=tb_lines,
                )
            )

        native_estimate = max(0, rss_delta_bytes - python_tracked_bytes)

        return AllocationReport(
            entries=entries,
            python_tracked_bytes=python_tracked_bytes,
            native_estimate_bytes=native_estimate,
            rss_delta_bytes=rss_delta_bytes,
            total_allocated_bytes=total_allocated_bytes,
            total_freed_bytes=total_freed_bytes,
            frames=self.frames,
        )


__all__ = [
    "AllocEntry",
    "AllocationReport",
    "AllocationTracker",
    "ObjectGrowth",
    "get_object_census",
]
