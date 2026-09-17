from __future__ import annotations

import json


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
        result.stdout.fnmatch_lines(["*--proctrace*", "*--proctrace-output*", "*--proctrace-interval*"])
