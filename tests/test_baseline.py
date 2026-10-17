from __future__ import annotations

import json
from pathlib import Path

import pytest

from proctrace.core import ResourceDelta
from proctrace.testing import (
    BaselineData,
    BaselineEntry,
    compare_entry,
    load_baseline,
    parse_tolerance,
    save_baseline,
)


def make_delta(
    *,
    peak_mb: float = 10.0,
    fd_delta: int = 0,
    elapsed_ms: float = 20.0,
) -> ResourceDelta:
    mb = 1024 * 1024
    return ResourceDelta(
        rss_delta_bytes=0,
        vms_delta_bytes=0,
        fd_delta=fd_delta,
        peak_rss_bytes=int(peak_mb * mb),
        thread_delta=0,
        child_delta=0,
        elapsed_ns=int(elapsed_ms * 1_000_000),
        leaked_fds=[],
        new_thread_names=[],
    )


def test_parse_tolerance():
    t_pct = parse_tolerance("15%")
    assert t_pct.percent == 0.15
    assert t_pct.absolute_bytes == 0

    t_mb = parse_tolerance("5MB")
    assert t_mb.absolute_bytes == 5 * 1024 * 1024
    assert t_mb.percent == 0.0

    t_kb = parse_tolerance("500KB")
    assert t_kb.absolute_bytes == 500 * 1024

    with pytest.raises(Exception):
        parse_tolerance("invalid")


def test_baseline_save_and_load_roundtrip(tmp_path: Path):
    baseline_file = tmp_path / "baseline.json"
    data = BaselineData()
    data.tests["tests/test_a.py::test_one"] = BaselineEntry(
        peak_rss_bytes=50 * 1024 * 1024,
        fd_delta=0,
        elapsed_ms=15.0,
        samples=3,
    )

    save_baseline(baseline_file, data)
    assert baseline_file.is_file()

    loaded = load_baseline(baseline_file)
    assert loaded.schema_version == 1
    assert "tests/test_a.py::test_one" in loaded.tests
    entry = loaded.tests["tests/test_a.py::test_one"]
    assert entry.peak_rss_bytes == 50 * 1024 * 1024
    assert entry.samples == 3


def test_compare_entry_within_tolerance():
    entry = BaselineEntry(
        peak_rss_bytes=50 * 1024 * 1024,
        fd_delta=0,
        elapsed_ms=100.0,
    )
    delta = make_delta(peak_mb=52.0, fd_delta=0, elapsed_ms=105.0)
    tolerance = parse_tolerance("10%")
    violations = compare_entry(entry, delta, tolerance)
    assert violations == []


def test_compare_entry_peak_rss_regression():
    entry = BaselineEntry(
        peak_rss_bytes=50 * 1024 * 1024,
        fd_delta=0,
        elapsed_ms=100.0,
    )
    delta = make_delta(peak_mb=65.0, fd_delta=0, elapsed_ms=100.0)
    tolerance = parse_tolerance("10%")
    violations = compare_entry(entry, delta, tolerance)
    assert len(violations) == 1
    assert "peak RSS regressed" in violations[0]


def test_compare_entry_fd_regression():
    entry = BaselineEntry(
        peak_rss_bytes=50 * 1024 * 1024,
        fd_delta=0,
        elapsed_ms=100.0,
    )
    delta = make_delta(peak_mb=50.0, fd_delta=1, elapsed_ms=100.0)
    tolerance = parse_tolerance("10%")
    violations = compare_entry(entry, delta, tolerance)
    assert len(violations) == 1
    assert "FD delta regressed" in violations[0]


def test_compare_entry_elapsed_regression():
    entry = BaselineEntry(
        peak_rss_bytes=50 * 1024 * 1024,
        fd_delta=0,
        elapsed_ms=100.0,
    )
    delta = make_delta(peak_mb=50.0, fd_delta=0, elapsed_ms=150.0)
    tolerance = parse_tolerance("20%")
    violations = compare_entry(entry, delta, tolerance)
    assert len(violations) == 1
    assert "elapsed time regressed" in violations[0]
