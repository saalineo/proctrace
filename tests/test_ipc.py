import os
import queue
import socket

import pytest

import proctrace
from proctrace.ipc import _registry


def test_ipc_queue_tracing():
    _registry.clear()
    q = queue.Queue(maxsize=100)
    tq = proctrace.trace_ipc(q, name="test_queue")

    # Put and get 10 items
    for i in range(10):
        tq.put(f"item-{i}")
        assert tq.get() == f"item-{i}"

    stats = tq.stats
    assert stats.total_messages() == 10
    assert stats.avg_latency_us() >= 0.0
    assert stats.peak_depth() >= 1

    report = proctrace.ipc_report()
    assert "test_queue" in report
    assert "10 msgs" in report


def test_ipc_pipe_tracing():
    _registry.clear()
    r_fd, w_fd = os.pipe()
    try:
        tp = proctrace.trace_pipe(r_fd, w_fd, name="test_pipe")
        msg = b"hello world"
        tp.write(msg)
        res = tp.read(len(msg))
        assert res == msg

        stats = tp.stats
        assert stats.total_messages() == 1
        assert stats.avg_latency_us() >= 0.0

        report = proctrace.ipc_report()
        assert "test_pipe" in report
        assert "1 msgs" in report
    finally:
        os.close(r_fd)
        os.close(w_fd)


def test_ring_buffer_capacity():
    _registry.clear()
    q = queue.Queue()
    tq = proctrace.trace_ipc(q, name="cap_queue", ring_capacity=5)

    for i in range(10):
        tq.put(i)
        tq.get()

    assert tq.stats.total_messages() == 10
    # Average and p99 operate on the capped ring buffer of 5 items
    assert tq.stats.avg_latency_us() >= 0.0


def test_ipc_socket_tracing():
    _registry.clear()
    s1, s2 = socket.socketpair()
    try:
        with proctrace.trace_socket(s1, name="socket_conn1") as ts1:
            data = b"hello socket world"
            ts1.sendall(data)
            recvd = s2.recv(1024)
            assert recvd == data

            stats = ts1.stats
            assert stats.bytes_sent() == len(data)
            assert stats.avg_send_latency_us() >= 0.0

        with proctrace.trace_socket(s2, name="socket_conn2") as ts2:
            s1.sendall(b"response")
            recvd2 = ts2.recv(1024)
            assert recvd2 == b"response"

            stats2 = ts2.stats
            assert stats2.bytes_recv() == len(b"response")
            assert stats2.peak_recv_buffer_pct() >= 0

        report = proctrace.ipc_report()
        assert "socket_conn1" in report
        assert "socket_conn2" in report
        assert f"sent {len(data)}B" in report

        # Test context manager exception non-suppression
        with (
            pytest.raises(ValueError),
            proctrace.trace_socket(s1, name="err_socket"),
        ):
            raise ValueError("test error")
    finally:
        s1.close()
        s2.close()


def test_fionread_opcode():
    from proctrace.ipc import _FIONREAD
    import sys
    if sys.platform == "darwin":
        assert _FIONREAD == 0x4004667F
    else:
        assert _FIONREAD == 0x541B

