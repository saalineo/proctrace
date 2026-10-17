from __future__ import annotations

from proctrace.diagnostics.dump import do_dump
from proctrace.diagnostics.signals import (
    SIGNAL_MAP,
    SignalHandlerHandle,
    install_signal_handler,
    uninstall_all_signal_handlers,
    uninstall_signal_handler,
)

__all__ = [
    "SIGNAL_MAP",
    "SignalHandlerHandle",
    "do_dump",
    "install_signal_handler",
    "uninstall_all_signal_handlers",
    "uninstall_signal_handler",
]
