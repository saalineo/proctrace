from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ResourceDelta:
    rss_delta_bytes: int  # RSS at exit minus RSS at entry,it can be in negative
    vms_delta_bytes: int  # VMS
    fd_delta: int  # open fd count at exit minus at entry
    peak_rss_bytes: int  # highest RSS seen by background sampler
    thread_delta: int  # thread count at exit minus at entry
    child_delta: int  # child process count at exit minus at entry
    elapsed_ns: int  # wall clock time of the watched block in nanoseconds
    leaked_fds: list[str]  # fds present at exit but NOT at entry
    new_thread_names: list[str]  # names of threads created during the block
    sample_interval_ms: int = 100  # background sampler interval in milliseconds

    @property
    def sample_interval_s(self) -> float:
        return self.sample_interval_ms / 1000.0

    def is_comparable_with(self, other: ResourceDelta) -> bool:
        """Check if another ResourceDelta was measured using the same sample rate."""
        if not isinstance(other, ResourceDelta):
            return False
        return self.sample_interval_ms == other.sample_interval_ms

    @property
    def rss_delta_mb(self) -> float:
        return self.rss_delta_bytes / (1024 * 1024)

    @property
    def vms_delta_mb(self) -> float:
        return self.vms_delta_bytes / (1024 * 1024)

    @property
    def peak_rss_mb(self) -> float:
        return self.peak_rss_bytes / (1024 * 1024)

    @property
    def elapsed_ms(self) -> float:
        return self.elapsed_ns / 1_000_000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResourceDelta:
        return cls(**d)


    def report(self) -> str:

        use_color = sys.stderr.isatty() if hasattr(sys.stderr, "isatty") else False

        def green(s: str) -> str:
            return f"\033[32m{s}\033[0m" if use_color else s

        def yellow(s: str) -> str:
            return f"\033[33m{s}\033[0m" if use_color else s

        def red(s: str) -> str:
            return f"\033[31m{s}\033[0m" if use_color else s

        def fmt_bytes(n: int) -> str:
            sign = "+" if n >= 0 else ""
            mb = n / (1024 * 1024)
            return f"{sign}{mb:.2f} MB"

        def fmt_count(n: int) -> str:
            sign = "+" if n >= 0 else ""
            return f"{sign}{n}"

        mem_str = fmt_bytes(self.rss_delta_bytes)
        if self.rss_delta_bytes > 10 * 1024 * 1024:
            mem_str = red(mem_str)
        elif self.rss_delta_bytes > 1 * 1024 * 1024:
            mem_str = yellow(mem_str)
        else:
            mem_str = green(mem_str)

        fd_str = fmt_count(self.fd_delta)
        leaked_str = ""
        if self.leaked_fds:
            fd_str = red(fd_str)
            leaked_str = f"\n│  ⚠ leaked: {', '.join(self.leaked_fds[:3])}"
            if len(self.leaked_fds) > 3:
                leaked_str += f" (+{len(self.leaked_fds) - 3} more)"
        else:
            fd_str = green(fd_str)

        thread_str = fmt_count(self.thread_delta)
        if self.thread_delta > 0 and self.new_thread_names:
            thread_str += f" (new: {', '.join(self.new_thread_names[:3])})"

        lines = [
            f"  memory rss      {mem_str}",
            f"  memory peak     {fmt_bytes(self.peak_rss_bytes)}",
            f"  virtual mem     {fmt_bytes(self.vms_delta_bytes)}",
            f"  open fds        {fd_str}" + leaked_str,
            f"  threads         {thread_str}",
            f"  child procs     {fmt_count(self.child_delta)}",
            f"  elapsed         {self.elapsed_ms:.1f} ms",
        ]
        return "\n".join(lines)
