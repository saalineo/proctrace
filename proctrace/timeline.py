from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence


@dataclass(slots=True)
class Timeline:
    """Time-series recording of resource samples captured during a monitored block.

    Attributes:
        t_ms: Relative timestamps in milliseconds since monitoring started.
        rss: Resident Set Size in bytes for each sample.
        vms: Virtual Memory Size in bytes for each sample.
        fds: Open file descriptor count for each sample.
    """

    t_ms: list[float] = field(default_factory=list)
    rss: list[int] = field(default_factory=list)
    vms: list[int] = field(default_factory=list)
    fds: list[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.t_ms)

    @classmethod
    def from_samples(cls, samples: Sequence[tuple[int, int, int, int]]) -> Timeline:
        """Construct a Timeline from raw (t_ns, rss, vms, fds) sampler tuples."""
        t_ms: list[float] = []
        rss: list[int] = []
        vms: list[int] = []
        fds: list[int] = []
        for t_ns, r, v, f in samples:
            t_ms.append(t_ns / 1_000_000.0)
            rss.append(r)
            vms.append(v)
            fds.append(f)
        return cls(t_ms=t_ms, rss=rss, vms=vms, fds=fds)

    @property
    def peak_at_ms(self) -> float | None:
        """Timestamp in milliseconds when peak RSS was observed."""
        if not self.rss:
            return None
        max_idx = max(range(len(self.rss)), key=lambda i: self.rss[i])
        return self.t_ms[max_idx]

    @property
    def min_rss_bytes(self) -> int | None:
        """Minimum RSS observed in bytes."""
        return min(self.rss) if self.rss else None

    @property
    def max_rss_bytes(self) -> int | None:
        """Maximum RSS observed in bytes."""
        return max(self.rss) if self.rss else None

    @property
    def mean_rss_bytes(self) -> float | None:
        """Mean RSS observed in bytes."""
        return sum(self.rss) / len(self.rss) if self.rss else None

    def compute_slope(self, warmup_ms: float = 0.0) -> float | None:
        """Compute ordinary least squares RSS growth rate in bytes/second."""
        pts = [(t / 1000.0, r) for t, r in zip(self.t_ms, self.rss) if t >= warmup_ms]
        if len(pts) < 3:
            return None

        n = len(pts)
        t_s = [p[0] for p in pts]
        r = [p[1] for p in pts]
        mt = sum(t_s) / n
        mr = sum(r) / n

        denom = sum((x - mt) ** 2 for x in t_s)
        if denom == 0.0:
            return 0.0
        return sum((x - mt) * (y - mr) for x, y in zip(t_s, r)) / denom

    def compute_r2(self, warmup_ms: float = 0.0) -> float:
        """Compute coefficient of determination (R^2) for the linear fit."""
        pts = [(t / 1000.0, r) for t, r in zip(self.t_ms, self.rss) if t >= warmup_ms]
        if len(pts) < 3:
            return 0.0

        n = len(pts)
        t_s = [p[0] for p in pts]
        r = [p[1] for p in pts]
        mt = sum(t_s) / n
        mr = sum(r) / n

        denom = sum((x - mt) ** 2 for x in t_s)
        if denom == 0.0:
            return 0.0
        slope = sum((x - mt) * (y - mr) for x, y in zip(t_s, r)) / denom

        ss_tot = sum((y - mr) ** 2 for y in r)
        if ss_tot == 0.0:
            return 1.0 if abs(slope) < 1e-9 else 0.0

        pred = [mr + slope * (x - mt) for x in t_s]
        ss_res = sum((y - p) ** 2 for y, p in zip(r, pred))
        return max(0.0, min(1.0, 1.0 - (ss_res / ss_tot)))

    @property
    def rss_slope_bytes_per_s(self) -> float | None:
        """RSS growth slope in bytes per second."""
        return self.compute_slope(warmup_ms=0.0)

    @property
    def r2(self) -> float:
        """Coefficient of determination (R^2) of the RSS linear fit."""
        return self.compute_r2(warmup_ms=0.0)

    def is_growing(
        self,
        threshold_bytes_per_s: float = 100_000.0,
        min_r2: float = 0.8,
        warmup_ms: float = 0.0,
    ) -> bool:
        """Determine if RSS shows sustained linear growth indicating a potential leak.

        Requires:
        1. At least 3 samples after warmup_ms.
        2. Positive slope exceeding threshold_bytes_per_s.
        3. Good linear fit with R^2 >= min_r2.
        4. Last quarter mean strictly greater than first quarter mean.
        """
        pts = [r for t, r in zip(self.t_ms, self.rss) if t >= warmup_ms]
        if len(pts) < 3:
            return False

        slope = self.compute_slope(warmup_ms=warmup_ms)
        if slope is None or slope <= threshold_bytes_per_s:
            return False

        r2 = self.compute_r2(warmup_ms=warmup_ms)
        if r2 < min_r2:
            return False

        q_len = max(1, len(pts) // 4)
        first_q_mean = sum(pts[:q_len]) / q_len
        last_q_mean = sum(pts[-q_len:]) / q_len
        return last_q_mean > first_q_mean

    def to_dict(self) -> dict[str, Any]:
        """Convert Timeline to a dictionary representation."""
        return asdict(self)

    def to_json(self, indent: int | None = None) -> str:
        """Serialize Timeline to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Timeline:
        """Construct a Timeline from a dictionary representation."""
        return cls(
            t_ms=[float(x) for x in d.get("t_ms", [])],
            rss=[int(x) for x in d.get("rss", [])],
            vms=[int(x) for x in d.get("vms", [])],
            fds=[int(x) for x in d.get("fds", [])],
        )

    @classmethod
    def from_json(cls, s: str) -> Timeline:
        """Deserialize a Timeline from a JSON string."""
        data = json.loads(s)
        if isinstance(data, dict):
            if "timeline" in data and isinstance(data["timeline"], dict):
                data = data["timeline"]
            return cls.from_dict(data)
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    def to_svg(
        self,
        title: str = "proctrace Resource Timeline",
        width: int = 900,
        height: int = 450,
    ) -> str:
        """Render this Timeline to a standalone SVG string."""
        from proctrace.telemetry.plot import render_svg

        return render_svg(self, title=title, width=width, height=height)

    def to_html(
        self,
        title: str = "proctrace Resource Timeline",
        width: int = 900,
        height: int = 450,
    ) -> str:
        """Render this Timeline to a standalone HTML string."""
        from proctrace.telemetry.plot import render_html

        return render_html(self, title=title, width=width, height=height)

    def plot(
        self,
        output_path: str | Any,
        title: str = "proctrace Resource Timeline",
        width: int = 900,
        height: int = 450,
    ) -> None:
        """Export this Timeline as an HTML or SVG plot file."""
        from proctrace.telemetry.plot import plot_timeline

        plot_timeline(self, output_path, title=title, width=width, height=height)


__all__ = ["Timeline"]

