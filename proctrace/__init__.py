"""
proctrace - Process introspection for humans.

Provides lightweight, non-invasive process and resource introspection
tools for measuring memory deltas, file descriptor leaks, thread creation,
IPC latency, and real-time process diagnostics.
"""

from __future__ import annotations

import pathlib
from typing import Final

from proctrace._proctrace_core import probe_version as _probe_version
from proctrace.alloc import (
    AllocEntry,
    AllocationReport,
    AllocationTracker,
    ObjectGrowth,
)
from proctrace.core import (
    ConfigurationError,
    Error,
    ProcessNotFoundError,
    ProctraceError,
    ResourceDelta,
    ResourceMonitoringError,
    SignalHandlingError,
    Timeline,
)
from proctrace.diagnostics import (
    SignalHandlerHandle,
    install_signal_handler,
    uninstall_all_signal_handlers,
    uninstall_signal_handler,
)
from proctrace.ipc import ipc_report, trace_ipc, trace_pipe, trace_socket
from proctrace.telemetry import (
    ResourceWatcher,
    plot_timeline,
    probe,
    render_html,
    render_svg,
    watch,
)
from proctrace.testing import Limits, load_baseline, save_baseline

__version__: Final[str] = _probe_version()

_GUIDE_PATH: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parent.parent / "guidedoc.md"
)
_FALLBACK_GUIDE_PATH: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parent.parent / "GUIDE.md"
)


def help() -> None:
    """Print the developer guide to standard output if available."""
    for path in (_GUIDE_PATH, _FALLBACK_GUIDE_PATH):
        if path.is_file():
            try:
                print(path.read_text(encoding="utf-8"))
                return
            except OSError:
                pass
    print("Guide not found. See the project repository for documentation.")


__all__ = [
    "AllocEntry",
    "AllocationReport",
    "AllocationTracker",
    "ConfigurationError",
    "Error",
    "Limits",
    "ObjectGrowth",
    "ProcessNotFoundError",
    "ProctraceError",
    "ResourceDelta",
    "ResourceMonitoringError",
    "ResourceWatcher",
    "SignalHandlerHandle",
    "SignalHandlingError",
    "Timeline",
    "__version__",
    "help",
    "install_signal_handler",
    "ipc_report",
    "load_baseline",
    "plot_timeline",
    "probe",
    "render_html",
    "render_svg",
    "save_baseline",
    "trace_ipc",
    "trace_pipe",
    "trace_socket",
    "uninstall_all_signal_handlers",
    "uninstall_signal_handler",
    "watch",
]
