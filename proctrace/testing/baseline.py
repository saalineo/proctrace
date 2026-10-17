"""
Performance baselines and regression detection.

Provides atomic persistence, schema versioning, tolerance parsing,
and multi-metric regression checks for CI gating.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from proctrace.core.exceptions import ConfigurationError
from proctrace.core.types import ResourceDelta

SCHEMA_VERSION: Final[int] = 1
_BYTES_PER_MB: Final[int] = 1024 * 1024
_BYTES_PER_KB: Final[int] = 1024
_BYTES_PER_GB: Final[int] = 1024 * 1024 * 1024
_DEFAULT_MEMORY_FLOOR_BYTES: Final[int] = 1 * _BYTES_PER_MB
_DEFAULT_TIME_FLOOR_MS: Final[float] = 5.0


@dataclass(slots=True, frozen=True)
class BaselineEntry:
    """Stored performance baseline for a single test nodeid."""

    peak_rss_bytes: int
    fd_delta: int
    elapsed_ms: float
    samples: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaselineEntry:
        return cls(
            peak_rss_bytes=int(data.get("peak_rss_bytes", 0)),
            fd_delta=int(data.get("fd_delta", 0)),
            elapsed_ms=float(data.get("elapsed_ms", 0.0)),
            samples=int(data.get("samples", 1)),
        )


@dataclass(slots=True)
class BaselineData:
    """Complete baseline file payload with environment metadata."""

    schema_version: int = SCHEMA_VERSION
    platform: str = sys.platform
    python: str = f"{sys.version_info.major}.{sys.version_info.minor}"
    tests: dict[str, BaselineEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "platform": self.platform,
            "python": self.python,
            "tests": {nodeid: entry.to_dict() for nodeid, entry in self.tests.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaselineData:
        tests = {
            nodeid: BaselineEntry.from_dict(raw)
            for nodeid, raw in data.get("tests", {}).items()
        }
        return cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            platform=str(data.get("platform", sys.platform)),
            python=str(data.get("python", f"{sys.version_info.major}.{sys.version_info.minor}")),
            tests=tests,
        )


@dataclass(slots=True, frozen=True)
class Tolerance:
    """Parsed tolerance budget for regression comparisons."""

    raw: str
    percent: float = 0.0
    absolute_bytes: int = 0


def parse_tolerance(spec: str) -> Tolerance:
    """Parse a tolerance string into a structured Tolerance instance.

    Supported formats:
        - "10%" -> 10% relative regression tolerance
        - "5MB", "500KB", "1GB" -> absolute byte tolerance
    """
    cleaned = spec.strip()
    if cleaned.endswith("%"):
        try:
            val = float(cleaned[:-1])
            if val < 0:
                raise ValueError
            return Tolerance(raw=cleaned, percent=val / 100.0)
        except ValueError as err:
            raise ConfigurationError(f"Invalid percentage tolerance: {spec!r}") from err

    byte_match = re.match(r"^(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)?$", cleaned, re.IGNORECASE)
    if byte_match:
        number = float(byte_match.group(1))
        unit = (byte_match.group(2) or "B").upper()
        multipliers = {
            "B": 1,
            "KB": _BYTES_PER_KB,
            "MB": _BYTES_PER_MB,
            "GB": _BYTES_PER_GB,
        }
        bytes_val = int(number * multipliers[unit])
        return Tolerance(raw=cleaned, absolute_bytes=bytes_val)

    raise ConfigurationError(
        f"Unrecognized tolerance format: {spec!r}. Use e.g. '10%' or '5MB'."
    )


def load_baseline(path: Path | str) -> BaselineData:
    """Load and parse baseline file from disk."""
    p = Path(path)
    if not p.is_file():
        return BaselineData()

    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return BaselineData()

    try:
        raw = json.loads(text)
        return BaselineData.from_dict(raw)
    except Exception as err:
        warnings.warn(
            f"Failed to load proctrace baseline from {p}: {err}",
            UserWarning,
            stacklevel=2,
        )
        return BaselineData()


def save_baseline(path: Path | str, data: BaselineData) -> None:
    """Persist baseline data atomically using a temporary file and replace."""
    p = Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        dir=p.parent,
        delete=False,
        encoding="utf-8",
        prefix=".tmp_proctrace_baseline_",
    ) as tmp:
        json.dump(data.to_dict(), tmp, indent=2)
        tmp_name = tmp.name

    os.replace(tmp_name, p)


def is_compatible_environment(data: BaselineData) -> bool:
    """Check whether baseline was generated on a compatible OS and major Python version."""
    if data.platform != sys.platform:
        return False
    current_major = str(sys.version_info.major)
    baseline_major = data.python.split(".")[0]
    return current_major == baseline_major


def compare_entry(
    entry: BaselineEntry,
    delta: ResourceDelta,
    tolerance: Tolerance,
) -> list[str]:
    """Compare a ResourceDelta against an existing baseline entry.

    Returns a list of regression violations (empty list means passed).
    """
    violations: list[str] = []

    # 1. Open FDs: any increase beyond baseline is a regression
    if delta.fd_delta > entry.fd_delta:
        violations.append(
            f"FD delta regressed: +{delta.fd_delta} (baseline was {entry.fd_delta})"
        )

    # 2. Peak RSS: percentage or absolute tolerance with minimum noise floor
    allowed_rss_increase = max(
        int(entry.peak_rss_bytes * tolerance.percent),
        tolerance.absolute_bytes,
        _DEFAULT_MEMORY_FLOOR_BYTES,
    )
    max_allowed_peak_rss = entry.peak_rss_bytes + allowed_rss_increase

    if delta.peak_rss_bytes > max_allowed_peak_rss:
        current_mb = delta.peak_rss_bytes / _BYTES_PER_MB
        baseline_mb = entry.peak_rss_bytes / _BYTES_PER_MB
        diff_mb = (delta.peak_rss_bytes - entry.peak_rss_bytes) / _BYTES_PER_MB
        violations.append(
            f"peak RSS regressed by {diff_mb:.1f} MB to {current_mb:.1f} MB "
            f"(baseline: {baseline_mb:.1f} MB, tolerance: {tolerance.raw})"
        )

    # 3. Elapsed Time: percentage tolerance with minimum 5ms noise floor
    if tolerance.percent > 0:
        allowed_time_increase = max(
            entry.elapsed_ms * tolerance.percent,
            _DEFAULT_TIME_FLOOR_MS,
        )
        max_allowed_elapsed_ms = entry.elapsed_ms + allowed_time_increase
        if delta.elapsed_ms > max_allowed_elapsed_ms:
            diff_ms = delta.elapsed_ms - entry.elapsed_ms
            violations.append(
                f"elapsed time regressed by {diff_ms:.1f} ms to {delta.elapsed_ms:.1f} ms "
                f"(baseline: {entry.elapsed_ms:.1f} ms, tolerance: {tolerance.raw})"
            )

    return violations


__all__ = [
    "SCHEMA_VERSION",
    "BaselineData",
    "BaselineEntry",
    "Tolerance",
    "compare_entry",
    "is_compatible_environment",
    "load_baseline",
    "parse_tolerance",
    "save_baseline",
]
