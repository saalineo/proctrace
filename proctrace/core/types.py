from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from typing import Any, Final

_BYTES_PER_MB: Final[int] = 1024 * 1024
_NS_PER_MS: Final[int] = 1_000_000


@dataclass(slots=True)
class ResourceDelta:
    """Represents changes in process resources between two points in time.

    Attributes:
        rss_delta_bytes: Resident Set Size difference (exit minus entry).
        vms_delta_bytes: Virtual Memory Size difference (exit minus entry).
        fd_delta: Open file descriptor count difference (exit minus entry).
        peak_rss_bytes: Peak RSS measured by the background sampler.
        thread_delta: Active thread count difference (exit minus entry).
        child_delta: Child process count difference (exit minus entry).
        elapsed_ns: Monotonic wall-clock elapsed time in nanoseconds.
        leaked_fds: List of file descriptors present at exit but not at entry.
        new_thread_names: Names of threads created during the watched block.
        sample_interval_ms: Sampler polling interval in milliseconds.
    """

    rss_delta_bytes: int
    vms_delta_bytes: int
    fd_delta: int
    peak_rss_bytes: int
    thread_delta: int
    child_delta: int
    elapsed_ns: int
    leaked_fds: list[str]
    new_thread_names: list[str]
    sample_interval_ms: int = 100

    @property
    def sample_interval_s(self) -> float:
        """Sample interval in seconds."""
        return self.sample_interval_ms / 1000.0

    def is_comparable_with(self, other: ResourceDelta) -> bool:
        """Check if another ResourceDelta was measured using the same sample rate."""
        if not isinstance(other, ResourceDelta):
            return False
        return self.sample_interval_ms == other.sample_interval_ms

    @property
    def rss_delta_mb(self) -> float:
        """RSS delta expressed in megabytes (MiB)."""
        return self.rss_delta_bytes / _BYTES_PER_MB

    @property
    def vms_delta_mb(self) -> float:
        """VMS delta expressed in megabytes (MiB)."""
        return self.vms_delta_bytes / _BYTES_PER_MB

    @property
    def peak_rss_mb(self) -> float:
        """Peak RSS expressed in megabytes (MiB)."""
        return self.peak_rss_bytes / _BYTES_PER_MB

    @property
    def elapsed_ms(self) -> float:
        """Elapsed wall-clock time in milliseconds."""
        return self.elapsed_ns / _NS_PER_MS

    def to_dict(self) -> dict[str, Any]:
        """Convert the ResourceDelta to a dictionary."""
        return asdict(self)

    def to_json(self, indent: int | None = None) -> str:
        """Serialize the ResourceDelta to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResourceDelta:
        """Construct a ResourceDelta instance from a dictionary."""
        return cls(**d)

    def report(self) -> str:
        """Format a human-readable, ANSI-colored summary report."""
        use_color = (
            sys.stderr.isatty()
            if hasattr(sys.stderr, "isatty") and not getattr(sys, "is_finalizing", lambda: False)()
            else False
        )

        def green(s: str) -> str:
            return f"\033[32m{s}\033[0m" if use_color else s

        def yellow(s: str) -> str:
            return f"\033[33m{s}\033[0m" if use_color else s

        def red(s: str) -> str:
            return f"\033[31m{s}\033[0m" if use_color else s

        def fmt_bytes(n: int) -> str:
            sign = "+" if n >= 0 else ""
            mb = n / _BYTES_PER_MB
            return f"{sign}{mb:.2f} MB"

        def fmt_count(n: int) -> str:
            sign = "+" if n >= 0 else ""
            return f"{sign}{n}"

        if self.rss_delta_bytes > 10 * _BYTES_PER_MB:
            mem_str = red(fmt_bytes(self.rss_delta_bytes))
        elif self.rss_delta_bytes > 1 * _BYTES_PER_MB:
            mem_str = yellow(fmt_bytes(self.rss_delta_bytes))
        else:
            mem_str = green(fmt_bytes(self.rss_delta_bytes))

        if self.leaked_fds:
            fd_str = red(fmt_count(self.fd_delta))
            leaked_summary = ", ".join(self.leaked_fds[:3])
            more_suffix = (
                f" (+{len(self.leaked_fds) - 3} more)" if len(self.leaked_fds) > 3 else ""
            )
            leaked_str = f"\n│  leaked: {leaked_summary}{more_suffix}"
        else:
            fd_str = green(fmt_count(self.fd_delta))
            leaked_str = ""

        thread_str = fmt_count(self.thread_delta)
        if self.thread_delta > 0 and self.new_thread_names:
            thread_str += f" (new: {', '.join(self.new_thread_names[:3])})"

        lines = [
            f"  memory rss      {mem_str}",
            f"  memory peak     {fmt_bytes(self.peak_rss_bytes)}",
            f"  virtual mem     {fmt_bytes(self.vms_delta_bytes)}",
            f"  open fds        {fd_str}{leaked_str}",
            f"  threads         {thread_str}",
            f"  child procs     {fmt_count(self.child_delta)}",
            f"  elapsed         {self.elapsed_ms:.1f} ms",
        ]
        return "\n".join(lines)


__all__ = ["ResourceDelta"]
