from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field, fields
from typing import TYPE_CHECKING, Any, Final

from proctrace.timeline import Timeline

if TYPE_CHECKING:
    from proctrace.alloc import AllocationReport, ObjectGrowth

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
        timeline: Optional high-frequency timeline of resource usage.
        peak_at_ms: Relative timestamp in milliseconds when peak RSS was reached.
        allocations: Optional memory allocation attribution report.
        object_growth: Optional object count growth delta dictionary.
        cpu_user_ns: User-mode CPU time difference in nanoseconds.
        cpu_sys_ns: Kernel-mode CPU time difference in nanoseconds.
        ctx_switches_vol: Voluntary context switch difference.
        ctx_switches_invol: Involuntary context switch difference.
        minor_faults: Minor page fault difference.
        major_faults: Major page fault difference.
        io_read_bytes: Actual disk read bytes difference.
        io_write_bytes: Actual disk write bytes difference.
        io_rchar_bytes: Syscall read bytes difference.
        io_wchar_bytes: Syscall write bytes difference.
        sockets_by_state: Mapping of open TCP socket state names to active socket count.
        socket_deltas: Net change in socket counts by state over the watched block.
        busiest_threads: List of (thread_name, cpu_percent) sorted by highest CPU.
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
    timeline: Timeline | None = None
    peak_at_ms: float | None = None
    allocations: AllocationReport | None = None
    object_growth: dict[str, int] | None = None
    cpu_user_ns: int = 0
    cpu_sys_ns: int = 0
    ctx_switches_vol: int = 0
    ctx_switches_invol: int = 0
    minor_faults: int = 0
    major_faults: int = 0
    io_read_bytes: int = 0
    io_write_bytes: int = 0
    io_rchar_bytes: int = 0
    io_wchar_bytes: int = 0
    sockets_by_state: dict[str, int] = field(default_factory=dict)
    socket_deltas: dict[str, int] = field(default_factory=dict)
    busiest_threads: list[tuple[str, float]] = field(default_factory=list)

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

    @property
    def cpu_user_s(self) -> float:
        """User CPU time in seconds."""
        return self.cpu_user_ns / 1_000_000_000.0

    @property
    def cpu_sys_s(self) -> float:
        """System CPU time in seconds."""
        return self.cpu_sys_ns / 1_000_000_000.0

    @property
    def cpu_percent(self) -> float:
        """CPU utilization percentage across all cores during the interval."""
        if self.elapsed_ns <= 0:
            return 0.0
        elapsed_s = self.elapsed_ns / 1_000_000_000.0
        return ((self.cpu_user_s + self.cpu_sys_s) / elapsed_s) * 100.0

    @property
    def io_read_mb(self) -> float:
        """Disk read bytes expressed in megabytes (MiB)."""
        return self.io_read_bytes / _BYTES_PER_MB

    @property
    def io_write_mb(self) -> float:
        """Disk write bytes expressed in megabytes (MiB)."""
        return self.io_write_bytes / _BYTES_PER_MB

    @property
    def io_rchar_mb(self) -> float:
        """Syscall read bytes expressed in megabytes (MiB)."""
        return self.io_rchar_bytes / _BYTES_PER_MB

    @property
    def io_wchar_mb(self) -> float:
        """Syscall write bytes expressed in megabytes (MiB)."""
        return self.io_wchar_bytes / _BYTES_PER_MB

    def to_dict(self, include_timeline: bool = False) -> dict[str, Any]:
        """Convert the ResourceDelta to a dictionary.

        Args:
            include_timeline: Whether to include the full timeline array. Defaults to False
                to avoid exploding serialization payload size.
        """
        d = asdict(self)
        if not include_timeline:
            d.pop("timeline", None)
        return d

    def to_json(self, indent: int | None = None, include_timeline: bool = False) -> str:
        """Serialize the ResourceDelta to a JSON string."""
        return json.dumps(self.to_dict(include_timeline=include_timeline), indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResourceDelta:
        """Construct a ResourceDelta instance from a dictionary."""
        data = dict(d)
        tl_data = data.get("timeline")
        if isinstance(tl_data, dict):
            data["timeline"] = Timeline(**tl_data)
        alloc_data = data.get("allocations")
        if isinstance(alloc_data, dict):
            from proctrace.alloc import AllocationReport
            data["allocations"] = AllocationReport.from_dict(alloc_data)
        growth_data = data.get("object_growth")
        if isinstance(growth_data, dict):
            from proctrace.alloc import ObjectGrowth
            data["object_growth"] = ObjectGrowth(growth_data)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

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

        if self.cpu_user_ns > 0 or self.cpu_sys_ns > 0:
            cpu_str = f"{self.cpu_user_s:.3f}s user / {self.cpu_sys_s:.3f}s sys ({self.cpu_percent:.1f}%)"
            lines.append(f"  cpu time        {cpu_str}")

        if self.ctx_switches_vol > 0 or self.ctx_switches_invol > 0:
            ctx_str = f"{self.ctx_switches_vol} vol / {self.ctx_switches_invol} invol"
            lines.append(f"  ctx switches    {ctx_str}")

        if self.minor_faults > 0 or self.major_faults > 0:
            fault_str = f"{self.minor_faults} minor / {self.major_faults} major"
            lines.append(f"  page faults     {fault_str}")

        if (
            self.io_read_bytes > 0
            or self.io_write_bytes > 0
            or self.io_rchar_bytes > 0
            or self.io_wchar_bytes > 0
        ):
            io_str = f"read {fmt_bytes(self.io_read_bytes)} / write {fmt_bytes(self.io_write_bytes)}"
            lines.append(f"  disk io         {io_str}")

        if self.sockets_by_state or self.socket_deltas:
            sock_parts = []
            for state, count in sorted(self.sockets_by_state.items()):
                delta = self.socket_deltas.get(state, 0)
                if delta != 0:
                    delta_str = f" ({fmt_count(delta)})"
                else:
                    delta_str = ""
                sock_parts.append(f"{state}: {count}{delta_str}")
            lines.append(f"  sockets         {', '.join(sock_parts) if sock_parts else 'none'}")

        if self.busiest_threads:
            busiest_str = ", ".join(f"{name} ({pct:.1f}%)" for name, pct in self.busiest_threads[:3])
            lines.append(f"  busiest threads {busiest_str}")

        if (
            (self.cpu_user_ns > 0 or self.cpu_sys_ns > 0)
            and self.cpu_percent < 20.0
            and self.elapsed_ms >= 200.0
        ):
            hint_str = yellow("mostly waiting (I/O or lock bound)")
            lines.append(f"  hint            {hint_str}")

        return "\n".join(lines)


__all__ = ["ResourceDelta", "Timeline"]
