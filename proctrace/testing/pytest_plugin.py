"""
Pytest plugin for per-test process resource tracking, limit assertions, and baselines.

Integrates ResourceWatcher, Limits checking, regression baselines, and
the `proctrace_probe` fixture into pytest execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import sys
import warnings
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pytest

from proctrace.core.types import ResourceDelta
from proctrace.telemetry.logger import ProctraceLogger
from proctrace.telemetry.watcher import ResourceWatcher
from proctrace.testing.baseline import (
    BaselineData,
    BaselineEntry,
    Tolerance,
    compare_entry,
    is_compatible_environment,
    load_baseline,
    parse_tolerance,
    save_baseline,
)
from proctrace.testing.limits import Limits

PROCTRACE_KEY: Final = pytest.StashKey[ResourceDelta]()
PROCTRACE_VIOLATIONS_KEY: Final = pytest.StashKey[list[str]]()


class _ProbeHelper:
    """Helper object providing context managers for the `proctrace_probe` fixture."""

    def __init__(self, request: pytest.FixtureRequest) -> None:
        self._request = request

    @contextlib.contextmanager
    def assert_no_leaks(
        self,
        *,
        fds: bool = True,
        threads: bool = True,
        children: bool = True,
        allow_leaked: Sequence[str] = (),
        allow_threads: Sequence[str] = (),
    ) -> Iterator[ResourceWatcher]:
        """Assert that no file descriptors, threads, or child processes leak.

        Raises:
            AssertionError: If any resource leaks are detected outside ignore lists.
        """
        watcher = ResourceWatcher(
            memory=False,
            fds=fds,
            threads=threads,
            children=children,
        )
        with watcher:
            yield watcher

        if watcher.result is not None:
            limits = Limits.from_options(
                max_fd_delta=0 if fds else None,
                max_threads=0 if threads else None,
                max_children=0 if children else None,
                allow_leaked=allow_leaked,
                allow_threads=allow_threads,
            )
            violations = limits.check(watcher.result)
            if violations:
                msg = "\n  ".join(violations)
                raise AssertionError(f"proctrace leak assertion failed:\n  {msg}")

    def watch(self, **kwargs: Any) -> ResourceWatcher:
        """Create and return a configured ResourceWatcher."""
        return ResourceWatcher(**kwargs)


@pytest.fixture
def proctrace_probe(request: pytest.FixtureRequest) -> _ProbeHelper:
    """Fixture providing scoped resource monitoring and leak assertions."""
    return _ProbeHelper(request)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register proctrace CLI options with pytest."""
    group = parser.getgroup("proctrace")
    group.addoption(
        "--proctrace",
        action="store_true",
        default=False,
        help="Enable proctrace resource tracking for every test",
    )
    group.addoption(
        "--proctrace-output",
        default=None,
        metavar="PATH",
        help="Write per-test ResourceDelta as JSONL to this path",
    )
    group.addoption(
        "--proctrace-interval",
        default=None,
        type=float,
        metavar="SECONDS",
        help="Background sampler interval in seconds (default: 0.1s or PROCTRACE_SAMPLE_INTERVAL)",
    )
    group.addoption(
        "--proctrace-baseline",
        default=None,
        metavar="PATH",
        help="Path to proctrace baseline JSON file",
    )
    group.addoption(
        "--proctrace-update-baseline",
        action="store_true",
        default=False,
        help="Rewrite baseline JSON file with metrics measured during this run",
    )
    group.addoption(
        "--proctrace-fail-on-regression",
        default=None,
        metavar="TOLERANCE",
        help="Fail tests that exceed baseline metrics by TOLERANCE (e.g. '10%%' or '5MB')",
    )
    group.addoption(
        "--proctrace-top",
        default=20,
        type=int,
        metavar="N",
        help="Show top N highest peak memory tests in terminal summary (default: 20)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register marker and configure ProctracePlugin."""
    config.addinivalue_line(
        "markers",
        "proctrace(**limits): enforce resource limits and regression thresholds for this test",
    )
    if not config.pluginmanager.has_plugin("proctrace_plugin"):
        config.pluginmanager.register(ProctracePlugin(config), "proctrace_plugin")


def _find_baseline_entry(baseline_data: BaselineData, nodeid: str) -> BaselineEntry | None:
    """Find a baseline entry by exact nodeid, or basename fallback if rootdir differed."""
    if nodeid in baseline_data.tests:
        return baseline_data.tests[nodeid]
    parts = nodeid.split("::", 1)
    if len(parts) == 2:
        filename, test_qual = Path(parts[0]).name, parts[1]
        for key, entry in baseline_data.tests.items():
            kparts = key.split("::", 1)
            if len(kparts) == 2 and Path(kparts[0]).name == filename and kparts[1] == test_qual:
                return entry
    return None


class ProctracePlugin:
    """Pytest plugin implementation managing per-test watchers and report generation."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.enabled = config.getoption("--proctrace", default=False)
        self.output_path = config.getoption("--proctrace-output", default=None)
        self.interval = config.getoption("--proctrace-interval", default=None)
        self.baseline_path = config.getoption("--proctrace-baseline", default=None)
        self.update_baseline = config.getoption("--proctrace-update-baseline", default=False)
        self.top_n = config.getoption("--proctrace-top", default=20)

        regression_spec = config.getoption("--proctrace-fail-on-regression", default=None)
        self.regression_tolerance: Tolerance | None = None
        if regression_spec:
            self.regression_tolerance = parse_tolerance(regression_spec)

        self.baseline_data: BaselineData | None = None
        if self.baseline_path:
            self.baseline_data = load_baseline(self.baseline_path)
            if self.baseline_data.tests and not is_compatible_environment(self.baseline_data):
                warnings.warn(
                    f"Baseline was created on {self.baseline_data.platform} with Python {self.baseline_data.python}; "
                    f"skipping regression comparison on {sys.platform} Python {sys.version_info.major}.{sys.version_info.minor}",
                    UserWarning,
                    stacklevel=2,
                )
                self.regression_tolerance = None

        self._results: list[dict[str, Any]] = []
        self._current_watcher: ResourceWatcher | None = None
        self._logger: ProctraceLogger | None = None

        if self.output_path:
            self._logger = ProctraceLogger(self.output_path, format="jsonl")

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        """Warm up asyncio event loop infrastructure once per session."""
        try:
            loop = asyncio.new_event_loop()
            loop.close()
        except Exception:
            pass

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item: pytest.Item):
        """Wrap test body execution in a ResourceWatcher."""
        marker = item.get_closest_marker("proctrace")
        if not (self.enabled or marker is not None):
            yield
            return

        interval = self.interval
        if marker and "sample_interval" in marker.kwargs:
            interval = marker.kwargs["sample_interval"]

        watcher = ResourceWatcher(
            memory=True,
            fds=True,
            threads=True,
            sample_interval=interval,
        )
        self._current_watcher = watcher

        with watcher:
            yield

        self._current_watcher = None

        if watcher.result is not None:
            item.stash[PROCTRACE_KEY] = watcher.result
            violations: list[str] = []

            # 1. Check Limits if marked
            if marker is not None:
                limits = Limits.from_options(
                    max_rss_mb=marker.kwargs.get("max_rss_mb"),
                    max_peak_mb=marker.kwargs.get("max_peak_mb"),
                    max_fd_delta=marker.kwargs.get("max_fd_delta"),
                    max_threads=marker.kwargs.get("max_threads"),
                    max_children=marker.kwargs.get("max_children"),
                    max_time_ms=marker.kwargs.get("max_time_ms"),
                    allow_leaked=marker.kwargs.get("allow_leaked", ()),
                    allow_threads=marker.kwargs.get("allow_threads", ()),
                )
                violations.extend(limits.check(watcher.result))

            # 2. Check baseline regressions if configured
            if self.regression_tolerance is not None and self.baseline_data is not None:
                entry = _find_baseline_entry(self.baseline_data, item.nodeid)
                if entry is not None:
                    violations.extend(
                        compare_entry(entry, watcher.result, self.regression_tolerance)
                    )

            if violations:
                item.stash[PROCTRACE_VIOLATIONS_KEY] = violations

            record = {
                "test": item.nodeid,
                "delta": watcher.result,
                "violations": violations,
            }
            self._results.append(record)

            if self._logger is not None:
                self._logger.log(watcher.result, label=item.nodeid)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo[None]):
        """Evaluate resource violations and gate test outcome."""
        outcome = yield
        rep = outcome.get_result()

        if call.when == "call":
            violations = item.stash.get(PROCTRACE_VIOLATIONS_KEY, None)
            if violations:
                if rep.passed:
                    rep.outcome = "failed"
                    rep.longrepr = "proctrace: " + "; ".join(violations)
                else:
                    rep.sections.append(("proctrace", "\n".join(violations)))

    def pytest_terminal_summary(
        self,
        terminalreporter: Any,
        exitstatus: int,
    ) -> None:
        """Render the terminal summary table and violations list."""
        if not self._results:
            return

        terminalreporter.write_sep("=", "proctrace resource summary")
        header = f"{'Test':<50} {'RSS Δ':>10} {'Peak RSS':>10} {'FD Δ':>6} {'ms':>8}"
        terminalreporter.write_line(header)
        terminalreporter.write_line("-" * len(header))

        # Show top N by peak RSS using heapq.nlargest (Effective Python Item 73)
        top_records = heapq.nlargest(
            self.top_n,
            self._results,
            key=lambda r: r["delta"].peak_rss_bytes,
        )

        for record in top_records:
            d = record["delta"]
            name = record["test"]
            if len(name) > 48:
                name = "..." + name[-45:]
            terminalreporter.write_line(
                f"{name:<50} "
                f"{d.rss_delta_mb:>+9.1f}M "
                f"{d.peak_rss_mb:>9.1f}M "
                f"{d.fd_delta:>+5} "
                f"{d.elapsed_ms:>7.1f}"
            )

        # Print failing violations in red
        failing = [r for r in self._results if r["violations"]]
        if failing:
            terminalreporter.write_sep("=", "proctrace violations", red=True)
            for r in failing:
                terminalreporter.write_line(
                    f"{r['test']}: {'; '.join(r['violations'])}",
                    red=True,
                )

        if self._logger is not None:
            self._logger.close()
            self._logger = None

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        """Flush loggers, update baselines, and serialize xdist worker output."""
        if self._logger is not None:
            self._logger.close()
            self._logger = None

        # Update baseline file if requested
        if self.update_baseline and self.baseline_path:
            target_data = self.baseline_data or BaselineData()
            for record in self._results:
                d: ResourceDelta = record["delta"]
                target_data.tests[record["test"]] = BaselineEntry(
                    peak_rss_bytes=d.peak_rss_bytes,
                    fd_delta=d.fd_delta,
                    elapsed_ms=d.elapsed_ms,
                    samples=1,
                )
            save_baseline(self.baseline_path, target_data)

        # xdist worker support: send results to controller
        workeroutput = getattr(self.config, "workeroutput", None)
        if workeroutput is not None:
            workeroutput["proctrace_results"] = [
                {
                    "test": r["test"],
                    "delta": r["delta"].to_dict(),
                    "violations": r["violations"],
                }
                for r in self._results
            ]

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: Any, error: Any) -> None:
        """xdist controller hook: merge results from finished worker node."""
        workeroutput = getattr(node, "workeroutput", None)
        if workeroutput and "proctrace_results" in workeroutput:
            for raw in workeroutput["proctrace_results"]:
                delta = ResourceDelta.from_dict(raw["delta"])
                self._results.append({
                    "test": raw["test"],
                    "delta": delta,
                    "violations": raw["violations"],
                })


__all__ = [
    "PROCTRACE_KEY",
    "PROCTRACE_VIOLATIONS_KEY",
    "ProctracePlugin",
    "proctrace_probe",
    "pytest_addoption",
    "pytest_configure",
]
