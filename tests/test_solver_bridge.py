"""Tests for the postflop solver CLI bridge."""

from __future__ import annotations

import io
import queue
import subprocess
import time
from typing import Any

import pytest

from solver.solver_bridge import PostflopSolverBridge


class FakeProcess:
    """Small subprocess.Popen stand-in for bridge unit tests."""

    def __init__(
        self,
        stdout_text: str = '{"success": true}\n',
        stderr_text: str = "ready\n",
        poll_value: int | None = None,
    ) -> None:
        """Create fake process streams and state."""
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO(stderr_text)
        self.pid = 12345
        self._poll_value = poll_value
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        """Return the configured process state."""
        return self._poll_value

    def terminate(self) -> None:
        """Record terminate calls and mark the process exited."""
        self.terminated = True
        self._poll_value = 0

    def kill(self) -> None:
        """Record kill calls and mark the process exited."""
        self.killed = True
        self._poll_value = -9

    def wait(self, timeout: float | None = None) -> int:
        """Return the configured exit status."""
        return self._poll_value or 0


class SlowStdout:
    """Blocking stdout stand-in used for timeout tests."""

    def readline(self) -> str:
        """Sleep long enough for the bridge timeout to fire."""
        time.sleep(0.2)
        return '{"success": true}\n'


def test_start_waits_for_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """start() launches the CLI and waits for stderr ready."""
    fake_process = FakeProcess()

    def fake_popen(*args: Any, **kwargs: Any) -> FakeProcess:
        assert kwargs["stdin"] == subprocess.PIPE
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.PIPE
        assert kwargs["text"] is True
        assert kwargs["bufsize"] == 1
        return fake_process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    bridge = PostflopSolverBridge("fake_solver.exe")

    bridge.start()

    assert bridge.process is fake_process
    assert bridge.is_alive()
    assert not bridge.disabled


def test_solve_sends_request_and_returns_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """solve() writes one JSON line and returns the decoded response."""
    fake_process = FakeProcess('{"success": true, "value": 1}\n')
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake_process)
    bridge = PostflopSolverBridge("fake_solver.exe")
    bridge.start()

    response = bridge.solve({"board": "QsJh2h"}, timeout=0.5)

    assert response == {"success": True, "value": 1}
    assert fake_process.stdin.getvalue() == '{"board": "QsJh2h"}\n'


def test_solve_timeout_returns_error() -> None:
    """solve() resets the process when stdout does not answer in time."""
    fake_process = FakeProcess()
    fake_process.stdout = SlowStdout()
    bridge = PostflopSolverBridge("fake_solver.exe")
    bridge.process = fake_process  # type: ignore[assignment]

    response = bridge.solve({"board": "QsJh2h"}, timeout=0.01)

    assert response["success"] is False
    assert response["error"] == "Solver timeout (no response within 0.01s)"
    assert fake_process.killed
    assert bridge.process is None


def test_reset_process_kills_alive_process_and_clears_process() -> None:
    """reset_process() kills a live solver process and clears the handle."""
    fake_process = FakeProcess()
    bridge = PostflopSolverBridge("fake_solver.exe")
    bridge.process = fake_process  # type: ignore[assignment]

    killed = bridge.reset_process("unit_test")

    assert killed is True
    assert fake_process.killed
    assert bridge.process is None


def test_reset_process_safe_when_process_is_none() -> None:
    """reset_process() is safe when no process exists."""
    bridge = PostflopSolverBridge("fake_solver.exe")

    killed = bridge.reset_process("no_process")

    assert killed is False
    assert bridge.process is None


def test_solve_after_reset_starts_clean_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A solve after reset starts a fresh process through the normal path."""
    first_process = FakeProcess()
    second_process = FakeProcess('{"success": true, "fresh": true}\n')
    processes = [first_process, second_process]

    def fake_popen(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return processes.pop(0)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    bridge = PostflopSolverBridge("fake_solver.exe")
    bridge.start()
    bridge.reset_process("unit_test")

    response = bridge.solve({"board": "QsJh2h"}, timeout=0.5)

    assert first_process.killed
    assert response == {"success": True, "fresh": True}
    assert bridge.process is second_process


def test_solve_invalid_json_returns_error() -> None:
    """solve() returns a failure dict for invalid JSON from the solver."""
    fake_process = FakeProcess("not json\n")
    bridge = PostflopSolverBridge("fake_solver.exe")
    bridge.process = fake_process  # type: ignore[assignment]

    response = bridge.solve({"board": "QsJh2h"}, timeout=0.5)

    assert response == {
        "success": False,
        "error": "Invalid JSON response from solver",
    }


def test_read_json_response_single_line() -> None:
    """A single-line JSON response is returned unchanged."""
    fake_process = FakeProcess('{"success": true, "value": 1}\n')
    result_queue: queue.Queue[str | BaseException] = queue.Queue()
    bridge = PostflopSolverBridge("fake_solver.exe")

    bridge._read_json_response(fake_process, result_queue)

    result = result_queue.get_nowait()
    assert result == '{"success": true, "value": 1}\n'


def test_read_json_response_multiline() -> None:
    """A JSON response split across lines is accumulated before returning."""
    stdout = '{"success": true,\n"value": [1,\n2, 3]}\n'
    fake_process = FakeProcess(stdout)
    result_queue: queue.Queue[str | BaseException] = queue.Queue()
    bridge = PostflopSolverBridge("fake_solver.exe")

    bridge._read_json_response(fake_process, result_queue)

    result = result_queue.get_nowait()
    assert result == stdout


def test_read_json_response_eof_with_partial() -> None:
    """EOF after partial content returns the partial buffer."""
    stdout = '{"success": true'
    fake_process = FakeProcess(stdout)
    result_queue: queue.Queue[str | BaseException] = queue.Queue()
    bridge = PostflopSolverBridge("fake_solver.exe")

    bridge._read_json_response(fake_process, result_queue)

    result = result_queue.get_nowait()
    assert result == stdout


def test_read_json_response_eof_empty() -> None:
    """Immediate EOF returns a RuntimeError."""
    fake_process = FakeProcess("")
    result_queue: queue.Queue[str | BaseException] = queue.Queue()
    bridge = PostflopSolverBridge("fake_solver.exe")

    bridge._read_json_response(fake_process, result_queue)

    result = result_queue.get_nowait()
    assert isinstance(result, RuntimeError)
    assert str(result) == "Solver process closed stdout"


def test_stop_terminates_process() -> None:
    """stop() terminates the current process and clears it."""
    fake_process = FakeProcess()
    bridge = PostflopSolverBridge("fake_solver.exe")
    bridge.process = fake_process  # type: ignore[assignment]

    bridge.stop()

    assert fake_process.terminated
    assert bridge.process is None


def test_restart_failures_disable_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three consecutive restart failures disable future solve calls."""

    def raise_missing(*args: Any, **kwargs: Any) -> FakeProcess:
        raise FileNotFoundError("missing")

    monkeypatch.setattr(subprocess, "Popen", raise_missing)
    bridge = PostflopSolverBridge("missing_solver.exe")

    for _ in range(4):
        response = bridge.solve({"board": "QsJh2h"}, timeout=0.01)

    assert response == {
        "success": False,
        "error": "Solver disabled after repeated failures",
    }
    assert bridge.disabled


def test_start_raises_when_cli_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """start() propagates process launch errors for a missing CLI."""

    def raise_missing(*args: Any, **kwargs: Any) -> FakeProcess:
        raise FileNotFoundError("missing")

    monkeypatch.setattr(subprocess, "Popen", raise_missing)
    bridge = PostflopSolverBridge("missing_solver.exe")

    with pytest.raises(FileNotFoundError):
        bridge.start()
