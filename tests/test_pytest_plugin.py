from __future__ import annotations

import json
from pathlib import Path


class TestPytestPlugin:
    def test_inactive_without_flag(self, pytester):
        pytester.makepyfile("def test_ok(): assert True")
        result = pytester.runpytest("-v")
        result.stdout.no_fnmatch_line("*proctrace resource summary*")

    def test_summary_shown_with_flag(self, pytester):
        pytester.makepyfile("def test_ok(): assert True")
        result = pytester.runpytest("--proctrace", "-v")
        result.stdout.fnmatch_lines(["*proctrace resource summary*"])
        result.stdout.fnmatch_lines(["*test_ok*"])

    def test_jsonl_output_written(self, pytester, tmp_path):
        out = tmp_path / "out.jsonl"
        pytester.makepyfile("def test_a(): assert True\n\ndef test_b(): assert True\n")
        result = pytester.runpytest(
            "--proctrace",
            "--proctrace-output",
            str(out),
        )
        assert result.ret == 0

        lines = out.read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            entry = json.loads(line)
            assert "rss_delta_mb" in entry
            assert "elapsed_ms" in entry
            assert "sample_interval_ms" in entry
            assert entry["sample_interval_ms"] == 100
        labels = {json.loads(line)["label"] for line in lines}
        assert any(labels)  # each entry labeled with a test nodeid

    def test_custom_interval_flag(self, pytester, tmp_path):
        out = tmp_path / "out.jsonl"
        pytester.makepyfile("def test_custom(): assert True\n")
        result = pytester.runpytest(
            "--proctrace",
            "--proctrace-output",
            str(out),
            "--proctrace-interval",
            "0.05",
        )
        assert result.ret == 0

        lines = out.read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["sample_interval_ms"] == 50

    def test_help_lists_options(self, pytester):
        result = pytester.runpytest("--help")
        result.stdout.fnmatch_lines([
            "*--proctrace*",
            "*--proctrace-output*",
            "*--proctrace-interval*",
            "*--proctrace-baseline*",
            "*--proctrace-update-baseline*",
            "*--proctrace-fail-on-regression*",
        ])

    def test_marker_fails_on_fd_leak(self, pytester):
        pytester.makepyfile("""
            import pytest
            import tempfile

            leaked = []

            @pytest.mark.proctrace(max_fd_delta=0)
            def test_leaky():
                f = tempfile.NamedTemporaryFile(delete=False)
                leaked.append(f)
        """)
        result = pytester.runpytest()
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*proctrace: leaked 1 fd(s)*"])

    def test_marker_passes_when_clean(self, pytester):
        pytester.makepyfile("""
            import pytest

            @pytest.mark.proctrace(max_fd_delta=0, max_threads=0)
            def test_clean():
                pass
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)

    def test_marker_suppressed_by_allow_leaked(self, pytester):
        pytester.makepyfile("""
            import pytest
            import tempfile

            leaked = []

            @pytest.mark.proctrace(max_fd_delta=0, allow_leaked=[r".*"])
            def test_leaky_suppressed():
                f = tempfile.NamedTemporaryFile(delete=False)
                leaked.append(f)
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)

    def test_fixture_assert_no_leaks(self, pytester):
        pytester.makepyfile("""
            import tempfile

            def test_clean_probe(proctrace_probe):
                with proctrace_probe.assert_no_leaks():
                    x = 1 + 1

            def test_leaky_probe(proctrace_probe):
                leaked = []
                with proctrace_probe.assert_no_leaks():
                    f = tempfile.NamedTemporaryFile(delete=False)
                    leaked.append(f)
        """)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1, failed=1)
        result.stdout.fnmatch_lines(["*proctrace leak assertion failed*"])

    def test_natural_failure_preserves_error(self, pytester):
        pytester.makepyfile("""
            import pytest
            import tempfile

            leaked = []

            @pytest.mark.proctrace(max_fd_delta=0)
            def test_fails_naturally():
                f = tempfile.NamedTemporaryFile(delete=False)
                leaked.append(f)
                assert 1 == 2, "original_assertion_failure"
        """)
        result = pytester.runpytest()
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines([
            "*original_assertion_failure*",
            "*proctrace*",
        ])

    def test_baseline_creation_and_regression_failure(self, pytester, tmp_path, monkeypatch):
        baseline = tmp_path / "baseline.json"
        pytester.makepyfile("""
            import mmap
            import os
            import pytest

            def allocate_dirty_mb(n: int) -> mmap.mmap:
                total = n * 1024 * 1024
                region = mmap.mmap(-1, total)
                for i in range(0, total, 4096):
                    region[i] = 1
                return region

            pinned = []

            @pytest.mark.proctrace()
            def test_alloc():
                size = int(os.environ.get("ALLOC_MB", "10"))
                pinned.append(allocate_dirty_mb(size))
        """)

        # 1. Generate baseline
        res1 = pytester.runpytest_subprocess(
            "--proctrace",
            "--proctrace-baseline",
            str(baseline),
            "--proctrace-update-baseline",
        )
        assert res1.ret == 0
        assert baseline.is_file()

        # 2. Run with same size -> passes
        res2 = pytester.runpytest_subprocess(
            "--proctrace",
            "--proctrace-baseline",
            str(baseline),
            "--proctrace-fail-on-regression",
            "10%",
        )
        res2.assert_outcomes(passed=1)

        # 3. Run with large increase -> fails
        monkeypatch.setenv("ALLOC_MB", "40")
        res3 = pytester.runpytest_subprocess(
            "--proctrace",
            "--proctrace-baseline",
            str(baseline),
            "--proctrace-fail-on-regression",
            "10%",
        )
        res3.assert_outcomes(failed=1)
        res3.stdout.fnmatch_lines(["*peak RSS regressed*"])
