from __future__ import annotations

import ctypes
import json
import mmap
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest

import proctrace
from proctrace.alloc import (
    AllocEntry,
    AllocationReport,
    AllocationTracker,
    ObjectGrowth,
    get_object_census,
)
from proctrace.core.types import ResourceDelta


class TestTracemallocAttribution:
    def test_allocate_10mb_known_line(self):
        """Allocate 10 MB in a known line: assert the first entry matches filename/lineno and size is ~10MB."""
        target_size = 10 * 1024 * 1024

        with proctrace.watch(attribution="tracemalloc", top=5) as p:
            known_allocation = bytearray(target_size)  # Target allocation site
            current_lineno = sys._getframe().f_lineno - 1

        assert p.result is not None
        assert p.result.allocations is not None
        report = p.result.allocations
        assert len(report.entries) > 0

        top_entry = report.entries[0]
        assert Path(top_entry.filename).name == "test_attribution.py"
        assert top_entry.lineno == current_lineno
        # Size within 20%
        assert abs(top_entry.size_diff - target_size) < 0.20 * target_size
        assert top_entry.size_diff_mb > 9.0

        # Verify report formatting
        rep_text = report.report()
        assert "test_attribution.py" in rep_text
        assert "python-tracked:" in rep_text
        assert "blocks" in rep_text

    def test_block_with_no_allocation(self):
        """Block with no allocation: reports cleanly without exception."""
        with proctrace.watch(attribution="tracemalloc") as p:
            pass

        assert p.result is not None
        assert p.result.allocations is not None
        report = p.result.allocations.report()
        assert isinstance(report, str)
        assert "python-tracked:" in report

    def test_user_already_running_tracemalloc(self):
        """If user is already running tracemalloc, do not stop it on exit."""
        tracemalloc.start()
        try:
            assert tracemalloc.is_tracing()
            with proctrace.watch(attribution="tracemalloc") as p:
                _ = [x for x in range(1000)]
            assert tracemalloc.is_tracing()
        finally:
            tracemalloc.stop()

    def test_native_estimate_positive(self):
        """Allocating through mmap touches pages (untracked by tracemalloc), so native_estimate > 0 and hint appears."""
        alloc_size = 20 * 1024 * 1024

        with proctrace.watch(attribution="tracemalloc") as p:
            mm = mmap.mmap(-1, alloc_size)
            mm.write(b"\xaa" * alloc_size)

        assert p.result is not None
        assert p.result.allocations is not None
        alloc_rep = p.result.allocations
        # tracemalloc should not track mmap page allocations as Python bytes
        assert alloc_rep.python_tracked_bytes < 5 * 1024 * 1024
        # Process RSS should have grown
        if p.result.rss_delta_bytes >= alloc_size * 0.7:
            assert alloc_rep.native_estimate_bytes > 0
            rep_text = alloc_rep.report()
            if alloc_rep.native_estimate_bytes > (0.5 * p.result.rss_delta_bytes):
                assert "growth is mostly outside Python objects" in rep_text

    def test_deep_stack_frames(self):
        """Multiple frames capture traceback call chains."""
        def level_c():
            return bytearray(5 * 1024 * 1024)

        def level_b():
            return level_c()

        def level_a():
            return level_b()

        with proctrace.watch(attribution="tracemalloc", frames=5) as p:
            buf = level_a()

        assert p.result is not None
        assert p.result.allocations is not None
        assert len(p.result.allocations.entries) > 0
        top = p.result.allocations.entries[0]
        assert top.traceback_lines is not None
        assert len(top.traceback_lines) > 1
        rep_text = p.result.allocations.report()
        assert "level_c" in rep_text or "level_a" in rep_text


class TestObjectGrowth:
    def test_object_census_local_class(self):
        """Create 1000 instances of a local class: assert growth is about 1000."""
        class WidgetRecord:
            def __init__(self, val: int):
                self.val = val

        with proctrace.watch(attribution="objects") as p:
            widgets = [WidgetRecord(i) for i in range(1000)]

        assert p.result is not None
        assert p.result.object_growth is not None
        growth = p.result.object_growth

        # Test both short name lookup and qualified lookup
        assert "WidgetRecord" in growth or growth["WidgetRecord"] >= 1000
        assert growth.get("WidgetRecord", 0) >= 1000
        assert growth.get("NonExistentType", 0) == 0

    def test_attribution_all_mode(self):
        """'all' mode captures both tracemalloc and object growth simultaneously."""
        class MarkerObject:
            pass

        with proctrace.watch(attribution="all", top=5) as p:
            objs = [MarkerObject() for _ in range(500)]
            raw = bytearray(2 * 1024 * 1024)

        assert p.result is not None
        assert p.result.allocations is not None
        assert p.result.object_growth is not None
        assert len(p.result.allocations.entries) > 0
        assert p.result.object_growth["MarkerObject"] >= 500


class TestSerialization:
    def test_json_and_dict_roundtrip(self):
        """Verify to_dict and from_dict roundtrip with allocations and object_growth."""
        with proctrace.watch(attribution="all") as p:
            data = [dict(a=i) for i in range(100)]

        delta = p.result
        assert delta is not None
        d = delta.to_dict()
        assert "allocations" in d
        assert "object_growth" in d

        # Roundtrip through JSON
        json_str = delta.to_json()
        restored_d = json.loads(json_str)
        restored = ResourceDelta.from_dict(restored_d)

        assert restored.allocations is not None
        assert restored.object_growth is not None
        assert restored.allocations.python_tracked_bytes == delta.allocations.python_tracked_bytes
        assert len(restored.allocations.entries) == len(delta.allocations.entries)


class TestDecoratorsAndWarmup:
    def test_probe_with_attribution_and_warmup(self):
        """@probe with attribution and warmup executes cleanly."""
        calls = 0

        @proctrace.probe(attribution="tracemalloc", warmup=2, output="none")
        def work():
            nonlocal calls
            calls += 1
            return bytearray(1024 * 1024)

        result = work()
        assert len(result) == 1024 * 1024
        # 2 warmups + 1 actual run = 3 calls
        assert calls == 3
        delta = work.last_probe_result
        assert delta is not None
        assert delta.allocations is not None
        assert len(delta.allocations.entries) > 0

    @pytest.mark.asyncio
    async def test_async_probe_with_attribution_and_warmup(self):
        """Async @probe with attribution and warmup."""
        calls = 0

        @proctrace.probe(attribution="objects", warmup=1, output="none")
        async def async_work():
            nonlocal calls
            calls += 1
            return [i for i in range(500)]

        res = await async_work()
        assert len(res) == 500
        assert calls == 2
        delta = async_work.last_probe_result
        assert delta is not None
        assert delta.object_growth is not None


class TestCliAttribution:
    def test_cli_run_attribution_json(self):
        """proctrace run --attribution --json outputs allocation details."""
        code = (
            "import sys\n"
            "buf = bytearray(5 * 1024 * 1024)\n"
        )
        cmd = [
            sys.executable,
            "-m",
            "proctrace",
            "run",
            "--attribution",
            "--json",
            "--",
            sys.executable,
            "-c",
            code,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        assert data["exit_code"] == 0
        assert "allocations" in data
        allocs = data["allocations"]
        assert allocs["python_tracked_bytes"] > 4 * 1024 * 1024
        assert len(allocs["entries"]) > 0
