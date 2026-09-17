from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from proctrace.logger import ProctraceLogger
    from proctrace.watch import ResourceWatcher


def pytest_addoption(parser: pytest.Parser) -> None:
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


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--proctrace", default=False):
        config.pluginmanager.register(ProctracePlugin(config), "proctrace_plugin")


class ProctracePlugin:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.output_path = config.getoption("--proctrace-output", default=None)
        self.interval = config.getoption("--proctrace-interval", default=None)
        self._results: list[dict[str, Any]] = []
        self._current_watcher: ResourceWatcher | None = None
        self._logger: ProctraceLogger | None = None

        if self.output_path:
            from proctrace.logger import ProctraceLogger

            self._logger = ProctraceLogger(self.output_path, format="jsonl")

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item: pytest.Item):
        from proctrace.watch import ResourceWatcher

        watcher = ResourceWatcher(
            memory=True,
            fds=True,
            threads=True,
            sample_interval=self.interval,
        )
        self._current_watcher = watcher

        with watcher:
            yield

        self._current_watcher = None

        if watcher.result is not None:
            record = {
                "test": item.nodeid,
                "delta": watcher.result,
            }
            self._results.append(record)

            if self._logger is not None:
                self._logger.log(watcher.result, label=item.nodeid)

    def pytest_terminal_summary(
        self,
        terminalreporter: Any,
        exitstatus: int,
    ) -> None:
        if not self._results:
            return

        terminalreporter.write_sep("=", "proctrace resource summary")
        header = f"{'Test':<50} {'RSS Δ':>10} {'Peak RSS':>10} {'FD Δ':>6} {'ms':>8}"
        terminalreporter.write_line(header)
        terminalreporter.write_line("-" * len(header))

        for record in self._results:
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

        if self._logger is not None:
            self._logger.close()
