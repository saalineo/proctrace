import proctrace
from proctrace import _proctrace_core as core


def test_version_is_set():
    assert isinstance(proctrace.__version__, str)
    assert proctrace.__version__


def test_snapshot_reports_memory_and_fds():
    snap = core.snapshot_resources()
    assert snap.rss_bytes > 0
    assert snap.vms_bytes >= snap.rss_bytes
    assert snap.open_fds > 0
    assert snap.timestamp_ns > 0


def test_snapshot_mb_helpers():
    snap = core.snapshot_resources()
    assert snap.rss_mb() == snap.rss_bytes / 1024**2
    assert snap.vms_mb() == snap.vms_bytes / 1024**2


def test_list_open_fds_returns_paths():
    paths = core.list_open_fds()
    assert len(paths) > 0
    assert all(isinstance(p, str) for p in paths)


def test_root_exception_hierarchy():
    assert issubclass(proctrace.ProctraceError, proctrace.Error)
    assert issubclass(proctrace.ConfigurationError, proctrace.Error)
    assert issubclass(proctrace.ProcessNotFoundError, proctrace.Error)
    assert issubclass(proctrace.ProcessNotFoundError, ProcessLookupError)
    assert issubclass(proctrace.SignalHandlingError, proctrace.Error)
    assert issubclass(proctrace.ResourceMonitoringError, proctrace.Error)


def test_public_api_exports():
    expected_exports = {
        "ConfigurationError",
        "Error",
        "Limits",
        "ProcessNotFoundError",
        "ProctraceError",
        "ResourceDelta",
        "ResourceMonitoringError",
        "ResourceWatcher",
        "SignalHandlerHandle",
        "SignalHandlingError",
        "__version__",
        "help",
        "install_signal_handler",
        "ipc_report",
        "load_baseline",
        "probe",
        "save_baseline",
        "trace_ipc",
        "trace_pipe",
        "trace_socket",
        "uninstall_all_signal_handlers",
        "uninstall_signal_handler",
        "watch",
    }
    assert expected_exports.issubset(set(proctrace.__all__))
    for symbol in proctrace.__all__:
        assert hasattr(proctrace, symbol), f"Missing exported symbol: {symbol}"


def test_subpackage_exports():
    import proctrace.cli as cli
    import proctrace.core as core
    import proctrace.diagnostics as diagnostics
    import proctrace.ipc as ipc
    import proctrace.telemetry as telemetry
    import proctrace.testing as testing

    assert hasattr(core, "ResourceDelta")
    assert hasattr(core, "Error")
    assert hasattr(telemetry, "watch")
    assert hasattr(telemetry, "ResourceWatcher")
    assert hasattr(telemetry, "probe")
    assert hasattr(telemetry, "ProctraceLogger")
    assert hasattr(ipc, "trace_ipc")
    assert hasattr(ipc, "ipc_report")
    assert hasattr(diagnostics, "install_signal_handler")
    assert hasattr(diagnostics, "SignalHandlerHandle")
    assert hasattr(testing, "Limits")
    assert hasattr(testing, "BaselineData")
    assert hasattr(testing, "ProctracePlugin")
    assert hasattr(testing, "proctrace_probe")
    assert hasattr(cli, "main")
    assert hasattr(cli, "build_parser")


def test_help_prints_guide(capsys):
    proctrace.help()
    captured = capsys.readouterr()
    assert "Technical Guide & Developer Manual" in captured.out
    assert "Architecture & Core Mechanics" in captured.out
    assert "Guide not found" not in captured.out


def test_help_fallback(monkeypatch, capsys):
    # Simulate missing guide
    monkeypatch.setattr(proctrace, "_read_guide", lambda: None)
    proctrace.help()
    captured = capsys.readouterr()
    assert captured.out.strip() == "Guide not found. See the project repository for documentation."

