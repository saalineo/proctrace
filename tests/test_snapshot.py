import asyncio
import io
import os
import signal
import sys
import time

import pytest
import proctrace

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="requires POSIX signals"
)


def test_install_signal_handler_does_not_raise():
    proctrace.install_signal_handler("SIGUSR1")


def test_signal_triggers_thread_dump(capsys):
    buf = io.StringIO()
    proctrace.install_signal_handler("SIGUSR1", output=buf, include_asyncio=False)
    os.kill(os.getpid(), signal.SIGUSR1)
    time.sleep(0.25)
    output = buf.getvalue()
    assert "proctrace thread dump @" in output
    assert "proctrace-signal-watcher" in output


@pytest.mark.asyncio
async def test_signal_triggers_asyncio_dump():
    buf = io.StringIO()
    loop = asyncio.get_running_loop()
    proctrace.install_signal_handler("SIGUSR2", output=buf, include_asyncio=True, loop=loop)

    async def _dummy_task():
        await asyncio.sleep(0.5)

    task = asyncio.create_task(_dummy_task(), name="day5_dummy_task")
    await asyncio.sleep(0.05)

    os.kill(os.getpid(), signal.SIGUSR2)
    await asyncio.sleep(0.25)

    output = buf.getvalue()
    assert "proctrace thread dump @" in output
    assert "day5_dummy_task" in output

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_rapid_signals_do_not_crash():
    buf = io.StringIO()
    proctrace.install_signal_handler("SIGALRM", output=buf, include_asyncio=False)
    for _ in range(5):
        os.kill(os.getpid(), signal.SIGALRM)
    time.sleep(0.3)
    output = buf.getvalue()
    assert "proctrace thread dump @" in output
