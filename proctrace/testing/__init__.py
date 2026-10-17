"""
Testing utilities, limits checking, regression baselines, and Pytest plugin.
"""

from __future__ import annotations

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
from proctrace.testing.pytest_plugin import (
    PROCTRACE_KEY,
    PROCTRACE_VIOLATIONS_KEY,
    ProctracePlugin,
    proctrace_probe,
    pytest_addoption,
    pytest_configure,
)

__all__ = [
    "BaselineData",
    "BaselineEntry",
    "Limits",
    "PROCTRACE_KEY",
    "PROCTRACE_VIOLATIONS_KEY",
    "ProctracePlugin",
    "Tolerance",
    "compare_entry",
    "is_compatible_environment",
    "load_baseline",
    "parse_tolerance",
    "proctrace_probe",
    "pytest_addoption",
    "pytest_configure",
    "save_baseline",
]
