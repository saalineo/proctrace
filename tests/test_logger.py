from __future__ import annotations

import json
import socket

import proctrace
from proctrace.core import ResourceDelta
from proctrace.telemetry import ProctraceLogger


def make_delta(rss_mb: float = 1.0) -> ResourceDelta:
    mb = 1024 * 1024
    return ResourceDelta(
        rss_delta_bytes=int(rss_mb * mb),
        vms_delta_bytes=0,
        fd_delta=0,
        peak_rss_bytes=int(rss_mb * mb),
        thread_delta=0,
        child_delta=0,
        elapsed_ns=1_500_000,
        leaked_fds=[],
        new_thread_names=[],
    )


class TestJsonlFormat:
    def test_writes_one_valid_json_per_line(self, tmp_path):
        path = tmp_path / "out.jsonl"

        logger = ProctraceLogger(str(path))
        logger.log(make_delta(2.0), label="a")
        logger.log(make_delta(4.0), label="b")
        logger.close()

        lines = path.read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # must not raise

    def test_entries_have_expected_fields(self, tmp_path):
        path = tmp_path / "out.jsonl"

        logger = ProctraceLogger(str(path))
        logger.log(make_delta(3.0), label="work")
        logger.close()

        entry = json.loads(path.read_text().splitlines()[0])
        assert entry["label"] == "work"
        assert entry["rss_delta_mb"] == 3.0
        assert entry["elapsed_ms"] == 1.5
        assert "timestamp_iso" in entry
        assert entry["fd_delta"] == 0
        assert entry["leaked_fds"] == []

    def test_min_rss_delta_suppresses_small_deltas(self, tmp_path):
        path = tmp_path / "out.jsonl"

        logger = ProctraceLogger(str(path), min_rss_delta_mb=100.0)
        logger.log(make_delta(1.0))
        logger.close()

        assert path.read_text() == ""

    def test_min_rss_delta_zero_logs_everything(self, tmp_path):
        path = tmp_path / "out.jsonl"

        logger = ProctraceLogger(str(path), min_rss_delta_mb=0.0)
        logger.log(make_delta(0.0), label="zero")
        logger.close()

        lines = path.read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["rss_delta_mb"] == 0.0


class TestJsonFormat:
    def test_writes_array_on_close(self, tmp_path):
        path = tmp_path / "out.json"

        logger = ProctraceLogger(str(path), format="json")
        logger.log(make_delta(1.0), label="one")
        logger.log(make_delta(2.0), label="two")
        logger.close()

        entries = json.loads(path.read_text())
        assert isinstance(entries, list)
        assert [e["label"] for e in entries] == ["one", "two"]


class TestTextFormat:
    def test_writes_human_report(self, tmp_path):
        path = tmp_path / "out.txt"

        logger = ProctraceLogger(str(path), format="text")
        logger.log(make_delta(5.0))
        logger.close()

        content = path.read_text()
        assert "memory rss" in content
        assert "rss" in content


class TestSocketOutput:
    def test_sends_jsonl_over_socket(self):
        left, right = socket.socketpair()

        logger = ProctraceLogger(left)
        logger.log(make_delta(2.0), label="net")
        logger.close()

        data = right.recv(4096)
        entry = json.loads(data.decode("utf-8").splitlines()[0])
        assert entry["label"] == "net"
        assert entry["rss_delta_mb"] == 2.0

        left.close()
        right.close()


class TestAttach:
    def test_attach_autologs_when_watcher_exits(self, tmp_path):
        path = tmp_path / "out.jsonl"

        watcher = proctrace.watch(memory=True, fds=False, threads=False)
        logger = ProctraceLogger(str(path))
        logger.attach(watcher)

        with watcher:
            pass

        logger.close()

        lines = path.read_text().splitlines()
        assert len(lines) == 1
        json.loads(lines[0])


class TestLifecycle:
    def test_context_manager_closes_output(self, tmp_path):
        path = tmp_path / "out.jsonl"

        with ProctraceLogger(str(path)) as logger:
            logger.log(make_delta(1.0))

        lines = path.read_text().splitlines()
        assert len(lines) == 1

    def test_appends_to_existing_file(self, tmp_path):
        path = tmp_path / "out.jsonl"
        path.write_text("")

        logger = ProctraceLogger(str(path))
        logger.log(make_delta(1.0), label="first")
        logger.close()

        logger = ProctraceLogger(str(path))
        logger.log(make_delta(2.0), label="second")
        logger.close()

        labels = [json.loads(line)["label"] for line in path.read_text().splitlines()]
        assert labels == ["first", "second"]
