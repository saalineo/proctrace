from __future__ import annotations

from proctrace.core import ResourceDelta
from proctrace.testing import Limits


def make_delta(
    *,
    rss_mb: float = 0.0,
    peak_mb: float = 0.0,
    fd_delta: int = 0,
    thread_delta: int = 0,
    child_delta: int = 0,
    elapsed_ms: float = 10.0,
    leaked_fds: list[str] | None = None,
    new_thread_names: list[str] | None = None,
) -> ResourceDelta:
    mb = 1024 * 1024
    return ResourceDelta(
        rss_delta_bytes=int(rss_mb * mb),
        vms_delta_bytes=0,
        fd_delta=fd_delta,
        peak_rss_bytes=int(peak_mb * mb),
        thread_delta=thread_delta,
        child_delta=child_delta,
        elapsed_ns=int(elapsed_ms * 1_000_000),
        leaked_fds=leaked_fds or [],
        new_thread_names=new_thread_names or [],
    )


def test_empty_limits_pass_everything():
    limits = Limits()
    delta = make_delta(rss_mb=500.0, fd_delta=10, leaked_fds=["/tmp/a", "/tmp/b"])
    violations = limits.check(delta)
    assert violations == []


def test_fd_limit_violation_and_reporting():
    limits = Limits(max_fd_delta=0)
    delta = make_delta(fd_delta=2, leaked_fds=["/tmp/leak1", "socket:[999]"])
    violations = limits.check(delta)
    assert len(violations) == 1
    assert "leaked 2 fd(s)" in violations[0]
    assert "/tmp/leak1" in violations[0]
    assert "socket:[999]" in violations[0]


def test_allow_leaked_regex_suppression():
    limits = Limits.from_options(
        max_fd_delta=0,
        allow_leaked=[r"^/tmp/pytest-.*", r"socket:\[.*\]"],
    )
    delta = make_delta(
        fd_delta=2,
        leaked_fds=["/tmp/pytest-temp-123", "socket:[4444]"],
    )
    violations = limits.check(delta)
    assert violations == []


def test_allow_leaked_partial_suppression():
    limits = Limits.from_options(
        max_fd_delta=0,
        allow_leaked=[r"^/tmp/pytest-.*"],
    )
    delta = make_delta(
        fd_delta=2,
        leaked_fds=["/tmp/pytest-temp-123", "/var/log/leaked.log"],
    )
    violations = limits.check(delta)
    assert len(violations) == 1
    assert "leaked 1 fd(s)" in violations[0]
    assert "/var/log/leaked.log" in violations[0]
    assert "/tmp/pytest-temp-123" not in violations[0]


def test_thread_limit_and_allow_threads():
    limits = Limits.from_options(
        max_threads=0,
        allow_threads=[r"^worker-daemon-.*"],
    )
    delta = make_delta(
        thread_delta=2,
        new_thread_names=["worker-daemon-1", "rogue-thread"],
    )
    violations = limits.check(delta)
    assert len(violations) == 1
    assert "spawned 1 thread(s)" in violations[0]
    assert "rogue-thread" in violations[0]


def test_memory_limits_reporting():
    limits = Limits(max_rss_mb=10.0, max_peak_mb=50.0)
    delta = make_delta(rss_mb=15.5, peak_mb=60.2)
    violations = limits.check(delta)
    assert len(violations) == 2
    assert any("RSS delta 15.5 MB exceeded limit 10.0 MB" in v for v in violations)
    assert any("peak RSS 60.2 MB exceeded limit 50.0 MB" in v for v in violations)


def test_child_and_time_limits():
    limits = Limits(max_children=0, max_time_ms=100.0)
    delta = make_delta(child_delta=2, elapsed_ms=250.0)
    violations = limits.check(delta)
    assert len(violations) == 2
    assert any("spawned 2 child process(es)" in v for v in violations)
    assert any("elapsed time 250.0 ms exceeded limit 100.0 ms" in v for v in violations)
